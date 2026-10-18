// Typed-address and named-place lookup over the on-device indexes (the region pack's addresses and places
// files). Both are static files served with the app and precached by the service worker; nothing here sends a
// query anywhere.

import { regionFileUrl } from "./region.js";

const INDEX_URL = regionFileUrl("addresses");
const PLACES_URL = regionFileUrl("places");
const MAX_SUGGESTIONS = 8;
const NEAREST_NUMBERS_PER_STREET = 2;
const METERS_PER_DEGREE = 111_320;

// Each spelling maps to one canonical token so "Road" and "Rd" are interchangeable on both sides.
const SUFFIXES = {
  road: "rd",
  highway: "hwy",
  avenue: "ave",
  drive: "dr",
  street: "st",
  lane: "ln",
  court: "ct",
  circle: "cir",
  trail: "trl",
  boulevard: "blvd",
};

let indexPromise = null;

export function normaliseTokens(text) {
  return String(text ?? "")
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9/]+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean)
    .map((token) => SUFFIXES[token] || token);
}

function distanceMeters(first, second) {
  const longitudeScale = METERS_PER_DEGREE * Math.cos(first[1] * Math.PI / 180);
  return Math.hypot((second[0] - first[0]) * longitudeScale, (second[1] - first[1]) * METERS_PER_DEGREE);
}

function prepareIndex(data) {
  const places = data.places.map((name) => ({ name, joined: normaliseTokens(name).join(" ") }));
  const streets = data.streets.map((name) => {
    const tokens = normaliseTokens(name);
    return { name, tokens, joined: tokens.join(" "), groups: new Map() };
  });
  // Rows grouped by street, then by postal city, keeping the index's number order.
  data.addresses.forEach(([street, number, unit, city, community, longitude, latitude]) => {
    const groups = streets[street].groups;
    if (!groups.has(city)) groups.set(city, { city, communities: new Set(), rows: [] });
    const group = groups.get(city);
    if (community >= 0) group.communities.add(community);
    group.rows.push({ number, unit, point: [longitude, latitude] });
  });
  return { places, streets, placeNames: new Set(places.map((place) => place.joined)) };
}

// Named places (businesses, services, facilities): [name, category, label, city, address, phone, website, lon, lat].
function prepareNamedPlaces(data) {
  return data.places.map(([name, category, label, city, address, phone, website, longitude, latitude]) => {
    const tokens = normaliseTokens(name);
    const cityName = city >= 0 ? data.cities[city] : "";
    return {
      name,
      tokens,
      joined: tokens.join(" "),
      context: [...normaliseTokens(data.labels[label]), ...normaliseTokens(cityName)],
      cityJoined: normaliseTokens(cityName).join(" "),
      category: data.categories[category],
      label: data.labels[label],
      city: cityName,
      address,
      phone,
      website,
      point: [longitude, latitude],
    };
  });
}

