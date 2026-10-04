@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "C:\MonitorHolandesa\config.json" ( echo  Primero instala con instalar.bat & pause & exit /b 1 )
echo  Actualizando el monitor...
call "C:\MonitorHolandesa\detener_monitor.bat" silencioso >nul 2>&1
copy /y "%~dp0holandesa_monitor.py" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0panel.html" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0detener_monitor.bat" "C:\MonitorHolandesa\" >nul
start "" wscript "C:\MonitorHolandesa\iniciar_monitor.vbs"
echo.
echo  Listo. El monitor ya tiene la version nueva. Tu contrasena no cambio.
pause
