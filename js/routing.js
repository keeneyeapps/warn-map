import * as maplibregl from "../vendor/maplibre-gl.js";
import { loadAddressIndex, nearestNamedPlace, normaliseTokens, searchAddresses } from "./addresses.js";
import { formatHazardAge, openHazardPopup } from "./hazards.js";
import { hazardsAlongRoute } from "./route-hazards.js";
import { region, regionFileUrl } from "./region.js";

const SOURCE_ID = "computed-route";
// A plain tap this close to an indexed place opens that place's popup.
const PLACE_TAP_RADIUS_M = 30;
const CASING_LAYER_ID = "computed-route-casing";
const LINE_LAYER_ID = "computed-route-line";
const HINT_KEY = "warn-map-route-hint-dismissed";
const FARE_RATE_KEY = "warn-map-fare-rate";
const METERS_PER_MILE = 1609.344;
const SHEET_TITLES = {
  start: "Set start",
  stop: "Add a stop",
  destination: "Set destination",
};
const PICK_PROMPTS = {
  start: "Tap the map to set the start",
  stop: "Tap the map to add a stop",
  destination: "Tap the map to set the destination",
};
const PLACE_SEARCH_RADIUS_M = 12_000;
const DESTINATION_FLAG_SVG = '<svg viewBox="0 0 22 28" width="22" height="28" aria-hidden="true">'
  + '<path d="M3 3v23" stroke="#171b1d" stroke-width="4" stroke-linecap="round"/>'
  + '<path d="M3 3v23" stroke="#f4f3ed" stroke-width="2" stroke-linecap="round"/>'
  + '<path d="M4 3h15l-4 5 4 5H4z" fill="#54dfb3" stroke="#171b1d" stroke-width="1.5" stroke-linejoin="round"/>'
  + "</svg>";
const ROAD_ABBREVIATIONS = [
  [/\bHighway\b/g, "Hwy"],
  [/\bRoad\b/g, "Rd"],
  [/\bAvenue\b/g, "Ave"],
  [/\bStreet\b/g, "St"],
  [/\bDrive\b/g, "Dr"],
  [/\bBoulevard\b/g, "Blvd"],
  [/\bLane\b/g, "Ln"],
];

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

function milesInTenths(distanceM) {
  return Math.round(distanceM / METERS_PER_MILE * 10);
}

function formatTenths(tenths) {
  return `${(tenths / 10).toFixed(1)} mi`;
}

function formatMiles(distanceM) {
  return formatTenths(milesInTenths(distanceM));
}

function formatMinutes(seconds) {
  return `${Math.ceil(seconds / 60)} min`;
}

function formatRouteStatus(route) {
  return `Route ${formatMiles(route.totalDistanceM)} · ${formatMinutes(route.estimatedTimeSeconds)}`;
}

function approximateMeters(first, second) {
  const latitudeScale = 111_320;
  const longitudeScale = latitudeScale * Math.cos(first[1] * Math.PI / 180);
  return Math.hypot((second[0] - first[0]) * longitudeScale, (second[1] - first[1]) * latitudeScale);
}

function abbreviateRoad(name) {
  return ROAD_ABBREVIATIONS.reduce((text, [pattern, short]) => text.replace(pattern, short), name);
}

// Main roads in travel order: named stretches that carry a meaningful share of the route.
function routeRoadNames(route, limit = 6) {
  const runs = [];
  for (const edge of route.edgeIndices || []) {
    if (!edge.name) continue;
    const distanceM = edge.end_m - edge.start_m;
    const last = runs[runs.length - 1];
    if (last && last.name === edge.name) last.distanceM += distanceM;
    else runs.push({ name: edge.name, distanceM });
  }
  if (runs.length === 0) return [];
  const minimumM = Math.max(800, route.totalDistanceM * 0.05);
  let main = runs.filter((run) => run.distanceM >= minimumM);
  if (main.length === 0) {
    main = [runs.reduce((longest, run) => (run.distanceM > longest.distanceM ? run : longest))];
  }
  const names = [];
  for (const run of main) {
    const name = abbreviateRoad(run.name);
    if (!names.includes(name)) names.push(name);
  }
  return names.slice(0, limit);
}

function lowerFirst(text) {
  return text ? text[0].toLowerCase() + text.slice(1) : text;
}

function joinLegs(legRoutes) {
  const geometry = [];
  const edgeIndices = [];
  let totalDistanceM = 0;
  let estimatedTimeSeconds = 0;
  for (const leg of legRoutes) {
    const first = leg.geometry[0];
    const last = geometry[geometry.length - 1];
    const sharesJoint = Boolean(first && last && first[0] === last[0] && first[1] === last[1]);
    const offset = geometry.length - (sharesJoint ? 1 : 0);
    geometry.push(...(sharesJoint ? leg.geometry.slice(1) : leg.geometry));
    for (const edge of leg.edgeIndices || []) {
      edgeIndices.push({
        ...edge,
        start_m: edge.start_m + totalDistanceM,
        end_m: edge.end_m + totalDistanceM,
        geometry_start_index: edge.geometry_start_index + offset,
        geometry_end_index: edge.geometry_end_index + offset,
      });
    }
    totalDistanceM += leg.totalDistanceM;
    estimatedTimeSeconds += leg.estimatedTimeSeconds;
  }
  return {
    totalDistanceM,
    estimatedTimeSeconds,
    geometry,
    edgeIndices,
    startSnap: legRoutes[0].startSnap,
    endSnap: legRoutes[legRoutes.length - 1].endSnap,
  };
}

