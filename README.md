# CitaPro multi-negocio (Flask + SQLite)

Una sola instalación sirve a todos tus clientes (barberías, peluquerías, manicure…).
**Cada negocio tiene su propia base de datos independiente** (`data/tenants/<negocio>.db`), su administrador,
sus clientes, sus servicios, sus fotos y su propio enlace con código QR.

```
https://beta1.fjmsystem.com/master/                 ← tu sala de control (solo tú, cuenta Master)
https://beta1.fjmsystem.com/barberia-don-pepe/      ← app de ese negocio (enlace + QR para sus clientes)
https://beta1.fjmsystem.com/unas-bella/             ← app de otro negocio
```

## Comandos (`main.py`)
```
python main.py install 'CLAVE_MASTER'   crea la base maestra y tu cuenta Master (una sola vez)
python main.py run [--port 8001]        inicia el servidor
python main.py reminders                recordatorios de 1 hora de todos los negocios (cron cada 5 min)
python main.py backup                   copia de seguridad de la base maestra y de cada negocio (cron diario)
python main.py info                     lista los negocios con sus cifras
python main.py adopt SLUG               convierte una instalación antigua (data/database.db) en un negocio
```

## Cómo vender y dar de alta un negocio
1. Entra a `/master/` con el usuario **master** y tu clave.
2. En **Crear negocio**: nombre (debe ser único), rubro, usuario del administrador. Pulsa crear.
3. El sistema crea **automáticamente** la base de datos del negocio, con los servicios del rubro, y te muestra:
   el **enlace**, el **QR** (descargable) y el usuario con **contraseña temporal** del administrador.
   Usa "Copiar mensaje para el cliente" y envíaselo.
4. El administrador entra a su enlace, crea su contraseña privada y su teléfono, ajusta servicios, precios, fotos
   y datos de pago. Desde su panel (Panel → "Enlace y QR de tu negocio") puede descargar su QR para imprimirlo.
5. Sus clientes escanean el QR (o abren el enlace), crean su cuenta y reservan. La app se instala desde ese mismo
   enlace ("Añadir a pantalla de inicio" o botón **Instalar app**).

**¿Cómo elige un cliente el negocio?** No elige: cada negocio tiene su propio enlace/QR y la persona solo ve ese negocio.
Las cuentas de clientes son independientes por negocio (el mismo usuario o teléfono puede existir en dos negocios).

## Monitoreo (sala de control)
Resumen de negocios totales/activos/suspendidos y por rubro, y por cada negocio: clientes, citas, citas de hoy,
ingresos del mes, última actividad. Puedes **suspender/reactivar** un negocio (p. ej. por falta de pago: sus clientes
verán "Servicio suspendido"), **restablecer la clave del administrador** y ver/descargar su QR.
Los datos privados de los clientes (pagos, teléfonos) NO se muestran en la sala de control.

**Editar nombre y eliminar un negocio**
- **Editar nombre:** cambia el nombre visible (en la app, el título y el nombre de la app instalada). El **enlace y el QR no cambian**,
  así los QR ya impresos y las apps ya instaladas siguen funcionando.
- **Eliminar:** pide escribir el nombre exacto del negocio. El negocio deja de funcionar al instante, pero sus datos NO se borran para
  siempre: la base de datos y las fotos se mueven a `data/deleted/<negocio>-<fecha>/`. Cuando estés seguro, borra esa carpeta a mano.
  Después puedes volver a crear un negocio con el mismo nombre (empieza con una base nueva y vacía).

## Instalación en tu servidor (VPS Linux)
```
sudo apt install python3-venv nginx certbot python3-certbot-nginx
sudo mkdir -p /opt/citapro && sudo chown $USER /opt/citapro          # sube aquí esta carpeta
cd /opt/citapro && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py install 'TU_CLAVE_MASTER'
sudo chown -R www-data data
```
Servicio `/etc/systemd/system/citapro.service`:
```
[Unit]
Description=CitaPro
After=network.target
[Service]
WorkingDirectory=/opt/citapro
ExecStart=/opt/citapro/venv/bin/python main.py run --port 8001
User=www-data
Restart=always
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now citapro`

Nginx (junto a tu ERP en el 8000, sin tocarlo) `/etc/nginx/sites-available/citapro`:
```
server {
  server_name beta1.fjmsystem.com;
  client_max_body_size 4m;
  location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```
