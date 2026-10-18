WARN Map is a public, read-only PWA for viewing current WARN road hazards on a map. It displays a self-hosted Protomaps basemap of the Kenai Peninsula and refreshes live hazards from the public `public_hazards` view every 60 seconds.

The app is read-only and may only query the WARN Supabase `public_hazards` view using its anon key. Browser geolocation never leaves the device; used only by the map and on-device routing. All runtime assets and tiles are self-hosted, there are no CDNs, and there is no build step.

## Road graph

`graph/kenai-roads.json` is a compact, offline road network for on-device routing. It is derived from OpenStreetMap data under the ODbL, downloaded from Geofabrik. To rebuild it, install pyosmium with `python -m pip install osmium`, download the Alaska extract with `curl.exe -L --fail --output tools/alaska-latest.osm.pbf https://download.geofabrik.de/north-america/us/alaska-latest.osm.pbf`, then run `python tools/build_graph.py`.

## Address index

`addresses/kenai-addresses.json` is an offline address index for destination entry, built from the Kenai Peninsula Borough's KPB Physical Addresses (E911 address points, KPB GeoHub). It keeps only house number, street, unit, postal city, the Borough's dispatch community (so either name matches), and a point; no other field from the source is kept. The index is for locating a destination on the map; it is not an authoritative address record. The Borough provides the data without warranty and asks users to accept that it assumes no liability for its use. To rebuild it, run `python tools/build_addresses.py --fetch`, which downloads the points to the ignored `tools/kpb-physical-addresses.geojson`; add `--check-alignment` to report address-to-road distances against the road graph.

## iOS notes

Wake lock requires Safari 16.4 or later. iOS has no Battery Status API, so there is no charging-aware behaviour. Safari may evict a non-installed PWA's cache after about a week unused. Install via the Share sheet → Add to Home Screen.