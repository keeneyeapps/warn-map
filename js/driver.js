// Driver tools for professional drivers: tariff settings, the planner fare, and a trip meter. Loaded by app.js on
// the meter page (meter.html, <html data-app="meter">) only, where they are always on: the page itself is the
// opt-in. No other page loads this module. Every tariff value and trip record stays in this browser's localStorage.

import {
  appendRow, clearRunning, clearTraces, downloadTrace, loadRunning, loadTraces, newLog, saveRunning, storeTrace,
} from "./trace.js";

const TRIP_KEY = "warn-map-trip-running";
const TRIPS_KEY = "warn-map-trips";
const MAX_TRIPS = 200;
const PERSIST_INTERVAL_MS = 10_000;
const MAX_ACCURACY_M = 50;
const MAX_SPEED_MS = 100 * 0.44704; // a jump implying more than 100 mph is a bad fix
const SLOW_STREAK = 3; // consecutive slow fixes before time counts as waiting
const METERS_PER_MILE = 1609.344;

export const TARIFF_FIELDS = [
  { key: "start", storage: "warn-map-tariff-start", label: "Start fare", unit: "$", fallback: 3.75,
    help: "Charged once when a trip starts." },
  { key: "increment", storage: "warn-map-tariff-increment", label: "Increment", unit: "$", fallback: 0.20,
    help: "The fare rises in steps of this amount." },
  // The planner's earlier "Rate per mile" setting used this key, so a saved rate carries over.
  { key: "ratePerMile", storage: "warn-map-fare-rate", label: "Rate per mile", unit: "$", fallback: 2.80,
    help: "Distance charge; one increment every increment ÷ rate miles." },
  { key: "waitPerMinute", storage: "warn-map-tariff-wait-rate", label: "Wait rate per minute", unit: "$",
    fallback: 0.50, help: "Waiting charge, per full minute of cumulative waiting; partial minutes are not charged." },
  { key: "waitBelowMph", storage: "warn-map-tariff-wait-below", label: "Waiting below", unit: "mph", fallback: 12,
    help: "Time counts as waiting below this speed, after 3 slow fixes in a row." },
];

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage unavailable: the value applies to this page only.
  }
}

export function tariff() {
  const values = {};
  for (const field of TARIFF_FIELDS) {
    const stored = Number.parseFloat(readStorage(field.storage) ?? "");
    values[field.key] = Number.isFinite(stored) && stored > 0 ? stored : field.fallback;
  }
  return values;
}

/**
 * Fare, after a cab meter: start + increment x distance steps + wait rate x whole minutes of cumulative wait.
 * A distance step is increment / rate per mile (1/14 mile by default), the miles rounded half-up to a whole step.
 * Wait is the trip's total waiting seconds, rounded to whole seconds (as the trip record stores them), floored to
 * whole minutes: a partial minute is not charged, and stops do not restart the count. Wait takes no part in the
 * step rounding.
 */
export function distanceUnits(miles, rates = tariff()) {
  const distanceUnitMiles = rates.increment / rates.ratePerMile;
  // Half-up, with a tolerance so 3.4999999999 from binary arithmetic still counts as 3.5.
  return Math.floor(miles / distanceUnitMiles + 0.5 + 1e-9);
}

export function waitMinutes(waitSeconds) {
  return Math.floor(Math.round(waitSeconds) / 60);
}

export function fareFor(miles, waitSeconds = 0, rates = tariff()) {
  const cents = Math.round(rates.start * 100)
    + Math.round(rates.increment * 100) * distanceUnits(miles, rates)
    + Math.round(rates.waitPerMinute * 100) * waitMinutes(waitSeconds);
  return cents / 100;
}

export function formatMoney(dollars) {
  return `$${dollars.toFixed(2)}`;
}

