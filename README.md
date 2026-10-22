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

Versión 30.6.20 — horas con lista para elegir (7:30 am, 7:45 am…, cada 15 min) en lugar del reloj del teléfono, que en algunos Android no dejaba escribir: expediente, rol de Jorge y página de la encargada.

Versión 30.6.21 — nómina con séptimo día: lo ganado en la semana ÷ 6 (si falta un día o trabaja menos horas, baja en proporción). Sale en el expediente, la nómina, el resumen y el recibo.

Versión 30.6.22 — comandero: clave en cuadro chico centrado; alarma a los 10 min (amarilla), 14 min (roja clara con sirena) y 20 min ("CRÍTICO: ya tardó la comanda"); botón "No ha salido" (la esconde 5 min); basura con confirmación para quitar platillos no enviados.

Versión 30.6.23 — Reportes → Comandas tardadas: las que pasaron de 14 y de 20 min, por turno y por mesero, con los platillos de cada una. Se guardan en la caja en comandas_tiempos.json (no se sube a GitHub).

Versión 30.6.24 — PDF del comandero: si el teléfono no deja compartirlo ("Permission denied"), botones "Mandar dirección por WhatsApp" y "Descargar PDF".

Versión 30.6.25 — Didi/Uber: clave de hasta 12 caracteres ("Clave del pedido" más grande); Efectivo/Tarjeta con contorno; "Para llevar" en azul claro; comanda de cocina con platillos y canal en letra más chica.

Versión 30.6.26 — COMANDERO → Comanda de cocina: vista en pantalla de cómo sale la comanda, elegir tamaño de platillos y de "para quién es", espacio entre platillos, guardar e imprimir una prueba.

Versión 30.6.27 — comanda de cocina por defecto con platillos y "para quién es" en Grande (el formato que eligió Jorge).

Versión 30.7.2 — comanda de cocina: un solo renglón vacío entre la raya punteada y "para quién es" (antes 3).

Versión 30.7.3 — comandero: "Servicio a domicilio" en letras verdes (en negro no se veía con el modo oscuro del iPhone).

Versión 30.7.4 — iPhone: ícono del comandero y del panel con dirección nueva (?v=31) para que el iPhone no use uno viejo guardado, tamaño 180x180 declarado y /apple-touch-icon.png en la raíz.

Versión 30.7.5 — PDF del comandero en 2 hojas: "¡Bienvenida/Bienvenido, nombre!", cómo entrar, cómo usar el comandero y la nota "No compartas tu clave, es confidencial". Nombre de archivo sin acentos ni espacios; si el teléfono no deja compartir, el PDF se descarga solo y el error queda en monitor.log.

Versión 30.7.6 — botón "Enviar PDF por WhatsApp"; en Samsung Internet (no deja compartir archivos) abre la misma pantalla en Chrome (/panel?pdf=código) para mandarlo desde ahí.

Versión 30.7.9 — comandero: mapa del comedor (dibujo de Jorge): mesas 1A/1B, 2A/2B, 3, 4A/4B, 5A/5B, 6A/6B, 7, 8A/8B, 9A/9B; se pueden juntar A+B (cuenta "M 1AB"); pestaña "Mapa" con mesas libres/ocupadas; la comanda dice MESA 1A o MESA 1A+1B.

Versión 30.7.10 — mapa del comedor rehecho con el segundo dibujo de Jorge (visto desde el comedor): 2 y 1 a la izquierda, 3-6-7 arriba, 4 y 5 inclinadas al centro, 8 y 9 a la derecha, caja abajo.

Versión 30.7.11 — URGENTE: se restaura comandero.html (en la 30.7.10 se subió vacío por error) con el mapa del segundo dibujo.

Versión 30.7.12 — comandero: "¿Va a querer factura?" en la cuenta: buscar cliente de Mr. Tienda por RFC, nombre o teléfono, revisar y completar CP/régimen/uso CFDI/correo, y poner la cuenta a su nombre (COD_CTE, DES_CTE, SOLICITA). Cliente nuevo: foto de la constancia (IA) o a mano, revisar con el cliente y enviar a la caja (panel → COMANDERO → Clientes para dar de alta). El actualizador ya no instala páginas vacías.

