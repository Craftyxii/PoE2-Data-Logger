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
    score[:35, :] = -1
    score[:, :180] = -1
    peaks = []
    for _ in range(limit):
        y, x = np.unravel_index(np.argmax(score), score.shape)
        confidence = float(score[y, x])
        if confidence < threshold:
            break
        peaks.append((int(x), int(y), confidence))
        score[max(0, y - h) : y + h + 1, max(0, x - w) : x + w + 1] = -1
    return peaks


def ncc_find(
    im: Image.Image, template: Image.Image, gray: np.ndarray | None = None
) -> tuple[int, int, float]:
    peaks = ncc_find_all(im, template, gray, threshold=-1, limit=1)
    return peaks[0] if peaks else (0, 0, -1.0)


def center_for(book_x: int, book_y: int, sockets: int, slot: int) -> tuple[int, int]:
    return book_x - BOOK_TO_LAST - (sockets - slot) * SPACING, book_y + BOOK_TO_CENTER_Y


def crop_at(im: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image | None:
    box = (x - w // 2, y - h // 2, x - w // 2 + w, y - h // 2 + h)
    if box[0] < 0 or box[1] < 0 or box[2] > im.width or box[3] > im.height:
        return None
    return im.crop(box)