function formatWait(seconds) {
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

function formatMiles(meters) {
  return `${(meters / METERS_PER_MILE).toFixed(1)} mi`;
}

function haversineMeters(first, second) {
  const toRadians = Math.PI / 180;
  const dLat = (second[1] - first[1]) * toRadians;
  const dLon = (second[0] - first[0]) * toRadians;
  const value = Math.sin(dLat / 2) ** 2
    + Math.cos(first[1] * toRadians) * Math.cos(second[1] * toRadians) * Math.sin(dLon / 2) ** 2;
  return 2 * 6_371_008.8 * Math.asin(Math.sqrt(value));
}

function readTrips() {
  try {
    const trips = JSON.parse(readStorage(TRIPS_KEY) ?? "[]");
    return Array.isArray(trips) ? trips : [];
  } catch {
    return [];
  }
}

function tripShareText(trip) {
  const start = new Date(trip.startTime);
  const end = new Date(trip.endTime);
  const day = start.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const time = (date) => date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const lines = [];
  if (trip.startPlace || trip.endPlace) lines.push(`${trip.startPlace || "Start"} → ${trip.endPlace || "End"}`);
  lines.push(`${trip.miles.toFixed(1)} mi · wait ${formatWait(trip.waitSeconds)}`);
  lines.push(`Fare ${formatMoney(trip.fare)}`);
  lines.push(`${day}, ${time(start)}–${time(end)}`);
  return lines.join("\n");
}

async function shareText(text, button) {
  if (typeof navigator.share === "function") {
    try {
      await navigator.share({ text });
      return;
    } catch (error) {
      if (error?.name === "AbortError") return;
    }
  }
  let label = "Copied";
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    label = "Copy failed";
  }
  const original = button.textContent;
  button.textContent = label;
  globalThis.setTimeout(() => {
    if (button.isConnected) button.textContent = original;
  }, 2000);
}

