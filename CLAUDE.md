# WARN Map Rules

- This is a public, read-only PWA, separate from the Android WARN app and any admin UI.
- The only server the app may contact is the WARN Supabase project, and only with `SELECT` on the `public_hazards` view using the anon key. Nothing is ever written.
- Browser geolocation never leaves the device; used only by the map and on-device routing.
- No third-party requests at runtime. Map tiles, fonts, sprites, JavaScript, and CSS are served from this repository. Do not use CDNs.
- There is no build step. Use plain HTML, CSS, and ES modules; GitHub Pages serves the repository as-is.
- Work one step at a time. Implement only what the current prompt requests.
- Region pack: everything region-specific (bbox, start view, data file paths, sources, address field mapping, hazard region_code, wildlife) lives in `regions/<id>/region.json`, and `js/config.js` names the one region file to load. Nothing region-specific belongs in code; read it from the region file.
- Hazards are never filtered by region: the region's `region_code` and bbox are informational and never scope hazard visibility.
- `places_overrides` in region.json may set a place's phone or point; each entry carries a `source` note, and a phone must come from the business's or facility's own public listing.
- Phone numbers in the places index come only from public business and facility records (OSM business/facility tags and Borough facility layers), never from residential or address data. A phone is shown only as a tap-to-call link for a place the person chose; nothing dials or sends on its own.

## Pages and modules

<!--
WHICH PAGE MAY LOAD WHICH MODULES. Both pages share one shell (keep their markup identical apart from title,
data-app-name, manifest, and About heading), one service worker, and one module graph rooted at js/app.js.

  index.html  WARN Map    read-only forever. Loads js/app.js and only what it imports: config, region, map,
                          hazards (SELECT on public_hazards only), cards, alerts, routing, routing-worker,
                          route-hazards, addresses, driver (localStorage only), print. It must NEVER load
                          js/report.js or any module that writes to the WARN database, directly or indirectly.
  warn.html   WARN Drive  everything index.html loads, plus (in a later step) js/report.js, the ONLY module that
                          may write. report.js is an empty placeholder until then and no shared module may import it.

"Writes" means anything sent to a server other than the read-only hazard SELECT. localStorage on the device is
not a write in this sense. A new shared module must not import report.js; anything that writes goes in report.js.
-->

- `index.html` (WARN Map) and `warn.html` (WARN Drive) load the same shared modules; only `warn.html` may ever load `js/report.js`, and `index.html` must never load anything that writes.

## Layout

- `index.html`: full-viewport map document.
- `css/app.css`: map and control styles.
- `js/app.js`: app startup and the one-shot locate control.
- `js/config.js`: public Supabase project URL, anon key, and `REGION_FILE` (which region pack to load).
- `js/region.js`: loads the region pack at startup; other modules take paths, bbox, and start view from it.
- `js/addresses.js`: typed-address and named-place matching over the on-device address and places indexes (never queried over the network).
- `start.html`: the public entry point to share (two cards: WARN Map and WARN Drive, plus a Share button). Static, no manifest, never installable. The root (`index.html`) stays the Map because installed phones start there. It must not mention driver mode, tariffs, or fleet features.
- `warn.html` / `warn.webmanifest`: WARN Drive, the same shell as `index.html` under its own name and manifest.
- `js/hazards.js`: read-only hazard polling and GeoJSON layers; its popups are hazard cards.
- `js/cards.js`: hazard cards after the Android card (colour band, name, distance, age, confirmations; a disabled row for the three action buttons).
- `js/alerts.js`: spoken alerts while following (cone, 0.5 mi, once per 20 min, mute); wording after Android's AlertWording and SpokenAgePolicy.
- `js/report.js`: placeholder for WARN Drive reporting; the only module that may ever write, loaded by `warn.html` only.
- `js/print.js`: route printing; captures the map canvas at print time and builds the print-only sheet (`@media print` in `css/app.css`).
- `js/driver.js`: driver tools (tariff, planner fare, trip meter, saved trips). Hidden unless driver mode is on (`?driver` once, or 7 taps on the About version line); with it off, no fare, tariff, or trip UI may exist in the DOM. Tariff and trip data stay in localStorage only.
- `js/map.js`: MapLibre, PMTiles, local theme assets, and map setup.
- `vendor/`: locally served libraries, glyphs, and sprites.
- `regions/kenai/region.json`: the Kenai region pack.
- `sw.js`: service worker; app shell from `js/precache-list.js`, region data files from the region pack.
- `tiles/kenai.pmtiles`: extracted Kenai Peninsula basemap; rebuild with `tools/build_tiles.py --region`.
- `graph/kenai-roads.json`: offline road graph; rebuild with `tools/build_graph.py --region` from the ignored Geofabrik extract.
- `addresses/kenai-addresses.json`: offline address index (street, number, unit, postal city, dispatch community, point only); rebuild with `tools/build_addresses.py --region … --fetch`.
- `places/kenai-places.json`: offline named-places index (name, category, type label, city, street address, public phone, website, point only); rebuild with `tools/build_places.py --region … --fetch` after the graph and address index.
- `icons/`: app icons built by `tools/build_icons.py` from Luna Montgomery's moose-head master (kept in the Android repo). The moose artwork is © Luna Montgomery, all rights reserved, used by permission; see `LICENSE`. Never copy the master or the artwork into other assets without that permission.
- `LICENSE`: PolyForm Noncommercial 1.0.0 for the code (the same licence and Required Notice wording as the Android WARN repo), then the artwork Required Notice and the data notices. Keep the Required Notice lines identical in LICENSE, README.md, and the About card in index.html.
- About card (behind the gear): its version is read from the service worker's cache name, so bumping `CACHE_NAME` in `sw.js` is the only version bump.
- `docs/screenshots/`: local verification screenshots.