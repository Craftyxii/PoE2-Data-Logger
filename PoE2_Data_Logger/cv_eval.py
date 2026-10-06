from __future__ import annotations

import numpy as np
from scipy.ndimage import sobel


def features(image, cx, cy):
    if cx < 29 or cx >= image.width - 29 or cy < 29 or cy >= image.height - 29:
        return None
    patch = np.asarray(
        image.crop((cx - 28, cy - 28, cx + 29, cy + 29)).convert("RGB"),
        dtype=np.float32,
    )
    gray = patch.mean(2)
    yy, xx = np.mgrid[-28:29, -28:29]
    rr = np.sqrt(xx * xx + yy * yy)
    edge = np.hypot(sobel(gray, axis=0), sobel(gray, axis=1))
    vec = []
    for lo, hi in [(0, 8), (8, 13), (13, 16), (16, 19), (19, 22), (22, 25), (25, 28)]:
        mask = (rr >= lo) & (rr < hi)
        for arr in [
            gray,
            edge,
            patch[:, :, 0] - patch[:, :, 1],
            patch[:, :, 2] - patch[:, :, 1],
        ]:
            vec.extend((float(arr[mask].mean()), float(arr[mask].std())))
    for y0, y1 in [(0, 8), (8, 18), (18, 28), (28, 38), (38, 48), (48, 57)]:
        vec.extend((float(gray[y0:y1].mean()), float(gray[y0:y1].std())))
    return np.array(vec, dtype=np.float32)


def decode(p, js):
    by_index = {int(j): v for j, v in zip(js, p)}
    scores = []
    for n in range(3, 11):
        if any(j not in by_index for j in range(n)):
            continue
        candidates = []
        for rune_j in range(n):
            score = 0.0
            for j, v in by_index.items():
                want = 2 if j == rune_j else (1 if j < n else 0)
                score += float(np.log(max(v[want], 1e-6)))
            candidates.append((score, rune_j))
        scores.append((*max(candidates), n))
    return max(scores) if scores else None
