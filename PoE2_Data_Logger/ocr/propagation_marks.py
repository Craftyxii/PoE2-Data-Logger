"""Read the three gold crown peaks without requiring a rune tile frame."""

from __future__ import annotations

import numpy as np


def supported_peak_boxes(masks, recipe_rows, *, tile_layout, tile_size=38):
    """Retain native crowns or crowns verified at two exposure samples."""
    return _supported_boxes(masks, recipe_rows, tile_layout, tile_size, three_peak_boxes)


def supported_partial_peak_boxes(masks, recipe_rows, *, tile_layout, tile_size=38):
    """Keep a genuine incomplete crown so a paired rune cannot be omitted."""
    return _supported_boxes(masks, recipe_rows, tile_layout, tile_size, _partial_peak_boxes)


def _supported_boxes(masks, recipe_rows, tile_layout, tile_size, finder):
    groups = []
    for factor, mask in masks:
        for box in finder(mask, recipe_rows, tile_layout=tile_layout, tile_size=tile_size):
            match = next((entry for entry in groups if abs(box[0] - entry[0][0]) < 6
                          and abs(box[1] - entry[0][1]) < 8), None)
            if match is None:
                groups.append([box, {factor}, factor == 1])
            else:
                match[1].add(factor)
                if factor == 1:
                    match[0], match[2] = box, True
    return [box for box, factors, native in groups if native or len(factors) >= 2]


def _partial_peak_boxes(mask, recipe_rows, *, tile_layout, tile_size):
    pixels = np.asarray(mask, dtype=bool)
    first_x, pitch = tile_layout
    found = []
    for row in recipe_rows:
        target = float(row.get("crown_top", float(row["y1"]) - pitch))
        for index in range(int(row.get("sockets", 0))):
            centre = first_x + index * pitch
            left, right = max(0, round(centre - tile_size / 2)), min(pixels.shape[1], round(centre + tile_size / 2))
            expected = (centre - tile_size / 4, centre, centre + tile_size / 4)
            radius = 2 if "crown_top" in row else 5
            for bottom in range(max(2, round(target) - radius),
                                min(pixels.shape[0] - 2,
                                    round(target) + (1 if "crown_top" in row else radius + 1))):
                band = pixels[max(0, bottom - 6):bottom, left:right]
                columns = band.sum(axis=0)
                active = np.flatnonzero(columns)
                if not active.size:
                    continue
                runs = [np.arange(run[0], run[-1] + 1) for run in np.split(
                    active, np.flatnonzero(np.diff(active) > 3) + 1)]
                if len(runs) not in (1, 2):
                    continue
                offsets = []
                for run in runs:
                    area = int(columns[run].sum())
                    if (not 2 <= len(run) <= 8 or area < 2
                            or band[:, run].any(axis=1).sum() < 2):
                        break
                    at = left + float(np.average(run, weights=columns[run]))
                    offset = min(range(3), key=lambda position: abs(at - expected[position]))
                    if abs(at - expected[offset]) > 2.5:
                        break
                    offsets.append(offset)
                if len(set(offsets)) != len(runs):
                    continue
                below = pixels[bottom:bottom + 2, left:right]
                inset = max(2, round(tile_size * .18))
                joined_border = any(
                    max((len(run) for run in np.split(np.flatnonzero(line),
                         np.flatnonzero(np.diff(np.flatnonzero(line)) > 1) + 1)), default=0)
                    >= tile_size * .5 for line in below)
                if below[:, inset:-inset].sum() > 2 and not joined_border:
                    continue
                found.append((centre, bottom, tile_size, tile_size))
                break
    return found


