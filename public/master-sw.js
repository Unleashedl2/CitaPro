const C = 'citapro-master-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => e.waitUntil(clients.claim()));

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // Ignorar métodos que no sean GET, peticiones externas o llamadas a la API
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.includes('/api')) return;

  e.respondWith(
    fetch(e.request).catch(async () => {
      const cached = await caches.match(e.request);
      // Si está en caché lo devuelve, de lo contrario devuelve una respuesta de respaldo para que la app no colapse
      return cached || new Response('Sin conexión a internet', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' }
      });
    })
  );
});
