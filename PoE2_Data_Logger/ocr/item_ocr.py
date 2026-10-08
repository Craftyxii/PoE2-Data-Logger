"""Turn item, inventory and Ritual captures into suggestions for review.

Shared RapidOCR calls use OCR_LOCK; currency readers remain worker-local.
Ritual grid geometry establishes occupied footprints, while labels, icon
references and review flags separately describe identity and count evidence."""

from __future__ import annotations

import io
import re
import hashlib
import threading
from collections import OrderedDict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageOps

from PoE2_Data_Logger.ocr.opened_scan import OCR_LOCK, _engine
from PoE2_Data_Logger.ocr import currency_ocr
from PoE2_Data_Logger.ocr.affix_capture import affix_key, affix_unit, modifier_value, looks_like_modifier


_RITUAL_MATCH_CACHE = OrderedDict()
_RITUAL_MATCH_LOCK = threading.Lock()


def _key(text):
    """Normalize arbitrary item text to lowercase alphanumerics for name comparisons."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _reference_image(raw, maximum):
    """Decode a supplied PIL image or bytes only within pixel/dimension bounds, then thumbnail it."""
    def bounded(source):
        """Reject oversized source images and return a bounded RGB thumbnail."""
        if (min(source.size) < 1 or max(source.size) > 8192 or
                source.width * source.height > 12_000_000):
            return None
        image = source.convert("RGB")
        image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
        return image

    try:
        if isinstance(raw, Image.Image):
            return bounded(raw)
        if not isinstance(raw, bytes):
            return None
        with Image.open(io.BytesIO(raw)) as source:
            return bounded(source)
    except (TypeError, ValueError, OSError, Image.DecompressionBombError):
        return None


def _reviewed_pixels(image):
    """Normalize reviewed one-cell artwork while masking counts and retaining tier badges."""
    pixels = np.asarray(image.convert("RGB").resize((40, 40), Image.Resampling.LANCZOS),
                        dtype=np.uint8).copy()
    # Stack counts can change between scans. Keep the lower-right tier badge:
    # shared essence artwork must not teach every tier the same name.
    pixels[:15, :26] = [26, 26, 40]
    return Image.fromarray(pixels)


def _reviewed_bank(reader, references, shape=(1, 1)):
    """Prepare only reviewed references with the requested footprint and retain their item kinds."""
    examples, kinds = [], {}
    for reference in references:
        if (not reference.get("reviewed") or
                (reference.get("columns", 1), reference.get("rows", 1)) != shape):
            continue
        name = reference.get("name")
        if not isinstance(name, str) or not name:
            continue
        sample = _reference_image(reference.get("image"), 384)
        if sample is not None:
            if shape == (1, 1):
                sample = _reviewed_pixels(sample)
            examples.append({"name": name, "image": sample})
            kinds[name] = reference.get("kind", "item")
    if examples and hasattr(reader, "prepare_examples"):
        examples = reader.prepare_examples(examples)
    return examples, kinds, shape


def _reviewed_match(reader, image, bank, catalog=None):
    """Accept a reviewed-reference override only for an unambiguous score > -450 and lead > 250.

    Shared catalog artwork still uses tier resolution, so a reviewed label cannot
    replace the badge check for multiple currency variants.
    """
    examples, kinds, shape = bank
    if not examples or (catalog and (catalog.get("shared_icon") or len(catalog.get("members") or []) > 1)):
        # A labelled Chaos/Exalted/etc. picture cannot replace the existing
        # badge resolver: several tiers deliberately share that artwork.
        return None
    ranked = reader.examples(_reviewed_pixels(image) if shape == (1, 1) else image, examples)
    if not ranked:
        return None
    top = ranked[0]
    runner = next((row["score"] for row in ranked[1:] if row["name"] != top["name"]), float("-inf"))
    # Only a close, unambiguous captured match overrides a bundled label.
    # We retain the legacy fallback threshold for existing manual references.
    if top["score"] > -450 and top["score"] - runner > 250:
        return top["name"], max(0, 1 + top["score"] / 8000), kinds[top["name"]]
    return None


def is_ritual_page(lines):
    """Recognize Ritual UI wording from .75+ rows without claiming reward-grid completeness."""
    text = " ".join(str(row.get("text", "")) for row in lines
                    if isinstance(row, dict) and row.get("score", 0) >= .75).casefold()
    title = bool(re.search(r"\bfavou?rs\b", text))
    tribute = bool(re.search(r"\btribute\b", text))
    controls = bool(re.search(r"\bdefer(?:red|\s+mode)?\b|\breroll\s+favou?rs\b|offer\s+tribute", text))
    return tribute and (title or controls)


def ocr_lines(image):
    """Read text under the shared engine lock, then merge nearby rows with their original parts.

    Merged confidence is the weakest part, and part boxes remain available for
    spatial parsing and local retries.
    """
    if isinstance(image, (str, Path)):
        with Image.open(image) as source:
            image = source.convert("RGB")
    with OCR_LOCK:
        result = _engine()(image.convert("RGB"))
    rows = []
    if result.boxes is not None:
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if text.strip():
                rows.append({"text": text.strip(), "score": float(score),
                             "x": min(float(p[0]) for p in box),
                             "y": min(float(p[1]) for p in box),
                             "right": max(float(p[0]) for p in box),
                             "bottom": max(float(p[1]) for p in box)})
    rows.sort(key=lambda row: (row["y"], row["x"]))
    groups = []
    for row in rows:
        group = next((g for g in reversed(groups[-3:]) if abs(g["y"] - row["y"]) < 12), None)
        if group is None:
            groups.append({"y": row["y"], "parts": [row]})
        else:
            group["parts"].append(row)
    return [{"text": " ".join(part["text"] for part in sorted(g["parts"], key=lambda r: r["x"])),
             "score": min(part["score"] for part in g["parts"]),
             "x": min(part["x"] for part in g["parts"]), "y": g["y"],
             "right": max(part["right"] for part in g["parts"]),
             "bottom": max(part["bottom"] for part in g["parts"]),
             "parts": g["parts"]}
            for g in groups]


def _affix_match(text, names):
    """Resolve known aliases/exact affix keys, then require .96 fuzzy similarity and a .035 lead."""
    raw = affix_key(text)
    aliases = {
        "Monsters have increased Effectiveness": "Effectiveness",
        "increased Effectiveness": "Effectiveness",
        "increased Monster Rarity": "Monster Rarity",
        "increased Pack Size": "Pack Size",
        "increased Pack Size in Map": "Pack Size",
        "increased Rarity of Items found in Map": "Rarity Of Items Found In Map",
        "increased Gold found in Map": "Increased Gold Found In Map",
        "Map has increased Monster Rarity": "Monster Rarity",
        "Map has increased number of Rare Monsters": "Rare Monster%",
        "Map has increased Magic Monsters": "Magic Monster %",
        "increased Quantity of Waystones found in Map": "Waystone %",
        "Map has increased chance to contain Essences": "Chance to Contain Essences",
        "Map contains increased number of Runic Monster Markers": "% Increased Monster Markers",
        "Expeditions have Surpassing chance to contain an additional Verisium Remnant":
            "Surpassing Chance for a additional remnant",
    }
    for full, alias in aliases.items():
        if raw == affix_key(full) and alias in names:
            return alias, 1.0
    for name in names:
        if raw and raw == affix_key(name):
            return name, 1.0
    scored = []
    for name in names:
        target = affix_key(name)
        score = SequenceMatcher(None, raw, target).ratio()
        scored.append((score, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < .96 or (len(scored) > 1 and scored[0][0] - scored[1][0] < .035):
        return None, scored[0][0] if scored else 0.0
    return scored[0][1], scored[0][0]


def merge_tablet_lines(lines):
    """Join likely wrapped modifier continuations, preserving the weaker OCR score and final bottom."""
    merged = []
    for source in lines:
        row = dict(source) if isinstance(source, dict) else {"text": str(source)}
        text = row["text"].strip()
        previous = merged[-1] if merged else None
        wraps = bool(previous and (re.search(r"\b(?:an?|in|of|to|from|with|contain|additional|before)$",
                                             previous["text"], re.I) or
                                   re.match(r"^additional\b", text, re.I) or
                                   text.lower() in ("map", "maps", "remnant", "remnants")))
        continuation = bool(wraps and
                            re.search(r"[A-Za-z]", text) and
                            not re.search(r"\d\s*%", text) and
                            not re.match(r"^(?:Can be used|Right click|Shift click|Item Level|"
                                         r"Uses Remaining|Corrupted|Mirrored|Unstash|Tablet|"
                                         r"The first|Map (?:has|contains)|Expeditions (?:have|are))\b", text, re.I) and
                            ("y" not in previous or "y" not in row or
                             0 <= row["y"] - previous["y"] <= 45))
        if continuation:
            previous["text"] += " " + text
            previous["score"] = min(float(previous.get("score", 1)), float(row.get("score", 1)))
            if "bottom" in row:
                previous["bottom"] = row["bottom"]
        else:
            merged.append(row)
    return merged


def parse_tablet(lines, affixes):
    """Match extracted values to same-unit known affixes and return up to four unique proposals.

    Unknown wording, unit conflicts and extra matches remain uncertain; ready
    means the parsing constraints passed, not that OCR was manually approved.
    """
    found, uncertain = [], []
    for line in merge_tablet_lines(lines):
        text = line["text"] if isinstance(line, dict) else str(line)
        amount = modifier_value(text)
        if not amount:
            if looks_like_modifier(text):
                uncertain.append(text)
            continue
        name, score = _affix_match(text, affixes)
        if (name and affix_unit(name) == amount["unit"] and
                name not in [item["affix"] for item in found]):
            found.append({"affix": name, "value": amount["value"], "unit": amount["unit"],
                          "score": min(score, float(line.get("score", 1)) if isinstance(line, dict) else 1),
                          "raw": text})
        else:
            uncertain.append(text)
    return {"matches": found[:4], "uncertain": uncertain + [item["raw"] for item in found[4:]],
            "status": "ready" if found and not uncertain and len(found) <= 4 else "review"}


def inventory_cell(image, slot):
    """Crop a one-based slot from an aligned 12×5 grid, rejecting indices outside 1–60."""
    if slot < 1 or slot > 60:
        raise ValueError("Choose an inventory slot from 1 to 60.")
    column, row = (slot - 1) % 12, (slot - 1) // 12
    left = round(column * image.width / 12)
    top = round(row * image.height / 5)
    right = round((column + 1) * image.width / 12)
    bottom = round((row + 1) * image.height / 5)
    return image.crop((left, top, right, bottom)).convert("RGB")


def inventory_grid(image):
    """Fit the gold/navy divider lattice and crop to an aligned 12×5 grid when supported.

    Require eight horizontal-profile and four vertical-profile hits; insufficient
    evidence returns the supplied image for the caller’s existing workflow.
    """
    if image.info.get("poe2_inventory_aligned"):
        return image
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    gold = (r > g * 1.12) & (g > b * 1.08) & (r > 17) & (r < 170)
    navy = ((b > 20) & (b < 45) & (r >= 5) & (r < 25) &
            (g >= 4) & (g < 22) & (b > r * 1.4) & (r > g))
    separators = gold | navy
    horizontal, vertical = separators.mean(axis=0), separators.mean(axis=1)

    def fit(profile, count, pitches, required, max_padding=1):
        """Search pitches and origins for regularly spaced divider hits, weighting interior evidence."""
        smooth = cv2.dilate(profile.astype(np.float32)[None, :],
                            np.ones((1, 3), np.uint8))[0]
        best = None
        for pitch in pitches:
            span = count * pitch
            if span > len(profile) + 3:
                continue
            starts = np.arange(-2, max(-1.5, min(len(profile) - span + 2,
                                                len(profile) * max_padding)), .5)
            places = starts[:, None] + np.arange(count + 1)[None, :] * pitch
            samples = np.interp(places, np.arange(len(smooth)), smooth, left=0, right=0)
            hits = (samples > .15).sum(axis=1)
            scores = samples[:, 1:-1].sum(axis=1) * 2 + samples[:, (0, -1)].sum(axis=1) * .2 + hits * .1
            scores[hits < required] = -1
            index = int(np.argmax(scores))
            if scores[index] >= 0 and (best is None or scores[index] > best[0]):
                best = float(scores[index]), float(starts[index]), float(pitch)
        return best

    low, high = image.width / 14.5, min(image.width / 11.8, image.height / 4.9)
    if high <= low:
        return image
    xfit = fit(horizontal, 12, np.arange(low, high + .25, .25), 8)
    if xfit is None:
        return image
    pitch = xfit[2]
    yfit = fit(vertical, 5, (pitch,), 4, .18)
    if yfit is None:
        return image
    x, y = xfit[1], yfit[1]
    box = (max(0, round(x + 1)), max(0, round(y + 1)),
           min(image.width, round(x + 12 * pitch)),
           min(image.height, round(y + 5 * yfit[2])))
    cropped = image.crop(box)
    cropped.info["poe2_inventory_aligned"] = True
    return cropped


def _stack_count(cell, read):
    """Read an enlarged upper-left count at .85 confidence, otherwise return an unverified one."""
    corner = cell.crop((0, 0, max(8, round(cell.width * .5)),
                        max(8, round(cell.height * .3))))
    enlarged = ImageOps.expand(corner.resize((corner.width * 6, corner.height * 6),
                                             Image.Resampling.NEAREST), border=20, fill="black")
    digits = []
    for line in read(enlarged):
        match = re.fullmatch(r"\s*(\d{1,5})\s*", line["text"])
        if match and line.get("score", 1) >= .85:
            digits.append((float(line.get("score", 1)), int(match.group(1))))
    if digits:
        return max(digits)[1], False
    return 1, True


def _inventory_labels(image):
    """Read full count and tier labels, holding clipped counts for review."""
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from PoE2_Data_Logger.ocr.inventory_labels import count_crops, tier_crops, fallback_crops

    labels = {slot: {} for slot in range(1, 61)}
    patches, positions = [], []
    counts = {slot: [] for slot in labels}
    tiers = {slot: [] for slot in labels}
    for slot in labels:
        cell = inventory_cell(image, slot)
        number_crops, possible_count = count_crops(cell, complete_only=True)
        labels[slot]["count_present"] = possible_count
        for patch in number_crops:
            patches.append(np.asarray(patch)); positions.append((slot, "count"))
        roman_crops, possible_tier = tier_crops(cell)
        labels[slot]["tier_present"] = possible_tier
        for patch in roman_crops:
            patches.append(np.asarray(patch)); positions.append((slot, "tier"))
        if not possible_count:
            labels[slot]["count"] = 1
    if not patches:
        return labels
    with OCR_LOCK:
        result = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    for (slot, kind), raw, score in zip(positions, result.txts, result.scores):
        text = raw.strip().replace(" ", "").rstrip(".,:;")
        if kind == "count" and re.fullmatch(r"\d{1,6}", text) and float(score) >= .85:
            if 0 < int(text) <= 1000000:
                counts[slot].append((int(text), float(score)))
        elif kind == "tier" and float(score) >= .8:
            roman = text.upper().replace("L", "I").replace("1", "I")
            if roman in ("II", "III"):
                tiers[slot].append(roman)
    for slot in labels:
        readings = counts[slot]
        numbers = [value for value, score in readings if score >= .98]
        if numbers:
            value = max(dict.fromkeys(numbers), key=numbers.count)
            labels[slot]["count_candidate"] = value
            if len(numbers) >= 2 and len(set(numbers)) == 1:
                labels[slot]["count"] = value
        if tiers[slot] and len(set(tiers[slot])) == 1:
            labels[slot]["tier"] = tiers[slot][0]
    fallback_images, fallback_positions = [], []
    for slot in labels:
        if labels[slot].get("count") in (None, 1) and labels[slot]["count_present"]:
            for kind, patch in fallback_crops(inventory_cell(image, slot)):
                fallback_images.append(np.asarray(patch))
                fallback_positions.append((slot, kind))
    if fallback_images:
        with OCR_LOCK:
            result = _engine().text_rec(TextRecInput(img=fallback_images, return_word_box=False))
        alternatives = {}
        for (slot, kind), raw, score in zip(fallback_positions, result.txts, result.scores):
            text = raw.strip().replace(" ", "").rstrip(".,:;")
            if re.fullmatch(r"\d{1,6}", text) and float(score) >= .85 and 0 < int(text) <= 1000000:
                alternatives.setdefault(slot, {"mask": [], "raw": []})[kind].append(int(text))
        for slot, readings in alternatives.items():
            masked, raw = readings["mask"], readings["raw"]
            if labels[slot].get("count") == 1:
                if len(masked) >= 2 and len(set(masked)) == 1 and masked[0] != 1:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = masked[0]
                elif 11 in raw:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = 11
                elif len(raw) >= 2 and len(set(raw)) == 1 and raw[0] != 1:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = raw[0]
                continue
            value = max(dict.fromkeys(masked), key=masked.count) if masked else None
            if value == 1 and 11 in raw:
                value = 11
            elif len(raw) >= 2 and len(set(raw)) == 1 and raw[0] != value:
                value = raw[0]
            if value is not None:
                labels[slot]["count_candidate"] = value
    return labels


def _inventory_equipment_slots(image):
    """Find occupied rectangular multi-cell equipment from missing inventory dividers.

    Only rectangles up to two columns by four rows with content across their
    span are treated as equipment; this helps exclude partial gear icons.
    """
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    groups = [{slot} for slot in range(1, 61)]
    occupied = set()
    for slot in range(1, 61):
        cell = np.asarray(inventory_cell(image, slot), dtype=np.float32)
        dx, dy = max(1, round(cell.shape[1] * .18)), max(1, round(cell.shape[0] * .18))
        core = cell[dy:-dy, dx:-dx]
        if float(np.percentile(core, 95)) >= 32 and float(core.std(axis=(0, 1)).mean()) >= 8:
            occupied.add(slot)

    def join(first, second):
        # Empty neighbouring cells can have the same flat background. They
        # are not evidence that a single stack spans several inventory slots.
        """Merge neighbouring cell groups only when at least one has occupied content."""
        if first not in occupied and second not in occupied:
            return
        a = next(group for group in groups if first in group)
        b = next(group for group in groups if second in group)
        if a is not b:
            a.update(b)
            groups.remove(b)

    def continuous(axis, at, start, end):
        """Check for smooth artwork across a cell boundary while retaining navy divider evidence."""
        radius = max(1, round(min(image.width / 12, image.height / 5) * .04))
        strip = (pixels[start:end, at-radius:at+radius+1] if axis == 1
                 else pixels[at-radius:at+radius+1, start:end])
        r, g, b = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
        navy = (b > r * 1.3) & (b > g * 1.3) & (b < 65)
        if float(navy.mean()) >= .3:
            return False
        changes = np.abs(np.diff(strip, axis=axis)).max(axis=(axis, 2))
        return float((changes < 35).mean()) >= .75

    for row in range(5):
        top, bottom = round(row * image.height / 5), round((row + 1) * image.height / 5)
        for column in range(1, 12):
            if continuous(1, round(column * image.width / 12), top, bottom):
                join(row * 12 + column, row * 12 + column + 1)
    for row in range(1, 5):
        for column in range(12):
            left, right = round(column * image.width / 12), round((column + 1) * image.width / 12)
            if continuous(0, round(row * image.height / 5), left, right):
                join((row - 1) * 12 + column + 1, row * 12 + column + 1)
    equipment = set()
    for group in groups:
        columns = [(slot - 1) % 12 for slot in group]
        rows = [(slot - 1) // 12 for slot in group]
        width, height = max(columns) - min(columns) + 1, max(rows) - min(rows) + 1
        content_columns = {(slot - 1) % 12 for slot in group & occupied}
        content_rows = {(slot - 1) // 12 for slot in group & occupied}
        if (2 <= len(group) <= 8 and width <= 2 and height <= 4 and len(group) == width * height
                and len(content_columns) == width and len(content_rows) == height):
            equipment.update(group)
    return equipment


def scan_inventory_grid(image, references=(), read=None):
    """Match inventory stacks and hold incomplete or conflicting counts for review."""
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    if image.width < 360 or image.height < 180 or image.width * image.height > 12_000_000:
        raise ValueError("Select the complete 12×5 inventory grid.")
    image = inventory_grid(image)
    equipment = _inventory_equipment_slots(image)
    labels = _inventory_labels(image) if read is None else None
    if labels is not None:
        # Similar colours can make separate stacks appear continuous across a
        # grid boundary. A visible stack label keeps that cell reviewable.
        equipment.difference_update(slot for slot, label in labels.items() if label["count_present"])
    read = read or ocr_lines
    reader = currency_ocr.get_reader()
    references = list(references)
    reviewed = _reviewed_bank(reader, references)
    examples = []
    for reference in references:
        if (reference.get("reviewed") or
                (reference.get("columns", 1), reference.get("rows", 1)) != (1, 1)):
            continue
        raw = _reference_image(reference.get("image"), 96)
        if raw is not None:
            examples.append({"name": reference["name"], "image": raw})
    if examples and hasattr(reader, "prepare_examples"):
        examples = reader.prepare_examples(examples)
    found, unknown = [], []
    for slot in range(1, 61):
        cell = inventory_cell(image, slot)
        if float(np.asarray(cell, dtype=np.uint8).std(axis=(0, 1)).mean()) < 8:
            continue
        count_hint = labels[slot].get("count") if labels is not None and labels[slot]["count_present"] else None
        icon = reader.icon(cell, count_digits=len(str(count_hint)) if count_hint is not None else None)
        # A saved reference must not turn a confirmed empty cell into a reward.
        if icon.get("empty"):
            continue
        correction = _reviewed_match(reader, cell, reviewed, catalog=icon) if slot not in equipment else None
        name = correction[0] if correction else None
        score = icon.get("score", 0)
        if correction:
            score = correction[1]
        elif icon.get("family"):
            if labels is not None:
                tier = labels[slot].get("tier", "")
                if len(icon["members"]) > 1 and labels[slot].get("tier_present") and not tier:
                    unknown.append({"slot": slot, "candidate": " / ".join(icon["members"]),
                                    "score": round(score, 3), "reason": "check tier badge"})
                    continue
                name = currency_ocr.resolve_tier(icon["members"], cell,
                                                lambda unused, tier=tier: [{"text": tier, "score": 1}] if tier else [])
            else:
                name = currency_ocr.resolve_tier(icon["members"], cell, read)
            if not name:
                unknown.append({"slot": slot, "candidate": " / ".join(icon["members"]),
                                "score": round(score, 3), "reason": "shared icon; check tier"})
                continue
        elif examples:
            ranked = reader.examples(cell, examples)
            if ranked:
                top = ranked[0]
                runner = next((entry["score"] for entry in ranked[1:]
                               if entry["name"] != top["name"]), float("-inf"))
                if top["score"] > -1200 and top["score"] - runner > 150:
                    name = top["name"]
                    score = top["score"]
                elif top["score"] > -1200:
                    unknown.append({"slot": slot, "candidate": " / ".join(
                        entry["name"] for entry in ranked if top["score"] - entry["score"] <= 150),
                        "score": round(top["score"], 3), "reason": "shared reference; check name"})
                    continue
        if name:
            if labels is not None:
                generic_count = labels[slot].get("count")
                verify_count = generic_count is not None and labels[slot]["count_present"]
                # CurrencyReader.count returns only positive glyph readings
                # meeting its native confidence threshold (currently .76).
                native_count = (reader.count(cell) if verify_count or (generic_count is None and
                                labels[slot].get("count_candidate") is None) else None)
                quantity = (generic_count if generic_count is not None
                            else labels[slot].get("count_candidate") or native_count or 1)
                guessed = generic_count is None
                if verify_count and native_count is not None and native_count != generic_count:
                    # A confident prefix can still omit trailing digits. An
                    # independent glyph reader disagreeing keeps the row held.
                    guessed = True
                    if len(str(native_count)) > len(str(generic_count)):
                        quantity = native_count
            else:
                native_count = reader.count(cell)
                generic_count, generic_unclear = (_stack_count(cell, read) if native_count is None
                                                  else (native_count, False))
                quantity = native_count if native_count is not None else generic_count
                guessed = native_count is None and generic_unclear
            found.append({"slot": slot, "name": name, "quantity": quantity,
                          "score": round(score, 3), "count_needs_review": guessed})
        elif slot not in equipment and icon.get("all") and (icon.get("uncertain") or score >= .55):
            unknown.append({"slot": slot, "candidate": icon["all"][0]["name"],
                            "score": round(score, 3)})
        elif slot not in equipment and icon.get("ignored"):
            unknown.append({"slot": slot, "candidate": icon.get("candidate", "Unrecognized item"),
                            "score": round(score, 3), "reason": "label if tracked"})
    return {"items": found, "unknown": unknown, "status": "review"}


def _ritual_header_index(lines):
    """Identify available Tribute immediately under the recognised Favours title.

    Reward prices can also end in "Tribute", so only the header sequence may be
    skipped. An intervening reward or unknown label ends that sequence.
    """
    title = False
    for index, line in enumerate(lines):
        text = line.get("text", "") if isinstance(line, dict) else str(line)
        text = re.sub(r"\s+", " ", text).strip()
        score = float(line.get("score", 1)) if isinstance(line, dict) else 1.0
        if re.fullmatch(r"favou?rs", text, re.I) and score >= .75:
            title = True
            continue
        if not title or not text:
            continue
        if re.fullmatch(r"(?:\d{1,2}\s+)?\d[\d,]*\s*Tribute", text, re.I) and score >= .94:
            return index
        if re.fullmatch(r"\d{1,2}", text) and score >= .85:
            continue  # Reroll counter can be a separate OCR row.
        title = False
    return None


def parse_ritual(lines, omen_names):
    """Extract reviewable reward names, quantities and prices while excluding Ritual controls.

    Separate prices attach only to nearby aligned proposals; fuzzy Omen names
    need .75 similarity and a .035 lead, while unmatched text stays visible.
    """
    proposals, unmatched, anchors = [], [], []
    excluded = re.compile(r"^(?:ritual|favou?rs?|defer|reroll|tribute|purchase|refresh|remaining|"
                          r"items?|rewards?|cost|cancel|close|\d[\d, ]*)$", re.I)
    known = [(name, _key(name)) for name in omen_names]
    header_index = _ritual_header_index(lines)
    for line_index, line in enumerate(lines):
        if line_index == header_index:
            continue
        raw = line["text"] if isinstance(line, dict) else str(line)
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            continue
        if re.search(r"^(?:Offer Tribute to the King|Reroll Favou?rs)\b|\bDefer Mode\b", raw, re.I):
            continue
        price_only = re.fullmatch(r"(?:Tribute\s*[:x-]?\s*)?(\d{1,3}(?:,\d{3})+|\d{2,7})"
                                  r"(?:\s*Tribute)?", raw, re.I)
        if price_only:
            y = line.get("y") if isinstance(line, dict) else None
            if y is not None:
                previous = []
                for index, anchor in anchors:
                    gap = y - anchor["y"]
                    if not 0 < gap <= 110 or proposals[index]["tribute"] is not None:
                        continue
                    if all(key in line and key in anchor for key in ("x", "right")):
                        center = (line["x"] + line["right"]) / 2
                        if not anchor["x"] - 30 <= center <= anchor["right"] + 30:
                            continue
                    previous.append((gap, index))
                if previous:
                    item = proposals[min(previous)[1]]
                    item["tribute"] = int(price_only.group(1).replace(",", ""))
                    item["score"] = min(item["score"], float(line.get("score", 1)))
                    continue
            unmatched.append(raw)
            continue
        stack = re.search(r"\s+[x×]\s*(\d{1,6})(?=\s|$)", raw, re.I)
        quantity = int(stack.group(1)) if stack else 1
        priced = (raw[:stack.start()] + raw[stack.end():]).strip() if stack else raw
        amount = re.search(r"(?:\bTribute\s*[:x-]?\s*|\s+)(\d{1,3}(?:,\d{3})+|\d{1,7})\s*$", priced, re.I)
        tribute = int(amount.group(1).replace(",", "")) if amount else None
        title = priced[:amount.start()].strip(" -·:") if amount else priced
        if quantity < 1:
            unmatched.append(raw)
            continue
        if excluded.fullmatch(title) or len(title) < 4 or len(title) > 160 or not re.search("[A-Za-z]", title):
            continue
        score = float(line.get("score", 1)) if isinstance(line, dict) else 1.0
        if score < .55:
            unmatched.append(raw)
            continue
        is_omen = bool(re.search(r"\b[O0]men\b", title, re.I))
        if is_omen:
            ranked = sorted(((SequenceMatcher(None, _key(title), key).ratio(), name)
                             for name, key in known), reverse=True)
            best = ranked[0] if ranked else (0.0, title)
            next_score = ranked[1][0] if len(ranked) > 1 else 0.0
            name = best[1] if best[0] >= .75 and best[0] - next_score >= .035 else title
            category = "Omen"
            name_match = best[0] if name == best[1] else 0.0
        elif re.fullmatch(r"[A-Za-z][A-Za-z' -]{3,70}", title) and score >= .72:
            name, category = title, "Item"
            name_match = 1.0
        else:
            unmatched.append(raw)
            continue
        proposals.append({"category": category, "name": name, "quantity": quantity,
                          "tribute": tribute, "source": raw, "score": round(score, 2),
                          "name_match": round(name_match, 3)})
        if isinstance(line, dict) and all(key in line for key in ("x", "y", "right", "bottom")):
            proposals[-1]["box"] = (line["x"], line["y"], line["right"], line["bottom"])
        if isinstance(line, dict) and line.get("y") is not None:
            anchors.append((len(proposals) - 1, line))
    return {"items": proposals[:120], "unmatched": unmatched + [item["source"] for item in proposals[120:]],
            "raw_text": "\n".join(r["text"] if isinstance(r, dict) else str(r) for r in lines),
            "status": "review"}


def deferred_markers(image, *, grid=None):
    """Locate deferred badges at .86+ correlation and estimate their associated reward boxes.

    A complete grid enables a local sampling-phase retry at reward corners;
    matching marks alone does not identify the reward or prove page coverage.
    """
    with Image.open(Path(__file__).resolve().parent.parent / "deferred_marker.png") as reference:
        marker = np.asarray(reference.convert("L"))
    gray = np.asarray(image.convert("L"))
    scale = 1.0
    found = []
    factors = {.5, .67, .75, .85, 1.0, 1.15, 1.25, 1.5, 2.0}
    if grid is not None:
        left, _, right, _ = grid["bounds"]
        factors.add((right - left) / (12 * 52.65))
    for factor in sorted(factors):
        size = max(8, round(marker.shape[0] * factor * scale))
        if size >= min(gray.shape):
            continue
        sample = cv2.resize(marker, (size, size), interpolation=cv2.INTER_AREA)
        scores = cv2.matchTemplate(gray, sample, cv2.TM_CCOEFF_NORMED)
        for _ in range(100):
            _, score, _, point = cv2.minMaxLoc(scores)
            if score < .86:
                break
            x, y = point
            actual_x, actual_y, unit = x / scale, y / scale, size / scale
            if not any(abs(actual_x - old["x"]) < unit and abs(actual_y - old["y"]) < unit for old in found):
                found.append({"x": actual_x, "y": actual_y, "size": unit, "score": score,
                              "box": (round(actual_x - 1.85 * unit), round(actual_y - 2 * unit),
                                      round(actual_x + unit), round(actual_y + unit))})
            scores[max(0, y - size):y + size + 1, max(0, x - size):x + size + 1] = -1
    if grid is not None and grid.get("evidence", {}).get("complete"):
        # A resize moves an eighteen-pixel gold marker onto fractional pixel
        # coordinates. Integer-sized reference resizing alone can miss that
        # marker. Keep the same score threshold and compare its sampling
        # phases only in the lower-right corner of verified reward boxes.
        # This avoids an expensive phase search across the whole screenshot.
        factor = (grid["bounds"][2] - grid["bounds"][0]) / (12 * 52.65)
        base_size = marker.shape[0] * factor
        sizes = sorted({max(8, int(np.floor(base_size))), max(8, round(base_size)),
                        max(8, int(np.ceil(base_size)) + 1)})
        templates = []
        for size in sizes:
            for dx in (-.75, -.5, -.25, 0., .25, .5, .75):
                for dy in (-.75, -.5, -.25, 0., .25, .5, .75):
                    matrix = np.asarray(((factor, 0., dx), (0., factor, dy)), dtype=np.float32)
                    sample = cv2.warpAffine(marker, matrix, (size, size),
                                            flags=cv2.INTER_LANCZOS4,
                                            borderMode=cv2.BORDER_REPLICATE)
                    templates.append(sample)
        radius = max(2, round(factor * 3))
        for reward in grid["rewards"]:
            left, top, right, bottom = reward["box"]
            if any(left <= old["x"] <= right and top <= old["y"] <= bottom for old in found):
                continue
            x1, y1 = max(0, left, right - sizes[-1] - radius), max(0, top, bottom - sizes[-1] - radius)
            x2, y2 = min(gray.shape[1], right + radius), min(gray.shape[0], bottom + radius)
            corner = gray[y1:y2, x1:x2]
            best = None
            for sample in templates:
                size = sample.shape[0]
                if min(corner.shape[:2]) < size:
                    continue
                _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(corner, sample, cv2.TM_CCOEFF_NORMED))
                if score >= .86 and (best is None or score > best[0]):
                    best = score, x1 + point[0], y1 + point[1], size
            if best is None:
                continue
            score, x, y, size = best
            if not any(abs(x - old["x"]) < size and abs(y - old["y"]) < size for old in found):
                found.append({"x": float(x), "y": float(y), "size": float(size), "score": score,
                              "box": (round(x - 1.85 * size), round(y - 2 * size), x + size, y + size)})
    return sorted(found, key=lambda item: (item["y"], item["x"]))


def _ritual_grid_rerolls(image, grid):
    """Read the whole counter relative to the verified reward grid.

    Tribute text width and OCR text height vary with the amount and font bounds;
    using either to locate this button can clip a second digit. Isolate all
    adjacent numeral outlines from the button art, including the grey disabled
    counter, then require the raw and isolated readings to agree.
    """
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    left, top, right, _ = grid["bounds"]
    pitch = (right - left) / 12
    box = (round(left + pitch * 1.02), round(top - pitch * .90),
           round(left + pitch * 1.62), round(top - pitch * .37))
    if (pitch < 10 or box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height or
            box[2] <= box[0] or box[3] <= box[1]):
        return None
    crop = image.crop(box).convert("RGB")
    pixels = np.asarray(crop)
    bright = ((pixels[:, :, 0] > 160) & (pixels[:, :, 1] > 160) &
              (pixels[:, :, 2] > 140)).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
    candidates = []
    for label, (x, y, width, height, area) in enumerate(stats[1:], 1):
        if (.18 * pitch <= height <= .38 * pitch and .03 * pitch <= y <= .23 * pitch and
                width >= .055 * pitch and area >= max(3, .003 * pitch * pitch)):
            candidates.append((int(x), label, int(y), int(width), int(height)))
    if not candidates:
        return None
    x, label, y, width, height = min(candidates)
    if x >= .30 * pitch:
        return None
    selected = [label]
    glyph_left, glyph_top, glyph_right, glyph_bottom = x, y, x + width, y + height
    for other_x, other_label, other_y, other_width, other_height in sorted(candidates):
        if other_label == label:
            continue
        if (abs(other_y + other_height - (y + height)) > .07 * pitch or
                not .8 * height <= other_height <= 1.25 * height):
            continue
        # Include a second adjacent digit. A separated numeral on this baseline
        # makes the crop ambiguous; never accept only part of the number.
        if not 0 <= other_x - glyph_right <= .10 * pitch:
            return None
        selected.append(other_label)
        glyph_top = min(glyph_top, other_y)
        glyph_right = other_x + other_width
        glyph_bottom = max(glyph_bottom, other_y + other_height)
    glyph_box = (glyph_left, glyph_top, glyph_right, glyph_bottom)
    mask = Image.fromarray(np.where(np.isin(labels, selected), 255, 0).astype(np.uint8)).convert("RGB")
    patches = [np.asarray(ImageOps.expand(patch.resize((patch.width * 3, patch.height * 3)),
                                          border=15, fill="black"))
               for patch in (crop.crop(glyph_box), mask.crop(glyph_box))]
    with OCR_LOCK:
        read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    numbers = [int(text) for text, score in zip(read.txts, read.scores)
               if re.fullmatch(r"\d{1,2}", text) and score >= .97]
    if len(numbers) == 2 and numbers[0] == numbers[1]:
        return numbers[0]
    # Smaller captures can break an outlined digit into several components.
    # Read the entire fixed counter field at two scales rather than accepting
    # a partial glyph mask; both complete readings must still agree.
    raw_crop = image.crop((box[0], round(top - pitch * .85), box[2], box[3])).convert("RGB")
    patches = [np.asarray(ImageOps.expand(raw_crop.resize((raw_crop.width * factor, raw_crop.height * factor)),
                                          border=15, fill="black")) for factor in (3, 6)]
    with OCR_LOCK:
        read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    numbers = [int(text) for text, score in zip(read.txts, read.scores)
               if re.fullmatch(r"\d{1,2}", text) and score >= .97]
    return numbers[0] if len(numbers) == 2 and numbers[0] == numbers[1] else None


def _ritual_grid_tribute(parts, image, grid):
    """Read available Tribute only from the bar above a verified reward grid.

    The ornamental Favours title need not be recognised. Spatially anchoring
    this field also prevents a hovered reward's price becoming the available
    Tribute when the whole-page OCR misses or merges the title.
    """
    left, top, right, _ = grid["bounds"]
    pitch = (right - left) / 12
    box = (round(left + pitch * 2.65), round(top - pitch * 1.25),
           round(left + pitch * 9.5), round(top - pitch * .10))
    if (pitch < 10 or box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height or
            box[2] <= box[0] or box[3] <= box[1]):
        return None

    def amount(row):
        """Accept one complete numeric Tribute label only when row confidence is at least .94."""
        if float(row.get("score", 0)) < .94:
            return None
        match = re.fullmatch(r"(\d{1,3}(?:,\d{3})+|\d{1,9})\s*Tribute", row.get("text", "").strip(), re.I)
        return int(match.group(1).replace(",", "")) if match else None

    values = set()
    for row in parts:
        if not all(key in row for key in ("x", "y", "right", "bottom")):
            continue
        if (box[0] - 2 <= row["x"] < row["right"] <= box[2] + 2 and
                box[1] - 2 <= row["y"] < row["bottom"] <= box[3] + 2):
            value = amount(row)
            if value is not None:
                values.add(value)
    if values:
        return next(iter(values)) if len(values) == 1 else None
    crop = image.crop(box).convert("RGB")
    if float(np.asarray(crop).std()) < 5:
        return None
    if pitch < 45:
        factor = min(3, 54 / pitch)
        crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.LANCZOS)
    values = {value for row in ocr_lines(crop) if (value := amount(row)) is not None}
    return next(iter(values)) if len(values) == 1 else None


def ritual_totals(lines, image=None, grid=None):
    """Read available Tribute and rerolls from grid-anchored fields or a verified text header.

    With an image but no grid, unrelated Tribute text cannot substitute for the header.
    """
    tribute = None
    rerolls = None
    header = None
    # Individual OCR parts keep the counter separate when a row is merged with
    # the Tribute heading or unrelated text elsewhere on a full-screen capture.
    parts = [part for row in lines for part in (row.get("parts") or [row])]
    if grid is not None and image is not None:
        return {"tribute_available": _ritual_grid_tribute(parts + list(lines), image, grid),
                "rerolls_remaining": _ritual_grid_rerolls(image, grid)}
    header_index = _ritual_header_index(parts)
    candidates = ([parts[header_index]] if header_index is not None else
                  parts if image is None else [])
    for row in candidates:
        if float(row.get("score", 0)) < .8:
            continue
        match = re.search(r"(\d[\d,]*)\s*Tribute\b", row["text"], re.I)
        if match:
            tribute = int(match.group(1).replace(",", ""))
            header = row
            break
    if header:
        for row in parts:
            if (re.fullmatch(r"\d{1,2}", row["text"].strip()) and row.get("score", 0) >= .85 and
                    row.get("right", 0) < header.get("x", 0) and
                    abs(row.get("y", 0) - header.get("y", 0)) <= max(25, header.get("bottom", 0) - header.get("y", 0))):
                rerolls = int(row["text"].strip())
    return {"tribute_available": tribute, "rerolls_remaining": rerolls}


def _ritual_icon_matches(shown, icon, scale, page_key):
    """Cache .965+ icon correlation occurrences across three artwork scales.

    The cache lock protects lookup/update only; expensive matching runs outside it
    and returned boxes are converted back to capture coordinates.
    """
    key = (page_key, icon.shape, hashlib.sha256(icon.tobytes()).digest(), scale)
    with _RITUAL_MATCH_LOCK:
        cached = _RITUAL_MATCH_CACHE.get(key)
        if cached is not None:
            _RITUAL_MATCH_CACHE.move_to_end(key)
            return cached
    matches = []
    for factor in (1.0, .8, 1.25):
        width, height = round(icon.shape[1] * scale * factor), round(icon.shape[0] * scale * factor)
        if min(width, height) < 16 or width >= shown.shape[1] or height >= shown.shape[0]:
            continue
        sample = cv2.resize(icon, (width, height), interpolation=cv2.INTER_AREA)
        scores = cv2.matchTemplate(shown, sample, cv2.TM_CCOEFF_NORMED)
        strongest = 0.0
        for _ in range(20):
            _, score, _, point = cv2.minMaxLoc(scores)
            if score < .965:
                break
            strongest = max(strongest, score)
            x, y = point
            matches.append((score, (x / scale, y / scale, width / scale, height / scale)))
            scores[max(0, y - height + 1):y + height,
                   max(0, x - width + 1):x + width] = -1
        if strongest >= .97:
            break
    matches = tuple(matches)
    with _RITUAL_MATCH_LOCK:
        _RITUAL_MATCH_CACHE[key] = matches
        _RITUAL_MATCH_CACHE.move_to_end(key)
        if len(_RITUAL_MATCH_CACHE) > 256:
            _RITUAL_MATCH_CACHE.popitem(last=False)
    return matches


def _ritual_cell_labels(cells):
    """Read complete count/tier crops for occupied Ritual cells."""
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from PoE2_Data_Logger.ocr.inventory_labels import count_crops, tier_crops

    labels, patches, positions = {}, [], []
    for index, cell in cells.items():
        number_crops, possible_count = count_crops(cell, complete_only=True)
        roman_crops, possible_tier = tier_crops(cell)
        labels[index] = {"count_present": possible_count, "tier_present": possible_tier,
                         "count_verified": False}
        for kind, crops in (("count", number_crops), ("tier", roman_crops)):
            for patch in crops:
                patches.append(np.asarray(patch)); positions.append((index, kind))
    if not patches:
        return labels
    with OCR_LOCK:
        read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    counts, tiers = {}, {}
    for (index, kind), raw, score in zip(positions, read.txts, read.scores):
        text = raw.strip().replace(" ", "").rstrip(".,:;")
        if kind == "count" and re.fullmatch(r"\d{1,6}", text) and float(score) >= .85:
            if 0 < int(text) <= 1000000:
                counts.setdefault(index, []).append((int(text), float(score)))
        elif kind == "tier" and float(score) >= .8:
            roman = text.upper().replace("L", "I").replace("1", "I")
            if roman in ("II", "III"):
                tiers.setdefault(index, []).append(roman)
    for index in labels:
        values = counts.get(index, [])
        if values:
            labels[index]["count_candidate"] = max(values, key=lambda value: value[1])[0]
            confident = [value for value, score in values if score >= .98]
            if len(confident) >= 2 and len(set(confident)) == 1:
                labels[index]["count"] = confident[0]
                labels[index]["count_verified"] = True
        if tiers.get(index) and len(set(tiers[index])) == 1:
            labels[index]["tier"] = tiers[index][0]
    return labels


def _ritual_reward_icon(reader, cell, label, examples, icon=None):
    """Resolve one-cell Ritual artwork with count masking, tier checks and local reference margins.

    Inventory-ignored families remain eligible as Ritual rewards, and ambiguous
    shared tiers or example names stay as candidates for review.
    """
    count = label.get("count") or label.get("count_candidate")
    count_digits = len(str(count)) if count is not None else None
    icon = reader.icon(cell, count_digits=count_digits) if icon is None else icon
    candidates = []
    # Maps, tablets and runes are excluded from currency inventories, but they
    # are legitimate Ritual rewards. Keep the same weighted match thresholds.
    if icon.get("ignored") and hasattr(reader, "inventory_ranked"):
        ranked = reader.inventory_ranked(cell, calibrated=True, count_digits=count_digits)
        if ranked:
            top = ranked[0]
            margin = top["score"] - ranked[1]["score"] if len(ranked) > 1 else float("inf")
            clear = ((top["score"] > -3000 and margin > 300) or
                     (top["score"] > -3200 and margin > 1500))
            if clear:
                icon = {"family": top["name"], "members": reader.inventory_members[top["name"]],
                        "score": max(0, 1 + top["score"] / 8000), "method": "inventory"}
    name = None
    if icon.get("family"):
        members = icon.get("members") or []
        candidates = list(members)
        if not (len(members) > 1 and label.get("tier_present") and not label.get("tier")):
            tier = label.get("tier", "")
            name = currency_ocr.resolve_tier(members, cell,
                                            lambda unused: [{"text": tier, "score": 1}] if tier else [])
    else:
        candidates = [row["name"] for row in icon.get("all") or [] if row.get("name")]
        if examples:
            ranked = reader.examples(cell, examples)
            if ranked:
                top = ranked[0]
                runner = next((row["score"] for row in ranked[1:]
                               if row["name"] != top["name"]), float("-inf"))
                if top["score"] > -1200 and top["score"] - runner > 150:
                    name = top["name"]
                    icon = {"score": max(0, 1 + top["score"] / 8000), "method": "local reference"}
                elif top["score"] > -1200:
                    candidates = [row["name"] for row in ranked if top["score"] - row["score"] <= 150]
    return name, float(icon.get("score", 0)), candidates


def _ritual_grid_items(image, grid, parsed, markers, omen_names, references):
    """Identify each verified reward footprint without including its frame.

    The frame grows with the reward grid at higher game resolutions. Keeping
    the same proportional inset avoids shrinking the icon artwork differently
    between standard and 4K captures.
    """
    from PoE2_Data_Logger.core.review_learning import footprint
    reader = currency_ocr.get_reader()
    references = list(references)
    reviewed = {}
    cells = {}
    frame_inset = max(1, round(grid["pitch"] * .02))
    for index, reward in enumerate(grid["rewards"]):
        if len(reward["slots"]) == 1:
            left, top, right, bottom = reward["box"]
            # Keep the game icon's cell proportions, excluding the frame line.
            cells[index] = image.crop((left + frame_inset, top + frame_inset, right, bottom)).convert("RGB")
    labels = _ritual_cell_labels(cells)
    examples = []
    for reference in references:
        if (reference.get("reviewed") or
                (reference.get("columns", 1), reference.get("rows", 1)) != (1, 1) or
                not reference.get("name")):
            continue
        sample = _reference_image(reference.get("image"), 96)
        if sample is not None:
            examples.append({"name": reference["name"], "image": sample})
    if examples and hasattr(reader, "prepare_examples"):
        examples = reader.prepare_examples(examples)
    deferred_rewards = set()
    for marker in markers:
        nearby = []
        for index, reward in enumerate(grid["rewards"]):
            left, top, right, bottom = reward["box"]
            x, y = marker["x"], marker["y"]
            if left - 2 <= x <= right + 2 and top - 2 <= y <= bottom + 2:
                inside = left <= x <= right and top <= y <= bottom
                distance = (x - (left + right) / 2) ** 2 + (y - (top + bottom) / 2) ** 2
                nearby.append((not inside, distance, index))
        if nearby:
            deferred_rewards.add(min(nearby)[2])
    items = []
    for index, reward in enumerate(grid["rewards"]):
        box = tuple(reward["box"])
        multiple = len(reward["slots"]) > 1
        label = labels.get(index, {})
        shape = footprint(reward["slots"])
        correction = None
        catalog_icon = None
        if not multiple:
            count = label.get("count") or label.get("count_candidate")
            catalog_icon = reader.icon(cells[index], count_digits=len(str(count)) if count is not None else None)
        if shape is not None:
            if shape not in reviewed:
                reviewed[shape] = _reviewed_bank(reader, references, shape)
            shown = (image.crop((box[0] + frame_inset, box[1] + frame_inset, box[2], box[3])).convert("RGB")
                     if multiple else cells[index])
            correction = _reviewed_match(reader, shown, reviewed[shape], catalog=catalog_icon)
        if correction:
            name, score, kind = correction
            candidates = []
        else:
            name, score, candidates = (None, 0.0, []) if multiple else _ritual_reward_icon(
                reader, cells[index], label, examples, icon=catalog_icon)
            kind = "omen" if name in omen_names else "item"
        quantity = 1 if multiple else label.get("count") or label.get("count_candidate") or 1
        deferred = index in deferred_rewards
        item = {"category": "Omen" if kind == "omen" else "Item", "name": name or "",
                "quantity": quantity, "tribute": None,
                "source": "icon reference: Ritual reward grid" if name else "Occupied Ritual reward; name unresolved",
                "score": round(score, 3), "name_match": 1.0 if name in omen_names else 0.0,
                "needs_review": True, "name_needs_review": not bool(name), "unresolved": not bool(name),
                "category_verified": bool(name) or multiple,
                "count_needs_review": False if multiple else not label.get("count_verified", False),
                "deferred": deferred, "box": box, "grid_slots": list(reward["slots"])}
        if not name and candidates:
            item["candidate_names"] = candidates
        # Text may confirm an ambiguous occupied icon or its price. It cannot
        # introduce extra rewards from tooltip prose or surrounding UI.
        for proposal in parsed:
            anchor = proposal.get("box")
            if anchor is None:
                continue
            x, y = (anchor[0] + anchor[2]) / 2, (anchor[1] + anchor[3]) / 2
            if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                continue
            if (proposal["name"] == name or proposal["name"] in candidates) and float(proposal.get("name_match", 0)) >= .9:
                item.update(name=proposal["name"], category=proposal["category"], name_match=proposal["name_match"],
                            unresolved=False, name_needs_review=False, tribute=proposal.get("tribute"))
        items.append(item)
    return items


def scan_ritual_page(image, omen_names, references=()):
    """Combine OCR metadata, grid footprints, icon references and deferred markers for review.

    A complete grid limits rewards to occupied footprints; without one, text and
    Omen occurrences form a fallback with partial-grid coverage uncertainty retained.
    Identity, quantity and page coverage remain separate review evidence.
    """
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    rows = ocr_lines(image)
    result = parse_ritual(rows, omen_names)
    from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid, has_grid_structure
    header = next((row for row in rows if re.fullmatch(r"favou?rs", row["text"].strip(), re.I)
                   and float(row.get("score", 0)) >= .75), None)
    box_keys = ("x", "y", "right", "bottom")
    header_box = tuple(header[key] for key in box_keys) if header and all(key in header for key in box_keys) else None
    grid = detect_reward_grid(image, header_box=header_box)
    markers = deferred_markers(image, grid=grid)
    result.update(ritual_totals(rows, image, grid=grid))
    if grid is not None:
        result["items"] = _ritual_grid_items(image, grid, result["items"], markers, omen_names, references)
        result.update(grid_detected=True, grid_reward_count=len(grid["rewards"]),
                      grid_bounds=tuple(grid["bounds"]), grid_confidence=grid.get("confidence"),
                      grid_evidence=dict(grid.get("evidence") or {}),
                      coverage_uncertain=False, reward_count=len(result["items"]),
                      unresolved_count=sum(bool(item["unresolved"]) for item in result["items"]),
                      deferred_count=len(markers))
        return result
    result.update(grid_detected=False, coverage_uncertain=header is not None or has_grid_structure(image))
    omens = [item for item in result["items"] if item["category"] == "Omen"]
    supplied = list(references)
    supplied_names = {reference.get("name") for reference in supplied}
    references = supplied + [reference for reference in currency_ocr.omen_references()
                             if reference["name"] not in supplied_names]
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    scale = min(1.0, 1100 / max(image.size))
    shown = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if scale < 1:
        shown = cv2.resize(shown, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if (shown == shown[0, 0]).all():
        references = []
    page_key = (shown.shape, hashlib.sha256(shown.tobytes()).digest())
    candidates = []
    for reference in list(references)[:128]:
        name = reference.get("name")
        if name not in omen_names:
            continue
        stored = _reference_image(reference.get("image"), 512)
        if stored is None:
            continue
        icon = np.asarray(stored, dtype=np.uint8)
        if (min(icon.shape[:2]) < 20 or float(icon.std()) < 12 or
                (icon == icon[0, 0]).all()):
            continue
        candidates.extend((score, name, location)
                          for score, location in _ritual_icon_matches(shown, icon, scale, page_key))
    occurrences = []
    for candidate in sorted(candidates, reverse=True):
        x, y, width, height = candidate[2]
        if any(x < a + w and a < x + width and y < b + h and b < y + height
               for _, _, (a, b, w, h) in occurrences):
            continue
        occurrences.append(candidate)
        if len(occurrences) == 20:
            break
    occurrences.sort(key=lambda candidate: (candidate[2][1], candidate[2][0]))
    occurrence_markers = {index: [] for index in range(len(occurrences))}
    for marker_index, marker in enumerate(markers):
        nearby = []
        for index, (_, _, (x, y, width, height)) in enumerate(occurrences):
            if (x - width * .25 <= marker["x"] <= x + width * 1.25 and
                    y - height * .25 <= marker["y"] <= y + height * 1.25):
                distance = (marker["x"] - (x + width)) ** 2 + (marker["y"] - (y + height)) ** 2
                nearby.append((distance, index))
        if nearby:
            occurrence_markers[min(nearby)[1]].append(marker_index)
    assigned = {}
    for name in {candidate[1] for candidate in occurrences}:
        named = [item for item in omens if item["name"] == name]
        locations = [index for index, candidate in enumerate(occurrences) if candidate[1] == name]
        pairs = []
        for item_index, item in enumerate(named):
            box = item.get("box")
            for index in locations:
                x, y, width, height = occurrences[index][2]
                distance = ((x + width / 2 - (box[0] + box[2]) / 2) ** 2 +
                            (y + height / 2 - (box[1] + box[3]) / 2) ** 2) if box else float("inf")
                pairs.append((distance, item_index, index))
        used_items = set()
        for _, item_index, index in sorted(pairs):
            if item_index in used_items or index in assigned:
                continue
            assigned[index] = named[item_index]
            used_items.add(item_index)
            if len(locations) > 1 and not named[item_index].get("box"):
                named[item_index]["needs_review"] = True
    matched_markers = set()
    for index, (score, name, location) in enumerate(occurrences):
        nearby = occurrence_markers[index]
        matched_markers.update(nearby)
        if index in assigned:
            assigned[index]["deferred"] = bool(assigned[index].get("deferred") or nearby)
        else:
            x, y, width, height = location
            result["items"].append({"category": "Omen", "name": name, "quantity": 1,
                                    "tribute": None, "source": f"icon reference {score:.2f}",
                                    "score": round(score, 2), "deferred": bool(nearby),
                                    "box": (x, y, x + width, y + height),
                                    "needs_review": True})
    for index, marker in enumerate(markers):
        if index not in matched_markers:
            result["items"].append({"category": "Item", "name": "", "quantity": 1,
                                    "tribute": None, "source": "Deferred marker; reward name not resolved",
                                    "score": round(marker["score"], 2), "deferred": True,
                                    "needs_review": True, "unresolved": True, "name_needs_review": True,
                                    "count_needs_review": True, "box": tuple(marker.get("box", ()))})
    result["deferred_count"] = len(markers)
    result["reward_count"] = len(result["items"])
    result["unresolved_count"] = sum(not bool(item.get("name")) for item in result["items"])
    return result
