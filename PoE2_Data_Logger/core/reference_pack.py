from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
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


def _name(raw, limit=200):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > limit or any(
            ord(char) < 32 for char in raw):
        raise ValueError("Reference pack contains an invalid name.")
    return raw.strip()


def _image(raw, limit=MAX_IMAGE, kind=None):
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
    matches = db.execute("SELECT name FROM recipes WHERE name=? COLLATE NOCASE", (name,)).fetchall()
    if len(matches) > 1:
        raise ValueError("Recipe database contains conflicting case spellings.")
    return matches[0][0] if matches else None


def _recipe_name(db, raw):
    name = _existing_recipe(db, _name(raw))
    if name is None:
        raise ValueError("Reference pack references an unknown recipe.")
    return name


def _review_entry(entry):
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


def export_pack():
    assets = {}
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
        tables["glyphs"] = [{"sha256": row[0], "rune": row[1],
                              "vector": base64.b64encode(row[2]).decode("ascii")}
                             for row in db.execute("SELECT image_sha256,seed_rune,vector FROM reviewed_glyphs")]
        icons = []
        for kind, table in (("currency", "currency_icons"), ("omen", "omen_icons"), ("item", "item_icons")):
            for row in db.execute(f"SELECT name,image_png FROM {table} ORDER BY id"):
                path = f"icons/{len(icons):04d}.png"
                assets[path] = bytes(row[1])
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
            _review_entry(entry)
            raw = _review_image(bytes(row["image_png"]), entry)
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("Saved learned icon hash does not match its record.")
            assets[path] = raw
            learned.append(entry)
        tables["review_icon_examples"] = learned
        scans = []
        for row in db.execute("SELECT file_name,image_name,image_sha256,sockets,seed_slot,"
                              "seed_rune,family,rewards_json,mapping_status FROM scans ORDER BY id"):
            if not re.fullmatch(r"[a-f0-9]{64}\.(png|jpg)", row["image_name"]):
                raise ValueError("Saved screenshot has an invalid local filename.")
            raw = (store.DATA_DIR / "images" / row["image_name"]).read_bytes()
            _image(raw)
            if hashlib.sha256(raw).hexdigest() != row["image_sha256"]:
                raise ValueError("Saved screenshot hash does not match its record.")
            path = "screens/" + row["image_name"]
            assets[path] = raw
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
    if len(manifest_raw) > 5_000_000 or len(manifest_raw) + sum(map(len, assets.values())) > MAX_PACK:
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
    data, assets = _inspect(path)
    counts = {key: 0 for key in ("recipes", "families", "aliases", "seed_states", "affixes",
                                  "master_perks", "currency_names", "omen_names", "item_names", "glyphs",
                                  "currency_icons", "omen_icons", "item_icons", "review_icon_examples", "scans")}
    created = []
    try:
        with logger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed_recipes, changed_families = set(), set()
            incoming_recipes = {}
            for row in data["recipes"]:
                name = _name(row["name"])
                name = _existing_recipe(db, name) or name
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
                canonical = db.execute(f"SELECT name FROM {names_table} WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                if canonical is None:
                    raise ValueError("Icon label is missing from its name database.")
                name = canonical[0]
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
            file.unlink(missing_ok=True)
        raise
    return counts
