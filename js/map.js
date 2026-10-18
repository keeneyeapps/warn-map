import * as maplibregl from "../vendor/maplibre-gl.js";
import { layers, namedTheme } from "../vendor/protomaps-themes-base.js";
import { region, regionFileUrl } from "./region.js";

const protocol = new globalThis.pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const sourceName = "protomaps";
const theme = namedTheme("dark");

export function createMap(container) {
  const vendorBase = new URL("../vendor/", import.meta.url).href;
  const glyphs = `${vendorBase}fonts/{fontstack}/{range}.pbf`;
  const sprite = `${vendorBase}sprites/v4/dark`;
  const tiles = region.files.tiles;

  return new maplibregl.Map({
    container,
    style: {
      version: 8,
      glyphs,
      sprite,
      sources: {
        [sourceName]: {
          type: "vector",
          url: `pmtiles://${regionFileUrl("tiles").href}`,
          attribution: tiles.attribution,
          maxzoom: tiles.maxzoom,
        },
      },
      layers: layers(sourceName, theme, { lang: "en" }),
    },
    center: region.start.center,
    zoom: region.start.zoom,
    maxZoom: tiles.maxzoom,
    attributionControl: true,
  });
}