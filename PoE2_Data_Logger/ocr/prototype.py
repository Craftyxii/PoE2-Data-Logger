"""Locate book anchors and normalize pre-open bars to fixed socket geometry."""

from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter
from scipy.signal import fftconvolve

SPACING = 57
BOOK_TO_LAST = 47
BOOK_TO_CENTER_Y = 20


def ncc_find_all(
    im: Image.Image,
    template: Image.Image,
    gray: np.ndarray | None = None,
    threshold: float = 0.65,
    limit: int = 24,
) -> list[tuple[int, int, float]]:
    """Find up to limit grayscale correlation peaks, suppressing nearby duplicate anchors."""
    a = gray if gray is not None else np.asarray(im.convert("L"), dtype=np.float32)
    t = np.asarray(template.convert("L"), dtype=np.float32)
    h, w = t.shape
    if a.shape[0] < h or a.shape[1] < w:
        return []
    zero = t - t.mean()
    numerator = fftconvolve(a, zero[::-1, ::-1], mode="valid")
    means = uniform_filter(a, size=(h, w), mode="constant")
    squares = uniform_filter(a * a, size=(h, w), mode="constant")
    sums = means[
        h // 2 : h // 2 + numerator.shape[0], w // 2 : w // 2 + numerator.shape[1]
    ]
    vars_ = (
        squares[
            h // 2 : h // 2 + numerator.shape[0], w // 2 : w // 2 + numerator.shape[1]
        ]
        - sums * sums
    )
    score = numerator / (h * w * np.sqrt(np.maximum(vars_, 1)) * zero.std())
    peaks = []
    for _ in range(limit):
        y, x = np.unravel_index(np.argmax(score), score.shape)
        confidence = float(score[y, x])
        if confidence < threshold:
            break
        peaks.append((int(x), int(y), confidence))
        score[max(0, y - h) : y + h + 1, max(0, x - w) : x + w + 1] = -1
    return peaks


def find_books(im: Image.Image, templates, threshold=0.65, limit=24):
    """Locate book anchors and their UI scale, including tightly cropped bars."""
    import cv2

    gray = np.asarray(im.convert("L"), dtype=np.float32)
    found = []
    for scale in [round(value, 2) for value in np.arange(.5, 1.51, .05)] + [1.75, 2.0]:
        for template in templates:
            width, height = round(template.width * scale), round(template.height * scale)
            if width > im.width or height > im.height:
                continue
            resized = template.resize((width, height), Image.Resampling.LANCZOS)
            values = np.asarray(resized.convert("L"), dtype=np.float32)
            scores = cv2.matchTemplate(gray, values, cv2.TM_CCOEFF_NORMED)
            for _ in range(limit):
                y, x = np.unravel_index(np.argmax(scores), scores.shape)
                confidence = float(scores[y, x])
                if confidence < threshold:
                    break
                found.append((int(x), int(y), confidence, scale))
                scores[max(0, y - height):y + height + 1,
                       max(0, x - width):x + width + 1] = -1
    unique = []
    for peak in sorted(found, key=lambda item: item[2], reverse=True):
        if not any(abs(peak[0] - other[0]) < 26 * max(peak[3], other[3]) and
                   abs(peak[1] - other[1]) < 38 * max(peak[3], other[3]) for other in unique):
            unique.append(peak)
            if len(unique) == limit:
                break
    return unique


def normalize_book(im: Image.Image, book):
    """Return model-size pixels and anchor; book coordinates remain capture-based."""
    x, y, confidence, scale = book
    left, top = max(0, round(x - 640 * scale)), max(0, round(y - 25 * scale))
    right, bottom = min(im.width, round(x + 40 * scale)), min(im.height, round(y + 90 * scale))
    image = im.crop((left, top, right, bottom))
    if scale != 1:
        image = image.resize((round(image.width / scale), round(image.height / scale)), Image.Resampling.LANCZOS)
    image.info["seed_origin"] = (left, top)
    return image, (round((x - left) / scale), round((y - top) / scale), confidence)


def ncc_find(
    im: Image.Image, template: Image.Image, gray: np.ndarray | None = None
) -> tuple[int, int, float]:
    """Return the strongest correlation anchor, or a negative-score sentinel if none fits."""
    peaks = ncc_find_all(im, template, gray, threshold=-1, limit=1)
    return peaks[0] if peaks else (0, 0, -1.0)


def center_for(book_x: int, book_y: int, sockets: int, slot: int) -> tuple[int, int]:
    """Map a one-based left-to-right slot using 57-pixel spacing left of the book anchor."""
    return book_x - BOOK_TO_LAST - (sockets - slot) * SPACING, book_y + BOOK_TO_CENTER_Y


def crop_at(im: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image | None:
    """Return a centered crop only when its entire rectangle lies inside the image."""
    box = (x - w // 2, y - h // 2, x - w // 2 + w, y - h // 2 + h)
    if box[0] < 0 or box[1] < 0 or box[2] > im.width or box[3] > im.height:
        return None
    return im.crop(box)
