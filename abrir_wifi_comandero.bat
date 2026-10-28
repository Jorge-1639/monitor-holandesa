@echo off
chcp 65001 >nul
title Abrir el comandero al WiFi del restaurante
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Este archivo necesita permisos de administrador.
  echo  Cierra esta ventana, da clic derecho en abrir_wifi_comandero.bat
  echo  y elige "Ejecutar como administrador".
  echo.
  pause ^& exit /b 1
)
netsh advfirewall firewall delete rule name="Comandero La Holandesa (WiFi)" >nul 2>&1
netsh advfirewall firewall add rule name="Comandero La Holandesa (WiFi)" dir=in action=allow protocol=TCP localport=8765 remoteip=LocalSubnet profile=any >nul
if errorlevel 1 ( echo  No se pudo abrir el puerto. Avisale a Claude. ^& pause ^& exit /b 1 )
echo.
echo  Listo. Los telefonos conectados al WiFi del restaurante ya pueden abrir el comandero.
echo  Esto se hace una sola vez.
echo.
pause
