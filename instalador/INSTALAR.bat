@echo off
chcp 65001 >nul
title Instalar Panel de Ventas
cd /d "%~dp0"
if not exist "%~dp0instalar.ps1" (
  echo.
  echo  Falta el archivo instalar.ps1.
  echo  Primero extrae TODO el ZIP: clic derecho en el ZIP, "Extraer todo", y abre INSTALAR desde la carpeta nueva.
  echo.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo  Pidiendo permisos de administrador... elige "Si" en la ventana de Windows.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
