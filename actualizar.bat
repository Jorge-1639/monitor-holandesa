@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "C:\MonitorHolandesa\config.json" ( echo  Primero instala con instalar.bat & pause & exit /b 1 )
echo  Actualizando el monitor...
call "%~dp0detener_monitor.bat" silencioso >nul 2>&1
copy /y "%~dp0holandesa_monitor.py" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0panel.html" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0comandero.html" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0logo_comandero.png" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0detener_monitor.bat" "C:\MonitorHolandesa\" >nul
copy /y "%~dp0iniciar_monitor.vbs" "C:\MonitorHolandesa\" >nul
copy /y "C:\MonitorHolandesa\iniciar_monitor.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Monitor La Holandesa.vbs" >nul
start "" wscript "C:\MonitorHolandesa\iniciar_monitor.vbs"
echo.
for /f "tokens=3" %%v in ('findstr /b "VERSION" "C:\MonitorHolandesa\holandesa_monitor.py"') do echo  Listo. Version instalada: %%v
echo  Tu contrasena no cambio.
pause
