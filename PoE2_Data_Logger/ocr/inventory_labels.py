from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps


def count_crops(cell):
    pixels = np.asarray(cell.convert('RGB'), dtype=np.uint8)
    height = max(11, round(cell.height * .36))
    strip = pixels[:height, :max(14, round(cell.width * .85))]
    lo = strip.min(axis=2); hi = strip.max(axis=2)
    groups = []
    for floor in (145, 195):
        ink = ((lo > floor) & (hi.astype(np.int16) - lo < 55)).astype(np.uint8)
        count, components, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        parts = []
        for label, (x, y, w, h, area) in enumerate(stats[1:count], 1):
            if (h >= max(6, round(cell.height * .12)) and h <= round(cell.height * .31) and
                    y <= max(6, round(cell.height * .16)) and area >= 7 and w <= cell.width * .26):
                parts.append((int(x), int(y), int(w), int(h), label))
        parts.sort()
        if not parts or parts[0][0] > max(7, round(cell.width * .16)):
            continue
        group = [parts[0]]
        for part in parts[1:]:
            previous = group[-1]
            if (part[0] - (previous[0] + previous[2]) > max(4, round(cell.width * .09)) or
                    abs(part[1] - group[0][1]) > 2 or abs(part[3] - group[0][3]) > 2):
                break
            group.append(part)
        x0 = max(0, min(p[0] for p in group) - 1)
        y0 = max(0, min(p[1] for p in group) - 1)
        x1 = min(strip.shape[1], max(p[0] + p[2] for p in group) + 1)
        y1 = min(strip.shape[0], max(p[1] + p[3] for p in group) + 1)
        raw = Image.fromarray(strip[y0:y1, x0:x1]).convert('RGB')
        selected = np.isin(components[y0:y1, x0:x1], [part[4] for part in group])
        clean = Image.fromarray(np.where(selected, lo[y0:y1, x0:x1], 0).astype(np.uint8)).convert('RGB')
        groups.append((len(group), (raw, clean)))
    patches = []
    for size, variants in groups:
        for patch in variants:
            patch = ImageOps.expand(patch, border=10, fill='black')
            patches.append(patch.resize((patch.width * 2, patch.height * 2), Image.Resampling.BICUBIC))
    anchor = strip[:max(8, round(cell.height * .28)), :max(7, round(cell.width * .14))]
    anchor_ink = ((anchor.min(axis=2) > 145) &
                  (anchor.max(axis=2).astype(np.int16) - anchor.min(axis=2) < 55)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(anchor_ink, 8)
    possible = bool(groups) or any(h >= max(3, round(cell.height * .09)) and area >= 5
                                  for x, y, w, h, area in stats[1:count])
    return patches, possible


def fallback_crops(cell):
    patches = []
    for width, height, floor, saturation in ((.46, .28, 145, 55), (.46, .32, 145, 55),
                                            (.40, .32, 145, 55), (.46, .32, 210, 25),
                                            (.46, .28, 210, 25)):
        pixels = np.asarray(cell.crop((0, 0, round(cell.width * width),
                                      round(cell.height * height))), dtype=np.int16)
        value = pixels.min(axis=2)
        ink = (value > floor) & (pixels.max(axis=2) - value < saturation)
        if ink.any():
            patch = Image.fromarray(np.where(ink, value, 0).astype(np.uint8)).convert('RGB')
            patches.append(('mask', ImageOps.expand(patch, border=2, fill='black')))
    for height in (.22, .25):
        patch = cell.crop((0, 0, round(cell.width * .46), round(cell.height * height))).convert('L')
        for image in (patch, ImageOps.autocontrast(patch)):
            patches.append(('raw', ImageOps.expand(image.convert('RGB'), border=2, fill='black')))
    return patches


def tier_crops(cell):
    pixels = np.asarray(cell.convert('RGB'), dtype=np.uint8)
    x0, y0 = round(cell.width * .5), round(cell.height * .62)
    strip = pixels[y0:, x0:]
    lo = strip.min(axis=2); hi = strip.max(axis=2)
    ink = ((lo > 165) & (hi.astype(np.int16) - lo < 45)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    strokes = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:count]
               if 1 <= w <= max(5, round(cell.width * .1)) and
               max(6, round(cell.height * .12)) <= h <= round(cell.height * .28) and
               y + h >= strip.shape[0] // 2 and area >= 6]
    if len(strokes) < 2:
        return [], False
    strokes.sort()
    clusters = []
    for part in strokes:
        if not clusters or part[0] - (clusters[-1][-1][0] + clusters[-1][-1][2]) > 4:
            clusters.append([part])
        else:
            clusters[-1].append(part)
    group = next((g for g in reversed(clusters) if 2 <= len(g) <= 3 and
                  max(p[1] for p in g) - min(p[1] for p in g) <= 2 and
                  max(p[3] for p in g) - min(p[3] for p in g) <= 2), None)
    if group is None:
        return [], False
    left, top = min(p[0] for p in group), min(p[1] for p in group)
    right, bottom = max(p[0] + p[2] for p in group), max(p[1] + p[3] for p in group)
    raw = Image.fromarray(strip[max(0,top-1):bottom+1,max(0,left-1):right+1]).convert('RGB')
    clean = Image.fromarray(ink[max(0,top-1):bottom+1,max(0,left-1):right+1] * 255).convert('RGB')
    return [ImageOps.expand(p, border=4, fill='black') for p in (raw, clean)], True
