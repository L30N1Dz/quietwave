/* QUIETWAVE service worker.

   Scope is deliberately narrow: cache the application shell so the station
   opens instantly and survives a dropped connection. Message traffic is
   never cached -- stale messages would be worse than none, and attachments
   are private.
*/

const CACHE = 'quietwave-shell-v1';

const SHELL = [
  '/',
  '/static/css/base.css',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/theme.js',
  '/static/themes/themes.json',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// Themes are part of the shell: without them the app paints unstyled.
const THEMES = ['quietwave', 'relay-nine', 'emberlink', 'neonwire'];
for (const id of THEMES) {
  SHELL.push(`/static/themes/${id}/theme.css`);
  SHELL.push(`/static/themes/${id}/lexicon.json`);
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // addAll fails the whole install if any single file 404s; individual
      // puts keep a partial shell working.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Anything live or private goes straight to the network, never the cache.
  if (
    url.pathname.startsWith('/api/')
    || url.pathname.startsWith('/auth/')
    || url.pathname.startsWith('/webhooks/')
    || url.pathname.startsWith('/m/')
    || url.pathname === '/healthz'
  ) {
    return;
  }

  // Shell assets: cache first, refresh in the background.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});
