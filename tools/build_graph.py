import argparse
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

import osmium


BBOX = (-154.8, 58.9, -148.2, 61.6)
CLASSES = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "unclassified_link",
    "residential",
    "residential_link",
    "service",
    "service_link",
    "living_street",
    "living_street_link",
    "track",
    "track_link",
)
CLASS_INDEX = {name: index for index, name in enumerate(CLASSES)}
EARTH_RADIUS_M = 6_371_008.8


def clip_segment(start, end):
    """Return the segment portion inside BBOX as coordinates and source fractions."""
    min_lon, min_lat, max_lon, max_lat = BBOX
    x0, y0 = start[1]
    x1, y1 = end[1]
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0

    for p, q in (
        (-dx, x0 - min_lon),
        (dx, max_lon - x0),
        (-dy, y0 - min_lat),
        (dy, max_lat - y0),
    ):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None

    def point_at(fraction, source):
        coordinate = (x0 + fraction * dx, y0 + fraction * dy)
        node_id = source[0] if fraction <= 1e-12 else end[0] if fraction >= 1 - 1e-12 else None
        return node_id, coordinate

    return point_at(lower, start), point_at(upper, start)


def way_fragments(way_id, nodes):
    fragments = []
    current = []

    for segment_index, (start, end) in enumerate(zip(nodes, nodes[1:])):
        clipped = clip_segment(start, end)
        if clipped is None:
            if len(current) > 1:
                fragments.append(current)
            current = []
            continue

        clipped_start, clipped_end = clipped
        if clipped_start[0] is None:
            clipped_start = (("clip", way_id, segment_index, 0), clipped_start[1])
        if clipped_end[0] is None:
            clipped_end = (("clip", way_id, segment_index, 1), clipped_end[1])

        if current and current[-1][0] == clipped_start[0]:
            current.append(clipped_end)
        else:
            if len(current) > 1:
                fragments.append(current)
            current = [clipped_start, clipped_end]

    if len(current) > 1:
        fragments.append(current)
    return fragments


class RoadHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []

    def way(self, way):
        tags = dict(way.tags)
        highway = tags.get("highway")
        if highway not in CLASS_INDEX:
            return

        nodes = []
        for node in way.nodes:
            if not node.location.valid():
                return
            nodes.append((int(node.ref), (float(node.location.lon), float(node.location.lat))))
        if len(nodes) < 2:
            return

        fragments = way_fragments(int(way.id), nodes)
        if not fragments:
            return

        oneway_tag = tags.get("oneway", "").lower()
        if oneway_tag in ("yes", "1", "true"):
            oneway = True
        elif oneway_tag in ("-1", "reverse"):
            oneway = "reverse"
        elif not oneway_tag and tags.get("junction") in ("roundabout", "circular"):
            oneway = True
        else:
            oneway = False

        self.ways.append(
            {
                "fragments": fragments,
                "class": CLASS_INDEX[highway],
                "oneway": oneway,
                "maxspeed": tags.get("maxspeed"),
                "name": tags.get("name"),
            }
        )


