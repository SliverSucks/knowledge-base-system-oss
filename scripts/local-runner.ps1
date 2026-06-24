Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $RootDir

$env:KB_BACKEND = "sqlite"
$env:SQLITE_PATH = "$RootDir\data\knowledge.db"
$env:VECTOR_ENABLED = "1"
$env:QDRANT_MODE = "local"
$env:QDRANT_LOCAL_PATH = "$RootDir\data\qdrant_local"
$env:UVICORN_WORKERS = "1"

$DEFAULT_PORT = 18000
$port = $DEFAULT_PORT
$portSource = "default"
$configPath = "$RootDir\config\config.toml"
if (Test-Path -LiteralPath $configPath) {
  try {
    $inServer = $false
    foreach ($line in Get-Content -LiteralPath $configPath) {
      if ($line -match '^\s*\[server\]\s*$') {
        $inServer = $true
        continue
      }
      if ($inServer -and $line -match '^\s*\[') {
        break
      }
      if ($inServer -and $line -match '^\s*port\s*=\s*(\d+)\s*$') {
        $port = [int]$Matches[1]
        $portSource = "config.toml"
        break
      }
    }
  } catch {
    $port = $DEFAULT_PORT
    $portSource = "default"
  }
}

Write-Output "[local-runner] KB_BACKEND=$($env:KB_BACKEND)"
Write-Output "[local-runner] SQLITE_PATH=$($env:SQLITE_PATH)"
Write-Output "[local-runner] QDRANT_MODE=$($env:QDRANT_MODE)"
Write-Output "[local-runner] QDRANT_LOCAL_PATH=$($env:QDRANT_LOCAL_PATH)"
Write-Output "[local-runner] PORT=$port (source=$portSource)"

& "$RootDir\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port --workers 1
