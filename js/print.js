// Route printing. MapLibre's WebGL canvas does not print, so at print time the route is rendered onto a hidden,
// offscreen map in the Protomaps light theme (framed exactly like the on-screen map, which keeps its dark theme),
// captured to an image with the waypoint badges drawn on, and a print-only sheet is built from the route plan and
// hazards list. The print stylesheet (css/app.css, @media print) shows only that sheet; it stays (hidden on screen)
// until the next print replaces it, so printing again or to another paper size still works.
import * as maplibregl from "../vendor/maplibre-gl.js";
import { basemapStyle } from "./map.js";
import { region } from "./region.js";

const SHEET_ID = "print-sheet";
const RENDER_TIMEOUT_MS = 20000;
// Colours that read on the light basemap and on paper.
const PRINT_ROUTE = { casing: "#ffffff", line: "#0b6b4f" };
const PRINT_BADGES = {
  start: { fill: "#d9480f", text: "#ffffff" },
  stop: { fill: "#343a40", text: "#ffffff" },
  destination: { fill: "#0b6b4f", text: "#ffffff" },
};

function waitForIdle(map) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, RENDER_TIMEOUT_MS);
    map.once("idle", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

// Renders the route on an offscreen light-theme map the same size and view as `map`; returns a PNG data URL.
async function renderLightMap(map, routeGeometry, waypoints) {
  const source = map.getContainer();
  const container = document.createElement("div");
  container.className = "print-render-map";
  container.setAttribute("aria-hidden", "true");
  container.style.width = `${source.clientWidth}px`;
  container.style.height = `${source.clientHeight}px`;
  document.body.append(container);
  const printMap = new maplibregl.Map({
    container,
    style: basemapStyle("light"),
    center: map.getCenter(),
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
    interactive: false,
    attributionControl: false,
    preserveDrawingBuffer: true,
    fadeDuration: 0,
    pixelRatio: Math.max(2, globalThis.devicePixelRatio || 1),
  });
  try {
    await new Promise((resolve) => printMap.once("load", resolve));
    printMap.addSource("print-route", {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "LineString", coordinates: routeGeometry }, properties: {} },
    });
    printMap.addLayer({
      id: "print-route-casing",
      type: "line",
      source: "print-route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": PRINT_ROUTE.casing, "line-width": 9 },
    });
    printMap.addLayer({
      id: "print-route-line",
      type: "line",
      source: "print-route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": PRINT_ROUTE.line, "line-width": 5 },
    });
    await waitForIdle(printMap);

    const canvas = printMap.getCanvas();
    const output = document.createElement("canvas");
    output.width = canvas.width;
    output.height = canvas.height;
    const context = output.getContext("2d");
    context.drawImage(canvas, 0, 0);
    const scale = canvas.width / canvas.clientWidth;
    for (const { point, badge, kind } of waypoints) {
      const colours = PRINT_BADGES[kind] || PRINT_BADGES.stop;
      const { x, y } = printMap.project(point);
      context.beginPath();
      context.arc(x * scale, y * scale, 11 * scale, 0, Math.PI * 2);
      context.fillStyle = colours.fill;
      context.fill();
      context.lineWidth = 2.5 * scale;
      context.strokeStyle = "#ffffff";
      context.stroke();
      context.fillStyle = colours.text;
      context.font = `700 ${11 * scale}px "Noto Sans", "Segoe UI", sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(badge, x * scale, y * scale + 0.5 * scale);
    }
    return output.toDataURL("image/png");
  } finally {
    printMap.remove();
    container.remove();
  }
}

function sanitisedCopy(element) {
  const copy = element.cloneNode(true);
  copy.removeAttribute("id");
  for (const node of copy.querySelectorAll("[id]")) node.removeAttribute("id");
  // Controls have no place on paper: drag handles, remove buttons, Share/Print, avoid toggles, map links.
  for (const node of copy.querySelectorAll(
    ".route-leg-handle, .route-leg-remove, .route-plan-actions, .route-hazard-avoid, .route-avoid-button, button:empty",
  )) node.remove();
  return copy;
}

function paragraph(text, className) {
  const node = document.createElement("p");
  node.className = className;
  node.textContent = text;
  return node;
}

/**
 * Builds the print sheet and opens the print dialog. `routeGeometry` is the active route's [lon, lat] line;
 * `waypoints` are { point: [lon, lat], badge, kind: "start" | "stop" | "destination" }; `plan` and `hazardsList`
 * are the live route plan and hazards list elements; `hazardsTitle` their heading text.
 */
export async function printRoute({
  map, routeGeometry, waypoints, plan, hazardsTitle, hazardsList, print = () => globalThis.print(),
}) {
  const image = await renderLightMap(map, routeGeometry, waypoints);
  document.getElementById(SHEET_ID)?.remove();

  const sheet = document.createElement("section");
  sheet.id = SHEET_ID;
  sheet.className = "print-sheet";
  sheet.setAttribute("aria-hidden", "true");

  const first = document.createElement("div");
  first.className = "print-first-page";
  const title = document.createElement("h1");
  title.textContent = "WARN Map route";
  const picture = document.createElement("img");
  picture.className = "print-map";
  picture.alt = "Map of the route";
  picture.src = image;
  first.append(
    title,
    paragraph(new Date().toLocaleString(undefined, { dateStyle: "long", timeStyle: "short" }), "print-date"),
    picture,
  );

  const legsHeading = document.createElement("h2");
  legsHeading.textContent = "Route";
  const hazardsHeading = document.createElement("h2");
  hazardsHeading.className = "print-hazards-heading";
  hazardsHeading.textContent = hazardsTitle;

  const addresses = region.files.addresses?.source;
  const notices = document.createElement("footer");
  notices.className = "print-notices";
  notices.append(
    paragraph(`Map data ${region.files.tiles.attribution}, ODbL.`, "print-notice"),
    ...(addresses
      ? [paragraph(`Address data: ${addresses.name}. ${addresses.licence}`, "print-notice")]
      : []),
  );

  sheet.append(first, legsHeading, sanitisedCopy(plan), hazardsHeading, sanitisedCopy(hazardsList), notices);
  document.body.append(sheet);
  await picture.decode().catch(() => {});
  print();
  return sheet;
}
