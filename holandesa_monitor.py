# -*- coding: utf-8 -*-
"""
Monitor de ventas - Restaurante La Holandesa
Lee la base de datos de MrTienda (SOLO LECTURA) y muestra el tablero de ventas
en http://<esta-computadora>:8765  (protegido con usuario y contraseña).

No modifica ningún archivo de MrTienda: abre cada archivo solo para leerlo.
Requiere Python 3.9 o superior. No necesita instalar paquetes adicionales.
"""
import os, re, sys, json, gzip, glob, time, base64, struct, hashlib, secrets, threading, datetime, collections, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

AQUI = os.path.dirname(os.path.abspath(__file__))
VERSION = 10
REPO_RAW = 'https://raw.githubusercontent.com/Jorge-1639/monitor-holandesa/main/'
CONFIG_FILE = os.path.join(AQUI, 'config.json')
LOG_FILE = os.path.join(AQUI, 'monitor.log')
DEFAULT_CONFIG = {
    "carpeta_mrtienda": r"C:\MRHOLA",
    "puerto": 8765,
    "usuario": "jorge",
    "contrasena": "",
    "minutos_actualizacion": 2,
    "dias_detalle": 70,
    "bitacora_borrados_hasta": "2025-02-21"
}

def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except Exception: pass
    try: print(line)
    except Exception: pass

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8') as f: cfg.update(json.load(f))
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
        flds.append((name, t, ln)); i += 32
    rows = []
    for r in range(n):
        s = hl + r*rl; rec = b[s:s+rl]
        if len(rec) < rl: break
        d = {'_del': rec[0:1] == b'*'}; p = 1
        for name, t, ln in flds:
            v = rec[p:p+ln]; p += ln
            if t in 'CM': v = v.decode(enc, 'ignore').strip()
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

class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg['carpeta_mrtienda']
        self.cache = {}           # (ruta, tamaño, mtime) -> filas
        self.json = b'{}'
        self.jsongz = gzip.compress(b'{}')
        self.error = None
        self.lock = threading.Lock()

    def rows(self, path):
        try: st = os.stat(path)
        except OSError: return []
        key = (path, st.st_size, int(st.st_mtime))
        if key not in self.cache:
            for k in [k for k in self.cache if k[0] == path]: del self.cache[k]
            self.cache[key] = [x for x in safe_rows(path) if not x['_del']]
        return self.cache[key]

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
            t = tickets.setdefault(k, {'tot': 0, 'hora': p.get('HORA') or '', 'mesa': p.get('MESA') or '', 'mesero': self.name(p.get('COD_VENDED')),
                                       'pers': p.get('COMENSALES') or 0, 'esc': p.get('COD_ESCALA'), 'pagos': [],
                                       'cobro': self.name(p.get('COD_CAJERO')), 'items': [], 'capt': collections.Counter(), 'folz': p.get('FOL_Z') or ''})
            a = amt(p); t['tot'] += a
            f = FORMAS.get(p.get('COD_FORMA'), 'Otro')
            if p.get('COD_MONEDA') == '02': f = 'Efectivo (dólares)'
            t['pagos'].append((f, a))
            t.setdefault('raw', []).append([f, round((p.get('PAGO') or 0) * (p.get('TC') or 1), 2), round((p.get('CAMBIO') or 0) * (p.get('TC_CAMBIO') or 1), 2)])
        tot = sum(t['tot'] for t in tickets.values())
        s = {'fecha': d.isoformat(), 'total': round(tot, 2), 'tickets': len(tickets), 'personas': int(sum(t['pers'] for t in tickets.values()))}
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

    def refresh(self):
        t0 = time.time()
        self.catalogs()
        idx = self.index_files()
        days = sorted({d for (pre, d) in idx if pre == 'P'})
        if not days: raise RuntimeError('No encontré tickets en ' + self.base)
        out = {'version': VERSION, 'generado': datetime.datetime.now().isoformat(timespec='seconds'), 'desde': days[0].isoformat(), 'hasta': days[-1].isoformat(),
               'bitacora_hasta': self.cfg.get('bitacora_borrados_hasta')}
        out['diario'] = [self.day(idx, d, False) for d in days]
        out['detalle'] = {d.isoformat(): self.day(idx, d, True) for d in days[-int(self.cfg['dias_detalle']):]}
        mon = collections.defaultdict(lambda: [0, 0])
        for x in out['diario']: m = mon[x['fecha'][:7]]; m[0] += x['total']; m[1] += x['tickets']
        out['mensual'] = [{'mes': k, 'total': round(v[0], 2), 'tickets': v[1]} for k, v in sorted(mon.items())]
        data = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        gz = gzip.compress(data, 6)
        with self.lock: self.json = data; self.jsongz = gz; self.error = None
        log(f'Actualizado: {len(days)} días, hoy {out["detalle"][days[-1].isoformat()]["total"]:.2f}, {time.time()-t0:.1f}s')

    def loop(self):
        while True:
            try: self.refresh()
            except Exception as e:
                self.error = str(e); log('ERROR ' + traceback.format_exc())
            time.sleep(max(30, float(self.cfg['minutos_actualizacion']) * 60))

