// The region pack named in config.js: bbox, start view, and the data files the map, routing, and address
// lookup read. Loaded once at startup (precached by the service worker); no module names a region itself.
import { REGION_FILE } from "./config.js";

const APP_ROOT = new URL("../", import.meta.url);

export const REGION_URL = new URL(REGION_FILE, APP_ROOT);

const response = await fetch(REGION_URL);
if (!response.ok) throw new Error(`Region file request failed (${response.status})`);

export const region = await response.json();

// Absolute URL of a region data file (paths in region.json are relative to the app root), or null if unset.
export function regionFileUrl(name) {
  const path = region.files?.[name]?.path;
  return path ? new URL(path, APP_ROOT) : null;
}

// Each page names its app on <html data-app-name> (WARN Map or WARN Drive).
document.title = `${document.documentElement.dataset.appName || "WARN Map"} | ${region.name}`;
document.querySelector("#map")?.setAttribute("aria-label", `Map of the ${region.name}`);
