"""The currency-overlay icon matcher (third_party/currency_overlay/currency-reader.js) in numpy.

The same arithmetic in the same order as the JavaScript: integer pixel sums, float32
luminance, and each weighted sum added term by term (cumulative sums), so every score
is the same number. All icons are scored together: a pixel the JavaScript skips adds an
exact zero here, which leaves every sum unchanged. tests/test_icon_reader_port.py
compares both on real captures.
"""

import math

import numpy as np

# Mirrors currency-reader.js.
MIN_SCORE = 0.35
MIN_MARGIN = 0.06
MIN_RATIO = 1.15
SOLID = 0.5


def _round(value):
    """JavaScript Math.round: halves go up."""
    return math.floor(value + .5)


def _trim(rgba, width, height):
    """Find the icon's bounding box by brightness above the border's median, or None."""
    pixels = rgba.reshape(height, width, 4).astype(np.float64)
    lum = (0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1] + 0.114 * pixels[:, :, 2]).astype(np.float32)
    flat = lum.reshape(-1)
    edge = np.concatenate([np.stack([lum[0], lum[height - 1]], axis=1).reshape(-1),
                           np.stack([lum[:, 0], lum[:, width - 1]], axis=1).reshape(-1)])
    background = float(np.sort(edge)[len(edge) >> 1])
    peak = max(0.0, float(flat.max()))
    cut = background + max(10, (peak - background) * 0.22)
    rows, columns = np.nonzero(lum.astype(np.float64) >= cut)
    if len(rows) < 12:
        return None
    x0, x1, y0, y1 = int(columns.min()), int(columns.max()), int(rows.min()), int(rows.max())
    if (x1 - x0 + 1) < width * 0.15 or (y1 - y0 + 1) < height * 0.15:
        return None
    return {"x": x0, "y": y0, "w": x1 - x0 + 1, "h": y1 - y0 + 1}


def _crops(rgba, width, height):
    """Candidate framings, best guess first (sliding squares along a strip)."""
    out = []
    if width > height * 1.6:
        for factor in (1.0, 0.85, 0.7):
            size = max(6, _round(height * factor))
            if size > width:
                continue
            step = max(1, _round(size * 0.18))
            y = max(0, _round((height - size) / 2))
            out.extend({"x": x, "y": y, "w": size, "h": size} for x in range(0, width - size + 1, step))
        return out
    trimmed = _trim(rgba, width, height)
    if trimmed:
        out.append(trimmed)
        for slack in (1, 2):
            x, y = max(0, trimmed["x"] - slack), max(0, trimmed["y"] - slack)
            out.append({"x": x, "y": y, "w": min(width - x, trimmed["w"] + slack * 2),
                        "h": min(height - y, trimmed["h"] + slack * 2)})
    for inset in (0, 0.12, 0.24):
        dx, dy = _round(width * inset), _round(height * inset)
        crop_width, crop_height = width - dx * 2, height - dy * 2
        if crop_width < 6 or crop_height < 6:
            continue
        out.append({"x": dx, "y": dy, "w": crop_width, "h": crop_height})
    return out


