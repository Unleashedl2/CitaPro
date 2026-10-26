"""Base de datos SQLite y utilidades compartidas (usado por app.py, install.py y cron.py)."""
import os, json, time, sqlite3, re, threading, base64, urllib.request, urllib.parse, urllib.error, hmac, hashlib, secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import config

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
TENANTS = os.path.join(DATA, 'tenants')      # una base de datos SQLite por negocio
UPLOADS = os.path.join(DATA, 'uploads')      # fotos de cada negocio (carpeta propia)
MASTER_DB = os.path.join(DATA, 'master.db')  # registro de negocios + cuenta Master
for _d in (DATA, TENANTS, UPLOADS): os.makedirs(_d, exist_ok=True)
try:
    TZ = ZoneInfo(config.TZ)
except Exception:   # Windows sin base de zonas horarias: Venezuela es UTC-4 todo el año
    from datetime import timezone, timedelta
    TZ = timezone(timedelta(hours=-4))

# Rubros disponibles y sus servicios iniciales (el administrador luego los edita)
TYPES = {
    'barberia': {'n': 'Barbería', 'svc': [('Corte de cabello', 8), ('Barba', 5), ('Corte + barba', 12), ('Cejas', 3)]},
    'peluqueria': {'n': 'Peluquería', 'svc': [('Corte', 15), ('Tinte', 40), ('Brushing', 12), ('Tratamiento capilar', 25)]},
    'manicure': {'n': 'Manicure', 'svc': [('Manos', 10), ('Pies', 12), ('Completo (manos y pies)', 20)]},
}
RESERVED = {'master', 'api', 'static', 'admin', 'www', 'uploads', 'login', 'app', 'qr', 'sw', 'assets', 'index', 'manifest'}
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$')

def slugify(name):
    import unicodedata
    t = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', t)).strip('-')[:40].strip('-')

def tenant_path(slug): return os.path.join(TENANTS, slug + '.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, pass TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'client', name TEXT, phone TEXT UNIQUE, ci TEXT,
  must_change INTEGER DEFAULT 0, created INTEGER);
