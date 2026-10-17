const EARTH_RADIUS_M = 6_371_008.8;
const METERS_PER_DEGREE = 111_320;
const MAX_DISTANCE_FROM_ROUTE_M = 60;

function haversineMeters(first, second) {
  const latitude1 = first[1] * Math.PI / 180;
  const latitude2 = second[1] * Math.PI / 180;
  const deltaLatitude = latitude2 - latitude1;
  const deltaLongitude = (second[0] - first[0]) * Math.PI / 180;
  const sinLatitude = Math.sin(deltaLatitude / 2);
  const sinLongitude = Math.sin(deltaLongitude / 2);
  const value = sinLatitude * sinLatitude
    + Math.cos(latitude1) * Math.cos(latitude2) * sinLongitude * sinLongitude;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(value));
}

function projectPointToSegment(point, first, second) {
  const longitudeScale = METERS_PER_DEGREE * Math.cos(point[1] * Math.PI / 180);
  const firstX = (first[0] - point[0]) * longitudeScale;
  const firstY = (first[1] - point[1]) * METERS_PER_DEGREE;
  const deltaX = (second[0] - first[0]) * longitudeScale;
  const deltaY = (second[1] - first[1]) * METERS_PER_DEGREE;
  const denominator = deltaX * deltaX + deltaY * deltaY;
  const fraction = denominator === 0
    ? 0
    : Math.max(0, Math.min(1, -(firstX * deltaX + firstY * deltaY) / denominator));
  const coordinate = [
    first[0] + (second[0] - first[0]) * fraction,
    first[1] + (second[1] - first[1]) * fraction,
  ];
  return { coordinate, fraction, distanceM: haversineMeters(point, coordinate) };
}

function routeMetrics(route) {
  const cumulative = new Float64Array(route.geometry.length);
  const edgeBySegment = new Array(Math.max(0, route.geometry.length - 1)).fill(null);
  for (let index = 1; index < route.geometry.length; index += 1) {
    cumulative[index] = cumulative[index - 1]
      + haversineMeters(route.geometry[index - 1], route.geometry[index]);
  }
  for (const edge of route.edgeIndices || []) {
    for (let index = edge.geometry_start_index; index < edge.geometry_end_index; index += 1) {
      edgeBySegment[index] = edge;
    }
  }
  return {
    cumulative,
    edgeBySegment,
    geometryDistanceM: cumulative[cumulative.length - 1] || 0,
  };
}

function nearestRoutePosition(point, route, metrics) {
  let nearest = null;
  for (let index = 0; index + 1 < route.geometry.length; index += 1) {
    const first = route.geometry[index];
    const second = route.geometry[index + 1];
    const projection = projectPointToSegment(point, first, second);
    if (!nearest || projection.distanceM < nearest.distanceFromLineM) {
      const segmentLength = metrics.cumulative[index + 1] - metrics.cumulative[index];
      const geometryDistanceAlong = metrics.cumulative[index] + segmentLength * projection.fraction;
      const edgeRange = metrics.edgeBySegment[index];
      let distanceAlongRouteM = metrics.geometryDistanceM > 0
        ? geometryDistanceAlong / metrics.geometryDistanceM * route.totalDistanceM
        : 0;
      if (edgeRange) {
        const edgeGeometryStart = metrics.cumulative[edgeRange.geometry_start_index];
        const edgeGeometryEnd = metrics.cumulative[edgeRange.geometry_end_index];
        const edgeGeometryLength = edgeGeometryEnd - edgeGeometryStart;
        const edgeFraction = edgeGeometryLength > 0
          ? (geometryDistanceAlong - edgeGeometryStart) / edgeGeometryLength
          : 0;
        distanceAlongRouteM = edgeRange.start_m
          + Math.max(0, Math.min(1, edgeFraction)) * (edgeRange.end_m - edgeRange.start_m);
      }
      nearest = {
        coordinate: projection.coordinate,
        distanceFromLineM: projection.distanceM,
        distanceAlongRouteM,
        edgeIndex: edgeRange?.index ?? null,
      };
    }
  }
  return nearest;
}

export function hazardsAlongRoute(hazards, route, liveFix) {
  if (!route?.geometry || route.geometry.length < 2) return [];
  const metrics = routeMetrics(route);
  const progress = liveFix ? nearestRoutePosition(liveFix, route, metrics) : null;
  const results = [];

  for (const feature of hazards) {
    const point = feature.geometry?.coordinates;
    if (!Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) continue;
    const nearest = nearestRoutePosition(point, route, metrics);
    if (!nearest || nearest.distanceFromLineM > MAX_DISTANCE_FROM_ROUTE_M) continue;
    if (progress && nearest.distanceAlongRouteM < progress.distanceAlongRouteM) continue;
    results.push({
      feature,
      distanceFromLineM: nearest.distanceFromLineM,
      distanceAlongRouteM: nearest.distanceAlongRouteM,
      distanceAheadM: Math.max(0, nearest.distanceAlongRouteM - (progress?.distanceAlongRouteM || 0)),
      edgeIndex: nearest.edgeIndex,
    });
  }

  results.sort((first, second) => first.distanceAheadM - second.distanceAheadM);
  return results;
}