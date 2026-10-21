"""Match sourced unique artwork within an already established item footprint.

The bundled RePoE manifest supplies names, dimensions and original GGG artwork.
This module performs no network requests, OCR, logging or approval. An artwork
match is a suggestion until the caller applies its normal review rules.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import threading

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import ocr_sensitivity


ASSET_ROOT = Path(__file__).resolve().parents[1] / "unique_item_assets"
# Names, source dimensions and shared-art alternatives are one reviewed unit.
# A future catalog refresh must deliberately update both pins after validation.
_REVISION = "75885a099f2e9be1619aa3c793fe6ab0754a511f"
_MANIFEST_SHA256 = "4e71e5f8b74218dfcceab69592ffd7cceb3081fc4665b5d1bc7c7219f6de8a7e"
_UNIT = 32
_BANK_BYTES = 32 * 1024 * 1024
_QUERY_LIMIT = 128
_LOCK = threading.RLock()
_CATALOG = None
_BANKS = OrderedDict()
_QUERIES = OrderedDict()
_RETAINED_BYTES = 0


@dataclass(frozen=True)
class _Catalog:
    root: Path
    signature: tuple
    revision: str
    items: tuple


@dataclass(frozen=True)
class _Bank:
    signature: tuple
    revision: str
    shape: tuple
    names: tuple
    rgb: np.ndarray
    weights: np.ndarray
    centered: np.ndarray
    support: np.ndarray
    weight_sums: np.ndarray
    byte_size: int


def clear_caches():
    """Discard only prepared catalog/query evidence, never user data or approvals."""
    global _CATALOG, _RETAINED_BYTES
    with _LOCK:
        _CATALOG = None
        _BANKS.clear()
        _QUERIES.clear()
        _RETAINED_BYTES = 0


def _label(value):
    return (isinstance(value, str) and 0 < len(value) <= 160 and
            not any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF for ch in value))


def _catalog():
    global _CATALOG
    root = Path(ASSET_ROOT).resolve()
    path = root / "manifest.json"
    stat = path.stat()
    signature = (str(root), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if _CATALOG is not None and _CATALOG.signature[:3] == signature:
            return _CATALOG
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) > 8 * 1024 * 1024 or digest != _MANIFEST_SHA256:
            raise ValueError("Unique catalog integrity check failed.")
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("items"), list) or
                not 1 <= len(data["items"]) <= 2000 or
                not isinstance(data.get("source"), dict) or
                not isinstance(data["source"].get("revision"), str) or
                data["source"]["revision"] != _REVISION):
            raise ValueError("Unsupported unique catalog.")
        ids = set()
        items = []
        for row in data["items"]:
            if (not isinstance(row, dict) or not _label(row.get("name")) or
                    not isinstance(row.get("id"), str) or row["id"] in ids or
                    type(row.get("columns")) is not int or not 1 <= row["columns"] <= 2 or
                    type(row.get("rows")) is not int or not 1 <= row["rows"] <= 4 or
                    not isinstance(row.get("image_sha256"), str) or
                    not re.fullmatch(r"[a-f0-9]{64}", row["image_sha256"]) or
                    row.get("icon") != "icons/" + row["image_sha256"] + ".png" or
                    not isinstance(row.get("shared_names", []), list) or
                    len(row.get("shared_names", [])) > 128 or
                    any(not _label(name) for name in row.get("shared_names", []))):
                raise ValueError("Invalid unique catalog entry.")
            ids.add(row["id"])
            items.append(dict(row))
        _CATALOG = _Catalog(root, signature + (digest,),
                            data["source"]["revision"], tuple(items))
        return _CATALOG


def names():
    """Return canonical unique labels without registering them in a user database."""
    try:
        return tuple(sorted({row["name"] for row in _catalog().items}, key=str.casefold))
    except (OSError, ValueError, TypeError):
        return ()


def is_unique_name(name):
    """Recognize sourced unique labels; ordinary shared-base alternatives stay Items too."""
    if not isinstance(name, str):
        return False
    return name.casefold() in {value.casefold() for value in names()}


def _bank(shape):
    global _RETAINED_BYTES
    catalog = _catalog()
    groups = OrderedDict()
    for row in catalog.items:
        if (row["columns"], row["rows"]) != shape:
            continue
        group = groups.setdefault(row["image_sha256"], {"row": row, "names": set()})
        group["names"].add(row["name"])
        group["names"].update(row.get("shared_names", []))
    if not groups:
        return None
    # Runtime assets are immutable. Stat identity also invalidates a prepared
    # bank after an explicit local replacement without rereading every PNG.
    versions = []
    for digest, group in groups.items():
        stat = (catalog.root / group["row"]["icon"]).stat()
        versions.append((digest, stat.st_mtime_ns, stat.st_size))
    key = (catalog.signature, shape, tuple(versions))
    with _LOCK:
        if key in _BANKS:
            _BANKS.move_to_end(key)
            return _BANKS[key]
        height, width = shape[1] * _UNIT, shape[0] * _UNIT
        pixels = height * width
        # RGB bytes, three float/byte planes and scalar metadata are bounded
        # before any potentially large bank allocation or decoding.
        if len(groups) * pixels * 12 + len(groups) * 512 > _BANK_BYTES:
            return None
        rgbs, weights, centered, supports, sums, labels = [], [], [], [], [], []
        for digest, group in groups.items():
            raw = (catalog.root / group["row"]["icon"]).read_bytes()
            if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
                # A missing rival must not turn a previously ambiguous image
                # into a confident name; reject this entire footprint bank.
                raise ValueError("Unique artwork integrity check failed.")
            with Image.open(io.BytesIO(raw)) as image:
                if (image.mode != "RGBA" or min(image.size) < 1 or max(image.size) > 1024 or
                        image.width * image.height > 1_000_000):
                    raise ValueError("Unsupported unique artwork.")
                # Source sprites can occupy less than the metadata footprint
                # (for example a 2x3 sprite assigned to a 2x4 shield). Retain
                # their aspect and alpha rather than stretching into cells.
                scale = min(width / image.width, height / image.height)
                sizes = {(max(1, round(image.width * scale)),
                          max(1, round(image.height * scale))),
                         (max(1, int(image.width * scale)),
                          max(1, int(image.height * scale)))}
                ratio_error = abs(image.width / image.height / (width / height) - 1)
                if ratio_error <= .025:
                    # Source cell textures include a few border pixels (108,
                    # 212, 316, 420), not exact integer-cell proportions.
                    # Retain fit and full-cell rounding, never stretch odd art.
                    sizes.add((width, height))
                if ((len(rgbs) + len(sizes)) * pixels * 12 +
                        (len(rgbs) + len(sizes)) * 512 > _BANK_BYTES):
                    return None
                variants = []
                for fitted_size in sorted(sizes):
                    fitted = image.resize(fitted_size, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGBA", (width, height))
                    canvas.paste(fitted, ((width - fitted.width) // 2,
                                          (height - fitted.height) // 2))
                    variants.append(np.asarray(canvas, dtype=np.uint8))
            for rgba in variants:
                rgb = rgba[:, :, :3].copy().reshape(-1, 3)
                alpha = rgba[:, :, 3].astype(np.float32) / 255
                weight = np.square(alpha)
                weight[alpha < .2] = 0
                if float(weight.sum()) < 12:
                    raise ValueError("Unique artwork has no usable foreground.")
                grey = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32).reshape(-1)
                weight = weight.reshape(-1)
                total = float(weight.sum(dtype=np.float64))
                mean = float(np.sum(grey * weight, dtype=np.float64) / total)
                vector = (grey - mean) * weight
                norm = float(np.sqrt(np.sum(np.square(grey - mean) * weight, dtype=np.float64)))
                if norm < 1:
                    raise ValueError("Unique artwork has no usable contrast.")
                support = cv2.dilate((alpha > .15).astype(np.uint8), np.ones((5, 5), np.uint8))
                rgbs.append(rgb)
                weights.append(weight)
                centered.append(vector / norm)
                supports.append(support.reshape(-1))
                sums.append(total)
                labels.append(tuple(sorted(group["names"], key=str.casefold)))
        arrays = [np.stack(rgbs), np.stack(weights), np.stack(centered),
                  np.stack(supports), np.asarray(sums, dtype=np.float32)]
        for array in arrays:
            array.flags.writeable = False
        size = sum(a.nbytes for a in arrays) + sum(
            256 + sum(len(name.encode("utf-8")) + 80 for name in group) for group in labels)
        bank = _Bank(key, catalog.revision, shape, tuple(labels), *arrays, size)
        if size > _BANK_BYTES:
            return None
        while _BANKS and _RETAINED_BYTES + size > _BANK_BYTES:
            _, removed = _BANKS.popitem(last=False)
            _RETAINED_BYTES -= removed.byte_size
        _BANKS[key] = bank
        _RETAINED_BYTES += size
        return bank


def _correlations(bank, grey):
    """Compute masked, contrast-invariant NCC without creating per-reference crops."""
    weighted = np.einsum("ij,j->i", bank.weights, grey, optimize=False)
    squares = np.einsum("ij,j->i", bank.weights, np.square(grey), optimize=False)
    variance = np.maximum(0, squares - np.square(weighted) / bank.weight_sums)
    numerator = np.einsum("ij,j->i", bank.centered, grey, optimize=False)
    return np.divide(numerator, np.sqrt(variance), out=np.full_like(numerator, -1),
                     where=variance > 1)


def _rgb_score(reference, query, weight, total):
    r = reference.astype(np.float32)
    q = query.astype(np.float32)
    mean_r = float(np.sum(r * weight[:, None], dtype=np.float64) / (total * 3))
    mean_q = float(np.sum(q * weight[:, None], dtype=np.float64) / (total * 3))
    r -= mean_r
    q -= mean_q
    nr = float(np.sum(r * r * weight[:, None], dtype=np.float64))
    nq = float(np.sum(q * q * weight[:, None], dtype=np.float64))
    if nr < 1 or nq < 1:
        return -1.0
    return float(np.clip(np.sum(r * q * weight[:, None], dtype=np.float64) / np.sqrt(nr * nq), -1, 1))


def _rank(bank, image):
    height, width = bank.shape[1] * _UNIT, bank.shape[0] * _UNIT
    rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS),
                     dtype=np.uint8)
    if float(rgb.std(axis=(0, 1)).mean()) < 6:
        return []
    padded = np.pad(rgb, ((2, 2), (2, 2), (0, 0)), mode="edge")
    best_grey = np.full(len(bank.names), -1.0, dtype=np.float32)
    best_queries = np.zeros(len(bank.names), dtype=np.uint8)
    queries = []
    crops = [padded[dy:dy + height, dx:dx + width] for dy in range(5) for dx in range(5)]
    # Centering an odd-width sprite produces half-pixel differences between
    # selected region resolutions. Preserve shape and sample that phase too.
    for dy in (-.5, 0, .5):
        for dx in (-.5, 0, .5):
            if dx or dy:
                translated = cv2.warpAffine(rgb, np.float32([[1, 0, dx], [0, 1, dy]]),
                                            (width, height), flags=cv2.INTER_CUBIC,
                                            borderMode=cv2.BORDER_REPLICATE)
                crops.append(translated)
    for query in crops:
        grey = cv2.cvtColor(query, cv2.COLOR_RGB2GRAY).astype(np.float32).reshape(-1)
        score = np.clip(_correlations(bank, grey), -1, 1)
        # Reject a match that explains a small piece of a different item.
        border = np.concatenate((query[0], query[-1], query[:, 0], query[:, -1]))
        background = np.median(border, axis=0)
        foreground = (np.square(query.astype(np.float32) - background).sum(axis=2) > 900)
        foreground[:2] = foreground[-2:] = False
        foreground[:, :2] = foreground[:, -2:] = False
        total = int(foreground.sum())
        if total < 8:
            continue
        explained = np.einsum("ij,j->i", bank.support,
                              foreground.reshape(-1).astype(np.float32), optimize=False) / total
        score[explained < .82] = -1
        improved = score > best_grey
        best_grey[improved] = score[improved]
        best_queries[improved] = len(queries)
        queries.append(query.reshape(-1, 3).copy())
    scores = []
    for index, grey in enumerate(best_grey):
        if grey < .7:
            continue
        query = queries[int(best_queries[index])]
        weight, total = bank.weights[index], float(bank.weight_sums[index])
        chroma = query.max(axis=1).astype(np.float32) - query.min(axis=1)
        brightness = np.maximum(1, query.max(axis=1).astype(np.float32))
        desaturated = float(np.sum(chroma / brightness * weight, dtype=np.float64) / total) < .10
        color = _rgb_score(bank.rgb[index], query, weight, total)
        score = float(grey) if desaturated else .7 * float(grey) + .3 * color
        scores.append((float(np.clip(score, 0, 1)), bank.names[index], desaturated))
    return sorted(scores, key=lambda row: (-row[0], row[1]))


def match(image, shape, strictness=ocr_sensitivity.DEFAULT):
    """Return a name or ambiguity choices only for credible full-footprint artwork.

    Missing/corrupt optional catalog assets leave the existing scanner path in
    charge. This API never changes scan admission, counts or approval policy.
    """
    strictness = ocr_sensitivity.validate(strictness)
    if (not isinstance(image, Image.Image) or not isinstance(shape, tuple) or len(shape) != 2 or
            any(type(value) is not int for value in shape) or
            not 1 <= shape[0] <= 2 or not 1 <= shape[1] <= 4 or
            min(image.size) < 8 or max(image.size) > 2048 or
            image.width * image.height > 2_000_000 or
            abs(image.width / image.height / (shape[0] / shape[1]) - 1) > .22):
        return None
    try:
        bank = _bank(shape)
        if bank is None:
            return None
        normalized = image.convert("RGB").resize((shape[0] * _UNIT, shape[1] * _UNIT),
                                                 Image.Resampling.LANCZOS)
        key = (bank.signature, strictness, hashlib.sha256(normalized.tobytes()).digest())
        with _LOCK:
            if key in _QUERIES:
                _QUERIES.move_to_end(key)
                cached = _QUERIES[key]
                return None if cached is None else {**cached, "candidate_names": list(cached["candidate_names"])}
        ranked = _rank(bank, normalized)
        floor = ocr_sensitivity.review_threshold(.90, strictness, .06)
        clear = ocr_sensitivity.clear_threshold(.97, strictness, .025)
        result = None
        if ranked and ranked[0][0] >= floor:
            top, _, gray = ranked[0]
            margin = .045 if gray else .025
            by_name = {}
            for score, labels, _ in ranked:
                for name in labels:
                    by_name[name] = max(by_name.get(name, -1), score)
            choices = sorted((name for name, score in by_name.items()
                              if score >= max(floor, top - margin)), key=str.casefold)
            runner = max((score for name, score in by_name.items() if name not in choices), default=-1)
            name = choices[0] if (len(choices) == 1 and top >= clear and top - runner >= margin
                                  and strictness != 100) else ""
            result = {"name": name, "candidate_names": choices, "score": round(top, 6),
                      "source": "unique item catalog: " + bank.revision,
                      "catalog_unique": True, "category": "Item", "catalog_revision": bank.revision}
        with _LOCK:
            _QUERIES[key] = None if result is None else {**result, "candidate_names": tuple(result["candidate_names"])}
            while len(_QUERIES) > _QUERY_LIMIT:
                _QUERIES.popitem(last=False)
        return result
    except (OSError, ValueError, TypeError, Image.DecompressionBombError):
        return None
