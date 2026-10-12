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

Comandero (`/comandero`, versión 26.0): los meseros entran con una clave de 4 números que Jorge asigna en la pestaña COMANDERO
(se guarda solo la huella en `meseros.json`, en la caja). Ligado al código de vendedor de MrTienda. Lee menú táctil, precios Comedor/Recoger aquí, variantes y cuentas abiertas.

Versión 26.1: si Jorge enciende "Enviar pedidos a MrTienda" (config `comandero_escribe`), el comandero abre cuentas o agrega platillos
replicando exactamente lo que hace MrTienda (PENDIENT.DBF, PDxxxxxx.DBF/.BAK/.ENC y FOL_CTA_P en FOLIOS.DBF) e imprime la comanda
de cocina (puerto 1) en la impresora `impresora_cocina` (por omisión EPSON TM-T88V ReceiptE4). Antes de cada envío respalda los
archivos en `respaldos_comandero\` (en la caja). Platillos con opciones de costo extra todavía se capturan en la caja.

Recursos Humanos (pestaña Personal, versión 27.0): catálogo de empleados con expediente y estado del contrato, rol de descansos semanal,
nómina (sueldo semanal menos faltas, comida, vales, abono de préstamo, transferencia/efectivo y recibo por WhatsApp), vales y préstamos con saldo,
vacaciones, prima vacacional y aguinaldo según la LFT. Los datos (incluye NSS, CURP, RFC) se guardan solo en la caja en `personal.json`,
con una copia diaria en `respaldos_personal\`; nunca van en data.json, en la copia de Drive ni en este repositorio.

Versión 27.2 — candado del rol: si Jorge enciende "Revisar rol de descansos" (Reportes → Meseros, config `candado_rol`), el mesero
ligado a un empleado de Personal (código de MrTienda) solo entra al comandero el día que le toca trabajar según el rol de la semana
y dentro de su horario (30 min antes de entrar y hasta 60 min después de salir; config `candado_antes_min` y `candado_despues_min`).
Descanso, falta, vacaciones o incapacidad = no entra. Quien no está ligado en Personal no tiene este candado.

Versión 27.3 — PDF de bienvenida: en Reportes → Meseros, "Mandar PDF" (o al dar de alta / cambiar clave) descarga un PDF con el nombre del mesero,
la dirección del comandero en el WiFi del restaurante (se toca para abrir), los pasos para entrar y dejarlo como app, y sus reglas (su horario
si está ligado en Personal). Se genera en la caja sin paquetes extra (`/api/comandero-pdf?cod=`, solo con la clave del panel). La clave nunca va en el PDF.

Versión 27.4 — "Enviar PDF": el panel abre el menú de compartir del teléfono con "Comandero <Nombre>.pdf" ya cargado (se elige WhatsApp y la persona).
El teléfono solo permite compartir archivos desde una dirección segura, así que al arrancar la caja activa `tailscale serve` y el panel queda también en
https://<caja>.<red>.ts.net (requiere MagicDNS y HTTPS Certificates encendidos en Tailscale; se apaga con `"https_tailscale": false` en config.json).
Lo que llega por esa dirección cuenta como Tailscale, nunca como WiFi del restaurante.

Versión 27.6 — todo lo del comandero pasó de Reportes → Meseros a su propia pestaña COMANDERO en la barra de abajo (no aparece en la copia de Drive).
Cada mesero muestra su clave tapada (••••) con un ojito para verla 15 segundos. La clave se guarda en `meseros.json` en la caja (nunca en data.json
ni en Drive); las que se dieron antes de esta versión se aprenden la próxima vez que el mesero entra, o al cambiarla.

Versión 28.0 — pedidos para llevar por canal: en el comandero, "Pedidos para llevar" tiene 4 botones: Llevar (nombre de quien recoge), Domicilio
(busca al cliente por teléfono en el catálogo de clientes de MrTienda, solo lectura; si no está, nombre y teléfono), Didi (clave del pedido y efectivo/tarjeta)
y Uber (clave del pedido). Todos con precios de llevar. En MrTienda la referencia queda con el nombre o "DIDI 4821"/"UBER 4821"; el canal, la clave, el pago
y el teléfono se guardan en `comandero_pedidos.json` en la caja. La comanda de cocina sale con el canal y el nombre o pedido en grande, y abajo PARA LLEVAR/COMER AQUI
en grande. Al quitar una cuenta de Didi o Uber en el panel ya viene marcado el motivo (efectivo o tarjeta) y la clave en la nota. Todavía no da de alta clientes en MrTienda.

Versión 28.1 — comanda por canal: abajo, en grande, "DIDI 4821" / "UBER 4821", o "RECOGER AQUI" / "SERVICIO A DOMICILIO" con el nombre de la persona debajo.
No imprime efectivo/tarjeta (se sigue preguntando en Didi y se guarda para el panel) ni el teléfono. Datos de domicilio (calles, ubicación, cambio): pendiente, con el formato de Jorge.

Versión 28.2 — Personal (/personal) con contorno azul claro en todas las tarjetas.

Versión 28.3 — expediente: sueldo diario, prima dominical (25% del diario si trabaja domingo), incentivo semanal prorrateado por días trabajados,
horario por día (entra/sale, descansa) y minutos de comida que no se pagan. Contratos: botón de cámara que primero pregunta si es por tiempo
determinado o indeterminado; foto, PDF o Word se guardan en la caja en `expedientes\<empleado>\` (nunca en el repositorio). Rol de descansos con
horas por día que Jorge y la encargada (/descansos) pueden cambiar. Orden de la lista: meseros, cocineros, ayudantes generales.

Versión 28.4 — horario del expediente más compacto para que en el teléfono se vea completa la hora con a.m./p.m.

Versión 29.0 — Personal: la encargada (/descansos) ve el rol en una sola tabla como el panel (sin sueldos). Expediente: se paga por día o por hora;
casilla "Restarle la comida (medio turno)". Nómina: horas del rol de cada quien y casilla para restar la comida esa semana (por hora cambia el sueldo);
separa vales de comida y vales de dinero; abono de préstamo automático. Nueva pestaña "Resumen de nómina": total, menos préstamos, vales de comida,
vales de dinero, transferencias = efectivo, con botón para mandarle el efectivo de cada quien a Berenice por WhatsApp. Pestañas visibles en varias filas;
el catálogo muestra lo que debe cada quien y sus vales de la semana.

Versión 29.1 — la semana del rol y de la nómina va de martes a lunes (lo ya capturado se acomoda solo una vez al instalar).
Préstamos y vales: "Vales de dinero" (los que no pasaron por caja) y "Vales de comida": lista de lo que comió cada empleado esa semana
con un cuadrito para el descuento que Jorge decide, más vales de comida a mano (consumo y descuento). Lo capturado pasa solo a la nómina;
si la nómina ya estaba guardada, avisa "ahora suman…".

Versión 29.2 — Personal (/personal) en blancos y grises (encabezado blanco, botones grises); los colores del rol (T, F, V…) se quedan porque dicen algo.

Versión 29.3 — rol de descansos (panel y página de la encargada): Trabaja en gris clarito con letras negras para que se lean las horas; Descansa en verde.

Versión 29.4 — horas en formato de 12 horas: en el rol 7:30a / 3:30p y en los textos 7:30 am–3:30 pm (panel y página de la encargada).

Versión 29.6 — `abrir_wifi_comandero.bat`: el firewall de la caja solo dejaba entrar a Tailscale (regla de instalar.bat con remoteip=100.64.0.0/10),
por eso el comandero no abría desde el WiFi del restaurante. Este archivo, ejecutado una vez como administrador en la caja, agrega la regla
"Comandero La Holandesa (WiFi)" para el puerto 8765 desde la red local (LocalSubnet). El programa sigue revisando por su cuenta quién es WiFi y quién no.

Versión 29.7 — ícono del comandero en la pantalla de inicio: logo de La Holandesa en cuadrado (icono_comandero_180/192/512.png) y nombre "Holandesa Comandero".
