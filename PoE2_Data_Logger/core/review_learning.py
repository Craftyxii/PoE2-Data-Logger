"""Prepare captured, corrected review artwork for local recognition.

These are labelled icon examples, not new OCR models. Full gear footprints are
kept separate from one-cell currency so a piece of armour never teaches a cell.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image


def validate_name(name, category="Item"):
    if not isinstance(name, str):
        raise ValueError("Enter an item name.")
    name = name.strip()
    maximum = 160 if str(category).title() == "Omen" else 120
    if (not name or len(name) > maximum or
            any(ord(character) < 32 or ord(character) == 127 or
                0xD800 <= ord(character) <= 0xDFFF for character in name)):
        raise ValueError(f"Enter an item name under {maximum} characters without control characters.")
    return name


def footprint(slots, *, columns=12, maximum_rows=10):
    if (not isinstance(slots, (list, tuple)) or not slots or
            any(type(slot) is not int or not 1 <= slot <= columns * maximum_rows for slot in slots) or
            len(set(slots)) != len(slots)):
        return None
    xs = [(slot - 1) % columns for slot in slots]
    ys = [(slot - 1) // columns for slot in slots]
    width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    if width > 2 or height > 4 or len(slots) != width * height:
        return None
    return width, height


def make_example(image, original, name, category, mode):
    """Return an example only for an actual correction with captured evidence.

    Call this for accepted rows, after the edited cell has been submitted. An
    unchanged name, count-only edit, or manually added row produces no example.
    """
    name = str(name or "").strip()
    category = str(category or "").strip().title()
    if not name or not isinstance(image, Image.Image) or not isinstance(original, dict):
        return None
    old = str(original.get("name") or "").strip()
    old_category = str(original.get("category") or "").strip().title()
    if old.casefold() == name.casefold() and (not old_category or old_category == category):
        return None
    if original.get("rejected"):
        return None
    if mode == "currency":
        slot = original.get("slot")
        if type(slot) is not int or not 1 <= slot <= 60:
            return None
        from PoE2_Data_Logger.ocr.item_ocr import inventory_cell, inventory_grid
        crop = inventory_cell(inventory_grid(image), slot)
        shape = (1, 1)
    elif mode == "ritual":
        shape = footprint(original.get("grid_slots"))
        box = original.get("box")
        if shape is None or not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not math.isfinite(value) for value in box):
            return None
        left, top, right, bottom = [round(value) for value in box]
        if not 0 <= left < right <= image.width or not 0 <= top < bottom <= image.height:
            return None
        crop = image.crop((left + 1, top + 1, right, bottom)).convert("RGB")
    else:
        return None
    example = {"name": name, "category": category, "image": crop,
               "columns": shape[0], "rows": shape[1]}
    try:
        encode_example(example)
    except ValueError:
        return None
    return example


def encode_example(example):
    """Validate and bound stored pixel data before a logging transaction."""
    if not isinstance(example, dict):
        raise ValueError("Review icon examples are invalid.")
    image = example.get("image")
    width, height = example.get("columns", 1), example.get("rows", 1)
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 2 or not 1 <= height <= 4):
        raise ValueError("Review artwork needs a complete item footprint.")
    if (not isinstance(image, Image.Image) or min(image.size) < 20 or
            max(image.size) > 1024 or image.width * image.height > 1_000_000 or
            abs(image.width / image.height / (width / height) - 1) > .25):
        raise ValueError("Review artwork must contain one full captured item.")
    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if (width, height) == (1, 1):
        # Match the scanner's existing empty-cell rule without creating another
        # worker-owned recognition session in the UI thread.
        dx, dy = max(1, round(image.width * .18)), max(1, round(image.height * .18))
        inside = pixels[dy:-dy, dx:-dx]
        if float(np.percentile(inside, 95)) < 32:
            raise ValueError("An empty captured cell cannot teach an item icon.")
    if float(pixels.std(axis=(0, 1)).mean()) < 8:
        raise ValueError("An empty captured cell cannot teach an item icon.")
    normalized = image.convert("RGB").resize((96 * width, 96 * height), Image.Resampling.LANCZOS)
    if float(np.asarray(normalized, dtype=np.uint8).std(axis=(0, 1)).mean()) < 8:
        raise ValueError("An empty captured cell cannot teach an item icon.")
    output = io.BytesIO()
    normalized.save(output, format="PNG", optimize=True)
    if output.tell() > 500000:
        raise ValueError("The reviewed icon example is too large.")
    return output.getvalue(), width, height