def haversine_m(start, end):
    lon1, lat1 = map(math.radians, start)
    lon2, lat2 = map(math.radians, end)
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def build_graph(ways):
    node_way_refs = defaultdict(set)
    for way_index, way in enumerate(ways):
        for fragment in way["fragments"]:
            for node_id, _ in fragment:
                if isinstance(node_id, int):
                    node_way_refs[node_id].add(way_index)

    anchors = []
    for way_index, way in enumerate(ways):
        for fragment in way["fragments"]:
            split_indices = {0, len(fragment) - 1}
            for index, (node_id, _) in enumerate(fragment):
                if isinstance(node_id, int) and len(node_way_refs[node_id]) > 1:
                    split_indices.add(index)
            anchors.append((way_index, fragment, sorted(split_indices)))

    graph_node_indices = {}
    graph_nodes = []
    edges = []

    def graph_node(node_id, coordinate):
        if node_id not in graph_node_indices:
            graph_node_indices[node_id] = len(graph_nodes)
            graph_nodes.append([round(coordinate[0], 6), round(coordinate[1], 6)])
        return graph_node_indices[node_id]

    for way_index, fragment, split_indices in anchors:
        way = ways[way_index]
        for start_index, end_index in zip(split_indices, split_indices[1:]):
            if start_index == end_index:
                continue
            geometry = fragment[start_index : end_index + 1]
            start_node = graph_node(*geometry[0])
            end_node = graph_node(*geometry[-1])
            length_m = sum(
                haversine_m(first[1], second[1])
                for first, second in zip(geometry, geometry[1:])
            )
            edges.append(
                [
                    start_node,
                    end_node,
                    round(length_m, 3),
                    way["class"],
                    way["oneway"],
                    way["maxspeed"],
                    way["name"],
                    [[round(lon, 6), round(lat, 6)] for _, (lon, lat) in geometry],
                ]
            )

    adjacency = [[] for _ in graph_nodes]
    for edge_index, edge in enumerate(edges):
        adjacency[edge[0]].append((edge[1], edge_index))
        adjacency[edge[1]].append((edge[0], edge_index))

    components = []
    unseen = set(range(len(graph_nodes)))
    while unseen:
        seed = unseen.pop()
        component_nodes = {seed}
        component_edge_indices = set()
        stack = [seed]
        while stack:
            current = stack.pop()
            for neighbor, edge_index in adjacency[current]:
                component_edge_indices.add(edge_index)
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    component_nodes.add(neighbor)
                    stack.append(neighbor)
        component_edges = sorted(component_edge_indices)
        sample_name = next((edges[index][6] for index in component_edges if edges[index][6]), None)
        components.append(
            {"nodes": component_nodes, "edge_indices": component_edges, "sample_name": sample_name}
        )

    components.sort(key=lambda component: (len(component["edge_indices"]), len(component["nodes"])), reverse=True)
    if not components:
        raise RuntimeError("No eligible road graph components found inside the requested bbox")

    largest = components[0]
    dropped = components[1:]
    dropped_node_count = sum(len(component["nodes"]) for component in dropped)
    dropped_edge_count = sum(len(component["edge_indices"]) for component in dropped)

    retained_node_order = sorted(largest["nodes"])
    retained_index = {old_index: index for index, old_index in enumerate(retained_node_order)}
    retained_edges = []
    for edge_index in largest["edge_indices"]:
        edge = edges[edge_index]
        retained_edges.append(
            [retained_index[edge[0]], retained_index[edge[1]], *edge[2:]]
        )

    result = {
        "classes": list(CLASSES),
        "nodes": [graph_nodes[index] for index in retained_node_order],
        "edges": retained_edges,
    }
    report = {
        "dropped_nodes": dropped_node_count,
        "dropped_edges": dropped_edge_count,
        "largest_dropped": [
            {
                "nodes": len(component["nodes"]),
                "edges": len(component["edge_indices"]),
                "sample_name": component["sample_name"],
            }
            for component in dropped[:3]
        ],
    }
    return result, report


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Build the Kenai Peninsula offline road graph")
    parser.add_argument("--input", type=Path, default=root / "tools" / "alaska-latest.osm.pbf")
    parser.add_argument("--output", type=Path, default=root / "graph" / "kenai-roads.json")
    args = parser.parse_args()

    handler = RoadHandler()
    handler.apply_file(str(args.input), locations=True)
    graph, component_report = build_graph(handler.ways)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output_file:
        json.dump(graph, output_file, separators=(",", ":"), ensure_ascii=False)
        output_file.write("\n")

    file_size = args.output.stat().st_size
    total_km = sum(edge[2] for edge in graph["edges"]) / 1000
    if file_size > 15_000_000:
        print("WARNING: graph file exceeds 15 MB")
    print(f"Nodes: {len(graph['nodes']):,}")
    print(f"Edges: {len(graph['edges']):,}")
    print(f"Total road length: {total_km:,.2f} km")
    print(f"Graph file: {args.output} ({file_size:,} bytes)")
    print(
        "Dropped components: "
        f"{component_report['dropped_nodes']:,} nodes, "
        f"{component_report['dropped_edges']:,} edges"
    )
    for component in component_report["largest_dropped"]:
        print(
            "  "
            f"{component['nodes']:,} nodes, {component['edges']:,} edges; "
            f"sample road: {component['sample_name'] or '(unnamed)'}"
        )
if __name__ == "__main__":
    main()