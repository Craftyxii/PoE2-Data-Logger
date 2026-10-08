"""Convert a 36×36 rune crop into a centered unit vector for gallery similarity."""

import numpy as np
from scipy.ndimage import gaussian_filter


def vector(patch, kind):
    """Mask a 36×36 crop to a radius-15 disk, center it and normalize its magnitude.

    Optional stroke or brightness preprocessing changes the grayscale signal
    before normalization for dot-product comparison with gallery vectors.
    """
    a = np.asarray(patch.convert("RGB"), dtype=np.float32)
    gray = a.mean(2)
    if kind == "stroke":
        gray = np.maximum(gray - gaussian_filter(gray, 4), 0)
    elif kind == "bright":
        gray = np.maximum(gray - 45, 0)
    yy, xx = np.mgrid[-18:18, -18:18]
    gray[np.sqrt(xx * xx + yy * yy) > 15] = 0
    v = gray.reshape(-1)
    v -= v.mean()
    return v / max(np.linalg.norm(v), 1)
