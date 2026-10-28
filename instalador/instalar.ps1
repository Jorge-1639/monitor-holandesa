# Instalador del Panel de Ventas (La Holandesa) — solo el panel de ventas, copia fija.
# Lo abre INSTALAR.bat. Funciona con la PowerShell que ya trae Windows 10 y 11.
param(
  [switch]$Prueba,                                  # solo para probarlo fuera de Windows
  [string]$Destino = 'C:\MonitorHolandesa',
  [string]$BuscarEn = ''
)
$ErrorActionPreference = 'Continue'   # los errores se revisan uno por uno (con 'Stop', Windows PowerShell corta al primer aviso de Python)
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$Programa = Join-Path $Aqui 'programa'
$Puerto = 8765
$Total = 8
$PyUrl = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
$Temp = if ($env:TEMP) { $env:TEMP } else { [IO.Path]::GetTempPath() }
$Log = Join-Path $Temp 'instalacion_panel_ventas.log'
try { Start-Transcript -Path $Log -Force | Out-Null } catch {}

function Titulo {
  Clear-Host
  Write-Host ''
  Write-Host '  =============================================' -ForegroundColor DarkYellow
  Write-Host '     PANEL DE VENTAS  -  Instalación' -ForegroundColor Yellow
  Write-Host '  =============================================' -ForegroundColor DarkYellow
  Write-Host '  Sigue las instrucciones. Si algo sale en rojo, tómale foto y mándasela a Jorge.'
}
function Paso($n, $t) { Write-Host ''; Write-Host "  [$n de $Total] $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "        OK  $t" -ForegroundColor Green }
function Info($t) { Write-Host "            $t" }
function Aviso($t) { Write-Host "        !!  $t" -ForegroundColor Yellow }
function Alto($t) {
  Write-Host ''
  Write-Host "        XX  $t" -ForegroundColor Red
  Write-Host ''
  Write-Host '  La instalación NO terminó. Tómale foto a esta pantalla y mándasela a Jorge.' -ForegroundColor Red
  Write-Host "  (Registro: $Log)"
  try { Stop-Transcript | Out-Null } catch {}
  Read-Host '  Presiona Enter para cerrar' | Out-Null
  exit 1
}
function Pregunta($t, $def) {
  $r = Read-Host "        $t"
  if ([string]::IsNullOrWhiteSpace($r)) { return $def }
  return $r.Trim()
}
function SiNo($t) {
  while ($true) {
    $r = (Read-Host "        $t (S/N)").Trim().ToUpper()
    if ($r -eq 'S' -or $r -eq 'SI' -or $r -eq 'SÍ') { return $true }
    if ($r -eq 'N' -or $r -eq 'NO') { return $false }
  }
}

# ---------------------------------------------------------------- Python
function Probar-Python($exe, $pre) {
  try {
    if ($exe -like '*WindowsApps*') { return $null }    # el acceso directo de la Tienda de Windows no es Python
    $args_ = @()
    if ($pre) { $args_ += $pre }
    $args_ += @('-c', 'import sys;print(sys.executable);print(sys.version_info[0]*100+sys.version_info[1])')
    $out = & $exe @args_ 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $L = @($out)
    if ($L.Count -lt 2) { return $null }
    $ruta = $L[0].Trim(); $ver = [int]$L[1].Trim()
    if ($ver -lt 308) { return $null }
    if ($ruta -like '*WindowsApps*') { return $null }
    return $ruta
  } catch { return $null }
}
function Buscar-Python {
  if ($Prueba) { return (Probar-Python 'python3' $null) }
  $p = Probar-Python 'py' '-3'; if ($p) { return $p }
  $p = Probar-Python 'python' $null; if ($p) { return $p }
  $cands = @()
  foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs\Python'), 'C:\')) {
    if ($base -and (Test-Path $base)) { $cands += Get-ChildItem -Path $base -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending }
  }
  foreach ($d in $cands) {
    $exe = Join-Path $d.FullName 'python.exe'
    if (Test-Path $exe) { $p = Probar-Python $exe $null; if ($p) { return $p } }
  }
  return $null
}