async function fetchJson(url, what) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${what} request failed (${response.status})`);
  return response.json();
}

export function loadAddressIndex() {
  if (!indexPromise) {
    indexPromise = Promise.all([
      fetchJson(INDEX_URL, "Address index").then(prepareIndex),
      PLACES_URL ? fetchJson(PLACES_URL, "Places index").then(prepareNamedPlaces) : [],
    ])
      .then(([index, namedPlaces]) => ({ ...index, namedPlaces }))
      .catch((error) => {
        indexPromise = null;
        throw error;
      });
  }
  return indexPromise;
}

function parseQuery(text, index) {
  const [main, ...rest] = String(text ?? "").split(",");
  let tokens = normaliseTokens(main);
  let cityTokens = normaliseTokens(rest.join(" "));
  let number = null;
  const numberMatch = tokens.length > 0 ? tokens[0].match(/^(\d+)[a-z]?$/) : null;
  if (numberMatch) {
    number = Number(numberMatch[1]);
    tokens = tokens.slice(1);
    if (tokens.length > 0 && /^\d+\/\d+$/.test(tokens[0])) tokens = tokens.slice(1);
  }
  // Without a comma, trailing words that spell a place name are the city ("main st springfield").
  if (cityTokens.length === 0) {
    for (let count = Math.min(3, tokens.length - 1); count >= 1; count -= 1) {
      if (index.placeNames.has(tokens.slice(-count).join(" "))) {
        cityTokens = tokens.slice(-count);
        tokens = tokens.slice(0, -count);
        break;
      }
    }
  }
  return { number, streetTokens: tokens, city: cityTokens.join(" ") };
}

// 0: no match. Otherwise every typed word is a prefix of a different word of the street name, and
// 4 = the whole name, 3 = the name starts with what was typed, 2 = whole words elsewhere, 1 = partial words.
function streetQuality(street, queryTokens) {
  const used = new Array(street.tokens.length).fill(false);
  let wholeWords = true;
  for (const token of queryTokens) {
    let hit = street.tokens.findIndex((word, index) => !used[index] && word === token);
    if (hit < 0) {
      hit = street.tokens.findIndex((word, index) => !used[index] && word.startsWith(token));
      wholeWords = false;
    }
    if (hit < 0) return 0;
    used[hit] = true;
  }
  const typed = queryTokens.join(" ");
  if (street.joined === typed) return 4;
  if (street.joined.startsWith(typed)) return 3;
  return wholeWords ? 2 : 1;
}

// Like streetQuality for a place name, except typed words that miss the name may match its type or city
// ("soldotna post" finds a post office in Soldotna). At least one word must hit the name; such partial
// matches score 0.5, below every whole-name match.
function placeQuality(place, queryTokens) {
  const used = new Array(place.tokens.length).fill(false);
  let wholeWords = true;
  let nameHits = 0;
  let contextHits = 0;
  for (const token of queryTokens) {
    let hit = place.tokens.findIndex((word, index) => !used[index] && word === token);
    if (hit < 0) {
      hit = place.tokens.findIndex((word, index) => !used[index] && word.startsWith(token));
      if (hit >= 0) wholeWords = false;
    }
    if (hit >= 0) {
      used[hit] = true;
      nameHits += 1;
    } else if (place.context.some((word) => word.startsWith(token))) {
      contextHits += 1;
    } else {
      return 0;
    }
  }
  if (nameHits === 0) return 0;
  if (contextHits > 0) return 0.5;
  const typed = queryTokens.join(" ");
  if (place.joined === typed) return 4;
  if (place.joined.startsWith(typed)) return 3;
  return wholeWords ? 2 : 1;
}

// A place matches "well" when its name is, or starts with, what was typed; those rank above streets.
const WELL_MATCHED_PLACE = 3;

function placeLabel(place) {
  return `${place.name} · ${place.label}${place.city ? `, ${place.city}` : ""}`;
}

function addressLabel(number, street, unit, city) {
  return `${number} ${street}${unit ? ` ${unit}` : ""}${city ? `, ${city}` : ""}`;
}

function nearestRow(rows, location) {
  let best = rows[0];
  let bestDistance = Infinity;
  for (const row of rows) {
    const distance = distanceMeters(location, row.point);
    if (distance < bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }
  return { row: best, distance: bestDistance };
}

/**
 * Suggestions for typed text, best first. Each is { label, kind: "address" | "street" | "place", point() },
 * where point() gives the [longitude, latitude] to route to (for a street: the address nearest `location`,
 * or the street's middle address when location is unknown). A place suggestion also has `place`: its name,
 * type label, city, street address, and phone (public business or facility records only).
 *
 * Order: addresses for a typed house number; then places whose name is or starts with the typed text; then
 * streets; then other place matches. Within a place group, nearest first when the location is known.
 */
export async function searchAddresses(text, location = null) {
  const index = await loadAddressIndex();
  const query = parseQuery(text, index);
  const hasLocation = Array.isArray(location) && location.every(Number.isFinite);

  const candidates = [];
  const placeTokens = normaliseTokens(String(text ?? "").split(",")[0]);
  if (placeTokens.length > 0) {
    for (const place of index.namedPlaces) {
      const quality = placeQuality(place, placeTokens);
      if (quality === 0) continue;
      candidates.push({
        tier: quality >= WELL_MATCHED_PLACE ? 1 : 3,
        cityMatch: Boolean(query.city) && place.cityJoined.startsWith(query.city),
        exactNumber: false,
        quality,
        numberGap: 0,
        distance: hasLocation ? distanceMeters(location, place.point) : Infinity,
        kind: "place",
        label: placeLabel(place),
        point: () => place.point,
        place: { name: place.name, label: place.label, city: place.city, address: place.address, phone: place.phone },
      });
    }
  }

  for (const street of query.streetTokens.length > 0 ? index.streets : []) {
    const quality = streetQuality(street, query.streetTokens);
    if (quality === 0) continue;
    for (const group of street.groups.values()) {
      const cityName = group.city >= 0 ? index.places[group.city].name : "";
      const cityMatch = Boolean(query.city) && [group.city, ...group.communities]
        .some((place) => place >= 0 && index.places[place].joined.startsWith(query.city));
      const base = { quality, cityMatch, street: street.name };

      if (query.number === null) {
        const middle = group.rows[Math.floor((group.rows.length - 1) / 2)];
        const distance = hasLocation ? nearestRow(group.rows, location).distance : Infinity;
        candidates.push({
          ...base,
          tier: 2,
          exactNumber: false,
          numberGap: 0,
          distance,
          kind: "street",
          label: `${street.name}${cityName ? `, ${cityName}` : ""} (street)`,
          point: () => (hasLocation ? nearestRow(group.rows, location).row.point : middle.point),
        });
        continue;
      }

      const exact = group.rows.filter((row) => row.number === query.number);
      const rows = exact.length > 0
        ? exact
        : [...new Set(group.rows.map((row) => row.number))]
          .sort((first, second) => Math.abs(first - query.number) - Math.abs(second - query.number) || first - second)
          .slice(0, NEAREST_NUMBERS_PER_STREET)
          .map((number) => group.rows.find((row) => row.number === number));
      for (const row of rows) {
        candidates.push({
          ...base,
          tier: 0,
          exactNumber: exact.length > 0,
          numberGap: Math.abs(row.number - query.number),
          distance: hasLocation ? distanceMeters(location, row.point) : Infinity,
          kind: "address",
          label: addressLabel(row.number, street.name, row.unit, cityName),
          point: () => row.point,
        });
      }
    }
  }

  candidates.sort((first, second) => (
    (first.tier - second.tier)
    || (second.cityMatch - first.cityMatch)
    || (second.exactNumber - first.exactNumber)
    || (second.quality - first.quality)
    || (first.numberGap - second.numberGap)
    || (first.distance - second.distance)
    || first.label.localeCompare(second.label)
  ));

  const seen = new Set();
  const results = [];
  for (const candidate of candidates) {
    if (seen.has(candidate.label)) continue;
    seen.add(candidate.label);
    const result = { label: candidate.label, kind: candidate.kind, point: candidate.point };
    if (candidate.place) result.place = candidate.place;
    results.push(result);
    if (results.length === MAX_SUGGESTIONS) break;
  }
  return results;
}
