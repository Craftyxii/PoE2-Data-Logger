"""Suggest a family and socket stage from a Runeshape pre-open screenshot."""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from PIL import Image

import store
from cv_eval import decode, features
from glyph_eval import vector
from prototype import center_for, crop_at, ncc_find_all


HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _assets():
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


def scan(path: Path):
    with Image.open(path) as image:
        im = image.convert("RGB")
    assets = _assets()
    gray = np.asarray(im.convert("L"), dtype=np.float32)
    books = sorted(
        (
            peak
            for template in assets[3]
            for peak in ncc_find_all(im, template, gray=gray)
        ),
        key=lambda item: item[2],
        reverse=True,
    )
    unique = []
    for peak in books:
        if not any(
            abs(peak[0] - other[0]) < 32 and abs(peak[1] - other[1]) < 40
            for other in unique
        ):
            unique.append(peak)
    if not unique:
        return {"status": "No reliable pre-open rune bar found", "remnants": []}
    states = store.states()
    local = store.reviewed_glyphs({state["seed_rune"] for state in states})
    model, names, vectors, templates = assets
    if local:
        names = np.concatenate((names, np.array([rune for rune, _ in local])))
        vectors = np.concatenate((vectors, np.stack([glyph for _, glyph in local])))
    readings = []
    for bx, by, confidence in sorted(unique, key=lambda item: (item[1], item[0])):
        reading = _scan_bar(im, (bx, by, confidence), model, names, vectors, states)
        if reading.get("sockets"):
            reading["scan_index"] = len(readings) + 1
            readings.append(reading)
            if len(readings) == 24:
                break
    if not readings:
        result = _scan_bar(im, unique[0], model, names, vectors, states)
        return {**result, "remnants": []}
    if len(readings) == 1:
        return {**readings[0], "remnants": readings}
    return {
        "status": f"{len(readings)} visible remnants found — review each match.",
        "remnants": readings,
    }


def _scan_bar(im, book, model, names, vectors, all_states):
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
    if float(np.exp(score / len(indices))) < 0.60:
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
        best = positions[0]
        runner = positions[1][0] if len(positions) > 1 else 0
        if best[0] >= 0.90 and best[0] - runner >= 0.08:
            slot = best[1]
        else:
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
        }
    glyph_score, rune = scores[0]
    matches = [s for s in entries if s["seed_rune"] == rune]
    stage = matches[0] if len(matches) == 1 else None
    margin = glyph_score - scores[1][0] if len(scores) > 1 else 1.0
    review = position_unclear or bar_score < 0.70 or glyph_score < 0.70 or margin < 0.08
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
        "seed_center": {"x": cx, "y": cy},
        "can_commit": bool(
            stage
            and not review
            and not str(stage.get("status", "")).startswith("inferred")
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", type=Path)
    args = parser.parse_args()
    result = scan(args.screenshot)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
