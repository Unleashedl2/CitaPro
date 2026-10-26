# /etc/nginx/citapro-proxy.inc — cabeceras para el servidor de la app
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 30s;
proxy_buffering on;
