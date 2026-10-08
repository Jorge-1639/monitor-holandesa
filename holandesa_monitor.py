# -*- coding: utf-8 -*-
"""
Monitor de ventas - Restaurante La Holandesa
Lee la base de datos de MrTienda y muestra el tablero de ventas
en http://<esta-computadora>:8765  (protegido con usuario y contraseña).

La consulta de ventas es de solo lectura. La única escritura en MrTienda es quitar, con confirmación y motivo,
una cuenta pendiente NO pagada (comida de la familia, pedidos Didi/Uber, errores). Antes respalda los archivos
y deja el registro en cuentas_quitadas.json (fuera de MrTienda), que alimenta los reportes y el Excel.
Requiere Python 3.9 o superior. No necesita instalar paquetes adicionales.
"""
import os, re, sys, json, gzip, glob, time, base64, struct, hashlib, secrets, threading, datetime, collections, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

AQUI = os.path.dirname(os.path.abspath(__file__))
VERSION = 23
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
    "bitacora_borrados_hasta": "2025-02-21",
    "segundos_revision_actualizacion": 300
}
QUITADAS_FILE = os.path.join(AQUI, 'cuentas_quitadas.json')
GASTOS_FILE = os.path.join(AQUI, 'gastos.json')       # compras y gastos que captura Jorge (no es de MrTienda)
CATEGORIAS_GASTO = ['Insumos y compras', 'Sueldos', 'Seguro social', 'Luz', 'Agua', 'Gas', 'Renta', 'Mantenimiento', 'Comisiones', 'Otros gastos']
RESPALDO_CUENTAS = os.path.join(AQUI, 'respaldos_cuentas')   # fuera de _respaldo: las actualizaciones no lo borran
MOTIVOS = ['Familia', 'Didi · efectivo', 'Didi · tarjeta', 'Uber · efectivo', 'Uber · tarjeta', 'Error de captura', 'Otro', 'Sin clasificar']


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except Exception: pass
    try: print(line)
    except Exception: pass

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

