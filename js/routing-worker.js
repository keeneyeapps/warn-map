const EARTH_RADIUS_M = 6_371_008.8;
const METERS_PER_DEGREE = 111_320;
const MIN_LON = -154.8;
const MIN_LAT = 58.9;
const MAX_LON = -148.2;
const MAX_LAT = 61.6;
const GRID_CELL_DEGREES = 0.025;
const NO_INDEX = 0xffffffff;
const DEFAULT_SPEED_KPH = [
  100, 65, 80, 55, 65, 45, 55, 40, 45, 35,
  35, 30, 30, 25, 20, 20, 10, 10, 20, 15,
];

let graphPromise;
let graphState;
let graphLoadTimeMs = null;
let graphTransfer = null;

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

function speedMetersPerSecond(maxspeed, classIndex) {
  if (maxspeed !== null && maxspeed !== undefined) {
    const value = String(maxspeed).trim().toLowerCase();
    const match = value.match(/[0-9]+(?:\.[0-9]+)?/);
    if (match) {
      const speed = Number(match[0]);
      if (speed > 0) {
        if (value.includes("knot")) return speed * 0.514444;
        if (value.includes("km/h") || value.includes("kph")) return speed / 3.6;
        return speed * 0.44704;
      }
    }
  }
  return DEFAULT_SPEED_KPH[classIndex] / 3.6;
}

function gridX(longitude, state) {
  return Math.max(0, Math.min(state.gridWidth - 1, Math.floor((longitude - MIN_LON) / GRID_CELL_DEGREES)));
}

function gridY(latitude, state) {
  return Math.max(0, Math.min(state.gridHeight - 1, Math.floor((latitude - MIN_LAT) / GRID_CELL_DEGREES)));
}

function addSegmentToGrid(gridLists, state, segmentId, firstPoint, secondPoint) {
  const firstOffset = firstPoint * 2;
  const secondOffset = secondPoint * 2;
  const firstLongitude = state.geometry[firstOffset];
  const firstLatitude = state.geometry[firstOffset + 1];
  const secondLongitude = state.geometry[secondOffset];
  const secondLatitude = state.geometry[secondOffset + 1];
  const left = gridX(Math.min(firstLongitude, secondLongitude), state);
  const right = gridX(Math.max(firstLongitude, secondLongitude), state);
  const top = gridY(Math.min(firstLatitude, secondLatitude), state);
  const bottom = gridY(Math.max(firstLatitude, secondLatitude), state);

  for (let y = top; y <= bottom; y += 1) {
    for (let x = left; x <= right; x += 1) {
      const cell = y * state.gridWidth + x;
      let list = gridLists.get(cell);
      if (!list) {
        list = [];
        gridLists.set(cell, list);
      }
      list.push(segmentId);
    }
  }
}

