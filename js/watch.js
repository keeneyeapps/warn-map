// WARN Watch (watch.html): a read-only hazard board for a desktop monitor. The Map's basemap and hazard layer, plus a
// right-hand panel that summarises and lists every active hazard, newest first. No inputs, no location, no routes:
// it never asks the browser for a position and shows only what the public_hazards view already exposes.
import * as maplibregl from "../vendor/maplibre-gl.js";
import { REGION_FILE } from "./config.js";
import { formatHazardAge, openHazardPopup, startHazardLayer } from "./hazards.js";
import { createMap } from "./map.js";
import { loadDescriber } from "./place-describe.js";

const POLL_MS = 30_000;
const STALE_AFTER_MS = 100_000; // three missed polls
const HIGHLIGHT_MS = 10_000;
const AGE_TICK_MS = 15_000;
const QUIET_MS = 120_000;
const UPDATE_CHECK_MS = 30 * 60_000;
const FOCUS_ZOOM = 12.5;
// The basemap must draw before anything large is downloaded: on a slow link the 17 MB of place data, fetched at the
// same moment as the tiles, starved the tile requests until the connection dropped and the map stayed blank for good
// (2026-10-19, live site). Place data waits for the first drawn basemap, or this long if that never happens.
const PLACE_DATA_FALLBACK_MS = 120_000;
// A place-data download that failed (a slow or dropped link) is tried again after this long.
const PLACE_DATA_RETRY_MS = 5 * 60_000;
// If the basemap has not drawn by then, or its source fails before drawing, the page reloads itself when quiet.
const BASEMAP_TIMEOUT_MS = 90_000;
const BASEMAP_RETRY_GAP_MS = 10 * 60_000;
const BASEMAP_RETRY_KEY = "warn-watch-basemap-retry-at";
const BASEMAP_SOURCE = "protomaps";

const TEST_MODE = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
  && new URLSearchParams(location.search).get("test") === "1";

const map = createMap("map");
map.addControl(new maplibregl.NavigationControl(), "top-right");

const countNumber = document.querySelector("#watch-count-number");
const countLabel = document.querySelector("#watch-count-label");
const categoriesList = document.querySelector("#watch-categories");
const updatedLine = document.querySelector("#watch-updated");
const staleLine = document.querySelector("#watch-stale");
const list = document.querySelector("#watch-list");
const panel = document.querySelector("#watch-panel");
const mapStatus = document.querySelector("#watch-map-status");

let features = [];
let featuresById = new Map();
let knownIds = null; // null until the first successful load, so the first list is not all "new"
const highlightUntil = new Map();
const descriptions = new Map(); // "id|lon|lat" -> text
let describe = null;
let lastOkAt = null;
let lastRefreshFailed = false;
let selectedId = null;
let popup = null;
let fitted = false;
let hazardLayer = null;

function pad(value) {
  return String(value).padStart(2, "0");
}

