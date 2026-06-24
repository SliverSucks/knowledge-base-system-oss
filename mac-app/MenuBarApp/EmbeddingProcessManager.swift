// Mac 壳层 EmbeddingProcessManager —— 内置 embedding 服务子进程生命周期。
//
// 完整契约见 docs/14-phase3-process-manager-contract.md。本文件是
// windows-app/embedding_process_manager.py 的 Swift 翻译,行为一一对应:
//
// - OwnerTokenSource:   读 runtime/owner_token,启动期阻塞等
// - KbApiClient:        URLSession HTTP 客户端,带 X-Embedding-Owner-Token
// - InstallExecutor:    venv / pip / snapshot_download,hf-mirror 兜底
// - StartHandler:       Process spawn + /health 探活
// - StopHandler:        terminate (SIGTERM) -> 3s -> kill (SIGKILL)
// - StaleResidueCleaner ps -p {pid} cmdline 比对,adopt 自家进程
// - EmbeddingActionHandler / EmbeddingProcessManager: 顶层串联 + reconcile loop
//
// 设计原则:
// 1. 单文件,不引入外部依赖 (Foundation 自带)
// 2. 所有 IO 走 async/DispatchQueue,reconcile loop 独占一个 background queue
// 3. AppDelegate 只持有一个 EmbeddingProcessManager 实例,通过 start()/stop() 控制
//
// 用法示例 (见 main.swift 集成):
//
//   let mgr = EmbeddingProcessManager(
//       dataRoot: "/Users/x/.knowledgebase",
//       kbApiPort: 18000,
//   )
//   mgr.start()
//   // ...
//   mgr.stop()

import Foundation

// MARK: - 数据类型

struct EmbedDesiredState {
    var action: String = "none"          // none|install|start|stop|switch_model
    var modelId: String = ""
    var device: String = "cpu"
    var enabled: Bool = false
    var generation: Int = 0
    var updatedAt: Double = 0.0

    static func decode(_ json: [String: Any]) -> EmbedDesiredState {
        var s = EmbedDesiredState()
        s.action = json["action"] as? String ?? "none"
        s.modelId = json["model_id"] as? String ?? ""
        s.device = json["device"] as? String ?? "cpu"
        s.enabled = json["enabled"] as? Bool ?? false
        s.generation = (json["generation"] as? Int) ?? Int(json["generation"] as? Double ?? 0)
        s.updatedAt = (json["updated_at"] as? Double) ?? 0.0
        return s
    }
}

struct EmbedActualState {
    var acknowledgedGeneration: Int = 0
    var installed: Bool = false
    var running: Bool = false
    var warmingUp: Bool = false
    var modelId: String = ""
    var port: Int = 0
    var pid: Int? = nil
    var device: String = "cpu"
    var restartCount: Int = 0
    var lastError: String = ""

    func toPayload() -> [String: Any] {
        var p: [String: Any] = [
            "acknowledged_generation": acknowledgedGeneration,
            "installed": installed,
            "running": running,
            "warming_up": warmingUp,
            "model_id": modelId,
            "port": port,
            "device": device,
            "restart_count": restartCount,
            "last_error": lastError,
        ]
        if let pid = pid {
            p["pid"] = pid
        } else {
            p["pid"] = NSNull()
        }
        return p
    }
}

// MARK: - 异常

enum EmbedError: Error {
    case ownerTokenUnavailable(String)
    case kbApiUnauthorized(String)
    case kbApiConflict(String)
    case kbApiTransport(String)
    case spawnFailed(String)
}

// MARK: - OwnerTokenSource

final class OwnerTokenSource {
    let path: URL
    let bootTimeoutSec: Double
    let pollIntervalSec: Double

    private let lock = NSLock()
    private var cached: String?

    init(path: URL, bootTimeoutSec: Double = 60.0, pollIntervalSec: Double = 1.0) {
        self.path = path
        self.bootTimeoutSec = bootTimeoutSec
        self.pollIntervalSec = pollIntervalSec
    }

    func loadBlocking() throws -> String {
        lock.lock()
        if let t = cached {
            lock.unlock()
            return t
        }
        lock.unlock()

        let deadline = Date().addingTimeInterval(bootTimeoutSec)
        while Date() < deadline {
            if let token = readOnce() {
                lock.lock()
                cached = token
                lock.unlock()
                return token
            }
            Thread.sleep(forTimeInterval: pollIntervalSec)
        }
        throw EmbedError.ownerTokenUnavailable("owner_token 在 \(bootTimeoutSec)s 内未出现于 \(path.path)")
    }

    func invalidate() {
        lock.lock()
        cached = nil
        lock.unlock()
    }

