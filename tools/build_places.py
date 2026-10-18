import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import osmium

from build_addresses import fetch, normalise_place
from region import load_region, root_path

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_DEGREE = 111_320
DEDUPE_M = 50
CITY_SEARCH_M = 5_000
CITY_CELL_DEGREES = 0.05

CATEGORIES = ["health", "school", "government", "emergency", "store", "food", "lodging", "fuel", "transport", "park",
              "landmark", "other"]
OSM_KEYS = ["amenity", "shop", "tourism", "office", "healthcare", "leisure", "aeroway", "public_transport", "emergency",
            "man_made"]
SKIP_AMENITY = {"bench", "waste_basket", "waste_disposal", "recycling", "vending_machine", "parking", "parking_space",
                "parking_entrance", "bicycle_parking", "motorcycle_parking", "toilets", "drinking_water", "shelter",
                "fire_hydrant", "post_box", "telephone", "atm", "bbq", "hunting_stand", "grit_bin", "clock"}
KEEP_AEROWAY = {"aerodrome", "airstrip", "heliport", "terminal", "seaplane_base"}
KEEP_MAN_MADE = {"lighthouse", "pier"}
KEEP_EMERGENCY = {"ambulance_station", "lifeguard_station", "mountain_rescue", "water_rescue", "coast_guard"}

AMENITY_CATEGORY = {
    "hospital": "health", "clinic": "health", "doctors": "health", "dentist": "health", "pharmacy": "health",
    "school": "school", "college": "school", "university": "school", "kindergarten": "school",
    "townhall": "government", "courthouse": "government", "post_office": "government", "library": "government",
    "community_centre": "government", "social_facility": "government", "prison": "government",
    "fire_station": "emergency", "police": "emergency", "ranger_station": "emergency",
    "restaurant": "food", "cafe": "food", "fast_food": "food", "bar": "food", "pub": "food", "ice_cream": "food",
    "food_court": "food",
    "fuel": "fuel", "charging_station": "fuel",
    "ferry_terminal": "transport", "bus_station": "transport", "car_rental": "transport", "taxi": "transport",
}
TOURISM_CATEGORY = {
    "hotel": "lodging", "motel": "lodging", "guest_house": "lodging", "hostel": "lodging", "camp_site": "lodging",
    "caravan_site": "lodging", "chalet": "lodging", "apartment": "lodging", "alpine_hut": "lodging",
    "wilderness_hut": "lodging",
    "attraction": "landmark", "museum": "landmark", "viewpoint": "landmark", "artwork": "landmark",
    "gallery": "landmark", "zoo": "landmark", "aquarium": "landmark", "information": "landmark",
    "picnic_site": "park",
}
PARK_LEISURE = {"park", "nature_reserve", "playground", "garden", "dog_park", "recreation_ground", "common",
                "slipway", "fishing", "beach_resort"}
LABELS = {
    "supermarket": "Grocery", "convenience": "Convenience store", "fuel": "Gas station", "fast_food": "Fast food",
    "doctors": "Doctor", "dentist": "Dentist", "post_office": "Post office", "townhall": "City hall",
    "community_centre": "Community center", "fire_station": "Fire station", "police": "Police",
    "camp_site": "Campground", "caravan_site": "RV park", "guest_house": "Guest house", "place_of_worship": "Church",
    "car_repair": "Auto repair", "hardware": "Hardware store", "doityourself": "Hardware store",
    "alcohol": "Liquor store", "slipway": "Boat launch", "aerodrome": "Airport", "information": "Visitor center",
    "ferry_terminal": "Ferry terminal", "bus_station": "Bus station", "charging_station": "EV charging",
    "sports_centre": "Sports center", "fitness_centre": "Gym", "social_facility": "Social services",
    "ranger_station": "Ranger station", "nature_reserve": "Nature reserve", "picnic_site": "Picnic site",
    "viewpoint": "Viewpoint", "attraction": "Attraction", "government": "Government office",
    "car": "Car dealer", "clothes": "Clothing store", "gift": "Gift shop", "beauty": "Beauty salon",
    "outdoor": "Outdoor store", "company": "Office", "cannabis": "Cannabis store", "pitch": "Sports field",
}


