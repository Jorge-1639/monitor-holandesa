# monitor-holandesa

Programa que lee las ventas de MrTienda en la computadora de la caja de Restaurante La Holandesa
(solo lectura) y las muestra en el teléfono del dueño.

Aquí solo está el código del programa. Las ventas, contraseñas y la configuración se quedan en la caja
(`C:\MonitorHolandesa\config.json`) y nunca se suben a este repositorio.

La caja revisa cada 5 minutos si `version.txt` es mayor que su versión; si lo es, descarga los archivos
de `archivos.txt`, los revisa y se reinicia sola. La versión anterior queda en `_respaldo\programa`.

Las cuentas abiertas que se quitan desde el panel (familia, Didi, Uber, errores) quedan registradas en
`cuentas_quitadas.json` y sus respaldos en `respaldos_cuentas\`, ambos en la caja; nunca se suben aquí.
Los gastos y compras capturados (por Jorge o desde la página del cajero, `/captura`) quedan en `gastos.json`
también solo en la caja. Las fotos de tickets NO se guardan en la computadora: se leen y, si Google Drive está conectado,
se suben directo a la carpeta "Fotos de tickets - La Holandesa" del Drive de Jorge (con un Apps Script propio), de donde se pueden borrar todas. Los cajeros entran a `/captura` con su clave (se guarda solo la huella en `cajeros.json`). La llave de la API de Claude
para leer tickets se guarda en config.json; nunca se sube aquí.

Comandero (`/comandero`, versión 26.0): los meseros entran con una clave de 4 números que Jorge asigna en Reportes → Meseros
(se guarda solo la huella en `meseros.json`, en la caja). Ligado al código de vendedor de MrTienda. Lee menú táctil, precios Comedor/Recoger aquí, variantes y cuentas abiertas.

Versión 26.1: si Jorge enciende "Enviar pedidos a MrTienda" (config `comandero_escribe`), el comandero abre cuentas o agrega platillos
replicando exactamente lo que hace MrTienda (PENDIENT.DBF, PDxxxxxx.DBF/.BAK/.ENC y FOL_CTA_P en FOLIOS.DBF) e imprime la comanda
de cocina (puerto 1) en la impresora `impresora_cocina` (por omisión EPSON TM-T88V ReceiptE4). Antes de cada envío respalda los
archivos en `respaldos_comandero\` (en la caja). Platillos con opciones de costo extra todavía se capturan en la caja.
