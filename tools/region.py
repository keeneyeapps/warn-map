"""Shared loader for a region pack (regions/<id>/region.json) used by the build scripts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_region(path):
    """The parsed region.json; data paths in it are relative to the repository root."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def root_path(relative):
    """A region.json path (relative to the repository root) as an absolute Path."""
    return ROOT / relative