# ---------------------------------------------------------------- MrTienda
function Es-MrTienda($d) {
  return ((Test-Path (Join-Path $d 'REGISTRO')) -and (Test-Path (Join-Path $d 'COMUNES')))
}
function Buscar-MrTienda {
  $raices = @()
  if ($BuscarEn) { $raices = @($BuscarEn) }
  else { $raices = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Used -gt 0 } | ForEach-Object { $_.Root } }
  $salta = @('Windows', '$Recycle.Bin', 'System Volume Information', 'ProgramData', 'Recovery', 'PerfLogs', 'Users', 'MonitorHolandesa')
  $hall = @()
  foreach ($r in $raices) {
    $n1 = Get-ChildItem -Path $r -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $salta -notcontains $_.Name }
    foreach ($a in $n1) {
      if (Es-MrTienda $a.FullName) { $hall += $a.FullName; continue }
      $n2 = Get-ChildItem -Path $a.FullName -Directory -Force -ErrorAction SilentlyContinue
      foreach ($b in $n2) { if (Es-MrTienda $b.FullName) { $hall += $b.FullName } }
    }
  }
  return @($hall | Select-Object -Unique)
}

# ================================================================= inicio
Titulo
Info 'Este instalador pone el panel de ventas en esta computadora (la de la caja, donde está MrTienda).'
Info 'Tarda de 3 a 10 minutos. Necesita internet solo si hay que instalar Python.'

# ---- 1. permisos
Paso 1 'Revisando permisos de administrador'
if (-not $Prueba) {
  $adm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $adm) { Alto 'No tengo permisos de administrador. Cierra esta ventana y abre INSTALAR.bat otra vez; cuando Windows pregunte "¿Quieres permitir...?", elige Sí.' }
}
if (-not (Test-Path (Join-Path $Programa 'holandesa_monitor.py'))) {
  Alto 'No encuentro la carpeta "programa" junto a este instalador. Primero extrae TODO el archivo ZIP (clic derecho > Extraer todo) y abre INSTALAR.bat desde la carpeta extraída.'
}
Ok 'Permisos correctos'

# ---- 2. Python
Paso 2 'Revisando Python (el programa lo necesita)'
$py = Buscar-Python
if ($py) { Ok "Python ya está instalado: $py" }
else {
  Aviso 'Python no está instalado. Lo voy a descargar e instalar (unos 25 MB).'
  $tmp = Join-Path $Temp 'python-instalador.exe'
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Info 'Descargando Python... (puede tardar unos minutos)'
    Invoke-WebRequest -Uri $PyUrl -OutFile $tmp -UseBasicParsing -ErrorAction Stop
  } catch {
    Alto ('No pude descargar Python. Revisa que la computadora tenga internet y vuelve a abrir INSTALAR.bat. Detalle: ' + $_.Exception.Message)
  }
  Info 'Instalando Python... (no cierres esta ventana)'
  $pr = Start-Process -FilePath $tmp -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0' -Wait -PassThru
  if ($pr.ExitCode -ne 0) { Alto "El instalador de Python terminó con error $($pr.ExitCode). Instálalo a mano desde python.org (marca 'Add python.exe to PATH') y vuelve a abrir INSTALAR.bat." }
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
  $py = Buscar-Python
  if (-not $py) { Alto 'Instalé Python pero no lo encuentro. Reinicia la computadora y vuelve a abrir INSTALAR.bat.' }
  Ok "Python instalado: $py"
}
$pyw = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
if (-not $Prueba -and -not (Test-Path $pyw)) { $pyw = $py }