def haversine_m(first, second):
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    value = (math.sin((lat2 - lat1) / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def label_for(value, generic="Place"):
    """A readable subtype label; a bare "yes" (shop=yes, office=yes) gets the key's generic label."""
    if value in ("yes", "no", ""):
        return generic
    return LABELS.get(value) or value.replace("_", " ").capitalize()


def osm_category(tags):
    """(category, subtype label) for an OSM object's tags, or None to skip it."""
    amenity = tags.get("amenity")
    if amenity in SKIP_AMENITY:
        return None
    if tags.get("healthcare") or amenity in ("hospital", "clinic", "doctors", "dentist", "pharmacy"):
        return "health", label_for(amenity or tags["healthcare"], "Health care")
    if amenity:
        return AMENITY_CATEGORY.get(amenity, "other"), label_for(amenity)
    if tags.get("emergency") in KEEP_EMERGENCY:
        return "emergency", label_for(tags["emergency"])
    if tags.get("shop"):
        return "store", label_for(tags["shop"], "Store")
    if tags.get("tourism"):
        tourism = tags["tourism"]
        if tourism == "information" and tags.get("information") != "office":
            return None
        return TOURISM_CATEGORY.get(tourism, "other"), label_for(tourism)
    if tags.get("office"):
        return ("government" if tags["office"] == "government" else "other"), label_for(tags["office"], "Office")
    if tags.get("aeroway"):
        return ("transport", label_for(tags["aeroway"])) if tags["aeroway"] in KEEP_AEROWAY else None
    if tags.get("public_transport"):
        return None if tags["public_transport"] == "stop_position" else ("transport", "Transit stop")
    if tags.get("man_made") in KEEP_MAN_MADE:
        return ("landmark" if tags["man_made"] == "lighthouse" else "transport"), label_for(tags["man_made"])
    if tags.get("leisure"):
        return ("park" if tags["leisure"] in PARK_LEISURE else "other"), label_for(tags["leisure"])
    return None


def normalise_phone(*values):
    """The first US number in the given tag values as +1-NNN-NNN-NNNN, or ""."""
    for value in values:
        for part in re.split(r"[;,/]", value or ""):
            digits = re.sub(r"\D", "", part)
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            if len(digits) == 10:
                return f"+1-{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return ""


def normalise_website(value):
    value = (value or "").strip().split(";")[0].strip()
    if not value or " " in value or "." not in value:
        return ""
    return value if re.match(r"^https?://", value, re.I) else f"https://{value}"


def street_address(tags):
    number = (tags.get("addr:housenumber") or "").strip()
    street = (tags.get("addr:street") or "").strip()
    unit = (tags.get("addr:unit") or "").strip()
    if not street:
        return ""
    return " ".join(part for part in (number, street, unit) if part)


def dedupe_key(name):
    name = name.casefold().replace("&", " and ")
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    words = [word for word in name.split() if word != "the"]
    return " ".join(words)


def in_bbox(point, bbox):
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def osm_record(tags, point):
    name = (tags.get("name") or "").strip()
    if not name:
        return None
    category = osm_category(tags)
    if category is None:
        return None
    return {
        "name": name,
        "category": category[0],
        "label": category[1],
        "city": (tags.get("addr:city") or "").strip(),
        "address": street_address(tags),
        "phone": normalise_phone(tags.get("phone"), tags.get("contact:phone")),
        "website": normalise_website(tags.get("website") or tags.get("contact:website")),
        "point": point,
        "source": "osm",
    }


def read_osm(path, bbox):
    tag_filter = osmium.filter.KeyFilter(*OSM_KEYS)
    processor = (osmium.FileProcessor(str(path))
                 .with_locations()
                 .with_areas(tag_filter)
                 .with_filter(osmium.filter.EmptyTagFilter())
                 .with_filter(tag_filter))
    records = []
    for obj in processor:
        tags = dict(obj.tags)
        if "name" not in tags:
            continue
        if obj.is_node():
            if not obj.location.valid():
                continue
            point = (obj.location.lon, obj.location.lat)
        elif obj.is_way():
            # Closed ways arrive again as areas; open ways (piers, linear features) use their vertex mean.
            if obj.is_closed():
                continue
            try:
                coordinates = [(node.lon, node.lat) for node in obj.nodes]
            except osmium.InvalidLocationError:
                continue
            if not coordinates:
                continue
            point = (sum(c[0] for c in coordinates) / len(coordinates), sum(c[1] for c in coordinates) / len(coordinates))
        elif obj.is_area():
            coordinates = [(node.lon, node.lat) for ring in obj.outer_rings() for node in ring]
            if not coordinates:
                continue
            point = (sum(c[0] for c in coordinates) / len(coordinates), sum(c[1] for c in coordinates) / len(coordinates))
        else:
            continue
        if not in_bbox(point, bbox):
            continue
        record = osm_record(tags, point)
        if record:
            records.append(record)
    return records


def title_name(name, keep_upper):
    titled = normalise_place(name)
    return " ".join(word.upper() if word.upper() in keep_upper else word for word in titled.split(" "))


def read_geohub(source, keep_upper, bbox):
    features = json.loads(root_path(source["download_to"]).read_text(encoding="utf-8"))["features"]
    fields = source["fields"]
    records = []
    dropped = Counter()
    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != fields["geometry"]:
            dropped["no point"] += 1
            continue
        name = (properties.get(fields["name"]) or "").strip()
        if not name:
            dropped["unnamed"] += 1
            continue
        if "category" in fields:
            mapped = source["categories"].get((properties.get(fields["category"]) or "").strip())
        else:
            mapped = source["category"]
        if not mapped:
            dropped[f"category {properties.get(fields.get('category'))}"] += 1
            continue
        point = tuple(geometry["coordinates"][:2])
        if not in_bbox(point, bbox):
            dropped["outside bbox"] += 1
            continue
        records.append({
            "name": title_name(name, keep_upper),
            "category": mapped[0],
            "label": mapped[1],
            "city": "",
            "address": "",
            "phone": "",
            "website": normalise_website(properties.get(fields["website"])) if "website" in fields else "",
            "point": point,
            "source": source["name"],
        })
    return records, dropped


def dedupe(records):
    """Drop a record with the same name and category as an earlier one within DEDUPE_M; the earlier one keeps
    its fields and takes any phone, website, or street address it lacks. Earlier records (GeoHub) win. The
    category keeps a store, its in-store pharmacy, and its fuel station apart when they share a name."""
    cell = 0.001
    grid = defaultdict(list)
    kept = []
    merged = 0
    for record in records:
        key = (dedupe_key(record["name"]), record["category"])
        x, y = math.floor(record["point"][0] / cell), math.floor(record["point"][1] / cell)
        match = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid[(x + dx, y + dy)]:
                    if other["key"] == key and haversine_m(other["point"], record["point"]) < DEDUPE_M:
                        match = other
                        break
                if match:
                    break
            if match:
                break
        if match:
            merged += 1
            for field in ("phone", "website", "address", "city"):
                if not match[field] and record[field]:
                    match[field] = record[field]
            continue
        record["key"] = key
        grid[(x, y)].append(record)
        kept.append(record)
    return kept, merged


def nearest_city_lookup(addresses_path):
    """Postal city of the nearest address point within CITY_SEARCH_M, from the region's address index."""
    index = json.loads(addresses_path.read_text(encoding="utf-8"))
    places = index["places"]
    grid = defaultdict(list)
    for _street, _number, _unit, city, _community, longitude, latitude in index["addresses"]:
        if city >= 0:
            grid[(math.floor(longitude / CITY_CELL_DEGREES), math.floor(latitude / CITY_CELL_DEGREES))].append(
                ((longitude, latitude), places[city]))

    def lookup(point):
        x, y = math.floor(point[0] / CITY_CELL_DEGREES), math.floor(point[1] / CITY_CELL_DEGREES)
        best, best_distance = "", CITY_SEARCH_M
        for dx in range(-2, 3):
            for dy in range(-1, 2):
                for address_point, city in grid.get((x + dx, y + dy), ()):
                    distance = haversine_m(point, address_point)
                    if distance < best_distance:
                        best, best_distance = city, distance
        return best

    return lookup


def main():
    parser = argparse.ArgumentParser(description="Build a region's offline named-places index")
    parser.add_argument("--region", type=Path, required=True, help="region pack, e.g. regions/kenai/region.json")
    parser.add_argument("--output", type=Path, help="places JSON (default: the region's places path)")
    parser.add_argument("--fetch", action="store_true", help="download the region's GeoHub layers first")
    args = parser.parse_args()

    region = load_region(args.region)
    places = region["files"]["places"]
    bbox = region["bbox"]
    keep_upper = set(places.get("keep_upper", []))
    output = args.output or root_path(places["path"])

    geohub = []
    for source in places["sources"]:
        if args.fetch:
            fetch(source, root_path(source["download_to"]))
        records, dropped = read_geohub(source, keep_upper, bbox)
        print(f"{source['name']}: {len(records):,} kept"
              + "".join(f"; dropped {reason}: {count}" for reason, count in sorted(dropped.items())))
        geohub.extend(records)

    osm = read_osm(root_path(places["osm"]["download_to"]), bbox)
    print(f"OpenStreetMap: {len(osm):,} named places in the bbox")

    kept, merged = dedupe(geohub + osm)
    print(f"Merged duplicates (same name and category within {DEDUPE_M} m): {merged:,}")

    city_for = nearest_city_lookup(root_path(region["files"]["addresses"]["path"]))
    for record in kept:
        record["city"] = record["city"] or city_for(record["point"])

    kept.sort(key=lambda record: (record["name"].casefold(), record["point"]))
    labels = sorted({record["label"] for record in kept})
    cities = sorted({record["city"] for record in kept if record["city"]}, key=str.casefold)
    label_index = {label: index for index, label in enumerate(labels)}
    city_index = {city: index for index, city in enumerate(cities)}
    result = {
        "source": ["OpenStreetMap contributors (ODbL)"] + [source["name"] for source in places["sources"]],
        "built": date.today().isoformat(),
        "categories": CATEGORIES,
        "labels": labels,
        "cities": cities,
        "fields": ["name", "category", "label", "city", "address", "phone", "website", "lon", "lat"],
        # [name, category index, label index, city index or -1, street address or "", phone or "",
        #  website or "", longitude, latitude]
        "places": [
            [record["name"], CATEGORIES.index(record["category"]), label_index[record["label"]],
             city_index.get(record["city"], -1), record["address"], record["phone"], record["website"],
             round(record["point"][0], 5), round(record["point"][1], 5)]
            for record in kept
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")

    by_category = Counter(record["category"] for record in kept)
    by_source = Counter("GeoHub" if record["source"] != "osm" else "OSM" for record in kept)
    print(f"Places: {len(kept):,} ({', '.join(f'{name} {count:,}' for name, count in by_source.items())})")
    for category in CATEGORIES:
        print(f"  {category}: {by_category[category]:,}")
    print(f"With phone: {sum(1 for record in kept if record['phone']):,}; "
          f"with website: {sum(1 for record in kept if record['website']):,}; "
          f"with street address: {sum(1 for record in kept if record['address']):,}; "
          f"without a city: {sum(1 for record in kept if not record['city']):,}")
    print(f"Places file: {output} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
