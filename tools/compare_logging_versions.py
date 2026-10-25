#!/usr/bin/env python3
"""Run one scripted logging session on two source trees and compare every export cell by cell.

Use it to show that logging and export behave as an earlier release did:

    python tools/compare_logging_versions.py --old <tree> [--new <tree>] [--json OUT]

Each tree is a folder holding a ``PoE2_Data_Logger`` package (for example a release
extracted with ``git archive v1.3.2.27-beta PoE2_Data_Logger | tar -x -C <tree>``).
Every session runs in its own empty profile with the same fixed clock and IDs, through
logger functions that exist in both trees. The comparison lists every differing cell;
``INTENDED`` names the differences that a later release introduced on purpose.
"""

import argparse
import csv
import difflib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Sheets read from both trees; a sheet missing in either tree is reported, not skipped.
EXPORTS = ("export_all_csv", "export_maps_csv", "export_map_summary_csv", "export_csv",
           "export_primary_csv", "export_record_history_csv", "export_commits_csv",
           "export_currency_csv", "export_ritual_csv", "export_atlas_csv")

# Differences introduced on purpose after 1.3.2.27, as (sheet, column) pairs, plus two
# rules in compare(): a deferred Ritual reward was logged on the page that first offered
# it, so it is not saved again, and Rite fields belong to Ritual maps only. A kill-count
# reading becomes the map's kill totals.
INTENDED = {
    ("export_ritual_csv", "Type"), ("export_ritual_csv", "Name"), ("export_ritual_csv", "Quantity"),
    ("export_ritual_csv", "Tribute"), ("export_ritual_csv", "OCR Source"),
    ("export_ritual_csv", "Page OCR Text"), ("export_ritual_csv", "Recorded UTC"),
    ("export_ritual_csv", "Scan Commit #"), ("export_ritual_csv", "Deferred"),
    ("export_ritual_csv", "New Find Quantity"), ("export_ritual_csv", "Ritual Page"),
    ("export_ritual_csv", "Map ID"), ("export_ritual_csv", "Ritual Tribute Available"),
    ("export_ritual_csv", "Ritual Rerolls Remaining"),
}
KILL_COLUMNS = ("Normal Kills (Map)", "Magic Kills (Map)", "Rare Kills (Map)", "Unique Kills (Map)",
                "Total Kills (Map)", "Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills")

