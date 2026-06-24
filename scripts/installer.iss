; 知识库直装版 Windows 安装脚本
; 使用方式：由 build_direct_install.ps1 自动调用，或手动用 ISCC.exe 编译

#define AppName "百变怪芝士包"
#ifndef AppVersion
  #define AppVersion "1.2.8"
#endif
#define AppExeName "kb-tray.exe"
#define RootDir ".."

[Setup]
AppId={{D2B0E5C4-7F1A-4E3B-9A8C-1B2C3D4E5F60}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=knowledge-base-system
DefaultDirName={localappdata}\KnowledgeBase
DefaultGroupName={#AppName}
AllowNoIcons=yes
; 强制显示「选择安装位置 / 开始菜单文件夹」向导页，
; 不让 Inno 的 auto 模式因任何残留状态偷偷跳过
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=no
DisableReadyPage=no
OutputDir={#RootDir}\dist
OutputBaseFilename=KnowledgeBase-Setup-{#AppVersion}
SetupIconFile={#RootDir}\windows-app\assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 不需要管理员权限，安装到 localappdata
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 最低 Windows 10
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 核心程序
Source: "{#RootDir}\bin\kb-api.exe";   DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#RootDir}\bin\kb-tray.exe";  DestDir: "{app}\bin"; Flags: ignoreversion
; 图标（供快捷方式使用）
Source: "{#RootDir}\windows-app\assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion
; 引导配置 — 首次安装写入，升级时保留用户已修改的版本
Source: "{#RootDir}\config\config.toml"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist
; 使用说明
Source: "{#RootDir}\使用说明.md"; DestDir: "{app}"; Flags: ignoreversion
; 版本标识 — app/main.py 启动时读 {KB_APP_ROOT}\VERSION 作为 APP_VERSION
Source: "{#RootDir}\VERSION"; DestDir: "{app}"; Flags: ignoreversion
; 直装版重启脚本（/v1/system/restart 调用）
Source: "{#RootDir}\scripts\local-restart-direct.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
; Agent 接入工具包
Source: "{#RootDir}\agent-integration\*"; DestDir: "{app}\agent-integration"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; 运行时目录，预先建好避免权限问题
Name: "{app}\data"
Name: "{app}\logs"

[Icons]
; 安装根目录放一个启动快捷方式，双击即可启动
Name: "{app}\{#AppName}";             Filename: "{app}\bin\{#AppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\{#AppName}";           Filename: "{app}\bin\{#AppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\卸载 {#AppName}";      Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";   Filename: "{app}\bin\{#AppExeName}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\使用说明.md"; Description: "查看使用说明"; Flags: nowait postinstall skipifsilent shellexec unchecked
Filename: "{app}\bin\{#AppExeName}"; Description: "启动百变怪芝士包"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先终止进程
Filename: "taskkill.exe"; Parameters: "/IM kb-tray.exe /F"; Flags: runhidden waituntilterminated; RunOnceId: "KillTray"
Filename: "taskkill.exe"; Parameters: "/IM kb-api.exe /F";  Flags: runhidden waituntilterminated; RunOnceId: "KillApi"

[UninstallDelete]
; 卸载时始终清理"程序文件"，与 mac Uninstall.command 行为对齐：
;   - logs：运行时日志，下次重装会重建
;   - runtime：owner_token 等运行时状态，重装时重新生成
; 用户数据（data / models / embedding-service / auto-backup）由 [Code] 段
; 在 InitializeUninstall 里弹 4 个 MsgBox 询问，在 usPostUninstall 阶段按需删
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\runtime"

[Code]
// 与 macOS Install.command 行为对齐：
// 1. InitializeSetup —— 检测旧版 kb-api.exe / kb-tray.exe 是否在跑，
//    在跑则提示用户先退出再装（防 SQLite / Qdrant 拿到不一致 snapshot）
// 2. PrepareToInstall —— 升级场景下，先 cp {app}\data 到
//    {localappdata}\KnowledgeBase\auto-backup\{时间戳}\data，失败则 abort，
//    不动任何旧文件（与 mac 端 #3 审计修复一致）

function IsProcessRunning(const ExeName: String): Boolean;
var
  ResultCode: Integer;
  TmpFile: String;
  Lines: TArrayOfString;
  i: Integer;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\kb-tasklist.txt');
  if Exec(ExpandConstant('{cmd}'),
          '/C tasklist /FI "IMAGENAME eq ' + ExeName + '" /NH > "' + TmpFile + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if LoadStringsFromFile(TmpFile, Lines) then
    begin
      for i := 0 to GetArrayLength(Lines) - 1 do
      begin
        if Pos(LowerCase(ExeName), LowerCase(Lines[i])) > 0 then
        begin
          Result := True;
          Break;
        end;
      end;
    end;
    DeleteFile(TmpFile);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if IsProcessRunning('kb-api.exe') or IsProcessRunning('kb-tray.exe') then
  begin
    MsgBox('检测到知识库服务正在运行。' + #13#10 + #13#10 +
           '请先在托盘图标上右键「退出」后再继续安装。' + #13#10 +
           '（防止 SQLite / Qdrant 拿到不一致 snapshot）',
           mbError, MB_OK);
    Result := False;
  end;
end;

function GetTimestamp(): String;
begin
  // Inno 内置：DateTimeFormat 用 yyyymmdd_hhnnss，分隔符传 #0 表示忽略
  Result := GetDateTimeString('yyyymmdd_hhnnss', #0, #0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  SrcDataDir, BackupRoot, BackupDir, ManifestPath: String;
  ResultCode: Integer;
  ManifestContent: AnsiString;
begin
  Result := '';
  NeedsRestart := False;

  SrcDataDir := ExpandConstant('{app}\data');
  // 首次安装：data 目录不存在或为空文件夹 → 跳过备份
  if not DirExists(SrcDataDir) then
    Exit;
  if not (FileExists(SrcDataDir + '\knowledge.db') or DirExists(SrcDataDir + '\qdrant_local')) then
    Exit;

  BackupRoot := ExpandConstant('{localappdata}\KnowledgeBase\auto-backup');
  BackupDir := BackupRoot + '\' + GetTimestamp();

  if not ForceDirectories(BackupDir) then
  begin
    Result := '无法创建自动备份目录：' + BackupDir + #13#10 +
              '安装已中止，旧版数据未被改动。';
    Exit;
  end;

  // xcopy /E /I /Q /Y data → backup\data（/Y 防覆盖确认；/H 含隐藏）
  if not Exec(ExpandConstant('{cmd}'),
              '/C xcopy "' + SrcDataDir + '" "' + BackupDir + '\data\" /E /I /Q /Y /H',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := '调用 xcopy 失败（无法启动子进程）。' + #13#10 +
              '安装已中止，旧版数据未被改动。';
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    Result := 'xcopy 返回错误码 ' + IntToStr(ResultCode) + #13#10 +
              '可能磁盘空间不足或路径权限受限。' + #13#10 +
              '安装已中止，旧版数据未被改动。';
    Exit;
  end;

  // 最小 manifest — 与 mac 端 auto-backup manifest 同形
  ManifestPath := BackupDir + '\manifest.json';
  ManifestContent :=
    '{"schema_version":1,' +
    '"created_at":"' + GetTimestamp() + '",' +
    '"source":"windows-installer",' +
    '"app_version":"{#AppVersion}"}';
  SaveStringToFile(ManifestPath, ManifestContent, False);
end;

// ============================================================================
// 卸载阶段：4 个用户数据目录的去留交互（与 mac Uninstall.command 对齐）
// ============================================================================
// 设计：
//   - InitializeUninstall —— 进程检测 + 4 个 MsgBox 询问；用户选项记到全局 var
//   - CurUninstallStepChanged(usPostUninstall) —— Inno 卸载完声明式 [Files] /
//     [UninstallDelete] 后，按全局 var 删 data / models / embedding-service /
//     auto-backup；没被选中的目录保留原处，方便重装时找回
//   - MsgBox 默认按钮：删了找不回的（data / models / auto-backup）默认 No（mb_DefButton2）；
//     embedding-service venv 可重建，默认 Yes
// ----------------------------------------------------------------------------

var
  UninstCleanData: Boolean;
  UninstCleanModels: Boolean;
  UninstCleanEmbedding: Boolean;
  UninstCleanBackup: Boolean;

function GetBackupRoot(): String;
begin
  Result := ExpandConstant('{localappdata}\KnowledgeBase\auto-backup');
end;

function InitializeUninstall(): Boolean;
var
  AppDir, DataDir, ModelsDir, EmbedDir, BackupDir: String;
begin
  Result := True;

  // 1. 进程检测：跟 Install 端同步
  if IsProcessRunning('kb-api.exe') or IsProcessRunning('kb-tray.exe') then
  begin
    MsgBox('检测到知识库服务正在运行。' + #13#10 + #13#10 +
           '请先在托盘图标上右键「退出」后再卸载。' + #13#10 +
           '（防止 SQLite / Qdrant 拿到不一致 snapshot）',
           mbError, MB_OK);
    Result := False;
    Exit;
  end;

  AppDir := ExpandConstant('{app}');
  DataDir := AppDir + '\data';
  ModelsDir := AppDir + '\models';
  EmbedDir := AppDir + '\embedding-service';
  BackupDir := GetBackupRoot();

  UninstCleanData := False;
  UninstCleanModels := False;
  UninstCleanEmbedding := False;
  UninstCleanBackup := False;

  // 2. 四问 —— 不存在的目录直接跳过，不打扰用户
  if DirExists(DataDir) then
  begin
    if MsgBox('删除知识库数据吗？' + #13#10 + #13#10 +
              '路径：' + DataDir + #13#10 +
              '内容：SQLite 主库 + Qdrant 向量索引' + #13#10 + #13#10 +
              '⚠ 删除后无法恢复，重装也找不回。' + #13#10 +
              '默认「否」（保留），建议保留。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      UninstCleanData := True;
  end;

  if DirExists(ModelsDir) then
  begin
    if MsgBox('删除本地模型吗？' + #13#10 + #13#10 +
              '路径：' + ModelsDir + #13#10 +
              '内容：已下载的 embedding 模型权重（通常 2~5 GB）' + #13#10 + #13#10 +
              '删除后重装需重新下载。默认「否」（保留）。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      UninstCleanModels := True;
  end;

  if DirExists(EmbedDir) then
  begin
    if MsgBox('删除 Embedding 服务运行环境吗？' + #13#10 + #13#10 +
              '路径：' + EmbedDir + #13#10 +
              '内容：独立 Python venv（infinity-emb 等依赖）' + #13#10 + #13#10 +
              '重装时可自动重建。默认「是」（删除）。',
              mbConfirmation, MB_YESNO) = IDYES then
      UninstCleanEmbedding := True;
  end;

  if DirExists(BackupDir) then
  begin
    if MsgBox('删除历史自动备份吗？' + #13#10 + #13#10 +
              '路径：' + BackupDir + #13#10 +
              '内容：每次安装 / 升级前自动备份的 data/' + #13#10 + #13#10 +
              '⚠ 这是最后的救命稻草，删除后无法恢复。' + #13#10 +
              '默认「否」（保留），建议保留。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      UninstCleanBackup := True;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir, DataDir, ModelsDir, EmbedDir, BackupDir: String;
  RetainedMsg: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  AppDir := ExpandConstant('{app}');
  DataDir := AppDir + '\data';
  ModelsDir := AppDir + '\models';
  EmbedDir := AppDir + '\embedding-service';
  BackupDir := GetBackupRoot();

  // 按 InitializeUninstall 阶段记录的选项删
  if UninstCleanData and DirExists(DataDir) then
    DelTree(DataDir, True, True, True);
  if UninstCleanModels and DirExists(ModelsDir) then
    DelTree(ModelsDir, True, True, True);
  if UninstCleanEmbedding and DirExists(EmbedDir) then
    DelTree(EmbedDir, True, True, True);
  if UninstCleanBackup and DirExists(BackupDir) then
    DelTree(BackupDir, True, True, True);

  // 残留汇总：告诉用户哪些目录被保留了，方便手动清 / 重装识别
  RetainedMsg := '';
  if (not UninstCleanData) and DirExists(DataDir) then
    RetainedMsg := RetainedMsg + #13#10 + '  - ' + DataDir;
  if (not UninstCleanModels) and DirExists(ModelsDir) then
    RetainedMsg := RetainedMsg + #13#10 + '  - ' + ModelsDir;
  if (not UninstCleanEmbedding) and DirExists(EmbedDir) then
    RetainedMsg := RetainedMsg + #13#10 + '  - ' + EmbedDir;
  if (not UninstCleanBackup) and DirExists(BackupDir) then
    RetainedMsg := RetainedMsg + #13#10 + '  - ' + BackupDir;

  if RetainedMsg <> '' then
    MsgBox('卸载完成。' + #13#10 + #13#10 +
           '以下目录按你的选择保留在原处（重装时会被自动识别复用）：' +
           RetainedMsg,
           mbInformation, MB_OK);

  // 安装根目录如果空了，Inno 会自动删；非空则保留
end;