def three_peak_boxes(mask, recipe_rows, *, tile_layout=None, tile_size=38):
    """Return crown tile anchors from three distinct, regularly spaced peaks.

    The caller supplies a gold-colour mask from a panel normalized to 575
    pixels wide and OCR recipe-row bounds. Search only above those recipe
    rows, where crown marks occur, never inside glyph artwork or text. An
    optional gray-tile lattice constrains the marked rune position without
    requiring a gold tile frame. Recipe and cursor validation remain the
    caller's responsibility.
    """
    pixels = np.asarray(mask, dtype=bool)
    if pixels.ndim != 2 or min(pixels.shape) < 8 or not recipe_rows:
        return []
    crown_tops = [float(row["y1"]) - tile_size * 41 / 38 for row in recipe_rows]
    candidates = []
    for bottom in range(2, pixels.shape[0]):
        if min(abs(bottom - top) for top in crown_tops) > 5:
            continue
        band = pixels[max(0, bottom - 6):bottom]
        columns = band.sum(axis=0)
        active = np.flatnonzero(columns)
        if active.size < 6:
            continue
        # Resampling can leave a one-pixel hole inside a peak. Crown peaks
        # are separated by at least three empty columns, so only join these
        # tiny internal holes, preserving the three distinct components.
        runs = [np.arange(run[0], run[-1] + 1)
                for run in np.split(active, np.flatnonzero(np.diff(active) > 3) + 1)]
        peaks = []
        for run in runs:
            area = int(columns[run].sum())
            if 2 <= len(run) <= 8 and area >= 2:
                centre = float(np.average(run, weights=columns[run]))
                # Crown peaks must share their lower baseline. Scattered
                # gold specks across the six-pixel band are not three marks.
                end_rows = np.flatnonzero(band[:, run].any(axis=1))
                peaks.append((centre, area, int(end_rows[-1]), int(run[0]), int(run[-1])))
        for first, middle, last in zip(peaks, peaks[1:], peaks[2:]):
            gap_a, gap_b = middle[0] - first[0], last[0] - middle[0]
            if (not 6.5 <= gap_a <= 13 or not 6.5 <= gap_b <= 13
                    or max(gap_a, gap_b) > min(gap_a, gap_b) * 1.5
                    or max(first[2], middle[2], last[2]) - min(first[2], middle[2], last[2]) > 2
                    or min(middle[3] - first[4], last[3] - middle[4]) < 3):
                continue
            centre = (first[0] + last[0]) / 2
            if not 15 <= centre <= min(470, pixels.shape[1] - 15):
                continue
            if tile_layout is not None:
                first_x, pitch = tile_layout
                index = round((centre - first_x) / pitch)
                if index < 0 or abs(centre - first_x - index * pitch) > 2.5:
                    continue
                row_index = min(range(len(crown_tops)), key=lambda at: abs(bottom - crown_tops[at]))
                sockets = recipe_rows[row_index].get("sockets")
                if sockets is not None and index >= sockets:
                    continue
            # No fourth gold peak or horizontal highlight can occupy this
            # crown's footprint. The three components must be isolated.
            left, right = max(0, round(centre - tile_size / 2)), min(pixels.shape[1], round(centre + tile_size / 2))
            expected = np.zeros(right - left, dtype=bool)
            for peak in (first, middle, last):
                expected[max(0, peak[3] - left):min(right - left, peak[4] - left + 1)] = True
            if np.any((columns[left:right] > 0) & ~expected):
                continue
            below = pixels[bottom:bottom + 2, left:right]
            # A crown terminates in open space, or joins the tile's optional
            # horizontal highlight. Three growing strokes inside a rune
            # drawing must not be mistaken for crown peaks.
            joined_border = False
            for line in below:
                active_below = np.flatnonzero(line)
                if active_below.size:
                    lengths = np.diff(np.r_[-1, np.flatnonzero(np.diff(active_below) > 1), len(active_below) - 1])
                    joined_border |= bool(lengths.max(initial=0) >= tile_size * .5)
            inset = max(2, round(tile_size * .18))
            interior_below = below[:, inset:-inset]
            if interior_below.sum() > 2 and not joined_border:
                continue
            if any(abs(centre - box[0]) < 6 and abs(bottom - box[1]) < 8 for box in candidates):
                continue
            candidates.append((centre, bottom, tile_size, tile_size))
    return candidates
