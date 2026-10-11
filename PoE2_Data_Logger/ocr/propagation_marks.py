"""Read the three gold crown peaks without requiring a rune tile frame."""

from __future__ import annotations

import numpy as np


def supported_peak_boxes(masks, recipe_rows, *, tile_layout, tile_size=38):
    """Retain native crowns or crowns verified at two exposure samples."""
    return _supported_boxes(masks, recipe_rows, tile_layout, tile_size, three_peak_boxes)


def supported_partial_peak_boxes(masks, recipe_rows, *, tile_layout, tile_size=38):
    """Keep supported damaged crown evidence so a paired rune cannot be omitted."""
    return _supported_boxes(masks, recipe_rows, tile_layout, tile_size, _partial_peak_boxes)


def supported_fragment_peak_boxes(masks, recipe_rows, *, tile_layout, tile_size=38):
    """Retain supported one/two-peak fragments suitable for manual-review consensus.

    Fragments require isolated, vertically supported peaks at expected crown
    offsets. Extra peaks, lines and growing glyph strokes cannot vote for a slot.
    Exposure samples corroborate a fragment but never count as independent rows.
    """
    return _supported_boxes(masks, recipe_rows, tile_layout, tile_size, _fragment_peak_boxes)


def _fragment_peak_boxes(mask, recipe_rows, *, tile_layout, tile_size):
    """Filter damaged marks to incomplete crown shapes with scale-adjusted vertical support."""
    pixels = np.asarray(mask, dtype=bool)
    found = []
    band_height = max(4, round(6 * tile_size / 38))
    join_gap = max(2, int(3 * tile_size / 38))
    for box in _partial_peak_boxes(mask, recipe_rows, tile_layout=tile_layout, tile_size=tile_size):
        centre, bottom = box[:2]
        left, right = max(0, round(centre - tile_size / 2)), min(pixels.shape[1], round(centre + tile_size / 2))
        band = pixels[max(0, bottom - band_height):bottom, left:right]
        columns = band.sum(axis=0)
        active = np.flatnonzero(columns)
        runs = [run for run in np.split(active, np.flatnonzero(np.diff(active) > join_gap) + 1) if run.size]
        if not 1 <= len(runs) <= 2:
            continue
        offsets, baselines, widens = [], [], []
        expected = (centre - tile_size / 4, centre, centre + tile_size / 4)
        valid = True
        for run in runs:
            counts = band[:, run].sum(axis=1)
            support = np.flatnonzero(counts)
            if not 2 <= len(run) <= 8 or len(support) < max(2, round(3 * tile_size / 38)):
                valid = False
                break
            at = left + float(np.average(run, weights=columns[run]))
            offset = min(range(3), key=lambda index: abs(at - expected[index]))
            valid &= abs(at - expected[offset]) <= 2.5
            continuation = pixels[bottom + 2:bottom + 2 + max(4, round(tile_size * .28)),
                                  max(0, round(at) - 2):round(at) + 3]
            # A real crown ends at its frame. Tapered gold glyph strokes can
            # look like a tip above that frame but continue down into the tile.
            if continuation.size and (continuation.sum(axis=1) >= 2).mean() >= .7:
                valid = False
            offsets.append(offset)
            baselines.append(int(support[-1]))
            widens.append(counts[support[0]] < counts.max())
        if (valid and len(set(offsets)) == len(offsets) and any(widens)
                and max(baselines) - min(baselines) <= 2):
            found.append(box)
    return found


