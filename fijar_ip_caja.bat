<# : fijar_ip_caja.bat  (parte de Windows; la parte de abajo es PowerShell)
@echo off
set "OPCION=%~1"
set "CARPETA=%~dp0"
title Fijar la direccion WiFi de la caja
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Este archivo necesita permisos de administrador.
  echo  Cierra esta ventana, da clic derecho en fijar_ip_caja.bat
  echo  y elige "Ejecutar como administrador".
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((Get-Content -LiteralPath '%~f0' -Raw))"
echo.
if defined OPCION ( timeout /t 20 ) else ( pause )
exit /b
#>
$ErrorActionPreference = 'Continue'
$res = Join-Path $env:CARPETA 'fijar_ip_resultado.txt'
function Linea($t) { Write-Host "  $t"; try { Add-Content -LiteralPath $res -Value $t } catch {} }
function Internet { (Test-Connection 8.8.8.8 -Count 2 -Quiet -ErrorAction SilentlyContinue) }
function Automatico($alias) {
  netsh interface ipv4 set address name="$alias" source=dhcp | Out-Null
  netsh interface ipv4 set dnsservers name="$alias" source=dhcp | Out-Null
}
try {
$cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
if (-not $cfg) { Linea 'No encontre la conexion al modem. Revisa que la caja tenga internet y vuelve a intentar.'; return }
$alias = $cfg.InterfaceAlias
$ip    = $cfg.IPv4Address[0].IPAddress
$pref  = $cfg.IPv4Address[0].PrefixLength
$gw    = $cfg.IPv4DefaultGateway[0].NextHop
Write-Host ''
Linea "Conexion: $alias"
Linea "Direccion actual de la caja: $ip   (modem: $gw)"
Write-Host ''
Linea '1 = Dejar la direccion FIJA (ya no cambia aunque se vaya el internet)'
Linea '2 = Regresar a como estaba (automatica)'
$op = $env:OPCION
if (-not $op) { $op = Read-Host '  Escribe 1 o 2 y da Enter' }
if ($op -eq '2') {
  Automatico $alias; Start-Sleep 8
  Linea 'Listo. La direccion vuelve a ser automatica.'; return
}
if ($op -ne '1') { Linea 'No se hizo ningun cambio.'; return }
if ($pref -ne 24) { Linea "La red del modem es distinta a la normal (/$pref). No se hizo ningun cambio. Avisale a Claude."; return }
$base = ($ip -split '\.')[0..2] -join '.'
$nueva = $null
foreach ($n in 250..240) {
  $c = "$base.$n"
  if ($c -eq $ip) { $nueva = $c; break }
  if (-not (Test-Connection $c -Count 1 -Quiet -ErrorAction SilentlyContinue)) { $nueva = $c; break }
}
if (-not $nueva) { Linea 'No encontre una direccion libre. No se hizo ningun cambio. Avisale a Claude.'; return }
Linea "Fijando la direccion $nueva ..."
netsh interface ipv4 set address name="$alias" static $nueva 255.255.255.0 $gw | Out-Null
netsh interface ipv4 set dnsservers name="$alias" static $gw primary validate=no | Out-Null
netsh interface ipv4 add dnsservers name="$alias" 8.8.8.8 index=2 validate=no | Out-Null
Start-Sleep 8
if (-not (Internet)) {
  Start-Sleep 7
  if (-not (Internet)) {
    Linea 'Con la direccion fija no hubo internet. Regrese todo a como estaba.'
    Automatico $alias; Start-Sleep 8
    Linea 'No se hizo ningun cambio. Avisale a Claude.'; return
  }
}
Write-Host ''
Linea '======================================================'
Linea "  LISTO. La caja ya tiene direccion fija: $nueva"
Linea "  Comandero de los meseros:  http://$nueva`:8765/comandero"
Linea "  Descansos (Berenice):      http://$nueva`:8765/descansos"
Linea '======================================================'
Linea 'Manda el PDF otra vez a los meseros y a Berenice. Sera la ULTIMA vez.'
} finally { try { Add-Content -LiteralPath $res -Value 'FIN' } catch {} }