    private func readOnce() -> String? {
        guard let data = try? Data(contentsOf: path),
              let text = String(data: data, encoding: .utf8) else {
            return nil
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

// MARK: - KbApiClient

final class KbApiClient {
    let baseURL: URL
    let tokenSource: OwnerTokenSource
    let session: URLSession

    init(baseURL: URL, tokenSource: OwnerTokenSource, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokenSource = tokenSource
        self.session = session
    }

    func getDesired() throws -> EmbedDesiredState {
        let body = try doRequest(method: "GET", path: "/v1/system/embedding-service/desired-state", payload: nil)
        return EmbedDesiredState.decode(body)
    }

    func postActual(_ snap: EmbedActualState) throws {
        _ = try doRequest(
            method: "POST",
            path: "/v1/system/embedding-service/actual-state",
            payload: snap.toPayload()
        )
    }

    /// 拉 install plan(壳层 ProcessManager 据此执行 venv/pip/下载/启动)。
    /// 单一真源:Python build_install_plan，避免 Swift 端复刻。
    func getInstallPlan(modelId: String, device: String) throws -> [String: Any] {
        return try doRequest(
            method: "GET",
            path: "/v1/system/embedding-service/install-plan",
            query: ["model_id": modelId, "device": device],
            payload: nil
        )
    }

    // MARK: - 内部 IO

    private func doRequest(
        method: String,
        path: String,
        query: [String: String]? = nil,
        payload: [String: Any]?
    ) throws -> [String: Any] {
        let token = try tokenSource.loadBlocking()
        var comps = URLComponents(
            url: baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: false
        )!
        if let q = query, !q.isEmpty {
            comps.queryItems = q.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "X-Embedding-Owner-Token")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 5.0
        if let p = payload {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: p, options: [])
        }

        let semaphore = DispatchSemaphore(value: 0)
        var responseData: Data?
        var responseError: Error?
        var statusCode = 0

        let task = session.dataTask(with: req) { data, response, error in
            responseData = data
            responseError = error
            statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            semaphore.signal()
        }
        task.resume()
        _ = semaphore.wait(timeout: .now() + 10.0)

        if let e = responseError {
            throw EmbedError.kbApiTransport("transport failure: \(e)")
        }
        if statusCode == 401 {
            tokenSource.invalidate()
            throw EmbedError.kbApiUnauthorized("\(method) \(path) -> 401")
        }
        if statusCode == 409 {
            throw EmbedError.kbApiConflict("\(method) \(path) -> 409")
        }
        if statusCode >= 400 {
            throw EmbedError.kbApiTransport("\(method) \(path) -> \(statusCode)")
        }
        guard let data = responseData, !data.isEmpty else {
            return [:]
        }
        guard let json = try JSONSerialization.jsonObject(with: data, options: []) as? [String: Any] else {
            throw EmbedError.kbApiTransport("bad json from \(path)")
        }
        return json
    }
}

// MARK: - InstallStatusWriter

final class InstallStatusWriter {
    let path: URL
    private let startedAt: Double
    private let lock = NSLock()

    init(path: URL) {
        self.path = path
        self.startedAt = Date().timeIntervalSince1970
    }

    func flush(
        phase: String,
        progress: Double = 0.0,
        message: String = "",
        bytesDownloaded: Int = 0,
        totalBytes: Int = 0,
        error: String = ""
    ) {
        let clamped = max(0.0, min(1.0, progress))
        let payload: [String: Any] = [
            "phase": phase,
            "progress": clamped,
            "message": message,
            "bytes_downloaded": bytesDownloaded,
            "total_bytes": totalBytes,
            "started_at": startedAt,
            "updated_at": Date().timeIntervalSince1970,
            "error": error,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: []) else {
            return
        }
        let tmp = path.appendingPathExtension("tmp")
        let dir = path.deletingLastPathComponent()
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: nil)
        do {
            try data.write(to: tmp, options: .atomic)
            // os.replace 等价:macOS Foundation 的 replaceItem 即可
            _ = try? FileManager.default.replaceItemAt(path, withItemAt: tmp)
            // 若 replaceItemAt 失败 (target 不存在),回退到 moveItem
            if !FileManager.default.fileExists(atPath: path.path) {
                try? FileManager.default.moveItem(at: tmp, to: path)
            }
        } catch {
            // 写失败吞掉:下次 flush 会重写
        }
        try? FileManager.default.removeItem(at: tmp)
    }
}

// MARK: - ProcessRunner —— 同步跑命令,可选 tee 日志

struct CommandResult {
    let exitCode: Int32
    let stdoutTail: String
}

final class ProcessRunner {
    func run(_ cmd: [String], logPath: URL? = nil) -> CommandResult {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: cmd[0])
        proc.arguments = Array(cmd.dropFirst())

        var logHandle: FileHandle?
        if let lp = logPath {
            try? FileManager.default.createDirectory(
                at: lp.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: nil
            )
            if !FileManager.default.fileExists(atPath: lp.path) {
                FileManager.default.createFile(atPath: lp.path, contents: nil, attributes: nil)
            }
            logHandle = try? FileHandle(forWritingTo: lp)
            logHandle?.seekToEndOfFile()
        }

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe

        var tail = [String]()
        let queue = DispatchQueue(label: "embed.runner.read")
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { return }
            logHandle?.write(data)
            if let text = String(data: data, encoding: .utf8) {
                queue.sync {
                    tail.append(text)
                    if tail.count > 50 {
                        tail.removeFirst(tail.count - 50)
                    }
                }
            }
        }

