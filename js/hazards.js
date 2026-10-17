import * as maplibregl from "../vendor/maplibre-gl.js";
import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./config.js";

const SOURCE_ID = "live-hazards";
const CIRCLE_LAYER_ID = "hazard-circles";
const LABEL_LAYER_ID = "hazard-labels";
const REFRESH_INTERVAL_MS = 60_000;
const STALE_AFTER_MS = 5 * 60_000;
const EMPTY_COLLECTION = { type: "FeatureCollection", features: [] };

const categoryByType = {
  MOOSE: "ANIMALS",
  BEAR: "ANIMALS",
  CARIBOU: "ANIMALS",
  LIVESTOCK: "ANIMALS",
  ANIMAL_OTHER: "ANIMALS",
  DOMESTIC_ANIMAL: "ANIMALS",
  ANIMAL_DEBRIS: "ANIMALS",
  DAMAGE_MY_LANE: "ROAD_DAMAGE",
  DAMAGE_ONCOMING: "ROAD_DAMAGE",
  DAMAGE_BOTH_LANES: "ROAD_DAMAGE",
  FOG: "VISIBILITY",
  SNOW_WHITEOUT: "VISIBILITY",
  SMOKE: "VISIBILITY",
  DUST: "VISIBILITY",
  HEAVY_RAIN: "VISIBILITY",
  HYDROPLANE: "OBSTRUCTIONS",
  SNOW_DRIFTS: "OBSTRUCTIONS",
  BLACK_ICE: "OBSTRUCTIONS",
  ACCIDENT: "OBSTRUCTIONS",
  STALLED_VEHICLE: "OBSTRUCTIONS",
  CONSTRUCTION: "OBSTRUCTIONS",
  CARCASS: "OBSTRUCTIONS",
  DEBRIS: "OBSTRUCTIONS",
  ACCIDENT_BLOCKING: "CLOSURES",
  EMERGENCY_RESPONSE: "CLOSURES",
  DOWNED_POWER_LINE: "CLOSURES",
  STRUCTURAL_DAMAGE: "CLOSURES",
  OTHER_HAZARD: "OBSTRUCTIONS",
  DELAY: "DELAY",
  PEDESTRIAN_IN_ROAD: "PEDESTRIAN",
};

const categoryLabels = {
  ANIMALS: "Animals",
  PEDESTRIAN: "Pedestrian",
  ROAD_DAMAGE: "Road Damage",
  VISIBILITY: "Visibility",
  OBSTRUCTIONS: "Obstructions",
  CLOSURES: "Closures",
  DELAY: "Delay",
};

const categoryColors = {
  ANIMALS: "#FF9800",
  PEDESTRIAN: "#C6FF00",
  ROAD_DAMAGE: "#FDD835",
  VISIBILITY: "#E0E0E0",
  OBSTRUCTIONS: "#1976D2",
  CLOSURES: "#D32F2F",
  DELAY: "#8E24AA",
};

function categoryForType(value) {
  const type = String(value || "OTHER_HAZARD").toUpperCase();
  if (categoryByType[type]) return categoryByType[type];

  if (type.includes("CARCASS") || type.includes("ROADKILL")) return "OBSTRUCTIONS";
  if (/LIVESTOCK|HORSE|COW|CATTLE|SHEEP|GOAT/.test(type)) return "ANIMALS";
  if (/DOG|POULTRY|CHICKEN|DUCK|GOOSE|GEESE/.test(type)) return "ANIMALS";
  if (/STALLED|DISABLED|BREAKDOWN/.test(type)) return "OBSTRUCTIONS";
  if (/DEBRIS|TREE|TRAILER|TRACTOR|EQUIPMENT|SPILL/.test(type)) return "OBSTRUCTIONS";
  if (/MOOSE|CARIBOU|BEAR|ANIMAL|WILDLIFE|DEER|ELK|BISON|COYOTE|WOLF|LYNX|FOX|EAGLE|PORCUPINE|OTTER|CAT/.test(type)) return "ANIMALS";
  if (/PEDESTRIAN|CHILD/.test(type)) return "PEDESTRIAN";
  if (/FOG|SNOW|VISIBILITY|RAIN/.test(type)) return "VISIBILITY";
  if (/POTHOLE|DAMAGE|HEAVE|CRACK/.test(type)) return "ROAD_DAMAGE";
  if (/ACCIDENT|CRASH|BLOCK/.test(type)) return "CLOSURES";
  return "OBSTRUCTIONS";
}

