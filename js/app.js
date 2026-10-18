import * as maplibregl from "../vendor/maplibre-gl.js";
import { REGION_FILE } from "./config.js";
import { startHazardLayer } from "./hazards.js";
import { createMap } from "./map.js";
import { setupRouting } from "./routing.js";

const MOVING_SPEED_MS = 1.5;
const MIN_COURSE_DISTANCE_M = 15;

const map = createMap("map");
const locationStatus = document.querySelector("#location-status");
const locateButton = document.querySelector("#locate");
const wakeStatus = document.querySelector("#wake-status");
let activeWakeLock = null;
let latestLocation = null;
let routingController = null;
let watchId = null;
let followMode = "off";
let followPaused = false;
let lastCourse = null;
let courseAnchor = null;
let receivedWatchFix = false;
let toastTimer = null;

function showToast(message, durationMs = 3000) {
  window.clearTimeout(toastTimer);
  locationStatus.textContent = message;
  locationStatus.hidden = false;
  toastTimer = window.setTimeout(() => {
    locationStatus.hidden = true;
    locationStatus.textContent = "";
  }, durationMs);
}

function distanceMeters(first, second) {
  const latitudeScale = 111_320;
  const longitudeScale = latitudeScale * Math.cos(first[1] * Math.PI / 180);
  return Math.hypot((second[0] - first[0]) * longitudeScale, (second[1] - first[1]) * latitudeScale);
}

function bearingDegrees(first, second) {
  const latitude1 = first[1] * Math.PI / 180;
  const latitude2 = second[1] * Math.PI / 180;
  const deltaLongitude = (second[0] - first[0]) * Math.PI / 180;
  const y = Math.sin(deltaLongitude) * Math.cos(latitude2);
  const x = Math.cos(latitude1) * Math.sin(latitude2)
    - Math.sin(latitude1) * Math.cos(latitude2) * Math.cos(deltaLongitude);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// Positions only ever go to the map and the routing controller; nothing here touches the network.
function setLatestLocation(point) {
  latestLocation = point;
  routingController?.onLocationFix(point);
  map.getSource("device-location")?.setData({
    type: "Feature",
    geometry: { type: "Point", coordinates: point },
    properties: {},
  });
}

function requestCurrentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Location is not available in this browser."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        const point = [coords.longitude, coords.latitude];
        setLatestLocation(point);
        resolve(point);
      },
      reject,
      { enableHighAccuracy: false, maximumAge: 0, timeout: 10000 },
    );
  });
}

// Course while moving: the device heading when it reports one, else the bearing between fixes.
function courseForFix(coords, point) {
  const speed = Number.isFinite(coords.speed) ? coords.speed : null;
  if (Number.isFinite(coords.heading) && speed !== null && speed >= MOVING_SPEED_MS) {
    courseAnchor = point;
    return coords.heading;
  }
  if (!courseAnchor) {
    courseAnchor = point;
    return null;
  }
  if (speed !== null && speed < MOVING_SPEED_MS) return null;
  if (distanceMeters(courseAnchor, point) < MIN_COURSE_DISTANCE_M) return null;
  const course = bearingDegrees(courseAnchor, point);
  courseAnchor = point;
  return course;
}

function recentre(point, { initial = false } = {}) {
  const options = { center: point, duration: 600, essential: true };
  if (initial) options.zoom = Math.max(map.getZoom(), 14);
  if (followMode === "heading" && lastCourse !== null) options.bearing = lastCourse;
  map.easeTo(options);
}

function renderLocateButton() {
  const state = followMode === "off" ? "off" : followPaused ? "paused" : followMode;
  const labels = {
    off: "Locate me",
    follow: "Following",
    heading: "Heading up",
    paused: "Resume following",
  };
  locateButton.dataset.state = state;
  locateButton.textContent = labels[state];
  locateButton.setAttribute("aria-pressed", String(state === "follow" || state === "heading"));
}

function onWatchFix({ coords }) {
  const point = [coords.longitude, coords.latitude];
  const course = courseForFix(coords, point);
  if (course !== null) lastCourse = course;
  setLatestLocation(point);
  const firstFix = !receivedWatchFix;
  receivedWatchFix = true;
  if (firstFix) showToast("Showing your location on this device.");
  if (followMode !== "off" && !followPaused) recentre(point, { initial: firstFix });
}

function onWatchError(error) {
  if (error.code === 1) {
    setFollowMode("off");
    showToast("Location permission was denied.", 6000);
    return;
  }
  if (!receivedWatchFix) showToast(error.message || "Your location could not be determined.", 6000);
}