        do {
            try proc.run()
        } catch {
            return CommandResult(exitCode: 127, stdoutTail: "\(error)")
        }
        proc.waitUntilExit()
        pipe.fileHandleForReading.readabilityHandler = nil
        try? logHandle?.close()
        let combinedTail = queue.sync { tail.joined() }
        return CommandResult(exitCode: proc.terminationStatus, stdoutTail: combinedTail)
    }
}

// MARK: - InstallExecutor

struct InstallSpec {
    let modelId: String          // 充当残留判定的 model_id(实际 = 模型目录绝对路径)
    let venvDir: String
    let modelDir: String
    let device: String
    let createVenvCmd: [String]
    let pipInstallCmd: [String]
    let downloadArgs: [String: String]   // repo_id / local_dir / endpoint
    let mirrorChain: [String]
}

final class InstallExecutor {
    let statusWriter: InstallStatusWriter
    let pipLogPath: URL
    let runner: ProcessRunner

    init(statusWriter: InstallStatusWriter, pipLogPath: URL, runner: ProcessRunner = ProcessRunner()) {
        self.statusWriter = statusWriter
        self.pipLogPath = pipLogPath
        self.runner = runner
    }

    func execute(_ spec: InstallSpec) -> Bool {
        statusWriter.flush(phase: "preparing", progress: 0.05, message: "准备安装 \(spec.modelId)")

        let venvRes = runner.run(spec.createVenvCmd, logPath: pipLogPath)
        if venvRes.exitCode != 0 {
            statusWriter.flush(
                phase: "failed", progress: 0.05,
                message: "创建 embedding venv 失败",
                error: String(venvRes.stdoutTail.suffix(512))
            )
            return false
        }
        statusWriter.flush(phase: "pip_installing", progress: 0.15, message: "安装 infinity-emb 依赖")

        let pipRes = runner.run(spec.pipInstallCmd, logPath: pipLogPath)
        if pipRes.exitCode != 0 {
            statusWriter.flush(
                phase: "failed", progress: 0.15,
                message: "pip install infinity-emb 失败",
                error: String(pipRes.stdoutTail.suffix(512))
            )
            return false
        }

        // bug 1 修复：跑 snapshot_download 前先检测 local_dir 是不是已经完整。
        // 触发场景：升级 dmg 把 backup 注入回新 staging 时模型已经在 models/ 里（Install.command 已 cp 过来），
        // 或者用户重装但 models 目录还在。这两种情况都不该重新下 ~4GB。
        let localDir = spec.downloadArgs["local_dir"] ?? ""
        if !localDir.isEmpty && isModelDirComplete(localDir) {
            NSLog("model dir already complete at \(localDir); skipping snapshot_download")
            statusWriter.flush(
                phase: "downloading", progress: 0.95,
                message: "检测到本地模型权重已完整，跳过下载"
            )
            statusWriter.flush(phase: "completed", progress: 1.0, message: "安装完成（模型复用）")
            return true
        }

        // 镜像链:primary endpoint + mirrorChain 去重
        var chain = [String]()
        if let primary = spec.downloadArgs["endpoint"], !primary.isEmpty {
            chain.append(primary)
        }
        for ep in spec.mirrorChain where !chain.contains(ep) && !ep.isEmpty {
            chain.append(ep)
        }
        if chain.isEmpty {
            chain = ["https://huggingface.co"]
        }

        var lastError = ""
        for endpoint in chain {
            statusWriter.flush(phase: "downloading", progress: 0.5, message: "下载模型(\(endpoint))")
            let cmd = buildDownloadCmd(spec: spec, endpoint: endpoint)
            let res = runner.run(cmd, logPath: pipLogPath)
            if res.exitCode == 0 {
                statusWriter.flush(phase: "completed", progress: 1.0, message: "安装完成")
                return true
            }
            lastError = String(res.stdoutTail.suffix(512))
            NSLog("download via \(endpoint) failed (rc=\(res.exitCode)); trying next mirror")
        }
        statusWriter.flush(
            phase: "failed", progress: 0.5,
            message: "所有镜像下载失败",
            error: lastError.isEmpty ? "all mirrors exhausted" : lastError
        )
        return false
    }

    /// 检测本地 model 目录是不是包含完整权重，命中即可跳过 snapshot_download。
    ///
    /// 判定规则（保守）：config.json 存在 且 至少一份权重文件（.safetensors / .bin / onnx/*.onnx）
    /// 单文件大于 50MB（防止只下了元数据壳就被误判为完整）。
    ///
    /// 不命中（即使只缺一份权重）就放弃跳过，让 snapshot_download 走标准 resume 路径。
    fileprivate func isModelDirComplete(_ localDir: String) -> Bool {
        let fm = FileManager.default
        let baseURL = URL(fileURLWithPath: localDir)
        let configURL = baseURL.appendingPathComponent("config.json")
        guard fm.fileExists(atPath: configURL.path) else { return false }

        let weightCandidates: [String] = [
            "pytorch_model.bin",
            "model.safetensors",
            "onnx/model.onnx_data",
            "onnx/model.onnx",
        ]
        let minWeightBytes: Int64 = 50 * 1024 * 1024  // 50MB
        for rel in weightCandidates {
            let fileURL = baseURL.appendingPathComponent(rel)
            guard let attrs = try? fm.attributesOfItem(atPath: fileURL.path) else { continue }
            let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
            if size >= minWeightBytes {
                return true
            }
        }
        return false
    }

