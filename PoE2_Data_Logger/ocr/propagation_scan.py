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
            "candidates": [], **values}


def _panel(image):
    if image.width < 160 or image.height < 120:
        return None
    width = image.width
    if image.width > image.height * 1.3:
        # A cropped recipe list can be wider than it is tall. Preserve it
        # when its actual cursor and marked borders already have the expected
        # scale, rather than trimming away the right-hand reward text.
        candidate = image.resize((575, max(1, round(image.height * 575 / image.width))),
                                 Image.Resampling.LANCZOS)
        mask = _gold_mask(candidate)
        cursors = _cursors(mask)
        if len(cursors) == 1:
            tiles, uncertain = _marked_tiles(mask, cursors[0]["y"])
            if not uncertain and 1 <= len(tiles) <= 2:
                return candidate
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
    _, _, components, centres = cv2.connectedComponentsWithStats(strip)
    found = []
    for (x, y, width, height, area), (cx, cy) in zip(components[1:], centres[1:]):
        if (18 <= width <= 45 and 9 <= height <= 32 and 1.15 <= width / height <= 3.8
                and area >= width * height * .18 and x + width <= 44):
            found.append({"x": float(cx), "y": float(cy)})
    return found


def _marked_tiles(mask, cursor_y):
    top, bottom = max(0, round(cursor_y - 31)), min(mask.shape[0], round(cursor_y + 29))
    found, uncertain = [], False
    for position in range(1, 11):
        centre_x = 71 + 41 * (position - 1)
        left = centre_x - 21
        strip = mask[top:bottom, left:centre_x + 21]
        joined = cv2.morphologyEx(strip, cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))
        _, labels, components, _ = cv2.connectedComponentsWithStats(joined)
        for index, (x, y, width, height, area) in enumerate(components[1:], 1):
            if not (28 <= width <= 44 and 32 <= height <= 49 and .65 <= width / height <= 1.25):
                continue
            component = (labels[y:y + height, x:x + width] == index).astype(np.uint8)
            edge = max(2, round(width * .09))
            if (component[:, :edge].any(axis=1).mean() < .65
                    or component[:, -edge:].any(axis=1).mean() < .65):
                continue
            horizontal = np.flatnonzero(component[:max(5, height // 3)].sum(axis=1) >= width * .65)
            if not horizontal.size:
                continue
            border_y = int(horizontal[0])
            band = mask[top + y + max(0, border_y - 7):top + y + border_y, left + x:left + x + width]
            peaks = []
            for centre in (.24, .49, .74):
                start, end = max(0, round(width * (centre - .07))), min(width, round(width * (centre + .07)))
                peaks.append(int(band[:, start:end].sum()) if band.size else 0)
            if border_y < 2 or min(peaks) < 2:
                uncertain = True
                continue
            if abs(left + x + width / 2 - centre_x) > 8:
                uncertain = True
                continue
            found.append((position, top + y + border_y, width, height - border_y))
    found.sort()
    return found, uncertain


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


def _visible_tile_count(image, tile_top, tile_height):
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    values = []
    for position in range(10):
        x = 71 + 41 * position
        top, bottom = max(0, round(tile_top + tile_height * .18)), min(image.height, round(tile_top + tile_height * .83))
        sample = gray[top:bottom, x - 12:x + 12]
        values.append(float(sample.std()) if sample.size else 0)
    count = 0
    for value in values:
        if value < 28:
            break
        count += 1
    if any(value >= 35 for value in values[count + 1:]):
        return None
    return count


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
    if len(cursors) != 1:
        return _result("Selected recipe cursor was not clear — widen the Propagation scan region "
                       "to include the gold arrow beside the recipe row.")
    cursor_y = cursors[0]["y"]
    detections, title = _read_panel_rows(panel)
    if title is None:
        return _result("Runeshape Combinations panel was not found — review the capture.")
    with logger._connect() as db:
        rows = _reward_rows(db, detections)
        selected = [row for row in rows if 3 <= row["y1"] - cursor_y <= 62]
        if len(selected) != 1:
            return _result("The cursor-selected reward row was not clear — review the capture.")
        selected = selected[0]
        recipe = selected["recipe"]
        if not recipe or selected["score"] < .85 or selected["match_score"] < .9:
            return _result("Selected reward was unclear — review its name, quantity and level.")
        candidates = _family_candidates(db, rows)
        if not candidates:
            return _result("Visible rewards do not match the family database — review the recipe list.",
                           selected_recipe=recipe)
        entry = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (recipe,)).fetchone()
    info = {"selected_recipe": recipe, "candidates": candidates,
            "family": f"Family {candidates[0]}" if len(candidates) == 1 else None,
            "visible_recipes": [row["recipe"] for row in rows]}
    runes = [rune.strip() for rune in entry["combo"].split("+")] if entry else []
    if not entry or len(runes) != entry["sockets"] or any(not rune or rune.lower() == "unresolved" for rune in runes):
        return _result("The selected recipe's rune order is unresolved in the database.", **info)
    tiles, uncertain = _marked_tiles(mask, cursor_y)
    if uncertain or not 1 <= len(tiles) <= 2 or len({tile[0] for tile in tiles}) != len(tiles):
        return _result("One or two marked rune positions were not clear — review the capture.", **info)
    if any(tile[0] > len(runes) for tile in tiles):
        return _result("Marked rune positions conflict with the selected recipe's rune order.", **info)
    tile_top = float(np.median([tile[1] for tile in tiles]))
    tile_height = float(np.median([tile[3] for tile in tiles]))
    if _visible_tile_count(panel, tile_top, tile_height) != len(runes):
        return _result("Visible rune count conflicts with the selected recipe — review the capture.", **info)
    positions = [tile[0] for tile in tiles]
    detected = [runes[position - 1] for position in positions]
    return _result("Propagation runes detected — review, then add them to the chain.",
                   **info, runes=detected, positions=positions, can_use=True)
