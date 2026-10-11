"""Measure physical propagation marks independently of OCR text and database inference.

The unchanged capture strips have independent one-based socket labels. They are
reassembled at their original coordinates before the scanner's 575-pixel
normalization; resizing individual strips would change the fractional row phase.
Controlled tip removal is synthetic evidence, never a measurement of naturally
partial marks or a substitute for scanner family/row-consensus tests.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import combinations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
EXPOSURES = (1, .9, 1.1, .8, 1.2, 1.35, 1.5, .95, 1.05, 1.25)
STANDARD_EXPOSURES = EXPOSURES
DENSE_EXPOSURES = tuple(dict.fromkeys((*EXPOSURES, .825, .85, .875, .925, .975,
                                      1.025, 1.075, 1.125, 1.15, 1.175)))
sys.path.insert(0, str(ROOT))
from PoE2_Data_Logger.ocr import propagation_marks, propagation_scan  # noqa: E402


def process_peak_rss_bytes():
    """Return this process's high-water RSS, or None when the OS lacks a supported API."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            """Represent the Windows process-memory structure without optional dependencies."""
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in (
                            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD)
        if api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except (ImportError, OSError):
        return None


def load_manifest(directory):
    """Validate labeled socket positions and crop provenance before counting evidence."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    sources = {source["index"]: source for source in manifest["sources"]}
    if len(sources) != len(manifest["sources"]):
        raise ValueError("Source indices must be unique.")
    counts = dict(panels=len(sources), rows=len(manifest["rows"]),
                  full_marks=0, partial_marks=0, uncertain_marks=0)
    identities = set()
    for row in manifest["rows"]:
        source = sources[row["source_index"]]
        identity = (row["source_index"], row["row"])
        if identity in identities:
            raise ValueError("Rows must have independent, unique identities.")
        identities.add(identity)
        if row["source_sha256"] != source["sha256"]:
            raise ValueError("Crop source hashes must match the source manifest.")
        box = row["crop_box"]
        if (len(box) != 4 or box[0] != 0 or box[1] < 0
                or box[2] != source["label_view_dimensions"][0]
                or box[3] > source["label_view_dimensions"][1]
                or box[3] <= box[1]):
            raise ValueError("Crop must retain original horizontal and vertical coordinates.")
        dimensions = [box[2] - box[0], box[3] - box[1]]
        expected_hash = row.get("strip_sha256")
        if expected_hash is not None and hashlib.sha256(
                (directory / row["image"]).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Fixture strip hash differs from the labeled capture artifact.")
        with Image.open(directory / row["image"]) as image:
            if list(image.size) != dimensions or dimensions != row["crop_dimensions"]:
                raise ValueError("Fixture dimensions must agree with crop coordinates.")
        if row["pitch"] <= 0 or row["sockets"] < 1:
            raise ValueError("Annotated socket geometry must be positive.")
        marked = set()
        for field, count_field in (("full_positions", "full_marks"),
                                   ("partial_positions", "partial_marks"),
                                   ("uncertain_positions", "uncertain_marks")):
            positions = row.get(field, [])
            if (len(set(positions)) != len(positions) or marked.intersection(positions)
                    or any(type(position) is not int or not 1 <= position <= row["sockets"]
                           for position in positions)):
                raise ValueError("Socket labels must be unique, disjoint, one-based and in range.")
            marked.update(positions)
            counts[count_field] += len(positions)
    if counts != manifest["counts"]:
        raise ValueError(f"Manifest counts disagree with independent labels: {counts}.")
    return manifest


def reconstructed_panels(directory, manifest):
    """Reassemble original pixels at original y offsets, then normalize each panel once."""
    rows_by_source = defaultdict(list)
    for row in manifest["rows"]:
        rows_by_source[row["source_index"]].append(row)
    for source in manifest["sources"]:
        image = Image.new("RGB", tuple(source["label_view_dimensions"]))
        rows = sorted(rows_by_source[source["index"]], key=lambda row: row["row"])
        for row in rows:
            with Image.open(Path(directory) / row["image"]) as strip:
                image.paste(strip.convert("RGB"), tuple(row["crop_box"][:2]))
        scale = 575 / image.width
        normalized = image.resize((575, round(image.height * scale)), Image.Resampling.LANCZOS)
        yield source, normalized, rows, scale


def masks_for(image):
    """Use pale-capable scanner exposure masks without counting them as independent rows."""
    pixels = np.asarray(image, dtype=np.float32)
    return [(factor, propagation_scan._gold_mask(image if factor == 1 else Image.fromarray(
        np.clip(pixels * factor, 0, 255).astype(np.uint8)), pale=True)) for factor in EXPOSURES]


def geometry_for(row, scale):
    """Convert approximate original centers and tile tops to normalized detector geometry."""
    pitch = row["pitch"] * scale
    size = pitch * 38 / 41
    top = (row["crop_box"][1] + row["tile_top"]) * scale
    return ([{"y1": top + pitch, "sockets": row["sockets"]}],
            (row["first_x"] * scale, pitch), size)


def box_positions(boxes, layout):
    """Deduplicate exposure-supported physical boxes by their one-based socket position."""
    first, pitch = layout
    return {round((box[0] - first) / pitch) + 1 for box in boxes}


def counts_for(expected, found, uncertain=()):
    """Count physical labels once and exclude independently uncertain positions from scoring."""
    expected, found = set(expected), set(found) - set(uncertain)
    return {"tp": len(expected & found), "fp": len(found - expected),
            "fn": len(expected - found), "exact_rows": int(expected == found), "rows": 1}


def finalized_counts(counts):
    """Calculate precision and recall with explicit denominators, retaining zero-evidence None."""
    result = dict(counts)
    precision_denominator = result["tp"] + result["fp"]
    recall_denominator = result["tp"] + result["fn"]
    result["precision"] = result["tp"] / precision_denominator if precision_denominator else None
    result["recall"] = result["tp"] / recall_denominator if recall_denominator else None
    return result


def evaluate_full(detector, directory, manifest):
    """Score every visible annotated socket without family inference or reward-text recognition."""
    started = time.monotonic()
    totals = dict(tp=0, fp=0, fn=0, exact_rows=0, rows=0)
    weak = dict(full_misses_with_fragment_evidence=0, fragment_evidence_at_unmarked_slots=0)
    results = []
    for source, image, rows, scale in reconstructed_panels(directory, manifest):
        masks = masks_for(image)
        for row in rows:
            regions, layout, size = geometry_for(row, scale)
            boxes = detector.supported_peak_boxes(masks, regions, tile_layout=layout, tile_size=size)
            found = box_positions(boxes, layout)
            counts = counts_for(row["full_positions"], found,
                                row.get("uncertain_positions", []))
            for key in totals:
                totals[key] += counts[key]
            fragments = detector.supported_fragment_peak_boxes(
                masks, regions, tile_layout=layout, tile_size=size)
            fragment_positions = box_positions(fragments, layout) - found
            uncertain = set(row.get("uncertain_positions", []))
            weak["full_misses_with_fragment_evidence"] += len(
                fragment_positions & (set(row["full_positions"]) - found))
            weak["fragment_evidence_at_unmarked_slots"] += len(
                fragment_positions - set(row["full_positions"]) - uncertain)
            results.append({"source": source["filename"], "row": row["row"],
                            "expected": row["full_positions"], "found": sorted(found),
                            "fragment_positions": sorted(fragment_positions),
                            **counts})
    return {"counts": finalized_counts(totals), "rows": results,
            "weaker_evidence": weak,
            "elapsed_seconds": time.monotonic() - started}


def erase_tips(image, box, removed):
    """Replace selected captured tips with nearby parchment, retaining frame and all other pixels.

    Only pixels above the actual contiguous gold frame line are changed. The
    detector's approximate anchor can precede that frame by several pixels;
    stopping at that anchor would leave a flat tip stub and mislabel damage.
    The original image and masks remain unchanged.
    """
    centre, bottom, size = box[:3]
    pixels = np.asarray(image).copy()
    gold = propagation_scan._gold_mask(image, pale=True).astype(bool)
    left, right = max(0, round(centre - size / 2)), min(image.width, round(centre + size / 2))
    start = max(0, round(bottom) - 6)
    frame_lines = []
    for y in range(max(start, round(bottom) - 1), min(image.height, round(bottom) + 9)):
        active = np.flatnonzero(gold[y, left:right])
        runs = np.split(active, np.flatnonzero(np.diff(active) > 1) + 1)
        # JPEG/Lanczos can perforate a real frame by single pixels. Requiring
        # broad coverage as well as a joined span avoids mistaking spaced tips
        # for the frame while keeping that physical baseline untouched.
        joined = np.split(active, np.flatnonzero(np.diff(active) > 2) + 1)
        perforated_frame = (len(active) >= size * .65
                            and any(len(run) and run[-1] - run[0] + 1 >= size * .75
                                    for run in joined))
        if any(len(run) >= size * .5 for run in runs) or perforated_frame:
            frame_lines.append(y)
    if not frame_lines:
        return None, None
    end = frame_lines[0]
    # Keep an overlapping recipe border above the tips, and any socket frame
    # starting just before the detected baseline. Only the intervening tips change.
    for y in range(start, max(start, round(bottom) - 1)):
        active = np.flatnonzero(gold[y, left:right])
        runs = np.split(active, np.flatnonzero(np.diff(active) > 1) + 1)
        if any(len(run) >= size * .5 for run in runs):
            start = y + 1
    samples = pixels[start:end, left:right][~gold[start:end, left:right]]
    if not len(samples) or end <= start:
        return None, None
    fill = np.median(samples, axis=0).astype(np.uint8)
    changed = np.zeros(gold.shape, dtype=bool)
    radius = max(3, round(size * .105))
    for offset in removed:
        x = centre + (offset - 1) * size / 4
        x1, x2 = max(left, round(x) - radius), min(right, round(x) + radius + 1)
        # Erase a rectangle, rather than only gold pixels, to model an occluded tip.
        pixels[start:end, x1:x2] = fill
        changed[start:end, x1:x2] = True
    return Image.fromarray(pixels), changed


def evaluate_partials(detector, directory, manifest, limit=None):
    """Measure controlled tip occlusion on intact captured crowns, with conditional eligibility.

    Each original crown detected by this detector receives all three one-tip
    and three two-tip removals, plus all-tip removal as a negative control.
    The detector-dependent eligible count is explicit, avoiding a claim of
    recall over undetected source crowns. These tests do not supply the two
    distinct recipe rows needed for scanner partial-consensus acceptance.
    """
    started = time.monotonic()
    totals = {str(count): {"cases": 0, "fragment_detected": 0, "full_detected": 0,
                          "unsupported": 0, "other_fragment_positions": 0,
                          "other_full_positions": 0} for count in (1, 2, 3)}
    eligible, skipped, cases = 0, 0, []
    for source, panel, rows, scale in reconstructed_panels(directory, manifest):
        for row in rows:
            # Keep exact normalized pixels and y phase, but avoid rescanning blank panel height.
            regions, layout, size = geometry_for(row, scale)
            top = regions[0]["y1"] - layout[1]
            y0, y2 = max(0, int(top) - 12), min(panel.height, int(top + size) + 4)
            image = panel.crop((0, y0, panel.width, y2))
            regions[0]["y1"] -= y0
            masks = masks_for(image)
            originals = detector.supported_peak_boxes(masks, regions, tile_layout=layout, tile_size=size)
            for position in row["full_positions"]:
                if limit is not None and eligible >= limit:
                    break
                matching = [box for box in originals if position in box_positions([box], layout)]
                if not matching:
                    skipped += 1
                    continue
                eligible += 1
                box = matching[0]
                expected_others = set(row["full_positions"]) - {position}
                for count in (1, 2, 3):
                    for removed in combinations(range(3), count):
                        damaged, changed = erase_tips(image, box, removed)
                        if damaged is None or not changed.any():
                            totals[str(count)]["unsupported"] += 1
                            continue
                        damaged_masks = masks_for(damaged)
                        full = box_positions(detector.supported_peak_boxes(
                            damaged_masks, regions, tile_layout=layout, tile_size=size), layout)
                        fragments = box_positions(detector.supported_fragment_peak_boxes(
                            damaged_masks, regions, tile_layout=layout, tile_size=size), layout) - full
                        total = totals[str(count)]
                        total["cases"] += 1
                        total["fragment_detected"] += int(position in fragments)
                        total["full_detected"] += int(position in full)
                        total["other_fragment_positions"] += len(fragments - {position} - expected_others)
                        total["other_full_positions"] += len(full - {position} - expected_others)
                        cases.append({"source": source["filename"], "row": row["row"],
                                      "position": position, "removed_tips": list(removed),
                                      "full_positions": sorted(full), "fragment_positions": sorted(fragments)})
            if limit is not None and eligible >= limit:
                break
        if limit is not None and eligible >= limit:
            break
    for values in totals.values():
        values["fragment_recovery"] = (values["fragment_detected"] / values["cases"]
                                       if values["cases"] else None)
    return {"synthetic": True, "naturally_partial_marks": manifest["counts"]["partial_marks"],
            "eligible_detected_source_crowns": eligible, "undetected_source_crowns_skipped": skipped,
            "sample_limit": limit, "tip_removal": totals, "cases": cases,
            "elapsed_seconds": time.monotonic() - started,
            "limitation": "Conditional direct-fragment evidence; no family inference or cross-row consensus."}


def baseline_detector(reference):
    """Load the tracked historical helper without checking out or modifying shared source files."""
    path = "PoE2_Data_Logger/ocr/propagation_marks.py"
    source = subprocess.run(["git", "show", f"{reference}:{path}"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout
    module = ModuleType("propagation_crowns_baseline")
    module.__file__ = f"git:{reference}:{path}"
    module._benchmark_source_sha256 = hashlib.sha256(source.encode()).hexdigest()
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def evaluate_detector(name, reference, directory, manifest, partial_limit):
    """Run one helper and include this dedicated process's runtime and peak RSS."""
    started = time.monotonic()
    detector = propagation_marks if name == "current" else baseline_detector(reference)
    source_hash = (hashlib.sha256(Path(propagation_marks.__file__).read_bytes()).hexdigest()
                   if name == "current" else detector._benchmark_source_sha256)
    report = {"detector": name, "baseline_ref": reference if name == "baseline" else None,
              "helper_source_sha256": source_hash, "exposure_factors": EXPOSURES,
              "full_marks": evaluate_full(detector, directory, manifest)}
    if partial_limit != 0:
        report["controlled_partials"] = evaluate_partials(detector, directory, manifest, partial_limit)
    report["elapsed_seconds"] = time.monotonic() - started
    report["peak_rss_bytes"] = process_peak_rss_bytes()
    return report


