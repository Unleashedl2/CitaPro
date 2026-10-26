"""CitaPro – punto de entrada único.

  python main.py install CLAVE     crea la base maestra y la cuenta Master (una sola vez)
  python main.py run [--port N]    inicia el servidor (por defecto 127.0.0.1:8001)
  python main.py reminders         recordatorios de 1 hora de TODOS los negocios y tasa BCV (cron cada 5 min)
  python main.py rates             consulta la tasa BCV ahora mismo y la muestra
  python main.py whatsapp-test N   envía un WhatsApp de prueba al número N (con código de país)
  python main.py backup            copia de seguridad de la base maestra y de cada negocio (cron diario)
  python main.py info              lista los negocios y sus cifras
  python main.py adopt SLUG        convierte una instalación antigua de un solo negocio (data/database.db) en un negocio
"""
import argparse, os, sys, shutil, time

def main():
    ap = argparse.ArgumentParser(description='CitaPro')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run'); r.add_argument('--host', default='127.0.0.1'); r.add_argument('--port', type=int, default=8001)
    i = sub.add_parser('install'); i.add_argument('master_password')
    ad = sub.add_parser('adopt'); ad.add_argument('slug')
    sub.add_parser('reminders'); sub.add_parser('backup'); sub.add_parser('info'); sub.add_parser('rates')
    wt = sub.add_parser('whatsapp-test'); wt.add_argument('numero')
    sub.add_parser('master-2fa-off')   # emergencia: desactiva la doble verificación de Master (requiere acceso al servidor)
    a = ap.parse_args()

    if a.cmd == 'run':
        from app import app
        try:
            from waitress import serve
            print(f'CitaPro en http://{a.host}:{a.port}   (sala de control: /master/)'); serve(app, host=a.host, port=a.port, threads=int(os.environ.get('CITAPRO_THREADS', '16')), channel_timeout=30, max_request_body_size=5*1024*1024)
        except ImportError:
            print('Aviso: instala waitress (pip install -r requirements.txt) para producción.')
            app.run(host=a.host, port=a.port)
    elif a.cmd == 'install':
        if len(a.master_password) < 8: sys.exit('La clave de Master debe tener al menos 8 caracteres.')
        from install import install; install(a.master_password)
    elif a.cmd == 'reminders':
        from core import tenants, use, update_rates
        from cron import reminders
        update_rates()            # tasa BCV (solo consulta si pasó 1 hora)
        n = 0
        for t in tenants(only_active=True):
            use(t['slug']); n += reminders()
        from core import notify_billing
        print('Avisos enviados:', n, '| avisos de vencimiento:', notify_billing())
    elif a.cmd == 'whatsapp-test':
        from core import wa_on, wa_send_template
        if not wa_on(): sys.exit('Configura WA_TOKEN y WA_PHONE_ID primero.')
        ok = wa_send_template(a.numero, ['Negocio de prueba', '23/11/2026', '2'])
        print('Enviado' if ok else 'No enviado: revisa el número, la plantilla y el token.')
    elif a.cmd == 'master-2fa-off':
        from core import mdb
        mdb().execute("UPDATE musers SET totp=NULL, recovery=NULL WHERE username='master'")
        print('Doble verificación de Master desactivada. Entra a la sala de control y actívala de nuevo.')
    elif a.cmd == 'rates':
        from core import update_rates
        r = update_rates(force=True)
        print('BCV OK' if r['ok'] else 'BCV no disponible: ' + r['err'], '| dólar', r['usd'], '| euro', r['eur'], '| ' + r['upd'])
    elif a.cmd == 'backup':
        from core import backup_all
        for f in backup_all(): print('Copia creada:', f)
    elif a.cmd == 'info':
        from core import tenants, stats, TENANTS
        ts = tenants(); print(f'Negocios: {len(ts)}   (bases de datos en {TENANTS})')
        for t in ts:
            st = stats(t['slug'])
            print(f"- /{t['slug']}/  {t['name']} [{t['type']}] {'activo' if t['active'] else 'SUSPENDIDO'}  clientes={st['clients']} citas={st['citas']} ingresos_mes=${st['ingresos_mes']}")
    elif a.cmd == 'adopt':
        import sqlite3
        from core import DATA, BASE, TENANTS, UPLOADS, SLUG_RE, RESERVED, tenant_path, mdb, use, get_doc, db
        legacy = os.path.join(DATA, 'database.db')
        if not os.path.exists(legacy): sys.exit('No hay data/database.db que adoptar.')
        if not SLUG_RE.match(a.slug) or a.slug in RESERVED or os.path.exists(tenant_path(a.slug)): sys.exit('Nombre de negocio no válido o ya existe.')
        for ext in ('', '-wal', '-shm'):
            if os.path.exists(legacy + ext): shutil.move(legacy + ext, tenant_path(a.slug) + ext)
        old_up = os.path.join(BASE, 'public', 'uploads')
        if os.path.isdir(old_up): shutil.move(old_up, os.path.join(UPLOADS, a.slug))
        use(a.slug); db().execute("DELETE FROM users WHERE role='master'"); cfg = get_doc('cfg', 'main') or {}
        adm = db().execute("SELECT username FROM users WHERE role='admin' LIMIT 1").fetchone()
        mdb().execute('INSERT INTO tenants(slug,name,type,admin_user,active,created) VALUES(?,?,?,?,1,?)',
                      (a.slug, cfg.get('biz') or a.slug, cfg.get('type') or 'barberia', adm[0] if adm else 'admin', int(time.time())))
        print(f'Negocio adoptado en /{a.slug}/')

if __name__ == '__main__':
    main()
