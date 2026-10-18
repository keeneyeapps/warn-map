importScripts("./js/precache-list.js");

const CACHE_NAME = "warn-map-shell-v20";
const SCOPE_URL = new URL(self.registration.scope);
const ASSET_URLS = self.WARN_PRECACHE_URLS.map((path) => new URL(path, SCOPE_URL).href);
const ASSET_PATHS = new Set(ASSET_URLS.map((assetUrl) => new URL(assetUrl).pathname));
// app.js registers this worker with ?region= set to config.js's REGION_FILE.
const REGION_URL = new URL(new URL(self.location.href).searchParams.get("region"), SCOPE_URL).href;

let regionAssetsPromise = null;

// Region data paths by how they are served, read from the region file itself so a new region's files are
// picked up without editing this worker: the region file and its small data files are precached, the road
// graph is warmed at install and cached on first use, and tiles (range requests) always go to the network.
function regionAssets() {
  if (!regionAssetsPromise) {
    // This version's cache only, so an install never reads an older version's region file.
    regionAssetsPromise = caches.open(CACHE_NAME)
      .then((cache) => cache.match(REGION_URL))
      .then((cached) => cached || fetch(REGION_URL))
      .then((response) => {
        if (!response.ok) throw new Error(`Region file request failed (${response.status})`);
        return response.json();
      })
      .then(({ files }) => {
        const href = (file) => (file?.path ? new URL(file.path, SCOPE_URL).href : null);
        const { tiles, graph, ...precached } = files;
        const precache = [REGION_URL, ...Object.values(precached).map(href)].filter(Boolean);
        return {
          precache,
          precachePaths: new Set(precache.map((url) => new URL(url).pathname)),
          graph: href(graph),
          tilesPath: tiles?.path ? new URL(href(tiles)).pathname : null,
        };
      })
      .catch((error) => {
        regionAssetsPromise = null;
        throw error;
      });
  }
  return regionAssetsPromise;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    Promise.all([caches.open(CACHE_NAME), regionAssets()])
      .then(([cache, assets]) => cache.addAll([...ASSET_URLS, ...assets.precache])
        // Warm the road graph so routing works offline after one visit; a failure here
        // must not block install, because the fetch handler also caches it on first use.
        .then(() => assets.graph && cache.add(assets.graph).catch(() => {})))
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

function cacheFirst(request, requestUrl) {
  return caches.open(CACHE_NAME)
    .then((cache) => cache.match(requestUrl.href, { ignoreSearch: true }))
    .then((cached) => cached || fetch(request));
}

function cacheOnUse(request, requestUrl) {
  return caches.open(CACHE_NAME)
    .then((cache) => cache.match(requestUrl.href))
    .then((cached) => cached || fetch(request).then((response) => {
      if (!response.ok) return response;
      return caches.open(CACHE_NAME)
        .then((cache) => cache.put(requestUrl.href, response.clone()))
        .then(() => response);
    }));
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const requestUrl = new URL(request.url);
  if (requestUrl.hostname === "wpiviguiqqajlxrbvtkx.supabase.co") {
    event.respondWith(fetch(request));
    return;
  }

  if (requestUrl.origin !== SCOPE_URL.origin || !requestUrl.pathname.startsWith(SCOPE_URL.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      caches.open(CACHE_NAME)
        .then((cache) => cache.match(new URL("index.html", SCOPE_URL).href))
        .then((cached) => cached || fetch(request)),
    );
    return;
  }

  if (ASSET_PATHS.has(requestUrl.pathname)) {
    event.respondWith(cacheFirst(request, requestUrl));
    return;
  }

  event.respondWith(regionAssets().then((assets) => {
    if (requestUrl.pathname === assets.tilesPath) return fetch(request);
    if (assets.precachePaths.has(requestUrl.pathname)) return cacheFirst(request, requestUrl);
    if (requestUrl.href === assets.graph) return cacheOnUse(request, requestUrl);
    return fetch(request);
  }, () => fetch(request)));
});
