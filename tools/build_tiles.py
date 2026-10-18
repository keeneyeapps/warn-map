import argparse
import shutil
import subprocess
from pathlib import Path

from region import ROOT, load_region, root_path


def main():
    parser = argparse.ArgumentParser(description="Extract a region's basemap tiles from a Protomaps build")
    parser.add_argument("--region", type=Path, required=True, help="region pack, e.g. regions/kenai/region.json")
    parser.add_argument("--source", help="input .pmtiles URL or path (default: the region's tiles source url)")
    parser.add_argument("--pmtiles", help="pmtiles CLI (default: on PATH, else tools/pmtiles.exe)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be extracted without downloading")
    args = parser.parse_args()

    region = load_region(args.region)
    tiles = region["files"]["tiles"]
    pmtiles = args.pmtiles or shutil.which("pmtiles") or str(ROOT / "tools" / "pmtiles.exe")
    output = root_path(tiles["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        pmtiles, "extract", args.source or tiles["source"]["url"], str(output),
        "--bbox=" + ",".join(str(value) for value in region["bbox"]),
        f"--maxzoom={tiles['maxzoom']}",
    ]
    if args.dry_run:
        command.append("--dry-run")
    print(" ".join(command))
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