function clock(timestamp) {
  const date = new Date(timestamp);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function descriptionKey(feature) {
  const [lon, lat] = feature.geometry.coordinates;
  return `${feature.properties.id}|${lon}|${lat}`;
}

function describeFeature(feature) {
  if (!describe) return "Locating…";
  const key = descriptionKey(feature);
  if (!descriptions.has(key)) descriptions.set(key, describe(feature.geometry.coordinates));
  return descriptions.get(key);
}

function confirmationsText(count) {
  return `${count} ${count === 1 ? "confirmation" : "confirmations"}`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderSummary() {
  countNumber.textContent = String(features.length);
  countLabel.textContent = features.length === 1 ? "active hazard" : "active hazards";
  const byCategory = new Map();
  for (const { properties } of features) {
    const entry = byCategory.get(properties.category) ?? { label: properties.category_label, color: properties.category_color, count: 0 };
    entry.count += 1;
    byCategory.set(properties.category, entry);
  }
  const entries = [...byCategory.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  categoriesList.replaceChildren(...entries.map((entry) => {
    const item = element("li", "watch-category");
    const dot = element("span", "watch-dot");
    dot.style.setProperty("--dot", entry.color);
    item.append(dot, element("span", "watch-category-label", entry.label), " ", element("span", "watch-category-count", String(entry.count)));
    return item;
  }));
}

function renderList() {
  const focusedId = document.activeElement?.closest?.(".watch-row")?.dataset.id;
  const now = Date.now();
  const sorted = [...features].sort((a, b) => Date.parse(b.properties.created_at) - Date.parse(a.properties.created_at)
    || String(b.properties.id).localeCompare(String(a.properties.id)));
  if (sorted.length === 0) {
    list.replaceChildren(element("li", "watch-empty", lastOkAt === null
      ? "Waiting for the first update…" : "No active hazards"));
    return;
  }
  list.replaceChildren(...sorted.map((feature) => {
    const { properties } = feature;
    const item = element("li", "watch-item");
    const row = element("button", "watch-row");
    row.type = "button";
    row.dataset.id = String(properties.id);
    row.dataset.created = properties.created_at;
    row.style.setProperty("--band", properties.category_color);
    if (String(properties.id) === String(selectedId)) row.classList.add("is-selected");
    if ((highlightUntil.get(String(properties.id)) ?? 0) > now) row.classList.add("is-new");
    const head = element("span", "watch-row-head");
    head.append(element("span", "watch-title", properties.title), element("span", "watch-age", formatHazardAge(properties.created_at, now)));
    row.append(
      head,
      element("span", "watch-category-name", properties.category_label),
      element("span", "watch-place", describeFeature(feature)),
      element("span", "watch-confirmations", confirmationsText(properties.confirmation_count)),
    );
    item.append(row);
    return item;
  }));
  if (focusedId) list.querySelector(`.watch-row[data-id="${CSS.escape(focusedId)}"]`)?.focus({ preventScroll: true });
}

function renderStatus() {
  const now = Date.now();
  const stale = lastRefreshFailed || (lastOkAt !== null && now - lastOkAt > STALE_AFTER_MS);
  panel.classList.toggle("is-stale", stale);
  updatedLine.textContent = lastOkAt === null ? (lastRefreshFailed ? "No data yet" : "Loading hazards") : `Updated ${clock(lastOkAt)}`;
  staleLine.hidden = !stale;
  staleLine.textContent = !stale ? ""
    : lastOkAt === null ? "Stale: the hazard feed cannot be reached yet."
      : `Stale: the last refresh failed. Showing data from ${clock(lastOkAt)}.`;
}

function onHazardsUpdate(nextFeatures) {
  const now = Date.now();
  const ids = new Set(nextFeatures.map((feature) => String(feature.properties.id)));
  if (knownIds !== null) {
    for (const id of ids) if (!knownIds.has(id)) highlightUntil.set(id, now + HIGHLIGHT_MS);
  }
  knownIds = ids;
  features = nextFeatures;
  featuresById = new Map(nextFeatures.map((feature) => [String(feature.properties.id), feature]));
  if (selectedId !== null && !featuresById.has(String(selectedId))) selectedId = null;
  if (!fitted && nextFeatures.length > 0) {
    fitted = true;
    const bounds = new maplibregl.LngLatBounds();
    for (const feature of nextFeatures) bounds.extend(feature.geometry.coordinates);
    map.fitBounds(bounds, { padding: 80, maxZoom: 11.5, duration: 0 });
  }
  renderSummary();
  renderList();
  renderStatus();
}

function onStatus({ ok, lastSuccessAt }) {
  lastRefreshFailed = !ok;
  if (lastSuccessAt !== null) lastOkAt = lastSuccessAt;
  renderStatus();
}

function focusFeature(id) {
  const feature = featuresById.get(String(id));
  if (!feature) return;
  selectedId = String(id);
  popup?.remove();
  const coordinate = feature.geometry.coordinates;
  map.flyTo({ center: coordinate, zoom: Math.max(map.getZoom(), FOCUS_ZOOM), essential: true });
  popup = openHazardPopup(map, feature.properties, coordinate);
  for (const row of list.querySelectorAll(".watch-row")) {
    row.classList.toggle("is-selected", row.dataset.id === String(id));
  }
}

list.addEventListener("click", (event) => {
  const row = event.target.closest(".watch-row");
  if (row) focusFeature(row.dataset.id);
});

// Ages tick on their own so "12 min ago" stays true between polls.
globalThis.setInterval(() => {
  const now = Date.now();
  for (const row of list.querySelectorAll(".watch-row")) {
    row.querySelector(".watch-age").textContent = formatHazardAge(row.dataset.created, now);
    if (!(highlightUntil.get(row.dataset.id) > now)) row.classList.remove("is-new");
  }
}, AGE_TICK_MS);
globalThis.setInterval(renderStatus, 1000);

// ---- the basemap first, then the place data ----

let basemapReady = false;
let basemapStuck = false;
let placeDataRequestedAt = null;

function startPlaceData() {
  if (placeDataRequestedAt !== null) return;
  placeDataRequestedAt = Date.now();
  loadDescriber().then((describer) => {
    describe = describer;
    descriptions.clear();
    renderList();
    if (!describer.complete) {
      globalThis.setTimeout(() => {
        placeDataRequestedAt = null;
        startPlaceData();
      }, (TEST_MODE && Number(globalThis.__WARN_WATCH_PLACE_RETRY_MS)) || PLACE_DATA_RETRY_MS);
    }
  }).catch(() => {});
}

function basemapLoaded() {
  try {
    return map.isSourceLoaded(BASEMAP_SOURCE) && map.querySourceFeatures(BASEMAP_SOURCE, { sourceLayer: "earth" }).length > 0;
  } catch {
    return false;
  }
}

function onBasemapReady() {
  if (basemapReady) return;
  basemapReady = true;
  basemapStuck = false;
  mapStatus.hidden = true;
  startPlaceData();
}

function onBasemapStuck() {
  if (basemapReady || basemapStuck) return;
  basemapStuck = true;
  let lastRetry = 0;
  try {
    lastRetry = Number(sessionStorage.getItem(BASEMAP_RETRY_KEY)) || 0;
  } catch {
    lastRetry = 0;
  }
  const retry = Date.now() - lastRetry >= BASEMAP_RETRY_GAP_MS;
  mapStatus.textContent = retry
    ? "The map is slow to load. The page will reload to try again."
    : "The map could not load. Hazards are still listed and updated.";
  mapStatus.hidden = false;
  if (retry) {
    try {
      sessionStorage.setItem(BASEMAP_RETRY_KEY, String(Date.now()));
    } catch {
      // No session storage: the gap is not enforced, the reload still waits for a quiet moment.
    }
    requestQuietReload();
  }
}

map.on("idle", () => {
  if (basemapLoaded()) onBasemapReady();
});
map.on("error", (event) => {
  if (event?.sourceId === BASEMAP_SOURCE && !basemapReady) onBasemapStuck();
});
const basemapTimeoutMs = (TEST_MODE && Number(globalThis.__WARN_WATCH_BASEMAP_MS)) || BASEMAP_TIMEOUT_MS;
globalThis.setTimeout(() => {
  if (!basemapReady) onBasemapStuck();
}, basemapTimeoutMs);
globalThis.setTimeout(startPlaceData, (TEST_MODE && Number(globalThis.__WARN_WATCH_PLACE_FALLBACK_MS)) || PLACE_DATA_FALLBACK_MS);

map.once("style.load", () => {
  hazardLayer = startHazardLayer(map, { intervalMs: POLL_MS, onHazardsUpdate, onStatus });
  if (TEST_MODE) {
    globalThis.__WARN_WATCH_TEST__ = {
      map, refresh: () => hazardLayer.refresh(), focus: focusFeature,
      state: () => ({ basemapReady, basemapStuck, placeDataRequestedAt, describerReady: !!describe }),
    };
  }
});

// ---- kiosk behaviour: screen on, refresh on return, quiet reload on a new version ----

let activeWakeLock = null;

async function requestWakeLock() {
  if (!navigator.wakeLock?.request || document.visibilityState !== "visible") return;
  try {
    const wakeLock = await navigator.wakeLock.request("screen");
    if (document.visibilityState !== "visible") {
      await wakeLock.release().catch(() => {});
      return;
    }
    activeWakeLock = wakeLock;
    wakeLock.addEventListener("release", () => {
      if (activeWakeLock === wakeLock) activeWakeLock = null;
    }, { once: true });
  } catch {
    activeWakeLock = null;
  }
}

let registration = null;

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    requestWakeLock();
    hazardLayer?.refresh();
    registration?.update().catch(() => {});
  } else if (activeWakeLock) {
    activeWakeLock.release().catch(() => {});
    activeWakeLock = null;
  }
});
requestWakeLock();

