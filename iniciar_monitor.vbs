Set f = CreateObject("Scripting.FileSystemObject")
carpeta = "C:\MonitorHolandesa"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = carpeta
sh.Run "pythonw """ & carpeta & "\holandesa_monitor.py""", 0, False
