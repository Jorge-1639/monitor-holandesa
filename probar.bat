@echo off
chcp 65001 >nul
cd /d "%~dp0"
python holandesa_monitor.py --prueba
pause
