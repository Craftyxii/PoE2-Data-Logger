"""The currency-overlay digit reader (third_party/currency_overlay/digit-reader.js) in Python.

readCellAdaptive with the same arithmetic in the same order: integer IoU matching (the
exact digit_kernels used under the JavaScript), JavaScript rounding (halves up) and the
same stable sorts, so every count and confidence is the same number.
tests/test_digit_reader_port.py compares both on real captures.
"""

import json
import math
import re

import cv2
import numpy as np

from numpy.lib.stride_tricks import sliding_window_view

DEFAULTS = {"floor": 122, "up": 12, "dn": 12, "stripWidth": 17,
            "iouThresh": 0.76, "dyLo": -2, "dyHi": 3, "minInkFrac": 0.45}
ADAPTIVE_FLOORS = (80, 95, 110, 122, 135, 150, 165, 180)
DESAT_SAT = 40
OVERLAP = 0.20


def _round(value):
    """JavaScript Math.round: halves go up."""
    return math.floor(value + .5)


def load_templates(path):
    """templatesFromJSON: each glyph as a 0/1 uint8 plane, in the file's digit order."""
    source = json.loads(path.read_text(encoding="utf-8"))
    source = source.get("templates", source)
    return [(ch, np.asarray(glyph["data"], dtype=np.uint8).reshape(glyph["h"], glyph["w"]))
            for ch, glyph in source.items()]


def value_channel(rgba):
    """valueChannelDesatMax: max(R,G,B) of near-white pixels, 0 elsewhere."""
    rgb = rgba[:, :, :3].astype(np.int16)
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    return np.where(high - low <= DESAT_SAT, high, 0).astype(np.uint8)


def _crop(value, x0, y0, x1, y1):
    height, width = value.shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    return value[y0:max(y0, y1), x0:max(x0, x1)]


def _resample(sub, width, height):
    """Bilinear resample with JavaScript's per-pixel arithmetic and rounding."""
    h, w = sub.shape
    if width == w and height == h:
        return sub
    sx, sy = w / width, h / height
    fy = (np.arange(height) + 0.5) * sy - 0.5
    fx = (np.arange(width) + 0.5) * sx - 0.5
    y0, x0 = np.floor(fy), np.floor(fx)
    wy, wx = fy - y0, fx - x0
    y0 = y0.astype(int)
    x0 = x0.astype(int)
    y1, x1 = np.clip(y0 + 1, 0, h - 1), np.clip(x0 + 1, 0, w - 1)
    y0, x0 = np.clip(y0, 0, h - 1), np.clip(x0, 0, w - 1)
    data = sub.astype(np.float64)
    a, b = data[y0][:, x0], data[y0][:, x1]
    c, d = data[y1][:, x0], data[y1][:, x1]
    top = a + (b - a) * wx[None, :]
    bottom = c + (d - c) * wx[None, :]
    return np.floor(top + (bottom - top) * wy[:, None] + .5).astype(np.uint8)


def _otsu(data):
    histogram = np.bincount(data.reshape(-1), minlength=256).astype(np.float64).tolist()
    total = data.size
    every = 0.0
    for t in range(256):
        every += t * histogram[t]
    weight = back = 0.0
    best, threshold = -1, 150
    for t in range(256):
        weight += histogram[t]
        if weight == 0:
            continue
        front = total - weight
        if front <= 0:
            break
        back += t * histogram[t]
        mean_back, mean_front = back / weight, (every - back) / front
        value = weight * front * (mean_back - mean_front) * (mean_back - mean_front)
        if value > best:
            best, threshold = value, t
    return threshold


def _slide(strip, glyph, dy_low, dy_high, minimum_ink):
    """slideMatch: best vertical offset per x by exact integer IoU (first offset wins ties).

    The same integers and the same division as digit_kernels._slide_arrays.
    """
    height, width = strip.shape
    glyph_height, glyph_width = glyph.shape
    if glyph_width > width or glyph_height > height:
        return []
    offsets = np.arange(dy_low, dy_high + 1)
    starts = height // 2 + offsets - glyph_height // 2
    valid = (starts >= 0) & (starts + glyph_height <= height)
    offsets, starts = offsets[valid], starts[valid]
    if not len(offsets):
        return []
    windows = sliding_window_view(strip, (glyph_height, glyph_width))[starts].astype(np.int32)
    intersection = np.einsum("yxij,ij->yx", windows, glyph.astype(np.int32))
    ink = windows.sum(axis=(2, 3))
    glyph_ink = int(glyph.sum())
    union = ink + glyph_ink - intersection
    scores = np.divide(intersection, union, out=np.zeros(intersection.shape, dtype=np.float64), where=union > 0)
    scores[ink < minimum_ink * glyph_ink] = 0
    best = scores.argmax(axis=0)
    picked = scores[best, np.arange(scores.shape[1])]
    return [{"x": x, "dy": int(offsets[best[x]]), "score": float(picked[x])}
            for x in np.nonzero(picked > 0)[0].tolist()]


def _collect(strip, templates, threshold, settings, memo=None):
    """Every template match at or above the threshold, in template then x order.

    The same 0/1 strip often comes back from neighbouring floors; ``memo`` reuses its
    matches within one cell.
    """
    key = (strip.shape, strip.tobytes(), threshold)
    if memo is not None and key in memo:
        return [dict(match) for match in memo[key]]
    out = []
    for ch, glyph in templates:
        for match in _slide(strip, glyph, settings["dyLo"], settings["dyHi"], settings["minInkFrac"]):
            if match["score"] >= threshold:
                out.append({"x": match["x"], "ch": ch, "score": match["score"], "tw": glyph.shape[1]})
    if memo is not None:
        memo[key] = [dict(match) for match in out]
    return out


