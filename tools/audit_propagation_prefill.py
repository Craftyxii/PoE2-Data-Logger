"""Measure production propagation slot suggestions on original, independently labeled captures.

No annotated text boxes or tile geometry are supplied to the scanner. Coverage
includes family inference and manual-review suggestions, not just crown detection.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import propagation_scan


def score_rows(expected, choices):
    """Compare one-based socket suggestions against the corresponding readable labeled rows."""
    if len(expected) != len(choices):
        raise ValueError("Recognized and readable annotated row counts differ; inspect identities before scoring.")
    counts = dict(rows=len(expected), suggested_rows=0, tp=0, fp=0, fn=0, exact_rows=0)
    for annotation, choice in zip(expected, choices):
        actual = set(choice["positions"]) if choice["can_use"] else set()
        wanted = set(annotation["full_positions"])
        counts["suggested_rows"] += bool(choice["can_use"])
        counts["tp"] += len(actual & wanted)
        counts["fp"] += len(actual - wanted)
        counts["fn"] += len(wanted - actual)
        counts["exact_rows"] += actual == wanted
    return counts


def audit_sources(directory):
    """Read each unchanged original through actual OCR in a disposable catalog profile."""
    manifest_path = ROOT / "tests/fixtures/propagation_crowns/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {}
    wanted = {source["filename"] for source in manifest["sources"]}
    for path in directory.rglob("*"):
        if path.is_file() and path.name in wanted:
            files.setdefault(path.name, []).append(path)
    previous = store.DATA_DIR
    records = []
    totals = dict(panels=0, exact_panels=0, rows=0, suggested_rows=0,
                  tp=0, fp=0, fn=0, exact_rows=0, excluded_rows=0, excluded_marks=0)
    try:
        with tempfile.TemporaryDirectory(prefix="propagation-prefill-audit-") as temporary:
            store.DATA_DIR = Path(temporary)
            logger._READY = False
            logger.initialize()
            for source in sorted(manifest["sources"], key=lambda item: item["index"]):
                paths = files.get(source["filename"], [])
                if not paths or any(hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]
                                    for path in paths):
                    raise ValueError(f"Missing or changed original capture: {source['filename']}")
                labels = sorted((row for row in manifest["rows"] if row["source_index"] == source["index"]),
                                key=lambda row: row["row"])
                readable = [row for row in labels if row["recipe"] and not row["recipe"].startswith("[")]
                omitted = [row for row in labels if row not in readable]
                started = time.monotonic()
                result = propagation_scan.scan_propagation(paths[0])
                choices = result["choices"]
                counts = score_rows(readable, choices)
                for key, value in counts.items():
                    totals[key] += value
                totals["panels"] += 1
                totals["exact_panels"] += counts["exact_rows"] == counts["rows"]
                totals["excluded_rows"] += len(omitted)
                totals["excluded_marks"] += sum(len(row["full_positions"]) for row in omitted)
                records.append(dict(filename=source["filename"], sha256=source["sha256"],
                                    seconds=round(time.monotonic() - started, 3),
                                    family_candidates=result["candidates"], counts=counts,
                                    rows=[dict(annotation=row["recipe"], recipe=choice["selected_recipe"],
                                               expected=row["full_positions"], positions=choice["positions"],
                                               runes=choice["runes"], can_use=choice["can_use"],
                                               status=choice["status"])
                                          for row, choice in zip(readable, choices)]))
                print(f"{source['filename']}: {counts['exact_rows']}/{counts['rows']} exact readable rows", flush=True)
    finally:
        store.DATA_DIR = previous
        logger._READY = False
    totals["slot_coverage"] = totals["tp"] / (totals["tp"] + totals["fn"])
    totals["suggestion_precision"] = totals["tp"] / (totals["tp"] + totals["fp"])
    return dict(measure="Full production OCR and family-inferred slot suggestions for manual review",
                limitation="Supplied calibration captures; no natural partial crowns. Ordered readable rows are scored; clipped/unnamed rows are excluded.",
                propagation_scan_sha256=hashlib.sha256(Path(propagation_scan.__file__).read_bytes()).hexdigest(),
                manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                counts=totals, captures=records)


def main():
    """Write the full-capture audit separately from isolated physical crown benchmarks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = audit_sources(args.sources)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
