const C='citapro-master-v1';
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);
 if(e.request.method!=='GET'||u.origin!==location.origin||u.pathname.includes('/api'))return;
 e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
