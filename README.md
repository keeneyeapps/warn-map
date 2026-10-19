WARN Map is a public, read-only PWA for viewing current WARN road hazards on a map. It displays a self-hosted Protomaps basemap of one region (currently the Kenai Peninsula) and refreshes live hazards from the public `public_hazards` view every 60 seconds.

The app is read-only and may only query the WARN Supabase `public_hazards` view using its anon key. Browser geolocation never leaves the device; used only by the map and on-device routing. All runtime assets and tiles are self-hosted, there are no CDNs, and there is no build step.

## The family of pages

`index.html` is WARN Map, the trunk: a plain map with search, routes and hazards as plain popups. It has no hazard cards, no spoken alerts and no report inputs, because on the tablets the Android overlay app does the alerting and a map that also spoke would double up with it. Every future UI branches from the Map.

`warn.html` is WARN Alerts (working name): the Map plus hazard cards and spoken alerts, read-only, under its own name and manifest (`warn.webmanifest`). `drive.html`, WARN Drive, does not exist yet; it will add the report inputs through `js/report.js`, the only module that may ever write.

Hazard cards follow the Android app's card: a category colour band, the hazard's name, its distance, when it was last seen, and its confirmation count. The Confirm / Mute / Clear row is a disabled placeholder until reporting exists. While following, a hazard within ±30° of the direction of travel and within 0.5 mi gets its card at the top of the screen and one spoken sentence ("Caution, Moose reported 0.4 miles away, last seen 5 minutes ago."), at most once every 20 minutes; pedestrians and wildlife are "Caution", everything else "Note". Wording follows the Android app's AlertWording and SpokenAgePolicy, so distances read "away" (not "ahead") until the Android heading calibration flag is on, and only the types Android lets say their age do. The Voice button in the status strip mutes speech only; cards still show.

## Licence

Required Notice: Copyright 2026 Keen Eye Apps (https://keeneyeapps.github.io). Licensed under PolyForm Noncommercial 1.0.0.

The code is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE), the same licence as the WARN Android app. The moose-head artwork is excluded (see Icon below), and the data files keep their own terms (see `LICENSE` and the sections below).

## Region pack

Everything region-specific lives in `regions/<id>/region.json`, and `js/config.js` names the one region file the app loads (`REGION_FILE`). The region file holds the id and display name, the bbox, the start view, the data files (basemap tiles, road graph, address index, and named-places index) with their source URLs and licence notes, the address source's field mapping, the `region_code` the WARN database uses for the area, and the expected wildlife. The code reads paths, bbox, and start view from it, and the service worker builds its data precache list from it. Data paths in `region.json` are relative to the repository root.

The `hazards.region_code` entry is informational. The app shows every row of `public_hazards` and never filters hazards by region code or by the bbox. The `wildlife` section (wild species and livestock expected in the region, notes on how some are usually reported, the quick-button species, and an optional button icon path, empty to keep the default moose logo) is not read by the map yet.

## Basemap tiles

