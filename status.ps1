# Show whether the running deploy matches the source, and service health.
# Usage:  powershell -ExecutionPolicy Bypass -File status.ps1
$src = $PSScriptRoot
$dst = 'C:\Users\L5DKA\AppData\Local\RedmiWatchLive'
$code = 'activity.py','client.py','dashboard.py','service.py','notify.py','store.py',
        'miniproto.py','spp.py','xcrypto.py','index.dc.html','support.js','svc.vbs'

Write-Host "=== source vs runtime drift ===" -ForegroundColor Cyan
$drift = 0
foreach ($f in $code) {
  $a = if (Test-Path "$src\$f") { (Get-FileHash "$src\$f" -Algorithm MD5).Hash } else { 'MISSING' }
  $b = if (Test-Path "$dst\$f") { (Get-FileHash "$dst\$f" -Algorithm MD5).Hash } else { 'MISSING' }
  if ($a -ne $b) { $drift++; Write-Host ("  DIFF  {0}" -f $f) -ForegroundColor Yellow }
}
if ($drift -eq 0) { Write-Host "  clean: runtime matches source" -ForegroundColor Green }
else { Write-Host ("  {0} file(s) differ -> run deploy.ps1" -f $drift) -ForegroundColor Yellow }

Write-Host "`n=== process / port ===" -ForegroundColor Cyan
$p = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
  (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like '*RedmiWatchLive*'
}
if ($p) { Write-Host ("  service PID {0} running" -f $p.Id) -ForegroundColor Green }
else { Write-Host "  service NOT running -> run deploy.ps1" -ForegroundColor Yellow }

try {
  $j = (Invoke-WebRequest 'http://127.0.0.1:8765/state' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
  $ago = $j.now - $j.last_sync
  Write-Host ("  dashboard OK  connected={0} on_wrist={1} hr={2} battery={3}% last_sync={4}s ago" -f `
    $j.connected,$j.on_wrist,$j.stats.hr_cur,$j.battery.level,$ago) -ForegroundColor Green
} catch { Write-Host "  dashboard not answering" -ForegroundColor Yellow }
