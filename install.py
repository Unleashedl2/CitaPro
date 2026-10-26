import time
from werkzeug.security import generate_password_hash
from core import mdb

def install(master_pw):
    """Crea la base maestra y la cuenta Master (los negocios se crean desde la sala de control)."""
    m = mdb()
    if m.execute("SELECT 1 FROM musers WHERE username='master'").fetchone():
        print('master ya existe, sin cambios'); return
    m.execute('INSERT INTO musers(username,pass,name) VALUES(?,?,?)', ('master', generate_password_hash(master_pw), 'Master'))
    print('master creado. Entra en /master/ para crear negocios.')