CREATE TABLE IF NOT EXISTS docs(col TEXT, id TEXT, data TEXT NOT NULL, updated INTEGER, PRIMARY KEY(col,id));
CREATE TABLE IF NOT EXISTS resets(phone TEXT PRIMARY KEY, hash TEXT, exp INTEGER, tries INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempts(k TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);
INSERT OR IGNORE INTO meta VALUES('rev',1);
"""

VIEWS = """
DROP VIEW IF EXISTS v_citas; DROP VIEW IF EXISTS v_pagos; DROP VIEW IF EXISTS v_clientes;
DROP VIEW IF EXISTS v_servicios; DROP VIEW IF EXISTS v_ingresos;
CREATE VIEW v_citas AS
SELECT d.id AS id_cita, json_extract(d.data,'$.date') AS fecha, json_extract(d.data,'$.time') AS hora,
  json_extract(d.data,'$.svc') AS servicio, json_extract(d.data,'$.price') AS precio_usd,
  json_extract(d.data,'$.bsAmt') AS monto_bs, json_extract(d.data,'$.rate') AS tasa,
  json_extract(d.data,'$.pay') AS metodo_pago, json_extract(d.data,'$.status') AS estado,
  u.name AS cliente, u.phone AS telefono, u.ci AS cedula,
  json_extract(d.data,'$.rating') AS estrellas, json_extract(d.data,'$.comment') AS comentario
FROM docs d LEFT JOIN users u ON u.id = CAST(json_extract(d.data,'$.client') AS INTEGER)
WHERE d.col='appts';
CREATE VIEW v_pagos AS
SELECT p.id AS id_cita, json_extract(p.data,'$.ref') AS referencia, json_extract(p.data,'$.bank') AS banco,
  json_extract(p.data,'$.payName') AS titular, json_extract(p.data,'$.ci') AS cedula_titular,
  a.fecha, a.hora, a.servicio, a.precio_usd, a.monto_bs, a.metodo_pago, a.estado, a.cliente
FROM docs p LEFT JOIN v_citas a ON a.id_cita = p.id WHERE p.col='pay';
CREATE VIEW v_clientes AS
SELECT id, username AS usuario, name AS nombre, phone AS telefono, ci AS cedula, role AS rol,
  datetime(created,'unixepoch','localtime') AS registrado FROM users;
CREATE VIEW v_servicios AS
SELECT json_extract(j.value,'$.name') AS servicio, json_extract(j.value,'$.price') AS precio_usd
FROM docs d, json_each(d.data,'$.svc') j WHERE d.col='cfg' AND d.id='main';
CREATE VIEW v_ingresos AS
SELECT substr(fecha,1,7) AS mes, COUNT(*) AS citas_pagadas, SUM(precio_usd) AS ingresos_usd
FROM v_citas WHERE estado IN ('confirmada','completada') AND (metodo_pago != 'efectivo' OR estado = 'completada')
GROUP BY mes ORDER BY mes DESC;
"""
MSCHEMA = """
CREATE TABLE IF NOT EXISTS musers(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, pass TEXT NOT NULL, name TEXT);
CREATE TABLE IF NOT EXISTS tenants(slug TEXT PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE NOT NULL, type TEXT NOT NULL,
  admin_user TEXT, active INTEGER DEFAULT 1, created INTEGER);
CREATE TABLE IF NOT EXISTS attempts(k TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS billing(slug TEXT PRIMARY KEY, start TEXT, end TEXT, amount REAL DEFAULT 0, ref TEXT, paid_on TEXT, notified TEXT);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, amount REAL NOT NULL, ref TEXT,
  paid_on TEXT NOT NULL, period_start TEXT, period_end TEXT, created INTEGER);
"""
_ctx = threading.local()

def _open(path, *scripts):
    c = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)  # transacciones manuales
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')      # WAL + NORMAL: mucho más rápido, seguro ante caídas de la app
    c.execute('PRAGMA busy_timeout=5000')
    c.execute('PRAGMA temp_store=MEMORY')
    for sc in scripts: c.executescript(sc)
    try: os.chmod(path, 0o600)
    except OSError: pass
    return c

def use(slug):
    """Selecciona el negocio (su base de datos) para las funciones de este hilo."""
    _ctx.slug = slug

_all, _gen, _lock = {}, {}, threading.Lock()

def db():
    """Conexión a la base de datos del negocio activo."""
    slug = getattr(_ctx, 'slug', None)
    if not slug: raise RuntimeError('Sin negocio activo')
    conns = _ctx.__dict__.setdefault('conns', {})
    e = conns.get(slug)
    if e is None or e[1] != _gen.get(slug, 0):          # nueva, o cerrada por close_tenant()
        c = _open(tenant_path(slug), SCHEMA, VIEWS)
        with _lock: _all.setdefault(slug, []).append(c)
        e = conns[slug] = (c, _gen.get(slug, 0))
    return e[0]

def close_tenant(slug):
    """Cierra todas las conexiones abiertas a la base de un negocio (todos los hilos)."""
    with _lock:
        _gen[slug] = _gen.get(slug, 0) + 1; cs = _all.pop(slug, [])
    for c in cs:
        try: c.close()
        except Exception: pass

def mdb():
    """Conexión a la base de datos maestra (negocios y cuenta Master)."""
    conns = _ctx.__dict__.setdefault('conns', {})
    if '__m__' not in conns:
        m = _open(MASTER_DB, MSCHEMA)
        try: m.execute('ALTER TABLE musers ADD COLUMN totp TEXT')   # doble verificación (instalaciones antiguas)
        except sqlite3.OperationalError: pass
        try: m.execute('ALTER TABLE musers ADD COLUMN recovery TEXT')  # códigos de recuperación (hash)
        except sqlite3.OperationalError: pass
        try: m.execute('ALTER TABLE musers ADD COLUMN trusted TEXT')   # equipos de confianza (hash + vencimiento)
        except sqlite3.OperationalError: pass
        # historial de pagos: en instalaciones anteriores se toma el último pago de cada negocio
        m.execute("""INSERT INTO payments(slug,amount,ref,paid_on,period_start,period_end,created)
                     SELECT b.slug,b.amount,b.ref,b.paid_on,b.start,b.end,0 FROM billing b
                     WHERE b.paid_on!='' AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.slug=b.slug)""")
        conns['__m__'] = m
    return conns['__m__']

def tenants(only_active=False):
    q = 'SELECT * FROM tenants' + (' WHERE active=1' if only_active else '') + ' ORDER BY created DESC'
    return [dict(r) for r in mdb().execute(q).fetchall()]

def _stats_raw(slug):
    """Cifras de un negocio, leyendo su base de datos en modo solo lectura."""
    z = {'clients': 0, 'citas': 0, 'hoy': 0, 'ingresos_mes': 0, 'last_seen': 0}
    p = tenant_path(slug)
    if not os.path.exists(p): return z
    today = datetime.now(TZ).strftime('%Y-%m-%d')
    try:
        c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=5)
        q1 = lambda sql, a=(): c.execute(sql, a).fetchone()[0]
        j = lambda k: f"json_extract(data,'$.{k}')"
        z['clients'] = q1("SELECT COUNT(*) FROM users WHERE role='client'")
        z['citas'] = q1("SELECT COUNT(*) FROM docs WHERE col='appts'")
        z['hoy'] = q1(f"SELECT COUNT(*) FROM docs WHERE col='appts' AND {j('date')}=? AND {j('status')} NOT IN ('cancelada','rechazada')", (today,))
        z['ingresos_mes'] = q1(f"SELECT COALESCE(SUM({j('price')}),0) FROM docs WHERE col='appts' AND substr({j('date')},1,7)=? "
                               f"AND {j('status')} IN ('confirmada','completada') AND ({j('pay')}!='efectivo' OR {j('status')}='completada')", (today[:7],))
        r = c.execute("SELECT v FROM meta WHERE k='last_seen'").fetchone(); z['last_seen'] = r[0] if r else 0
        c.close()
    except sqlite3.Error:
        pass
    return z

def bump(): db().execute("UPDATE meta SET v=v+1 WHERE k='rev'")
def rev_now(): return db().execute("SELECT v FROM meta WHERE k='rev'").fetchone()[0]

def get_doc(col, id):
    r = db().execute('SELECT data FROM docs WHERE col=? AND id=?', (col, str(id))).fetchone()
    return json.loads(r[0]) if r else None

def put_doc(col, id, d):
    d = {k: v for k, v in d.items() if k != 'id'}
    db().execute('INSERT OR REPLACE INTO docs(col,id,data,updated) VALUES(?,?,?,?)',
                 (col, str(id), json.dumps(d, ensure_ascii=False), int(time.time() * 1000)))
    bump()

def del_doc(col, id):
    db().execute('DELETE FROM docs WHERE col=? AND id=?', (col, str(id))); bump()

def all_docs(col, limit=0):
    q = 'SELECT id,data FROM docs WHERE col=? ORDER BY updated DESC' + (' LIMIT %d' % int(limit) if limit else '')
    out = []
    for r in db().execute(q, (col,)).fetchall():
        d = json.loads(r['data']); d['id'] = r['id']; out.append(d)
    return out

def too_many(k, mx, sec, c=None):
    c = c or db(); now = int(time.time())
    c.execute('DELETE FROM attempts WHERE ts<?', (now - 86400,))
    n = c.execute('SELECT COUNT(*) FROM attempts WHERE k=? AND ts>?', (k, now - sec)).fetchone()[0]
    if n >= mx: return True
    c.execute('INSERT INTO attempts VALUES(?,?)', (k, now)); return False

def norm_phone(v):
    d = re.sub(r'[^\d+]', '', str(v or ''))
    if not d: return ''
    if d[0] == '+': return d
    d = d.lstrip('0')
    return '+' + d if d.startswith('58') else '+58' + d

def local_ts(date, time_):
    return datetime.strptime(f'{date} {time_}', '%Y-%m-%d %H:%M').replace(tzinfo=TZ).timestamp()

def sms_on():
    s = config.SMS; return bool(s['sid'] and s['token'] and s['from'])

def sms(to, text):
    if not sms_on(): return False
    s = config.SMS
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{s['sid']}/Messages.json",
        data=urllib.parse.urlencode({'To': to, 'From': s['from'], 'Body': text}).encode())
    req.add_header('Authorization', 'Basic ' + base64.b64encode(f"{s['sid']}:{s['token']}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return 200 <= r.status < 300
    except Exception:
        return False

def backup_all(keep=14):
    """Copia consistente de la base maestra y de la de cada negocio en data/backups/."""
    d = os.path.join(DATA, 'backups'); os.makedirs(d, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M'); made = []
    items = [('master', MASTER_DB)] + [(t['slug'], tenant_path(t['slug'])) for t in tenants()]
    for name, path in items:
        if not os.path.exists(path): continue
        out = os.path.join(d, f'{name}-{stamp}.db')
        src = sqlite3.connect(path); dst = sqlite3.connect(out); src.backup(dst); dst.close(); src.close(); made.append(out)
        old = sorted(f for f in os.listdir(d) if re.fullmatch(re.escape(name) + r'-\d{8}-\d{4}\.db', f))
        for f in old[:-keep]: os.remove(os.path.join(d, f))
    return made


# ================= Tasa BCV automática =================
BCV_URL = 'https://www.bcv.org.ve/'
BCV_TTL = 3600   # consulta como máximo cada hora

def _http_get(url):
    """Descarga la página del BCV. Si el certificado del sitio falla, reintenta sin verificarlo
    (solo para esta lectura pública de tasas; no se envían datos sensibles)."""
    import ssl
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (CitaPro)'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.read().decode('utf-8', 'ignore')
    except urllib.error.URLError as e:
        if 'CERTIFICATE' not in str(e).upper() and not isinstance(getattr(e, 'reason', None), ssl.SSLError): raise
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r: return r.read().decode('utf-8', 'ignore')

def _num(txt):
    """'1.234,56' -> 1234.56  (formato venezolano)"""
    return float(txt.strip().replace('.', '').replace(',', '.'))

def parse_bcv(html):
    """Lee la tasa oficial del dólar (USD) y del euro (EUR) de la página del BCV.
    En la página, cada moneda está en un bloque con id="dolar" / id="euro" y el valor en <strong class="strong-tb">."""
    out = {}
    for key, ident, code in (('usd', 'dolar', 'USD'), ('eur', 'euro', 'EUR')):
        m = re.search(r'id=["\']%s["\'][^>]{0,200}>.{0,800}?<strong[^>]*>\s*([0-9][0-9.,]*)\s*</strong>' % ident, html, re.S | re.I)
        if not m:   # plan B: etiqueta visible (USD / EUR) seguida del número
            m = re.search(r'>\s*%s\s*</span>.{0,400}?<strong[^>]*>\s*([0-9][0-9.,]*)\s*</strong>' % code, html, re.S | re.I)
        if not m: raise ValueError('No se encontró la tasa del ' + ('dólar' if key == 'usd' else 'euro') + ' en la página del BCV')
        out[key] = round(_num(m.group(1)), 4)
    if not (1 < out['usd'] < 1e9 and 1 < out['eur'] < 1e9): raise ValueError('Valores fuera de rango: %s' % out)
    return out

def get_bcv():
    r = mdb().execute("SELECT v FROM kv WHERE k='bcv'").fetchone()
    return json.loads(r[0]) if r else {'usd': 0, 'eur': 0, 'upd': '', 'ok': False, 'err': 'Aún no se ha consultado el BCV', 'at': 0}

def update_rates(force=False):
    """Consulta el BCV y copia la tasa a todos los negocios activos. Respeta BCV_TTL salvo force=True."""
    cur = get_bcv()
    if not force and cur.get('at') and time.time() - cur['at'] < BCV_TTL: return cur
    now = datetime.now(TZ)
    try:
        r = parse_bcv(_http_get(BCV_URL))
        new = {'usd': r['usd'], 'eur': r['eur'], 'upd': now.strftime('%d/%m/%Y %H:%M'), 'at': time.time(), 'ok': True, 'err': ''}
    except Exception as e:
        new = {**cur, 'at': time.time(), 'ok': False, 'err': str(e)[:200]}   # conserva la última tasa válida
    mdb().execute("INSERT OR REPLACE INTO kv(k,v) VALUES('bcv',?)", (json.dumps(new, ensure_ascii=False),))
    if new['ok']:
        for t in tenants(only_active=True):
            use(t['slug'])
            try:
                cfg = get_doc('cfg', 'main')
                if cfg is not None:
                    cfg['rates'] = {'usd': new['usd'], 'eur': new['eur'], 'upd': new['upd']}
                    put_doc('cfg', 'main', cfg)
            finally: use(None)
    return new

# ================= Reportes para la sala de control =================
def _report_raw(slug, month=None):
    """Ingresos y clientes confirmados de un negocio (solo lectura). month='AAAA-MM' o None = histórico."""
    out = {'ingresos': 0.0, 'clientes': 0, 'citas': 0}
    p = tenant_path(slug)
    if not os.path.exists(p): return out
    try:
        c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=5)
        rows = c.execute("SELECT data FROM docs WHERE col='appts'").fetchall(); c.close()
    except sqlite3.Error:
        return out
    cl = set()
    for (raw,) in rows:
        a = json.loads(raw)
        if month and not str(a.get('date', '')).startswith(month): continue
        if a.get('status') not in ('confirmada', 'completada'): continue
        out['citas'] += 1; cl.add(a.get('client'))
        if a.get('pay') != 'efectivo' or a.get('status') == 'completada':
            out['ingresos'] += float(a.get('price') or 0)
    out['clientes'] = len(cl)
    return out


_CACHE, _CACHE_LOCK = {}, threading.Lock()
def _cached(key, fn, ttl=60):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl: return hit[1]
    val = fn()
    with _CACHE_LOCK:
        if len(_CACHE) > 500: _CACHE.clear()
        _CACHE[key] = (now, val)
    return val

def stats(slug): return _cached(('stats', slug), lambda: _stats_raw(slug))
def report(slug, month=None): return _cached(('report', slug, month), lambda: _report_raw(slug, month))

# ================= Doble verificación (TOTP, RFC 6238) =================
def new_totp_secret(): return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
def totp_code(secret, at=None, step=30, digits=6):
    key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // step)
    h = hmac.new(key, counter.to_bytes(8, 'big'), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((int.from_bytes(h[o:o + 4], 'big') & 0x7FFFFFFF) % 10 ** digits).zfill(digits)
def totp_ok(secret, code):
    code = re.sub(r'\s', '', str(code or ''))
    if not re.fullmatch(r'\d{6}', code): return False
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + d * 30), code) for d in (-1, 0, 1))


# ================= Suscripción mensual =================
PERIOD_DAYS = 30   # cada periodo cubre 30 días completos (incluido el primero)

def today_local():
    return datetime.now(TZ).date()

def _d(s):
    return datetime.strptime(s, '%Y-%m-%d').date()

def get_billing(slug):
    """Periodo vigente del negocio. Si no existe (negocios anteriores), arranca hoy."""
    m = mdb()
    r = m.execute('SELECT * FROM billing WHERE slug=?', (slug,)).fetchone()
    if r: return dict(r)
    start = today_local()
    end = start + timedelta(days=PERIOD_DAYS - 1)
    m.execute('INSERT INTO billing(slug,start,end,amount,ref,paid_on,notified) VALUES(?,?,?,0,"","","")',
              (slug, start.isoformat(), end.isoformat()))
    return get_billing(slug)

def billing_status(b):
    """estado: 'al_dia' | 'por_vencer' (últimos 3 días, incluido el último) | 'vencido' (desde las 00:00 del día siguiente)."""
    days = (_d(b['end']) - today_local()).days
    if days < 0: estado = 'vencido'
    elif days <= 2: estado = 'por_vencer'
    else: estado = 'al_dia'
    return {'estado': estado, 'dias_restantes': days, 'inicio': b['start'], 'vence': b['end'], 'ultimo_pago': b.get('paid_on') or '', 'monto': b.get('amount') or 0, 'ref': b.get('ref') or ''}

def register_payment(slug, amount, ref, paid_on=None):
    """Registra un pago. Regla: si el periodo sigue vigente, el nuevo empieza el día siguiente al vencimiento
    (nunca se regalan ni se pierden días). Si ya venció, empieza hoy."""
    b = get_billing(slug)
    today = today_local()
    end = _d(b['end'])
    new_start = end + timedelta(days=1) if end >= today else today
    new_end = new_start + timedelta(days=PERIOD_DAYS - 1)
    paid = paid_on or today.isoformat()
    mdb().execute('UPDATE billing SET start=?, end=?, amount=?, ref=?, paid_on=?, notified=? WHERE slug=?',
                  (new_start.isoformat(), new_end.isoformat(), float(amount or 0), str(ref or ''), paid, '', slug))
    mdb().execute('INSERT INTO payments(slug,amount,ref,paid_on,period_start,period_end,created) VALUES(?,?,?,?,?,?,?)',
                  (slug, float(amount or 0), str(ref or ''), paid, new_start.isoformat(), new_end.isoformat(), int(time.time() * 1000)))
    return get_billing(slug)

def notify_billing():
    """Avisa al administrador los últimos 3 días, una vez por día."""
    n = 0
    for t in tenants(only_active=True):
        b = get_billing(t['slug']); st = billing_status(b)
        if st['estado'] != 'por_vencer' or b.get('notified') == today_local().isoformat(): continue
        use(t['slug'])
        try:
            when = _d(b['end']).strftime('%d/%m/%Y')
            txt = (f"⚠️ Tu suscripción vence el {when} (en {st['dias_restantes']} día(s) o hoy). "
                   f"Si no se renueva, la app se suspende a partir de las 00:00 del día siguiente.")
            put_doc('anotifs', 'bill' + today_local().isoformat(), {'text': txt, 'apptId': None, 'ts': int(time.time() * 1000), 'read': False})
        finally: use(None)
        name = t['name']; fecha = _d(b['end']).strftime('%d/%m/%Y'); dias = str(st['dias_restantes'])
        if wa_enabled():
            wa_send_template(admin_phone(t['slug']), [name, fecha, dias])
            if config.MASTER_PHONE: wa_send_template(config.MASTER_PHONE, [name, fecha, dias])
        use(t['slug'])
        try: push_to_role('admin', 'Vencimiento de tu suscripción', f'Vence el {fecha} (faltan {dias} día(s)).', './')
        finally: use(None)
        mdb().execute('UPDATE billing SET notified=? WHERE slug=?', (today_local().isoformat(), t['slug']))
        n += 1
    return n


# ================= WhatsApp (Meta Cloud API) =================
def _wa_digits(phone):
    return re.sub(r'\D', '', str(phone or ''))

def wa_configured():
    w = config.WHATSAPP
    return bool(w.get('token') and w.get('phone_id'))

def wa_enabled():
    """Interruptor del Master (sala de control). Activado por defecto si WhatsApp está configurado."""
    r = mdb().execute("SELECT v FROM kv WHERE k='wa_on'").fetchone()
    return wa_configured() and (r is None or r[0] == '1')

def wa_set_enabled(on):
    mdb().execute("INSERT OR REPLACE INTO kv(k,v) VALUES('wa_on',?)", ('1' if on else '0',))

def wa_on():
    return wa_configured()

def wa_send_template(phone, params):
    """Envía la plantilla aprobada con sus variables ({{1}}, {{2}}…). Devuelve True si Meta la aceptó."""
    to = _wa_digits(phone)
    if not wa_on() or len(to) < 10: return False
    w = config.WHATSAPP
    body = {'messaging_product': 'whatsapp', 'to': to, 'type': 'template',
            'template': {'name': w['template'], 'language': {'code': w['lang']},
                         'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': str(p)} for p in params]}]}}
    req = urllib.request.Request(f"https://graph.facebook.com/v20.0/{w['phone_id']}/messages",
                                 data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + w['token'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return 200 <= r.status < 300
    except Exception:
        return False

def admin_phone(slug):
    """Teléfono del administrador del negocio (guardado en su base)."""
    p = tenant_path(slug)
    if not os.path.exists(p): return ''
    try:
        c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=5)
        r = c.execute("SELECT phone FROM users WHERE role='admin' LIMIT 1").fetchone(); c.close()
        return r[0] if r and r[0] else ''
    except sqlite3.Error:
        return ''


# ================= Notificaciones push (Web Push / VAPID) =================
VAPID_PEM = os.path.join(DATA, 'vapid_private.pem')
VAPID_CLAIM = 'mailto:' + (os.environ.get('CITAPRO_CONTACT', 'soporte@citapro.app'))

def vapid_public_key():
    """Clave pública VAPID (se crea una sola vez y se guarda en data/). Los navegadores la usan para suscribirse."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if not os.path.exists(VAPID_PEM):
        key = ec.generate_private_key(ec.SECP256R1())
        with open(VAPID_PEM, 'wb') as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        try: os.chmod(VAPID_PEM, 0o600)
        except OSError: pass
    with open(VAPID_PEM, 'rb') as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')

