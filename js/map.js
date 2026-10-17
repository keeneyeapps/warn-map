import * as maplibregl from "../vendor/maplibre-gl.js";
import { layers, namedTheme } from "../vendor/protomaps-themes-base.js";

const protocol = new globalThis.pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const sourceName = "protomaps";
const theme = namedTheme("dark");

export function createMap(container) {
  const vendorBase = new URL("../vendor/", import.meta.url).href;
  const glyphs = `${vendorBase}fonts/{fontstack}/{range}.pbf`;
  const sprite = `${vendorBase}sprites/v4/dark`;

  return new maplibregl.Map({
    container,
    style: {
      version: 8,
      glyphs,
      sprite,
      sources: {
        [sourceName]: {
          type: "vector",
          url: "pmtiles://./tiles/kenai.pmtiles",
          attribution: "© OpenStreetMap contributors",
          maxzoom: 15,
        },
      },
      layers: layers(sourceName, theme, { lang: "en" }),
    },
    center: [-151.06, 60.49],
    zoom: 10,
    maxZoom: 15,
    attributionControl: true,
  });
}