# monitor-holandesa

Programa que lee las ventas de MrTienda en la computadora de la caja de Restaurante La Holandesa
(solo lectura) y las muestra en el teléfono del dueño.

Aquí solo está el código del programa. Las ventas, contraseñas y la configuración se quedan en la caja
(`C:\MonitorHolandesa\config.json`) y nunca se suben a este repositorio.

Numeración (pedida por Jorge el 11-oct-2026): los cambios pequeños usan un tercer número sobre la versión actual (30.6.1, 30.6.2…);
VERSION (entero) sigue subiendo de 1 en 1 para la actualización automática.

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

Versión 29.8 — ícono del panel de ventas: logo de La Holandesa sobre naranja (icono_panel_180/192/512.png). El manifest del panel ya se entrega sin contraseña
(no trae datos), porque el teléfono lo pide sin ella; antes el ícono salía gris con un "1". El icon.png anterior nunca existió en la caja (había icon.jpeg).

Versión 29.9 — comandero: la pantalla principal solo tiene "+ Nueva cuenta" y las cuentas abiertas. "+ Nueva cuenta" abre las opciones en este orden:
Comedor (elige mesa libre), Para llevar, Servicio a domicilio, Didi, Uber. En MrTienda, la cuenta de Didi queda como D + clave + E (efectivo) o T (tarjeta), ej. D4821E.

Versión 30.0 — cuentas abiertas por corte: cada cuenta abierta cuenta cuántos cortes (turnos cerrados en CORTESZ) han pasado desde que se abrió,
usando la fecha más reciente de los archivos del turno. 1 corte = amarillo ("Del corte anterior"), 2 o más = rojo ("De hace N cortes"),
en el panel (Tickets → Mesas abiertas y el aviso de Hoy) y en los mosaicos del comandero. MrTienda no se puede pintar desde aquí.

Versión 30.1 — comandero: alarma roja arriba (con vibración y pitido) cuando una comanda de las cuentas del mesero lleva 15 minutos o más
desde que se envió; una vez por envío, se apaga con "Ya salió" (se recuerda en ese teléfono). Solo suena con el comandero abierto.
Al elegir "Comida del día" o "Especial del día" sale el recordatorio "Lleva la sopa y el agua" (en la ficha y al agregarlo).

Versión 30.2 — "Comida del día completa": "Lleva la sopa y el agua". "Comida del día sin agua": "Lleva la sopa (sin agua)".

Versión 30.3 — comandero: al abrir una mesa pregunta cuántas personas son (se guarda en COMENSALES de MrTienda). En Comedor y Para llevar,
la primera vez que se agrega un desayuno a la cuenta sale "Ofrece jugo: jugo del día y jugo verde"; con platillos a la carta, antojitos,
ensaladas, burritos, tortas, sándwiches y hamburguesas sale "Ofrece la soda italiana (agua mineral con extracto de fresa)". 20 minutos después
de mandar una comida del día sale el aviso "¿Ya llevaste el postre?".

Versión 30.4 — comanda de cocina más limpia: arriba solo "CUENTA NUEVA", mesero y hora; abajo, en grande y una sola vez:
COMER AQUI + MESA 2, RECOGER AQUI / SERVICIO A DOMICILIO + nombre, o DIDI / UBER + los últimos 3 números de la clave.
El "> PARA LLEVAR / COMER AQUI" por platillo solo sale si ese platillo va distinto al resto. Los textos de MrTienda (cp850) ya se imprimen
y se ven en el comandero con su Ñ y acentos (antes la Ñ salía como ¥). Las referencias que escribe el comandero se guardan en cp850.

Versión 30.5 — "comida del día" se reconoce aunque el nombre esté abreviado (COM. DEL DIA, S/AGUA…) o por estar en la sección "Especial del día" del menú.

Versión 30.6.1 — comandero en azul suave (como MrTienda) y cada tipo de cuenta con fondo blanco y contorno de color: Comedor naranja pastel,
Para llevar negro, Domicilio letras negras con contorno verde, Didi naranja fuerte, Uber verde fuerte (en los mosaicos y en "Nueva cuenta").

Versión 30.6.2 — comandero: los avisos parpadean. Comanda sin "Ya salió": amarilla a los 15 min y roja a los 25 min (vuelve a sonar al pasar a rojo).
Sugerencias (sopa y agua, jugo, soda italiana) parpadean en su color. Soda italiana "la especial de la casa" también por nombre: burritos,
hamburguesas, tortas, bistec, empanizados, milanesa, sándwich.

Versión 30.6.3 — comanda: platillo en doble de alto (las variantes igual) y 3 renglones de espacio antes del bloque de abajo (PARA LLEVAR / MESA 5).

Versión 30.6.4 — el ícono del comandero va dentro de la misma página (data URI) para que Chrome lo use al crear el acceso directo en direcciones http.

Versión 30.6.5 — comandero instalable: service worker mínimo (/comandero-sw.js) y botón azul "📲 Instalar Holandesa Comandero en este teléfono"
que aparece abajo cuando Chrome permite instalarlo (dirección segura https://newcaja…/comandero). Manifest con id /comandero.

Versión 30.6.6 — el ícono del panel de ventas también es el logo sobre fondo blanco (sin el marco naranja), igual que el del comandero.

Versión 30.6.7 — cada app tiene su propio alcance (scope) para que no se "traguen" entre sí en el teléfono: panel en /panel (también sigue en /),
comandero /comandero, personal /personal, descansos /descansos, compras /captura. Antes el panel instalado (alcance "/") abarcaba al comandero
y Chrome no dejaba instalarlo aparte (salía "Crear acceso directo" con una letra).

Versión 30.6.8 — íconos: Control Holandesa con el logo a todo color sobre blanco; Holandesa Comandero con el logo todo en negro sobre blanco.

Versión 30.6.9 — ícono del comandero: solo el contorno del molino y las letras "La Holandesa" en negro, sin la mancha verde ni la franja de Restaurante, fondo blanco.

Versión 30.6.10 — ícono del comandero con trazo más delgado: solo el negro del logo, sin mezclar el naranja.

Versión 30.6.11 — íconos para la pantalla de inicio: el comandero usa archivos normales (no data URI), /favicon.ico sin contraseña y los íconos con caché de un día.

Versión 30.6.13 — al abrir el comandero aparece el logo de La Holandesa con las aspas del molino dando dos vueltas (1.6 s) y luego entra.

Versión 30.6.15 — el comandero pide la clave del mesero en cada envío a cocina (aunque la sesión siga abierta). Clave equivocada = no se envía
y los platillos se quedan; 5 errores seguidos bloquean 5 minutos.

Versión 30.6.16 — clave al entrar al comandero (ya no se guarda la sesión en el teléfono) y otra vez al empezar cada comanda
("+ Nueva cuenta" o abrir una cuenta para agregar). La clave que se escribe define el mesero de esa comanda, aunque el teléfono sea de otro.
Al enviar ya no se pide (se usa la de la captura; la caja la sigue verificando).

Versión 30.6.18 — nómina por día: si en el rol se le ponen menos horas que su horario (ej. 6 h de 8), ese día se paga en proporción. El mensaje de WhatsApp a la encargada trae también el link de Tailscale para capturar desde su casa.

Versión 30.6.19 — botón "Personal ›" arriba en el panel de ventas para abrir Recursos Humanos.
