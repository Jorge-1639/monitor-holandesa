# -*- coding: utf-8 -*-
"""
Monitor de ventas - Restaurante La Holandesa
Lee la base de datos de MrTienda y muestra el tablero de ventas
en http://<esta-computadora>:8765  (protegido con usuario y contraseña).

La consulta de ventas es de solo lectura. La única escritura en MrTienda es quitar, con confirmación y motivo,
una cuenta pendiente NO pagada (comida de la familia, pedidos Didi/Uber, errores). Antes respalda los archivos
y deja el registro en cuentas_quitadas.json (fuera de MrTienda), que alimenta los reportes y el Excel.
El comandero (/comandero) es por ahora de solo lectura: muestra a los meseros el menú y las cuentas abiertas de MrTienda.
Requiere Python 3.9 o superior. No necesita instalar paquetes adicionales.
"""
import os, re, sys, json, gzip, glob, time, base64, struct, hashlib, secrets, threading, datetime, collections, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

AQUI = os.path.dirname(os.path.abspath(__file__))
LOCK_TIEMPOS = threading.Lock()
VERSION = 180            # número interno que compara la actualización automática (siempre entero, sube de 1 en 1)
VERSION_TXT = '30.7.78'    # versión que se muestra: 24.1, 24.2… y 25.0 cuando hay un cambio grande
REPO_RAW = 'https://raw.githubusercontent.com/Jorge-1639/monitor-holandesa/main/'
REPO_API = 'https://api.github.com/repos/Jorge-1639/monitor-holandesa'
ESTADO_ACT = {}   # 30.7.39: resultado de la última revisión de actualizaciones (se muestra en la tarjeta de la llave de GitHub)
CONFIG_FILE = os.path.join(AQUI, 'config.json')
LOG_FILE = os.path.join(AQUI, 'monitor.log')
DEFAULT_CONFIG = {
    "carpeta_mrtienda": r"C:\MRHOLA",
    "puerto": 8765,
    "usuario": "jorge",
    "contrasena": "",
    "minutos_actualizacion": 2,
    "dias_detalle": 70,
    "bitacora_borrados_hasta": "2025-02-21",
    "segundos_revision_actualizacion": 300
}
QUITADAS_FILE = os.path.join(AQUI, 'cuentas_quitadas.json')
GASTOS_FILE = os.path.join(AQUI, 'gastos.json')       # compras y gastos capturados (no es de MrTienda)
FOTOS_DIR = os.path.join(AQUI, 'fotos_compras')        # fotos de tickets de compra
CAJEROS_FILE = os.path.join(AQUI, 'cajeros.json')       # cajeros con su clave (solo se guarda la huella de la clave)
MESEROS_FILE = os.path.join(AQUI, 'meseros.json')
PLAT_PAGOS_FILE = os.path.join(AQUI, 'plataformas_pagos.json')   # 30.7.47: lo que Didi y Uber depositaron por rango de fechas (solo en la caja)
PEDIDOS_FILE = os.path.join(AQUI, 'comandero_pedidos.json')   # canal de cada cuenta del comandero (recoger, domicilio, Didi, Uber): solo en la caja
CANALES = {'recoger': 'RECOGER AQUI', 'domicilio': 'SERVICIO A DOMICILIO', 'didi': 'DIDI', 'uber': 'UBER', 'empleado': 'COMIDA EMPLEADOS'}
COMIDAS_EMP_FILE = os.path.join(AQUI, 'comidas_empleados.json')   # 30.7.55: cada comida de empleado del comandero (quién, hora, quién firmó/autorizó): solo en la caja
RH_FILE = os.path.join(AQUI, 'personal.json')
EXPED_DIR = os.path.join(AQUI, 'expedientes')            # contratos y documentos de cada empleado (fotos, Word, PDF), solo en la caja
DIAS_SEM = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
TIPOS_DOC = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'application/pdf': '.pdf', 'application/msword': '.doc',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx'}

def _hhmm(v):
    m = re.fullmatch(r'(\d{1,2}):(\d{2})', str(v or '').strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59: return ''
    return '%02d:%02d' % (int(m.group(1)), int(m.group(2)))

def rh_base(e):
    # horario base de la semana del empleado: 7 días [entra, sale] o None si descansa (antes de 28.3: un solo horario y un día de descanso)
    h = e.get('horario')
    if isinstance(h, list) and len(h) == 7:
        return [[_hhmm(x[0]), _hhmm(x[1])] if isinstance(x, (list, tuple)) and len(x) == 2 else None for x in h]
    d = DIAS_SEM.index(e['descanso']) if e.get('descanso') in DIAS_SEM else -1
    return [None if i == d else [_hhmm(e.get('entrada')), _hhmm(e.get('salida'))] for i in range(7)]

SEM_INI = 1   # la nómina de La Holandesa va de martes a lunes (29.1): las semanas del rol y la nómina empiezan en martes

def inicio_sem(f):
    return f - datetime.timedelta(days=(f.weekday() - SEM_INI) % 7)

def base_sem(base):
    # horario base (lunes a domingo) acomodado en el orden de la semana de nómina (martes a lunes)
    return [base[(SEM_INI + i) % 7] for i in range(7)]

def rh_dia(rh, e, f):
    # (código del día, [entra, sale]) según el rol de esa semana; si no se capturó, según su horario base
    sem = inicio_sem(f).isoformat(); i = (f.weekday() - SEM_INI) % 7; base = rh_base(e)[f.weekday()]
    r = (rh.get('roles', {}).get(sem) or {}).get(e['id'])
    c = r[i] if isinstance(r, list) and len(r) == 7 else ('D' if base is None else 'T')
    hs = (rh.get('roles_h', {}).get(sem) or {}).get(e['id'])
    h = hs[i] if isinstance(hs, list) and len(hs) == 7 and hs[i] else base
    return c, (h or ['', ''])

def rh_a_martes(d):
    # una sola vez: pasa el rol, horarios, quién capturó y nóminas de semanas lunes–domingo a semanas martes–lunes
    emps = {e['id']: e for e in d.get('empleados', [])}
    roles, horas, meta, nom = {}, {}, {}, {}
    for k, v in d.get('roles', {}).items():
        try: K = datetime.date.fromisoformat(k)
        except ValueError: continue
        for emp, arr in (v or {}).items():
            if not isinstance(arr, list) or len(arr) != 7: continue
            hs = ((d.get('roles_h') or {}).get(k) or {}).get(emp)
            for j in range(7):
                f = K + datetime.timedelta(days=j); nk = inicio_sem(f).isoformat(); i = (f.weekday() - SEM_INI) % 7
                b = rh_base(emps[emp]) if emp in emps else [['', '']] * 7
                roles.setdefault(nk, {}).setdefault(emp, ['D' if b[(SEM_INI + x) % 7] is None else 'T' for x in range(7)])[i] = arr[j]
                if isinstance(hs, list) and len(hs) == 7 and hs[j]: horas.setdefault(nk, {}).setdefault(emp, [None] * 7)[i] = hs[j]
                if (d.get('roles_meta') or {}).get(k, {}).get(emp): meta.setdefault(nk, {})[emp] = d['roles_meta'][k][emp]
    for k, v in d.get('nominas', {}).items():
        try: nom[inicio_sem(datetime.date.fromisoformat(k) + datetime.timedelta(days=1)).isoformat()] = v
        except ValueError: pass
    d['roles'], d['roles_h'], d['roles_meta'], d['nominas'] = roles, horas, meta, nom
    d['semana_inicia'] = 'martes'
    return d

def horas_list(v):
    # 7 horarios que manda el teléfono: [entra, sale] o None
    if not isinstance(v, list) or len(v) != 7: return None
    return [[_hhmm(x[0]), _hhmm(x[1])] if isinstance(x, (list, tuple)) and len(x) == 2 and _hhmm(x[0]) and _hhmm(x[1]) else None for x in v]

def horario_texto(base):
    # "Lun a Vie 7:30–15:30 · Dom 12:30–20:30 · descansa Sáb"
    def c(v):   # 15:30 -> 3:30 pm (como lo maneja La Holandesa)
        m = re.fullmatch(r'(\d{1,2}):(\d{2})', str(v or ''))
        if not m: return str(v or '')
        H = int(m.group(1)); return '%d:%s %s' % (H % 12 or 12, m.group(2), 'am' if H < 12 else 'pm')
    cortos = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']; partes = []; i = 0
    while i < 7:
        j = i
        while j + 1 < 7 and base[j + 1] == base[i]: j += 1
        if base[i]: partes.append((cortos[i] if i == j else cortos[i] + (' y ' if j == i + 1 else ' a ') + cortos[j]) + ' ' + (c(base[i][0]) + '–' + c(base[i][1]) if base[i][0] else ''))
        i = j + 1
    d = [cortos[k] for k in range(7) if base[k] is None]
    return ' · '.join(p.strip() for p in partes) + ((' · descansa ' + ', '.join(d)) if d else '')           # Recursos Humanos: expedientes, rol, nómina, vales y préstamos (solo en la caja)
ORDEN_ROL = ['Cocinero', 'Ayudante de cocina', 'Mesero', 'Cajero', 'Encargada', 'Ayudante general', 'Repartidor', 'Lavaloza', 'Otro']   # así los agrupa Jorge: cocineros, meseros, ayudantes generales…
PUESTOS = ['Mesero', 'Cajero', 'Encargada', 'Cocinero', 'Ayudante de cocina', 'Ayudante general', 'Repartidor', 'Lavaloza', 'Otro']       # meseros del comandero: código de MrTienda + huella de su clave
IA_URL = 'https://api.anthropic.com/v1/messages'
IA_MODELO = 'claude-haiku-5-5'
PROMPT_RECIBO_PLAT = '''Lee la foto de este recibo de pedido de Didi Food o Uber Eats (el que imprime la tableta del restaurante) y responde SOLO con un objeto JSON, sin texto antes ni después:
{"plataforma": "didi o uber", "pedido": "", "subtotal": 0, "descuento_entrega": 0, "otros_restaurante": 0, "efectivo": 0, "total_app": 0}
Reglas: pedido = el número o clave del pedido (en Didi el que va con #, ej. 792006; en Uber el código corto arriba, ej. C176B), sin # ni espacios. subtotal = el renglón Subtotal (en Uber, si no hay Subtotal, el Importe pagado). descuento_entrega = el renglón "Descuento de la tarifa de entrega" como número positivo (0 si no viene). otros_restaurante = otros descuentos que diga que paga la tienda o el restaurante (no "Platform discount" ni "Tarifa de servicio"); 0 si no hay. efectivo = el renglón "Pago en efectivo" (0 si no viene). total_app = "Pago de DiDi Food" o "Importe pagado". Solo números, sin signo de pesos. No inventes: lo que no se vea, 0.'''

PROMPT_CONSTANCIA = '''Lee la foto de esta Constancia de Situación Fiscal del SAT, o del papel con datos de facturación que dio el cliente, o de la pantalla o captura del mensaje de WhatsApp "SOLICITUD DE FACTURA" que manda la página del restaurante (trae renglones como "Nombre:", "RFC:", "Código postal fiscal:", "Régimen fiscal: 612 - ...", "Uso de CFDI: G03 - ...", "Correo:"; los asteriscos son negritas de WhatsApp, ignóralos; "Nombre" es la razón social). Responde SOLO con un objeto JSON, sin texto antes ni después, con esta forma:
{"rfc": "", "razon_social": "", "cp": "", "regimen": "", "uso": "", "calle": "", "no_ext": "", "no_int": "", "colonia": "", "localidad": "", "municipio": "", "estado": "", "correo": "", "telefono": ""}
uso: SOLO la clave del uso de CFDI si viene escrita (ej. G03, S01, G01, CP01); si no viene, déjalo vacío. En un mensaje de WhatsApp no tomes el número de teléfono del chat ni la hora como datos del cliente.
Reglas: rfc en mayúsculas sin espacios. razon_social: la denominación o razón social; si es persona física, nombre completo (nombre y apellidos) en mayúsculas, sin régimen de capital. cp: el código postal del domicilio fiscal (5 números). regimen: SOLO la clave de 3 números del catálogo del SAT (601 General de Ley Personas Morales, 603 Personas Morales con Fines no Lucrativos, 605 Sueldos y Salarios, 606 Arrendamiento, 608 Demás ingresos, 612 Personas Físicas con Actividades Empresariales y Profesionales, 616 Sin obligaciones fiscales, 621 Incorporación Fiscal, 622 Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras, 625 Plataformas Tecnológicas, 626 Régimen Simplificado de Confianza); si tiene varios, el primero de la lista. estado: nombre completo en mayúsculas (ej. BAJA CALIFORNIA). Lo que no se vea, déjalo vacío; no inventes.'''

PROMPT_TICKET = '''Eres el capturista de compras de un restaurante en Ensenada, México. Lee la foto de este ticket o factura de compra y responde SOLO con un objeto JSON, sin texto antes ni después, con esta forma:
{"legible": true, "proveedor": "nombre corto del negocio que vendió", "fecha": "AAAA-MM-DD o vacío", "ticket": "número de ticket o factura o vacío",
 "subtotal": 0, "iva": 0, "total": 0,
 "items": [{"descripcion": "texto del renglón como viene", "insumo": "nombre genérico corto en español, en singular", "cantidad": 0, "unidad": "pz|kg|g|l|ml|paquete|caja", "precio_unitario": 0, "importe": 0}]}
Reglas: los montos son números en pesos sin signos. "total" es lo que se pagó. Si el ticket no trae IVA desglosado pon iva 0 y subtotal igual al total.
"insumo" agrupa productos iguales de distintas marcas (por ejemplo "LECHE LALA ENTERA 1L" es "Leche"; "QUESO OAXACA KG" es "Queso oaxaca"). Usa de preferencia uno de estos nombres si corresponde: {catalogo}.
Si no se puede leer, responde {"legible": false}.'''
NEGOCIOS = ['Restaurante', 'Cafetería']
PAGOS = ['Efectivo de la caja', 'Tarjeta o transferencia', 'A crédito', 'Otro']
CATEGORIAS_GASTO = ['Insumos y compras', 'Sueldos', 'Seguro social', 'Luz', 'Agua', 'Gas', 'Renta', 'Mantenimiento', 'Comisiones', 'Otros gastos']
RESPALDO_CUENTAS = os.path.join(AQUI, 'respaldos_cuentas')   # fuera de _respaldo: las actualizaciones no lo borran
MOTIVOS = ['Familia', 'Empleado', 'Comida de empleado', 'Cortesía', 'Didi · efectivo', 'Didi · tarjeta', 'Uber · tarjeta', 'Error de captura', 'Otro', 'Sin clasificar']   # Uber siempre se paga con tarjeta
CON_PERSONA = {'Familia', 'Empleado', 'Comida de empleado'}   # 'Comida de empleado' = regalada (no se descuenta); 'Empleado' = se descuenta de nómina   # estos motivos piden de quién fue la comida


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except Exception: pass
    try: print(line)
    except Exception: pass

# 30.7.48: partes del sistema que se pueden apagar en config.json ("modulos"), p. ej. en un negocio que solo usa el panel de ventas
MODULOS = ('comandero', 'personal', 'compras', 'plataformas')
RUTAS_MODULO = {   # se apaga todo lo que empiece así
    'comandero': ('/comandero', '/api/comandero', '/api/mesero-login', '/api/meseros', '/manifest-comandero', '/api/comanda-formato', '/api/clientes-nuevos', '/api/plano'),
    'personal': ('/personal', '/descansos', '/api/rh', '/api/rol', '/api/descansos', '/manifest-rol', '/manifest-rh'),
    'compras': ('/captura', '/api/compra', '/api/captura-info', '/api/leer-ticket', '/api/leer-pendientes', '/api/caja-login', '/api/cajeros', '/api/drive', '/manifest-caja'),
    'plataformas': ('/api/plataformas', '/api/leer-recibo'),
}

def modulos(cfg):
    m = cfg.get('modulos') if isinstance(cfg.get('modulos'), dict) else {}
    return {k: m.get(k, True) is not False for k in MODULOS}

def ruta_apagada(cfg, path):
    p = path.split('?')[0]; on = modulos(cfg)
    return any(not on[k] and p.startswith(rutas) for k, rutas in RUTAS_MODULO.items())

def load_config_ro():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f: cfg.update(json.load(f))
    except Exception: pass
    return cfg

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8') as f: cfg.update(json.load(f))
    if not cfg.get('usuario_caja'): cfg['usuario_caja'] = 'caja'
    if not cfg.get('contrasena_caja'):
        cfg['contrasena_caja'] = ''.join(secrets.choice('23456789') for _ in range(6))
        log('Se generó la contraseña de cajeros; está en config.json')
    if not cfg.get('drive_secreto'): cfg['drive_secreto'] = secrets.token_urlsafe(24)
    if not cfg.get('contrasena'):
        cfg['contrasena'] = secrets.token_urlsafe(6)
        log('Se generó una contraseña nueva; está en config.json')
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

# ---------------------------------------------------------------- lectura DBF (solo lectura)
def read_dbf(path, enc='latin-1'):
    with open(path, 'rb') as fh:          # 'rb' = solo lectura
        b = fh.read()
    if len(b) < 32: return [], []
    n, hl, rl = struct.unpack('<IHH', b[4:12])
    flds = []; i = 32
    while i < hl and b[i] != 0x0D:
        name = b[i:i+11].split(b'\0')[0].decode('latin-1'); t = chr(b[i+11]); ln = b[i+16]
        if t == 'C': ln += b[i+17] * 256   # texto de más de 255 (ej. OBSERVAR): el tamaño usa dos bytes
        flds.append((name, t, ln)); i += 32
    rows = []
    for r in range(n):
        s = hl + r*rl; rec = b[s:s+rl]
        if len(rec) < rl: break
        d = {'_del': rec[0:1] == b'*'}; p = 1
        for name, t, ln in flds:
            v = rec[p:p+ln]; p += ln
            if t in 'CM': v = v.decode(enc, 'ignore').rstrip() if name == 'VARIAASCII' else v.decode(enc, 'ignore').strip()   # VARIAASCII: la posición importa
            elif t in 'NF':
                v = v.strip()
                try: v = float(v) if v else None
                except ValueError: v = None
            elif t == 'D': v = v.decode('latin-1').strip()
            elif t == 'L': v = v in (b'T', b't', b'Y', b'y')
            elif t == 'I': v = struct.unpack('<i', v)[0]
            elif t == 'Y': v = struct.unpack('<q', v)[0] / 10000
            elif t == 'B': v = struct.unpack('<d', v)[0]
            else: v = None
            d[name] = v
        rows.append(d)
    return flds, rows

def safe_rows(path):
    for intento in range(3):
        try: return read_dbf(path)[1]
        except (PermissionError, OSError) as e:
            time.sleep(1.5)
    log('No se pudo leer ' + path); return []

# ---------------------------------------------------------------- escritura DBF (solo la usa el comandero)
RESPALDO_COMANDERO = os.path.join(AQUI, 'respaldos_comandero')
# Valores fijos tal como MrTienda los dejó en la cuenta de prueba (los demás campos van en blanco)
DEF_DET = {'XTIEMPO': '1', 'V_PUNTOS': 0, 'L_ORDEN': 0, 'SUBSIDIO': False, 'FRACCION_S': 0, 'PREC_VAR': 0, 'COST_VAR': 0, 'CADUCIDAD': False,
           'DCTO': 0, 'NOTAGUIA': False, 'FOODSTAMP': False, 'NOFRACCION': False, 'OFERTA': 0, 'COMISION': 0, 'SERVICIO': False, 'ULTIMO': False,
           'CTRLVER': 'O', 'DEVOLUCI': False, 'CUPON': False, 'BASCULA': False, 'VARIANTE': b'\x00\x00\x00\x00 ', 'CANTADO': True, 'NUMSERIE': False,
           'PEDIMENTO': False, 'IEPS': 0, 'VALIDAR_EX': False, 'MCOMBOS': False, 'BASE_PROD': False, 'SIN_DCTO': False, 'LMAX_DCTO': 0, 'NODCTO': False,
           'FPGC': 0, 'F_COMANDA': 0, 'MR_PERSONA': 1, 'NODISPARAR': False, 'C_ENROJO': False, 'RECARGA': False, 'MNTR_EN_Z': False,
           'BOLETAJE': False, 'EX_PUNTOS': False, 'M_PSUB_TL': False, 'V_PSUB_TL': 0, 'SAT_EXENTO': False, 'LIM1_ESC1': 0, 'LIM2_ESC2': 0, 'LIM3_ESC3': 0,
           'MC_3X2': False, 'A_OFER123': False, 'L_OFER123': 0, 'D_OFER123': 0, 'N_AF_CORTE': False, 'L_NEGRA': False, 'XML_NOI': False}
DEF_PEND = {'PAGADO': False, 'IMPRESOS': 0, 'AHORRO': 0, 'ADOMICILIO': False, 'MCSUBSIDIO': 0, 'CXSUBSIDIO': b'\x00', 'CTE_DCTO': 0, 'CAMBIODE': 0,
            'COMENSALES': 1, 'BABYS': 0, 'NUM_CONT': '0000', 'CARGO1': 0, 'CARGO2': 0, 'CARGO3': 0, 'VCARGO1': 0, 'VCARGO2': 0, 'VCARGO3': 0,
            'ENTREGAR': False, 'ORIGEN': b'\x00', 'ENRUTA': False, 'ENV_WEB': False, 'YA_VISTA': True, 'CTE_QIVA': False, 'BLOQUEADO': False}
CAMPOS_DET = ('COD_ESCALA', 'COD_PROD', 'DES_PROD', 'DES_CANTAR', 'PRECIO_O', 'PRECIO', 'CANTIDAD', 'IMPORTE', 'VARIAASCII', 'SUB_VARIA', 'PUERTO', 'CANTADO', 'VAR_LIBRE')
CAMPOS_PEND = ('PAGADO', 'FECHA_AP', 'HORA_AP', 'FILE', 'FOL_CTA_P', 'REF', 'ARTS', 'COD_VENDED', 'DES_VENDED', 'IMPORTE_MN', 'IVA', 'COD_ESCALA')

def _layout(b):
    if len(b) < 32: raise RuntimeError('Archivo DBF vacío o dañado.')
    n, hl, rl = struct.unpack('<IHH', b[4:12]); flds = {}; i = 32; off = 1
    while i < hl and b[i] != 0x0D:
        t = chr(b[i+11]); ln, dec = b[i+16], b[i+17]
        if t == 'C': ln, dec = ln + dec * 256, 0   # texto de más de 255: el tamaño usa dos bytes
        name = b[i:i+11].split(b'\0')[0].decode('latin-1'); flds[name] = (t, ln, dec, off); off += ln; i += 32
    if off != rl: raise RuntimeError('La estructura del archivo no es la esperada.')
    return n, hl, rl, flds

def _campo(rec, flds, name):
    t, ln, dec, o = flds[name]; return bytes(rec[o:o+ln])

def _pon(rec, flds, name, v):
    if name not in flds: return
    t, ln, dec, o = flds[name]
    if isinstance(v, (bytes, bytearray)): raw = bytes(v)
    elif t == 'L': raw = b'T' if v else b'F'
    elif t in 'NF':
        raw = (f'{float(v):{ln}.{dec}f}' if dec else f'{int(round(float(v))):{ln}d}').encode()
        if len(raw) > ln: raise ValueError(f'El valor {v} no cabe en {name}.')
    else: raw = str(v).encode('latin-1', 'replace')
    rec[o:o+ln] = raw[:ln].ljust(ln, b' ')

def _fecha_hdr(b, ahora):
    b[1], b[2], b[3] = ahora.year % 100, ahora.month, ahora.day   # MrTienda escribe el año con dos dígitos

def _escribe_en(path, pos, datos, intentos=6):
    for k in range(intentos):
        try:
            with open(path, 'r+b') as f: f.seek(pos); f.write(datos); f.flush(); os.fsync(f.fileno())
            return
        except PermissionError:
            if k == intentos - 1: raise RuntimeError('MrTienda tiene ocupado ' + os.path.basename(path) + '. Intenta en unos segundos.')
            time.sleep(0.5)

def _plantilla(com, patrones, campos, rl=None):
    """Encabezado de un archivo que MrTienda ya creó: de los más recientes, el de la estructura más nueva (más campos)."""
    mejor = None
    for patron in patrones:
        for p in sorted(glob.glob(os.path.join(com, patron)), key=lambda p: os.path.getmtime(p), reverse=True)[:30]:
            try:
                with open(p, 'rb') as f: b = f.read()
                n, hl, rl_, fl = _layout(b)
                if all(c in fl for c in campos) and (rl is None or rl_ == rl) and (mejor is None or len(fl) > len(mejor[1])): mejor = (bytearray(b[:hl]), fl, rl_)
            except Exception: continue
    if mejor: return mejor
    raise RuntimeError('No encontré un archivo de MrTienda que sirva de modelo (' + patrones[0] + ').')

def _respaldar(archivos, etiqueta):
    sello = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
    d = os.path.join(RESPALDO_COMANDERO, sello + '_' + etiqueta); os.makedirs(d, exist_ok=False)
    for a in archivos:
        if os.path.isfile(a): shutil.copy2(a, os.path.join(d, os.path.basename(a)))
    try:
        viejos = sorted(os.listdir(RESPALDO_COMANDERO))[:-800]
        for v in viejos: shutil.rmtree(os.path.join(RESPALDO_COMANDERO, v), ignore_errors=True)
    except OSError: pass
    return d

def _renglon_det(fl, rl, r):
    rec = bytearray(b' ' * rl)
    for k, v in DEF_DET.items(): _pon(rec, fl, k, v)
    for k, v in r.items():
        if k.startswith('_'): continue
        if k == 'COD_PLU': v = str(v).rjust(fl['COD_PLU'][1]) if 'COD_PLU' in fl else v
        _pon(rec, fl, k, v)
    return bytes(rec)

def _totales(detalle_b, tc):
    n, hl, rl, fl = _layout(detalle_b); tot = iva = arts = 0.0
    for k in range(n):
        rec = detalle_b[hl + k*rl: hl + (k+1)*rl]
        if rec[0:1] == b'*': continue
        num = lambda c: float(_campo(rec, fl, c).strip() or 0)
        imp = num('IMPORTE'); tasa = num('IVA'); tot += imp; arts += num('CANTIDAD')
        iva += imp - imp / (1 + tasa / 100) if tasa else 0
    return round(tot, 2), round(iva, 2), int(round(arts)), (round(tot / tc, 2) if tc else 0)

def _abrir_cuenta(com, pend, folios, ref, cod, nombre, escala, ren, tc, ahora, pers=1):
    with open(pend, 'rb') as f: pb = bytearray(f.read())
    pn, phl, prl, pfl = _layout(pb)
    if not all(c in pfl for c in CAMPOS_PEND): raise RuntimeError('PENDIENT.DBF no tiene la estructura esperada.')
    with open(folios, 'rb') as f: fb = bytearray(f.read())
    fn, fhl, frl, ffl = _layout(fb)
    if fn < 1 or 'FOL_CTA_P' not in ffl: raise RuntimeError('FOLIOS.DBF no tiene la estructura esperada.')
    fol_raw = _campo(fb[fhl:fhl+frl], ffl, 'FOL_CTA_P').decode('latin-1')
    nuevo = int(fol_raw.strip() or 0) + 1
    ln_f = ffl['FOL_CTA_P'][1]
    # el folio de la cuenta lleva adelante el número de caja (10021025 = caja 1, folio 21025); se copia de las cuentas de MrTienda
    pref, mejor = '1', -1
    for k in range(pn):
        v = _campo(pb[phl + k*prl: phl + (k+1)*prl], pfl, 'FOL_CTA_P').decode('latin-1').strip()
        if v.isdigit() and int(v[1:] or 0) > mejor: mejor, pref = int(v[1:] or 0), v[0]
    fol_cta = pref + str(nuevo).zfill(ln_f - 1)[-(ln_f - 1):]
    # número de orden: MrTienda le suma uno a la última cuenta abierta y vuelve a empezar después del 20
    # (en la prueba: ... 14, 15 → la cuenta PRUEBA quedó con 16; antes una cuenta con 20 y la siguiente con 2)
    orden, ult = 0, ''
    for k in range(pn):
        rr = pb[phl + k*prl: phl + (k+1)*prl]
        if 'FOL_ORDEN' not in pfl: break
        cuando = _campo(rr, pfl, 'FECHA_AP').decode('latin-1') + _campo(rr, pfl, 'HORA_AP').decode('latin-1')
        if cuando.strip() and cuando > ult:
            try: orden, ult = int(_campo(rr, pfl, 'FOL_ORDEN').strip() or 0), cuando
            except ValueError: pass
    plant = None
    for k in range(pn - 1, -1, -1):
        rec = pb[phl + k*prl: phl + (k+1)*prl]
        if _campo(rec, pfl, 'COD_CTE').strip(): plant = rec; break
    while True:
        archivo = 'PD' + str(secrets.randbelow(900000) + 100000)
        if not any(os.path.exists(os.path.join(com, archivo + e)) for e in ('.DBF', '.BAK', '.ENC')): break
    dh, dfl, drl = _plantilla(com, ('PD*.DBF', 'PD*.BAK'), CAMPOS_DET)
    eh, efl, erl = _plantilla(com, ('PD*.ENC',), CAMPOS_PEND, prl)
    if set(efl) != set(pfl): raise RuntimeError('El encabezado de cuenta (.ENC) no coincide con PENDIENT.DBF.')
    # detalle
    dets = b''.join(_renglon_det(dfl, drl, r) for r in ren)
    struct.pack_into('<I', dh, 4, len(ren)); _fecha_hdr(dh, ahora)
    detalle = bytes(dh) + dets + b'\x1a'
    total, iva, arts, dl = _totales(detalle, tc)
    # renglón de PENDIENT
    rec = bytearray(b' ' * prl)
    for k, v in DEF_PEND.items(): _pon(rec, pfl, k, v)
    for c in ('COD_ALMA', 'COD_CTE', 'DES_CTE'):
        if plant is not None and c in pfl: rec[pfl[c][3]:pfl[c][3] + pfl[c][1]] = _campo(plant, pfl, c)
    if not _campo(rec, pfl, 'COD_ALMA').strip(): _pon(rec, pfl, 'COD_ALMA', '001')
    vals = {'FECHA_AP': ahora.strftime('%Y%m%d'), 'HORA_AP': ahora.strftime('%H:%M'), 'FECHA': ahora.strftime('%Y%m%d'), 'FILE': archivo, 'FOL_CTA_P': fol_cta,
            'REF': str(ref).encode('cp850', 'replace'), 'HORA': ahora.strftime('%H:%M'), 'ARTS': arts, 'COD_CAJERO': cod, 'COD_VENDED': cod, 'DES_CAJERO': nombre[:15], 'DES_VENDED': nombre[:15],
            'IVA': iva, 'IMPORTE_MN': total, 'IMPORTE_DL': dl, 'COD_ESCALA': escala, 'FECHA_ENT': ahora.strftime('%Y%m%d'), 'HORA_ENT': ahora.strftime('%H:%M'),
            'FOL_ORDEN': orden + 1 if 0 < orden < 20 else 1}
    if 'COMENSALES' in pfl: vals['COMENSALES'] = max(1, min(int(pers or 1), 99))   # personas en la mesa (lo pregunta el comandero)
    for k, v in vals.items(): _pon(rec, pfl, k, v)
    enc_rec = bytearray(rec)
    for c in ('IVA2', 'IVA3', 'IVA4', 'IVA5'): _pon(enc_rec, efl, c, 0)
    for c in ('FACTURAR', 'ENVTICKET', 'DECONTADO'): _pon(enc_rec, efl, c, False)
    struct.pack_into('<I', eh, 4, 1); _fecha_hdr(eh, ahora)
    resp = _respaldar([pend, folios], archivo)
    rutas = [os.path.join(com, archivo + e) for e in ('.DBF', '.BAK', '.ENC')]
    try:
        for r_, datos in zip(rutas, (detalle, detalle, bytes(eh) + bytes(enc_rec) + b'\x1a')):
            with open(r_, 'xb') as f: f.write(datos); f.flush(); os.fsync(f.fileno())
        frec = bytearray(fb[fhl:fhl+frl]); _pon(frec, ffl, 'FOL_CTA_P', str(nuevo).zfill(ln_f))
        _escribe_en(folios, fhl + ffl['FOL_CTA_P'][3], _campo(frec, ffl, 'FOL_CTA_P'))
        if 'HORA_ENT' in ffl:   # MrTienda deja aquí la hora del último movimiento
            _pon(frec, ffl, 'HORA_ENT', ahora.strftime('%H:%M')); _escribe_en(folios, fhl + ffl['HORA_ENT'][3], _campo(frec, ffl, 'HORA_ENT'))
        hdr = bytearray(fb[:4]); _fecha_hdr(hdr, ahora); _escribe_en(folios, 0, bytes(hdr))
        # MrTienda reutiliza el primer renglón borrado; si no hay, agrega al final
        with open(pend, 'rb') as f: pb = bytearray(f.read())
        pn, phl, prl, pfl = _layout(pb)
        slot = next((k for k in range(pn) if pb[phl + k*prl: phl + k*prl + 1] == b'*'), None)
        if slot is None:
            _escribe_en(pend, phl + pn*prl, bytes(rec) + b'\x1a')
            hdr = bytearray(pb[:8]); struct.pack_into('<I', hdr, 4, pn + 1)
        else:
            _escribe_en(pend, phl + slot*prl, bytes(rec))
            hdr = bytearray(pb[:8])
        _fecha_hdr(hdr, ahora); _escribe_en(pend, 0, bytes(hdr))
    except Exception:
        for r_ in rutas:
            try:
                if os.path.exists(r_): os.remove(r_)
            except OSError: pass
        for a in (pend, folios):
            try: shutil.copy2(os.path.join(resp, os.path.basename(a)), a)
            except OSError: pass
        raise
    return {'archivo': archivo, 'ref': ref, 'folio': fol_cta, 'total': total, 'agregado': total, 'respaldo': os.path.basename(resp)}

def _agregar_a_cuenta(com, pend, archivo, cod, ren, tc, ahora):
    with open(pend, 'rb') as f: pb = bytearray(f.read())
    pn, phl, prl, pfl = _layout(pb)
    slot = None
    for k in range(pn):
        rec = pb[phl + k*prl: phl + (k+1)*prl]
        if rec[0:1] != b'*' and _campo(rec, pfl, 'FILE').decode('latin-1').strip().upper() == archivo.upper(): slot = k; break
    if slot is None: raise RuntimeError('Esa cuenta ya no está abierta (¿ya la cobraron?). Actualiza el comandero.')
    rec = bytearray(pb[phl + slot*prl: phl + (slot+1)*prl])
    if _campo(rec, pfl, 'PAGADO') in (b'T', b't'): raise RuntimeError('Esa cuenta ya está pagada.')
    if _campo(rec, pfl, 'COD_VENDED').decode('latin-1').strip() != cod: raise RuntimeError('Esa cuenta es de otro mesero.')
    det = os.path.join(com, archivo + '.DBF'); bak = os.path.join(com, archivo + '.BAK'); enc = os.path.join(com, archivo + '.ENC')
    if not os.path.isfile(det): raise RuntimeError('No encontré el detalle de esa cuenta en MrTienda.')
    with open(det, 'rb') as f: db = bytearray(f.read())
    dn, dhl, drl, dfl = _layout(db)
    if not all(c in dfl for c in CAMPOS_DET): raise RuntimeError('El detalle de la cuenta no tiene la estructura esperada.')
    nuevos = b''.join(_renglon_det(dfl, drl, r) for r in ren)
    agregado = round(sum(r['IMPORTE'] for r in ren), 2)
    resp = _respaldar([pend, det, bak, enc], archivo)
    try:
        _escribe_en(det, dhl + dn*drl, nuevos + b'\x1a')
        hdr = bytearray(db[:8]); struct.pack_into('<I', hdr, 4, dn + len(ren)); _fecha_hdr(hdr, ahora); _escribe_en(det, 0, bytes(hdr))
        with open(det, 'rb') as f: nuevo_det = f.read()
        dn2, dhl2, drl2, _ = _layout(nuevo_det)
        nuevo_det = nuevo_det[:dhl2 + dn2*drl2] + b'\x1a'
        with open(det, 'r+b') as f: f.truncate(len(nuevo_det))
        tmp = bak + '.holtmp'
        with open(tmp, 'wb') as f: f.write(nuevo_det)
        os.replace(tmp, bak)
        total, iva, arts, dl = _totales(nuevo_det, tc)
        for k, v in {'ARTS': arts, 'IVA': iva, 'IMPORTE_MN': total, 'IMPORTE_DL': dl, 'HORA': ahora.strftime('%H:%M')}.items(): _pon(rec, pfl, k, v)
        _escribe_en(pend, phl + slot*prl, bytes(rec))
        hdr = bytearray(pb[:4]); _fecha_hdr(hdr, ahora); _escribe_en(pend, 0, bytes(hdr))
        if os.path.isfile(enc):
            with open(enc, 'rb') as f: eb = bytearray(f.read())
            en, ehl, erl, efl = _layout(eb)
            if en >= 1:
                er = bytearray(eb[ehl:ehl+erl])
                for k, v in {'ARTS': arts, 'IVA': iva, 'IMPORTE_MN': total, 'IMPORTE_DL': dl, 'HORA': ahora.strftime('%H:%M')}.items(): _pon(er, efl, k, v)
                _escribe_en(enc, ehl, bytes(er))
    except Exception:
        for a in (pend, det, bak, enc):
            try:
                if os.path.isfile(os.path.join(resp, os.path.basename(a))): shutil.copy2(os.path.join(resp, os.path.basename(a)), a)
            except OSError: pass
        raise
    return {'archivo': archivo, 'ref': _campo(rec, pfl, 'REF').decode('latin-1').strip(), 'folio': _campo(rec, pfl, 'FOL_CTA_P').decode('latin-1').strip(),
            'total': total, 'agregado': agregado, 'respaldo': os.path.basename(resp)}

def _dbf_txt(x):
    """Texto leído de MrTienda (viene en cp850 leído como latin-1, por eso la Ñ salía como ¥): lo regresa a su letra real."""
    try: return str(x or '').encode('latin-1').decode('cp850')
    except (UnicodeEncodeError, UnicodeDecodeError): return str(x or '')

# ---------------------------------------------------------------- alta de clientes en Mr. Tienda (30.7.75)
# Índices CDX de FoxPro (compactos). Comprobado contra el alta real de MrTienda del 14-oct-2026 (cliente 009331):
# mismas llaves en las 9 etiquetas de CLIENTES.CDX y las 2 de CTES_BQ.CDX, mismas 5 páginas nuevas y mismo tamaño.
class CdxError(Exception): pass
class _CdxTag:
    def __init__(s, cdx, name, off):
        s.c = cdx; s.name = name; s.off = off; b = cdx.b
        s.kl = struct.unpack('<H', b[off+12:off+14])[0]
        el = struct.unpack('<H', b[off+504:off+506])[0]   # largo de la expresión de la llave; la expresión empieza en +512
        s.expr = bytes(b[off+512:off+512+el]).split(b'\0')[0].decode('latin-1')
        s.fill = b' '
    @property
    def root(s): return struct.unpack('<i', s.c.b[s.off:s.off+4])[0]
    @root.setter
    def root(s, v): s.c.b[s.off:s.off+4] = struct.pack('<i', v); s.c.dirty.add(s.off)
class _Cdx:
    N = 512
    def __init__(s, b):
        s.b = bytearray(b); s.dirty = set(); s.nuevas = []; s.largo0 = len(s.b)
        if len(s.b) % s.N or len(s.b) < 1536: raise CdxError('El índice no tiene el tamaño esperado.')
        s.tags = {}
        for k, r in s.cadena(_CdxTag(s, '', 0)):
            nm = k.rstrip(b' \0').decode('latin-1'); s.tags[nm] = _CdxTag(s, nm, r)
    def node(s, p):
        if p < 0 or p + s.N > len(s.b) or p % s.N: raise CdxError('Página fuera del índice.')
        return struct.unpack('<HHii', s.b[p:p+12])
    def _hdr(s, p, att, nk, l, r): s.b[p:p+12] = struct.pack('<HHii', att, nk, l, r); s.dirty.add(p)
    def hoja(s, p, t):
        b = s.b; att, nk, l, r = s.node(p)
        if not att & 2: raise CdxError('Se esperaba una hoja.')
        rmask = struct.unpack('<I', b[p+14:p+18])[0]; dmask, tmask, rb, db, tb, nb = b[p+18:p+24]
        out = []; kp = p + s.N; prev = b''
        for i in range(nk):
            e = int.from_bytes(b[p+24+i*nb:p+24+(i+1)*nb], 'little')
            rec = e & rmask; dup = (e >> rb) & dmask; trl = (e >> (rb+db)) & tmask; n = t.kl - dup - trl
            if n < 0 or kp - n < p + 24 + nk*nb: raise CdxError('Hoja dañada.')
            kp -= n; key = prev[:dup] + bytes(b[kp:kp+n]) + t.fill*trl; out.append((key, rec)); prev = key
        return out, (rmask, dmask, tmask, rb, db, tb, nb)
    def _hoja_bytes(s, ents, prm, t):
        rmask, dmask, tmask, rb, db, tb, nb = prm; info = bytearray(); keys = bytearray(); prev = None
        for key, rec in ents:
            if rec > rmask: raise CdxError('Hay demasiados clientes para este índice; usa Reindexar en Mr. Tienda.')
            trl = min(len(key) - len(key.rstrip(t.fill)), tmask); dup = 0
            if prev is not None:
                lim = min(t.kl - trl, dmask, len(prev.rstrip(t.fill)))   # como FoxPro: solo contra lo escrito de la llave anterior
                while dup < lim and prev[dup] == key[dup]: dup += 1
            info += (rec | (dup << rb) | (trl << (rb+db))).to_bytes(nb, 'little'); keys[0:0] = key[dup:t.kl-trl]; prev = key
        free = 488 - len(info) - len(keys)
        if free < 0: return None
        body = bytearray(500); body[0:2] = struct.pack('<H', free); body[2:6] = struct.pack('<I', rmask)
        body[6:12] = bytes([dmask, tmask, rb, db, tb, nb]); body[12:12+len(info)] = info
        if keys: body[500-len(keys):] = keys
        return body
    def _hoja_pon(s, p, ents, prm, t, att, l, r):
        body = s._hoja_bytes(ents, prm, t)
        if body is None: raise CdxError('La hoja no cabe.')
        s._hdr(p, att, len(ents), l, r); s.b[p+12:p+s.N] = body
    def interior(s, p, t):
        att, nk, l, r = s.node(p); out = []
        if 12 + nk*(t.kl+8) > s.N: raise CdxError('Página interior dañada.')
        for i in range(nk):
            o = p + 12 + i*(t.kl+8); out.append((bytes(s.b[o:o+t.kl]),) + struct.unpack('>II', s.b[o+t.kl:o+t.kl+8]))
        return out
    def _cap(s, t): return 500 // (t.kl + 8)
    def _int_pon(s, p, ents, t, att, l, r):
        if len(ents) > s._cap(t): raise CdxError('La página interior no cabe.')
        body = bytearray(500)
        for i, (k, rec, ch) in enumerate(ents):
            o = i*(t.kl+8); body[o:o+t.kl] = k; body[o+t.kl:o+t.kl+8] = struct.pack('>II', rec, ch)
        s._hdr(p, att, len(ents), l, r); s.b[p+12:p+s.N] = body
    def cadena(s, t):
        p = t.root; vistos = 0
        while not s.node(p)[0] & 2:
            p = s.interior(p, t)[0][2]; vistos += 1
            if vistos > 20: raise CdxError('Árbol del índice dañado.')
        out = []; vistos = set()
        while p != -1:
            if p in vistos: raise CdxError('Cadena de hojas en círculo.')
            vistos.add(p); e, _ = s.hoja(p, t); out += e; p = s.node(p)[3]
        return out
    def _pagina(s):
        p = len(s.b); s.b += bytes(s.N); s.nuevas.append(p); return p
    def insertar(s, t, key, rec):
        if len(key) != t.kl: raise CdxError('Llave de largo incorrecto.')
        item = (key, rec); camino = []; p = t.root
        while not s.node(p)[0] & 2:
            ents = s.interior(p, t)
            i = next((j for j, (k, r, ch) in enumerate(ents) if (k, r) >= item), len(ents) - 1)
            camino.append((p, i)); p = ents[i][2]
        ents, prm = s.hoja(p, t); att, nk, l, r = s.node(p)
        ents.insert(next((j for j, e in enumerate(ents) if e > item), len(ents)), item)
        if s._hoja_bytes(ents, prm, t) is not None:
            s._hoja_pon(p, ents, prm, t, att, l, r); rep = (p, ents[-1]); nuevo = None
        else:
            medio = (len(ents) + 1) // 2   # lo más parejo posible, pero que las dos mitades quepan en bytes
            h = next((m for d in range(len(ents)) for m in (medio - d, medio + d) if 0 < m < len(ents)
                      and s._hoja_bytes(ents[:m], prm, t) is not None and s._hoja_bytes(ents[m:], prm, t) is not None), None)
            if h is None: raise CdxError('No se pudo dividir la hoja del índice.')
            a, z = ents[:h], ents[h:]; q = s._pagina()
            s._hoja_pon(p, a, prm, t, att & ~1, l, q); s._hoja_pon(q, z, prm, t, 2, p, r)
            if r != -1: x = s.node(r); s._hdr(r, x[0], x[1], q, x[3])
            rep = (p, a[-1]); nuevo = (q, z[-1])
            if att & 1:
                nr = s._pagina(); s._int_pon(nr, [a[-1] + (p,), z[-1] + (q,)], t, 1, -1, -1); t.root = nr; return
        while camino:
            p, i = camino.pop(); ents = s.interior(p, t); att, nk, l, r = s.node(p)
            ents[i] = rep[1] + (rep[0],)
            if nuevo: ents.insert(i+1, nuevo[1] + (nuevo[0],))
            if len(ents) <= s._cap(t):
                s._int_pon(p, ents, t, att, l, r); rep = (p, ents[-1][:2]); nuevo = None
            else:
                h = (len(ents) + 1) // 2; a, z = ents[:h], ents[h:]; q = s._pagina()
                s._int_pon(p, a, t, att & ~1, l, q); s._int_pon(q, z, t, 0, p, r)
                if r != -1: x = s.node(r); s._hdr(r, x[0], x[1], q, x[3])
                rep = (p, a[-1][:2]); nuevo = (q, z[-1][:2])
                if att & 1:
                    nr = s._pagina(); s._int_pon(nr, [a[-1][:2] + (p,), z[-1][:2] + (q,)], t, 1, -1, -1); t.root = nr; return
    def version_mas_1(s):
        v = struct.unpack('>I', s.b[8:12])[0]; s.b[8:12] = struct.pack('>I', (v + 1) & 0xffffffff); s.dirty.add(0)
    def revisar(s, t):
        """Revisa el árbol completo (orden, llaves de los interiores, hermanos) y regresa todas las llaves."""
        hojas = []
        def baja(p, nivel):
            if nivel > 20: raise CdxError('Árbol del índice dañado.')
            if s.node(p)[0] & 2:
                e, _ = s.hoja(p, t); hojas.append(p); return e
            todo = []
            for k, r, ch in s.interior(p, t):
                e = baja(ch, nivel + 1)
                if not e or e[-1] != (k, r): raise CdxError(f'Índice {t.name}: llave de interior no coincide.')
                todo += e
            return todo
        todo = baja(t.root, 0)
        if any(todo[i] > todo[i+1] for i in range(len(todo) - 1)): raise CdxError(f'Índice {t.name}: fuera de orden.')
        for i, p in enumerate(hojas):
            att, nk, l, r = s.node(p)
            if l != (hojas[i-1] if i else -1) or r != (hojas[i+1] if i + 1 < len(hojas) else -1): raise CdxError(f'Índice {t.name}: hojas mal ligadas.')
        return todo
    def cambios(s):
        """(posición, bytes) a escribir: páginas tocadas y páginas nuevas al final."""
        out = [(p, bytes(s.b[p:p+s.N])) for p in sorted({(d // s.N) * s.N for d in s.dirty}) if p < s.largo0]
        if len(s.b) > s.largo0: out.append((s.largo0, bytes(s.b[s.largo0:])))
        return out

def fupper(raw):
    """La función fUPPER de MrTienda (sacada de sus índices): a-z a mayúsculas; | } y todo lo que no es ASCII se vuelve espacio."""
    return bytes((c - 32) if 97 <= c <= 122 else (32 if c > 122 else c) for c in raw)

def _cp850(v, ln):
    return str(v or '').upper().encode('cp850', 'replace')[:ln].ljust(ln, b' ')
# Llaves de cada etiqueta de CLIENTES.CDX y CTES_BQ.CDX (las expresiones tal como las tiene MrTienda)
LLAVES_CLIENTES = {
    'CLIENTES->COD_CTE': lambda f: f('COD_CTE'),
    'CLIENTES->COD_GIRO+fUPPER(CLIENTES->CTE_NOMBRE)': lambda f: f('COD_GIRO') + fupper(f('CTE_NOMBRE')),
    'fUPPER(CLIENTES->CTE_NOMBRE)': lambda f: fupper(f('CTE_NOMBRE')),
    'CLIENTES->COD_ESCALA + fUPPER(CLIENTES->CTE_NOMBRE)': lambda f: f('COD_ESCALA') + fupper(f('CTE_NOMBRE')),
    'CLIENTES->COD_VENDED + fUPPER(CLIENTES->CTE_NOMBRE)': lambda f: f('COD_VENDED') + fupper(f('CTE_NOMBRE')),
    'CLIENTES->COD_UNICO': lambda f: f('COD_UNICO'),
    'CLIENTES->WEB_PASS': lambda f: f('WEB_PASS'),
    'CLIENTES->CTE_RFC': lambda f: f('CTE_RFC'),
    'CLIENTES->ID_CTE_WOO': None,   # numérico: el cliente nuevo lleva 0, igual que casi todos
}
LLAVES_CTES_BQ = {'CTES_BQ->COD_CTE': lambda f: f('COD_CTE'), 'fUPPER(CTES_BQ->CTE_NOMBRE)': lambda f: fupper(f('CTE_NOMBRE'))}

# Formato de la comanda de cocina (versión 30.6.26): Jorge lo elige en el panel → COMANDERO → Comanda de cocina.
# Tamaños de la Epson (ESC !): normal = negritas; alto = negritas y doble de alto; grande = doble de alto y de ancho.
TAM_COMANDA = {'normal': b'\x08', 'alto': b'\x18', 'grande': b'\x38'}
FMT_COMANDA = {'platillo': 'grande', 'abajo': 'grande', 'espacio': 1}   # el que eligió Jorge el 13-oct-2026
def fmt_comanda(f=None):
    f = dict(FMT_COMANDA, **{k: v for k, v in (f or {}).items() if k in FMT_COMANDA})
    for k in ('platillo', 'abajo'):
        if f[k] not in TAM_COMANDA: f[k] = FMT_COMANDA[k]
    try: f['espacio'] = max(0, min(3, int(f['espacio'])))
    except (TypeError, ValueError): f['espacio'] = 1
    return f

def comanda_bytes(ref, mesero, renglones, folio, nueva, ahora, canal=None, fmt=None):
    """Comanda de cocina en ESC/POS (versión 30.4, como la pidió Jorge): arriba solo cuenta nueva, mesero y hora;
    los platillos; y abajo, en grande, una sola vez: COMER AQUI + mesa, RECOGER AQUI / SERVICIO A DOMICILIO + nombre,
    o DIDI / UBER + los últimos 3 números de la clave."""
    ESC, GS = b'\x1b', b'\x1d'
    e = lambda x: str(x).encode('cp850', 'replace')
    n_ = re.match(r'^M\s*(\d+\s*(?:A|B|AB)?)$', ref.strip(), re.I)
    modos = {r['COD_ESCALA'] for r in renglones}
    canal = canal or {}
    c_ = canal.get('canal') if canal.get('canal') in CANALES else None
    if c_ in ('didi', 'uber'):
        dig = re.sub(r'\D', '', canal.get('pedido', '')) or canal.get('pedido', '')
        abajo = [c_.upper(), dig[-3:]]
    elif c_:
        abajo = [CANALES[c_], (canal.get('nombre') or ref).upper()[:21]]
    elif n_:
        mt = re.sub(r'\s+', '', n_.group(1)).upper(); mt = re.sub(r'^(\d+)AB$', r'\1A+\1B', mt)
        abajo = ['COMER AQUI' if modos != {'02'} else 'PARA LLEVAR', 'MESA ' + mt]
    else:
        abajo = ['PARA LLEVAR' if modos == {'02'} else 'COMER AQUI', _dbf_txt(ref).upper()[:21]]
    F = fmt_comanda(fmt)
    principal = '02' if (c_ or abajo[0] == 'PARA LLEVAR') else '01'      # lo normal de esta cuenta; solo se marca el platillo que sea distinto
    t = ESC + b'@' + ESC + b't\x02' + ESC + b'a\x01'
    t += ESC + b'!\x08' + e('CUENTA NUEVA' if nueva else 'SE AGREGA A LA CUENTA') + ESC + b'!\x00' + b'\n' + ESC + b'a\x00'
    t += e('Mesero: ') + ESC + b'!\x08' + e(_dbf_txt(mesero).upper()) + ESC + b'!\x00' + b'\n'
    t += e(f'Enviado: {ahora.strftime("%d/%m/%Y %H:%M")}   Cuenta #{folio[-5:].lstrip("0")}') + b'\n'
    t += b'-' * 42 + b'\n'
    for r in renglones:
        cant = int(r['CANTIDAD']) if float(r['CANTIDAD']).is_integer() else r['CANTIDAD']
        t += ESC + b'!' + TAM_COMANDA[F['platillo']] + e(f'{cant}  {_dbf_txt(r["DES_CANTAR"])}') + b'\n' + ESC + b'!\x00'  # tamaño que eligió Jorge
        for v in [v for v in (r.get('_det') or '').split(' · ') if v]:
            t += e('     - ' + _dbf_txt(v).upper()) + b'\n'
        if r.get('VAR_LIBRE'): t += e('     * NOTA: ' + r['VAR_LIBRE']) + b'\n'
        if r['COD_ESCALA'] != principal:   # solo si este platillo va distinto al resto
            t += ESC + b'!\x08' + e('     > ' + ('PARA LLEVAR' if r['COD_ESCALA'] == '02' else 'COMER AQUI')) + ESC + b'!\x00' + b'\n'
        t += b'\n' * F['espacio']
    t += b'-' * 42 + b'\n' + b'\n' + ESC + b'a\x01'      # un solo renglón entre la raya y "para quién es" (30.7.2: antes eran 3 y ocupaba mucho papel)
    t += ESC + b'!' + TAM_COMANDA[F['abajo']] + e(abajo[0]) + b'\n' + e(abajo[1]) + ESC + b'!\x00' + b'\n'      # abajo: para quién es, del tamaño que eligió Jorge
    t += e('Enviado desde el COMANDERO') + b'\n' + b'\n' * 6 + GS + b'VB\x0a'
    return t

def comanda_texto(datos):
    # vista previa en texto de la comanda (para revisarla sin impresora)
    out = re.sub(rb'\x1d.{3}$', b'', datos, flags=re.S)
    out = re.sub(rb'\x1b[@]|\x1b[ta!E].|\x1dB.', b'', out)
    return out.decode('cp850', 'replace')

def comanda_en_pantalla(datos, ancho=42):
    """Lee la comanda ESC/POS tal como va a la impresora y la regresa en renglones (texto, tamaño, alineación) para verla en el panel."""
    out = []; flags = 0; al = 0; cur = []; i = 0; n = len(datos)
    def corta():
        nonlocal cur
        out.append({'al': al, 'seg': cur}); cur = []
    while i < n:
        b = datos[i]
        if b == 0x1b and i + 1 < n:
            c = datos[i + 1]
            if c == ord('!'): flags = datos[i + 2]; i += 3; continue
            if c == ord('a'): al = datos[i + 2]; i += 3; continue
            if c == ord('t'): i += 3; continue
            if c == ord('@'): flags = 0; al = 0; i += 2; continue
            i += 2; continue
        if b == 0x1d:
            if i + 1 < n and datos[i + 1] == ord('V'): break   # corte de papel
            i += 2; continue
        if b == 0x0a: corta(); i += 1; continue
        j = i
        while j < n and datos[j] not in (0x0a, 0x1b, 0x1d): j += 1
        txt = datos[i:j].decode('cp850', 'replace'); i = j
        w = ancho // (2 if flags & 0x20 else 1)
        while txt:
            usado = sum(len(s_['t']) * (2 if s_['f'] & 0x20 else 1) for s_ in cur) // (2 if flags & 0x20 else 1)
            cabe = max(1, w - usado); cur.append({'t': txt[:cabe], 'f': flags}); txt = txt[cabe:]
            if txt: corta()
    if cur: corta()
    while out and not out[-1]['seg']: out.pop()
    return out

def _imprimir_raw(impresora, datos):
    """Manda bytes ESC/POS tal cual a una impresora de Windows (como el 'driver Windows' de MrTienda)."""
    if os.name != 'nt': raise RuntimeError('Solo se puede imprimir en la computadora de la caja.')
    import ctypes
    from ctypes import wintypes
    ws = ctypes.WinDLL('winspool.drv', use_last_error=True)
    class DOC_INFO_1(ctypes.Structure): _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR), ('pDatatype', wintypes.LPWSTR)]
    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]; ws.OpenPrinterW.restype = wintypes.BOOL
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DOC_INFO_1)]; ws.StartDocPrinterW.restype = wintypes.DWORD
    ws.StartPagePrinter.argtypes = [wintypes.HANDLE]; ws.EndPagePrinter.argtypes = [wintypes.HANDLE]
    ws.EndDocPrinter.argtypes = [wintypes.HANDLE]; ws.ClosePrinter.argtypes = [wintypes.HANDLE]
    ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]; ws.WritePrinter.restype = wintypes.BOOL
    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(impresora, ctypes.byref(h), None): raise RuntimeError('No encontré la impresora "' + impresora + '"')
    try:
        di = DOC_INFO_1('Comanda', None, 'RAW')
        if not ws.StartDocPrinterW(h, 1, ctypes.byref(di)): raise RuntimeError('La impresora no aceptó el trabajo (' + str(ctypes.get_last_error()) + ')')
        try:
            ws.StartPagePrinter(h)
            buf = ctypes.create_string_buffer(datos, len(datos)); n = wintypes.DWORD()
            if not ws.WritePrinter(h, buf, len(datos), ctypes.byref(n)): raise RuntimeError('No se pudo mandar a la impresora (' + str(ctypes.get_last_error()) + ')')
            ws.EndPagePrinter(h)
        finally: ws.EndDocPrinter(h)
    finally: ws.ClosePrinter(h)

# ---------------------------------------------------------------- reglas del negocio
FORMAS = {'001': 'Efectivo', '004': 'Tarjeta crédito', '006': 'Tarjeta débito', '009': 'Transferencia', '012': 'Tarjeta crédito/débito'}
ESC = {'01': 'Comedor', '02': 'Recoger aquí'}
DIDI = re.compile(r'DIDI|UBER|^D ?-? ?79\d{4}', re.I)
FNAME = re.compile(r'^([PVDA])(\d{2})(\d{2})(\d{2})(\d)\.001$', re.I)

def cat_in(r):
    u = r.upper()
    if DIDI.search(r): return 'Didi'
    if re.search(r'JORGE|NOMINA|TARJETA|DEBITO|REINGRES|REGRES', u): return 'Reingresos'
    if re.search(r'CORTE|TURNO|FERIA', u): return 'Cambio entre turnos'
    return 'Otros ingresos'

def cat_out(r):
    u = r.upper()
    if re.search(r'PROP|PR.?PINA|PIROPIN', u): return 'Propinas'
    if re.search(r'^VALE|VALE ', u): return 'Vales'
    if re.search(r'PAGO DE |NOMINA', u): return 'Pagos a personal'
    if re.search(r'JORGE|RETIR|FERIA', u): return 'Retiros'
    return 'Gastos y compras'

def minutos(a, b):
    # minutos entre la apertura (primer producto capturado) y el cobro
    try:
        d = (int(b[:2]) * 60 + int(b[3:5])) - (int(a[:2]) * 60 + int(a[3:5]))
        return d if d >= 0 else d + 1440
    except (ValueError, TypeError, IndexError):
        return None

def amt(p):
    # lo que pagó el cliente menos el cambio, cada uno convertido a pesos con su tipo de cambio
    return (p.get('PAGO') or 0) * (p.get('TC') or 1) - (p.get('CAMBIO') or 0) * (p.get('TC_CAMBIO') or 1)

def denom(ref, q, tc):
    r = ref.upper()
    if 'DOLAR' in r: return q * tc
    m = re.search(r'([\d.]+)', r)
    try: return float(m.group(1)) * q if m else 0
    except ValueError: return 0

# ---------------------------------------------------------------- programa para Google Drive (Apps Script que pega Jorge en su cuenta)
SCRIPT_DRIVE = r'''// Fotos de tickets de La Holandesa en tu Google Drive.
// Solo la computadora de la caja que conoce esta clave puede subir o borrar fotos.
const SECRETO = '{SECRETO}';
const CARPETA = 'Fotos de tickets - La Holandesa';

function carpeta_() {
  const it = DriveApp.getFoldersByName(CARPETA);
  return it.hasNext() ? it.next() : DriveApp.createFolder(CARPETA);
}

function doPost(e) {
  let r;
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.secreto !== SECRETO) throw new Error('no autorizado');
    if (d.accion === 'subir') {
      const blob = Utilities.newBlob(Utilities.base64Decode(d.datos), 'image/jpeg', d.nombre || 'ticket.jpg');
      r = { ok: true, id: carpeta_().createFile(blob).getId() };
    } else if (d.accion === 'borrar') {
      const it = carpeta_().getFiles(); let n = 0, papelera = 0;
      while (it.hasNext()) {
        const f = it.next();
        try { Drive.Files.remove(f.getId()); } catch (x) { f.setTrashed(true); papelera++; }
        n++;
      }
      r = { ok: true, borradas: n, papelera: papelera };
    } else if (d.accion === 'probar') {
      r = { ok: true, carpeta: carpeta_().getName() };
    } else throw new Error('acción no válida');
  } catch (err) { r = { ok: false, error: String(err && err.message || err) }; }
  return ContentService.createTextOutput(JSON.stringify(r)).setMimeType(ContentService.MimeType.JSON);
}

// Ejecuta esta función una vez desde el editor para dar los permisos.
function autorizar() { carpeta_(); }
'''

# ---------------------------------------------------------------- cajeros y lectura de tickets: utilidades
SESIONES = {}   # token -> (nombre, vence)
SESIONES_M = {} # comandero: token -> (id del mesero, vence)
HUELLA_M = {}   # 30.7.58: comandero: token -> cuándo se puso la huella (usuario "solo huella")
SESIONES_R = {} # captura del rol de descansos (encargada): token -> (nombre, vence)
FALLOS = {}     # ip -> [intentos, bloqueado_hasta]
# ------------------------------------------------ PDF de bienvenida al comandero (sin paquetes extra)
# Anchos de Helvetica (1/1000 de punto) para acomodar renglones; lo que no esté vale 556.
_HW = dict(zip(' !"#$%&\'()*+,-./0123456789:;<=>?@', [278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278] + [556]*10 + [278,278,584,584,584,556,1015]))
_HW.update(dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', [667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611])))
_HW.update(dict(zip('abcdefghijklmnopqrstuvwxyz', [556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500])))
_HWB = dict(_HW); _HWB.update(dict(zip('abcdefghijklmnopqrstuvwxyz', [556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,333,611,556,778,556,556,500])))
_HWB.update({k: 722 for k in 'ABCDEFGHKNRUV'})
def _ancho(t, tam, negrita=False):
    w = _HWB if negrita else _HW
    import unicodedata
    return sum(w.get(unicodedata.normalize('NFD', c)[0], 556) for c in t) * tam / 1000.0
def _renglones(t, tam, ancho, negrita=False):
    out, cur = [], ''
    for p in t.split(' '):
        prueba = (cur + ' ' + p).strip()
        if cur and _ancho(prueba, tam, negrita) > ancho: out.append(cur); cur = p
        else: cur = prueba
    if cur: out.append(cur)
    return out
def _pdf_txt(t):
    b = t.encode('cp1252', 'replace')
    return b.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')

def _png_rgb_alfa(png):
    """Abre un PNG de 8 bits (RGB o RGBA, sin entrelazar) y regresa ancho, alto, RGB y alfa sin filtros."""
    import zlib
    if png[:8] != b'\x89PNG\r\n\x1a\n': raise ValueError('No es PNG')
    i, idat, w = 8, b'', 0
    while i < len(png):
        n = struct.unpack('>I', png[i:i + 4])[0]; t = png[i + 4:i + 8]; c = png[i + 8:i + 8 + n]; i += 12 + n
        if t == b'IHDR': w, h, bits, tipo, _, _, inter = struct.unpack('>IIBBBBB', c)
        elif t == b'IDAT': idat += c
        elif t == b'IEND': break
    if bits != 8 or tipo not in (2, 6) or inter: raise ValueError('PNG no compatible')
    bpp = 4 if tipo == 6 else 3; fila = w * bpp; raw = zlib.decompress(idat)
    prev = bytearray(fila); px = bytearray()
    for y in range(h):
        f = raw[y * (fila + 1)]; cur = bytearray(raw[y * (fila + 1) + 1:(y + 1) * (fila + 1)])
        for x in range(fila):
            a = cur[x - bpp] if x >= bpp else 0; b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: cur[x] = (cur[x] + a) & 255
            elif f == 2: cur[x] = (cur[x] + b) & 255
            elif f == 3: cur[x] = (cur[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        px += cur; prev = cur
    if bpp == 3: return w, h, bytes(px), None
    return w, h, bytes(b for k in range(0, len(px), 4) for b in px[k:k + 3]), bytes(px[3::4])

# Para el saludo del PDF: "Bienvenida" o "Bienvenido" según el primer nombre
NOMBRES_MUJER = {'berenice', 'beatriz', 'raquel', 'isabel', 'carmen', 'mercedes', 'dolores', 'guadalupe', 'pilar', 'rocio', 'abigail', 'esther', 'ruth',
                 'ingrid', 'miriam', 'noemi', 'ines', 'lucero', 'nayeli', 'itzel', 'yaretzi', 'abril', 'belen', 'sol', 'flor', 'luz', 'maribel', 'marisol',
                 'araceli', 'lizbeth', 'elizabeth', 'ivonne', 'janet', 'jazmin', 'yazmin', 'karen', 'liz', 'monserrat', 'montserrat', 'citlali', 'leslie',
                 'nicole', 'michelle', 'estefany', 'evelyn', 'jocelyn', 'kimberly', 'lourdes', 'consuelo', 'socorro', 'soledad', 'concepcion', 'asuncion',
                 'irene', 'alexis', 'yuridia', 'nohemi', 'aylin', 'ailin', 'zoe', 'denisse', 'denise', 'yanet', 'yaneth', 'arely', 'nancy', 'cindy', 'betty',
                 'mary', 'ruby', 'heidi', 'rubi', 'ivette', 'lisset', 'yesenia', 'genesis', 'nathaly', 'natali', 'yamileth', 'maite', 'anahi', 'noely', 'sarai'}
NOMBRES_HOMBRE_A = {'luca', 'joshua', 'bautista', 'garcia', 'nikita', 'elia'}
def _sin_acento(t):
    import unicodedata
    return ''.join(ch for ch in unicodedata.normalize('NFD', t) if unicodedata.category(ch) != 'Mn')

def pdf_comandero(nombre, url, horario='', candado=False, logo=None):
    """Hoja de bienvenida al comandero para mandarla por WhatsApp (2 páginas: cómo entrar y cómo usarlo). La clave NUNCA va aquí."""
    import zlib
    W, H, M = 612, 792, 54
    VERDE, VERDE_S, NARANJA, TINTA, GRIS = (0.114, 0.361, 0.231), (0.886, 0.937, 0.898), (0.886, 0.443, 0.114), (0.082, 0.137, 0.102), (0.365, 0.431, 0.384)
    ROJO, ROJO_S = (0.70, 0.13, 0.11), (0.99, 0.91, 0.89)
    paginas = [[]]; ann = [[]]
    c = paginas[0]
    col = lambda rgb, op='rg': c.append('%.3f %.3f %.3f %s' % (rgb + (op,)))
    def texto(x, y, t, tam=12, negrita=False, rgb=TINTA):
        col(rgb); c.append(('BT /%s %g Tf %.1f %.1f Td (' % ('F2' if negrita else 'F1', tam, x, y)).encode() + _pdf_txt(t) + b') Tj ET')
    def parrafo(x, y, t, tam=12, ancho=W - 2 * M, negrita=False, rgb=TINTA, inter=1.38):
        for r in _renglones(t, tam, ancho, negrita): texto(x, y, r, tam, negrita, rgb); y -= tam * inter
        return y
    def alto_parrafo(t, tam=12, ancho=W - 2 * M, inter=1.38): return len(_renglones(t, tam, ancho)) * tam * inter
    def pie():
        col(VERDE_S, 'RG'); c.append('0.8 w %d 58 m %d 58 l S' % (M, W - M))
        texto(M, 40, 'La Holandesa · Av. Reforma 1366, Ensenada · ' + datetime.date.today().strftime('%d/%m/%Y'), 9, False, GRIS)
        texto(W - M - 60, 40, 'Hoja %d de 2' % len(paginas), 9, False, GRIS)
    def nota_clave(y):
        """Recuadro rojo: no compartas tu clave."""
        col(ROJO_S); c.append('%d %.1f %d 46 re f' % (M, y - 34, W - 2 * M))
        col(ROJO, 'RG'); c.append('1.5 w %d %.1f %d 46 re S' % (M, y - 34, W - 2 * M))
        texto(M + 16, y - 16, 'NOTA: No compartas tu clave, es confidencial.', 14, True, ROJO)
        return y - 60
    def punto(n, y, t, tam=12):
        col(NARANJA); c.append('%.1f %.1f m %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c f' % _circulo(M + 10, y + 4, 10))
        texto(M + (6.5 if n < 10 else 3.5), y, str(n), 12 if n < 10 else 10, True, (1, 1, 1))
        return parrafo(M + 30, y, t, tam, W - 2 * M - 30) - 8
    # ---------------- hoja 1: bienvenida, dirección y cómo entrar
    col(VERDE); c.append('0 %d %d %d re f' % (H - 150, W, 150))
    hay_logo = False
    if logo:
        try: lw, lh, rgb_, alfa = _png_rgb_alfa(logo); hay_logo = True
        except Exception as e: log('PDF comandero: no pude usar el logo: ' + str(e))
    tx = M
    if hay_logo:
        alto = 112; ancho_l = alto * lw / lh
        c.append('q %.1f 0 0 %.1f %.1f %.1f cm /Im1 Do Q' % (ancho_l, alto, M, H - 131)); tx = M + ancho_l + 20
    texto(tx, H - 72, 'Comandero', 30, True, (1, 1, 1))
    texto(tx, H - 98, 'Restaurante La Holandesa', 15, False, (1, 1, 1))
    y = H - 196
    pila = (nombre.split() or [''])[0].strip().lower()
    saludo = 'Bienvenida' if (pila.endswith('a') or _sin_acento(pila) in NOMBRES_MUJER) and _sin_acento(pila) not in NOMBRES_HOMBRE_A else 'Bienvenido'
    texto(M, y, '¡' + saludo + ', ' + nombre.title() + '!', 22, True); y -= 26
    y = parrafo(M, y, 'Ya tienes acceso al comandero. Desde tu teléfono puedes ver el menú, abrir cuentas y mandar los pedidos a cocina. Las cuentas salen a tu nombre en la caja. En la hoja 2 vienen las instrucciones para usarlo.', 12, rgb=GRIS) - 14
    col(VERDE_S); c.append('%d %.1f %d 74 re f' % (M, y - 62, W - 2 * M))
    col(VERDE, 'RG'); c.append('1.5 w %d %.1f %d 74 re S' % (M, y - 62, W - 2 * M))
    texto(M + 16, y - 10 - 8, 'TU DIRECCIÓN (tócala para abrir)', 10, True, VERDE)
    tam_u = 17 if _ancho(url, 17, True) < W - 2 * M - 32 else 13
    texto(M + 16, y - 46, url, tam_u, True, TINTA)
    ann[0].append((M, y - 62, W - M, y + 12, url)); y -= 100
    texto(M, y, 'Cómo entrar', 16, True, VERDE); y -= 26
    pasos = ['Conéctate al WiFi del restaurante. Sin ese WiFi el comandero no abre.',
             'Toca la dirección de arriba. En iPhone, si se abre dentro de WhatsApp, toca "Abrir en Safari".',
             'Escribe tu clave de 4 números. Te la da Jorge en persona; no viene en este documento.',
             'Déjalo como app con el logotipo: en Android toca los tres puntitos y "Agregar a pantalla de inicio" (o "Instalar"); en iPhone toca Compartir (el cuadrito con flecha) y "Agregar a pantalla de inicio".']
    for n, t in enumerate(pasos, 1): y = punto(n, y, t)
    y -= 8
    texto(M, y, 'Importante', 16, True, VERDE); y -= 26
    reglas = []
    if candado: reglas.append('Solo entras el día que te toca trabajar y en tu horario: abre 30 minutos antes de tu entrada y se cierra 1 hora después de tu salida. En tu día de descanso, falta o vacaciones no entra.')
    if horario: reglas.append('Tu horario: ' + horario.rstrip('. ') + '.')
    reglas.append('Todo lo que se mande con tu clave sale a tu nombre.')
    for r in reglas:
        col(VERDE); c.append('%.1f %.1f 5 5 re f' % (M + 2, y + 2))
        y = parrafo(M + 18, y, r, 12, W - 2 * M - 18) - 8
    nota_clave(y - 4)
    pie()
    # ---------------- hoja 2: cómo usar el comandero
    paginas.append([]); ann.append([]); c = paginas[-1]
    col(VERDE); c.append('0 %d %d 70 re f' % (H - 70, W))
    texto(M, H - 45, 'Cómo usar el comandero', 22, True, (1, 1, 1))
    y = H - 104
    uso = [
        'Al abrir el comandero escribe tu clave. También te la pide al empezar cada cuenta nueva: la cuenta queda a nombre de quien escribe la clave.',
        'Toca "+ NUEVA CUENTA" y elige: Comedor (la mesa y cuántas personas), Para llevar (nombre del cliente), Servicio a domicilio (busca al cliente por su teléfono), Didi (clave del pedido y si paga en efectivo o con tarjeta) o Uber (clave del pedido).',
        'Busca el platillo o elígelo por sección. Escoge sus opciones, si es para comer aquí o para llevar, y si hace falta escribe una nota para cocina (ej. sin cebolla). Toca Agregar.',
        'Revisa el pedido. Para quitar un platillo que todavía no mandas, toca el bote de basura "Quitar" y confirma. Lo que ya se mandó a cocina solo lo puede quitar la caja.',
        'Toca "Enviar a cocina". La comanda se imprime en cocina y la cuenta queda abierta en la caja a tu nombre.',
        'Para agregar algo a una cuenta que ya está abierta, tócala en "Mis cuentas", agrega los platillos y vuelve a tocar "Enviar a cocina". Solo se imprime lo nuevo.',
        'Pon atención a los avisos: ofrece el jugo del día en los desayunos y la soda italiana con la carta; en la comida del día lleva la sopa y el agua; a los 20 minutos te recuerda el postre.',
        'Si una comanda tarda, suena una alarma: a los 10 minutos en amarillo, a los 14 en rojo y a los 20 dice CRÍTICO. Revisa con cocina. Toca "Ya salió" cuando salga, o "No ha salido" para que te vuelva a avisar en 5 minutos. Las comandas que tardan quedan registradas.',
        'El cobro lo hace la caja. Cuando termines tu turno toca "Salir".',
        'Si algo no funciona, avísale a la encargada o a Jorge.',
    ]
    for n, t in enumerate(uso, 1): y = punto(n, y, t, 11)
    # cómo escribir la mesa en la caja (15-oct-2026): así el comandero la marca ocupada en el mapa
    y -= 6; col(VERDE_S); c.append('%d %.1f %d 2 re f' % (M, y + 8, W - 2 * M))
    texto(M, y - 12, 'Cómo escribir la mesa en la caja (referencia de la cuenta)', 13, True, VERDE); y -= 30
    texto(M, y, 'M + espacio + número + letra. Así la mesa sale ocupada en el mapa del comandero.', 11, False, GRIS); y -= 18
    filas = [('Mesa 3 (sola)', 'M 3'), ('Mesa 7 (sola)', 'M 7'), ('Mesa 1A', 'M 1A'), ('Mesa 1B', 'M 1B'), ('Mesas 1A y 1B juntas', 'M 1AB')]
    for k, (a_, b_) in enumerate(filas):
        x0 = M + (k % 2) * ((W - 2 * M) / 2); yy = y - (k // 2) * 18
        texto(x0, yy, a_ + ':', 11, False, TINTA); texto(x0 + 150, yy, b_, 12, True, VERDE)
    y -= ((len(filas) + 1) // 2) * 18 + 2
    texto(M, y, 'No escribas: M1A+B, M 1A-1B, 1A (sin la M) ni MESA UNO.', 11, True, ROJO); y -= 14
    nota_clave(y - 8)
    pie()
    conts = [b'\n'.join(x if isinstance(x, bytes) else x.encode('latin-1') for x in pg_) for pg_ in paginas]
    # armar el PDF
    objs = []
    def obj(b): objs.append(b); return len(objs)
    cat = obj(None); pags = obj(None)
    f1 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    f2 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    xo = b''
    if hay_logo:
        smask = ''
        if alfa:
            z = zlib.compress(alfa, 9)
            sm = obj(b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n' % (lw, lh, len(z)) + z + b'\nendstream')
            smask = ' /SMask %d 0 R' % sm
        z = zlib.compress(rgb_, 9)
        im = obj(b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode%s /Length %d >>\nstream\n' % (lw, lh, smask.encode(), len(z)) + z + b'\nendstream')
        xo = b' /XObject << /Im1 %d 0 R >>' % im
    kids = []
    for cont, an_ in zip(conts, ann):
        z = zlib.compress(cont, 9)
        st = obj(b'<< /Filter /FlateDecode /Length %d >>\nstream\n' % len(z) + z + b'\nendstream')
        an = [obj(b'<< /Type /Annot /Subtype /Link /Rect [%.1f %.1f %.1f %.1f] /Border [0 0 0] /A << /S /URI /URI (' % a[:4] + _pdf_txt(a[4]) + b') >> >>') for a in an_]
        kids.append(obj(b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >>%s >> /Contents %d 0 R /Annots [%s] >>' %
                        (pags, W, H, f1, f2, xo, st, b' '.join(b'%d 0 R' % a for a in an))))
    objs[cat - 1] = b'<< /Type /Catalog /Pages %d 0 R >>' % pags
    objs[pags - 1] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(b'%d 0 R' % k_ for k_ in kids), len(kids))
    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'); pos = []
    for k, b in enumerate(objs, 1): pos.append(len(out)); out += b'%d 0 obj\n' % k + b + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % p for p in pos)
    out += b'trailer\n<< /Size %d /Root %d 0 R /Info << /Title (' % (len(objs) + 1, cat) + _pdf_txt('Comandero ' + (nombre.split() or [''])[0].title()) + b') >> >>\nstartxref\n%d\n%%%%EOF\n' % x
    return bytes(out)

def pdf_descansos(nombre, url_wifi, url_ts='', logo=None, clave=''):
    """Hoja de bienvenida para la encargada: cómo entrar a capturar el rol de descansos (1 página). La clave solo va si Jorge lo pide."""
    import zlib
    W, H, M = 612, 792, 54
    VERDE, VERDE_S, NARANJA, NARANJA_S, TINTA, GRIS = (0.114, 0.361, 0.231), (0.886, 0.937, 0.898), (0.886, 0.443, 0.114), (1.0, 0.929, 0.835), (0.082, 0.137, 0.102), (0.365, 0.431, 0.384)
    ROJO, ROJO_S = (0.70, 0.13, 0.11), (0.99, 0.91, 0.89)
    c = []; ann = []
    col = lambda rgb, op='rg': c.append('%.3f %.3f %.3f %s' % (rgb + (op,)))
    def texto(x, y, t, tam=12, negrita=False, rgb=TINTA):
        col(rgb); c.append(('BT /%s %g Tf %.1f %.1f Td (' % ('F2' if negrita else 'F1', tam, x, y)).encode() + _pdf_txt(t) + b') Tj ET')
    def parrafo(x, y, t, tam=12, ancho=W - 2 * M, negrita=False, rgb=TINTA, inter=1.36):
        for r in _renglones(t, tam, ancho, negrita): texto(x, y, r, tam, negrita, rgb); y -= tam * inter
        return y
    def punto(n, y, t, tam=11):
        col(NARANJA); c.append('%.1f %.1f m %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c %.1f %.1f %.1f %.1f %.1f %.1f c f' % _circulo(M + 10, y + 4, 10))
        texto(M + 6.5, y, str(n), 12, True, (1, 1, 1))
        return parrafo(M + 30, y, t, tam, W - 2 * M - 30) - 6
    def caja(y, titulo, url, fondo, borde):
        col(fondo); c.append('%d %.1f %d 62 re f' % (M, y - 50, W - 2 * M))
        col(borde, 'RG'); c.append('1.5 w %d %.1f %d 62 re S' % (M, y - 50, W - 2 * M))
        texto(M + 16, y - 6, titulo, 10, True, borde)
        tam_u = 16 if _ancho(url, 16, True) < W - 2 * M - 32 else 12
        texto(M + 16, y - 36, url, tam_u, True, TINTA)
        if url.startswith('http'): ann.append((M, y - 50, W - M, y + 12, url))
        return y - 68
    # encabezado verde con el logotipo de Personal (los dos monitos)
    col(VERDE); c.append('0 %d %d %d re f' % (H - 140, W, 140))
    hay_logo = False
    if logo:
        try: lw, lh, rgb_, alfa = _png_rgb_alfa(logo); hay_logo = True
        except Exception as e: log('PDF descansos: no pude usar el logo: ' + str(e))
    tx = M
    if hay_logo:
        c.append('q 100 0 0 100 %d %d cm /Im1 Do Q' % (M, H - 120)); tx = M + 120
    texto(tx, H - 66, 'Rol de descansos', 28, True, (1, 1, 1))
    texto(tx, H - 92, 'Restaurante La Holandesa', 15, False, (1, 1, 1))
    y = H - 172
    pila = (nombre.split() or [''])[0].strip().lower()
    saludo = 'Bienvenida' if (pila.endswith('a') or _sin_acento(pila) in NOMBRES_MUJER) and _sin_acento(pila) not in NOMBRES_HOMBRE_A else 'Bienvenido'
    texto(M, y, '¡' + saludo + ', ' + (nombre.split() or [''])[0].title() + '!', 22, True); y -= 24
    y = parrafo(M, y, 'Desde tu teléfono vas a capturar el rol de descansos de cada semana (de martes a lunes): quién trabaja, quién descansa, faltas, vacaciones e incapacidades, y el horario de cada día. Lo que captures le llega a Jorge para la nómina.', 11, rgb=GRIS) - 10
    if clave:
        col(NARANJA_S); c.append('%d %.1f %d 46 re f' % (M, y - 34, W - 2 * M))
        col(NARANJA, 'RG'); c.append('1.5 w %d %.1f %d 46 re S' % (M, y - 34, W - 2 * M))
        texto(M + 16, y - 18, 'TU CLAVE:', 13, True, NARANJA); texto(M + 110, y - 20, '  '.join(clave), 22, True, TINTA)
        y -= 58
    y = caja(y, 'EN EL RESTAURANTE (conectada al WiFi) — tócala para abrir', url_wifi, VERDE_S, VERDE)
    if url_ts: y = caja(y, 'DESDE TU CASA (con Tailscale prendido) — tócala para abrir', url_ts, NARANJA_S, NARANJA)
    texto(M, y, 'Cómo entrar', 15, True, VERDE); y -= 22
    pasos = ['En el restaurante: conéctate al WiFi y toca la primera dirección.' + (' Desde tu casa: prende Tailscale en tu teléfono y toca la segunda.' if url_ts else ''),
             'Escribe tu clave de 4 números.' + ('' if clave else ' Te la da Jorge en persona; no viene en este documento.'),
             'Déjala como app: en Android toca los tres puntitos y "Agregar a pantalla de inicio"; en iPhone toca Compartir y "Agregar a pantalla de inicio".']
    for n, t in enumerate(pasos, 1): y = punto(n, y, t)
    y -= 2; texto(M, y, 'Cómo capturar', 15, True, VERDE); y -= 20
    uso = ['Arriba eliges la semana con las flechas. Los trabajadores salen por grupo: cocineros, meseros y ayudantes generales.',
           'Toca el día de un trabajador y elige: T trabaja, D descansa, F faltó, V vacaciones o I incapacidad. Si trabaja, elige la hora de entrada y de salida. Toca Guardar.',
           '"Copiar la semana anterior" llena la semana igual que la pasada; luego solo cambias lo diferente.',
           'Si una semana ya está pagada no se puede cambiar; avísale a Jorge. No ves sueldos ni nómina.']
    for n, t in enumerate(uso, 1): y = punto(n, y, t)
    if True:   # recuadro rojo: la clave es confidencial
        col(ROJO_S); c.append('%d %.1f %d 40 re f' % (M, y - 30, W - 2 * M))
        col(ROJO, 'RG'); c.append('1.5 w %d %.1f %d 40 re S' % (M, y - 30, W - 2 * M))
        texto(M + 16, y - 15, 'NOTA: No compartas tu clave, es confidencial.', 13, True, ROJO)
    col(VERDE_S, 'RG'); c.append('0.8 w %d 52 m %d 52 l S' % (M, W - M))
    texto(M, 36, 'La Holandesa · Av. Reforma 1366, Ensenada · ' + datetime.date.today().strftime('%d/%m/%Y'), 9, False, GRIS)
    cont = b'\n'.join(x if isinstance(x, bytes) else x.encode('latin-1') for x in c)
    objs = []
    def obj(b): objs.append(b); return len(objs)
    cat = obj(None); pags = obj(None)
    f1 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    f2 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    xo = b''
    if hay_logo:
        smask = ''
        if alfa:
            z = zlib.compress(alfa, 9)
            sm = obj(b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n' % (lw, lh, len(z)) + z + b'\nendstream')
            smask = ' /SMask %d 0 R' % sm
        z = zlib.compress(rgb_, 9)
        im = obj(b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode%s /Length %d >>\nstream\n' % (lw, lh, smask.encode(), len(z)) + z + b'\nendstream')
        xo = b' /XObject << /Im1 %d 0 R >>' % im
    z = zlib.compress(cont, 9)
    st = obj(b'<< /Filter /FlateDecode /Length %d >>\nstream\n' % len(z) + z + b'\nendstream')
    an = [obj(b'<< /Type /Annot /Subtype /Link /Rect [%.1f %.1f %.1f %.1f] /Border [0 0 0] /A << /S /URI /URI (' % a[:4] + _pdf_txt(a[4]) + b') >> >>') for a in ann]
    kid = obj(b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >>%s >> /Contents %d 0 R /Annots [%s] >>' %
              (pags, W, H, f1, f2, xo, st, b' '.join(b'%d 0 R' % a for a in an)))
    objs[cat - 1] = b'<< /Type /Catalog /Pages %d 0 R >>' % pags
    objs[pags - 1] = b'<< /Type /Pages /Kids [%d 0 R] /Count 1 >>' % kid
    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'); pos = []
    for k, b in enumerate(objs, 1): pos.append(len(out)); out += b'%d 0 obj\n' % k + b + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % p for p in pos)
    out += b'trailer\n<< /Size %d /Root %d 0 R /Info << /Title (' % (len(objs) + 1, cat) + _pdf_txt('Rol de descansos ' + (nombre.split() or [''])[0].title()) + b') >> >>\nstartxref\n%d\n%%%%EOF\n' % x
    return bytes(out)

def _circulo(cx, cy, r):
    k = 0.5523 * r
    return (cx + r, cy, cx + r, cy + k, cx + k, cy + r, cx, cy + r, cx - k, cy + r, cx - r, cy + k, cx - r, cy,
            cx - r, cy - k, cx - k, cy - r, cx, cy - r, cx + k, cy - r, cx + r, cy - k, cx + r, cy)

def ip_del_restaurante(ip):
    # WiFi del restaurante (red local) o la misma computadora. Tailscale (100.64.0.0/10) y fuera NO cuentan.
    import ipaddress
    try: a = ipaddress.ip_address((ip or '').split('%')[0])
    except ValueError: return False
    if getattr(a, 'ipv4_mapped', None): a = a.ipv4_mapped
    if a.version == 4 and a in ipaddress.ip_network('100.64.0.0/10'): return False        # Tailscale IPv4
    if a.version == 6 and a in ipaddress.ip_network('fd7a:115c:a1e0::/48'): return False  # Tailscale IPv6
    return a.is_loopback or a.is_private
FUERA_WIFI = 'El comandero solo funciona conectado al WiFi del restaurante.'
FUERA_TS = 'Tu usuario del comandero no tiene permiso de entrar por Tailscale. Jorge lo activa en el panel → Comandero → "Permitir Tailscale".'
def fuera_msg(ip):   # 30.7.59: por Tailscale el aviso dice qué permiso falta, no "solo WiFi"
    try:
        import ipaddress; a = ipaddress.ip_address((ip or '').split('%')[0])
        if (a.version == 4 and a in ipaddress.ip_network('100.64.0.0/10')) or (a.version == 6 and a in ipaddress.ip_network('fd7a:115c:a1e0::/48')): return FUERA_TS
    except ValueError: pass
    return FUERA_WIFI
SESION_FIN = 'Tu sesión terminó. Vuelve a entrar con tu clave o con tu huella.'
def huella_pin(sal, pin): return hashlib.sha256((sal + ':' + pin).encode()).hexdigest()
def verifica_pin(c, pin): return bool(c.get('sal')) and secrets.compare_digest(c.get('huella', ''), huella_pin(c['sal'], pin))
def sesion(tok):
    s_ = SESIONES.get(tok or '')
    if not s_ or s_[1] < time.time(): SESIONES.pop(tok or '', None); return None
    return s_[0]
def _num(v):
    try: return round(float(str(v).replace('$', '').replace(',', '')), 2)
    except (TypeError, ValueError): return 0.0
def limpia_items(L):
    out = []
    for i in L[:150]:
        if not isinstance(i, dict): continue
        u = str(i.get('unidad') or 'pz').strip().lower()[:10]
        out.append({'descripcion': str(i.get('descripcion') or '').strip()[:80], 'insumo': str(i.get('insumo') or '').strip()[:50].capitalize(),
                    'cantidad': _num(i.get('cantidad')), 'unidad': u, 'precio_unitario': _num(i.get('precio_unitario')), 'importe': _num(i.get('importe'))})
    return out
def limpia_lectura(r):
    if not isinstance(r, dict): return {'legible': False}
    if r.get('error'): return {'intentado': True, 'error': str(r['error'])[:120]}
    if r.get('legible') is False: return {'legible': False, 'intentado': True}
    f = str(r.get('fecha') or '')[:10]
    try: datetime.date.fromisoformat(f)
    except ValueError: f = ''
    return {'legible': True, 'intentado': True, 'proveedor': str(r.get('proveedor') or '').strip()[:60], 'fecha': f, 'ticket': str(r.get('ticket') or '').strip()[:30],
            'subtotal': _num(r.get('subtotal')), 'iva': _num(r.get('iva')), 'total': _num(r.get('total')), 'items': limpia_items(r.get('items') or [])}
def aplica_lectura(m, r):
    m['lectura'] = {k: v for k, v in r.items() if k != 'items'}
    if r.get('legible'):
        m['items'] = r.get('items') or []
        if not m.get('proveedor') and r.get('proveedor'): m['proveedor'] = r['proveedor']
        if not m.get('ticket') and r.get('ticket'): m['ticket'] = r['ticket']


# ---------------------------------------------------------------- entrada de Jorge: sesión con cookie, contraseña o huella (WebAuthn / passkey)
HUELLAS_FILE = os.path.join(AQUI, 'huellas.json')   # llaves públicas de los teléfonos de Jorge (no son secretas)
SES_ADMIN = {}     # token -> vence (segundos); se carga de sesiones_panel.json al arrancar (ver _ses_cargar)
RETOS = {}         # reto -> (tipo, vence)

def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
def unb64u(s):
    s = str(s or ''); return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))

def _cbor(b, i=0):
    ib = b[i]; mt = ib >> 5; ai = ib & 31; i += 1
    if ai < 24: v = ai
    elif ai == 24: v = b[i]; i += 1
    elif ai == 25: v = int.from_bytes(b[i:i + 2], 'big'); i += 2
    elif ai == 26: v = int.from_bytes(b[i:i + 4], 'big'); i += 4
    elif ai == 27: v = int.from_bytes(b[i:i + 8], 'big'); i += 8
    else: raise ValueError('Formato no soportado')
    if mt == 0: return v, i
    if mt == 1: return -1 - v, i
    if mt == 2: return bytes(b[i:i + v]), i + v
    if mt == 3: return bytes(b[i:i + v]).decode('utf-8'), i + v
    if mt == 4:
        out = []
        for _ in range(v): x, i = _cbor(b, i); out.append(x)
        return out, i
    if mt == 5:
        out = {}
        for _ in range(v): k, i = _cbor(b, i); x, i = _cbor(b, i); out[k] = x
        return out, i
    if mt == 6: return _cbor(b, i)
    return {20: False, 21: True, 22: None}.get(ai), i

P256_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
P256_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
P256_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
P256_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296, 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)
def _ec_add(P, Q):
    if P is None: return Q
    if Q is None: return P
    p = P256_P
    if P[0] == Q[0] and (P[1] + Q[1]) % p == 0: return None
    if P == Q: l = (3 * P[0] * P[0] - 3) * pow(2 * P[1], -1, p) % p
    else: l = (Q[1] - P[1]) * pow(Q[0] - P[0], -1, p) % p
    x = (l * l - P[0] - Q[0]) % p
    return (x, (l * (P[0] - x) - P[1]) % p)
def _ec_mul(k, P):
    R = None
    while k:
        if k & 1: R = _ec_add(R, P)
        P = _ec_add(P, P); k >>= 1
    return R
def es256_ok(x, y, msg, der):
    try:
        if der[0] != 0x30: return False
        i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7f)
        def ent(i):
            if der[i] != 0x02: raise ValueError
            l = der[i + 1]; return int.from_bytes(der[i + 2:i + 2 + l], 'big'), i + 2 + l
        r, i = ent(i); s, i = ent(i)
    except (IndexError, ValueError): return False
    n = P256_N
    if not (1 <= r < n and 1 <= s < n): return False
    if (y * y - (x ** 3 - 3 * x + P256_B)) % P256_P: return False
    e = int.from_bytes(hashlib.sha256(msg).digest(), 'big')
    w = pow(s, -1, n)
    R = _ec_add(_ec_mul(e * w % n, P256_G), _ec_mul(r * w % n, (x, y)))
    return R is not None and R[0] % n == r
def rs256_ok(n, e, msg, sig):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k: return False
    m = pow(int.from_bytes(sig, 'big'), e, n).to_bytes(k, 'big')
    di = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(msg).digest()
    return m == b'\x00\x01' + b'\xff' * (k - len(di) - 3) + b'\x00' + di

# Las sesiones del panel se guardan en la caja (sesiones_panel.json) para que una actualización automática
# (que reinicia el programa) no saque a Jorge a media captura. Solo vive en la caja; nunca va a data.json ni a Drive.
SES_FILE = os.path.join(AQUI, 'sesiones_panel.json')
_ses_escrito = [0.0]
def _ses_guardar(forzar=False):
    if not forzar and time.time() - _ses_escrito[0] < 60: return
    try:
        tmp = SES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump({k: v for k, v in SES_ADMIN.items() if v > time.time()}, f)
        os.replace(tmp, SES_FILE); _ses_escrito[0] = time.time()
    except OSError: pass
def _ses_cargar():
    try:
        with open(SES_FILE, encoding='utf-8') as f: d = json.load(f)
        SES_ADMIN.update({k: float(v) for k, v in d.items() if isinstance(v, (int, float)) and v > time.time()})
    except (OSError, ValueError): pass
SESM_FILE = os.path.join(AQUI, 'sesiones_comandero.json')   # 30.7.59: solo existe unos segundos, mientras se reinicia por una actualización
def _sesm_guardar():
    try:
        ahora = time.time()
        with open(SESM_FILE, 'w', encoding='utf-8') as f:
            json.dump({k: [v[0], v[1], HUELLA_M.get(k, 0)] for k, v in SESIONES_M.items() if v[1] > ahora}, f)
    except OSError: pass
def _sesm_cargar():
    try:
        with open(SESM_FILE, encoding='utf-8') as f: d = json.load(f)
        for k, v in d.items():
            if isinstance(v, list) and len(v) == 3 and float(v[1]) > time.time():
                SESIONES_M[k] = (v[0], float(v[1]))
                if v[2]: HUELLA_M[k] = float(v[2])
    except (OSError, ValueError, TypeError): pass
    try: os.remove(SESM_FILE)   # se usa una sola vez
    except OSError: pass
def nueva_sesion_admin(minutos):
    t = secrets.token_urlsafe(32); SES_ADMIN[t] = time.time() + minutos * 60
    for k in [k for k, v in SES_ADMIN.items() if v < time.time()]: SES_ADMIN.pop(k, None)
    _ses_guardar(True)
    return t
def sesion_admin(tok, minutos):
    v = SES_ADMIN.get(tok or '')
    if not v or v < time.time(): SES_ADMIN.pop(tok or '', None); return False
    SES_ADMIN[tok] = time.time() + minutos * 60   # se renueva mientras se usa
    _ses_guardar()
    return True
def nuevo_reto(tipo):
    r = b64u(secrets.token_bytes(32)); RETOS[r] = (tipo, time.time() + 180)
    for k in [k for k, v in RETOS.items() if v[1] < time.time()]: RETOS.pop(k, None)
    return r
def usar_reto(r, tipo):
    v = RETOS.pop(r or '', None)
    return bool(v and v[0] == tipo and v[1] >= time.time())

def huellas():
    try:
        with open(HUELLAS_FILE, encoding='utf-8') as f: h = json.load(f)
        return h if isinstance(h, list) else []
    except (OSError, ValueError): return []
def guardar_huellas(h):
    tmp = HUELLAS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(h, f, ensure_ascii=False, indent=1)
    os.replace(tmp, HUELLAS_FILE)

def _datos_cliente(cd_b64, tipo, host):
    cd_raw = unb64u(cd_b64); cd = json.loads(cd_raw.decode('utf-8'))
    if cd.get('type') != tipo: raise RuntimeError('Respuesta de huella no válida.')
    if not usar_reto(cd.get('challenge'), tipo): raise RuntimeError('La solicitud venció. Intenta otra vez.')
    o = cd.get('origin', ''); oh = o.split('://', 1)[-1].split(':')[0].lower()
    if oh != host or not (o.startswith('https://') or oh in ('localhost', '127.0.0.1')): raise RuntimeError('La huella se pidió desde otra dirección.')
    return cd_raw

def registrar_huella(d, host):
    cd_raw = _datos_cliente(d.get('cd'), 'webauthn.create', host)
    ao, _ = _cbor(unb64u(d.get('ao')))
    ad = ao.get('authData') or b''
    if ad[:32] != hashlib.sha256(host.encode()).digest(): raise RuntimeError('La huella no corresponde a esta dirección.')
    fl = ad[32]
    if not (fl & 0x01 and fl & 0x04): raise RuntimeError('El teléfono no confirmó la huella.')
    if not fl & 0x40: raise RuntimeError('El teléfono no entregó la llave.')
    ln = int.from_bytes(ad[53:55], 'big'); cid = ad[55:55 + ln]
    cose, _ = _cbor(ad, 55 + ln)
    if cose.get(3) == -7 and cose.get(1) == 2: llave = {'alg': -7, 'x': cose[-2].hex(), 'y': cose[-3].hex()}
    elif cose.get(3) == -257 and cose.get(1) == 3: llave = {'alg': -257, 'n': cose[-1].hex(), 'e': cose[-2].hex()}
    else: raise RuntimeError('Este teléfono usa un tipo de llave que todavía no se acepta.')
    # reemplazar: el teléfono vuelve a registrar su huella y la llave anterior de esta dirección se quita
    h = [x for x in huellas() if x.get('id') != b64u(cid) and not (d.get('reemplazar') and x.get('rp') == host and x.get('nombre') == (re.sub(r'\s+', ' ', str(d.get('nombre') or '')).strip()[:40] or 'Mi teléfono'))]
    nombre = re.sub(r'\s+', ' ', str(d.get('nombre') or '')).strip()[:40] or 'Mi teléfono'
    h.append(dict(llave, id=b64u(cid), rp=host, nombre=nombre, v=2, cuenta=int.from_bytes(ad[33:37], 'big'),
                  alta=datetime.datetime.now().isoformat(timespec='seconds'), ultimo=''))
    guardar_huellas(h)
    log(f'Huella registrada: {nombre} ({host})')
    return {'ok': True}

def entrar_huella(d, host):
    h = huellas(); x = next((x for x in h if x.get('id') == d.get('id') and x.get('rp') == host), None)
    if not x: raise RuntimeError('Esta huella no está registrada en esta dirección.')
    cd_raw = _datos_cliente(d.get('cd'), 'webauthn.get', host)
    ad = unb64u(d.get('ad')); sig = unb64u(d.get('sig'))
    if ad[:32] != hashlib.sha256(host.encode()).digest(): raise RuntimeError('La huella no corresponde a esta dirección.')
    if not (ad[32] & 0x01 and ad[32] & 0x04): raise RuntimeError('El teléfono no confirmó la huella.')
    msg = ad + hashlib.sha256(cd_raw).digest()
    ok = es256_ok(int(x['x'], 16), int(x['y'], 16), msg, sig) if x['alg'] == -7 else rs256_ok(int(x['n'], 16), int(x['e'], 16), msg, sig)
    if not ok: raise RuntimeError('La huella no se pudo comprobar.')
    cta = int.from_bytes(ad[33:37], 'big')
    if cta and x.get('cuenta') and cta <= x['cuenta']: log('Aviso: contador de la huella no avanzó (' + x.get('nombre', '') + ').')
    x['cuenta'] = cta; x['ultimo'] = datetime.datetime.now().isoformat(timespec='seconds'); guardar_huellas(h)
    return {'ok': True}

# ---------------------------------------------------------------- Excel (.xlsx) sin paquetes extra
# ---------------------------------------------------------------- hojas imprimibles de Recursos Humanos (Excel y PDF, sin librerías)
# Cada hoja es un dict: nombre, titulo, apaisado, cols [(título, ancho, tipo)], grupos [{titulo, filas: [[valores]]}], totales, nota, alto
# tipos de columna: 'nom' nombre · 'n' número · 'd' dinero · 'f:<plantilla>' dinero calculado, ej. 'f:{G}+{H}' (letras = columnas del mismo renglón)
#                   'firma' raya para firmar · 'dia' horario del día (si no trae número, se resalta: DESCANSO, FALTA…)
RH_EXPORT_DIR = 'Avenida San Francisco 1366, Ensenada, Baja California'
RH_EXPORT_PATRON = 'JOSÉ JORGE SÁNCHEZ GUEVARA'

def _xcol(i):
    s_ = ''; i += 1
    while i: i, r_ = divmod(i - 1, 26); s_ = chr(65 + r_) + s_
    return s_

def _rh_num(v):
    try: return round(float(v or 0), 2)
    except (TypeError, ValueError): return 0.0

def _rh_eval(tpl, fila):
    # valor de una columna calculada: solo sumas y restas de otras columnas del renglón
    tot = 0.0
    for signo, letra in re.findall(r'([+-]?)\{([A-Z]+)\}', tpl):
        i = 0
        for ch in letra: i = i * 26 + ord(ch) - 64
        v = _rh_num(fila[i - 1]) if i - 1 < len(fila) else 0.0
        tot += -v if signo == '-' else v
    return round(tot, 2)

def _rh_completa(h):
    # llena los valores de las columnas calculadas (para el PDF y para los totales)
    for g in h.get('grupos') or []:
        for f in g['filas']:
            while len(f) < len(h['cols']): f.append('')
            for ci, (_, _, t) in enumerate(h['cols']):
                if t.startswith('f:'): f[ci] = _rh_eval(t[2:], f)
    return h

def _rh_dinero(t): return t == 'd' or t.startswith('f:')
def _rh_resalta(v): return str(v or '') in ('DESCANSO', 'FALTA', 'VACACIONES', 'INCAPACIDAD')

def rh_hoja_firma(d):
    C = [('Nombre del trabajador', 38, 'nom'), ('Días\ntrab.', 6.5, 'n'), ('Horas\ntrab.', 7, 'n'), ('Faltas', 6, 'n'), ('Horas\nextra', 6.5, 'n'), ('Salario\n(día / hora)', 9.5, 'd'),
         ('Sueldo', 11, 'd'), ('Séptimo\ndía', 10, 'd'), ('Horas\nextra $', 9.5, 'd'), ('Prima\ndominical', 9.5, 'd'), ('Incentivo', 9.5, 'd'), ('Extras /\nbonos', 9, 'd'),
         ('Total\npercepciones', 12.5, 'f:{G}+{H}+{I}+{J}+{K}+{L}'), ('Recibí de conformidad\n(firma)', 30, 'firma'), ('Huella', 11, 'firma')]
    G = [{'titulo': g.get('titulo', ''), 'filas': [[str(f.get('nombre', ''))[:60]] + [_rh_num(f.get(k)) for k in ('dias', 'horas', 'faltas', 'h_extra', 'salario', 'sueldo', 'septimo', 'extra_horas', 'prima', 'incentivo', 'extras')] + ['', '', '']
                                                for f in g.get('filas') or []]} for g in d.get('grupos') or []]
    return _rh_completa({'nombre': 'Para firmar', 'titulo': 'NÓMINA DE LA SEMANA DEL %s AL %s' % (d.get('semana', ''), d.get('al', '')), 'apaisado': True, 'cols': C, 'grupos': G, 'totales': True, 'alto': 36, 'sin_total': {5},
            'nota': 'Con mi firma o huella declaro que recibí de conformidad el pago de los días y horas trabajados en la semana indicada, incluido el séptimo día, y que no se me adeuda cantidad alguna por este periodo.'})

def rh_hoja_pago(d):
    C = [('Nombre del trabajador', 34, 'nom'), ('Nómina\n(percepciones)', 13, 'd'), ('Anticipo\n(vale semana)', 12, 'd'), ('Préstamo', 11, 'd'), ('Vales de\ncomida', 11, 'd'), ('Otros\ndescuentos', 11, 'd'),
         ('Gratifi-\ncación (+)', 11, 'd'), ('Total\na pagar', 13, 'f:{B}-{C}-{D}-{E}-{F}+{G}'), ('Transfe-\nrencia', 12, 'd'), ('Efectivo', 13, 'f:{H}-{I}'), ('Saldo del\npréstamo', 11, 'd')]
    G = [{'titulo': g.get('titulo', ''), 'filas': [[str(f.get('nombre', ''))[:60]] + [_rh_num(f.get(k)) for k in ('nomina', 'vales', 'prestamo', 'comida', 'otros', 'grat')] + ['', _rh_num(f.get('transfer')), '', _rh_num(f.get('saldo'))]
                                                for f in g.get('filas') or []]} for g in d.get('grupos') or []]
    return _rh_completa({'nombre': 'Pago', 'titulo': 'PAGO DE LA NÓMINA · SEMANA DEL %s AL %s' % (d.get('semana', ''), d.get('al', '')), 'apaisado': True, 'cols': C, 'grupos': G, 'totales': True, 'alto': 20,
            'sin_total': {10}, 'nota': 'Descuentos y forma de pago. Total a pagar = nómina − anticipos (vales de la semana) − préstamo − vales de comida − otros + gratificación. Efectivo = total a pagar − transferencia. Saldo del préstamo: lo que queda después del abono de esta semana.'})

RH_TIT_PUESTO = {'Cocinero': 'Cocineros', 'Ayudante de cocina': 'Ayudantes de cocina', 'Mesero': 'Meseros', 'Cajero': 'Cajeros', 'Encargada': 'Encargada',
                 'Ayudante general': 'Ayudantes generales', 'Repartidor': 'Repartidores', 'Lavaloza': 'Lavaloza', 'Otro': 'Otros'}

def rh_hoja_descansos(rh, sem):
    # concentrado de descansos de la semana (martes a lunes): horario de cada día o DESCANSO / FALTA / VACACIONES / INCAPACIDAD
    def h12(v):
        m = re.fullmatch(r'(\d{1,2}):(\d{2})', str(v or ''))
        if not m: return ''
        H = int(m.group(1)); return '%d:%s %s' % (H % 12 or 12, m.group(2), 'am' if H < 12 else 'pm')
    ini = inicio_sem(datetime.date.fromisoformat(sem)); dias = [ini + datetime.timedelta(days=i) for i in range(7)]
    nd = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    C = [('Nombre', 30, 'nom')] + [('%s\n%d' % (nd[f.weekday()], f.day), 13, 'dia') for f in dias]
    txt = {'D': 'DESCANSO', 'F': 'FALTA', 'V': 'VACACIONES', 'I': 'INCAPACIDAD'}
    emps = sorted([e for e in rh.get('empleados', []) if e.get('activo', True)], key=lambda e: (ORDEN_ROL.index(e['puesto']) if e.get('puesto') in ORDEN_ROL else 99, e.get('nombre', '')))
    G = []
    for e in emps:
        fila = [str(e.get('nombre', ''))[:60]]
        for f in dias:
            c, h = rh_dia(rh, e, f)
            fila.append(txt.get(c) or ((h12(h[0]) + '\n' + h12(h[1])) if h and h[0] and h[1] else 'Trabaja'))
        t = RH_TIT_PUESTO.get(e.get('puesto'), e.get('puesto') or 'Otros')
        if not G or G[-1]['titulo'] != t: G.append({'titulo': t, 'filas': []})
        G[-1]['filas'].append(fila)
    al = dias[6]
    return {'nombre': 'Descansos', 'titulo': 'RELACIÓN DE DESCANSOS · SEMANA DEL %s AL %s' % (ini.strftime('%d/%m/%Y'), al.strftime('%d/%m/%Y')), 'apaisado': True,
            'cols': C, 'grupos': G, 'totales': False, 'alto': 30, 'sin_patron': True, 'nota': 'Arriba la hora de entrada y abajo la de salida.'}

def xlsx_libro(hojas, direccion=RH_EXPORT_DIR, patron=RH_EXPORT_PATRON):
    import zipfile, io
    from xml.sax.saxutils import escape as x
    V, SU, DS = 'FF1D5C3B', 'FFE2EFE5', 'FFFDEBD0'
    # estilos: 0 normal · 1 título · 2 subtítulo · 3 encabezado · 4 grupo · 5 nombre · 6 número · 7 dinero · 8 dinero negritas · 9 firma
    # 10 total grupo texto · 11 total grupo dinero · 12 gran total texto · 13 gran total dinero · 14 nota · 15 total grupo vacío · 16 gran total vacío
    # 17 día (horario) · 18 día resaltado (DESCANSO…)
    WR = ' wrapText="1"'
    A = lambda h, v='center', w=0: '<alignment horizontal="%s" vertical="%s"%s/>' % (h, v, WR if w else '')
    b = '<border><left style="thin"><color rgb="FF9DB5A4"/></left><right style="thin"><color rgb="FF9DB5A4"/></right><top style="thin"><color rgb="FF9DB5A4"/></top><bottom style="thin"><color rgb="FF9DB5A4"/></bottom><diagonal/></border>'
    bf = '<border><left style="thin"><color rgb="FF9DB5A4"/></left><right style="thin"><color rgb="FF9DB5A4"/></right><top style="thin"><color rgb="FF9DB5A4"/></top><bottom style="medium"><color rgb="FF000000"/></bottom><diagonal/></border>'
    fonts = ['<font><sz val="10"/><name val="Arial"/></font>', '<font><b/><sz val="15"/><color rgb="%s"/><name val="Arial"/></font>' % V, '<font><sz val="9"/><name val="Arial"/></font>',
             '<font><b/><sz val="8"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>', '<font><b/><sz val="11"/><color rgb="%s"/><name val="Arial"/></font>' % V,
             '<font><b/><sz val="9"/><name val="Arial"/></font>', '<font><sz val="9"/><name val="Arial"/></font>', '<font><b/><sz val="9"/><color rgb="%s"/><name val="Arial"/></font>' % V,
             '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>', '<font><i/><sz val="8"/><color rgb="FF5D6E62"/><name val="Arial"/></font>',
             '<font><b/><sz val="8"/><color rgb="FF9A4A00"/><name val="Arial"/></font>']
    fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>',
             '<fill><patternFill patternType="solid"><fgColor rgb="%s"/></patternFill></fill>' % V, '<fill><patternFill patternType="solid"><fgColor rgb="%s"/></patternFill></fill>' % SU,
             '<fill><patternFill patternType="solid"><fgColor rgb="%s"/></patternFill></fill>' % DS]
    borders = ['<border><left/><right/><top/><bottom/><diagonal/></border>', b, bf]
    xf = [(0, 0, 0, 0, ''), (0, 1, 0, 0, A('center')), (0, 2, 0, 0, A('center')), (0, 3, 2, 1, A('center', 'center', 1)),
          (0, 4, 3, 1, A('left')), (0, 5, 0, 1, A('left', 'center', 1)), (165, 6, 0, 1, A('center')), (164, 6, 0, 1, A('right')),
          (164, 5, 0, 1, A('right')), (0, 0, 0, 2, ''), (0, 7, 3, 1, A('left')), (164, 7, 3, 1, A('right')),
          (0, 8, 2, 1, A('left')), (164, 8, 2, 1, A('right')), (0, 9, 0, 0, A('left', 'center', 1)), (0, 0, 3, 1, ''), (0, 0, 2, 1, ''),
          (0, 6, 0, 1, A('center', 'center', 1)), (0, 10, 4, 1, A('center', 'center', 1))]
    cellxfs = ''.join('<xf numFmtId="%d" fontId="%d" fillId="%d" borderId="%d" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"' % (a, f_, fi, bo)
                      + (' applyAlignment="1">' + al + '</xf>' if al else '/>') for a, f_, fi, bo, al in xf)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00;-#,##0.00;&quot;-&quot;"/><numFmt numFmtId="165" formatCode="0.##;-0.##;&quot;-&quot;"/></numFmts>'
              '<fonts count="%d">%s</fonts><fills count="%d">%s</fills><borders count="%d">%s</borders>' % (len(fonts), ''.join(fonts), len(fills), ''.join(fills), len(borders), ''.join(borders)) +
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="%d">%s</cellXfs></styleSheet>' % (len(xf), cellxfs))
    hojas_xml, nombres, defs = [], [], []
    for hi, h in enumerate(hojas):
        C = h['cols']; NC = len(C); ult = _xcol(NC - 1); rows = []; merges = []; heights = {}
        def fila(celdas, alto=None):
            rows.append(celdas)
            if alto: heights[len(rows)] = alto
        fila([('RESTAURANTE LA HOLANDESA', 1)], 26); merges.append('A1:%s1' % ult)
        fila([(direccion + ('' if h.get('sin_patron') else '   ·   Patrón: ' + patron), 2)]); merges.append('A2:%s2' % ult)
        fila([(h.get('titulo', ''), 1)], 24); merges.append('A3:%s3' % ult)
        fila([])
        fila([(t, 3) for t, _, _ in C], 32)
        sumables = [ci for ci, (_, _, t) in enumerate(C) if _rh_dinero(t) and ci not in (h.get('sin_total') or set())]
        tot_g = []
        for g in h.get('grupos') or []:
            fila([(str(g.get('titulo', '')).upper(), 4)] + [('', 4)] * (NC - 1), 20); merges.append('A%d:%s%d' % (len(rows), ult, len(rows)))
            ini = len(rows) + 1
            for f in g['filas']:
                r = len(rows) + 1; cel = []
                for ci, (_, _, t) in enumerate(C):
                    v = f[ci] if ci < len(f) else ''
                    if t == 'nom': cel.append((v, 5))
                    elif t == 'n': cel.append((_rh_num(v), 6))
                    elif t == 'd': cel.append((_rh_num(v), 7))
                    elif t.startswith('f:'): cel.append(('=' + re.sub(r'\{([A-Z]+)\}', lambda m: m.group(1) + str(r), t[2:]), 8))
                    elif t == 'firma': cel.append(('', 9))
                    elif t == 'dia': cel.append((v, 18 if _rh_resalta(v) else 17))
                    else: cel.append((v, 5))
                fila(cel, h.get('alto') or 20)
            fin = len(rows)
            if h.get('totales') and fin >= ini:
                r = fin + 1
                fila([('Total ' + str(g.get('titulo', '')).lower(), 10)] + [(('=SUM(%s%d:%s%d)' % (_xcol(ci), ini, _xcol(ci), fin), 11) if ci in sumables else ('', 15)) for ci in range(1, NC)], 18)
                tot_g.append(r)
            fila([])
        if h.get('totales') and tot_g:
            fila([('TOTAL', 12)] + [(('=' + '+'.join('%s%d' % (_xcol(ci), t) for t in tot_g), 13) if ci in sumables else ('', 16)) for ci in range(1, NC)], 22)
        area = len(rows)
        if h.get('nota'):
            fila([]); fila([(h['nota'], 14)], 28); merges.append('A%d:%s%d' % (len(rows), ult, len(rows)))
        sd = []
        for ri, row in enumerate(rows, 1):
            cs = []
            for ci, (v, st) in enumerate(row):
                ref = _xcol(ci) + str(ri)
                if isinstance(v, str) and v.startswith('='): cs.append('<c r="%s" s="%d"><f>%s</f></c>' % (ref, st, x(v[1:])))
                elif isinstance(v, (int, float)) and not isinstance(v, bool): cs.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, v))
                elif v not in (None, ''): cs.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, x(str(v))))
                else: cs.append('<c r="%s" s="%d"/>' % (ref, st))
            hh = ' ht="%s" customHeight="1"' % heights[ri] if ri in heights else ''
            sd.append('<row r="%d"%s>%s</row>' % (ri, hh, ''.join(cs)))
        cols = ''.join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w) for i, (_, w, _) in enumerate(C))
        mg = ''.join('<mergeCell ref="%s"/>' % m for m in merges)
        hojas_xml.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
              '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><sheetViews><sheetView workbookViewId="0" showGridLines="0"%s><pane ySplit="5" topLeftCell="A6" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' % (' tabSelected="1"' if hi == 0 else '') +
              '<cols>%s</cols><sheetData>%s</sheetData><mergeCells count="%d">%s</mergeCells>' % (cols, ''.join(sd), len(merges), mg) +
              '<printOptions horizontalCentered="1"/><pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
              '<pageSetup paperSize="1" orientation="%s" fitToWidth="1" fitToHeight="0"/>' % ('landscape' if h.get('apaisado') else 'portrait') +
              '<headerFooter><oddFooter>&amp;LLa Holandesa · %s&amp;RPágina &amp;P de &amp;N</oddFooter></headerFooter></worksheet>' % x(h.get('nombre', '')))
        nom = re.sub(r'[\[\]\*\?/\\:]', '', h.get('nombre') or 'Hoja%d' % (hi + 1))[:31]
        nombres.append(nom)
        defs.append('<definedName name="_xlnm.Print_Area" localSheetId="%d">\'%s\'!$A$1:$%s$%d</definedName>' % (hi, nom, ult, area))
        defs.append('<definedName name="_xlnm.Print_Titles" localSheetId="%d">\'%s\'!$5:$5</definedName>' % (hi, nom))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets>%s</sheets><definedNames>%s</definedNames><calcPr fullCalcOnLoad="1"/></workbook>' % (''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (x(n), i + 1, i + 1) for i, n in enumerate(nombres)), ''.join(defs)))
    rels = ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(hojas)))
    rels += '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (len(hojas) + 1)
    over = ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(hojas)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>%s<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>' % over)
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        for i, s_ in enumerate(hojas_xml): z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), s_)
        z.writestr('xl/styles.xml', styles)
    return buf.getvalue()

# anchos de Helvetica (milésimas del tamaño) para alinear y recortar texto en el PDF
_HW = {' ': 278, '-': 333, '.': 278, ',': 278, ':': 278, '/': 278, '(': 333, ')': 333, '$': 556, '%': 889, '·': 278, 'I': 278, 'J': 500, 'M': 833, 'W': 944, 'i': 222, 'j': 222, 'l': 222, 'f': 278, 't': 278, 'r': 333, 'm': 833, 'w': 722}
def _pdf_ancho(t, tam, negrita=False):
    s_ = 0
    for ch in str(t):
        if ch in _HW: s_ += _HW[ch]
        elif ch.isdigit(): s_ += 556
        elif ch.isupper(): s_ += 722 if negrita else 680
        else: s_ += 556 if negrita else 520
    return s_ * tam / 1000.0

def _pdf_corta(t, tam, ancho, negrita=False):
    t = str(t)
    if _pdf_ancho(t, tam, negrita) <= ancho: return t
    while t and _pdf_ancho(t + '…', tam, negrita) > ancho: t = t[:-1]
    return t + '…'

def _pdf_dinero(v):
    v = _rh_num(v)
    return '-' if abs(v) < 0.005 else '{:,.2f}'.format(v)

def pdf_libro(hojas, direccion=RH_EXPORT_DIR, patron=RH_EXPORT_PATRON, titulo='Nómina La Holandesa'):
    paginas = []   # contenido (bytes) y tamaño de cada página
    VERDE, SUAVE, RES = (0.114, 0.361, 0.231), (0.886, 0.937, 0.898), (0.992, 0.922, 0.816)
    for h in hojas:
        W, H = (792, 612) if h.get('apaisado') else (612, 792)
        M = 24; C = h['cols']; tot_w = sum(w for _, w, _ in C); esc_ = (W - 2 * M) / float(tot_w); anchos = [w * esc_ for _, w, _ in C]
        xs = [M]
        for a in anchos: xs.append(xs[-1] + a)
        alto = max(15, min(30, (h.get('alto') or 20) * 0.8)); tb = 7.5 if len(C) > 10 else 8.5
        sumables = [ci for ci, (_, _, t) in enumerate(C) if _rh_dinero(t) and ci not in (h.get('sin_total') or set())]
        pags = []; ops = []; y = [0]
        def txt(x0, y0, t, tam, neg=False, col=(0, 0, 0)):
            t = str(t).replace('−', '-')
            ops.append(b'BT /%s %.1f Tf %.3f %.3f %.3f rg %.2f %.2f Td (' % (b'F2' if neg else b'F1', tam, col[0], col[1], col[2], x0, y0) + _pdf_txt(t) + b') Tj ET')
        def rect(x0, y0, w, hh, fill=None, linea=True):
            if fill: ops.append(b'%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f' % (fill[0], fill[1], fill[2], x0, y0, w, hh))
            if linea: ops.append(b'0.6 0.7 0.64 RG 0.5 w %.2f %.2f %.2f %.2f re S' % (x0, y0, w, hh))
        def celda(ci, y0, hh, v, neg=False, col=(0, 0, 0), tam=None):
            tam = tam or tb; t = C[ci][2]; x0, w = xs[ci], anchos[ci]
            lineas = str(v).split('\n') if v not in (None, '') else []
            if t in ('n',): lineas = [('%g' % _rh_num(v)) if _rh_num(v) else '-']
            if _rh_dinero(t): lineas = [_pdf_dinero(v)]
            if t == 'nom' and len(lineas) == 1 and _pdf_ancho(lineas[0], tam, neg) > w - 5 and hh >= 2 * tam + 4:   # nombre largo: en dos renglones
                ps = lineas[0].split(); k = len(ps)
                while k > 1 and _pdf_ancho(' '.join(ps[:k]), tam, neg) > w - 5: k -= 1
                lineas = [' '.join(ps[:k]), ' '.join(ps[k:])]
            n = len(lineas); base = y0 + hh / 2 + (n - 1) * (tam + 1) / 2 - tam * 0.35
            for k, l in enumerate(lineas):
                l = _pdf_corta(l, tam, w - 5, neg); aw = _pdf_ancho(l, tam, neg)
                xx = x0 + 3 if t == 'nom' else (x0 + w - 3 - aw if _rh_dinero(t) else x0 + (w - aw) / 2)
                txt(xx, base - k * (tam + 1), l, tam, neg, col)
        def nueva():
            if ops: pags.append(b'\n'.join(ops))
            del ops[:]; y[0] = H - M
            txt(M, y[0] - 14, 'RESTAURANTE LA HOLANDESA', 14, True, VERDE)
            d_ = direccion + ('' if h.get('sin_patron') else '   ·   Patrón: ' + patron)
            txt(W - M - _pdf_ancho(d_, 8), y[0] - 12, d_, 8)
            txt(M, y[0] - 32, h.get('titulo', ''), 11, True, VERDE)
            y[0] -= 42
            hh = 24; y[0] -= hh
            for ci, (t, _, _) in enumerate(C):
                rect(xs[ci], y[0], anchos[ci], hh, VERDE)
                ls = t.split('\n'); b0 = y[0] + hh / 2 + (len(ls) - 1) * 4 - 2.4
                for k, l in enumerate(ls):
                    l = _pdf_corta(l, 7, anchos[ci] - 3, True); txt(xs[ci] + (anchos[ci] - _pdf_ancho(l, 7, True)) / 2, b0 - k * 8, l, 7, True, (1, 1, 1))
        def espacio(hh):
            if y[0] - hh < M + 18: nueva()
        nueva()
        gran = [0.0] * len(C)
        for g in h.get('grupos') or []:
            espacio(14 + alto * min(2, len(g['filas'])))
            y[0] -= 14; rect(M, y[0], W - 2 * M, 14, SUAVE); txt(M + 3, y[0] + 4, str(g.get('titulo', '')).upper(), 8.5, True, VERDE)
            sub = [0.0] * len(C)
            for f in g['filas']:
                espacio(alto); y[0] -= alto
                for ci, (_, _, t) in enumerate(C):
                    v = f[ci] if ci < len(f) else ''
                    res = t == 'dia' and _rh_resalta(v)
                    rect(xs[ci], y[0], anchos[ci], alto, RES if res else None)
                    if t == 'firma': ops.append(b'0 0 0 RG 0.8 w %.2f %.2f m %.2f %.2f l S' % (xs[ci] + 4, y[0] + 5, xs[ci] + anchos[ci] - 4, y[0] + 5)); continue
                    celda(ci, y[0], alto, v, t == 'nom' or t.startswith('f:') or res, (0.6, 0.29, 0) if res else (0, 0, 0), 7 if res else None)
                    if ci in sumables: sub[ci] += _rh_num(v)
            if h.get('totales') and g['filas']:
                espacio(14); y[0] -= 14
                for ci in range(len(C)):
                    rect(xs[ci], y[0], anchos[ci], 14, SUAVE)
                    if ci == 0: celda(0, y[0], 14, 'Total ' + str(g.get('titulo', '')).lower(), True, VERDE, 7.5)
                    elif ci in sumables: celda(ci, y[0], 14, sub[ci], True, VERDE, 7.5); gran[ci] += sub[ci]
            y[0] -= 6
        if h.get('totales'):
            espacio(18); y[0] -= 18
            for ci in range(len(C)):
                rect(xs[ci], y[0], anchos[ci], 18, VERDE)
                if ci == 0: celda(0, y[0], 18, 'TOTAL', True, (1, 1, 1), 9)
                elif ci in sumables: celda(ci, y[0], 18, gran[ci], True, (1, 1, 1), 8)
        if h.get('nota'):
            palabras = h['nota'].split(); lineas = ['']
            for p in palabras:
                if _pdf_ancho(lineas[-1] + ' ' + p, 7) > W - 2 * M: lineas.append(p)
                else: lineas[-1] = (lineas[-1] + ' ' + p).strip()
            espacio(10 * len(lineas) + 8); y[0] -= 8
            for l in lineas: y[0] -= 10; txt(M, y[0], l, 7, False, (0.36, 0.43, 0.38))
        pags.append(b'\n'.join(ops))
        for p in pags: paginas.append((p, W, H, h.get('nombre', '')))
    objs = []
    def obj(b_): objs.append(b_); return len(objs)
    cat = obj(b''); pg = obj(b'')
    f1 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    f2 = obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    kids = []
    for n, (cont, W, H, nom) in enumerate(paginas, 1):
        pie = b'BT /F1 7 Tf 0.36 0.43 0.38 rg 24 12 Td (' + _pdf_txt('La Holandesa · ' + nom) + b') Tj ET BT /F1 7 Tf %.2f 12 Td (' % (W - 70) + _pdf_txt('Página %d de %d' % (n, len(paginas))) + b') Tj ET'
        c = cont + b'\n' + pie
        co = obj(b'<< /Length %d >>\nstream\n' % len(c) + c + b'\nendstream')
        kids.append(obj(b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>' % (pg, W, H, f1, f2, co)))
    objs[cat - 1] = b'<< /Type /Catalog /Pages %d 0 R >>' % pg
    objs[pg - 1] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(b'%d 0 R' % k for k in kids), len(kids))
    out = b'%PDF-1.4\n'; pos = []
    for k, b_ in enumerate(objs, 1): pos.append(len(out)); out += b'%d 0 obj\n' % k + b_ + b'\nendobj\n'
    xr = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % p for p in pos)
    out += b'trailer\n<< /Size %d /Root %d 0 R /Info << /Title (' % (len(objs) + 1, cat) + _pdf_txt(titulo) + b') >> >>\nstartxref\n%d\n%%%%EOF\n' % xr
    return out

def xlsx(rows, sheet='Hoja1', widths=None, money_cols=()):
    import zipfile, io
    from xml.sax.saxutils import escape as x
    def col(i):
        s = ''
        i += 1
        while i: i, r = divmod(i - 1, 26); s = chr(65 + r) + s
        return s
    sd = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            ref = col(ci) + str(ri)
            st = 1 if ri == 1 else (2 if ci in money_cols else 0)
            if ri == len(rows) and ri > 1: st = 3 if ci in money_cols else 1
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}" s="{st}"><v>{v}</v></c>')
            elif v not in (None, ''):
                cells.append(f'<c r="{ref}" s="{st}" t="inlineStr"><is><t xml:space="preserve">{x(str(v))}</t></is></c>')
        sd.append(f'<row r="{ri}">{"".join(cells)}</row>')
    cols = ''.join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths or []))
    ws = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
          + (f'<cols>{cols}</cols>' if cols else '') + f'<sheetData>{"".join(sd)}</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/></numFmts>'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="4"><xf/><xf fontId="1" applyFont="1"/><xf numFmtId="164" applyNumberFormat="1"/><xf numFmtId="164" fontId="1" applyNumberFormat="1" applyFont="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{x(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': ws, 'xl/styles.xml': styles}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, c in files.items(): z.writestr(n, c)
    return buf.getvalue()

def _sin_acentos(t):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg['carpeta_mrtienda']
        self.cache = {}           # (ruta, tamaño, mtime) -> filas
        self.json = b'{}'
        self.jsongz = gzip.compress(b'{}')
        self.error = None
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.q_lock = threading.Lock()
        self.psum = {}            # día -> (firma de archivos, {producto: [piezas, importe, familia]})
        self._touched = set()
        self.rows_lock = threading.RLock()   # el comandero lee desde otros hilos al mismo tiempo que el ciclo

    def rows(self, path):
        try: st = os.stat(path)
        except OSError: return []
        key = (path, st.st_size, int(st.st_mtime))
        with self.rows_lock:
            self._touched.add(path)
            if key not in self.cache:
                for k in [k for k in self.cache if k[0] == path]: del self.cache[k]
                self.cache[key] = [x for x in safe_rows(path) if not x['_del']]
            return self.cache[key]

    def _cat(self, nombre):
        # catálogos de MrTienda: DATABASE (en la caja); REGISTRO\PROTECT es la copia que trae el respaldo
        for sub in ('DATABASE', os.path.join('REGISTRO', 'PROTECT')):
            p = os.path.join(self.base, sub, nombre)
            if os.path.isfile(p): return p
        return os.path.join(self.base, 'DATABASE', nombre)

    def _variantes(self):
        """VARIANTE.DBF (por producto) y SVARIANT.DBF (opciones de los grupos obligatorios)."""
        var = collections.defaultdict(list); svar = collections.defaultdict(list)
        for x in self.rows(self._cat('VARIANTE.DBF')): var[x['COD_PROD']].append(x)
        for x in self.rows(self._cat('SVARIANT.DBF')): svar[(x['COD_PROD'], x['COD_VAR'])].append(x)
        return var, svar

    def detalle_variantes(self, v, var=None, svar=None):
        """Texto de las variantes de un renglón de cuenta, como lo guarda MrTienda.
        SUB_VARIA: bloques de 4 dígitos grupo+opción (0801 = grupo 08, opción 01).
        VARIAASCII: un "1" en la posición de cada variante sencilla (posición 1 = variante 01; la posición 0 va en blanco)."""
        if var is None: var, svar = self._variantes()
        cp = v.get('COD_PROD') or ''
        grupos = {x['COD_VAR'] for x in var.get(cp, []) if svar.get((cp, x['COD_VAR']))}
        out = []
        sv = (v.get('SUB_VARIA') or '').strip()
        for i in range(0, len(sv) - 3, 4):
            g, o = sv[i:i+2], sv[i+2:i+4]
            n = next((s['DES_VAR'] for s in svar.get((cp, g), []) if s['COD_SVAR'] == o), '')
            if n: out.append(n.strip().capitalize())
        va = v.get('VARIAASCII') or ''
        for i, ch in enumerate(va):
            if ch != '1' or i == 0: continue
            cv = '%02d' % i
            if cv in grupos: continue
            n = next((x['DES_VAR'] for x in var.get(cp, []) if x['COD_VAR'] == cv), '')
            if n: out.append(re.sub(r'(?<=\b\w) (?=\w\b)', '', n).strip().capitalize())
        libre = (v.get('VAR_LIBRE') or '').strip()
        if libre: out.append(libre.capitalize())
        return ' · '.join(out)

    def catalogs(self):
        db = os.path.join(self.base, 'DATABASE')
        self.EMP = {x['COD_EMPLEA']: x['DES_EMPLEA'].strip().title() for x in self.rows(os.path.join(db, 'PAREA.DBF'))}
        fam = {}
        for x in self.rows(os.path.join(db, 'FAMILIAS.DBF')):
            v = re.sub(r'(?<=\b\w) (?=\w\b)', '', x['CONCEPTO']).replace('  ', ' ').strip().title()
            fam[x['COD_FAMILI']] = re.sub(r'^1', '', v)
        self.FAM = fam
        prod = {x['COD_PROD']: x['DES_PROD'].strip() for x in self.rows(os.path.join(db, 'PRODUCTO.DBF'))}
        ap = sorted([x for x in self.rows(os.path.join(db, 'AUD_PREC.DBF')) if x['FECHA']], key=lambda x: (x['FECHA'], x['HORA']))
        last = {}; self.PRECIOS = collections.defaultdict(list)
        for x in ap:
            k = (x['COD_PROD'], x['COD_ESCALA']); prev = last.get(k); last[k] = x['ACTIVO']
            if prev is not None and abs(prev - (x['ACTIVO'] or 0)) < 0.01: continue
            d = x['FECHA']
            self.PRECIOS[f'{d[:4]}-{d[4:6]}-{d[6:]}'].append({'prod': prod.get(x['COD_PROD'], x['COD_PROD']), 'lista': ESC.get(x['COD_ESCALA'], x['COD_ESCALA']),
                'antes': prev, 'ahora': x['ACTIVO'], 'hora': x['HORA'], 'quien': x['USUARIO'].strip().title()})

    def name(self, c): return self.EMP.get(c, c or 'Sin asignar')

    def index_files(self):
        """Turnos cerrados: CORTESZ/<MES> y REGISTRO/<MES>. Turno abierto: REGISTRO/ (raíz)."""
        found = {}
        pats = [os.path.join(self.base, 'CORTESZ', '*', '*.001'), os.path.join(self.base, 'REGISTRO', '*', '*.001'), os.path.join(self.base, 'REGISTRO', '*.001')]
        for pat in pats:
            for f in glob.glob(pat):
                m = FNAME.match(os.path.basename(f))
                if not m: continue
                pre, dd, mm, yy, tn = m.groups()
                try: d = datetime.date(2000 + int(yy), int(mm), int(dd))
                except ValueError: continue
                k = (pre.upper(), d, tn)
                try: sz = os.path.getsize(f)
                except OSError: continue
                if k not in found or sz > found[k][1]: found[k] = (f, sz)
        idx = collections.defaultdict(list)
        for (pre, d, tn), (f, _) in found.items(): idx[(pre, d)].append((tn, f))
        return idx

    def load(self, idx, pre, d):
        out = []
        for tn, f in sorted(idx.get((pre, d), [])):
            for x in self.rows(f):
                y = dict(x); y['_turno'] = tn; out.append(y)
        return out

    def day(self, idx, d, detail):
        P = [p for p in self.load(idx, 'P', d) if p.get('FOL_VTA')]
        V = self.load(idx, 'V', d); D = self.load(idx, 'D', d); A = self.load(idx, 'A', d)
        validk = {(p['_turno'], p['FOL_VTA']) for p in P if not p.get('CANCELADA')}
        canc = [p for p in P if p.get('CANCELADA')]
        tickets = {}
        for p in P:
            if p.get('CANCELADA'): continue
            k = (p['_turno'], p['FOL_VTA'])
            t = tickets.setdefault(k, {'tot': 0, 'hora': p.get('HORA') or '', 'mesa': p.get('MESA') or '', 'mesero': self.name(p.get('COD_VENDED')), 'adom': bool(p.get('ADOMICILIO')),
                                       'pers': p.get('COMENSALES') or 0, 'esc': p.get('COD_ESCALA'), 'pagos': [],
                                       'cobro': self.name(p.get('COD_CAJERO')), 'items': [], 'capt': collections.Counter(), 'folz': p.get('FOL_Z') or ''})
            a = amt(p); t['tot'] += a
            f = FORMAS.get(p.get('COD_FORMA'), 'Otro')
            if p.get('COD_MONEDA') == '02': f = 'Efectivo (dólares)'
            t['pagos'].append((f, a))
            t.setdefault('raw', []).append([f, round((p.get('PAGO') or 0) * (p.get('TC') or 1), 2), round((p.get('CAMBIO') or 0) * (p.get('TC_CAMBIO') or 1), 2)])
        tot = sum(t['tot'] for t in tickets.values())
        s = {'fecha': d.isoformat(), 'total': round(tot, 2), 'tickets': len(tickets), 'personas': int(sum(t['pers'] for t in tickets.values()))}
        # 30.7.40: venta por tipo (comedor, para llevar, a domicilio, Didi efectivo/tarjeta, Uber) para el reporte de Resultados
        dom = self._domicilios(d)
        can = collections.Counter()
        for t in tickets.values(): can[self._canal_ticket(t, dom)] += t['tot']
        s['canales'] = {k: round(v, 2) for k, v in can.items() if v}
        if not detail: return s
        hr = collections.Counter(); fp = collections.Counter(); ms = collections.defaultdict(lambda: [0, 0]); tur = collections.Counter()
        for (tn, _), t in tickets.items():
            h = int(t['hora'][:2]) if t['hora'][:2].isdigit() else 0
            hr[h] += t['tot']; tur[tn] += t['tot']; m = ms[t['mesero']]; m[0] += t['tot']; m[1] += 1
            for f, a in t['pagos']: fp[f] += a
        prod = collections.defaultdict(lambda: [0, 0, '']); fm = collections.Counter(); desc = []
        for v in V:
            if (v['_turno'], v.get('FOL_VTA')) not in validk: continue
            q = v.get('CANTIDAD') or 0; imp = (v.get('PRECIO') or 0) * q
            tk = tickets.get((v['_turno'], v.get('FOL_VTA')))
            if tk is not None:
                tk['items'].append({'n': (v.get('DES_PROD') or '').strip(), 'q': q, 'imp': round(imp, 2), 'h': v.get('HORACAPTUR') or '',
                                    'llevar': v.get('COD_ESCALA') == '02', 'f': self.FAM.get(v.get('COD_FAMILI'), 'Otros'), 'dcto': round(((v.get('PRECIO_O') or 0) - (v.get('PRECIO') or 0)) * q, 2)})
                tk['capt'][self.name(v.get('COD_VENDED'))] += 1
                hc = (v.get('HORACAPTUR') or '').strip()
                if len(hc) >= 5 and hc[:2].isdigit() and (not tk.get('abrio') or hc < tk['abrio']): tk['abrio'] = hc[:5]
            fam = self.FAM.get(v.get('COD_FAMILI'), 'Otros')
            pp = prod[(v.get('DES_PROD') or '').strip()]; pp[0] += q; pp[1] += imp; pp[2] = fam
            fm[fam] += imp
            dd = ((v.get('PRECIO_O') or 0) - (v.get('PRECIO') or 0)) * q
            if dd > 0.01:
                desc.append({'prod': v['DES_PROD'].strip(), 'orig': v.get('PRECIO_O'), 'cobrado': v.get('PRECIO'),
                             'pct': v.get('DCTO') or round(100 * dd / ((v.get('PRECIO_O') or 1) * (q or 1))), 'monto': round(dd, 2),
                             'quien': self.name(v.get('AUTORIZA') or v.get('COD_VENDED')), 'hora': v.get('HORACAPTUR'), 'ticket': v.get('FOL_VTA')})
        borr = [{'prod': (a.get('DES_PROD') or '').strip(), 'cant': a.get('CANTIDAD'), 'importe': a.get('IMPORTE'), 'mesa': (a.get('MESA') or '').strip(),
                 'hora': a.get('HORA'), 'quien': self.name(a.get('COD_CAJERO')), 'mesero': self.name(a.get('COD_VENDED')), 'motivo': (a.get('MOTIVO') or '').strip()} for a in A]
        def mov(k):
            return [{'ref': (x.get('REF') or '').strip() or '—', 'importe': x.get('IMPORTE') or 0, 'hora': x.get('HORA'), 'turno': x['_turno'],
                     'cat': 'Vales' if k == '01' else (cat_in if k == '07' else cat_out)((x.get('REF') or '').strip())} for x in D if x.get('TIP_MD') == k]
        # 07 = reingreso, 02 = retiro, 01 = vale de dinero (Mr. Tienda los resta del efectivo igual que los retiros)
        ent = mov('07'); sal = sorted(mov('02') + mov('01'), key=lambda x: (x['turno'], x['hora'] or ''))
        didi = [e for e in ent if DIDI.search(e['ref'])]
        cortes = []
        for tn in sorted(set(x['_turno'] for x in P) | set(x['_turno'] for x in D)):
            efe = sum(amt(p) for p in P if p['_turno'] == tn and p.get('COD_FORMA') == '001' and not p.get('CANCELADA'))
            g = lambda k: sum(x.get('IMPORTE') or 0 for x in D if x['_turno'] == tn and x.get('TIP_MD') == k)
            cnt = [x for x in D if x['_turno'] == tn and x.get('TIP_MD') == '77']
            contado = sum(denom(x.get('REF') or '', x.get('IMPORTE') or 0, x.get('TC') or 1) for x in cnt)
            esperado = efe + g('07') - g('02') - g('01')
            tf = collections.Counter()
            for (tt, _), tk in tickets.items():
                if tt == tn:
                    for f, a in tk['pagos']: tf[f] += a
            cortes.append({'formas': {k: round(v, 2) for k, v in tf.most_common()}, 'venta': round(sum(tf.values()), 2),
                           'tickets': sum(1 for (tt, _) in tickets if tt == tn), 'turno': tn, 'fondo': g('78'), 'ventas_efe': round(efe, 2),
                           'entradas': g('07'), 'salidas': g('02'), 'vales': g('01'), 'esperado': round(esperado, 2),
                           'contado': round(contado, 2) if cnt else None, 'dif': round(contado - esperado, 2) if cnt else None,
                           'cajero': self.name(next((p.get('COD_CAJERO') for p in P if p['_turno'] == tn and p.get('COD_CAJERO')), '')),
                           'folz': next((p.get('FOL_Z') for p in P if p['_turno'] == tn and p.get('FOL_Z')), '')})
        rec = [t for t in tickets.values() if t['esc'] == '02']
        def pagos_de(t):
            c = collections.Counter()
            for f, a in t['pagos']: c[f] += a
            return [[f, round(a, 2)] for f, a in c.items()]
        tlist = sorted([{'folio': k[1], 'turno': k[0], 'hora': t['hora'], 'mesa': t['mesa'].strip(), 'mesero': t['mesero'], 'total': round(t['tot'], 2),
                         'pago': ' + '.join(sorted({f for f, _ in t['pagos']})), 'llevar': t['esc'] == '02',
                         'cobro': t['cobro'], 'capturo': ', '.join(n for n, _ in t['capt'].most_common()) or t['mesero'],
                         'abrio': t.get('abrio') or t['hora'], 'min': minutos(t.get('abrio') or t['hora'], t['hora']), 'pagos': pagos_de(t), 'raw': t.get('raw', []), 'items': t['items'], 'pers': int(t['pers'])} for k, t in tickets.items()], key=lambda x: (x['turno'], x['hora'], x['folio']))
        s.update({'hora': {str(k): round(v, 2) for k, v in sorted(hr.items())}, 'formas': {k: round(v, 2) for k, v in fp.most_common()},
                  'turnos': {k: round(v, 2) for k, v in sorted(tur.items())},
                  'meseros': [{'n': k, 'total': round(v[0], 2), 'tk': v[1]} for k, v in sorted(ms.items(), key=lambda x: -x[1][0])],
                  'productos': [{'n': k, 'q': v[0], 'imp': round(v[1], 2), 'f': v[2]} for k, v in sorted(prod.items(), key=lambda x: -x[1][1])],
                  'familias': {k: round(v, 2) for k, v in fm.most_common()}, 'descuentos': desc, 'borrados': borr,
                  'cancelados': [{'folio': p['FOL_VTA'], 'hora': p.get('HORA'), 'importe': round(amt(p), 2), 'quien': self.name(p.get('COD_CAJERO'))} for p in canc],
                  'entradas': ent, 'salidas': sal, 'didi': didi, 'cortes': cortes, 'precios': self.PRECIOS.get(d.isoformat(), []),
                  'llevar': {'total': round(sum(t['tot'] for t in rec), 2), 'tickets': len(rec)}, 'lista': tlist,
                  'abierto': any(os.path.dirname(f) == os.path.join(self.base, 'REGISTRO') for tn, f in idx.get(('P', d), []))})
        return s

    def horas_de_corte(self):
        """Hora en que se cerró cada corte (turno) de MrTienda: la fecha más reciente de los archivos del turno ya archivado en CORTESZ."""
        c = getattr(self, '_horas_corte', None)
        if c and time.time() - c[0] < 60: return c[1]
        hechos = {}
        for pat in (os.path.join(self.base, 'CORTESZ', '*', '*.001'), os.path.join(self.base, 'REGISTRO', '*', '*.001')):   # V, P, D y A del turno: la más reciente es el cierre
            for f in glob.glob(pat):
                m = FNAME.match(os.path.basename(f))
                if not m: continue
                try: t = os.path.getmtime(f)
                except OSError: continue
                k = m.group(2, 3, 4, 5); hechos[k] = max(hechos.get(k, 0), t)
        lst = sorted(hechos.values())[-40:]
        self._horas_corte = (time.time(), lst)
        return lst

    def abiertas(self):
        # cuentas abiertas (sin cobrar): COMUNES\PENDIENT.DBF, renglones vigentes con PAGADO = falso. Solo lectura.
        com = os.path.join(self.base, 'COMUNES')
        out = []
        try: var, svar = self._variantes()
        except Exception: var, svar = {}, {}
        for x in self.rows(os.path.join(com, 'PENDIENT.DBF')):
            if x.get('PAGADO'): continue
            fa = x.get('FECHA_AP') or x.get('FECHA') or ''
            items = []
            arch = (x.get('FILE') or '').strip()
            if arch:
              try:
                for nombre in (arch + '.DBF', arch, arch + '.001'):
                    ruta = os.path.join(com, nombre)
                    if os.path.isfile(ruta):
                        for v in self.rows(ruta):
                            if v.get('DES_PROD') is None: break
                            q = v.get('CANTIDAD') or 0
                            fv = (v.get('FECHA') or '').strip()
                            items.append({'n': (v.get('DES_PROD') or '').strip(), 'q': q, 'imp': round((v.get('PRECIO') or 0) * q, 2),
                                          'orig': round((v.get('PRECIO_O') or 0) * q, 2), 'h': (v.get('HORACAPTUR') or '')[:5],
                                          'f': f'{fv[:4]}-{fv[4:6]}-{fv[6:8]}' if len(fv) == 8 else '',
                                          'quien': self.name(v.get('COD_VENDED')) if v.get('COD_VENDED') else '',
                                          'llevar': v.get('COD_ESCALA') == '02',
                                          'det': self.detalle_variantes(v, var, svar) if var else ''})
                        break
              except Exception as e_:   # 30.7.76: un detalle dañado o a medio escribir no debe impedir que entren los meseros
                items = []; k_ = 'det:' + arch
                if not getattr(self, '_avisados', None): self._avisados = set()
                if k_ not in self._avisados: self._avisados.add(k_); log('Cuentas abiertas: no pude leer el detalle de ' + arch + ' (' + (x.get('REF') or '').strip() + '): ' + repr(e_))
            turno_raw = next((str(x.get(k) or '').strip() for k in ('TURNO','COD_TURNO','NUM_TURNO','CORTE') if str(x.get(k) or '').strip()), '')
            out.append({'ref': (x.get('REF') or '').strip(), 'mesa': (x.get('REF') or '').strip() or 'Sin nombre', 'fecha': f'{fa[:4]}-{fa[4:6]}-{fa[6:8]}' if len(fa) == 8 else '',
                        'abrio': (x.get('HORA_AP') or x.get('HORA') or '')[:5], 'mesero': (x.get('DES_VENDED') or '').strip().title() or self.name(x.get('COD_VENDED')),
                        'pers': int(x.get('COMENSALES') or 1), 'arts': int(x.get('ARTS') or 0), 'total': round(x.get('IMPORTE_MN') or 0, 2),
                        'llevar': x.get('COD_ESCALA') == '02', 'archivo': arch, 'items': items, 'turno': turno_raw,
                        'cod': (x.get('COD_VENDED') or '').strip(), 'cte': (x.get('COD_CTE') or '').strip(), 'des_cte': _dbf_txt((x.get('DES_CTE') or '').strip())})
        # cuántos cortes de caja han pasado desde que se abrió cada cuenta (1 = amarillo, 2 o más = rojo)
        try:
            cortes = self.horas_de_corte()
            for a in out:
                try: ab = datetime.datetime.strptime(a['fecha'] + ' ' + (a['abrio'] or '00:00'), '%Y-%m-%d %H:%M').timestamp()
                except ValueError: a['cortes'] = 0; continue
                a['cortes'] = sum(1 for t in cortes if t > ab + 60)
        except Exception as e: log('No pude contar los cortes de las cuentas abiertas: ' + str(e))
        try:
            ped = self.pedidos()
            for a in out:
                p_ = ped.get(a['archivo'])
                if p_: a['canal'] = {k: p_.get(k, '') for k in ('canal', 'pedido', 'pago', 'desc', 'cobrado', 'nombre', 'completo', 'emp')}
        except Exception: pass
        return sorted(out, key=lambda a: (a['fecha'], a['abrio']))

    def eliminar_abierta(self, archivo, referencia, motivo='Sin clasificar', nota='', persona='', liq=None):
        """Replica la baja observada en MrTienda para una cuenta abierta y NO pagada.
        Respalda PENDIENT.DBF y el DBF de la cuenta, marca el renglón de PENDIENT como eliminado
        y retira el DBF activo. No toca DATABASE ni archivos .ENC/.BAK de MrTienda.
        """
        archivo = re.sub(r'[^A-Za-z0-9_-]', '', str(archivo or '').strip())
        referencia = str(referencia or '').strip()
        if not archivo: raise ValueError('Faltan datos de la cuenta.')
        if motivo not in MOTIVOS: raise ValueError('Elige un motivo válido.')
        nota = str(nota or '').strip()[:120]
        ped = self.pedidos().get(archivo) or {}
        persona = self._persona(motivo, persona, previo=ped.get('completo') or '')
        liq_g = self._liq_guardar(motivo, liq, ped=ped)   # se valida antes de tocar MrTienda
        foto = next((a for a in self.abiertas() if a['archivo'].upper() == archivo.upper() and a['ref'] == referencia), None)
        if foto is None: raise RuntimeError('La cuenta ya no está abierta o cambió. Actualiza la pantalla.')
        com = os.path.join(self.base, 'COMUNES')
        pend = os.path.join(com, 'PENDIENT.DBF')
        cuenta = os.path.join(com, archivo + '.DBF')
        with self.write_lock:
            with open(pend, 'rb') as f: b = bytearray(f.read())
            if len(b) < 32: raise RuntimeError('PENDIENT.DBF no es válido.')
            n, hl, rl = struct.unpack('<IHH', b[4:12])
            flds=[]; i=32; off=1
            while i < hl and b[i] != 0x0D:
                name=b[i:i+11].split(b'\0')[0].decode('latin-1'); typ=chr(b[i+11]); ln=b[i+16]
                flds.append((name,typ,ln,off)); off += ln; i += 32
            fm={x[0]:x for x in flds}
            if 'FILE' not in fm or 'REF' not in fm or 'PAGADO' not in fm: raise RuntimeError('PENDIENT.DBF no tiene la estructura esperada.')
            def txt(rec, name):
                _,typ,ln,o=fm[name]; return rec[o:o+ln].decode('latin-1','ignore').strip()
            target=None
            for r in range(n):
                s=hl+r*rl; rec=b[s:s+rl]
                if len(rec)<rl: break
                if rec[0:1] == b'*': continue
                if txt(rec,'FILE').upper()==archivo.upper() and txt(rec,'REF')==referencia:
                    _,_,ln,o=fm['PAGADO']; pag=rec[o:o+ln] in (b'T',b't',b'Y',b'y')
                    if pag: raise RuntimeError('La cuenta ya está pagada. No se eliminó nada.')
                    target=s; break
            if target is None: raise RuntimeError('La cuenta ya no está abierta o cambió. Actualiza la pantalla.')
            # La prueba controlada mostró que al vaciar la cuenta MrTienda marca PENDIENT con * y desaparece FILE.DBF.
            sello=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            bdir=os.path.join(RESPALDO_CUENTAS,sello+'_'+archivo)
            os.makedirs(bdir, exist_ok=False)
            shutil.copy2(pend, os.path.join(bdir,'PENDIENT_ANTES.DBF'))
            if os.path.isfile(cuenta): shutil.copy2(cuenta, os.path.join(bdir,archivo+'.DBF'))
            b[target]=0x2A
            tmp=pend+'.holtmp'
            try:
                with open(tmp,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
                os.replace(tmp, pend)
                if os.path.isfile(cuenta): os.remove(cuenta)
            except Exception:
                try:
                    if os.path.exists(tmp): os.remove(tmp)
                    shutil.copy2(os.path.join(bdir,'PENDIENT_ANTES.DBF'), pend)
                except Exception: pass
                raise
            self.cache = {k:v for k,v in self.cache.items() if k[0] not in (pend,cuenta)}
            log(f'CUENTA QUITADA remotamente: {referencia} / {archivo} · {motivo}{" · " + persona if persona else ""}. Respaldo: {bdir}')
        ahora = datetime.datetime.now()
        try:
            self.anotar_quitada({'id': sello + '_' + archivo, 'quitada': ahora.isoformat(timespec='seconds'),
                                 'fecha': foto['fecha'] or ahora.date().isoformat(), 'abrio': foto['abrio'], 'cuenta': foto['mesa'],
                                 'mesero': foto['mesero'], 'pers': foto['pers'], 'arts': foto['arts'], 'total': foto['total'],
                                 'llevar': foto['llevar'], 'items': [{'n': i['n'], 'q': i['q'], 'imp': i['imp']} for i in foto['items']],
                                 'motivo': motivo, 'persona': persona, 'nota': nota, 'respaldo': os.path.basename(bdir),
                                 **({'liq': liq_g} if liq_g is not None else {}), **({'pedido': ped['pedido']} if ped.get('pedido') else {}), 'archivo': archivo,
                                 **({'ce': {k: ped.get(k) for k in ('emp', 'completo', 'segunda', 'autoriza')}} if ped.get('canal') == 'empleado' else {})})
        except Exception as e:
            log('La cuenta se quitó pero no pude anotarla en la bitácora: ' + str(e))
        self.refresh()
        return {'ok':True,'mensaje':'Cuenta quitada','respaldo':os.path.basename(bdir)}

    def excel_quitadas(self, desde, hasta, grupo):
        q = [r for r in self.quitadas() if (not desde or r.get('fecha', '') >= desde) and (not hasta or r.get('fecha', '') <= hasta)]
        if grupo == 'didi': q = [r for r in q if r.get('motivo', '').startswith('Didi')]
        elif grupo == 'uber': q = [r for r in q if r.get('motivo', '').startswith('Uber')]
        elif grupo == 'plataformas': q = [r for r in q if r.get('motivo', '').startswith(('Didi', 'Uber'))]
        elif grupo == 'familia': q = [r for r in q if r.get('motivo') == 'Familia']
        elif grupo == 'empleados': q = [r for r in q if r.get('motivo') == 'Empleado']
        elif grupo == 'cortesias': q = [r for r in q if r.get('motivo') == 'Cortesía']
        if grupo in ('familia', 'empleados', 'cortesias'): q.sort(key=lambda r: ((r.get('persona') or '').lower(), r.get('fecha', ''), r.get('abrio', '')))
        else: q.sort(key=lambda r: (r.get('fecha', ''), r.get('abrio', '')))
        head = ['Fecha', 'Abrió', 'Quitada', 'Cuenta', 'Motivo', 'De quién', 'Plataforma', 'Pago', 'Nota', 'Mesero', 'Artículos', 'Importe MrTienda', 'Detalle']
        rows = []
        for r in q:
            m = r.get('motivo', ''); plat, _, pago = m.partition(' · ')
            rows.append([r.get('fecha', ''), r.get('abrio', ''), (r.get('quitada') or '')[11:16], r.get('cuenta', ''), m, r.get('persona', ''),
                         plat if pago else '', pago, r.get('nota', ''), r.get('mesero', ''), r.get('arts') or len(r.get('items') or []),
                         float(r.get('total') or 0), ', '.join(f"{int(i['q']) if float(i['q']).is_integer() else i['q']} {i['n']}" for i in r.get('items') or [])])
        tot = round(sum(x[11] for x in rows), 2)
        titulo = {'didi': 'Didi', 'uber': 'Uber', 'plataformas': 'Didi y Uber', 'familia': 'Familia', 'empleados': 'Empleados', 'cortesias': 'Cortesías'}.get(grupo, 'Cuentas quitadas')
        extra = []
        if grupo in ('familia', 'empleados', 'cortesias'):
            por = collections.OrderedDict()
            for x in rows: por[x[5] or 'Sin nombre'] = por.get(x[5] or 'Sin nombre', 0) + x[11]
            extra = [[], ['', '', '', 'TOTAL POR PERSONA']] + [['', '', '', n, '', '', '', '', '', '', '', round(v, 2), ''] for n, v in por.items()]
        return xlsx([head] + rows + extra + [[], ['', '', '', 'TOTAL · ' + str(len(rows)) + ' cuentas', '', '', '', '', '', '', '', tot, '']], titulo,
                    widths=[11, 7, 8, 22, 15, 18, 10, 9, 22, 14, 9, 15, 60], money_cols={11})

    def productos_dia(self, idx, d):
        """Piezas e importe por producto de un día, sin guardar los renglones en memoria (solo el resumen)."""
        arch = sorted(idx.get(('P', d), [])) + sorted(idx.get(('V', d), []))
        firma = []
        for tn, f in arch:
            try: st = os.stat(f); firma.append((f, st.st_size, int(st.st_mtime)))
            except OSError: pass
        firma = tuple(firma)
        prev = self.psum.get(d)
        if prev and prev[0] == firma: return prev[1]
        validos = set()
        for tn, f in idx.get(('P', d), []):
            for p in safe_rows(f):
                if not p['_del'] and p.get('FOL_VTA') and not p.get('CANCELADA'): validos.add((tn, p['FOL_VTA']))
        out = {}
        for tn, f in idx.get(('V', d), []):
            for v in safe_rows(f):
                if v['_del'] or (tn, v.get('FOL_VTA')) not in validos: continue
                n = (v.get('DES_PROD') or '').strip()
                if not n: continue
                q = v.get('CANTIDAD') or 0
                o = out.setdefault(n, [0, 0, self.FAM.get(v.get('COD_FAMILI'), 'Otros')])
                o[0] += q; o[1] += (v.get('PRECIO') or 0) * q
        self.psum[d] = (firma, out)
        return out

    def productos_mes(self, idx, days):
        mm = collections.defaultdict(dict)
        for d in days:
            m = mm[d.isoformat()[:7]]
            for n, (q, imp, fam) in self.productos_dia(idx, d).items():
                o = m.setdefault(n, [0, 0, fam]); o[0] += q; o[1] += imp; o[2] = fam
        vivos = set(days)
        self.psum = {k: v for k, v in list(self.psum.items()) if k in vivos}
        return {k: [[n, o[2], round(o[0], 2), round(o[1], 2)] for n, o in sorted(v.items(), key=lambda x: -x[1][0])] for k, v in sorted(mm.items())}

    # ------------------------------------------------ bitácora de cuentas quitadas (archivo propio, no de MrTienda)
    def quitadas(self):
        with self.q_lock:
            try:
                with open(QUITADAS_FILE, encoding='utf-8') as f: q = json.load(f)
                return q if isinstance(q, list) else []
            except (OSError, ValueError): return []

    def _guardar_quitadas(self, q):
        tmp = QUITADAS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(q, f, ensure_ascii=False, indent=1)
        os.replace(tmp, QUITADAS_FILE)

    # ------------------------------------------------ 30.7.47: Didi y Uber — lo que paga la plataforma (precio − comisión − descuentos)
    # 30.7.49: como el estado de cuenta de Didi que mandó Jorge (20-oct-2026): lo cobrado al cliente ya trae IVA 8%;
    # base = cobrado / 1.08; comisión 30% de la base + IVA 8% de la comisión; retenciones de ISR 2.5% y de IVA sobre la base; menos lo que absorbe el restaurante.
    PLAT_BASE = {'pct': 30.0, 'iva_com': 8.0, 'iva_venta': 8.0, 'ret_isr': 2.5, 'ret_iva': 8.0, 'iva': True}
    def plat_cfg(self):
        base = {'Didi': dict(self.PLAT_BASE), 'Uber': dict(self.PLAT_BASE)}
        for k, v in (self.cfg.get('plataformas') or {}).items():
            if k in base and isinstance(v, dict):
                c = dict(base[k])
                for f in ('pct', 'iva_com', 'iva_venta', 'ret_isr', 'ret_iva'):
                    if isinstance(v.get(f), (int, float)): c[f] = float(v[f])
                    elif f in v and v[f] is None and f == 'pct': c[f] = None
                if 'iva_com' not in v and 'iva' in v: c['iva_com'] = 8.0 if v.get('iva') else 0.0   # configuración vieja (30.7.47)
                c['iva'] = c['iva_com'] > 0
                base[k] = c
        return base

    @staticmethod
    def _monto(v, nombre):
        if v is None or str(v).strip() == '': return None
        try: x = round(float(str(v).replace('$', '').replace(',', '').strip()), 2)
        except ValueError: raise ValueError(nombre + ': escribe solo números.')
        if x < 0 or x > 1000000: raise ValueError(nombre + ': cantidad no válida.')
        return x

    def _liq_guardar(self, motivo, liq, previo=None, ped=None):
        """Lo que se guarda en la cuenta quitada: descuentos y, si se escribió a mano, la comisión."""
        if not str(motivo or '').startswith(('Didi', 'Uber')): return None
        liq = liq if isinstance(liq, dict) else {}
        out = dict(previo or {})
        if 'desc' in liq: out['desc'] = self._monto(liq.get('desc'), 'Descuentos') or 0
        elif 'desc' not in out and ped and ped.get('desc') is not None: out['desc'] = ped['desc']
        if 'cobrado' in liq:
            cb = self._monto(liq.get('cobrado'), 'Lo cobrado al cliente')
            if cb is None: out.pop('cobrado', None)
            else: out['cobrado'] = cb
        elif 'cobrado' not in out and ped and ped.get('cobrado') is not None: out['cobrado'] = ped['cobrado']
        if 'comision' in liq:
            c = self._monto(liq.get('comision'), 'Comisión')
            if c is None: out.pop('comision', None)
            else: out['comision'] = c
        return out

    def liquidacion(self, r):
        """Precio de menú, comisión (automática con el % de la plataforma o la escrita a mano), descuentos y lo que paga la plataforma."""
        mot = str(r.get('motivo') or '')
        if not mot.startswith(('Didi', 'Uber')): return None
        plat = 'Didi' if mot.startswith('Didi') else 'Uber'; c = self.plat_cfg()[plat]
        g = r.get('liq') or {}; precio = round(float(r.get('total') or 0), 2); desc = round(float(g.get('desc') or 0), 2)
        cobrado = round(float(g['cobrado']), 2) if g.get('cobrado') is not None else precio   # sin recibo capturado: precio de menú
        base = round(cobrado / (1 + c['iva_venta'] / 100), 2)
        ret_isr = round(base * c['ret_isr'] / 100, 2); ret_iva = round(base * c['ret_iva'] / 100, 2)
        if g.get('comision') is not None: com, iva_com, manual = round(float(g['comision']), 2), 0.0, True   # escrita a mano: ya con IVA
        elif c['pct'] is not None: com = round(base * c['pct'] / 100, 2); iva_com = round(com * c['iva_com'] / 100, 2); manual = False
        else: com, iva_com, manual = None, 0.0, False
        neto = round(cobrado - com - iva_com - ret_isr - ret_iva - desc, 2) if com is not None else None
        return {'plat': plat, 'precio': precio, 'cobrado': cobrado, 'sin_cobrado': g.get('cobrado') is None, 'base': base, 'desc': desc,
                'comision': com, 'iva_com': iva_com, 'ret_isr': ret_isr, 'ret_iva': ret_iva, 'manual': manual, 'capturado': 'desc' in g or 'cobrado' in g,
                'neto': neto, 'pct': c['pct'], 'iva': c['iva'], 'cfg': c}

    def quitadas_liq(self):
        out = []
        for r in self.quitadas():
            x = dict(r); l_ = self.liquidacion(x)
            if l_: x['liq'] = l_
            else: x.pop('liq', None)
            out.append(x)
        return out

    def plat_pagos(self):
        try:
            with open(PLAT_PAGOS_FILE, encoding='utf-8') as f: d = json.load(f)
            return d if isinstance(d, list) else []
        except (OSError, ValueError): return []

    def plataformas_accion(self, d):
        acc = d.get('accion')
        if acc == 'config':
            cfg = {}
            for k in ('Didi', 'Uber'):
                v = (d.get('plataformas') or {}).get(k) or {}
                pct = self._monto(v.get('pct'), 'Porcentaje de ' + k)
                if pct is not None and pct > 100: raise ValueError('Porcentaje de ' + k + ': no puede ser más de 100.')
                cfg[k] = {'pct': pct}
                for f, nom in (('iva_com', 'IVA de la comisión'), ('iva_venta', 'IVA de la venta'), ('ret_isr', 'Retención de ISR'), ('ret_iva', 'Retención de IVA')):
                    x = self._monto(v.get(f), nom + ' de ' + k)
                    if x is not None and x > 100: raise ValueError(nom + ' de ' + k + ': no puede ser más de 100.')
                    cfg[k][f] = x if x is not None else self.PLAT_BASE[f]
            self.cfg['plataformas'] = cfg
            with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(self.cfg, fh, ensure_ascii=False, indent=2)
        elif acc in ('pago', 'pago_borrar'):
            with self.q_lock:
                L = self.plat_pagos()
                if acc == 'pago':
                    plat = d.get('plataforma')
                    if plat not in ('Didi', 'Uber'): raise ValueError('Plataforma no válida.')
                    de, ha = str(d.get('desde') or '')[:10], str(d.get('hasta') or '')[:10]
                    datetime.date.fromisoformat(de); datetime.date.fromisoformat(ha)
                    if de > ha: de, ha = ha, de
                    monto = self._monto(d.get('monto'), 'Lo que pagó ' + plat)
                    if monto is None: raise ValueError('Escribe cuánto pagó ' + plat + '.')
                    L.append({'id': secrets.token_hex(5), 'plataforma': plat, 'desde': de, 'hasta': ha, 'monto': monto,
                              'nota': str(d.get('nota') or '').strip()[:80], 'capturado': datetime.datetime.now().isoformat(timespec='seconds')})
                else:
                    n0 = len(L); L = [x for x in L if x.get('id') != d.get('id')]
                    if len(L) == n0: raise RuntimeError('No encontré ese pago.')
                tmp = PLAT_PAGOS_FILE + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f: json.dump(L, f, ensure_ascii=False, indent=1)
                os.replace(tmp, PLAT_PAGOS_FILE)
        else: raise ValueError('Acción no válida.')
        self.refresh()
        return {'ok': True}

    def anotar_quitada(self, reg):
        with self.q_lock:
            try:
                with open(QUITADAS_FILE, encoding='utf-8') as f: q = json.load(f)
                if not isinstance(q, list): q = []
            except (OSError, ValueError): q = []
            q.append(reg); self._guardar_quitadas(q)

    # ------------------------------------------------ gastos y compras (archivo propio)
    def gastos(self):
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
            except (OSError, ValueError): g = {}
        if not isinstance(g, dict): g = {}
        g.setdefault('movs', []); g.setdefault('fijos', []); g.setdefault('recibos', [])
        return g

    def _guardar_gastos(self, g):
        tmp = GASTOS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(g, f, ensure_ascii=False, indent=1)
        os.replace(tmp, GASTOS_FILE)

    def _limpia_mov(self, m):
        fecha = str(m.get('fecha') or '')[:10] or datetime.date.today().isoformat()
        datetime.date.fromisoformat(fecha)
        cat = m.get('categoria') if m.get('categoria') in CATEGORIAS_GASTO else 'Otros gastos'
        monto = round(float(str(m.get('monto')).replace('$', '').replace(',', '')), 2)
        if monto <= 0 or monto > 10000000: raise ValueError('Monto no válido.')
        concepto = str(m.get('concepto') or '').strip()[:80] or (str(m.get('proveedor') or '').strip()[:80] or cat)
        out = {'fecha': fecha, 'concepto': concepto, 'categoria': cat, 'monto': monto,
               'proveedor': str(m.get('proveedor') or '').strip()[:60], 'nota': str(m.get('nota') or '').strip()[:120],
               'negocio': m.get('negocio') if m.get('negocio') in NEGOCIOS else 'Restaurante',
               'ticket': str(m.get('ticket') or '').strip()[:30], 'pago': m.get('pago') if m.get('pago') in PAGOS else '',
               'quien': str(m.get('quien') or '').strip()[:40]}
        try:
            tasa = float(m.get('tasa')) if m.get('tasa') not in (None, '') else None
        except (TypeError, ValueError): tasa = None
        if tasa is not None and tasa in (0, 0.08, 0.16):
            sub = round(monto / (1 + tasa), 2)
            out.update({'tasa': tasa, 'subtotal': sub, 'iva': round(monto - sub, 2)})
        try:
            s_, i_ = float(m.get('subtotal')), float(m.get('iva'))
            if s_ >= 0 and i_ >= 0 and abs(s_ + i_ - monto) <= max(2, monto * 0.02): out.update({'subtotal': round(s_, 2), 'iva': round(i_, 2)})
        except (TypeError, ValueError): pass
        if isinstance(m.get('items'), list): out['items'] = limpia_items(m['items'])
        if isinstance(m.get('lectura'), dict): out['lectura'] = limpia_lectura(m['lectura'])
        try:
            ic = round(float(m.get('importe_capturado')), 2)
            if ic > 0: out['importe_capturado'] = ic
        except (TypeError, ValueError): pass
        return out

    # ------------------------------------------------ cajeros con clave propia
    def cajeros(self):
        try:
            with open(CAJEROS_FILE, encoding='utf-8') as f: c = json.load(f)
            return c if isinstance(c, list) else []
        except (OSError, ValueError): return []

    def cajero_accion(self, d):
        acc = d.get('accion'); c = self.cajeros()
        if acc == 'alta':
            nombre = str(d.get('nombre') or '').strip()[:40]; pin = str(d.get('pin') or '').strip()
            if not nombre: raise ValueError('Escribe el nombre.')
            if not re.fullmatch(r'\d{4,6}', pin): raise ValueError('La clave debe ser de 4 a 6 números.')
            if any(x.get('activo') and verifica_pin(x, pin) for x in c): raise ValueError('Esa clave ya la tiene otra persona. Usa otra.')
            sal = secrets.token_hex(8)
            c.append({'id': secrets.token_hex(4), 'nombre': nombre, 'sal': sal, 'huella': huella_pin(sal, pin), 'activo': True,
                      'alta': datetime.datetime.now().isoformat(timespec='seconds')})
        elif acc == 'clave':
            pin = str(d.get('pin') or '').strip()
            if not re.fullmatch(r'\d{4,6}', pin): raise ValueError('La clave debe ser de 4 a 6 números.')
            x = next((x for x in c if x.get('id') == d.get('id') and x.get('activo')), None)
            if not x: raise RuntimeError('No encontré a ese cajero.')
            if any(o is not x and o.get('activo') and verifica_pin(o, pin) for o in c): raise ValueError('Esa clave ya la tiene otra persona. Usa otra.')
            x['sal'] = secrets.token_hex(8); x['huella'] = huella_pin(x['sal'], pin); x['cambio_clave'] = datetime.datetime.now().isoformat(timespec='seconds')
            for t in [t for t, v in SESIONES.items() if v[0] == x['nombre']]: SESIONES.pop(t, None)
        elif acc == 'baja':
            for x in c:
                if x.get('id') == d.get('id'): x['activo'] = False; x['baja'] = datetime.datetime.now().isoformat(timespec='seconds')
            SESIONES.clear()
        else: raise ValueError('Acción no válida.')
        tmp = CAJEROS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CAJEROS_FILE)
        self.refresh()
        return {'ok': True}

    def login_cajero(self, pin, ip):
        ahora = time.time(); f = FALLOS.get(ip, [0, 0])
        if f[1] > ahora: raise RuntimeError('Demasiados intentos. Espera unos minutos.')
        for x in self.cajeros():
            if x.get('activo') and verifica_pin(x, str(pin or '')):
                FALLOS.pop(ip, None); tok = secrets.token_urlsafe(24)
                SESIONES[tok] = (x['nombre'], ahora + 14 * 3600)
                return {'ok': True, 'token': tok, 'nombre': x['nombre']}
        f[0] += 1
        if f[0] >= 5: f = [0, ahora + 300]
        FALLOS[ip] = f
        raise RuntimeError('Clave incorrecta.')

    # ------------------------------------------------ comandero (meseros con clave de 4 números)
    # Lee de MrTienda menú, precios, variantes y cuentas abiertas; envía pedidos solo si Jorge lo enciende.
    # ------------------------------------------------ 30.7.55: comida de empleados desde el comandero
    def comidas_emp(self):
        try:
            with open(COMIDAS_EMP_FILE, encoding='utf-8') as f: c = json.load(f)
            return c if isinstance(c, list) else []
        except (OSError, ValueError): return []

    def _anotar_comida_emp(self, reg):
        with self.q_lock:
            c = self.comidas_emp(); c.append(reg); c = c[-3000:]
            tmp = COMIDAS_EMP_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(c, f, ensure_ascii=False, indent=1)
            os.replace(tmp, COMIDAS_EMP_FILE)

    def _firma_ok(self, e, pin):
        """La clave con que firma su comida: la suya de Personal o, si es mesero, la del comandero."""
        pin = re.sub(r'\D', '', str(pin or ''))
        if not pin: return False
        if isinstance(e.get('firma'), dict) and verifica_pin(e['firma'], pin): return True
        cod = str(e.get('cod_mt') or '').strip()
        return bool(cod) and any(m.get('activo') and str(m.get('cod') or '').strip() == cod and verifica_pin(m, pin) for m in self.meseros())

    def _tiene_firma(self, e):
        cod = str(e.get('cod_mt') or '').strip()
        return bool((e.get('firma') or {}).get('sal')) or (bool(cod) and any(m.get('activo') and str(m.get('cod') or '').strip() == cod for m in self.meseros()))

    def empleados_ce(self):
        """Lo que ve el comandero: empleados activos con sobrenombre y puesto (sin claves)."""
        out = []
        for e in self.rh().get('empleados', []):
            if not e.get('activo') or not e.get('nombre'): continue
            nom = re.sub(r'\s+', ' ', e['nombre']).strip().title()
            out.append({'id': e['id'], 'nombre': nom, 'apodo': str(e.get('apodo') or '').strip(), 'puesto': e.get('puesto') or 'Otro', 'firma': self._tiene_firma(e)})
        return sorted(out, key=lambda x: (x['puesto'], x['apodo'] or x['nombre']))

    def ce_validar(self, d, ahora=None):
        """Revisa que el empleado pueda pedir su comida: firma con su clave, trabaja hoy y no ha comido (o lo autoriza la encargada)."""
        ahora = ahora or datetime.datetime.now(); rh = self.rh()
        e = next((x for x in rh.get('empleados', []) if x.get('id') == d.get('emp') and x.get('activo')), None)
        if not e: raise ValueError('Elige al empleado de la lista.')
        nom = re.sub(r'\s+', ' ', e['nombre']).strip().title(); corto = str(e.get('apodo') or '').strip() or nom.split(' ')[0]
        if not self._tiene_firma(e): raise ValueError(corto + ' todavía no tiene clave para firmar su comida. Jorge se la pone en Personal.')
        k = 'ce:' + e['id']; f = FALLOS.get(k, [0, 0])
        if f[1] > time.time(): raise ValueError('Demasiados intentos con la clave de ' + corto + '. Espera 5 minutos.')
        if not self._firma_ok(e, d.get('firma')):
            f[0] += 1
            if f[0] >= 5: f = [0, time.time() + 300]
            FALLOS[k] = f
            raise ValueError('Clave incorrecta. ' + corto + ' debe poner SU clave.')
        FALLOS.pop(k, None)
        if self.cfg.get('comida_emp_rol', True):
            c_, _ = rh_dia(rh, e, ahora.date())
            if c_ != 'T':
                txt = {'D': 'descansa', 'F': 'tiene falta', 'V': 'está de vacaciones', 'I': 'está de incapacidad'}.get(c_, 'no trabaja')
                raise ValueError('Según el rol, hoy ' + corto + ' ' + txt + '. La comida de empleados es solo para quien trabaja.')
        hoy = ahora.date().isoformat()
        ya = [c for c in self.comidas_emp() if c.get('emp') == e['id'] and c.get('fecha') == hoy]
        autoriza = ''
        if ya and self.cfg.get('comida_emp_una', True):
            if not str(d.get('autoriza') or '').strip():
                return {'ok': False, 'autorizar': True, 'error': corto + ' ya comió hoy a las ' + ya[-1].get('hora', '') + '. Para otra comida, la encargada pone su clave.'}
            fa = FALLOS.get('ce-autoriza', [0, 0])   # 30.7.56: 5 claves de encargada equivocadas bloquean las autorizaciones 10 minutos
            if fa[1] > time.time(): raise ValueError('Demasiados intentos con la clave de la encargada. Espera 10 minutos.')
            enc = [x for x in rh.get('empleados', []) if x.get('activo') and x.get('puesto') == 'Encargada' and x.get('id') != e['id']]
            a = next((x for x in enc if self._firma_ok(x, d.get('autoriza'))), None)
            if not a:
                fa[0] += 1
                if fa[0] >= 5:
                    fa = [0, time.time() + 600]; log('Comida de empleados: 5 claves de encargada equivocadas; autorizaciones bloqueadas 10 minutos.')
                FALLOS['ce-autoriza'] = fa
                raise ValueError('Esa clave no es de una encargada.')
            FALLOS.pop('ce-autoriza', None)
            autoriza = str(a.get('apodo') or '').strip() or a['nombre'].title().split(' ')[0]
        return {'ok': True, 'emp': {'id': e['id'], 'nombre': nom, 'corto': corto}, 'segunda': bool(ya), 'autoriza': autoriza}

    def comida_emp_accion(self, d):
        if d.get('accion') == 'tope':
            v = self._monto(d.get('monto'), 'Monto')
            if v is None: self.cfg.pop('comida_emp_tope', None)
            else: self.cfg['comida_emp_tope'] = v
            with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(self.cfg, fh, ensure_ascii=False, indent=2)
        else: raise ValueError('Acción no válida.')
        self.refresh()
        return {'ok': True}

    def meseros(self):
        try:
            with open(MESEROS_FILE, encoding='utf-8') as f: c = json.load(f)
            return c if isinstance(c, list) else []
        except (OSError, ValueError): return []

    def mesero_accion(self, d):
        acc = d.get('accion'); c = self.meseros()
        emp = getattr(self, 'EMP', {})
        if acc == 'ver_clave':
            x = next((x for x in c if x.get('id') == d.get('id') and x.get('activo')), None)
            if not x: raise RuntimeError('No encontré a ese mesero.')
            return {'ok': True, 'clave': x.get('clave', '')}
        if acc == 'candado':
            self.cfg['candado_rol'] = bool(d.get('activo'))
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            log('Candado del rol de descansos ' + ('ENCENDIDO' if self.cfg['candado_rol'] else 'apagado'))
            self.refresh(); return {'ok': True}
        if acc == 'alta':
            cod = str(d.get('cod') or '').strip(); pin = str(d.get('pin') or '').strip()
            if cod not in emp: raise ValueError('Elige a la persona de la lista de MrTienda.')
            if not re.fullmatch(r'\d{4}', pin): raise ValueError('La clave debe ser de 4 números.')
            if any(x.get('activo') and x.get('cod') == cod for x in c): raise ValueError(emp[cod] + ' ya tiene acceso al comandero. Si quieres, cámbiale la clave.')
            if any(x.get('activo') and verifica_pin(x, pin) for x in c): raise ValueError('Esa clave ya la tiene otra persona. Usa otra.')
            sal = secrets.token_hex(8)
            c.append({'id': secrets.token_hex(4), 'cod': cod, 'nombre': emp[cod], 'sal': sal, 'huella': huella_pin(sal, pin), 'clave': pin, 'activo': True,
                      'alta': datetime.datetime.now().isoformat(timespec='seconds')})
        elif acc == 'clave':
            pin = str(d.get('pin') or '').strip()
            if not re.fullmatch(r'\d{4}', pin): raise ValueError('La clave debe ser de 4 números.')
            x = next((x for x in c if x.get('id') == d.get('id') and x.get('activo')), None)
            if not x: raise RuntimeError('No encontré a ese mesero.')
            if any(o is not x and o.get('activo') and verifica_pin(o, pin) for o in c): raise ValueError('Esa clave ya la tiene otra persona. Usa otra.')
            x['sal'] = secrets.token_hex(8); x['huella'] = huella_pin(x['sal'], pin); x['clave'] = pin; x['cambio_clave'] = datetime.datetime.now().isoformat(timespec='seconds')
            for t in [t for t, v in SESIONES_M.items() if v[0] == x['id']]: SESIONES_M.pop(t, None)
        elif acc == 'solo_huella':
            x = next((x for x in c if x.get('id') == d.get('id') and x.get('activo')), None)
            if not x: raise RuntimeError('No encontré a ese mesero.')
            if d.get('activo'):
                if not huellas(): raise RuntimeError('Primero activa tu huella en el panel (dirección segura): ⚙️ Ajustes → Entrada con huella.')
                for y in c: y['solo_huella'] = False   # solo un usuario: el de Jorge
                x['solo_huella'] = True; x['remoto'] = True   # la huella solo funciona en la dirección segura (Tailscale)
            else: x['solo_huella'] = False
            for t in [t for t, v in SESIONES_M.items() if v[0] == x['id']]: SESIONES_M.pop(t, None)
        elif acc == 'remoto':
            x = next((x for x in c if x.get('id') == d.get('id') and x.get('activo')), None)
            if not x: raise RuntimeError('No encontré a ese mesero.')
            x['remoto'] = bool(d.get('activo'))
            if not x['remoto']:
                for t in [t for t, v in SESIONES_M.items() if v[0] == x['id']]: SESIONES_M.pop(t, None)
        elif acc == 'baja':
            for x in c:
                if x.get('id') == d.get('id') and x.get('activo'): x['activo'] = False; x['baja'] = datetime.datetime.now().isoformat(timespec='seconds')
            for t in [t for t, v in SESIONES_M.items() if v[0] == d.get('id')]: SESIONES_M.pop(t, None)
        else: raise ValueError('Acción no válida.')
        tmp = MESEROS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MESEROS_FILE)
        self.refresh()
        return {'ok': True}

    def login_mesero(self, pin, ip):
        ahora = time.time(); k = 'm:' + ip; f = FALLOS.get(k, [0, 0])
        if f[1] > ahora: raise RuntimeError('Demasiados intentos. Espera unos minutos.')
        emp = getattr(self, 'EMP', {})
        for x in self.meseros():
            if x.get('activo') and not x.get('solo_huella') and verifica_pin(x, str(pin or '')):   # 30.7.58: "solo huella" no entra con clave
                if emp and x.get('cod') not in emp: raise RuntimeError('Tu usuario está dado de baja en MrTienda. Avísale a Jorge.')
                if not x.get('remoto') and not ip_del_restaurante(ip): raise RuntimeError(fuera_msg(ip))
                if x.get('clave') != str(pin):   # claves dadas antes de la 27.6: se aprenden al entrar
                    try:
                        c_ = self.meseros()
                        for y in c_:
                            if y.get('id') == x['id']: y['clave'] = str(pin)
                        tmp = MESEROS_FILE + '.tmp'
                        with open(tmp, 'w', encoding='utf-8') as f: json.dump(c_, f, ensure_ascii=False, indent=1)
                        os.replace(tmp, MESEROS_FILE)
                    except Exception as e: log('No pude guardar la clave del mesero: ' + str(e))
                bloqueo = self.candado_rol(x.get('cod'))
                if bloqueo: FALLOS.pop(k, None); raise RuntimeError(bloqueo)
                FALLOS.pop(k, None); tok = secrets.token_urlsafe(24)
                SESIONES_M[tok] = (x['id'], ahora + 14 * 3600)
                return {'ok': True, 'token': tok, 'nombre': emp.get(x['cod'], x['nombre']), 'cod': x['cod']}
        f[0] += 1
        if f[0] >= 5: f = [0, ahora + 300]
        FALLOS[k] = f
        raise RuntimeError('Clave incorrecta.')

    def login_mesero_huella(self, d, host, ip):
        """30.7.58: Jorge entra al comandero con la huella de su teléfono (la misma del panel), sin clave."""
        ahora = time.time(); k = 'mh:' + ip; f = FALLOS.get(k, [0, 0])
        if f[1] > ahora: raise RuntimeError('Demasiados intentos. Espera unos minutos.')
        try: entrar_huella(d, host)
        except Exception:
            f[0] += 1
            if f[0] >= 5: f = [0, ahora + 300]
            FALLOS[k] = f; raise
        FALLOS.pop(k, None)
        emp = getattr(self, 'EMP', {})
        x = next((x for x in self.meseros() if x.get('activo') and x.get('solo_huella')), None)
        if not x: raise RuntimeError('Ningún usuario del comandero está marcado para entrar con huella.')
        if emp and x.get('cod') not in emp: raise RuntimeError('Tu usuario está dado de baja en MrTienda.')
        if not x.get('remoto') and not ip_del_restaurante(ip): raise RuntimeError(fuera_msg(ip))
        bloqueo = self.candado_rol(x.get('cod'))
        if bloqueo: raise RuntimeError(bloqueo)
        tok = secrets.token_urlsafe(24); SESIONES_M[tok] = (x['id'], ahora + 14 * 3600); HUELLA_M[tok] = ahora
        log('COMANDERO: ' + emp.get(x['cod'], x['nombre']) + ' entró con huella.')
        return {'ok': True, 'token': tok, 'nombre': emp.get(x['cod'], x['nombre']), 'cod': x['cod'], 'huella': True}

    def mesero_de(self, tok, ip=''):
        s_ = SESIONES_M.get(tok or '')
        if not s_ or s_[1] < time.time(): SESIONES_M.pop(tok or '', None); return None
        x = next((x for x in self.meseros() if x.get('id') == s_[0] and x.get('activo')), None)
        emp = getattr(self, 'EMP', {})
        if not x or (emp and x.get('cod') not in emp): SESIONES_M.pop(tok, None); return None
        if not x.get('remoto') and not ip_del_restaurante(ip): SESIONES_M.pop(tok, None); return {'bloqueo': fuera_msg(ip)}   # candado: solo WiFi del restaurante
        bloqueo = self.candado_rol(x.get('cod'))
        if bloqueo: SESIONES_M.pop(tok, None); return {'bloqueo': bloqueo}             # candado: rol de descansos y horario
        return {'id': x['id'], 'cod': x['cod'], 'nombre': emp.get(x['cod'], x['nombre']), 'solo_huella': bool(x.get('solo_huella'))}

    # Candado del rol de descansos (versión 27.2): el mesero ligado a un empleado de Personal solo entra
    # si ese día le toca trabajar (T) y está dentro de su horario, con tolerancia antes y después.
    # Quien no está ligado a ningún empleado de Personal (por ejemplo Jorge) no tiene este candado.
    DIAS_RH = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    def candado_rol(self, cod, ahora=None):
        if not self.cfg.get('candado_rol') or not cod: return None
        try: rh = self.rh()
        except Exception as e: log('Candado del rol: no pude leer personal.json: ' + str(e)); return None
        es = [e for e in rh['empleados'] if str(e.get('cod_mt') or '').strip() == str(cod).strip()]
        if not es: return None
        e = next((e for e in es if e.get('activo', True)), None)
        if not e: return 'Estás dado de baja en Personal. Avísale a Jorge o a la encargada.'
        ahora = ahora or datetime.datetime.now()
        def hm(v):
            m = re.fullmatch(r'(\d{1,2}):(\d{2})', str(v or '').strip())
            return (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else None
        hoy = ahora.date(); c, h = rh_dia(rh, e, hoy); ent, sal = hm(h[0]), hm(h[1])
        antes = datetime.timedelta(minutes=int(self.cfg.get('candado_antes_min', 30)))
        despues = datetime.timedelta(minutes=int(self.cfg.get('candado_despues_min', 60)))
        for f in (hoy, hoy - datetime.timedelta(days=1)):     # ayer cuenta si su turno pasa de la medianoche
            cf, hf = rh_dia(rh, e, f)
            if cf != 'T': continue
            a_, b_ = hm(hf[0]), hm(hf[1])
            if not (a_ and b_):
                if f == hoy: return None                     # trabaja hoy y no tiene horario capturado: entra
                continue
            a = datetime.datetime.combine(f, datetime.time(*a_)); b = datetime.datetime.combine(f, datetime.time(*b_))
            if b <= a: b += datetime.timedelta(days=1)
            if a - antes <= ahora <= b + despues: return None
        msg = {'D': 'Hoy es tu día de descanso. El comandero no está disponible.',
               'F': 'Hoy estás marcado con falta. Si viniste a trabajar, avísale a la encargada.',
               'V': 'Hoy estás de vacaciones. El comandero no está disponible.',
               'I': 'Hoy estás marcado con incapacidad. El comandero no está disponible.'}
        if c in msg: return msg[c]
        if not (ent and sal): return 'Estás fuera de tu horario. El comandero no está disponible.'
        return ('Estás fuera de tu horario (%d:%02d a %d:%02d). El comandero abre %d minutos antes de tu entrada.' %
                (ent[0], ent[1], sal[0], sal[1], int(self.cfg.get('candado_antes_min', 30))))

    def horario_mesero(self, cod):
        try: e = next((e for e in self.rh()['empleados'] if e.get('activo', True) and str(e.get('cod_mt') or '').strip() == str(cod).strip()), None)
        except Exception: e = None
        if not e: return ''
        t = horario_texto(rh_base(e))
        return t + (' (o lo que marque el rol de la semana)' if t else '')

    def menu_comandero(self):
        """Menú táctil de MrTienda (TOUCHDPT = secciones, TOUCHART = botones) con precios de Comedor (01) y Recoger aquí (02)."""
        prod = {x['COD_PROD']: x for x in self.rows(self._cat('PRODUCTO.DBF'))}
        precio = {}
        for x in self.rows(self._cat('PRECIOS.DBF')):
            v = x.get('ACTIVO') if x.get('ACTIVO') is not None else x.get('PRECIO')
            precio[(x['COD_PROD'], x['COD_ESCALA'])] = round(v or 0, 2)
        var, svar = self._variantes()
        lim = lambda s: re.sub(r'(?<=\b\w) (?=\w\b)', '', _dbf_txt(s or '')).replace('  ', ' ').strip()
        def opciones(cp):
            g, o = [], []
            for v in sorted(var.get(cp, []), key=lambda v: v['COD_VAR']):
                subs = sorted(svar.get((cp, v['COD_VAR']), []), key=lambda s: s['COD_SVAR'])
                if subs:
                    g.append({'n': lim(v['DES_VAR']).capitalize(), 'cv': v['COD_VAR'], 'ob': bool(v.get('OBLIGADO')),
                              'o': [{'n': lim(s['DES_VAR']).capitalize(), 'cs': s['COD_SVAR'], 'p': round(s.get('PRECIO') or 0, 2)} for s in subs]})
                else:
                    o.append({'n': lim(v['DES_VAR']).capitalize(), 'cv': v['COD_VAR'], 'p': round(v.get('PRECIO') or 0, 2)})
            return g, o
        def item(cp):
            p = prod.get(cp)
            if not p or (cp, '01') not in precio: return None
            g, o = opciones(cp)
            p1 = precio[(cp, '01')]
            return {'id': cp, 'n': _dbf_txt(p.get('DES_PROD') or '').strip(), 'p': p1, 'p2': precio.get((cp, '02'), p1), 'g': g, 'o': o,
                    'pt': (p.get('PUERTO') or '').strip()}
        def num(s):
            try: return float(s or 0)
            except ValueError: return 999
        arts = collections.defaultdict(list)
        for a in self.rows(self._cat('TOUCHART.DBF')):
            if a.get('COD_PROD'): arts[(a['COD_FAMILI'], a['COD_DEPTO'])].append(a)
        cats, usados = [], set()
        for dp in sorted(self.rows(self._cat('TOUCHDPT.DBF')), key=lambda d: num(d.get('LUGAR'))):
            L = []
            for a in sorted(arts.get((dp['COD_FAMILI'], dp['COD_DEPTO']), []), key=lambda a: num(a.get('LUGAR'))):
                if any(i['id'] == a['COD_PROD'] for i in L): continue
                it = item(a['COD_PROD'])
                if it: L.append(it); usados.add(a['COD_PROD'])
            if L: cats.append({'n': lim(dp.get('CONCEPTO')).title(), 'i': L})
        # Solo lo que está en el menú táctil de la caja: los productos que no tienen botón suelen ser viejos o repetidos.
        return {'cats': cats}

    def _domicilios(self, d):
        """Nombres (como quedan en MrTienda, 10 letras) de las cuentas que el comandero abrió como Servicio a domicilio ese día o el anterior."""
        try: mt = os.path.getmtime(PEDIDOS_FILE)
        except OSError: return set()
        c = getattr(self, '_dom_idx', None)
        if not c or c[0] != mt:
            idx = collections.defaultdict(set)
            for x in self.pedidos().values():
                if isinstance(x, dict) and x.get('canal') == 'domicilio' and x.get('nombre') and x.get('fecha'):
                    idx[str(x['fecha'])[:10]].add(_sin_acentos(re.sub(r'\s+', ' ', str(x['nombre'])).strip().upper()[:10]).strip())
            c = self._dom_idx = (mt, idx)
        return c[1].get(d.isoformat(), set()) | c[1].get((d - datetime.timedelta(days=1)).isoformat(), set())

    @staticmethod
    def _canal_ticket(t, dom):
        m = _sin_acentos(re.sub(r'\s+', ' ', str(t.get('mesa') or '')).strip().upper())
        if re.match(r'^D ?-? ?\d{4,}[ET]?$', m) or m.startswith('DIDI'):
            if m.endswith('E'): return 'didi_e'
            if m.endswith('T'): return 'didi_t'
            return 'didi_e' if any(f.startswith('Efectivo') for f, _ in t.get('pagos', [])) else 'didi_t'
        if m.startswith('UBER'): return 'uber'
        if t.get('adom') or (len(m) >= 3 and any(r == m or (len(m) >= 8 and r.startswith(m)) for r in dom)): return 'domicilio'
        return 'llevar' if t.get('esc') == '02' else 'comedor'

    def pedidos(self):
        try:
            with open(PEDIDOS_FILE, encoding='utf-8') as f: d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError): return {}

    def factura_resp(self, archivo, resp):
        if resp not in ('si', 'no'): raise ValueError('Respuesta no válida.')
        x = dict(self.pedidos().get(archivo) or {}); x['factura'] = resp; x.setdefault('fecha', datetime.datetime.now().isoformat(timespec='seconds'))
        self._guardar_pedido(archivo, x); return {'ok': True}

    def _guardar_pedido(self, archivo, info):
        d = self.pedidos(); d[archivo] = info
        if len(d) > 800: d = dict(sorted(d.items(), key=lambda kv: kv[1].get('fecha', ''))[-600:])
        tmp = PEDIDOS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PEDIDOS_FILE)

    def clientes_por_tel(self, tel):
        """Busca en el catálogo de clientes de MrTienda por teléfono (solo lectura)."""
        dig = re.sub(r'\D', '', str(tel or ''))
        if len(dig) < 4: raise ValueError('Escribe al menos 4 números del teléfono.')
        ruta = next((self._cat(n) for n in ('CLIENTES.DBF', 'CLIENTE.DBF', 'CTES.DBF') if os.path.isfile(self._cat(n))), None)
        if not ruta: log('Comandero: no encontré el catálogo de clientes de MrTienda.'); return {'ok': True, 'clientes': [], 'sin_catalogo': True}
        out = []
        for x in self.rows(ruta):
            if not getattr(self, '_campos_cte_log', False):
                self._campos_cte_log = True; log('Comandero: campos de clientes de MrTienda: ' + ', '.join(list(x.keys())[:60]))
            tels = [re.sub(r'\D', '', str(v or '')) for k, v in x.items() if re.search(r'TEL|CEL|MOVIL|FON', k, re.I)]
            if not any(t and (t.endswith(dig) or (len(dig) >= 7 and dig in t)) for t in tels): continue
            nom = next((str(x.get(k) or '').strip() for k in ('DES_CTE', 'NOMBRE', 'NOM_CTE', 'RAZON', 'RAZON_SOC', 'NOMBRE_CTE') if str(x.get(k) or '').strip()), '')
            if not nom: nom = next((str(v).strip() for k, v in x.items() if re.search(r'NOM|DES', k, re.I) and str(v or '').strip()), '')
            if nom: out.append({'nombre': nom.title(), 'tel': next((t for t in tels if t), '')})
            if len(out) >= 8: break
        return {'ok': True, 'clientes': out}

    # ------------------------------------------------ tiempos de comanda (versión 30.6.23)
    # El teléfono del mesero avisa cuando su alarma llega a 14 min (roja) o 20 min (crítico) y cuando toca "Ya salió".
    # La caja calcula los minutos con la hora de envío que guardó MrTienda, guarda los platillos de esa comanda y el turno.
    TIEMPOS_F = os.path.join(AQUI, 'comandas_tiempos.json')
    def tiempos(self):
        try: return json.load(open(self.TIEMPOS_F, encoding='utf-8'))
        except (OSError, ValueError): return {}
    def turno_de(self, ts):
        """Turno del día: 1 + cortes de MrTienda cerrados ese mismo día antes de esa hora (de las 4 am en adelante)."""
        d = datetime.datetime.fromtimestamp(ts); ini = d.replace(hour=4, minute=0, second=0, microsecond=0)
        if d < ini: ini -= datetime.timedelta(days=1)
        try: c = self.horas_de_corte()
        except Exception: c = []
        return 1 + sum(1 for t in c if ini.timestamp() <= t < ts)
    def comanda_tiempo(self, d, mesero):
        ev = d.get('ev'); arch = str(d.get('archivo') or ''); h = str(d.get('h') or '')[:5]
        if ev not in ('alarma', 'salio') or not re.match(r'^\d{1,2}:\d{2}$', h) or not arch: return
        a = next((a for a in self.abiertas() if a['archivo'] == arch), None)
        if not a: return
        its = [i for i in a['items'] if i['h'] == h]
        if not its: return
        f = its[0].get('f') or a['fecha'] or datetime.date.today().isoformat()
        try: t0 = datetime.datetime.strptime(f + ' ' + h, '%Y-%m-%d %H:%M')
        except ValueError: return
        ahora = datetime.datetime.now(); mn = int((ahora - t0).total_seconds() // 60)
        if mn < 0 or mn > 300: return
        with LOCK_TIEMPOS:
            T = self.tiempos(); k = arch + '|' + f + '|' + h; r = T.get(k)
            if not r:
                if mn < 14: return          # salió a tiempo: no se registra
                canal = (self.pedidos().get(arch) or {})
                r = T[k] = {'fecha': f, 'enviada': h, 'ref': a['ref'], 'canal': canal.get('canal', ''), 'nombre': canal.get('nombre', ''),
                            'mesero': a['mesero'], 'turno': self.turno_de(t0.timestamp()), 'visto': 0, 'min': None,
                            'platillos': [{'n': _dbf_txt(i['n']), 'q': i['q'], 'det': _dbf_txt(i.get('det', ''))} for i in its]}
            if ev == 'alarma': r['visto'] = max(r.get('visto') or 0, mn)
            elif r.get('min') is None: r['min'] = mn; r['salio'] = ahora.strftime('%H:%M'); r['confirmo'] = (mesero or {}).get('nombre', '')
            lim = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
            T = {x: y for x, y in T.items() if y.get('fecha', '') >= lim}
            tmp = self.TIEMPOS_F + '.tmp'
            json.dump(T, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False); os.replace(tmp, self.TIEMPOS_F)
    def tiempos_lista(self):
        return sorted(({'k': k, **v} for k, v in self.tiempos().items()), key=lambda r: (r['fecha'], r['enviada']))

    # ------------------------------------------------ clientes para factura (versión 30.7.12)
    # Buscar en el catálogo de MrTienda (DATABASE\CLIENTES.DBF) y asignar el cliente a una cuenta abierta (COD_CTE, DES_CTE y SOLICITA,
    # igual que lo hizo MrTienda en la prueba del 14-oct-2026). Completar datos fiscales que no son parte de ningún índice
    # (CP, régimen, uso CFDI, correo) es seguro: no toca CLIENTES.CDX. El alta de clientes nuevos todavía la hace la caja.
    CTE_FISCALES = ('CTE_CP', 'I_RGF', 'USOCFDI', 'CTE_MAIL')
    @staticmethod
    def _cte(x):
        t = lambda k: _dbf_txt(str(x.get(k) or '').strip())
        dom = ' '.join(v for v in (t('CTE_CALLE'), t('CTE_NOEXT'), ('int. ' + t('CTE_NOINT')) if t('CTE_NOINT') else '', t('CTE_COLON'), t('CTE_MUNIC')) if v) or ' '.join(v for v in (t('CTE_DIREC1'), t('CTE_DIREC2')) if v)
        return {'cod': t('COD_CTE'), 'nombre': t('CTE_NOMBRE'), 'razon': t('RAZON_SOC'), 'rfc': t('CTE_RFC'), 'cp': t('CTE_CP'), 'regimen': t('I_RGF'),
                'uso': t('USOCFDI'), 'correo': t('CTE_MAIL'), 'correos': [v for v in (t('CTE_MAIL'), t('CTE_MAIL2'), t('CTE_MAIL3')) if v], 'tel': t('CTE_TEL1') or t('CTE_TEL2'), 'contacto': t('CTE_CONTAC'), 'domicilio': dom, 'referencia': t('CTE_REFLOC')}
    def clientes_buscar(self, q):
        ruta = self._cat('CLIENTES.DBF')
        if not os.path.isfile(ruta): raise RuntimeError('No encontré el catálogo de clientes de MrTienda.')
        sa = lambda v: re.sub(r'\s+', ' ', _sin_acento(_dbf_txt(str(v or ''))).upper()).strip()
        Q = sa(q); dig = re.sub(r'\D', '', Q); alnum = re.sub(r'[^A-Z0-9&Ñ]', '', Q)
        if len(Q) < 3: raise ValueError('Escribe al menos 3 letras o números.')
        out = []
        for x in self.rows(ruta):
            if x.get('INACTIVO') or not str(x.get('COD_CTE') or '').strip(): continue
            rfc = sa(x.get('CTE_RFC')).replace(' ', '').replace('-', '')
            nom, raz = sa(x.get('CTE_NOMBRE')), sa(x.get('RAZON_SOC'))
            tels = [re.sub(r'\D', '', str(x.get(k) or '')) for k in ('CTE_TEL1', 'CTE_TEL2')]
            pts = 0
            if len(alnum) >= 4 and rfc and rfc.startswith(alnum): pts = 3 if rfc == alnum else 2
            elif len(dig) >= 7 and len(dig) == len(Q.replace(' ', '')) and any(t and (t.endswith(dig) or dig in t) for t in tels): pts = 2
            elif all(w in nom or w in raz for w in Q.split()): pts = 1 + (nom.startswith(Q) or raz.startswith(Q))
            if pts: out.append((pts, x))
        out.sort(key=lambda z: (-z[0], sa(z[1].get('CTE_NOMBRE'))))
        return {'ok': True, 'clientes': [self._cte(x) for _, x in out[:15]], 'mas': len(out) > 15}
    def cliente_aplicar(self, mesero, d):
        archivo = re.sub(r'[^A-Za-z0-9]', '', str(d.get('k') or ''))[:11]; cod = re.sub(r'\D', '', str(d.get('cod') or ''))[:6]
        if not archivo or len(cod) != 6: raise ValueError('Falta la cuenta o el cliente.')
        com = os.path.join(self.base, 'COMUNES'); pend = os.path.join(com, 'PENDIENT.DBF'); ruta = self._cat('CLIENTES.DBF')
        comp = {k: str(v).strip().upper() if k != 'CTE_MAIL' else str(v).strip() for k, v in (d.get('completar') or {}).items() if k in self.CTE_FISCALES and str(v or '').strip()}
        if 'CTE_CP' in comp and not re.fullmatch(r'\d{5}', comp['CTE_CP']): raise ValueError('El código postal debe ser de 5 números.')
        if 'I_RGF' in comp and not re.fullmatch(r'\d{3}', comp['I_RGF']): raise ValueError('Régimen fiscal no válido.')
        if 'USOCFDI' in comp and not re.fullmatch(r'[A-Z]{1,2}\d{2}', comp['USOCFDI']): raise ValueError('Uso de CFDI no válido.')
        comp.pop('CTE_MAIL', None)   # los correos se agregan sin borrar los que ya tiene (MrTienda guarda 3: CTE_MAIL, CTE_MAIL2, CTE_MAIL3)
        correo_n = str(d.get('correo_nuevo') or '').strip()
        if correo_n and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', correo_n): raise ValueError('Revisa el correo.')
        with self.write_lock:
            with open(ruta, 'rb') as f: cb = bytearray(f.read())
            cn, chl, crl, cfl = _layout(cb)
            slot = next((k for k in range(cn) if cb[chl + k*crl:chl + k*crl + 1] != b'*' and _campo(cb[chl + k*crl:chl + (k+1)*crl], cfl, 'COD_CTE').decode('latin-1').strip() == cod), None)
            if slot is None: raise RuntimeError('Ese cliente ya no está en MrTienda.')
            crec = bytearray(cb[chl + slot*crl:chl + (slot+1)*crl])
            nombre = _campo(crec, cfl, 'CTE_NOMBRE').decode('latin-1').strip(); contacto = _campo(crec, cfl, 'CTE_CONTAC').decode('latin-1').strip()
            if correo_n:
                ranuras = [k for k in ('CTE_MAIL', 'CTE_MAIL2', 'CTE_MAIL3') if k in cfl]
                tiene = {k: _campo(crec, cfl, k).decode('latin-1').strip() for k in ranuras}
                if correo_n.lower() not in [v.lower() for v in tiene.values()]:
                    libre = next((k for k in ranuras if not tiene[k]), None)
                    if not libre:
                        libre = str(d.get('reemplazar') or '')
                        if libre not in ranuras: raise ValueError('Ese cliente ya tiene 3 correos. Elige cuál reemplazar.')
                    comp[libre] = correo_n
            # 1) completar datos fiscales del cliente (campos sin índice); respaldo del renglón original
            if comp:
                d_ = _respaldar([], 'CTE' + cod)
                with open(os.path.join(d_, 'CLIENTE_%s_renglon_%d.bin' % (cod, slot)), 'wb') as f: f.write(bytes(crec))
                for k, v in comp.items(): _pon(crec, cfl, k, v)
                _escribe_en(ruta, chl + slot*crl, bytes(crec))
                log(f'COMANDERO: {mesero["nombre"]} completó datos fiscales del cliente {cod} ({_dbf_txt(nombre)}): {", ".join(comp)}')
            # 2) asignar el cliente a la cuenta abierta (PENDIENT y .ENC), como MrTienda
            self._poner_cuenta(archivo, cod, nombre, contacto, mesero)
        log(f'COMANDERO: {mesero["nombre"]} puso la cuenta {archivo} a nombre del cliente {cod} ({_dbf_txt(nombre)}) para factura')
        return {'ok': True, 'cod': cod, 'nombre': _dbf_txt(nombre)}
    def _poner_cuenta(self, archivo, cod, nombre, contacto, mesero=None):
        """Pone COD_CTE, DES_CTE y SOLICITA en PENDIENT.DBF y en el .ENC de la cuenta abierta (igual que MrTienda). Llamar con write_lock.
        mesero=None: lo hace el sistema (30.7.74, cliente nuevo dado de alta por la caja), sin revisar de qué mesero es la cuenta."""
        com = os.path.join(self.base, 'COMUNES'); pend = os.path.join(com, 'PENDIENT.DBF')
        if True:
            with open(pend, 'rb') as f: pb = bytearray(f.read())
            pn, phl, prl, pfl = _layout(pb)
            k_ = next((k for k in range(pn) if pb[phl + k*prl:phl + k*prl + 1] != b'*' and _campo(pb[phl + k*prl:phl + (k+1)*prl], pfl, 'FILE').decode('latin-1').strip().upper() == archivo.upper()), None)
            if k_ is None: raise RuntimeError('Esa cuenta ya no está abierta (¿ya la cobraron?).')
            rec = bytearray(pb[phl + k_*prl:phl + (k_+1)*prl])
            if _campo(rec, pfl, 'PAGADO') in (b'T', b't'): raise RuntimeError('Esa cuenta ya está pagada.')
            if mesero is not None and _campo(rec, pfl, 'COD_VENDED').decode('latin-1').strip() != mesero['cod'] and not mesero.get('solo_huella'): raise RuntimeError('Esa cuenta es de otro mesero.')
            enc = os.path.join(com, archivo + '.ENC')
            _respaldar([pend, enc], archivo + '_CTE')
            vals = {'COD_CTE': cod, 'DES_CTE': nombre.encode('latin-1'), 'SOLICITA': contacto.encode('latin-1')}
            for k, v in vals.items(): _pon(rec, pfl, k, v)
            _escribe_en(pend, phl + k_*prl, bytes(rec))
            if os.path.isfile(enc):
                with open(enc, 'rb') as f: eb = bytearray(f.read())
                en, ehl, erl, efl = _layout(eb)
                if en >= 1:
                    er = bytearray(eb[ehl:ehl + erl])
                    for k, v in vals.items(): _pon(er, efl, k, v)
                    _escribe_en(enc, ehl, bytes(er))
            with self.rows_lock:
                self.cache = {k: v for k, v in self.cache.items() if not k[0].upper().startswith(com.upper()) and k[0] != self._cat('CLIENTES.DBF')}
    def probar_ia(self, llave):
        """30.7.52: prueba la llave con una pregunta mínima (cuesta menos de un centavo). Regresa (estado, mensaje)."""
        cuerpo = {'model': self.cfg.get('ia_modelo') or IA_MODELO, 'max_tokens': 1, 'messages': [{'role': 'user', 'content': 'Hola'}]}
        req = urllib.request.Request(IA_URL, data=json.dumps(cuerpo).encode('utf-8'), method='POST',
                                     headers={'x-api-key': llave, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r: r.read()
            return 'ok', 'La llave funciona. La lectura de fotos está activa.'
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'ignore')[:400]
            log('Prueba de la llave de Claude: ' + str(e.code) + ' ' + txt)
            if e.code in (401, 403): return 'mala', 'Claude dice que esa llave no es válida. Revisa que la copiaste completa o crea otra.'
            if 'credit' in txt.lower() or 'billing' in txt.lower() or e.code == 402: return 'saldo', 'La llave es correcta, pero la cuenta no tiene saldo. Compra créditos en platform.claude.com → Billing.'
            if e.code == 429: return 'limite', 'La llave es correcta, pero llegó a su límite por ahora. Intenta en unos minutos.'
            return 'error', 'Claude respondió con error ' + str(e.code) + '. Intenta otra vez en unos minutos.'
        except Exception as e:
            return 'error', 'No pude comunicarme con Claude (' + str(e)[:80] + '). Revisa el internet de la caja.'

    def leer_constancia(self, b64):
        llave = self.cfg.get('ia_llave')
        if not llave: raise RuntimeError('La lectura automática no está activada (Reportes → Gastos). Escribe los datos a mano.')
        if ',' in b64[:80]: b64 = b64.split(',', 1)[1]
        cuerpo = {'model': self.cfg.get('ia_modelo') or IA_MODELO, 'max_tokens': 1000,
                  'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64}},
                                                            {'type': 'text', 'text': PROMPT_CONSTANCIA}]}]}
        req = urllib.request.Request(IA_URL, data=json.dumps(cuerpo).encode('utf-8'), method='POST',
                                     headers={'x-api-key': llave, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=90) as r: resp = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            log('La lectura de la constancia falló: ' + str(e.code) + ' ' + e.read().decode('utf-8', 'ignore')[:300])
            raise RuntimeError('No se pudo leer la foto (' + ('llave no válida' if e.code in (401, 403) else 'sin saldo o límite' if e.code in (402, 429) else 'error ' + str(e.code)) + '). Escribe los datos a mano.')
        txt = ''.join(b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text')
        a, z = txt.find('{'), txt.rfind('}')
        if a < 0 or z < a: raise RuntimeError('No se entendió la foto. Intenta otra más clara o escribe los datos.')
        j = json.loads(txt[a:z + 1])
        out = {k: str(j.get(k) or '').strip()[:120] for k in ('rfc', 'razon_social', 'cp', 'regimen', 'uso', 'calle', 'no_ext', 'no_int', 'colonia', 'localidad', 'municipio', 'estado', 'correo', 'telefono')}
        out['rfc'] = re.sub(r'[^A-Z0-9&Ñ]', '', out['rfc'].upper())[:13]; out['cp'] = re.sub(r'\D', '', out['cp'])[:5]; out['regimen'] = re.sub(r'\D', '', out['regimen'])[:3]
        out['uso'] = out['uso'].upper().replace(' ', '') if re.fullmatch(r'[A-Z]{1,2}\d{2}', out['uso'].upper().replace(' ', '')) else ''
        try: out['existentes'] = self.clientes_buscar(out['rfc'])['clientes'] if len(out['rfc']) >= 12 else []
        except Exception: out['existentes'] = []
        return {'ok': True, 'datos': out}
    def leer_recibo_plat(self, b64):
        llave = self.cfg.get('ia_llave')
        if not llave: raise RuntimeError('La lectura automática no está activada (Reportes → Gastos). Escribe los datos a mano.')
        if ',' in b64[:80]: b64 = b64.split(',', 1)[1]
        cuerpo = {'model': self.cfg.get('ia_modelo') or IA_MODELO, 'max_tokens': 600,
                  'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64}},
                                                            {'type': 'text', 'text': PROMPT_RECIBO_PLAT}]}]}
        req = urllib.request.Request(IA_URL, data=json.dumps(cuerpo).encode('utf-8'), method='POST',
                                     headers={'x-api-key': llave, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=90) as r: resp = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            log('La lectura del recibo de plataforma falló: ' + str(e.code) + ' ' + e.read().decode('utf-8', 'ignore')[:300])
            raise RuntimeError('No se pudo leer la foto (' + ('llave no válida' if e.code in (401, 403) else 'sin saldo o límite' if e.code in (402, 429) else 'error ' + str(e.code)) + '). Escribe los datos a mano.')
        txt = ''.join(b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text')
        a, z = txt.find('{'), txt.rfind('}')
        if a < 0 or z < a: raise RuntimeError('No se entendió la foto. Intenta otra más clara o escribe los datos.')
        j = json.loads(txt[a:z + 1]); num = lambda k: max(0.0, round(float(str(j.get(k) or 0).replace('$', '').replace(',', '') or 0), 2))
        try: out = {'plataforma': 'uber' if 'uber' in str(j.get('plataforma', '')).lower() else 'didi', 'pedido': re.sub(r'[^0-9A-Za-z]', '', str(j.get('pedido') or ''))[:12].upper(),
                    'subtotal': num('subtotal'), 'desc': round(num('descuento_entrega') + num('otros_restaurante'), 2), 'efectivo': num('efectivo'), 'total_app': num('total_app')}
        except ValueError: raise RuntimeError('No se entendieron los números de la foto. Escríbelos a mano.')
        return {'ok': True, 'datos': out}

    CLIENTES_NUEVOS_F = os.path.join(AQUI, 'clientes_nuevos.json')
    def clientes_nuevos(self):
        try: return json.load(open(self.CLIENTES_NUEVOS_F, encoding='utf-8'))
        except (OSError, ValueError): return []
    # ------------------------------------------------ alta de cliente nuevo directo en Mr. Tienda (30.7.75)
    def _cte_registro(self, fl, rl, D, cod, ahora):
        rec = bytearray(b' ' * rl)
        u = lambda k: str(D.get(k) or '').strip()
        dom = ' '.join([u('calle'), u('no_ext'), u('no_int'), u('colonia'), u('localidad'), u('municipio'), u('cp'), u('estado')]).upper()
        vals = {'COD_CTE': cod, 'CTE_NOMBRE': u('nombre') or u('razon_social'), 'CTE_CONTAC': u('contacto'), 'CTE_DIREC1': dom[:45], 'CTE_DIREC2': dom[45:90],
                'CTE_RFC': u('rfc'), 'CTE_TEL1': u('telefono'), 'CTE_MAIL': u('correo'), 'COD_ESCALA': '02', 'RAZON_SOC': u('razon_social'),
                'CTE_CALLE': u('calle'), 'CTE_NOEXT': u('no_ext'), 'CTE_NOINT': u('no_int'), 'CTE_COLON': u('colonia'), 'CTE_LOCAL': u('localidad'),
                'CTE_REFLOC': u('referencia'), 'CTE_MUNIC': u('municipio'), 'CTE_ESTDO': u('estado'), 'CTE_PAIS': 'MEXICO', 'CTE_CP': u('cp'),
                'HORA_ALTA': ahora.strftime('%H:%M'), 'USOCFDI': u('uso').upper() or 'G03', 'CMP_FORMA': '001', 'I_RGF': u('regimen')}
        for k, v in vals.items():
            if k in fl and v: _pon(rec, fl, k, _cp850(v, fl[k][1]))
        for k, v in (('CTE_CREDIT', 0), ('CTE_LIMITE', 0), ('DIAS_APAR', 0), ('F_RTIVA', 0)):
            if k in fl: _pon(rec, fl, k, v)
        for k in ('FACTURAR', 'ACCESO_KEY', 'INACTIVO', 'RETENCION', 'RETEN_ISR', 'DESG_IEPS', 'CTE_QIVA', 'ASUST_TASA', 'V_HUELLA', 'M_HUELLA', 'RI_CEDULAR'):
            if k in fl: _pon(rec, fl, k, False)
        if 'EXC_PUNTOS' in fl: _pon(rec, fl, 'EXC_PUNTOS', True)
        if 'FECH_ALTA' in fl: _pon(rec, fl, 'FECH_ALTA', ahora.strftime('%Y%m%d').encode())
        return rec
    def alta_mrtienda(self, D, quien=''):
        """Da de alta un cliente en Mr. Tienda igual que la pantalla Clientes → Nuevo: CLIENTES.DBF + CLIENTES.CDX (9 etiquetas),
        CTES_BQ.DBF + CTES_BQ.CDX y CONFIG.FOL_CTES + 1. Revisa todo en memoria antes de escribir y guarda respaldo. Regresa (cod, nombre)."""
        if not self.cfg.get('alta_clientes_auto', True): raise CdxError('El alta automática está apagada.')
        db = lambda n: self._cat(n)
        rutas = {k: db(k) for k in ('CLIENTES.DBF', 'CLIENTES.CDX', 'CTES_BQ.DBF', 'CTES_BQ.CDX', 'CONFIG.DBF')}
        for k, r in rutas.items():
            if not os.path.isfile(r): raise CdxError('No encontré ' + k + ' de Mr. Tienda.')
        if os.path.basename(os.path.dirname(rutas['CLIENTES.DBF'])).upper() != 'DATABASE': raise CdxError('Solo se da de alta en la caja (carpeta DATABASE).')
        with self.write_lock:
            firma = {k: (os.stat(r).st_size, os.stat(r).st_mtime_ns) for k, r in rutas.items()}
            leer = lambda k: open(rutas[k], 'rb').read()
            cfgb = bytearray(leer('CONFIG.DBF')); qn, qhl, qrl, qfl = _layout(cfgb)
            if qn < 1 or 'FOL_CTES' not in qfl: raise CdxError('CONFIG de Mr. Tienda sin folio de clientes.')
            crec = bytearray(cfgb[qhl:qhl+qrl])
            try: folio = int(_campo(crec, qfl, 'FOL_CTES').strip() or 0)
            except ValueError: raise CdxError('Folio de clientes ilegible.')
            if folio <= 0 or folio >= 999999: raise CdxError('Folio de clientes fuera de rango.')
            cod = '%06d' % folio
            # CLIENTES.DBF: solo el encabezado (el archivo pesa ~27 MB y solo se le agrega un renglón)
            with open(rutas['CLIENTES.DBF'], 'rb') as f: h0 = f.read(32)
            n, hl, rl = struct.unpack('<IHH', h0[4:12])
            with open(rutas['CLIENTES.DBF'], 'rb') as f: hdr = bytearray(f.read(hl)); f.seek(hl + n*rl); cola = f.read()   # cola: la marca de fin (0x1A) si la tiene
            _, _, _, fl = _layout(hdr + bytes(rl * 0))
            tam = firma['CLIENTES.DBF'][0]
            if tam not in (hl + n*rl, hl + n*rl + 1) or cola not in (b'', b'\x1a'): raise CdxError('CLIENTES.DBF no tiene el tamaño esperado; usa Reindexar en Mr. Tienda.')
            bqb = bytearray(leer('CTES_BQ.DBF')); bn, bhl, brl, bfl = _layout(bqb)
            if len(bqb) not in (bhl + bn*brl, bhl + bn*brl + 1): raise CdxError('CTES_BQ.DBF no tiene el tamaño esperado.')
            cx = _Cdx(leer('CLIENTES.CDX')); bx = _Cdx(leer('CTES_BQ.CDX'))
            for x, esperadas, total in ((cx, LLAVES_CLIENTES, n), (bx, LLAVES_CTES_BQ, bn)):
                if sorted(t.expr for t in x.tags.values()) != sorted(esperadas): raise CdxError('Los índices de Mr. Tienda cambiaron; no los toco.')
                for t in x.tags.values():
                    if t.expr == 'CLIENTES->ID_CTE_WOO': t.fill = b'\0'
                    ks = x.revisar(t)
                    if len(ks) != total: raise CdxError(f'El índice {t.name} no cuadra con la tabla; usa Reindexar en Mr. Tienda.')
                    if t.expr.endswith('COD_CTE') and any(k == cod.encode() for k, r in ks): raise CdxError(f'El código {cod} ya existe; revisa el folio en Mr. Tienda.')
                    if t.expr == 'CLIENTES->CTE_RFC' and any(k.rstrip() == str(D.get('rfc') or '').upper().encode('cp850', 'replace') for k, r in ks): raise CdxError('Ese RFC ya está en Mr. Tienda.')
            ahora = datetime.datetime.now()
            nuevo = self._cte_registro(fl, rl, D, cod, ahora)
            bq = bytearray(b' ' * brl)
            for k in bfl:
                if k in fl: _pon(bq, bfl, k, _campo(nuevo, fl, k)[:bfl[k][1]])
            f1 = lambda k: _campo(nuevo, fl, k); f2 = lambda k: _campo(bq, bfl, k)
            for x, esperadas, campo, recno in ((cx, LLAVES_CLIENTES, f1, n + 1), (bx, LLAVES_CTES_BQ, f2, bn + 1)):
                for t in x.tags.values():
                    fn_ = esperadas[t.expr]
                    key = (b'\x80' + b'\0' * (t.kl - 1)) if fn_ is None else fn_(campo)
                    if len(key) != t.kl: raise CdxError(f'Llave de {t.name} con largo raro.')
                    x.insertar(t, key, recno)
                    ks = x.revisar(t)
                    if len(ks) != recno or (key, recno) not in ks: raise CdxError(f'No quedó bien el índice {t.name}.')
                x.version_mas_1()
            fpos = qhl + qfl['FOL_CTES'][3]; fant = bytes(_campo(crec, qfl, 'FOL_CTES'))
            _pon(crec, qfl, 'FOL_CTES', folio + 1); fnew = bytes(_campo(crec, qfl, 'FOL_CTES'))
            bq_tam = len(bqb); bq_hdr = bytes(bqb[:bhl]); bq_cola = bytes(bqb[bhl + bn*brl:])
            # respaldo: todo lo que se va a tocar (de CLIENTES.DBF basta el encabezado y su tamaño, porque solo se agrega al final)
            d_ = _respaldar([rutas['CLIENTES.CDX'], rutas['CTES_BQ.DBF'], rutas['CTES_BQ.CDX'], rutas['CONFIG.DBF']], 'ALTA_' + cod)
            with open(os.path.join(d_, 'CLIENTES_encabezado.bin'), 'wb') as f: f.write(bytes(hdr))
            with open(os.path.join(d_, 'CLIENTES_tamano.txt'), 'w') as f: f.write(f'{tam}\nPara deshacer: copiar este encabezado al inicio de CLIENTES.DBF y cortar el archivo a {tam} bytes (o usar Reindexar tras borrar el cliente).')
            if {k: (os.stat(r).st_size, os.stat(r).st_mtime_ns) for k, r in rutas.items()} != firma: raise CdxError('Mr. Tienda estaba guardando al mismo tiempo; intenta otra vez.')
            with open(rutas['CONFIG.DBF'], 'rb') as f: f.seek(fpos); hoy_folio = f.read(len(fant))
            if hoy_folio != fant: raise CdxError('Mr. Tienda acaba de usar el folio de clientes; intenta otra vez.')
            hecho = []
            try:
                # 1) renglón nuevo al final de CLIENTES.DBF y CTES_BQ.DBF, luego el conteo del encabezado
                for ruta, h_, r_, cnt, reg in ((rutas['CLIENTES.DBF'], hl, rl, n, nuevo), (rutas['CTES_BQ.DBF'], bhl, brl, bn, bq)):
                    hecho.append(ruta)
                    _escribe_en(ruta, h_ + cnt*r_, bytes(reg) + b'\x1a')
                    _escribe_en(ruta, 4, struct.pack('<I', cnt + 1))
                    _escribe_en(ruta, 1, bytes([ahora.year % 100, ahora.month, ahora.day]))
                # 2) índices
                for ruta, x in ((rutas['CLIENTES.CDX'], cx), (rutas['CTES_BQ.CDX'], bx)):
                    hecho.append(ruta)
                    for pos, datos in x.cambios(): _escribe_en(ruta, pos, datos)
                # 3) folio: solo sus 6 bytes (CONFIG trae ~30 folios más que Mr. Tienda va moviendo)
                hecho.append(rutas['CONFIG.DBF'])
                _escribe_en(rutas['CONFIG.DBF'], fpos, fnew)
            except Exception:
                log('ALTA: falló a la mitad; regreso lo que alcancé a escribir. ' + traceback.format_exc())
                deshacer = {rutas['CLIENTES.DBF']: (bytes(hdr), hl + n*rl, cola, tam), rutas['CTES_BQ.DBF']: (bq_hdr, bhl + bn*brl, bq_cola, bq_tam)}
                for ruta in reversed(hecho):
                    try:
                        if ruta in deshacer:
                            h_, pos, c_, t_ = deshacer[ruta]
                            with open(ruta, 'r+b') as f: f.seek(0); f.write(h_); f.seek(pos); f.write(c_); f.truncate(t_)
                        elif ruta == rutas['CONFIG.DBF']: _escribe_en(ruta, fpos, fant)
                        else: shutil.copy2(os.path.join(d_, os.path.basename(ruta)), ruta)
                    except Exception: log('ALTA: NO PUDE REGRESAR ' + ruta + '. Usa Reindexar en Mr. Tienda. ' + traceback.format_exc())
                raise CdxError('No se pudo guardar en Mr. Tienda; quedó como estaba.')
            with self.rows_lock:
                self.cache = {k: v for k, v in self.cache.items() if k[0] not in rutas.values()}
        nombre = _campo(nuevo, fl, 'CTE_NOMBRE').decode('latin-1').strip()
        log(f'ALTA: {quien} dio de alta en Mr. Tienda al cliente {cod} ({_dbf_txt(nombre)}, RFC {D.get("rfc")})')
        return cod, nombre, _campo(nuevo, fl, 'CTE_CONTAC').decode('latin-1').strip()
    def cliente_nuevo(self, mesero, d):
        D = {k: re.sub(r'\s+', ' ', str(v or '')).strip()[:150] for k, v in (d.get('datos') or {}).items()
             if k in ('rfc', 'nombre', 'razon_social', 'cp', 'regimen', 'uso', 'correo', 'telefono', 'calle', 'no_ext', 'no_int', 'colonia', 'localidad', 'municipio', 'estado', 'referencia', 'contacto')}
        D['rfc'] = re.sub(r'[^A-Z0-9&Ñ]', '', D.get('rfc', '').upper())
        if len(D['rfc']) not in (12, 13): raise ValueError('El RFC debe tener 12 o 13 letras y números.')
        if not D.get('razon_social'): raise ValueError('Escribe la razón social o el nombre.')
        if not re.fullmatch(r'\d{5}', D.get('cp', '')): raise ValueError('El código postal debe ser de 5 números.')
        if not re.fullmatch(r'\d{3}', D.get('regimen', '')): raise ValueError('Elige el régimen fiscal.')
        D['nombre'] = D.get('nombre') or D['razon_social']
        cta = next((a for a in self.abiertas() if a['archivo'] == str(d.get('k') or '')), None)
        r = {'id': secrets.token_hex(4), 'fecha': datetime.datetime.now().isoformat(timespec='minutes'), 'mesero': mesero['nombre'],
             'cuenta': (cta or {}).get('ref', ''), 'archivo': (cta or {}).get('archivo', ''), 'datos': D}
        with self.write_lock:
            L = [x for x in self.clientes_nuevos() if x.get('datos', {}).get('rfc') != D['rfc']] + [r]
            tmp = self.CLIENTES_NUEVOS_F + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(L[-300:], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.CLIENTES_NUEVOS_F)
        log(f'COMANDERO: {mesero["nombre"]} capturó datos de factura de {D["rfc"]} para dar de alta en MrTienda')
        # 30.7.75: darlo de alta en Mr. Tienda y poner la cuenta a su nombre en el mismo paso; si algo no cuadra, queda para la caja como antes
        try:
            cod, nombre, contacto = self.alta_mrtienda(D, mesero['nombre'])
        except Exception as e:
            log('ALTA: no se pudo dar de alta automática (' + str(e) + '); queda en la lista para la caja.')
            return {'ok': True, 'alta': False, 'motivo': str(e)}
        r['alta'] = cod; asignada = False; aviso = ''
        if r['archivo']:
            try:
                with self.write_lock: self._poner_cuenta(r['archivo'], cod, nombre, contacto, mesero)
                asignada = True
            except Exception as e: aviso = str(e)
        with self.write_lock:
            L = self.clientes_nuevos()
            for x in L:
                if x.get('id') == r['id']:
                    if asignada: x['asignado'] = {'cod': cod, 'nombre': _dbf_txt(nombre), 'fecha': datetime.datetime.now().isoformat(timespec='minutes'), 'solo': True}
                    else: x['aviso'] = (aviso + ' ' if aviso else '') + f'Ya está en Mr. Tienda como {cod}; ponlo en la cuenta desde el comandero.'
                    x['alta'] = cod
            tmp = self.CLIENTES_NUEVOS_F + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(L[-300:], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.CLIENTES_NUEVOS_F)
        try: self.refresh()
        except Exception: pass
        return {'ok': True, 'alta': cod, 'nombre': _dbf_txt(nombre), 'cuenta': asignada, 'aviso': aviso}
    def auto_asignar_nuevos(self):
        """30.7.74: si un cliente nuevo que capturó el mesero ya aparece en Mr. Tienda (mismo RFC), poner su cuenta a su nombre sola.
        Completa CP, régimen, uso de CFDI y correo si la caja los dejó vacíos (campos sin índice). Solo vuelve a leer CLIENTES.DBF cuando cambia."""
        pend = [x for x in self.clientes_nuevos() if x.get('archivo') and not x.get('asignado') and not x.get('aviso')]
        if not pend: return
        ruta = self._cat('CLIENTES.DBF')
        if not os.path.isfile(ruta): return
        sa = lambda v: re.sub(r'[^A-Z0-9&Ñ]', '', _sin_acento(_dbf_txt(str(v or ''))).upper())
        por_rfc = collections.defaultdict(list)
        for x in self.rows(ruta):
            if x.get('INACTIVO') or not str(x.get('COD_CTE') or '').strip(): continue
            r = sa(x.get('CTE_RFC'))
            if r and r not in ('XAXX010101000', 'XEXX010101000'): por_rfc[r].append(x)
        cambio = False
        for it in pend:
            D = it.get('datos', {}); rfc = sa(D.get('rfc'))
            cands = por_rfc.get(rfc) or []
            if not cands: continue
            try: viejo = (datetime.datetime.now() - datetime.datetime.fromisoformat(it.get('fecha') or '')).total_seconds() > 12 * 3600
            except ValueError: viejo = True
            if viejo:   # Mr. Tienda vuelve a usar los nombres de archivo de las cuentas: no tocar cuentas de otro turno
                it['aviso'] = 'Pasaron más de 12 horas desde que se capturó; no la puse sola en la cuenta.'; cambio = True; continue
            rz = sa(D.get('razon_social'))
            x = next((c for c in cands if rz and rz in (sa(c.get('RAZON_SOC')), sa(c.get('CTE_NOMBRE')))), None) or max(cands, key=lambda c: str(c.get('COD_CTE') or ''))
            cod = str(x.get('COD_CTE') or '').strip()
            if len(cod) != 6: continue
            try:
                with self.write_lock:
                    with open(ruta, 'rb') as f: cb = bytearray(f.read())
                    cn, chl, crl, cfl = _layout(cb)
                    slot = next((k for k in range(cn) if cb[chl + k*crl:chl + k*crl + 1] != b'*' and _campo(cb[chl + k*crl:chl + (k+1)*crl], cfl, 'COD_CTE').decode('latin-1').strip() == cod), None)
                    if slot is None: continue
                    crec = bytearray(cb[chl + slot*crl:chl + (slot+1)*crl])
                    nombre = _campo(crec, cfl, 'CTE_NOMBRE').decode('latin-1').strip(); contacto = _campo(crec, cfl, 'CTE_CONTAC').decode('latin-1').strip()
                    # completar lo que la caja haya dejado vacío (mismo cuidado que cliente_aplicar: campos sin índice y respaldo del renglón)
                    comp = {}
                    for campo, v, ok in (('CTE_CP', str(D.get('cp') or ''), r'\d{5}'), ('I_RGF', str(D.get('regimen') or ''), r'\d{3}'), ('USOCFDI', str(D.get('uso') or '').upper(), r'[A-Z]{1,2}\d{2}'), ('CTE_MAIL', str(D.get('correo') or ''), r'[^@\s]+@[^@\s]+\.[^@\s]+')):
                        if campo in cfl and re.fullmatch(ok, v) and not _campo(crec, cfl, campo).decode('latin-1').strip(): comp[campo] = v
                    if comp:
                        d_ = _respaldar([], 'CTE' + cod)
                        with open(os.path.join(d_, 'CLIENTE_%s_renglon_%d.bin' % (cod, slot)), 'wb') as f: f.write(bytes(crec))
                        for k, v in comp.items(): _pon(crec, cfl, k, v)
                        _escribe_en(ruta, chl + slot*crl, bytes(crec))
                        log(f'FACTURA: completé en Mr. Tienda lo que le faltaba al cliente {cod} ({_dbf_txt(nombre)}): {", ".join(comp)}')
                    self._poner_cuenta(it['archivo'], cod, nombre, contacto)
                it['asignado'] = {'cod': cod, 'nombre': _dbf_txt(nombre), 'fecha': datetime.datetime.now().isoformat(timespec='minutes')}
                log(f'FACTURA: la cuenta {it.get("cuenta") or it["archivo"]} quedó sola a nombre de {cod} ({_dbf_txt(nombre)}) en cuanto la caja lo dio de alta')
            except RuntimeError as e:
                it['aviso'] = str(e) + ' Factura el ticket a su nombre en Mr. Tienda.'
                log(f'FACTURA: no pude poner la cuenta {it.get("archivo")} a nombre de {cod}: {e}')
            except Exception as e:
                log('FACTURA: error al poner la cuenta a nombre del cliente nuevo: ' + str(e)); continue
            cambio = True
        if cambio:
            with self.write_lock:
                act = {x['id']: x for x in pend}
                L2 = [act.get(x.get('id'), x) for x in self.clientes_nuevos()]
                tmp = self.CLIENTES_NUEVOS_F + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f: json.dump(L2, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.CLIENTES_NUEVOS_F)
            try: self.refresh()
            except Exception: pass
    def ciclo_clientes_nuevos(self):
        while True:
            time.sleep(15)
            try: self.auto_asignar_nuevos()
            except Exception: log('FACTURA: ' + traceback.format_exc())
    def clientes_nuevos_accion(self, d):
        with self.write_lock:
            L = [x for x in self.clientes_nuevos() if x.get('id') != d.get('id')]
            with open(self.CLIENTES_NUEVOS_F, 'w', encoding='utf-8') as f: json.dump(L, f, ensure_ascii=False, indent=1)
        self.refresh(); return {'ok': True}

    # ------------------------------------------------ mapa del comedor (editor con los dedos en el panel, 19-oct-2026)
    PLANO_F = os.path.join(AQUI, 'plano.json')
    PLANO_DEF = {'mesas': [
        {'g': '3', 'x': 15, 'y': 3, 'w': 17, 'h': 15, 'p': ['3']},
        {'g': '6', 'x': 40, 'y': 4, 'w': 27, 'h': 14, 'p': ['6A', '6B']},
        {'g': '7', 'x': 74, 'y': 3, 'w': 15, 'h': 16, 'p': ['7']},
        {'g': '2', 'x': 2, 'y': 13, 'w': 12, 'h': 27, 'p': ['2B', '2A'], 'v': 1},
        {'g': '1', 'x': 2, 'y': 52, 'w': 12, 'h': 27, 'p': ['1B', '1A'], 'v': 1},
        {'g': '4', 'x': 27, 'y': 34, 'w': 13, 'h': 29, 'r': -20, 'p': ['4A', '4B'], 'v': 1},
        {'g': '5', 'x': 46, 'y': 29, 'w': 12, 'h': 27, 'r': -20, 'p': ['5A', '5B'], 'v': 1},
        {'g': '8', 'x': 76, 'y': 25, 'w': 13, 'h': 29, 'p': ['8A', '8B'], 'v': 1},
        {'g': '9', 'x': 76, 'y': 60, 'w': 13, 'h': 27, 'p': ['9A', '9B'], 'v': 1}],
        'etiq': [{'t': 'Caja', 'x': 47, 'y': 89, 'w': 24, 'h': 10}, {'t': 'Entrada', 'x': 91, 'y': 25, 'w': 8, 'h': 30, 'v': 1}]}
    def plano_actual(self):
        try:
            with open(self.PLANO_F, encoding='utf-8') as f: return json.load(f)
        except (OSError, ValueError): return self.PLANO_DEF
    def plano_accion(self, d):
        if d.get('accion') == 'original':
            try: os.remove(self.PLANO_F)
            except OSError: pass
            log('Mapa del comedor: se regresó al original'); return {'ok': True, 'plano': self.PLANO_DEF}
        P = d.get('plano') or {}
        num = lambda v, lo, hi: max(lo, min(hi, round(float(v), 1)))
        mesas, vistos = [], set()
        for m in (P.get('mesas') or [])[:60]:
            g = str(m.get('g') or '').strip().upper()
            if not re.fullmatch(r'\d{1,2}', g): raise ValueError('Cada mesa necesita un número (1 a 99).')
            if g in vistos: raise ValueError('La mesa ' + g + ' está repetida.')
            vistos.add(g)
            p = [str(x).upper() for x in (m.get('p') or [])]
            p = [g] if len(p) != 2 else ([g + 'A', g + 'B'] if p[0].endswith('A') else [g + 'B', g + 'A'])
            x = {'g': g, 'x': num(m.get('x', 0), 0, 95), 'y': num(m.get('y', 0), 0, 95), 'w': num(m.get('w', 12), 4, 60), 'h': num(m.get('h', 12), 4, 60), 'p': p}
            if m.get('r'): x['r'] = num(m['r'], -180, 180)
            if m.get('v') and len(p) == 2: x['v'] = 1
            mesas.append(x)
        etiq = []
        for e in (P.get('etiq') or [])[:20]:
            t = re.sub(r'\s+', ' ', str(e.get('t') or '')).strip()[:20]
            if not t: continue
            x = {'t': t, 'x': num(e.get('x', 0), 0, 98), 'y': num(e.get('y', 0), 0, 98), 'w': num(e.get('w', 15), 4, 60), 'h': num(e.get('h', 8), 4, 60)}
            if e.get('v'): x['v'] = 1
            etiq.append(x)
        if not mesas: raise ValueError('El mapa necesita al menos una mesa.')
        out = {'mesas': mesas, 'etiq': etiq}
        tmp = self.PLANO_F + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.PLANO_F)
        log('Mapa del comedor guardado: ' + ', '.join(sum((m['p'] for m in mesas), [])))
        return {'ok': True, 'plano': out}

    def cuentas_comandero(self):
        out = []; ped = self.pedidos()
        for a in self.abiertas():
            out.append({'archivo': a['archivo'], 'ref': a['ref'], 'abrio': a['abrio'], 'fecha': a['fecha'], 'mesero': a['mesero'], 'cod': a['cod'],
                        'llevar': a['llevar'], 'pers': a['pers'], 'total': a['total'],
                        'canal': {k: v for k, v in (ped.get(a['archivo']) or {}).items() if k in ('canal', 'pedido', 'pago', 'nombre', 'desc', 'cobrado', 'completo')}, 'cortes': a.get('cortes', 0),
                        'cte': a.get('cte', ''), 'des_cte': a.get('des_cte', ''), 'factura': (ped.get(a['archivo']) or {}).get('factura', ''),
                        'items': [{'n': _dbf_txt(i['n']), 'q': i['q'], 'imp': i['imp'], 'det': _dbf_txt(i.get('det', '')), 'h': i['h'], 'llevar': i['llevar']} for i in a['items']]})
        return out

    # ------------------------------------------------ comandero: enviar pedidos a MrTienda (versión 2)
    # Replica lo que hizo MrTienda en la prueba del 9-oct-2026 (respaldos ACTUAL → CON_COMANDA_ABIERTA):
    #  cuenta nueva = renglón en COMUNES\PENDIENT.DBF + COMUNES\PDxxxxxx.DBF (detalle) con su .BAK y .ENC + FOL_CTA_P+1 en REGISTRO\FOLIOS.DBF.
    #  agregar = renglones nuevos al final de PDxxxxxx.DBF/.BAK y totales del renglón de PENDIENT/.ENC.
    # Antes de tocar nada respalda los archivos en respaldos_comandero\. Solo funciona si Jorge lo enciende en el panel.
    def _catalogo_envio(self):
        prod = {x['COD_PROD']: x for x in self.rows(self._cat('PRODUCTO.DBF'))}
        precio = {}
        for x in self.rows(self._cat('PRECIOS.DBF')):
            v = x.get('ACTIVO') if x.get('ACTIVO') is not None else x.get('PRECIO')
            precio[(x['COD_PROD'], x['COD_ESCALA'])] = round(v or 0, 2)
        ivas = {x['COD_IVA']: x.get('IVA') or 0 for x in self.rows(self._cat('IVA.DBF'))}
        deptos = {(x['COD_FAMILI'], x['COD_DEPTO']): x.get('COD_IVA') or '04' for x in self.rows(self._cat('DEPTOS.DBF'))}
        plu = {}
        for x in self.rows(self._cat('CODIGOS.DBF')):
            if (x.get('SIZE') or '00') in ('00', '') and (x.get('ATRIB') or '00') in ('00', '') and x['COD_PROD'] not in plu: plu[x['COD_PROD']] = x.get('COD_PLU') or ''
        var, svar = self._variantes()
        emp_raw = {x['COD_EMPLEA']: (x.get('DES_EMPLEA') or '').strip().upper() for x in self.rows(self._cat('PAREA.DBF'))}
        return prod, precio, ivas, deptos, plu, var, svar, emp_raw

    def _renglones(self, items, cat, ahora):
        """Convierte lo que mandó el teléfono en renglones de MrTienda. Precios y datos salen del catálogo, no del teléfono."""
        prod, precio, ivas, deptos, plu, var, svar, _ = cat
        out = []
        for it in items[:40]:
            cp = str(it.get('id') or '')
            p = prod.get(cp)
            if not p: raise ValueError('Un platillo ya no existe en MrTienda. Actualiza el comandero.')
            esc = '02' if it.get('llevar') else '01'
            base = precio.get((cp, esc), precio.get((cp, '01')))
            if base is None: raise ValueError((p.get('DES_PROD') or cp).strip() + ' no tiene precio en MrTienda.')
            try: q = int(it.get('qty') or 1)
            except (TypeError, ValueError): q = 1
            q = max(1, min(q, 50))
            grupos = {v['COD_VAR']: v for v in var.get(cp, []) if svar.get((cp, v['COD_VAR']))}
            simples = {v['COD_VAR']: v for v in var.get(cp, []) if v['COD_VAR'] not in grupos}
            sub, msub, nombres, extra = '', '', [], 0.0
            elegidos = {}
            for par in it.get('g') or []:
                if not (isinstance(par, (list, tuple)) and len(par) == 2): continue
                cv, cs = str(par[0]), str(par[1])
                s = next((s for s in svar.get((cp, cv), []) if s['COD_SVAR'] == cs), None)
                if cv not in grupos or not s: raise ValueError('Una opción de ' + (p.get('DES_PROD') or '').strip() + ' ya no existe. Actualiza el comandero.')
                elegidos[cv] = s
            faltan = [g for cv, g in grupos.items() if g.get('OBLIGADO') and cv not in elegidos]
            if faltan: raise ValueError('Falta elegir ' + faltan[0]['DES_VAR'].strip().lower() + ' en ' + (p.get('DES_PROD') or '').strip() + '.')
            for cv in sorted(elegidos):
                s = elegidos[cv]; sub += cv + s['COD_SVAR']; msub += cv + s['COD_SVAR'] + '00'; extra += s.get('PRECIO') or 0
                nombres.append(s['DES_VAR'].strip().capitalize())
            va = [' '] * 60
            if elegidos:   # así lo guardó MrTienda en la prueba (Huevos con jamón): posición 1 y una posición antes de cada grupo
                va[1] = '1'
                for cv in elegidos:
                    k = int(cv) - 1
                    if 0 < k < 60: va[k] = '1'
            for cv in sorted({str(x) for x in (it.get('o') or [])}):
                v = simples.get(cv)
                if not v: raise ValueError('Una opción de ' + (p.get('DES_PROD') or '').strip() + ' ya no existe. Actualiza el comandero.')
                k = int(cv)
                if 0 < k < 60: va[k] = '1'
                extra += v.get('PRECIO') or 0
                nombres.append(re.sub(r'(?<=\b\w) (?=\w\b)', '', v['DES_VAR']).strip().capitalize())
            if extra > 0.001: raise ValueError((p.get('DES_PROD') or '').strip() + ' lleva una opción con costo extra. Por ahora captúralo en la caja.')
            nota = re.sub(r'\s+', ' ', str(it.get('nota') or '')).strip().upper()[:44]
            cod_iva = deptos.get((p.get('COD_FAMILI'), p.get('COD_DEPTO')), '04')
            cont = p.get('CONTENIDO') or 1
            obl, lim = int(p.get('V_OBLIGDS') or 0), int(p.get('V_LIMITAR') or 0)
            out.append({'COD_ESCALA': esc, 'COD_PROD': cp, 'DES_PROD': (p.get('DES_PROD') or '').strip(), 'DES_CANTAR': (p.get('DES_CANTAR') or p.get('DES_PROD') or '').strip(),
                        'HORACAPTUR': ahora.strftime('%H:%M'), 'HORA': ahora.strftime('%H:%M'), 'FECHA': ahora.strftime('%Y%m%d'),
                        'COSTO': (p.get('COSTO') or 0) / (cont if cont > 0 else 1), 'PRECIO_O': base, 'PRECIO': base, 'CANTIDAD': q, 'IMPORTE': round(base * q, 5),
                        'ID_SAT_COD': p.get('ID_SAT_COD') or '', 'ID_SAT_UNI': p.get('ID_SAT_UNI') or '', 'UNIDAD': (p.get('PAQ_UNID') or p.get('UNIDAD') or '').strip(),
                        'IVA': ivas.get(cod_iva, 8.0), 'COD_IVA': cod_iva, 'COD_FAMILI': p.get('COD_FAMILI') or '', 'COD_DEPTO': p.get('COD_DEPTO') or '',
                        'VARIAASCII': ''.join(va).rstrip(), 'SUB_VARIA': sub, 'MSUB_VARIA': msub, 'VBLOQUE': '0' if elegidos else ' ',
                        'PUERTO': (p.get('PUERTO') or '').strip(), 'COD_PLU': (plu.get(cp) or '').strip(), 'RECETA': bool(p.get('RECETA')),
                        'V_OBLIGDS': obl, 'V_LIMITAR': lim, 'MNTR_EN_Z': bool(p.get('MNTR_EN_Z')), 'VAR_LIBRE': nota,
                        '_det': ' · '.join(nombres)})
        if not out: raise ValueError('No hay platillos para enviar.')
        return out

    _ce_lock = threading.Lock()   # 30.7.56: revisar "ya comió hoy" y anotar la comida sin que se crucen dos envíos

    def enviar_comandero(self, mesero, d):
        if not d.get('k') and str(d.get('canal') or '').lower() == 'empleado':
            with self._ce_lock: return self._enviar_comandero(mesero, d)
        return self._enviar_comandero(mesero, d)

    def _enviar_comandero(self, mesero, d):
        if not self.cfg.get('comandero_escribe'): raise RuntimeError('El envío a MrTienda está apagado. Jorge lo enciende en la pestaña Comandero del panel.')
        envio = str(d.get('envio') or '')[:40]
        hechos = getattr(self, '_envios', {})
        if envio and envio in hechos: return hechos[envio]
        ahora = datetime.datetime.now()
        canal = None
        if not d.get('k') and d.get('canal'):
            limpio = lambda v, n: re.sub(r'\s+', ' ', str(v or '')).strip()[:n]
            c_ = str(d['canal']).lower()
            if c_ not in CANALES: raise ValueError('Tipo de pedido no válido.')
            canal = {'canal': c_, 'fecha': ahora.isoformat(timespec='seconds'), 'mesero': mesero['cod']}
            if c_ == 'empleado':
                v_ = self.ce_validar(d, ahora)
                if not v_.get('ok'): raise ValueError(v_['error'])
                canal.update({'emp': v_['emp']['id'], 'nombre': v_['emp']['corto'], 'completo': v_['emp']['nombre'], 'segunda': v_['segunda'], 'autoriza': v_['autoriza'],
                              'ref': ('CE ' + _sin_acentos(v_['emp']['corto']).upper())[:10]})
            elif c_ in ('didi', 'uber'):
                ped = re.sub(r'[^0-9A-Za-z]', '', str(d.get('pedido') or ''))[:12].upper()   # clave completa (hasta 12); en MrTienda va solo el final
                if not ped: raise ValueError('Escribe la clave del pedido de ' + c_.title() + '.')
                dsc = self._monto(d.get('desc'), 'Descuentos')
                if dsc is not None: canal['desc'] = dsc
                cob = self._monto(d.get('cobrado'), 'Lo cobrado al cliente')
                if cob is not None: canal['cobrado'] = cob
                canal.update({'pedido': ped, 'pago': ('efectivo' if d.get('pago') == 'efectivo' else 'tarjeta') if c_ == 'didi' else 'tarjeta', 'ref': ('D' + ped[-5:] + ('E' if d.get('pago') == 'efectivo' else 'T')) if c_ == 'didi' else 'UBER ' + ped[-5:]})   # Didi: D + clave + E (efectivo) o T (tarjeta)
                if c_ == 'didi' and d.get('pago') not in ('efectivo', 'tarjeta'): raise ValueError('Marca si el pedido de Didi es en efectivo o con tarjeta.')
            else:
                nom = limpio(d.get('nombre'), 40)
                if not nom: raise ValueError('Escribe el nombre del cliente.')
                canal.update({'nombre': nom, 'ref': nom.upper()[:10]})
                if c_ == 'domicilio':
                    tel = re.sub(r'\D', '', str(d.get('tel') or ''))[:12]
                    if len(tel) < 7: raise ValueError('Escribe el teléfono del cliente (10 números).')
                    canal['tel'] = tel
        cat = self._catalogo_envio()
        ren = self._renglones(d.get('items') or [], cat, ahora)
        nombre = cat[7].get(mesero['cod']) or mesero['nombre'].upper()
        for r in ren: r['COD_VENDED'] = mesero['cod']
        com = os.path.join(self.base, 'COMUNES'); pend = os.path.join(com, 'PENDIENT.DBF')
        folios = os.path.join(self.base, 'REGISTRO', 'FOLIOS.DBF')
        with self.write_lock:
            if d.get('k'):
                archivo = re.sub(r'[^A-Za-z0-9]', '', str(d['k']))[:11]
                cod_cta = mesero['cod']
                if mesero.get('solo_huella'):   # 30.7.64: el administrador (Jorge, con huella) agrega a la cuenta de cualquier mesero; la cuenta sigue siendo de ese mesero
                    due = next((a for a in self.abiertas() if a['archivo'].upper() == archivo.upper()), None)
                    if due and due.get('cod'):
                        cod_cta = due['cod']
                        for r in ren: r['COD_VENDED'] = cod_cta
                        if cod_cta != mesero['cod']: d['_ajena'] = due.get('mesero') or cod_cta
                res = _agregar_a_cuenta(com, pend, archivo, cod_cta, ren, self._tc(), ahora)
            else:
                if d.get('mesa'):
                    # mesa del plano: 3, 7 o con lado 1A / 1B, o juntas 1AB (versión 30.7.9)
                    mm = re.match(r'^\s*(\d{1,2})\s*(A|B|AB)?\s*$', str(d['mesa']).upper())
                    if not mm: raise ValueError('Mesa no válida.')
                    num, lado = str(int(mm.group(1))), mm.group(2) or ''
                    quiero = set(lado) if lado else {'A', 'B'}
                    for a in self.abiertas():
                        n_ = re.match(r'^(?:M|MESA)\s*-?\s*(\d{1,2})\s*(A|B|AB)?$', a['ref'].strip(), re.I)
                        if not n_ or str(int(n_.group(1))) != num: continue
                        tiene = set((n_.group(2) or '').upper()) or {'A', 'B'}
                        if quiero & tiene: raise RuntimeError(f'La mesa {num}{lado} ya tiene cuenta abierta ({a["mesero"]}). Actualiza el comandero.')
                    ref = 'M ' + num + lado
                elif canal:
                    ref = canal['ref']
                else:
                    ref = re.sub(r'\s+', ' ', str(d.get('nombre') or '')).strip().upper()[:10]
                    if not ref: raise ValueError('Escribe para quién es el pedido.')
                res = _abrir_cuenta(com, pend, folios, ref, mesero['cod'], nombre, '02' if (d.get('llevar') or (canal and canal['canal'] != 'empleado')) else '01', ren, self._tc(), ahora,
                                   pers=int(d.get('pers') or 1) if str(d.get('pers') or '1').isdigit() else 1)
            with self.rows_lock:
                self.cache = {k: v for k, v in self.cache.items() if not k[0].upper().startswith(com.upper())}
        log(f'COMANDERO: {mesero["nombre"]} {"agregó a" if d.get("k") else "abrió"} {res["ref"]} ({res["archivo"]}){" (cuenta de " + str(d["_ajena"]) + ")" if d.get("_ajena") else ""} · {len(ren)} renglones · ${res["agregado"]:.2f}. Respaldo: {res["respaldo"]}')
        if canal:
            try: self._guardar_pedido(res['archivo'], {k: v for k, v in canal.items() if k != 'ref'})
            except Exception as e: log('No pude guardar el canal del pedido: ' + str(e))
            if canal['canal'] == 'empleado':
                try:
                    self._anotar_comida_emp({'id': secrets.token_hex(4), 'archivo': res['archivo'], 'emp': canal['emp'], 'nombre': canal['completo'], 'corto': canal['nombre'],
                                             'fecha': ahora.date().isoformat(), 'hora': ahora.strftime('%H:%M'), 'mesero': mesero['nombre'],
                                             'segunda': canal['segunda'], 'autoriza': canal['autoriza']})
                    log(f"COMIDA DE EMPLEADO: {canal['completo']} ({res['archivo']}), firmó con su clave{', 2a comida autorizada por ' + canal['autoriza'] if canal['autoriza'] else ''}")
                except Exception as e: log('No pude anotar la comida del empleado: ' + str(e))
        if d.get('factura') in ('si', 'no'):   # respuesta a "¿Va a necesitar factura?" al mandar la cuenta nueva (30.7.13)
            try: self.factura_resp(res['archivo'], d['factura'])
            except Exception as e: log('No pude guardar si lleva factura: ' + str(e))
        info = canal or (self.pedidos().get(res['archivo']) if d.get('k') else None)
        imp = self.imprimir_comanda(res['ref'], mesero['nombre'], ren, res['folio'], nueva=not d.get('k'), ahora=ahora, canal=info)
        out = {'ok': True, 'archivo': res['archivo'], 'ref': res['ref'], 'total': res['total'], 'impreso': imp.get('ok', False), 'aviso': imp.get('error', '')}
        hechos[envio] = out; self._envios = dict(list(hechos.items())[-50:])
        return out

    def _tc(self):
        for x in self.rows(os.path.join(self.base, 'REGISTRO', 'VALORES.DBF')):
            if x.get('TC'): return x['TC']
        return 0

    def imprimir_comanda(self, ref, mesero, ren, folio, nueva, ahora, canal=None):
        """Imprime en la impresora de cocina solo los platillos del puerto de cocina (1), como MrTienda."""
        cocina = [r for r in ren if r['PUERTO'][:1] == str(self.cfg.get('puerto_cocina', '1'))]
        if not cocina: return {'ok': True, 'nada': True}
        nombre = self.cfg.get('impresora_cocina') or 'EPSON TM-T88V ReceiptE4'
        t = comanda_bytes(ref, mesero, cocina, folio, nueva, ahora, canal, self.cfg.get('comanda_fmt'))
        try:
            _imprimir_raw(nombre, t)
            return {'ok': True}
        except Exception as e:
            log('No pude imprimir la comanda en ' + nombre + ': ' + str(e))
            return {'ok': False, 'error': 'La cuenta sí quedó en MrTienda, pero no se imprimió la comanda. Avísale a la caja.'}

    def comanda_formato(self, d):
        """Panel → COMANDERO → Comanda de cocina: ver una comanda de ejemplo, guardar el formato o imprimir una prueba."""
        acc = d.get('accion'); F = fmt_comanda(d.get('fmt') or self.cfg.get('comanda_fmt'))
        ej = d.get('ejemplo') or 'comedor'
        ren = [{'CANTIDAD': 2, 'DES_CANTAR': 'Chilaquiles verdes', '_det': 'Con huevo estrellado · Frijoles', 'VAR_LIBRE': '', 'COD_ESCALA': '01'},
               {'CANTIDAD': 1, 'DES_CANTAR': 'Club sandwich', '_det': '', 'VAR_LIBRE': 'SIN CEBOLLA', 'COD_ESCALA': '01'},
               {'CANTIDAD': 1, 'DES_CANTAR': 'Comida completa', '_det': '', 'VAR_LIBRE': '', 'COD_ESCALA': '01'}]
        canal = None; ref = 'M 4'
        if ej == 'llevar': canal = {'canal': 'recoger', 'nombre': 'Maria'}; ref = 'LLEVAR'
        elif ej == 'didi': canal = {'canal': 'didi', 'pedido': '123456789012', 'pago': 'efectivo'}; ref = 'D89012E'
        if canal:
            for r in ren: r['COD_ESCALA'] = '02'
        datos = comanda_bytes(ref, 'Angel', ren, '10021025', True, datetime.datetime.now(), canal, F)
        if acc == 'guardar':
            self.cfg['comanda_fmt'] = F
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            log('Formato de la comanda de cocina: ' + json.dumps(F))
        elif acc == 'prueba':
            nombre = self.cfg.get('impresora_cocina') or 'EPSON TM-T88V ReceiptE4'
            try: _imprimir_raw(nombre, datos.replace('CUENTA NUEVA'.encode('cp850'), 'PRUEBA - NO PREPARAR'.encode('cp850'), 1))
            except Exception as e: raise RuntimeError('No pude imprimir en ' + nombre + ': ' + str(e))
        return {'ok': True, 'fmt': F, 'guardado': fmt_comanda(self.cfg.get('comanda_fmt')), 'lineas': comanda_en_pantalla(datos)}

    def comandero_interruptor(self, d):
        self.cfg['comandero_escribe'] = bool(d.get('activo'))
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        log('Envío del comandero a MrTienda ' + ('ENCENDIDO' if self.cfg['comandero_escribe'] else 'apagado'))
        self.refresh()
        return {'ok': True}

    # ------------------------------------------------ Recursos Humanos (personal.json, solo en la caja; nunca va en data.json ni a Drive)
    def rh(self):
        try:
            with open(RH_FILE, encoding='utf-8') as f: d = json.load(f)
        except (OSError, ValueError): d = {}
        for k, v in (('empleados', []), ('roles', {}), ('roles_meta', {}), ('roles_h', {}), ('nominas', {}), ('prestamos', []), ('abonos', []), ('vales', []), ('acceso_rol', {}), ('desc_comida', {}), ('aud_ok', {}), ('res_aj', {})):
            if not isinstance(d.get(k), type(v)): d[k] = v
        if d.get('semana_inicia') != 'martes':
            rh_a_martes(d)
            if os.path.exists(RH_FILE):
                self._rh_guardar(d); log('Personal: las semanas del rol y la nómina ahora van de martes a lunes.')
        return d

    # Carga única (30.7.46): préstamos y abonos del Excel de Jorge (hoja 06 AL 12, oct-2026). Corre una sola vez.
    # No toca a quien ya tenga préstamos capturados, y no repite un abono si esa semana ya tiene abono en la nómina del panel.
    CARGA_PRESTAMOS = [
        ('rene', 2000, '2026-08-31', 500, [(500, '2026-08-31', 'vacaciones'), (250, '2026-09-07', ''), (250, '2026-09-14', ''), (250, '2026-09-21', ''), (250, '2026-09-28', ''),
                                         ('P', 1500, '2026-09-29'), (500, '2026-10-05', ''), (500, '2026-10-12', '')]),
        ('martin', 1100, '2026-09-01', 500, [(500, '2026-09-05', ''), (500, '2026-10-05', '')]),
        ('berenisse', 1750, '2026-10-01', 500, [(500, '2026-10-05', '')])]
    def carga_prestamos_excel(self):
        try:
            if not os.path.exists(RH_FILE): return
            d = self.rh(); cargas = d.setdefault('cargas', {})
            if cargas.get('prestamos_excel_oct2026'): return
            def norm(t): return re.sub(r'[^A-Z ]', ' ', _sin_acento(str(t or '')).upper()).split()
            def lev(a, b):
                v = list(range(len(b) + 1))
                for i in range(1, len(a) + 1):
                    p_, v[0] = v[0], i
                    for j in range(1, len(b) + 1): t_ = v[j]; v[j] = min(v[j] + 1, v[j - 1] + 1, p_ + (a[i - 1] != b[j - 1])); p_ = t_
                return v[len(b)]
            act = [e for e in d['empleados'] if e.get('activo', True)]
            def busca(clave):
                if clave == 'rene': m = [e for e in act if set(norm(e['nombre'])) & {'RENE', 'ANTUAN', 'ANTOINE'}]
                elif clave == 'martin': m = [e for e in act if norm(e['nombre'])[:1] == ['MARTIN']]
                else: m = [e for e in act if any(lev(w, 'BERENISSE') <= 2 for w in norm(e['nombre'])) and not any(lev(w, 'MONSERRAT') <= 2 for w in norm(e['nombre']))]
                return m[0] if len(m) == 1 else None
            hechos, faltan = [], []
            for clave, monto, f0, sug, movs in self.CARGA_PRESTAMOS:
                e = busca(clave)
                if not e: faltan.append(clave); continue
                if any(p_.get('emp') == e['id'] for p_ in d['prestamos']): faltan.append(clave + ' (ya tenía préstamos)'); continue
                d['prestamos'].append({'id': secrets.token_hex(4), 'emp': e['id'], 'fecha': f0, 'monto': monto, 'concepto': 'de tu Excel', 'abono_sugerido': sug})
                for mv in movs:
                    if mv[0] == 'P':
                        d['prestamos'].append({'id': secrets.token_hex(4), 'emp': e['id'], 'fecha': mv[2], 'monto': mv[1], 'concepto': 'de tu Excel', 'abono_sugerido': sug}); continue
                    sem = inicio_sem(datetime.date.fromisoformat(mv[1])).isoformat()
                    ya = (d['nominas'].get(sem) or {}).get(e['id'], {}).get('abono') or ((d.get('res_aj') or {}).get(sem) or {}).get(e['id'], {}).get('abono')
                    if ya: continue
                    d['abonos'].append({'id': secrets.token_hex(4), 'emp': e['id'], 'fecha': mv[1], 'monto': mv[0], 'nota': ((mv[2] + ' · ') if mv[2] else '') + 'de tu Excel'})
                hechos.append(e['nombre'])
            cargas['prestamos_excel_oct2026'] = {'cuando': datetime.datetime.now().isoformat(timespec='seconds'), 'cargados': hechos, 'faltan': faltan}
            self._rh_guardar(d)
            log('Personal: préstamos del Excel cargados para ' + (', '.join(hechos) or 'nadie') + ('; no encontré: ' + ', '.join(faltan) if faltan else ''))
        except Exception as e_: log('Personal: no pude cargar los préstamos del Excel: ' + str(e_))

    def _rh_guardar(self, d):
        tmp = RH_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False, indent=1)
        if os.path.exists(RH_FILE):
            os.makedirs(os.path.join(AQUI, 'respaldos_personal'), exist_ok=True)
            hoy = os.path.join(AQUI, 'respaldos_personal', 'personal_' + datetime.date.today().isoformat() + '.json')
            if not os.path.exists(hoy): shutil.copy2(RH_FILE, hoy)   # una copia por día, por si algo se borra por error
        os.replace(tmp, RH_FILE)
        self._refresh_pronto()   # 30.7.54: el panel ve al momento altas, bajas y sobrenombres

    _ref_lock = threading.RLock(); _ref_pend = [False]
    def _refresh_pronto(self):
        """30.7.56: muchos guardados seguidos en Personal = una sola actualización, 2 segundos después."""
        if self._ref_pend[0]: return
        self._ref_pend[0] = True
        def corre():
            self._ref_pend[0] = False
            try: self.refresh()
            except Exception as e: log('No pude actualizar después de guardar Personal: ' + str(e))
        t = threading.Timer(2, corre); t.daemon = True; t.start()

    def rh_info(self):
        d = self.rh()
        ac = d.get('acceso_rol') or {}
        d['acceso_rol'] = {'nombre': ac.get('nombre', ''), 'activo': bool(ac.get('activo') and ac.get('huella')), 'desde': ac.get('desde', '')}
        d['puestos'] = PUESTOS
        d['emp_mrtienda'] = sorted([{'cod': k, 'nombre': v} for k, v in getattr(self, 'EMP', {}).items() if v], key=lambda e: e['nombre'])
        try: d['vales_caja'] = self.vales_caja()
        except Exception as e: log('No pude leer vales de caja: ' + str(e)); d['vales_caja'] = []
        d['comidas'] = [{'id': q.get('id'), 'fecha': q.get('fecha'), 'persona': q.get('persona'), 'total': q.get('total'), 'arts': q.get('arts'),
                         'abrio': q.get('abrio'), 'cuenta': q.get('cuenta'), 'mesero': q.get('mesero'), 'nota': q.get('nota'),
                         'items': [{'n': i.get('n'), 'q': i.get('q'), 'imp': i.get('imp')} for i in (q.get('items') or [])][:60]}
                        for q in self.quitadas() if q.get('motivo') == 'Empleado']   # comidas de empleados (con su comanda) para los vales de comida
        d['hoy'] = datetime.date.today().isoformat()
        return d

    def _rol_poner(self, d, sem, emp, dias, quien, horas=None):
        # guarda el rol de un empleado (y su horario de cada día, si cambió) y anota quién lo capturó y quién lo corrigió después
        d['roles'].setdefault(sem, {})[emp] = dias
        if horas is not None: d.setdefault('roles_h', {}).setdefault(sem, {})[emp] = horas
        m = d['roles_meta'].setdefault(sem, {}).get(emp) or {}
        m.update({'por': quien, 'cuando': datetime.datetime.now().isoformat(timespec='minutes')})
        m.setdefault('primero', quien)
        d['roles_meta'][sem][emp] = m

    # Captura del rol de descansos por la encargada (27.8): su propia dirección (/descansos) y su clave de 4 números.
    # Solo ve nombres y puestos (nada de sueldos, nómina, vales ni préstamos) y solo puede capturar el rol.
    def login_rol(self, pin, ip):
        ahora = time.time(); k = 'r:' + ip; f = FALLOS.get(k, [0, 0])
        if f[1] > ahora: raise RuntimeError('Demasiados intentos. Espera unos minutos.')
        ac = self.rh().get('acceso_rol') or {}
        if ac.get('activo') and verifica_pin(ac, str(pin or '')):
            FALLOS.pop(k, None); tok = secrets.token_urlsafe(24)
            SESIONES_R[tok] = (ac.get('nombre') or 'Encargada', ahora + 30 * 24 * 3600)
            return {'ok': True, 'token': tok, 'nombre': ac.get('nombre') or 'Encargada'}
        f[0] += 1
        if f[0] >= 5: f = [0, ahora + 300]
        FALLOS[k] = f
        raise RuntimeError('Clave incorrecta.')

    def rol_de(self, tok):
        s_ = SESIONES_R.get(tok or '')
        if not s_ or s_[1] < time.time(): SESIONES_R.pop(tok or '', None); return None
        return s_[0]

    def rol_info(self, sem=''):
        d = self.rh(); hoy = datetime.date.today()
        try: f = datetime.date.fromisoformat(sem)
        except ValueError: f = hoy
        lun = inicio_sem(f).isoformat()
        emps = sorted([e for e in d['empleados'] if e.get('activo', True)], key=lambda e: (ORDEN_ROL.index(e['puesto']) if e.get('puesto') in ORDEN_ROL else 99, e.get('nombre', '')))
        pagados = {k for k, v in (d['nominas'].get(lun) or {}).items() if v.get('pagada')}
        return {'ok': True, 'semana': lun, 'hoy': hoy.isoformat(),
                'empleados': [{'id': e['id'], 'nombre': e.get('nombre', ''), 'puesto': e.get('puesto', ''), 'cubre_mesero': bool(e.get('cubre_mesero')), 'descanso': e.get('descanso', ''),
                               'horario': horario_texto(rh_base(e)), 'base': base_sem(rh_base(e)), 'comida': int(e.get('comida_min') or 0), 'pagada': e['id'] in pagados} for e in emps],
                'rol': d['roles'].get(lun) or {}, 'meta': d['roles_meta'].get(lun) or {}, 'aud_ok': {k: {} for k in d.get('aud_ok', {})}, 'horas': (d.get('roles_h') or {}).get(lun) or {},
                'anterior_tiene': bool(d['roles'].get((datetime.date.fromisoformat(lun) - datetime.timedelta(days=7)).isoformat()))}

    def rol_accion_enc(self, quien, a):
        d = self.rh(); acc = a.get('accion')
        try: lun = datetime.date.fromisoformat(str(a.get('semana') or ''))
        except ValueError: raise ValueError('Revisa la semana.')
        if lun.weekday() != SEM_INI: raise ValueError('Revisa la semana.')
        sem = lun.isoformat()
        if lun < datetime.date.today() - datetime.timedelta(days=35): raise ValueError('Esa semana ya pasó hace mucho. Si hay que corregirla, avísale a Jorge.')
        pagados = {k for k, v in (d['nominas'].get(sem) or {}).items() if v.get('pagada')}
        activos = {e['id'] for e in d['empleados'] if e.get('activo', True)}
        if acc == 'guardar':
            emp = a.get('emp')
            if emp not in activos: raise ValueError('No encontré a ese empleado.')
            if emp in pagados: raise ValueError('La nómina de esa semana ya se pagó. Si hay que corregir algo, avísale a Jorge.')
            dias = [c if c in ('T', 'D', 'F', 'V', 'I') else 'T' for c in (a.get('dias') or [])][:7]
            if len(dias) != 7: raise ValueError('Faltan días.')
            self._rol_poner(d, sem, emp, dias, quien, horas_list(a.get('horas')))
        elif acc == 'copiar':
            ant = (lun - datetime.timedelta(days=7)).isoformat()
            if not d['roles'].get(ant): raise ValueError('La semana anterior no tiene rol capturado.')
            for k, v in d['roles'][ant].items():
                if k in activos and k not in pagados: self._rol_poner(d, sem, k, list(v), quien, ((d.get('roles_h') or {}).get(ant) or {}).get(k))
        else: raise ValueError('Acción no válida.')
        self._rh_guardar(d)
        log('Rol de descansos: ' + quien + ' capturó la semana ' + sem)
        return self.rol_info(sem)

    def rh_archivo_subir(self, a):
        d = self.rh(); x = next((x for x in d['empleados'] if x['id'] == a.get('emp')), None)
        if not x: raise ValueError('Primero guarda el expediente del empleado.')
        mime = str(a.get('mime') or '').lower(); ext = TIPOS_DOC.get(mime)
        if not ext: raise ValueError('Sube una foto, un PDF o un archivo de Word.')
        tipo = str(a.get('tipo') or '').strip()[:30]
        if not tipo: raise ValueError('Antes dime si el contrato es por tiempo determinado o indeterminado.')
        try: datos = base64.b64decode(str(a.get('datos') or '').split(',')[-1], validate=True)
        except Exception: raise ValueError('No pude leer el archivo. Intenta otra vez.')
        if not datos or len(datos) > 12 * 1024 * 1024: raise ValueError('El archivo pesa demasiado (máximo 12 MB).')
        carpeta = os.path.join(EXPED_DIR, x['id']); os.makedirs(carpeta, exist_ok=True)
        fid = secrets.token_hex(4); nom = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '_' + fid + ext
        with open(os.path.join(carpeta, nom), 'wb') as f: f.write(datos)
        orig = re.sub(r'[^\w .()-]', '', str(a.get('nombre') or ''))[:80] or ('Contrato' + ext)
        x.setdefault('archivos', []).append({'id': fid, 'archivo': nom, 'nombre': orig, 'mime': mime, 'tipo': tipo, 'fecha': datetime.date.today().isoformat(), 'bytes': len(datos)})
        if not (x.get('contrato') or {}).get('tipo'): x.setdefault('contrato', {})['tipo'] = tipo
        self._rh_guardar(d)
        log('Expediente: se subió ' + orig + ' de ' + x.get('nombre', ''))
        return {'ok': True, 'rh': self.rh_info()}

    def rh_archivo(self, emp, fid):
        x = next((x for x in self.rh()['empleados'] if x['id'] == emp), None)
        doc = next((f for f in (x or {}).get('archivos') or [] if f.get('id') == fid), None)
        if not doc: return None
        ruta = os.path.join(EXPED_DIR, x['id'], os.path.basename(doc['archivo']))
        if not os.path.isfile(ruta): return None
        with open(ruta, 'rb') as f: return doc, f.read()

    def vales_caja(self):
        # salidas de caja de MrTienda que la cajera capturó como vale (últimos 70 días)
        out = []
        try: data = json.loads(self.json)
        except ValueError: return out
        for f, D in (data.get('detalle') or {}).items():
            cajeros = {c.get('turno'): c.get('cajero', '') for c in (D.get('cortes') or []) if isinstance(c, dict)}
            for m in D.get('salidas', []) if isinstance(D.get('salidas'), list) else []:
                if (m.get('cat') or '') == 'Vales' or re.match(r'^\s*VALE', str(m.get('ref') or ''), re.I):
                    out.append({'fecha': f, 'ref': m.get('ref', ''), 'monto': m.get('importe', 0), 'hora': m.get('hora', ''), 'turno': m.get('turno'), 'cajero': cajeros.get(m.get('turno'), '')})
        return out

    def rh_accion(self, a):
        d = self.rh(); acc = a.get('accion')
        txt = lambda v, n=80: re.sub(r'\s+', ' ', str(v or '')).strip()[:n]
        def num(v):
            try: return round(float(str('' if v is None else v).replace('$', '').replace(',', '').strip() or 0), 2)
            except ValueError: raise ValueError('Revisa las cantidades: solo números.')
        def fecha(v, req=False):
            v = txt(v, 10)
            if not v and not req: return ''
            try: datetime.date.fromisoformat(v); return v
            except ValueError: raise ValueError('Revisa la fecha.')
        def emp_id(v):
            if not any(e['id'] == v for e in d['empleados']): raise ValueError('No encontré a ese empleado.')
            return v
        if acc == 'emp_guardar':
            e = a.get('emp') or {}
            nombre = txt(e.get('nombre'), 80).upper()
            if not nombre: raise ValueError('Escribe el nombre completo.')
            puesto = e.get('puesto') if e.get('puesto') in PUESTOS else 'Otro'
            base = horas_list(e.get('horario')) if isinstance(e.get('horario'), list) else None
            if base is None: base = rh_base(e)
            diario = num(e.get('diario')); trab = [h for h in base if h]
            comunes = collections.Counter(tuple(h) for h in trab).most_common(1)
            try: comida_min = max(0, min(180, int(float(e.get('comida_min') or 0))))
            except ValueError: raise ValueError('Revisa los minutos de comida.')
            reg = {'nombre': nombre, 'apodo': txt(e.get('apodo'), 20).title(), 'puesto': puesto, 'cod_mt': txt(e.get('cod_mt'), 6), 'ingreso': fecha(e.get('ingreso')),
                   'diario': diario, 'incentivo': num(e.get('incentivo')), 'comida_min': comida_min, 'horario': base,
                   'pago': 'hora' if e.get('pago') == 'hora' else 'dia', 'tarifa': num(e.get('tarifa')), 'resta_comida': bool(e.get('resta_comida')), 'paga_prima': bool(e.get('paga_prima')), 'prima_siempre': bool(e.get('prima_siempre')), 'paga_extra': e.get('paga_extra') is not False, 'cubre_mesero': bool(e.get('cubre_mesero')),
                   'sueldo': round(diario * len(trab), 2) if diario else num(e.get('sueldo')),
                   'telefono': txt(e.get('telefono'), 20), 'nss': txt(e.get('nss'), 15), 'curp': txt(e.get('curp'), 18).upper(), 'rfc': txt(e.get('rfc'), 13).upper(),
                   'domicilio': txt(e.get('domicilio'), 160), 'entrada': comunes[0][0][0] if comunes else '', 'salida': comunes[0][0][1] if comunes else '',
                   'descanso': next((DIAS_SEM[i] for i in range(7) if base[i] is None), ''),
                   'contrato': {'estado': 'firmado' if (e.get('contrato') or {}).get('estado') == 'firmado' else 'pendiente',
                                'tipo': txt((e.get('contrato') or {}).get('tipo'), 30), 'fecha': fecha((e.get('contrato') or {}).get('fecha')),
                                'nota': txt((e.get('contrato') or {}).get('nota'), 200)},
                   'notas': txt(e.get('notas'), 500)}
            fpin = re.sub(r'\D', '', str(e.get('firma_nueva') or ''))
            if e.get('firma_nueva') not in (None, '') and len(fpin) != 4: raise ValueError('La clave para firmar la comida es de 4 números.')
            if fpin: sal_ = secrets.token_hex(8); reg['firma'] = {'sal': sal_, 'huella': huella_pin(sal_, fpin), 'clave': fpin, 'desde': datetime.date.today().isoformat()}
            elif e.get('firma_quitar'): reg['firma'] = {}
            x = next((x for x in d['empleados'] if x['id'] == e.get('id')), None) if e.get('id') else None
            if x: x.update(reg)   # 'archivos' (contratos subidos) no se toca aquí
            else: reg.update({'id': secrets.token_hex(4), 'activo': True, 'alta': datetime.datetime.now().isoformat(timespec='seconds')}); d['empleados'].append(reg)
        elif acc == 'emp_nombres':   # solo cambia el nombre (para completar apellidos) sin tocar lo demás del expediente
            n_ = 0
            for c_ in (a.get('cambios') or [])[:100]:
                nom = txt((c_ or {}).get('nombre'), 80).upper()
                x = next((x for x in d['empleados'] if x['id'] == (c_ or {}).get('id')), None)
                if x and nom and nom != x.get('nombre'): x['nombre'] = nom; n_ += 1
            if not n_: raise ValueError('No hubo nombres que cambiar.')
        elif acc == 'emp_baja':
            for x in d['empleados']:
                if x['id'] == a.get('id'): x['activo'] = bool(a.get('reactivar')); x['fecha_baja'] = '' if a.get('reactivar') else datetime.date.today().isoformat()
        elif acc == 'rol_guardar':
            sem = fecha(a.get('semana'), True); dias = [c if c in ('T', 'D', 'F', 'V', 'I') else 'T' for c in (a.get('dias') or [])][:7]
            if len(dias) != 7: raise ValueError('Faltan días.')
            self._rol_poner(d, sem, emp_id(a.get('emp')), dias, 'Jorge', horas_list(a.get('horas')))
        elif acc == 'rol_copiar':
            sem, ant = fecha(a.get('semana'), True), fecha(a.get('anterior'), True)
            if ant not in d['roles']: raise ValueError('La semana anterior no tiene rol capturado.')
            for k, v in d['roles'][ant].items(): self._rol_poner(d, sem, k, list(v), 'Jorge', ((d.get('roles_h') or {}).get(ant) or {}).get(k))
        elif acc == 'acceso_rol':
            pin = txt(a.get('pin'), 8); nombre = txt(a.get('nombre'), 40).title() or 'Encargada'
            if not re.fullmatch(r'\d{4}', pin): raise ValueError('La clave debe ser de 4 números.')
            if any(x.get('activo') and verifica_pin(x, pin) for x in self.meseros()): raise ValueError('Esa clave ya la tiene un mesero. Escoge otra.')
            sal = secrets.token_hex(8)
            d['acceso_rol'] = {'nombre': nombre, 'sal': sal, 'huella': huella_pin(sal, pin), 'clave': pin, 'activo': True, 'desde': datetime.date.today().isoformat()}
            for t_ in [t_ for t_, v in SESIONES_R.items()]: SESIONES_R.pop(t_, None)   # la clave anterior deja de servir
        elif acc == 'acceso_rol_quitar':
            d['acceso_rol'] = {}
            SESIONES_R.clear()
        elif acc == 'acceso_rol_ver':
            return {'ok': True, 'clave': (d.get('acceso_rol') or {}).get('clave', '')}
        elif acc == 'nomina_guardar':
            sem = fecha(a.get('semana'), True); c = a.get('campos') or {}
            d['nominas'].setdefault(sem, {})[emp_id(a.get('emp'))] = {k: num(c.get(k)) for k in ('base', 'septimo', 'extra_horas', 'h_extra', 'prima_dom', 'incentivo', 'extras', 'comida', 'vales', 'abono', 'otros', 'transfer', 'horas')} | {'nota': txt(c.get('nota'), 200), 'pagada': bool(c.get('pagada')), 'resta_comida': bool(c.get('resta_comida'))}
        elif acc == 'prestamo_alta':
            m = num(a.get('monto'))
            if m <= 0: raise ValueError('Escribe el monto del préstamo.')
            d['prestamos'].append({'id': secrets.token_hex(4), 'emp': emp_id(a.get('emp')), 'fecha': fecha(a.get('fecha'), True), 'monto': m, 'concepto': txt(a.get('concepto'), 120), 'abono_sugerido': num(a.get('abono'))})
        elif acc == 'abono_alta':   # abonos a préstamos que no salieron de la nómina del panel (los de antes, en Excel, o pagados aparte)
            m = num(a.get('monto'))
            if m <= 0: raise ValueError('Escribe el monto del abono.')
            d.setdefault('abonos', []).append({'id': secrets.token_hex(4), 'emp': emp_id(a.get('emp')), 'fecha': fecha(a.get('fecha'), True), 'monto': m, 'nota': txt(a.get('nota'), 120)})
        elif acc == 'prestamo_semanal':   # cuánto se le descuenta por semana de UN préstamo (cada préstamo lleva el suyo)
            x = next((x for x in d['prestamos'] if x.get('id') == a.get('id')), None) if a.get('id') else None
            if not x:
                e = emp_id(a.get('emp')); P = [p_ for p_ in d['prestamos'] if p_.get('emp') == e]
                if not P: raise ValueError('No tiene préstamos.')
                x = P[-1]
            x['abono_sugerido'] = num(a.get('abono'))
        elif acc == 'abono_borrar':
            d['abonos'] = [x for x in d.get('abonos', []) if x.get('id') != a.get('id')]
        elif acc == 'vale_alta':
            m = num(a.get('monto')); tipo = 'comida' if a.get('tipo') == 'comida' else 'dinero'
            if m <= 0: raise ValueError('Escribe el descuento.' if tipo == 'comida' else 'Escribe el monto del vale.')
            d['vales'].append({'id': secrets.token_hex(4), 'emp': emp_id(a.get('emp')), 'fecha': fecha(a.get('fecha'), True), 'monto': m, 'tipo': tipo,
                               'consumo': num(a.get('consumo')) if tipo == 'comida' else 0, 'nota': txt(a.get('nota'), 120)})
        elif acc == 'res_aj':
            # ajuste que Jorge escribe en el Resumen de nómina (préstamo, vales, otros, transferencia); vacío = automático
            sem = fecha(a.get('semana'), True); e = emp_id(a.get('emp')); k = a.get('k')
            if k not in ('abono', 'comida', 'vales', 'otros', 'transfer', 'grat'): raise ValueError('Ese dato no se puede cambiar aquí.')
            r = d['res_aj'].setdefault(sem, {}).setdefault(e, {})
            if a.get('valor') in (None, ''): r.pop(k, None)
            else: r[k] = num(a.get('valor'))
            if not r: d['res_aj'][sem].pop(e, None)
        elif acc in ('aud_ok', 'aud_quitar'):
            # diferencias de horario que Jorge acepta en la auditoría del rol (p. ej. Martín entra 15 min después); opcional: pagar la diferencia
            k = txt(a.get('k'), 80)
            if not re.fullmatch(r'[0-9a-f]{4,16}\|(ent|sal)\|\d\d:\d\d\|\d\d:\d\d', k): raise ValueError('No entendí qué aceptar.')
            if acc == 'aud_ok': d['aud_ok'][k] = {'pagar': bool(a.get('pagar')), 'cuando': datetime.date.today().isoformat()}
            else: d['aud_ok'].pop(k, None)
        elif acc == 'comida_desc':
            # descuento que Jorge decide para una comida de empleado registrada en la caja (cuenta quitada con motivo Empleado)
            k = txt(a.get('id'), 80)
            if not k: raise ValueError('Falta la comida.')
            # 30.7.20: además del monto se guarda el porcentaje que se usó y a qué empleado le toca (si el nombre no se reconoció solo)
            vacio = a.get('monto') in (None, '')
            m = None if vacio else num(a.get('monto'))
            if m is not None and m < 0: raise ValueError('Revisa el descuento.')
            pct = None if a.get('pct') in (None, '') else num(a.get('pct'))
            if pct is not None and not 0 <= pct <= 100: raise ValueError('El porcentaje va de 0 a 100.')
            emp = a.get('emp') or ''
            if emp: emp_id(emp)
            if m is None and not emp: d['desc_comida'].pop(k, None)
            else: d['desc_comida'][k] = {'monto': m, 'pct': pct, 'emp': emp}
        elif acc == 'archivo_borrar':
            x = next((x for x in d['empleados'] if x['id'] == a.get('emp')), None)
            if not x: raise ValueError('No encontré a ese empleado.')
            doc = next((f for f in x.get('archivos') or [] if f.get('id') == a.get('id')), None)
            if doc:
                ruta = os.path.join(EXPED_DIR, x['id'], os.path.basename(doc.get('archivo', '')))
                try:
                    os.makedirs(os.path.join(EXPED_DIR, '_borrados'), exist_ok=True)
                    if os.path.isfile(ruta): shutil.move(ruta, os.path.join(EXPED_DIR, '_borrados', x['id'] + '_' + os.path.basename(ruta)))   # se aparta, no se destruye
                except OSError: pass
                x['archivos'] = [f for f in x['archivos'] if f.get('id') != a.get('id')]
        elif acc in ('prestamo_borrar', 'vale_borrar'):
            k = 'prestamos' if acc == 'prestamo_borrar' else 'vales'
            d[k] = [x for x in d[k] if x['id'] != a.get('id')]
        else: raise ValueError('Acción no válida.')
        self._rh_guardar(d)
        return {'ok': True, 'rh': self.rh_info()}

    # ------------------------------------------------ lectura de tickets con la API de Claude
    def catalogo_insumos(self):
        c = collections.Counter()
        for m in self.gastos()['movs']:
            for i in m.get('items') or []:
                if i.get('insumo'): c[i['insumo']] += 1
        return [n for n, _ in c.most_common(200)]

    def leer_ticket(self, b64):
        llave = self.cfg.get('ia_llave')
        if not llave: raise RuntimeError('La lectura automática no está activada.')
        if ',' in b64[:80]: b64 = b64.split(',', 1)[1]
        cat = ', '.join(self.catalogo_insumos()) or '(todavía no hay catálogo)'
        cuerpo = {'model': self.cfg.get('ia_modelo') or IA_MODELO, 'max_tokens': 4000,
                  'messages': [{'role': 'user', 'content': [
                      {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64}},
                      {'type': 'text', 'text': PROMPT_TICKET.replace('{catalogo}', cat)}]}]}
        req = urllib.request.Request(IA_URL, data=json.dumps(cuerpo).encode('utf-8'), method='POST',
                                     headers={'x-api-key': llave, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=90) as r: resp = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            det = e.read().decode('utf-8', 'ignore')[:300]
            log('La lectura del ticket falló: ' + str(e.code) + ' ' + det)
            raise RuntimeError('No se pudo leer el ticket (' + ('llave no válida' if e.code in (401, 403) else 'sin saldo o límite' if e.code in (402, 429) else 'error ' + str(e.code)) + ').')
        txt = ''.join(b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text')
        a, z = txt.find('{'), txt.rfind('}')
        if a < 0 or z < a: raise RuntimeError('No se entendió la lectura del ticket.')
        return limpia_lectura(json.loads(txt[a:z + 1]))

    def leer_pendientes(self, maximo=15):
        g = self.gastos(); hechos = 0; errores = 0
        pend = [m for m in g['movs'] if m.get('foto') and not m.get('items') and not (m.get('lectura') or {}).get('intentado')][:maximo]
        resultados = {}
        for m in pend:
            try:
                with open(os.path.join(FOTOS_DIR, m['foto']), 'rb') as f: b64 = base64.b64encode(f.read()).decode()
                resultados[m['id']] = self.leer_ticket(b64); hechos += 1
            except Exception as e:
                resultados[m['id']] = {'intentado': True, 'error': str(e)[:120]}; errores += 1
        with self.q_lock:
            with open(GASTOS_FILE, encoding='utf-8') as f: g2 = json.load(f)
            for m in g2.get('movs', []):
                r = resultados.get(m.get('id'))
                if not r: continue
                if r.get('error'): m['lectura'] = r; continue
                aplica_lectura(m, r)
            self._guardar_gastos(g2)
        self.refresh()
        return {'ok': True, 'leidos': hechos, 'errores': errores, 'faltan': max(0, len([m for m in g['movs'] if m.get('foto') and not m.get('items')]) - hechos)}

    def guardar_foto(self, mid, b64):
        # Por decisión de Jorge las fotos NO se guardan en la computadora: solo se leen y se descartan.
        if not b64 or not self.cfg.get('guardar_fotos', False): return ''
        if ',' in b64[:80]: b64 = b64.split(',', 1)[1]
        raw = base64.b64decode(b64)
        if not raw.startswith(b'\xff\xd8') or len(raw) > 6 * 1024 * 1024: raise ValueError('La foto no es válida.')
        os.makedirs(FOTOS_DIR, exist_ok=True)
        nom = re.sub(r'[^0-9A-Za-z_-]', '', mid) + '.jpg'
        with open(os.path.join(FOTOS_DIR, nom), 'wb') as f: f.write(raw)
        return nom

    # ------------------------------------------------ fotos en Google Drive (nunca en el disco de la computadora)
    def _drive(self, cuerpo, timeout=60):
        url = self.cfg.get('drive_url')
        if not url: raise RuntimeError('Google Drive no está conectado.')
        cuerpo = dict(cuerpo, secreto=self.cfg.get('drive_secreto'))
        req = urllib.request.Request(url, data=json.dumps(cuerpo).encode('utf-8'), method='POST', headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r: txt = r.read().decode('utf-8', 'ignore')
        try: j = json.loads(txt)
        except ValueError: raise RuntimeError('Google Drive no respondió bien. Revisa que la dirección sea la de la "aplicación web" y que el acceso sea "Cualquier usuario".')
        if not j.get('ok'): raise RuntimeError('Google Drive: ' + str(j.get('error') or 'error'))
        return j

    def subir_foto_drive(self, mid, b64, nombre):
        """Sube la foto a Drive en segundo plano y anota el id en la compra. La foto solo vive en memoria."""
        def tarea():
            try:
                datos = b64.split(',', 1)[1] if ',' in b64[:80] else b64
                j = self._drive({'accion': 'subir', 'nombre': nombre, 'datos': datos}, timeout=120)
                with self.q_lock:
                    with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
                    for m in g.get('movs', []):
                        if m.get('id') == mid: m['foto_drive'] = j.get('id')
                    self._guardar_gastos(g)
                self.refresh()
            except Exception as e:
                log('No pude subir la foto a Google Drive: ' + str(e))
        threading.Thread(target=tarea, daemon=True).start()

    def borrar_fotos_drive(self, motivo='manual'):
        j = self._drive({'accion': 'borrar'}, timeout=300)
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
                for m in g.get('movs', []): m.pop('foto_drive', None)
                self._guardar_gastos(g)
            except (OSError, ValueError): pass
        self.cfg['drive_ultimo_borrado'] = datetime.date.today().isoformat()
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        log(f"Fotos de Google Drive borradas ({motivo}): {j.get('borradas', 0)}" + (' (a la papelera)' if j.get('papelera') else ''))
        self.refresh()
        return {'ok': True, 'borradas': j.get('borradas', 0), 'papelera': j.get('papelera', 0)}

    def borrado_mensual(self):
        if not (self.cfg.get('drive_url') and self.cfg.get('drive_mensual')): return
        hoy = datetime.date.today(); ult = str(self.cfg.get('drive_ultimo_borrado') or '')
        if hoy.day == 1 and ult[:7] != hoy.isoformat()[:7]:
            try: self.borrar_fotos_drive('automático del mes')
            except Exception as e: log('No pude hacer el borrado mensual de fotos: ' + str(e))

    def borrar_fotos(self):
        """Quita de la computadora cualquier foto guardada antes y las referencias a ellas."""
        if self.cfg.get('guardar_fotos', False): return
        n = 0
        if os.path.isdir(FOTOS_DIR):
            for f in os.listdir(FOTOS_DIR):
                try: os.remove(os.path.join(FOTOS_DIR, f)); n += 1
                except OSError: pass
            try: os.rmdir(FOTOS_DIR)
            except OSError: pass
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
            except (OSError, ValueError): g = None
            if isinstance(g, dict):
                cambio = False
                for x in g.get('movs', []) + g.get('recibos', []):
                    if x.get('foto'): x['foto'] = ''; cambio = True
                if cambio: self._guardar_gastos(g)
        if n: log(f'Se borraron {n} fotos de tickets de esta computadora (las fotos ya no se guardan).')

    def captura(self, d, origen, quien=None):
        """Compra o gasto capturado desde el teléfono del cajero (o por Jorge), con foto opcional."""
        m = self._limpia_mov(d.get('mov') or {})
        if origen == 'caja':
            m['quien'] = quien or m['quien']; m['fecha'] = datetime.date.today().isoformat()   # el cajero solo captura compras del día
        ahora = datetime.datetime.now()
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
                if not isinstance(g, dict): g = {}
            except (OSError, ValueError): g = {}
            g.setdefault('movs', []); g.setdefault('fijos', []); g.setdefault('recibos', [])
            m['id'] = ahora.strftime('%Y%m%d%H%M%S') + '-' + str(len(g['movs']))
            m['capturado'] = ahora.isoformat(timespec='seconds'); m['origen'] = origen
            m['foto'] = self.guardar_foto(m['id'], d.get('foto'))
            subir = bool(d.get('foto') and self.cfg.get('drive_url'))
            g['movs'].append(m); self._guardar_gastos(g)
        log(f"Compra capturada ({origen}): {m['proveedor'] or m['concepto']} {m['monto']:.2f} · {m['negocio']}")
        if subir:
            nom = f"{m['fecha']} {m['proveedor'] or m['concepto']} {m['monto']:.2f} {m['id']}.jpg"
            self.subir_foto_drive(m['id'], d.get('foto'), re.sub(r'[\\/:*?"<>|]', '', nom))
        self.refresh()
        return {'ok': True, 'id': m['id']}

    def captura_info(self):
        hoy = datetime.date.today().isoformat()
        g = self.gastos()
        provs = collections.Counter(m.get('proveedor') for m in g['movs'] if m.get('proveedor'))
        return {'ok': True, 'version': VERSION_TXT, 'hoy': hoy, 'categorias': CATEGORIAS_GASTO, 'negocios': NEGOCIOS, 'pagos': PAGOS,
                'ia': bool(self.cfg.get('ia_llave')), 'drive': bool(self.cfg.get('drive_url')), 'insumos': self.catalogo_insumos(),
                'proveedores': [p for p, _ in provs.most_common(60)],
                'hoy_lista': [{k: m.get(k) for k in ('fecha', 'proveedor', 'concepto', 'monto', 'negocio', 'quien', 'capturado', 'ticket')}
                              for m in g['movs'] if m.get('fecha') == hoy and m.get('origen') == 'caja']}

    def gasto_accion(self, d):
        acc = d.get('accion')
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
                if not isinstance(g, dict): g = {}
            except (OSError, ValueError): g = {}
            g.setdefault('movs', []); g.setdefault('fijos', []); g.setdefault('recibos', [])
            ahora = datetime.datetime.now()
            if acc == 'recibo':
                r = d.get('recibo') or {}
                de, ha = str(r.get('desde') or '')[:10], str(r.get('hasta') or '')[:10]
                if datetime.date.fromisoformat(de) > datetime.date.fromisoformat(ha): raise ValueError('La fecha "desde" va después de "hasta".')
                monto = round(float(str(r.get('monto')).replace('$', '').replace(',', '')), 2)
                if monto <= 0: raise ValueError('Monto no válido.')
                rec = {'id': ahora.strftime('%Y%m%d%H%M%S') + '-r' + str(len(g['recibos'])), 'concepto': str(r.get('concepto') or '').strip()[:60],
                       'desde': de, 'hasta': ha, 'monto': monto, 'capturado': ahora.isoformat(timespec='seconds')}
                if not rec['concepto']: raise ValueError('Falta el concepto.')
                rec['foto'] = self.guardar_foto(rec['id'], d.get('foto'))
                g['recibos'].append(rec); n = 1
            elif acc == 'borrar_recibo':
                antes = len(g['recibos']); g['recibos'] = [r for r in g['recibos'] if r.get('id') != d.get('id')]; n = antes - len(g['recibos'])
                if not n: raise RuntimeError('No encontré ese recibo.')
            elif acc == 'agregar':
                nuevos = d.get('movs') or [d.get('mov')]
                limpios = [self._limpia_mov(m) for m in nuevos if m]
                if not limpios: raise ValueError('No hay gastos para guardar.')
                for i, m in enumerate(limpios):
                    m['id'] = ahora.strftime('%Y%m%d%H%M%S') + '-' + str(len(g['movs']) + i)
                    m['capturado'] = ahora.isoformat(timespec='seconds')
                g['movs'].extend(limpios); n = len(limpios)
            elif acc == 'borrar':
                antes = len(g['movs']); g['movs'] = [m for m in g['movs'] if m.get('id') != d.get('id')]; n = antes - len(g['movs'])
                if not n: raise RuntimeError('No encontré ese gasto.')
            elif acc == 'fijos':
                fj = []
                for x in d.get('fijos') or []:
                    try: monto = round(float(str(x.get('monto')).replace('$', '').replace(',', '')), 2)
                    except (TypeError, ValueError): continue
                    if monto <= 0: continue
                    fj.append({'concepto': str(x.get('concepto') or '').strip()[:60] or 'Gasto fijo',
                               'categoria': x.get('categoria') if x.get('categoria') in CATEGORIAS_GASTO else 'Otros gastos', 'monto': monto})
                g['fijos'] = fj; n = len(fj)
            else: raise ValueError('Acción no válida.')
            self._guardar_gastos(g)
        self.refresh()
        return {'ok': True, 'n': n}

    def _persona(self, motivo, persona, previo=''):
        persona = re.sub(r'\s+', ' ', str(persona or '')).strip()[:60]
        if motivo in CON_PERSONA and not persona: raise ValueError('Escribe de quién fue la comida.')
        if persona:   # usar siempre la misma escritura del nombre (Mónica = mónica = MONICA)
            conocidos = self.familia() + self.empleados() + [r.get('persona') for r in self.quitadas() if r.get('persona')]
            persona = next((x for x in conocidos if x and x.lower() == persona.lower()), persona if persona != persona.lower() else persona.title())
        if motivo == 'Familia' and persona: self._alta_familia(persona)
        nom = self.empleados_nomina() if motivo in ('Empleado', 'Comida de empleado') else {}
        if nom and persona:   # con nómina: el nombre tiene que ser uno de los de Personal (sin importar mayúsculas ni acentos)
            k = _sin_acentos(persona.lower())
            ap = self.empleados_apodo()
            persona = next((n for n in nom if _sin_acentos(n.lower()) == k), None) or next((n for n, a in ap.items() if _sin_acentos(a.lower()) == k), None)
            if not persona and previo and _sin_acentos(str(previo).lower()) == k: persona = previo   # 30.7.56: el nombre que ya tenía (de antes o de alguien dado de baja)
            if not persona: raise ValueError('Ese nombre no está en la nómina. Tócalo en la lista; si es alguien nuevo, dalo de alta primero en Personal.')
        elif motivo in ('Empleado', 'Comida de empleado') and persona and persona not in self.empleados():
            self.cfg['empleados_extra'] = (self.cfg.get('empleados_extra') or []) + [persona]
            with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(self.cfg, fh, ensure_ascii=False, indent=2)
        return persona if motivo in CON_PERSONA or motivo == 'Cortesía' else ''

    def empleados_nomina(self):
        """30.7.53: empleados activos de Personal, con su nombre completo tal como está en la nómina, y su puesto."""
        out = {}
        for e in self.rh().get('empleados', []):
            n = re.sub(r'\s+', ' ', str(e.get('nombre') or '')).strip().title()
            if e.get('activo') and n and n.lower() not in {x.lower() for x in out}: out[n] = str(e.get('puesto') or '').strip() or 'Otros'
        return out

    def empleados_apodo(self):
        """30.7.54: sobrenombre de cada empleado activo (Pepe → José ...), para mostrarlo y para reconocerlo al escribirlo."""
        return {re.sub(r'\s+', ' ', str(e.get('nombre') or '')).strip().title(): str(e.get('apodo') or '').strip()
                for e in self.rh().get('empleados', []) if e.get('activo') and e.get('apodo')}

    def empleados(self):
        nom = self.empleados_nomina()
        if nom: return sorted(nom)   # solo la nómina: lo que se escoja aquí cae directo a la nómina de esa persona
        rh = {e['nombre'].title() for e in self.rh()['empleados'] if e.get('activo')}   # negocio sin Personal: como antes
        return sorted(set(v for v in getattr(self, 'EMP', {}).values() if v) | {x['nombre'] for x in self.cajeros() if x.get('activo')} | set(self.cfg.get('empleados_extra') or []) | rh)

    def familia(self):
        f = self.cfg.get('familia')
        return f if isinstance(f, list) else []

    def _alta_familia(self, nombre):
        f = self.familia()
        if any(x.lower() == nombre.lower() for x in f): return
        self.cfg['familia'] = f + [nombre]
        with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(self.cfg, fh, ensure_ascii=False, indent=2)

    def familia_accion(self, d):
        nombre = re.sub(r'\s+', ' ', str(d.get('nombre') or '')).strip()[:40]
        if not nombre: raise ValueError('Escribe el nombre.')
        if nombre == nombre.lower(): nombre = nombre.title()
        if d.get('accion') == 'alta': self._alta_familia(nombre)
        elif d.get('accion') == 'baja':
            self.cfg['familia'] = [x for x in self.familia() if x.lower() != nombre.lower()]
            with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(self.cfg, fh, ensure_ascii=False, indent=2)
        else: raise ValueError('Acción no válida.')
        self.refresh()
        return {'ok': True}

    def cambiar_motivo(self, rid, motivo, nota, persona='', liq=None):
        if motivo not in MOTIVOS: raise ValueError('Motivo no válido.')
        try:
            with open(QUITADAS_FILE, encoding='utf-8') as f: previo = next((r.get('persona') or '' for r in json.load(f) if r.get('id') == rid), '')
        except (OSError, ValueError, AttributeError): previo = ''
        persona = self._persona(motivo, persona, previo=previo)
        with self.q_lock:
            try:
                with open(QUITADAS_FILE, encoding='utf-8') as f: q = json.load(f)
            except (OSError, ValueError): q = []
            for r in q:
                if r.get('id') == rid:
                    g = self._liq_guardar(motivo, liq, previo=r.get('liq'))
                    if g is None: r.pop('liq', None)
                    else: r['liq'] = g
                    r['motivo'] = motivo; r['nota'] = str(nota or '').strip()[:120]; r['persona'] = persona
                    r['editado'] = datetime.datetime.now().isoformat(timespec='seconds')
                    self._guardar_quitadas(q); break
            else: raise RuntimeError('No encontré ese registro.')
        self.refresh()
        return {'ok': True}

    def refresh(self):
        with self._ref_lock:   # 30.7.56: nunca dos actualizaciones al mismo tiempo (una vieja no tapa a una nueva)
            return self._refresh()

    def _refresh(self):
        t0 = time.time()
        self._touched = set()
        self.catalogs()
        idx = self.index_files()
        days = sorted({d for (pre, d) in idx if pre == 'P'})
        if not days: raise RuntimeError('No encontré tickets en ' + self.base)
        out = {'version': VERSION_TXT, 'build': VERSION, 'generado': datetime.datetime.now().isoformat(timespec='seconds'), 'desde': days[0].isoformat(), 'hasta': days[-1].isoformat(),
               'bitacora_hasta': self.cfg.get('bitacora_borrados_hasta')}
        try: out['abiertas'] = self.abiertas()
        except Exception as e: log('No pude leer cuentas abiertas: ' + str(e)); out['abiertas'] = []
        out['diario'] = [self.day(idx, d, False) for d in days]
        out['detalle'] = {d.isoformat(): self.day(idx, d, True) for d in days[-int(self.cfg['dias_detalle']):]}
        mon = collections.defaultdict(lambda: [0, 0, 0])
        for x in out['diario']: m = mon[x['fecha'][:7]]; m[0] += x['total']; m[1] += x['tickets']; m[2] += x['personas']
        out['mensual'] = [{'mes': k, 'total': round(v[0], 2), 'tickets': v[1], 'personas': v[2]} for k, v in sorted(mon.items())]
        try: out['mensual_prod'] = self.productos_mes(idx, days)
        except Exception as e: log('No pude resumir productos por mes: ' + str(e)); out['mensual_prod'] = {}
        out['clientes_nuevos'] = self.clientes_nuevos(); out['plano'] = self.plano_actual()
        try: out['tardadas'] = self.tiempos_lista()
        except Exception as e: log('No pude leer los tiempos de comanda: ' + str(e)); out['tardadas'] = []
        out['huellas'] = [{k: x.get(k, '') for k in ('id', 'nombre', 'rp', 'alta', 'ultimo', 'v')} for x in huellas()]; out['minutos_sesion'] = self.cfg.get('minutos_sesion', 30)
        out['quitadas'] = self.quitadas_liq(); out['motivos'] = MOTIVOS if modulos(self.cfg)['plataformas'] else [m for m in MOTIVOS if not m.startswith(('Didi', 'Uber'))]
        out['modulos'] = modulos(self.cfg); out['actualiza'] = self.cfg.get('actualizar_solo', True) is not False
        out['plataformas'] = {'config': self.plat_cfg(), 'pagos': self.plat_pagos()}; out['familia'] = self.familia()
        out['empleados'] = self.empleados(); out['empleados_puesto'] = self.empleados_nomina(); out['empleados_apodo'] = self.empleados_apodo()
        desde_ce = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        out['comidas_emp'] = [c for c in self.comidas_emp() if c.get('fecha', '') >= desde_ce]; out['comida_emp_tope'] = self.cfg.get('comida_emp_tope')
        out['gastos'] = self.gastos(); out['categorias_gasto'] = CATEGORIAS_GASTO
        out['cajeros'] = [{'id': x['id'], 'nombre': x['nombre'], 'activo': x.get('activo', True), 'alta': x.get('alta', '')} for x in self.cajeros()]
        try: ligados = {str(e.get('cod_mt') or '').strip() for e in self.rh()['empleados'] if e.get('cod_mt')}
        except Exception: ligados = set()
        out['meseros'] = [{'id': x['id'], 'cod': x.get('cod', ''), 'nombre': self.EMP.get(x.get('cod'), x['nombre']), 'activo': x.get('activo', True),
                           'en_mrtienda': x.get('cod') in self.EMP, 'alta': x.get('alta', ''), 'remoto': bool(x.get('remoto')), 'solo_huella': bool(x.get('solo_huella')), 'ligado': x.get('cod') in ligados, 'tiene_clave': bool(x.get('clave'))} for x in self.meseros()]
        out['candado_rol'] = bool(self.cfg.get('candado_rol'))
        out['comandero_escribe'] = bool(self.cfg.get('comandero_escribe'))
        out['emp_mrtienda'] = sorted([{'cod': k, 'nombre': v} for k, v in self.EMP.items() if v], key=lambda e: e['nombre'])
        out['ia'] = {'activa': bool(self.cfg.get('ia_llave')), 'modelo': self.cfg.get('ia_modelo') or IA_MODELO, 'estado': self.cfg.get('ia_estado') or {}}
        tok_ = str(self.cfg.get('github_token') or '').strip()
        out['github'] = {'activa': bool(tok_), 'termina': tok_[-4:] if tok_ else '', 'privado': self.cfg.get('github_privado'), 'vence': self.cfg.get('github_vence') or '',
                         'revision': ESTADO_ACT.get('t', ''), 'revision_ok': ESTADO_ACT.get('ok'), 'revision_error': ESTADO_ACT.get('error', '')}
        out['drive'] = {'conectado': bool(self.cfg.get('drive_url')), 'mensual': bool(self.cfg.get('drive_mensual')), 'ultimo_borrado': self.cfg.get('drive_ultimo_borrado') or ''}
        # suelta de la memoria los archivos que ya no se leyeron en esta vuelta
        with self.rows_lock:
            vivos = self._touched
            self.cache = {k: v for k, v in list(self.cache.items()) if k[0] in vivos}
        data = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        gz = gzip.compress(data, 6)
        with self.lock: self.json = data; self.jsongz = gz; self.error = None
        log(f'Actualizado: {len(days)} días, hoy {out["detalle"][days[-1].isoformat()]["total"]:.2f}, {time.time()-t0:.1f}s')

    # ------------------------------------------------ copia automática en Google Drive
    def carpeta_copia(self):
        c = self.cfg.get('carpeta_copia')
        if c: return c if os.path.isdir(os.path.dirname(c.rstrip('\\/')) or c) else None
        if os.name != 'nt': return None
        cands = []
        for L in 'GHIJKLMNOPQRSTUVWXYZDEF':
            for n in ('Mi unidad', 'My Drive'): cands.append(f'{L}:\\{n}')
        home = os.path.expanduser('~')
        for n in ('Google Drive\\Mi unidad', 'Google Drive\\My Drive', 'Google Drive', 'Mi unidad', 'My Drive'): cands.append(os.path.join(home, n))
        for c in cands:
            if os.path.isdir(c): return os.path.join(c, 'La Holandesa reportes')
        return None

    def guardar_copia(self, forzar=False):
        dest = self.carpeta_copia()
        if not dest:
            if not getattr(self, '_aviso_drive', False): log('No encontré Google Drive en esta computadora; no se guarda copia.'); self._aviso_drive = True
            return
        firma = hashlib.md5(self.json).hexdigest()
        ncortes = self.json.count(b'"folz":"') - self.json.count(b'"folz":""')
        ahora = time.time()
        if not forzar and firma == getattr(self, '_firma', None): return
        if not forzar and ahora - getattr(self, '_ult_copia', 0) < 600 and ncortes == getattr(self, '_ncortes', -1): return
        try:
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(AQUI, 'panel.html'), 'r', encoding='utf-8') as f: page = f.read()
            datos = self.json.decode('utf-8').replace('</', '<\\/')
            page = page.replace('</head>', '<script>window.__COPIA__=' + datos + ';</script></head>', 1)
            final = os.path.join(dest, 'La Holandesa - reportes.html'); tmp = final + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: f.write(page)
            os.replace(tmp, final)
            self._firma, self._ult_copia, self._ncortes = firma, ahora, ncortes
        except Exception as e:
            log('No pude guardar la copia en Drive: ' + str(e))

    def loop(self):
        while True:
            try: self.refresh(); self.guardar_copia(); self.borrado_mensual()
            except Exception as e:
                self.error = str(e); log('ERROR ' + traceback.format_exc())
            time.sleep(max(30, float(self.cfg['minutos_actualizacion']) * 60))

# ---------------------------------------------------------------- servidor web
ULTIMA_PAGINA = {}   # qué app se abrió al último (para elegir el favicon cuando el navegador no dice de dónde viene)
def make_handler(mon, cfg):
    token = base64.b64encode(f"{cfg['usuario']}:{cfg['contrasena']}".encode()).decode()
    token_caja = base64.b64encode(f"{cfg['usuario_caja']}:{cfg['contrasena_caja']}".encode()).decode()
    def ips_locales():
        import socket
        out = []
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if not ip.startswith('127.'): out.append(ip)
        except OSError: pass
        try:
            s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s_.connect(('10.255.255.255', 1)); ip = s_.getsockname()[0]; s_.close()
            if ip not in out and not ip.startswith('127.'): out.append(ip)
        except OSError: pass
        return out
    with open(os.path.join(AQUI, 'panel.html'), 'rb') as f: page = f.read()
    icon_path = os.path.join(AQUI, 'icon.png')
    try:
        with open(icon_path, 'rb') as f: icon_png = f.read()
    except OSError:
        icon_png = b''
    manifest_caja = json.dumps({'id':'/captura','name':'Compras Holandesa','short_name':'Compras','start_url':'/captura','scope':'/captura','display':'standalone','background_color':'#fbf6f1','theme_color':'#ea580c','icons':[{'src':'/icono_panel_192.png','sizes':'192x192','type':'image/png','purpose':'any'},{'src':'/icono_panel_512.png','sizes':'512x512','type':'image/png','purpose':'any'}]}, ensure_ascii=False).encode('utf-8')
    def _leer(nombre):
        try:
            with open(os.path.join(AQUI, nombre), 'rb') as f: return f.read()
        except OSError: return b''
    page_cmd = _leer('comandero.html')
    logo_cmd = _leer('logo_comandero.png')
    iconos_cmd = {n: _leer(n) for n in ('icono_comandero_180.png', 'icono_comandero_192.png', 'icono_comandero_512.png', 'icono_panel_180.png', 'icono_panel_192.png', 'icono_panel_512.png')}
    icon_rh = _leer('icon_personal.png')
    page_rol = _leer('descansos.html')
    page_entrar = _leer('entrar.html')
    manifest_rol = json.dumps({'id':'/descansos','name':'Descansos La Holandesa','short_name':'Descansos','start_url':'/descansos','scope':'/descansos','display':'standalone','background_color':'#f3f6f2','theme_color':'#1d5c3b','icons':[{'src':'/icon_personal.png','sizes':'512x512','type':'image/png'}]}, ensure_ascii=False).encode('utf-8')
    manifest_rh = json.dumps({'id':'/personal','name':'Personal Holandesa','short_name':'Personal','start_url':'/personal','scope':'/personal','display':'standalone','background_color':'#f3f6f2','theme_color':'#1d5c3b','icons':[{'src':'/icon_personal.png','sizes':'512x512','type':'image/png'}]}, ensure_ascii=False).encode('utf-8')
    manifest_cmd = json.dumps({'id':'/comandero','name':'La Holandesa Comandero','short_name':'Holandesa Comandero','start_url':'/comandero','scope':'/comandero','display':'standalone','background_color':'#ffffff','theme_color':'#2f6db3','icons':[{'src':'/icono_comandero_192.png','sizes':'192x192','type':'image/png','purpose':'any'},{'src':'/icono_comandero_512.png','sizes':'512x512','type':'image/png','purpose':'any'}]}, ensure_ascii=False).encode('utf-8')
    manifest = json.dumps({'id':'/panel','name':'Control Holandesa','short_name':'Control Holandesa','start_url':'/panel','scope':'/panel','display':'standalone','background_color':'#fbf6f1','theme_color':'#ea580c','icons':[{'src':'/icono_panel_192.png','sizes':'192x192','type':'image/png','purpose':'any'},{'src':'/icono_panel_512.png','sizes':'512x512','type':'image/png','purpose':'any'}]}, ensure_ascii=False).encode('utf-8')
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _host(self):
            h = (self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or '').split(',')[0].strip()
            h = h.rsplit(':', 1)[0].lower().rstrip('.') if not h.startswith('[') else h
            # si Tailscale entrega la petición como 127.0.0.1, el nombre real es el de la dirección segura
            if h in ('127.0.0.1', 'localhost') and HTTPS_TS.get('url') and (self.headers.get('Tailscale-User-Login') or self.headers.get('X-Forwarded-Proto') == 'https'):
                h = HTTPS_TS['url'].split('://', 1)[-1].rstrip('/').lower()
            return h
        def _cookie(self, nombre):
            for parte in (self.headers.get('Cookie') or '').split(';'):
                k, _, v = parte.strip().partition('=')
                if k == nombre: return v
            return ''
        def _min_sesion(self):
            try: return max(5, int(cfg.get('minutos_sesion', 30)))
            except (TypeError, ValueError): return 30
        def _es_admin(self):
            if sesion_admin(self._cookie('hol_s'), self._min_sesion()): return True
            a = self.headers.get('Authorization', '')   # la ventanita del navegador ya no se pide; solo si se activa en config.json
            return bool(cfg.get('acepta_basic')) and secrets.compare_digest(a, 'Basic ' + token)
        def _json(self, obj, status=200, cookie=None):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Cache-Control', 'no-store')
            if cookie is not None:
                seguro = '; Secure' if self._host().endswith('.ts.net') else ''
                self.send_header('Set-Cookie', f'hol_s={cookie}; Path=/; HttpOnly; SameSite=Lax{seguro}' + ('; Max-Age=0' if not cookie else ''))
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def _auth(self, caja_ok=False):
            if self._es_admin(): self.rol = 'admin'; self.quien = 'Jorge'; return True
            if caja_ok:
                n_ = sesion(self.headers.get('X-Caja-Token'))
                if n_: self.rol = 'caja'; self.quien = n_; return True
                body = b'{"ok":false,"error":"Tu sesion termino. Vuelve a entrar con tu clave.","sesion":false}'
                self.send_response(401); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                return False
            self._json({'ok': False, 'error': 'Tu sesión terminó. Vuelve a entrar.', 'login': True}, 401)
            return False
        def _rh_mandar(self, hojas, formato, nombre):
            import urllib.parse
            dr = cfg.get('direccion_nomina') or RH_EXPORT_DIR; pt = cfg.get('patron_nomina') or RH_EXPORT_PATRON
            nom = re.sub(r'[^0-9A-Za-z -]', '-', nombre).strip()
            if formato == 'pdf': body = pdf_libro(hojas, dr, pt, nombre); ct = 'application/pdf'; nom += '.pdf'
            else: body = xlsx_libro(hojas, dr, pt); ct = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; nom += '.xlsx'
            self.send_response(200); self.send_header('Content-Type', ct); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Disposition', "attachment; filename=\"archivo%s\"; filename*=UTF-8''%s" % (nom[-5:] if nom.endswith('.xlsx') else '.pdf', urllib.parse.quote(nom)))
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def _send(self, body, ctype, gz=False):
            self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
            if gz: self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def _apagada(self):
            if not ruta_apagada(cfg, self.path): return False
            body = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>No activado</title><body style="font:16px system-ui;padding:24px;background:#fbf6f1;color:#1f1a17"><h2 style="color:#c2410c">Esta parte no está activada</h2><p>En este negocio solo está activado el panel de ventas.</p><p><a href="/panel" style="color:#c2410c">Ir al panel</a></p>'.encode('utf-8')
            self.send_response(404); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            return True

        def do_POST(self):
            self._por_tailscale_serve()
            if self._apagada(): return
            if self.path in ('/api/entrar', '/api/huella/reto', '/api/huella/entrar', '/api/huella/registrar', '/api/huella/quitar', '/api/salir'):
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 16384)
                    d = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
                    ip = 'adm:' + self.client_address[0]; ahora = time.time(); f = FALLOS.get(ip, [0, 0])
                    if self.path in ('/api/entrar', '/api/huella/entrar') and f[1] > ahora: raise RuntimeError('Demasiados intentos. Espera unos minutos.')
                    if self.path == '/api/entrar':
                        # 30.7.58: desde el WiFi del restaurante solo con huella; la contraseña sirve en la computadora de la caja y por Tailscale
                        ip_ = self.client_address[0]
                        if (huellas() and not cfg.get('clave_por_wifi') and ip_del_restaurante(ip_) and not ip_.startswith('127.') and ip_ not in ('::1',)
                                and ip_ not in ips_locales()):
                            raise RuntimeError('Desde el WiFi del restaurante el panel solo abre con la huella de Jorge, en la dirección segura. La contraseña sirve en la computadora de la caja.')
                        ok = secrets.compare_digest(str(d.get('usuario') or '').strip().lower(), str(cfg['usuario']).lower()) & secrets.compare_digest(str(d.get('contrasena') or ''), str(cfg['contrasena']))
                        if not ok:
                            f[0] += 1
                            if f[0] >= 5: f = [0, ahora + 300]
                            FALLOS[ip] = f; raise RuntimeError('Usuario o contraseña incorrectos.')
                        FALLOS.pop(ip, None); return self._json({'ok': True}, cookie=nueva_sesion_admin(self._min_sesion()))
                    if self.path == '/api/huella/reto':
                        host = self._host(); ids = [x['id'] for x in huellas() if x.get('rp') == host]
                        if d.get('modo') == 'registrar':
                            if not self._es_admin(): return self._json({'ok': False, 'error': 'Entra primero con tu contraseña.', 'login': True}, 401)
                            if not cfg.get('huella_uid'):
                                cfg['huella_uid'] = b64u(secrets.token_bytes(16))
                                with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(cfg, fh, ensure_ascii=False, indent=2)
                            return self._json({'ok': True, 'reto': nuevo_reto('webauthn.create'), 'uid': cfg['huella_uid'], 'usuario': cfg['usuario'], 'ids': ids})
                        if not ids: raise RuntimeError('Todavía no hay huella activada en esta dirección.')
                        return self._json({'ok': True, 'reto': nuevo_reto('webauthn.get'), 'ids': ids})
                    if self.path == '/api/huella/entrar':
                        try: entrar_huella(d, self._host())
                        except Exception:
                            f[0] += 1
                            if f[0] >= 5: f = [0, ahora + 300]
                            FALLOS[ip] = f; raise
                        FALLOS.pop(ip, None); return self._json({'ok': True}, cookie=nueva_sesion_admin(self._min_sesion()))
                    if self.path == '/api/salir':
                        SES_ADMIN.pop(self._cookie('hol_s'), None); _ses_guardar(True); return self._json({'ok': True}, cookie='')
                    if not self._es_admin(): return self._json({'ok': False, 'error': 'Tu sesión terminó. Vuelve a entrar.', 'login': True}, 401)
                    if self.path == '/api/huella/registrar': out = registrar_huella(d, self._host())
                    else:
                        guardar_huellas([x for x in huellas() if x.get('id') != d.get('id')]); log('Huella quitada: ' + str(d.get('id'))[:12]); out = {'ok': True}
                    mon.refresh(); return self._json(out)
                except Exception as e:
                    log('No se pudo completar ' + self.path + ': ' + str(e))
                    return self._json({'ok': False, 'error': str(e)}, 403)
            if self.path == '/api/caja-login':
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 2048)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    body = json.dumps(mon.login_cajero(d.get('pin'), self.client_address[0]), ensure_ascii=False).encode('utf-8')
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    body = json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(403); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path in ('/api/comandero/huella-reto', '/api/comandero/huella-entrar'):   # 30.7.58
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 16384)
                    d = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
                    host = self._host()
                    if self.path.endswith('reto'):
                        if not any(x.get('activo') and x.get('solo_huella') for x in mon.meseros()): raise RuntimeError('Ningún usuario del comandero entra con huella.')
                        ids = [x['id'] for x in huellas() if x.get('rp') == host]
                        if not ids: raise RuntimeError('La huella solo funciona en la dirección segura, con la huella ya activada en el panel.')
                        out = {'ok': True, 'reto': nuevo_reto('webauthn.get'), 'ids': ids}
                    else: out = mon.login_mesero_huella(d, host, self.client_address[0])
                    return self._send(json.dumps(out, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
                except Exception as e:
                    body = json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(403); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path == '/api/mesero-login':
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 2048)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    body = json.dumps(mon.login_mesero(d.get('pin'), self.client_address[0]), ensure_ascii=False).encode('utf-8')
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    body = json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(403); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path == '/api/rol-export':   # la encargada comparte o imprime el concentrado de descansos (sin dinero)
                try:
                    if not mon.rol_de(self.headers.get('X-Rol-Token')): raise PermissionError('Tu sesión terminó. Vuelve a entrar con tu clave.')
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 4096)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    return self._rh_mandar([rh_hoja_descansos(mon.rh(), d.get('semana_iso') or datetime.date.today().isoformat())], d.get('formato'), 'Descansos ' + str(d.get('archivo') or ''))
                except Exception as e:
                    if not isinstance(e, PermissionError): log('Exportar descansos: ' + traceback.format_exc())
                    return self._json({'ok': False, 'error': str(e), 'sesion': not isinstance(e, PermissionError)}, 409)
            if self.path in ('/api/rol-login', '/api/rol'):
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 8192)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    if self.path == '/api/rol-login': out = mon.login_rol(d.get('pin'), self.client_address[0])
                    else:
                        q_ = mon.rol_de(self.headers.get('X-Rol-Token'))
                        if not q_: raise PermissionError('Tu sesión terminó. Vuelve a entrar con tu clave.')
                        out = mon.rol_accion_enc(q_, d)
                    return self._send(json.dumps(out, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
                except Exception as e:
                    if not isinstance(e, (ValueError, PermissionError, RuntimeError)): log('Rol de descansos: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': str(e), 'sesion': not isinstance(e, PermissionError)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(409); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path in ('/api/comandero/cliente-aplicar', '/api/comandero/leer-constancia', '/api/comandero/cliente-nuevo', '/api/comandero/factura-resp', '/api/comandero/leer-recibo', '/api/comandero/ce-validar'):   # factura (30.7.12); comida de empleados (30.7.55)
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                try:
                    if m_ and m_.get('bloqueo'): raise PermissionError(m_['bloqueo'])
                    if not m_: raise PermissionError('Tu sesion termino. Vuelve a entrar con tu clave.')
                    n = int(self.headers.get('Content-Length', '0') or 0)
                    if n > 8 * 1024 * 1024: raise ValueError('La foto es demasiado grande.')
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    if self.path.endswith('aplicar'): out = mon.cliente_aplicar(m_, d)
                    elif self.path.endswith('constancia'): out = mon.leer_constancia(str(d.get('foto') or ''))
                    elif self.path.endswith('leer-recibo'): out = mon.leer_recibo_plat(str(d.get('foto') or ''))
                    elif self.path.endswith('ce-validar'): out = mon.ce_validar(d)
                    elif self.path.endswith('factura-resp'):
                        if not any(a['archivo'] == str(d.get('k') or '') and (a['cod'] == m_['cod'] or m_.get('solo_huella')) for a in mon.abiertas()): raise RuntimeError('Esa cuenta ya no está abierta o es de otro mesero.')
                        out = mon.factura_resp(str(d.get('k')), d.get('factura'))
                    else: out = mon.cliente_nuevo(m_, d)
                    return self._send(json.dumps(out, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
                except Exception as e:
                    if not isinstance(e, (ValueError, PermissionError, RuntimeError)): log('Comandero, factura: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': str(e), 'sesion': not isinstance(e, PermissionError)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(409); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path in ('/api/comandero/tiempo', '/api/comandero/tarde'):   # alarma de 14/20 min y "Ya salió" → reporte de comandas tardadas
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                try:
                    if not m_ or m_.get('bloqueo'): raise PermissionError('sin sesion')
                    d = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', '0') or 0), 4096)).decode('utf-8'))
                    d.setdefault('ev', 'alarma'); mon.comanda_tiempo(d, m_)
                except Exception as e: log('Tiempos de comanda: ' + str(e))
                return self._send(b'{"ok":true}', 'application/json')
            if self.path == '/api/comandero/enviar':
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                try:
                    if m_ and m_.get('bloqueo'): raise PermissionError(m_['bloqueo'])
                    if not m_: raise PermissionError(SESION_FIN)
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 65536)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    # la clave se pide en cada envío: así nadie manda comandas con el teléfono de otro mesero
                    k_ = 'e:' + self.client_address[0]; f_ = FALLOS.get(k_, [0, 0])
                    if f_[1] > time.time(): raise ValueError('Demasiados intentos. Espera unos minutos.')
                    x_ = next((x for x in mon.meseros() if x.get('id') == m_['id'] and x.get('activo')), None)
                    tok_ = self.headers.get('X-Mesero-Token') or ''
                    if x_ and x_.get('solo_huella'):
                        if time.time() - HUELLA_M.get(tok_, 0) > 900: raise ValueError('Pon tu huella otra vez para mandar la comanda.')
                    elif not x_ or not verifica_pin(x_, str(d.get('pin') or '')):
                        f_[0] += 1
                        if f_[0] >= 5: f_ = [0, time.time() + 300]
                        FALLOS[k_] = f_
                        raise ValueError('Clave incorrecta. La comanda no se envió.')
                    FALLOS.pop(k_, None)
                    body = json.dumps(mon.enviar_comandero(m_, d), ensure_ascii=False).encode('utf-8')
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    if not isinstance(e, (ValueError, PermissionError)): log('Comandero, no se pudo enviar: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': str(e), 'sesion': not isinstance(e, PermissionError)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(409); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if not self._auth(caja_ok=(self.path in ('/api/compra', '/api/leer-ticket'))): return
            if self.path in ('/api/rh-export', '/api/rh-firma'):   # nómina completa, hoja para firmar, pago o descansos, en Excel o PDF
                if self.headers.get('X-Holandesa-Action') != '1': self.send_response(403); self.end_headers(); return
                try:
                    n = min(int(self.headers.get('Content-Length', '0') or 0), 524288)
                    d = json.loads(self.rfile.read(n).decode('utf-8'))
                    tipo = d.get('tipo') or 'firma'; hojas = []
                    if tipo in ('completa', 'firma'): hojas.append(rh_hoja_firma(d.get('firma') or d))
                    if tipo in ('completa', 'pago'): hojas.append(rh_hoja_pago(d.get('pago') or {}))
                    if tipo in ('completa', 'descansos'): hojas.append(rh_hoja_descansos(mon.rh(), d.get('semana_iso') or datetime.date.today().isoformat()))
                    return self._rh_mandar(hojas, d.get('formato'), {'completa': 'Nomina completa', 'firma': 'Nomina para firmar', 'pago': 'Pago de nomina', 'descansos': 'Descansos'}.get(tipo, 'Nomina') + ' ' + str(d.get('archivo') or ''))
                except Exception as e:
                    log('Exportar nómina: ' + traceback.format_exc())
                    return self._json({'ok': False, 'error': 'No pude armar el archivo: ' + str(e)}, 409)
            if self.path not in ('/api/eliminar-abierta', '/api/quitada-motivo', '/api/gastos', '/api/compra', '/api/leer-ticket', '/api/cajeros', '/api/meseros', '/api/rh', '/api/rh-archivo', '/api/comandero-envio', '/api/comanda-formato', '/api/log-cliente', '/api/clientes-nuevos', '/api/plano', '/api/leer-recibo', '/api/ia', '/api/leer-pendientes', '/api/drive', '/api/familia', '/api/github', '/api/plataformas', '/api/comida-emp', '/api/clave-panel'): self.send_response(404); self.end_headers(); return
            if not self.headers.get('Content-Type','').lower().startswith('application/json') or self.headers.get('X-Holandesa-Action') != '1':
                self.send_response(403); self.end_headers(); return
            try:
                lim = 17 * 1024 * 1024 if self.path == '/api/rh-archivo' else 9 * 1024 * 1024 if self.path in ('/api/compra', '/api/gastos', '/api/leer-ticket', '/api/leer-recibo') else 262144
                n=int(self.headers.get('Content-Length','0') or 0)
                if n > lim: raise ValueError('La foto es demasiado grande.')
                d=json.loads(self.rfile.read(n).decode('utf-8'))
                if self.path == '/api/compra': out=mon.captura(d, 'caja' if self.rol == 'caja' else 'admin', self.quien)
                elif self.path == '/api/leer-ticket': out={'ok': True, 'lectura': mon.leer_ticket(d.get('foto') or '')}
                elif self.path == '/api/cajeros': out=mon.cajero_accion(d)
                elif self.path == '/api/meseros': out=mon.mesero_accion(d)
                elif self.path == '/api/rh': out=mon.rh_accion(d)
                elif self.path == '/api/rh-archivo': out=mon.rh_archivo_subir(d)
                elif self.path == '/api/comandero-envio': out=mon.comandero_interruptor(d)
                elif self.path == '/api/comanda-formato': out=mon.comanda_formato(d)
                elif self.path == '/api/clientes-nuevos': out = mon.clientes_nuevos_accion(d)
                elif self.path == '/api/plano': out = mon.plano_accion(d); mon.refresh()
                elif self.path == '/api/leer-recibo': out = mon.leer_recibo_plat(str(d.get('foto') or ''))
                elif self.path == '/api/log-cliente': log('Teléfono (' + str(d.get('que', ''))[:40] + '): ' + str(d.get('error', ''))[:200] + ' · ' + str(d.get('ua', ''))[:160]); out = {'ok': True}
                elif self.path == '/api/leer-pendientes': out=mon.leer_pendientes()
                elif self.path == '/api/drive':
                    acc = d.get('accion')
                    if acc == 'guardar':
                        url = str(d.get('url') or '').strip()
                        if not re.match(r'^https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec$', url): raise ValueError('Esa no parece la dirección de la aplicación web (debe empezar con https://script.google.com/macros/s/ y terminar en /exec).')
                        cfg['drive_url'] = url
                        try: mon._drive({'accion': 'probar'})
                        except Exception: cfg.pop('drive_url', None); raise
                    elif acc == 'quitar': cfg.pop('drive_url', None)
                    elif acc == 'mensual': cfg['drive_mensual'] = bool(d.get('activo'))
                    elif acc == 'borrar': out = mon.borrar_fotos_drive()
                    else: raise ValueError('Acción no válida.')
                    if acc != 'borrar':
                        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
                        mon.cfg = cfg; mon.refresh(); out = {'ok': True}
                elif self.path == '/api/ia':
                    llave = re.sub(r'\s+', '', str(d.get('llave') or ''))
                    out = {'ok': True}
                    if d.get('quitar'): cfg.pop('ia_llave', None); cfg.pop('ia_estado', None)
                    else:
                        if not llave and d.get('probar') and cfg.get('ia_llave'): llave = cfg['ia_llave']   # "Probar otra vez" con la guardada
                        if not llave.startswith('sk-'): raise ValueError('Esa no parece una llave de Claude (empieza con sk-ant-). Cópiala completa.')
                        est, msg = mon.probar_ia(llave)
                        if est == 'mala': raise ValueError(msg)   # una llave mala no se guarda
                        cfg['ia_llave'] = llave
                        cfg['ia_estado'] = {'estado': est, 'msg': msg, 'fecha': datetime.datetime.now().isoformat(timespec='minutes'), 'fin': llave[-4:]}
                        out = {'ok': True, 'estado': est, 'mensaje': msg}
                    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
                    mon.cfg = cfg; mon.refresh()
                elif self.path == '/api/github':   # 30.7.39: llave de solo lectura para que la caja se actualice con el repositorio privado
                    if d.get('quitar'):
                        for k_ in ('github_token', 'github_privado', 'github_vence'): cfg.pop(k_, None)
                        out = {'ok': True}
                    else:
                        llave = re.sub(r'\s+', '', str(d.get('llave') or ''))
                        if not llave and cfg.get('github_token'): llave = cfg['github_token']   # "Probar otra vez" con la que ya está guardada
                        if not re.fullmatch(r'(github_pat_|ghp_)[A-Za-z0-9_]{20,255}', llave): raise ValueError('Esa no parece una llave de GitHub. Debe empezar con github_pat_ y ser un texto largo, sin espacios.')
                        r_ = github_probar(llave)
                        cfg['github_token'] = llave; cfg['github_privado'] = r_['privado']; cfg['github_vence'] = r_['vence']
                        out = {'ok': True, **r_}
                        log('Llave de GitHub guardada y probada' + (' (repositorio privado)' if r_['privado'] else ' (repositorio público)'))
                    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
                    mon.cfg = cfg; mon.refresh()
                elif self.path == '/api/gastos': out=mon.gasto_accion(d)
                elif self.path == '/api/quitada-motivo': out=mon.cambiar_motivo(d.get('id'),d.get('motivo'),d.get('nota'),d.get('persona'),d.get('liq'))
                elif self.path == '/api/plataformas': out=mon.plataformas_accion(d)
                elif self.path == '/api/comida-emp': out=mon.comida_emp_accion(d)
                elif self.path == '/api/clave-panel':   # 30.7.58: Jorge cambia su contraseña del panel
                    if not secrets.compare_digest(str(d.get('actual') or ''), str(cfg['contrasena'])): raise ValueError('La contraseña actual no es correcta.')
                    nueva = str(d.get('nueva') or '')
                    if len(nueva) < 8 or re.search(r'\s', nueva): raise ValueError('La nueva contraseña debe tener al menos 8 caracteres, sin espacios.')
                    if not (re.search(r'[A-Za-z]', nueva) and re.search(r'\d', nueva)): raise ValueError('Usa letras y números en la nueva contraseña.')
                    if nueva == cfg['contrasena']: raise ValueError('La nueva contraseña es igual a la de antes.')
                    cfg['contrasena'] = nueva; mon.cfg['contrasena'] = nueva
                    with open(CONFIG_FILE, 'w', encoding='utf-8') as fh: json.dump(cfg, fh, ensure_ascii=False, indent=2)
                    actual_ = self._cookie('hol_s')
                    for k_ in [k_ for k_ in SES_ADMIN if k_ != actual_]: SES_ADMIN.pop(k_, None)   # los demás que hayan entrado salen
                    _ses_guardar(True); log('Se cambió la contraseña del panel; se cerraron las demás sesiones.')
                    out = {'ok': True}
                elif self.path == '/api/familia': out=mon.familia_accion(d)
                else: out=mon.eliminar_abierta(d.get('archivo'),d.get('referencia'),d.get('motivo') or 'Sin clasificar',d.get('nota'),d.get('persona'),d.get('liq'))
                body=json.dumps(out,ensure_ascii=False).encode('utf-8')
                return self._send(body,'application/json; charset=utf-8')
            except Exception as e:
                log('No se pudo completar '+self.path+': '+str(e))
                body=json.dumps({'ok':False,'error':str(e)},ensure_ascii=False).encode('utf-8')
                self.send_response(409); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def _por_tailscale_serve(self):
            # tailscale serve entrega la petición desde 127.0.0.1; sin esto parecería la misma computadora (WiFi)
            if self.client_address[0] not in ('127.0.0.1', '::1'): return   # 30.7.58: alguien del WiFi no puede hacerse pasar por la dirección segura
            if self.headers.get('Tailscale-User-Login') or self.headers.get('Host', '').split(':')[0].lower().rstrip('.').endswith('.ts.net'):
                self.client_address = ('100.64.0.1', 0)
        def do_GET(self):
            self._por_tailscale_serve()
            if self._apagada(): return
            p0 = self.path.split('?')[0]
            if p0 in ('/captura', '/captura/'):
                return self._send(page, 'text/html; charset=utf-8')   # la página no trae datos; pide la clave del cajero
            if p0 == '/api/captura-info':
                if not self._auth(caja_ok=True): return
                info = mon.captura_info(); info['nombre'] = self.quien
                if self.rol == 'caja': info['hoy_lista'] = [x for x in info['hoy_lista'] if x.get('quien') == self.quien] or info['hoy_lista']
                body = json.dumps(info, ensure_ascii=False).encode('utf-8')
                return self._send(body, 'application/json; charset=utf-8')
            if p0 in ('/comandero', '/comandero/'):
                ULTIMA_PAGINA.update(p='cmd', t=time.time())
                if not page_cmd: self.send_response(404); self.end_headers(); return
                return self._send(page_cmd, 'text/html; charset=utf-8')   # la página no trae datos; pide la clave del mesero
            if p0 == '/comandero-sw.js':   # service worker mínimo: Chrome lo pide para ofrecer "Instalar app"
                sw = (b"self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
                      b"self.addEventListener('fetch',e=>{if(e.request.mode==='navigate')e.respondWith(fetch(e.request).catch(()=>new Response('<h2 style=\"font-family:sans-serif\">Sin conexi&oacute;n con la caja</h2><p style=\"font-family:sans-serif\">Revisa el WiFi del restaurante o Tailscale.</p>',{headers:{'Content-Type':'text/html'}})))});")
                self.send_response(200); self.send_header('Content-Type', 'application/javascript'); self.send_header('Cache-Control', 'no-cache')
                self.send_header('Service-Worker-Allowed', '/comandero'); self.send_header('Content-Length', str(len(sw))); self.end_headers(); self.wfile.write(sw); return
            if p0 == '/manifest-comandero.webmanifest':
                return self._send(manifest_cmd, 'application/manifest+json; charset=utf-8')
            if p0 in ('/descansos', '/descansos/'):
                if not page_rol: self.send_response(404); self.end_headers(); return
                return self._send(page_rol, 'text/html; charset=utf-8')   # la página no trae datos; pide la clave de la encargada
            if p0 == '/manifest-descansos.webmanifest':
                return self._send(manifest_rol, 'application/manifest+json; charset=utf-8')
            if p0 == '/api/rol-info':
                q_ = mon.rol_de(self.headers.get('X-Rol-Token'))
                if not q_:
                    body = b'{"ok":false,"sesion":false,"error":"Tu sesion termino. Vuelve a entrar con tu clave."}'
                    self.send_response(401); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
                import urllib.parse
                sem = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('semana') or [''])[0]
                out = mon.rol_info(sem); out['nombre'] = q_
                return self._send(json.dumps(out, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            if p0 == '/manifest-personal.webmanifest':
                return self._send(manifest_rh, 'application/manifest+json; charset=utf-8')
            if p0 == '/icon_personal.png' and icon_rh:
                return self._send(icon_rh, 'image/png')
            if p0 == '/favicon.ico':   # el navegador lo pide sin decir de qué app: por Referer o por la última página que se abrió
                ref = self.headers.get('Referer') or ''
                cmd_ = '/comandero' in ref or (not ref and ULTIMA_PAGINA.get('p') == 'cmd' and time.time() - ULTIMA_PAGINA.get('t', 0) < 300)
                p0 = '/icono_comandero_192.png' if cmd_ else '/icono_panel_192.png'
            if p0 in ('/apple-touch-icon.png', '/apple-touch-icon-precomposed.png', '/apple-touch-icon-180x180.png', '/apple-touch-icon-180x180-precomposed.png'):
                # algunos iPhone piden el ícono en la raíz aunque la página diga cuál es: por Referer o por la última página abierta
                ref = self.headers.get('Referer') or ''
                cmd_ = '/comandero' in ref or (not ref and ULTIMA_PAGINA.get('p') == 'cmd' and time.time() - ULTIMA_PAGINA.get('t', 0) < 300)
                p0 = '/icono_comandero_180.png' if cmd_ else '/icono_panel_180.png'
            if p0.lstrip('/') in iconos_cmd and iconos_cmd[p0.lstrip('/')]:
                b_ = iconos_cmd[p0.lstrip('/')]   # íconos: sin contraseña y con caché, como los piden los navegadores para la pantalla de inicio
                self.send_response(200); self.send_header('Content-Type', 'image/png'); self.send_header('Cache-Control', 'public, max-age=86400')
                self.send_header('Content-Length', str(len(b_))); self.end_headers(); self.wfile.write(b_); return
            if p0 == '/logo_comandero.png' and logo_cmd:
                return self._send(logo_cmd, 'image/png')
            if p0 == '/api/comandero/clientes-buscar':
                import urllib.parse
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                try:
                    if not m_ or m_.get('bloqueo'): raise PermissionError('Tu sesión terminó. Vuelve a entrar con tu clave.')
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    body = json.dumps(mon.clientes_buscar((q.get('q') or [''])[0]), ensure_ascii=False).encode('utf-8')
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    if not isinstance(e, (ValueError, PermissionError, RuntimeError)): log('Comandero, buscar clientes: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(400); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if p0 == '/api/comandero/clientes':
                import urllib.parse
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                try:
                    if not m_ or m_.get('bloqueo'): raise PermissionError('Tu sesión terminó. Vuelve a entrar con tu clave.')
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    body = json.dumps(mon.clientes_por_tel((q.get('tel') or [''])[0]), ensure_ascii=False).encode('utf-8')
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    if not isinstance(e, (ValueError, PermissionError)): log('Comandero, clientes: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(400); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if p0 == '/api/comandero':
                m_ = mon.mesero_de(self.headers.get('X-Mesero-Token'), self.client_address[0])
                if not m_ or m_.get('bloqueo'):
                    body = json.dumps({'ok': False, 'sesion': False, 'error': m_['bloqueo'] if m_ else SESION_FIN}, ensure_ascii=False).encode('utf-8')
                    self.send_response(401); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
                try:
                    # 30.7.76: si una parte falla, las demás siguen; el comandero muestra el aviso pero deja trabajar
                    avisos = []
                    def parte(nom, fn, defecto):
                        try: return fn()
                        except Exception as e_:
                            log('Comandero (' + nom + '): ' + traceback.format_exc()); avisos.append(nom + ': ' + str(e_)[:160]); return defecto
                    out = {'ok': True, 'mesero': m_, 'hora': datetime.datetime.now().strftime('%H:%M'), 'escribe': bool(mon.cfg.get('comandero_escribe'))}
                    out['cuentas'] = parte('cuentas abiertas de MrTienda', mon.cuentas_comandero, [])
                    out['plataformas'] = parte('Didi y Uber', mon.plat_cfg, {})
                    out['empleados_ce'] = parte('comida de empleados', mon.empleados_ce, []) if mon.cfg.get('comida_emp', True) else []
                    if 'menu=1' in self.path: out['menu'] = parte('menú de MrTienda', mon.menu_comandero, {'cats': []})
                    out['plano'] = parte('mapa', mon.plano_actual, mon.PLANO_DEF)
                    if avisos: out['aviso'] = 'Problema al leer ' + '; '.join(avisos) + '. Avísale a Jorge.'
                    body = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                    if 'gzip' in self.headers.get('Accept-Encoding', ''): return self._send(gzip.compress(body, 6), 'application/json; charset=utf-8', True)
                    return self._send(body, 'application/json; charset=utf-8')
                except Exception as e:
                    log('Comandero: ' + traceback.format_exc())
                    body = json.dumps({'ok': False, 'error': 'No pude leer MrTienda: ' + str(e)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(500); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if p0 in ('/manifest-caja.webmanifest',):
                return self._send(manifest_caja, 'application/manifest+json; charset=utf-8')
            if p0 == '/manifest.webmanifest':
                return self._send(manifest, 'application/manifest+json; charset=utf-8')
            if p0 == '/icon.png' and icon_png and self._es_admin():
                return self._send(icon_png, 'image/png')
            if p0 == '/api/entrar-info':
                host = self._host()
                return self._json({'ok': True, 'ids': [x['id'] for x in huellas() if x.get('rp') == host], 'https': HTTPS_TS.get('url', '')})
            if (self.path in ('/', '/index.html') or p0 in ('/personal', '/personal/', '/panel', '/panel/')) and not self._es_admin() and page_entrar:
                return self._send(page_entrar, 'text/html; charset=utf-8')
            if not self._auth(): return
            if p0 == '/api/drive-script':
                body = SCRIPT_DRIVE.replace('{SECRETO}', cfg['drive_secreto']).encode('utf-8')
                return self._send(body, 'text/plain; charset=utf-8')
            if p0 == '/api/rh-archivo':
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query); r_ = mon.rh_archivo((qs.get('emp') or [''])[0], (qs.get('id') or [''])[0])
                if not r_: self.send_response(404); self.end_headers(); return
                doc, datos = r_
                self.send_response(200); self.send_header('Content-Type', doc.get('mime') or 'application/octet-stream'); self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Disposition', "inline; filename*=UTF-8''" + __import__('urllib.parse').parse.quote(doc.get('nombre') or doc['archivo']))
                self.send_header('Content-Length', str(len(datos))); self.end_headers(); self.wfile.write(datos); return
            if p0 == '/api/rh':
                body = json.dumps({'ok': True, 'rh': mon.rh_info()}, ensure_ascii=False).encode('utf-8')
                return self._send(body, 'application/json; charset=utf-8')
            if p0 == '/api/comandero-pdf':
                import urllib.parse
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); cod = (q.get('cod') or [''])[0]
                x = next((x for x in mon.meseros() if x.get('activo') and x.get('cod') == cod), None)
                wifi = [ip for ip in ips_locales() if not ip.startswith('100.')]
                if not x or not wifi: self.send_response(404); self.end_headers(); return
                nombre = mon.EMP.get(cod, x['nombre'])
                pdf = pdf_comandero(nombre, 'http://%s:%s/comandero' % (wifi[0], cfg['puerto']), mon.horario_mesero(cod), bool(mon.cfg.get('candado_rol')), logo_cmd)
                arch = 'Comandero_' + re.sub(r'[^A-Za-z]', '', _sin_acento((nombre.split() or ['LaHolandesa'])[0])).title() + '.pdf'
                self.send_response(200); self.send_header('Content-Type', 'application/pdf'); self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Disposition', "attachment; filename=\"Comandero.pdf\"; filename*=UTF-8''" + urllib.parse.quote(arch))
                self.send_header('Content-Length', str(len(pdf))); self.end_headers(); self.wfile.write(pdf); return
            if p0 == '/api/descansos-pdf':
                # hoja de bienvenida para la encargada (Personal → Rol de descansos → Captura de la encargada)
                import urllib.parse
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ac = mon.rh().get('acceso_rol') or {}
                wifi = [ip for ip in ips_locales() if not ip.startswith('100.')]; ts = [ip for ip in ips_locales() if ip.startswith('100.')]
                if not ac.get('activo') or not wifi: self.send_response(404); self.end_headers(); return
                nombre = ac.get('nombre') or 'Encargada'
                # decisión de Jorge (15-oct-2026): la encargada solo captura en el restaurante (sin Tailscale) y la clave se la da él en persona
                pdf = pdf_descansos(nombre, 'http://%s:%s/descansos' % (wifi[0], cfg['puerto']), '', icon_rh, '')
                arch = 'Descansos_' + re.sub(r'[^A-Za-z]', '', _sin_acento((nombre.split() or ['Encargada'])[0])).title() + '.pdf'
                self.send_response(200); self.send_header('Content-Type', 'application/pdf'); self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Disposition', "attachment; filename=\"Descansos.pdf\"; filename*=UTF-8''" + urllib.parse.quote(arch))
                self.send_header('Content-Length', str(len(pdf))); self.end_headers(); self.wfile.write(pdf); return
            if p0 == '/api/registro':   # 30.7.76: últimas líneas de monitor.log para revisar errores desde el teléfono
                try:
                    with open(LOG_FILE, 'rb') as f_:
                        f_.seek(0, 2); tam = f_.tell(); f_.seek(max(0, tam - 60000)); txt = f_.read().decode('utf-8', 'replace')
                except OSError: txt = ''
                lineas = txt.splitlines()[-250:]
                return self._json({'ok': True, 'lineas': lineas, 'version': VERSION_TXT})
            if p0 == '/api/acceso':
                body = json.dumps({'ok': True, 'puerto': cfg['puerto'], 'ips': ips_locales(), 'https': HTTPS_TS.get('url', '')}, ensure_ascii=False).encode('utf-8')
                return self._send(body, 'application/json; charset=utf-8')
            if p0.startswith('/foto/'):
                nom = re.sub(r'[^0-9A-Za-z_.-]', '', p0[6:])
                ruta = os.path.join(FOTOS_DIR, nom)
                if nom.endswith('.jpg') and os.path.isfile(ruta):
                    with open(ruta, 'rb') as f: return self._send(f.read(), 'image/jpeg')
                self.send_response(404); self.end_headers(); return
            if self.path.startswith('/api/abiertas'):
                try:
                    body=json.dumps({'ok':True,'abiertas':mon.abiertas(),'generado':datetime.datetime.now().isoformat(timespec='seconds')},ensure_ascii=False).encode('utf-8')
                    return self._send(body,'application/json; charset=utf-8')
                except Exception as e:
                    body=json.dumps({'ok':False,'error':str(e)},ensure_ascii=False).encode('utf-8')
                    self.send_response(500); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path.startswith('/api/quitadas.xlsx'):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query); g = lambda k: (qs.get(k) or [''])[0]
                body = mon.excel_quitadas(g('desde'), g('hasta'), g('grupo') or 'todas')
                nom = 'Cuentas quitadas ' + (g('grupo') or 'todas') + ' ' + g('desde') + ' a ' + g('hasta') + '.xlsx'
                self.send_response(200); self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition', "attachment; filename=\"cuentas_quitadas.xlsx\"; filename*=UTF-8''" + __import__('urllib.parse').parse.quote(nom))
                self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path.startswith('/manifest.webmanifest'):
                return self._send(manifest, 'application/manifest+json; charset=utf-8')
            if self.path.startswith('/icon.png') and icon_png:
                return self._send(icon_png, 'image/png')
            if self.path.startswith('/data.json'):
                usegz = 'gzip' in self.headers.get('Accept-Encoding', '')
                with mon.lock: body = mon.jsongz if usegz else mon.json
                return self._send(body, 'application/json; charset=utf-8', usegz)
            if self.path in ('/', '/index.html') or p0 in ('/personal', '/personal/', '/panel', '/panel/'):
                ULTIMA_PAGINA.update(p='panel', t=time.time()); return self._send(page, 'text/html; charset=utf-8')
            self.send_response(404); self.end_headers()
    return H

# ---------------------------------------------------------------- actualización automática (solo descarga ESTE programa)
import urllib.request, urllib.error, shutil, subprocess, py_compile

def _bajar(nombre):
    hd = {'User-Agent': 'monitor-holandesa'}
    # 30.7.38: si el repositorio se hace privado, la caja necesita una llave de solo lectura en config.json ("github_token")
    try: tok = (json.load(open(CONFIG_FILE, encoding='utf-8')).get('github_token') or '').strip()
    except Exception: tok = ''
    if tok: hd['Authorization'] = 'token ' + tok
    req = urllib.request.Request(REPO_RAW + nombre + '?t=' + str(int(time.time())), headers=hd)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def github_probar(tok):
    """30.7.39: prueba una llave de GitHub contra el repositorio. Regresa {'privado', 'vence'} o lanza un error claro."""
    hd = {'User-Agent': 'monitor-holandesa', 'Authorization': 'token ' + tok, 'Accept': 'application/vnd.github+json'}
    try:
        with urllib.request.urlopen(urllib.request.Request(REPO_API, headers=hd), timeout=20) as r:
            info = json.loads(r.read().decode('utf-8')); vence = r.headers.get('github-authentication-token-expiration') or ''
    except urllib.error.HTTPError as e:
        if e.code == 401: raise ValueError('GitHub no reconoce esa llave. Revisa que la copiaste completa (empieza con github_pat_) y que no haya vencido.')
        if e.code in (403, 404): raise ValueError('La llave es válida, pero no tiene permiso para leer monitor-holandesa. Al crearla, en "Repository access" elige "Only select repositories" → monitor-holandesa, y en permisos "Contents: Read-only".')
        raise ValueError('GitHub respondió con error ' + str(e.code) + '. Intenta de nuevo en un momento.')
    except urllib.error.URLError as e:
        raise ValueError('La caja no pudo conectarse a GitHub (¿hay internet?): ' + str(e.reason))
    req = urllib.request.Request(REPO_RAW + 'version.txt?t=' + str(int(time.time())), headers={'User-Agent': 'monitor-holandesa', 'Authorization': 'token ' + tok})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: int(r.read().decode().strip() or 0)
    except Exception:
        raise ValueError('La llave abre el repositorio pero no puede leer los archivos. En permisos de la llave pon "Contents: Read-only".')
    return {'privado': bool(info.get('private')), 'vence': vence[:10]}

_RECHAZADA = [0, 0]   # 30.7.17 (número, cuándo); 30.7.56: se vuelve a intentar a los 30 minutos: versión publicada con números disparejos que ya se rechazó (no se vuelve a bajar hasta que cambie)

def buscar_actualizacion():
    """Regresa True si instaló una versión nueva (y hay que reiniciar)."""
    try:
        remota = int(_bajar('version.txt').decode().strip() or 0)
        ESTADO_ACT.update(t=datetime.datetime.now().isoformat(timespec='minutes'), ok=True, error='')
    except Exception as e:
        ESTADO_ACT.update(t=datetime.datetime.now().isoformat(timespec='minutes'), ok=False, error=str(e)[:200])
        log('No pude revisar actualizaciones: ' + str(e)); return False
    if remota <= VERSION or (remota == _RECHAZADA[0] and time.time() - _RECHAZADA[1] < 1800): return False
    log(f'Hay versión nueva {remota} (tengo {VERSION}). Descargando…')
    nuevo = os.path.join(AQUI, '_nuevo'); resp = os.path.join(AQUI, '_respaldo', 'programa')
    shutil.rmtree(nuevo, ignore_errors=True); os.makedirs(nuevo, exist_ok=True)
    try:
        # 30.7.17: el programa nuevo debe decir el mismo número que version.txt; si no, instalarlo haría que la caja
        # se reinicie sin parar (pasó el 15-oct-2026). Se rechaza y se sigue con la versión actual.
        m = re.search(rb'^VERSION = (\d+)', _bajar('holandesa_monitor.py'), re.M)
        interna = int(m.group(1)) if m else 0
        if interna != remota:
            _RECHAZADA[0] = remota; _RECHAZADA[1] = time.time()
            raise RuntimeError(f'la versión publicada está dispareja (version.txt dice {remota} y el programa dice {interna}); no se instala')
        archivos = [a.strip() for a in _bajar('archivos.txt').decode().splitlines() if a.strip()]
        permitidos = {'holandesa_monitor.py', 'panel.html', 'detener_monitor.bat', 'iniciar_monitor.vbs', 'probar.bat', 'actualizar.bat', 'icon.png'}
        archivos = [a for a in archivos if a in permitidos or re.fullmatch(r'[a-z0-9_]+\.(html|css|js|png|bat|vbs)', a)]
        if 'holandesa_monitor.py' not in archivos: raise RuntimeError('archivos.txt incompleto')
        for a in archivos:
            datos_ = _bajar(a)
            if a.endswith('.html') and (len(datos_) < 2000 or b'<script' not in datos_): raise RuntimeError(a + ' llegó vacío o incompleto')   # 30.7.12: nunca instalar una página vacía
            with open(os.path.join(nuevo, a), 'wb') as f: f.write(datos_)
        py_compile.compile(os.path.join(nuevo, 'holandesa_monitor.py'), doraise=True)   # si no compila, no se instala
        shutil.rmtree(resp, ignore_errors=True); os.makedirs(resp, exist_ok=True)
        for a in archivos:
            if os.path.exists(os.path.join(AQUI, a)): shutil.copy2(os.path.join(AQUI, a), os.path.join(resp, a))
        for a in archivos: shutil.copy2(os.path.join(nuevo, a), os.path.join(AQUI, a))
        log(f'Versión {remota} instalada. Respaldo de la anterior en _respaldo\\programa')
        return True
    except Exception as e:
        log('La actualización falló y se conserva la versión actual: ' + str(e)); return False
    finally:
        shutil.rmtree(nuevo, ignore_errors=True)

def _pythonw():
    exe = sys.executable
    if exe.lower().endswith('python.exe') and os.path.exists(exe[:-10] + 'pythonw.exe'): exe = exe[:-10] + 'pythonw.exe'
    return exe

def _lanzar(args):
    # arranca un proceso independiente (que no muera cuando este termine)
    kw = {'cwd': AQUI, 'close_fds': True}
    if os.name == 'nt':
        DET, GRP, BRK = 0x00000008, 0x00000200, 0x01000000
        for fl in (DET | GRP | BRK, DET | GRP, 0):
            try: return subprocess.Popen(args, creationflags=fl, **kw)
            except OSError: continue
    return subprocess.Popen(args, **kw)

def reiniciar():
    log('Reiniciando con la versión nueva…')
    _sesm_guardar()   # 30.7.59: los que están en el comandero siguen dentro después de la actualización
    _lanzar([_pythonw(), os.path.join(AQUI, 'holandesa_monitor.py'), '--esperar'])
    os._exit(0)

def responde(puerto):
    import socket
    try:
        with socket.create_connection(('127.0.0.1', int(puerto)), timeout=3): return True
    except OSError: return False

HTTPS_TS = {}   # dirección segura https://<caja>.<red>.ts.net (tailscale serve), para que el teléfono pueda compartir archivos
def tailscale_exe():
    for c in (shutil.which('tailscale'), r'C:\Program Files\Tailscale\tailscale.exe', r'C:\Program Files (x86)\Tailscale\tailscale.exe'):
        if c and os.path.exists(c): return c
    return None
def activar_https(cfg):
    if cfg.get('https_tailscale') is False: return
    ts = tailscale_exe()
    if not ts: log('HTTPS: no encontré Tailscale en esta computadora.'); return
    kw = {'capture_output': True, 'text': True, 'timeout': 25}
    if os.name == 'nt': kw['creationflags'] = 0x08000000   # sin ventana
    try:
        st = json.loads(subprocess.run([ts, 'status', '--json'], **kw).stdout or '{}')
        dns = ((st.get('Self') or {}).get('DNSName') or '').rstrip('.')
        if not dns: log('HTTPS: Tailscale no tiene nombre (MagicDNS apagado).'); return
        ver = lambda: subprocess.run([ts, 'serve', 'status', '--json'], **kw).stdout or ''
        actual = ver()
        if not (dns in actual and str(cfg['puerto']) in actual):
            r = subprocess.run([ts, 'serve', '--bg', str(cfg['puerto'])], **kw)
            log('HTTPS: tailscale serve -> ' + re.sub(r'\s+', ' ', (r.stdout + ' ' + r.stderr).strip())[:300])
            actual = ver()
        if dns in actual and str(cfg['puerto']) in actual:
            HTTPS_TS['url'] = 'https://' + dns; log('HTTPS listo: ' + HTTPS_TS['url'])
        else: log('HTTPS: no quedó activo. Revisa que en el panel de Tailscale estén encendidos MagicDNS y "HTTPS Certificates".')
    except subprocess.TimeoutExpired: log('HTTPS: Tailscale tardó demasiado (quizá pide encender HTTPS Certificates en su panel).')
    except Exception as e: log('HTTPS: ' + str(e))

def instalar_vigilante():
    # tarea de Windows que cada 10 minutos revisa que el monitor esté prendido y, si no, lo arranca
    if os.name != 'nt': return
    try:
        tr = f'"{_pythonw()}" "{os.path.join(AQUI, "holandesa_monitor.py")}" --vigilar'
        r = subprocess.run(['schtasks', '/Create', '/F', '/TN', 'Monitor La Holandesa', '/SC', 'MINUTE', '/MO', '10', '/TR', tr],
                           capture_output=True, text=True, creationflags=0x08000000)
        log('Vigilante instalado (revisa cada 10 min)' if r.returncode == 0 else 'No pude instalar el vigilante: ' + (r.stderr or r.stdout).strip())
    except Exception as e:
        log('No pude instalar el vigilante: ' + str(e))

def ciclo_actualizacion():
    # Revisa GitHub cada 5 minutos (ajustable en config.json); solo reinicia si hay una versión nueva válida.
    try: espera = max(60, int(load_config_ro().get('segundos_revision_actualizacion', 300)))
    except Exception: espera = 300
    time.sleep(15)
    while True:
        try:
            if buscar_actualizacion():
                reiniciar()
        except Exception as e:
            log('Error en ciclo de actualización: ' + str(e))
        time.sleep(espera)

def main():
    cfg = load_config()
    if not os.path.isdir(cfg['carpeta_mrtienda']):
        log('No existe la carpeta ' + cfg['carpeta_mrtienda'] + '. Corrígela en config.json'); sys.exit(1)
    mon = Monitor(cfg)
    _ses_cargar()   # quien ya había entrado al panel sigue dentro después de una actualización
    _sesm_cargar()  # 30.7.59: y quien estaba en el comandero también
    mon.carga_prestamos_excel()
    try: mon.borrar_fotos()
    except Exception as e: log('No pude borrar las fotos viejas: ' + str(e))
    threading.Thread(target=mon.loop, daemon=True).start()
    threading.Thread(target=mon.ciclo_clientes_nuevos, daemon=True).start()   # 30.7.74
    if cfg.get('actualizar_solo', True): threading.Thread(target=ciclo_actualizacion, daemon=True).start()
    srv = None
    for intento in range(30):
        try:
            ThreadingHTTPServer.allow_reuse_address = (os.name != 'nt')
            srv = ThreadingHTTPServer(('0.0.0.0', int(cfg['puerto'])), make_handler(mon, cfg)); break
        except OSError:
            time.sleep(2)
    if srv is None: log('El puerto sigue ocupado; no pude arrancar.'); sys.exit(1)
    log(f"Monitor versión {VERSION_TXT} (#{VERSION}) listo en el puerto {cfg['puerto']} (usuario: {cfg['usuario']})")
    threading.Thread(target=instalar_vigilante, daemon=True).start()
    def _https_hasta_que_quede():   # reintenta cada 10 min hasta que Jorge encienda HTTPS en Tailscale
        while not HTTPS_TS.get('url'):
            activar_https(cfg)
            if HTTPS_TS.get('url') or cfg.get('https_tailscale') is False: break
            time.sleep(600)
    threading.Thread(target=_https_hasta_que_quede, daemon=True).start()
    srv.serve_forever()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--esperar':
        time.sleep(4); main()
    elif len(sys.argv) > 1 and sys.argv[1] == '--vigilar':
        try:
            with open(CONFIG_FILE, encoding='utf-8') as f: puerto = json.load(f).get('puerto', 8765)
        except Exception: puerto = 8765
        if not responde(puerto):
            log('El vigilante no encontró el monitor prendido; lo arranco.'); main()
    elif len(sys.argv) > 1 and sys.argv[1] == '--actualizar':
        print('Versión actual:', VERSION_TXT); print('Instalé versión nueva' if buscar_actualizacion() else 'No hay versión nueva')
    elif len(sys.argv) > 1 and sys.argv[1] == '--prueba':
        cfg = load_config(); m = Monitor(cfg); m.refresh()
        d = json.loads(m.json); h = d['detalle'][d['hasta']]
        print('Último día:', d['hasta'], 'Venta:', h['total'], 'Tickets:', h['tickets'])
    else:
        main()