    private func buildDownloadCmd(spec: InstallSpec, endpoint: String) -> [String] {
        // venv python 绝对路径; Mac 是 venv/bin/python
        let venvPython = "\(spec.venvDir)/bin/python"
        let repoId = spec.downloadArgs["repo_id"] ?? ""
        let localDir = spec.downloadArgs["local_dir"] ?? ""
        let script = """
        from huggingface_hub import snapshot_download;\
        snapshot_download(repo_id=\(quoted(repoId)),local_dir=\(quoted(localDir)),endpoint=\(quoted(endpoint)),resume_download=True)
        """
        return [venvPython, "-c", script]
    }

    private func quoted(_ s: String) -> String {
        let escaped = s.replacingOccurrences(of: "\\", with: "\\\\")
                       .replacingOccurrences(of: "'", with: "\\'")
        return "'\(escaped)'"
    }
}

// MARK: - StartHandler / StopHandler

struct StartSpec {
    let modelId: String
    let device: String
    let startCmd: [String]
    let port: Int
    let runtimeDir: URL
    let infinityLogPath: URL
    let env: [String: String]  // 启动时合并进 proc.environment（如 INFINITY_BETTERTRANSFORMER=false）
}

final class InfinityProcess {
    let process: Process
    let pid: Int32
    init(process: Process) {
        self.process = process
        self.pid = process.processIdentifier
    }
    var isRunning: Bool { process.isRunning }
    func terminate() { process.terminate() }
    func kill() { Foundation.kill(pid, SIGKILL) }
}

final class StartHandler {
    let warmupTimeoutSec: Double
    let probeIntervalSec: Double

    init(warmupTimeoutSec: Double = 120.0, probeIntervalSec: Double = 1.0) {
        self.warmupTimeoutSec = warmupTimeoutSec
        self.probeIntervalSec = probeIntervalSec
    }

    /// Spawn 并等 ready。返回 (handle, ready, lastError)。
    func spawnAndWaitReady(_ spec: StartSpec) -> (InfinityProcess?, Bool, String) {
        // 准备 log 文件
        try? FileManager.default.createDirectory(
            at: spec.infinityLogPath.deletingLastPathComponent(),
            withIntermediateDirectories: true, attributes: nil
        )
        if !FileManager.default.fileExists(atPath: spec.infinityLogPath.path) {
            FileManager.default.createFile(atPath: spec.infinityLogPath.path, contents: nil, attributes: nil)
        }
        let logHandle = try? FileHandle(forWritingTo: spec.infinityLogPath)
        logHandle?.seekToEndOfFile()

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: spec.startCmd[0])
        proc.arguments = Array(spec.startCmd.dropFirst())
        if let lh = logHandle {
            proc.standardOutput = lh
            proc.standardError = lh
        }

        // CWD：infinity_emb env.py:186 拿 cache_dir 用相对路径 ".infinity_cache"，
        // 不设 CWD 会继承菜单栏 App 的 / 然后 mkdir("/.infinity_cache") → 只读崩。
        // spec.startCmd[0] = <venv>/bin/infinity_emb，往上三级 = embedding-service/
        // 是可写目录（跟 venv 平级），让 cache 落在那里。
        let execURL = URL(fileURLWithPath: spec.startCmd[0])
        let embeddingServiceDir = execURL
            .deletingLastPathComponent()  // venv/bin
            .deletingLastPathComponent()  // venv
            .deletingLastPathComponent()  // embedding-service
        proc.currentDirectoryURL = embeddingServiceDir

        // 合并 plan.env 进 process env（如 INFINITY_BETTERTRANSFORMER=false 关掉
        // BetterTransformer 探测，绕开 acceleration.py NameError；详见 Python 端
        // build_install_plan 注释）
        if !spec.env.isEmpty {
            var procEnv = ProcessInfo.processInfo.environment
            for (k, v) in spec.env {
                procEnv[k] = v
            }
            proc.environment = procEnv
        }

        do {
            try proc.run()
        } catch {
            return (nil, false, "spawn failed: \(error)")
        }
        let handle = InfinityProcess(process: proc)

        // 落 pid / port
        try? FileManager.default.createDirectory(
            at: spec.runtimeDir, withIntermediateDirectories: true, attributes: nil
        )
        try? "\(handle.pid)".write(
            to: spec.runtimeDir.appendingPathComponent("pid"),
            atomically: true, encoding: .utf8
        )
        try? "\(spec.port)".write(
            to: spec.runtimeDir.appendingPathComponent("port"),
            atomically: true, encoding: .utf8
        )

