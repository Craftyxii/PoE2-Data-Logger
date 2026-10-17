"""Read the four skull counters from screenshot pixels, retaining uncertain readings.

The captured reference has a number immediately BEFORE each skull. Skull shape,
colour, order and spacing establish the counter row before numeric OCR runs.
No game state, process, clipboard, or installed game files are consulted.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr.opened_scan import OCR_LOCK, _engine


CATEGORIES = ("normal", "magic", "rare", "unique")
EXAMPLE = Path(__file__).resolve().parent.parent / "region_examples" / "kills.jpg"
# Half-open bounds in the unchanged 1919x1079 user capture, not drawn artwork.
SKULL_BOUNDS = ((1705, 301, 1723, 322), (1759, 301, 1777, 322),
                (1815, 301, 1833, 322), (1869, 301, 1887, 322))
DEFAULT_REGION = (.85, .24, .15, .12)


@lru_cache(maxsize=1)
def _references():
    """Load actual captured skulls and their foreground colour signatures once."""
    with Image.open(EXAMPLE) as source:
        samples = [np.asarray(source.convert("RGB").crop(box)).copy() for box in SKULL_BOUNDS]
    references = []
    for sample in samples:
        mask = sample.max(axis=2) >= 65
        colour = sample[mask].astype(float)
        colour = np.median(colour / np.maximum(colour.sum(axis=1, keepdims=True), 1), axis=0)
        references.append((sample, colour))
    return references


def _source(image):
    """Bound inputs and map optional full-screen normalization back to original pixels."""
    if isinstance(image, (str, Path)):
        with Image.open(image) as source:
            image = source.copy()
    if not isinstance(image, Image.Image):
        raise ValueError("Choose a screenshot containing the four kill counters.")
    if (min(image.size) < 20 or max(image.size) > 8192 or
            image.width * image.height > 12_000_000):
        raise ValueError("The kill-count screenshot has unsupported dimensions.")
    image = image.convert("RGB")
    # Full screen captures have the reference's 16:9 game composition. Selected
    # regions remain unconstrained in position and are searched at bounded scales.
    if image.height >= 450 and 1.65 <= image.width / image.height <= 1.9:
        scale = (image.width / 1919, image.height / 1079)
        frame = image.resize((1919, 1079), Image.Resampling.LANCZOS)
        box = (1631, 259, 1919, 389)
        return frame.crop(box), (box[0], box[1]), scale, (1.,)
    return image, (0, 0), (1., 1.), (2 / 3, 1., 1.25, 4 / 3, 2.)


def _colour_distance(patch, expected):
    """Compare chromaticity independently from skull template shape."""
    pixels = patch[patch.max(axis=2) >= 65].astype(float)
    if len(pixels) < 15:
        return float("inf")
    colour = np.median(pixels / np.maximum(pixels.sum(axis=1, keepdims=True), 1), axis=0)
    return float(np.abs(colour - expected).sum())


def _locate(image, scales, strictness):
    """Require exactly one coherent ordered four-colour skull row, without filling gaps."""
    pixels = np.asarray(image)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    floor = ocr_sensitivity.review_threshold(.90, strictness, .06)
    groups = []
    for scale in scales:
        found = []
        for sample, colour in _references():
            size = (round(sample.shape[1] * scale), round(sample.shape[0] * scale))
            if min(size) < 8 or size[0] > image.width or size[1] > image.height:
                found = []; break
            template = cv2.resize(sample, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            response = cv2.matchTemplate(gray, cv2.cvtColor(template, cv2.COLOR_RGB2GRAY), cv2.TM_CCOEFF_NORMED)
            choices = []
            # Remove each peak's neighbourhood, avoiding a duplicate match for
            # adjacent pixels while rejecting multiple distinct counter rows.
            for _ in range(12):
                _, score, _, (x, y) = cv2.minMaxLoc(response)
                if score < floor:
                    break
                patch = pixels[y:y + size[1], x:x + size[0]]
                if _colour_distance(patch, colour) <= .12:
                    choices.append({"x": x, "y": y, "w": size[0], "h": size[1], "score": float(score)})
                response[max(0, y - size[1] // 2):y + size[1] // 2 + 1,
                         max(0, x - size[0] // 2):x + size[0] // 2 + 1] = -1
            if len(choices) != 1:
                found = []; break
            found.append(choices[0])
        if len(found) != 4:
            continue
        xs = [p["x"] for p in found]
        ys = [p["y"] for p in found]
        gaps = np.diff(xs)
        width = found[0]["w"]
        if (min(gaps) <= 0 or max(ys) - min(ys) > max(2, scale * 2) or
                min(gaps) < width * 2.5 or max(gaps) > width * 3.5 or
                max(gaps) - min(gaps) > width * .4):
            continue
        quality = min(p["score"] for p in found)
        groups.append((quality, found))
    if not groups:
        return None
    groups.sort(key=lambda group: group[0], reverse=True)
    best = groups[0][1]
    if any(abs(group[1][0]["x"] - best[0]["x"]) > best[0]["w"] or
           abs(group[1][0]["y"] - best[0]["y"]) > best[0]["h"] for group in groups[1:]):
        return None
    return best


def _gold_ink(patch):
    """Locate the captured counter font's gold pixels without interpreting characters."""
    rgb = np.asarray(patch).astype(float)
    return ((rgb[:, :, 0] >= 65) & (rgb[:, :, 1] >= 55) &
            (rgb[:, :, 2] <= rgb[:, :, 0] * .88) & (rgb[:, :, 1] >= rgb[:, :, 0] * .65))