def push_send(sub, title, body, url='./'):
    """Envía una notificación a un dispositivo suscrito. Devuelve 'ok', 'gone' (ya no existe) o 'error'."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return 'error'
    try:
        webpush(subscription_info=sub, data=json.dumps({'title': title, 'body': body, 'url': url}, ensure_ascii=False),
                vapid_private_key=VAPID_PEM, vapid_claims={'sub': VAPID_CLAIM}, ttl=3600)
        return 'ok'
    except Exception as e:
        if getattr(getattr(e, 'response', None), 'status_code', 0) in (404, 410): return 'gone'
        return 'error'

def push_to_uid(uid, title, body, url='./'):
    """Envía a todos los dispositivos suscritos de un usuario del negocio activo."""
    n = 0
    for d in all_docs('push'):
        if str(d.get('uid')) != str(uid): continue
        res = push_send(d['sub'], title, body, url)
        if res == 'gone': del_doc('push', d['id'])
        elif res == 'ok': n += 1
    return n

def admin_uid():
    r = db().execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    return str(r[0]) if r else None

def push_to_role(role, title, body, url='./'):
    if role == 'admin':
        uid = admin_uid()
        return push_to_uid(uid, title, body, url) if uid else 0
    return 0


def income_report():
    """Ingresos por suscripciones: total, mes en curso, mes anterior y listado de pagos (más reciente primero)."""
    m = mdb()
    today = today_local()
    mes = today.strftime('%Y-%m')
    prev = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    def tot(pref=None):
        if pref: r = m.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE substr(paid_on,1,7)=?', (pref,)).fetchone()
        else: r = m.execute('SELECT COALESCE(SUM(amount),0) FROM payments').fetchone()
        return round(float(r[0] or 0), 2)
    names = {t['slug']: t['name'] for t in tenants()}
    rows = [dict(r) for r in m.execute('SELECT * FROM payments ORDER BY paid_on DESC, id DESC LIMIT 300').fetchall()]
    for r in rows: r['negocio'] = names.get(r['slug'], r['slug'] + ' (eliminado)')
    por_negocio = [dict(r) for r in m.execute(
        'SELECT slug, COUNT(*) AS pagos, SUM(amount) AS total, MAX(paid_on) AS ultimo FROM payments GROUP BY slug ORDER BY total DESC').fetchall()]
    for r in por_negocio:
        r['negocio'] = names.get(r['slug'], r['slug'] + ' (eliminado)'); r['total'] = round(float(r['total'] or 0), 2)
    return {'total': tot(), 'mes': tot(mes), 'mes_anterior': tot(prev), 'mes_label': mes, 'negocios_pagaron': len(por_negocio),
            'pagos': rows, 'por_negocio': por_negocio}
