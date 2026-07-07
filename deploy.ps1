# One-command deploy: stop -> sync source -> runtime -> start -> verify.
# Kills the manual-copy drift + zombie-process class of bugs.
# Usage:  powershell -ExecutionPolicy Bypass -File deploy.ps1
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = 'C:\Users\L5DKA\AppData\Local\RedmiWatchLive'
$py  = 'C:\Users\L5DKA\AppData\Local\Programs\Python\Python312\python.exe'

# gate: never deploy protocol code that fails the regression suite
if (-not $SkipTests) {
  Write-Host "==> running protocol regression tests" -ForegroundColor Cyan
  & $py "$src\run_tests.py" | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "==> ABORT: tests failed. Fix them or re-run with -SkipTests." -ForegroundColor Red
    exit 1
  }
  Write-Host "   tests passed" -ForegroundColor DarkGray
}

# Code/asset files that are always synced. config.json, history.db and logs
# live only in the runtime folder and are intentionally NOT overwritten.
$code = 'activity.py','client.py','dashboard.py','service.py','notify.py','store.py',
        'miniproto.py','spp.py','xcrypto.py','index.dc.html','support.js','svc.vbs',
        'watch_notify_hook.py','watchicon.py','watch_send.py','tray.py','tray.vbs',
        'morning_report.py','sleep_engine.py','demo_state.py','test_sleep_engine.py','run_tests.py',
        'requirements.txt','README.md'

Write-Host "==> stopping running service" -ForegroundColor Cyan
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
  (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like '*RedmiWatchLive*'
} | ForEach-Object { Stop-Process -Id $_.Id -Force }
Start-Sleep -Milliseconds 900

Write-Host "==> syncing source -> $dst" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($f in $code) { if (Test-Path "$src\$f") { Copy-Item "$src\$f" "$dst\$f" -Force } }
if (Test-Path "$src\vendor") { Copy-Item "$src\vendor" $dst -Recurse -Force }

# config.json: only seed it on first deploy; never clobber the user's runtime mode
if (-not (Test-Path "$dst\config.json")) {
  if (Test-Path "$src\config.json") { Copy-Item "$src\config.json" "$dst\config.json" }
  Write-Host "   seeded config.json (first deploy)" -ForegroundColor DarkGray
} else {
  Write-Host "   kept existing runtime config.json (mode etc. preserved)" -ForegroundColor DarkGray
}

Write-Host "==> starting service" -ForegroundColor Cyan
Start-Process wscript.exe -ArgumentList """$dst\svc.vbs"""
Start-Sleep -Seconds 6

try {
  $r = Invoke-WebRequest 'http://127.0.0.1:8765/state' -UseBasicParsing -TimeoutSec 6
  $j = $r.Content | ConvertFrom-Json
  Write-Host ("==> OK  dashboard up  (connected={0} hr={1} mode-port={2})" -f $j.connected,$j.stats.hr_cur,$j.device.port) -ForegroundColor Green
  Write-Host "    http://127.0.0.1:8765"
} catch {
  Write-Host "==> WARN dashboard not answering yet: $($_.Exception.Message)" -ForegroundColor Yellow
  Write-Host "    check log: $dst\service.log"
}