# ---- 3. MrTienda
Paso 3 'Buscando la carpeta de MrTienda'
Info 'Buscando en los discos de esta computadora...'
$hall = @(Buscar-MrTienda)
$mrt = $null
if ($hall.Count -eq 1) {
  Ok "Encontré MrTienda en: $($hall[0])"
  if (SiNo '¿Es la carpeta correcta?') { $mrt = $hall[0] }
} elseif ($hall.Count -gt 1) {
  Info 'Encontré varias carpetas de MrTienda:'
  for ($i = 0; $i -lt $hall.Count; $i++) { Info ("   " + ($i + 1) + ") " + $hall[$i]) }
  while (-not $mrt) {
    $r = Pregunta "Escribe el número de la que usa la caja (1 a $($hall.Count))" ''
    $k = 0
    if ([int]::TryParse($r, [ref]$k) -and $k -ge 1 -and $k -le $hall.Count) { $mrt = $hall[$k - 1] }
  }
} else {
  Aviso 'No encontré MrTienda solo.'
}
while (-not $mrt) {
  Info 'Escribe dónde está la carpeta de MrTienda (la que tiene adentro las carpetas REGISTRO y COMUNES).'
  Info 'Ejemplo: C:\MRHOLA   (si no sabes, abre MrTienda > clic derecho en su ícono > Abrir ubicación del archivo)'
  $r = Pregunta 'Carpeta' ''
  if ($r -and (Es-MrTienda $r)) { $mrt = (Resolve-Path $r).Path; Ok "Carpeta correcta: $mrt" }
  elseif ($r) { Aviso "En $r no están las carpetas REGISTRO y COMUNES. Intenta otra vez." }
}

# ---- 4. instalación anterior
Paso 4 'Revisando si ya hay una instalación'
$cfgFile = Join-Path $Destino 'config.json'
$conservar = $false
if (Test-Path $cfgFile) {
  Aviso "Ya hay un panel instalado en $Destino."
  if (SiNo '¿Lo actualizo conservando su usuario, contraseña y datos?') { $conservar = $true }
  else { Alto 'Cancelado. No se cambió nada.' }
  if (-not $Prueba) {
    Info 'Deteniendo el programa anterior...'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*holandesa_monitor*' } | Invoke-CimMethod -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
  }
  Ok 'Listo para actualizar'
} else { Ok 'Instalación nueva' }

# ---- 5. copiar
Paso 5 "Copiando el programa a $Destino"
try {
  New-Item -ItemType Directory -Force -Path $Destino -ErrorAction Stop | Out-Null
  Copy-Item -Path (Join-Path $Programa '*') -Destination $Destino -Recurse -Force -ErrorAction Stop
  if (-not $Prueba) {
    Get-ChildItem $Destino -File | Unblock-File -ErrorAction SilentlyContinue
    & icacls $Destino /grant '*S-1-5-32-545:(OI)(CI)M' /T /Q | Out-Null    # que el programa pueda escribir aunque el usuario no sea administrador
  }
} catch { Alto ('No pude copiar los archivos: ' + $_.Exception.Message) }
Ok ((Get-ChildItem $Destino -File).Count.ToString() + ' archivos copiados')

# ---- 6. usuario y contraseña
Paso 6 'Usuario y contraseña para entrar al panel'
$cfg = @{}
if ($conservar) {
  try { $old = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json; foreach ($p in $old.PSObject.Properties) { $cfg[$p.Name] = $p.Value } } catch { Alto 'El config.json anterior está dañado. Avísale a Jorge.' }
  Ok "Se conservan el usuario ($($cfg['usuario'])) y la contraseña de antes"
} else {
  $u = ''
  while ($u -notmatch '^[A-Za-z0-9._-]{3,20}$') {
    $u = Pregunta 'Escribe el usuario (sin espacios ni acentos, ej. admin) y Enter' 'admin'
    if ($u -notmatch '^[A-Za-z0-9._-]{3,20}$') { Aviso 'Usa de 3 a 20 letras o números, sin espacios ni acentos.' }
  }
  $c = ''
  while ($true) {
    $c = Pregunta 'Escribe la contraseña (mínimo 6, sin espacios) y Enter' ''
    if ($c.Length -lt 6 -or $c -match '\s') { Aviso 'Mínimo 6 caracteres y sin espacios.'; continue }
    Info "Usuario: $u    Contraseña: $c"
    if (SiNo '¿Están bien? Anótalos en un papel') { break }
  }
  $cfg['usuario'] = $u; $cfg['contrasena'] = $c
}
$cfg['carpeta_mrtienda'] = $mrt
$cfg['puerto'] = $Puerto
$cfg['actualizar_solo'] = $false          # copia fija: no se actualiza sola
$cfg['https_tailscale'] = $false          # sin Tailscale
$cfg['modulos'] = @{ comandero = $false; personal = $false; compras = $false; plataformas = $false }   # solo el panel de ventas
if (-not $cfg.ContainsKey('minutos_actualizacion')) { $cfg['minutos_actualizacion'] = 2 }
if (-not $cfg.ContainsKey('dias_detalle')) { $cfg['dias_detalle'] = 70 }
try {
  $json = $cfg | ConvertTo-Json -Depth 6
  [IO.File]::WriteAllText($cfgFile, $json, (New-Object Text.UTF8Encoding $false))
} catch { Alto ('No pude guardar la configuración: ' + $_.Exception.Message) }
Ok 'Configuración guardada'

