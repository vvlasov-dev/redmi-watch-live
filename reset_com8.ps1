# Reset the wedged Bluetooth SPP COM port for the Redmi Watch 5 Active.
# The RFCOMM/SPP data channel can hang (semaphore-timeout on open) while the
# watch still shows "OK" in Bluetooth. Cycling the COM device clears it.
# Requires ADMIN. Run:  Start-Process powershell -Verb RunAs -File reset_com8.ps1
$ErrorActionPreference = 'Stop'
$d = Get-PnpDevice | Where-Object { $_.FriendlyName -like '*COM8*' }
if (-not $d) { Write-Host 'COM8 device not found — is the watch paired?'; Start-Sleep 4; exit 1 }
Write-Host "Cycling: $($d.FriendlyName)"
try {
  Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false
  Start-Sleep -Seconds 2
  Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false
  Start-Sleep -Seconds 3
  Write-Host "Status now: $((Get-PnpDevice -InstanceId $d.InstanceId).Status)"
  Write-Host 'Done. The watch service should reconnect within ~10s.'
} catch {
  Write-Host ("Failed (need admin?): " + $_.Exception.Message)
}
Start-Sleep 4