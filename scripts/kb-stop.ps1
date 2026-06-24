Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $RootDir

docker compose stop api postgres qdrant prometheus grafana *> $null
Write-Output "Knowledge base services stopped"

