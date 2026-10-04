@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*holandesa_monitor*' } | Invoke-CimMethod -MethodName Terminate | Out-Null"
if not "%1"=="silencioso" ( echo Monitor detenido. & pause )