`tiles/kenai.pmtiles` is an extract of a Protomaps daily basemap build (OpenStreetMap, ODbL; Natural Earth). To rebuild it, put the [pmtiles CLI](https://github.com/protomaps/go-pmtiles/releases) on your PATH or at `tools/pmtiles.exe`, set `files.tiles.source.url` to a current build from <https://maps.protomaps.com/builds/>, then run `python tools/build_tiles.py --region regions/kenai/region.json`. It extracts the region's bbox up to `files.tiles.maxzoom`; add `--dry-run` to see the tile count without downloading.

## Route preference

Behind the gear, "Route by" chooses how the on-device router weighs roads: Fastest (the default) minimises the estimated time from each road's `maxspeed`, or a default speed for its class; Main roads is Fastest with residential, service, track, and living-street roads counted three times as costly, so arterials win unless there is no other way; Shortest minimises distance. The choice is stored in the browser and the route panel names the one in use.

## Route alternatives

After the chosen route, the router looks for up to two alternatives by searching again with every edge of the routes already found counted 1.6 times as costly. An alternative is kept only if at least 25% of its length is on roads the routes already shown do not use, and it is no more than 40% longer than the best route. Alternatives use the same route preference and avoided closures. They are drawn as thinner dashed lines under the route, each labelled with its distance and minutes; tapping one (or its label) makes it the active route. Hazards along the route, the route panel and share text always describe the active route.

## Wide screens and printing

From 900 px wide, the route bar, status strip, route panel, and hazards list sit in a 380 px left column and the map fills the rest; settings and About open as a sheet over the column. Narrower screens keep the phone layout.

With a route showing, Print (beside Share) opens the browser's print dialog. The first page is the map as currently framed, rendered at print time on an offscreen map in the Protomaps light theme (the on-screen map keeps its dark theme) with the route and its waypoints in colours that read on white, under the title "WARN Map route" and the date; the next page lists the legs with their distances and the total, then the reported hazards along the route, then the OpenStreetMap attribution and the Borough address-data notice. Both Letter and A4 fit; `docs/screenshots/print-sample.pdf` is a sample.

## Road graph

`graph/kenai-roads.json` is a compact, offline road network for on-device routing. It is derived from OpenStreetMap data under the ODbL, downloaded from Geofabrik. To rebuild it, install pyosmium with `python -m pip install osmium`, then run `python tools/build_graph.py --region regions/kenai/region.json --fetch`, which downloads the region's extract (`files.graph.source`) to the ignored `tools/` folder and clips it to the bbox. Leave out `--fetch` to reuse an extract already downloaded.

## Address index

`addresses/kenai-addresses.json` is an offline address index for destination entry, built from the Kenai Peninsula Borough's KPB Physical Addresses (E911 address points, KPB GeoHub). It keeps only house number, street, unit, postal city, the Borough's dispatch community (so either name matches), and a point; no other field from the source is kept. The index is for locating a destination on the map; it is not an authoritative address record. The Borough provides the data without warranty and asks users to accept that it assumes no liability for its use. To rebuild it, run `python tools/build_addresses.py --region regions/kenai/region.json --fetch`, which downloads the points to the ignored `tools/kpb-physical-addresses.geojson`; add `--check-alignment` to report address-to-road distances against the road graph.

## Places

`places/kenai-places.json` is an offline index of named places (businesses, services, government offices, schools, health and emergency facilities, lodging, parks, and landmarks) for destination entry. Typed text matches the start of any word in a place name, and a place shows as "Central Peninsula Hospital · Hospital, Soldotna". Places whose name is or starts with the typed text rank above streets; addresses for a typed house number still rank first.

Sources:

- OpenStreetMap (ODbL), from the same Geofabrik extract as the road graph: named nodes, and the centre of named ways and areas, inside the region bbox that carry an `amenity`, `shop`, `tourism`, `office`, `healthcare`, `leisure`, `aeroway`, `public_transport`, or `emergency` tag, or `man_made=lighthouse|pier`. Benches, waste baskets, vending machines, parking, toilets, hydrants and similar street furniture are skipped, as is anything unnamed.
- Kenai Peninsula Borough GeoHub layers KPB Facilities, KPB Boat Launches, KPB Parks, and KPB Campgrounds. The Borough provides them without warranty and asks users to accept that it assumes no liability for their use (the same terms as the address points); they do not restrict redistribution. Facility categories that are not destinations (sirens, levees, fill sites, reservoirs, water towers, firefighting water resources) are dropped.

Each place keeps only its name, a category (health, school, government, emergency, store, food, lodging, fuel, transport, park, landmark, other), a type label such as "Hospital" or "Grocery", the city (its `addr:city`, else the postal city of the nearest address point within 5 km), a street address when OSM has `addr:*` tags, a phone number when tagged (`phone` or `contact:phone`, normalised to +1-NNN-NNN-NNNN), a website when tagged, and a point. Opening hours and every other tag are dropped. A place with the same name and category as another within 50 m is a duplicate; the GeoHub record is kept and takes the OSM record's phone, website, or address if it has none. Phone numbers come only from these public business and facility records; the map shows them as a tap-to-call link on a place chosen for the route and dials nothing on its own.

The region's optional `boundary` polygon marks its core area (for Kenai, the hull of the Borough's address points pushed out 3 km, printed by `python tools/build_boundary.py --region regions/kenai/region.json`). Places outside it are suggested only when fewer than 3 matches lie inside it, and always after them, so "walmart" near Kenai lists the Kenai store before any in Anchorage or Wasilla. The boundary never affects hazards.

`places_overrides` in `region.json` corrects the built index at load time: each entry names a place (matched ignoring case) and gives a `phone` and/or `point` to use instead, with a `source` note. Central Peninsula Hospital's main number, (907) 714-4404, comes from the hospital's own website.

Tapping the map within 30 m of an indexed place opens that place's popup (name, type, call link if it has a phone, and Route here). When a place popup would sit under the route panel, the panel folds to its header and unfolds again when the popup closes.

To rebuild it, run `python tools/build_places.py --region regions/kenai/region.json --fetch` after the road graph and address index (it reads the OSM extract the graph script downloads, and the address index for cities). Leave out `--fetch` to reuse the GeoHub layers already downloaded.

## Adding a region

1. Copy `regions/kenai` to `regions/<id>`.
2. Edit `regions/<id>/region.json`: id, name, bbox, start view, output paths for the tiles, graph, address index, and places, their sources, the address and place-layer field mappings, the hazard `region_code`, and the wildlife list. The address builder reads ArcGIS feature services; set `street_includes_number` to false if the street field has no house number in it.
3. Run the build scripts with `--region regions/<id>/region.json`: `build_tiles.py`, `build_graph.py --fetch`, `build_addresses.py --fetch`, then `build_places.py --fetch`.
4. Point `REGION_FILE` in `js/config.js` at the new region file.

No code changes are needed.

## Icon

The app icons in `icons/` show the moose-head artwork, which is © Luna Montgomery, all rights reserved, used by permission; it is not covered by any licence for the rest of this repository (see `LICENSE`). The share page (`start.html`) also shows the full moose head as a faint background, `icons/home-moose.webp`, copied unchanged from the Android app's home screen (the same artwork and the same credit). The road pattern and hazard-stripe border are drawn by `tools/build_icons.py`, which composites the icons from the moose master kept in the Android repository: `python tools/build_icons.py --moose ../WARN/art/moose_head_master.png`.

## iOS notes

Wake lock requires Safari 16.4 or later. iOS has no Battery Status API, so there is no charging-aware behaviour. Safari may evict a non-installed PWA's cache after about a week unused. Install via the Share sheet → Add to Home Screen.