function buildGraph(graph) {
  const nodeCount = graph.nodes.length;
  const edgeCount = graph.edges.length;
  const geometryPointCount = graph.edges.reduce((total, edge) => total + edge[7].length, 0);
  const segmentCount = graph.edges.reduce((total, edge) => total + edge[7].length - 1, 0);
  const nodes = new Float64Array(nodeCount * 2);
  const edgeStart = new Uint32Array(edgeCount);
  const edgeEnd = new Uint32Array(edgeCount);
  const edgeLength = new Float64Array(edgeCount);
  const edgeClass = new Uint8Array(edgeCount);
  const edgeOneway = new Uint8Array(edgeCount);
  const edgeSpeed = new Float32Array(edgeCount);
  const edgeName = new Array(edgeCount);
  const geometry = new Float64Array(geometryPointCount * 2);
  const geometryCumulative = new Float64Array(geometryPointCount);
  const edgeGeometryStart = new Uint32Array(edgeCount);
  const edgeGeometryCount = new Uint32Array(edgeCount);
  let geometryCursor = 0;

  for (let index = 0; index < nodeCount; index += 1) {
    nodes[index * 2] = graph.nodes[index][0];
    nodes[index * 2 + 1] = graph.nodes[index][1];
  }

  const degree = new Uint32Array(nodeCount);
  let adjacencyCount = 0;
  for (let index = 0; index < edgeCount; index += 1) {
    const edge = graph.edges[index];
    const start = edge[0];
    const end = edge[1];
    const oneway = edge[4] === true ? 1 : edge[4] === "reverse" ? 2 : 0;
    edgeStart[index] = start;
    edgeEnd[index] = end;
    edgeLength[index] = edge[2];
    edgeClass[index] = edge[3];
    edgeOneway[index] = oneway;
    edgeSpeed[index] = speedMetersPerSecond(edge[5], edge[3]);
    edgeName[index] = edge[6];
    edgeGeometryStart[index] = geometryCursor;
    edgeGeometryCount[index] = edge[7].length;

    for (let pointIndex = 0; pointIndex < edge[7].length; pointIndex += 1) {
      const point = edge[7][pointIndex];
      const offset = geometryCursor * 2;
      geometry[offset] = point[0];
      geometry[offset + 1] = point[1];
      if (pointIndex > 0) {
        const previousOffset = offset - 2;
        geometryCumulative[geometryCursor] = geometryCumulative[geometryCursor - 1]
          + haversineMeters(
            [geometry[previousOffset], geometry[previousOffset + 1]],
            [point[0], point[1]],
          );
      }
      geometryCursor += 1;
    }

    if (oneway !== 2) {
      degree[start] += 1;
      adjacencyCount += 1;
    }
    if (oneway !== 1) {
      degree[end] += 1;
      adjacencyCount += 1;
    }
  }

  const adjacencyOffsets = new Uint32Array(nodeCount + 1);
  for (let index = 0; index < nodeCount; index += 1) {
    adjacencyOffsets[index + 1] = adjacencyOffsets[index] + degree[index];
  }
  const adjacencyEdges = new Uint32Array(adjacencyCount);
  const adjacencyNeighbors = new Uint32Array(adjacencyCount);
  const adjacencyCursor = adjacencyOffsets.slice(0, nodeCount);
  for (let index = 0; index < edgeCount; index += 1) {
    const start = edgeStart[index];
    const end = edgeEnd[index];
    if (edgeOneway[index] !== 2) {
      const position = adjacencyCursor[start]++;
      adjacencyEdges[position] = index;
      adjacencyNeighbors[position] = end;
    }
    if (edgeOneway[index] !== 1) {
      const position = adjacencyCursor[end]++;
      adjacencyEdges[position] = index;
      adjacencyNeighbors[position] = start;
    }
  }

  const state = {
    classes: graph.classes,
    nodes,
    edgeStart,
    edgeEnd,
    edgeLength,
    edgeClass,
    edgeOneway,
    edgeSpeed,
    edgeName,
    geometry,
    geometryCumulative,
    edgeGeometryStart,
    edgeGeometryCount,
    adjacencyOffsets,
    adjacencyEdges,
    adjacencyNeighbors,
    gridWidth: Math.ceil((MAX_LON - MIN_LON) / GRID_CELL_DEGREES),
    gridHeight: Math.ceil((MAX_LAT - MIN_LAT) / GRID_CELL_DEGREES),
  };

  const gridLists = new Map();
  const segmentEdges = new Uint32Array(segmentCount);
  const segmentFirstPoints = new Uint32Array(segmentCount);
  let segmentCursor = 0;
  for (let edgeIndex = 0; edgeIndex < edgeCount; edgeIndex += 1) {
    const firstPoint = edgeGeometryStart[edgeIndex];
    const pointCount = edgeGeometryCount[edgeIndex];
    for (let pointIndex = 0; pointIndex + 1 < pointCount; pointIndex += 1) {
      const point = firstPoint + pointIndex;
      segmentEdges[segmentCursor] = edgeIndex;
      segmentFirstPoints[segmentCursor] = point;
      addSegmentToGrid(gridLists, state, segmentCursor, point, point + 1);
      segmentCursor += 1;
    }
  }
  state.segmentEdges = segmentEdges;
  state.segmentFirstPoints = segmentFirstPoints;
  state.grid = new Map();
  for (const [cell, segments] of gridLists) {
    state.grid.set(cell, Uint32Array.from(segments));
  }
  return state;
}

