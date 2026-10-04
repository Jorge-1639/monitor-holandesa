@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*holandesa_monitor*' } | Invoke-CimMethod -MethodName Terminate | Out-Null"
timeout /t 2 /nobreak >nul
if not "%1"=="silencioso" ( echo Monitor detenido. & pause )
