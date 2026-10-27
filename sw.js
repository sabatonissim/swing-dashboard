// Swing Desk service worker (v19) — makes the site installable as an app, opens fast on a weak connection, and shows
// push notifications (alerts).
// Rules: only this site's own files are cached. The page itself is "network first" — online you always get the newest
// version (the owner uploads index.html often); the cached copy is used only when the network fails. Data (the Render
// API), TradingView and Google are never touched here: they go straight to the network as before.
const CACHE = 'swingdesk-v19';
const SHELL = ['/', '/manifest.json', '/favicon.svg', '/icon-192.png', '/icon-512.png', '/logo.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;            // API, TradingView, fonts, Google: not ours to cache

  if (req.mode === 'navigate') {                              // the page: network first, cached copy when offline
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put('/', copy)); }
          return res;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }
  // icons / logo / manifest: from the cache, refreshed in the background
  e.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

// ---- push notifications (alerts) ----
// The server sends JSON: {title, body, url, tag}. Text only — shown by the operating system, never as HTML.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  const title = String(d.title || 'Swing Desk').slice(0, 120);
  e.waitUntil(self.registration.showNotification(title, {
    body: String(d.body || '').slice(0, 300),
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: d.tag ? String(d.tag).slice(0, 64) : undefined,
    data: { url: typeof d.url === 'string' && d.url.startsWith('/') ? d.url : '/' },   // only links inside the site
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ('navigate' in w) { return w.navigate(target).then((c) => (c || w).focus()); }
      }
      return self.clients.openWindow(target);
    })
  );
});