async function ensureGraph() {
  if (!graphPromise) {
    const loadStart = performance.now();
    graphPromise = fetch(new URL("../graph/kenai-roads.json", import.meta.url))
      .then(async (response) => {
        if (!response.ok) throw new Error(`Road graph request failed (${response.status})`);
        const contentLength = response.headers.get("content-length");
        graphTransfer = {
          contentEncoding: response.headers.get("content-encoding") || "identity",
          bytesTransferred: contentLength === null ? null : Number(contentLength),
        };
        const graph = await response.json();
        graphState = buildGraph(graph);
        graphLoadTimeMs = performance.now() - loadStart;
        self.postMessage({
          type: "ready",
          loadTimeMs: graphLoadTimeMs,
          graphTransfer,
          nodeCount: graphState.nodes.length / 2,
          edgeCount: graphState.edgeStart.length,
        });
        return graphState;
      });
  }
  return graphPromise;
}

function projectToSegment(point, first, second) {
  const latitudeScale = METERS_PER_DEGREE;
  const longitudeScale = METERS_PER_DEGREE * Math.cos(point[1] * Math.PI / 180);
  const firstX = (first[0] - point[0]) * longitudeScale;
  const firstY = (first[1] - point[1]) * latitudeScale;
  const secondX = (second[0] - point[0]) * longitudeScale;
  const secondY = (second[1] - point[1]) * latitudeScale;
  const deltaX = secondX - firstX;
  const deltaY = secondY - firstY;
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

function snapToNearestEdge(point, state) {
  const originX = gridX(point[0], state);
  const originY = gridY(point[1], state);
  const maxRadius = Math.max(state.gridWidth, state.gridHeight);
  const seen = new Set();
  let best = null;

  for (let radius = 0; radius <= maxRadius; radius += 1) {
    const left = Math.max(0, originX - radius);
    const right = Math.min(state.gridWidth - 1, originX + radius);
    const top = Math.max(0, originY - radius);
    const bottom = Math.min(state.gridHeight - 1, originY + radius);
    for (let y = top; y <= bottom; y += 1) {
      for (let x = left; x <= right; x += 1) {
        if (radius > 0 && x !== left && x !== right && y !== top && y !== bottom) continue;
        const segments = state.grid.get(y * state.gridWidth + x);
        if (!segments) continue;
        for (let listIndex = 0; listIndex < segments.length; listIndex += 1) {
          const segmentIndex = segments[listIndex];
          if (seen.has(segmentIndex)) continue;
          seen.add(segmentIndex);
          const edgeIndex = state.segmentEdges[segmentIndex];
          const firstPointIndex = state.segmentFirstPoints[segmentIndex];
          const firstOffset = firstPointIndex * 2;
          const first = [state.geometry[firstOffset], state.geometry[firstOffset + 1]];
          const second = [state.geometry[firstOffset + 2], state.geometry[firstOffset + 3]];
          const projection = projectToSegment(point, first, second);
          if (!best || projection.distanceM < best.distanceM) {
            const edgeGeometryStart = state.edgeGeometryStart[edgeIndex];
            const beforeDistance = state.geometryCumulative[firstPointIndex]
              - state.geometryCumulative[edgeGeometryStart];
            const segmentLength = state.geometryCumulative[firstPointIndex + 1]
              - state.geometryCumulative[firstPointIndex];
            best = {
              edgeIndex,
              coordinate: projection.coordinate,
              distanceM: projection.distanceM,
              alongM: beforeDistance + segmentLength * projection.fraction,
            };
          }
        }
      }
    }

    if (best && point[0] >= MIN_LON && point[0] <= MAX_LON
        && point[1] >= MIN_LAT && point[1] <= MAX_LAT) {
      const west = MIN_LON + left * GRID_CELL_DEGREES;
      const east = Math.min(MAX_LON, MIN_LON + (right + 1) * GRID_CELL_DEGREES);
      const south = MIN_LAT + top * GRID_CELL_DEGREES;
      const north = Math.min(MAX_LAT, MIN_LAT + (bottom + 1) * GRID_CELL_DEGREES);
      const lonScale = METERS_PER_DEGREE * Math.cos(point[1] * Math.PI / 180);
      const boundaryDistance = Math.min(
        (point[0] - west) * lonScale,
        (east - point[0]) * lonScale,
        (point[1] - south) * METERS_PER_DEGREE,
        (north - point[1]) * METERS_PER_DEGREE,
      );
      if (boundaryDistance > best.distanceM) break;
    }
  }
  return best;
}

function pointAtDistance(edgeIndex, distanceM, state) {
  const start = state.edgeGeometryStart[edgeIndex];
  const count = state.edgeGeometryCount[edgeIndex];
  const end = start + count - 1;
  const relativeDistance = Math.max(0, Math.min(
    state.geometryCumulative[end] - state.geometryCumulative[start],
    distanceM,
  ));
  for (let pointIndex = start + 1; pointIndex <= end; pointIndex += 1) {
    const before = state.geometryCumulative[pointIndex - 1] - state.geometryCumulative[start];
    const after = state.geometryCumulative[pointIndex] - state.geometryCumulative[start];
    if (relativeDistance <= after || pointIndex === end) {
      const length = after - before;
      const fraction = length === 0 ? 0 : (relativeDistance - before) / length;
      const firstOffset = (pointIndex - 1) * 2;
      const secondOffset = pointIndex * 2;
      return [
        state.geometry[firstOffset]
          + (state.geometry[secondOffset] - state.geometry[firstOffset]) * fraction,
        state.geometry[firstOffset + 1]
          + (state.geometry[secondOffset + 1] - state.geometry[firstOffset + 1]) * fraction,
      ];
    }
  }
  return [state.geometry[end * 2], state.geometry[end * 2 + 1]];
}

function geometryBetween(edgeIndex, fromM, toM, state) {
  const start = state.edgeGeometryStart[edgeIndex];
  const end = start + state.edgeGeometryCount[edgeIndex] - 1;
  const edgeBase = state.geometryCumulative[start];
  const from = Math.max(0, Math.min(state.edgeLength[edgeIndex], fromM));
  const to = Math.max(0, Math.min(state.edgeLength[edgeIndex], toM));
  const result = [pointAtDistance(edgeIndex, from, state)];
  if (to >= from) {
    for (let pointIndex = start + 1; pointIndex < end; pointIndex += 1) {
      const vertexDistance = state.geometryCumulative[pointIndex] - edgeBase;
      if (vertexDistance > from && vertexDistance < to) {
        result.push([state.geometry[pointIndex * 2], state.geometry[pointIndex * 2 + 1]]);
      }
    }
  } else {
    for (let pointIndex = end - 1; pointIndex > start; pointIndex -= 1) {
      const vertexDistance = state.geometryCumulative[pointIndex] - edgeBase;
      if (vertexDistance < from && vertexDistance > to) {
        result.push([state.geometry[pointIndex * 2], state.geometry[pointIndex * 2 + 1]]);
      }
    }
  }
  result.push(pointAtDistance(edgeIndex, to, state));
  return result;
}

function heapPush(heap, distance, node) {
  let index = heap.nodes.length;
  heap.nodes.push(node);
  heap.distances.push(distance);
  while (index > 0) {
    const parent = (index - 1) >> 1;
    if (heap.distances[parent] <= distance) break;
    heap.nodes[index] = heap.nodes[parent];
    heap.distances[index] = heap.distances[parent];
    index = parent;
  }
  heap.nodes[index] = node;
  heap.distances[index] = distance;
}

function heapPop(heap) {
  const result = [heap.distances[0], heap.nodes[0]];
  const lastIndex = heap.nodes.length - 1;
  const lastNode = heap.nodes.pop();
  const lastDistance = heap.distances.pop();
  if (lastIndex > 0) {
    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      const right = left + 1;
      if (left >= lastIndex) break;
      const child = right < lastIndex && heap.distances[right] < heap.distances[left]
        ? right
        : left;
      if (heap.distances[child] >= lastDistance) break;
      heap.nodes[index] = heap.nodes[child];
      heap.distances[index] = heap.distances[child];
      index = child;
    }
    heap.nodes[index] = lastNode;
    heap.distances[index] = lastDistance;
  }
  return result;
}

