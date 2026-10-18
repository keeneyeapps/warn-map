# WARN Map Rules

- This is a public, read-only PWA, separate from the Android WARN app and any admin UI.
- The only server the app may contact is the WARN Supabase project, and only with `SELECT` on the `public_hazards` view using the anon key. Nothing is ever written.
- Browser geolocation never leaves the device; used only by the map and on-device routing.
- No third-party requests at runtime. Map tiles, fonts, sprites, JavaScript, and CSS are served from this repository. Do not use CDNs.
- There is no build step. Use plain HTML, CSS, and ES modules; GitHub Pages serves the repository as-is.
- Work one step at a time. Implement only what the current prompt requests.

## Layout

- `index.html`: full-viewport map document.
- `css/app.css`: map and control styles.
- `js/app.js`: app startup and the one-shot locate control.
- `js/config.js`: public Supabase project URL and anon key.
- `js/hazards.js`: read-only hazard polling, GeoJSON layers, and popups.
- `js/map.js`: MapLibre, PMTiles, local theme assets, and map setup.
- `vendor/`: locally served libraries, glyphs, and sprites.
- `tiles/kenai.pmtiles`: extracted Kenai Peninsula basemap.
- `graph/kenai-roads.json`: offline road graph; rebuild with `tools/build_graph.py` from the ignored Geofabrik extract.
- `docs/screenshots/`: local verification screenshots.