# ---------------------------------------------------------------- Excel (.xlsx) sin paquetes extra
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

    def rows(self, path):
        try: st = os.stat(path)
        except OSError: return []
        key = (path, st.st_size, int(st.st_mtime))
        self._touched.add(path)
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

    def abiertas(self):
        # cuentas abiertas (sin cobrar): COMUNES\PENDIENT.DBF, renglones vigentes con PAGADO = falso. Solo lectura.
        com = os.path.join(self.base, 'COMUNES')
        out = []
        for x in self.rows(os.path.join(com, 'PENDIENT.DBF')):
            if x.get('PAGADO'): continue
            fa = x.get('FECHA_AP') or x.get('FECHA') or ''
            items = []
            arch = (x.get('FILE') or '').strip()
            if arch:
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
                                          'llevar': v.get('COD_ESCALA') == '02'})
                        break
            turno_raw = next((str(x.get(k) or '').strip() for k in ('TURNO','COD_TURNO','NUM_TURNO','CORTE') if str(x.get(k) or '').strip()), '')
            out.append({'ref': (x.get('REF') or '').strip(), 'mesa': (x.get('REF') or '').strip() or 'Sin nombre', 'fecha': f'{fa[:4]}-{fa[4:6]}-{fa[6:8]}' if len(fa) == 8 else '',
                        'abrio': (x.get('HORA_AP') or x.get('HORA') or '')[:5], 'mesero': (x.get('DES_VENDED') or '').strip().title() or self.name(x.get('COD_VENDED')),
                        'pers': int(x.get('COMENSALES') or 1), 'arts': int(x.get('ARTS') or 0), 'total': round(x.get('IMPORTE_MN') or 0, 2),
                        'llevar': x.get('COD_ESCALA') == '02', 'archivo': arch, 'items': items, 'turno': turno_raw})
        return sorted(out, key=lambda a: (a['fecha'], a['abrio']))

    def eliminar_abierta(self, archivo, referencia, motivo='Sin clasificar', nota=''):
        """Replica la baja observada en MrTienda para una cuenta abierta y NO pagada.
        Respalda PENDIENT.DBF y el DBF de la cuenta, marca el renglón de PENDIENT como eliminado
        y retira el DBF activo. No toca DATABASE ni archivos .ENC/.BAK de MrTienda.
        """
        archivo = re.sub(r'[^A-Za-z0-9_-]', '', str(archivo or '').strip())
        referencia = str(referencia or '').strip()
        if not archivo: raise ValueError('Faltan datos de la cuenta.')
        if motivo not in MOTIVOS: raise ValueError('Elige un motivo válido.')
        nota = str(nota or '').strip()[:120]
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
            log(f'CUENTA QUITADA remotamente: {referencia} / {archivo} · {motivo}. Respaldo: {bdir}')
        ahora = datetime.datetime.now()
        try:
            self.anotar_quitada({'id': sello + '_' + archivo, 'quitada': ahora.isoformat(timespec='seconds'),
                                 'fecha': foto['fecha'] or ahora.date().isoformat(), 'abrio': foto['abrio'], 'cuenta': foto['mesa'],
                                 'mesero': foto['mesero'], 'pers': foto['pers'], 'arts': foto['arts'], 'total': foto['total'],
                                 'llevar': foto['llevar'], 'items': [{'n': i['n'], 'q': i['q'], 'imp': i['imp']} for i in foto['items']],
                                 'motivo': motivo, 'nota': nota, 'respaldo': os.path.basename(bdir)})
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
        q.sort(key=lambda r: (r.get('fecha', ''), r.get('abrio', '')))
        head = ['Fecha', 'Abrió', 'Quitada', 'Cuenta', 'Motivo', 'Plataforma', 'Pago', 'Nota', 'Mesero', 'Artículos', 'Importe MrTienda', 'Detalle']
        rows = []
        for r in q:
            m = r.get('motivo', ''); plat, _, pago = m.partition(' · ')
            rows.append([r.get('fecha', ''), r.get('abrio', ''), (r.get('quitada') or '')[11:16], r.get('cuenta', ''), m,
                         plat if pago else '', pago, r.get('nota', ''), r.get('mesero', ''), r.get('arts') or len(r.get('items') or []),
                         float(r.get('total') or 0), ', '.join(f"{int(i['q']) if float(i['q']).is_integer() else i['q']} {i['n']}" for i in r.get('items') or [])])
        tot = round(sum(x[10] for x in rows), 2)
        titulo = {'didi': 'Didi', 'uber': 'Uber', 'plataformas': 'Didi y Uber', 'familia': 'Familia'}.get(grupo, 'Cuentas quitadas')
        return xlsx([head] + rows + [[], ['', '', '', 'TOTAL · ' + str(len(rows)) + ' cuentas', '', '', '', '', '', '', tot, '']], titulo,
                    widths=[11, 7, 8, 22, 17, 10, 9, 22, 14, 9, 15, 60], money_cols={10})

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
        g.setdefault('movs', []); g.setdefault('fijos', [])
        return g

    def _guardar_gastos(self, g):
        tmp = GASTOS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(g, f, ensure_ascii=False, indent=1)
        os.replace(tmp, GASTOS_FILE)

    def _limpia_mov(self, m):
        fecha = str(m.get('fecha') or '')[:10]
        datetime.date.fromisoformat(fecha)
        cat = m.get('categoria') if m.get('categoria') in CATEGORIAS_GASTO else 'Otros gastos'
        monto = round(float(str(m.get('monto')).replace('$', '').replace(',', '')), 2)
        if monto <= 0 or monto > 10000000: raise ValueError('Monto no válido.')
        concepto = str(m.get('concepto') or '').strip()[:80] or cat
        return {'fecha': fecha, 'concepto': concepto, 'categoria': cat, 'monto': monto,
                'proveedor': str(m.get('proveedor') or '').strip()[:60], 'nota': str(m.get('nota') or '').strip()[:120]}

    def gasto_accion(self, d):
        acc = d.get('accion')
        with self.q_lock:
            try:
                with open(GASTOS_FILE, encoding='utf-8') as f: g = json.load(f)
                if not isinstance(g, dict): g = {}
            except (OSError, ValueError): g = {}
            g.setdefault('movs', []); g.setdefault('fijos', [])
            ahora = datetime.datetime.now()
            if acc == 'agregar':
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

    def cambiar_motivo(self, rid, motivo, nota):
        if motivo not in MOTIVOS: raise ValueError('Motivo no válido.')
        with self.q_lock:
            try:
                with open(QUITADAS_FILE, encoding='utf-8') as f: q = json.load(f)
            except (OSError, ValueError): q = []
            for r in q:
                if r.get('id') == rid:
                    r['motivo'] = motivo; r['nota'] = str(nota or '').strip()[:120]
                    r['editado'] = datetime.datetime.now().isoformat(timespec='seconds')
                    self._guardar_quitadas(q); break
            else: raise RuntimeError('No encontré ese registro.')
        self.refresh()
        return {'ok': True}

    def refresh(self):
        t0 = time.time()
        self._touched = set()
        self.catalogs()
        idx = self.index_files()
        days = sorted({d for (pre, d) in idx if pre == 'P'})
        if not days: raise RuntimeError('No encontré tickets en ' + self.base)
        out = {'version': VERSION, 'generado': datetime.datetime.now().isoformat(timespec='seconds'), 'desde': days[0].isoformat(), 'hasta': days[-1].isoformat(),
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
        out['quitadas'] = self.quitadas(); out['motivos'] = MOTIVOS
        out['gastos'] = self.gastos(); out['categorias_gasto'] = CATEGORIAS_GASTO
        # suelta de la memoria los archivos que ya no se leyeron en esta vuelta
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
            try: self.refresh(); self.guardar_copia()
            except Exception as e:
                self.error = str(e); log('ERROR ' + traceback.format_exc())
            time.sleep(max(30, float(self.cfg['minutos_actualizacion']) * 60))

# ---------------------------------------------------------------- servidor web
def make_handler(mon, cfg):
    token = base64.b64encode(f"{cfg['usuario']}:{cfg['contrasena']}".encode()).decode()
    with open(os.path.join(AQUI, 'panel.html'), 'rb') as f: page = f.read()
    icon_path = os.path.join(AQUI, 'icon.png')
    try:
        with open(icon_path, 'rb') as f: icon_png = f.read()
    except OSError:
        icon_png = b''
    manifest = json.dumps({'name':'Control Holandesa','short_name':'Control Holandesa','start_url':'/','display':'standalone','background_color':'#f7f7f4','theme_color':'#ffffff','icons':[{'src':'/icon.png','sizes':'512x512','type':'image/png'}]}, ensure_ascii=False).encode('utf-8')
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
        def do_POST(self):
            if not self._auth(): return
            if self.path not in ('/api/eliminar-abierta', '/api/quitada-motivo', '/api/gastos'): self.send_response(404); self.end_headers(); return
            if not self.headers.get('Content-Type','').lower().startswith('application/json') or self.headers.get('X-Holandesa-Action') != '1':
                self.send_response(403); self.end_headers(); return
            try:
                n=min(int(self.headers.get('Content-Length','0') or 0),262144)
                d=json.loads(self.rfile.read(n).decode('utf-8'))
                if self.path == '/api/gastos': out=mon.gasto_accion(d)
                elif self.path == '/api/quitada-motivo': out=mon.cambiar_motivo(d.get('id'),d.get('motivo'),d.get('nota'))
                else: out=mon.eliminar_abierta(d.get('archivo'),d.get('referencia'),d.get('motivo') or 'Sin clasificar',d.get('nota'))
                body=json.dumps(out,ensure_ascii=False).encode('utf-8')
                return self._send(body,'application/json; charset=utf-8')
            except Exception as e:
                log('No se pudo completar '+self.path+': '+str(e))
                body=json.dumps({'ok':False,'error':str(e)},ensure_ascii=False).encode('utf-8')
                self.send_response(409); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if not self._auth(): return
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
    nuevo = os.path.join(AQUI, '_nuevo'); resp = os.path.join(AQUI, '_respaldo', 'programa')
    shutil.rmtree(nuevo, ignore_errors=True); os.makedirs(nuevo, exist_ok=True)
    try:
        archivos = [a.strip() for a in _bajar('archivos.txt').decode().splitlines() if a.strip()]
        permitidos = {'holandesa_monitor.py', 'panel.html', 'detener_monitor.bat', 'iniciar_monitor.vbs', 'probar.bat', 'actualizar.bat', 'icon.png'}
        archivos = [a for a in archivos if a in permitidos]
        if 'holandesa_monitor.py' not in archivos: raise RuntimeError('archivos.txt incompleto')
        for a in archivos:
            with open(os.path.join(nuevo, a), 'wb') as f: f.write(_bajar(a))
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
    _lanzar([_pythonw(), os.path.join(AQUI, 'holandesa_monitor.py'), '--esperar'])
    os._exit(0)

def responde(puerto):
    import socket
    try:
        with socket.create_connection(('127.0.0.1', int(puerto)), timeout=3): return True
    except OSError: return False

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
    threading.Thread(target=mon.loop, daemon=True).start()
    if cfg.get('actualizar_solo', True): threading.Thread(target=ciclo_actualizacion, daemon=True).start()
    srv = None
    for intento in range(30):
        try:
            ThreadingHTTPServer.allow_reuse_address = (os.name != 'nt')
            srv = ThreadingHTTPServer(('0.0.0.0', int(cfg['puerto'])), make_handler(mon, cfg)); break
        except OSError:
            time.sleep(2)
    if srv is None: log('El puerto sigue ocupado; no pude arrancar.'); sys.exit(1)
    log(f"Monitor versión {VERSION} listo en el puerto {cfg['puerto']} (usuario: {cfg['usuario']})")
    threading.Thread(target=instalar_vigilante, daemon=True).start()
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
        print('Versión actual:', VERSION); print('Instalé versión nueva' if buscar_actualizacion() else 'No hay versión nueva')
    elif len(sys.argv) > 1 and sys.argv[1] == '--prueba':
        cfg = load_config(); m = Monitor(cfg); m.refresh()
        d = json.loads(m.json); h = d['detalle'][d['hasta']]
        print('Último día:', d['hasta'], 'Venta:', h['total'], 'Tickets:', h['tickets'])
    else:
        main()