function displayValue(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  return "";
}

function isFiniteCoordinate(value, maximum) {
  return value !== null && value !== undefined && Number.isFinite(Number(value)) && Math.abs(Number(value)) <= maximum;
}

function isLive(row, now) {
  if (!row.expires_at) return true;
  const expiresAt = Date.parse(row.expires_at);
  return Number.isFinite(expiresAt) && expiresAt > now;
}

function featureForRow(row) {
  const hasAlertPoint = row.alert_latitude !== null && row.alert_latitude !== undefined
    && row.alert_longitude !== null && row.alert_longitude !== undefined;
  const latitude = hasAlertPoint ? row.alert_latitude : row.latitude;
  const longitude = hasAlertPoint ? row.alert_longitude : row.longitude;
  if (!isFiniteCoordinate(latitude, 90) || !isFiniteCoordinate(longitude, 180)) return null;

  const hazardType = displayValue(row.hazard_type) || "OTHER_HAZARD";
  const category = categoryForType(hazardType);
  const title = displayValue(row.display_text) || displayValue(row.specific) || hazardType;

  return {
    type: "Feature",
    id: row.id,
    geometry: {
      type: "Point",
      coordinates: [Number(longitude), Number(latitude)],
    },
    properties: {
      id: row.id,
      label: title,
      title,
      category,
      category_label: categoryLabels[category],
      category_color: categoryColors[category],
      hazard_type: hazardType,
      created_at: displayValue(row.created_at),
      confirmation_count: Number(row.confirmation_count) || 0,
      delay_transit_s: Number(row.delay_transit_s) || 0,
    },
  };
}

export function formatHazardAge(timestamp, now) {
  const createdAt = Date.parse(timestamp);
  if (!Number.isFinite(createdAt)) return "Reported time unavailable";

  const totalMinutes = Math.max(0, Math.floor((now - createdAt) / 60_000));
  if (totalMinutes < 1) return "Just now";
  if (totalMinutes < 60) return `${totalMinutes} min ago`;

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) return minutes ? `${hours} h ${minutes} min ago` : `${hours} h ago`;

  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours ? `${days} d ${remainingHours} h ago` : `${days} d ago`;
}

function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function popupContent(properties, now) {
  const lines = [
    `<p>${escapeHtml(formatHazardAge(properties.created_at, now))}</p>`,
  ];
  if (properties.confirmation_count > 0) {
    lines.push(`<p>${properties.confirmation_count} confirmations</p>`);
  }
  if (properties.category === "DELAY" && properties.delay_transit_s > 0) {
    const minutes = Math.ceil(properties.delay_transit_s / 60);
    lines.push(`<p>At least ${minutes} min</p>`);
  }

  return `<article class="hazard-popup"><h2>${escapeHtml(properties.title)}</h2><p class="hazard-popup-category">${escapeHtml(properties.category_label)}</p>${lines.join("")}</article>`;
}

export function openHazardPopup(map, properties, coordinate) {
  return new maplibregl.Popup({ closeButton: true, maxWidth: "300px" })
    .setLngLat(coordinate)
    .setHTML(popupContent(properties, Date.now()))
    .addTo(map);
}