        let deadline = Date().addingTimeInterval(warmupTimeoutSec)
        while Date() < deadline {
            if !proc.isRunning {
                return (nil, false, "infinity exited during warmup with code \(proc.terminationStatus)")
            }
            if probe(port: spec.port) {
                return (handle, true, "")
            }
            Thread.sleep(forTimeInterval: probeIntervalSec)
        }
        return (handle, false, "warmup timeout after \(warmupTimeoutSec)s")
    }

    /// /health GET 探活（HTTP 2xx 视为 ready）。
    /// 暴露成 fileprivate 让 EmbeddingProcessManager.selfHealWarmupIfNeeded() 复用同款探针逻辑。
    fileprivate func probe(port: Int) -> Bool {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/health")!)
        req.timeoutInterval = 2.0
        var ok = false
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { _, response, _ in
            if let http = response as? HTTPURLResponse {
                ok = http.statusCode >= 200 && http.statusCode < 300
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 3.0)
        return ok
    }
}

final class StopHandler {
    let graceSec: Double
    let pollIntervalSec: Double
    init(graceSec: Double = 3.0, pollIntervalSec: Double = 0.1) {
        self.graceSec = graceSec
        self.pollIntervalSec = pollIntervalSec
    }

    /// terminate -> wait grace -> kill。返回 (graceful, lastError)。
    func terminateAndWait(_ handle: InfinityProcess, runtimeDir: URL) -> (Bool, String) {
        handle.terminate()
        let deadline = Date().addingTimeInterval(graceSec)
        var graceful = false
        while Date() < deadline {
            if !handle.isRunning {
                graceful = true
                break
            }
            Thread.sleep(forTimeInterval: pollIntervalSec)
        }
        var lastErr = ""
        if !graceful {
            handle.kill()
            let dl2 = Date().addingTimeInterval(1.0)
            while Date() < dl2 {
                if !handle.isRunning { break }
                Thread.sleep(forTimeInterval: pollIntervalSec)
            }
            if handle.isRunning {
                lastErr = "process did not respond to SIGKILL"
            }
        }
        for fname in ["pid", "port"] {
            try? FileManager.default.removeItem(at: runtimeDir.appendingPathComponent(fname))
        }
        return (graceful, lastErr)
    }
}

// MARK: - StaleResidueCleaner

final class StaleResidueCleaner {
    let runtimeDir: URL
    init(runtimeDir: URL) { self.runtimeDir = runtimeDir }

    /// 返回 (adoptPid, stalePort)。adoptPid != nil 表示直接管这个 PID。
    func adoptOrClean(expectedModelId: String) -> (Int32?, Int?) {
        guard let pidInt = readInt("pid") else {
            return (nil, readInt("port"))
        }
        let pid = Int32(pidInt)
        let port = readInt("port")

        if !pidAlive(pid) {
            try? FileManager.default.removeItem(at: runtimeDir.appendingPathComponent("pid"))
            try? FileManager.default.removeItem(at: runtimeDir.appendingPathComponent("port"))
            return (nil, nil)
        }

        let cmdline = psCmdline(pid: pid)
        if cmdline.contains("infinity"),
           let p = port, cmdline.contains("--port \(p)"),
           cmdline.contains("--model-id \(expectedModelId)") {
            return (pid, port)
        }
        // 外人占了 PID,告诉 caller 端口 stale
        return (nil, port)
    }

