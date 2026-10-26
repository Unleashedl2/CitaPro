"""Configuración de CitaPro. Puedes cambiar valores aquí o con variables de entorno."""
import os
TZ = os.environ.get('CITAPRO_TZ', 'America/Caracas')
# SMS para recuperar contraseña (Twilio). Vacío = desactivado (Master restablece claves).
SMS = {'sid': os.environ.get('TWILIO_SID', ''), 'token': os.environ.get('TWILIO_TOKEN', ''), 'from': os.environ.get('TWILIO_FROM', '')}

# WhatsApp (Meta WhatsApp Cloud API) para avisos de vencimiento.
# Deja vacío para desactivarlo: el aviso sigue apareciendo dentro de la app.
WHATSAPP = {
    'token': os.environ.get('WA_TOKEN', ''),          # token de acceso permanente
    'phone_id': os.environ.get('WA_PHONE_ID', ''),    # ID del número de WhatsApp Business
    'template': os.environ.get('WA_TEMPLATE', 'vencimiento_suscripcion'),  # plantilla aprobada por Meta
    'lang': os.environ.get('WA_LANG', 'es'),
}
MASTER_PHONE = os.environ.get('CITAPRO_MASTER_PHONE', '')  # tu número para recibir copia del aviso
