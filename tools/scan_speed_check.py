"""Time Currency and Ritual scans and record their final readings, to prove a change keeps them identical.

Every capture is read in one disposable profile with the models and artwork loaded first, as the app's
workers are at startup. Each capture is also read at 1440p and 4K scale. A run saves one JSON record per
reading (the whole result) and its time; --compare reports every difference between two runs.

    python tools/scan_speed_check.py --save before.json [--standard] [--ritual-folder DIR] [--only currency|ritual]
    python tools/scan_speed_check.py --compare before.json after.json

Exits 1 when --compare finds any difference.
"""

import argparse
import glob
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures"
SCALES = {"native": 1.0, "1440p": 4 / 3, "4k": 2.0}
# Readings that are timing, not results.
IGNORED = frozenset()


def captures(kind, ritual_folder=None):
    """Yield (name, image) for every capture of one kind."""
    if kind == "currency":
        paths = sorted(glob.glob(str(FIXTURES / "currency_training" / "*.png")))
        paths += [str(FIXTURES / "unique_items" / "inventory.png")]
    else:
        paths = sorted(glob.glob(str(FIXTURES / "ritual_pages*" / "*.png")))
        paths += sorted(glob.glob(str(FIXTURES / "ritual_same_rolls" / "*.png")))
        paths += sorted(glob.glob(str(FIXTURES / "ritual_greyed" / "*.png")))
        paths += sorted(glob.glob(str(FIXTURES / "ritual_grayscale" / "*.png")))
        if ritual_folder:
            paths += sorted(glob.glob(str(Path(ritual_folder) / "*.png")))
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGB")
        for scale, factor in SCALES.items():
            shown = image if factor == 1 else image.resize(
                (round(image.width * factor), round(image.height * factor)), Image.Resampling.LANCZOS)
            yield f"{Path(path).parent.name}/{Path(path).name}@{scale}", shown


def plain(value):
    """Make a result JSON-comparable (tuples become lists, sets sorted lists)."""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items() if key not in IGNORED}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(plain(item) for item in value)
    if hasattr(value, "item"):
        return value.item()
    return value


def run(only, ritual_folder, standard=False):
    """Read every capture once after loading, returning records and times."""
    from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers
    from PoE2_Data_Logger.ocr import item_ocr

    ocr_runtime._FAST = not standard

    logger.initialize()
    started = time.perf_counter()
    ocr_workers._prepare_artwork()
    print(f"loaded in {time.perf_counter() - started:.1f} s", flush=True)
    names, ritual_references, inventory_references = logger.ritual_names(), logger.ritual_icons(), logger.inventory_icons()
    records = {}
    for kind in ("currency", "ritual"):
        if only and kind != only:
            continue
        for name, image in captures(kind, ritual_folder):
            started = time.perf_counter()
            try:
                result = (item_ocr.scan_inventory_grid(image, inventory_references) if kind == "currency" else
                          item_ocr.scan_ritual_page(image, names, ritual_references))
                result = plain(result)
            except Exception as error:
                result = {"error": f"{type(error).__name__}: {error}"}
            seconds = time.perf_counter() - started
            records[f"{kind}:{name}"] = {"result": result, "seconds": round(seconds, 3)}
            print(f"{kind:8} {seconds:6.2f} s  {name}", flush=True)
    return records


def differences(before, after, path=""):
    """List every differing value between two readings."""
    if isinstance(before, dict) and isinstance(after, dict):
        found = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                found.append(f"{path}.{key}: {'missing before' if key not in before else 'missing after'}")
            else:
                found += differences(before[key], after[key], f"{path}.{key}")
        return found
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [line for index, (old, new) in enumerate(zip(before, after))
                for line in differences(old, new, f"{path}[{index}]")]
    return [] if before == after else [f"{path}: {before!r} -> {after!r}"]


def compare(before_path, after_path):
    """Print every difference and the time per kind; return the number of differing captures."""
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))
    changed = 0
    for key in sorted(set(before) | set(after)):
        if key not in before or key not in after:
            print(f"ONLY IN {'AFTER' if key not in before else 'BEFORE'}: {key}")
            changed += 1
            continue
        found = differences(before[key]["result"], after[key]["result"])
        if found:
            changed += 1
            print(f"DIFFERENT {key}")
            for line in found[:20]:
                print("   ", line)
    for kind in ("currency", "ritual"):
        for scale in SCALES:
            keys = [key for key in before if key.startswith(kind + ":") and key.endswith("@" + scale) and key in after]
            if keys:
                old = sum(before[key]["seconds"] for key in keys) / len(keys)
                new = sum(after[key]["seconds"] for key in keys) / len(keys)
                print(f"{kind} {scale}: {len(keys)} captures, {old:.2f} s -> {new:.2f} s per scan")
    print(f"{changed} of {len(set(before) | set(after))} readings differ")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", help="read every capture and write the readings here")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--ritual-folder", help="also read every PNG Ritual page in this folder")
    parser.add_argument("--only", choices=("currency", "ritual"))
    parser.add_argument("--standard", action="store_true", help="read with Fast recognition off")
    arguments = parser.parse_args()
    if arguments.compare:
        return 1 if compare(*arguments.compare) else 0
    if not arguments.save:
        parser.error("choose --save or --compare")
    logging.disable(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="poe2-speed-check-") as profile:
        os.environ["RUNESHAPE_SCAN_DATA_DIR"] = profile
        records = run(arguments.only, arguments.ritual_folder, arguments.standard)
    Path(arguments.save).write_text(json.dumps(records, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
