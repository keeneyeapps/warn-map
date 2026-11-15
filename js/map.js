import * as maplibregl from "../vendor/maplibre-gl.js";
import { layers, namedTheme } from "../vendor/protomaps-themes-base.js";
import { region, regionFileUrl } from "./region.js";

const protocol = new globalThis.pmtiles.Protocol();

// The vendored PMTiles library (vendor/pmtiles.js, kept as shipped) caches each archive's header and directory reads as
// PROMISES and never removes one that failed: after a single failed or timed-out Range request every later request for the
// archive replays that same failure without touching the network, so the basemap stays blank until the page is reloaded.
// So when a tile, header or directory request fails (an abort is not a failure: the tile just scrolled away), the archive's
// entry is dropped from the Protocol, and its next request builds a fresh reader and really goes to the network again.
// (Same fix as warn-drive 1639e48. js/basemap-guard.js retries the source and offers "Map not loaded. Tap to retry".)
function archiveUrl(url) {
  return String(url).replace(/^pmtiles:\/\//, "").replace(/\/\d+\/\d+\/\d+$/, "");
}

async function pmtilesRequest(params, abortController) {
  try {
    return await protocol.tile(params, abortController);
  } catch (error) {
    if (error?.name !== "AbortError" && !abortController?.signal?.aborted) protocol.tiles.delete(archiveUrl(params?.url));
    throw error;
  }
}

maplibregl.addProtocol("pmtiles", pmtilesRequest);

const sourceName = "protomaps";

/** The basemap source's URL, as the style names it (the guard re-sets it to reload the source). */
export function basemapSourceUrl() {
  return `pmtiles://${regionFileUrl("tiles").href}`;
}

/** The basemap style in a vendored Protomaps theme ("dark" on screen, "light" for printing). */
export function basemapStyle(themeName) {
  const vendorBase = new URL("../vendor/", import.meta.url).href;
  const tiles = region.files.tiles;
  return {
    version: 8,
    glyphs: `${vendorBase}fonts/{fontstack}/{range}.pbf`,
    sprite: `${vendorBase}sprites/v4/${themeName}`,
    sources: {
      [sourceName]: {
        type: "vector",
        url: basemapSourceUrl(),
        attribution: tiles.attribution,
        maxzoom: tiles.maxzoom,
      },
    },
    layers: layers(sourceName, namedTheme(themeName), { lang: "en" }),
  };
}

export function createMap(container) {
  return new maplibregl.Map({
    container,
    style: basemapStyle("dark"),
    center: region.start.center,
    zoom: region.start.zoom,
    maxZoom: region.files.tiles.maxzoom,
    attributionControl: true,
  });
}
