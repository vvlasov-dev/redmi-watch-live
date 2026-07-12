' Launches the Redmi Watch Live service hidden (no console window).
Set sh = CreateObject("WScript.Shell")
appdir = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\RedmiWatchLive")
pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
sh.CurrentDirectory = appdir
sh.Run """" & pyw & """ """ & appdir & "\service.py""", 0, False
