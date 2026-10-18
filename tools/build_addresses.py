import argparse
import json
import math
import re
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

# KPB Physical Addresses (Kenai Peninsula Borough E911 address points, KPB GeoHub item
# 03f1d7cbe70244c7bf3cba92a4cee5e1). Served in NAD83 Alaska State Plane 4 (ft); requested as WGS84.
SERVICE_URL = (
    "https://services.arcgis.com/ba4DH9pIcqkXJVfl/arcgis/rest/services/"
    "Physical_Addresses_view/FeatureServer/0/query"
)
PAGE_SIZE = 2000
EARTH_RADIUS_M = 6_371_008.8
METERS_PER_DEGREE = 111_320
GRID_CELL_DEGREES = 0.002
COARSE_CELL_DEGREES = 0.05
FINE_SEARCH_M = 1_500
MAX_SEARCH_M = 25_000
OFF_NETWORK_M = 2_000

DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
LOWERCASE_WORDS = {"of", "the", "and", "on", "at"}
ROMAN_NUMERALS = {"II", "III", "IV", "VI", "VII", "VIII"}


def fetch(output):
    features = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({
            "where": "1=1",
            "outFields": "*",
            "outSR": 4326,
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "f": "geojson",
        })
        with urllib.request.urlopen(f"{SERVICE_URL}?{query}", timeout=120) as response:
            page = json.load(response)
        batch = page.get("features", [])
        features.extend(batch)
        print(f"Fetched {len(features):,} records")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.5)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle)
    print(f"Wrote {output} ({output.stat().st_size:,} bytes)")


def title_word(word):
    upper = word.upper()
    if upper in DIRECTIONALS or upper in ROMAN_NUMERALS:
        return upper
    if re.fullmatch(r"\d+(ST|ND|RD|TH)", upper):
        return upper[:-2] + upper[-2:].lower()
    if upper.startswith("MC") and len(upper) > 2:
        return "Mc" + upper[2:].capitalize()
    # Keep hyphenated and apostrophe parts capitalised (O'Brien, Kenai-Soldotna).
    return re.sub(r"[A-Za-z]+", lambda match: match.group(0).capitalize(), word.lower())


def normalise_street(street):
    street = re.sub(r"\s+", " ", street).strip()
    if not street or street != street.upper():
        return street
    words = street.split(" ")
    titled = [title_word(word) for word in words]
    return " ".join(
        word.lower() if index and word.lower() in LOWERCASE_WORDS else word
        for index, word in enumerate(titled)
    )


def normalise_place(name):
    name = re.sub(r"\s+", " ", name or "").strip()
    return normalise_street(name) if name else ""


def split_address(address, number, unit):
    """Street name and any house-number fraction from KPB's one-line Address string."""
    address = re.sub(r"\s+", " ", address or "").strip()
    # 661 records repeat their unit ("... Way Bldg 2"); it belongs in the unit field only.
    if unit and address.casefold().endswith(" " + unit.casefold()):
        address = address[: -len(unit) - 1].rstrip()
    match = re.match(r"^(\d+)(?:\s+(\d+/\d+))?\s+(.*)$", address)
    if match and (number is None or int(match.group(1)) == number):
        return match.group(3), match.group(2) or ""
    return address, ""


