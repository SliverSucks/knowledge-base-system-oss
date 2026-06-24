param(
    [string]$Version = "1.2.8"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $RootDir

# ── 用户配置区：以下路径请根据本地环境修改 ────────────────────────────────
#   - Anaconda 安装路径（提供 ffi.dll / libexpat.dll 等 PyInstaller 需打包的运行时依赖）
#   - Inno Setup 6 安装路径（生成 .exe 安装程序）
#   搜索 <your-anaconda-path> / <your-inno-setup-path> 替换为你的实际路径
# ──────────────────────────────────────────────────────────────────────────

$VenvPython = "$RootDir\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "找不到虚拟环境 Python: $VenvPython"
    exit 1
}

# 写入 VERSION 文件 — 与 mac dmg 流程一致，供 app/main.py 启动时读为 APP_VERSION
Set-Content -Path "$RootDir\VERSION" -Value $Version -NoNewline -Encoding utf8
Write-Host "=== 版本: $Version ===" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "$RootDir\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$RootDir\build" | Out-Null

# ── kb-api.exe ────────────────────────────────────────────────────────────────
Write-Host "=== 构建 kb-api.exe ===" -ForegroundColor Cyan

$AnacondaDll = "<your-anaconda-path>\Library\bin"

& $VenvPython -m PyInstaller `
    --onefile `
    --name kb-api `
    --distpath "$RootDir\bin" `
    --workpath "$RootDir\build\api" `
    --specpath "$RootDir\build" `
    --collect-all app `
    --collect-all qdrant_client `
    --collect-all grpc `
    --hidden-import uvicorn.lifespan.on `
    --hidden-import uvicorn.protocols.http.h11_impl `
    --hidden-import uvicorn.protocols.http.httptools_impl `
    --hidden-import uvicorn.protocols.websockets.websockets_impl `
    --hidden-import uvicorn.protocols.websockets.wsproto_impl `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.loops.asyncio `
    --hidden-import h11 `
    --hidden-import anyio `
    --hidden-import starlette `
    --add-data "$RootDir\app\static;app/static" `
    --add-binary "$AnacondaDll\ffi.dll;." `
    --add-binary "$AnacondaDll\ffi-8.dll;." `
    --add-binary "$AnacondaDll\libexpat.dll;." `
    --add-binary "$AnacondaDll\sqlite3.dll;." `
    --add-binary "$AnacondaDll\libbz2.dll;." `
    --add-binary "$AnacondaDll\liblzma.dll;." `
    --add-binary "$AnacondaDll\libmpdec-4.dll;." `
    --noconsole `
    "$RootDir\app\server_entry.py"

if ($LASTEXITCODE -ne 0) {
    Write-Error "kb-api.exe 构建失败（exit $LASTEXITCODE）"
    exit 1
}

# ── kb-tray.exe ───────────────────────────────────────────────────────────────
Write-Host "=== 构建 kb-tray.exe ===" -ForegroundColor Cyan

$AnacondaDll = "<your-anaconda-path>\Library\bin"

$AnacondaLib = "<your-anaconda-path>\Library\lib"

& $VenvPython -m PyInstaller `
    --onefile `
    --name kb-tray `
    --distpath "$RootDir\bin" `
    --workpath "$RootDir\build\tray" `
    --specpath "$RootDir\build" `
    --noconsole `
    --icon "$RootDir\windows-app\assets\app.ico" `
    --add-data "$RootDir\windows-app\assets;assets" `
    --hidden-import tkinter `
    --hidden-import tkinter.ttk `
    --hidden-import tkinter.filedialog `
    --hidden-import tkinter.messagebox `
    --hidden-import _tkinter `
    --add-binary "$AnacondaDll\tcl86t.dll;." `
    --add-binary "$AnacondaDll\tk86t.dll;." `
    --add-binary "$AnacondaDll\zlib.dll;." `
    --add-data "$AnacondaLib\tcl8.6;_tcl" `
    --add-data "$AnacondaLib\tk8.6;_tk" `
    --add-binary "$AnacondaDll\ffi.dll;." `
    --add-binary "$AnacondaDll\ffi-8.dll;." `
    --add-binary "$AnacondaDll\libexpat.dll;." `
    --add-binary "$AnacondaDll\sqlite3.dll;." `
    --add-binary "$AnacondaDll\libbz2.dll;." `
    --add-binary "$AnacondaDll\liblzma.dll;." `
    --add-binary "$AnacondaDll\libmpdec-4.dll;." `
    "$RootDir\windows-app\tray_app_local.py"

if ($LASTEXITCODE -ne 0) {
    Write-Error "kb-tray.exe 构建失败（exit $LASTEXITCODE）"
    exit 1
}

# ── 安装包 ────────────────────────────────────────────────────────────────────
Write-Host "=== 构建安装包 ===" -ForegroundColor Cyan

$InnoSetup = "<your-inno-setup-path>\ISCC.exe"
if (-not (Test-Path $InnoSetup)) {
    Write-Warning "找不到 Inno Setup：$InnoSetup，跳过安装包构建"
} else {
    New-Item -ItemType Directory -Force -Path "$RootDir\dist" | Out-Null
    & $InnoSetup "/DAppVersion=$Version" "$RootDir\scripts\installer.iss"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "安装包构建失败（exit $LASTEXITCODE）"
        exit 1
    }
    Write-Host "  dist\KnowledgeBase-Setup-*.exe"
}

# ── 完成 ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Build OK ===" -ForegroundColor Green
Write-Host "  bin\kb-api.exe"
Write-Host "  bin\kb-tray.exe"
Write-Host "  dist\KnowledgeBase-Setup-*.exe"
