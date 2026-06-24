Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

# 直装版重启脚本：用于 /v1/system/restart 在 Windows 平台调用。
# 安装目录布局：
#   <RootDir>\scripts\local-restart-direct.ps1   (本脚本)
#   <RootDir>\bin\kb-api.exe                     (FastAPI 服务可执行)
#   <RootDir>\config\config.toml                 (端口/数据路径)

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $RootDir

$ApiExe = Join-Path $RootDir "bin\kb-api.exe"
if (-not (Test-Path -LiteralPath $ApiExe)) {
    Write-Error "kb-api.exe not found at: $ApiExe"
    exit 1
}

Get-Process -Name "kb-api" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

Start-Process -FilePath $ApiExe `
    -WorkingDirectory $RootDir `
    -WindowStyle Hidden

Write-Output "Local knowledge base restarted via $ApiExe"
