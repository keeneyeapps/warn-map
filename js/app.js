import * as maplibregl from "../vendor/maplibre-gl.js";
import { startHazardLayer } from "./hazards.js";
import { createMap } from "./map.js";

const map = createMap("map");
const status = document.querySelector("#location-status");
const locateButton = document.querySelector("#locate");
const wakeStatus = document.querySelector("#wake-status");
let activeWakeLock = null;

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
  if (document.visibilityState === "visible") requestWakeLock();
  else releaseWakeLock();
});

requestWakeLock();

if ("serviceWorker" in navigator) {
  const serviceWorkerUrl = new URL("../sw.js", import.meta.url);
  const serviceWorkerScope = new URL("../", import.meta.url).pathname;
  navigator.serviceWorker.register(serviceWorkerUrl, { scope: serviceWorkerScope }).catch(() => {});
}

map.addControl(new maplibregl.NavigationControl(), "top-right");

map.on("load", () => {
  startHazardLayer(map);

  map.addSource("device-location", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
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
  if (!navigator.geolocation) {
    status.textContent = "Location is not available in this browser.";
    return;
  }

  locateButton.disabled = true;
  status.textContent = "Finding your location...";

  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      const point = [coords.longitude, coords.latitude];
      map.flyTo({ center: point, zoom: 13 });
      map.getSource("device-location")?.setData({
        type: "Feature",
        geometry: { type: "Point", coordinates: point },
        properties: {},
      });
      status.textContent = "Showing your location on this device.";
      locateButton.disabled = false;
    },
    (error) => {
      status.textContent = error.code === error.PERMISSION_DENIED
        ? "Location permission was denied."
        : "Your location could not be determined.";
      locateButton.disabled = false;
    },
    { enableHighAccuracy: false, maximumAge: 0, timeout: 10000 },
  );
});