# ---- 7. WiFi (firewall)
Paso 7 'Abriendo el panel al WiFi del negocio (para verlo en el celular)'
if (-not $Prueba) {
  & netsh advfirewall firewall delete rule name="Panel de ventas (WiFi)" | Out-Null
  & netsh advfirewall firewall add rule name="Panel de ventas (WiFi)" dir=in action=allow protocol=TCP localport=$Puerto remoteip=LocalSubnet profile=any | Out-Null
  if ($LASTEXITCODE -ne 0) { Aviso 'No pude abrir el puerto del firewall. El panel funcionará en esta computadora, pero quizá no en el celular.' }
  else { Ok 'Listo: los celulares conectados al WiFi del negocio pueden abrir el panel' }
  # Dirección fija: si el internet o el módem se reinician, la caja conserva su dirección y los celulares no dejan de abrir el panel
  try {
    $cfgIp = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
    if (-not $cfgIp) { throw 'no encontré la conexión al módem' }
    $alias = $cfgIp.InterfaceAlias; $ipAct = $cfgIp.IPv4Address[0].IPAddress; $gw = $cfgIp.IPv4DefaultGateway[0].NextHop
    if ($cfgIp.IPv4Address[0].PrefixLength -ne 24) { throw 'la red del módem no es la común' }
    $base = ($ipAct -split '\.')[0..2] -join '.'; $nueva = $null
    foreach ($n in 250..240) { $c = "$base.$n"; if ($c -eq $ipAct -or -not (Test-Connection $c -Count 1 -Quiet -ErrorAction SilentlyContinue)) { $nueva = $c; break } }
    if (-not $nueva) { throw 'no hay dirección libre' }
    & netsh interface ipv4 set address name="$alias" static $nueva 255.255.255.0 $gw | Out-Null
    & netsh interface ipv4 set dnsservers name="$alias" static $gw primary validate=no | Out-Null
    & netsh interface ipv4 add dnsservers name="$alias" 8.8.8.8 index=2 validate=no | Out-Null
    Start-Sleep 8
    if (-not (Test-Connection 8.8.8.8 -Count 2 -Quiet -ErrorAction SilentlyContinue)) { Start-Sleep 7 }
    if (-not (Test-Connection 8.8.8.8 -Count 2 -Quiet -ErrorAction SilentlyContinue)) {
      & netsh interface ipv4 set address name="$alias" source=dhcp | Out-Null
      & netsh interface ipv4 set dnsservers name="$alias" source=dhcp | Out-Null
      Start-Sleep 8; throw 'con dirección fija no hubo internet; la dejé automática'
    }
    Ok "Dirección fija de esta computadora: $nueva (ya no cambia aunque se vaya el internet)"
  } catch { Aviso ('No dejé fija la dirección (' + $_ + '). Si un día el celular deja de abrir el panel, ejecuta fijar_ip_caja.bat como administrador.') }
} else { Ok '(prueba: se omite el firewall y la dirección fija)' }

