Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$marker = "$RootDir\data\.local_api.pid"

if (Test-Path -LiteralPath $marker) {
  $procIdText = Get-Content -LiteralPath $marker -ErrorAction SilentlyContinue
  if ($procIdText) {
    Stop-Process -Id ([int]$procIdText) -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
}

# Safety net: kill any uvicorn app.main:app python process
# 注意：RootDir 是 WorkingDirectory 不是命令行参数，不能用于匹配
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -match '^python(w)?(\.exe)?$' -and
    $_.CommandLine -match 'uvicorn\s+app.main:app'
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Output "Local knowledge base stopped"
