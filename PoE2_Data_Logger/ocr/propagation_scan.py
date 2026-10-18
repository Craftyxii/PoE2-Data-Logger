"""Read propagated rune positions from a normalized Runeshape recipe panel.

Three crown peaks mark propagation; a gold tile frame is optional. Verified
positions index each stored recipe combination and can recover missing crowns
across a recognized family. Two rows with matching plausible fragments, or one
fragment for a lone recipe, supply tentative suggestions requiring manual approval.
Unrecognized recipes, conflicting marks or unverified source origins remain unresolved."""

from __future__ import annotations

import re
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr import opened_scan, runehelper_ocr
from PoE2_Data_Logger.ocr.propagation_marks import (
    PEAK_EXPOSURES, supported_fragment_peak_boxes, supported_partial_peak_boxes, supported_peak_boxes, three_peak_boxes,
)

_RAW_ROW_CACHE = threading.local()


def _result(status, **values):
    """Build a held propagation result whose caller-supplied fields can override defaults."""
    return {"mode": "propagation", "selected_recipe": None, "runes": [],
            "positions": [], "can_use": False, "status": status, "family": None,
            "candidates": [], "choices": [], **values}


def _panel(image, strictness=ocr_sensitivity.DEFAULT):
    """Return a 575-pixel-wide recipe panel, preserving verified cropped-panel OCR rows.

    Try existing marks or a heading before finding a left-side panel in a larger
    capture; images below the minimum usable dimensions return no panel.
    Heading confidence uses the same frozen strictness as its reward rows.
    """
    if image.width < 160 or image.height < 120:
        return None
    candidate = image.resize((575, max(1, round(image.height * 575 / image.width))),
                             Image.Resampling.LANCZOS)
    marks = _marked_image(candidate)
    _, marked, _ = marks
    # Only an accepted candidate reuses this reading. Reframing below changes
    # the pixels and must perform its own crown search.
    candidate.info["propagation_marks"] = marks
    if marked:
        # Already-cropped panels can omit the arrow margin. The generic frame
        # finder may otherwise mistake a recipe border for the panel header.
        return candidate
    detections, title = (_read_panel_rows(candidate) if strictness == ocr_sensitivity.DEFAULT else
                         _read_panel_rows(candidate, strictness=strictness))
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
    panel = frame.resize((575, max(1, round(frame.height * scale))), Image.Resampling.LANCZOS)
    panel.info.pop("propagation_marks", None)
    return panel


def _rapid_rows(image):
    """Reuse bounded worker-local raw OCR evidence for exactly unchanged pixels.

    Recipe/family matching, strictness, mark geometry and approval are evaluated
    afresh by callers. Cached row dictionaries never escape to caller mutation.
    """
    with opened_scan.OCR_LOCK:
        engine = opened_scan._engine()
        parts = tuple(getattr(engine, name, None) for name in ("text_det", "text_cls", "text_rec"))
        sessions = tuple(getattr(part, "session", None) for part in parts)
        owners = (engine, getattr(engine, "load_img", None), *parts, *sessions,
                  *(getattr(session, "session", None) for session in sessions),
                  *(getattr(part, "postprocess_op", None) for part in parts))
        attributes = (("limit_side_len", "limit_type", "mean", "std"),
                      ("cls_image_shape", "cls_batch_num", "cls_thresh"),
                      ("rec_image_shape", "rec_batch_num"))
        processing = tuple(repr(tuple(getattr(part, name, None) for name in names))
                           for part, names in zip(parts, attributes))
        detection = getattr(parts[0], "postprocess_op", None)
        signature = (repr(getattr(engine, "cfg", None)),
                     tuple(getattr(engine, name, None) for name in
                           ("text_score", "use_det", "use_cls", "use_rec", "min_side_len", "max_side_len")),
                     processing, repr(tuple(getattr(detection, name, None) for name in
                         ("thresh", "box_thresh", "max_candidates", "unclip_ratio", "use_dilation", "score_mode"))))
        state = getattr(_RAW_ROW_CACHE, "state", None)
        if (state is None or any(old is not new for old, new in zip(state[0], owners))
                or state[1] != signature):
            state = (owners, signature, OrderedDict())
            _RAW_ROW_CACHE.state = state
        cache = state[2]
        key = (image.mode, image.size, hashlib.sha256(image.tobytes()).digest())
        if key in cache:
            cache.move_to_end(key)
            return [dict(row) for row in cache[key]]
        found = engine(image)
        rows = []
        if found.boxes is not None:
            for box, text, score in zip(found.boxes, found.txts, found.scores):
                xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
                rows.append({"text": text.strip(), "score": float(score), "x1": min(xs),
                             "x2": max(xs), "y1": min(ys), "y2": max(ys)})
        # Eight small raw-evidence sets; oversized arbitrary inputs still run
        # normally and do not enlarge a long-lived worker's retained memory.
        if len(rows) <= 1024 and sum(len(row["text"].encode("utf-8")) for row in rows) <= 65536:
            cache[key] = tuple(dict(row) for row in rows)
            if len(cache) > 8:
                cache.popitem(last=False)
    return rows


