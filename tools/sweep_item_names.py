"""Scan every catalog item on synthetic Ritual pages and inventories and report wrong names.

Each currency/Omen icon and each unique is drawn at its in-game size on a real
empty Ritual cell (1080p, 1440p, JPEG and gray looks) and each currency icon on
a real empty inventory cell, then read by the same per-reward code as a scan.
A held reward is safe; a name other than the drawn item's is a failure.

    python tools/sweep_item_names.py [--looks native 1440p jpeg gray] [--json OUT]

Exits 1 when any item is named as something else. A full run takes about ten
minutes per look.
"""

import argparse
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RITUAL_PAGE = ROOT / "tests" / "fixtures" / "ritual_pages_hover" / "124949.png"
INVENTORY = ROOT / "tests" / "fixtures" / "currency_training" / "inventory_120330.png"
COLUMNS, ROWS = 12, 10


def catalog_items(currency_ocr, unique_items):
    """Yield (kind, label, accepted names, footprint, RGBA art) for every catalog item."""
    catalog = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text(encoding="utf-8"))
    size = catalog["size"]
    for icon in catalog["icons"]:
        art = Image.fromarray(np.array(icon["rgba"], dtype=np.uint8).reshape(size, size, 4), "RGBA")
        yield "currency", icon["members"][0], set(icon["members"]), (1, 1), art
    manifest = json.loads((unique_items.ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["items"]:
        art = Image.open(unique_items.ASSET_ROOT / row["icon"]).convert("RGBA")
        yield "unique", row["name"], {row["name"], *row.get("shared_names", [])}, (row["columns"], row["rows"]), art


def ritual_pages(entries, pitch, tile):
    """Pack entries column by column onto 12x10 Ritual grids; yield (image, grid, placed)."""
    origin = 6
    width, height = round(origin * 2 + COLUMNS * pitch), round(origin * 2 + ROWS * pitch)

    def box(column, row, columns=1, rows=1):
        return (round(origin + column * pitch), round(origin + row * pitch),
                round(origin + (column + columns) * pitch), round(origin + (row + rows) * pitch))

    queue = list(entries)
    while queue:
        image = Image.new("RGB", (width, height), (8, 8, 10))
        for row in range(ROWS):
            for column in range(COLUMNS):
                cell = box(column, row)
                image.paste(tile.resize((cell[2] - cell[0], cell[3] - cell[1])), cell[:2])
        occupied, rewards, placed, rest = set(), [], [], []
        for entry in queue:
            columns, rows = entry[3]
            spot = next(((column, row) for column in range(COLUMNS - columns + 1) for row in range(ROWS - rows + 1)
                         if not any((column + dx, row + dy) in occupied
                                    for dx in range(columns) for dy in range(rows))), None)
            if spot is None:
                rest.append(entry)
                continue
            column, row = spot
            occupied |= {(column + dx, row + dy) for dx in range(columns) for dy in range(rows)}
            area = box(column, row, columns, rows)
            art = entry[4]
            scale = min((area[2] - area[0]) / art.width, (area[3] - area[1]) / art.height)
            shown = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))),
                               Image.Resampling.LANCZOS)
            image.paste(shown, (area[0] + (area[2] - area[0] - shown.width) // 2,
                                area[1] + (area[3] - area[1] - shown.height) // 2), shown)
            rewards.append({"slots": sorted((row + dy) * COLUMNS + column + dx + 1
                                            for dy in range(rows) for dx in range(columns)), "box": area})
            placed.append(entry)
        yield image, {"pitch": pitch, "rewards": rewards}, placed
        queue = rest


def sweep_ritual(look, modules, names):
    """Read every catalog item on Ritual pages in one look."""
    item_ocr, ritual_grid, currency_ocr, unique_items = modules
    with Image.open(RITUAL_PAGE) as source:
        page = source.convert("RGB")
    grid = ritual_grid.detect_reward_grid(page)
    used = {slot for reward in grid["rewards"] for slot in reward["slots"]}
    slot = next(slot for slot in range(61, 121) if slot not in used)
    left, top, pitch = *grid["bounds"][:2], grid["pitch"]
    column, row = (slot - 1) % COLUMNS, (slot - 1) // COLUMNS
    tile = page.crop((round(left + column * pitch), round(top + row * pitch),
                      round(left + (column + 1) * pitch), round(top + (row + 1) * pitch)))
    results = []
    entries = list(catalog_items(currency_ocr, unique_items))
    for image, grid, placed in ritual_pages(entries, pitch * (4 / 3 if look == "1440p" else 1), tile):
        if look == "gray":
            image = image.convert("L").convert("RGB")
        if look == "jpeg":
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            image = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
        for entry, row in zip(placed, item_ocr._ritual_grid_items(image, grid, [], [], names, ())):
            results.append(("ritual " + look, entry[1], entry[2], row.get("name") or ""))
    return results


def sweep_inventory(modules):
    """Read every currency icon on synthetic 12x5 inventories."""
    item_ocr, _ritual_grid, currency_ocr, _unique_items = modules
    with Image.open(INVENTORY) as source:
        real = item_ocr.inventory_grid(source.convert("RGB"))
    tile = item_ocr.inventory_cell(real, next(slot for slot in range(60, 0, -1)
                                              if currency_ocr._inventory_empty(item_ocr.inventory_cell(real, slot))))
    catalog = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text(encoding="utf-8"))
    size, icons, (width, height) = catalog["size"], catalog["icons"], real.size
    results = []
    for start in range(0, len(icons), 60):
        page = Image.new("RGB", (width, height))
        batch = icons[start:start + 60]
        for slot in range(1, 61):
            column, row = (slot - 1) % 12, (slot - 1) // 12
            box = (round(column * width / 12), round(row * height / 5),
                   round((column + 1) * width / 12), round((row + 1) * height / 5))
            page.paste(tile.resize((box[2] - box[0], box[3] - box[1])), box[:2])
            if slot <= len(batch):
                art = Image.fromarray(np.array(batch[slot - 1]["rgba"], dtype=np.uint8).reshape(size, size, 4), "RGBA")
                art = art.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
                page.paste(art, box[:2], art)
        page.info["poe2_inventory_aligned"] = True
        read = item_ocr.scan_inventory_grid(page)
        rows = {row["slot"]: row for row in read["items"]}
        for slot, icon in enumerate(batch, 1):
            results.append(("inventory", icon["members"][0], set(icon["members"]), rows.get(slot, {}).get("name") or ""))
    return results


def main():
    """Run the requested looks in a disposable profile and report wrong names."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--looks", nargs="+", default=["native", "1440p", "jpeg", "gray", "inventory"],
                        choices=["native", "1440p", "jpeg", "gray", "inventory"])
    parser.add_argument("--json", help="write every read to this file")
    arguments = parser.parse_args()
    logging.disable(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="poe2-name-sweep-") as profile:
        os.environ["RUNESHAPE_SCAN_DATA_DIR"] = profile
        from PoE2_Data_Logger.core import logger_store as logger
        logger.initialize()
        from PoE2_Data_Logger.ocr import item_ocr, ritual_grid, currency_ocr, unique_items
        modules = item_ocr, ritual_grid, currency_ocr, unique_items
        reads = []
        for look in arguments.looks:
            reads += sweep_inventory(modules) if look == "inventory" else sweep_ritual(look, modules, logger.ritual_names())
    wrong = [(look, item, name) for look, item, accepted, name in reads if name and name not in accepted]
    for look in dict.fromkeys(look for look, *_ in reads):
        rows = [read for read in reads if read[0] == look]
        named = sum(bool(name) and name in accepted for _, _, accepted, name in rows)
        print(f"{look}: {len(rows)} items, {named} named correctly, "
              f"{sum(not name for *_, name in rows)} held, {sum(read[0] == look for read in wrong)} wrong")
    for look, item, name in wrong:
        print(f"WRONG {look}: {item} read as {name}")
    if arguments.json:
        Path(arguments.json).write_text(json.dumps([[look, item, sorted(accepted), name]
                                                    for look, item, accepted, name in reads]), encoding="utf-8")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
