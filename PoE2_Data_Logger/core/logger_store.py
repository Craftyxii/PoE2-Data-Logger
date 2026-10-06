from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, affix_key, affix_unit, modifier_value, tablet_random_mod_counts


HERE = Path(__file__).resolve().parent.parent
_INIT_LOCK = threading.Lock()
_READY = False
BIOMES = ("None", "Water", "Mountain", "Grass", "Forest", "Swamp", "Desert", "Ocean", "Island")
CITY_TYPES = ("None", "Faridun", "Ezomyte", "Vaal")
ALDUR_AFFIXES = ("None", "All +5", "All +6", "All +7", "Lucky",
                 "1 Guaranteed 7", "1 Guaranteed 8", "1 Guaranteed 9")
WAYSTONE_DEFAULTS = {"tier": 15, "waystone": 0, "map_mods": 0, "waystone_name": "",
                     "waystone_mods": [], "item_rarity": None, "monster_rarity": None,
                     "pack_size": None, "effectiveness": None}
BASE_EXTRA_HEADERS = ("Item Rarity %", "Monster Rarity %", "Pack Size %",
                        "Effectiveness %", "Waystone Name", "Biome", "City Type", "Ocean Map",
                        "Waystone Modifiers", *(f"Map Mod {n}" for n in range(1, 11)),
                        *(f"Tablet {n} Modifiers" for n in range(1, 5)), "Scan Commit #")
TABLET_DETAIL_HEADERS = (*(label for tablet in range(1, 5) for slot in range(1, 5)
                           for label in (f"Tablet {tablet} Mod {slot} Value", f"Tablet {tablet} Mod {slot} Unit")),
                        *(f"Tablet {tablet} Random Modifiers" for tablet in range(1, 5)))
EXPORT_EXTRA_HEADERS = (*BASE_EXTRA_HEADERS, *TABLET_DETAIL_HEADERS)
TABLET_EXPORT_HEADERS = tuple(label for tablet in range(1, 5) for slot in range(1, 5)
                             for label in (f"Tablet {tablet} Mod {slot} Affix", f"Tablet {tablet} Mod {slot} %"))
CONFIG_EXPORT_HEADERS = ("Tier", "Area Level", "Base Map Mods", "Map Mods", "# +2 Mod Tablets",
                         "Tablet Mods", "Total Mods", "Aldur's Saga Affix", "Atlas Master",
                         "Perk 1", "Perk 2", "Perk 3", "Perk 4", "Master +Mods", "Irradiated",
                         "Waystone %", "Tablets Used", *BASE_EXTRA_HEADERS[:-1], *TABLET_EXPORT_HEADERS,
                         *TABLET_DETAIL_HEADERS)


def _tablet_detail_values(context):
    pairs = list(context.get("tablet_affixes") or [])
    values = []
    for i in range(16):
        pair = pairs[i] if i < len(pairs) else {}
        active = pair.get("affix") and i // 4 < int(context.get("tablet_capacity", context.get("tablets_used")) or 0)
        values.extend([pair.get("value", ""), pair.get("unit", "%")] if active else ["", ""])
    random = list(context.get("tablet_random_mods") or [])
    return [*values, *(random[:4] + [""] * max(0, 4 - len(random)))]


def _config_export_values(context):
    perks = list(context.get("perks") or [])
    tablets = list(context.get("tablet_values") or [])
    return [*[context.get(key, "") for key in ("tier", "area", "base_map_mods", "map_mods", "tablets",
             "tablet_mods", "total_mods", "aldur", "master")],
            *(perks[:4] + [""] * max(0, 4 - len(perks))),
            *[context.get(key, "") for key in ("master_adds_mod", "irradiated", "waystone", "tablets_used")],
            *_extra_export_values(context)[:len(BASE_EXTRA_HEADERS) - 1],
            *(tablets[:32] + [""] * max(0, 32 - len(tablets))), *_tablet_detail_values(context)]


def _extra_export_values(context):
    mods = list(context.get("waystone_mods") or [])
    tablets = list(context.get("tablet_raw_mods") or [])
    return [*[context.get(key) if context.get(key) is not None else ""
              for key in ("item_rarity", "monster_rarity", "pack_size", "effectiveness")],
            context.get("waystone_name", ""), context.get("biome", ""),
            context.get("city_type", ""), context.get("ocean", ""),
            "\n".join(mod for mod in mods if mod),
            *(mods[:10] + [""] * max(0, 10 - len(mods))),
            *("\n".join(tablets[n]) if n < len(tablets) else "" for n in range(4)), "",
            *_tablet_detail_values(context)]


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value):
    return json.loads(value)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _map_id(number):
    return f"M{number:04d}"


def _exp_id(map_id, number):
    return f"{map_id}-E{number:02d}"


def _meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return _load(row[0]) if row else default


def _set_meta(db, key, value):
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, _dump(value)))