def main(argv=None):
    """Compare helpers in separate child processes so each detector has independent RSS evidence."""
    global EXPOSURES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests/fixtures/propagation_crowns")
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial-samples", type=int, default=-1,
                        help="Detected source crowns to damage per detector; -1 all, 0 skip.")
    parser.add_argument("--detector", choices=("current", "baseline", "both"), default="both")
    parser.add_argument("--exposures", choices=("standard", "dense"), default="standard",
                        help="Each detector's production bracket or an experimental twenty-mask tuning sample.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    EXPOSURES = (getattr(propagation_marks, "PEAK_EXPOSURES", STANDARD_EXPOSURES)
                 if args.exposures == "standard" and args.detector == "current"
                 else STANDARD_EXPOSURES if args.exposures == "standard" else DENSE_EXPOSURES)
    if args.partial_samples < -1:
        parser.error("--partial-samples must be -1, zero or positive.")
    manifest = load_manifest(args.fixtures)
    limit = None if args.partial_samples == -1 else args.partial_samples
    if args.worker:
        if args.detector == "both":
            parser.error("A worker evaluates exactly one detector.")
        report = evaluate_detector(args.detector, args.baseline_ref, args.fixtures, manifest, limit)
    else:
        started = time.monotonic()
        report = {"dataset": manifest["counts"], "protocol": {
            "full_marks": "Known annotated rows/lattice, unchanged pixels, one 575px panel resize, exposure bracket.",
            "exposure_factors": "Recorded per detector; historical ten and current production bracket.",
            "scope": "Direct physical marks only; excludes text OCR, family matching and inferred missing marks.",
            "partials": "Controlled captured-tip erasure; natural partial accuracy is unmeasured when labels are zero.",
            "rss": "Independent child-process peak RSS, including imports; not application runtime model RSS."},
            "detectors": {}}
        selected = ("baseline", "current") if args.detector == "both" else (args.detector,)
        with tempfile.TemporaryDirectory(prefix="poe2-crown-benchmark-") as temporary:
            for name in selected:
                output = Path(temporary) / f"{name}.json"
                subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--detector", name,
                                "--fixtures", str(args.fixtures.resolve()), "--baseline-ref", args.baseline_ref,
                                "--exposures", args.exposures,
                                "--partial-samples", str(args.partial_samples), "--output", str(output)],
                               cwd=ROOT, check=True)
                report["detectors"][name] = json.loads(output.read_text(encoding="utf-8"))
                counts = report["detectors"][name]["full_marks"]["counts"]
                print(f"{name}: {json.dumps(counts, sort_keys=True)}", flush=True)
        report["elapsed_seconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