SESSION = r'''
import io, json, os, random, sys, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
root, profile, out = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["RUNESHAPE_SCAN_DATA_DIR"] = profile
sys.path.insert(0, root)
ids = random.Random(27)
uuid.uuid4 = lambda: uuid.UUID(int=ids.getrandbits(128), version=4)
from PoE2_Data_Logger.core import logger_store as logger, store, kill_counts
store.DATA_DIR = Path(profile)
clock = [datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)]
def tick():
    clock[0] += timedelta(seconds=7)
    return clock[0].isoformat(timespec="seconds")
logger._now = tick
logger._READY = False
logger.initialize()
log = []
def step(name, action):
    try:
        action()
        log.append([name, "ok"])
    except Exception as error:
        log.append([name, f"{type(error).__name__}: {error}"])
setup = {"tier": 16, "waystone": 87, "map_mods": 5, "biome": "Forest", "city_type": "None",
         "ocean": True, "deli": False, "wisp": True, "irradiated": True, "atlas_master": "Jado",
         "tablets_used": 2, "plus_two_tablets": 1,
         "item_rarity": 40, "monster_rarity": 25, "pack_size": 15, "effectiveness": 30}
# Map 1: Expedition with remnants, a propagated chain, currency, kill readings.
step("map1 start", logger.start_map)
step("map1 settings", lambda: logger.save_settings({**setup, "activity_type": "Expedition"}))
step("map1 start currency", lambda: logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 5}]))
step("map1 remnant 1", lambda: logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3))
step("map1 chain 1", lambda: logger.commit_chain("Rage", "Time"))
step("map1 remnant 2", lambda: logger.commit_remnant("Perfect Exalted Orb x3", None, 3))
step("map1 chain 2", lambda: logger.commit_chain("Rage"))
step("map1 kill reading", lambda: kill_counts.save_snapshot(
    {**dict(zip(kill_counts.CATEGORIES, (300, 40, 9, 1))), "complete": True},
    context=kill_counts.capture_context("kills"), request_id=str(uuid.uuid4()),
    source_mode="kills", scope="map_final"))
step("map1 end currency", lambda: logger.save_currency_snapshot(
    "end", [{"name": "Chaos Orb", "quantity": 17}, {"name": "Divine Orb", "quantity": 2}]))
step("map1 finish", lambda: logger.finish_map(300, 40, 9, 2, unique=1))
# Map 2: Ritual with a Rite, pages with deferred rewards and counters.
step("map2 start", logger.start_map)
step("map2 settings", lambda: logger.save_settings({**setup, "activity_type": "Ritual", "biome": "Grass"}))
step("map2 rite", lambda: logger.new_ritual_rite("Comparison Rite"))
step("map2 rite affixes", lambda: logger.save_ritual_rite_affixes([{"text": "Comparison local wording", "kind": "local"}]))
step("map2 page 1", lambda: logger.save_ritual_page(
    [{"category": "Omen", "name": "Omen of Whittling", "quantity": 1, "tribute": 900, "source": "page 1", "deferred": False},
     {"category": "Item", "name": "Chaos Orb", "quantity": 3, "tribute": 120, "source": "page 1", "deferred": True}],
    raw_text="FAVOURS 5,000 TRIBUTE", tribute_available=5000, rerolls_remaining=6))
step("map2 page 2", lambda: logger.save_ritual_page(
    [{"category": "Item", "name": "Divine Orb", "quantity": 1, "tribute": 2500, "source": "page 2", "deferred": False}],
    raw_text="FAVOURS 4,100 TRIBUTE", tribute_available=4100, rerolls_remaining=5))
step("map2 finish", lambda: logger.finish_map(210, 30, 6, unique=0))
# Map 3 and 4: Abyss and Breach, currency only. Undo a New map once in between.
step("map3 start", logger.start_map)
# Undo of + New map reopens the Ritual map; finish it again before the next map.
step("map3 undo new map", logger.undo_last_action)
step("map2 finish again", lambda: logger.finish_map(210, 30, 6, unique=0))
step("map3 start again", logger.start_map)
step("map3 settings", lambda: logger.save_settings({**setup, "activity_type": "Abyss", "biome": "Desert"}))
step("map3 end currency", lambda: logger.save_currency_snapshot("end", [{"name": "Exalted Orb", "quantity": 4}]))
step("map3 finish", lambda: logger.finish_map(150, 12, 3, unique=0))
step("map4 start", logger.start_map)
step("map4 settings", lambda: logger.save_settings({**setup, "activity_type": "Breach", "biome": "Mountain"}))
step("map4 end currency", lambda: logger.save_currency_snapshot("end", [{"name": "Regal Orb", "quantity": 6}]))
exports = {}
for name in json.loads(sys.argv[4]):
    function = getattr(logger, name, None)
    exports[name] = None if function is None else function().decode("utf-8-sig")
exports["kill_counts.export_csv"] = kill_counts.export_csv().decode("utf-8-sig")
Path(out).write_text(json.dumps({"steps": log, "exports": exports}), encoding="utf-8")
'''


