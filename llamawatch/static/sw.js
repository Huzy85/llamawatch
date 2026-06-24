const CACHE_NAME = "lw-studio-v30";

const SHELL = [
  "/studio",
  "/static/studio.css?v=131",
  "/static/studio.js?v=146",
  "/static/topojson-client.min.js",
  "/static/gauge.js?v=33",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/vendor/xterm.min.js",
  "/static/vendor/xterm.min.css",
  "/static/vendor/addon-fit.min.js",
  "/static/vendor/addon-web-links.min.js",
];

// Install: pre-cache shell, activate immediately
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

// Activate: delete old caches, claim clients
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => {
      self.clients.matchAll({ includeUncontrolled: true }).then(clients =>
        clients.forEach(c => c.postMessage({ type: "SW_UPDATED" }))
      );
    })
  );
  self.clients.claim();
});

// Fetch: network-first for everything; skip SSE, WebSocket, API calls
self.addEventListener("fetch", (e) => {
  const url = e.request.url;
  if (e.request.method !== "GET") return;
  if (url.includes("/sse") || url.includes("/ws") || url.includes("/api/")) return;

  e.respondWith(
    fetch(e.request)
      .then(resp => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
