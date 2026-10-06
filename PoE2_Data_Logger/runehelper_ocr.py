"""Offline adapter for RuneHelper's trained Runeshape loot OCR model.

The model, preprocessing constants, row filtering, and CTC decode follow the
MIT-licensed RuneHelper LineReader, PanelPreparation, RowFinder and TextStart.
The thin adapter accepts the logger's PIL screen capture and returns row boxes.
See third_party/runehelper/README.txt for upstream revision and provenance.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent / "third_party" / "runehelper"


def _find_panel(gray: np.ndarray):
    h, w = gray.shape
    whole = (0, 0, w, h)
    if min(h, w) < 64:
        return whole
    low, high = np.percentile(gray, [5, 95])
    threshold = max(12, int(high - low) // 5)
    smooth = cv2.blur(gray, (7, 1))
    change = smooth[:, :-6].astype(np.int16) - smooth[:, 6:].astype(np.int16)
    darkening = change > threshold
    dark = darkening.sum(axis=0)
    bright = (change < -threshold).sum(axis=0)
    width = len(dark)
    right = width * 55 // 100 + int(np.argmax(dark[width * 55 // 100:]))
    if dark[right] < .30 * h:
        return whole
    edge_rows = darkening[:, max(0, right - 1):min(width, right + 2)].any(axis=1)
    density = cv2.blur(edge_rows.astype(np.float32)[:, None], (1, max(8, h // 16)),
                       borderType=cv2.BORDER_REPLICATE)[:, 0]
    rows = np.flatnonzero(density >= .40)
    if not rows.size:
        return whole
    left = int(np.argmax(bright[:width * 25 // 100]))
    panel_left = left + 3 if bright[left] >= .50 * dark[right] else 0
    panel_right = right + 3
    side_margin, end_margin = int(w * .05), int(h * .05)
    return (panel_left if panel_left >= side_margin else 0,
            int(rows[0]) if rows[0] >= end_margin else 0,
            panel_right if w - panel_right >= side_margin else w,
            int(rows[-1]) + 1 if h - 1 - rows[-1] >= end_margin else h)


def default_frame(image: Image.Image):
    if image.width < 900 or image.height < 600 or image.width < image.height * 1.25:
        return image
    window = image.crop((0, 0, min(image.width, round(image.height * .70)), image.height))
    gray = cv2.cvtColor(np.asarray(window.convert("RGB")), cv2.COLOR_RGB2GRAY)
    _, top, right, bottom = _find_panel(gray)
    return window.crop((0, top, right, bottom))


@lru_cache(maxsize=1)
def _model():
    info = json.loads((ROOT / "english.json").read_text(encoding="utf-8"))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(ROOT / "english.onnx"), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    return info, session


def _prepare_row(gray: np.ndarray, height: int, stride: int):
    scale = height / gray.shape[0]
    width = max(8, round(gray.shape[1] * scale))
    method = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(gray, (width, height), interpolation=method).astype(np.float32)
    low, high = np.percentile(resized, [2, 98])
    prepared = np.clip((resized - low) / max(8.0, high - low), 0, 1)
    padded_width = (width + stride - 1) // stride * stride
    padded = np.ones((1, 1, height, padded_width), dtype=np.float32)
    padded[0, 0, :, :width] = prepared
    return padded, width // stride


def _read_row(gray: np.ndarray):
    info, session = _model()
    tensor, steps = _prepare_row(gray, info["height"], info["stride"])
    logits = session.run(None, {"input": tensor})[0][0, :, 0, :steps]
    winners = np.argmax(logits, axis=0)
    text, confidence, previous = [], [], 0
    for i, index in enumerate(winners):
        index = int(index)
        if index and index != previous:
            text.append(info["charset"][index - 1])
            column = logits[:, i] - logits[index, i]
            confidence.append(1.0 / float(np.exp(column.astype(np.float64)).sum()))
        previous = index
    return "".join(text).strip(), 100 * sum(confidence) / len(confidence) if confidence else 0


def _gray_panel(image: Image.Image):
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    scale = min(1.0, 680 / gray.shape[1]) if gray.shape[1] > 750 else 1.0
    if scale < 1:
        gray = cv2.resize(gray, (round(gray.shape[1] * scale), round(gray.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
    h, w = gray.shape
    sample = gray[h // 10:9 * h // 10, w // 2:85 * w // 100]
    if sample.size:
        p25, p50, p95 = np.percentile(sample, [25, 50, 95])
        if abs(p50 - 165) > 12 or abs(p95 - 190) > 12:
            lo, hi = np.percentile(sample, [5, 95])
            if hi > lo + 8:
                gray = np.clip((gray.astype(np.float32) - lo) * (165 / max(hi - lo, 1))
                               + 115, 0, 255).astype(np.uint8)
    return gray, scale


def _rows(gray: np.ndarray):
    h, w = gray.shape
    x0 = w // 2
    right = gray[:, x0:]
    dark = cv2.threshold(right, 115, 255, cv2.THRESH_BINARY_INV)[1]
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((2, 3), np.uint8))
    sums = (dark > 0).sum(axis=0)
    dark[:, sums >= h * .95] = 0
    sums = (dark > 0).sum(axis=0)
    for x in range(dark.shape[1] - dark.shape[1] * 15 // 100, dark.shape[1]):
        if sums[x] >= h * .5:
            dark[:, max(0, x - 1):] = 0
            break
    row_ink = (dark > 0).sum(axis=1)
    bands = []
    start = end = -1
    blank = 0
    for y, ink in enumerate(row_ink):
        if ink >= 12:
            if start < 0:
                start = y
            end, blank = y, 0
        elif start >= 0:
            blank += 1
            if blank > 4:
                bands.append((start, end))
                start = end = -1
                blank = 0
    if start >= 0:
        bands.append((start, end))
    if not bands:
        return []
    edges = []
    for start, end in bands:
        minimum = max(1, math.ceil((end - start + 1) * .2))
        cols = (dark[start:end + 1] > 0).sum(axis=0)
        found = np.flatnonzero(cols >= minimum)
        edges.append(int(found[-1]) if found.size else -1)
    aligned = max(edges) - dark.shape[1] * .08
    heights = sorted(end - start + 1 for (start, end), edge in zip(bands, edges)
                     if end - start + 1 >= 6 and edge >= aligned)
    median = heights[len(heights) // 2] if heights else 0
    rows = []
    for (start, end), edge in zip(bands, edges):
        band_h = end - start + 1
        if band_h < 6 or (median and band_h > median * 3) or edge < aligned:
            continue
        if median and band_h > median * 2:
            end = start + median - 1
        reference = median or band_h
        y1 = max(0, start - max(2, reference * 66 // 100))
        y2 = min(h, end + max(2, reference * 33 // 100) + 1)
        if np.count_nonzero(dark[y1:y2]) / max((y2 - y1) * dark.shape[1], 1) <= .25:
            rows.append((y1, y2))
    return rows


def _text_start(row: np.ndarray):
    dark = cv2.threshold(row, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    ink = (dark > 0).sum(axis=0)
    nonempty = np.flatnonzero(ink)
    if not nonempty.size:
        return -1
    last = int(nonempty[-1])
    width, height = row.shape[1], row.shape[0]
    gap_start, gap_width, run_start = -1, 0, -1
    for x in range(last + 1):
        if ink[x] == 0:
            if run_start < 0:
                run_start = x
        else:
            if run_start >= 0 and x - run_start > gap_width:
                gap_start, gap_width = run_start, x - run_start
            run_start = -1
    if gap_start < 0:
        return -1
    in_gap = gap_start + int(gap_width * .5)
    tall = math.ceil(height * .8)

    def first_tall(start, end):
        for x in range(max(0, start), min(width - 1, end) + 1):
            if ink[x] >= tall:
                return x
        return -1

    gaps = []
    x = 0
    while x <= last:
        if ink[x]:
            x += 1
            continue
        begin = x
        while x <= last and not ink[x]:
            x += 1
        end = x - 1
        if (end - begin + 1) * 100 > width * 3:
            continue
        frame = first_tall(end + 1, end + 3)
        if frame < 0:
            continue
        closed = first_tall(begin - 3, begin - 1) >= 0
        gaps.append((begin, end, frame, closed))

    tile_right, tile_blank, best_count = -1, 0, 0
    for i, first in enumerate(gaps):
        if not first[3]:
            continue
        if first[0] * 100 > width * 25:
            break
        for second in gaps[i + 1:]:
            if not second[3]:
                continue
            pitch = second[2] - first[2]
            if pitch * 100 < width * 4:
                continue
            if pitch * 100 > width * 14:
                break
            if abs((second[1] - second[0]) - (first[1] - first[0])) > 1 or first[0] < pitch - (first[1] - first[0] + 1):
                continue
            right = second[0] - 1
            while right > first[2] and ink[right] < tall:
                right -= 1
            if right <= first[2]:
                continue
            chain_end, count = second, 2
            while True:
                next_frame = chain_end[2] + pitch
                follows = [g for g in gaps if g[0] > chain_end[2]
                           and g[1] - g[0] + 1 <= first[1] - first[0] + 2
                           and abs(g[2] - next_frame) <= 3]
                if not follows:
                    break
                chain_end = min(follows, key=lambda g: abs(g[2] - next_frame))
                count += 1
            if count > best_count:
                best_count = count
                tile_right = chain_end[2] + right - first[2]
                tile_blank = first[1] - first[0] + 1

    if tile_right < 0:
        return in_gap
    expected = tile_right
    tile_right = -1
    for x in range(expected - 2, expected + 3):
        if 0 <= x <= last and ink[x] >= tall:
            tile_right = x
    if tile_right < 0:
        tile_right = min(expected, last)
        while tile_right > expected - 2 and not ink[tile_right]:
            tile_right -= 1
        if not ink[tile_right]:
            return in_gap
    if tile_right >= last:
        return in_gap
    solid = max(2, math.ceil(height * .2))
    text_from = tile_right + 3
    while text_from <= last and ink[text_from] < solid:
        text_from += 1
    after_tile = tile_right + 1 + int((text_from - tile_right - 3) * .5)
    if gap_start <= tile_right:
        return after_tile
    if gap_start < text_from:
        return in_gap
    next_frame = min(last, tile_right + tile_blank + 3)
    if first_tall(tile_right + 2, next_frame) >= 0 or gap_start - text_from < tile_blank:
        return in_gap
    covered = ink[text_from:gap_start].sum()
    coverage = covered / max(1, height * (gap_start - text_from))
    return in_gap if coverage < .08 else after_tile


def _recognize_rows(image: Image.Image):
    gray, scale = _gray_panel(image)
    results = []
    for y1, y2 in _rows(gray):
        row = gray[y1:y2]
        start = _text_start(row)
        if start >= row.shape[1] - 8:
            continue
        text, confidence = _read_row(row[:, start:])
        prefix = r"^(?:(?:\d{1,3}|[Il])\s*[xX×]\s|Skill Level\s*\d{1,2}\s*:)"
        if not re.match(prefix, text, re.I):
            full_text, full_confidence = _read_row(row)
            if full_confidence >= 80 and re.match(prefix, full_text, re.I):
                text, confidence, start = full_text, full_confidence, 0
        if confidence >= 80 and text:
            results.append({"text": text, "score": confidence / 100,
                            "x1": round(start / scale), "y1": round(y1 / scale),
                            "x2": image.width, "y2": round(y2 / scale)})
    return results


def recognize(image: Image.Image):
    rows = _recognize_rows(image)
    prefix = re.compile(r"^(?:(?:\d{1,3}|[Il])\s*[xX×]\s|Skill Level\s*\d{1,2}\s*:)", re.I)
    if any(prefix.match(row["text"]) for row in rows):
        return rows
    width = image.width
    if image.width >= 900 and image.height >= 600 and image.width >= image.height * 1.25:
        width = min(width, round(image.height * .70))
    window = image.crop((0, 0, width, image.height))
    gray = cv2.cvtColor(np.asarray(window.convert("RGB")), cv2.COLOR_RGB2GRAY)
    left, top, right, bottom = _find_panel(gray)
    if (left, top, right, bottom) == (0, 0, image.width, image.height):
        return rows
    prepared = _recognize_rows(window.crop((left, top, right, bottom)))
    if not any(prefix.match(row["text"]) for row in prepared):
        return rows
    for row in prepared:
        row["x1"] += left
        row["x2"] += left
        row["y1"] += top
        row["y2"] += top
    return prepared