function element(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

/**
 * Wires the driver tools into the page. `routeDistance(from, to)` gives the road distance in meters between two
 * [lon, lat] points (snapped to the road graph) or null; `placeNameNear(point)` a nearby place name or null.
 */
export function setupDriverTools({
  settingsPanel, controls, beforeControl, showToast, routeDistance, placeNameNear, keepScreenOn, onTariffChange,
}) {
  let mounted = null;
  let trip = null;
  let fixQueue = Promise.resolve();
  let watchId = null;
  let persistTimer = null;
  let traceLog = newLog(); // one row per GPS fix of the running trip; see trace.js
  let tracePersistedRows = -1;

  // ---- trip state ----

  function persistTrip() {
    writeStorage(TRIP_KEY, trip ? JSON.stringify(trip) : null);
    if (!trip) {
      clearRunning();
      tracePersistedRows = -1;
    } else if (traceLog.lines.length !== tracePersistedRows) {
      // A full browser store costs the trace, never the trip: the result is deliberately not checked.
      saveRunning(traceLog);
      tracePersistedRows = traceLog.lines.length;
    }
  }

  function tripFare(state = trip) {
    return fareFor(state.meters / METERS_PER_MILE, state.waitSeconds);
  }

  async function acceptFix(position) {
    if (!trip) return;
    const { coords } = position;
    const time = Number.isFinite(position.timestamp) ? position.timestamp : Date.now();
    // The trace row for this fix, accepted or not (trace.js). Filled in as the fix is judged.
    const row = {
      t: (time - trip.startTime) / 1000, lat: coords.latitude, lon: coords.longitude,
      accuracy: coords.accuracy, speed: coords.speed,
    };
    const reject = (reason) => {
      row.reject = reason;
      appendRow(traceLog, row);
    };
    if (!Number.isFinite(coords.latitude) || !Number.isFinite(coords.longitude)) return reject("no_position");
    const point = [coords.longitude, coords.latitude];
    const last = trip.lastFix;
    if (last) {
      row.interval = (time - last.time) / 1000;
      row.straight = haversineMeters(last.point, point);
    }
    if (Number.isFinite(coords.accuracy) && coords.accuracy > MAX_ACCURACY_M) return reject("low_accuracy");
    if (!last) {
      trip.lastFix = { point, time };
      if (!trip.startPlace) trip.startPlace = placeNameNear(point);
      row.added = 0;
      appendRow(traceLog, row);
      render();
      return;
    }
    const seconds = (time - last.time) / 1000;
    if (seconds <= 0) return reject("bad_time");
    const straight = row.straight;
    if (straight / seconds > MAX_SPEED_MS) return reject("speed_jump");

    // Road distance between the snapped fixes; a straight line when the snap gives no sensible road path.
    let meters = straight;
    try {
      const road = await routeDistance(last.point, point);
      if (Number.isFinite(road)) row.road = road;
      if (Number.isFinite(road) && road <= Math.max(straight * 1.5, straight + 150)) meters = road;
    } catch {
      // Routing unavailable: keep the straight-line distance.
    }
    if (!trip) return;

    const speed = Number.isFinite(coords.speed) && coords.speed >= 0 ? coords.speed : straight / seconds;
    const slow = speed < trip.waitBelowMs;
    const waitBefore = trip.waitSeconds;
    if (slow) {
      trip.slowSeconds.push(seconds);
      if (trip.slowSeconds.length === SLOW_STREAK) {
        trip.waitSeconds += trip.slowSeconds.reduce((sum, value) => sum + value, 0);
      } else if (trip.slowSeconds.length > SLOW_STREAK) {
        trip.waitSeconds += seconds;
      }
    } else {
      trip.slowSeconds = [];
    }
    trip.meters += meters;
    trip.lastFix = { point, time };
    row.added = meters;
    row.slow = slow;
    row.waitAdded = trip.waitSeconds - waitBefore;
    appendRow(traceLog, row);
    render();
  }

  function onFix(position) {
    fixQueue = fixQueue.then(() => acceptFix(position)).catch(() => {});
  }

  function startWatching() {
    if (watchId !== null || !navigator.geolocation) return;
    watchId = navigator.geolocation.watchPosition(onFix, () => {}, {
      enableHighAccuracy: true,
      maximumAge: 0,
      timeout: 20000,
    });
    persistTimer = globalThis.setInterval(persistTrip, PERSIST_INTERVAL_MS);
    keepScreenOn();
  }

  function stopWatching() {
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    globalThis.clearInterval(persistTimer);
    persistTimer = null;
  }

  function startTrip() {
    const rates = tariff();
    trip = {
      startTime: Date.now(),
      meters: 0,
      waitSeconds: 0,
      slowSeconds: [],
      waitBelowMs: rates.waitBelowMph * 0.44704,
      lastFix: null,
      startPlace: null,
    };
    traceLog = newLog();
    tracePersistedRows = -1;
    persistTrip();
    startWatching();
    render();
  }

  async function endTrip() {
    await fixQueue;
    if (!trip) return;
    stopWatching();
    const record = {
      startTime: trip.startTime,
      endTime: Date.now(),
      miles: Math.round((trip.meters / METERS_PER_MILE) * 100) / 100,
      waitSeconds: Math.round(trip.waitSeconds),
      // The exact figures behind the rounded ones above, so rounding cannot lose them again.
      metersExact: trip.meters,
      waitSecondsExact: trip.waitSeconds,
      fare: tripFare(),
      startPlace: trip.startPlace || null,
      endPlace: trip.lastFix ? placeNameNear(trip.lastFix.point) : null,
    };
    // The trip record first, so a full browser store can cost the trace but never the trip.
    writeStorage(TRIPS_KEY, JSON.stringify([record, ...readTrips()].slice(0, MAX_TRIPS)));
    if (traceLog.lines.length > 0 && !storeTrace(record.startTime, traceLog).saved) {
      showToast("Trace not saved: browser storage is full");
    }
    traceLog = newLog();
    trip = null;
    persistTrip();
    render(record);
  }

  // ---- UI (the meter page only) ----

  function tariffSection() {
    const section = element("section", { className: "driver-tariff", id: "driver-tariff" });
    section.append(element("h2", { textContent: "Tariff" }));
    for (const field of TARIFF_FIELDS) {
      const id = `tariff-${field.key}`;
      const input = element("input", {
        id, type: "number", inputMode: "decimal", min: "0", step: field.unit === "mph" ? "1" : "0.01",
        placeholder: String(field.fallback), autocomplete: "off",
      });
      input.value = readStorage(field.storage) ?? "";
      input.addEventListener("input", () => {
        writeStorage(field.storage, input.value.trim() || null);
        onTariffChange();
        render();
      });
      const label = element("label", { htmlFor: id, textContent: `${field.label} (${field.unit === "$" ? `$${field.fallback.toFixed(2)}` : `${field.fallback} mph`} default)` });
      section.append(label, input, element("p", { className: "driver-help", textContent: field.help }));
    }
    return section;
  }

  function tripsSection() {
    const section = element("section", { className: "driver-trips", id: "driver-trips" });
    const list = element("ol", { className: "driver-trip-list" });
    const trips = readTrips();
    const traces = new Map(loadTraces().map((trace) => [trace.startTime, trace]));
    section.append(
      element("h2", { textContent: `Trips (${trips.length})` }),
      element("p", {
        className: "driver-help", id: "driver-trace-note",
        textContent: "A GPS trace of your last 10 trips is kept on this device for meter calibration. It is never "
          + "uploaded. Export a trace to a file, or delete all traces, from a trip below.",
      }),
    );
    for (const saved of trips) {
      const share = element("button", { type: "button", className: "driver-button", textContent: "Share" });
      share.addEventListener("click", () => shareText(tripShareText(saved), share));
      const actions = [share];
      const trace = traces.get(saved.startTime);
      if (trace) {
        const exportTrace = element("button", {
          type: "button", className: "driver-button driver-export-trace", textContent: "Export trace",
        });
        exportTrace.addEventListener("click", () => downloadTrace(trace));
        const deleteTraces = element("button", {
          type: "button", className: "driver-button driver-delete-traces", textContent: "Delete traces",
        });
        deleteTraces.addEventListener("click", () => {
          clearTraces();
          render();
        });
        actions.push(exportTrace, deleteTraces);
      }
      const when = new Date(saved.startTime).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      });
      list.append(element("li", {}, [
        element("span", {
          className: "driver-trip-text",
          textContent: `${when} · ${saved.miles.toFixed(1)} mi · wait ${formatWait(saved.waitSeconds)} · ${formatMoney(saved.fare)}`,
        }),
        element("div", { className: "driver-actions" }, actions),
      ]));
    }
    if (trips.length > 0) {
      const clear = element("button", { type: "button", className: "driver-button", textContent: "Clear all" });
      clear.addEventListener("click", () => {
        writeStorage(TRIPS_KEY, null);
        clearTraces(); // a trip's trace goes with the trip
        render();
      });
      section.append(list, clear);
    } else {
      section.append(element("p", { className: "driver-help", textContent: "No saved trips yet." }));
    }
    return section;
  }

  function tripControls(summary) {
    const box = element("div", { className: "driver-trip", id: "driver-trip" });
    if (trip) {
      box.append(element("p", {
        className: "driver-trip-live", id: "driver-trip-live",
        textContent: `Trip ${formatMiles(trip.meters)} · wait ${formatWait(trip.waitSeconds)} · ${formatMoney(tripFare())}`,
      }));
      const end = element("button", { type: "button", className: "driver-trip-button", id: "end-trip", textContent: "End trip" });
      end.addEventListener("click", endTrip);
      box.append(end);
    } else if (summary) {
      const share = element("button", { type: "button", className: "driver-button", textContent: "Share" });
      share.addEventListener("click", () => shareText(tripShareText(summary), share));
      const dismiss = element("button", { type: "button", className: "driver-button", textContent: "Dismiss" });
      dismiss.addEventListener("click", () => render());
      box.append(
        element("p", { className: "driver-trip-summary", id: "driver-trip-summary", textContent: tripShareText(summary) }),
        element("div", { className: "driver-actions" }, [share, dismiss]),
      );
    } else {
      const start = element("button", { type: "button", className: "driver-trip-button", id: "start-trip", textContent: "Start trip" });
      start.addEventListener("click", startTrip);
      box.append(start);
    }
    return box;
  }

  function render(summary = null) {
    if (!mounted) {
      mounted = { settings: element("div", { className: "driver-settings", id: "driver-settings" }), trip: null };
      settingsPanel.prepend(mounted.settings);
    }
    const focusedId = document.activeElement?.id;
    mounted.settings.replaceChildren(tariffSection(), tripsSection());
    if (focusedId?.startsWith("tariff-")) document.getElementById(focusedId)?.focus();
    const controlsBox = tripControls(summary);
    if (mounted.trip) mounted.trip.replaceWith(controlsBox);
    else controls.insertBefore(controlsBox, beforeControl);
    mounted.trip = controlsBox;
  }

  // ---- resume a trip that was running when the page reloaded ----

  try {
    const saved = JSON.parse(readStorage(TRIP_KEY) ?? "null");
    if (saved && Number.isFinite(saved.meters) && Number.isFinite(saved.waitSeconds)) {
      trip = { slowSeconds: [], lastFix: null, startPlace: null, ...saved };
      traceLog = loadRunning() ?? newLog();
      tracePersistedRows = traceLog.lines.length;
      startWatching();
    }
  } catch {
    trip = null;
  }
  globalThis.addEventListener("pagehide", () => {
    if (trip) persistTrip();
  });
  render();

  return {
    // For tests on localhost only (see routing's __WARN_MAP_TEST__): the live trip totals.
    tripState: () => (trip
      ? { meters: trip.meters, waitSeconds: trip.waitSeconds, fare: tripFare(), traceRows: traceLog.lines.length }
      : null),
  };
}
