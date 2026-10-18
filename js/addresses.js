// Typed-address lookup over the on-device index (addresses/kenai-addresses.json). The index is a static file
// served with the app and precached by the service worker; nothing here sends a query anywhere.

const INDEX_URL = new URL("../addresses/kenai-addresses.json", import.meta.url);
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

export function loadAddressIndex() {
  if (!indexPromise) {
    indexPromise = fetch(INDEX_URL)
      .then((response) => {
        if (!response.ok) throw new Error(`Address index request failed (${response.status})`);
        return response.json();
      })
      .then(prepareIndex)
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
  // Without a comma, trailing words that spell a place name are the city ("main st kenai").
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
 * Suggestions for typed text, best first. Each is { label, kind: "address" | "street", point() }, where
 * point() gives the [longitude, latitude] to route to (for a street: the address nearest `location`,
 * or the street's middle address when location is unknown).
 */
export async function searchAddresses(text, location = null) {
  const index = await loadAddressIndex();
  const query = parseQuery(text, index);
  if (query.streetTokens.length === 0) return [];
  const hasLocation = Array.isArray(location) && location.every(Number.isFinite);

  const candidates = [];
  for (const street of index.streets) {
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
    (second.cityMatch - first.cityMatch)
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
    results.push({ label: candidate.label, kind: candidate.kind, point: candidate.point });
    if (results.length === MAX_SUGGESTIONS) break;
  }
  return results;
}