# ---------------------------------------------------------------- servidor web
def make_handler(mon, cfg):
    token = base64.b64encode(f"{cfg['usuario']}:{cfg['contrasena']}".encode()).decode()
    with open(os.path.join(AQUI, 'panel.html'), 'rb') as f: page = f.read()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _auth(self):
            if secrets.compare_digest(self.headers.get('Authorization', ''), 'Basic ' + token): return True
            self.send_response(401); self.send_header('WWW-Authenticate', 'Basic realm="La Holandesa", charset="UTF-8"'); self.end_headers()
            return False
        def _send(self, body, ctype, gz=False):
            self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
            if gz: self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if not self._auth(): return
            if self.path.startswith('/data.json'):
                usegz = 'gzip' in self.headers.get('Accept-Encoding', '')
                with mon.lock: body = mon.jsongz if usegz else mon.json
                return self._send(body, 'application/json; charset=utf-8', usegz)
            if self.path in ('/', '/index.html'): return self._send(page, 'text/html; charset=utf-8')
            self.send_response(404); self.end_headers()
    return H

# ---------------------------------------------------------------- actualización automática (solo descarga ESTE programa)
import urllib.request, shutil, subprocess, py_compile

def _bajar(nombre):
    req = urllib.request.Request(REPO_RAW + nombre + '?t=' + str(int(time.time())), headers={'User-Agent': 'monitor-holandesa'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def buscar_actualizacion():
    """Regresa True si instaló una versión nueva (y hay que reiniciar)."""
    try:
        remota = int(_bajar('version.txt').decode().strip() or 0)
    except Exception as e:
        log('No pude revisar actualizaciones: ' + str(e)); return False
    if remota <= VERSION: return False
    log(f'Hay versión nueva {remota} (tengo {VERSION}). Descargando…')
    nuevo = os.path.join(AQUI, '_nuevo'); resp = os.path.join(AQUI, '_respaldo')
    shutil.rmtree(nuevo, ignore_errors=True); os.makedirs(nuevo, exist_ok=True)
    try:
        archivos = [a.strip() for a in _bajar('archivos.txt').decode().splitlines() if a.strip()]
        permitidos = {'holandesa_monitor.py', 'panel.html', 'detener_monitor.bat', 'iniciar_monitor.vbs', 'probar.bat', 'actualizar.bat'}
        archivos = [a for a in archivos if a in permitidos]
        if 'holandesa_monitor.py' not in archivos: raise RuntimeError('archivos.txt incompleto')
        for a in archivos:
            with open(os.path.join(nuevo, a), 'wb') as f: f.write(_bajar(a))
        py_compile.compile(os.path.join(nuevo, 'holandesa_monitor.py'), doraise=True)   # si no compila, no se instala
        shutil.rmtree(resp, ignore_errors=True); os.makedirs(resp, exist_ok=True)
        for a in archivos:
            if os.path.exists(os.path.join(AQUI, a)): shutil.copy2(os.path.join(AQUI, a), os.path.join(resp, a))
        for a in archivos: shutil.copy2(os.path.join(nuevo, a), os.path.join(AQUI, a))
        log(f'Versión {remota} instalada. Respaldo de la anterior en _respaldo')
        return True
    except Exception as e:
        log('La actualización falló y se conserva la versión actual: ' + str(e)); return False
    finally:
        shutil.rmtree(nuevo, ignore_errors=True)

def reiniciar():
    exe = sys.executable
    if exe.lower().endswith('python.exe') and os.path.exists(exe[:-10] + 'pythonw.exe'): exe = exe[:-10] + 'pythonw.exe'
    subprocess.Popen([exe, os.path.join(AQUI, 'holandesa_monitor.py'), '--esperar'], cwd=AQUI, close_fds=True)
    os._exit(0)

def ciclo_actualizacion():
    time.sleep(90)
    while True:
        if buscar_actualizacion(): reiniciar()
        ahora = datetime.datetime.now()
        siguiente = (ahora + datetime.timedelta(days=1)).replace(hour=3, minute=30, second=0, microsecond=0)
        if ahora.hour < 3 or (ahora.hour == 3 and ahora.minute < 30):
            siguiente = ahora.replace(hour=3, minute=30, second=0, microsecond=0)
        time.sleep(max(60, (siguiente - ahora).total_seconds()))

def main():
    cfg = load_config()
    if not os.path.isdir(cfg['carpeta_mrtienda']):
        log('No existe la carpeta ' + cfg['carpeta_mrtienda'] + '. Corrígela en config.json'); sys.exit(1)
    mon = Monitor(cfg)
    threading.Thread(target=mon.loop, daemon=True).start()
    if cfg.get('actualizar_solo', True): threading.Thread(target=ciclo_actualizacion, daemon=True).start()
    srv = None
    for intento in range(30):
        try:
            srv = ThreadingHTTPServer(('0.0.0.0', int(cfg['puerto'])), make_handler(mon, cfg)); break
        except OSError:
            time.sleep(2)
    if srv is None: log('El puerto sigue ocupado; no pude arrancar.'); sys.exit(1)
    log(f"Monitor versión {VERSION} listo en el puerto {cfg['puerto']} (usuario: {cfg['usuario']})")
    srv.serve_forever()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--esperar':
        time.sleep(4); main()
    elif len(sys.argv) > 1 and sys.argv[1] == '--actualizar':
        print('Versión actual:', VERSION); print('Instalé versión nueva' if buscar_actualizacion() else 'No hay versión nueva')
    elif len(sys.argv) > 1 and sys.argv[1] == '--prueba':
        cfg = load_config(); m = Monitor(cfg); m.refresh()
        d = json.loads(m.json); h = d['detalle'][d['hasta']]
        print('Último día:', d['hasta'], 'Venta:', h['total'], 'Tickets:', h['tickets'])
    else:
        main()
