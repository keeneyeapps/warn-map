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

The apps (2026-10-19: the trip meter left the Map for its own page):
  (a) WARN Map      index.html   the trunk. A plain map: search, routes, hazards as plain popups. No meter, no
                                 fare, no tariff, no trips, no unlock. Loads js/app.js and only what it imports
                                 statically.
  (b) WARN Alerts   warn.html    Map + cards.js + alerts.js. No inputs. Its manifest id /warn-map/warn,
                                 start_url ./warn.html and scope ./warn.html are fixed so the installed app
                                 updates in place.
  (c) WARN Meter    meter.html   UNLISTED. Map + driver.js (and trace.js through it): the trip meter, tariff
                                 fields, Trips list with trace export/delete, and the planner fare line. Always
                                 on; the page itself is the opt-in, so there is no unlock. Manifest id
                                 /warn-map/meter, start_url and scope ./meter.html. Linked from nowhere (not
                                 start.html, not the Map, not Alerts) and mentioned in no other page's text or
                                 metadata; it is precached by the service worker and served from its own cached
                                 copy like warn.html. Shared by URL only.
  (d) WARN Drive    drive.html   FUTURE, not yet created. Alerts + the report inputs (js/report.js).

Module rules:
  - cards.js and alerts.js may be imported only by warn.html and drive.html. app.js loads them by dynamic
    import only when <html data-app> is "alerts" or "drive"; no static import of either may appear anywhere
    except alerts.js importing cards.js. hazards.js shows its own plain popup unless an alerts page installs
    the card renderer through setHazardPopupRenderer.
  - report.js may be imported only by drive.html. Nothing imports it today.
  - driver.js (and trace.js, which only driver.js imports) may be loaded only by meter.html, or by a future UI
    that explicitly opts in here. app.js loads it by dynamic import only when <html data-app> is "meter"; no
    static import of it may appear anywhere. routing.js never imports it: the planner fare line exists only
    after the meter page installs a formatter (setFareFormatter), so no stored flag can turn it on elsewhere.
  - index.html must never load cards.js, alerts.js, report.js or driver.js, statically or dynamically, and
    must never load anything that writes.
  - No page other than meter.html may mention trips, fares, tariffs or a meter, in markup, text or metadata
    (tests: step8, meter, start). The old driver-mode flag (`warn-map-driver-mode`) and `?driver` query are
    gone and ignored; the stored trips, tariff and trace keys are unchanged, so existing data carries over to the
    meter page.
  - The pages share one shell: keep their markup identical apart from <html data-app / data-app-name>,
    title, apple-mobile-web-app-title, manifest link, About heading, and the #alert-cards host (alerts pages
    only). One service worker serves all of them.

"Writes" means anything sent to a server other than the read-only hazard SELECT. localStorage on the device is
not a write in this sense.
-->

- Family tree: `index.html` (WARN Map) is the trunk. `meter.html` (WARN Meter, unlisted) = Map + `driver.js`.
  `warn.html` (WARN Alerts, working name) = Map + `cards.js` + `alerts.js`. The future `drive.html` (WARN Drive)
  = Alerts + inputs + `report.js`. `driver.js` only on `meter.html` (or a future UI that opts in); `cards.js` and
  `alerts.js` only on `warn.html` and `drive.html`; `report.js` only on `drive.html`; the Map loads none of
  them. No page other than the meter page mentions trips or fares.

## Layout

- `index.html`: full-viewport map document.
- `css/app.css`: map and control styles.
- `js/app.js`: app startup and the one-shot locate control.
- `js/config.js`: public Supabase project URL, anon key, and `REGION_FILE` (which region pack to load).
- `js/region.js`: loads the region pack at startup; other modules take paths, bbox, and start view from it.
- `js/addresses.js`: typed-address and named-place matching over the on-device address and places indexes (never queried over the network).
- `start.html`: the public entry point to share (two cards: WARN Map and WARN Alerts, plus a Share button). Static, no manifest, never installable. The root (`index.html`) stays the Map because installed phones start there. It must not mention driver mode, tariffs, fleet features, or the meter page (no link to meter.html, none of the words driver, tariff, fare, taxi, fleet or meter).
- `warn.html` / `warn.webmanifest`: WARN Alerts (working name), the same shell as `index.html` under its own name and manifest, plus `#alert-cards` and `data-app="alerts"`. Its manifest scope is `./warn.html` only: a scope of `./` would contain the Map's start_url, and Chrome would open Map links in an installed Drive. The Map keeps scope `./`.
- `js/hazards.js`: read-only hazard polling, GeoJSON layers, and the Map's plain popup (`setHazardPopupRenderer` lets an alerts page swap in a card).
- `js/cards.js`: hazard cards after the Android card (colour band, name, distance, age, confirmations; a disabled row for the three action buttons). Alerts pages only.
- `js/alerts.js`: spoken alerts while following (cone, 0.5 mi, once per 20 min, mute); wording after Android's AlertWording and SpokenAgePolicy. Alerts pages only.
- `js/report.js`: placeholder for WARN Drive reporting; the only module that may ever write, loaded by the future `drive.html` only.
- `js/print.js`: route printing; captures the map canvas at print time and builds the print-only sheet (`@media print` in `css/app.css`).
- Header band (`#app-header` on `index.html`, `warn.html`, `meter.html`; not `start.html`): a slim black band (`--brand-black` #000) with yellow text and accents (`--brand-yellow` #FDD835, both the Android app's `CautionStripe.kt` values), the unchanged `icons/home-moose.webp`, "WARN" in bold, then the variant name from `<html data-app-name>` filled by `js/header.js`; at most 44 px (36 px plus the safe-area inset), the map and every top-anchored control start below `--header-offset`, hidden in print.
- `js/driver.js`: the driver tools (tariff, planner fare line, trip meter, saved trips), loaded by `app.js` on `meter.html` only and always on there. No other page loads it, so no fare, tariff, or trip UI exists anywhere else. Fares follow a cab meter: 1/14-mile steps plus $0.50 per full cumulative minute of wait. Tariff and trip data stay in localStorage only (keys unchanged from the Map's driver mode).
- `meter.html` / `meter.webmanifest`: WARN Meter, the same shell as `index.html` under its own name and manifest, `data-app="meter"`. Unlisted: never linked from or mentioned by any other page. Its manifest scope is `./meter.html` only.
- `js/trace.js`: the per-trip GPS trace, used by `driver.js` only. One CSV row per fix the trip meter saw (accepted or rejected, with the reject reason, the straight and snapped metres, metres added and wait added), for calibrating the meter against a real cab meter. The last 10 trips, at most 20,000 rows each, are kept in localStorage on the device (`warn-map-traces`, the running trip in `warn-map-trace-running`), apart from the 200-trip list. It is never uploaded or sent anywhere. It leaves the device only when the driver taps "Export trace" (a CSV saved locally); "Delete traces" removes all of them, and "Clear all" trips removes them too. The note saying so is in the meter page's Trips section, above the About card. The trip record also keeps the exact unrounded distance (`metersExact`) and wait (`waitSecondsExact`) beside the rounded figures. A change to what the trace stores needs the privacy re-read: the published policy page (keeneyeapps.github.io/warn/privacy.html) does not yet mention trip traces. [GAP] it is in another repo and has not been edited.
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
- `tests/`: the Playwright browser suite. `python tests
un_all.py` runs it (about six minutes); output goes to the git-ignored `tests/out/`. See `tests/README.md`. Run it before committing page or module changes.
- `docs/screenshots/`: local verification screenshots.