export function startHazardLayer(map, { onHazardsUpdate = () => {} } = {}) {
  const status = document.querySelector("#hazard-summary");
  const statusStrip = document.querySelector("#hazard-status");
  map.addSource(SOURCE_ID, {
    type: "geojson",
    data: EMPTY_COLLECTION,
  });
  let lastGoodRows = [];
  let lastSuccessfulAt = null;
  let renderedCount = 0;
  let refreshFailed = false;

  map.addLayer({
    id: CIRCLE_LAYER_ID,
    type: "circle",
    source: SOURCE_ID,
    paint: {
      "circle-radius": ["match", ["get", "category"], ["ANIMALS", "PEDESTRIAN"], 9, 6],
      "circle-color": ["match", ["get", "category"],
        "ANIMALS", categoryColors.ANIMALS,
        "PEDESTRIAN", categoryColors.PEDESTRIAN,
        "ROAD_DAMAGE", categoryColors.ROAD_DAMAGE,
        "VISIBILITY", categoryColors.VISIBILITY,
        "OBSTRUCTIONS", categoryColors.OBSTRUCTIONS,
        "CLOSURES", categoryColors.CLOSURES,
        "DELAY", categoryColors.DELAY,
        categoryColors.OBSTRUCTIONS,
      ],
      "circle-stroke-color": "#171b1d",
      "circle-stroke-width": ["match", ["get", "category"], ["ANIMALS", "PEDESTRIAN"], 2.5, 1.5],
      "circle-opacity": 0.96,
    },
  });

  map.addLayer({
    id: LABEL_LAYER_ID,
    type: "symbol",
    source: SOURCE_ID,
    minzoom: 10,
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Medium"],
      "text-size": 11,
      "text-anchor": "top",
      "text-offset": [0, 1.1],
      "text-max-width": 12,
      "text-optional": true,
      "text-allow-overlap": false,
    },
    paint: {
      "text-color": "#f4f3ed",
      "text-halo-color": "#171b1d",
      "text-halo-width": 1.5,
    },
  });

  function buildFeatures(now) {
    return lastGoodRows
      .filter((row) => isLive(row, now))
      .map(featureForRow)
      .filter(Boolean);
  }

  function updateStatus(now) {
    if (lastSuccessfulAt === null) {
      status.textContent = refreshFailed ? "Hazards unavailable · stale" : "Loading hazards";
      statusStrip.classList.toggle("is-stale", refreshFailed);
      return;
    }

    const age = now - lastSuccessfulAt;
    status.textContent = `${renderedCount} ${renderedCount === 1 ? "hazard" : "hazards"} · updated ${formatElapsed(age)}`;
    statusStrip.classList.toggle("is-stale", age > STALE_AFTER_MS);
  }

  function updateLayer(now, force = false) {
    const features = buildFeatures(now);
    if (force || features.length !== renderedCount) {
      map.getSource(SOURCE_ID).setData({ type: "FeatureCollection", features });
      renderedCount = features.length;
      onHazardsUpdate(features);
    }
    updateStatus(now);
  }

  map.on("click", CIRCLE_LAYER_ID, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;

    openHazardPopup(map, feature.properties, event.lngLat);
  });
  map.on("mouseenter", CIRCLE_LAYER_ID, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", CIRCLE_LAYER_ID, () => { map.getCanvas().style.cursor = ""; });

  async function refresh() {
    try {
      const response = await fetch(`${SUPABASE_URL}/rest/v1/public_hazards?select=*`, {
        method: "GET",
        headers: {
          apikey: SUPABASE_ANON_KEY,
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`Hazard request failed (${response.status})`);

      const rows = await response.json();
      if (!Array.isArray(rows)) throw new Error("Hazard response was not a row array");

      lastGoodRows = rows;
      lastSuccessfulAt = Date.now();
      refreshFailed = false;
      updateLayer(lastSuccessfulAt, true);
    } catch {
      refreshFailed = true;
      updateStatus(Date.now());
    }
  }

  updateStatus(Date.now());
  refresh();
  window.setInterval(refresh, REFRESH_INTERVAL_MS);
  window.setInterval(() => updateLayer(Date.now()), 1000);
  return { refresh };
}