    private func readInt(_ fname: String) -> Int? {
        let p = runtimeDir.appendingPathComponent(fname)
        guard let text = try? String(contentsOf: p, encoding: .utf8) else { return nil }
        return Int(text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func pidAlive(_ pid: Int32) -> Bool {
        if pid <= 0 { return false }
        return Foundation.kill(pid, 0) == 0 || errno == EPERM
    }

    private func psCmdline(pid: Int32) -> String {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/ps")
        proc.arguments = ["-p", "\(pid)", "-o", "command="]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()
        do { try proc.run() } catch { return "" }
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}

// MARK: - EmbeddingProcessManager (reconcile loop + 串联)

/// SpecFactory 回调:根据 desired/actual 拼 InstallSpec/StartSpec。
typealias EmbedSpecFactory = (EmbedDesiredState, EmbedActualState) -> (InstallSpec?, StartSpec?, URL)

final class EmbeddingProcessManager {
    let client: KbApiClient
    let installer: InstallExecutor
    let starter: StartHandler
    let stopper: StopHandler
    let cleaner: StaleResidueCleaner
    let specFactory: EmbedSpecFactory
    let loopPeriodSec: Double
    let heartbeatSec: Double

    private let actualLock = NSLock()
    private var actual = EmbedActualState()
    private var currentHandle: InfinityProcess?
    private var lastDoneGeneration: Int = -1
    private var lastHeartbeatAt: Double = -1.0
    private var restartCount: Int = 0
    private let maxRestartCount: Int = 3
    private var backoff: Double = 0.0
    private let maxBackoffSec: Double = 30.0

    private var stopFlag = false
    private let workQueue = DispatchQueue(label: "embed.reconcile", qos: .utility)

    init(
        client: KbApiClient,
        installer: InstallExecutor,
        starter: StartHandler,
        stopper: StopHandler,
        cleaner: StaleResidueCleaner,
        specFactory: @escaping EmbedSpecFactory,
        loopPeriodSec: Double = 3.0,
        heartbeatSec: Double = 5.0
    ) {
        self.client = client
        self.installer = installer
        self.starter = starter
        self.stopper = stopper
        self.cleaner = cleaner
        self.specFactory = specFactory
        self.loopPeriodSec = loopPeriodSec
        self.heartbeatSec = heartbeatSec
    }

    func start() {
        workQueue.async { [weak self] in
            self?.runLoop()
        }
    }

    func stop(timeoutSec: Double = 5.0) {
        stopFlag = true
        // 顺带关掉 infinity 子进程,避免 tray quit 后 orphan
        actualLock.lock()
        let handle = currentHandle
        let runtimeDir = specFactory(EmbedDesiredState(), actual).2
        actualLock.unlock()
        if let h = handle {
            _ = stopper.terminateAndWait(h, runtimeDir: runtimeDir)
        }
    }

    func snapshotActual() -> EmbedActualState {
        actualLock.lock()
        defer { actualLock.unlock() }
        return actual
    }

    // MARK: - 主循环

    private func runLoop() {
        while !stopFlag {
            do {
                try tick()
            } catch {
                NSLog("reconcile tick crashed: \(error)")
            }
            let delay = backoff > 0 ? min(backoff, maxBackoffSec) : loopPeriodSec
            // 拆分小 sleep 块让 stopFlag 能更快被检测
            let chunk = 0.5
            var elapsed = 0.0
            while elapsed < delay && !stopFlag {
                Thread.sleep(forTimeInterval: min(chunk, delay - elapsed))
                elapsed += chunk
            }
        }
    }

    private func tick() throws {
        // bug 2 自愈：StartHandler.spawnAndWaitReady 在 120s 内拿不到 /health 200 时
        // 会返回 (handle, ready=false, "warmup timeout")，actual.warmingUp 会卡 true。
        // 但 infinity 实际可能在 120s 后才完成 model load——此时进程仍在跑、/health 真返
        // 200，只是 actual 状态没人重置。shouldSkip 又会因 generation 没涨直接跳过
        // dispatch，永远不会进 doStart 重写 actual。下面在每个 tick 起手做一次轻量自愈：
        // process 仍活着 + /health 200 + warmingUp/lastError 还在脏值 → 立即清状态。
        selfHealWarmupIfNeeded()

        var desired: EmbedDesiredState
        do {
            desired = try client.getDesired()
            backoff = 0.0
        } catch EmbedError.kbApiUnauthorized {
            // token invalidate 已在 client 完成;下轮直接 retry
            return
        } catch {
            NSLog("get_desired transport error: \(error)")
            bumpBackoff()
            return
        }

        if shouldSkip(desired: desired) {
            maybeHeartbeat(desired: desired)
            return
        }
        dispatch(desired: desired)
        lastDoneGeneration = desired.generation
        writeActual(desired: desired)
    }

    /// 当 actual.warmingUp=true 或 lastError 含 warmup 字样、但 process 健康 + /health 200 时，
    /// 重置脏状态。避免用户首次 warmup timeout 后必须手动 stop+start 才能让 banner 变绿。
    private func selfHealWarmupIfNeeded() {
        actualLock.lock()
        let handle = currentHandle
        let snap = actual
        actualLock.unlock()

        // 没接管 process / 进程已退 → 不属于自愈范畴（让正常 reconcile 流程处理）
        guard let h = handle, h.isRunning else { return }
        // 只清那种"warmup 期已过、process 健康但 actual 没人重写"的脏态
        let stuckWarmup = snap.warmingUp || snap.lastError.contains("warmup timeout")
        guard stuckWarmup else { return }
        guard starter.probe(port: snap.port > 0 ? snap.port : 7687) else { return }

        actualLock.lock()
        // 锁内再校验一次（避免与 doStart/doStop 竞态把刚改对的状态又踩回来）
        if actual.warmingUp || actual.lastError.contains("warmup timeout") {
            actual.running = true
            actual.warmingUp = false
            actual.lastError = ""
            NSLog("selfHeal: warmup state cleared (process %d healthy on port %d)",
                  Int(h.pid), actual.port)
        }
        actualLock.unlock()
    }

    private func shouldSkip(desired: EmbedDesiredState) -> Bool {
        if desired.action == "none" {
            if lastDoneGeneration < desired.generation {
                lastDoneGeneration = desired.generation
            }
            return true
        }
        return desired.generation <= lastDoneGeneration
    }

    private func dispatch(desired: EmbedDesiredState) {
        let (installSpec, startSpec, runtimeDir) = specFactory(desired, snapshotActual())
        switch desired.action {
        case "install":
            doInstall(desired: desired, spec: installSpec)
        case "start":
            doStart(desired: desired, spec: startSpec, runtimeDir: runtimeDir)
        case "stop":
            doStop(desired: desired, runtimeDir: runtimeDir)
        case "switch_model":
            doStop(desired: desired, runtimeDir: runtimeDir)
            if let isp = installSpec {
                doInstall(desired: desired, spec: isp)
            }
            doStart(desired: desired, spec: startSpec, runtimeDir: runtimeDir)
        default:
            actualLock.lock()
            actual.lastError = "unknown action: \(desired.action)"
            actualLock.unlock()
        }
    }

    private func doInstall(desired: EmbedDesiredState, spec: InstallSpec?) {
        guard let s = spec else {
            actualLock.lock()
            actual.lastError = "install spec missing"
            actualLock.unlock()
            return
        }
        let ok = installer.execute(s)
        actualLock.lock()
        actual.installed = ok
        actual.modelId = desired.modelId
        actual.device = desired.device
        actual.lastError = ok ? "" : "install failed (see install_status.json)"
        actualLock.unlock()
    }

    private func doStart(desired: EmbedDesiredState, spec: StartSpec?, runtimeDir: URL) {
        guard let s = spec else {
            actualLock.lock()
            actual.lastError = "start spec missing"
            actualLock.unlock()
            return
        }
        let (adoptPid, _) = cleaner.adoptOrClean(expectedModelId: s.modelId)
        if let pid = adoptPid {
            actualLock.lock()
            actual.running = true
            actual.warmingUp = false
            actual.pid = Int(pid)
            actual.port = s.port
            actual.modelId = desired.modelId
            actual.lastError = ""
            actualLock.unlock()
            return
        }
        let (handle, ready, err) = starter.spawnAndWaitReady(s)
        actualLock.lock()
        defer { actualLock.unlock() }
        if let h = handle {
            currentHandle = h
            actual.running = true
            actual.warmingUp = !ready
            actual.pid = Int(h.pid)
            actual.port = s.port
            actual.modelId = desired.modelId
            actual.device = desired.device
            actual.lastError = ready ? "" : err
        } else {
            actual.running = false
            actual.warmingUp = false
            actual.lastError = err
        }
    }

    private func doStop(desired: EmbedDesiredState, runtimeDir: URL) {
        actualLock.lock()
        let handle = currentHandle
        actualLock.unlock()
        guard let h = handle else {
            actualLock.lock()
            actual.running = false
            actual.warmingUp = false
            actual.pid = nil
            actual.lastError = ""
            actualLock.unlock()
            return
        }
        let (graceful, err) = stopper.terminateAndWait(h, runtimeDir: runtimeDir)
        actualLock.lock()
        currentHandle = nil
        restartCount = 0
        actual.running = false
        actual.warmingUp = false
        actual.pid = nil
        actual.restartCount = 0
        actual.lastError = err.isEmpty ? (graceful ? "" : "force-killed after grace") : err
        actualLock.unlock()
    }

    private func maybeHeartbeat(desired: EmbedDesiredState) {
        let now = Date().timeIntervalSince1970
        if lastHeartbeatAt >= 0 && now - lastHeartbeatAt < heartbeatSec {
            return
        }
        writeActual(desired: desired)
    }

    private func writeActual(desired: EmbedDesiredState) {
        actualLock.lock()
        var snap = actual
        actualLock.unlock()
        snap.acknowledgedGeneration = max(
            snap.acknowledgedGeneration, lastDoneGeneration, desired.generation
        )
        do {
            try client.postActual(snap)
            lastHeartbeatAt = Date().timeIntervalSince1970
            backoff = 0.0
        } catch EmbedError.kbApiConflict {
            // 心跳时 generation 落后,丢弃即可
        } catch EmbedError.kbApiUnauthorized {
            // token invalidate 已发生
        } catch {
            NSLog("post_actual transport error: \(error)")
            bumpBackoff()
        }
    }

    private func bumpBackoff() {
        if backoff <= 0 {
            backoff = 1.0
        } else {
            backoff = min(backoff * 2.0, maxBackoffSec)
        }
    }
}

// MARK: - 默认工厂 (给 AppDelegate 用)

/// 一键构造生产级 EmbeddingProcessManager。
///
/// - Parameters:
///   - dataRoot: KB_APP_ROOT 等价路径,通常 = projectRoot
///   - kbApiPort: kb-api 实际端口
///
/// 内部组装:
/// - 所有 runtime / log 文件落在 dataRoot/runtime + dataRoot/logs
/// - mirror chain: hf-mirror.com → huggingface.co
/// - specFactory 暂时返回空 spec; install/start 真启用前需要 wire 完整命令
///   (待 Phase 4 或与 kb-api 联调时补)
func buildDefaultEmbeddingManager(
    dataRoot: String, kbApiPort: Int
) -> EmbeddingProcessManager {
    let root = URL(fileURLWithPath: dataRoot)
    let runtimeDir = root.appendingPathComponent("runtime")
    let logsDir = root.appendingPathComponent("logs")

    let tokenSrc = OwnerTokenSource(
        path: runtimeDir.appendingPathComponent("owner_token")
    )
    let baseURL = URL(string: "http://127.0.0.1:\(kbApiPort)")!
    let client = KbApiClient(baseURL: baseURL, tokenSource: tokenSrc)

    let installer = InstallExecutor(
        statusWriter: InstallStatusWriter(path: runtimeDir.appendingPathComponent("install_status.json")),
        pipLogPath: logsDir.appendingPathComponent("pip.log")
    )
    let starter = StartHandler()
    let stopper = StopHandler()
    let cleaner = StaleResidueCleaner(runtimeDir: runtimeDir)

    // specFactory: 调 kb-api GET /v1/system/embedding-service/install-plan 拉 plan,
    // 转换成 InstallSpec + StartSpec(单一真源,与 Windows Python 端共用 Python
    // build_install_plan)。
    //
    // Cache 设计:reconcile loop 每 3s 调一次 specFactory,但 plan 在 modelId+device
    // 没变时是稳定值,缓存 plan 避免每轮 HTTP。modelId 为空(desired.action=none)时
    // 跳过拉取(返回 nil)。HTTP 失败也返回 nil,让 ProcessManager 走 "spec missing"
    // 分支记 last_error,下轮重试。
    final class PlanCache {
        var key: String = ""
        var installSpec: InstallSpec?
        var startSpec: StartSpec?
    }
    let planCache = PlanCache()
    let infinityLogPath = logsDir.appendingPathComponent("infinity.log")

    let specFactory: EmbedSpecFactory = { desired, _ in
        let modelId = desired.modelId
        let device = desired.device.isEmpty ? "cpu" : desired.device
        if modelId.isEmpty {
            return (nil, nil, runtimeDir)
        }
        let cacheKey = "\(modelId)|\(device)"
        if planCache.key == cacheKey,
           let inst = planCache.installSpec,
           let start = planCache.startSpec {
            return (inst, start, runtimeDir)
        }

        let planJson: [String: Any]
        do {
            planJson = try client.getInstallPlan(modelId: modelId, device: device)
        } catch {
            NSLog("[embed] getInstallPlan(\(modelId), \(device)) failed: \(error)")
            return (nil, nil, runtimeDir)
        }

        // 解析 JSON → InstallSpec
        guard
            let venvDir = planJson["venv_dir"] as? String,
            let modelDir = planJson["model_dir"] as? String,
            let resolvedDevice = planJson["device"] as? String,
            var createVenvCmd = planJson["create_venv_cmd"] as? [String],
            let pipInstallCmd = planJson["pip_install_cmd"] as? [String],
            let downloadArgs = planJson["download_args"] as? [String: String],
            let startCmd = planJson["start_cmd"] as? [String]
        else {
            NSLog("[embed] install-plan JSON missing required fields: \(planJson)")
            return (nil, nil, runtimeDir)
        }

        // Python 端 build_install_plan 返回 ["python", "-m", "venv", ...]，"python"
        // 是 platform-agnostic 逻辑名（注释里明确说"壳层负责映射到 bin/python 或
        // Scripts/python.exe"）。Swift Process.executableURL 必须绝对路径，"python"
        // 会被当作 /python → "doesn't exist"。Mac 用系统自带 /usr/bin/python3（macOS
        // 默认安装，3.9+，足够建 venv）；pip/start cmd 是 venv 内绝对路径，无需映射。
        if !createVenvCmd.isEmpty && (createVenvCmd[0] == "python" || createVenvCmd[0] == "python3") {
            createVenvCmd[0] = "/usr/bin/python3"
        }

        let installSpec = InstallSpec(
            modelId: modelDir,                // 与 build_install_plan 约定:start_cmd 用 modelDir
            venvDir: venvDir,
            modelDir: modelDir,
            device: resolvedDevice,
            createVenvCmd: createVenvCmd,
            pipInstallCmd: pipInstallCmd,
            downloadArgs: downloadArgs,
            mirrorChain: ["https://huggingface.co"]   // 主镜像在 downloadArgs.endpoint(hf-mirror),兜底官方
        )
        // start_cmd 已绑定 modelDir + device + --port {plan.port}（Python 端
        // build_install_plan 显式塞了 --port，避免 infinity v2 用自己默认 7997）
        // Swift StartHandler.probe 用 spec.port 命中相同端口。
        let port = (planJson["port"] as? Int) ?? 7687
        let planEnv = (planJson["env"] as? [String: String]) ?? [:]
        let startSpec = StartSpec(
            modelId: modelDir,
            device: resolvedDevice,
            startCmd: startCmd,
            port: port,
            runtimeDir: runtimeDir,
            infinityLogPath: infinityLogPath,
            env: planEnv
        )

        planCache.key = cacheKey
        planCache.installSpec = installSpec
        planCache.startSpec = startSpec
        return (installSpec, startSpec, runtimeDir)
    }

    return EmbeddingProcessManager(
        client: client,
        installer: installer,
        starter: starter,
        stopper: stopper,
        cleaner: cleaner,
        specFactory: specFactory
    )
}
