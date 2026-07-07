' Launches the Redmi Watch Live service hidden (no console window).
Set sh = CreateObject("WScript.Shell")
appdir = "C:\Users\L5DKA\AppData\Local\RedmiWatchLive"
pyw = "C:\Users\L5DKA\AppData\Local\Programs\Python\Python312\pythonw.exe"
sh.CurrentDirectory = appdir
sh.Run """" & pyw & """ """ & appdir & "\service.py""", 0, False
