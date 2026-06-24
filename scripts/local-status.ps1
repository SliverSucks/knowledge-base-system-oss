Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$short = $false
if ($args.Count -ge 1 -and $args[0] -eq "--short") { $short = $true }

$health = "down"
try {
  $api = Invoke-RestMethod -Uri "http://127.0.0.1:18000/health" -TimeoutSec 2
  if ($api.status -eq "ok") { $health = "ok" }
} catch {}

if ($short) {
  if ($health -eq "ok") { Write-Output "Running (local healthy)" } else { Write-Output "Not ready" }
  exit 0
}

Write-Output "Local Knowledge Base Status"
Write-Output "---------------------------"
Write-Output ("API health: " + $health)