Versión 30.7.13 — al mandar una cuenta nueva el comandero pregunta "¿Va a necesitar factura?" (Sí/No; no en Didi/Uber). Sí: aviso naranja parpadeando en la cuenta (y en su tarjeta) hasta capturar los datos. No: queda "No va a necesitar factura · cambiar".

Versión 30.7.14 — factura: el cliente puede tener hasta 3 correos (CTE_MAIL, CTE_MAIL2, CTE_MAIL3 de Mr. Tienda). Un correo nuevo se agrega en el siguiente espacio libre sin borrar los otros; con 3 llenos, el mesero elige cuál reemplazar.

Versión 30.7.15 — Personal: por hora = salario mínimo frontera ($440.87) ÷ 8 por defecto; casilla 'Pagarle prima dominical' por empleado (por hora llega apagada); el expediente muestra Sueldo + Séptimo día + Prima dominical + Incentivo = Nómina total.

Versión 30.7.16 — la sesión del panel ya no se pierde cuando la caja se actualiza sola (se guarda en sesiones_panel.json, solo en la caja); en Personal la sesión se mantiene viva mientras se llena un expediente o una nómina.

Versión 30.7.17 — protección en la actualización automática: si alguien publica con `version.txt` distinto del `VERSION` del programa, la caja rechaza esa versión (lo anota en el registro) y sigue funcionando con la actual, en lugar de reiniciarse sin parar. No vuelve a intentar hasta que se publique un número nuevo.

Versión 30.7.18 — PDF del comandero: en la hoja 2, "Cómo escribir la mesa en la caja" (M 3, M 1A, M 1AB…) para que la mesa salga ocupada en el mapa.

Versión 30.7.19 — nómina: horas extra automáticas (más de 48 h a la semana; las primeras 9 al doble y de ahí al triple; hora = sueldo diario ÷ 8 o su pago por hora). Salen en el expediente, la nómina, el resumen y el recibo.

Versión 30.7.20 — vales de comida: cada comida de empleado (cuenta quitada en Ventas con motivo Empleado) se abre con su comanda; descuento en % o en $ (se calculan uno al otro) y a qué empleado le toca si el nombre no se reconoció; pasa solo a la nómina y al resumen.

Versión 30.7.21 — nómina y resumen agrupados por puesto con título (cocineros, meseros, ayudantes generales…); botón 'Volver a calcular' en la nómina ya guardada.

Versión 30.7.22 — Captura de la encargada: el nombre se elige del catálogo de empleados; botón 'Ver como la ve' (abre su página); hoja de bienvenida en PDF con el logotipo de Personal, las direcciones del WiFi y de Tailscale y, si se marca, su clave. El rol (panel y página de la encargada) agrupado por puesto con títulos.

Versión 30.7.23 — la encargada solo captura en el restaurante: el PDF y el WhatsApp ya no traen la dirección de Tailscale ni la clave (Jorge se la da en persona).

Versión 30.7.24 — rol de descansos (panel y encargada): los días con su fecha se repiten en cada grupo; horas más grandes sin a.m./p.m.; con el teléfono acostado los cuadros crecen.

Versión 30.7.25 — rol de descansos: al tocar un día se abre una ventanita encima (sin bajar hasta abajo); Enter guarda y se cierra; la pantalla se queda en el mismo lugar.

Versión 30.7.26 — botón 'Auditoría' en el rol (Jorge y encargada): revisa las reglas de turnos de cocineros (6:30, 9:00, 12:30; Moisés suple 6:30–12:30), meseros (7:30–3:30, 9–5, 3–8:30) y ayudantes generales (6:45–2:30 y 2:00 L–V; 8–8 sáb y dom); marca en rojo los cuadros con error y lista lo que falta por día.

Versión 30.7.27 — auditoría: el cocinero de las 6:30 sale 2:30 si nadie descansa y 12:30 si hay descanso (Moisés suple); ayudante de la tarde 2:00–8:30.

Versión 30.7.28 — auditoría: cocineros sábado y domingo 7:30–3:30, 9–5 y 12:30–8:30; Ángel los viernes sale a las 2:00 pm (escuela).

Versión 30.7.29 — expediente: casilla 'Pagarle horas extra' por empleado (si no, no se suman aunque pase de 48 h).

