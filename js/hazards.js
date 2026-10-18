import * as maplibregl from "../vendor/maplibre-gl.js";
import { openHazardCard } from "./cards.js";
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

// Plain names per type, from the Android app's AlertWording.descriptor, except road damage, which the
// map calls "Rough road". Never show a raw hazard_type code.
const typeLabels = {
  MOOSE: "Moose",
  CARIBOU: "Caribou",
  BEAR: "Bear",
  ANIMAL_DEBRIS: "Animal debris",
  ANIMAL_OTHER: "Animal",
  LIVESTOCK: "Livestock",
  DOMESTIC_ANIMAL: "Loose animal",
  DEBRIS: "Debris",
  STALLED_VEHICLE: "Stalled vehicle",
  DAMAGE_MY_LANE: "Rough road",
  DAMAGE_ONCOMING: "Rough road",
  DAMAGE_BOTH_LANES: "Rough road",
  FOG: "Fog",
  SNOW_WHITEOUT: "Whiteout",
  SMOKE: "Smoke",
  DUST: "Blowing dust",
  HEAVY_RAIN: "Heavy rain",
  HYDROPLANE: "Standing water",
  SNOW_DRIFTS: "Snow drifts",
  BLACK_ICE: "Black ice",
  ACCIDENT: "Accident",
  CONSTRUCTION: "Construction",
  CARCASS: "Dead animal",
  ACCIDENT_BLOCKING: "Accident blocking the road",
  EMERGENCY_RESPONSE: "Emergency scene",
  DOWNED_POWER_LINE: "Downed power line",
  STRUCTURAL_DAMAGE: "Structural damage",
  OTHER_HAZARD: "Hazard",
  DELAY: "Possible delay",
  PEDESTRIAN_IN_ROAD: "Person in the road",
};

// Spoken nouns: AlertWording.descriptor exactly (road damage is "Road damage" there; the map's "Rough road" is a
// display choice). Spoken alerts never name a lane or a direction.
const spokenLabels = { ...typeLabels, DAMAGE_MY_LANE: "Road damage", DAMAGE_ONCOMING: "Road damage",
  DAMAGE_BOTH_LANES: "Road damage" };
// SpokenAgePolicy: the exact codes whose spoken alert ends with "last seen N minutes ago". Any other code, including
// an unknown one, says no age (pedestrians and downed lines are vetoed; road damage, ice and the like persist).
const SPEAKS_AGE = new Set(["MOOSE", "CARIBOU", "BEAR", "ANIMAL_OTHER", "LIVESTOCK", "DOMESTIC_ANIMAL", "DEBRIS",
  "STALLED_VEHICLE", "ACCIDENT", "CARCASS", "ANIMAL_DEBRIS", "ACCIDENT_BLOCKING", "EMERGENCY_RESPONSE", "FOG",
  "SNOW_WHITEOUT", "SMOKE", "DUST", "HEAVY_RAIN"]);

// Exact codes only, as the Android LaneScope table: a guessed type is never lane-scoped. The stored
// heading is already the damaged lane's direction (flipped 180 at report time for DAMAGE_ONCOMING).
const LANE_SCOPED_DAMAGE = new Set(["DAMAGE_MY_LANE", "DAMAGE_ONCOMING"]);

// Report text that names a direction or lane can't be shown as the hazard's name (AlertWording.CLAIM_WORDS).
const CLAIM_WORDS = /\b(lane|lanes|my|your|oncoming|ahead|behind|left|right|northbound|southbound|eastbound|westbound)\b/i;

// Mirrors HazardType.fromCode: exact code first, then the same keyword order for free-form codes.
function typeForCode(value) {
  const code = String(value || "OTHER_HAZARD").toUpperCase();
  if (categoryByType[code]) return code;

  if (code.includes("CARCASS") || code.includes("ROADKILL")) return "CARCASS";
  if (/LIVESTOCK|HORSE|COW|CATTLE|SHEEP|GOAT/.test(code)) return "LIVESTOCK";
  if (/DOG|POULTRY|CHICKEN|DUCK|GOOSE|GEESE/.test(code)) return "DOMESTIC_ANIMAL";
  if (/STALLED|DISABLED|BREAKDOWN/.test(code)) return "STALLED_VEHICLE";
  if (/DEBRIS|TREE|TRAILER|TRACTOR|EQUIPMENT|SPILL/.test(code)) return "DEBRIS";
  if (code.includes("MOOSE")) return "MOOSE";
  if (code.includes("CARIBOU")) return "CARIBOU";
  if (code.includes("BEAR")) return "BEAR";
  if (/ANIMAL|WILDLIFE|DEER|ELK|BISON|COYOTE|WOLF|LYNX|FOX|EAGLE|PORCUPINE|OTTER|CAT/.test(code)) return "ANIMAL_OTHER";
  if (/PEDESTRIAN|CHILD/.test(code)) return "PEDESTRIAN_IN_ROAD";
  if (/FOG|SNOW|VISIBILITY|RAIN/.test(code)) return "FOG";
  if (/POTHOLE|DAMAGE|HEAVE|CRACK/.test(code)) return "DAMAGE_BOTH_LANES";
  if (/ACCIDENT|CRASH|BLOCK/.test(code)) return "ACCIDENT_BLOCKING";
  return "OTHER_HAZARD";
}

function compassLane(heading) {
  if (heading === null || heading === undefined || heading === "" || !Number.isFinite(Number(heading))) {
    return "lane unspecified";
  }
  const degrees = ((Number(heading) % 360) + 360) % 360;
  return `${["northbound", "eastbound", "southbound", "westbound"][Math.round(degrees / 90) % 4]} lane`;
}

function displayValue(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  return "";
}

function reportName(value) {
  const text = displayValue(value);
  if (!text || text.length > 40 || /^[A-Z0-9_]+$/.test(text) || CLAIM_WORDS.test(text)) return "";
  return text[0].toUpperCase() + text.slice(1);
}

export function hazardTitle(row) {
  const code = (displayValue(row.hazard_type) || "OTHER_HAZARD").toUpperCase();
  const type = typeForCode(code);
  const name = type === "DELAY"
    ? typeLabels.DELAY
    : reportName(row.display_text) || reportName(row.specific) || typeLabels[type];
  if (LANE_SCOPED_DAMAGE.has(code)) return `${name}, ${compassLane(row.heading)}`;
  if (code === "DAMAGE_BOTH_LANES") return `${name}, both lanes`;
  return name;
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
  const type = typeForCode(hazardType);
  const category = categoryByType[type];
  const title = hazardTitle(row);
  const code = hazardType.toUpperCase();

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
      // AlertWording.descriptor: the report's own specific when it names only the hazard, else the type's noun.
      descriptor: type === "DELAY" ? typeLabels.DELAY : reportName(row.specific) || spokenLabels[type],
      speaks_age: SPEAKS_AGE.has(code),
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

export function openHazardPopup(map, properties, coordinate) {
  return openHazardCard(map, properties, coordinate);
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
  return { refresh, lastSuccessAt: () => lastSuccessfulAt };
}