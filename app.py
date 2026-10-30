"""CitaPro – servidor Flask multi-negocio. Cada negocio tiene su propia base de datos SQLite.
   /master/        sala de control (Master)        /<negocio>/   la app de cada negocio (PWA)"""
import os, re, json, time, secrets, hashlib, hmac, base64
from datetime import timedelta, datetime
from flask import Flask, request, session, jsonify, send_from_directory, g, Response, abort
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from core import *
import qr as qrlib

PUB = os.path.join(BASE, 'public')
app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(MAX_CONTENT_LENGTH=4 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_NAME='citapro_session', PERMANENT_SESSION_LIFETIME=timedelta(days=30))
_kf = os.path.join(DATA, 'secret.key')
if not os.path.exists(_kf):
    with open(_kf, 'w') as f: f.write(secrets.token_hex(32))
    os.chmod(_kf, 0o600)
app.secret_key = open(_kf).read().strip()

class _Sess(SecureCookieSessionInterface):
    def get_cookie_secure(self, app): return request.is_secure          # Secure automático si hay HTTPS
    def get_cookie_path(self, app):                                      # cada negocio tiene su propia sesión
        seg = request.path.strip('/').split('/')[0]
        return '/%s/' % seg if seg else '/'
app.session_interface = _Sess()

@app.after_request
def _security_headers(r):
    r.headers.setdefault('X-Content-Type-Options', 'nosniff')
    r.headers.setdefault('X-Frame-Options', 'DENY')
    r.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    r.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
    r.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    r.headers.setdefault('Content-Security-Policy',
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.is_secure: r.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return r

class Err(Exception):
    def __init__(s, m, c=400): s.m, s.c = m, c
@app.errorhandler(Err)
def _err(e): return jsonify({'error': e.m}), e.c
@app.errorhandler(413)
def _big(e): return jsonify({'error': 'Archivo demasiado grande'}), 413
@app.errorhandler(404)
def _nf(e):
    if request.path.endswith(('api.php', '/api')): return jsonify({'error': 'No encontrado'}), 404
    return Response('<h3>No encontrado</h3>', 404, mimetype='text/html')

def norm(v): return re.sub(r'[^a-z0-9._-]', '', str(v or '').strip().lower())
def ip(): return request.remote_addr or '0'
def s(v): return v if isinstance(v, str) else ''
def valid_theme(t):
    """Color principal (#rrggbb) y modo de la app de cada negocio; valores inválidos vuelven al predeterminado."""
    t = t if isinstance(t, dict) else {}
    acc = t.get('accent') if isinstance(t.get('accent'), str) else ''
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', acc): acc = '#b4532a'
    mode = t.get('mode') if t.get('mode') in ('auto', 'light', 'dark') else 'auto'
    return {'accent': acc.lower(), 'mode': mode}
def public_url(slug=''):
    base = (getattr(config, 'PUBLIC_URL', '') or request.url_root).rstrip('/')
    return f'{base}/{slug}/' if slug else base

# ---------------- negocio activo según la URL ----------------
@app.before_request
def pick_tenant():
    g.slug = g.tenant = None
    seg = request.path.strip('/').split('/')[0]
    if seg == 'master' or not seg: use(None); return
    row = mdb().execute('SELECT * FROM tenants WHERE slug=?', (seg,)).fetchone() if SLUG_RE.match(seg) else None
    if not row: use(None); abort(404)
    if not row['active']:
        use(None)
        if request.path.endswith('api.php'): return jsonify({'error': 'Este negocio está suspendido. Contacta a tu proveedor.'}), 503
        return Response('<meta name=viewport content="width=device-width"><h2 style="font-family:sans-serif;text-align:center;margin-top:30vh">'
                        'Servicio suspendido temporalmente</h2>', 503, mimetype='text/html')
    if billing_status(get_billing(seg))['estado'] == 'vencido':
        use(None)
        if request.path.endswith('api.php'): return jsonify({'error': 'Servicio suspendido por falta de pago. Contacta a tu proveedor.'}), 402
        return Response('<meta name=viewport content="width=device-width"><h2 style="font-family:sans-serif;text-align:center;margin-top:30vh">'
                        'Servicio suspendido por falta de pago</h2><p style="font-family:sans-serif;text-align:center">Contacta a tu proveedor para renovar.</p>', 402, mimetype='text/html')
    g.slug, g.tenant = seg, dict(row); use(seg)

def me():
    uid = session.get('uid')
    if not uid or session.get('t') != g.slug: return None      # la sesión solo vale en su propio negocio
    r = db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    return dict(r) if r else None
def login_as(uid): session.clear(); session['uid'] = int(uid); session['t'] = g.slug; session.permanent = True

_seen = {}
def touch():
    now = int(time.time())
    if now - _seen.get(g.slug, 0) > 60:
        _seen[g.slug] = now; db().execute("INSERT OR REPLACE INTO meta VALUES('last_seen',?)", (now,))

# ---------------- páginas ----------------
@app.route('/')
def root():
    return Response('<meta name=viewport content="width=device-width"><body style="font-family:sans-serif;text-align:center;margin-top:28vh;color:#555">'
                    '<h2>CitaPro</h2><p>Usa el enlace o el código QR de tu negocio.</p></body>', mimetype='text/html')

@app.route('/<slug>/')
@app.route('/<slug>/index.html')
def t_index(slug):
    r = send_from_directory(PUB, 'index.html'); r.headers['Cache-Control'] = 'no-cache'; return r

@app.route('/<slug>/manifest.webmanifest')
def t_manifest(slug):
    t = g.tenant; cfg = get_doc('cfg', 'main') or {}; nm = cfg.get('biz') or t['name']
    m = {'name': nm, 'short_name': nm[:12], 'start_url': './', 'scope': './', 'id': f'/{slug}/', 'display': 'standalone', 'lang': 'es',
         'background_color': '#faf7f2', 'theme_color': valid_theme(cfg.get('theme'))['accent'],
         'icons': [{'src': 'icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
                   {'src': 'icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'}]}
    return Response(json.dumps(m), mimetype='application/manifest+json')

@app.route('/<slug>/qr.svg')
def t_qr(slug):
    return Response(qrlib.svg(public_url(slug)), mimetype='image/svg+xml', headers={'Cache-Control': 'no-cache'})

@app.route('/<slug>/uploads/<name>')
def t_upload(slug, name):
    if not re.fullmatch(r'[0-9a-f]{16}\.jpg', name): abort(404)
    return send_from_directory(os.path.join(UPLOADS, slug), name)

@app.route('/<slug>/<name>')
def t_static(slug, name):
    if name not in ('sw.js', 'icon-192.png', 'icon-512.png'): abort(404)
    r = send_from_directory(PUB, name)
    if name == 'sw.js': r.headers['Cache-Control'] = 'no-cache'
    return r

# ---------------- API del negocio ----------------
@app.route('/<slug>/api.php', methods=['GET', 'POST'])
def api(slug):
    if request.method != 'POST' or request.headers.get('X-Requested-With') != 'citapro': raise Err('Solicitud no válida')
    inp = request.get_json(silent=True)
    if not isinstance(inp, dict): inp = {}
    out = dispatch(request.args.get('a', ''), inp)
    try: touch()
    except sqlite3.Error: pass
    r = jsonify(out); r.headers['Cache-Control'] = 'no-store'
    return r

def dispatch(a, inp):
    c = db()
    # ----- públicas -----
    if a == 'register':
        un, name, ph, pw, ci = norm(inp.get('username')), s(inp.get('name')).strip(), norm_phone(inp.get('phone')), inp.get('password'), s(inp.get('ci')).strip()
        if not un or not name or len(ph) < 8 or not isinstance(pw, str) or len(pw) < 6: raise Err('Completa todos los campos (contraseña mín. 6).')
        if un in ('admin', 'master'): raise Err('Ese usuario está reservado.')
        if too_many('reg:' + ip(), 10, 3600): raise Err('Demasiados intentos. Intenta más tarde.', 429)
        try:
            cur = c.execute('INSERT INTO users(username,pass,role,name,phone,ci,created) VALUES(?,?,?,?,?,?,?)',
                            (un, generate_password_hash(pw), 'client', name, ph, ci, int(time.time())))
        except sqlite3.IntegrityError: raise Err('Ese usuario o teléfono ya está registrado.', 409)
        put_doc('profiles', cur.lastrowid, {'name': name, 'phone': ph, 'ci': ci, 'username': un})
        login_as(cur.lastrowid); return {'ok': 1}
    if a == 'login':
        un = norm(inp.get('username'))
        if too_many('login:' + un, 8, 600) or too_many('loginip:' + ip(), 40, 600): raise Err('Demasiados intentos. Espera unos minutos.', 429)
        u = c.execute('SELECT * FROM users WHERE username=?', (un,)).fetchone()
        if not u or not check_password_hash(u['pass'], s(inp.get('password'))): raise Err('Usuario o contraseña incorrectos.', 401)
        login_as(u['id']); return {'ok': 1}
    if a == 'logout': session.clear(); return {'ok': 1}
    if a == 'info':
        cfg = get_doc('cfg', 'main') or {}
        return {'biz': cfg.get('biz') or g.tenant['name'], 'type': cfg.get('type') or g.tenant['type'], 'open': cfg.get('open', True), 'theme': valid_theme(cfg.get('theme')), 'vapid': vapid_public_key()}
    if a == 'me':
        u = me()
        return {'user': {'id': str(u['id']), 'username': u['username'], 'name': u['name'], 'role': u['role'], 'mustChange': bool(u['must_change'])} if u else None}
    if a == 'forgotStart':
        ph = norm_phone(inp.get('phone'))
        if not sms_on(): raise Err('El envío de SMS no está activado. Pide a soporte que restablezca tu contraseña.', 503)
        if too_many('fs:' + ph, 3, 3600) or too_many('fsip:' + ip(), 10, 3600): raise Err('Demasiados intentos. Intenta más tarde.', 429)
        if c.execute('SELECT 1 FROM users WHERE phone=?', (ph,)).fetchone():
            code = str(secrets.randbelow(900000) + 100000)
            c.execute('REPLACE INTO resets(phone,hash,exp,tries) VALUES(?,?,?,0)', (ph, hashlib.sha256((code + ph).encode()).hexdigest(), int(time.time()) + 600))
            sms(ph, f'CitaPro: tu código de verificación es {code}. Vence en 10 minutos.')
        return {'ok': 1}   # misma respuesta exista o no el teléfono
    if a == 'forgotConfirm':
        ph, code, pw = norm_phone(inp.get('phone')), s(inp.get('code')).strip(), inp.get('newPassword')
        if not isinstance(pw, str) or len(pw) < 6: raise Err('La contraseña debe tener al menos 6 caracteres.')
        r = c.execute('SELECT * FROM resets WHERE phone=?', (ph,)).fetchone()
        if not r or r['exp'] < time.time() or r['tries'] >= 5: raise Err('Código vencido o inválido. Solicita uno nuevo.')
        if not hmac.compare_digest(r['hash'], hashlib.sha256((code + ph).encode()).hexdigest()):
            c.execute('UPDATE resets SET tries=tries+1 WHERE phone=?', (ph,)); raise Err('Código incorrecto.')
        u = c.execute('SELECT id FROM users WHERE phone=?', (ph,)).fetchone()
        if not u: raise Err('Cuenta no encontrada.', 404)
        c.execute('UPDATE users SET pass=?, must_change=0 WHERE id=?', (generate_password_hash(pw), u['id']))
        c.execute('DELETE FROM resets WHERE phone=?', (ph,))
        login_as(u['id']); return {'ok': 1}

    # ----- con sesión -----
    u = me()
    if not u: raise Err('Sin sesión', 401)
    uid, role = str(u['id']), u['role']
    if a == 'firstChange':
        if not u['must_change']: raise Err('No aplica.', 403)
        np_, ph = inp.get('newPassword'), norm_phone(inp.get('phone'))
        if not isinstance(np_, str) or len(np_) < 8 or np_ == '123456': raise Err('Contraseña demasiado débil (mín. 8, distinta de 123456).')
        if len(ph) < 8: raise Err('Indica un teléfono válido.')
        try: c.execute('UPDATE users SET pass=?, phone=?, must_change=0 WHERE id=?', (generate_password_hash(np_), ph, uid))
        except sqlite3.IntegrityError: raise Err('Ese teléfono ya está registrado en otra cuenta.', 409)
        p = get_doc('profiles', uid) or {}; p['phone'] = ph; put_doc('profiles', uid, p)
        login_as(uid); return {'ok': 1}
    if u['must_change']: raise Err('Debes cambiar tu contraseña primero.', 403)

    if a == 'changePass':
        np_ = inp.get('new')
        if too_many('cp:' + uid, 5, 600): raise Err('Demasiados intentos.', 429)
        if not check_password_hash(u['pass'], s(inp.get('old'))): raise Err('La contraseña actual no es correcta.', 403)
        if not isinstance(np_, str) or len(np_) < 8 or np_ == '123456': raise Err('Contraseña demasiado débil (mín. 8).')
        c.execute('UPDATE users SET pass=? WHERE id=?', (generate_password_hash(np_), uid)); return {'ok': 1}
    if a == 'adminReset':   # el administrador restablece la clave de un cliente
        if role != 'admin': raise Err('Solo el administrador.', 403)
        t = c.execute('SELECT role FROM users WHERE id=?', (str(inp.get('uid', '')),)).fetchone()
        if not t or t['role'] != 'client': raise Err('Cuenta no encontrada.', 404)
        tmp = secrets.token_hex(4)
        c.execute('UPDATE users SET pass=?, must_change=1 WHERE id=?', (generate_password_hash(tmp), str(inp['uid']))); return {'tmp': tmp}
    if a == 'upload':
        if role != 'admin': raise Err('Sin permiso', 403)
        d = s(inp.get('data'))
        if not d.startswith('data:image/jpeg;base64,'): raise Err('Solo imágenes JPG.')
        try: b = base64.b64decode(d.split(',', 1)[1], validate=True)
        except Exception: raise Err('Imagen inválida.')
        if len(b) > 2_000_000 or b[:3] != b'\xff\xd8\xff': raise Err('Imagen inválida o muy grande (máx. 2 MB).')
        folder = os.path.join(UPLOADS, g.slug); os.makedirs(folder, exist_ok=True)
        fn = secrets.token_hex(8) + '.jpg'
        with open(os.path.join(folder, fn), 'wb') as f: f.write(b)
        return {'url': 'uploads/' + fn}
    if a == 'snapshot': return snapshot(inp, uid, role)
    if a == 'pushSub':
        sub = inp.get('sub')
        if not isinstance(sub, dict) or not str(sub.get('endpoint', '')).startswith('https://'): raise Err('Suscripción no válida')
        key = hashlib.sha256(sub['endpoint'].encode()).hexdigest()[:24]
        put_doc('push', key, {'uid': uid, 'sub': {'endpoint': sub['endpoint'], 'keys': sub.get('keys', {})}})
        return {'ok': 1}
    if a == 'pushOff':
        for d in all_docs('push'):
            if str(d.get('uid')) == uid: del_doc('push', d['id'])
        return {'ok': 1}
    if a == 'doc': return doc_op(inp, u, uid)
    raise Err('Acción desconocida', 404)

_SNAP = {}
def snapshot(inp, uid, role):
    """Estado del negocio para la app. Solo envía lo necesario:
    - citas: ventana de 60 días atrás a 120 días adelante. Un cliente ve sus propias citas completas;
      de las demás solo fecha, hora y estado (nunca nombre, pago ni datos de otros).
    - el resultado se guarda en caché por versión (rev): miles de clientes reciben la misma respuesta sin recalcularla."""
    rev = rev_now()
    if inp.get('rev') == rev: return {'same': 1, 'rev': rev}
    bl = get_billing(g.slug)
    key = (g.slug, rev, role, uid if role == 'client' else '', bl['end'], today_local().isoformat())
    if key in _SNAP: return _SNAP[key]
    today = datetime.now(TZ).date()
    lo, hi = (today - timedelta(days=60)).isoformat(), (today + timedelta(days=120)).isoformat()
    appts = all_docs('appts')
    pub = {}
    for x in appts:
        d = x.get('date', '')
        if not (lo <= d <= hi): continue
        if role != 'client':
            pub[x['id']] = {
                'id': x['id'],
                'date': d,
                'time': x.get('time'),
                'status': x.get('status'),
                'svcId': x.get('svcId'),
                'svc': x.get('svc'),
                'price': x.get('price'),
                'pay': x.get('pay'),
                'bsAmt': x.get('bsAmt'),
                'rate': x.get('rate'),
                'rtype': x.get('rtype'),
                'client': x.get('client'),
                'created': x.get('created'),
                'rating': x.get('rating'),
                'comment': x.get('comment')
            }
        else:
            pub[x['id']] = {
                'id': x['id'],
                'date': d,
                'time': x.get('time'),
                'status': x.get('status')
            }
    if role == 'client':
        out_appts = []
        for x in appts:
            if x.get('client') == uid: out_appts.append(x)            # sus propias citas, completas
            elif x['id'] in pub: out_appts.append(pub[x['id']])       # las demás: solo horario y estado
    else:
        out_appts = list(pub.values())
    ids = {x['id'] for x in out_appts}
    blocks = [b for b in all_docs('blocks') if b.get('date', '') >= today.isoformat()]
    o = {'rev': rev, 'cfg': get_doc('cfg', 'main'), 'appts': out_appts, 'blocks': blocks,
         'notifs': [d for d in all_docs('notifs', 300) if d.get('to') == uid], 'anotifs': [], 'prof': {}, 'pay': {}}
    if role != 'client':
        o['anotifs'] = all_docs('anotifs', 300)
        o['prof'] = {p['id']: p for p in all_docs('profiles')}
        o['pay'] = {p['id']: p for p in all_docs('pay') if p['id'] in ids}
        o['billing'] = billing_status(bl)
    else:
        p = get_doc('profiles', uid)
        if p: o['prof'][uid] = {**p, 'id': uid}
    if len(_SNAP) > 300: _SNAP.clear()
    _SNAP[key] = o
    return o

COLS = ('cfg', 'blocks', 'appts', 'pay', 'notifs', 'anotifs', 'profiles')
def doc_op(inp, u, uid):
    c = db()
    pp = s(inp.get('path')).strip('/').split('/')
    if len(pp) != 2 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', pp[1]): raise Err('Ruta no válida')
    col, id_ = pp
    if col not in COLS: raise Err('Colección no válida', 403)
    op, data = inp.get('op', 'get'), inp.get('data', {})
    if not isinstance(data, dict): data = {}
    if len(json.dumps(data)) > 20000: raise Err('Datos demasiado grandes')
    if op == 'get':
        old = get_doc(col, id_)
        if old and not can_read(col, {**old, 'id': id_}, u): raise Err('Sin permiso', 403)
        return {'exists': bool(old), 'data': old or {}}
    if op not in ('set', 'update', 'delete'): raise Err('Operación no válida')
    c.execute('BEGIN IMMEDIATE')
    try:
        old = get_doc(col, id_)
        if op == 'delete':
            if old:
                if not can_write('delete', col, id_, old, old, u)[0]: raise Err('Sin permiso', 403)
                del_doc(col, id_)
        else:
            if op == 'update' and not old: raise Err('El documento no existe', 404)
            new = {**old, **data} if op == 'update' else data
            ok, new = can_write(op, col, id_, old, new, u)
            if not ok: raise Err('Sin permiso', 403)
            put_doc(col, id_, new)
        c.execute('COMMIT')
    except BaseException:
        c.execute('ROLLBACK'); raise
    return {'ok': 1}

# ---------------- reglas de acceso ----------------
def can_read(col, row, u):
    uid, priv = str(u['id']), u['role'] in ('admin', 'master')
    if col in ('cfg', 'blocks', 'appts'): return True
    if col == 'pay': return priv or row.get('client') == uid
    if col == 'notifs': return priv or row.get('to') == uid
    if col == 'anotifs': return priv
    if col == 'profiles': return priv or row['id'] == uid
    return False

def can_write(op, col, id_, old, new, u):
    """Devuelve (permitido, documento_final). El servidor puede corregir campos sensibles."""
    uid, no = str(u['id']), (False, new)
    if u['role'] == 'master': return no
    if u['role'] == 'admin':
        if col == 'profiles': return no
        if col == 'cfg' and old is not None:      # la tasa BCV la controla el sistema, no el administrador
            new = {**new, 'rates': old.get('rates', {})}
        if col == 'cfg' and 'theme' in new: new = {**new, 'theme': valid_theme(new['theme'])}
        return True, new
    if col == 'appts':
        if op == 'delete': return no
        if op == 'set':
            if new.get('client') != uid: return no
            if old and old.get('status') not in ('cancelada', 'rechazada'): return no
            cfg = get_doc('cfg', 'main')
            if not cfg or cfg.get('open', True) is False: return no
            sv = next((x for x in cfg.get('svc', []) if x.get('id') == new.get('svcId')), None)
            d, t = s(new.get('date')), s(new.get('time'))
            if not sv or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) or not re.fullmatch(r'\d{2}:00', t): return no
            h = int(t[:2])
            try: ts = local_ts(d, t)
            except ValueError: return no
            if h < cfg.get('from', 0) or h >= cfg.get('to', 24) or ts <= time.time(): return no
            if id_ != d + '_' + t.replace(':', ''): return no
            if any(b.get('date') == d and b.get('time') in ('all', t) for b in all_docs('blocks')): return no
            pm = new.get('pay')
            if pm not in ('transferencia', 'pagomovil', 'usdt', 'efectivo'): return no
            rt = cfg.get('rateType', 'usd'); bs = pm in ('transferencia', 'pagomovil')
            try: rate = float((cfg.get('rates') or {}).get(rt) or 0)
            except (TypeError, ValueError): rate = 0
            if bs and rate <= 0: return no
            amt = f"{float(sv['price']) * rate:,.2f}".replace(',', '#').replace('.', ',').replace('#', '.') if bs else ''
            # el servidor fija nombre, precio, monto en Bs y estado: el cliente no puede alterarlos
            return True, {'date': d, 'time': t, 'svcId': sv['id'], 'svc': sv['name'], 'price': sv['price'], 'client': uid, 'pay': pm,
                          'status': 'reservada' if pm == 'efectivo' else 'pendiente', 'created': int(time.time() * 1000),
                          'bsAmt': amt, 'rate': rate if bs else 0, 'rtype': rt}
        if (old or {}).get('client') != uid: return no
        allowed = {'status', 'rating', 'comment', 'doneBy', 'doneAt', 'remC'}
        if any(old.get(k) != v and k not in allowed for k, v in new.items()): return no
        r = new.get('rating')
        if r is not None and (not isinstance(r, int) or isinstance(r, bool) or not 1 <= r <= 5): return no
        ns = new.get('status', old.get('status'))
        return (ns == old.get('status') or ns == 'cancelada' or (ns == 'completada' and old.get('status') in ('confirmada', 'reservada'))), new
    if col == 'pay':
        if op == 'delete' or new.get('client') != uid: return no
        if not old or old.get('client') == uid: return True, new
        return (get_doc('appts', id_) or {}).get('status') in ('cancelada', 'rechazada'), new
    if col == 'notifs':
        if op == 'set': return new.get('to') == uid, new
        return op == 'update' and (old or {}).get('to') == uid, new
    if col == 'anotifs': return op == 'set' and not old, new
    return no

# ---------------- Sala de control (Master) ----------------
ALLOWED_MASTER_IPS = {x.strip() for x in os.environ.get('CITAPRO_MASTER_IPS', '').split(',') if x.strip()}
def master_guard():
    """Si defines CITAPRO_MASTER_IPS, la sala de control solo responde a esas direcciones."""
    if ALLOWED_MASTER_IPS and ip() not in ALLOWED_MASTER_IPS: abort(403)

TRUST_DAYS = 30
TRUST_COOKIE = 'citapro_trust'
def trust_ok(m, u):
    """¿Este navegador es un equipo de confianza vigente? (sin pedir código del celular)"""
    tok = request.cookies.get(TRUST_COOKIE)
    if not tok: return False
    h = hashlib.sha256(tok.encode()).hexdigest()
    lst = json.loads(u['trusted'] or '[]')
    now = time.time()
    lst = [x for x in lst if x['exp'] > now]                      # se descartan los vencidos
    m.execute('UPDATE musers SET trusted=? WHERE id=?', (json.dumps(lst), u['id']))
    return any(hmac.compare_digest(x['h'], h) for x in lst)

def add_trust(m, u, resp):
    tok = secrets.token_urlsafe(32)
    lst = json.loads(u['trusted'] or '[]')
    lst = [x for x in lst if x['exp'] > time.time()][-9:] + [{'h': hashlib.sha256(tok.encode()).hexdigest(), 'exp': time.time() + TRUST_DAYS * 86400}]
    m.execute('UPDATE musers SET trusted=? WHERE id=?', (json.dumps(lst), u['id']))
    resp.set_cookie(TRUST_COOKIE, tok, max_age=TRUST_DAYS * 86400, path='/master/', httponly=True, samesite='Lax', secure=request.is_secure)
    return resp

def use_recovery(m, u, code):
    """Acepta un código de recuperación (un solo uso) en lugar del código de la app."""
    h = hashlib.sha256(re.sub(r'\s', '', str(code or '')).lower().encode()).hexdigest()
    lst = json.loads(u['recovery'] or '[]')
    if h not in lst: return False
    lst.remove(h); m.execute('UPDATE musers SET recovery=? WHERE id=?', (json.dumps(lst), u['id'])); return True

def m_user():
    uid = session.get('m')
    if not uid: return None
    if time.time() - session.get('ma', 0) > 1800:            # 30 min sin actividad: se cierra la sesión
        session.clear(); return None
    session['ma'] = time.time()
    r = mdb().execute('SELECT * FROM musers WHERE id=?', (uid,)).fetchone()
    return dict(r) if r else None

@app.route('/master/')
def master_page():
    master_guard()
    r = send_from_directory(PUB, 'master.html'); r.headers['Cache-Control'] = 'no-cache'; return r
    
    
@app.route('/master/manifest.webmanifest')
def master_manifest():
    manifest = {
        'name': 'CitaPro Master',
        'short_name': 'CitaPro Master',
        'id': '/master/',
        'start_url': '/master/',
        'scope': '/master/',
        'display': 'standalone',
        'lang': 'es',
        'background_color': '#faf7f2',
        'theme_color': '#b4532a',
        'icons': [
            {
                'src': '/master/icon-192.png',
                'sizes': '192x192',
                'type': 'image/png',
                'purpose': 'any'
            },
            {
                'src': '/master/icon-512.png',
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'any'
            }
        ]
    }
    return Response(
        json.dumps(manifest),
        mimetype='application/manifest+json',
        headers={'Cache-Control': 'no-cache'}
    )


@app.route('/master/sw.js')
def master_service_worker():
    r = send_from_directory(PUB, 'master-sw.js')
    r.headers['Cache-Control'] = 'no-cache'
    r.headers['Service-Worker-Allowed'] = '/master/'
    return r


@app.route('/master/api', methods=['POST'])
def master_api():
    master_guard()
    if request.headers.get('X-Requested-With') != 'citapro': raise Err('Solicitud no válida')
    inp = request.get_json(silent=True)
    if not isinstance(inp, dict): inp = {}
    a, m = request.args.get('a', ''), mdb()
    if a == 'login':
        un = norm(inp.get('username'))
        if too_many('m:' + un, 6, 600, m) or too_many('mip:' + ip(), 30, 600, m): raise Err('Demasiados intentos. Espera unos minutos.', 429)
        u = m.execute('SELECT * FROM musers WHERE username=?', (un,)).fetchone()
        if not u or not check_password_hash(u['pass'], s(inp.get('password'))): raise Err('Usuario o contraseña incorrectos.', 401)
        if u['totp'] and not trust_ok(m, u):             # con doble verificación: equipo nuevo o vencido pide código
            session.clear(); session['mp'] = u['id']; session['mpt'] = time.time(); return jsonify({'need2fa': 1})
        if u['totp']: session.clear(); session['m'] = u['id']; session['ma'] = time.time(); session.permanent = True; return jsonify({'ok': 1})
        session.clear(); session['m'] = u['id']; session['ma'] = time.time(); session.permanent = True; return jsonify({'ok': 1})
    if a == 'login2':
        uid = session.get('mp')
        if not uid or time.time() - session.get('mpt', 0) > 300: raise Err('Vuelve a iniciar sesión.', 401)
        if too_many('m2:' + str(uid), 5, 600, m): raise Err('Demasiados intentos. Espera unos minutos.', 429)
        u = m.execute('SELECT * FROM musers WHERE id=?', (uid,)).fetchone()
        if not u or not u['totp'] or not (totp_ok(u['totp'], inp.get('code')) or use_recovery(m, u, inp.get('code'))): raise Err('Código incorrecto.', 401)
        session.clear(); session['m'] = u['id']; session['ma'] = time.time(); session.permanent = True
        resp = jsonify({'ok': 1})
        return add_trust(m, u, resp) if inp.get('remember') else resp
    if a == 'logout': session.clear(); return jsonify({'ok': 1})
    u = m_user()
    if a == 'me': return jsonify({'user': {'username': u['username'], 'totp': bool(u['totp']), 'trusted': len([x for x in json.loads(u['trusted'] or '[]') if x['exp'] > time.time()]) if u else 0} if u else None})
    if a == 'totpSetup' and u:
        sec = new_totp_secret(); session['tp'] = sec
        uri = f"otpauth://totp/CitaPro%20Master:{u['username']}?secret={sec}&issuer=CitaPro"
        return jsonify({'secret': sec, 'svg': qrlib.svg(uri)})
    if a == 'totpEnable' and u:
        sec = session.get('tp')
        if not sec or not totp_ok(sec, inp.get('code')): raise Err('Código incorrecto.', 400)
        codes = [secrets.token_hex(4) + '-' + secrets.token_hex(4) for _ in range(10)]   # se muestran UNA sola vez
        m.execute('UPDATE musers SET totp=?, recovery=? WHERE id=?', (sec, json.dumps([hashlib.sha256(x.encode()).hexdigest() for x in codes]), u['id']))
        session.pop('tp', None); return jsonify({'ok': 1, 'recovery': codes})
    if a == 'totpDisable' and u:
        if not check_password_hash(u['pass'], s(inp.get('password'))) or not totp_ok(u['totp'] or '', inp.get('code')):
            raise Err('Contraseña o código incorrecto.', 403)
        m.execute('UPDATE musers SET totp=NULL, recovery=NULL, trusted=NULL WHERE id=?', (u['id'],)); return jsonify({'ok': 1})
    if a == 'revokeTrusted' and u:
        m.execute('UPDATE musers SET trusted=NULL WHERE id=?', (u['id'],)); return jsonify({'ok': 1})
    if not u: raise Err('Sin sesión', 401)

    if a == 'list':
        rows = []
        for t in tenants(): rows.append({**t, 'path': f"/{t['slug']}/", **stats(t['slug']), 'billing': billing_status(get_billing(t['slug']))})
        per = inp.get('period') if inp.get('period') in ('mes', 'todo') else 'mes'
        month = None if per == 'todo' else datetime.now(TZ).strftime('%Y-%m')
        rep_ = []
        for t in tenants():
            r = report(t['slug'], month)
            rep_.append({'label': t['name'] + ('' if t['active'] else ' (suspendido)'), **r})
        ing = sorted([{'label': x['label'], 'value': round(x['ingresos'], 2)} for x in rep_], key=lambda x: -x['value'])
        cli = sorted([{'label': x['label'], 'value': x['clientes']} for x in rep_], key=lambda x: -x['value'])
        return jsonify({'tenants': rows, 'types': {k: v['n'] for k, v in TYPES.items()},
                        'charts': {'period': per, 'ingresos': ing, 'clientes': cli}, 'bcv': get_bcv(), 'wa': {'configured': wa_configured(), 'on': wa_enabled()}})
    if a == 'income':
        return jsonify(income_report())
    if a == 'rates':
        return jsonify(update_rates(force=True))
    if a == 'waToggle':
        wa_set_enabled(bool(inp.get('on')))
        return jsonify({'ok': 1, 'wa_on': wa_enabled()})
    if a == 'pay':
        slug = s(inp.get('slug'))
        if not m.execute('SELECT 1 FROM tenants WHERE slug=?', (slug,)).fetchone(): raise Err('Negocio no encontrado.', 404)
        try: amount = float(inp.get('amount'))
        except (TypeError, ValueError): raise Err('Indica el monto pagado.')
        ref = s(inp.get('ref')).strip()
        if amount <= 0 or not ref: raise Err('Monto y referencia son obligatorios.')
        b = register_payment(slug, amount, ref)
        return jsonify({'ok': 1, 'billing': billing_status(b)})

    if a == 'create':
        name, typ = s(inp.get('name')).strip(), inp.get('type')
        au = norm(inp.get('adminUser')) or 'admin'
        slug = slugify(inp.get('slug') or name)
        tmp = s(inp.get('adminPass')).strip() or secrets.token_hex(4)
        if not 3 <= len(name) <= 60: raise Err('El nombre del negocio debe tener entre 3 y 60 caracteres.')
        if typ not in TYPES: raise Err('Elige un rubro válido.')
        if au == 'master' or len(tmp) < 6: raise Err('Usuario reservado o contraseña temporal muy corta (mín. 6).')
        if not SLUG_RE.match(slug) or slug in RESERVED: raise Err('Ese nombre no sirve para el enlace; prueba con otro.')
        if m.execute('SELECT 1 FROM tenants WHERE slug=? OR name=?', (slug, name)).fetchone() or os.path.exists(tenant_path(slug)):
            raise Err('Ya existe un negocio con ese nombre.', 409)
        m.execute('INSERT INTO tenants(slug,name,type,admin_user,active,created) VALUES(?,?,?,?,1,?)', (slug, name, typ, au, int(time.time())))
        try:
            use(slug); c = db()                      # crea la base de datos independiente del negocio
            cur = c.execute('INSERT INTO users(username,pass,role,name,must_change,created) VALUES(?,?,?,?,1,?)',
                            (au, generate_password_hash(tmp), 'admin', 'Administrador', int(time.time())))
            put_doc('profiles', cur.lastrowid, {'name': 'Administrador', 'phone': '', 'ci': '', 'username': au})
            put_doc('cfg', 'main', {'biz': name, 'type': typ, 'from': 9, 'to': 19, 'payInfo': '', 'open': True, 'rateType': 'usd',
                'theme': valid_theme(inp.get('theme')), 'rates': {k: get_bcv().get(k, 0) for k in ('usd', 'eur', 'upd')} if get_bcv().get('ok') else {'usd': 0, 'eur': 0, 'upd': ''}, 'closedMsg': 'Estamos de vacaciones. Pronto abriremos de nuevo.',
                'svc': [{'id': f's{i}', 'name': n, 'price': p, 'img': ''} for i, (n, p) in enumerate(TYPES[typ]['svc'])]})
        except BaseException:
            m.execute('DELETE FROM tenants WHERE slug=?', (slug,))
            for ext in ('', '-wal', '-shm'):
                try: os.remove(tenant_path(slug) + ext)
                except OSError: pass
            close_tenant(slug); raise
        finally: use(None)
        return jsonify({'slug': slug, 'path': f'/{slug}/', 'adminUser': au, 'tempPass': tmp})

    slug = s(inp.get('slug'))
    t = m.execute('SELECT * FROM tenants WHERE slug=?', (slug,)).fetchone() if a in ('toggle', 'resetAdmin', 'rename', 'delete') else None
    if a in ('toggle', 'resetAdmin', 'rename', 'delete') and not t: raise Err('Negocio no encontrado.', 404)
    if a == 'toggle':
        m.execute('UPDATE tenants SET active=? WHERE slug=?', (1 if inp.get('active') else 0, slug)); return jsonify({'ok': 1})
    if a == 'resetAdmin':
        tmp = secrets.token_hex(4)
        try:
            use(slug)
            db().execute("UPDATE users SET pass=?, must_change=1 WHERE role='admin'", (generate_password_hash(tmp),))
        finally: use(None)
        return jsonify({'tempPass': tmp, 'adminUser': t['admin_user']})
    if a == 'rename':    # cambia el nombre visible; el enlace (y los QR ya impresos) NO cambian
        name = s(inp.get('name')).strip()
        if not 3 <= len(name) <= 60: raise Err('El nombre del negocio debe tener entre 3 y 60 caracteres.')
        if m.execute('SELECT 1 FROM tenants WHERE name=? AND slug!=?', (name, slug)).fetchone(): raise Err('Ya existe un negocio con ese nombre.', 409)
        m.execute('UPDATE tenants SET name=? WHERE slug=?', (name, slug))
        try:
            use(slug); cfg = get_doc('cfg', 'main')
            if cfg is not None: cfg['biz'] = name; put_doc('cfg', 'main', cfg)
        finally: use(None)
        return jsonify({'ok': 1, 'name': name})
    if a == 'delete':    # no borra para siempre: mueve la base y las fotos a data/deleted/
        if s(inp.get('confirm')).strip().lower() != t['name'].lower(): raise Err('Escribe el nombre exacto del negocio para confirmar.')
        import shutil
        dest = os.path.join(DATA, 'deleted', f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"); os.makedirs(dest, exist_ok=True)
        close_tenant(slug)
        try:
            for ext in ('', '-wal', '-shm'):
                if os.path.exists(tenant_path(slug) + ext): shutil.move(tenant_path(slug) + ext, os.path.join(dest, slug + '.db' + ext))
            up = os.path.join(UPLOADS, slug)
            if os.path.isdir(up): shutil.move(up, os.path.join(dest, 'uploads'))
        except OSError as e:
            raise Err('No se pudo eliminar ahora (archivos en uso). Suspende el negocio y reintenta.', 500)
        m.execute('DELETE FROM tenants WHERE slug=?', (slug,))
        return jsonify({'ok': 1, 'kept': os.path.relpath(dest, BASE)})
    if a == 'changePass':
        np_ = inp.get('new')
        if not check_password_hash(u['pass'], s(inp.get('old'))): raise Err('La contraseña actual no es correcta.', 403)
        if not isinstance(np_, str) or len(np_) < 8: raise Err('La nueva contraseña debe tener al menos 8 caracteres.')
        m.execute('UPDATE musers SET pass=? WHERE id=?', (generate_password_hash(np_), u['id'])); return jsonify({'ok': 1})
    raise Err('Acción desconocida', 404)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8001)
