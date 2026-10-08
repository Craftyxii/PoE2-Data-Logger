"""Locate a complete Ritual reward grid and its occupied item footprints.

This module examines a captured image only.  It neither reads labels nor
identifies item artwork.  Returning ``None`` means that a complete 12 by 10
grid could not be established; callers must not treat that as an empty page.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


COLUMNS = 12
ROWS = 10


def _line_groups(lines, horizontal, tolerance=2.5):
    """Combine the two Canny edges belonging to one thin divider."""
    segments = []
    for x1, y1, x2, y2 in lines:
        if horizontal and abs(y2 - y1) <= 1:
            segments.append(((y1 + y2) / 2, min(x1, x2), max(x1, x2)))
        elif not horizontal and abs(x2 - x1) <= 1:
            segments.append(((x1 + x2) / 2, min(y1, y2), max(y1, y2)))
    groups = []
    for at, start, end in sorted(segments):
        if groups and at - groups[-1]["last"] <= tolerance:
            group = groups[-1]
            group["segments"].append((at, start, end))
            group["last"] = at
        else:
            groups.append({"segments": [(at, start, end)], "last": at})
    result = []
    for group in groups:
        parts = group["segments"]
        weights = [end - start for _, start, end in parts]
        result.append({
            "at": float(np.average([at for at, _, _ in parts], weights=weights)),
            "start": min(start for _, start, _ in parts),
            "end": max(end for _, _, end in parts),
        })
    return result


def _hits(groups, start, pitch, intervals, tolerance, boundary=None):
    matches = []
    for index in range(intervals + 1):
        at = start + index * pitch
        nearest = min(groups, key=lambda line: abs(line["at"] - at), default=None)
        if nearest is not None and abs(nearest["at"] - at) <= tolerance:
            matches.append((index, nearest))
        elif boundary is not None and min(abs(at), abs(at - boundary)) <= 1.5:
            # A tightly cropped grid can omit the edge outside the image.
            matches.append((index, {"at": at, "start": 0, "end": float("inf"),
                                    "image_boundary": True}))
    return matches


def _locate(pixels, header_box):
    height, width = pixels.shape[:2]
    edges = cv2.Canny(pixels, 12, 35)
    minimum = max(24, round(min(width, height) * .18))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
                            max(18, round(min(width, height) / 24)),
                            minLineLength=minimum,
                            maxLineGap=max(2, round(min(width, height) * .004)))
    if lines is None:
        return None
    horizontal = _line_groups(lines[:, 0], True, max(2.5, min(6., min(width, height) * .003)))
    if len(horizontal) < ROWS - 2:
        return None
    # A region ending exactly at the lower grid frame has no outside pixel
    # for Canny to compare.  Accept only that missing divider, established by
    # all ten preceding regularly spaced dividers; a clipped row cannot pass.
    for first in horizontal:
        for last in horizontal:
            edge_pitch = (last["at"] - first["at"]) / (ROWS - 1)
            if edge_pitch < 12 or abs(first["at"] + ROWS * edge_pitch - height) > 1:
                continue
            observed = _hits(horizontal, first["at"], edge_pitch, ROWS - 1,
                             max(1.8, min(3., edge_pitch * .045)))
            if len(observed) == ROWS and not any(
                    abs(line["at"] - height) <= 3 for line in horizontal):
                horizontal.append({
                    "at": float(height),
                    "start": float(np.median([line["start"] for _, line in observed])),
                    "end": float(np.median([line["end"] for _, line in observed])),
                    "image_boundary": True,
                })
                break
        else:
            continue
        break
    candidates = []
    # A normal page must expose both its top and bottom grid dividers.
    for first in horizontal:
        for last in horizontal:
            pitch = (last["at"] - first["at"]) / ROWS
            if 12 <= pitch <= min(width / COLUMNS, height / ROWS) + .5:
                candidates.append((first["at"], pitch, False))
    tight = abs(width / height - COLUMNS / ROWS) < .012
    if tight:
        candidates.append((0., (width / COLUMNS + height / ROWS) / 2, True))

    best = None
    for top, pitch, at_edge in candidates:
        tolerance = max(1.8, min(3., pitch * .045))
        bottom = top + ROWS * pitch
        if top < -.5 or bottom > height + .5:
            continue
        y_matches = _hits(horizontal, top, pitch, ROWS, tolerance,
                          height if at_edge else None)
        if len(y_matches) < ROWS - 2 or not all(
                index in {place for place, _ in y_matches} for index in (0, ROWS)):
            continue
        # Resizing can move an edge between adjacent pixels on successive
        # rows.  Requiring one perfectly straight Hough segment then loses
        # otherwise complete grids.  Project a narrow edge neighbourhood
        # through the full proposed grid height instead.
        strip = edges[max(0, round(top)):min(height, round(bottom))]
        radius = max(1, round(pitch * .04))
        neighbourhood = cv2.dilate(strip, np.ones((1, radius * 2 + 1), np.uint8))
        profile = neighbourhood.mean(axis=0) / 255
        raw_profile = strip.mean(axis=0) / 255
        row_profiles = []
        inset = max(1, round(pitch * .10))
        for row in range(ROWS):
            body = edges[max(0, round(top + row * pitch) + inset):
                         min(height, round(top + (row + 1) * pitch) - inset)]
            if not body.size:
                break
            straight = body.mean(axis=0).astype(np.float32) / 255
            row_profiles.append(cv2.dilate(straight[None, :],
                                          np.ones((1, round(tolerance) * 2 + 1), np.uint8))[0])
        if len(row_profiles) != ROWS:
            continue
        row_profiles = np.asarray(row_profiles)
        ends = [line["end"] for _, line in y_matches if not line.get("image_boundary")]
        if not ends:
            continue
        # Most horizontal dividers terminate at the right grid frame.  This
        # anchor avoids shifting the lattice into the surrounding ornament or
        # into another inventory in a full screenshot.
        right_anchor = float(np.median(ends))
        anchor = right_anchor - COLUMNS * pitch
        margin = max(3., pitch * .40)
        starts = set(np.arange(anchor - margin, anchor + margin + .25, .5))
        if at_edge:
            starts.add(0.)
        for left in sorted(starts):
            right = left + COLUMNS * pitch
            if left < -.5 or right > width + .5:
                continue
            x_matches = []
            edge_score = 0.
            for index in range(COLUMNS + 1):
                at = left + index * pitch
                low, high = max(0, round(at - tolerance)), min(width, round(at + tolerance) + 1)
                if high <= low:
                    continue
                nearest = low + int(np.argmax(raw_profile[low:high]))
                if profile[nearest] >= .78:
                    x_matches.append((index, {"at": float(nearest), "start": top, "end": bottom}))
                    edge_score += float(raw_profile[nearest])
                elif at_edge and min(abs(at), abs(at - width)) <= 1.5:
                    x_matches.append((index, {"at": at, "start": top, "end": bottom,
                                              "image_boundary": True}))
            positions = {place for place, _ in x_matches}
            # The left two columns can be covered by large equipment.  The
            # intact right side still anchors the complete page, rather than
            # allowing a shifted or partially clipped lattice.
            if len(x_matches) < COLUMNS - 1 or not all(
                    index in positions for index in (COLUMNS - 2, COLUMNS - 1, COLUMNS)):
                continue
            columns = np.rint(left + np.arange(3, COLUMNS) * pitch).astype(int)
            if columns.min() < 0 or columns.max() >= width:
                continue
            # Each of the ten rows must contain actual straight dividers.
            # Averaging the entire grid height alone can mistake eight intact
            # rows plus the ornament below them for a complete ten-row page.
            if ((row_profiles[:, columns] >= .60).sum(axis=1) < 7).any():
                continue
            coverage = [max(0., min(line["end"], right) - max(line["start"], left))
                        / (COLUMNS * pitch) for _, line in y_matches]
            if sum(value >= .45 for value in coverage) < ROWS - 3:
                continue
            horizontal_hits = 0
            for row in range(ROWS + 1):
                at = round(top + row * pitch)
                band = edges[max(0, at - radius):min(height, at + radius + 1),
                             max(0, round(left)):min(width, round(right))]
                supported = bool(band.size and (band > 0).any(axis=0).mean() >= .70)
                image_edge = (at_edge and min(abs(at), abs(at - height)) <= 1.5) or any(
                    index == row and line.get("image_boundary") for index, line in y_matches)
                horizontal_hits += supported or image_edge
            if horizontal_hits != ROWS + 1:
                continue
            if header_box is not None:
                hx1, hy1, hx2, hy2 = header_box
                center = (hx1 + hx2) / 2
                if not (left <= center <= right and hy2 - pitch * .3 <= top
                        and top - hy2 <= pitch * 5):
                    continue
            score = len(x_matches) + horizontal_hits + edge_score + sum(coverage) * .1
            record = (score, -left, left, top, pitch, x_matches, y_matches, at_edge)
            if best is None or record[:2] > best[:2]:
                best = record
    if best is None:
        return None
    _, _, left, top, pitch, x_matches, y_matches, at_edge = best
    # Refine the origin from all observed dividers, rather than one Hough edge.
    if not at_edge:
        x_real = [(index, line["at"]) for index, line in x_matches]
        y_real = [(index, line["at"]) for index, line in y_matches]
        x_pitch, _ = np.polyfit(*zip(*x_real), 1)
        y_pitch, _ = np.polyfit(*zip(*y_real), 1)
        if abs(x_pitch - y_pitch) > pitch * .025:
            return None
        pitch = float((x_pitch + y_pitch) / 2)
        left = float(np.median([at - index * pitch for index, at in x_real]))
        top = float(np.median([at - index * pitch for index, at in y_real]))
    right, bottom = left + COLUMNS * pitch, top + ROWS * pitch
    if left < -1 or top < -1 or right > width + 1 or bottom > height + 1:
        return None
    return {
        "origin": (max(0., left), max(0., top)), "pitch": pitch,
        "bounds": (max(0, round(left)), max(0, round(top)),
                   min(width, round(right)), min(height, round(bottom))),
        "horizontal_hits": ROWS + 1, "vertical_hits": len(x_matches),
        "image_boundary": at_edge or any(line.get("image_boundary") for _, line in y_matches),
        "edges": edges,
    }


def _cell_box(origin, pitch, column, row, width=1, height=1):
    x, y = origin
    return tuple(round(value) for value in
                 (x + column * pitch, y + row * pitch,
                  x + (column + width) * pitch, y + (row + height) * pitch))


def _occupied(pixels, box, pitch):
    left, top, right, bottom = box
    inset = max(2, round(pitch * .15))
    cell = pixels[top + inset:bottom - inset, left + inset:right - inset]
    if not cell.size:
        return False
    bright = cell.max(axis=2)
    # Empty slot motifs are dim; a highlighted cell's bright frame is outside
    # this inset.  A coloured item backing also survives a mostly dark icon.
    return bool((bright > 24).mean() >= .14 and
                (float(bright.std()) >= 6 or float(bright.mean()) >= 25))


@lru_cache(maxsize=2)
def _corner_template(bottom_right):
    name = "corner_bottom_right.png" if bottom_right else "corner_top_left.png"
    with Image.open(Path(__file__).resolve().parent / "ritual_assets" / name) as image:
        return np.asarray(image.convert("RGB"))


def _corner_strength(pixels, box, pitch, bottom_right=False):
    left, top, right, bottom = box
    # Brown equipment artwork alone is not an ornate frame.  Verify the
    # actual curled corner shape before using it to join multiple cells.
    template_size = max(3, round(pitch * 8 / 52.65))
    template = cv2.resize(_corner_template(bottom_right),
                          (template_size, template_size), interpolation=cv2.INTER_AREA)
    radius = max(1, round(pitch * .055))
    x, y = ((right - template_size, bottom - template_size) if bottom_right else (left, top))
    search = pixels[max(0, y - radius):y + template_size + radius,
                    max(0, x - radius):x + template_size + radius]
    if min(search.shape[:2]) < template_size:
        return 0.
    _, score, _, at = cv2.minMaxLoc(cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED))
    # Divider coordinates are rounded independently from the resampled art.
    # Check the colour of the actual matched ornament, rather than a fixed
    # patch which can include an extra row of background at another scale.
    patch = search[at[1]:at[1] + template_size, at[0]:at[0] + template_size]
    red, green, blue = patch.astype(np.float32).transpose(2, 0, 1)
    ornate = ((red > green * 1.17) & (green > blue * 1.15) &
              (red > 65) & (blue < red * .65))
    if float(ornate.mean()) < .35:
        return 0.
    # Icon artwork sometimes overlaps the lower ornament.  At small capture
    # scales the upper eight-pixel ornament also loses detail to resampling.
    threshold = .60 if bottom_right else (.65 if pitch < 35 else .72)
    return float(score >= threshold)


def _no_divider(pixels, edges, origin, pitch, row, column, vertical):
    """Join unframed items only when the interior divider is clearly absent."""
    x, y = origin
    inset = max(2, round(pitch * .16))
    radius = max(1, round(pitch * .045))
    if vertical:
        at = round(x + (column + 1) * pitch)
        start, end = round(y + row * pitch) + inset, round(y + (row + 1) * pitch) - inset
        strip = pixels[start:end, at - radius:at + radius + 1].astype(np.float32)
        line_edges = edges[start:end, at - radius:at + radius + 1].mean(axis=0) / 255
        changes = np.abs(np.diff(strip, axis=1)).max(axis=(1, 2))
    else:
        at = round(y + (row + 1) * pitch)
        start, end = round(x + column * pitch) + inset, round(x + (column + 1) * pitch) - inset
        strip = pixels[at - radius:at + radius + 1, start:end].astype(np.float32)
        line_edges = edges[at - radius:at + radius + 1, start:end].mean(axis=1) / 255
        changes = np.abs(np.diff(strip, axis=0)).max(axis=(0, 2))
    return bool(strip.size and float(line_edges.max()) < .25 and
                float((changes < 12).mean()) >= .90)


def _rewards(pixels, geometry):
    scale = 52.65 / geometry["pitch"]
    if abs(scale - 1) > .05:
        # The ornament samples are eight pixels wide at the game's standard
        # grid scale. Compare at that scale so resampling cannot turn an item
        # detail into a false lower corner or hide a genuine upper corner.
        normalized = cv2.resize(pixels, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_LANCZOS4)
        normalized_geometry = dict(geometry)
        normalized_geometry["origin"] = tuple(value * scale for value in geometry["origin"])
        normalized_geometry["pitch"] *= scale
        normalized_geometry["edges"] = cv2.Canny(normalized, 12, 35)
        groups = _rewards_at_scale(normalized, normalized_geometry)
        for reward in groups:
            row, column = divmod(min(reward["slots"]) - 1, COLUMNS)
            width = max((part - 1) % COLUMNS for part in reward["slots"]) - column + 1
            height = max((part - 1) // COLUMNS for part in reward["slots"]) - row + 1
            reward["box"] = _cell_box(geometry["origin"], geometry["pitch"], column, row,
                                      width, height)
        return groups
    return _rewards_at_scale(pixels, geometry)


def _rewards_at_scale(pixels, geometry):
    origin, pitch = geometry["origin"], geometry["pitch"]
    boxes = {row * COLUMNS + column + 1: _cell_box(origin, pitch, column, row)
             for row in range(ROWS) for column in range(COLUMNS)}
    occupied = {slot for slot, box in boxes.items() if _occupied(pixels, box, pitch)}
    top_corners = {slot: _corner_strength(pixels, boxes[slot], pitch) for slot in occupied}
    bottom_corners = {slot: _corner_strength(pixels, boxes[slot], pitch, True)
                      for slot in occupied}
    groups, used = [], set()
    # The game keeps faint cell dividers beneath large unique gear artwork.
    # Its paired ornate corners establish the outer item rectangle instead.
    for slot in sorted(occupied):
        if slot in used or top_corners[slot] < .35:
            continue
        row, column = divmod(slot - 1, COLUMNS)
        rectangles = []
        for height in range(1, min(4, ROWS - row) + 1):
            for width in range(1, min(2, COLUMNS - column) + 1):
                cells = {slot + dy * COLUMNS + dx
                         for dy in range(height) for dx in range(width)}
                last = slot + (height - 1) * COLUMNS + width - 1
                if not cells <= occupied or cells & used or bottom_corners[last] < .35:
                    continue
                if any(top_corners[part] >= .35 for part in cells - {slot}):
                    continue
                if any(bottom_corners[part] >= .35 for part in cells - {last}):
                    continue
                rectangles.append((len(cells), cells))
        if rectangles:
            # A narrow rectangle continuing into the next item's lower
            # corner can have fewer cells than this item's proper rectangle.
            # The first matching bottom edge closes the current frame.
            _, cells = min(rectangles, key=lambda pair: (
                max((part - 1) // COLUMNS for part in pair[1]), pair[0]))
            groups.append(cells)
            used.update(cells)

    remaining = occupied - used
    components = {slot: {slot} for slot in remaining}

    def join(first, second):
        a, b = components[first], components[second]
        if a is not b:
            combined = a | b
            for part in combined:
                components[part] = combined

    for slot in sorted(remaining):
        row, column = divmod(slot - 1, COLUMNS)
        for other, vertical in ((slot + 1, True), (slot + COLUMNS, False)):
            if other not in remaining or (vertical and column == COLUMNS - 1):
                continue
            if _no_divider(pixels, geometry["edges"], origin, pitch, row, column, vertical):
                join(slot, other)
    seen = set()
    for slot in sorted(remaining):
        cells = components[slot]
        key = tuple(sorted(cells))
        if key in seen:
            continue
        seen.add(key)
        rows = [(part - 1) // COLUMNS for part in cells]
        columns = [(part - 1) % COLUMNS for part in cells]
        width, height = max(columns) - min(columns) + 1, max(rows) - min(rows) + 1
        if len(cells) == width * height and width <= 2 and height <= 4:
            groups.append(cells)
        else:
            # An irregular region is not evidence for one large item.
            groups.extend({part} for part in sorted(cells))
    rewards = []
    for cells in sorted(groups, key=min):
        row, column = divmod(min(cells) - 1, COLUMNS)
        width = max((part - 1) % COLUMNS for part in cells) - column + 1
        height = max((part - 1) // COLUMNS for part in cells) - row + 1
        rewards.append({"slots": sorted(cells),
                        "box": _cell_box(origin, pitch, column, row, width, height)})
    return rewards


def detect_reward_grid(image: Image.Image, header_box=None) -> dict | None:
    """Return original-image boxes for every reward in a complete 12×10 grid.

    ``header_box`` can constrain detection to the grid below a separately
    verified Favours heading.  No heading is needed for a tightly cropped grid.
    Geometry confidence concerns the complete lattice, not item identity.
    """
    if not isinstance(image, Image.Image) or image.width < 144 or image.height < 120:
        return None
    pixels = np.asarray(image.convert("RGB"))
    geometry = _locate(pixels, header_box)
    if geometry is None and max(image.size) <= 1800:
        # At smaller capture scales, thin dividers can alternate between
        # neighbouring pixels. A full screenshot's Hough vote then misses
        # lines which remain visible in the reward grid. Search at a larger
        # sampling scale with the same complete-grid requirements; identify
        # item footprints from the original pixels afterwards.
        for scale in (4 / 3, 1.5):
            enlarged = image.resize((round(image.width * scale), round(image.height * scale)),
                                    Image.Resampling.LANCZOS)
            scale_x, scale_y = enlarged.width / image.width, enlarged.height / image.height
            enlarged_header = (tuple(value * (scale_x if index % 2 == 0 else scale_y)
                                     for index, value in enumerate(header_box))
                               if header_box is not None else None)
            candidate = _locate(np.asarray(enlarged.convert("RGB")), enlarged_header)
            if candidate is None:
                continue
            candidate["origin"] = (candidate["origin"][0] / scale_x,
                                   candidate["origin"][1] / scale_y)
            candidate["pitch"] /= (scale_x + scale_y) / 2
            candidate["bounds"] = tuple(round(value / (scale_x if index % 2 == 0 else scale_y))
                                        for index, value in enumerate(candidate["bounds"]))
            candidate["edges"] = cv2.Canny(pixels, 12, 35)
            geometry = candidate
            break
    if geometry is None:
        return None
    return {
        "bounds": geometry["bounds"], "columns": COLUMNS, "rows": ROWS,
        "pitch": geometry["pitch"], "rewards": _rewards(pixels, geometry),
        "confidence": round(min(1., (geometry["horizontal_hits"] / (ROWS + 1) +
                                     geometry["vertical_hits"] / (COLUMNS + 1)) / 2), 3),
        "evidence": {"horizontal_hits": geometry["horizontal_hits"],
                     "vertical_hits": geometry["vertical_hits"],
                     "complete": True, "image_boundary": geometry["image_boundary"]},
    }


def has_grid_structure(image: Image.Image) -> bool:
    """Flag an identifiable partial grid without claiming complete coverage.

    This also flags the ordinary inventory grid: a wrong capture region must
    not become a confidently complete Ritual page.  Text-only metadata or one
    isolated reference icon do not establish a grid.
    """
    if not isinstance(image, Image.Image) or min(image.size) < 64:
        return False
    pixels = np.asarray(image.convert("RGB"))
    height, width = pixels.shape[:2]
    edges = cv2.Canny(pixels, 12, 35)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
                            max(16, round(min(width, height) / 24)),
                            minLineLength=max(12, round(min(width, height) * .18)),
                            maxLineGap=max(2, round(min(width, height) * .004)))
    if lines is None:
        return False
    tolerance = max(2.5, min(6., min(width, height) * .003))
    horizontal = _line_groups(lines[:, 0], True, tolerance)
    vertical = _line_groups(lines[:, 0], False, tolerance)
    if len(horizontal) < 5 or len(vertical) < 5:
        return False
    gaps = [second["at"] - first["at"]
            for first, second in zip(horizontal, horizontal[1:])]
    pitches = {round(gap / multiple, 1) for gap in gaps for multiple in (1, 2)
               if 8 <= gap / multiple <= min(width, height) / 4}
    for pitch in sorted(pitches, reverse=True):
        tolerance = max(1.8, min(3., pitch * .045))
        radius = round(tolerance)
        for row in horizontal:
            top = row["at"]
            y_matches = _hits(horizontal, top, pitch, 4, tolerance)
            bottom = top + 4 * pitch
            if len(y_matches) < 4 or top < 0 or bottom >= height:
                continue
            strip = edges[round(top):round(bottom)]
            profile = strip.mean(axis=0).astype(np.float32) / 255
            profile = cv2.dilate(profile[None, :],
                                 np.ones((1, radius * 2 + 1), np.uint8))[0]
            for column in vertical:
                left = column["at"]
                right = left + 4 * pitch
                if left < 0 or right >= width:
                    continue
                positions = np.rint(left + np.arange(5) * pitch).astype(int)
                if (profile[positions] < .60).any():
                    continue
                supported = 0
                for index in range(5):
                    at = round(top + index * pitch)
                    band = edges[max(0, at - radius):min(height, at + radius + 1),
                                 round(left):round(right)]
                    supported += bool(band.size and (band > 0).any(axis=0).mean() >= .70)
                if supported == 5:
                    return True
    return False
