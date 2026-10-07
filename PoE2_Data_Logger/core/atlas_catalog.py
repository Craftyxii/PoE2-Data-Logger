"""Offline, versioned Path of Exile 2 Atlas data and artwork.

The data is game-derived, not a character passive tree. Catalog node IDs are
the game's stable internal IDs; graph hashes are retained separately.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

ATLAS_DIR = Path(__file__).resolve().parent.parent / "atlas"


@lru_cache(maxsize=1)
def catalog() -> dict:
    """Load the bundled catalog once without network or OCR initialization."""
    with gzip.open(ATLAS_DIR / "catalog.json.gz", "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("nodes"), dict):
        raise ValueError("Unsupported bundled Atlas catalog")
    return data


def asset_path(relative: str) -> Path:
    """Resolve a bundled artwork path while rejecting paths outside the bundle."""
    path = (ATLAS_DIR / relative).resolve()
    if not path.is_relative_to(ATLAS_DIR.resolve()):
        raise ValueError("Atlas asset path must stay inside the Atlas bundle")
    return path
