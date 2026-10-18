// Route printing. MapLibre's WebGL canvas does not print, so at print time the framed map (route line included) is
// copied to an image during a render, the waypoint badges are drawn onto it, and a print-only sheet is built from
// the route plan and hazards list. The print stylesheet (css/app.css, @media print) shows only that sheet; it stays
// (hidden on screen) until the next print replaces it, so printing again or to another paper size still works.
import { region } from "./region.js";

const SHEET_ID = "print-sheet";

function captureMap(map, waypoints) {
  return new Promise((resolve) => {
    map.once("render", () => {
      const source = map.getCanvas();
      const canvas = document.createElement("canvas");
      canvas.width = source.width;
      canvas.height = source.height;
      const context = canvas.getContext("2d");
      context.drawImage(source, 0, 0);
      const scale = source.width / source.clientWidth;
      for (const { point, badge, colour, text } of waypoints) {
        const { x, y } = map.project(point);
        const radius = 11 * scale;
        context.beginPath();
        context.arc(x * scale, y * scale, radius, 0, Math.PI * 2);
        context.fillStyle = colour;
        context.fill();
        context.lineWidth = 2 * scale;
        context.strokeStyle = "#171b1d";
        context.stroke();
        context.fillStyle = text;
        context.font = `700 ${11 * scale}px "Noto Sans", "Segoe UI", sans-serif`;
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(badge, x * scale, y * scale + 0.5 * scale);
      }
      resolve(canvas.toDataURL("image/png"));
    });
    map.triggerRepaint();
  });
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
 * Builds the print sheet and opens the print dialog. `waypoints` are { point: [lon, lat], badge, colour, text };
 * `plan` and `hazardsList` are the live route plan and hazards list elements; `hazardsTitle` their heading text.
 */
export async function printRoute({ map, waypoints, plan, hazardsTitle, hazardsList, print = () => globalThis.print() }) {
  const image = await captureMap(map, waypoints);
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
