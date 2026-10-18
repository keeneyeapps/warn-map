// Hazard cards, styled after the Android app's hazard card (item_hazard_card.xml): a category colour band, the
// hazard's name, its distance, how long ago it was last seen, and its confirmation count. The Android card's
// Confirm / Mute / Clear row is a disabled placeholder here (no reporting or actions on the web yet). Shared by both
// pages; this module never writes anywhere.
import * as maplibregl from "../vendor/maplibre-gl.js";

const METERS_PER_MILE = 1609.344;
// Android's AlertWording.HEADING_BANDS_CALIBRATED is false: no card or alert claims a direction ("ahead") until the
// heading is shown to be trustworthy, so distances read "away". Flip with the Android flag, never on its own.
export const DIRECTION_CLAIMS = false;

let locationSource = () => null;

/** Where the cards measure distance from: a function returning [lon, lat] or null. */
export function setCardLocationSource(source) {
  locationSource = source;
}

export function distanceMeters(first, second) {
  const toRadians = Math.PI / 180;
  const dLat = (second[1] - first[1]) * toRadians;
  const dLon = (second[0] - first[0]) * toRadians;
  const value = Math.sin(dLat / 2) ** 2
    + Math.cos(first[1] * toRadians) * Math.cos(second[1] * toRadians) * Math.sin(dLon / 2) ** 2;
  return 2 * 6_371_008.8 * Math.asin(Math.sqrt(value));
}

/** AlertWording.distanceShort: "150 ft" below 0.1 mile (50 ft steps, at least 50), else "0.3 mi". */
export function distanceShort(meters) {
  const miles = meters / METERS_PER_MILE;
  if (miles >= 0.1) return `${miles.toFixed(1)} mi`;
  return `${Math.max(50, Math.round((meters * 3.28084) / 50) * 50)} ft`;
}

/** AlertWording.distanceSpoken: "150 feet" below 0.1 mile, else "0.4 miles". */
export function distanceSpoken(meters) {
  const miles = meters / METERS_PER_MILE;
  if (miles >= 0.1) return `${miles.toFixed(1)} miles`;
  return `${Math.max(50, Math.round((meters * 3.28084) / 50) * 50)} feet`;
}

/** LastSeen.formatAge: "just now" / "12 min ago" / "2 h 5 min ago"; null when the time is unknown. */
export function cardAge(timestamp, now = Date.now()) {
  const seen = Date.parse(timestamp);
  if (!Number.isFinite(seen)) return null;
  const minutes = Math.floor(Math.max(0, now - seen) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
}

function textElement(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

/**
 * A hazard card for a hazard feature's properties. `distanceM` (optional) overrides the distance from the card
 * location source; `point` is the hazard's [lon, lat].
 */
export function hazardCard(properties, { point = null, distanceM = null, now = Date.now() } = {}) {
  const card = document.createElement("article");
  card.className = "hazard-card";
  card.style.setProperty("--hazard-band", properties.category_color || "#9E9E9E");
  card.dataset.hazardId = String(properties.id ?? "");

  const head = document.createElement("div");
  head.className = "hazard-card-head";
  head.append(textElement("h2", "hazard-card-title", properties.title || "Hazard"));
  const from = locationSource();
  const meters = Number.isFinite(distanceM) ? distanceM
    : (point && Array.isArray(from) ? distanceMeters(from, point) : null);
  if (Number.isFinite(meters)) {
    head.append(textElement("span", "hazard-card-distance", `${distanceShort(meters)} ${DIRECTION_CLAIMS ? "ahead" : "away"}`));
  }

  const details = [properties.category_label].filter(Boolean);
  const age = cardAge(properties.created_at, now);
  if (age) details.push(`last seen ${age}`);
  const confirmations = Number(properties.confirmation_count) || 0;
  if (confirmations > 0) details.push(`${confirmations} ${confirmations === 1 ? "confirmation" : "confirmations"}`);
  if (properties.category === "DELAY" && properties.delay_transit_s >= 60) {
    details.push(`at least ${Math.floor(properties.delay_transit_s / 60)} min`);
  }

  // Sized for the Android card's three in-car buttons (Confirm, Mute, Clear); disabled until reporting exists.
  const actions = document.createElement("div");
  actions.className = "hazard-card-actions";
  actions.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 3; index += 1) {
    const slot = document.createElement("span");
    slot.className = "hazard-card-action-slot";
    actions.append(slot);
  }

  card.append(head, textElement("p", "hazard-card-meta", details.join(" · ")), actions);
  return card;
}

/** Opens a hazard's card as a map popup (replacing the old generic popup). */
export function openHazardCard(map, properties, coordinate) {
  const point = Array.isArray(coordinate) ? coordinate : [coordinate.lng, coordinate.lat];
  return new maplibregl.Popup({ closeButton: true, maxWidth: "320px", className: "hazard-card-popup" })
    .setLngLat(point)
    .setDOMContent(hazardCard(properties, { point }))
    .addTo(map);
}
