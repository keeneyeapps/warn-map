import { formatHazardAge, openHazardPopup } from "./hazards.js";
import { hazardsAlongRoute } from "./route-hazards.js";

const SOURCE_ID = "computed-route";
const CASING_LAYER_ID = "computed-route-casing";
const LINE_LAYER_ID = "computed-route-line";
const HINT_KEY = "warn-map-route-hint-dismissed";

function validPoint(point) {
  return Array.isArray(point) && point.length === 2
    && Number.isFinite(point[0]) && Number.isFinite(point[1]);
}

function routeBounds(coordinates) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const [longitude, latitude] of coordinates) {
    west = Math.min(west, longitude);
    south = Math.min(south, latitude);
    east = Math.max(east, longitude);
    north = Math.max(north, latitude);
  }
  return [[west, south], [east, north]];
}

function formatRouteStatus(route) {
  const miles = route.totalDistanceM / 1609.344;
  const minutes = Math.ceil(route.estimatedTimeSeconds / 60);
  return `Route ${miles.toFixed(1)} mi · ${minutes} min`;
}

export function setupRouting(map, {
  getLatestLocation,
  requestLocation,
  refreshHazards = () => Promise.resolve(),
}) {
  const routeStatus = document.querySelector("#route-status");
  const routeHint = document.querySelector("#route-hint");
  const routeAction = document.querySelector("#route-to-here");
  const clearRouteButton = document.querySelector("#clear-route");
  const hazardsPanel = document.querySelector("#route-hazards-panel");
  const hazardsToggle = document.querySelector("#route-hazards-toggle");
  const hazardsTitle = document.querySelector("#route-hazards-title");
  const hazardsList = document.querySelector("#route-hazards-list");
  const worker = new Worker(new URL("./routing-worker.js", import.meta.url), { type: "module" });
  const pendingRequests = new Map();
  let requestId = 0;
  let workerLoadTimeMs = null;
  let graphTransfer = null;
  let destination = null;
  let activeRoute = null;
  let hazardFeatures = [];
  let routeHazardRows = [];
  let liveFix = null;
  let lastFixRecomputeAt = -Infinity;
  let fixRecomputeTimer = null;
  let lastRouteFeature = null;
  let holdTimer = null;
  let holdPointer = null;
  let suppressMapClick = false;
  let suppressClickTimer = null;

  map.addSource(SOURCE_ID, {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });
  map.addLayer({
    id: CASING_LAYER_ID,
    type: "line",
    source: SOURCE_ID,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#17201f",
      "line-width": 8,
      "line-opacity": 0.9,
    },
  }, "hazard-circles");
  map.addLayer({
    id: LINE_LAYER_ID,
    type: "line",
    source: SOURCE_ID,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#54dfb3",
      "line-width": 4,
      "line-opacity": 0.98,
    },
  }, "hazard-circles");

  worker.addEventListener("message", ({ data }) => {
    if (data.type === "ready") {
      workerLoadTimeMs = data.loadTimeMs;
      graphTransfer = data.graphTransfer;
      return;
    }
    const pending = pendingRequests.get(data.requestId);
    if (!pending) return;
    pendingRequests.delete(data.requestId);
    if (data.type === "route-error") {
      pending.reject(new Error(data.message));
      return;
    }
    pending.resolve(data);
  });

  worker.addEventListener("error", () => {
    for (const pending of pendingRequests.values()) {
      pending.reject(new Error("The on-device routing worker failed."));
    }
    pendingRequests.clear();
  });

  function dismissHint() {
    routeHint.hidden = true;
    try {
      localStorage.setItem(HINT_KEY, "1");
    } catch {
      // Keep the hint dismissed for this page if storage is unavailable.
    }
  }

  try {
    routeHint.hidden = localStorage.getItem(HINT_KEY) === "1";
  } catch {
    routeHint.hidden = false;
  }

  function renderRouteHazards() {
    if (!activeRoute) {
      hazardsPanel.hidden = true;
      hazardsList.replaceChildren();
      routeHazardRows = [];
      return;
    }

    hazardsPanel.hidden = false;
    hazardsTitle.textContent = `${routeHazardRows.length} ${routeHazardRows.length === 1 ? "hazard" : "hazards"} on your route`;
    hazardsList.replaceChildren();
    if (routeHazardRows.length === 0) {
      const emptyMessage = document.createElement("p");
      emptyMessage.className = "route-hazards-empty";
      emptyMessage.textContent = "No hazards on your route";
      hazardsList.append(emptyMessage);
      return;
    }

    const now = Date.now();
    for (const row of routeHazardRows) {
      const properties = row.feature.properties;
      const button = document.createElement("button");
      button.className = "route-hazard-row";
      button.type = "button";
      button.setAttribute("role", "listitem");

      const dot = document.createElement("span");
      dot.className = "route-hazard-dot";
      dot.style.backgroundColor = properties.category_color || "#1976d2";
      dot.setAttribute("aria-hidden", "true");

      const title = document.createElement("span");
      title.className = "route-hazard-title";
      title.textContent = properties.title || properties.label || properties.specific
        || properties.hazard_type || "Hazard";

      const distance = document.createElement("span");
      distance.className = "route-hazard-distance";
      distance.textContent = `${(row.distanceAheadM / 1609.344).toFixed(1)} mi`;

      const age = document.createElement("span");
      age.className = "route-hazard-age";
      age.textContent = formatHazardAge(properties.created_at, now);

      button.append(dot, title, distance, age);
      button.addEventListener("click", () => {
        const coordinate = row.feature.geometry.coordinates;
        map.flyTo({ center: coordinate, zoom: Math.max(map.getZoom(), 14) });
        openHazardPopup(map, properties, coordinate);
      });
      hazardsList.append(button);
    }
  }

  function recomputeRouteHazards() {
    routeHazardRows = activeRoute
      ? hazardsAlongRoute(hazardFeatures, activeRoute, liveFix)
      : [];
    renderRouteHazards();
  }

  function setHazards(features) {
    hazardFeatures = features;
    recomputeRouteHazards();
  }

  function onLocationFix(point) {
    liveFix = validPoint(point) ? point : null;
    if (!activeRoute) return;
    if (fixRecomputeTimer !== null) clearTimeout(fixRecomputeTimer);
    const elapsed = performance.now() - lastFixRecomputeAt;
    const delay = Math.max(0, 5000 - elapsed);
    fixRecomputeTimer = window.setTimeout(() => {
      fixRecomputeTimer = null;
      lastFixRecomputeAt = performance.now();
      recomputeRouteHazards();
    }, delay);
  }

  function clearRoute(message = "") {
    map.getSource(SOURCE_ID).setData({ type: "FeatureCollection", features: [] });
    lastRouteFeature = null;
    activeRoute = null;
    routeStatus.textContent = message;
    clearRouteButton.hidden = true;
    recomputeRouteHazards();
  }

  function calculateRoute(start, end) {
    if (!validPoint(start) || !validPoint(end)) {
      return Promise.reject(new Error("Route coordinates are invalid."));
    }
    const id = ++requestId;
    routeStatus.textContent = "Calculating route…";
    return new Promise((resolve, reject) => {
      pendingRequests.set(id, { resolve, reject });
      worker.postMessage({ type: "route", requestId: id, start, destination: end });
    }).then((data) => {
      if (!data.route) {
        clearRoute(data.reason || "No route exists.");
        return data;
      }
      lastRouteFeature = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: data.route.geometry },
        properties: {},
      };
      activeRoute = data.route;
      const currentLocation = getLatestLocation();
      if (validPoint(currentLocation)) liveFix = currentLocation;
      map.getSource(SOURCE_ID).setData(lastRouteFeature);
      clearRouteButton.hidden = false;
      routeAction.hidden = true;
      routeStatus.textContent = formatRouteStatus(data.route);
      lastFixRecomputeAt = performance.now();
      recomputeRouteHazards();
      dismissHint();
      map.fitBounds(routeBounds(data.route.geometry), {
        padding: { top: 112, right: 52, bottom: 132, left: 52 },
        maxZoom: 12.5,
        duration: 450,
      });
      return data;
    }).catch((error) => {
      clearRoute(error.message || "Routing is unavailable.");
      throw error;
    });
  }

  function selectDestination(point) {
    destination = point;
    routeAction.hidden = false;
    routeStatus.textContent = "Destination selected · choose Route to here";
    dismissHint();
  }

  routeAction.addEventListener("click", async () => {
    if (!destination) return;
    routeAction.disabled = true;
    try {
      const start = getLatestLocation() || await requestLocation();
      await calculateRoute(start, destination);
    } catch (error) {
      routeStatus.textContent = error?.code === 1
        ? "Location permission was denied."
        : error?.message || "Your location could not be determined.";
    } finally {
      routeAction.disabled = false;
    }
  });

  clearRouteButton.addEventListener("click", () => {
    destination = null;
    clearRoute();
    routeAction.hidden = true;
  });

  hazardsToggle.addEventListener("click", () => {
    const expanded = hazardsToggle.getAttribute("aria-expanded") !== "false";
    hazardsToggle.setAttribute("aria-expanded", String(!expanded));
    hazardsList.hidden = expanded;
  });

  const canvasContainer = map.getCanvasContainer();
  function cancelHold() {
    if (holdTimer !== null) clearTimeout(holdTimer);
    holdTimer = null;
    holdPointer = null;
  }

  canvasContainer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".maplibregl-ctrl")) return;
    cancelHold();
    const rect = canvasContainer.getBoundingClientRect();
    holdPointer = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      point: map.unproject([event.clientX - rect.left, event.clientY - rect.top]).toArray(),
    };
    holdTimer = window.setTimeout(() => {
      if (!holdPointer) return;
      suppressMapClick = true;
      window.clearTimeout(suppressClickTimer);
      suppressClickTimer = window.setTimeout(() => { suppressMapClick = false; }, 1200);
      selectDestination(holdPointer.point);
      holdPointer = null;
      holdTimer = null;
    }, 600);
  });
  canvasContainer.addEventListener("pointermove", (event) => {
    if (!holdPointer || event.pointerId !== holdPointer.id) return;
    if (Math.hypot(event.clientX - holdPointer.x, event.clientY - holdPointer.y) > 10) cancelHold();
  });
  canvasContainer.addEventListener("pointerup", cancelHold);
  canvasContainer.addEventListener("pointercancel", cancelHold);
  canvasContainer.addEventListener("click", (event) => {
    if (!suppressMapClick || event.target !== map.getCanvas()) return;
    suppressMapClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  const url = new URL(globalThis.location.href);
  const isLocalTest = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    && url.searchParams.get("test") === "1";
  if (isLocalTest) {
    globalThis.__WARN_MAP_TEST__ = Object.freeze({
      route: (startLatLon, destinationLatLon) => calculateRoute(
        [startLatLon[1], startLatLon[0]],
        [destinationLatLon[1], destinationLatLon[0]],
      ),
      workerLoadTime: () => workerLoadTimeMs,
      graphTransfer: () => graphTransfer,
      routeLineRendered: () => map.queryRenderedFeatures({ layers: [LINE_LAYER_ID] }).length > 0,
      routeFeature: () => lastRouteFeature,
      routeHazards: () => routeHazardRows.map((row) => ({
        id: row.feature.id,
        title: row.feature.properties.title,
        distanceFromLineM: row.distanceFromLineM,
        distanceAheadM: row.distanceAheadM,
        distanceAlongRouteM: row.distanceAlongRouteM,
        edgeIndex: row.edgeIndex,
      })),
      setLiveFix: (latitudeLongitude) => onLocationFix(
        validPoint(latitudeLongitude)
          ? [latitudeLongitude[1], latitudeLongitude[0]]
          : null,
      ),
      refreshHazards,
    });
  }

  return { setHazards, onLocationFix };
}