`sudo certbot --nginx -d beta1.fjmsystem.com` (HTTPS). Con Caddy: `beta1.fjmsystem.com { reverse_proxy 127.0.0.1:8001 }`.
Los códigos QR usan la dirección real con la que se abre la app (https://beta1.fjmsystem.com/…).
Si prefieres fijarla, define `PUBLIC_URL` en `config.py`.

Tareas programadas (`sudo crontab -u www-data -e`):
```
*/5 * * * *  cd /opt/citapro && ./venv/bin/python main.py reminders   # avisos + tasa BCV (1 vez por hora)
0 3 * * *    cd /opt/citapro && ./venv/bin/python main.py backup
```

## Windows / pruebas locales
```
python -m venv venv && venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py install "TU_CLAVE_MASTER"
venv\Scripts\python main.py run --port 8001
```
Abre http://127.0.0.1:8001/master/

## Si ya tenías la versión de un solo negocio
Con el servidor detenido: `python main.py install 'CLAVE_MASTER'` y `python main.py adopt mi-negocio`.
Tu base actual pasa a ser el negocio `/mi-negocio/` (sus clientes, citas y fotos se conservan).

## Tasa BCV automática
El sistema consulta https://www.bcv.org.ve/ cada hora (desde el mismo comando `reminders` del cron) y copia el
dólar y el euro oficiales a todos los negocios activos. El administrador ya no escribe la tasa: solo elige si
cobra en **dólar BCV** o **euro BCV**. Si el BCV no responde, se conserva la última tasa válida y la sala de
control muestra el error. Para probar la lectura en tu servidor:
```
python main.py rates
```
Lee el dólar y el euro del bloque «Tipo de Cambio de Referencia» de la página del BCV (ids `dolar` y `euro`).
Si el BCV cambia la estructura de su página, la lectura fallará con un mensaje claro en lugar de cobrar mal.
En ese caso avísame con el resultado de ese comando y ajusto el lector.
Nota: el sitio del BCV a veces presenta un certificado que no se valida; en ese caso la lectura se hace
sin verificar el certificado, solo para esta consulta pública de tasas.

## Colores por negocio
Al crear un negocio, la sala de control permite elegir un color principal (10 preajustes o uno personalizado).
El administrador puede cambiarlo cuando quiera en su **Panel → Apariencia de tu app**, junto con el modo claro, oscuro o automático.
El color se aplica a la app, a la pantalla de entrada y al color de la app instalada.

## Estadísticas en la sala de control
Dos gráficas de torta con porcentajes: **negocios que más ingresos generan** y **negocios con más clientes
confirmados**. Puedes verlas por mes en curso o histórico. Debajo de cada una aparece el líder: su monto en
dólares en la de ingresos, y su número de personas en la de clientes.

## Ver los datos con DB Browser for SQLite
`python main.py backup`, descarga una copia de `data/backups/` y ábrela en DB Browser (pestaña Examinar datos):
cada negocio trae las vistas `v_citas`, `v_pagos`, `v_clientes`, `v_servicios` y `v_ingresos`.
No edites una base mientras el servidor la usa.

## SMS de recuperación (opcional, Twilio)
Variables `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM` o `config.py`. Sin SMS, el administrador del negocio
restablece la clave de un cliente (Clientes → Restablecer contraseña) y tú restableces la del administrador.

## Respaldos y seguridad
Copia `data/` completo (bases, fotos y `secret.key`). Cada sesión solo vale dentro de su negocio.

## Seguridad y despliegue en producción
Archivos listos en la carpeta `deploy/`:
- `nginx-citapro.conf`: HTTPS, compresión, caché de imágenes, límite de peticiones por IP y sala de control solo para tu IP.
- `citapro-proxy.inc`: cabeceras que nginx envía a la app.
- `citapro.service`: servicio de systemd endurecido (el proceso solo escribe en `data/` y `public/uploads/`).
- `firewall.sh`: deja abiertos solo SSH, 80 y 443, e instala fail2ban y actualizaciones automáticas.

Pasos:
1. Copia `deploy/nginx-citapro.conf` a `/etc/nginx/sites-available/citapro`, y `citapro-proxy.inc` a `/etc/nginx/`. Reemplaza `SU_IP` por tu IP fija (en los dos lugares).
2. `sudo ln -s /etc/nginx/sites-available/citapro /etc/nginx/sites-enabled/` y después `sudo nginx -t && sudo systemctl reload nginx`.
3. HTTPS: `sudo certbot --nginx -d citapro.fjmsystem.com`.
4. Copia `citapro.service` a `/etc/systemd/system/`, reemplaza `SU_IP` por tu IP y ejecuta `sudo systemctl daemon-reload && sudo systemctl enable --now citapro`.
5. Ejecuta `deploy/firewall.sh` una sola vez.
6. Entra a la sala de control y activa la **doble verificación** (Mi cuenta → Doble verificación). Al activarla aparecen **10 códigos de recuperación de un solo uso**. Guárdalos (papel o gestor de contraseñas): no se vuelven a mostrar.

**Equipos de confianza:** al entrar con el código, marca "Recordar este equipo 30 días" (activado por defecto). Ese navegador entra con usuario y contraseña durante 30 días. Un equipo nuevo siempre pide el código. Para cerrar la sesión en todos los equipos de confianza, usa "Cerrar sesión en todos los equipos de confianza" en Mi cuenta.

**Si pierdes el celular:** en la pantalla de verificación escribe uno de los códigos de recuperación.
**Si además cambió tu IP y no puedes entrar:** conéctate por SSH al servidor y ejecuta `python main.py master-2fa-off`. Luego actualiza `CITAPRO_MASTER_IPS` (servicio systemd) y `allow` (nginx) con tu IP nueva, y reinicia ambos.

Variables de entorno útiles:
- `CITAPRO_THREADS` (por defecto 16): hilos de atención simultánea.
- `CITAPRO_MASTER_IPS`: lista de IPs que pueden abrir la sala de control (separadas por coma).

## Rendimiento
- Cada cliente consulta cada 15 segundos (5 segundos el administrador), y solo cuando la pestaña está visible. Si no hubo cambios, la respuesta es mínima.
- Las respuestas se guardan en caché por versión de datos: miles de personas reciben lo mismo sin recalcularlo.
- Se envían solo las citas de 60 días atrás a 120 días adelante.
- Las estadísticas de la sala de control se guardan 60 segundos.

## Límites actuales
SQLite en un solo servidor soporta bien un salón o una cadena de negocios pequeña-mediana. Para decenas de miles de usuarios activos a la vez,
la siguiente etapa es PostgreSQL, varios servidores de la app detrás de un balanceador, y almacenamiento de fotos en un servicio externo.

## Suscripción mensual
- Cada negocio tiene un periodo de **30 días completos**, contando el día de inicio. Ejemplo: compra el 25/10/2026 → vence el 23/11/2026.
- **Vencimiento:** la app funciona hasta las 23:59 del último día. A partir de las 00:00 del día siguiente se suspende sola (no requiere tareas programadas).
- **Renovación:** el pago registrado desde la sala de control (botón "Registrar pago") extiende el servicio **desde el día siguiente al vencimiento**. Si pagas el 20/11, el nuevo periodo empieza el 24/11, sin regalar ni perder días.
- **Pago tardío:** si el negocio ya estaba suspendido, el nuevo periodo empieza el día que se registra el pago.
- **Avisos:** los últimos 3 días (incluido el de vencimiento) el vencimiento se muestra en rojo en la sala de control y en el panel del administrador, y el administrador recibe un aviso dentro de la app una vez al día. El aviso se envía con `python main.py reminders` (cron cada 5 minutos).

### Aviso por WhatsApp (Meta WhatsApp Cloud API)
Los últimos 3 días, el aviso de vencimiento llega por WhatsApp al administrador del negocio (y a tu número, si lo configuras).
Requisitos, una sola vez:
1. Cuenta de Meta Business y un número de WhatsApp Business.
2. En Meta, crea la plantilla **`vencimiento_suscripcion`** (categoría *Utilidad*, idioma *Español*) con este texto:
   `Hola {{1}}, tu suscripción de CitaPro vence el {{2}} (faltan {{3}} día(s)). Si no se renueva, la app se suspende a las 00:00 del día siguiente. Gracias.`
   Meta debe aprobarla antes de usarla (suele tardar minutos u horas).
3. Variables de entorno en el servicio systemd: `WA_TOKEN`, `WA_PHONE_ID`, y opcionalmente `CITAPRO_MASTER_PHONE` (tu número con código de país).
4. Prueba: `python main.py whatsapp-test 584121112233`.
Sin estas variables, el aviso solo aparece dentro de la app.

### Notificaciones push
- Requiere HTTPS (ya lo tienes) y la app instalada en la pantalla de inicio. En iPhone, las notificaciones solo llegan si la app está añadida a la pantalla de inicio.
- El administrador y los clientes activan los avisos con el botón **Activar avisos** dentro de la app.
- Se envían: recordatorio de cita 1 hora antes (cliente y administrador) y aviso de vencimiento (administrador).
- Instalación: `pip install -r requirements.txt` (incluye `pywebpush` y `cryptography`). La clave VAPID se crea sola la primera vez y se guarda en `data/vapid_private.pem`. **Haz respaldo de ese archivo**: si lo pierdes, los dispositivos suscritos dejan de recibir avisos y tienen que volver a activarlos.
- Opcional: `CITAPRO_CONTACT` con un correo de contacto (se usa en las cabeceras de Web Push).

### Interruptor de WhatsApp
En la sala de control, la tarjeta **Recordatorios de vencimiento por WhatsApp** permite activar o desactivar el envío. Las notificaciones push no se apagan desde ahí.

### Ingresos por suscripciones (sala de control)
- **Total histórico**, **mes en curso** y **mes anterior**, calculados con los pagos que registras con el botón "Registrar pago".
- **Negocios que han pagado:** cuánto ha pagado cada uno, cuántos pagos hizo y cuándo fue el último.
- **Detalle de pagos:** fecha, negocio, monto, referencia y el periodo que cubrió cada pago.
- Los pagos anteriores a esta versión se incorporan automáticamente como el último pago registrado de cada negocio.
