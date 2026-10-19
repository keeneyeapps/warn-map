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
FAMILY TREE (2026-10-18). WARN Map (index.html) is the TRUNK and the base for every future UI (Master,
enterprise, municipal). It has no hazard cards, no spoken alerts and no report inputs: on the tablets the
Android overlay app does all alerting, and a Map that also spoke would double up with it. Future UIs branch
from the Map and supply their own inputs and card styles.

Three public apps:
  (a) WARN Map      index.html   the trunk. Plain map: search, routes, hazards as plain popups, the trip meter
                                 behind driver mode. Loads js/app.js and only what it imports statically.
  (b) WARN Alerts   warn.html    the middle app (working name). The Map plus cards.js and alerts.js. No inputs.
                                 Its manifest id /warn-map/warn, start_url ./warn.html and scope ./warn.html
                                 are fixed so the installed app updates in place.
  (c) WARN Drive    drive.html   FUTURE, not yet created. Cards and voice plus the report inputs (js/report.js).

Module rules:
  - cards.js and alerts.js may be imported only by warn.html and drive.html. app.js loads them by dynamic
    import only when <html data-app> is "alerts" or "drive"; no static import of either may appear anywhere
    except alerts.js importing cards.js. hazards.js shows its own plain popup unless an alerts page installs
    the card renderer through setHazardPopupRenderer.
  - report.js may be imported only by drive.html. Nothing imports it today.
  - index.html must never load cards.js, alerts.js or report.js, statically or dynamically, and must never
    load anything that writes.
  - The two pages share one shell: keep their markup identical apart from <html data-app / data-app-name>,
    title, apple-mobile-web-app-title, manifest link, About heading, and the #alert-cards host (alerts pages
    only). One service worker serves both.

"Writes" means anything sent to a server other than the read-only hazard SELECT. localStorage on the device is
not a write in this sense.
-->

- Family tree: `index.html` (WARN Map) is the trunk; `warn.html` (WARN Alerts, working name) adds `cards.js` and
  `alerts.js`; the future `drive.html` (WARN Drive) adds `report.js`. `cards.js` and `alerts.js` only on
  `warn.html` and `drive.html`; `report.js` only on `drive.html`; the Map loads none of them.

## Layout

- `index.html`: full-viewport map document.
- `css/app.css`: map and control styles.
- `js/app.js`: app startup and the one-shot locate control.
- `js/config.js`: public Supabase project URL, anon key, and `REGION_FILE` (which region pack to load).
- `js/region.js`: loads the region pack at startup; other modules take paths, bbox, and start view from it.
- `js/addresses.js`: typed-address and named-place matching over the on-device address and places indexes (never queried over the network).
- `start.html`: the public entry point to share (two cards: WARN Map and WARN Alerts, plus a Share button). Static, no manifest, never installable. The root (`index.html`) stays the Map because installed phones start there. It must not mention driver mode, tariffs, or fleet features.
- `warn.html` / `warn.webmanifest`: WARN Alerts (working name), the same shell as `index.html` under its own name and manifest, plus `#alert-cards` and `data-app="alerts"`. Its manifest scope is `./warn.html` only: a scope of `./` would contain the Map's start_url, and Chrome would open Map links in an installed Drive. The Map keeps scope `./`.
- `js/hazards.js`: read-only hazard polling, GeoJSON layers, and the Map's plain popup (`setHazardPopupRenderer` lets an alerts page swap in a card).
- `js/cards.js`: hazard cards after the Android card (colour band, name, distance, age, confirmations; a disabled row for the three action buttons). Alerts pages only.
- `js/alerts.js`: spoken alerts while following (cone, 0.5 mi, once per 20 min, mute); wording after Android's AlertWording and SpokenAgePolicy. Alerts pages only.
- `js/report.js`: placeholder for WARN Drive reporting; the only module that may ever write, loaded by the future `drive.html` only.
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