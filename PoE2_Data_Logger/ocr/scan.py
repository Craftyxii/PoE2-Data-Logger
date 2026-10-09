"""Suggest a family and socket stage from a Runeshape pre-open screenshot."""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr.cv_eval import decode, features
from PoE2_Data_Logger.ocr.glyph_eval import vector
from PoE2_Data_Logger.ocr.prototype import center_for, crop_at, find_books, normalize_book


HERE = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _assets():
    """Cache the socket classifier, glyph gallery and book templates used by seed scans."""
    model = joblib.load(HERE / "socket_model.joblib")
    model.n_jobs = 1
    with np.load(HERE / "glyphs.npz") as gallery:
        names = gallery["names"]
        vectors = gallery["vectors"]
    templates = []
    for name in ("book_bronze.png", "book_purple.png"):
        with Image.open(HERE / name) as image:
            templates.append(image.convert("RGB"))
    return model, names, vectors, templates


def scan(path: Path, strictness=ocr_sensitivity.DEFAULT):
    """Locate up to 24 readable pre-open bars and return their seed/family suggestions.

    Reviewed glyphs extend the gallery; the visible-bar list requires socket
    confidence of at least .80 at default strictness. Lower settings admit
    tentative known matches; maximum strictness requires manual confirmation.
    """
    strictness = ocr_sensitivity.validate(strictness)
    with Image.open(path) as image:
        im = image.convert("RGB")
    assets = _assets()
    unique = (find_books(im, assets[3]) if strictness == ocr_sensitivity.DEFAULT else
              find_books(im, assets[3], threshold=ocr_sensitivity.review_threshold(.65, strictness, .15)))
    if not unique:
        return {"status": "No reliable pre-open rune bar found", "remnants": []}
    states = store.states()
    local = store.reviewed_glyphs({state["seed_rune"] for state in states})
    model, names, vectors, templates = assets
    if local:
        names = np.concatenate((names, np.array([rune for rune, _ in local])))
        vectors = np.concatenate((vectors, np.stack([glyph for _, glyph in local])))
    readings = []
    for book in sorted(unique, key=lambda item: (item[1], item[0])):
        reading = (_scan_scaled_bar(im, book, model, names, vectors, states)
                   if strictness == ocr_sensitivity.DEFAULT else
                   _scan_scaled_bar(im, book, model, names, vectors, states, strictness=strictness))
        if reading.get("sockets") and reading.get("socket_confidence", 0) >= ocr_sensitivity.review_threshold(.80, strictness, .15):
            if strictness != ocr_sensitivity.DEFAULT:
                reading["_ocr_strictness"] = strictness
            if strictness == 100:
                reading.update(can_commit=False, status="Review suggestion")
            reading["scan_index"] = len(readings) + 1
            readings.append(reading)
            if len(readings) == 24:
                break
    if not readings:
        result = (_scan_scaled_bar(im, unique[0], model, names, vectors, states)
                  if strictness == ocr_sensitivity.DEFAULT else
                  _scan_scaled_bar(im, unique[0], model, names, vectors, states, strictness=strictness))
        return {**result, "remnants": []}
    if len(readings) == 1:
        return {**readings[0], "remnants": readings}
    return {
        "status": f"{len(readings)} visible remnants found — review each match.",
        "remnants": readings,
    }


def _scan_scaled_bar(im, book, model, names, vectors, all_states, strictness=ocr_sensitivity.DEFAULT):
    """Refine a book’s scale and map the best reading back to capture coordinates.

    Competing socket/seed readings within .02 confidence disable commitment
    even when the highest-scoring alignment otherwise looks usable. All scale
    alternatives share the capture's strictness without relaxing that guard.
    """
    alternatives = []
    for delta in (0, -.025, .025, -.05, .05, -.075, .075):
        scale = round(book[3] + delta, 3)
        if scale < .4:
            continue
        # Keep the detected book centre when refining its physical scale.
        adjusted = (book[0] + 13 * (book[3] - scale),
                    book[1] + 19 * (book[3] - scale), book[2], scale)
        normalized, anchor = normalize_book(im, adjusted)
        result = (_scan_bar(normalized, anchor, model, names, vectors, all_states)
                  if strictness == ocr_sensitivity.DEFAULT else
                  _scan_bar(normalized, anchor, model, names, vectors, all_states, strictness=strictness))
        alternatives.append((result, normalized.info["seed_origin"], scale))
    reading, origin, scale = max(alternatives, key=lambda item: item[0].get("socket_confidence", 0))
    if not reading.get("bar_bounds"):
        return reading
    conflicting = [trial for trial, _, _ in alternatives if trial.get("sockets") and
                   trial.get("socket_confidence", 0) >= reading.get("socket_confidence", 0) - .02 and
                   (trial.get("sockets"), trial.get("seed_slot"), trial.get("seed_rune")) !=
                   (reading.get("sockets"), reading.get("seed_slot"), reading.get("seed_rune"))]
    if conflicting:
        reading.update(can_commit=False, status="Seed reading changes with alignment — confirm it manually.")
    if "bar_bounds" in reading:
        bounds = reading["bar_bounds"]
        x, y = origin[0] + round(bounds["x"] * scale), origin[1] + round(bounds["y"] * scale)
        reading["bar_bounds"] = {"x": x, "y": y,
            "width": min(im.width - x, round(bounds["width"] * scale)),
            "height": min(im.height - y, round(bounds["height"] * scale))}
    if "seed_center" in reading:
        reading["seed_center"] = {axis: origin[index] + round(value * scale)
                                  for index, (axis, value) in enumerate(reading["seed_center"].items())}
    return reading


