from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.ocr import opened_scan, runehelper_ocr


def _result(status, **values):
    return {"mode": "propagation", "selected_recipe": None, "runes": [],
            "positions": [], "can_use": False, "status": status, "family": None,
            "candidates": [], "choices": [], **values}


def _panel(image):
    if image.width < 160 or image.height < 120:
        return None
    candidate = image.resize((575, max(1, round(image.height * 575 / image.width))),
                             Image.Resampling.LANCZOS)
    marked, _ = _marked_boxes(_gold_mask(candidate))
    if marked:
        # Already-cropped panels can omit the arrow margin. The generic frame
        # finder may otherwise mistake a recipe border for the panel header.
        return candidate
    width = image.width
    if image.width > image.height * 1.3:
        width = min(width, round(image.height * .85))
    window = image.crop((0, 0, width, image.height))
    gray = cv2.cvtColor(np.asarray(window), cv2.COLOR_RGB2GRAY)
    _, top, right, bottom = runehelper_ocr._find_panel(gray)
    frame = window.crop((0, top, right, bottom))
    if frame.width < 160 or frame.height < 120:
        return None
    scale = 575 / frame.width
    return frame.resize((575, max(1, round(frame.height * scale))), Image.Resampling.LANCZOS)


def _rapid_rows(image):
    with opened_scan.OCR_LOCK:
        found = opened_scan._engine()(image)
    if found.boxes is None:
        return []
    rows = []
    for box, text, score in zip(found.boxes, found.txts, found.scores):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        rows.append({"text": text.strip(), "score": float(score), "x1": min(xs),
                     "x2": max(xs), "y1": min(ys), "y2": max(ys)})
    return rows


def _read_panel_rows(image):
    rows = _rapid_rows(image)
    title = next((row for row in rows
                  if opened_scan._key(row["text"]) == "runeshapecombinations"
                  and row["score"] >= .8), None)
    if title is None:
        return [], None
    return [row for row in rows if row["y1"] > title["y2"] + 12], title