def _overlaps(x, width, accepted):
    for other in accepted:
        shared = max(0, min(x + width, other["x"] + other["tw"]) - max(x, other["x"]))
        if shared > OVERLAP * min(width, other["tw"]) and shared > 1:
            return True
    return False


def _gap_fill(binary, templates, accepted, settings, memo):
    ordered = sorted(accepted, key=lambda candidate: candidate["x"])
    width = binary.shape[1]
    threshold = max(0.70, settings["iouThresh"] - 0.06)

    def fill(start, end):
        found = [{**candidate, "x": start + candidate["x"]}
                 for candidate in _collect(binary[:, start:end], templates, threshold, settings, memo)]
        for candidate in sorted(found, key=lambda candidate: -candidate["score"]):
            if not _overlaps(candidate["x"], candidate["tw"], accepted):
                accepted.append(candidate)
                break

    for first, second in zip(ordered, ordered[1:]):
        start, end = first["x"] + first["tw"], second["x"]
        if 4 <= end - start <= 15 and int(binary[:, start:end].sum()) > 20:
            fill(start, end)
    if len(ordered) >= 2:
        last = ordered[-1]["x"] + ordered[-1]["tw"]
        gap = width - last
        if 4 <= gap <= 15:
            ink = int(binary[:, last:width].sum())
            if ink > 120 and ink / gap > 12:
                fill(last, width)


def _read(value, cx, cy, templates, settings, scale, memo):
    """readCellEx: one threshold floor -> {"text", "conf"}."""
    scale = scale if scale and scale > 0 else 1
    if scale != 1:
        half, up, down = (_round(settings[key] * scale) for key in ("stripWidth", "up", "dn"))
        raw = _crop(value, cx - half, cy - up, cx + half, cy + down)
        if not raw.size:
            return {"text": "?", "conf": 0}
        sub = _resample(raw, max(1, _round(raw.shape[1] / scale)), max(1, _round(raw.shape[0] / scale)))
    else:
        sub = _crop(value, cx - settings["stripWidth"], cy - settings["up"],
                    cx + settings["stripWidth"], cy + settings["dn"])
    if not sub.size:
        return {"text": "?", "conf": 0}
    binary = (sub > max(_otsu(sub), settings["floor"])).astype(np.uint8)
    candidates = _collect(binary, templates, settings["iouThresh"], settings, memo)
    if not candidates:
        size = max(3, min(sub.shape) // 4)
        if size > 1 and size % 2 == 0:
            size += 1
        extent = 2 * (size // 2) + 1
        kernel = np.ones((extent, extent), dtype=np.uint8)
        opening = cv2.dilate(cv2.erode(sub, kernel, borderType=cv2.BORDER_REFLECT),
                             kernel, borderType=cv2.BORDER_REFLECT)
        top = np.clip(sub.astype(np.int16) - opening, 0, 255).astype(np.uint8)
        lifted = (top > max(_otsu(top), settings["floor"] - 20)).astype(np.uint8)
        found = _collect(lifted, templates, settings["iouThresh"] * 0.70, settings, memo)
        if found:
            trial = "".join(candidate["ch"] for candidate in sorted(found, key=lambda candidate: candidate["x"]))
            if not (len(re.findall("1", trial)) >= 2 or trial in ("11", "111")):
                candidates = found
    if not candidates:
        return {"text": "?", "conf": 0}
    accepted = []
    for candidate in sorted(candidates, key=lambda candidate: -candidate["score"]):
        if not _overlaps(candidate["x"], candidate["tw"], accepted):
            accepted.append(candidate)
    _gap_fill(binary, templates, accepted, settings, memo)
    accepted.sort(key=lambda candidate: candidate["x"])
    kept = []
    first = accepted[0]["x"] if accepted else math.inf
    for candidate in accepted:
        if candidate["x"] == first and candidate["ch"] == "1":
            left_ink = candidate["x"] > 0 and bool(binary[:, max(0, candidate["x"] - 2):candidate["x"]].any())
            if not left_ink and candidate["x"] > 2 and any(
                    other["ch"] == "1" and other["x"] != candidate["x"] and abs(other["x"] - candidate["x"]) <= 4
                    for other in accepted):
                continue
        kept.append(candidate)
    if not kept:
        return {"text": "?", "conf": 0}
    total = 0
    for candidate in kept:
        total = total + candidate["score"]
    return {"text": "".join(candidate["ch"] for candidate in kept), "conf": total / len(kept)}


def read_count(rgba, cx, cy, templates, scale):
    """readCellAdaptive over the value channel of an RGBA cell: {"text", "conf", "floor"?}."""
    value = value_channel(rgba)
    best, memo = None, {}
    for floor in ADAPTIVE_FLOORS:
        read = _read(value, cx, cy, templates, {**DEFAULTS, "floor": floor}, scale, memo)
        if not read["text"] or read["text"] == "?":
            continue
        score = read["conf"] * min(len(read["text"]), 6)
        if best is None or score > best["score"]:
            best = {"text": read["text"], "conf": read["conf"], "score": score, "floor": floor}
    return ({"text": best["text"], "conf": best["conf"], "floor": best["floor"]} if best
            else {"text": "?", "conf": 0})
