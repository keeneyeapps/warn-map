# WARN Map Rules

- This is a public, read-only PWA, separate from the Android WARN app and any admin UI.
- The only server the app may contact is the WARN Supabase project, and only with `SELECT` on the `public_hazards` view using the anon key. Nothing is ever written.
- Browser geolocation never leaves the device; used only by the map and on-device routing.
- No third-party requests at runtime. Map tiles, fonts, sprites, JavaScript, and CSS are served from this repository. Do not use CDNs.
- There is no build step. Use plain HTML, CSS, and ES modules; GitHub Pages serves the repository as-is.
- Work one step at a time. Implement only what the current prompt requests.
- Region pack: everything region-specific (bbox, start view, data file paths, sources, address field mapping, hazard region_code, wildlife) lives in `regions/<id>/region.json`, and `js/config.js` names the one region file to load. Nothing region-specific belongs in code; read it from the region file.
- Hazards are never filtered by region: the region's `region_code` and bbox are informational and never scope hazard visibility.
- Phone numbers in the places index come only from public business and facility records (OSM business/facility tags and Borough facility layers), never from residential or address data. A phone is shown only as a tap-to-call link for a place the person chose; nothing dials or sends on its own.

## Layout

- `index.html`: full-viewport map document.
- `css/app.css`: map and control styles.
- `js/app.js`: app startup and the one-shot locate control.
- `js/config.js`: public Supabase project URL, anon key, and `REGION_FILE` (which region pack to load).
- `js/region.js`: loads the region pack at startup; other modules take paths, bbox, and start view from it.
- `js/addresses.js`: typed-address and named-place matching over the on-device address and places indexes (never queried over the network).
- `js/hazards.js`: read-only hazard polling, GeoJSON layers, and popups.
- `js/map.js`: MapLibre, PMTiles, local theme assets, and map setup.
- `vendor/`: locally served libraries, glyphs, and sprites.
- `regions/kenai/region.json`: the Kenai region pack.
- `sw.js`: service worker; app shell from `js/precache-list.js`, region data files from the region pack.
- `tiles/kenai.pmtiles`: extracted Kenai Peninsula basemap; rebuild with `tools/build_tiles.py --region`.
- `graph/kenai-roads.json`: offline road graph; rebuild with `tools/build_graph.py --region` from the ignored Geofabrik extract.
- `addresses/kenai-addresses.json`: offline address index (street, number, unit, postal city, dispatch community, point only); rebuild with `tools/build_addresses.py --region … --fetch`.
- `places/kenai-places.json`: offline named-places index (name, category, type label, city, street address, public phone, website, point only); rebuild with `tools/build_places.py --region … --fetch` after the graph and address index.
- `docs/screenshots/`: local verification screenshots.