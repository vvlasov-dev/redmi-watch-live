Set ws = CreateObject("WScript.Shell")
pyw = ws.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
app = ws.ExpandEnvironmentStrings("%LOCALAPPDATA%\RedmiWatchLive\tray.py")
ws.Run """" & pyw & """ """ & app & """", 0, False
