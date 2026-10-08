from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.ocr import opened_scan, runehelper_ocr
from PoE2_Data_Logger.ocr.propagation_marks import supported_partial_peak_boxes, supported_peak_boxes


def _result(status, **values):
    return {"mode": "propagation", "selected_recipe": None, "runes": [],
            "positions": [], "can_use": False, "status": status, "family": None,
            "candidates": [], "choices": [], **values}


def _panel(image):
    if image.width < 160 or image.height < 120:
        return None
    candidate = image.resize((575, max(1, round(image.height * 575 / image.width))),
                             Image.Resampling.LANCZOS)
    _, marked, _ = _marked_image(candidate)
    if marked:
        # Already-cropped panels can omit the arrow margin. The generic frame
        # finder may otherwise mistake a recipe border for the panel header.
        return candidate
    detections, title = _read_panel_rows(candidate)
    if (title is not None and title["x2"] - title["x1"] >= candidate.width * .28
            and title["y2"] - title["y1"] >= 15):
        # A verified cropped recipe panel remains usable when only the
        # crown peaks are visible. Its marked tiles need no gold frame.
        candidate.info["propagation_rows"] = (detections, title)
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


def _marked_image(image):
    # Exposure changes can fragment the gold frame. Keep the same colour and
    # three-peak geometry checks while looking at a small exposure bracket.
    pixels = np.asarray(image, dtype=np.float32)
    marked, partials, supplementary = [], [], []
    cursor_mask = None
    base_mask = None
    for factor in (1, .9, 1.1, .8, 1.2, 1.35, 1.5, .95, 1.05, 1.25):
        shown = image if factor == 1 else Image.fromarray(np.clip(pixels * factor, 0, 255).astype(np.uint8))
        mask = _gold_mask(shown)
        rgb = np.asarray(shown)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        # Bright captures can make the frame almost white. This additional
        # mask locates the tile only; all three crown peaks still require
        # the original gold mask.
        pale_frame = ((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                      & (hsv[:, :, 1] >= 25) & (hsv[:, :, 2] >= 225)
                      & (rgb[:, :, 0].astype(np.int16) - rgb[:, :, 2] >= 25))
        found, uncertain = _marked_boxes(mask)
        pale_found, _ = _marked_boxes(mask, mask | pale_frame.astype(np.uint8))
        for box in pale_found:
            same = next((entry for entry in supplementary if abs(box[0] - entry[0][0]) < 7
                         and abs(box[1] - entry[0][1]) < 7), None)
            if same is None:
                supplementary.append([box, 1])
            else:
                same[1] += 1
        if base_mask is None:
            base_mask = mask
        if cursor_mask is None and _cursors(mask):
            cursor_mask = mask
        # Different rows can lose their frame at different exposures. Keep
        # each independently verified three-peak marker, preferring the
        # original exposure's bounds when it is already readable.
        for box in found:
            if not any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7
                       for old in marked):
                marked.append(box)
        for box in uncertain:
            same = next((entry for entry in partials if abs(box[0] - entry[0][0]) < 7
                         and abs(box[1] - entry[0][1]) < 7), None)
            if same is None:
                partials.append([box, 1, factor == 1])
            else:
                same[1] += 1
    for box, votes in supplementary:
        if votes < 2 or any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7
                            for old in marked):
            continue
        same_row = [old for old in marked if abs(box[1] - old[1]) < old[3] * .5]
        if same_row and (min(abs(box[1] - old[1]) for old in same_row) > 2 or
                         box[2] < .85 * float(np.median([old[2] for old in same_row]))):
            continue
        marked.append(box)
    # Exposure alone can turn an ordinary frame into a partial crown. Keep
    # native partials and those supported at more than one adjusted exposure.
    incomplete = [box for box, votes, native in partials if (native or votes >= 2) and not any(
        abs(box[0] - valid[0]) < 7 and abs(box[1] - valid[1]) < 7 for valid in marked)]
    return cursor_mask if cursor_mask is not None else base_mask, marked, incomplete


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


def _marked_boxes(mask, frame_mask=None):
    joined = cv2.morphologyEx(mask if frame_mask is None else frame_mask,
                            cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))
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
        # Resampling can separate a crown peak from the frame by one pixel.
        # Examine its actual position above the border, including pixels
        # outside the frame's connected component.
        band = mask[max(0, y + border_y - 7):y + border_y, x:x + width]
        peaks = []
        for centre in (.24, .49, .74):
            start, end = max(0, round(width * (centre - .07))), min(width, round(width * (centre + .07)))
            peaks.append(int(band[:, start:end].sum()) if band.size else 0)
        box = (float(x + width / 2), int(y + border_y), int(width), int(height - border_y))
        if min(peaks) < 2:
            # An ordinary gold/brown frame has no crown above its border.
            # A partly visible crown must still block automatic acceptance.
            if max(peaks) >= 2:
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
    width = float(np.median([box[2] for box in marked])) if marked else 38.
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
    gaps = []
    for a, b in zip(grouped, grouped[1:]):
        steps = round((b - a) / (width * 41 / 38))
        if 1 <= steps <= 9 and .90 * width <= (b - a) / steps <= 1.22 * width:
            gaps.append((b - a) / steps)
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