def _supported_boxes(masks, recipe_rows, tile_layout, tile_size, finder):
    """Merge nearby boxes across exposures, retaining native or multiply supported detections."""
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
    """Find plausible crown fragments at known tile positions to flag damaged marks.

    Search near the expected crown baseline and reject growing glyph strokes;
    extra or missing peaks preserve uncertainty rather than proving a crown.
    Scale-adjusted vertical support rejects exposure-amplified frame speckles.
    Complete crowns are removed from these boxes by the caller.
    """
    pixels = np.asarray(mask, dtype=bool)
    first_x, pitch = tile_layout
    band_height = max(4, round(6 * tile_size / 38))
    join_gap = max(2, int(3 * tile_size / 38))
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
                band = pixels[max(0, bottom - band_height):bottom, left:right]
                columns = band.sum(axis=0)
                active = np.flatnonzero(columns)
                if not active.size:
                    continue
                runs = [np.arange(run[0], run[-1] + 1) for run in np.split(
                    active, np.flatnonzero(np.diff(active) > join_gap) + 1)]
                # Bright frame corners can leave a peak-sized cluster beside
                # a broad horizontal border. That band is frame clutter;
                # malformed crowns still have separate, narrow components.
                if any(len(run) >= round(tile_size * .5) for run in runs):
                    continue
                offsets, widens = [], []
                for run in runs:
                    area = int(columns[run].sum())
                    if (not 2 <= len(run) <= 8 or area < 2
                            or band[:, run].any(axis=1).sum() < max(2, round(3 * tile_size / 38))):
                        continue
                    at = left + float(np.average(run, weights=columns[run]))
                    offset = min(range(3), key=lambda position: abs(at - expected[position]))
                    if abs(at - expected[offset]) > 2.5:
                        continue
                    offsets.append(offset)
                    counts = band[:, run].sum(axis=1)
                    support = np.flatnonzero(counts)
                    widens.append(counts[support[0]] < counts.max())
                if not offsets or len(set(offsets)) != len(offsets) or not any(widens):
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
    caller's responsibility. At least two peaks must have vertical support
    and one must widen below its tip; resampling may flatten another peak.
    Band height and peak spacing scale with the observed tile size.
    """
    pixels = np.asarray(mask, dtype=bool)
    if pixels.ndim != 2 or min(pixels.shape) < 8 or not recipe_rows:
        return []
    crown_tops = [float(row["y1"]) - tile_size * 41 / 38 for row in recipe_rows]
    minimum_gap, maximum_gap = 6.5 * tile_size / 38, 13 * tile_size / 38
    band_height = max(4, round(6 * tile_size / 38))
    join_gap = max(2, int(3 * tile_size / 38))
    candidates = []
    for bottom in range(2, pixels.shape[0]):
        if min(abs(bottom - top) for top in crown_tops) > 5:
            continue
        band = pixels[max(0, bottom - band_height):bottom]
        columns = band.sum(axis=0)
        active = np.flatnonzero(columns)
        if active.size < 6:
            continue
        # Resampling can leave a one-pixel hole inside a peak. Crown peaks
        # remain separate even when a smaller tile leaves only two empty
        # columns between them.
        runs = [np.arange(run[0], run[-1] + 1)
                for run in np.split(active, np.flatnonzero(np.diff(active) > join_gap) + 1)]
        peaks = []
        for run in runs:
            area = int(columns[run].sum())
            if 2 <= len(run) <= 8 and area >= 2:
                counts = band[:, run].sum(axis=1)
                end_rows = np.flatnonzero(counts)
                centre = float(np.average(run, weights=columns[run]))
                # Crown peaks must share their lower baseline. Scattered
                # gold specks and single-line glints are not three marks.
                widens = counts[end_rows[0]] < counts.max()
                peaks.append((centre, area, int(end_rows[-1]), int(run[0]), int(run[-1]),
                              end_rows.size, widens))
        for first, middle, last in zip(peaks, peaks[1:], peaks[2:]):
            gap_a, gap_b = middle[0] - first[0], last[0] - middle[0]
            if (not minimum_gap <= gap_a <= maximum_gap or not minimum_gap <= gap_b <= maximum_gap
                    or max(gap_a, gap_b) > min(gap_a, gap_b) * 1.5
                    or max(first[2], middle[2], last[2]) - min(first[2], middle[2], last[2]) > 2
                    or min(middle[3] - first[4], last[3] - middle[4]) < 3
                    or sum(peak[5] >= 2 for peak in (first, middle, last)) < 2
                    or not any(peak[6] for peak in (first, middle, last))):
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