Versión 30.7.40 — Reportes → Resultados: tarjeta "Ventas por tipo" (Comedor, Para llevar, A domicilio, Didi = efectivo + tarjeta, Uber) con el % de la venta, y tablas por día y por semana (lunes a domingo). La caja clasifica cada ticket: Didi por la referencia D…E / D…T, Uber por "UBER …", A domicilio si el comandero abrió la cuenta como Servicio a domicilio (comandero_pedidos.json, mismo nombre, ese día o el anterior) o si MrTienda la marca ADOMICILIO, y el resto por COD_ESCALA (02 llevar, 01 comedor). Con "Incluir Didi y Uber" se suman las cuentas quitadas según su motivo. El desglose también sale dentro de Ventas del Estado de resultados.

Versión 30.7.47 — Didi y Uber cuentan como venta lo que paga la plataforma: precio de menú − comisión − descuentos. La comisión es automática con el % de cada plataforma (Didi 30% + IVA; Uber en blanco hasta que Jorge lo ponga) en Reportes → Didi y Uber, o se escribe a mano en el pedido. Los descuentos se capturan en el comandero al abrir la cuenta y/o en el panel al quitarla o en "cambiar motivo o descuentos" (se guardan en cuentas_quitadas.json: liq.desc y liq.comision si es a mano). Reporte Didi y Uber: con las fechas que elija Jorge, pedidos con tarjeta (lo que debe pagar la plataforma), pedidos en efectivo aparte, y lo que pagó (plataformas_pagos.json, solo en la caja) con la diferencia.

Versión 30.7.48 — panel → COMANDERO → Mapa del comedor: editor con el dedo (agregar mesa sola o doble A/B, letreros, arrastrar, girar, voltear, tamaño, número, borrar). Se guarda en plano.json en la caja y el comandero lo usa.

Versión 30.7.49 — Didi y Uber como su estado de cuenta: se captura el subtotal del recibo (o foto del recibo que se llena sola) y lo que absorbes; el sistema saca la base sin IVA 8%, comisión 30% + IVA 8%, retenciones de ISR 2.5% e IVA 8% (todo editable en Reportes → Didi y Uber). Sin recibo usa el precio de menú.

Versión 30.7.50 — Resultados: Didi y Uber entran a ventas por lo cobrado al cliente; la comisión, su IVA y los descuentos que absorbes van como renglón de gasto ("Comisiones de Didi y Uber"); las retenciones de ISR e IVA aparecen aparte (no son gasto, son a cuenta de impuestos).

Versión 30.7.51 — partes que se pueden apagar en config.json para otro negocio: "modulos": {"comandero": false, "personal": false, "compras": false, "plataformas": false}. Lo apagado desaparece del panel (pestaña Comandero, botón Personal, Didi y Uber, Comandas tardadas, Cajeros, lectura de tickets y Drive) y sus direcciones responden "Esta parte no está activada". Sin "modulos" todo sigue encendido (La Holandesa). Con "actualizar_solo": false tampoco se muestra la tarjeta de la llave de GitHub.


Instalador para otro negocio (solo panel de ventas, copia fija): carpeta `instalador/` (INSTALAR.bat + instalar.ps1 + LEEME.txt). Para armar el ZIP se ponen junto a ellos, en `programa/`, los archivos de archivos.txt de la versión que se entrega. No se descarga por la caja (no está en archivos.txt).

Versión 30.7.52 — tarjeta "Lectura de fotos con Claude" (Reportes → Gastos): "Guardar y probar llave" la prueba con Claude antes de guardarla (llave mala no se guarda; avisa si no hay saldo o llegó al límite), "Probar otra vez", estado con los últimos 4 caracteres y la fecha. Sirve para tickets de compra, recibos de Didi/Uber y constancias fiscales; aparece si cualquiera de esas partes está encendida.

Versión 30.7.53 — comida de empleados (Quitar cuenta / cambiar motivo → Empleado): solo aparecen los empleados activos de Personal, con su nombre completo de la nómina y agrupados por puesto; ya no se escriben nombres a mano (si no está en la nómina, se rechaza). Sin empleados en Personal (otro negocio) funciona como antes.

Versión 30.7.54 — Personal: campo Sobrenombre (opcional, ej. Pepe) en el expediente; sale en el catálogo y en la comida de empleados (sobrenombre grande, nombre completo abajo). Siempre se guarda el nombre completo. Escribir el sobrenombre se reconoce como esa persona. Al guardar en Personal, el panel se actualiza al momento.