def _row_reading(db, panel, marked, incomplete, row, all_rows, candidates, shared_layout, min_first_x):
    recipe = row["recipe"]
    info = {"selected_recipe": recipe, "reward_text": row["text"], "candidates": candidates,
            "family": f"Family {candidates[0]}" if len(candidates) == 1 else None,
            "visible_recipes": [item["recipe"] for item in all_rows]}
    if not recipe or row["score"] < .85 or row["match_score"] < .9:
        return _result("Reward was unclear — enter its propagated runes manually.", **info)
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
    # All recipe rows share one lattice. Several agreeing rows are stronger
    # spacing evidence than one exposure-expanded frame on an isolated row.
    layout = shared_layout or _tile_layout(panel, tile_top, tile_height, selected, min_first_x) or (71, 41)
    first_x, pitch = layout
    positions = sorted(round((box[0] - first_x) / pitch) + 1 for box in selected)
    if (len(set(positions)) != len(positions) or any(position < 1 or position > len(runes) for position in positions)
            or any(abs(box[0] - first_x - round((box[0] - first_x) / pitch) * pitch) > 6 for box in selected)):
        return _result("Marked rune positions conflict with the recipe's rune order — enter them manually.", **info)
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
    mask, marked, incomplete = _marked_image(panel)
    cursors = _cursors(mask)
    detections, title = panel.info.get("propagation_rows") or _read_panel_rows(panel)
    if title is None:
        return _result("Runeshape Combinations panel was not found — review the capture.")
    with logger._connect() as db:
        rows = _reward_rows(db, detections)
        candidates = _family_candidates(db, rows)
        min_first_x = 46 if cursors else 15
        # Plain tile geometry locates the rune positions. The three crown
        # peaks identify propagation; a surrounding gold frame is optional.
        peak_masks = None
        for row in rows:
            entry = db.execute("SELECT sockets FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
            if entry is None:
                continue
            gray_layout = _tile_layout(panel, row["y1"] - 41, 38, [], min_first_x)
            if gray_layout is None:
                # The standard full-margin panel has a fixed native lattice.
                # A crop without that margin needs recoverable gray geometry.
                if not cursors:
                    continue
                gray_layout = (71., 41.)
            if peak_masks is None:
                pixels = np.asarray(panel, dtype=np.float32)
                peak_masks = [(factor, _gold_mask(panel if factor == 1 else Image.fromarray(
                    np.clip(pixels * factor, 0, 255).astype(np.uint8))))
                    for factor in (1, .9, 1.1, .8, 1.2, 1.35, 1.5, .95, 1.05, 1.25)]
            crown_rows = [{**row, "sockets": entry["sockets"]}]
            crowns = supported_peak_boxes(peak_masks, crown_rows,
                                          tile_layout=gray_layout,
                                          tile_size=gray_layout[1] * 38 / 41)
            for box in crowns:
                if not any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7 for old in marked):
                    marked.append(box)
            row_crowns = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
            if row_crowns:
                # A clear crown anchors its paired partial crown more
                # accurately than the OCR text's occasionally shifted top.
                crown_rows[0]["crown_top"] = float(np.median([box[1] for box in row_crowns]))
            for box in supported_partial_peak_boxes(peak_masks, crown_rows, tile_layout=gray_layout,
                                                     tile_size=gray_layout[1] * 38 / 41):
                if not any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7 for old in incomplete):
                    incomplete.append(box)
        incomplete = [box for box in incomplete if not any(
            abs(box[0] - valid[0]) < 7 and abs(box[1] - valid[1]) < 7 for valid in marked)]
        layouts = []
        for row in rows:
            boxes = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
            if boxes:
                layout = _tile_layout(panel, float(np.median([box[1] for box in boxes])),
                                      float(np.median([box[3] for box in boxes])), boxes, min_first_x)
                if layout:
                    layouts.append(layout)
        agreeing = max(([other for other in layouts if abs(other[0] - layout[0]) <= 2
                         and abs(other[1] - layout[1]) <= 2] for layout in layouts),
                       key=len, default=[])
        shared = tuple(float(np.median([layout[index] for layout in agreeing]))
                       for index in range(2)) if len(agreeing) >= 2 else None
        choices = [_row_reading(db, panel, marked, incomplete, row, rows, candidates, shared, min_first_x)
                   for row in rows]
    if len(cursors) == 1:
        selected = [index for index, row in enumerate(rows) if 3 <= row["y1"] - cursors[0]["y"] <= 62]
        if len(selected) == 1:
            return {**choices[selected[0]], "choices": choices}
    return _result("Choose the propagated recipe below and approve its runes, or enter the runes manually.",
                   choices=choices, candidates=candidates,
                   visible_recipes=[row["recipe"] for row in rows])
