// ImpactText service worker: lets the installed app open with no signal.
// Pages and styles: network first, cached copy when offline. API calls are never cached
// (notes typed offline are queued by the log page itself and sent when the phone is back online).
const CACHE = 'impacttext-v3';
// only pages everyone can open: admin pages redirect to sign-in, and a cached redirect breaks offline navigation
const SHELL = ['/', '/phone', '/login', '/me', '/security', '/static/style.css', '/static/nav.js', '/static/pwa.js',
               '/static/icons/icon-192.png', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/calendar/') || url.pathname.startsWith('/feeds/') || url.pathname === '/sms') return;
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok && !r.redirected && r.type === 'basic') { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return r;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || caches.match('/')))
  );
});
