const C = 'citapro-v3';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(C).then(c => c.addAll(['./', 'index.html', 'manifest.webmanifest', 'icon-192.png'])));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(k => Promise.all(k.map(x => caches.delete(x)))).then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.endsWith('api.php')) return;
  e.respondWith(
    fetch(e.request).then(r => {
      const c = r.clone();
      caches.open(C).then(x => x.put(e.request, c));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