def _signature(integral, width, height, box, size):
    """Area-average a box into a size x size RGB grid (exact integer sums, then one division)."""
    cells = np.arange(size)

    def edges(start, length, limit):
        low = start + cells * length // size
        high = start + np.maximum((cells + 1) * length // size, cells * length // size + 1)
        return np.minimum(low, limit), np.minimum(high, limit)

    y0, y1 = edges(box["y"], box["h"], height)
    x0, x1 = edges(box["x"], box["w"], width)
    y0, y1 = y0[:, None], y1[:, None]
    totals = (integral[y1, x1[None, :]] - integral[y0, x1[None, :]] -
              integral[y1, x0[None, :]] + integral[y0, x0[None, :]])
    count = np.maximum(y1 - y0, 0) * np.maximum(x1 - x0, 0)[None, :]
    rgb = np.zeros((size, size, 3), dtype=np.float64)
    filled = count > 0
    rgb[filled] = totals[filled] / count[filled][:, None]
    return rgb.reshape(-1)


def _sequential(values):
    """Add each row's terms in order, as a JavaScript loop does."""
    return np.cumsum(values, axis=-1)[..., -1] if values.shape[-1] else np.zeros(values.shape[:-1])


class _Bank:
    """The parsed currency-icons.json as arrays: solid-pixel weights and reference colours."""

    def __init__(self, bank):
        self.size = bank.get("n") or 24
        self.icons = bank["icons"]
        coverage = np.asarray([icon["cov"] for icon in self.icons], dtype=np.float64) / 255
        # The JavaScript compares only solid pixels, weighted by coverage squared.
        self.weights = np.where(coverage < SOLID, 0.0, coverage * coverage)
        self.references = np.asarray([icon["rgb"] for icon in self.icons], dtype=np.float64).reshape(
            len(self.icons), -1, 3).transpose(0, 2, 1)
        self.totals = _sequential(self.weights)
        self.solid = (coverage >= SOLID).any(axis=1)
        means = _sequential(self.weights[:, None, :] * self.references) / self.totals[:, None]
        self.centred = self.references - means[:, :, None]
        self.spread = _sequential(self.weights[:, None, :] * self.centred * self.centred)

    def scores(self, signature):
        """Every icon's coverage-weighted, zero-mean normalised cross-correlation."""
        shot = signature.reshape(-1, 3).T[None, :, :]
        with np.errstate(invalid="ignore", divide="ignore"):
            means = _sequential(self.weights[:, None, :] * shot) / self.totals[:, None]
            centred = shot - means[:, :, None]
            num = _sequential(self.weights[:, None, :] * centred * self.centred)
            spread = _sequential(self.weights[:, None, :] * centred * centred)
            channels = np.where((spread > 0) & (self.spread > 0),
                                num / np.sqrt(spread * self.spread), 0.0)
        scores = ((channels[:, 0] + channels[:, 1]) + channels[:, 2]) / 3
        return np.where(self.solid & (self.totals > 0), scores, 0.0)


_BANKS = {}


def identify(rgba, width, height, bank):
    """Rank icon families over the candidate framings, as CurrencyReader.identify does."""
    none = {"family": None, "members": [], "score": 0, "margin": 0, "all": []}
    if not width or not height or not bank or not bank.get("icons"):
        return none
    prepared = _BANKS.get(id(bank))
    if prepared is None or prepared[0] is not bank:
        prepared = (bank, _Bank(bank))
        _BANKS.clear()
        _BANKS[id(bank)] = prepared
    prepared = prepared[1]
    rgba = np.asarray(rgba, dtype=np.uint8).reshape(-1)
    pixels = rgba.reshape(height, width, 4)[:, :, :3].astype(np.int64)
    integral = np.zeros((height + 1, width + 1, 3), dtype=np.int64)
    integral[1:, 1:] = pixels.cumsum(axis=0).cumsum(axis=1)
    best = {}
    for box in _crops(rgba, width, height):
        signature = _signature(integral, width, height, box, prepared.size)
        for icon, score in zip(prepared.icons, prepared.scores(signature).tolist()):
            previous = best.get(icon["family"])
            if previous is None or score > previous[1]:
                best[icon["family"]] = (icon, score)
    ranked = sorted(best.values(), key=lambda entry: -entry[1])
    if not ranked:
        return none
    top, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    margin = top[1] - second[1] if second else 1
    shown = [{"family": icon["family"], "name": icon["members"][0], "score": score} for icon, score in ranked[:5]]
    clear = not second or (margin >= MIN_MARGIN and top[1] >= second[1] * MIN_RATIO)
    if top[1] < MIN_SCORE or not clear:
        return {"family": None, "members": [], "score": top[1], "margin": margin, "all": shown}
    # A fresh members list, as the JavaScript's JSON reply gives.
    return {"family": top[0]["family"], "members": list(top[0]["members"]), "score": top[1], "margin": margin,
            "all": shown}
