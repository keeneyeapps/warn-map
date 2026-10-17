importScripts("./js/precache-list.js");

const CACHE_NAME = "warn-map-shell-v13";
const SCOPE_URL = new URL(self.registration.scope);
const ASSET_URLS = self.WARN_PRECACHE_URLS.map((path) => new URL(path, SCOPE_URL).href);
const ASSET_PATHS = new Set(ASSET_URLS.map((assetUrl) => new URL(assetUrl).pathname));

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(ASSET_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => Promise.all(cacheNames
        .filter((name) => name.startsWith("warn-map-shell-") && name !== CACHE_NAME)
        .map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const requestUrl = new URL(request.url);
  if (requestUrl.hostname === "wpiviguiqqajlxrbvtkx.supabase.co") {
    event.respondWith(fetch(request));
    return;
  }

  if (requestUrl.pathname.endsWith("/tiles/kenai.pmtiles")) {
    event.respondWith(fetch(request));
    return;
  }

  if (requestUrl.origin === SCOPE_URL.origin
      && requestUrl.pathname.endsWith("/graph/kenai-roads.json")) {
    event.respondWith(
      caches.open(CACHE_NAME)
        .then((cache) => cache.match(requestUrl.href))
        .then((cached) => cached || fetch(request).then((response) => {
          if (!response.ok) return response;
          return caches.open(CACHE_NAME)
            .then((cache) => cache.put(requestUrl.href, response.clone()))
            .then(() => response);
        })),
    );
    return;
  }

  if (request.mode === "navigate"
      && requestUrl.origin === SCOPE_URL.origin
      && requestUrl.pathname.startsWith(SCOPE_URL.pathname)) {
    event.respondWith(
      caches.open(CACHE_NAME)
        .then((cache) => cache.match(new URL("index.html", SCOPE_URL).href))
        .then((cached) => cached || fetch(request)),
    );
    return;
  }

  if (requestUrl.origin === SCOPE_URL.origin && ASSET_PATHS.has(requestUrl.pathname)) {
    event.respondWith(
      caches.open(CACHE_NAME)
        .then((cache) => cache.match(requestUrl.href, { ignoreSearch: true }))
        .then((cached) => cached || fetch(request)),
    );
  }
});