def initialize():
    global _READY
    if _READY:
        return
    with _INIT_LOCK:
        if _READY:
            return
        with store._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_cells(
                    tab TEXT NOT NULL,address TEXT NOT NULL,raw_json TEXT NOT NULL,
                    cached_json TEXT,PRIMARY KEY(tab,address));
                CREATE TABLE IF NOT EXISTS families(
                    id INTEGER PRIMARY KEY,top_socket INTEGER NOT NULL,valid INTEGER NOT NULL,
                    recipes_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recipes(
                    name TEXT PRIMARY KEY,sockets INTEGER NOT NULL,combo TEXT NOT NULL,
                    level_band TEXT,category TEXT,source TEXT);
                CREATE TABLE IF NOT EXISTS aliases(
                    position INTEGER PRIMARY KEY,alias TEXT NOT NULL,target TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS alias_keys(
                    key TEXT PRIMARY KEY,target TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS alias_conflicts(key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS affixes(name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS master_perks(
                    master TEXT NOT NULL,name TEXT NOT NULL,tier TEXT,effect TEXT,
                    PRIMARY KEY(master,name));
                CREATE TABLE IF NOT EXISTS legacy_export(
                    position INTEGER PRIMARY KEY,map_id TEXT,expedition_id TEXT,
                    remnant_id TEXT,chain_step INTEGER,row_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS legacy_map_idx ON legacy_export(map_id);
                CREATE INDEX IF NOT EXISTS legacy_exp_idx ON legacy_export(expedition_id);
                CREATE TABLE IF NOT EXISTS new_export(
                    position INTEGER PRIMARY KEY AUTOINCREMENT,map_id TEXT,expedition_id TEXT,
                    remnant_id TEXT,chain_step INTEGER,row_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS new_map_idx ON new_export(map_id);
                CREATE INDEX IF NOT EXISTS new_exp_idx ON new_export(expedition_id);
                CREATE TABLE IF NOT EXISTS maps(
                    map_id TEXT PRIMARY KEY,snapshot_json TEXT NOT NULL,kills_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS expeditions(
                    expedition_id TEXT PRIMARY KEY,map_id TEXT NOT NULL,number INTEGER NOT NULL,
                    detonated INTEGER);
                CREATE TABLE IF NOT EXISTS scan_links(
                    remnant_id TEXT PRIMARY KEY,scan_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS seed_states(
                    family INTEGER NOT NULL,sockets INTEGER NOT NULL,seed_slot TEXT NOT NULL,
                    seed_rune TEXT NOT NULL,rewards_json TEXT NOT NULL,status TEXT NOT NULL,
                    PRIMARY KEY(family,sockets));
                CREATE TABLE IF NOT EXISTS reviewed_glyphs(
                    image_sha256 TEXT PRIMARY KEY,seed_rune TEXT NOT NULL,vector BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS currency_items(name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS currency_snapshots(
                    map_id TEXT NOT NULL,phase TEXT NOT NULL,items_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,PRIMARY KEY(map_id,phase));
                CREATE TABLE IF NOT EXISTS currency_icons(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    image_png BLOB NOT NULL,recorded_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS currency_icon_name_idx ON currency_icons(name);
                CREATE TABLE IF NOT EXISTS item_names(name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS item_icons(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    image_png BLOB NOT NULL,recorded_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS item_icon_name_idx ON item_icons(name);
                CREATE TABLE IF NOT EXISTS ritual_pages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,map_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,scan_hash TEXT,raw_text TEXT NOT NULL,
                    items_json TEXT NOT NULL,recorded_at TEXT NOT NULL,
                    UNIQUE(map_id,page_number));
                CREATE INDEX IF NOT EXISTS ritual_map_idx ON ritual_pages(map_id);
                CREATE TABLE IF NOT EXISTS ritual_names(name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS omen_icons(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    image_png BLOB NOT NULL,recorded_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS omen_icon_name_idx ON omen_icons(name);
                CREATE TABLE IF NOT EXISTS commits(
                    number INTEGER PRIMARY KEY,kind TEXT NOT NULL,map_id TEXT,
                    expedition_id TEXT,reference TEXT,recorded_at TEXT NOT NULL);
            """)
            for table in ("currency_snapshots", "ritual_pages"):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if "snapshot_json" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{{}}'")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(commits)")}
            for column in ("snapshot_json", "details_json"):
                if column not in columns:
                    db.execute(f"ALTER TABLE commits ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'")
            db.execute("CREATE INDEX IF NOT EXISTS commits_map_kind_idx ON commits(map_id,kind)")
            if not _meta(db, "bootstrapped"):
                with gzip.open(HERE / "bootstrap.json.gz", "rt", encoding="utf-8") as file:
                    snapshot = json.load(file)
                for tab, cells in snapshot["tabs"].items():
                    db.executemany("INSERT INTO source_cells VALUES(?,?,?,?)",
                                   ((tab, cell, _dump(raw), _dump(cached))
                                    for cell, raw, cached in cells))
                db.executemany("INSERT INTO families VALUES(?,?,?,?)",
                               ((f["id"], f["top_socket"], int(f["valid"]), _dump(f["recipes"]))
                                for f in snapshot["families"]))
                db.executemany("INSERT INTO recipes VALUES(?,?,?,?,?,?)",
                               ((r["name"], r["sockets"], r["combo"], r["level_band"],
                                 r["category"], r["source"]) for r in snapshot["recipes"]))
                db.executemany("INSERT INTO aliases VALUES(?,?,?)",
                               ((i, alias, target) for i, (alias, target) in enumerate(snapshot["aliases"], 1)))
                db.executemany("INSERT INTO affixes VALUES(?)", ((a,) for a in snapshot["affixes"]))
                db.executemany("INSERT INTO master_perks VALUES(?,?,?,?)",
                               ((master, perk["name"], perk["tier"], perk["effect"])
                                for master, data in snapshot["masters"].items()
                                for perk in data["perks"]))
                db.executemany("INSERT INTO legacy_export VALUES(?,?,?,?,?,?)",
                               ((i, row[22], row[32], row[19], row[25] or None, _dump(row))
                                for i, row in enumerate(snapshot["export_rows"], 2)))
                seen_maps = set()
                seen_exps = set()
                for row in snapshot["export_rows"]:
                    mid, eid = row[22], row[32]
                    if mid and mid not in seen_maps:
                        kills = row[28:31]
                        db.execute("INSERT INTO maps VALUES(?,?,?,?)",
                                   (mid, _dump({}), _dump(kills), _now()))
                        seen_maps.add(mid)
                    if eid and eid not in seen_exps:
                        db.execute("INSERT INTO expeditions VALUES(?,?,?,?)",
                                   (eid, mid, int(row[31] or 1), row[33]))
                        seen_exps.add(eid)
                for key in ("source_name", "export_headers", "settings", "current_map_number",
                            "next_remnant_number", "pending_new_map"):
                    _set_meta(db, key, snapshot[key])
                current = _map_id(snapshot["current_map_number"])
                if current in seen_maps:
                    db.execute("UPDATE maps SET kills_json=? WHERE map_id=?",
                               (_dump([snapshot[k] for k in ("normal_kills", "magic_kills", "rare_kills")]), current))
                    eid = _exp_id(current, snapshot["settings"]["expedition"])
                    if snapshot["detonated"] is not None:
                        db.execute("UPDATE expeditions SET detonated=? WHERE expedition_id=?",
                                   (snapshot["detonated"], eid))
                _set_meta(db, "bootstrapped", True)
            if not _meta(db, "seed_states_initialized"):
                db.executemany("INSERT OR IGNORE INTO seed_states VALUES(?,?,?,?,?,?)",
                               ((s["family"], s["sockets"], s["seed_slot"], s["seed_rune"],
                                 _dump(s["rewards"]), s["status"]) for s in store.STATES))
                _set_meta(db, "seed_states_initialized", True)
            if not _meta(db, "rain_of_blades_six_socket_fix"):
                db.execute("UPDATE recipes SET sockets=6,combo=? WHERE name=? AND sockets=5 AND combo=?",
                           ("Tempest + Sky + Ward + Stone + Arcane + Ward", "Rain of Blades (Level 20)",
                            "Tempest + Sky + Ward + Stone + Arcane"))
                old_state = next((s for s in store.STATES if s["family"] == 38 and s["sockets"] == 5), None)
                if old_state:
                    state = db.execute("SELECT rewards_json,status FROM seed_states WHERE family=38 AND sockets=5").fetchone()
                    if state and state["status"] == "calculator" and _load(state["rewards_json"]) == old_state["rewards"]:
                        db.execute("UPDATE seed_states SET rewards_json=? WHERE family=38 AND sockets=5",
                                   (_dump([name for name in old_state["rewards"] if name != "Rain of Blades (Level 20)"]),))
                _set_meta(db, "rain_of_blades_six_socket_fix", True)
            if not _meta(db, "visible_seed_reference_fix_v1"):
                for original in store.STATES:
                    if original["family"] not in (3, 45):
                        continue
                    row = db.execute("SELECT * FROM seed_states WHERE family=? AND sockets=?",
                                     (original["family"], original["sockets"])).fetchone()
                    if not row or any(row[field] != original[field] for field in
                                      ("seed_slot", "seed_rune", "status")) or \
                            _load(row["rewards_json"]) != original["rewards"]:
                        continue
                    if original["family"] == 3:
                        rewards = ["Runic Alloy" if name == "Runic Alloy x2" else name
                                   for name in original["rewards"]]
                        db.execute("UPDATE seed_states SET rewards_json=? WHERE family=3 AND sockets=?",
                                   (_dump(rewards), original["sockets"]))
                    else:
                        db.execute("DELETE FROM seed_states WHERE family=45 AND sockets=?",
                                   (original["sockets"],))
                _set_meta(db, "visible_seed_reference_fix_v1", True)
            if not _meta(db, "alias_keys_v2_initialized"):
                rebuild_alias_keys(db)
                _set_meta(db, "alias_keys_initialized", True)
                _set_meta(db, "alias_keys_v2_initialized", True)
            if not _meta(db, "currency_items_initialized"):
                names = {re.sub(r"\s+x\d+$", "", name).strip()
                         for name, in db.execute("SELECT name FROM recipes WHERE category='Currency'")}
                db.executemany("INSERT OR IGNORE INTO currency_items(name) VALUES(?)",
                               ((name,) for name in sorted(names) if name))
                _set_meta(db, "currency_items_initialized", True)
            from PoE2_Data_Logger.ocr.currency_ocr import catalog_names, catalog_version
            catalog_key = catalog_version()
            if _meta(db, "currency_inventory_catalog_version") != catalog_key:
                db.executemany("INSERT OR IGNORE INTO currency_items(name) VALUES(?)",
                               ((name,) for name in catalog_names()))
                _set_meta(db, "currency_overlay_catalog_initialized", True)
                _set_meta(db, "currency_inventory_catalog_version", catalog_key)
            db.execute("INSERT OR IGNORE INTO affixes(name) VALUES(?)", ("Chance to Contain Essences",))
            if _meta(db, "affix_catalog_version") != 1:
                db.executemany("INSERT OR IGNORE INTO affixes(name) VALUES(?)",
                               ((item["name"],) for item in affix_catalog()))
                _set_meta(db, "affix_catalog_version", 1)
            if not _meta(db, "tablet_flat_values_migrated"):
                config = _meta(db, "settings")
                pairs = [dict(pair) for pair in config["tablet_affixes"]]
                known = {affix_key(item["name"]): item for item in affix_catalog()}
                added = False
                for tablet, mods in enumerate(config.get("tablet_raw_mods", [])):
                    for raw in mods:
                        amount = modifier_value(raw)
                        target = known.get(affix_key(raw))
                        if not amount or amount["unit"] == "%" or not target or target["unit"] != amount["unit"]:
                            continue
                        slots = range(tablet * 4, tablet * 4 + 4)
                        if any(affix_key(pairs[i]["affix"]) == affix_key(raw) for i in slots):
                            continue
                        empty = next((i for i in slots if not pairs[i]["affix"]), None)
                        if empty is not None:
                            pairs[empty] = {"affix": target["name"], "value": amount["value"], "unit": amount["unit"]}
                            added = True
                if added:
                    config["tablet_affixes"] = pairs
                    if any(affix_key(pair["affix"]) == affix_key("Map has additional random Modifiers") for pair in pairs):
                        config["tablet_random_mods_derived"] = True
                        config["plus_two_tablets"] = sum(v == 2 for v in tablet_random_mod_counts(config))
                    _set_meta(db, "settings", config)
                _set_meta(db, "tablet_flat_values_migrated", True)
            if not _meta(db, "tablet_capacity_default_v17"):
                config = _meta(db, "settings")
                if not any(pair["affix"] for pair in config["tablet_affixes"]) and not any(config.get("tablet_raw_mods", [])):
                    config["tablets_used"] = 4
                    _set_meta(db, "settings", config)
                _set_meta(db, "tablet_capacity_default_v17", True)
            if not _meta(db, "ritual_names_initialized"):
                from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
                db.executemany("INSERT OR IGNORE INTO ritual_names(name) VALUES(?)",
                               ((name,) for name in OMEN_NAMES))
                _set_meta(db, "ritual_names_initialized", True)
        _READY = True


def _connect():
    initialize()
    return store._connect()


def _integer(value, name, low=0, high=None, blank=False):
    if blank and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number.")
    try:
        number = int(value)
        if str(value).strip() not in (str(number), str(float(number))):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a whole number.")
    if number < low or (high is not None and number > high):
        raise ValueError(f"{name} must be between {low} and {high if high is not None else 'higher' }.")
    return number


def _number(value, name, low=0, high=9999):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.")
    if not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return int(result) if result.is_integer() else result


def area_level(config):
    return (79 if config["tier"] == 15 else 80) + int(config["irradiated"]) + int(config["ocean"])


def _validate_settings(db, data):
    source = _meta(db, "settings")
    c = dict(source)
    c.update(data)
    c["waystone"] = _number(c["waystone"], "Waystone %", 0, 999)
    c["tier"] = _integer(c["tier"], "Tier", 15, 16)
    c["map_mods"] = _integer(c["map_mods"], "Map Mods", 0, 100)
    for key, label in (("item_rarity", "Item Rarity"),
                       ("monster_rarity", "Monster Rarity"),
                       ("pack_size", "Pack Size"),
                       ("effectiveness", "Effectiveness")):
        c[key] = (_number(c[key], label, 0, 9999) if c.get(key) not in (None, "") else None)
    mods = c.get("waystone_mods", [])
    if not isinstance(mods, list) or len(mods) > 80 or any(
            not isinstance(mod, str) or len(mod) > 250 for mod in mods):
        raise ValueError("Waystone modifiers are invalid.")
    c["waystone_mods"] = mods
    c["waystone_name"] = str(c.get("waystone_name") or "")[:200]
    c["biome"] = str(c.get("biome") or "None")
    c["city_type"] = str(c.get("city_type") or "None")
    if c["biome"] not in BIOMES or c["city_type"] not in CITY_TYPES:
        raise ValueError("Choose Biome and City Type from the map dropdowns.")
    c["irradiated"] = bool(c["irradiated"])
    c["ocean"] = bool(c["ocean"])
    c["expedition"] = _integer(c["expedition"], "Expedition #", 1, 2)
    c["auto_commit"] = bool(c.get("auto_commit", False))
    c["tablet_auto_commit"] = bool(c.get("tablet_auto_commit", False))
    c["ocr_auto_commit"] = bool(c.get("ocr_auto_commit", False))
    c["tablets_used"] = _integer(c["tablets_used"], "Tablets used", 1, 4)
    c["plus_two_tablets"] = _integer(c["plus_two_tablets"], "+2 Tablets", 0, 4)
    if c["atlas_master"] not in ("None", "Jado", "Doryani", "Hilda"):
        raise ValueError("Choose an Atlas Master from the list.")
    allowed_affixes = {r[0] for r in db.execute("SELECT name FROM affixes")}
    pairs = c["tablet_affixes"]
    if not isinstance(pairs, list) or len(pairs) != 16:
        raise ValueError("Tablet Config needs four slots on each of four tablets.")
    checked = []
    for i, pair in enumerate(pairs):
        affix = str(pair.get("affix") or "").strip()
        value = pair.get("value")
        if affix and affix not in allowed_affixes:
            raise ValueError(f"Tablet slot {i+1}: add this affix to Affix DB first.")
        if bool(affix) != (value not in (None, "")):
            raise ValueError(f"Tablet slot {i+1} needs both an affix and its value.")
        unit = (pair.get("unit") or affix_unit(affix)) if affix else "%"
        if unit not in ("%", "count", "seconds", "flag"):
            raise ValueError(f"Tablet slot {i+1} has an invalid value unit.")
        amount = _number(value, "Tablet value") if affix else None
        if affix and unit != "%" and not float(amount).is_integer():
            raise ValueError(f"Tablet slot {i+1} needs a whole-number {unit} value.")
        checked.append({"affix": affix, "value": amount, **({"unit": unit} if unit != "%" else {})})
        if affix and affix_key(affix) == affix_key("Map has additional random Modifiers"):
            if unit != "count" or amount > 20:
                raise ValueError("Additional random map modifiers need a count between 0 and 20.")
        if affix and any(affix_key(p["affix"]) == affix_key(affix) for p in checked[(i // 4) * 4:i]):
            raise ValueError(f"Tablet {i // 4 + 1} selects the same affix twice.")
    c["tablet_affixes"] = checked
    raw_tablets = c.get("tablet_raw_mods", [[] for _ in range(4)])
    if (not isinstance(raw_tablets, list) or len(raw_tablets) != 4
            or any(not isinstance(mods, list) or len(mods) > 20 or any(
                not isinstance(mod, str) or len(mod) > 250 for mod in mods)
                   for mods in raw_tablets)):
        raise ValueError("Tablet modifier text is invalid.")
    c["tablet_raw_mods"] = raw_tablets
    if "tablet_affixes" in data or "tablet_raw_mods" in data:
        c["tablet_random_mods_derived"] = True
    if c.get("tablet_random_mods_derived"):
        c["plus_two_tablets"] = sum(v == 2 for v in tablet_random_mod_counts(c))
    selections = c["master_selections"]
    if not isinstance(selections, dict):
        raise ValueError("Master perks are invalid.")
    clean_selections = {}
    for master in ("Jado", "Doryani", "Hilda"):
        perks = selections.get(master, ["None"] * 4)
        allowed = {r[0] for r in db.execute("SELECT name FROM master_perks WHERE master=?", (master,))}
        if not isinstance(perks, list) or len(perks) != 4:
            raise ValueError(f"{master} needs four perk slots.")
        selected = [str(p or "None") for p in perks]
        if any(p != "None" and p not in allowed for p in selected):
            raise ValueError(f"Choose {master} perks from its list.")
        if len([p for p in selected if p != "None"]) != len(set(p for p in selected if p != "None")):
            raise ValueError(f"{master} has a duplicate perk.")
        clean_selections[master] = selected
    c["master_selections"] = clean_selections
    c["aldur"] = str(c.get("aldur") or "None")[:200]
    return c


def save_settings(data, *, confirmed_affixes=()):
    with _connect() as db:
        for name in confirmed_affixes:
            if not isinstance(name, str) or not name.strip() or len(name) > 120 or "%" in name:
                raise ValueError("Review the tablet affix name before saving.")
            db.execute("INSERT OR IGNORE INTO affixes(name) VALUES(?)", (name.strip(),))
        c = _validate_settings(db, data)
        pending = _meta(db, "ocr_pending")
        if pending and c["expedition"] != _meta(db, "settings")["expedition"]:
            raise ValueError("Save or discard the scanned remnant before switching expeditions.")
        _set_meta(db, "settings", c)
        current = _meta(db, "current_map_number", 0)
        if current and not _meta(db, "pending_new_map") and not _map_has_activity(db, _map_id(current)):
            db.execute("UPDATE maps SET snapshot_json=? WHERE map_id=?",
                       (_dump(_snapshot(c)), _map_id(current)))
    return get_state()


def add_affix(name):
    name = str(name or "").strip()
    if not name or len(name) > 120:
        raise ValueError("Enter an affix name under 120 characters.")
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO affixes VALUES(?)", (name,))
    return get_state()


def add_affixes(names):
    added = []
    with _connect() as db:
        for name in names:
            name = str(name or "").strip()
            if not name or len(name) > 120 or "%" in name:
                continue
            if db.execute("INSERT OR IGNORE INTO affixes(name) VALUES(?)", (name,)).rowcount:
                added.append(name)
    return added


def _check_seed_references(db, family_ids=(), recipe_names=()):
    families, recipes = set(family_ids), set(recipe_names)
    for stage in db.execute("SELECT family,sockets,rewards_json FROM seed_states"):
        rewards = _load(stage["rewards_json"])
        if stage["family"] not in families and not recipes.intersection(rewards):
            continue
        parent = db.execute("SELECT top_socket,recipes_json FROM families WHERE id=?",
                            (stage["family"],)).fetchone()
        members = set(_load(parent["recipes_json"])) if parent else set()
        if (not parent or stage["sockets"] > parent["top_socket"] or
                len(set(rewards)) != len(rewards) or any(
                    name not in members or (recipe := db.execute(
                        "SELECT sockets FROM recipes WHERE name=?", (name,)).fetchone()) is None or
                    recipe[0] > stage["sockets"] for name in rewards)):
            raise ValueError(f"Update Family {stage['family']}'s {stage['sockets']}-socket seed mapping before changing its recipes.")


def save_recipe(data):
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 200:
        raise ValueError("Recipe name is required and must be under 200 characters.")
    sockets = _integer(data.get("sockets"), "Recipe sockets", 2, 10)
    combo = str(data.get("combo") or "").strip()
    runes = [part.strip() for part in combo.split("+")]
    if len(runes) != sockets or any(not part for part in runes):
        raise ValueError("Exact Rune Combo must contain one rune per socket, separated by +.")
    with _connect() as db:
        existing = db.execute("SELECT name FROM recipes WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if existing:
            name = existing[0]
        for family in db.execute("SELECT id,top_socket,recipes_json FROM families"):
            if name in _load(family["recipes_json"]) and sockets > family["top_socket"]:
                raise ValueError(f"Family {family['id']} has a lower Top Socket; edit that family first.")
        db.execute("INSERT INTO recipes VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                   "sockets=excluded.sockets,combo=excluded.combo,level_band=excluded.level_band,"
                   "category=excluded.category,source=excluded.source",
                   (name, sockets, " + ".join(runes), str(data.get("level_band") or "").strip(),
                    str(data.get("category") or "").strip(), str(data.get("source") or "").strip()))
        _check_seed_references(db, recipe_names=(name,))
    return {"name": name, "sockets": sockets, "combo": " + ".join(runes)}


def save_family(data):
    family_id = _integer(data.get("family"), "Family ID", 1, 9999)
    top = _integer(data.get("top_socket"), "Top Socket", 3, 10)
    supplied = data.get("recipes", [])
    if isinstance(supplied, str):
        supplied = supplied.splitlines()
    if not isinstance(supplied, list):
        raise ValueError("Enter one recipe per line.")
    names = [str(name).strip() for name in supplied if str(name).strip()]
    if not names or len(names) > 100 or len(set(names)) != len(names):
        raise ValueError("Enter 1–100 distinct recipes in family order.")
    with _connect() as db:
        resolved = []
        for name in names:
            recipe = db.execute("SELECT name,sockets FROM recipes WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if recipe is None:
                raise ValueError(f"Add {name} to Recipe DB before saving this family.")
            if recipe["sockets"] > top:
                raise ValueError(f"{recipe['name']} has more sockets than this family's Top Socket.")
            resolved.append(recipe["name"])
        if len(set(resolved)) != len(resolved):
            raise ValueError("A recipe appears twice in this family.")
        if db.execute("SELECT 1 FROM seed_states WHERE family=? AND sockets>? LIMIT 1",
                      (family_id, top)).fetchone():
            raise ValueError("This family has a visible seed mapping above the new Top Socket.")
        db.execute("INSERT INTO families VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "top_socket=excluded.top_socket,valid=excluded.valid,recipes_json=excluded.recipes_json",
                   (family_id, top, int(bool(data.get("valid", True))), _dump(resolved)))
        _check_seed_references(db, family_ids=(family_id,))
    return {"family": family_id, "top_socket": top, "valid": bool(data.get("valid", True)),
            "recipes": resolved}


def save_seed_state(data):
    family_id = _integer(data.get("family"), "Family ID", 1, 9999)
    sockets = _integer(data.get("sockets"), "Seed sockets", 3, 10)
    slot = str(data.get("seed_slot") or "").strip().upper()
    rune = str(data.get("seed_rune") or "").strip()
    if slot not in [f"P{i}" for i in range(1, sockets + 1)]:
        raise ValueError("Visible slot must be inside the socket bar.")
    if not rune or rune.lower() == "unresolved" or len(rune) > 80:
        raise ValueError("Enter a visible rune name under 80 characters.")
    supplied = data.get("rewards", [])
    if isinstance(supplied, str):
        supplied = supplied.splitlines()
    if not isinstance(supplied, list):
        raise ValueError("Enter stage rewards one per line.")
    rewards = [str(item).strip() for item in supplied if str(item).strip()]
    with _connect() as db:
        family = db.execute("SELECT top_socket,recipes_json FROM families WHERE id=?", (family_id,)).fetchone()
        if family is None or sockets > family[0]:
            raise ValueError("Choose an existing family and a socket stage at or below its Top Socket.")
        if len(set(rewards)) != len(rewards):
            raise ValueError("A stage reward appears twice.")
        members = set(_load(family["recipes_json"]))
        for reward in rewards:
            recipe = db.execute("SELECT sockets FROM recipes WHERE name=?", (reward,)).fetchone()
            if not recipe:
                raise ValueError(f"Add {reward} to Recipe DB before using it as a stage reward.")
            if reward not in members or recipe[0] > sockets:
                raise ValueError("Stage rewards must belong to this family and fit its socket count.")
        db.execute("INSERT INTO seed_states VALUES(?,?,?,?,?,?) ON CONFLICT(family,sockets) DO UPDATE SET "
                   "seed_slot=excluded.seed_slot,seed_rune=excluded.seed_rune,"
                   "rewards_json=excluded.rewards_json,status=excluded.status",
                   (family_id, sockets, slot, rune, _dump(rewards), "local"))
    return {"family": family_id, "sockets": sockets, "seed_slot": slot,
            "seed_rune": rune, "rewards": rewards, "status": "local"}


def clear_tablets():
    state = save_settings({"tablets_used": 4, "plus_two_tablets": 0,
                           "tablet_affixes": [{"affix": "", "value": None} for _ in range(16)],
                           "tablet_raw_mods": [[] for _ in range(4)]})
    with _connect() as db:
        _set_meta(db, "tablet_auto_next", 1)
    return state


def tablet_next_slot():
    with _connect() as db:
        return _meta(db, "tablet_auto_next", 1)


def advance_tablet_slot(number):
    with _connect() as db:
        if _meta(db, "tablet_auto_next", 1) == number and 1 <= number <= 4:
            _set_meta(db, "tablet_auto_next", number + 1)


def save_scanned_tablet(matches, raw_mods):
    number = tablet_next_slot()
    if number > 4:
        raise ValueError("Four tablets are already saved. Clear tablet config before scanning a new set.")
    if not matches or len(matches) > 4 or not isinstance(raw_mods, list):
        raise ValueError("Review the tablet modifiers before saving.")
    state = get_state()["settings"]
    slots = ([{"affix": "", "value": None} for _ in range(16)] if number == 1 else
             [dict(item) for item in state["tablet_affixes"]])
    raw = ([[] for _ in range(4)] if number == 1 else
           [list(item) for item in state["tablet_raw_mods"]])
    for offset, item in enumerate(matches):
        slots[(number - 1) * 4 + offset] = {"affix": item["affix"], "value": item["value"],
                                          "unit": item.get("unit") or affix_unit(item["affix"])}
    raw[number - 1] = list(raw_mods)
    save_settings({"tablets_used": max(state["tablets_used"], number), "plus_two_tablets": 0 if number == 1 else
                   state["plus_two_tablets"], "tablet_affixes": slots,
                   "tablet_raw_mods": raw})
    with _connect() as db:
        _set_meta(db, "tablet_auto_next", number + 1)
        mid_number = _meta(db, "current_map_number", 0)
        pending = not mid_number or _meta(db, "pending_new_map", False)
        if pending:
            mid_number += 1
        mid = _map_id(mid_number)
        eid = _exp_id(mid, 1 if pending else _meta(db, "settings")["expedition"])
        _record_commit(db, "Tablet config", mid, eid, f"Tablet {number}")
    return number


def _canonical(db, input_value):
    raw = str(input_value or "").strip()
    if not raw:
        raise ValueError("Enter First Recipe.")
    exact = db.execute("SELECT name FROM recipes WHERE name=? COLLATE NOCASE", (raw,)).fetchone()
    if exact:
        return exact[0]
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    if db.execute("SELECT 1 FROM alias_conflicts WHERE key=?", (key,)).fetchone():
        raise ValueError(f"Recipe shortcut is ambiguous: {raw}. Choose the full recipe name.")
    hit = db.execute("SELECT target FROM alias_keys WHERE key=?", (key,)).fetchone()
    if hit:
        return hit[0]
    raise ValueError(f"Recipe not found: {raw}")


def rebuild_alias_keys(db):
    db.execute("DELETE FROM alias_keys")
    db.execute("DELETE FROM alias_conflicts")
    targets = {}
    conflicts = set()
    for alias, target in db.execute("SELECT alias,target FROM aliases ORDER BY position"):
        key = re.sub(r"[^a-z0-9]", "", alias.lower())
        if key in targets and targets[key] != target:
            conflicts.add(key)
        targets.setdefault(key, target)
    db.executemany("INSERT INTO alias_keys(key,target) VALUES(?,?)", targets.items())
    db.executemany("INSERT INTO alias_conflicts(key) VALUES(?)", ((key,) for key in conflicts))


def _resolve(db, first, next_recipe=None, family=None):
    canonical = _canonical(db, first)
    second = _canonical(db, next_recipe) if next_recipe else None
    candidates = []
    for row in db.execute("SELECT * FROM families WHERE valid=1"):
        recipes = _load(row["recipes_json"])
        for index, recipe in enumerate(recipes):
            if recipe == canonical and (not second or (index + 1 < len(recipes) and recipes[index+1] == second)):
                candidates.append({"family": row["id"], "start": index,
                                   "remaining": recipes[index:]})
    if family not in (None, ""):
        number = int(str(family).replace("Family", "").strip())
        candidates = [c for c in candidates if c["family"] == number]
    chosen = candidates[0] if len(candidates) == 1 else None
    rows = []
    if chosen:
        for name in chosen["remaining"]:
            recipe = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (name,)).fetchone()
            if recipe is None or not recipe["combo"]:
                raise ValueError(f"Recipe DB has no complete row for {name}.")
            rows.append({"recipe": name, "sockets": recipe["sockets"], "combo": recipe["combo"]})
    return {"canonical": canonical, "candidates": [c["family"] for c in candidates],
            "family": chosen["family"] if chosen else None, "rows": rows,
            "status": "ready" if chosen else "ambiguous" if candidates else "no matching family"}


def resolve(first, next_recipe=None, family=None):
    with _connect() as db:
        return _resolve(db, first, next_recipe, family)


def _first_row(db, field, value):
    for table in ("legacy_export", "new_export"):
        row = db.execute(f"SELECT position,row_json FROM {table} WHERE {field}=? ORDER BY position LIMIT 1",
                         (value,)).fetchone()
        if row:
            return table, row["position"], _load(row["row_json"])
    return None


def _has_remnant(db, map_id):
    return any(db.execute(f"SELECT 1 FROM {table} WHERE map_id=? AND remnant_id IS NOT NULL "
                          "AND remnant_id!='' LIMIT 1", (map_id,)).fetchone()
               for table in ("legacy_export", "new_export"))


def _map_has_activity(db, map_id):
    for table in ("legacy_export", "new_export", "currency_snapshots", "ritual_pages"):
        if db.execute(f"SELECT 1 FROM {table} WHERE map_id=? LIMIT 1", (map_id,)).fetchone():
            return True
    return bool(db.execute("SELECT 1 FROM commits WHERE map_id=? AND kind IN "
                           "('Map kills','Map totals','Detonated') LIMIT 1", (map_id,)).fetchone())


def _patch_first(db, field, value, replacements):
    hit = _first_row(db, field, value)
    if hit:
        table, position, row = hit
        for index, new_value in replacements.items():
            row[index] = new_value
        db.execute(f"UPDATE {table} SET row_json=? WHERE position=?", (_dump(row), position))
    return bool(hit)


def _snapshot(config):
    master = config["atlas_master"]
    perks = [p if p != "None" else "" for p in config["master_selections"].get(master, [])]
    perks += [""] * (4 - len(perks))
    master_adds_mod = int(master == "Jado" and "Unexpected Missions" in perks)
    tablet_values = []
    pairs = [{**pair, "unit": pair.get("unit") or affix_unit(pair.get("affix", ""))}
             for pair in config["tablet_affixes"]]
    random_mods = tablet_random_mod_counts(config)
    raw_tablets = config.get("tablet_raw_mods", [[] for _ in range(4)])
    tablet_count = sum(bool(raw_tablets[n]) or any(pair["affix"] for pair in pairs[n * 4:n * 4 + 4])
                       for n in range(config["tablets_used"]))
    for i, pair in enumerate(pairs):
        if i // 4 < config["tablets_used"]:
            tablet_values += [pair["affix"], pair["value"] if pair["affix"] and pair["unit"] == "%" else ""]
        else:
            tablet_values += ["", ""]
    return {
        "tier": config["tier"], "area": area_level(config), "map_mods": config["map_mods"] + master_adds_mod,
        "base_map_mods": config["map_mods"],
        "tablets": sum(v == 2 for v in random_mods), "tablet_mods": sum(random_mods),
        "total_mods": config["map_mods"] + master_adds_mod + sum(random_mods),
        "aldur": config["aldur"], "master": master, "perks": perks[:4],
        "master_selections": {name: list(items) for name, items in config["master_selections"].items()},
        "master_adds_mod": master_adds_mod, "irradiated": "Yes" if config["irradiated"] else "No",
        "ocean": "Yes" if config["ocean"] else "No",
        "waystone": config["waystone"], "expedition": config["expedition"],
        "tablets_used": tablet_count, "tablet_capacity": config["tablets_used"], "tablet_values": tablet_values,
        "tablet_affixes": pairs, "tablet_random_mods": random_mods,
        "item_rarity": config.get("item_rarity"), "monster_rarity": config.get("monster_rarity"),
        "pack_size": config.get("pack_size"), "effectiveness": config.get("effectiveness"),
        "waystone_mods": config.get("waystone_mods", []),
        "waystone_name": config.get("waystone_name", ""),
        "biome": config.get("biome", "None"), "city_type": config.get("city_type", "None"),
        "tablet_raw_mods": [mods if i < config["tablets_used"] else [] for i, mods in enumerate(
            config.get("tablet_raw_mods", [[] for _ in range(4)]))],
    }


def _add_new(db, row):
    db.execute("INSERT INTO new_export(map_id,expedition_id,remnant_id,chain_step,row_json) VALUES(?,?,?,?,?)",
               (row[22], row[32], row[19] or None, row[25] or None, _dump(row)))


def _record_commit(db, kind, map_id="", expedition_id="", reference="", *, context=None, details=None):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    number = _meta(db, "scan_commit_count", 0) + 1
    if context is None:
        context = _snapshot(_meta(db, "settings"))
        if kind in ("Map kills", "Map totals", "Detonated"):
            saved = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
            if saved:
                context = _load(saved[0])
    db.execute("INSERT INTO commits(number,kind,map_id,expedition_id,reference,recorded_at,snapshot_json,details_json) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (number, kind, map_id, expedition_id, str(reference or ""), _now(),
                _dump(context), _dump(details or {})))
    _set_meta(db, "scan_commit_count", number)
    return number


def record_commit(kind, reference=""):
    if kind not in ("Map settings", "Tablet config", "Atlas Master", "Master perks"):
        raise ValueError("Unknown configuration commit.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number", 0)
        pending = not number or _meta(db, "pending_new_map", False)
        if pending:
            number += 1
        mid = _map_id(number) if number else ""
        expedition = 1 if pending else _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition) if mid else ""
        return _record_commit(db, kind, mid, eid, reference)


def session_generation():
    with _connect() as db:
        return _meta(db, "session_generation", 0)


def scan_context():
    with _connect() as db:
        number = _meta(db, "current_map_number", 0)
        return {"_scan_generation": _meta(db, "session_generation", 0),
                "_capture_map_id": _map_id(number) if number else None,
                "_capture_map_pending": bool(_meta(db, "pending_new_map", False)),
                "_capture_expedition": 1 if not number or _meta(db, "pending_new_map", False)
                                       else _meta(db, "settings")["expedition"]}


def validate_scan_context(result):
    if "_scan_generation" not in result:
        return
    current = scan_context()
    if result["_scan_generation"] != current["_scan_generation"]:
        raise ValueError("This scan belongs to the previous session. Scan again.")
    if result.get("_capture_map_id") != current["_capture_map_id"]:
        raise ValueError("The map changed during the scan. Scan the current map again.")
    if "_capture_map_pending" in result and result["_capture_map_pending"] != current["_capture_map_pending"]:
        raise ValueError("The map ended during the scan. Scan again.")
    if "_capture_expedition" in result and result["_capture_expedition"] != current["_capture_expedition"]:
        raise ValueError("The expedition changed during the scan. Scan the current expedition again.")


def assign_ocr_id(mode, expected_map_id=None, expected_generation=None, expected_expedition=None):
    if mode not in ("seed", "opened"):
        raise ValueError("Choose visible seed or opened remnant mode.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if expected_generation is not None and expected_generation != _meta(db, "session_generation", 0):
            raise ValueError("This scan belongs to the previous session. Scan again.")
        current = _meta(db, "current_map_number")
        new_map = current == 0 or _meta(db, "pending_new_map")
        expedition = 1 if new_map else _meta(db, "settings")["expedition"]
        if expected_expedition is not None and expected_expedition != expedition:
            raise ValueError("The expedition changed during the scan. Scan the current expedition again.")
        pending = _meta(db, "ocr_pending")
        if pending:
            if expected_map_id is not None and pending["map_id"] != expected_map_id:
                raise ValueError("The map changed during the scan. Scan the current map again.")
            return pending
        number = current + 1 if new_map else current
        mid = _map_id(number)
        if expected_map_id is not None and mid != expected_map_id:
            raise ValueError("The map changed during the scan. Scan the current map again.")
        pending = {"remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
                   "map_id": mid, "expedition_id": _exp_id(mid, expedition)}
        _set_meta(db, "ocr_pending", pending)
        _set_meta(db, "ocr_pending_mode", mode)
        return pending


def discard_ocr_id():
    with _connect() as db:
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
    return get_state()


def start_next_chain():
    with _connect() as db:
        if not _meta(db, "current_map_number"):
            raise ValueError("Start a map before starting another chain.")
        if _meta(db, "pending_new_map"):
            raise ValueError("The next map is marked; its first chain is Expedition #1.")
        if _meta(db, "ocr_pending"):
            raise ValueError("Save or discard the scanned remnant before starting another chain.")
        config = _meta(db, "settings")
        if config["expedition"] == 2:
            raise ValueError("This map is already on Expedition #2.")
        config["expedition"] = 2
        _set_meta(db, "settings", config)
    return get_state()


def commit_remnant(first, next_recipe=None, family=None, scan_id=None, expected_pending=None, visible_seed=None):
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if expected_pending is not None:
            if not _meta(db, "settings").get("auto_commit", False):
                raise ValueError("Auto-commit is off.")
            if _meta(db, "ocr_pending") != expected_pending:
                raise ValueError("The scanned remnant was already saved or discarded.")
        return _commit_remnant(db, first, next_recipe, family, scan_id, visible_seed)


def _commit_remnant(db, first, next_recipe=None, family=None, scan_id=None, visible_seed=None):
    result = _resolve(db, first, next_recipe, family)
    if result["status"] != "ready":
        raise ValueError("Choose a matching family or enter Next Recipe before committing.")
    config = _validate_settings(db, {})
    current = _meta(db, "current_map_number")
    new_map = current == 0 or _meta(db, "pending_new_map")
    if current and new_map:
        config.update(WAYSTONE_DEFAULTS)
    number = current + 1 if new_map else current
    mid = _map_id(number)
    expedition = 1 if new_map else config["expedition"]
    eid = _exp_id(mid, expedition)
    remnant_number = _meta(db, "next_remnant_number")
    rid = f"R{remnant_number:04d}"
    pending = _meta(db, "ocr_pending")
    if pending and (pending["remnant_id"], pending["map_id"], pending["expedition_id"]) != (rid, mid, eid):
        raise ValueError("Scanned remnant belongs to another map or chain. Save or discard it first.")
    occurrence = str(uuid.uuid4())
    context = _snapshot({**config, "expedition": expedition})
    details = {"family": result["family"], "recipes": result["rows"]}
    if visible_seed:
        details["visible_seed"] = visible_seed
    commit_number = _record_commit(db, "Remnant", mid, eid, rid, context=context, details=details)
    first_remnant = not _has_remnant(db, mid)
    old_map_row = _first_row(db, "map_id", mid)
    old_exp_row = _first_row(db, "expedition_id", eid)
    kills = db.execute("SELECT kills_json FROM maps WHERE map_id=?", (mid,)).fetchone()
    det = db.execute("SELECT detonated FROM expeditions WHERE expedition_id=?", (eid,)).fetchone()
    for i, entry in enumerate(result["rows"]):
        row = [
            first if i == 0 else entry["recipe"], entry["recipe"], entry["sockets"], entry["combo"],
            context["tier"], context["area"], context["map_mods"], context["tablets"],
            context["tablet_mods"], context["total_mods"], context["aldur"], context["master"],
            *context["perks"], context["master_adds_mod"], context["irradiated"],
            "X" if i == 0 else "", rid, 13 + i, "X" if i == 0 and first_remnant else "", mid,
            context["waystone"], occurrence,
            "", "", "", "", "", "", expedition, eid, "", context["tablets_used"],
            *context["tablet_values"],
        ]
        if i == 0 and not old_map_row and kills:
            row[28:31] = _load(kills[0])
        if i == 0 and not old_exp_row and det:
            row[33] = det[0] if det[0] is not None else ""
        row.extend(_extra_export_values(context))
        row[67 + len(BASE_EXTRA_HEADERS) - 1] = commit_number
        if len(row) != 67 + len(EXPORT_EXTRA_HEADERS):
            raise RuntimeError("Export width is incorrect.")
        _add_new(db, row)
    if not old_map_row:
        db.execute("INSERT OR IGNORE INTO maps VALUES(?,?,?,?)",
                   (mid, _dump(context), _dump([None, None, None]), _now()))
    db.execute("INSERT OR IGNORE INTO expeditions VALUES(?,?,?,?)",
               (eid, mid, expedition, None))
    if scan_id not in (None, ""):
        scan_id = _integer(scan_id, "Scan ID", 1)
        scan_row = db.execute("SELECT family FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan_row:
            raise ValueError("Saved scan was not found.")
        if scan_row["family"] and scan_row["family"] != f"Family {result['family']}":
            raise ValueError("Saved scan family differs from the resolved remnant family.")
        db.execute("INSERT INTO scan_links VALUES(?,?)", (rid, scan_id))
    _set_meta(db, "current_map_number", number)
    _set_meta(db, "next_remnant_number", remnant_number + 1)
    _set_meta(db, "pending_new_map", False)
    if pending:
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
    if new_map:
        config["expedition"] = 1
        _set_meta(db, "settings", config)
    return {"remnant_id": rid, "map_id": mid, "expedition_id": eid,
            "recipes": len(result["rows"]), "family": result["family"],
            "scan_commit_number": commit_number}


def _seed_recipes(db, family, sockets):
    record = db.execute("SELECT recipes_json FROM families WHERE id=? AND valid=1", (family,)).fetchone()
    recipes = _load(record[0]) if record else []
    eligible = [name for name in recipes if (row := db.execute(
        "SELECT sockets FROM recipes WHERE name=?", (name,)).fetchone()) and row[0] <= sockets]
    if not eligible:
        raise ValueError("The family has no recipes at this socket stage.")
    first, second = eligible[0], eligible[1] if len(eligible)>1 else None
    resolved = _resolve(db, first, second, family)
    if resolved["status"] != "ready" or any(row["sockets"] > sockets for row in resolved["rows"]):
        raise ValueError("The family stage and Recipe DB disagree. Review the database entry.")
    return first, second, resolved


def commit_seed_batch(result, selections, automatic=False):
    if not isinstance(result, dict) or not isinstance(selections, list) or not 1 <= len(selections) <= 24:
        raise ValueError("Select the visible remnants to commit.")
    readings = result.get("remnants") or [result]
    if not isinstance(readings, list) or not 1 <= len(readings) <= 24:
        raise ValueError("Scan the visible remnants first.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        context = {"_scan_generation": _meta(db, "session_generation", 0),
                   "_capture_map_id": _map_id(_meta(db, "current_map_number", 0))
                                      if _meta(db, "current_map_number", 0) else None,
                   "_capture_expedition": 1 if not _meta(db, "current_map_number", 0) or
                         _meta(db, "pending_new_map", False) else _meta(db, "settings")["expedition"]}
        if any(result.get(key) != expected for key, expected in context.items()):
            raise ValueError("The map, expedition or session changed. Scan again before committing.")
        if ("_capture_map_pending" in result and result["_capture_map_pending"] !=
                bool(_meta(db, "pending_new_map", False))):
            raise ValueError("The map ended during the scan. Scan again.")
        pending = _meta(db, "ocr_pending")
        if not pending or any(result.get(key) != pending[key] for key in
                              ("remnant_id", "map_id", "expedition_id")):
            raise ValueError("The visible seed scan was already saved or discarded. Scan again.")
        if automatic and not _meta(db, "settings").get("auto_commit", False):
            raise ValueError("Auto-commit is off.")
        resolved = []
        seen = set()
        for item in selections:
            if not isinstance(item, dict):
                raise ValueError("Review each selected remnant.")
            index = _integer(item.get("index"), "Remnant index", 0, len(readings)-1)
            if index in seen:
                raise ValueError("A visible remnant can only be committed once per scan.")
            seen.add(index)
            source = readings[index]
            if source.get("saved") or source.get("rejected"):
                raise ValueError("That visible remnant was already saved or rejected.")
            if automatic and (not source.get("can_commit") or any(
                    item.get(key) != source.get(key) for key in ("sockets", "seed_slot", "seed_rune"))):
                raise ValueError("Review the uncertain seed before committing it.")
            sockets = _integer(item.get("sockets"), "Sockets", 3, 10)
            family = _integer(item.get("family"), "Family", 1)
            stage = db.execute("SELECT s.rewards_json,s.status FROM seed_states s JOIN families f "
                "ON f.id=s.family WHERE f.valid=1 AND s.family=? AND s.sockets=? AND "
                "s.seed_slot=? AND s.seed_rune=?", (family, sockets,
                item.get("seed_slot"), item.get("seed_rune"))).fetchone()
            if not stage:
                raise ValueError("That family does not match the reviewed seed and socket stage.")
            if automatic and (source.get("candidates") != [family] or
                              str(stage["status"]).startswith("inferred")):
                raise ValueError("Choose a verified family before auto-committing.")
            if not _load(stage["rewards_json"]):
                raise ValueError("The visible seed stage has no verified rewards.")
            first, second, _ = _seed_recipes(db, family, sockets)
            seed = {"sockets": sockets, "slot": item.get("seed_slot"),
                    "rune": item.get("seed_rune"), "scan_index": index+1, "mode": "seed"}
            resolved.append((index, first, second, family, seed))
        saved = [{"index": index, **_commit_remnant(db, first, second, family, visible_seed=seed)}
                 for index, first, second, family, seed in sorted(resolved)]
        remaining = any(i not in seen and not reading.get("saved") and not reading.get("rejected")
                        for i, reading in enumerate(readings))
        next_pending = None
        number = _meta(db, "current_map_number")
        expedition = _meta(db, "settings")["expedition"]
        if remaining:
            next_pending = {"remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
                            "map_id": _map_id(number),
                            "expedition_id": _exp_id(_map_id(number), expedition)}
            _set_meta(db, "ocr_pending", next_pending)
            _set_meta(db, "ocr_pending_mode", "seed")
        return {"committed": True, "saved": saved, "pending": next_pending,
                "context": {"_scan_generation": _meta(db, "session_generation", 0),
                            "_capture_map_id": _map_id(number),
                            "_capture_expedition": expedition},
                "scan_commit_number": saved[-1]["scan_commit_number"]}


def seed_previously_logged(map_id, expedition_id, family, sockets):
    with _connect() as db:
        return db.execute("SELECT 1 FROM commits WHERE kind='Remnant' AND map_id=? AND "
            "expedition_id=? AND json_extract(details_json,'$.family')=? AND "
            "json_extract(details_json,'$.visible_seed.sockets')=? LIMIT 1",
            (map_id, expedition_id, family, sockets)).fetchone() is not None


def _rune(value, label):
    rune = str(value or "").strip()
    if len(rune) > 80 or (label == "Rune 1" and not rune):
        raise ValueError(f"{label} is required and must be under 80 characters." if label == "Rune 1"
                         else f"{label} must be under 80 characters.")
    return rune


def commit_chain(rune1, rune2=""):
    result = commit_chain_steps([{"rune1": rune1, "rune2": rune2}])
    return {**result, "step": result["steps"][0], "rune1": rune1, "rune2": rune2}


def commit_chain_runes(runes):
    if not isinstance(runes, list) or not 1 <= len(runes) <= 96:
        raise ValueError("Enter 1–96 runes in chain order.")
    cleaned = [_rune(rune, "Rune 1") for rune in runes]
    return commit_chain_steps([{"rune1": rune, "rune2": ""} for rune in cleaned])


def commit_chain_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 96:
        raise ValueError("Enter 1–96 chain steps in order.")
    cleaned = []
    for item in steps:
        if not isinstance(item, dict):
            raise ValueError("Enter the chain runes in order.")
        cleaned.append((_rune(item.get("rune1"), "Rune 1"),
                        _rune(item.get("rune2"), "Rune 2")))
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number")
        if not number:
            raise ValueError("Start a map before committing a chain.")
        if _meta(db, "pending_new_map"):
            raise ValueError("Start the next map before committing a chain.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        step = max([int(row[0] or 0) for table in ("legacy_export", "new_export")
                    for row in db.execute(f"SELECT chain_step FROM {table} WHERE expedition_id=? AND chain_step IS NOT NULL AND chain_step!=''",
                                          (eid,))] or [0]) + 1
        existing = _first_row(db, "expedition_id", eid)
        det = db.execute("SELECT detonated FROM expeditions WHERE expedition_id=?", (eid,)).fetchone()
        context = _snapshot(_meta(db, "settings"))
        commit_number = _record_commit(db, "Chain", mid, eid, f"Steps {step}–{step + len(cleaned) - 1}",
                                       context=context, details={"steps": [
                                           {"step": step + offset, "rune1": rune1, "rune2": rune2}
                                           for offset, (rune1, rune2) in enumerate(cleaned)]})
        for offset, (rune1, rune2) in enumerate(cleaned):
            row = [""] * 67
            row[22], row[25], row[26], row[27], row[31], row[32] = (
                mid, step + offset, rune1, rune2, expedition, eid)
            if offset == 0 and not existing and det and det[0] is not None:
                row[33] = det[0]
            row.extend(_extra_export_values(context))
            row[67 + len(BASE_EXTRA_HEADERS) - 1] = commit_number
            _add_new(db, row)
        db.execute("INSERT OR IGNORE INTO expeditions VALUES(?,?,?,?)", (eid, mid, expedition, None))
        return {"map_id": mid, "expedition_id": eid,
                "steps": list(range(step, step + len(cleaned))),
                "scan_commit_number": commit_number}


def save_kills(normal, magic, rare):
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    with _connect() as db:
        number = _meta(db, "current_map_number")
        if number == 0:
            raise ValueError("Start a map before saving map kills.")
        mid = _map_id(number)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        commit_number = _record_commit(db, "Map kills", mid, details={"kills": values})
        return {"map_id": mid, "kills": values, "scan_commit_number": commit_number}


def save_detonated(value):
    count = _integer(value, "Remnants Detonated", 0, blank=True)
    with _connect() as db:
        number = _meta(db, "current_map_number")
        if number == 0:
            raise ValueError("Start a map before saving detonated totals.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) DO UPDATE SET detonated=excluded.detonated",
                   (eid, mid, expedition, count))
        _patch_first(db, "expedition_id", eid, {33: "" if count is None else count})
        commit_number = _record_commit(db, "Detonated", mid, eid, details={"detonated": count})
        return {"expedition_id": eid, "detonated": count, "scan_commit_number": commit_number}


def save_counts(normal, magic, rare, detonated):
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    count = _integer(detonated, "Remnants Detonated", 0, blank=True)
    with _connect() as db:
        number = _meta(db, "current_map_number")
        if not number:
            raise ValueError("Start a map before saving counts.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) DO UPDATE SET detonated=excluded.detonated",
                   (eid, mid, expedition, count))
        _patch_first(db, "expedition_id", eid, {33: "" if count is None else count})
        commit_number = _record_commit(db, "Map totals", mid, eid, details={"kills": values, "detonated": count})
        return {"map_id": mid, "expedition_id": eid, "kills": values,
                "detonated": count, "scan_commit_number": commit_number}


def finish_map(normal, magic, rare, detonated):
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    count = _integer(detonated, "Remnants Detonated", 0, blank=True)
    with _connect() as db:
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
        number = _meta(db, "current_map_number")
        if not number:
            raise ValueError("Start a map before finishing it.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) DO UPDATE SET detonated=excluded.detonated",
                   (eid, mid, expedition, count))
        _patch_first(db, "expedition_id", eid, {33: "" if count is None else count})
        _set_meta(db, "pending_new_map", True)
        _record_commit(db, "Map totals", mid, eid, details={"kills": values, "detonated": count})
    return get_state()


def mark_next_map(pending):
    with _connect() as db:
        if _meta(db, "ocr_pending"):
            raise ValueError("Save or discard the scanned remnant before changing the map marker.")
        if not _meta(db, "current_map_number"):
            raise ValueError("Log a remnant before marking the next map.")
        _set_meta(db, "pending_new_map", bool(pending))
    return get_state()


def start_map():
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = _meta(db, "current_map_number", 0)
        if current and not _meta(db, "pending_new_map"):
            raise ValueError("Finish the active map before starting the next one.")
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
        number = current + 1
        mid = _map_id(number)
        config = _meta(db, "settings")
        _set_meta(db, "previous_expedition", config["expedition"])
        _set_meta(db, "previous_waystone_settings", {key: config.get(key) for key in WAYSTONE_DEFAULTS})
        if current:
            config.update(WAYSTONE_DEFAULTS)
        config["expedition"] = 1
        db.execute("INSERT INTO maps VALUES(?,?,?,?)",
                   (mid, _dump(_snapshot(config)), _dump([None, None, None]), _now()))
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?)",
                   (_exp_id(mid, 1), mid, 1, None))
        _set_meta(db, "settings", config)
        _set_meta(db, "current_map_number", number)
        _set_meta(db, "pending_new_map", False)
    return get_state()


def undo_empty_map():
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _meta(db, "ocr_pending"):
            raise ValueError("Save or discard the scanned remnant first.")
        number = _meta(db, "current_map_number", 0)
        if not number:
            raise ValueError("There is no active map to undo.")
        if _meta(db, "pending_new_map"):
            _set_meta(db, "pending_new_map", False)
        else:
            mid = _map_id(number)
            if (any(db.execute(f"SELECT 1 FROM {table} WHERE map_id=? LIMIT 1", (mid,)).fetchone()
                    for table in ("legacy_export", "new_export", "currency_snapshots", "ritual_pages"))
                    or db.execute("SELECT 1 FROM maps WHERE map_id=? AND kills_json!=?", (mid, _dump([None]*3))).fetchone()
                    or db.execute("SELECT 1 FROM expeditions WHERE map_id=? AND detonated IS NOT NULL", (mid,)).fetchone()
                    or db.execute("SELECT 1 FROM commits WHERE map_id=? LIMIT 1", (mid,)).fetchone()):
                raise ValueError("This map has saved activity. It cannot be undone.")
            db.execute("DELETE FROM expeditions WHERE map_id=?", (mid,))
            db.execute("DELETE FROM maps WHERE map_id=?", (mid,))
            _set_meta(db, "current_map_number", number - 1)
            config = _meta(db, "settings")
            config["expedition"] = _meta(db, "previous_expedition", 1) if number > 1 else 1
            previous = _meta(db, "previous_waystone_settings")
            if number > 1 and previous:
                config.update(previous)
            _set_meta(db, "settings", config)
    return get_state()


def clear_export_and_reset_ids():
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _set_meta(db, "session_generation", _meta(db, "session_generation", 0) + 1)
        for table in ("legacy_export", "new_export", "maps", "expeditions", "scan_links",
                      "currency_snapshots", "ritual_pages", "commits"):
            db.execute(f"DELETE FROM {table}")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('new_export','ritual_pages')")
        _set_meta(db, "current_map_number", 0)
        _set_meta(db, "next_remnant_number", 1)
        _set_meta(db, "pending_new_map", False)
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
        _set_meta(db, "previous_expedition", 1)
        _set_meta(db, "previous_waystone_settings", None)
        _set_meta(db, "scan_commit_count", 0)
        config = _meta(db, "settings")
        config["expedition"] = 1
        _set_meta(db, "settings", config)
    return get_state()


def currency_names():
    with _connect() as db:
        return [row[0] for row in db.execute("SELECT name FROM currency_items ORDER BY name")]


def add_currency_item(name):
    name = str(name or "").strip()
    if not name or len(name) > 120 or any(ch in name for ch in "\r\n\t"):
        raise ValueError("Enter a currency name under 120 characters.")
    with _connect() as db:
        existing = db.execute("SELECT name FROM currency_items WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if existing:
            return existing[0]
        if db.execute("SELECT 1 FROM item_names WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            raise ValueError("This name already exists in the Item database.")
        db.execute("INSERT OR IGNORE INTO currency_items(name) VALUES(?)", (name,))
    return name


def save_currency_icon(name, image):
    from PIL import Image

    if not isinstance(image, Image.Image) or image.width < 20 or image.height < 20:
        raise ValueError("Select a full currency inventory cell from the captured grid.")
    with _connect() as db:
        canonical = db.execute("SELECT name FROM currency_items WHERE name=? COLLATE NOCASE",
                               (str(name or "").strip(),)).fetchone()
        if canonical is None:
            raise ValueError("Add the currency name to the local Currency DB first.")
        buffer = io.BytesIO()
        image.convert("RGB").resize((96, 96)).save(buffer, format="PNG", optimize=True)
        if buffer.tell() > 100000:
            raise ValueError("The icon example is too large.")
        duplicate = db.execute("SELECT id FROM currency_icons WHERE name=? AND image_png=?",
                               (canonical[0], buffer.getvalue())).fetchone()
        if duplicate:
            return duplicate[0]
        db.execute("INSERT INTO currency_icons(name,image_png,recorded_at) VALUES(?,?,?)",
                   (canonical[0], buffer.getvalue(), _now()))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def currency_icons():
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM currency_icons ORDER BY id")]


def delete_currency_icon(icon_id):
    with _connect() as db:
        db.execute("DELETE FROM currency_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def item_names():
    with _connect() as db:
        return [row[0] for row in db.execute("SELECT name FROM item_names ORDER BY name")]


def add_item_name(name):
    name = str(name or "").strip()
    if not name or len(name) > 120 or any(ord(ch) < 32 for ch in name):
        raise ValueError("Enter an item name under 120 characters.")
    with _connect() as db:
        existing = db.execute("SELECT name FROM item_names WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if existing:
            return existing[0]
        if db.execute("SELECT 1 FROM currency_items WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            raise ValueError("This name already exists in the Currency database.")
        db.execute("INSERT INTO item_names(name) VALUES(?)", (name,))
    return name


def save_item_icon(name, image):
    from PIL import Image

    if not isinstance(image, Image.Image) or min(image.size) < 20 or max(image.size) > 512:
        raise ValueError("Crop one item's inventory icon from the screenshot.")
    with _connect() as db:
        canonical = db.execute("SELECT name FROM item_names WHERE name=? COLLATE NOCASE",
                               (str(name or "").strip(),)).fetchone()
        if canonical is None:
            raise ValueError("Add the item name to the local Item database first.")
        buffer = io.BytesIO()
        image.convert("RGB").resize((96, 96)).save(buffer, format="PNG", optimize=True)
        if buffer.tell() > 100000:
            raise ValueError("The icon example is too large.")
        duplicate = db.execute("SELECT id FROM item_icons WHERE name=? AND image_png=?",
                               (canonical[0], buffer.getvalue())).fetchone()
        if duplicate:
            return duplicate[0]
        db.execute("INSERT INTO item_icons(name,image_png,recorded_at) VALUES(?,?,?)",
                   (canonical[0], buffer.getvalue(), _now()))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def item_icons():
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM item_icons ORDER BY id")]


def delete_item_icon(icon_id):
    with _connect() as db:
        db.execute("DELETE FROM item_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def inventory_names():
    return sorted(currency_names() + item_names(), key=str.casefold)


def inventory_icons():
    return [{**row, "kind": kind} for kind, entries in
            (("currency", currency_icons()), ("item", item_icons())) for row in entries]


def save_omen_icon(name, image):
    from PIL import Image

    if not isinstance(image, Image.Image) or min(image.size) < 20 or max(image.size) > 512:
        raise ValueError("Crop one Omen icon from the screenshot.")
    with _connect() as db:
        canonical = db.execute("SELECT name FROM ritual_names WHERE name=? COLLATE NOCASE",
                               (str(name or "").strip(),)).fetchone()
        if canonical is None:
            raise ValueError("Add the Omen name to the local list first.")
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        if buffer.tell() > 300000:
            raise ValueError("The Omen icon crop is too large.")
        duplicate = db.execute("SELECT id FROM omen_icons WHERE name=? AND image_png=?",
                               (canonical[0], buffer.getvalue())).fetchone()
        if duplicate:
            return duplicate[0]
        db.execute("INSERT INTO omen_icons(name,image_png,recorded_at) VALUES(?,?,?)",
                   (canonical[0], buffer.getvalue(), _now()))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def omen_icons():
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM omen_icons ORDER BY id")]


def delete_omen_icon(icon_id):
    with _connect() as db:
        db.execute("DELETE FROM omen_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def currency_target_map(phase):
    if phase not in ("start", "end"):
        raise ValueError("Choose start or end inventory.")
    with _connect() as db:
        number = _meta(db, "current_map_number", 0)
        if phase == "start" and (not number or _meta(db, "pending_new_map", False)):
            number += 1
        if not number:
            raise ValueError("Start a map before recording end inventory.")
        return _map_id(number)


def save_currency_snapshot(phase, items, expected_map_id=None):
    map_id = currency_target_map(phase)
    if not isinstance(items, list) or len(items) > 60:
        raise ValueError("A snapshot needs up to 60 inventory rows.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number", 0)
        if phase == "start" and (not number or _meta(db, "pending_new_map", False)):
            number += 1
        if not number:
            raise ValueError("Start a map before recording end inventory.")
        map_id = _map_id(number)
        if expected_map_id is not None and map_id != expected_map_id:
            raise ValueError(f"This currency scan belongs to {expected_map_id}. Scan {map_id} before saving.")
        known = {row[0].lower(): row[0] for row in db.execute("SELECT name FROM currency_items")}
        item_catalog = {row[0].lower(): row[0] for row in db.execute("SELECT name FROM item_names")}
        known.update(item_catalog)
        totals = {}
        for item in items:
            name = known.get(str(item.get("name") or "").strip().lower())
            if name is None:
                raise ValueError(f"Add {item.get('name')} to the Currency or Item database first.")
            quantity = _integer(item.get("quantity"), f"{name} stack count", 0, 1000000)
            totals[name] = totals.get(name, 0) + quantity
        settings = _meta(db, "settings")
        current = _map_id(_meta(db, "current_map_number", 0))
        previous = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
        context = (_load(previous[0]) if map_id == current and _meta(db, "pending_new_map") and previous
                   else _snapshot(settings))
        db.execute("INSERT INTO currency_snapshots(map_id,phase,items_json,recorded_at,snapshot_json) "
                   "VALUES(?,?,?,?,?) ON CONFLICT(map_id,phase) DO UPDATE SET "
                   "items_json=excluded.items_json,recorded_at=excluded.recorded_at",
                   (map_id, phase, _dump(totals), _now(), _dump(context)))
        _record_commit(db, "Currency", map_id, reference=phase.title() + " inventory", context=context,
                       details={"phase": phase, "items": totals,
                                "item_kinds": {name: "Item" if name.lower() in item_catalog else "Currency"
                                               for name in totals}})
    return currency_for_map(map_id)


def currency_for_map(map_id):
    with _connect() as db:
        snapshots = {row["phase"]: {"items": _load(row["items_json"]), "recorded_at": row["recorded_at"]}
                     for row in db.execute("SELECT phase,items_json,recorded_at FROM currency_snapshots "
                                           "WHERE map_id=?", (map_id,))}
    start = snapshots.get("start", {}).get("items", {})
    end = snapshots.get("end", {}).get("items", {})
    return {"map_id": map_id, "start": start, "end": end,
            "net": {name: end.get(name, 0) - start.get(name, 0) for name in set(start) | set(end)}
            if "start" in snapshots and "end" in snapshots else {},
            "recorded_at": {phase: row["recorded_at"] for phase, row in snapshots.items()}}


def export_currency_csv():
    with _connect() as db:
        snapshots = {}
        for row in db.execute("SELECT map_id,phase,items_json,recorded_at,snapshot_json FROM currency_snapshots "
                              "ORDER BY map_id,phase"):
            snapshots.setdefault(row["map_id"], {})[row["phase"]] = row
        commits = {(row["map_id"], row["reference"].split(" ", 1)[0].lower()): row["number"]
                   for row in db.execute("SELECT number,map_id,reference FROM commits WHERE kind='Currency' "
                                         "ORDER BY number")}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Map ID", "Currency", "Start Count", "End Count", "Net Change",
                     "Start Recorded UTC", "End Recorded UTC", "Start Commit #", "End Commit #",
                     *(f"Start {header}" for header in CONFIG_EXPORT_HEADERS),
                     *(f"End {header}" for header in CONFIG_EXPORT_HEADERS)])
    for map_id, phases in sorted(snapshots.items()):
        start = _load(phases["start"]["items_json"]) if "start" in phases else {}
        end = _load(phases["end"]["items_json"]) if "end" in phases else {}
        for name in sorted(set(start) | set(end)):
            both = "start" in phases and "end" in phases
            writer.writerow(_csv_row([map_id, name, start.get(name, 0) if "start" in phases else "",
                             end.get(name, 0) if "end" in phases else "",
                             end.get(name, 0) - start.get(name, 0) if both else "",
                             phases["start"]["recorded_at"] if "start" in phases else "",
                             phases["end"]["recorded_at"] if "end" in phases else "",
                             commits.get((map_id, "start"), "") if "start" in phases else "",
                             commits.get((map_id, "end"), "") if "end" in phases else "",
                             *_config_export_values(_load(phases["start"]["snapshot_json"]) if "start" in phases else {}),
                             *_config_export_values(_load(phases["end"]["snapshot_json"]) if "end" in phases else {})]))
    return output.getvalue().encode("utf-8-sig")


def ritual_names():
    with _connect() as db:
        return [row[0] for row in db.execute("SELECT name FROM ritual_names ORDER BY name")]


def add_ritual_name(name):
    name = str(name or "").strip()
    if not name or len(name) > 160 or any(ch in name for ch in "\r\n\t"):
        raise ValueError("Enter an Omen name under 160 characters.")
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO ritual_names VALUES(?)", (name,))
    return name


def ritual_pages_for_map(map_id):
    with _connect() as db:
        return [{"page_number": row["page_number"], "items": _load(row["items_json"]),
                 "recorded_at": row["recorded_at"]}
                for row in db.execute("SELECT page_number,items_json,recorded_at FROM ritual_pages "
                                      "WHERE map_id=? ORDER BY page_number", (map_id,))]


def save_ritual_page(items, raw_text="", scan_hash=None, expected_map_id=None,
                     tribute_available=None, rerolls_remaining=None):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Review up to 100 Ritual rewards on a page.")
    raw_text = str(raw_text or "")
    if len(raw_text) > 25000:
        raise ValueError("The Ritual OCR text is too long.")
    if scan_hash is not None and not re.fullmatch(r"[a-f0-9]{64}", str(scan_hash)):
        raise ValueError("Invalid Ritual capture fingerprint.")
    tribute_available = _integer(tribute_available, "Available Tribute", 0, 1000000000, blank=True)
    rerolls_remaining = _integer(rerolls_remaining, "Rerolls remaining", 0, 1000, blank=True)
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Review the Ritual reward rows first.")
        category = str(item.get("category") or "Item").strip().title()
        name = str(item.get("name") or "").strip()
        if category not in ("Omen", "Item") or not name or len(name) > 200:
            raise ValueError("Each Ritual reward needs an Omen/Item type and a name under 200 characters.")
        quantity = _integer(item.get("quantity", 1), "Ritual quantity", 1, 1000000)
        tribute = _integer(item.get("tribute"), "Tribute", 0, 1000000000, blank=True)
        source = str(item.get("source") or "").strip()
        if len(source) > 500:
            raise ValueError("Ritual OCR source text is too long.")
        deferred = item.get("deferred", False)
        if type(deferred) is not bool:
            raise ValueError("Deferred must be checked or unchecked.")
        cleaned.append({"category": category, "name": name, "quantity": quantity,
                        "tribute": tribute, "source": source, "deferred": deferred})
    if not cleaned:
        raise ValueError("Add or review at least one Ritual reward before saving a page.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number", 0)
        if not number or _meta(db, "pending_new_map"):
            raise ValueError("Start the current map before saving Ritual rewards.")
        map_id = _map_id(number)
        if expected_map_id is not None and map_id != expected_map_id:
            raise ValueError(f"This Ritual scan belongs to {expected_map_id}. Scan {map_id} before saving.")
        old = (db.execute("SELECT id,page_number FROM ritual_pages WHERE map_id=? AND scan_hash=?",
                          (map_id, scan_hash)).fetchone() if scan_hash else None)
        if old:
            db.execute("UPDATE ritual_pages SET items_json=?,raw_text=?,recorded_at=? WHERE id=?",
                       (_dump(cleaned), raw_text, _now(), old["id"]))
            page = old["page_number"]
        else:
            page = db.execute("SELECT COALESCE(MAX(page_number),0)+1 FROM ritual_pages WHERE map_id=?",
                              (map_id,)).fetchone()[0]
            db.execute("INSERT INTO ritual_pages(map_id,page_number,scan_hash,raw_text,items_json,recorded_at,snapshot_json) "
                       "VALUES(?,?,?,?,?,?,?)", (map_id, page, scan_hash, raw_text, _dump(cleaned), _now(),
                                               _dump(_snapshot(_meta(db, "settings")))))
        commit_number = _record_commit(db, "Ritual", map_id, reference=f"Page {page}",
                                       details={"page": page, "items": cleaned, "raw_text": raw_text,
                                                "tribute_available": tribute_available,
                                                "rerolls_remaining": rerolls_remaining})
        return {"map_id": map_id, "page_number": page, "count": len(cleaned),
                "updated": bool(old), "scan_commit_number": commit_number}


def export_ritual_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Map ID", "Ritual Page", "Type", "Name", "Quantity", "Tribute",
                     "OCR Source", "Page OCR Text", "Recorded UTC", "Scan Commit #",
                     "Deferred", "New Find Quantity", "Ritual Tribute Available", "Ritual Rerolls Remaining",
                     *CONFIG_EXPORT_HEADERS])
    with _connect() as db:
        commits = {(row["map_id"], row["reference"]): (row["number"], _load(row["details_json"]))
                   for row in db.execute("SELECT number,map_id,reference,details_json FROM commits WHERE kind='Ritual' "
                                         "ORDER BY number")}
        for page in db.execute("SELECT map_id,page_number,items_json,raw_text,recorded_at,snapshot_json "
                               "FROM ritual_pages ORDER BY map_id,page_number"):
            number, details = commits.get((page["map_id"], f"Page {page['page_number']}"), ("", {}))
            for index, item in enumerate(_load(page["items_json"])):
                writer.writerow(_csv_row([page["map_id"], page["page_number"],
                                         item["category"], item["name"], item["quantity"],
                                         item["tribute"] if item["tribute"] is not None else "",
                                         item["source"], page["raw_text"] if index == 0 else "",
                                         page["recorded_at"],
                                         number, bool(item.get("deferred")),
                                         0 if item.get("deferred") else item["quantity"],
                                         details.get("tribute_available"), details.get("rerolls_remaining"),
                                         *_config_export_values(_load(page["snapshot_json"]))]))
    return output.getvalue().encode("utf-8-sig")


def export_maps_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Map ID", "Tier", "Area Level", "Waystone %", "Map Mods",
                     "+2 Tablet Mods", "Total Mods", "Irradiated", "Atlas Master",
                     "Aldur's Saga", "Normal Kills", "Magic Kills", "Rare Kills",
                     "Expedition 1 Detonated", "Expedition 2 Detonated", "Map Recorded UTC",
                     "Item Rarity %", "Monster Rarity %", "Pack Size %", "Effectiveness %",
                     "Waystone Name", "Waystone Modifiers", "Tablet 1 Modifiers",
                     "Tablet 2 Modifiers", "Tablet 3 Modifiers", "Tablet 4 Modifiers",
                     "Biome", "City Type", "Ocean Map",
                     *[f"Map Mod {n}" for n in range(1, 11)], "Last Commit #",
                     "Perk 1", "Perk 2", "Perk 3", "Perk 4", "Master +Mods", "Base Map Mods",
                     "# +2 Mod Tablets", "Tablets Used", *TABLET_EXPORT_HEADERS, *TABLET_DETAIL_HEADERS])
    with _connect() as db:
        commits = {row["map_id"]: row["number"] for row in db.execute(
            "SELECT number,map_id FROM commits WHERE map_id!='' ORDER BY number")}
        for row in db.execute("SELECT map_id,snapshot_json,kills_json,recorded_at FROM maps ORDER BY map_id"):
            context = _load(row["snapshot_json"])
            if not context:
                hit = None
                for table in ("legacy_export", "new_export"):
                    hit = db.execute(f"SELECT row_json FROM {table} WHERE map_id=? AND remnant_id IS NOT NULL "
                                     "AND remnant_id!='' ORDER BY position LIMIT 1",
                                     (row["map_id"],)).fetchone()
                    if hit:
                        break
                if hit:
                    values = _load(hit[0])
                    context = {"tier": values[4], "area": values[5], "waystone": values[23],
                               "map_mods": values[6], "tablet_mods": values[8],
                               "total_mods": values[9], "irradiated": values[17],
                               "master": values[11], "aldur": values[10], "perks": values[12:16],
                               "master_adds_mod": values[16], "tablets": values[7],
                               "tablets_used": values[34] if len(values) > 34 else "",
                               "tablet_values": values[35:67]}
            kills = _load(row["kills_json"])
            detonated = {r["number"]: r["detonated"] for r in db.execute(
                "SELECT number,detonated FROM expeditions WHERE map_id=?", (row["map_id"],))}
            values = [row["map_id"], context.get("tier", ""),
                                     context.get("area", ""), context.get("waystone", ""),
                                     context.get("map_mods", ""), context.get("tablet_mods", ""),
                                     context.get("total_mods", ""), context.get("irradiated", ""),
                                     context.get("master", ""), context.get("aldur", ""),
                                     *[v if v is not None else "" for v in kills],
                                     *[detonated.get(n) if detonated.get(n) is not None else "" for n in (1, 2)],
                                     row["recorded_at"],
                                     *[context.get(key) if context.get(key) is not None else ""
                                       for key in ("item_rarity", "monster_rarity", "pack_size", "effectiveness")],
                                     context.get("waystone_name", ""),
                                     "\n".join(mod for mod in context.get("waystone_mods", []) if mod),
                                     *["\n".join(mods) for mods in context.get(
                                         "tablet_raw_mods", [[] for _ in range(4)])],
                                     context.get("biome", ""), context.get("city_type", ""),
                                     context.get("ocean", ""),
                                     *(list(context.get("waystone_mods", [])[:10]) +
                                       [""] * max(0, 10 - len(context.get("waystone_mods", [])))),
                                     commits.get(row["map_id"], ""),
                                     *(list(context.get("perks") or [])[:4] +
                                       [""] * max(0, 4 - len(context.get("perks") or []))),
                                     context.get("master_adds_mod", ""), context.get("base_map_mods", ""),
                                     context.get("tablets", ""), context.get("tablets_used", ""),
                                     *(list(context.get("tablet_values") or [])[:32] +
                                       [""] * max(0, 32 - len(context.get("tablet_values") or []))),
                                     *_tablet_detail_values(context)]
            writer.writerow(_csv_row([int(v) if isinstance(v, float) and v.is_integer() else v
                                     for v in values]))
    return output.getvalue().encode("utf-8-sig")


def get_state():
    with _connect() as db:
        config = _meta(db, "settings")
        n = _meta(db, "current_map_number")
        mid = _map_id(n) if n else ""
        eid = _exp_id(mid, config["expedition"]) if n else ""
        k = db.execute("SELECT kills_json FROM maps WHERE map_id=?", (mid,)).fetchone()
        d = db.execute("SELECT detonated FROM expeditions WHERE expedition_id=?", (eid,)).fetchone()
        families = [{"id": row["id"], "top_socket": row["top_socket"],
                     "valid": bool(row["valid"]), "recipes": _load(row["recipes_json"])}
                    for row in db.execute("SELECT * FROM families ORDER BY id")]
        seed_states = [{"family": row["family"], "sockets": row["sockets"],
                        "seed_slot": row["seed_slot"], "seed_rune": row["seed_rune"],
                        "rewards": _load(row["rewards_json"]), "status": row["status"]}
                       for row in db.execute("SELECT * FROM seed_states ORDER BY family,sockets")]
        masters = {}
        for master in ("Jado", "Doryani", "Hilda"):
            masters[master] = [dict(row) for row in db.execute(
                "SELECT name,tier,effect FROM master_perks WHERE master=? ORDER BY rowid", (master,))]
        recent = []
        for table in ("new_export", "legacy_export"):
            for row in db.execute(f"SELECT remnant_id,map_id,expedition_id,row_json FROM {table} "
                                  "WHERE remnant_id IS NOT NULL AND remnant_id!='' AND (chain_step IS NULL OR chain_step='') "
                                  "ORDER BY position DESC LIMIT 250"):
                r = _load(row[3])
                if r[18] == "X":
                    recent.append({"remnant_id": row[0], "map_id": row[1], "expedition_id": row[2],
                                   "first_recipe": r[0], "sockets": r[2]})
                if len(recent) >= 10:
                    break
            if len(recent) >= 10:
                break
        chain = []
        for table in ("legacy_export", "new_export"):
            for row in db.execute(f"SELECT chain_step,row_json FROM {table} WHERE expedition_id=? "
                                  "AND chain_step IS NOT NULL AND chain_step!='' ORDER BY chain_step", (eid,)):
                values = _load(row["row_json"])
                chain.append({"step": row["chain_step"], "rune1": values[26], "rune2": values[27]})
        chain.sort(key=lambda item: item["step"])
        return {
            "settings": config, "area_level": area_level(config),
            "current_map_id": mid, "current_expedition_id": eid,
            "next_remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
            "scan_commit_count": _meta(db, "scan_commit_count", 0),
            "ocr_pending": _meta(db, "ocr_pending"),
            "pending_new_map": _meta(db, "pending_new_map"),
            "kills": _load(k[0]) if k else [None, None, None],
            "detonated": d[0] if d else None,
            "affixes": [r[0] for r in db.execute("SELECT name FROM affixes ORDER BY rowid")],
            "masters": masters, "families": families, "seed_states": seed_states,
            "recipes": [r[0] for r in db.execute("SELECT name FROM recipes ORDER BY name")],
            "runes": [r[0] for r in db.execute("SELECT DISTINCT seed_rune FROM seed_states WHERE seed_rune!='Unresolved' ORDER BY seed_rune")],
            "chain": chain, "recent": recent,
            "counts": {"historical_rows": db.execute("SELECT count(*) FROM legacy_export").fetchone()[0],
                       "new_rows": db.execute("SELECT count(*) FROM new_export").fetchone()[0],
                       "saved_scans": db.execute("SELECT count(*) FROM scans").fetchone()[0],
                       "seed_states": db.execute("SELECT count(*) FROM seed_states").fetchone()[0],
            "rune_references": db.execute("SELECT count(*) FROM reviewed_glyphs").fetchone()[0]},
            "ritual_pages": db.execute("SELECT count(*) FROM ritual_pages").fetchone()[0],
            "export_folder": _meta(db, "export_folder", ""),
        }


def search_catalog(query="", kind="families", limit=100):
    q = str(query).strip().lower()
    with _connect() as db:
        if kind == "families":
            rows = [{"family": r["id"], "top_socket": r["top_socket"],
                     "valid": bool(r["valid"]), "recipes": _load(r["recipes_json"])}
                    for r in db.execute("SELECT * FROM families ORDER BY id")]
        elif kind == "recipes":
            rows = [dict(r) for r in db.execute("SELECT * FROM recipes ORDER BY name")]
        elif kind == "aliases":
            rows = [dict(r) for r in db.execute("SELECT alias,target FROM aliases")]
        elif kind == "affixes":
            rows = [{"name": r[0]} for r in db.execute("SELECT name FROM affixes ORDER BY rowid")]
        elif kind == "seed_states":
            rows = [{"family": r["family"], "sockets": r["sockets"],
                     "seed_slot": r["seed_slot"], "seed_rune": r["seed_rune"],
                     "rewards": _load(r["rewards_json"]), "status": r["status"]}
                    for r in db.execute("SELECT * FROM seed_states ORDER BY family,sockets")]
        else:
            raise ValueError("Choose families, recipes, aliases, affixes, or visible seeds.")
    if q:
        rows = [r for r in rows if q in _dump(r).lower() or
                (kind == "families" and q in f"family {r['family']}")]
    return rows[:max(1, min(int(limit), 500))]


def export_csv(*, _db=None):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        writer.writerow([*_meta(db, "export_headers"), *EXPORT_EXTRA_HEADERS])
        for table in ("legacy_export", "new_export"):
            for item in db.execute(f"SELECT row_json FROM {table} ORDER BY position"):
                values = _load(item[0])
                if len(values) in (67, 90, 91):
                    values.extend([""] * (67 + len(EXPORT_EXTRA_HEADERS) - len(values)))
                if len(values) != 67 + len(EXPORT_EXTRA_HEADERS):
                    raise ValueError("Saved Export row has an invalid width.")
                writer.writerow(_csv_row(values))
    return out.getvalue().encode("utf-8-sig")


def export_commits_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Scan Commit #", "Type", "Map ID", "Expedition ID", "Reference", "Recorded UTC"])
    with _connect() as db:
        for row in db.execute("SELECT number,kind,map_id,expedition_id,reference,recorded_at "
                              "FROM commits ORDER BY number"):
            writer.writerow(_csv_row(list(row)))
    return output.getvalue().encode("utf-8-sig")


HISTORY_DETAIL_HEADERS = ("Inventory Phase", "Currency", "Quantity", "Ritual Page", "Reward Type",
                          "Reward Name", "Tribute", "OCR Source", "Page OCR Text", "Family ID",
                          "Recipe", "Socket Count", "Exact Rune Combo", "Chain Step #",
                          "Propagation Rune 1", "Propagation Rune 2", "Normal Kills (Map)",
                          "Magic Kills (Map)", "Rare Kills (Map)", "Remnants Detonated (Expedition)",
                          "Visible Seed Sockets", "Visible Seed Slot", "Visible Seed Rune", "Remnant Scan Mode",
                          "Item Name", "Item Source", "Start Count", "End Count", "Net Change",
                          "Start Recorded UTC", "End Recorded UTC", "Deferred", "New Find Quantity",
                          "Ritual Tribute Available", "Ritual Rerolls Remaining")


def export_record_history_csv(*, _db=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Scan Commit #", "Type", "Map ID", "Expedition ID", "Reference", "Recorded UTC",
                     *HISTORY_DETAIL_HEADERS, *CONFIG_EXPORT_HEADERS, "Tablet Slot Capacity",
                     *(f"{master} Configured Perk {n}" for master in ("Jado", "Doryani", "Hilda")
                       for n in range(1, 5))])
    starts = {}
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        for commit in db.execute("SELECT * FROM commits ORDER BY number"):
            context = _load(commit["snapshot_json"])
            details = _load(commit["details_json"])
            base = [commit[key] for key in ("number", "kind", "map_id", "expedition_id", "reference", "recorded_at")]
            shared = {}
            entries = []
            if commit["kind"] == "Currency":
                phase = details.get("phase", "")
                shared["Inventory Phase"] = phase
                totals = details.get("items", {})
                start = starts.get(commit["map_id"])
                names = set(totals) | (set(start["items"]) if phase == "end" and start else set())
                kinds = {**(start["kinds"] if phase == "end" and start else {}),
                         **details.get("item_kinds", {})}
                for name in sorted(names):
                    is_item = kinds.get(name) == "Item"
                    amount = totals.get(name, 0)
                    entries.append({"Item Name" if is_item else "Currency": name, "Quantity": amount,
                                    "Item Source": "Inventory" if is_item else "",
                                    "Start Count": amount if phase == "start" else
                                                   start["items"].get(name, 0) if start else "",
                                    "End Count": amount if phase == "end" else "",
                                    "Net Change": amount - start["items"].get(name, 0)
                                                  if phase == "end" and start else "",
                                    "Start Recorded UTC": commit["recorded_at"] if phase == "start" else
                                                          start["recorded_at"] if start else "",
                                    "End Recorded UTC": commit["recorded_at"] if phase == "end" else ""})
                if phase == "start":
                    starts[commit["map_id"]] = {"items": totals, "kinds": kinds,
                                               "recorded_at": commit["recorded_at"]}
            elif commit["kind"] == "Ritual":
                shared = {"Ritual Page": details.get("page", ""), "Page OCR Text": details.get("raw_text", ""),
                          "Ritual Tribute Available": details.get("tribute_available", ""),
                          "Ritual Rerolls Remaining": details.get("rerolls_remaining", "")}
                entries = [{"Reward Type": item["category"], "Reward Name": item["name"],
                            "Quantity": item["quantity"], "Tribute": item["tribute"], "OCR Source": item["source"],
                            "Deferred": bool(item.get("deferred", False)),
                            "New Find Quantity": 0 if item.get("deferred") else item["quantity"],
                            "Item Name": item["name"] if item["category"] == "Item" else "",
                            "Item Source": "Ritual" if item["category"] == "Item" else ""}
                           for item in details.get("items", [])]
            elif commit["kind"] == "Remnant":
                shared["Family ID"] = details.get("family", "")
                seed = details.get("visible_seed") or {}
                shared.update({"Visible Seed Sockets": seed.get("sockets", ""),
                               "Visible Seed Slot": seed.get("slot", ""),
                               "Visible Seed Rune": seed.get("rune", ""),
                               "Remnant Scan Mode": seed.get("mode", "")})
                entries = [{"Recipe": item["recipe"], "Socket Count": item["sockets"], "Exact Rune Combo": item["combo"]}
                           for item in details.get("recipes", [])]
            elif commit["kind"] == "Chain":
                entries = [{"Chain Step #": item["step"], "Propagation Rune 1": item["rune1"],
                            "Propagation Rune 2": item["rune2"]} for item in details.get("steps", [])]
            kills = details.get("kills", [])
            shared.update({name: amount for name, amount in zip(HISTORY_DETAIL_HEADERS[16:19], kills)})
            if "detonated" in details:
                shared["Remnants Detonated (Expedition)"] = details["detonated"]
            selections = context.get("master_selections", {})
            configured = [selections.get(master, [""] * 4)[n]
                          for master in ("Jado", "Doryani", "Hilda") for n in range(4)]
            config_values = [*_config_export_values(context), context.get("tablet_capacity", ""), *configured]
            for entry in entries or [{}]:
                values = {**shared, **entry}
                entry_base = [*base]
                if commit["kind"] == "Currency" and entry.get("Item Name"):
                    entry_base[1] = "Item"
                writer.writerow(_csv_row([*entry_base, *(values.get(name, "") for name in HISTORY_DETAIL_HEADERS),
                                           *config_values]))
    return output.getvalue().encode("utf-8-sig")


def export_all_csv():
    with _connect() as db:
        db.execute("BEGIN")
        remnant_data = export_csv(_db=db)
        history_data = export_record_history_csv(_db=db)
        item_columns = {row[0].casefold(): f"Item: {row[0]}"
                        for row in db.execute("SELECT name FROM item_names ORDER BY name")}
    remnant_reader = csv.DictReader(io.StringIO(remnant_data.decode("utf-8-sig")))
    remnant_headers = list(remnant_reader.fieldnames)
    remnants = list(remnant_reader)
    history_reader = csv.DictReader(io.StringIO(history_data.decode("utf-8-sig")))
    headers = remnant_headers + [name for name in history_reader.fieldnames
                                if name not in remnant_headers and name != "Recipe"]
    headers.extend(item_columns.values())
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    by_commit = {}
    for row in remnants:
        number = row.get("Scan Commit #")
        if number:
            by_commit.setdefault(number, []).append(row)
        else:
            row["Type"] = "Chain" if row.get("Chain Step #") else "Remnant" if row.get("Remnant ID") else "Map totals"
            writer.writerow(row)
    offsets = {}
    for history in history_reader:
        number = history["Scan Commit #"]
        matching = by_commit.get(number, [])
        offset = offsets.get(number, 0)
        if history["Type"] in ("Remnant", "Chain") and offset < len(matching):
            row = {**matching[offset], **history}
            offsets[number] = offset + 1
        else:
            row = dict(history)
            row["Matched Recipe"] = history.get("Recipe", "")
            if history["Type"] == "Remnant":
                row["Remnant ID"] = history["Reference"]
            if row.get("Expedition ID"):
                row["Expedition #"] = int(row["Expedition ID"].rsplit("-E", 1)[-1])
        item_column = item_columns.get(history.get("Item Name", "").casefold())
        if item_column:
            row[item_column] = history.get("New Find Quantity", "") if history["Type"] == "Ritual" else history.get("Quantity", "")
        writer.writerow(row)
    for number, matching in by_commit.items():
        for row in matching[offsets.get(number, 0):]:
            row["Type"] = "Chain" if row.get("Chain Step #") else "Remnant" if row.get("Remnant ID") else "Map totals"
            writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def _csv_row(values):
    return [("'" + value) if isinstance(value, str) and
            value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values]


def save_export_folder(folder):
    if not isinstance(folder, str) or len(folder) > 1024:
        raise ValueError("Choose an existing local export folder.")
    path = Path(folder.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir() or str(path).startswith("\\\\"):
        raise ValueError("Enter an existing local folder's full path.")
    with _connect() as db:
        _set_meta(db, "export_folder", str(path.resolve()))
    return {"export_folder": str(path.resolve())}


def save_reference_export_folder(folder):
    if not isinstance(folder, str) or len(folder) > 1024:
        raise ValueError("Choose an existing local reference export folder.")
    path = Path(folder.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir() or str(path).startswith("\\\\"):
        raise ValueError("Enter an existing local folder's full path.")
    with _connect() as db:
        _set_meta(db, "reference_export_folder", str(path.resolve()))
    return str(path.resolve())


def save_export_file(kind="xlsx"):
    if kind not in ("xlsx", "csv"):
        raise ValueError("Choose XLSX or Export CSV.")
    with _connect() as db:
        folder = _meta(db, "export_folder", "")
    if not folder:
        raise ValueError("Set an export folder first.")
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError("The export folder no longer exists. Choose another folder.")
    if kind == "xlsx":
        from PoE2_Data_Logger.core.workbook_export import export_xlsx
        data = export_xlsx()
        filename = "PoE2_Export.xlsx"
    else:
        data = export_all_csv()
        filename = "PoE2_Export.csv"
    destination = directory / filename
    _write_export_bytes(destination, data)
    return {"path": str(destination), "bytes": len(data)}


def _write_export_bytes(destination, data):
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix=".PoE2_Data_Export_", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def backup_bytes():
    initialize()
    with store._connect() as source:
        destination = sqlite3.connect(":memory:")
        try:
            source.backup(destination)
            data = destination.serialize()
        finally:
            destination.close()
    return data