def build(source, output):
    features = json.loads(source.read_text(encoding="utf-8"))["features"]
    kept = {}
    dropped = defaultdict(int)
    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            dropped["no point geometry"] += 1
            continue
        longitude, latitude = geometry["coordinates"][:2]
        if not (math.isfinite(longitude) and math.isfinite(latitude)):
            dropped["no point geometry"] += 1
            continue
        number = properties.get("House_Number")
        if not isinstance(number, int) or number <= 0:
            dropped["no house number"] += 1
            continue
        unit = " ".join(
            part for part in (normalise_place(properties.get("unittype")), (properties.get("unitid") or "").strip())
            if part
        )
        raw_unit = " ".join(
            part for part in ((properties.get("unittype") or "").strip(), (properties.get("unitid") or "").strip()) if part
        )
        street, fraction = split_address(properties.get("Address"), number, raw_unit)
        street = normalise_street(street)
        if not street:
            dropped["no street name"] += 1
            continue
        unit = " ".join(part for part in (fraction, unit) if part)
        # The postal city is what people write; the dispatch (MSAG) community is kept so it matches too.
        community = normalise_place(properties.get("Community_Name"))
        city = normalise_place(properties.get("PO_Community_Name")) or community
        key = (street.casefold(), number, unit.casefold(), city.casefold())
        if key in kept:
            dropped["duplicate"] += 1
            continue
        kept[key] = (street, number, unit, city, community, round(longitude, 5), round(latitude, 5))

    # "1663 A Homer Spit Rd" is house 1663-A on Homer Spit Rd: a single non-directional letter before a
    # street that exists on its own is a house-number suffix ("D St" and "W Corral Ave" are untouched).
    known_streets = {record[0].casefold() for record in kept.values()}
    suffixed = {}
    suffix_count = 0
    for key, (street, number, unit, city, community, longitude, latitude) in kept.items():
        match = re.match(r"^([A-Z]) (.+)$", street)
        if match and match.group(1) not in DIRECTIONALS and match.group(2).casefold() in known_streets:
            street = match.group(2)
            unit = " ".join(part for part in (match.group(1), unit) if part)
            suffix_count += 1
        suffixed[(street.casefold(), number, unit.casefold(), city.casefold())] = (
            street, number, unit, city, community, longitude, latitude)
    kept = suffixed

    records = sorted(kept.values(), key=lambda record: (record[0].casefold(), record[1], record[2].casefold(), record[3]))
    streets = sorted({record[0] for record in records}, key=str.casefold)
    places = sorted({name for record in records for name in record[3:5] if name}, key=str.casefold)
    street_index = {name: index for index, name in enumerate(streets)}
    place_index = {name: index for index, name in enumerate(places)}

    result = {
        "source": "Kenai Peninsula Borough, KPB Physical Addresses (E911 address points)",
        "built": date.today().isoformat(),
        "fields": ["street", "number", "unit", "city", "community", "lon", "lat"],
        "streets": streets,
        # Postal cities and dispatch (MSAG) communities share one list.
        "places": places,
        # [street index, house number, unit or "", postal city place index, dispatch community place
        #  index (each -1 if absent), longitude, latitude]
        "addresses": [
            [street_index[street], number, unit, place_index.get(city, -1), place_index.get(community, -1),
             longitude, latitude]
            for street, number, unit, city, community, longitude, latitude in records
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")

    print(f"Source records: {len(features):,}")
    for reason, count in sorted(dropped.items()):
        print(f"Dropped ({reason}): {count:,}")
    print(f"House-number letter suffixes moved to unit: {suffix_count:,}")
    print(f"Addresses: {len(records):,}")
    print(f"Streets: {len(streets):,}")
    print(f"Postal cities: {len({record[3] for record in records if record[3]}):,}; "
          f"dispatch communities: {len({record[4] for record in records if record[4]}):,}")
    print(f"Index file: {output} ({output.stat().st_size:,} bytes)")
    return result


def check_alignment(index_path, graph_path):
    index = json.loads(index_path.read_text(encoding="utf-8"))
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    segments = []
    for edge in graph["edges"]:
        geometry = edge[7]
        segments.extend(zip(geometry, geometry[1:]))

    def build_grid(cell_degrees):
        grid = defaultdict(list)
        for segment_index, (first, second) in enumerate(segments):
            for x in range(math.floor(min(first[0], second[0]) / cell_degrees),
                           math.floor(max(first[0], second[0]) / cell_degrees) + 1):
                for y in range(math.floor(min(first[1], second[1]) / cell_degrees),
                               math.floor(max(first[1], second[1]) / cell_degrees) + 1):
                    grid[(x, y)].append(segment_index)
        return grid

    fine_grid = build_grid(GRID_CELL_DEGREES)
    coarse_grid = build_grid(COARSE_CELL_DEGREES)

    def ring_cells(cell_x, cell_y, radius):
        if radius == 0:
            yield cell_x, cell_y
            return
        for x in range(cell_x - radius, cell_x + radius + 1):
            yield x, cell_y - radius
            yield x, cell_y + radius
        for y in range(cell_y - radius + 1, cell_y + radius):
            yield cell_x - radius, y
            yield cell_x + radius, y

    def search(point, grid, cell_degrees, max_distance_m):
        """Nearest segment within max_distance_m, as (distance, east, north) to it, or None."""
        x_scale = METERS_PER_DEGREE * math.cos(math.radians(point[1]))
        cell_x = math.floor(point[0] / cell_degrees)
        cell_y = math.floor(point[1] / cell_degrees)
        # A cell is narrower east-west than north-south here, so x_scale bounds what a ring guarantees.
        ring_width_m = cell_degrees * x_scale
        best = None
        radius = 0
        while True:
            for cell in ring_cells(cell_x, cell_y, radius):
                for segment_index in grid.get(cell, ()):
                    first, second = segments[segment_index]
                    ax = (first[0] - point[0]) * x_scale
                    ay = (first[1] - point[1]) * METERS_PER_DEGREE
                    dx = (second[0] - first[0]) * x_scale
                    dy = (second[1] - first[1]) * METERS_PER_DEGREE
                    length = dx * dx + dy * dy
                    t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
                    qx, qy = ax + t * dx, ay + t * dy
                    distance = math.hypot(qx, qy)
                    if best is None or distance < best[0]:
                        best = (distance, qx, qy)
            # Every unscanned segment is at least radius * ring_width_m away.
            covered_m = radius * ring_width_m
            if best is not None and best[0] <= covered_m:
                return best
            if covered_m >= max_distance_m:
                return best if best is not None and best[0] <= max_distance_m else None
            radius += 1

    def nearest(point):
        return (search(point, fine_grid, GRID_CELL_DEGREES, FINE_SEARCH_M)
                or search(point, coarse_grid, COARSE_CELL_DEGREES, MAX_SEARCH_M))

    streets = index["streets"]
    places = index["places"]
    distances = []
    by_street = defaultdict(list)
    unreachable = defaultdict(int)
    for street, _number, _unit, _city, community, longitude, latitude in index["addresses"]:
        result = nearest((longitude, latitude))
        if result is None:
            distances.append(math.inf)
            unreachable[places[community] if community >= 0 else "(none)"] += 1
            continue
        distances.append(result[0])
        by_street[streets[street]].append(result)

    distances.sort()

    def percentile(values, fraction):
        position = fraction * (len(values) - 1)
        lower = math.floor(position)
        upper = min(len(values) - 1, lower + 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    print(f"Address points checked: {len(distances):,}")
    print(f"Median distance to nearest road edge: {statistics.median(distances):.1f} m")
    print(f"90th percentile: {percentile(distances, 0.9):.1f} m")
    print(f"Beyond 150 m: {sum(1 for d in distances if d > 150):,}")
    print(f"No road edge within {MAX_SEARCH_M / 1000:.0f} km: {sum(unreachable.values()):,} "
          f"({', '.join(f'{name} {count}' for name, count in sorted(unreachable.items(), key=lambda item: -item[1]))})")

    street_rows = []
    for street, results in by_street.items():
        if len(results) < 5:
            continue
        median_distance = statistics.median(r[0] for r in results)
        # Median offset vector from the address to its nearest road point (east, north).
        median_east = statistics.median(r[1] for r in results)
        median_north = statistics.median(r[2] for r in results)
        bearing = (math.degrees(math.atan2(median_east, median_north)) + 360) % 360
        street_rows.append((median_distance, street, len(results), median_east, median_north, bearing))
    street_rows.sort(reverse=True)
    # Streets in communities with no road link (Tyonek, Seldovia, Halibut Cove...) measure water, not alignment.
    off_network = [row for row in street_rows if row[0] > OFF_NETWORK_M]
    street_rows = [row for row in street_rows if row[0] <= OFF_NETWORK_M]
    print(f"Streets more than {OFF_NETWORK_M / 1000:.0f} km from any graph edge (off the road network): "
          f"{len(off_network)}: {', '.join(row[1] for row in off_network)}")
    print("Largest median offsets (streets with 5+ addresses, on the road network):")
    for median_distance, street, count, east, north, bearing in street_rows[:10]:
        print(f"  {street}: {median_distance:.0f} m median over {count} addresses; "
              f"median offset vector {east:+.0f} m E, {north:+.0f} m N (bearing {bearing:.0f})")

    # Datum signature: many streets sharing one offset vector of 150-250 m toward WNW or ESE.
    shifted = [row for row in street_rows if 150 <= math.hypot(row[3], row[4]) <= 250]
    wnw_ese = [row for row in shifted if 270 <= row[5] <= 315 or 90 <= row[5] <= 135]
    all_east = statistics.median(r[1] for results in by_street.values() for r in results)
    all_north = statistics.median(r[2] for results in by_street.values() for r in results)
    print(f"Streets with a 150-250 m median offset vector: {len(shifted)} of {len(street_rows)}; "
          f"of those toward WNW/ESE: {len(wnw_ese)}")
    sectors = defaultdict(int)
    for row in shifted:
        sectors[["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(row[5] / 45) % 8]] += 1
    print("  bearings of those offsets: " + ", ".join(f"{name} {sectors[name]}" for name in
                                                      ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]))
    print(f"Median offset vector over all addresses: {all_east:+.1f} m E, {all_north:+.1f} m N")


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Build the Kenai Peninsula offline address index")
    parser.add_argument("--input", type=Path, default=root / "tools" / "kpb-physical-addresses.geojson")
    parser.add_argument("--output", type=Path, default=root / "addresses" / "kenai-addresses.json")
    parser.add_argument("--graph", type=Path, default=root / "graph" / "kenai-roads.json")
    parser.add_argument("--fetch", action="store_true", help="download the KPB address points to --input first")
    parser.add_argument("--check-alignment", action="store_true",
                        help="report address-to-road distances against --graph (read-only)")
    args = parser.parse_args()

    if args.fetch:
        fetch(args.input)
    build(args.input, args.output)
    if args.check_alignment:
        check_alignment(args.output, args.graph)


if __name__ == "__main__":
    main()
