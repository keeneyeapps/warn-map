// Describes a point as "Road name, near Community" from the on-device data only: the road graph's named roads and the
// address index's dispatch communities, both served from this repository (and precached). No outside service is
// called. Used by WARN Watch to say where each hazard is.
import { regionFileUrl } from "./region.js";

const ROAD_RADIUS_M = 250;
const COMMUNITY_RADIUS_M = 15_000;
const MIN_ADDRESSES_FOR_COMMUNITY = 5;
// Parking aisles and tracks are not where a hazard report is meant; the road beside them is.
const SKIPPED_CLASSES = new Set(["service", "service_link", "track", "track_link"]);
const CELL_LON = 0.02;
const CELL_LAT = 0.01;
const METERS_PER_DEGREE = 111_195;

// Low priority: these files are large (the road graph is about 15 MB) and must never hold up the basemap.
async function fetchJson(url) {
  const response = await fetch(url, { priority: "low" });
  if (!response.ok) throw new Error(`${url} request failed (${response.status})`);
  return response.json();
}

function cellKey(x, y) {
  return x * 100_000 + y;
}

function buildRoadIndex(graph) {
  const skipped = new Set(graph.classes.map((name, index) => (SKIPPED_CLASSES.has(name) ? index : -1)).filter((i) => i >= 0));
  const names = [];
  const nameIds = new Map();
  const segments = [];
  const grid = new Map();
  for (const edge of graph.edges) {
    const name = edge[6];
    if (!name || skipped.has(edge[3])) continue;
    let nameId = nameIds.get(name);
    if (nameId === undefined) {
      nameId = names.length;
      names.push(name);
      nameIds.set(name, nameId);
    }
    const geometry = edge[7];
    for (let i = 0; i < geometry.length - 1; i += 1) {
      const [lon1, lat1] = geometry[i];
      const [lon2, lat2] = geometry[i + 1];
      const index = segments.length / 5;
      segments.push(lon1, lat1, lon2, lat2, nameId);
      const x1 = Math.floor(Math.min(lon1, lon2) / CELL_LON);
      const x2 = Math.floor(Math.max(lon1, lon2) / CELL_LON);
      const y1 = Math.floor(Math.min(lat1, lat2) / CELL_LAT);
      const y2 = Math.floor(Math.max(lat1, lat2) / CELL_LAT);
      for (let x = x1; x <= x2; x += 1) {
        for (let y = y1; y <= y2; y += 1) {
          const key = cellKey(x, y);
          const cell = grid.get(key);
          if (cell) cell.push(index);
          else grid.set(key, [index]);
        }
      }
    }
  }
  return { names, segments, grid };
}

function nearestRoad(index, point) {
  const [lon, lat] = point;
  const scaleX = METERS_PER_DEGREE * Math.cos(lat * Math.PI / 180);
  const cx = Math.floor(lon / CELL_LON);
  const cy = Math.floor(lat / CELL_LAT);
  let best = null;
  let bestDistance = ROAD_RADIUS_M;
  const seen = new Set();
  for (let x = cx - 1; x <= cx + 1; x += 1) {
    for (let y = cy - 1; y <= cy + 1; y += 1) {
      for (const segment of index.grid.get(cellKey(x, y)) ?? []) {
        if (seen.has(segment)) continue;
        seen.add(segment);
        const o = segment * 5;
        const ax = (index.segments[o] - lon) * scaleX;
        const ay = (index.segments[o + 1] - lat) * METERS_PER_DEGREE;
        const bx = (index.segments[o + 2] - lon) * scaleX;
        const by = (index.segments[o + 3] - lat) * METERS_PER_DEGREE;
        const dx = bx - ax;
        const dy = by - ay;
        const lengthSquared = dx * dx + dy * dy;
        const t = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared));
        const distance = Math.hypot(ax + t * dx, ay + t * dy);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = index.names[index.segments[o + 4]];
        }
      }
    }
  }
  return best;
}

// A community's centre is the mean of its address points: the address index names each point's dispatch community.
function buildCommunities(addresses) {
  const sums = new Map();
  for (const row of addresses.addresses) {
    const community = row[4];
    if (community < 0) continue;
    const sum = sums.get(community) ?? { lon: 0, lat: 0, count: 0 };
    sum.lon += row[5];
    sum.lat += row[6];
    sum.count += 1;
    sums.set(community, sum);
  }
  const communities = [];
  for (const [index, sum] of sums) {
    if (sum.count >= MIN_ADDRESSES_FOR_COMMUNITY) {
      communities.push({ name: addresses.places[index], lon: sum.lon / sum.count, lat: sum.lat / sum.count });
    }
  }
  return communities;
}

function nearestCommunity(communities, point) {
  const scaleX = METERS_PER_DEGREE * Math.cos(point[1] * Math.PI / 180);
  let best = null;
  let bestDistance = COMMUNITY_RADIUS_M;
  for (const community of communities) {
    const distance = Math.hypot((community.lon - point[0]) * scaleX, (community.lat - point[1]) * METERS_PER_DEGREE);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = community.name;
    }
  }
  return best;
}

export function describeFrom(roads, communities, point) {
  const road = roads ? nearestRoad(roads, point) : null;
  const community = communities ? nearestCommunity(communities, point) : null;
  if (road && community) return `${road}, near ${community}`;
  if (road) return road;
  if (community) return `Near ${community}`;
  return "Not on a named road";
}

let loading = null;

/** Loads the on-device data once; resolves to describe([lon, lat]) -> text. A missing file only loses that part. */
export function loadDescriber() {
  if (!loading) {
    loading = Promise.all([
      fetchJson(regionFileUrl("graph")).then(buildRoadIndex).catch(() => null),
      fetchJson(regionFileUrl("addresses")).then(buildCommunities).catch(() => null),
    ]).then(([roads, communities]) => (point) => describeFrom(roads, communities, point));
  }
  return loading;
}
