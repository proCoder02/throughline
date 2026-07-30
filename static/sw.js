// Minimal service worker: exists mainly to satisfy PWA installability
// criteria. This app is almost entirely dynamic (live websocket, session
// auth, LLM calls) so it deliberately does NOT cache API responses --
// only a network-first shell cache for the root page, so a reload while
// briefly offline doesn't show the browser's default error page.
const SHELL_CACHE = 'throughline-shell-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.add('/'))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate') return;
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put('/', copy));
        return res;
      })
      .catch(() => caches.match('/'))
  );
});
