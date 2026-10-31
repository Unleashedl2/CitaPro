const C = 'citapro-v3';
const ASSETS = ['manifest.webmanifest', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(C).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== C).map(key => caches.delete(key))
    )).then(() => clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  
  // Ignorar peticiones que no sean GET o que vayan a la API
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.endsWith('api.php')) {
    return;
  }

  // Para la página principal / index, usar Network-First (Red primero, luego caché)
  if (url.pathname.endsWith('/') || url.pathname.endsWith('index.html')) {
    e.respondWith(
      fetch(e.request)
        .then(response => {
          const clone = response.clone();
          caches.open(C).then(cache => cache.put(e.request, clone));
          return response;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Resto de recursos estáticos
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