export function setupRouting(map, {
  getLatestLocation,
  requestLocation,
  refreshHazards = () => Promise.resolve(),
}) {
  const routeStatus = document.querySelector("#route-status");
  const routeHint = document.querySelector("#route-hint");
  const routeMenu = document.querySelector("#route-menu");
  const routeAction = document.querySelector("#route-to-here");
  const startHereButton = document.querySelector("#start-here");
  const addStopButton = document.querySelector("#add-stop");
  const clearRouteButton = document.querySelector("#clear-route");
  const hazardsPanel = document.querySelector("#route-hazards-panel");
  const hazardsToggle = document.querySelector("#route-hazards-toggle");
  const hazardsTitle = document.querySelector("#route-hazards-title");
  const panelBody = document.querySelector("#route-panel-body");
  const planElement = document.querySelector("#route-plan");
  const hazardsList = document.querySelector("#route-hazards-list");
  const settingsToggle = document.querySelector("#settings-toggle");
  const settingsPanel = document.querySelector("#settings-panel");
  const fareRateInput = document.querySelector("#fare-rate");
  const slotStart = document.querySelector("#slot-start");
  const slotStops = document.querySelector("#slot-stops");
  const slotDestination = document.querySelector("#slot-destination");
  const pickBanner = document.querySelector("#pick-banner");
  const pickBannerText = document.querySelector("#pick-banner-text");
  const pickCancel = document.querySelector("#pick-cancel");
  const addressSheet = document.querySelector("#address-sheet");
  const addressSheetTitle = document.querySelector("#address-sheet-title");
  const addressSheetClose = document.querySelector("#address-sheet-close");
  const addressInput = document.querySelector("#address-input");
  const addressSuggestions = document.querySelector("#address-suggestions");
  const addressStatus = document.querySelector("#address-sheet-status");
  const addressTapMap = document.querySelector("#address-tap-map");
  const addressUseLocation = document.querySelector("#address-use-location");
  const worker = new Worker(new URL("./routing-worker.js", import.meta.url), { type: "module" });
  worker.postMessage({ type: "configure", graphUrl: regionFileUrl("graph").href, bbox: region.bbox });
  const pendingRequests = new Map();
  let requestId = 0;
  let workerLoadTimeMs = null;
  let graphTransfer = null;
  let pendingPoint = null;
  let pickTarget = null;
  let sheetTarget = null;
  let searchSerial = 0;
  let searchTimer = null;
  let currentSuggestions = [];
  // Typed-address text for each slot; null when the point came from the map or the long-press menu.
  let startLabel = null;
  let destinationLabel = null;
  // The named place a slot was set from ({ name, label, city, address, phone }), or null.
  let startPlace = null;
  let destinationPlace = null;
  const placePopups = new WeakMap();
  let customStart = null;
  let stops = [];
  let nextStopId = 0;
  let destinationPoint = null;
  let legs = [];
  let planVersion = 0;
  let startMarker = null;
  let stopMarkers = [];
  let destinationMarker = null;
  const placeIndex = new Map();
  let dragState = null;
  let planRenderPending = false;
  let fareRateText = "";
  let activeRoute = null;
  let routeStartPoint = null;
  let baseRouteDistanceM = 0;
  let avoidClosures = [];
  let avoidNotice = "";
  let avoidNoticeTimer = null;
  let avoidPending = false;
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

  try {
    fareRateText = localStorage.getItem(FARE_RATE_KEY) ?? "";
  } catch {
    fareRateText = "";
  }
  fareRateInput.value = fareRateText;

  function fareRate() {
    const rate = Number.parseFloat(fareRateText);
    return Number.isFinite(rate) && rate > 0 ? rate : null;
  }

  function hasPlan() {
    return Boolean(activeRoute || customStart || destinationPoint || stops.length > 0);
  }

  function setRouteStatus(message = "") {
    routeStatus.textContent = customStart
      ? (message ? `From pin · ${message}` : "From pin")
      : message;
  }

  function routeSummaryStatus() {
    if (!activeRoute) return "";
    if (avoidClosures.length === 0) return formatRouteStatus(activeRoute);
    const extraTenths = Math.max(0,
      milesInTenths(activeRoute.totalDistanceM) - milesInTenths(baseRouteDistanceM));
    const noun = avoidClosures.length === 1 ? "closure" : "closures";
    return `Avoiding ${avoidClosures.length} ${noun} · +${formatTenths(extraTenths)}`;
  }

  function markerElement(className, text, label) {
    const element = document.createElement("div");
    element.className = `route-marker ${className}`;
    element.textContent = text;
    element.setAttribute("role", "img");
    element.setAttribute("aria-label", label);
    return element;
  }

  function updateMarkers() {
    if (customStart && !startMarker) {
      startMarker = new maplibregl.Marker({
        element: markerElement("route-marker-start", "S", "Route start pin"),
      }).setLngLat(customStart).addTo(map);
    } else if (customStart) {
      startMarker.setLngLat(customStart);
    } else if (startMarker) {
      startMarker.remove();
      startMarker = null;
    }
    if (destinationPoint && !destinationMarker) {
      const element = document.createElement("div");
      element.className = "route-marker-destination";
      element.setAttribute("role", "img");
      element.setAttribute("aria-label", "Destination");
      element.innerHTML = DESTINATION_FLAG_SVG;
      destinationMarker = new maplibregl.Marker({ element, anchor: "bottom-left", offset: [-3, 1] })
        .setLngLat(destinationPoint).addTo(map);
    } else if (destinationPoint) {
      destinationMarker.setLngLat(destinationPoint);
    } else if (destinationMarker) {
      destinationMarker.remove();
      destinationMarker = null;
    }
    for (const marker of stopMarkers) marker.remove();
    stopMarkers = stops.map((stop, index) => new maplibregl.Marker({
      element: markerElement("route-marker-stop", String(index + 1), `Stop ${index + 1}`),
    }).setLngLat(stop.point).addTo(map));
    attachPlacePopup(startMarker, customStart ? startPlace : null);
    attachPlacePopup(destinationMarker, destinationPlace);
    stopMarkers.forEach((marker, index) => attachPlacePopup(marker, stops[index].place));
    renderRouteBar();
  }

  // Tap-to-call link for a place's public phone number; the only thing on the page that can dial.
  function callLink(place, className) {
    const link = document.createElement("a");
    link.className = className;
    link.href = `tel:${place.phone.replace(/-/g, "")}`;
    link.textContent = `Call ${place.phone.replace(/^\+1-/, "")}`;
    link.setAttribute("aria-label", `Call ${place.name}, ${place.phone}`);
    return link;
  }

  function rectsOverlap(first, second) {
    return first.left < second.right && second.left < first.right
      && first.top < second.bottom && second.top < first.bottom;
  }

  function collapsePanel() {
    hazardsToggle.setAttribute("aria-expanded", "false");
    panelBody.hidden = true;
  }

  // When a place popup opens where the route panel would cover it (or its marker), fold the panel to its header,
  // and unfold it again when that popup closes unless the panel was toggled meanwhile.
  function uncoverPopup(popup, anchorElement = null) {
    if (hazardsPanel.hidden || panelBody.hidden) return;
    const panelRect = hazardsPanel.getBoundingClientRect();
    const covered = [popup.getElement(), anchorElement]
      .some((element) => element && rectsOverlap(element.getBoundingClientRect(), panelRect));
    if (!covered) return;
    collapsePanel();
    popup.once("close", () => {
      if (!hazardsPanel.hidden && panelBody.hidden && hazardsToggle.getAttribute("aria-expanded") === "false"
          && !panelToggledSince) {
        hazardsToggle.setAttribute("aria-expanded", "true");
        panelBody.hidden = false;
      }
    });
    panelToggledSince = false;
  }

  let panelToggledSince = false;

  function placePopupContent(place, { routeHere = null } = {}) {
    const article = document.createElement("article");
    article.className = "place-popup";
    const title = document.createElement("h2");
    title.textContent = place.name;
    const type = document.createElement("p");
    type.className = "place-popup-type";
    type.textContent = place.label;
    article.append(title, type);
    const where = [place.address, place.city].filter(Boolean).join(", ");
    if (where) {
      const address = document.createElement("p");
      address.textContent = where;
      article.append(address);
    }
    if (place.phone) {
      const phone = document.createElement("p");
      phone.append(callLink(place, "place-call"));
      article.append(phone);
    }
    if (routeHere) {
      const button = document.createElement("button");
      button.className = "place-route-here";
      button.type = "button";
      button.textContent = "Route here";
      button.addEventListener("click", routeHere);
      article.append(button);
    }
    return article;
  }

  let tappedPlacePopup = null;

  function openTappedPlace(place) {
    tappedPlacePopup?.remove();
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px" });
    popup.setLngLat(place.point)
      .setDOMContent(placePopupContent(place, {
        routeHere: () => {
          popup.remove();
          setPlanPoint("destination", place.point, place.name, place);
        },
      }))
      .addTo(map);
    tappedPlacePopup = popup;
    uncoverPopup(popup);
  }

  function attachPlacePopup(marker, place) {
    if (!marker) return;
    if (!place) {
      if (marker.getPopup()) marker.setPopup(null);
      return;
    }
    if (!placePopups.has(place)) {
      const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px", offset: 18 })
        .setDOMContent(placePopupContent(place));
      popup.on("open", () => uncoverPopup(popup, popup.markerElement));
      placePopups.set(place, popup);
    }
    const popup = placePopups.get(place);
    popup.markerElement = marker.getElement();
    if (marker.getPopup() !== popup) marker.setPopup(popup);
  }

  function setSlot(slot, label, value, isSet) {
    slot.querySelector(".route-slot-value").textContent = value;
    slot.setAttribute("aria-label", `${label}: ${value}`);
    slot.classList.toggle("is-set", isSet);
  }

  function renderRouteBar() {
    setSlot(slotStart, "Start", customStart ? startLabel || "Pin" : "My location", Boolean(customStart));
    setSlot(slotStops, "Stops", stops.length === 0 ? "none" : String(stops.length), stops.length > 0);
    setSlot(slotDestination, "Destination",
      destinationPoint ? destinationLabel || nearestPlaceName(destinationPoint) || "Set" : "tap to set",
      Boolean(destinationPoint));
    slotStart.setAttribute("aria-pressed", String(pickTarget === "start" || sheetTarget === "start"));
    slotStops.setAttribute("aria-pressed", String(pickTarget === "stop" || sheetTarget === "stop"));
    slotDestination.setAttribute("aria-pressed",
      String(pickTarget === "destination" || sheetTarget === "destination"));
  }

  function setPickTarget(target) {
    pickTarget = target;
    pickBanner.hidden = !target;
    pickBannerText.textContent = target ? PICK_PROMPTS[target] : "";
    map.getCanvas().style.cursor = target ? "crosshair" : "";
    if (target) {
      pendingPoint = null;
      routeMenu.hidden = true;
    }
    renderRouteBar();
  }

  function setPlanPoint(target, point, label = null, place = null) {
    if (target === "start") {
      customStart = point;
      startLabel = label;
      startPlace = place;
    } else if (target === "stop") {
      nextStopId += 1;
      stops.push({ id: nextStopId, point, label, place });
    } else if (target === "destination") {
      destinationPoint = point;
      destinationLabel = label;
      destinationPlace = place;
    } else {
      return;
    }
    dismissHint();
    planChanged();
  }

  function applyPick(point) {
    const target = pickTarget;
    setPickTarget(null);
    setPlanPoint(target, point);
  }

  function openAddressSheet(target) {
    setPickTarget(null);
    sheetTarget = target;
    addressSheetTitle.textContent = SHEET_TITLES[target];
    addressInput.value = "";
    renderSuggestions([]);
    addressStatus.textContent = "";
    addressUseLocation.hidden = !(target === "start" && customStart);
    addressSheet.hidden = false;
    renderRouteBar();
    addressInput.focus();
    // Start reading the on-device index while the person types.
    loadAddressIndex().catch(() => {});
  }

  function closeAddressSheet() {
    window.clearTimeout(searchTimer);
    searchSerial += 1;
    sheetTarget = null;
    addressSheet.hidden = true;
    currentSuggestions = [];
    renderRouteBar();
  }

  function renderSuggestions(suggestions) {
    currentSuggestions = suggestions;
    addressSuggestions.replaceChildren(...suggestions.map((suggestion) => {
      const button = document.createElement("button");
      button.className = "address-suggestion";
      button.type = "button";
      button.textContent = suggestion.label;
      button.addEventListener("click", () => chooseSuggestion(suggestion));
      button.addEventListener("keydown", (event) => {
        const buttons = [...addressSuggestions.querySelectorAll(".address-suggestion")];
        const position = buttons.indexOf(button);
        if (event.key === "ArrowDown") buttons[position + 1]?.focus();
        else if (event.key === "ArrowUp") (buttons[position - 1] || addressInput).focus();
        else return;
        event.preventDefault();
      });
      return button;
    }));
  }

  function chooseSuggestion(suggestion) {
    const target = sheetTarget;
    if (!target) return;
    const point = suggestion.point();
    // A place slot shows the place's name; its type, city, and phone stay with the slot for details.
    const place = suggestion.kind === "place" ? suggestion.place : null;
    const label = place
      ? place.name
      : suggestion.kind === "street" ? suggestion.label.replace(/ \(street\)$/, "") : suggestion.label;
    closeAddressSheet();
    setPlanPoint(target, point, label, place);
  }

  async function runAddressSearch() {
    const serial = ++searchSerial;
    const text = addressInput.value;
    if (normaliseTokens(text).length === 0) {
      renderSuggestions([]);
      addressStatus.textContent = "";
      return;
    }
    addressStatus.textContent = "Loading addresses and places…";
    try {
      const location = getLatestLocation();
      const suggestions = await searchAddresses(text, Array.isArray(location) ? location : null);
      if (serial !== searchSerial) return;
      renderSuggestions(suggestions);
      addressStatus.textContent = suggestions.length > 0 ? "" : "No matching addresses or places";
    } catch {
      if (serial !== searchSerial) return;
      renderSuggestions([]);
      addressStatus.textContent = "Addresses are unavailable. Tap the map instead.";
    }
  }

  function closureKey(feature) {
    const id = feature.id ?? feature.properties.id;
    return id === undefined || id === null
      ? `${feature.geometry.coordinates[0]},${feature.geometry.coordinates[1]}`
      : String(id);
  }

  function blockedPointsFor(closures) {
    return closures.map(({ id, point, radiusM }) => ({ id, point, radiusM }));
  }

  function showAvoidNotice() {
    avoidNotice = "No alternative route around this closure";
    renderRouteHazards();
    if (avoidNoticeTimer !== null) clearTimeout(avoidNoticeTimer);
    avoidNoticeTimer = window.setTimeout(() => {
      avoidNoticeTimer = null;
      avoidNotice = "";
      renderRouteHazards();
    }, 5000);
  }

  function syncPanelVisibility() {
    hazardsPanel.hidden = !hasPlan();
    clearRouteButton.hidden = !hasPlan();
  }

  function waypointRow({
    kind, badge, label, place = null, legIndex = null, cumulative = [], stop = null, stopIndex = null,
  }) {
    const row = document.createElement("li");
    row.className = `route-leg route-leg-${kind}`;

    if (stop) {
      row.dataset.stopId = String(stop.id);
      const handle = document.createElement("button");
      handle.className = "route-leg-handle";
      handle.type = "button";
      handle.title = "Drag to reorder";
      handle.setAttribute("aria-label", `Reorder stop ${stopIndex + 1}`);
      handle.addEventListener("pointerdown", (event) => startStopDrag(event, stopIndex));
      handle.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
        event.preventDefault();
        moveStop(stopIndex, stopIndex + (event.key === "ArrowUp" ? -1 : 1), true);
      });
      row.append(handle);
    }

    const badgeElement = document.createElement("span");
    badgeElement.className = "route-leg-badge";
    badgeElement.textContent = badge;
    badgeElement.setAttribute("aria-hidden", "true");

    const labelElement = document.createElement("span");
    labelElement.className = "route-leg-label";
    labelElement.textContent = label;
    if (place?.phone) {
      const text = document.createElement("span");
      text.className = "route-leg-text";
      text.append(labelElement, callLink(place, "route-leg-call"));
      row.append(badgeElement, text);
    } else {
      row.append(badgeElement, labelElement);
    }

    if (legIndex !== null) {
      const distances = document.createElement("span");
      distances.className = "route-leg-distances";
      if (cumulative.length > legIndex) {
        const previous = legIndex > 0 ? cumulative[legIndex - 1] : 0;
        const legDistance = document.createElement("span");
        legDistance.className = "route-leg-distance";
        legDistance.textContent = formatTenths(cumulative[legIndex] - previous);
        const running = document.createElement("span");
        running.className = "route-leg-running";
        running.textContent = `${formatTenths(cumulative[legIndex])} total`;
        distances.append(legDistance, running);
      } else {
        distances.textContent = "…";
      }
      row.append(distances);
    }

    if (stop) {
      const remove = document.createElement("button");
      remove.className = "route-leg-remove";
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove stop ${stopIndex + 1}`);
      remove.addEventListener("click", () => removeStop(stopIndex));
      row.append(remove);
    }
    return row;
  }

  function legsAreCurrent() {
    return Boolean(destinationPoint && activeRoute && legs.length === stops.length + 1);
  }

  // Running totals rounded to the tenth; each leg shows the difference so legs sum to the total.
  function cumulativeLegTenths() {
    const cumulative = [];
    let runningM = 0;
    for (const leg of legs) {
      runningM += leg.distanceM;
      cumulative.push(milesInTenths(runningM));
    }
    return cumulative;
  }

  function fareText(totalTenths) {
    const rate = fareRate();
    if (rate === null) return "";
    return `Fare at $${rate.toFixed(2)}/mi: $${(Math.round(totalTenths * rate * 10) / 100).toFixed(2)}`;
  }

  // Smaller towns only appear in higher-zoom tiles, so keep every locality label seen so far.
  function indexPlaces() {
    let features;
    try {
      features = map.querySourceFeatures("protomaps", {
        sourceLayer: "places",
        filter: ["==", ["get", "kind"], "locality"],
      });
    } catch {
      return;
    }
    for (const feature of features) {
      if (feature.geometry?.type !== "Point") continue;
      const name = feature.properties?.["name:en"] || feature.properties?.name;
      if (!name) continue;
      const [longitude, latitude] = feature.geometry.coordinates;
      placeIndex.set(`${name}|${longitude.toFixed(2)}|${latitude.toFixed(2)}`, { name, point: [longitude, latitude] });
    }
  }

  function nearestPlaceName(point) {
    indexPlaces();
    let best = null;
    for (const place of placeIndex.values()) {
      const distanceM = approximateMeters(point, place.point);
      if (distanceM <= PLACE_SEARCH_RADIUS_M && (!best || distanceM < best.distanceM)) {
        best = { name: place.name, distanceM };
      }
    }
    return best?.name ?? null;
  }

  function buildShareText() {
    if (!legsAreCurrent() || !routeStartPoint) return "";
    const points = currentPoints(routeStartPoint);
    const fallbacks = ["Start", ...stops.map((stop, index) => `Stop ${index + 1}`), "Destination"];
    const typedNames = [customStart ? startLabel : null, ...stops.map((stop) => stop.label), destinationLabel];
    const totalTenths = cumulativeLegTenths().at(-1);
    const lines = [
      points.map((point, index) => typedNames[index] || nearestPlaceName(point) || fallbacks[index]).join(" → "),
      `${formatTenths(totalTenths)} · about ${formatMinutes(activeRoute.estimatedTimeSeconds)}`,
    ];
    const roads = routeRoadNames(activeRoute);
    if (roads.length > 0) lines.push(roads.join(" → "));

    const hazards = routeHazardRows.filter((row) => !row.offRoute);
    if (hazards.length === 0) {
      lines.push("No reported hazards along your route");
    } else {
      lines.push(`${hazards.length} reported ${hazards.length === 1 ? "hazard" : "hazards"} along your route:`);
      const now = Date.now();
      for (const row of hazards) {
        const properties = row.feature.properties;
        const title = properties.title || "Hazard";
        const age = Number.isFinite(Date.parse(properties.created_at))
          ? ` (reported ${lowerFirst(formatHazardAge(properties.created_at, now))})`
          : "";
        lines.push(`  ${lowerFirst(title)}, ${formatMiles(row.distanceAheadM)} ahead${age}`);
      }
    }

    const fare = fareText(totalTenths);
    if (fare) lines.push(fare);
    return lines.join("\n");
  }

  async function shareRoute(button) {
    const text = buildShareText();
    if (!text) return;
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
    button.textContent = label;
    window.setTimeout(() => {
      if (button.isConnected) button.textContent = "Share";
    }, 2000);
  }

  function renderPlan() {
    if (dragState) {
      planRenderPending = true;
      return;
    }
    planRenderPending = false;
    syncPanelVisibility();
    planElement.replaceChildren();
    if (!hasPlan()) return;

    const legsCurrent = legsAreCurrent();
    const cumulative = legsCurrent ? cumulativeLegTenths() : [];

    const list = document.createElement("ol");
    list.className = "route-legs";
    list.setAttribute("aria-label", "Route legs");
    list.append(waypointRow({
      kind: "start",
      badge: "S",
      label: customStart ? `Start · ${startLabel || "pin"}` : "Start · your location",
      place: customStart ? startPlace : null,
    }));
    stops.forEach((stop, index) => {
      list.append(waypointRow({
        kind: "stop",
        badge: String(index + 1),
        label: stop.label ? `Stop ${index + 1} · ${stop.label}` : `Stop ${index + 1}`,
        place: stop.place,
        legIndex: destinationPoint ? index : null,
        cumulative,
        stop,
        stopIndex: index,
      }));
    });
    if (destinationPoint) {
      list.append(waypointRow({
        kind: "destination",
        badge: "D",
        label: destinationLabel ? `Destination · ${destinationLabel}` : "Destination",
        place: destinationPlace,
        legIndex: stops.length,
        cumulative,
      }));
    }
    planElement.append(list);

    if (legsCurrent) {
      const totalTenths = cumulative[cumulative.length - 1];
      const total = document.createElement("p");
      total.className = "route-total";
      const totalText = document.createElement("span");
      totalText.className = "route-total-distance";
      totalText.textContent = `Total ${formatTenths(totalTenths)} · ${formatMinutes(activeRoute.estimatedTimeSeconds)}`;
      total.append(totalText);
      const fare = fareText(totalTenths);
      if (fare) {
        const fareElement = document.createElement("span");
        fareElement.className = "route-fare";
        fareElement.textContent = fare;
        total.append(fareElement);
      }
      planElement.append(total);
    } else if (!destinationPoint) {
      const hint = document.createElement("p");
      hint.className = "route-plan-hint";
      hint.textContent = "Tap and hold the map, then choose Route to here.";
      planElement.append(hint);
    }

    const actions = document.createElement("div");
    actions.className = "route-plan-actions";
    if (legsCurrent) {
      const share = document.createElement("button");
      share.className = "route-plan-button route-share-button";
      share.type = "button";
      share.textContent = "Share";
      share.addEventListener("click", () => shareRoute(share));
      actions.append(share);
    }
    if (customStart) {
      const useLocation = document.createElement("button");
      useLocation.className = "route-plan-button";
      useLocation.type = "button";
      useLocation.textContent = "Use my location";
      useLocation.addEventListener("click", useMyLocation);
      actions.append(useLocation);
    }
    if (actions.childElementCount > 0) planElement.append(actions);
  }

  function renderRouteHazards() {
    syncPanelVisibility();
    hazardsList.replaceChildren();
    if (!activeRoute) {
      routeHazardRows = [];
      hazardsTitle.textContent = "Route plan";
      return;
    }

    const onRouteCount = routeHazardRows.filter((row) => !row.offRoute).length;
    hazardsTitle.textContent = `${onRouteCount} reported ${onRouteCount === 1 ? "hazard" : "hazards"} along your route`;
    if (avoidNotice) {
      const notice = document.createElement("p");
      notice.className = "route-avoid-notice";
      notice.setAttribute("role", "status");
      notice.textContent = avoidNotice;
      hazardsList.append(notice);
    }
    if (routeHazardRows.length === 0) {
      const emptyMessage = document.createElement("p");
      emptyMessage.className = "route-hazards-empty";
      emptyMessage.textContent = "No reported hazards along your route";
      hazardsList.append(emptyMessage);
      return;
    }

    const now = Date.now();
    for (const row of routeHazardRows) {
      const properties = row.feature.properties;
      const key = closureKey(row.feature);
      const isAvoided = properties.category === "CLOSURES"
        && avoidClosures.some((closure) => closure.id === key);
      const entry = document.createElement("div");
      entry.className = "route-hazard-entry";
      entry.setAttribute("role", "listitem");
      const button = document.createElement("button");
      button.className = "route-hazard-row";
      button.type = "button";

      const dot = document.createElement("span");
      dot.className = "route-hazard-dot";
      dot.style.backgroundColor = properties.category_color || "#1976d2";
      dot.setAttribute("aria-hidden", "true");

      const title = document.createElement("span");
      title.className = "route-hazard-title";
      title.textContent = properties.title || "Hazard";

      const distance = document.createElement("span");
      distance.className = "route-hazard-distance";
      distance.textContent = formatMiles(row.distanceAheadM);

      const age = document.createElement("span");
      age.className = "route-hazard-age";
      age.textContent = formatHazardAge(properties.created_at, now);

      button.append(dot, title, distance, age);
      button.addEventListener("click", () => {
        const coordinate = row.feature.geometry.coordinates;
        map.flyTo({ center: coordinate, zoom: Math.max(map.getZoom(), 14) });
        openHazardPopup(map, properties, coordinate);
      });
      entry.append(button);
      if (properties.category === "CLOSURES") {
        const avoidButton = document.createElement("button");
        avoidButton.className = "route-avoid-button";
        avoidButton.type = "button";
        avoidButton.textContent = isAvoided ? "Stop avoiding" : "Route around";
        avoidButton.disabled = avoidPending;
        avoidButton.addEventListener("click", () => toggleClosureAvoidance(row));
        entry.append(avoidButton);
      }
      hazardsList.append(entry);
    }
  }

  function recomputeRouteHazards() {
    // A custom start means the phone is not on the route, so nothing is "behind" it.
    const rows = activeRoute
      ? hazardsAlongRoute(hazardFeatures, activeRoute, customStart ? null : liveFix)
      : [];
    const rowByKey = new Map(rows.map((row) => [closureKey(row.feature), row]));
    for (const closure of avoidClosures) {
      const currentRow = rowByKey.get(closure.id);
      if (currentRow) {
        currentRow.isAvoided = true;
      } else if (closure.row) {
        rows.push({ ...closure.row, isAvoided: true, offRoute: true });
      }
    }
    rows.sort((first, second) => first.distanceAheadM - second.distanceAheadM);
    routeHazardRows = rows;
    renderRouteHazards();
  }

  function setHazards(features) {
    hazardFeatures = features;
    recomputeRouteHazards();
  }

  function requestWorkerRoute(start, end, blockedPoints = []) {
    if (!validPoint(start) || !validPoint(end)) {
      return Promise.reject(new Error("Route coordinates are invalid."));
    }
    const id = ++requestId;
    return new Promise((resolve, reject) => {
      pendingRequests.set(id, { resolve, reject });
      worker.postMessage({ type: "route", requestId: id, start, destination: end, blockedPoints });
    });
  }

  async function routeChain(points, blockedPoints = []) {
    const results = await Promise.all(points.slice(1).map(
      (point, index) => requestWorkerRoute(points[index], point, blockedPoints),
    ));
    const affected = new Set();
    for (const result of results) {
      for (const id of result.blockedPointsAffected || []) affected.add(String(id));
    }
    const failed = results.find((result) => !result.route);
    if (failed) {
      return { route: null, reason: failed.reason, blockedPointsAffected: [...affected] };
    }
    return {
      route: joinLegs(results.map((result) => result.route)),
      legs: results.map((result) => ({
        distanceM: result.route.totalDistanceM,
        timeSeconds: result.route.estimatedTimeSeconds,
      })),
      blockedPointsAffected: [...affected],
    };
  }

  function currentPoints(start) {
    return [start, ...stops.map((stop) => stop.point), destinationPoint];
  }

  async function toggleClosureAvoidance(row) {
    if (avoidPending || !routeStartPoint || !destinationPoint || !activeRoute) return;
    const key = closureKey(row.feature);
    const alreadyAvoided = avoidClosures.some((closure) => closure.id === key);
    const candidateClosures = alreadyAvoided
      ? avoidClosures.filter((closure) => closure.id !== key)
      : [...avoidClosures, {
        id: key,
        point: row.feature.geometry.coordinates,
        radiusM: 40,
        feature: row.feature,
        row,
      }];
    clearAvoidNotice();
    avoidPending = true;
    const version = ++planVersion;
    renderRouteHazards();

    try {
      const result = await routeChain(currentPoints(routeStartPoint), blockedPointsFor(candidateClosures));
      if (version !== planVersion) return;
      if (!result.route) {
        if (!alreadyAvoided) showAvoidNotice();
        return;
      }

      const affected = new Set(result.blockedPointsAffected);
      if (!alreadyAvoided && !affected.has(key)) {
        showAvoidNotice();
        return;
      }

      avoidClosures = candidateClosures.filter((closure) => affected.has(closure.id));
      legs = result.legs;
      installRoute(result.route);
    } catch {
      if (!alreadyAvoided && version === planVersion) showAvoidNotice();
    } finally {
      avoidPending = false;
      renderRouteHazards();
    }
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

  function clearAvoidNotice() {
    if (avoidNoticeTimer !== null) clearTimeout(avoidNoticeTimer);
    avoidNoticeTimer = null;
    avoidNotice = "";
  }

  function installRoute(route) {
    activeRoute = route;
    lastRouteFeature = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: route.geometry },
      properties: {},
    };
    map.getSource(SOURCE_ID).setData(lastRouteFeature);
    routeMenu.hidden = true;
    lastFixRecomputeAt = performance.now();
    recomputeRouteHazards();
    renderPlan();
    setRouteStatus(routeSummaryStatus());
    map.fitBounds(routeBounds(route.geometry), {
      padding: { top: 112, right: 52, bottom: 132, left: 52 },
      maxZoom: 12.5,
      duration: 450,
    });
  }

  function clearRouteLine() {
    map.getSource(SOURCE_ID).setData({ type: "FeatureCollection", features: [] });
    lastRouteFeature = null;
    activeRoute = null;
    legs = [];
  }

  function showRouteFailure(message) {
    clearRouteLine();
    routeStartPoint = null;
    recomputeRouteHazards();
    renderPlan();
    setRouteStatus(message);
  }

  function clearRoute(message = "") {
    planVersion += 1;
    startLabel = null;
    destinationLabel = null;
    startPlace = null;
    destinationPlace = null;
    closeAddressSheet();
    clearRouteLine();
    pendingPoint = null;
    customStart = null;
    stops = [];
    destinationPoint = null;
    routeStartPoint = null;
    baseRouteDistanceM = 0;
    avoidClosures = [];
    clearAvoidNotice();
    setPickTarget(null);
    updateMarkers();
    routeMenu.hidden = true;
    recomputeRouteHazards();
    renderPlan();
    setRouteStatus(message);
  }

  async function updateRoute() {
    const version = ++planVersion;
    legs = [];
    renderPlan();
    if (!destinationPoint) {
      renderRouteHazards();
      setRouteStatus(hasPlan() ? "Choose a destination" : "");
      return null;
    }

    setRouteStatus("Calculating route…");
    let start;
    try {
      start = customStart || getLatestLocation() || await requestLocation();
    } catch (error) {
      if (version === planVersion) {
        showRouteFailure(error?.code === 1
          ? "Location permission was denied."
          : error?.message || "Your location could not be determined.");
      }
      return null;
    }
    if (version !== planVersion) return null;

    const points = currentPoints(start);
    try {
      const base = await routeChain(points);
      if (version !== planVersion) return null;
      if (!base.route) {
        showRouteFailure(base.reason || "No route exists.");
        return base;
      }

      let chosen = base;
      if (avoidClosures.length > 0) {
        const avoided = await routeChain(points, blockedPointsFor(avoidClosures));
        if (version !== planVersion) return null;
        if (avoided.route) {
          const affected = new Set(avoided.blockedPointsAffected);
          avoidClosures = avoidClosures.filter((closure) => affected.has(closure.id));
          chosen = avoided;
        } else {
          avoidClosures = [];
          showAvoidNotice();
        }
      }

      routeStartPoint = start;
      baseRouteDistanceM = base.route.totalDistanceM;
      legs = chosen.legs;
      if (!customStart) {
        const currentLocation = getLatestLocation();
        if (validPoint(currentLocation)) liveFix = currentLocation;
      }
      installRoute(chosen.route);
      dismissHint();
      return chosen;
    } catch (error) {
      if (version === planVersion) showRouteFailure(error.message || "Routing is unavailable.");
      return null;
    }
  }

  function planChanged() {
    updateMarkers();
    return updateRoute();
  }

  function moveStop(from, to, focusHandle = false) {
    if (from === to || to < 0 || to >= stops.length) return;
    const [stop] = stops.splice(from, 1);
    stops.splice(to, 0, stop);
    planChanged();
    if (focusHandle) {
      planElement.querySelector(`[data-stop-id="${stop.id}"] .route-leg-handle`)?.focus();
    }
  }

  function removeStop(index) {
    stops.splice(index, 1);
    planChanged();
  }

  function useMyLocation() {
    customStart = null;
    startLabel = null;
    startPlace = null;
    const currentLocation = getLatestLocation();
    if (validPoint(currentLocation)) liveFix = currentLocation;
    planChanged();
  }

  function startStopDrag(event, index) {
    if (event.button !== 0 || dragState) return;
    event.preventDefault();
    const handle = event.currentTarget;
    const rows = [...planElement.querySelectorAll(".route-leg-stop")];
    dragState = {
      pointerId: event.pointerId,
      index,
      targetIndex: index,
      startY: event.clientY,
      row: rows[index],
      middles: rows.map((row) => {
        const rect = row.getBoundingClientRect();
        return rect.top + rect.height / 2;
      }),
    };
    handle.setPointerCapture(event.pointerId);
    dragState.row.classList.add("is-dragging");
    handle.addEventListener("pointermove", moveStopDrag);
    handle.addEventListener("pointerup", finishStopDrag);
    handle.addEventListener("pointercancel", finishStopDrag);
  }

  function moveStopDrag(event) {
    if (!dragState || event.pointerId !== dragState.pointerId) return;
    const deltaY = event.clientY - dragState.startY;
    dragState.row.style.transform = `translateY(${deltaY}px)`;
    const center = dragState.middles[dragState.index] + deltaY;
    dragState.targetIndex = dragState.middles
      .filter((middle, index) => index !== dragState.index && middle < center).length;
  }

  function finishStopDrag(event) {
    if (!dragState || event.pointerId !== dragState.pointerId) return;
    const handle = event.currentTarget;
    handle.removeEventListener("pointermove", moveStopDrag);
    handle.removeEventListener("pointerup", finishStopDrag);
    handle.removeEventListener("pointercancel", finishStopDrag);
    const { index, row } = dragState;
    const targetIndex = event.type === "pointercancel" ? index : dragState.targetIndex;
    row.classList.remove("is-dragging");
    row.style.transform = "";
    dragState = null;
    if (targetIndex !== index) moveStop(index, targetIndex);
    else if (planRenderPending) renderPlan();
  }

  function selectPoint(point) {
    pendingPoint = point;
    routeMenu.hidden = false;
    setRouteStatus("Point selected · choose an option");
    dismissHint();
  }

  function takePendingPoint() {
    const point = pendingPoint;
    pendingPoint = null;
    routeMenu.hidden = true;
    return point;
  }

  routeAction.addEventListener("click", () => {
    const point = takePendingPoint();
    if (!point) return;
    setPlanPoint("destination", point);
  });

  startHereButton.addEventListener("click", () => {
    const point = takePendingPoint();
    if (!point) return;
    setPlanPoint("start", point);
  });

  addStopButton.addEventListener("click", () => {
    const point = takePendingPoint();
    if (!point) return;
    setPlanPoint("stop", point);
  });

  clearRouteButton.addEventListener("click", () => clearRoute());

  function toggleSlot(target) {
    if (sheetTarget === target || pickTarget === target) {
      closeAddressSheet();
      setPickTarget(null);
      return;
    }
    openAddressSheet(target);
  }

  slotStart.addEventListener("click", () => toggleSlot("start"));
  slotStops.addEventListener("click", () => toggleSlot("stop"));
  slotDestination.addEventListener("click", () => toggleSlot("destination"));
  pickCancel.addEventListener("click", () => setPickTarget(null));
  addressSheetClose.addEventListener("click", closeAddressSheet);
  addressTapMap.addEventListener("click", () => {
    const target = sheetTarget;
    closeAddressSheet();
    setPickTarget(target);
  });
  addressUseLocation.addEventListener("click", () => {
    closeAddressSheet();
    useMyLocation();
  });
  addressInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(runAddressSearch, 80);
  });
  addressInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && currentSuggestions.length > 0) {
      event.preventDefault();
      chooseSuggestion(currentSuggestions[0]);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      addressSuggestions.querySelector(".address-suggestion")?.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (sheetTarget) closeAddressSheet();
    else if (pickTarget) setPickTarget(null);
  });
  map.on("click", async (event) => {
    if (pickTarget) {
      applyPick(event.lngLat.toArray());
      return;
    }
    // Hazard circles have their own popup; any other tap near an indexed place opens the place.
    if (map.getLayer("hazard-circles")
        && map.queryRenderedFeatures(event.point, { layers: ["hazard-circles"] }).length > 0) return;
    try {
      const place = await nearestNamedPlace(event.lngLat.toArray(), PLACE_TAP_RADIUS_M);
      if (place) openTappedPlace(place);
    } catch {
      // The places index is unavailable; a tap does nothing, as before.
    }
  });

  hazardsToggle.addEventListener("click", () => {
    panelToggledSince = true;
    const expanded = hazardsToggle.getAttribute("aria-expanded") !== "false";
    hazardsToggle.setAttribute("aria-expanded", String(!expanded));
    panelBody.hidden = expanded;
  });

  settingsToggle.addEventListener("click", () => {
    const open = settingsPanel.hidden;
    settingsPanel.hidden = !open;
    settingsToggle.setAttribute("aria-expanded", String(open));
    if (open) fareRateInput.focus();
  });

  fareRateInput.addEventListener("input", () => {
    fareRateText = fareRateInput.value.trim();
    try {
      if (fareRateText) localStorage.setItem(FARE_RATE_KEY, fareRateText);
      else localStorage.removeItem(FARE_RATE_KEY);
    } catch {
      // The rate still applies for this page if storage is unavailable.
    }
    renderPlan();
  });

  map.on("idle", indexPlaces);

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
      selectPoint(holdPointer.point);
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
      // Routes from an explicit start, which behaves as a custom start pin.
      route: (startLatLon, destinationLatLon) => {
        customStart = [startLatLon[1], startLatLon[0]];
        startLabel = null;
        destinationLabel = null;
        startPlace = null;
        destinationPlace = null;
        stops = [];
        destinationPoint = [destinationLatLon[1], destinationLatLon[0]];
        return planChanged();
      },
      // Equivalent to a long press at this point; the menu buttons then act on it.
      selectPoint: (latitudeLongitude) => selectPoint([latitudeLongitude[1], latitudeLongitude[0]]),
      routeWithBlockedPoints: (startLatLon, destinationLatLon, blockedPoints = []) => requestWorkerRoute(
        [startLatLon[1], startLatLon[0]],
        [destinationLatLon[1], destinationLatLon[0]],
        blockedPoints,
      ),
      routeLegs: () => legs.map((leg) => ({ ...leg })),
      mapCenter: () => map.getCenter().toArray(),
      mapBearing: () => map.getBearing(),
      project: (latitudeLongitude) => {
        const point = map.project([latitudeLongitude[1], latitudeLongitude[0]]);
        const rect = map.getCanvas().getBoundingClientRect();
        return [rect.left + point.x, rect.top + point.y];
      },
      shareText: () => buildShareText(),
      routeRoads: () => (activeRoute ? routeRoadNames(activeRoute, Infinity) : []),
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

  renderRouteBar();

  return { setHazards, onLocationFix };
}