# ---- 8. arranque automático y prueba
Paso 8 'Probando que lea las ventas y dejando que arranque solo'
Push-Location $Destino
$salida = & $py (Join-Path $Destino 'holandesa_monitor.py') --prueba 2>&1 | Out-String
$ok = $LASTEXITCODE -eq 0
Pop-Location
if (-not $ok) { Alto ('No pude leer las ventas de MrTienda. Detalle: ' + ((($salida.Trim() -split "`n") | Select-Object -Last 3) -join ' ')) }
Ok ('Lee bien las ventas: ' + ($salida.Trim() -split "`n" | Select-Object -Last 1))
if (-not $Prueba) {
  $vbs = Join-Path $Destino 'iniciar_monitor.vbs'
  $txt = "Set sh = CreateObject(""WScript.Shell"")`r`nsh.CurrentDirectory = ""$Destino""`r`nsh.Run """"""$pyw"""" """"$Destino\holandesa_monitor.py"""""", 0, False`r`n"
  [IO.File]::WriteAllText($vbs, $txt, [Text.Encoding]::Unicode)   # UTF-16: Windows lo lee bien aunque la carpeta de Python tenga ñ o acentos
  $inicio = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\StartUp\Panel de ventas.vbs'
  Copy-Item $vbs $inicio -Force
  Ok 'Arranca solo cada vez que se prende la computadora'
  try {   # acceso directo en el escritorio
    $url = "[InternetShortcut]`r`nURL=http://localhost:$Puerto/panel`r`n"
    [IO.File]::WriteAllText((Join-Path $env:PUBLIC 'Desktop\Panel de ventas.url'), $url, [Text.Encoding]::ASCII)
    Ok 'Puse el acceso directo "Panel de ventas" en el escritorio'
  } catch {}
  Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $vbs + '"')
} else {
  Start-Process -FilePath $py -ArgumentList @((Join-Path $Destino 'holandesa_monitor.py')) -WorkingDirectory $Destino
}
Info 'Arrancando el panel...'
$arriba = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 1
  try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/panel" -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $arriba = $true; break } } catch {}
}
if (-not $arriba) { Alto "El panel no arrancó. Revisa el archivo $Destino\monitor.log o mándale foto a Jorge." }
Ok 'El panel está funcionando'

$ip = $null
try {
  $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } | Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress
} catch {}
$cfgF = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host ''
Write-Host '  =============================================' -ForegroundColor Green
Write-Host '     LISTO. El panel de ventas quedó instalado.' -ForegroundColor Green
Write-Host '  =============================================' -ForegroundColor Green
Write-Host ''
Write-Host '  En esta computadora abre:   ' -NoNewline; Write-Host "http://localhost:$Puerto/panel" -ForegroundColor Yellow
if ($ip) { Write-Host '  En el celular (conectado al WiFi del negocio):   ' -NoNewline; Write-Host "http://${ip}:$Puerto/panel" -ForegroundColor Yellow }
Write-Host ''
Write-Host "  Usuario:     $($cfgF.usuario)"
Write-Host "  Contraseña:  $($cfgF.contrasena)"
Write-Host ''
Write-Host '  Tómale foto a esta pantalla para no perder estos datos.'
Write-Host '  El panel arranca solo cada vez que se prende esta computadora.'
$resumen = "PANEL DE VENTAS`r`nEn esta computadora: http://localhost:$Puerto/panel`r`n" + $(if ($ip) { "En el celular (WiFi del negocio): http://${ip}:$Puerto/panel`r`n" } else { '' }) + "Usuario: $($cfgF.usuario)`r`n(La contraseña está en la foto que tomaste o en $cfgFile)`r`n"
try { [IO.File]::WriteAllText((Join-Path $Destino 'COMO ENTRAR.txt'), $resumen, (New-Object Text.UTF8Encoding $true)) } catch {}
if (-not $Prueba) { Start-Process "http://localhost:$Puerto/panel" }
try { Stop-Transcript | Out-Null } catch {}
Write-Host ''
Read-Host '  Presiona Enter para cerrar' | Out-Null