def run(tree, folder):
    """Run the scripted session against one tree; return its steps and exports."""
    profile, out = Path(folder) / "profile", Path(folder) / "session.json"
    profile.mkdir()
    # -I would ignore PYTHONHASHSEED (it implies -E), so isolate with -s -P and a clean environment.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    environment.update(QT_QPA_PLATFORM="offscreen", PYTHONHASHSEED="0")
    completed = subprocess.run([sys.executable, "-s", "-P", "-c", SESSION, str(tree), str(profile), str(out),
                                json.dumps(EXPORTS)], capture_output=True, text=True, env=environment)
    if completed.returncode:
        raise RuntimeError(f"Session failed on {tree}:\n{completed.stderr[-4000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


# Columns that identify what a row records, used to match rows before comparing cells.
IDENTITY = ("Type", "Map ID", "Expedition ID", "Remnant ID", "Reference", "Ritual Page", "Name", "Currency",
            "Inventory Phase", "Chain Step #")


def rows(text):
    """Parse one CSV export into a header list and rows."""
    table = list(csv.reader(io.StringIO(text or "")))
    return (table[0], table[1:]) if table else ([], [])


def compare(old, new):
    """Return every differing step and cell, each marked intended or unexpected."""
    differences = []
    for (name, old_result), (_, new_result) in zip(old["steps"], new["steps"]):
        if old_result != new_result:
            differences.append({"where": f"step {name}", "old": old_result, "new": new_result, "intended": False})
    for sheet in sorted(set(old["exports"]) | set(new["exports"])):
        old_text, new_text = old["exports"].get(sheet), new["exports"].get(sheet)
        if old_text is None or new_text is None:
            differences.append({"where": sheet, "old": old_text is not None, "new": new_text is not None,
                                "intended": False})
            continue
        old_header, old_rows = rows(old_text)
        new_header, new_rows = rows(new_text)
        old_dicts = [dict(zip(old_header, row)) for row in old_rows]
        new_dicts = [dict(zip(new_header, row)) for row in new_rows]
        # An item seen only as a deferred Ritual reward no longer gets its own column.
        deferred_only = {f"Item: {_reward_name(row)}" for row in old_dicts if _deferred_ritual(row)}
        deferred_only -= {f"Item: {_reward_name(row)}" for row in old_dicts if not _deferred_ritual(row)}
        for column in sorted(set(old_header) ^ set(new_header)):
            differences.append({"where": f"{sheet} column {column!r}", "old": column in old_header,
                                "new": column in new_header,
                                "intended": column in deferred_only and column not in new_header})
        shared = [column for column in old_header if column in new_header]
        keys = [column for column in IDENTITY if column in shared]
        old_keys = [tuple(row.get(column, "") for column in keys) for row in old_dicts]
        new_keys = [tuple(row.get(column, "") for column in keys) for row in new_dicts]
        pairs = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
            if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                pairs += zip(range(i1, i2), range(j1, j2))
                continue
            for index in range(i1, i2):
                differences.append({"where": f"{sheet} row only in old: {dict(zip(keys, old_keys[index]))}",
                                    "old": True, "new": False, "intended": _deferred_ritual(old_dicts[index])})
            for index in range(j1, j2):
                differences.append({"where": f"{sheet} row only in new: {dict(zip(keys, new_keys[index]))}",
                                    "old": False, "new": True, "intended": False})
        for old_index, new_index in pairs:
            old_row, new_row = old_dicts[old_index], new_dicts[new_index]
            index = new_index
            for column in shared:
                if old_row.get(column) != new_row.get(column):
                    # Rites belong to Ritual maps only; earlier releases also stamped other maps.
                    rite_off_ritual = (column.startswith("Ritual Rite ") and not new_row.get(column)
                                       and new_row.get("Activity Type", "Ritual") != "Ritual")
                    intended = (sheet, column) in INTENDED or column in KILL_COLUMNS or rite_off_ritual
                    differences.append({"where": f"{sheet} row {index + 2} {column!r}",
                                        "old": old_row.get(column), "new": new_row.get(column),
                                        "intended": intended})
    return differences


def _reward_name(row):
    """Read a Ritual reward's name from whichever column a sheet uses for it."""
    return row.get("Name") or row.get("Reward Name") or row.get("Item Name")


def _deferred_ritual(row):
    """A deferred Ritual reward row, which later releases no longer save."""
    return str(row.get("Deferred", "")).casefold() == "true" and (row.get("Type") in ("Ritual", "Item", "Omen")
                                                                  or "Ritual Page" in row)


def main(argv=None):
    """Compare two trees and exit 1 when any unexpected difference is found."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, default=ROOT)
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="poe2-compare-old-") as old_folder, \
            tempfile.TemporaryDirectory(prefix="poe2-compare-new-") as new_folder:
        old, new = run(arguments.old, old_folder), run(arguments.new, new_folder)
    differences = compare(old, new)
    unexpected = [entry for entry in differences if not entry["intended"]]
    for entry in differences:
        label = "intended  " if entry["intended"] else "UNEXPECTED"
        print(f"{label} {entry['where']}: {entry['old']!r} -> {entry['new']!r}")
    print(f"{len(differences)} differences, {len(unexpected)} unexpected; "
          f"steps: {sum(result == 'ok' for _, result in new['steps'])}/{len(new['steps'])} ok")
    if arguments.json:
        arguments.json.write_text(json.dumps({"old": old, "new": new, "differences": differences}, indent=1),
                                  encoding="utf-8")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