def _number_crops(image, skulls):
    """Bound each number by the preceding skull and reject missing/clipped pixel evidence."""
    pitch = float(np.median(np.diff([p["x"] for p in skulls])))
    crops = []
    for index, skull in enumerate(skulls):
        x, y, width, height = (skull[k] for k in ("x", "y", "w", "h"))
        left = skulls[index - 1]["x"] + width if index else round(x - pitch + width)
        box = (left, round(y - height / 21), x, round(y + height * 24 / 21))
        if min(box[:2]) < 0 or box[2] > image.width or box[3] > image.height:
            return None
        if not index:
            # Agreement between two crops cannot detect a prefix excluded from
            # both. Require captured context to the left of the first inferred
            # number cell, and reject additional character bodies there. This
            # holds widened/unsupported counters instead of accepting a suffix.
            gutter_left = left - (x - left)
            if gutter_left < 0:
                return None
            gutter = _gold_ink(image.crop((gutter_left, box[1], left, box[3])))
            count, _labels, stats, _ = cv2.connectedComponentsWithStats(gutter.astype(np.uint8))
            for component in range(1, count):
                _, _, w, h, area = stats[component]
                divider = w <= max(2, round(width / 9)) and h >= gutter.shape[0] * .8
                speck = area <= max(2, round(width / 4)) and h < height * .3
                if not divider and not speck:
                    return None
        patch = image.crop(box)
        ink = _gold_ink(patch)
        # The capture has thin vertical cell dividers, which are not digits.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8))
        for component in range(1, count):
            _, _, w, h, area = stats[component]
            if ((w <= max(2, round(width / 9)) and h >= patch.height * .8) or
                    (area <= max(2, round(width / 4)) and h < height * .3)):
                ink[labels == component] = False
        yy, xx = np.nonzero(ink)
        if len(xx) < max(8, width * .5):
            return None
        bounds = (int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1)
        if bounds[3] - bounds[1] < height * .5:
            return None
        # Separate numeric tokens within one cell cannot be silently concatenated.
        occupied = np.flatnonzero(ink.sum(axis=0))
        if len(occupied) > 1 and np.diff(occupied).max() > height * .32:
            return None
        tight = ImageOps.expand(patch.crop(bounds), max(2, round(height / 7)), fill=(15, 15, 15))
        crops.append((patch, tight, box))
    return crops


def _read_numbers(crops):
    """Batch two actual model readings per counter; keep zero and reject disagreement."""
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    patches = []
    for raw, tight, _box in crops:
        for patch in (raw, tight):
            factor = 72 / patch.height
            patches.append(np.asarray(patch.resize((max(1, round(patch.width * factor)), 72), Image.Resampling.LANCZOS)))
    with OCR_LOCK:
        read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    return list(zip(read.txts, read.scores))


def scan(image, strictness=ocr_sensitivity.DEFAULT):
    """Return four verified screenshot counts or an incomplete reviewable result.

    Only plain decimal digits are established by the supplied example. Grouped,
    abbreviated, clipped and contradictory values remain incomplete.
    """
    strictness = ocr_sensitivity.validate(strictness)
    result = {key: None for key in CATEGORIES}
    result.update(complete=False, status="The four kill counters need review.", readings=[])
    frame, offset, scale, scales = _source(image)
    skulls = _locate(frame, scales, strictness)
    if skulls is None:
        result["status"] = "Could not verify one complete row of Normal, Magic, Rare and Unique skulls."
        return result
    crops = _number_crops(frame, skulls)
    if crops is None:
        result["status"] = "A kill count is missing, clipped or contains separate numeric readings."
        return result
    numbers = _read_numbers(crops)
    floor = ocr_sensitivity.review_threshold(.90, strictness, .08)
    for index, category in enumerate(CATEGORIES):
        trials = numbers[index * 2:index * 2 + 2]
        values = [int(raw.strip()) if re.fullmatch(r"[0-9]{1,7}", raw.strip()) and score >= floor else None
                  for raw, score in trials]
        accepted = len(values) == 2 and None not in values and values[0] == values[1]
        box = crops[index][2]
        original = [round((box[i] + offset[i % 2]) * scale[i % 2], 2) for i in range(4)]
        result[category] = values[0] if accepted else None
        result["readings"].append({"category": category, "value": result[category],
                                   "raw": [raw for raw, _score in trials],
                                   "confidence": min((float(score) for _raw, score in trials), default=0.),
                                   "bounds": original, "skull_confidence": skulls[index]["score"]})
    result["complete"] = all(result[key] is not None for key in CATEGORIES)
    if result["complete"]:
        result["status"] = "Normal, Magic, Rare and Unique kill counts read from the screenshot."
    return result
