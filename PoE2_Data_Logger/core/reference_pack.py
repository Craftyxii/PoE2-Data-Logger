"""Exchange editable reference tables and labelled image evidence in bounded ZIP packs.

The manifest carries recipe, name, seed and recognition references plus saved
seed screenshots. Import checks archive membership, image hashes, footprints
and relational references before committing supported database changes.
"""

from __future__ import annotations

import base64
from contextlib import closing
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, UnidentifiedImageError

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import store


FORMAT = "poe2-data-logger-references"
MAX_PACK = 256 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_IMAGE = 16 * 1024 * 1024
MAX_REVIEW_IMAGE = 500000
_DEFAULT_TABLES = ("recipes", "families", "aliases", "alias_keys", "alias_conflicts",
                   "seed_states", "affixes", "master_perks", "currency_items",
                   "ritual_names", "item_names")
_EXAMPLE_TABLES = ("reviewed_glyphs", "currency_icons", "omen_icons", "item_icons",
                   "review_icon_examples")


def _default_reference_rows(db):
    """Build current shipped references in memory using startup's seeding and repairs.

    Use the installed schema and an empty migration ledger. Never replay profile
    initialization, which also seeds historical rows and changes saved settings.
    """
    with gzip.open(logger.HERE / "bootstrap.json.gz", "rt", encoding="utf-8") as file:
        snapshot = json.load(file)
    with closing(sqlite3.connect(":memory:")) as defaults:
        defaults.row_factory = sqlite3.Row
        for table in (*_DEFAULT_TABLES, "meta"):
            schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                (table,)).fetchone()
            if schema is None:
                raise ValueError("The reference database is missing a required table.")
            defaults.execute(schema[0])
        logger._seed_reference_snapshot(defaults, snapshot)
        logger._initialize_references(defaults)
        return {table: [tuple(row) for row in defaults.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in _DEFAULT_TABLES}


def _restore_reference_table(db, table, rows):
    """Replace a changed catalog while preserving its original row order when undone.

    Identical catalogs require no writes or Undo entry. Deleting changed rows
    in reverse order lets the row journal reinsert their before-images in the
    original order, including catalogs whose primary key is a name.
    """
    existing = list(db.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid"))
    if [tuple(row)[1:] for row in existing] == rows:
        return
    db.executemany(f"DELETE FROM {table} WHERE rowid=?", ((row[0],) for row in reversed(existing)))
    if rows:
        placeholders = ",".join("?" for _ in rows[0])
        db.executemany(f"INSERT INTO {table} VALUES({placeholders})", rows)


def reset_to_defaults():
    """Atomically restore shipped OCR references and discard all local learning.

    Restore editable catalogs with current bundled corrections, then clear local
    glyphs and manual/reviewed icons. Preserve recorded maps, exports, commits,
    inventories, Ritual pages, scan IDs/screenshots, settings and ID sequences.
    Recognition reloads local rows for each scan; its cached assets are shipped
    files and require no invalidation. Repeated resets leave the same baseline.
    Journal reference changes so Undo first restores local learning, then can
    reverse earlier approvals without mismatched catalog rows. Return the
    restored row count and the number of discarded image examples.
    """
    with logger._connect(undo_label="Reset OCR references") as db:
        # Reject a busy writer before rebuilding the bundled reference snapshot.
        db.execute("BEGIN IMMEDIATE")
        defaults = _default_reference_rows(db)
        removed = 0
        for table in _EXAMPLE_TABLES:
            removed += db.execute(f"DELETE FROM {table}").rowcount
        for table, rows in defaults.items():
            _restore_reference_table(db, table, rows)
        # Explicitly revived retired names are learned catalog state, not a
        # profile preference. Leave every other metadata key untouched.
        db.execute("DELETE FROM meta WHERE key='active_retired_currency_names'")
        # Restored recipes reuse their identities; logged custom IDs remain
        # available without returning removed recipes to the active catalog.
        logger._sync_recipe_ids(db)
    return {"reference_rows": sum(map(len, defaults.values())), "removed_examples": removed}


def _name(raw, limit=200):
    """Require bounded nonempty string metadata without characters below ASCII space, then trim it."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > limit or any(
            ord(char) < 32 for char in raw):
        raise ValueError("Reference pack contains an invalid name.")
    return raw.strip()


def _retired_name_revivals(data):
    """Validate explicit retirement overrides without activating unmarked legacy catalog rows.

    Optional metadata in version-3 packs distinguishes deliberately re-added
    names from obsolete default rows retained by older profiles. Every override
    must name a retired label present in the pack's Currency or Omen catalog.
    """
    raw = data.get("active_retired_currency_names", [])
    if not isinstance(raw, list) or len(raw) > len(logger._RETIRED_CURRENCY_DEFAULTS):
        raise ValueError("Reference pack has invalid retired-name revival metadata.")
    registered = {row["name"].strip().replace("’", "'").casefold()
                  for table in ("currency_names", "omen_names") for row in data.get(table, [])
                  if isinstance(row, dict) and isinstance(row.get("name"), str)}
    normalized = set()
    for name in raw:
        name = _name(name, 160)
        key = name.replace("’", "'").casefold()
        if not logger._retired_currency_default(name) or key not in registered or key in normalized:
            raise ValueError("Reference pack has invalid retired-name revival metadata.")
        normalized.add(key)
    return sorted(normalized)


def _image(raw, limit=MAX_IMAGE, kind=None):
    """Verify bounded PNG/JPEG pixels and normalize legacy icon formats by their category.

    Screenshot bytes remain unchanged; currency/item icons become 96-pixel RGB
    PNGs, while Omen artwork retains proportions within a 512-pixel bound.
    """
    if len(raw) > limit:
        raise ValueError("Reference image is too large.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format not in ("PNG", "JPEG") or max(image.size) > 8192 or
                    image.width * image.height > store.MAX_IMAGE_PIXELS):
                raise ValueError("Reference must be a PNG/JPEG under 12 megapixels.")
            if kind and min(image.size) < 20:
                raise ValueError("Reference icons must be at least 20 pixels on each side.")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if kind:
                canonical_size = (image.size == (96, 96) if kind in ("currency", "item")
                                  else max(image.size) <= 512)
                byte_limit = 100000 if kind in ("currency", "item") else 300000
                if (image.format == "PNG" and image.mode == "RGB" and canonical_size and
                        len(raw) <= byte_limit):
                    return raw
                image = image.convert("RGB")
                if kind in ("currency", "item"):
                    image = image.resize((96, 96))
                else:
                    image.thumbnail((512, 512))
                    if min(image.size) < 20:
                        raise ValueError("Reference Omen icons must be at least 20 pixels on each side.")
                output = io.BytesIO()
                image.save(output, format="PNG", optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Reference image is damaged or is not a PNG or JPEG.") from error
    return raw


def _existing_recipe(db, name):
    """Resolve exact legacy spellings or one Unicode-canonical recipe without merging ambiguous rows."""
    return logger._recipe_catalog_name(db, name)


def _distinct_recipe_spellings(rows):
    """Preserve explicitly identified legacy case collisions while rejecting ambiguous old pack duplicates."""
    groups = {}
    for row in rows:
        name = _name(row["name"])
        groups.setdefault(name.casefold(), {})[name] = row.get("recipe_id")
    preserved = set()
    for spellings in groups.values():
        if len(spellings) < 2:
            continue
        primary = 0
        for name, identifier in spellings.items():
            if identifier == logger._recipe_identifier(name):
                primary += 1
            elif identifier != logger._recipe_collision_identifier(name):
                raise ValueError("Reference pack contains conflicting records: ambiguous case spellings without distinct recipe IDs.")
        if primary > 1:
            raise ValueError("Reference pack assigns the same recipe ID to distinct case spellings.")
        preserved.update(spellings)
    return preserved


def _recipe_name(db, raw):
    """Validate a reference name and require a canonical recipe already present in the database."""
    name = _existing_recipe(db, _name(raw))
    if name is None:
        raise ValueError("Reference pack references an unknown recipe.")
    return name


def _review_entry(entry):
    """Validate learned-icon metadata, hash/path shape and category-compatible cell footprint."""
    from PoE2_Data_Logger.core.review_learning import validate_name

    if (not isinstance(entry, dict) or entry.get("kind") not in ("currency", "omen", "item") or
            not re.fullmatch(r"learned/\d{4}\.png", str(entry.get("file"))) or
            not isinstance(entry.get("sha256"), str) or
            not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])):
        raise ValueError("Reference pack has an invalid learned icon entry.")
    validate_name(entry.get("name"), entry["kind"].title())
    columns, rows = entry.get("columns"), entry.get("rows")
    if (type(columns) is not int or type(rows) is not int or
            not 1 <= columns <= 2 or not 1 <= rows <= 4 or
            (entry["kind"] in ("currency", "omen") and (columns, rows) != (1, 1))):
        raise ValueError("Reference pack has an invalid learned icon footprint.")


def _review_image(raw, entry):
    """Check canonical learned PNG dimensions and visible artwork without changing its hashed bytes."""
    from PoE2_Data_Logger.core.review_learning import encode_example

    _image(raw, limit=MAX_REVIEW_IMAGE)
    with Image.open(io.BytesIO(raw)) as image:
        if (image.format != "PNG" or image.mode != "RGB" or
                image.size != (96 * entry["columns"], 96 * entry["rows"])):
            raise ValueError("Learned reference images must match their complete item footprint.")
        # Validate visible artwork without re-encoding the stored PNG: its hash
        # identifies a correction and must survive a pack round trip unchanged.
        encode_example({"image": image, "columns": entry["columns"], "rows": entry["rows"]})
    return raw


def save_export_file(folder):
    """Save a shareable reference ZIP under an unused name, preserving earlier packs and racing exports."""
    from PoE2_Data_Logger.core.export_files import ExportWriteError, write_export_files

    data = export_pack()
    directory = Path(folder)
    number = 1
    while True:
        suffix = "" if number == 1 else f"_{number}"
        destination = directory / f"PoE2_OCR_References{suffix}.zip"
        if destination.exists() or destination.is_symlink():
            number += 1
            continue
        try:
            write_export_files({destination: data}, replace=False)
        except ExportWriteError as error:
            # The exclusive reservation also protects against another app
            # instance claiming this filename after the existence check.
            if isinstance(error.__cause__, FileExistsError):
                number += 1
                continue
            raise
        return {"path": str(destination), "bytes": len(data)}


def export_pack():
    """Package references, recipe IDs, explicit name revivals and images from one read transaction.

    Verify stored image hashes and retain learned PNG bytes while enforcing the
    importer's asset budgets before loading or decoding further screenshots.
    Check the completed manifest and compressed archive against the same limits.
    """
    assets = {}
    asset_bytes = 0

    def check_asset(path, size):
        """Reject an asset before collection exceeds the archive's entry or byte budget."""
        if path not in assets and len(assets) + 2 > MAX_ENTRIES:
            raise ValueError("Reference pack has too many images. Export fewer than 2000 unique images.")
        if size > MAX_IMAGE:
            raise ValueError("Reference image is too large.")
        if asset_bytes - len(assets.get(path, b"")) + size > MAX_PACK:
            raise ValueError("Reference pack exceeds the import size limits. Remove some saved references first.")

    def retain_asset(path, raw):
        """Keep validated bytes and count each unique archive path only once."""
        nonlocal asset_bytes
        check_asset(path, len(raw))
        asset_bytes += len(raw) - len(assets.get(path, b""))
        assets[path] = raw

    with logger._connect() as db:
        db.execute("BEGIN")
        tables = {}
        for table, query in {
            "families": "SELECT id,top_socket,valid,recipes_json FROM families ORDER BY id",
            "recipes": "SELECT name,sockets,combo,level_band,category,source FROM recipes ORDER BY name",
            "aliases": "SELECT alias,target FROM aliases ORDER BY position",
            "seed_states": "SELECT family,sockets,seed_slot,seed_rune,rewards_json,status FROM seed_states ORDER BY family,sockets",
            "affixes": "SELECT name FROM affixes ORDER BY name",
            "master_perks": "SELECT master,name,tier,effect FROM master_perks ORDER BY master,name",
            "currency_names": "SELECT name FROM currency_items ORDER BY name",
            "omen_names": "SELECT name FROM ritual_names ORDER BY name",
            "item_names": "SELECT name FROM item_names ORDER BY name",
        }.items():
            tables[table] = [dict(row) for row in db.execute(query)]
        # ID derivation also covers fixtures/direct catalog inserts without
        # writing during this consistent reference export snapshot.
        recipe_ids = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids"))
        for recipe in tables["recipes"]:
            recipe["recipe_id"] = recipe_ids.get(recipe["name"]) or logger._recipe_identifier(recipe["name"])
        tables["active_retired_currency_names"] = logger._meta(db, "active_retired_currency_names", [])
        tables["active_retired_currency_names"] = _retired_name_revivals(tables)
        tables["glyphs"] = [{"sha256": row[0], "rune": row[1],
                              "vector": base64.b64encode(row[2]).decode("ascii")}
                             for row in db.execute("SELECT image_sha256,seed_rune,vector FROM reviewed_glyphs")]
        icons = []
        for kind, table in (("currency", "currency_icons"), ("omen", "omen_icons"), ("item", "item_icons")):
            for row in db.execute(f"SELECT name,image_png FROM {table} ORDER BY id"):
                path = f"icons/{len(icons):04d}.png"
                retain_asset(path, bytes(row[1]))
                icons.append({"kind": kind, "name": row[0], "file": path,
                              "sha256": hashlib.sha256(assets[path]).hexdigest()})
        tables["icons"] = icons
        learned = []
        for row in db.execute("SELECT name,kind,columns,rows,image_png,image_sha256 "
                              "FROM review_icon_examples ORDER BY id"):
            path = f"learned/{len(learned):04d}.png"
            entry = {"name": row["name"], "kind": row["kind"],
                     "columns": row["columns"], "rows": row["rows"],
                     "file": path, "sha256": row["image_sha256"]}
            check_asset(path, len(row["image_png"]))
            _review_entry(entry)
            raw = _review_image(bytes(row["image_png"]), entry)
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("Saved learned icon hash does not match its record.")
            retain_asset(path, raw)
            learned.append(entry)
        tables["review_icon_examples"] = learned
        scans = []
        for row in db.execute("SELECT file_name,image_name,image_sha256,sockets,seed_slot,"
                              "seed_rune,family,rewards_json,mapping_status FROM scans ORDER BY id"):
            if not re.fullmatch(r"[a-f0-9]{64}\.(png|jpg)", row["image_name"]):
                raise ValueError("Saved screenshot has an invalid local filename.")
            path = "screens/" + row["image_name"]
            screenshot = store.DATA_DIR / "images" / row["image_name"]
            check_asset(path, screenshot.stat().st_size)
            raw = screenshot.read_bytes()
            check_asset(path, len(raw))
            _image(raw)
            if hashlib.sha256(raw).hexdigest() != row["image_sha256"]:
                raise ValueError("Saved screenshot hash does not match its record.")
            retain_asset(path, raw)
            scans.append({"file_name": row["file_name"], "file": path,
                          "sha256": row["image_sha256"], "sockets": row["sockets"],
                          "slot": row["seed_slot"], "rune": row["seed_rune"],
                          "family": row["family"], "rewards_json": row["rewards_json"],
                          "status": row["mapping_status"]})
        tables["scans"] = scans
    manifest = {"format": FORMAT, "version": 3, "data": tables}
    if len(assets) + 1 > MAX_ENTRIES:
        raise ValueError("Reference pack has too many images. Export fewer than 2000 unique images.")
    manifest_raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(manifest_raw) > 5_000_000 or len(manifest_raw) + asset_bytes > MAX_PACK:
        raise ValueError("Reference pack exceeds the import size limits. Remove some saved references first.")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", manifest_raw)
        for path, raw in assets.items():
            archive.writestr(path, raw)
    if output.tell() > MAX_PACK:
        raise ValueError("Reference pack exceeds 256 MB. Remove some saved screenshots first.")
    return output.getvalue()


def _inspect(path):
    """Read a bounded supported pack and require exactly its declared, hash-verified assets.

    Reject duplicate/encrypted entries, conflicting learned labels, invalid
    explicit retirement overrides and malformed images without extracting paths.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_PACK:
        raise ValueError("Choose a reference pack under 256 MB.")
    with ZipFile(path) as archive:
        info = archive.infolist()
        names = [entry.filename for entry in info]
        if (len(info) > MAX_ENTRIES or len(names) != len(set(names)) or
                sum(entry.file_size for entry in info) > MAX_PACK or
                not names or "manifest.json" not in names or
                any(entry.file_size > MAX_IMAGE and entry.filename != "manifest.json"
                    for entry in info) or
                any(entry.flag_bits & 1 for entry in info)):
            raise ValueError("Reference pack has too many, duplicate, large, or encrypted entries.")
        if archive.getinfo("manifest.json").file_size > 5_000_000:
            raise ValueError("Reference pack manifest is too large.")
        manifest = json.loads(archive.read("manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT or
                manifest.get("version") not in (1, 2, 3) or not isinstance(manifest.get("data"), dict)):
            raise ValueError("This is not a supported PoE2 reference pack.")
        data = manifest["data"]
        data.setdefault("item_names", [])
        if manifest["version"] < 3:
            data.setdefault("review_icon_examples", [])
        required = ("families", "recipes", "aliases", "seed_states", "affixes",
                    "master_perks", "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans",
                    "review_icon_examples")
        if any(not isinstance(data.get(key), list) or len(data[key]) > 10000 for key in required):
            raise ValueError("Reference pack has invalid database records.")
        # Existing packs omit this optional section and retain the previous retirement behavior.
        data["active_retired_currency_names"] = _retired_name_revivals(data)
        expected = {"manifest.json"}
        icon_files = set()
        for icon in data["icons"]:
            if not isinstance(icon, dict) or icon.get("kind") not in ("currency", "omen", "item") or not re.fullmatch(
                    r"icons/\d{4}\.png", str(icon.get("file"))):
                raise ValueError("Reference pack has an invalid icon entry.")
            if icon["file"] in icon_files:
                raise ValueError("Reference pack has duplicate icon files.")
            icon_files.add(icon["file"])
            expected.add(icon["file"])
        learned_labels = {}
        for entry in data["review_icon_examples"]:
            _review_entry(entry)
            if entry["file"] in icon_files:
                raise ValueError("Reference pack has duplicate learned icon files.")
            icon_files.add(entry["file"])
            expected.add(entry["file"])
            key = (entry["columns"], entry["rows"], entry.get("sha256"))
            label = (entry["name"].strip().casefold(), entry["kind"])
            if key in learned_labels and learned_labels[key] != label:
                raise ValueError("Reference pack has conflicting learned icon labels.")
            learned_labels[key] = label
        for scan in data["scans"]:
            if not isinstance(scan, dict) or not re.fullmatch(
                    r"screens/[a-f0-9]{64}\.(png|jpg)", str(scan.get("file"))):
                raise ValueError("Reference pack has an invalid screenshot entry.")
            expected.add(scan["file"])
        if set(names) != expected:
            raise ValueError("Reference pack contains unexpected or missing files.")
        assets = {}
        for entry in data["icons"] + data["scans"] + data["review_icon_examples"]:
            raw = archive.read(entry["file"])
            if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                raise ValueError("Reference image hash does not match its label.")
            if entry["file"].startswith("screens/") and Path(entry["file"]).stem != entry["sha256"]:
                raise ValueError("Reference screenshot filename does not match its hash.")
            kind = entry["kind"] if entry["file"].startswith("icons/") else None
            assets[entry["file"]] = (_review_image(raw, entry) if entry["file"].startswith("learned/")
                                     else _image(raw, kind=kind))
    return data, assets


def import_pack(path, replace_existing=False):
    """Import references and verify supplied recipe IDs atomically, accepting older packs without IDs.

    Optional replacement updates supported reference records; recipe/seed links,
    labels, explicit retired-name revivals and glyph vectors are checked. Apply
    revivals in the same transaction, removing new screenshots if import fails.
    Journal changed reference rows so an import can be undone before earlier
    approvals that taught the same artwork or catalog identities.
    """
    data, assets = _inspect(path)
    counts = {key: 0 for key in ("recipes", "families", "aliases", "seed_states", "affixes",
                                  "master_perks", "currency_names", "omen_names", "item_names", "glyphs",
                                  "currency_icons", "omen_icons", "item_icons", "review_icon_examples", "scans")}
    created = []
    try:
        with logger._connect(undo_label="Import OCR references") as db:
            db.execute("BEGIN IMMEDIATE")
            changed_recipes, changed_families = set(), set()
            incoming_recipes = {}
            distinct_spellings = _distinct_recipe_spellings(data["recipes"])
            for row in data["recipes"]:
                name = _name(row["name"])
                supplied_id = row.get("recipe_id")
                legacy_spelling = (name in distinct_spellings or
                                   supplied_id == logger._recipe_collision_identifier(name))
                if not legacy_spelling:
                    name = logger._recipe_catalog_name(db, name, include_retired=True) or name
                # Older packs omit IDs; current packs must carry the stable
                # identity for this canonical name. Explicit migrated case
                # collisions keep their exact spelling and distinct identity.
                allowed_ids = {logger._recipe_identifier(name)}
                if legacy_spelling:
                    allowed_ids.add(logger._recipe_collision_identifier(name))
                if supplied_id is not None and supplied_id not in allowed_ids:
                    raise ValueError("Reference recipe ID does not match its canonical name.")
                registered = db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?", (name,)).fetchone()
                if registered and supplied_id is not None and registered[0] != supplied_id:
                    raise ValueError("Reference recipe ID conflicts with its existing identity.")
                sockets = logger._integer(row["sockets"], "Recipe sockets", 2, 10)
                combo = _name(row["combo"], 500)
                runes = [part.strip() for part in combo.split("+")]
                if len(runes) != sockets or any(not rune for rune in runes):
                    raise ValueError("Imported recipe needs one rune per socket.")
                values = (name, sockets, " + ".join(runes),
                          None if row.get("level_band") is None else str(row["level_band"])[:100],
                          None if row.get("category") is None else str(row["category"])[:100],
                          None if row.get("source") is None else str(row["source"])[:200])
                if name in incoming_recipes:
                    if incoming_recipes[name] != values:
                        raise ValueError("Reference pack contains conflicting records for the same recipe.")
                    continue
                incoming_recipes[name] = values
                op = "REPLACE" if replace_existing else "IGNORE"
                result = db.execute(f"INSERT OR {op} INTO recipes VALUES(?,?,?,?,?,?)", values)
                counts["recipes"] += result.rowcount
                if result.rowcount:
                    changed_recipes.add(name)
                logger._ensure_recipe_id(db, name, preferred_id=supplied_id)
            for row in data["families"]:
                number = logger._integer(row["id"], "Family", 1, 9999)
                top = logger._integer(row["top_socket"], "Top socket", 3, 10)
                recipes = json.loads(row["recipes_json"])
                if not isinstance(recipes, list) or not recipes or len(recipes) > 100:
                    raise ValueError("Imported family references an unknown recipe.")
                recipes = [_recipe_name(db, name) for name in recipes]
                if len(set(recipes)) != len(recipes) or any(db.execute(
                        "SELECT sockets FROM recipes WHERE name=?", (name,)).fetchone()[0] > top
                        for name in recipes):
                    raise ValueError("Imported family has duplicate recipes or an incorrect Top Socket.")
                op = "REPLACE" if replace_existing else "IGNORE"
                result = db.execute(f"INSERT OR {op} INTO families VALUES(?,?,?,?)",
                                    (number, top, int(bool(row["valid"])), logger._dump(recipes)))
                counts["families"] += result.rowcount
                if result.rowcount:
                    changed_families.add(number)
            for family in db.execute("SELECT top_socket,recipes_json FROM families"):
                if any(db.execute("SELECT sockets FROM recipes WHERE name=?", (name,)).fetchone()[0]
                       > family["top_socket"] for name in json.loads(family["recipes_json"])):
                    raise ValueError("Imported recipe exceeds an existing family's Top Socket.")
            for kind, table, limit in (("affixes", "affixes", 120),
                                       ("omen_names", "ritual_names", 160),
                                       ("currency_names", "currency_items", 120),
                                       ("item_names", "item_names", 120)):
                for row in data[kind]:
                    raw_name = row["name"]
                    name_limit = (160 if kind == "currency_names" and isinstance(raw_name, str) and
                                  logger._catalog_name(db, "ritual_names", raw_name.strip()) else limit)
                    name = _name(raw_name, name_limit)
                    other = {"currency_items": "item_names", "item_names": "currency_items"}.get(table)
                    if other and logger._catalog_name(db, other, name) is not None:
                        raise ValueError("An inventory name cannot belong to both Currency and Item databases.")
                    if logger._catalog_name(db, table, name) is not None:
                        continue
                    result = db.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES(?)",
                                        (name,))
                    counts[kind] += result.rowcount
            for name in data["active_retired_currency_names"]:
                logger._retain_local_catalog_name(db, name)
            for row in data["master_perks"]:
                master = _name(row["master"], 50)
                if master not in ("Jado", "Doryani", "Hilda"):
                    raise ValueError("Invalid Atlas Master reference.")
                op = "REPLACE" if replace_existing else "IGNORE"
                result = db.execute(f"INSERT OR {op} INTO master_perks VALUES(?,?,?,?)",
                                    (master, _name(row["name"], 160),
                                     str(row.get("tier") or "")[:100],
                                     str(row.get("effect") or "")[:500]))
                counts["master_perks"] += result.rowcount
            aliases = {}
            for row in db.execute("SELECT position,alias,target FROM aliases ORDER BY position"):
                aliases.setdefault(row["alias"].lower(), []).append((row["position"], row["target"]))
            incoming = [(_name(row["alias"], 200), _recipe_name(db, row["target"]))
                        for row in data["aliases"]]
            incoming_targets = {}
            for alias, target in incoming:
                incoming_targets.setdefault(alias.lower(), set()).add(target)
            next_alias = db.execute("SELECT COALESCE(MAX(position),0) FROM aliases").fetchone()[0]
            for alias, target in incoming:
                if not db.execute("SELECT 1 FROM recipes WHERE name=?", (target,)).fetchone():
                    raise ValueError("Imported alias references an unknown recipe.")
                key = alias.lower()
                hits = aliases.get(key, [])
                if any(existing == target for _, existing in hits):
                    continue
                if hits and replace_existing and len(incoming_targets[key]) == 1:
                    for position, _ in hits:
                        db.execute("UPDATE aliases SET target=? WHERE position=?", (target, position))
                    aliases[key] = [(position, target) for position, _ in hits]
                    counts["aliases"] += len(hits)
                else:
                    next_alias += 1
                    db.execute("INSERT INTO aliases(position,alias,target) VALUES(?,?,?)",
                               (next_alias, alias, target))
                    aliases.setdefault(key, []).append((next_alias, target))
                    counts["aliases"] += 1
            if counts["aliases"]:
                logger.rebuild_alias_keys(db)
            for row in data["seed_states"]:
                family = logger._integer(row["family"], "Seed family", 1, 9999)
                sockets = logger._integer(row["sockets"], "Seed sockets", 3, 10)
                slot = _name(row["seed_slot"], 4)
                if slot not in ["P*", *(f"P{i}" for i in range(1, sockets + 1))]:
                    raise ValueError("Imported seed position is outside its socket bar.")
                rewards = json.loads(row["rewards_json"])
                if not isinstance(rewards, list) or len(rewards) > 100:
                    raise ValueError("Imported seed references an unknown recipe.")
                rewards = [_recipe_name(db, name) for name in rewards]
                parent = db.execute("SELECT top_socket,recipes_json FROM families WHERE id=?", (family,)).fetchone()
                if not parent:
                    raise ValueError("Imported seed references an unknown family.")
                members = set(json.loads(parent["recipes_json"]))
                if sockets > parent["top_socket"] or len(set(rewards)) != len(rewards) or any(
                        name not in members or db.execute("SELECT sockets FROM recipes WHERE name=?",
                                                         (name,)).fetchone()[0] > sockets
                        for name in rewards):
                    raise ValueError("Imported seed rewards disagree with its family or socket stage.")
                op = "REPLACE" if replace_existing else "IGNORE"
                result = db.execute(f"INSERT OR {op} INTO seed_states VALUES(?,?,?,?,?,?)",
                                    (family, sockets, slot, _name(row["seed_rune"], 80),
                                     logger._dump(rewards), _name(row["status"], 40)))
                counts["seed_states"] += result.rowcount
            logger._check_seed_references(db, changed_families, changed_recipes)
            for row in data["glyphs"]:
                sha = str(row["sha256"])
                if not re.fullmatch(r"[a-f0-9]{64}", sha):
                    raise ValueError("Invalid reviewed rune hash.")
                vector = base64.b64decode(row["vector"], validate=True)
                if len(vector) != 1296 * 4:
                    raise ValueError("Invalid reviewed rune vector.")
                import numpy as np
                values = np.frombuffer(vector, dtype=np.float32)
                if not np.isfinite(values).all():
                    raise ValueError("Reviewed rune vectors must contain finite numbers.")
                if np.linalg.norm(values.astype(np.float64)) > 1.00001:
                    raise ValueError("Reviewed rune vectors must have a norm of at most one.")
                op = "REPLACE" if replace_existing else "IGNORE"
                result = db.execute(f"INSERT OR {op} INTO reviewed_glyphs VALUES(?,?,?)",
                                    (sha, _name(row["rune"], 80), vector))
                counts["glyphs"] += result.rowcount
            known_icons = {kind: {(row[0], hashlib.sha256(row[1]).hexdigest()) for row in
                                  db.execute(f"SELECT name,image_png FROM {table}")}
                           for kind, table in (("currency", "currency_icons"), ("omen", "omen_icons"),
                                                ("item", "item_icons"))}
            for row in data["icons"]:
                kind, name = row["kind"], _name(row["name"], 160)
                sha = hashlib.sha256(assets[row["file"]]).hexdigest()
                names_table, icons_table = {"currency": ("currency_items", "currency_icons"),
                                            "omen": ("ritual_names", "omen_icons"),
                                            "item": ("item_names", "item_icons")}[kind]
                # Use the same Unicode identity as the catalog-name import above.
                canonical = logger._catalog_name(db, names_table, name)
                if canonical is None:
                    raise ValueError("Icon label is missing from its name database.")
                name = canonical
                if (name, sha) not in known_icons[kind]:
                    db.execute(f"INSERT INTO {icons_table} "
                               "(name,image_png,recorded_at) VALUES(?,?,?)",
                               (name, assets[row["file"]], logger._now()))
                    known_icons[kind].add((name, sha))
                    counts[kind + "_icons"] += 1
            for row in data["review_icon_examples"]:
                name, kind = logger._canonical_registered_name(
                    db, row["name"], row["kind"].title(), register=False)
                names_table = {"currency": "currency_items", "omen": "ritual_names", "item": "item_names"}[kind]
                if kind != row["kind"] or logger._catalog_name(db, names_table, name) is None:
                    raise ValueError("Learned icon label is missing from its name database or has the wrong category.")
                if kind == "omen":
                    currency = logger._catalog_name(db, "currency_items", name)
                    logger._canonical_registered_name(db, name, "Omen")
                    counts["currency_names"] += int(currency is None)
                key = (row["columns"], row["rows"], row["sha256"])
                existing = db.execute("SELECT id,name,kind FROM review_icon_examples "
                                      "WHERE columns=? AND rows=? AND image_sha256=?", key).fetchone()
                if existing:
                    if replace_existing and (existing["name"], existing["kind"]) != (name, kind):
                        db.execute("UPDATE review_icon_examples SET name=?,kind=?,recorded_at=? WHERE id=?",
                                   (name, kind, logger._now(), existing["id"]))
                        counts["review_icon_examples"] += 1
                    continue
                db.execute("INSERT INTO review_icon_examples"
                           "(name,kind,columns,rows,image_png,image_sha256,recorded_at) VALUES(?,?,?,?,?,?,?)",
                           (name, kind, *key[:2], assets[row["file"]], key[2], logger._now()))
                counts["review_icon_examples"] += 1
            images = store.DATA_DIR / "images"
            images.mkdir(parents=True, exist_ok=True)
            for row in data["scans"]:
                sha = row["sha256"]
                sockets = logger._integer(row["sockets"], "Screenshot sockets", 3, 10)
                slot = _name(row["slot"], 4)
                if slot not in [f"P{i}" for i in range(1, sockets + 1)]:
                    raise ValueError("Invalid screenshot seed position.")
                existing = db.execute("SELECT 1 FROM scans WHERE image_sha256=?", (sha,)).fetchone()
                raw = assets[row["file"]]
                file_name = Path(_name(row["file_name"], 200).replace("\\", "/")).name
                image_name = Path(row["file"]).name
                destination = images / image_name
                rewards = json.loads(row["rewards_json"])
                if not isinstance(rewards, list) or len(rewards) > 100:
                    raise ValueError("Invalid screenshot rewards.")
                rewards = [_name(reward, 200) for reward in rewards]
                if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                    existed = destination.exists()
                    fd, staged = tempfile.mkstemp(dir=images, suffix=".tmp")
                    try:
                        with os.fdopen(fd, "wb") as output:
                            output.write(raw)
                        os.replace(staged, destination)
                    finally:
                        Path(staged).unlink(missing_ok=True)
                    if not existed:
                        created.append(destination)
                if existing:
                    continue
                db.execute("INSERT INTO scans(recorded_at,file_name,image_name,image_sha256,"
                           "sockets,seed_slot,seed_rune,family,rewards_json,mapping_status) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (logger._now(), file_name, image_name, sha, sockets, slot,
                            _name(row["rune"], 80), str(row.get("family") or "")[:80],
                            logger._dump(rewards), _name(row["status"], 40)))
                counts["scans"] += 1
    except Exception:
        for file in created:
            # Another writer may have accepted the shared image after rollback.
            store._remove_failed_scan_image(file)
        raise
    return counts
