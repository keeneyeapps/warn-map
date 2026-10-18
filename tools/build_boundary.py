"""Print a rough region boundary: the convex hull of the region's address points, pushed out by a margin.

    python tools/build_boundary.py --region regions/kenai/region.json

Paste the printed polygon into region.json as "boundary". Suggestions prefer places inside it.
"""
import argparse
import json
import math
from pathlib import Path

from region import load_region, root_path

METERS_PER_DEGREE = 111_320


def convex_hull(points):
    points = sorted(set(points))
    if len(points) < 3:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def push_out(hull, margin_m):
    """Move each vertex margin_m away from the hull's centre (a rough buffer; fine for a 'core area' test)."""
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)
    out = []
    for x, y in hull:
        dx = (x - cx) * METERS_PER_DEGREE * math.cos(math.radians(cy))
        dy = (y - cy) * METERS_PER_DEGREE
        length = math.hypot(dx, dy) or 1
        scale = (length + margin_m) / length
        out.append([round(cx + (x - cx) * scale, 3), round(cy + (y - cy) * scale, 3)])
    return out


def main():
    parser = argparse.ArgumentParser(description="Print a region boundary from its address-point hull")
    parser.add_argument("--region", type=Path, required=True)
    parser.add_argument("--margin-m", type=float, default=3000)
    args = parser.parse_args()
    region = load_region(args.region)
    index = json.loads(root_path(region["files"]["addresses"]["path"]).read_text(encoding="utf-8"))
    hull = convex_hull([(row[5], row[6]) for row in index["addresses"]])
    boundary = push_out(hull, args.margin_m)
    print(f"Hull of {len(index['addresses']):,} address points: {len(boundary)} vertices")
    print(json.dumps(boundary, separators=(", ", ": ")))


if __name__ == "__main__":
    main()