def _scan_bar(im, book, model, names, vectors, all_states, strictness=ocr_sensitivity.DEFAULT):
    """Decode one seed in three to ten sockets, then compare slot-allowed glyphs and families.

    At default strictness, socket confidence below .60 stops identification. Position disagreement,
    book/glyph scores below .70 or a glyph margin below .08 require review;
    commitment also needs one non-inferred database stage. Strictness changes
    discovery confidence while retaining family, ambiguity and position guards.
    """
    bx, by, bar_score = book
    patches = []
    indices = []
    for j in range(10):
        f = features(im, bx - 47 - j * 57, by + 20)
        if f is not None:
            patches.append(f)
            indices.append(j)
    if not patches:
        return {
            "status": "Cannot read the complete bar",
            "bar_score": round(bar_score, 2),
        }
    probs = model.predict_proba(np.stack(patches))
    decoded = decode(probs, np.array(indices))
    if decoded is None:
        return {
            "status": "Cannot read the complete bar",
            "bar_score": round(bar_score, 2),
        }
    score, rune_j, n = decoded
    socket_confidence = float(np.exp(score / len(indices)))
    if socket_confidence < ocr_sensitivity.review_threshold(.60, strictness, .10):
        return {
            "status": "Cannot confirm a single visible seed",
            "bar_score": round(bar_score, 2),
        }
    slot = n - rune_j
    bar_bounds = {
        "x": max(0, bx - 47 - (n - 1) * 57 - 29),
        "y": max(0, by - 8),
        "width": 47 + (n - 1) * 57 + 62,
        "height": 57,
    }
    states = [s for s in all_states if s["sockets"] == n]
    positions = []
    for position in range(1, n + 1):
        cx, cy = center_for(bx, by, n, position)
        crops = [
            crop_at(im, cx + dx, cy + dy, 36, 36)
            for dx in (-4, -2, 0, 2, 4)
            for dy in (-4, -2, 0, 2, 4)
        ]
        variants = [vector(patch, "gray") for patch in crops if patch is not None]
        if not variants:
            continue
        similarities = (np.stack(variants) @ vectors.T).max(0)
        allowed = {s["seed_rune"] for s in states if s["seed_slot"] == f"P{position}"}
        scores = sorted(
            (
                (float(similarities[names == r].max()), r)
                for r in allowed
                if np.any(names == r)
            ),
            reverse=True,
        )
        if scores:
            positions.append((scores[0][0], position, scores))
    positions.sort(reverse=True)
    position_unclear = False
    if positions and positions[0][1] != slot:
        position_unclear = True
    scores = next(
        (scores for similarity, position, scores in positions if position == slot), []
    )
    entries = [s for s in states if s["seed_slot"] == f"P{slot}"]
    cx, cy = center_for(bx, by, n, slot)
    if not scores:
        return {
            "status": "Seed icon needs manual identification",
            "sockets": n,
            "seed_slot": f"P{slot}",
            "bar_score": round(bar_score, 2),
            "bar_bounds": bar_bounds,
            "socket_confidence": socket_confidence,
        }
    glyph_score, rune = scores[0]
    matches = [s for s in entries if s["seed_rune"] == rune]
    stage = matches[0] if len(matches) == 1 else None
    margin = glyph_score - scores[1][0] if len(scores) > 1 else 1.0
    review = (position_unclear or
              bar_score < ocr_sensitivity.clear_threshold(.70, strictness, .15) or
              glyph_score < ocr_sensitivity.clear_threshold(.70, strictness, .15) or
              margin < max(.08, ocr_sensitivity.clear_threshold(.08, strictness, .04)) or
              (strictness != ocr_sensitivity.DEFAULT and socket_confidence <
               ocr_sensitivity.clear_threshold(.80, strictness, .15)))
    if position_unclear:
        stage = None
    return {
        "status": "Multiple families match — choose one"
        if len(matches) > 1
        else "Review suggestion"
        if review
        else "Check screenshot before logging",
        "sockets": n,
        "seed_slot": f"P{slot}",
        "seed_rune": rune,
        "family": f"Family {stage['family']}" if stage else None,
        "candidates": [s["family"] for s in matches],
        "rewards": stage["rewards"] if stage else [],
        "bar_score": round(bar_score, 2),
        "rune_similarity": round(glyph_score, 2),
        "rune_margin": round(margin, 2),
        "bar_bounds": bar_bounds,
        "socket_confidence": socket_confidence,
        "seed_center": {"x": cx, "y": cy},
        "can_commit": bool(
            stage
            and not review
            and strictness < 100
            and not str(stage.get("status", "")).startswith("inferred")
        ),
        **({"_ocr_strictness": strictness} if strictness != ocr_sensitivity.DEFAULT else {}),
    }


def main():
    """Scan a screenshot supplied on the command line and print the result as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", type=Path)
    args = parser.parse_args()
    result = scan(args.screenshot)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
