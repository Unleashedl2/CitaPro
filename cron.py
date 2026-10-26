import time
from core import all_docs, put_doc, get_doc, local_ts, push_to_uid, admin_uid

def reminders():
    """Avisa 1 hora antes de cada cita al cliente y al administrador."""
    now = time.time(); n = 0
    for a in all_docs('appts'):
        if a.get('status') not in ('confirmada', 'reservada'): continue
        try: df = local_ts(a['date'], a['time']) - now
        except (KeyError, ValueError): continue
        if df <= 0 or df > 3600: continue
        aid = a['id']; upd = dict(a)
        if not a.get('remC'):
            put_doc('notifs', f'r{aid}c', {'to': str(a['client']), 'ts': int(now * 1000), 'read': False,
                    'text': f"⏰ Recordatorio: tu cita de {a['svc']} es a las {a['time']} (en menos de 1 hora)."})
            push_to_uid(a['client'], 'Recordatorio de cita', f"Tu cita de {a['svc']} es a las {a['time']} (en menos de 1 hora).", './')
            upd['remC'] = True; n += 1
        if not a.get('remA'):
            p = get_doc('profiles', a['client']) or {}
            put_doc('anotifs', f'r{aid}a', {'apptId': aid, 'ts': int(now * 1000), 'read': False,
                    'text': f"⏰ Próxima cita a las {a['time']}: {p.get('name', 'Cliente')} – {a['svc']}."})
            au = admin_uid()
            if au: push_to_uid(au, 'Próxima cita', f"{a['time']}: {p.get('name', 'Cliente')} – {a['svc']}.", './')
            upd['remA'] = True; n += 1
        put_doc('appts', aid, upd)
    return n