function canTravelForward(edgeIndex, state) {
  return state.edgeOneway[edgeIndex] !== 2;
}

function canTravelReverse(edgeIndex, state) {
  return state.edgeOneway[edgeIndex] !== 1;
}

function computeRoute(startPoint, endPoint, state) {
  const startSnap = snapToNearestEdge(startPoint, state);
  const endSnap = snapToNearestEdge(endPoint, state);
  if (!startSnap || !endSnap) return { route: null, reason: "No road is close enough to route from these points." };

  const startEdge = startSnap.edgeIndex;
  const endEdge = endSnap.edgeIndex;
  const startEdgeLength = state.edgeLength[startEdge];
  const endEdgeLength = state.edgeLength[endEdge];
  const startAlong = Math.min(startEdgeLength, startSnap.alongM);
  const endAlong = Math.min(endEdgeLength, endSnap.alongM);
  const startOptions = [];
  if (canTravelReverse(startEdge, state)) {
    startOptions.push({ node: state.edgeStart[startEdge], fromM: startAlong, toM: 0 });
  }
  if (canTravelForward(startEdge, state)) {
    startOptions.push({ node: state.edgeEnd[startEdge], fromM: startAlong, toM: startEdgeLength });
  }

  const finishOptions = [];
  if (canTravelForward(endEdge, state)) {
    finishOptions.push({ node: state.edgeStart[endEdge], fromM: 0, toM: endAlong });
  }
  if (canTravelReverse(endEdge, state)) {
    finishOptions.push({ node: state.edgeEnd[endEdge], fromM: endEdgeLength, toM: endAlong });
  }

  let best = null;
  if (startEdge === endEdge) {
    if (endAlong >= startAlong && canTravelForward(startEdge, state)) {
      best = { distanceM: endAlong - startAlong, direct: { edgeIndex: startEdge, fromM: startAlong, toM: endAlong } };
    } else if (endAlong < startAlong && canTravelReverse(startEdge, state)) {
      best = { distanceM: startAlong - endAlong, direct: { edgeIndex: startEdge, fromM: startAlong, toM: endAlong } };
    }
  }

  const nodeCount = state.nodes.length / 2;
  const distances = new Float64Array(nodeCount);
  distances.fill(Infinity);
  const parentNodes = new Uint32Array(nodeCount);
  const parentEdges = new Uint32Array(nodeCount);
  const roots = new Int8Array(nodeCount);
  parentNodes.fill(NO_INDEX);
  parentEdges.fill(NO_INDEX);
  roots.fill(-1);
  const heap = { nodes: [], distances: [] };
  for (let optionIndex = 0; optionIndex < startOptions.length; optionIndex += 1) {
    const option = startOptions[optionIndex];
    const cost = Math.abs(option.toM - option.fromM);
    if (cost < distances[option.node]) {
      distances[option.node] = cost;
      roots[option.node] = optionIndex;
      heapPush(heap, cost, option.node);
    }
  }

  while (heap.nodes.length > 0) {
    const [distance, node] = heapPop(heap);
    if (distance !== distances[node]) continue;
    if (best && distance >= best.distanceM) break;

    for (const finish of finishOptions) {
      if (finish.node !== node) continue;
      const totalDistance = distance + Math.abs(finish.toM - finish.fromM);
      if (!best || totalDistance < best.distanceM) {
        best = { distanceM: totalDistance, endNode: node, finish };
      }
    }

    const first = state.adjacencyOffsets[node];
    const last = state.adjacencyOffsets[node + 1];
    for (let adjacencyIndex = first; adjacencyIndex < last; adjacencyIndex += 1) {
      const neighbor = state.adjacencyNeighbors[adjacencyIndex];
      const edgeIndex = state.adjacencyEdges[adjacencyIndex];
      const candidate = distance + state.edgeLength[edgeIndex];
      if (candidate < distances[neighbor] && (!best || candidate < best.distanceM)) {
        distances[neighbor] = candidate;
        parentNodes[neighbor] = node;
        parentEdges[neighbor] = edgeIndex;
        roots[neighbor] = roots[node];
        heapPush(heap, candidate, neighbor);
      }
    }
  }

  if (!best) return { route: null, reason: "No route exists between the selected points." };

  let chunks;
  if (best.direct) {
    chunks = [best.direct];
  } else {
    const middleChunks = [];
    let node = best.endNode;
    while (parentNodes[node] !== NO_INDEX) {
      const previous = parentNodes[node];
      const edgeIndex = parentEdges[node];
      const forward = state.edgeStart[edgeIndex] === previous;
      middleChunks.push({
        edgeIndex,
        fromM: forward ? 0 : state.edgeLength[edgeIndex],
        toM: forward ? state.edgeLength[edgeIndex] : 0,
      });
      node = previous;
    }
    middleChunks.reverse();
    const rootOption = startOptions[roots[node]];
    chunks = [
      { edgeIndex: startEdge, fromM: rootOption.fromM, toM: rootOption.toM },
      ...middleChunks,
      { edgeIndex: endEdge, fromM: best.finish.fromM, toM: best.finish.toM },
    ];
  }

  const routeGeometry = [];
  const edgeIndices = [];
  let totalDistanceM = 0;
  let estimatedTimeSeconds = 0;
  for (const chunk of chunks) {
    const distanceM = Math.abs(chunk.toM - chunk.fromM);
    const startM = totalDistanceM;
    totalDistanceM += distanceM;
    estimatedTimeSeconds += distanceM / state.edgeSpeed[chunk.edgeIndex];
    if (distanceM > 0.001) {
      edgeIndices.push({
        index: chunk.edgeIndex,
        start_m: startM,
        end_m: totalDistanceM,
      });
    }
    for (const coordinate of geometryBetween(chunk.edgeIndex, chunk.fromM, chunk.toM, state)) {
      const previous = routeGeometry[routeGeometry.length - 1];
      if (!previous || previous[0] !== coordinate[0] || previous[1] !== coordinate[1]) {
        routeGeometry.push(coordinate);
      }
    }
  }

  return {
    route: {
      totalDistanceM,
      estimatedTimeSeconds,
      geometry: routeGeometry,
      edgeIndices,
      startSnap: startSnap.coordinate,
      endSnap: endSnap.coordinate,
    },
  };
}

self.addEventListener("message", async (event) => {
  const { type, requestId, start, destination } = event.data || {};
  if (type !== "route") return;

  try {
    const state = await ensureGraph();
    const computeStart = performance.now();
    const result = computeRoute(start, destination, state);
    self.postMessage({
      type: "route-result",
      requestId,
      ...result,
      loadTimeMs: graphLoadTimeMs,
      graphTransfer,
      computeTimeMs: performance.now() - computeStart,
    });
  } catch (error) {
    self.postMessage({
      type: "route-error",
      requestId,
      message: error instanceof Error ? error.message : "Routing failed.",
    });
  }
});