function startWatch() {
  if (watchId !== null || followMode === "off" || document.visibilityState !== "visible") return;
  if (!navigator.geolocation) {
    setFollowMode("off");
    showToast("Location is not available in this browser.", 6000);
    return;
  }
  watchId = navigator.geolocation.watchPosition(onWatchFix, onWatchError, {
    enableHighAccuracy: true,
    maximumAge: 5000,
    timeout: 20000,
  });
}

function stopWatch() {
  if (watchId === null) return;
  navigator.geolocation.clearWatch(watchId);
  watchId = null;
}

function setFollowMode(mode) {
  followMode = mode;
  followPaused = false;
  renderLocateButton();
  if (mode === "off") {
    stopWatch();
    receivedWatchFix = false;
    courseAnchor = null;
    lastCourse = null;
    if (map.getBearing() !== 0) map.easeTo({ bearing: 0, duration: 400 });
    return;
  }
  startWatch();
  if (mode === "follow" && map.getBearing() !== 0) map.easeTo({ bearing: 0, duration: 400 });
  if (latestLocation && receivedWatchFix) recentre(latestLocation);
}

function setWakeStatus(message) {
  wakeStatus.textContent = message;
}

async function requestWakeLock() {
  if (!navigator.wakeLock?.request) {
    setWakeStatus("not supported");
    return;
  }
  if (document.visibilityState !== "visible") return;

  try {
    const wakeLock = await navigator.wakeLock.request("screen");
    if (document.visibilityState !== "visible") {
      await wakeLock.release().catch(() => {});
      setWakeStatus("screen may sleep");
      return;
    }
    activeWakeLock = wakeLock;
    setWakeStatus("screen stays on");
    wakeLock.addEventListener("release", () => {
      if (activeWakeLock === wakeLock) {
        activeWakeLock = null;
        setWakeStatus("screen may sleep");
      }
    }, { once: true });
  } catch {
    activeWakeLock = null;
    setWakeStatus("screen may sleep");
  }
}

function releaseWakeLock() {
  const wakeLock = activeWakeLock;
  activeWakeLock = null;
  setWakeStatus("screen may sleep");
  if (wakeLock) wakeLock.release().catch(() => {});
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    requestWakeLock();
    startWatch();
  } else {
    releaseWakeLock();
    stopWatch();
  }
});

requestWakeLock();

if ("serviceWorker" in navigator) {
  // The worker reads the same region file to know which data files to precache.
  const serviceWorkerUrl = new URL("../sw.js", import.meta.url);
  serviceWorkerUrl.searchParams.set("region", REGION_FILE);
  const serviceWorkerScope = new URL("../", import.meta.url).pathname;
  navigator.serviceWorker.register(serviceWorkerUrl, { scope: serviceWorkerScope }).catch(() => {});
}

map.addControl(new maplibregl.NavigationControl(), "top-right");

// Only a person dragging the map pauses following; programmatic camera moves have no originalEvent.
map.on("dragstart", (event) => {
  if (!event.originalEvent || followMode === "off" || followPaused) return;
  followPaused = true;
  renderLocateButton();
});

map.once("style.load", () => {
  let hazardLayer;
  hazardLayer = startHazardLayer(map, {
    onHazardsUpdate: (features) => routingController?.setHazards(features),
  });
  routingController = setupRouting(map, {
    getLatestLocation: () => latestLocation,
    requestLocation: requestCurrentPosition,
    refreshHazards: () => hazardLayer.refresh(),
  });

  map.addSource("device-location", {
    type: "geojson",
    data: latestLocation
      ? { type: "Feature", geometry: { type: "Point", coordinates: latestLocation }, properties: {} }
      : { type: "FeatureCollection", features: [] },
  });

  map.addLayer({
    id: "device-location-halo",
    type: "circle",
    source: "device-location",
    paint: {
      "circle-radius": 11,
      "circle-color": "#45d7ab",
      "circle-opacity": 0.28,
    },
  });

  map.addLayer({
    id: "device-location-dot",
    type: "circle",
    source: "device-location",
    paint: {
      "circle-radius": 6,
      "circle-color": "#45d7ab",
      "circle-stroke-color": "#10211d",
      "circle-stroke-width": 2,
    },
  });
});

locateButton.addEventListener("click", () => {
  if (followMode === "off") {
    setFollowMode("follow");
  } else if (followPaused) {
    followPaused = false;
    renderLocateButton();
    if (latestLocation) recentre(latestLocation);
  } else if (followMode === "follow") {
    setFollowMode("heading");
  } else {
    setFollowMode("off");
  }
});

renderLocateButton();
