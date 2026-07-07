"""Windows toast notifications with no third-party dependencies.

Uses the WinRT ToastNotificationManager via a short PowerShell snippet, and
falls back to a Forms balloon tip if toasts are unavailable. Non-blocking:
each notification is fired in a detached process so it never stalls the client.
"""
import os
import subprocess
import tempfile
import threading

APP_ID = "Redmi Watch Live"

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
  $tmpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $texts = $tmpl.GetElementsByTagName('text')
  $texts.Item(0).AppendChild($tmpl.CreateTextNode('{title}')) > $null
  $texts.Item(1).AppendChild($tmpl.CreateTextNode('{message}')) > $null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($tmpl)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app}').Show($toast)
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  $ni = New-Object System.Windows.Forms.NotifyIcon
  $ni.Icon = [System.Drawing.SystemIcons]::Information
  $ni.Visible = $true
  $ni.ShowBalloonTip(6000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::Info)
  Start-Sleep -Milliseconds 7000
  $ni.Dispose()
}}
"""


def _esc(s: str) -> str:
    # single-quote escaping for PowerShell literals
    return str(s).replace("'", "''").replace("\r", " ").replace("\n", " ")


def toast(title: str, message: str = "") -> None:
    ps = _TOAST_PS.format(title=_esc(title), message=_esc(message), app=_esc(APP_ID))

    def _run():
        path = None
        try:
            # Write the script as UTF-8 *with BOM* and run via -File so PowerShell
            # reads Cyrillic correctly (passing non-ASCII on argv mangles it via the
            # OEM codepage). Self-deletes at the end of the script.
            fd, path = tempfile.mkstemp(suffix=".ps1", prefix="rwtoast_")
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(ps + "\nRemove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n")
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    threading.Thread(target=_run, daemon=True).start()
