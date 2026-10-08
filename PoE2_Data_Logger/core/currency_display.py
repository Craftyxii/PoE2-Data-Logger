"""Offline artwork for the rolling currency counter, independent of OCR.

The fixed bundled catalog is cached once. Local reference images are supplied
by the caller on each refresh, so new labels and deleted examples take effect
without retaining user artwork in a global cache.
"""
from __future__ import annotations

import io
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from PIL import Image, UnidentifiedImageError


_CATALOG = Path(__file__).resolve().parent.parent / "third_party" / "currency_overlay" / "inventory-icons.json"
_MAX_REFERENCE_BYTES = 500000
_MAX_REFERENCE_PIXELS = 1000000


def _name_key(name):
    """Strip and case-fold string labels for artwork matching, rejecting other types."""
    return name.strip().casefold() if isinstance(name, str) else ""


def _png(image):
    """Encode a prepared Pillow image as PNG bytes for the counter UI."""
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@lru_cache(maxsize=1)
def _bundled_pngs():
    """Render the bundled PNG-source pixel catalog without fetching its URLs."""
    try:
        catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
        size = catalog["size"]
        entries = catalog["icons"]
        if type(size) is not int or not 1 <= size <= 96 or not isinstance(entries, list):
            return {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("members"), list):
            continue
        rgba = entry.get("rgba")
        if not isinstance(rgba, list) or len(rgba) != size * size * 4:
            continue
        try:
            artwork = _png(Image.frombytes("RGBA", (size, size), bytes(rgba)))
        except (TypeError, ValueError, OverflowError):
            continue
        for name in entry["members"]:
            key = _name_key(name)
            if key:
                result.setdefault(key, artwork)
    return result


def _reference_png(raw):
    """Bound imported artwork and keep full equipment footprints in proportion."""
    def render(source):
        """Bound decoded dimensions and fit the full artwork within 96 pixels per side."""
        if (min(source.size) < 1 or max(source.size) > 1024 or
                source.width * source.height > _MAX_REFERENCE_PIXELS):
            return None
        image = source.convert("RGBA")
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        return _png(image)
    try:
        if isinstance(raw, Image.Image):
            return render(raw)
        if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_REFERENCE_BYTES:
            return None
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ("PNG", "JPEG"):
                return None
            return render(source)
    except (OSError, ValueError, TypeError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def icon_png(name, references=()):
    """Return PNG bytes for a canonical tracked name, or None for a placeholder.

    Bundled art supplies standard currency, omens and other catalog items.
    Caller-supplied learned/manual references supply custom names of every
    tracked type. Unknown names are never interpreted as paths or URLs.
    """
    key = _name_key(name)
    if not key:
        return None
    bundled = _bundled_pngs().get(key)
    if bundled is not None:
        return bundled
    # Newer learned corrections precede legacy manual examples. A damaged
    # example can fall back to another valid reference for the same label.
    matched = [entry for entry in references if isinstance(entry, Mapping) and
               _name_key(entry.get("name")) == key]
    matched.sort(key=lambda entry: bool(entry.get("reviewed")), reverse=True)
    for entry in matched:
        artwork = _reference_png(entry.get("image"))
        if artwork is not None:
            return artwork
    return None