// A new worker version activates by itself (sw.js skips waiting). The page then reloads to pick up the new files, but
// only when nobody has touched it for a while, so a person reading or clicking is never interrupted. No prompt.
const quietMs = (TEST_MODE && Number(globalThis.__WARN_WATCH_QUIET_MS)) || QUIET_MS;
const reloadPage = (TEST_MODE && globalThis.__WARN_WATCH_RELOAD) || (() => globalThis.location.reload());
let lastInteractionAt = Date.now();
let updatePending = false;
function requestQuietReload() {
  updatePending = true;
}
for (const name of ["pointerdown", "keydown", "wheel", "touchstart"]) {
  globalThis.addEventListener(name, () => { lastInteractionAt = Date.now(); }, { passive: true, capture: true });
}
globalThis.setInterval(() => {
  if (updatePending && Date.now() - lastInteractionAt >= quietMs) {
    updatePending = false;
    reloadPage();
  }
}, TEST_MODE ? 500 : 10_000);

if ("serviceWorker" in navigator) {
  const serviceWorkerUrl = new URL("../sw.js", import.meta.url);
  serviceWorkerUrl.searchParams.set("region", REGION_FILE);
  const serviceWorkerScope = new URL("../", import.meta.url).pathname;
  let controlled = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (controlled) updatePending = true; // not the first install taking control
    controlled = true;
  });
  navigator.serviceWorker.register(serviceWorkerUrl, { scope: serviceWorkerScope })
    .then((registered) => {
      registration = registered;
      globalThis.setInterval(() => registered.update().catch(() => {}), UPDATE_CHECK_MS);
    })
    .catch(() => {});
}
