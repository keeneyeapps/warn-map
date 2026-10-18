import * as maplibregl from "../vendor/maplibre-gl.js";
import { layers, namedTheme } from "../vendor/protomaps-themes-base.js";
import { region, regionFileUrl } from "./region.js";

const protocol = new globalThis.pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const sourceName = "protomaps";

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
        url: `pmtiles://${regionFileUrl("tiles").href}`,
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