def _read_panel_rows(image, strictness=ocr_sensitivity.DEFAULT):
    """Retain rows below the required heading using the capture's confidence floor."""
    rows = _rapid_rows(image)
    title = next((row for row in rows
                  if opened_scan._key(row["text"]) == "runeshapecombinations"
                  and row["score"] >= ocr_sensitivity.clear_threshold(.8, strictness, .15)), None)
    if title is None:
        return [], None
    return [row for row in rows if row["y1"] > title["y2"] + 12], title


def _gold_mask(image, *, pale=False):
    """Select gold pixels, optionally recovering bright, desaturated highlights.

    Cursor detection and initial crown searches use saturated gold. Pale gold
    is limited to frame recovery and crown checks above recognized recipe rows.
    """
    rgb = np.asarray(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue = (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
    colour = rgb[:, :, 0].astype(np.int16) - rgb[:, :, 2]
    gold = hue & (hsv[:, :, 1] >= 55) & (hsv[:, :, 2] >= 155) & (colour >= 55)
    if pale:
        gold |= hue & (hsv[:, :, 1] >= 25) & (hsv[:, :, 2] >= 225) & (colour >= 25)
    return gold.astype(np.uint8)


def _marked_image(image):
    # Exposure changes can fragment the gold frame. Keep the same colour and
    # three-peak geometry checks while looking at a small exposure bracket.
    """Combine exposure-bracket crown detections and retain supported incomplete marks.

    Pale pixels may recover a frame, but peaks still need the gold mask; nearby
    boxes are deduplicated and cursor evidence chooses the returned mask.
    """
    pixels = np.asarray(image, dtype=np.float32)
    marked, partials, supplementary = [], [], []
    cursor_mask = None
    base_mask = None
    for factor in (1, .9, 1.1, .8, 1.2, 1.35, 1.5, .95, 1.05, 1.25):
        shown = image if factor == 1 else Image.fromarray(np.clip(pixels * factor, 0, 255).astype(np.uint8))
        mask = _gold_mask(shown)
        # Bright captures can make the frame almost white. This additional
        # mask locates the tile only; peaks still require saturated gold.
        found, uncertain = _marked_boxes(mask)
        pale_found, _ = _marked_boxes(mask, _gold_mask(shown, pale=True))
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
    """Find arrow-shaped gold components in the normalized panel’s left 46-pixel margin."""
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
    """Locate framed tiles and validate isolated crown peaks above their border.

    Framed and frameless marks use the same spacing, baseline and vertical
    support checks. Gold artifacts with insufficient geometry remain uncertain
    so a second damaged mark cannot disappear from a paired reading.
    """
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
        if max(peaks) < 2:
            continue
        top = max(0, box[1] - 7)
        left, right = max(0, x - 6), min(mask.shape[1], x + width + 6)
        local_mask = mask[top:box[1] + 2, left:right]
        crown_rows = [{"y1": box[1] - top + width * 41 / 38, "sockets": 1}]
        crowns = three_peak_boxes(local_mask, crown_rows,
                                  tile_layout=(box[0] - left, width * 41 / 38), tile_size=width)
        if crowns:
            found.append(box)
        elif max(peaks) >= 2:
            uncertain.append(box)
    return found, uncertain


def _tile_layout(image, tile_top, tile_height, marked, min_first_x=15, sockets=None, reference=None):
    """Recover the first tile and spacing when a crop excludes the arrow margin."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    top, bottom = max(0, round(tile_top)), min(image.height, round(tile_top + tile_height))
    if bottom <= top:
        return None
    edges = cv2.Canny(gray[top:bottom], 50, 100)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    # An exposure-expanded crown frame can be wider than its square tile
    # body. It must not enlarge the pitch and shift the first rune by one.
    width = min(float(np.median([box[2] for box in marked])), tile_height) if marked else tile_height
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
    if not gaps and reference is None:
        return None
    first_x, pitch = reference if reference is not None else (grouped[0], float(np.median(gaps)))
    if reference is not None:
        # Interior rune strokes can form square contours whose centres are
        # not tile centres. The proven sibling lattice distinguishes those
        # strokes from aligned frames; it must still have visible support here.
        grouped = [centre for centre in grouped if
                   abs((centre - first_x) / pitch - round((centre - first_x) / pitch)) <= .16]
        if not grouped:
            return None
    if any(abs((centre - first_x) / pitch - round((centre - first_x) / pitch)) > .16
           for centre in grouped):
        return None
    # A glyph can interrupt its frame contour. Include preceding occupied
    # tiles on the same verified grid rather than mislabelling tile two as one.
    while reference is None and first_x - pitch >= min_first_x:
        x = round(first_x - pitch)
        half = round(pitch * 12 / 41)
        sample = gray[round(tile_top + tile_height * .18):round(tile_top + tile_height * .83),
                      max(0, x - half):x + half]
        if not sample.size or sample.std() < 28:
            break
        first_x -= pitch
    if sockets is not None and reference is not None:
        # A verified sibling row already establishes socket one. Interrupted
        # contours in this row need not independently rediscover both ends,
        # but an actual tile beyond its recipe still contradicts that recipe.
        if any(round((centre - first_x) / pitch) < 0 or
               round((centre - first_x) / pitch) >= sockets for centre in grouped):
            return None
    elif sockets is not None and abs(grouped[-1] - first_x - (sockets - 1) * pitch) > 6:
        # Rune artwork can interrupt the final frame contour. The complete
        # occupied bar and an empty following socket independently verify
        # its endpoint; an erased first tile or extra quantity variant cannot.
        half = round(pitch * 12 / 41)
        occupied = []
        for index in range(sockets + 1):
            centre = round(first_x + index * pitch)
            sample = gray[round(tile_top + tile_height * .18):round(tile_top + tile_height * .83),
                          max(0, centre - half):min(gray.shape[1], centre + half)]
            occupied.append(bool(sample.size and sample.std() >= 28))
        if not all(occupied[:sockets]) or occupied[sockets]:
            return None
    return (first_x, pitch) if min_first_x <= first_x <= 85 else None


def _reward_rows(db, detections, strictness=ocr_sensitivity.DEFAULT):
    """Canonicalize ordered reward rows while preserving quantity and skill-level constraints.

    Strictness adjusts the default .92 review floor for unprefixed prose while
    quantity, explicit level and ambiguity guards remain mandatory.
    """
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
        options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}
        recipe, confidence = opened_scan._match(db, text, count, choices, **options)
        if recipe and re.findall(r"\blevel\s*(\d+)\b", recipe, re.I) != levels:
            recipe = None
        if not matched and not level and (not recipe or confidence <
                                         ocr_sensitivity.review_threshold(.92, strictness, .08)):
            continue
        rows.append({**detection, "recipe": recipe, "match_score": confidence})
    return rows


def _family_candidates(db, rows):
    """Find valid families containing the complete recognized sequence as a contiguous slice."""
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


def _row_reading(db, panel, marked, incomplete, row, all_rows, candidates, shared_layout, min_first_x,
                 strictness=ocr_sensitivity.DEFAULT, row_layout=None):
    """Map three-marked tile positions to recipe-order names; hold unverified geometry.

    Rune names come from the stored combination order, not glyph classification.
    A verified row origin and spacing are required before a position can be saved.
    Lower strictness can accept tentative text, while crown and position guards
    remain mandatory before presenting approvable propagated runes.
    """
    recipe = row["recipe"]
    info = {"selected_recipe": recipe, "reward_text": row["text"], "candidates": candidates,
            "family": f"Family {candidates[0]}" if len(candidates) == 1 else None,
            "visible_recipes": [item["recipe"] for item in all_rows]}
    if (not recipe or row["score"] < ocr_sensitivity.review_threshold(.85, strictness, .15) or
            row["match_score"] < ocr_sensitivity.review_threshold(.9, strictness, .08)):
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
    layout = row_layout or _tile_layout(panel, tile_top, tile_height, selected, min_first_x,
                                        entry["sockets"], reference=shared_layout)
    if layout is None:
        return _result("The first rune position was not clear — enter the propagated runes manually.", **info)
    first_x, pitch = layout
    positions = sorted(round((box[0] - first_x) / pitch) + 1 for box in selected)
    if (len(set(positions)) != len(positions) or any(position < 1 or position > len(runes) for position in positions)
            or any(abs(box[0] - first_x - round((box[0] - first_x) / pitch) * pitch) > 6 for box in selected)):
        return _result("Marked rune positions conflict with the recipe's rune order — enter them manually.", **info)
    clear = (row["score"] >= ocr_sensitivity.clear_threshold(.85, strictness, .15) and
             row["match_score"] >= ocr_sensitivity.clear_threshold(.9, strictness, .08))
    status = ("Propagation runes detected — approve this recipe to add them to the chain." if clear else
              "Propagation suggestion needs review — enter its runes manually.")
    return _result(status, **info, positions=positions,
                   runes=[runes[position - 1] for position in positions], can_use=clear)


def _recover_group_slots(db, panel, marked, incomplete, rows, candidates, choices,
                         row_layouts, shared_layout, min_first_x, strictness, fragments):
    """Recover shared crown sockets in a verified family group or a lone recipe.

    Independent complete crowns contribute distinct indices across recipes.
    Plausible fragments can suggest a slot when two separate rows agree, or
    when just one recipe is visible. Sources need verified geometry and clear
    text; family recipients use their resolved database rune order. Short
    recipes receive only shared sockets that exist in their combination;
    conflicting crown evidence holds the group.
    Fragment consensus is tentative and always requires manual approval.
    """
    if not rows or (len(rows) > 1 and len(candidates) != 1):
        return choices
    positions = set()
    observations = []
    conflict = False
    for index, (row, choice) in enumerate(zip(rows, choices)):
        boxes = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
        # A held partial pair may still contain one independently verified
        # complete crown. Read it without letting the partial supply evidence.
        observed = (choice if choice["can_use"] else
                    _row_reading(db, panel, marked, [], row, rows, candidates,
                                 shared_layout, min_first_x, strictness=strictness,
                                 row_layout=row_layouts.get(index)))
        sockets = observed["positions"] if observed["can_use"] else []
        observations.append(sockets)
        positions.update(sockets)
        conflict |= len(boxes) > 2
    confirmed = positions.copy()
    fragment_rows = {}
    for index, row in enumerate(rows):
        layout = row_layouts.get(index)
        if (layout is None or row["score"] < ocr_sensitivity.clear_threshold(.85, strictness, .15)
                or row["match_score"] < ocr_sensitivity.clear_threshold(.9, strictness, .08)):
            continue
        first_x, pitch = layout
        # One row casts at most one vote for each socket, irrespective of the
        # number of exposures or duplicate fragment boxes that detected it.
        for box in fragments.get(index, []):
            socket = round((box[0] - first_x) / pitch) + 1
            if abs(box[0] - first_x - (socket - 1) * pitch) <= 6:
                fragment_rows.setdefault(socket, set()).add(index)
    minimum_rows = 1 if len(rows) == 1 else 2
    positions.update(socket for socket, votes in fragment_rows.items() if len(votes) >= minimum_rows)
    if not positions and not conflict:
        return choices
    conflict |= len(positions) > 2
    for index, row in enumerate(rows):
        complete = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
        partials = [box for box in incomplete if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
        if not complete and not partials:
            continue
        layout = row_layouts.get(index)
        if layout is None:
            # An unlocated mark could hide a second socket, so it holds recovery.
            conflict = True
            continue
        first_x, pitch = layout
        for box in complete + partials:
            socket = round((box[0] - first_x) / pitch) + 1
            if socket not in positions or abs(box[0] - first_x - (socket - 1) * pitch) > 6:
                conflict = True
    combinations = []
    for row in rows:
        entry = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
        runes = [rune.strip() for rune in entry["combo"].split("+")] if entry else []
        combinations.append(runes)
        if (not entry or len(runes) != entry["sockets"] or
                any(not rune or rune.lower() == "unresolved" for rune in runes)):
            conflict = True
    if conflict:
        return [{**choice, "positions": [], "runes": [], "can_use": False,
                 "status": (choice["status"] if "first rune position" in choice["status"] else
                            "Crown sockets conflict across this recipe group — review its marked runes."),
                 "position_source": "group_conflict"} for choice in choices]
    recovered = []
    for index, (row, choice, runes) in enumerate(zip(rows, choices, combinations)):
        clear = (row["score"] >= ocr_sensitivity.clear_threshold(.85, strictness, .15) and
                 row["match_score"] >= ocr_sensitivity.clear_threshold(.9, strictness, .08))
        # A unique family supplies each recipient's socket order. Only a lone
        # recipe must establish its own geometry and confidence at this stage.
        if (row.get("socket_span_conflict") or
                (len(rows) == 1 and (index not in row_layouts or not clear))):
            recovered.append(choice)
            continue
        ordered = sorted(socket for socket in positions if 1 <= socket <= len(runes))
        if not ordered:
            recovered.append(choice)
            continue
        recovered.append({**choice, "positions": ordered.copy(),
                          "runes": [runes[socket - 1] for socket in ordered], "can_use": True,
                          "status": ("Fragment-based propagation suggestion — verify its marked runes before approving."
                                     if positions - confirmed else
                                     "Propagation runes detected — approve this recipe to add them to the chain."),
                          "position_source": "fragment_consensus" if positions - confirmed else "family_group",
                          "fragment_votes": {str(socket): len(votes) for socket, votes in fragment_rows.items()},
                          "observed_positions": observations[index],
                          "recovered_positions": [socket for socket in ordered if socket not in observations[index]]})
    return recovered


def _scope_row_marks(db, panel, rows, marked, incomplete, row_layouts, shared_layout,
                     candidates, min_first_x, peak_masks, body_offset):
    """Recheck crown evidence at each verified socket instead of trusting broad frame anchors.

    Exposure-expanded frames and glyph glints can duplicate a crown or look
    like a damaged second crown inside a tile. Only peaks above the verified
    row lattice participate in shared-slot recovery. A unique family's intact
    sibling bars may establish missing endpoints, but never a default lattice.
    """
    complete, partials, fragments = [], [], {}
    for index, row in enumerate(rows):
        entry = db.execute("SELECT sockets FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
        if entry is None:
            continue
        boxes = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
        layout = row_layouts.get(index)
        reference = shared_layout if len(candidates) == 1 else None
        anchor_layout = layout or reference
        aligned = [box for box in boxes if anchor_layout is None or
                   abs(box[0] - anchor_layout[0] - round(
                       (box[0] - anchor_layout[0]) / anchor_layout[1]) * anchor_layout[1]) <= 6]
        top = (float(np.median([box[1] for box in aligned])) if aligned else
               row["y1"] - (body_offset if body_offset is not None else 41))
        if layout is None and reference is not None:
            height = reference[1] * 38 / 41
            layout = _tile_layout(panel, top, height, aligned, min_first_x,
                                  entry["sockets"], reference=reference)
            # The same supported lattice without recipe bounds distinguishes
            # extra visible sockets from missing recipient geometry.
            if layout is None and _tile_layout(panel, top, height, aligned, min_first_x,
                                               reference=reference) is not None:
                row["socket_span_conflict"] = True
        if layout is None or peak_masks is None:
            # Without a verified origin, retain uncertainty rather than
            # converting a guessed standard offset into a rune selection.
            complete.extend(boxes)
            partials.extend(box for box in incomplete
                            if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62)
            continue
        row_layouts[index] = layout
        size = layout[1] * 38 / 41
        crown_row = {**row, "sockets": entry["sockets"], "crown_top": top,
                     "y1": top + layout[1]}
        found = supported_peak_boxes(peak_masks, [crown_row], tile_layout=layout, tile_size=size)
        uncertain = supported_partial_peak_boxes(peak_masks, [crown_row],
                                                 tile_layout=layout, tile_size=size)
        fragments[index] = supported_fragment_peak_boxes(peak_masks, [crown_row],
                                                         tile_layout=layout, tile_size=size)
        # Socket identity, rather than raw pixel proximity, deduplicates
        # exposure retries without merging neighbouring propagated sockets.
        def socket(box):
            """Identify the one-based socket on this row's verified lattice."""
            return round((box[0] - layout[0]) / layout[1]) + 1

        found_by_socket = {socket(box): box for box in found}
        complete.extend(found_by_socket.values())
        partial_by_socket = {socket(box): box for box in uncertain + fragments[index]
                             if socket(box) not in found_by_socket}
        partials.extend(partial_by_socket.values())
    return complete, partials, fragments


def scan_propagation(image: Image.Image | Path, strictness=ocr_sensitivity.DEFAULT):
    """Resolve recipe suggestions with scoped pale-crown recovery and verified tile positions.

    Capture strictness applies to recipe text. Three-peak geometry confirms
    crowns; supported fragment consensus can suggest sockets for manual review.
    Verified source origins, paired-mark conflict checks and explicit approval remain
    mandatory. Recovery changes no database records or dropdown definitions.
    """
    strictness = ocr_sensitivity.validate(strictness)
    options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}
    if isinstance(image, Image.Image):
        source = image.convert("RGB")
    else:
        with Image.open(image) as opened:
            source = opened.convert("RGB")
    panel = _panel(source, **options)
    if panel is None:
        return _result("Runeshape panel is too small — capture the full recipe list and cursor.")
    detections, title = panel.info.get("propagation_rows") or _read_panel_rows(panel, **options)
    if title is None:
        return _result("Runeshape Combinations panel was not found — review the capture.")
    cached_marks = panel.info.pop("propagation_marks", None)
    mask, marked, incomplete = cached_marks if cached_marks is not None else _marked_image(panel)
    cursors = _cursors(mask)
    with logger._connect() as db:
        rows = _reward_rows(db, detections, **options)
        candidates = _family_candidates(db, rows)
        min_first_x = 46 if cursors else 15
        # Plain tile geometry locates the rune positions. The three crown
        # peaks identify propagation; a surrounding gold frame is optional.
        peak_masks = None
        initial_layouts = {}
        for row_index, row in enumerate(rows):
            entry = db.execute("SELECT sockets FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
            if entry is None:
                continue
            gray_layout = _tile_layout(panel, row["y1"] - 41, 38, [], min_first_x, entry["sockets"])
            if gray_layout is None:
                # OCR text boxes vary a few pixels in height. A clipped frame
                # contour must not hide an otherwise verified full socket bar.
                for offset in (-2, 2, -4, 4):
                    gray_layout = _tile_layout(panel, row["y1"] - 41 + offset,
                                              38, [], min_first_x, entry["sockets"])
                    if gray_layout is not None:
                        break
            if gray_layout is not None:
                initial_layouts[row_index] = gray_layout
            if gray_layout is None:
                # The standard full-margin panel has a fixed native lattice.
                # Without one, locate crown anchors only; the final reading
                # still requires a verified gray origin and full socket bar.
                if cursors:
                    gray_layout = (71., 41.)
            if peak_masks is None:
                pixels = np.asarray(panel, dtype=np.float32)
                peak_masks = [(factor, _gold_mask(panel if factor == 1 else Image.fromarray(
                    np.clip(pixels * factor, 0, 255).astype(np.uint8)), pale=True))
                    for factor in PEAK_EXPOSURES]
            crown_rows = [{**row, "sockets": entry["sockets"]}]
            crowns = supported_peak_boxes(peak_masks, crown_rows,
                                          tile_layout=gray_layout,
                                          tile_size=gray_layout[1] * 38 / 41 if gray_layout else 38)
            for box in crowns:
                if not any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7 for old in marked):
                    marked.append(box)
        row_layouts = dict(initial_layouts)
        anchors = [(row["y1"] - box[1], box[3]) for row in rows for box in marked
                   if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
        body_offset = float(np.median([anchor[0] for anchor in anchors])) if anchors else None
        body_height = float(np.median([anchor[1] for anchor in anchors])) if anchors else None
        if body_offset is not None and peak_masks is not None:
            # A panel can contain tiles smaller than the reference width.
            # Observed crown bodies anchor the other rows at that same scale;
            # their rune indices still need independent gray-frame validation.
            for row in rows:
                entry = db.execute("SELECT sockets FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
                if entry is None:
                    continue
                crown_rows = [{**row, "sockets": entry["sockets"],
                               "y1": row["y1"] - body_offset + body_height * 41 / 38}]
                for box in supported_peak_boxes(peak_masks, crown_rows, tile_layout=None,
                                                 tile_size=body_height):
                    if not any(abs(box[0] - old[0]) < 7 and abs(box[1] - old[1]) < 7 for old in marked):
                        marked.append(box)
        for row_index, row in enumerate(rows):
            boxes = [box for box in marked if 3 <= row["y1"] - box[1] - box[3] / 2 <= 62]
            if boxes or body_offset is not None:
                entry = db.execute("SELECT sockets FROM recipes WHERE name=?", (row["recipe"],)).fetchone()
                if entry is None:
                    continue
                tile_top = float(np.median([box[1] for box in boxes])) if boxes else row["y1"] - body_offset
                tile_height = float(np.median([box[3] for box in boxes])) if boxes else body_height
                layout = _tile_layout(panel, tile_top, tile_height, boxes, min_first_x, entry["sockets"])
                if layout:
                    row_layouts[row_index] = layout
        layouts = list(row_layouts.values())
        agreeing = max(([other for other in layouts if abs(other[0] - layout[0]) <= 2
                         and abs(other[1] - layout[1]) <= 2] for layout in layouts),
                       key=len, default=[])
        # One independently verified complete socket bar is enough to anchor
        # a unique family; requiring two bars defeated single-crown recovery.
        shared = tuple(float(np.median([layout[index] for layout in agreeing]))
                       for index in range(2)) if agreeing else None
        marked, incomplete, fragments = _scope_row_marks(
            db, panel, rows, marked, incomplete, row_layouts, shared, candidates,
            min_first_x, peak_masks, body_offset)
        choices = [_row_reading(db, panel, marked, incomplete, row, rows, candidates, shared,
                               min_first_x, row_layout=row_layouts.get(index), **options)
                   for index, row in enumerate(rows)]
        choices = _recover_group_slots(db, panel, marked, incomplete, rows, candidates, choices,
                                       row_layouts, shared, min_first_x, strictness, fragments)
        if strictness != ocr_sensitivity.DEFAULT:
            for choice in choices:
                choice["_ocr_strictness"] = strictness
    if len(cursors) == 1:
        selected = [index for index, row in enumerate(rows) if 3 <= row["y1"] - cursors[0]["y"] <= 62]
        if len(selected) == 1:
            return {**choices[selected[0]], "choices": choices}
    return _result("Choose the propagated recipe below and approve its runes, or enter the runes manually.",
                   choices=choices, candidates=candidates,
                   visible_recipes=[row["recipe"] for row in rows])