def _gold_mask(image):
    rgb = np.asarray(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    return ((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
            & (hsv[:, :, 1] >= 55) & (hsv[:, :, 2] >= 155)
            & (rgb[:, :, 0].astype(np.int16) - rgb[:, :, 2] >= 55)).astype(np.uint8)


def _cursors(mask):
    strip = cv2.morphologyEx(mask[:, :46], cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    _, labels, components, centres = cv2.connectedComponentsWithStats(strip)
    found = []
    for index, ((x, y, width, height, area), (cx, cy)) in enumerate(zip(components[1:], centres[1:]), 1):
        if (18 <= width <= 45 and 9 <= height <= 32 and 1.15 <= width / height <= 3.8
                and area >= width * height * .18 and x + width <= 44):
            component = labels[y:y + height, x:x + width] == index
            spans = []
            for row in component:
                points = np.flatnonzero(row)
                spans.append(points[-1] - points[0] + 1 if points.size else 0)
            edge = max(1, round(height * .2))
            middle = spans[round(height * .35):max(round(height * .65), round(height * .35) + 1)]
            if (np.mean(spans[:edge]) <= width * .55 and np.mean(spans[-edge:]) <= width * .55
                    and np.mean(middle) >= width * .6):
                found.append({"x": float(cx), "y": float(cy)})
    return found


def _marked_boxes(mask):
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))
    _, labels, components, _ = cv2.connectedComponentsWithStats(joined)
    found, uncertain = [], []
    for index, (x, y, width, height, _) in enumerate(components[1:], 1):
        if not (28 <= width <= 50 and 32 <= height <= 55 and .65 <= width / height <= 1.25):
            continue
        component = (labels[y:y + height, x:x + width] == index).astype(np.uint8)
        edge = max(2, round(width * .09))
        if (component[:, :edge].any(axis=1).mean() < .65
                or component[:, -edge:].any(axis=1).mean() < .65):
            continue
        horizontal = np.flatnonzero(component[:max(5, height // 3)].sum(axis=1) >= width * .55)
        if not horizontal.size:
            continue
        border_y = int(horizontal[0])
        band = mask[y + max(0, border_y - 7):y + border_y, x:x + width]
        peaks = []
        for centre in (.24, .49, .74):
            start, end = max(0, round(width * (centre - .07))), min(width, round(width * (centre + .07)))
            peaks.append(int(band[:, start:end].sum()) if band.size else 0)
        box = (float(x + width / 2), int(y + border_y), int(width), int(height - border_y))
        if border_y < 2 or min(peaks) < 2:
            uncertain.append(box)
        else:
            found.append(box)
    return found, uncertain


def _marked_tiles(mask, cursor_y, first_x=71, pitch=41):
    marked, incomplete = _marked_boxes(mask)
    found, uncertain = [], False
    for centre_x, tile_top, width, height in marked:
        if not cursor_y - 31 <= tile_top <= cursor_y + 10:
            continue
        position = round((centre_x - first_x) / pitch) + 1
        if not 1 <= position <= 10 or abs(centre_x - first_x - (position - 1) * pitch) > 6:
            uncertain = True
            continue
        found.append((position, tile_top, width, height))
    uncertain |= any(cursor_y - 31 <= box[1] <= cursor_y + 10 for box in incomplete)
    return sorted(found), uncertain


def _tile_layout(image, tile_top, tile_height, marked, min_first_x=15):
    """Recover the first tile and spacing when a crop excludes the arrow margin."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    top, bottom = max(0, round(tile_top)), min(image.height, round(tile_top + tile_height))
    if bottom <= top:
        return None
    edges = cv2.Canny(gray[top:bottom], 50, 100)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    width = float(np.median([box[2] for box in marked]))
    centres = [box[0] for box in marked]
    for contour in contours:
        x, _, w, h = cv2.boundingRect(contour)
        if not (.72 * width <= w <= 1.2 * width and .72 * tile_height <= h <= 1.2 * tile_height):
            continue
        if cv2.contourArea(contour) < w * h * .65:
            continue
        polygon = cv2.approxPolyDP(contour, cv2.arcLength(contour, True) * .025, True)
        if (len(polygon) == 4 or cv2.contourArea(contour) >= w * h * .85) and x < 470:
            centres.append(x + w / 2)
    grouped = []
    for centre in sorted(centres):
        if grouped and abs(centre - grouped[-1]) < 6:
            grouped[-1] = (grouped[-1] + centre) / 2
        else:
            grouped.append(centre)
    if not grouped:
        return None
    gaps = [b - a for a, b in zip(grouped, grouped[1:]) if .96 * width <= b - a <= 1.22 * width]
    pitch = float(np.median(gaps)) if gaps else width * 41 / 38
    if any(abs((centre - grouped[0]) / pitch - round((centre - grouped[0]) / pitch)) > .16
           for centre in grouped):
        return None
    first_x = grouped[0]
    # A glyph can interrupt its frame contour. Include preceding occupied
    # tiles on the same verified grid rather than mislabelling tile two as one.
    while first_x - pitch >= min_first_x:
        x = round(first_x - pitch)
        half = round(pitch * 12 / 41)
        sample = gray[round(tile_top + tile_height * .18):round(tile_top + tile_height * .83),
                      max(0, x - half):x + half]
        if not sample.size or sample.std() < 28:
            break
        first_x -= pitch
    return (first_x, pitch) if min_first_x <= first_x <= 85 else None


def _reward_rows(db, detections):
    names = [row[0] for row in db.execute("SELECT name FROM recipes")]
    quantity = re.compile(r"^\s*(\d{1,3}|[Il])\s*[xX×]\s+(.+?)\s*$")
    skill = re.compile(r"^Skill Level\s*(\d{1,2})\s*:\s*(.+?)\s*$", re.I)
    rows = []
    for detection in sorted(detections, key=lambda row: row["y1"]):
        text, count = detection["text"].strip(), 1
        matched, level = quantity.match(text), skill.match(text)
        if matched:
            count = 1 if matched.group(1) in ("I", "l") else int(matched.group(1))
            text = matched.group(2).strip()
        elif level:
            text = f"{level.group(2).strip()} (Level {level.group(1)})"
        elif detection["x1"] < 120 or len(text) < 8:
            continue
        levels = re.findall(r"\blevel\s*(\d+)\b", text, re.I)
        choices = [name for name in names if re.findall(r"\blevel\s*(\d+)\b", name, re.I) == levels]
        recipe, confidence = opened_scan._match(db, text, count, choices)
        if recipe and re.findall(r"\blevel\s*(\d+)\b", recipe, re.I) != levels:
            recipe = None
        if not matched and not level and (not recipe or confidence < .92):
            continue
        rows.append({**detection, "recipe": recipe, "match_score": confidence})
    return rows


def _family_candidates(db, rows):
    sequence = [row["recipe"] for row in rows]
    if not sequence or any(name is None for name in sequence):
        return []
    matches = set()
    for family in db.execute("SELECT id,recipes_json FROM families WHERE valid=1"):
        recipes = logger._load(family["recipes_json"])
        for start in range(len(recipes) - len(sequence) + 1):
            if recipes[start:start + len(sequence)] == sequence:
                matches.add(family["id"])
    return sorted(matches)


def _visible_tile_count(image, tile_top, tile_height, first_x=71, pitch=41):
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    values = []
    for position in range(10):
        x = round(first_x + pitch * position)
        top, bottom = max(0, round(tile_top + tile_height * .18)), min(image.height, round(tile_top + tile_height * .83))
        half = round(pitch * 12 / 41)
        sample = gray[top:bottom, max(0, x - half):x + half]
        values.append(float(sample.std()) if sample.size else 0)
    count = 0
    for value in values:
        if value < 28:
            break
        count += 1
    if any(value >= 35 for value in values[count + 1:]):
        return None
    return count


def _row_reading(db, panel, marked, incomplete, row, all_rows, candidates, shared_layout, min_first_x):
    recipe = row["recipe"]
    info = {"selected_recipe": recipe, "reward_text": row["text"], "candidates": candidates,
            "family": f"Family {candidates[0]}" if len(candidates) == 1 else None,
            "visible_recipes": [item["recipe"] for item in all_rows]}
    if not recipe or row["score"] < .85 or row["match_score"] < .9:
        return _result("Reward was unclear — enter its propagated runes manually.", **info)
    if not candidates:
        return _result("Visible rewards do not match the family database — enter the propagated runes manually.",
                       **info)
    entry = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (recipe,)).fetchone()
    runes = [rune.strip() for rune in entry["combo"].split("+")] if entry else []
    if not entry or len(runes) != entry["sockets"] or any(not rune or rune.lower() == "unresolved" for rune in runes):
        return _result("The recipe's rune order is unresolved — enter the propagated runes manually.", **info)
    selected = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
    uncertain = [box for box in incomplete if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
    if uncertain or not 1 <= len(selected) <= 2:
        return _result("One or two crown-marked rune positions were not clear — enter them manually.", **info)
    tile_top = float(np.median([tile[1] for tile in selected]))
    tile_height = float(np.median([tile[3] for tile in selected]))
    layout = _tile_layout(panel, tile_top, tile_height, selected, min_first_x) or shared_layout or (71, 41)
    first_x, pitch = layout
    positions = sorted(round((box[0] - first_x) / pitch) + 1 for box in selected)
    if (len(set(positions)) != len(positions) or any(position < 1 or position > len(runes) for position in positions)
            or any(abs(box[0] - first_x - round((box[0] - first_x) / pitch) * pitch) > 6 for box in selected)):
        return _result("Marked rune positions conflict with the recipe's rune order — enter them manually.", **info)
    if _visible_tile_count(panel, tile_top, tile_height, first_x, pitch) != len(runes):
        return _result("Visible rune count conflicts with the recipe — enter the propagated runes manually.", **info)
    return _result("Propagation runes detected — approve this recipe to add them to the chain.",
                   **info, positions=positions, runes=[runes[position - 1] for position in positions], can_use=True)


def scan_propagation(image: Image.Image | Path):
    if isinstance(image, Image.Image):
        source = image.convert("RGB")
    else:
        with Image.open(image) as opened:
            source = opened.convert("RGB")
    panel = _panel(source)
    if panel is None:
        return _result("Runeshape panel is too small — capture the full recipe list and cursor.")
    mask = _gold_mask(panel)
    cursors = _cursors(mask)
    detections, title = _read_panel_rows(panel)
    if title is None:
        return _result("Runeshape Combinations panel was not found — review the capture.")
    with logger._connect() as db:
        rows = _reward_rows(db, detections)
        candidates = _family_candidates(db, rows)
        marked, incomplete = _marked_boxes(mask)
        min_first_x = 46 if cursors else 15
        layouts = []
        for row in rows:
            boxes = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
            if boxes:
                layout = _tile_layout(panel, float(np.median([box[1] for box in boxes])),
                                      float(np.median([box[3] for box in boxes])), boxes, min_first_x)
                if layout:
                    layouts.append(layout)
        shared = tuple(float(np.median([layout[index] for layout in layouts])) for index in range(2)) if layouts else None
        choices = [_row_reading(db, panel, marked, incomplete, row, rows, candidates, shared, min_first_x)
                   for row in rows]
    if len(cursors) == 1:
        selected = [index for index, row in enumerate(rows) if 3 <= row["y1"] - cursors[0]["y"] <= 62]
        if len(selected) == 1:
            return {**choices[selected[0]], "choices": choices}
    return _result("Choose the propagated recipe below and approve its runes, or enter the runes manually.",
                   choices=choices, candidates=candidates,
                   visible_recipes=[row["recipe"] for row in rows])
