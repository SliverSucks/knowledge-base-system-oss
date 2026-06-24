Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $RootDir

powershell -ExecutionPolicy Bypass -File "$RootDir\scripts\local-stop.ps1" | Out-Null
Start-Sleep -Seconds 1
powershell -ExecutionPolicy Bypass -File "$RootDir\scripts\local-start.ps1" | Out-Null
