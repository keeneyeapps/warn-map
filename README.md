WARN Map is a public, read-only PWA for viewing current WARN road hazards on a map. It displays a self-hosted Protomaps basemap of the Kenai Peninsula and refreshes live hazards from the public `public_hazards` view every 60 seconds.

The app is read-only and may only query the WARN Supabase `public_hazards` view using its anon key. Location stays on-device and may only centre the map. All runtime assets and tiles are self-hosted, there are no CDNs, and there is no build step.

## Road graph

`graph/kenai-roads.json` is a compact, offline road network for on-device routing. It is derived from OpenStreetMap data under the ODbL, downloaded from Geofabrik. To rebuild it, install pyosmium with `python -m pip install osmium`, download the Alaska extract with `curl.exe -L --fail --output tools/alaska-latest.osm.pbf https://download.geofabrik.de/north-america/us/alaska-latest.osm.pbf`, then run `python tools/build_graph.py`.

## iOS notes

Wake lock requires Safari 16.4 or later. iOS has no Battery Status API, so there is no charging-aware behaviour. Safari may evict a non-installed PWA's cache after about a week unused. Install via the Share sheet → Add to Home Screen.