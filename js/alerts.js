// Spoken hazard alerts while following. On each fresh fix with a course, a hazard within ±30° of the course and within
// 0.5 mi gets its card at the top of the screen and one spoken sentence, at most once per 20 minutes. One sentence
// at a time; nothing is spoken while the page is hidden. Mute silences speech only: cards still show. Wording follows
// the Android app's AlertWording (no direction claim while DIRECTION_CLAIMS is false) and SpokenAgePolicy (which types
// say their age). Shared by both pages; this module never writes anywhere but this browser's localStorage (mute).
import { DIRECTION_CLAIMS, distanceMeters, distanceSpoken, hazardCard } from "./cards.js";

const METERS_PER_MILE = 1609.344;
const ALERT_RADIUS_M = 0.5 * METERS_PER_MILE;
const CLEAR_RADIUS_M = 0.6 * METERS_PER_MILE; // a shown card goes once the hazard is this far or out of the cone
const CONE_HALF_ANGLE_DEGREES = 30;
const REPEAT_AFTER_MS = 20 * 60_000;
// LastSeen.STALE_POLL_MS is three missed polls; the web polls every 60 s, so past 3 minutes no age is spoken.
const STALE_DATA_MS = 3 * 60_000;
const MAX_CARDS = 2;
const MUTE_KEY = "warn-map-alerts-muted";
// Pedestrians and wildlife are spoken with "Caution"; every other hazard with "Note".
const CAUTION_CATEGORIES = new Set(["ANIMALS", "PEDESTRIAN"]);

function bearingDegrees(first, second) {
  const toRadians = Math.PI / 180;
  const latitude1 = first[1] * toRadians;
  const latitude2 = second[1] * toRadians;
  const deltaLongitude = (second[0] - first[0]) * toRadians;
  const y = Math.sin(deltaLongitude) * Math.cos(latitude2);
  const x = Math.cos(latitude1) * Math.sin(latitude2)
    - Math.sin(latitude1) * Math.cos(latitude2) * Math.cos(deltaLongitude);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function angleBetween(first, second) {
  const difference = Math.abs(first - second) % 360;
  return difference > 180 ? 360 - difference : difference;
}

/** LastSeen.spokenAge: "just now" / "1 minute ago" / "12 minutes ago" / "1 hour ago" / "3 hours ago". */
function spokenAge(ageMs) {
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  if (minutes < 120) return "1 hour ago";
  return `${Math.floor(minutes / 60)} hours ago`;
}

/**
 * The spoken alert, e.g. "Caution, Moose reported 0.4 miles away, last seen 5 minutes ago." The age only for types
 * SpokenAgePolicy allows and only when the hazard data is fresh; a delay says its reporter's transit time instead.
 */
export function alertText(properties, meters, { now = Date.now(), dataFresh = true } = {}) {
  const prefix = CAUTION_CATEGORIES.has(properties.category) ? "Caution" : "Note";
  const name = properties.descriptor || properties.title || "Hazard";
  const where = `${distanceSpoken(meters)} ${DIRECTION_CLAIMS ? "ahead" : "away"}`;
  let text = `${prefix}, ${name} reported ${where}`;
  if (properties.category === "DELAY") {
    const minutes = Math.floor((Number(properties.delay_transit_s) || 0) / 60);
    if (minutes >= 1) text += `, at least ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  } else if (properties.speaks_age && dataFresh) {
    const seen = Date.parse(properties.created_at);
    if (Number.isFinite(seen)) text += `, last seen ${spokenAge(Math.max(0, now - seen))}`;
  }
  return `${text}.`;
}

function readMuted() {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * `strip` is the status strip (the mute toggle goes there); `cardHost` holds alert cards at the top of the screen;
 * `lastHazardPoll()` gives the time of the last successful hazard poll (for the age rule).
 */
export function setupAlerts({ strip, cardHost, lastHazardPoll = () => Date.now() }) {
  let hazards = [];
  let muted = readMuted();
  const alertedAt = new Map();
  const shown = new Map(); // hazard id -> card element
  const speechQueue = [];
  let speaking = false;

  const muteButton = document.createElement("button");
  muteButton.type = "button";
  muteButton.id = "alert-mute";
  muteButton.className = "alert-mute";
  muteButton.addEventListener("click", () => {
    muted = !muted;
    try {
      if (muted) localStorage.setItem(MUTE_KEY, "1");
      else localStorage.removeItem(MUTE_KEY);
    } catch {
      // Storage unavailable: mute applies to this page only.
    }
    if (muted) stopSpeech();
    renderMute();
  });
  strip.append(muteButton);

  function renderMute() {
    muteButton.textContent = muted ? "Voice muted" : "Voice on";
    muteButton.setAttribute("aria-pressed", String(muted));
    muteButton.setAttribute("aria-label", muted ? "Spoken alerts muted. Tap to unmute." : "Spoken alerts on. Tap to mute.");
    strip.classList.toggle("is-muted", muted);
  }
  renderMute();

  function stopSpeech() {
    speechQueue.length = 0;
    speaking = false;
    globalThis.speechSynthesis?.cancel();
  }

  function speakNext() {
    if (speaking || speechQueue.length === 0) return;
    const text = speechQueue.shift();
    if (muted || document.visibilityState !== "visible" || !globalThis.speechSynthesis) {
      speakNext();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    speaking = true;
    const done = () => {
      speaking = false;
      speakNext();
    };
    utterance.onend = done;
    utterance.onerror = done;
    globalThis.speechSynthesis.speak(utterance);
  }

  function speak(text) {
    if (muted || document.visibilityState !== "visible") return;
    speechQueue.push(text);
    speakNext();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") stopSpeech();
  });

  function hazardPoint(feature) {
    return feature.geometry.coordinates;
  }

  function showCard(feature, meters) {
    const id = String(feature.properties.id);
    shown.get(id)?.remove();
    const card = hazardCard(feature.properties, { distanceM: meters });
    card.classList.add("alert-card");
    card.setAttribute("role", "alert");
    cardHost.prepend(card);
    shown.set(id, card);
    while (shown.size > MAX_CARDS) {
      const [oldestId, oldest] = shown.entries().next().value; // insertion order: the oldest card first
      oldest.remove();
      shown.delete(oldestId);
    }
  }

  /** A fresh fix while following: `point` is [lon, lat], `course` the travel direction in degrees. */
  function onFix({ point, course }) {
    if (!Array.isArray(point) || !Number.isFinite(course)) return;
    const now = Date.now();
    const dataFresh = now - (lastHazardPoll() ?? 0) <= STALE_DATA_MS;
    const live = new Set();
    for (const feature of hazards) {
      const id = String(feature.properties.id);
      live.add(id);
      const meters = distanceMeters(point, hazardPoint(feature));
      const inCone = angleBetween(bearingDegrees(point, hazardPoint(feature)), course) <= CONE_HALF_ANGLE_DEGREES;
      if (shown.has(id) && (!inCone || meters > CLEAR_RADIUS_M)) {
        shown.get(id).remove();
        shown.delete(id);
      }
      if (!inCone || meters > ALERT_RADIUS_M) continue;
      const last = alertedAt.get(id);
      if (last !== undefined && now - last < REPEAT_AFTER_MS) continue;
      alertedAt.set(id, now);
      showCard(feature, meters);
      speak(alertText(feature.properties, meters, { now, dataFresh }));
    }
    for (const [id, card] of shown) {
      if (!live.has(id)) {
        card.remove();
        shown.delete(id);
      }
    }
  }

  return {
    setHazards(features) {
      hazards = Array.isArray(features) ? features : [];
    },
    onFix,
  };
}
