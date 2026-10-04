# monitor-holandesa

Programa que lee las ventas de MrTienda en la computadora de la caja de Restaurante La Holandesa
(solo lectura) y las muestra en el teléfono del dueño.

Aquí solo está el código del programa. Las ventas, contraseñas y la configuración se quedan en la caja
(`C:\MonitorHolandesa\config.json`) y nunca se suben a este repositorio.

La caja revisa cada noche (3:30 am) y al encender si `version.txt` es mayor que su versión; si lo es,
descarga los archivos de `archivos.txt`, los revisa y se reinicia sola.
