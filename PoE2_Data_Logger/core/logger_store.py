"""Maintain session state, export rows and commit snapshots in the shared SQLite store.

Current totals, inventories and export rows may be replaced; commit snapshots
retain recorded event details for history exports.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
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
from functools import lru_cache
from pathlib import Path

from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, affix_key, affix_unit, modifier_value, tablet_random_mod_counts


HERE = Path(__file__).resolve().parent.parent
_INIT_LOCK = threading.Lock()
_EXPORT_WRITE_LOCK = threading.Lock()
_READY = False
_UNSET = object()
_RETIRED_CURRENCY_DEFAULTS = frozenset(name.casefold() for name in (
    "Omen of Corruption", "Omen of Dextral Alchemy", "Omen of Dextral Coronation",
    "Omen of Recombination", "Omen of Sinistral Alchemy", "Omen of Sinistral Coronation",
    "Black Scythe Artifact", "Broken Circle Artifact", "Exotic Coinage", "Order Artifact",
    "Sun Artifact", "Aldur's Saga",
))
BIOMES = ("None", "Water", "Mountain", "Grass", "Forest", "Swamp", "Desert", "Ocean", "Island")
CITY_TYPES = ("None", "Faridun", "Ezomyte", "Vaal")
ALDUR_AFFIXES = ("None", "All +5", "All +6", "All +7", "Lucky",
                 "1 Guaranteed 7", "1 Guaranteed 8", "1 Guaranteed 9")
WAYSTONE_DEFAULTS = {"tier": 15, "waystone": 0, "map_mods": 0, "waystone_name": "",
                     "waystone_mods": [], "item_rarity": None, "monster_rarity": None,
                     "pack_size": None, "effectiveness": None}
WAYSTONE_SETUP_FIELDS = frozenset(("tier", "waystone", "map_mods"))
MAX_RITUAL_REWARDS = 120
BASE_EXTRA_HEADERS = ("Item Rarity %", "Monster Rarity %", "Pack Size %",
                        "Effectiveness %", "Waystone Name", "Biome", "City Type", "Ocean Map",
                        "Waystone Modifiers", *(f"Map Mod {n}" for n in range(1, 11)),
                        *(f"Tablet {n} Modifiers" for n in range(1, 5)), "Scan Commit #")
TABLET_DETAIL_HEADERS = (*(label for tablet in range(1, 5) for slot in range(1, 5)
                           for label in (f"Tablet {tablet} Mod {slot} Value", f"Tablet {tablet} Mod {slot} Unit")),
                        *(f"Tablet {tablet} Random Modifiers" for tablet in range(1, 5)))
EXPORT_EXTRA_HEADERS = (*BASE_EXTRA_HEADERS, *TABLET_DETAIL_HEADERS, "Deli", "Wisp")
ATLAS_EXPORT_HEADERS = ("Atlas Setup ID", "Gear Item Rarity %")
TABLET_EXPORT_HEADERS = tuple(label for tablet in range(1, 5) for slot in range(1, 5)
                             for label in (f"Tablet {tablet} Mod {slot} Affix", f"Tablet {tablet} Mod {slot} %"))
CONFIG_EXPORT_HEADERS = ("Tier", "Area Level", "Base Map Mods", "Map Mods", "# +2 Mod Tablets",
                         "Tablet Mods", "Total Mods", "Aldur's Saga Affix", "Atlas Master",
                         "Perk 1", "Perk 2", "Perk 3", "Perk 4", "Master +Mods", "Irradiated",
                         "Waystone %", "Tablets Used", *BASE_EXTRA_HEADERS[:-1], *TABLET_EXPORT_HEADERS,
                         *TABLET_DETAIL_HEADERS, *ATLAS_EXPORT_HEADERS, "Deli", "Wisp")


def _tablet_detail_values(context):
    """Flatten active tablet values, units and random-modifier counts into export columns."""
    pairs = list(context.get("tablet_affixes") or [])
    values = []
    for i in range(16):
        pair = pairs[i] if i < len(pairs) else {}
        active = pair.get("affix") and i // 4 < int(context.get("tablet_capacity", context.get("tablets_used")) or 0)
        values.extend([pair.get("value", ""), pair.get("unit", "%")] if active else ["", ""])
    random = list(context.get("tablet_random_mods") or [])
    return [*values, *(random[:4] + [""] * max(0, 4 - len(random)))]


def _config_export_values(context):
    """Build the ordered configuration columns from a saved context, padding absent slots."""
    perks = list(context.get("perks") or [])
    tablets = list(context.get("tablet_values") or [])
    return [*[context.get(key, "") for key in ("tier", "area", "base_map_mods", "map_mods", "tablets",
             "tablet_mods", "total_mods", "aldur", "master")],
            *(perks[:4] + [""] * max(0, 4 - len(perks))),
            *[context.get(key, "") for key in ("master_adds_mod", "irradiated", "waystone", "tablets_used")],
            *_extra_export_values(context)[:len(BASE_EXTRA_HEADERS) - 1],
            *(tablets[:32] + [""] * max(0, 32 - len(tablets))), *_tablet_detail_values(context),
            *_atlas_export_values(context),
            context.get("deli", ""), context.get("wisp", "")]


def _atlas_export_values(context):
    """Project the setup ID and optional gear rarity into Atlas export columns."""
    rarity = context.get("gear_item_rarity")
    return [context.get("atlas_setup_id", ""), "" if rarity is None else rarity]


def _extra_export_values(context):
    """Flatten modifier text and map extras, reserving a blank scan-commit column."""
    mods = list(context.get("waystone_mods") or [])
    tablets = list(context.get("tablet_raw_mods") or [])
    return [*[context.get(key) if context.get(key) is not None else ""
              for key in ("item_rarity", "monster_rarity", "pack_size", "effectiveness")],
            context.get("waystone_name", ""), context.get("biome", ""),
            context.get("city_type", ""), context.get("ocean", ""),
            "\n".join(mod for mod in mods if mod),
            *(mods[:10] + [""] * max(0, 10 - len(mods))),
            *("\n".join(tablets[n]) if n < len(tablets) else "" for n in range(4)), "",
            *_tablet_detail_values(context), context.get("deli", ""), context.get("wisp", "")]


def _dump(value):
    """Encode stored values as compact JSON while preserving Unicode text."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value):
    """Decode JSON persisted in the shared store."""
    return json.loads(value)


@lru_cache(maxsize=1)
def _atlas_catalog_data():
    """Cache the bundled Atlas catalog for validation and snapshot construction."""
    from PoE2_Data_Logger.core.atlas_catalog import catalog
    return catalog()


@lru_cache(maxsize=1)
def _atlas_catalog_identity():
    """Cache the catalog fingerprint and canonical JSON used to freeze its dataset."""
    data = _atlas_catalog_data()
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded


def _atlas_defaults():
    """Create an empty allocation tied to the current Atlas catalog identity."""
    return {"catalog_version": _atlas_catalog_data()["version"],
            "catalog_id": _atlas_catalog_identity()[0], "allocated": [], "choices": {},
            "gear_item_rarity": None}


def _validate_atlas_settings(data):
    """Validate catalog version, allocations, choices and gear rarity; return normalized settings."""
    if not isinstance(data, dict):
        raise ValueError("Atlas settings are invalid.")
    catalog = _atlas_catalog_data()
    if data.get("catalog_version", catalog["version"]) != catalog["version"]:
        raise ValueError("The Atlas data changed. Reload the Atlas editor before saving.")
    nodes = catalog["nodes"]
    allocated = data.get("allocated", [])
    choices = data.get("choices", {})
    if (not isinstance(allocated, list) or not isinstance(choices, dict) or
            len(allocated) > len(nodes) or len(choices) > len(nodes)):
        raise ValueError("Atlas allocations are invalid.")
    if any(not isinstance(node_id, str) or node_id not in nodes or
           not nodes[node_id].get("allocatable") for node_id in allocated):
        raise ValueError("Choose allocatable nodes from the Atlas tree.")
    if len(allocated) != len(set(allocated)):
        raise ValueError("An Atlas node cannot be allocated more than once.")
    clean_choices = {}
    for node_id, option_id in choices.items():
        node = nodes.get(node_id) if isinstance(node_id, str) else None
        if (not node or not node.get("allocatable") or not isinstance(option_id, str) or
                option_id not in {item["id"] for item in node.get("choices", [])}):
            raise ValueError("Choose an effect from that Atlas node's dropdown.")
        clean_choices[node_id] = option_id
    rarity = data.get("gear_item_rarity")
    if isinstance(rarity, bool):
        raise ValueError("Gear Item Rarity % must be a number.")
    return {"catalog_version": catalog["version"], "catalog_id": _atlas_catalog_identity()[0],
            "allocated": sorted(allocated), "choices": dict(sorted(clean_choices.items())),
            "gear_item_rarity": _number(rarity, "Gear Item Rarity %", 0, 9999)
                                if rarity not in (None, "") else None}


def _atlas_snapshot(config):
    """Derive a content-addressed setup ID and frozen Atlas fields from configuration."""
    settings = config.get("atlas_settings")
    if settings is None:
        return {}
    # Include the dataset fingerprint, even if its human-readable version stays unchanged.
    frozen = {"catalog_version": settings["catalog_version"],
              "catalog_id": settings["catalog_id"], "allocated": sorted(settings["allocated"]),
              "choices": dict(sorted(settings["choices"].items())),
              "gear_item_rarity": settings.get("gear_item_rarity")}
    encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"atlas_setup_id": "A" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "atlas_catalog_id": frozen["catalog_id"], "atlas_settings": frozen,
            "gear_item_rarity": frozen["gear_item_rarity"]}


def _register_atlas_snapshot(db, context):
    """Persist a setup and its catalog once; reject a missing noncurrent dataset."""
    setup_id = context.get("atlas_setup_id")
    if not setup_id or db.execute("SELECT 1 FROM atlas_setups WHERE setup_id=?", (setup_id,)).fetchone():
        return
    settings = context["atlas_settings"]
    catalog_id = settings["catalog_id"]
    if not db.execute("SELECT 1 FROM atlas_catalogs WHERE catalog_id=?", (catalog_id,)).fetchone():
        current_id, encoded = _atlas_catalog_identity()
        if catalog_id != current_id:
            raise ValueError("The saved Atlas dataset is missing. Reload the Atlas editor before saving.")
        db.execute("INSERT INTO atlas_catalogs(catalog_id,version,catalog_json) VALUES(?,?,?)",
                   (catalog_id, settings["catalog_version"], encoded))
    db.execute("INSERT INTO atlas_setups(setup_id,catalog_id,settings_json) VALUES(?,?,?)",
               (setup_id, catalog_id, _dump(settings)))


def _bind_atlas_context(db, map_id, context):
    """Bind only Atlas fields; leave the existing waystone/tablet behavior untouched."""
    saved = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
    if saved is None:
        # Start inventory can be saved before the map itself is created.
        saved = db.execute("SELECT snapshot_json FROM commits WHERE map_id=? AND kind IN "
                           "('Currency','Remnant','Chain','Ritual') ORDER BY number LIMIT 1",
                           (map_id,)).fetchone()
    result = dict(context)
    if saved is not None:
        prior = _load(saved[0])
        for key in ("atlas_setup_id", "atlas_catalog_id", "atlas_settings", "gear_item_rarity"):
            if key in prior:
                result[key] = prior[key]
            else:
                result.pop(key, None)
    _register_atlas_snapshot(db, result)
    return result


def _now():
    """Return a UTC timestamp with second precision for stored records."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _map_id(number):
    """Format a map number as its zero-padded session ID."""
    return f"M{number:04d}"


def _exp_id(map_id, number):
    """Format an expedition ID within a map."""
    return f"{map_id}-E{number:02d}"


def _meta(db, key, default=None):
    """Read a JSON metadata value, falling back when its key is absent."""
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return _load(row[0]) if row else default


def _set_meta(db, key, value):
    """Insert or replace a JSON metadata value on the supplied connection."""
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, _dump(value)))


def _waystone_settings(config):
    """Extract waystone fields with defaults and copy list values for later updates."""
    settings = {}
    for key, default in WAYSTONE_DEFAULTS.items():
        value = config.get(key, default)
        if value is None and default is not None:
            value = default
        settings[key] = list(value) if isinstance(value, list) else value
    return settings


def _prepared_waystone(db, map_id):
    """Return a staged waystone only when its map and session generation still match."""
    prepared = _meta(db, "prepared_waystone_setup")
    if (isinstance(prepared, dict) and prepared.get("map_id") == map_id and
            prepared.get("session_generation") == _meta(db, "session_generation", 0)):
        return prepared
    return None


def _map_context_settings(db, config, map_id):
    """Upcoming-map records never inherit the completed map's waystone fields."""
    match = re.fullmatch(r"M(\d+)", str(map_id or ""))
    if match and int(match[1]) > _meta(db, "current_map_number", 0):
        prepared = _prepared_waystone(db, map_id)
        result = dict(config)
        result.update(_waystone_settings(prepared["settings"] if prepared else {}))
        return result
    return config


def _cancel_prepared_waystone(db):
    """Restore settings from the next map's staged setup, then clear the staging record."""
    number = _meta(db, "current_map_number", 0) + 1
    prepared = _prepared_waystone(db, _map_id(number))
    if prepared:
        config = _meta(db, "settings")
        config.update(_waystone_settings(prepared["previous_settings"]))
        _set_meta(db, "settings", config)
    _set_meta(db, "prepared_waystone_setup", None)


def initialize():
    """Create and migrate logger tables, seed bundled data and repair defaults once per process."""
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
                CREATE TABLE IF NOT EXISTS map_unique_kills(
                    map_id TEXT PRIMARY KEY,unique_kills INTEGER);
                CREATE TABLE IF NOT EXISTS expeditions(
                    expedition_id TEXT PRIMARY KEY,map_id TEXT NOT NULL,number INTEGER NOT NULL,
                    detonated INTEGER);
                CREATE TABLE IF NOT EXISTS chain_completions(
                    expedition_id TEXT PRIMARY KEY,scan_commit_number INTEGER NOT NULL,
                    step_count INTEGER NOT NULL,next_expedition INTEGER NOT NULL,recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chain_append_receipts(
                    request_id TEXT PRIMARY KEY,session_generation INTEGER NOT NULL,
                    expedition_id TEXT NOT NULL,payload_json TEXT NOT NULL,result_json TEXT NOT NULL);
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
                CREATE TABLE IF NOT EXISTS review_icon_examples(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('currency','item','omen')),
                    columns INTEGER NOT NULL,rows INTEGER NOT NULL,
                    image_png BLOB NOT NULL,image_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(columns,rows,image_sha256));
                CREATE INDEX IF NOT EXISTS review_icon_name_idx ON review_icon_examples(name);
                CREATE TABLE IF NOT EXISTS commits(
                    number INTEGER PRIMARY KEY,kind TEXT NOT NULL,map_id TEXT,
                    expedition_id TEXT,reference TEXT,recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS atlas_catalogs(
                    catalog_id TEXT PRIMARY KEY,version TEXT NOT NULL,catalog_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS atlas_setups(
                    setup_id TEXT PRIMARY KEY,catalog_id TEXT NOT NULL,settings_json TEXT NOT NULL);
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
            from PoE2_Data_Logger.core.catalog_repairs import repair_farrul_hunt_family
            repair_farrul_hunt_family(db)
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
                               ((name,) for name in sorted(names) if name and not _retired_currency_default(name)))
                _set_meta(db, "currency_items_initialized", True)
            from PoE2_Data_Logger.ocr.currency_ocr import catalog_names, catalog_version
            catalog_key = catalog_version()
            if _meta(db, "currency_inventory_catalog_version") != catalog_key:
                db.executemany("INSERT OR IGNORE INTO currency_items(name) VALUES(?)",
                               ((name,) for name in catalog_names() if not _retired_currency_default(name)))
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
                               ((name,) for name in OMEN_NAMES if not _retired_currency_default(name)))
                _set_meta(db, "ritual_names_initialized", True)
            config = _meta(db, "settings")
            waystone = _waystone_settings(config)
            if (any(key not in config for key in ("deli", "wisp")) or
                    any(key not in config or config[key] != value for key, value in waystone.items())):
                config.update(waystone)
                config.setdefault("deli", False)
                config.setdefault("wisp", False)
                _set_meta(db, "settings", config)
            if "atlas_settings" not in config:
                config["atlas_settings"] = _atlas_defaults()
                _set_meta(db, "settings", config)
            _register_atlas_snapshot(db, _atlas_snapshot(config))
        _READY = True


def _connect():
    """Ensure logger initialization before opening a shared-store connection."""
    initialize()
    return store._connect()


def _integer(value, name, low=0, high=None, blank=False):
    """Parse a bounded whole number, rejecting booleans and optionally accepting blank values."""
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
    """Parse a bounded numeric value and return integral results as integers."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.")
    if not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return int(result) if result.is_integer() else result


def area_level(config):
    """Derive area level from tier, irradiation and the ocean flag."""
    return (79 if config["tier"] == 15 else 80) + int(config["irradiated"]) + int(config["ocean"])


def _validate_settings(db, data):
    """Merge edits with saved settings and validate modifier, tablet and master selections."""
    source = _meta(db, "settings")
    c = dict(source)
    c.update(data)
    if "atlas_settings" in data:
        c["atlas_settings"] = _validate_atlas_settings(data["atlas_settings"])
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
    c["deli"] = bool(c.get("deli", False))
    c["wisp"] = bool(c.get("wisp", False))
    c["expedition"] = _integer(c["expedition"], "Expedition #", 1)
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


def _save_settings(db, data, *, confirmed_affixes=()):
    """Save validated edits on the caller's connection and stage upcoming waystone fields.

    Update an active map snapshot before activity or while its first waystone
    setup is incomplete and start inventory is its only activity.
    """
    for name in confirmed_affixes:
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or "%" in name:
            raise ValueError("Review the tablet affix name before saving.")
        db.execute("INSERT OR IGNORE INTO affixes(name) VALUES(?)", (name.strip(),))
    c = _validate_settings(db, data)
    source = _meta(db, "settings")
    current = _meta(db, "current_map_number", 0)
    next_map = bool(_meta(db, "pending_new_map"))
    waystone_fields = WAYSTONE_DEFAULTS.keys() & data.keys()
    if waystone_fields and (not current or next_map):
        target = _map_id(current + 1)
        previous = _prepared_waystone(db, target)
        prepared_values = _waystone_settings(previous["settings"] if previous else {})
        prepared_values.update({key: c[key] for key in waystone_fields})
        fields = set(previous.get("fields", [])) if previous else set()
        fields.update(waystone_fields)
        c.update(prepared_values)
        _set_meta(db, "prepared_waystone_setup", {
            "map_id": target, "session_generation": _meta(db, "session_generation", 0),
            "settings": prepared_values, "fields": sorted(fields),
            "previous_settings": previous["previous_settings"] if previous else _waystone_settings(source),
        })
    _set_meta(db, "settings", c)
    _register_atlas_snapshot(db, _atlas_snapshot(c))
    if current and not next_map:
        mid = _map_id(current)
        saved = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (mid,)).fetchone()
        prior = _load(saved[0]) if saved else {}
        waystone_save = bool(waystone_fields)
        first_setup = (waystone_save and prior.get("waystone_setup_saved") is False and
                       _map_has_only_start_inventory(db, mid))
        if not _map_has_activity(db, mid) or first_setup:
            context = _snapshot(c)
            if first_setup:
                context = _bind_atlas_context(db, mid, context)
            if "waystone_setup_saved" in prior:
                fields = set(prior.get("waystone_setup_fields", [])) | waystone_fields
                context["waystone_setup_fields"] = sorted(fields)
                context["waystone_setup_saved"] = bool(prior["waystone_setup_saved"] or
                                                         WAYSTONE_SETUP_FIELDS <= fields)
            _register_atlas_snapshot(db, context)
            db.execute("UPDATE maps SET snapshot_json=? WHERE map_id=?", (_dump(context), mid))


def save_settings(data, *, confirmed_affixes=()):
    """Apply settings and confirmed affixes in one immediate transaction, then return refreshed state."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _save_settings(db, data, confirmed_affixes=confirmed_affixes)
    return get_state()


def save_atlas_settings(data):
    """Save the editable setup without changing Atlas data already attached to activity."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settings = _validate_atlas_settings(data)
        _save_settings(db, {"atlas_settings": settings})
        current = _meta(db, "current_map_number", 0)
        target = _atlas_settings_target(db)
        mid = _map_id(target)
        config = _meta(db, "settings")
        expedition = 1 if target != current else config["expedition"]
        _record_commit(db, "Atlas settings", mid, _exp_id(mid, expedition),
                       context=_snapshot(_map_context_settings(db, config, mid)))
    return get_state()


def add_affix(name):
    """Register a nonempty affix name if absent and return refreshed logger state."""
    name = str(name or "").strip()
    if not name or len(name) > 120:
        raise ValueError("Enter an affix name under 120 characters.")
    with _connect() as db:
        db.execute("INSERT OR IGNORE INTO affixes VALUES(?)", (name,))
    return get_state()


def add_affixes(names):
    """Register valid reviewed affix names, skipping invalid or existing entries; return additions."""
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
    """Reject recipe or family changes that invalidate affected visible-seed reward mappings."""
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
    """Upsert a canonical recipe and rune combo, rolling back edits that break family or seed constraints."""
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
    """Upsert an ordered family after resolving recipe names and checking socket and seed constraints."""
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
    """Upsert a locally verified visible rune and reward mapping for a family socket stage."""
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
    """Clear tablet modifiers and reset the scan slot in one immediate transaction."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _save_settings(db, {"tablets_used": 4, "plus_two_tablets": 0,
                            "tablet_affixes": [{"affix": "", "value": None} for _ in range(16)],
                            "tablet_raw_mods": [[] for _ in range(4)]})
        _set_meta(db, "tablet_auto_next", 1)
    return get_state()


def tablet_next_slot():
    """Read the next tablet slot reserved for sequential scanning."""
    with _connect() as db:
        return _meta(db, "tablet_auto_next", 1)


def advance_tablet_slot(number):
    """Advance the scan slot only when it still equals the supplied slot from one through four."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _meta(db, "tablet_auto_next", 1) == number and 1 <= number <= 4:
            _set_meta(db, "tablet_auto_next", number + 1)


def save_scanned_tablet(matches, raw_mods):
    """Save the next tablet and its configuration commit atomically, clearing the set at slot one."""
    if not matches or len(matches) > 4 or not isinstance(raw_mods, list):
        raise ValueError("Review the tablet modifiers before saving.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "tablet_auto_next", 1)
        if number > 4:
            raise ValueError("Four tablets are already saved. Clear tablet config before scanning a new set.")
        state = _meta(db, "settings")
        slots = ([{"affix": "", "value": None} for _ in range(16)] if number == 1 else
                 [dict(item) for item in state["tablet_affixes"]])
        raw = ([[] for _ in range(4)] if number == 1 else
               [list(item) for item in state["tablet_raw_mods"]])
        for offset, item in enumerate(matches):
            slots[(number - 1) * 4 + offset] = {"affix": item["affix"], "value": item["value"],
                                              "unit": item.get("unit") or affix_unit(item["affix"])}
        raw[number - 1] = list(raw_mods)
        _save_settings(db, {"tablets_used": max(state["tablets_used"], number),
                            "plus_two_tablets": 0 if number == 1 else state["plus_two_tablets"],
                            "tablet_affixes": slots, "tablet_raw_mods": raw})
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
    """Resolve a recipe by full name or normalized alias, rejecting ambiguous shortcuts."""
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
    """Rebuild normalized alias lookups and mark keys that name different recipe targets."""
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
    """Find valid family suffixes matching the first recipe and optional next recipe or family."""
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
    """Return family candidates and ordered recipe details for the supplied recipe selection."""
    with _connect() as db:
        return _resolve(db, first, next_recipe, family)


def _first_row(db, field, value):
    """Find the first matching export row, checking imported rows before new rows."""
    for table in ("legacy_export", "new_export"):
        row = db.execute(f"SELECT position,row_json FROM {table} WHERE {field}=? ORDER BY position LIMIT 1",
                         (value,)).fetchone()
        if row:
            return table, row["position"], _load(row["row_json"])
    return None


def _has_remnant(db, map_id):
    """Check whether either export table contains a remnant for the map."""
    return any(db.execute(f"SELECT 1 FROM {table} WHERE map_id=? AND remnant_id IS NOT NULL "
                          "AND remnant_id!='' LIMIT 1", (map_id,)).fetchone()
               for table in ("legacy_export", "new_export"))


def _map_has_activity(db, map_id):
    """Detect exported records, inventories, Ritual pages or saved count activity for a map."""
    for table in ("legacy_export", "new_export", "currency_snapshots", "ritual_pages"):
        if db.execute(f"SELECT 1 FROM {table} WHERE map_id=? LIMIT 1", (map_id,)).fetchone():
            return True
    if _unique_kills_for_map(db, map_id) is not None:
        return True
    return bool(db.execute("SELECT 1 FROM commits WHERE map_id=? AND kind IN "
                           "('Map kills','Map totals','Detonated','Propagation') LIMIT 1", (map_id,)).fetchone())


def _map_has_only_start_inventory(db, map_id):
    """Start inventory is preparation, so an unconfigured map can receive its first waystone."""
    phases = [row[0] for row in db.execute("SELECT phase FROM currency_snapshots WHERE map_id=?", (map_id,))]
    if phases != ["start"]:
        return False
    if any(db.execute(f"SELECT 1 FROM {table} WHERE map_id=? LIMIT 1", (map_id,)).fetchone()
           for table in ("legacy_export", "new_export", "ritual_pages")):
        return False
    if _unique_kills_for_map(db, map_id) is not None:
        return False
    return not db.execute("SELECT 1 FROM commits WHERE map_id=? AND kind IN "
                          "('Map kills','Map totals','Detonated','Propagation','Remnant','Chain','Ritual') LIMIT 1",
                          (map_id,)).fetchone()


def _atlas_settings_target(db):
    """Choose the current or upcoming map, advancing again when activity has frozen its setup."""
    number = _meta(db, "current_map_number", 0)
    if not number or _meta(db, "pending_new_map", False):
        number += 1
    # Start inventory may already freeze a map before its first remnant/waystone.
    if _map_has_activity(db, _map_id(number)):
        number += 1
    return number


def _patch_first(db, field, value, replacements):
    """Replace selected cells in the first matching export row and report whether one existed."""
    hit = _first_row(db, field, value)
    if hit:
        table, position, row = hit
        for index, new_value in replacements.items():
            row[index] = new_value
        db.execute(f"UPDATE {table} SET row_json=? WHERE position=?", (_dump(row), position))
    return bool(hit)


def _snapshot(config):
    """Capture export configuration with derived map totals, active tablet fields and Atlas identity."""
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
        "deli": "Yes" if config.get("deli", False) else "No",
        "wisp": "Yes" if config.get("wisp", False) else "No",
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
        **_atlas_snapshot(config),
    }


def _add_new(db, row):
    """Append a serialized export row and index its map, expedition, remnant and chain step."""
    db.execute("INSERT INTO new_export(map_id,expedition_id,remnant_id,chain_step,row_json) VALUES(?,?,?,?,?)",
               (row[22], row[32], row[19] or None, row[25] or None, _dump(row)))


def _record_commit(db, kind, map_id="", expedition_id="", reference="", *, context=None, details=None):
    """Allocate a session commit number and store event details with a configuration snapshot.

    Join an existing transaction or start an immediate one. Implicit count-event
    contexts prefer the map snapshot and retain bound Atlas fields.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    number = _meta(db, "scan_commit_count", 0) + 1
    if context is None:
        context = _snapshot(_map_context_settings(db, _meta(db, "settings"), map_id))
        if kind in ("Map kills", "Map totals", "Detonated", "Propagation"):
            saved = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
            if saved:
                context = _load(saved[0])
        context = _bind_atlas_context(db, map_id, context)
    _register_atlas_snapshot(db, context)
    db.execute("INSERT INTO commits(number,kind,map_id,expedition_id,reference,recorded_at,snapshot_json,details_json) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (number, kind, map_id, expedition_id, str(reference or ""), _now(),
                _dump(context), _dump(details or {})))
    _set_meta(db, "scan_commit_count", number)
    return number


def record_commit(kind, reference=""):
    """Record an allowed configuration event against the active or upcoming map and expedition."""
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
    """Read the generation token used to invalidate captures after an export reset."""
    with _connect() as db:
        return _meta(db, "session_generation", 0)


def scan_context():
    """Capture session, map marker and selected expedition tokens for later stale-scan checks."""
    with _connect() as db:
        number = _meta(db, "current_map_number", 0)
        return {"_scan_generation": _meta(db, "session_generation", 0),
                "_capture_map_id": _map_id(number) if number else None,
                "_capture_map_pending": bool(_meta(db, "pending_new_map", False)),
                "_capture_expedition": 1 if not number or _meta(db, "pending_new_map", False)
                                       else _meta(db, "settings")["expedition"]}


def validate_scan_context(result):
    """Reject changed capture tokens when present; accept results without a generation token."""
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


def _validate_remnant_context(db, result):
    """Keep a remnant bound to its capture when propagation advances a chain.

    Map/session changes still invalidate a reading. Only a retained remnant
    token or a committed chain on this map can account for an older expedition.
    Other scan types continue to use the stricter current-expedition check.
    """
    if not isinstance(result, dict):
        raise ValueError("The remnant capture context is invalid. Scan again.")
    pending = _meta(db, "ocr_pending")
    number = _meta(db, "current_map_number", 0)
    new_map = not number or bool(_meta(db, "pending_new_map", False))
    current_map = _map_id(number) if number else None
    if "_scan_generation" in result:
        if result["_scan_generation"] != _meta(db, "session_generation", 0):
            raise ValueError("This scan belongs to the previous session. Scan again.")
        if result.get("_capture_map_id") != current_map:
            raise ValueError("The map changed during the scan. Scan the current map again.")
        if "_capture_map_pending" in result and result["_capture_map_pending"] != bool(_meta(db, "pending_new_map", False)):
            raise ValueError("The map ended during the scan. Scan again.")
    if any(key in result for key in ("remnant_id", "expedition_id")):
        if not pending or any(result.get(key) != pending.get(key) for key in
                              ("remnant_id", "map_id", "expedition_id")):
            raise ValueError("The scanned remnant was already saved or discarded. Scan again.")
    if "_scan_generation" not in result:
        return
    mid = _map_id(number + 1 if new_map else number)
    current_expedition = 1 if new_map else _meta(db, "settings")["expedition"]
    captured = result.get("_capture_expedition", current_expedition)
    captured = _integer(captured, "Captured expedition", 1)
    if any(key in result for key in ("remnant_id", "expedition_id")) and pending["expedition_id"] != _exp_id(mid, captured):
        raise ValueError("The remnant and its capture belong to different expeditions. Scan again.")
    if captured == current_expedition:
        return
    eid = _exp_id(mid, captured)
    retained = bool(pending and pending.get("map_id") == mid and pending.get("expedition_id") == eid)
    completed = not new_map and db.execute(
        "SELECT 1 FROM commits WHERE kind='Chain' AND map_id=? AND expedition_id=? LIMIT 1", (mid, eid)).fetchone()
    if not retained and not completed:
        raise ValueError("The expedition changed during the scan. Scan the current expedition again.")


def validate_remnant_context(result):
    """Validate a remnant capture against stored session, map and retained expedition context."""
    with _connect() as db:
        _validate_remnant_context(db, result)


def assign_ocr_id(mode, expected_map_id=None, expected_generation=None, expected_expedition=None):
    """Reserve or reuse the next remnant ID for review without consuming its number.

    Validate the requested map, session and captured expedition within an immediate transaction.
    """
    if mode not in ("seed", "opened"):
        raise ValueError("Choose visible seed or opened remnant mode.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if expected_generation is not None and expected_generation != _meta(db, "session_generation", 0):
            raise ValueError("This scan belongs to the previous session. Scan again.")
        current = _meta(db, "current_map_number")
        new_map = current == 0 or _meta(db, "pending_new_map")
        number = current + 1 if new_map else current
        mid = _map_id(number)
        expedition = _integer(expected_expedition, "Captured expedition", 1) if expected_expedition is not None else (
            1 if new_map else _meta(db, "settings")["expedition"])
        _validate_remnant_context(db, {"_scan_generation": _meta(db, "session_generation", 0),
            "_capture_map_id": _map_id(current) if current else None,
            "_capture_map_pending": bool(_meta(db, "pending_new_map", False)),
            "_capture_expedition": expedition})
        pending = _meta(db, "ocr_pending")
        if pending:
            if expected_map_id is not None and pending["map_id"] != expected_map_id:
                raise ValueError("The map changed during the scan. Scan the current map again.")
            if expected_expedition is not None and pending["expedition_id"] != _exp_id(mid, expedition):
                raise ValueError("Another remnant is awaiting review for a different expedition. Save or reject that remnant first.")
            return pending
        if expected_map_id is not None and mid != expected_map_id:
            raise ValueError("The map changed during the scan. Scan the current map again.")
        pending = {"remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
                   "map_id": mid, "expedition_id": _exp_id(mid, expedition)}
        _set_meta(db, "ocr_pending", pending)
        _set_meta(db, "ocr_pending_mode", mode)
        return pending


def discard_ocr_id():
    """Clear the pending remnant reservation and mode without advancing the remnant counter."""
    with _connect() as db:
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
    return get_state()


def start_next_chain():
    """Complete a nonempty selected chain or advance an empty one to the next unused expedition."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        mid, expedition, eid = _chain_context(db)
        if _saved_chain_rows(db, eid):
            _complete_chain(db, mid, expedition, eid)
        else:
            config = _meta(db, "settings")
            config["expedition"] = _next_unused_expedition(db, mid, expedition)
            _set_meta(db, "settings", config)
    return get_state()


def commit_remnant(first, next_recipe=None, family=None, scan_id=None, expected_pending=None, visible_seed=None,
                    *, expected_context=None, seed_context=None):
    """Validate capture and optional auto-commit reservation checks, then save a remnant atomically."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if expected_context is not None:
            _validate_remnant_context(db, expected_context)
        if seed_context is not None:
            _validate_remnant_context(db, seed_context)
        if expected_pending is not None:
            if not _meta(db, "settings").get("auto_commit", False):
                raise ValueError("Auto-commit is off.")
            if _meta(db, "ocr_pending") != expected_pending:
                raise ValueError("The scanned remnant was already saved or discarded.")
        return _commit_remnant(db, first, next_recipe, family, scan_id, visible_seed)


def _commit_remnant(db, first, next_recipe=None, family=None, scan_id=None, visible_seed=None,
                    seed_rows=None, captured_expedition=None):
    """Validate an optional linked seed and append one remnant in the caller's transaction.

    Create map and expedition records as needed, retain captured expedition
    identity, consume the remnant number and clear its pending reservation.
    """
    result = (_resolve(db, first, next_recipe, family) if seed_rows is None else
              {"status": "ready", "family": family, "rows": seed_rows})
    if result["status"] != "ready":
        raise ValueError("Choose a matching family or enter Next Recipe before committing.")
    config = _validate_settings(db, {})
    current = _meta(db, "current_map_number")
    new_map = current == 0 or _meta(db, "pending_new_map")
    number = current + 1 if new_map else current
    mid = _map_id(number)
    prepared = _prepared_waystone(db, mid) if new_map else None
    if prepared:
        config.update(_waystone_settings(prepared["settings"]))
    elif current and new_map:
        config.update(WAYSTONE_DEFAULTS)
    remnant_number = _meta(db, "next_remnant_number")
    rid = f"R{remnant_number:04d}"
    pending = _meta(db, "ocr_pending")
    expedition = 1 if new_map else config["expedition"]
    if pending:
        match = re.fullmatch(re.escape(mid) + r"-E(\d+)", str(pending.get("expedition_id", "")))
        if pending.get("remnant_id") != rid or pending.get("map_id") != mid or not match:
            raise ValueError("Scanned remnant belongs to another map or chain. Save or discard it first.")
        expedition = _integer(int(match.group(1)), "Captured expedition", 1)
    if captured_expedition is not None:
        expedition = _integer(captured_expedition, "Captured expedition", 1)
    eid = _exp_id(mid, expedition)
    if pending and (pending["remnant_id"], pending["map_id"], pending["expedition_id"]) != (rid, mid, eid):
        raise ValueError("Scanned remnant belongs to another map or chain. Save or discard it first.")
    occurrence = str(uuid.uuid4())
    context = _bind_atlas_context(db, mid, _snapshot({**config, "expedition": expedition}))
    if new_map:
        fields = set(prepared["fields"]) if prepared else set()
        context["waystone_setup_fields"] = sorted(fields)
        context["waystone_setup_saved"] = WAYSTONE_SETUP_FIELDS <= fields
    details = {"family": result["family"], "recipes": result["rows"]}
    if visible_seed is not None:
        if not isinstance(visible_seed, dict):
            raise ValueError("The linked visible seed is invalid.")
        sockets = _integer(visible_seed.get("sockets"), "Visible seed sockets", 3, 10)
        stage = db.execute("SELECT 1 FROM seed_states WHERE family=? AND sockets=? AND "
                           "seed_slot=? AND seed_rune=?",
                           (result["family"], sockets, visible_seed.get("slot"),
                            visible_seed.get("rune"))).fetchone()
        if not stage:
            raise ValueError("The linked visible seed does not match the resolved family and socket stage.")
        if visible_seed.get("mode") == "both" and sockets != result["rows"][0]["sockets"]:
            raise ValueError("The linked visible seed and opened rewards have different socket counts.")
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
        _set_meta(db, "prepared_waystone_setup", None)
    return {"remnant_id": rid, "map_id": mid, "expedition_id": eid,
            "recipes": len(result["rows"]), "family": result["family"],
            "scan_commit_number": commit_number}


def _seed_recipes(db, family, sockets, rewards):
    """Build verified stage rewards in family order, rejecting missing or oversized recipe rows."""
    record = db.execute("SELECT recipes_json FROM families WHERE id=? AND valid=1", (family,)).fetchone()
    recipes = _load(record[0]) if record else []
    if not rewards or len(set(rewards)) != len(rewards) or not set(rewards).issubset(recipes):
        raise ValueError("The family stage and Recipe DB disagree. Review the database entry.")
    rows = []
    for name in recipes:
        if name not in rewards:
            continue
        recipe = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (name,)).fetchone()
        if not recipe or not recipe["combo"] or recipe["sockets"] > sockets:
            raise ValueError("The family stage and Recipe DB disagree. Review the database entry.")
        rows.append({"recipe": name, "sockets": recipe["sockets"], "combo": recipe["combo"]})
    return rows


def commit_seed_batch(result, selections, automatic=False):
    """Validate and commit selected visible seeds in one immediate transaction.

    Keep their captured expedition and reserve the next remnant ID when unsaved readings remain.
    """
    if not isinstance(result, dict) or not isinstance(selections, list) or not 1 <= len(selections) <= 24:
        raise ValueError("Select the visible remnants to commit.")
    if any(key not in result for key in ("_scan_generation", "_capture_map_id", "_capture_expedition", "_capture_map_pending")):
        raise ValueError("The visible seed capture context is missing. Scan again.")
    readings = result.get("remnants") or [result]
    if not isinstance(readings, list) or not 1 <= len(readings) <= 24:
        raise ValueError("Scan the visible remnants first.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _validate_remnant_context(db, result)
        if ("_capture_map_pending" in result and result["_capture_map_pending"] !=
                bool(_meta(db, "pending_new_map", False))):
            raise ValueError("The map ended during the scan. Scan again.")
        pending = _meta(db, "ocr_pending")
        if not pending or any(result.get(key) != pending[key] for key in
                              ("remnant_id", "map_id", "expedition_id")):
            raise ValueError("The visible seed scan was already saved or discarded. Scan again.")
        captured_expedition = _integer(result.get("_capture_expedition"), "Captured expedition", 1)
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
            rewards = _load(stage["rewards_json"])
            if not rewards:
                raise ValueError("The visible seed stage has no verified rewards.")
            rows = _seed_recipes(db, family, sockets, rewards)
            seed = {"sockets": sockets, "slot": item.get("seed_slot"),
                    "rune": item.get("seed_rune"), "scan_index": index+1, "mode": "seed"}
            resolved.append((index, family, seed, rows))
        saved = [{"index": index, **_commit_remnant(db, rows[0]["recipe"], family=family,
                                                  visible_seed=seed, seed_rows=rows,
                                                  captured_expedition=captured_expedition)}
                 for index, family, seed, rows in sorted(resolved)]
        remaining = any(i not in seen and not reading.get("saved") and not reading.get("rejected")
                        for i, reading in enumerate(readings))
        next_pending = None
        number = _meta(db, "current_map_number")
        expedition = captured_expedition
        if remaining:
            next_pending = {"remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
                            "map_id": _map_id(number),
                            "expedition_id": _exp_id(_map_id(number), expedition)}
            _set_meta(db, "ocr_pending", next_pending)
            _set_meta(db, "ocr_pending_mode", "seed")
        return {"committed": True, "saved": saved, "pending": next_pending,
                "context": {"_scan_generation": _meta(db, "session_generation", 0),
                            "_capture_map_id": _map_id(number),
                            "_capture_map_pending": bool(_meta(db, "pending_new_map", False)),
                            "_capture_expedition": expedition},
                "scan_commit_number": saved[-1]["scan_commit_number"]}


def seed_previously_logged(map_id, expedition_id, family, sockets):
    """Check commit history for a matching visible-seed family and socket stage in an expedition."""
    with _connect() as db:
        return db.execute("SELECT 1 FROM commits WHERE kind='Remnant' AND map_id=? AND "
            "expedition_id=? AND json_extract(details_json,'$.family')=? AND "
            "json_extract(details_json,'$.visible_seed.sockets')=? LIMIT 1",
            (map_id, expedition_id, family, sockets)).fetchone() is not None


def _rune(value, label):
    """Trim and bound a rune label, requiring a value for Rune 1."""
    rune = str(value or "").strip()
    if len(rune) > 80 or (label == "Rune 1" and not rune):
        raise ValueError(f"{label} is required and must be under 80 characters." if label == "Rune 1"
                         else f"{label} must be under 80 characters.")
    return rune


def commit_chain(rune1, rune2=""):
    """Append one rune pair and expose its step number in the single-step result."""
    result = commit_chain_steps([{"rune1": rune1, "rune2": rune2}])
    return {**result, "step": result["steps"][0], "rune1": rune1, "rune2": rune2}


def commit_chain_runes(runes):
    """Append a validated ordered list of runes as single-rune chain steps."""
    if not isinstance(runes, list) or not 1 <= len(runes) <= 96:
        raise ValueError("Enter 1–96 runes in chain order.")
    cleaned = [_rune(rune, "Rune 1") for rune in runes]
    return commit_chain_steps([{"rune1": rune, "rune2": ""} for rune in cleaned])


def _clean_chain_steps(steps, *, limit=96):
    """Validate chain step dictionaries and return trimmed rune pairs with an optional size limit."""
    if not isinstance(steps, list) or not steps or (limit is not None and len(steps) > limit):
        raise ValueError("Enter 1–96 chain steps in order." if limit is not None else
                         "Enter the saved chain steps to correct.")
    cleaned = []
    for item in steps:
        if not isinstance(item, dict) or not isinstance(item.get("rune1"), str) or (
                item.get("rune2") is not None and not isinstance(item.get("rune2"), str)):
            raise ValueError("Enter the chain runes in order.")
        cleaned.append((_rune(item.get("rune1"), "Rune 1"), _rune(item.get("rune2"), "Rune 2")))
    return cleaned


def _chain_context(db, expected_context=None, *, allow_selected_change=False):
    """Require an active map and resolve its expedition, checking supplied capture tokens.

    Allow a captured expedition to differ from selection only when explicitly requested.
    """
    number = _meta(db, "current_map_number", 0)
    if not number:
        raise ValueError("Start a map before saving a chain.")
    if _meta(db, "pending_new_map", False):
        raise ValueError("Start the next map before saving a chain.")
    mid = _map_id(number)
    expedition = _meta(db, "settings")["expedition"]
    if expected_context is not None:
        if not isinstance(expected_context, dict):
            raise ValueError("The chain capture context is invalid. Scan again.")
        current = {"_scan_generation": _meta(db, "session_generation", 0),
                   "_capture_map_id": mid, "_capture_map_pending": False}
        if not allow_selected_change:
            current["_capture_expedition"] = expedition
        if any(expected_context.get(key) != value for key, value in current.items()):
            raise ValueError("The map, expedition or session changed. Scan again before saving the chain.")
        if allow_selected_change:
            expedition = _integer(expected_context.get("_capture_expedition"), "Captured expedition", 1)
    return mid, expedition, _exp_id(mid, expedition)


def _saved_chain_rows(db, eid):
    """Read imported and new chain rows for an expedition, sorted by numeric step."""
    rows = []
    for table in ("legacy_export", "new_export"):
        for row in db.execute(f"SELECT position,chain_step,row_json FROM {table} WHERE expedition_id=? "
                              "AND chain_step IS NOT NULL AND chain_step!=''", (eid,)):
            values = _load(row["row_json"])
            rows.append({"table": table, "position": row["position"], "values": values,
                         "step": int(row["chain_step"]), "rune1": values[26], "rune2": values[27]})
    return sorted(rows, key=lambda item: item["step"])


def _chain_steps_snapshot(db, eid):
    """Project the current ordered chain into step and rune dictionaries for commit details."""
    return [{key: row[key] for key in ("step", "rune1", "rune2")} for row in _saved_chain_rows(db, eid)]


def _require_open_chain(db, eid):
    """Reject writes to an expedition already present in chain completions."""
    if db.execute("SELECT 1 FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone():
        raise ValueError("This chain is completed. Select the current expedition to build another chain.")


def _request_token(request_id):
    """Normalize an optional bounded request ID for chain-operation deduplication."""
    if request_id is None:
        return None
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
        raise ValueError("The chain request ID is invalid.")
    return request_id.strip()


def _chain_receipt(db, request_id, payload, expected_context):
    """Replay a saved request result only when payload, generation and captured expedition match."""
    if request_id is None:
        return None
    row = db.execute("SELECT * FROM chain_append_receipts WHERE request_id=?", (request_id,)).fetchone()
    if row is None:
        return None
    _, _, eid = _chain_context(db, expected_context, allow_selected_change=expected_context is not None)
    if (row["session_generation"] != _meta(db, "session_generation", 0) or row["expedition_id"] != eid
            or _load(row["payload_json"]) != payload):
        raise ValueError("This chain request ID was already used for a different scan or chain.")
    return {**_load(row["result_json"]), "reused": True}


def _save_chain_receipt(db, request_id, payload, result):
    """Persist a request result for retry reuse when a request ID was supplied."""
    if request_id is not None:
        db.execute("INSERT INTO chain_append_receipts VALUES(?,?,?,?,?)", (
            request_id, _meta(db, "session_generation", 0), result["expedition_id"], _dump(payload), _dump(result)))


def _append_chain_steps(db, mid, expedition, eid, cleaned):
    """Append numbered steps and a Chain commit to an open expedition without completing it."""
    _require_open_chain(db, eid)
    step = max([row["step"] for row in _saved_chain_rows(db, eid)] or [0]) + 1
    existing = _first_row(db, "expedition_id", eid)
    det = _detonated_value(db, eid, _UNSET)
    context = _bind_atlas_context(db, mid, _snapshot(_meta(db, "settings")))
    steps = [{"step": step + offset, "rune1": rune1, "rune2": rune2}
             for offset, (rune1, rune2) in enumerate(cleaned)]
    commit_number = _record_commit(db, "Chain", mid, eid, f"Steps {step}–{step + len(cleaned) - 1}",
                                   context=context, details={"detonated": det, "steps": steps})
    for offset, (rune1, rune2) in enumerate(cleaned):
        row = [""] * 67
        row[22], row[25], row[26], row[27], row[31], row[32] = (
            mid, step + offset, rune1, rune2, expedition, eid)
        if offset == 0 and not existing and det is not None:
            row[33] = det
        row.extend(_extra_export_values(context))
        row[67 + len(BASE_EXTRA_HEADERS) - 1] = commit_number
        _add_new(db, row)
    db.execute("INSERT OR IGNORE INTO expeditions VALUES(?,?,?,?)", (eid, mid, expedition, None))
    return {"map_id": mid, "expedition_id": eid, "steps": [item["step"] for item in steps],
            "scan_commit_number": commit_number, "reused": False}


def commit_chain_draft(steps, expected_context=None, *, request_id=None):
    """Append a reviewed part to the current chain; completion is a separate action."""
    return commit_chain_steps(steps, expected_context=expected_context, request_id=request_id)


def commit_chain_steps(steps, *, advance_expedition=False, expected_context=None, request_id=None):
    """Append steps atomically, optionally complete the chain, and cache a result for a request ID."""
    cleaned = _clean_chain_steps(steps)
    request_id = _request_token(request_id)
    payload = {"operation": "append", "steps": cleaned, "complete": bool(advance_expedition)}
    # Persist the same JSON representation used when replaying a receipt.
    payload = _load(_dump(payload))
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _chain_receipt(db, request_id, payload, expected_context)
        if previous is not None:
            return previous
        mid, expedition, eid = _chain_context(db, expected_context)
        result = _append_chain_steps(db, mid, expedition, eid, cleaned)
        if advance_expedition:
            completed = _complete_chain(db, mid, expedition, eid)
            result.update(next_expedition=completed["next_expedition"],
                          next_expedition_id=completed["next_expedition_id"],
                          completion_commit_number=completed["scan_commit_number"])
        _save_chain_receipt(db, request_id, payload, result)
        return result


def _next_unused_expedition(db, mid, expedition):
    """Find the first later expedition number absent from expedition records and export rows."""
    used = {row[0] for row in db.execute("SELECT number FROM expeditions WHERE map_id=?", (mid,))}
    for table in ("legacy_export", "new_export"):
        for row in db.execute(f"SELECT DISTINCT expedition_id FROM {table} WHERE map_id=?", (mid,)):
            if row[0] and str(row[0]).startswith(mid + "-E"):
                try:
                    used.add(int(str(row[0]).split("-E", 1)[1]))
                except ValueError:
                    pass
    following = expedition + 1
    while following in used:
        following += 1
    return following


def _completion_result(mid, eid, row, *, already_completed):
    """Format the stored completion outcome, including its next expedition and replay flag."""
    return {"map_id": mid, "expedition_id": eid, "next_expedition": row["next_expedition"],
            "next_expedition_id": _exp_id(mid, row["next_expedition"]),
            "scan_commit_number": row["scan_commit_number"], "step_count": row["step_count"],
            "already_completed": already_completed}


def _complete_chain(db, mid, expedition, eid):
    """Record a nonempty chain's final steps and advance selection once; reuse existing completion."""
    existing = db.execute("SELECT * FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone()
    if existing:
        return _completion_result(mid, eid, existing, already_completed=True)
    steps = _chain_steps_snapshot(db, eid)
    if not steps:
        raise ValueError("Save at least one chain part before completing the chain.")
    following = _next_unused_expedition(db, mid, expedition)
    number = _record_commit(db, "Chain completion", mid, eid, f"Completed {len(steps)} chain parts",
                            details={"steps": steps, "detonated": _detonated_value(db, eid, _UNSET),
                                     "next_expedition": following, "next_expedition_id": _exp_id(mid, following)})
    db.execute("INSERT INTO chain_completions VALUES(?,?,?,?,?)", (eid, number, len(steps), following, _now()))
    config = _meta(db, "settings")
    config["expedition"] = following
    _set_meta(db, "settings", config)
    row = db.execute("SELECT * FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone()
    return _completion_result(mid, eid, row, already_completed=False)


def complete_chain(expected_context=None):
    """Close saved parts and advance once, retaining the final ordered chain for history."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # A repeated callback may arrive after the first completion selected the next EID.
        mid, expedition, eid = _chain_context(db, expected_context,
                                              allow_selected_change=expected_context is not None)
        existing = db.execute("SELECT * FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone()
        if existing:
            return _completion_result(mid, eid, existing, already_completed=True)
        _chain_context(db, expected_context)
        return _complete_chain(db, mid, expedition, eid)


def update_chain_steps(steps, expected_context=None):
    """Correct existing parts while building, without changing identity, order or counts."""
    cleaned = _clean_chain_steps(steps, limit=None)
    ids = [_integer(item.get("step"), "Chain step", 1) for item in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("Each saved chain step may be corrected only once.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        mid, _, eid = _chain_context(db, expected_context)
        _require_open_chain(db, eid)
        saved = _saved_chain_rows(db, eid)
        by_step = {row["step"]: row for row in saved}
        if len(by_step) != len(saved) or any(step not in by_step for step in ids):
            raise ValueError("Correct only existing, uniquely identified chain steps.")
        known = {row[0] for row in db.execute("SELECT DISTINCT seed_rune FROM seed_states WHERE seed_rune!='Unresolved'")}
        # The correction dropdown also offers recipe runes that have no seed-state row.
        known.update(rune.strip() for row in db.execute("SELECT combo FROM recipes")
                     for rune in (row[0] or "").split("+")
                     if rune.strip() and rune.strip().casefold() != "unresolved")
        known.update(rune for row in saved for rune in (row["rune1"], row["rune2"]) if rune)
        if any(rune and rune not in known for pair in cleaned for rune in pair):
            raise ValueError("Choose a known rune when correcting the chain.")
        changes = []
        for step, (rune1, rune2) in zip(ids, cleaned):
            row = by_step[step]
            if (row["rune1"], row["rune2"]) == (rune1, rune2):
                continue
            changes.append({"step": step, "previous_rune1": row["rune1"], "previous_rune2": row["rune2"],
                            "rune1": rune1, "rune2": rune2})
            values = row["values"]
            values[26], values[27] = rune1, rune2
            db.execute(f"UPDATE {row['table']} SET row_json=? WHERE position=?", (_dump(values), row["position"]))
        number = (_record_commit(db, "Chain correction", mid, eid, f"Corrected {len(changes)} chain parts",
                                 details={"changes": changes, "detonated": _detonated_value(db, eid, _UNSET)})
                  if changes else None)
        return {"map_id": mid, "expedition_id": eid, "steps": [item["step"] for item in changes],
                "scan_commit_number": number, "changed": bool(changes), "chain_completed": False}

def _unique_kills_for_map(db, map_id):
    """Read a map's optional unique-kill count, returning None when absent."""
    row = db.execute("SELECT unique_kills FROM map_unique_kills WHERE map_id=?", (map_id,)).fetchone()
    return row[0] if row else None


def _save_unique_kills(db, map_id, unique):
    """Keep the stored unique count for an unset argument, otherwise validate and upsert it."""
    if unique is _UNSET:
        return _unique_kills_for_map(db, map_id)
    value = _integer(unique, "Unique kills", 0, blank=True)
    db.execute("INSERT INTO map_unique_kills VALUES(?,?) ON CONFLICT(map_id) "
               "DO UPDATE SET unique_kills=excluded.unique_kills", (map_id, value))
    return value


def _detonated_value(db, expedition_id, value):
    """Read the saved expedition total for an unset argument or validate an explicit count."""
    if value is _UNSET:
        row = db.execute("SELECT detonated FROM expeditions WHERE expedition_id=?", (expedition_id,)).fetchone()
        return row[0] if row else None
    return _integer(value, "Remnants Detonated", 0, blank=True)


def _clean_propagation_part(runes, recipe):
    """Validate one or two detected runes and trim the optional propagation recipe label."""
    if not isinstance(runes, list) or not 1 <= len(runes) <= 2:
        raise ValueError("A propagation scan must contain one or two detected runes.")
    if any(not isinstance(rune, str) or not rune.strip() for rune in runes):
        raise ValueError("A propagation scan must contain one or two detected runes.")
    cleaned = [_rune(rune, "Rune 1") for rune in runes]
    if not isinstance(recipe, str) or len(recipe) > 200:
        raise ValueError("The propagation recipe must be under 200 characters.")
    return cleaned, recipe.strip()


def _increment_propagation(db, mid, expedition, eid, cleaned, recipe, current_value):
    """Increase an open expedition's detonated count and record Propagation without appending steps."""
    _require_open_chain(db, eid)
    pending = _meta(db, "ocr_pending")
    if pending and (not isinstance(pending, dict) or pending.get("map_id") != mid):
        raise ValueError("The scanned remnant belongs to another map. Save or discard it first.")
    count = (_detonated_value(db, eid, current_value) or 0) + 1
    db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) "
               "DO UPDATE SET detonated=excluded.detonated", (eid, mid, expedition, count))
    _patch_first(db, "expedition_id", eid, {33: count})
    number = _record_commit(db, "Propagation", mid, eid, recipe,
                            details={"detonated": count, "runes": cleaned, "recipe": recipe})
    return {"map_id": mid, "expedition_id": eid, "detonated": count, "scan_commit_number": number}


def increment_propagation_detonated(expected_context, *, current_value=_UNSET, runes=None, recipe="", request_id=None):
    """Persist one accepted held/manual scan independently of its unsaved draft."""
    cleaned, recipe = _clean_propagation_part(runes, recipe)
    if not isinstance(expected_context, dict):
        raise ValueError("The propagation capture context is invalid. Scan again.")
    request_id = _request_token(request_id)
    payload = {"operation": "propagation-draft", "runes": cleaned, "recipe": recipe,
               "current_value": None if current_value is _UNSET else current_value,
               "count_from_saved": current_value is _UNSET}
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _chain_receipt(db, request_id, payload, expected_context)
        if previous is not None:
            return previous
        mid, expedition, eid = _chain_context(db, expected_context)
        result = _increment_propagation(db, mid, expedition, eid, cleaned, recipe, current_value)
        result["reused"] = False
        _save_chain_receipt(db, request_id, payload, result)
        return result


def accept_propagation_part(expected_context, *, runes=None, recipe="", request_id=None, current_value=_UNSET):
    """Atomically count and append a propagation part; a matching request ID reuses its result."""
    cleaned, recipe = _clean_propagation_part(runes, recipe)
    if not isinstance(expected_context, dict):
        raise ValueError("The propagation capture context is invalid. Scan again.")
    request_id = _request_token(request_id)
    payload = {"operation": "propagation", "runes": cleaned, "recipe": recipe,
               "current_value": None if current_value is _UNSET else current_value,
               "count_from_saved": current_value is _UNSET}
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = _chain_receipt(db, request_id, payload, expected_context)
        if previous is not None:
            return previous
        mid, expedition, eid = _chain_context(db, expected_context)
        accepted = _increment_propagation(db, mid, expedition, eid, cleaned, recipe, current_value)
        result = _append_chain_steps(db, mid, expedition, eid, [(cleaned[0], cleaned[1] if len(cleaned) > 1 else "")])
        result.update(detonated=accepted["detonated"], propagation_commit_number=accepted["scan_commit_number"])
        _save_chain_receipt(db, request_id, payload, result)
        return result


def save_kills(normal, magic, rare, *, unique=_UNSET):
    """Replace map kill totals and its first export-row counts, retaining the update in commit history."""
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number")
        if number == 0:
            raise ValueError("Start a map before saving map kills.")
        mid = _map_id(number)
        unique_count = _save_unique_kills(db, mid, unique)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        commit_number = _record_commit(db, "Map kills", mid, details={"kills": values, "unique_kills": unique_count})
        return {"map_id": mid, "kills": values, "unique_kills": unique_count, "scan_commit_number": commit_number}


def save_detonated(value):
    """Replace the selected expedition's detonated total and first export-row value, then record it."""
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


def save_counts(normal, magic, rare, detonated=_UNSET, *, unique=_UNSET):
    """Save map kills and expedition totals atomically, preserving unset optional counts and recording history."""
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        number = _meta(db, "current_map_number")
        if not number:
            raise ValueError("Start a map before saving counts.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        count = _detonated_value(db, eid, detonated)
        unique_count = _save_unique_kills(db, mid, unique)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) DO UPDATE SET detonated=excluded.detonated",
                   (eid, mid, expedition, count))
        _patch_first(db, "expedition_id", eid, {33: "" if count is None else count})
        commit_number = _record_commit(db, "Map totals", mid, eid, details={"kills": values,
                                       "unique_kills": unique_count, "detonated": count})
        return {"map_id": mid, "expedition_id": eid, "kills": values, "unique_kills": unique_count,
                "detonated": count, "scan_commit_number": commit_number}


def finish_map(normal, magic, rare, detonated=_UNSET, *, unique=_UNSET):
    """Save final counts, discard pending OCR review and mark the next map in one immediate transaction."""
    values = [_integer(v, label, 0, blank=True) for v, label in
              zip((normal, magic, rare), ("Normal kills", "Magic kills", "Rare kills"))]
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
        number = _meta(db, "current_map_number")
        if not number:
            raise ValueError("Start a map before finishing it.")
        mid = _map_id(number)
        expedition = _meta(db, "settings")["expedition"]
        eid = _exp_id(mid, expedition)
        count = _detonated_value(db, eid, detonated)
        unique_count = _save_unique_kills(db, mid, unique)
        db.execute("UPDATE maps SET kills_json=? WHERE map_id=?", (_dump(values), mid))
        _patch_first(db, "map_id", mid, {28+i: "" if v is None else v for i, v in enumerate(values)})
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?) ON CONFLICT(expedition_id) DO UPDATE SET detonated=excluded.detonated",
                   (eid, mid, expedition, count))
        _patch_first(db, "expedition_id", eid, {33: "" if count is None else count})
        _set_meta(db, "pending_new_map", True)
        _record_commit(db, "Map totals", mid, eid, details={"kills": values,
                       "unique_kills": unique_count, "detonated": count})
    return get_state()


def mark_next_map(pending):
    """Toggle the next-map marker when no remnant awaits review; cancellation restores staged waystone fields."""
    with _connect() as db:
        if _meta(db, "ocr_pending"):
            raise ValueError("Save or discard the scanned remnant before changing the map marker.")
        if not _meta(db, "current_map_number"):
            raise ValueError("Log a remnant before marking the next map.")
        if not pending:
            _cancel_prepared_waystone(db)
        _set_meta(db, "pending_new_map", bool(pending))
    return get_state()


def start_map():
    """Create the next map and first expedition in one immediate transaction.

    Apply staged waystone fields or clear prior-map fields, retain earlier Atlas
    binding and remember settings needed to undo an empty map.
    """
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
        prepared = _prepared_waystone(db, mid)
        _set_meta(db, "previous_expedition", config["expedition"])
        _set_meta(db, "previous_waystone_settings", _waystone_settings(
            prepared["previous_settings"] if prepared and current else config))
        if prepared:
            config.update(_waystone_settings(prepared["settings"]))
        elif current:
            config.update(_waystone_settings({}))
        config["expedition"] = 1
        context = _bind_atlas_context(db, mid, _snapshot(config))
        fields = set(prepared["fields"]) if prepared else set()
        context["waystone_setup_fields"] = sorted(fields)
        context["waystone_setup_saved"] = WAYSTONE_SETUP_FIELDS <= fields
        db.execute("INSERT INTO maps VALUES(?,?,?,?)",
                   (mid, _dump(context), _dump([None, None, None]), _now()))
        db.execute("INSERT INTO expeditions VALUES(?,?,?,?)",
                   (_exp_id(mid, 1), mid, 1, None))
        _set_meta(db, "settings", config)
        _set_meta(db, "current_map_number", number)
        _set_meta(db, "pending_new_map", False)
        _set_meta(db, "prepared_waystone_setup", None)
    return get_state()


def undo_empty_map():
    """Cancel a next-map marker or remove an activity-free map and restore its previous selection settings."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _meta(db, "ocr_pending"):
            raise ValueError("Save or discard the scanned remnant first.")
        number = _meta(db, "current_map_number", 0)
        if not number:
            raise ValueError("There is no active map to undo.")
        if _meta(db, "pending_new_map"):
            _cancel_prepared_waystone(db)
            _set_meta(db, "pending_new_map", False)
        else:
            mid = _map_id(number)
            if (any(db.execute(f"SELECT 1 FROM {table} WHERE map_id=? LIMIT 1", (mid,)).fetchone()
                    for table in ("legacy_export", "new_export", "currency_snapshots", "ritual_pages"))
                    or db.execute("SELECT 1 FROM maps WHERE map_id=? AND kills_json!=?", (mid, _dump([None]*3))).fetchone()
                    or _unique_kills_for_map(db, mid) is not None
                    or db.execute("SELECT 1 FROM expeditions WHERE map_id=? AND detonated IS NOT NULL", (mid,)).fetchone()
                    or db.execute("SELECT 1 FROM commits WHERE map_id=? LIMIT 1", (mid,)).fetchone()):
                raise ValueError("This map has saved activity. It cannot be undone.")
            db.execute("DELETE FROM expeditions WHERE map_id=?", (mid,))
            db.execute("DELETE FROM map_unique_kills WHERE map_id=?", (mid,))
            db.execute("DELETE FROM maps WHERE map_id=?", (mid,))
            _set_meta(db, "prepared_waystone_setup", None)
            _set_meta(db, "current_map_number", number - 1)
            config = _meta(db, "settings")
            config["expedition"] = _meta(db, "previous_expedition", 1) if number > 1 else 1
            previous = _meta(db, "previous_waystone_settings")
            if number > 1 and previous:
                config.update(_waystone_settings(previous))
            _set_meta(db, "settings", config)
    return get_state()


def clear_export_and_reset_ids():
    """Clear session records and reset IDs atomically while retaining catalogs and settings.

    Increment the generation token so pending captures and request receipts cannot carry into the reset session.
    """
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _set_meta(db, "session_generation", _meta(db, "session_generation", 0) + 1)
        for table in ("legacy_export", "new_export", "maps", "map_unique_kills", "expeditions", "scan_links",
                      "currency_snapshots", "ritual_pages", "commits", "chain_completions", "chain_append_receipts"):
            db.execute(f"DELETE FROM {table}")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('new_export','ritual_pages')")
        _set_meta(db, "current_map_number", 0)
        _set_meta(db, "next_remnant_number", 1)
        _set_meta(db, "pending_new_map", False)
        _set_meta(db, "ocr_pending", None)
        _set_meta(db, "ocr_pending_mode", None)
        _set_meta(db, "previous_expedition", 1)
        _set_meta(db, "previous_waystone_settings", None)
        _set_meta(db, "prepared_waystone_setup", None)
        _set_meta(db, "scan_commit_count", 0)
        config = _meta(db, "settings")
        config["expedition"] = 1
        _set_meta(db, "settings", config)
    return get_state()


def _retired_currency_default(name):
    """Recognize retired default names after trimming, apostrophe normalization and case folding."""
    return str(name).strip().replace("’", "'").casefold() in _RETIRED_CURRENCY_DEFAULTS


def _retain_local_catalog_name(db, name):
    """An explicit registration may reuse a retired default's name."""
    if not _retired_currency_default(name):
        return
    names = set(_meta(db, "active_retired_currency_names", []))
    names.add(name.replace("’", "'").casefold())
    _set_meta(db, "active_retired_currency_names", sorted(names))


def _active_catalog_names(db, table):
    """Hide obsolete seeds without deleting saved labels or reference artwork."""
    if table not in ("currency_items", "ritual_names", "item_names"):
        raise ValueError("Choose the Currency, Omen or Item catalog.")
    names = [row[0] for row in db.execute(f"SELECT name FROM {table} ORDER BY name")]
    if table == "item_names":
        return names
    local_names = set(_meta(db, "active_retired_currency_names", []))
    for icon_table in ("currency_icons", "omen_icons", "review_icon_examples"):
        local_names.update(row[0].replace("’", "'").casefold()
                           for row in db.execute(f"SELECT DISTINCT name FROM {icon_table}"))
    return [name for name in names if not _retired_currency_default(name) or
            name.replace("’", "'").casefold() in local_names]


def currency_names():
    """Return sorted currency names filtered through the active local catalog."""
    with _connect() as db:
        return _active_catalog_names(db, "currency_items")


def add_currency_item(name):
    """Register or reuse a validated currency name, reject Item conflicts and retain retired local names."""
    from PoE2_Data_Logger.core.review_learning import validate_name
    name = validate_name(str(name or ""), "Currency")
    with _connect() as db:
        existing = _catalog_name(db, "currency_items", name)
        if existing:
            _retain_local_catalog_name(db, existing)
            return existing
        if _catalog_name(db, "item_names", name):
            raise ValueError("This name already exists in the Item database.")
        db.execute("INSERT OR IGNORE INTO currency_items(name) VALUES(?)", (name,))
        _retain_local_catalog_name(db, name)
    return name


def save_currency_icon(name, image):
    """Store a registered currency's resized RGB PNG, reusing an identical example ID."""
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
    """Return stored currency reference IDs, names and PNG bytes in insertion order."""
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM currency_icons ORDER BY id")]


def delete_currency_icon(icon_id):
    """Delete the currency reference with a validated positive icon ID."""
    with _connect() as db:
        db.execute("DELETE FROM currency_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def item_names():
    """Return registered equipment and item names in database name order."""
    with _connect() as db:
        return [row[0] for row in db.execute("SELECT name FROM item_names ORDER BY name")]


def add_item_name(name):
    """Register or reuse a validated Item name while rejecting Currency catalog conflicts."""
    from PoE2_Data_Logger.core.review_learning import validate_name
    name = validate_name(str(name or ""), "Item")
    with _connect() as db:
        existing = _catalog_name(db, "item_names", name)
        if existing:
            return existing
        if _catalog_name(db, "currency_items", name):
            raise ValueError("This name already exists in the Currency database.")
        db.execute("INSERT INTO item_names(name) VALUES(?)", (name,))
    return name


def save_item_icon(name, image):
    """Store a registered item's resized RGB PNG, reusing an identical example ID."""
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
    """Return stored item reference IDs, names and PNG bytes in insertion order."""
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM item_icons ORDER BY id")]


def delete_item_icon(icon_id):
    """Delete the item reference with a validated positive icon ID."""
    with _connect() as db:
        db.execute("DELETE FROM item_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def inventory_names():
    """Combine active currency and item labels in case-insensitive name order."""
    return sorted(currency_names() + item_names(), key=str.casefold)


def inventory_icons():
    """Combine currency, item and reviewed examples with recognition category metadata."""
    return [{**row, "kind": kind} for kind, entries in
            (("currency", currency_icons()), ("item", item_icons())) for row in entries] + review_icons()


def _catalog_name(db, table, name):
    """Find a stored catalog label by Unicode case-insensitive comparison."""
    key = name.casefold()
    return next((row[0] for row in db.execute(f"SELECT name FROM {table}")
                 if row[0].casefold() == key), None)


def _canonical_registered_name(db, name, category, *, register=True):
    """Resolve review labels without changing an existing inventory category."""
    from PoE2_Data_Logger.core.review_learning import validate_name
    category = str(category or "Item").title()
    if category not in ("Currency", "Item", "Omen"):
        raise ValueError("Choose Currency, Item or Omen for the reviewed item.")
    registered_omen = _catalog_name(db, "ritual_names", name.strip()) if isinstance(name, str) else None
    name = validate_name(name, "Omen" if registered_omen else category)
    currency = _catalog_name(db, "currency_items", name)
    item = _catalog_name(db, "item_names", name)
    omen = registered_omen
    if currency and item:
        raise ValueError("This name occurs in both Currency and Item databases. Correct the catalog first.")
    if register and not item:
        _retain_local_catalog_name(db, currency or omen or name)
    if category == "Omen":
        if item:
            raise ValueError("This name already exists in the Item database and cannot be labelled Omen.")
        canonical = omen or currency or name
        if register:
            if not omen:
                db.execute("INSERT INTO ritual_names(name) VALUES(?)", (canonical,))
            if not currency:
                db.execute("INSERT INTO currency_items(name) VALUES(?)", (canonical,))
        return canonical, "omen"
    if omen:
        return currency or omen, "omen"
    if currency:
        return currency, "currency"
    if item:
        return item, "item"
    if register:
        table = "currency_items" if category == "Currency" else "item_names"
        db.execute(f"INSERT INTO {table}(name) VALUES(?)", (name,))
    return name, "currency" if category == "Currency" else "item"


def _save_review_examples(db, examples, accepted):
    """Save or relabel image-and-grid examples only for accepted positive-quantity review rows."""
    from PoE2_Data_Logger.core.review_learning import encode_example
    if not isinstance(examples, (list, tuple)) or len(examples) > MAX_RITUAL_REWARDS:
        raise ValueError("Review up to 120 captured icon examples at a time.")
    for example in examples:
        if not isinstance(example, dict):
            raise ValueError("The reviewed icon examples are invalid.")
        name, kind = _canonical_registered_name(db, example.get("name"),
                                                example.get("category"), register=False)
        approved = accepted.get(name.casefold())
        if approved is None or approved[0] != kind:
            raise ValueError("Only accepted named review rows can teach icon examples.")
        if approved[1] <= 0:
            continue
        image, columns, rows = encode_example(example)
        if kind in ("currency", "omen") and (columns, rows) != (1, 1):
            raise ValueError("Currency and Omen examples must contain one complete inventory cell.")
        digest = hashlib.sha256(image).hexdigest()
        db.execute("INSERT INTO review_icon_examples(name,kind,columns,rows,image_png,image_sha256,recorded_at) "
                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(columns,rows,image_sha256) DO UPDATE SET "
                   "name=excluded.name,kind=excluded.kind,recorded_at=excluded.recorded_at",
                   (name, kind, columns, rows, image, digest, _now()))


def review_icons():
    """Return reviewed reference images with category, grid dimensions and reviewed markers."""
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "kind": row["kind"],
                 "columns": row["columns"], "rows": row["rows"], "image": row["image_png"],
                 "reviewed": True}
                for row in db.execute("SELECT * FROM review_icon_examples ORDER BY id")]


def delete_review_icon(icon_id):
    """Delete the reviewed example with a validated positive icon ID."""
    with _connect() as db:
        db.execute("DELETE FROM review_icon_examples WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def ritual_icons():
    """Combine Omen, currency, item and reviewed references for Ritual recognition."""
    return [{**row, "kind": kind} for kind, entries in
            (("omen", omen_icons()), ("currency", currency_icons()), ("item", item_icons()))
            for row in entries] + review_icons()


def save_omen_icon(name, image):
    """Store a registered Omen's RGB PNG crop at its original size, reusing an identical example ID."""
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
    """Return stored Omen reference IDs, names and PNG bytes in insertion order."""
    with _connect() as db:
        return [{"id": row["id"], "name": row["name"], "image": row["image_png"]}
                for row in db.execute("SELECT id,name,image_png FROM omen_icons ORDER BY id")]


def delete_omen_icon(icon_id):
    """Delete the Omen reference with a validated positive icon ID."""
    with _connect() as db:
        db.execute("DELETE FROM omen_icons WHERE id=?", (_integer(icon_id, "Icon ID", 1),))


def currency_target_map(phase):
    """Target start inventory to the upcoming map when pending, and end inventory to the current map."""
    if phase not in ("start", "end"):
        raise ValueError("Choose start or end inventory.")
    with _connect() as db:
        number = _meta(db, "current_map_number", 0)
        if phase == "start" and (not number or _meta(db, "pending_new_map", False)):
            number += 1
        if not number:
            raise ValueError("Start a map before recording end inventory.")
        return _map_id(number)


def save_currency_snapshot(phase, items, expected_map_id=None, *, register_names=False, icon_examples=()):
    """Replace a map's approved inventory phase and append its commit atomically.

    Aggregate duplicate names, optionally register labels and teach icons, and preserve bound Atlas context.
    """
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
        if register_names:
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Review the inventory rows first.")
                _canonical_registered_name(db, item.get("name"), "Currency")
        known = {row[0].casefold(): row[0] for row in db.execute("SELECT name FROM currency_items")}
        item_catalog = {row[0].casefold(): row[0] for row in db.execute("SELECT name FROM item_names")}
        known.update(item_catalog)
        totals = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Review the inventory rows first.")
            name = known.get(str(item.get("name") or "").strip().casefold())
            if name is None:
                raise ValueError(f"Add {item.get('name')} to the Currency or Item database first.")
            quantity = _integer(item.get("quantity"), f"{name} stack count", 0, 1000000)
            totals[name] = totals.get(name, 0) + quantity
        accepted = {name.casefold(): ("item" if name.casefold() in item_catalog else
                    "omen" if _catalog_name(db, "ritual_names", name) else "currency", quantity)
                    for name, quantity in totals.items()}
        _save_review_examples(db, icon_examples, accepted)
        settings = _meta(db, "settings")
        current = _map_id(_meta(db, "current_map_number", 0))
        previous = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
        context = (_load(previous[0]) if map_id == current and _meta(db, "pending_new_map") and previous
                   else _snapshot(_map_context_settings(db, settings, map_id)))
        context = _bind_atlas_context(db, map_id, context)
        db.execute("INSERT INTO currency_snapshots(map_id,phase,items_json,recorded_at,snapshot_json) "
                   "VALUES(?,?,?,?,?) ON CONFLICT(map_id,phase) DO UPDATE SET "
                   "items_json=excluded.items_json,recorded_at=excluded.recorded_at,"
                   "snapshot_json=excluded.snapshot_json",
                   (map_id, phase, _dump(totals), _now(), _dump(context)))
        has_start = db.execute("SELECT 1 FROM currency_snapshots WHERE map_id=? AND phase='start'",
                               (map_id,)).fetchone() is not None
        _record_commit(db, "Currency", map_id, reference=phase.title() + " inventory", context=context,
                       details={"phase": phase, "items": totals,
                                "start_baseline": "Scanned" if has_start else "Assumed empty",
                                "item_kinds": {name: "Item" if name.casefold() in item_catalog else "Currency"
                                               for name in totals}})
    return currency_for_map(map_id)


def currency_for_map(map_id):
    """Return latest start/end inventories and their signed difference, assuming an empty missing start."""
    with _connect() as db:
        snapshots = {row["phase"]: {"items": _load(row["items_json"]), "recorded_at": row["recorded_at"]}
                     for row in db.execute("SELECT phase,items_json,recorded_at FROM currency_snapshots "
                                           "WHERE map_id=?", (map_id,))}
    start = snapshots.get("start", {}).get("items", {})
    end = snapshots.get("end", {}).get("items", {})
    return {"map_id": map_id, "start": start, "end": end,
            "net": {name: end.get(name, 0) - start.get(name, 0) for name in set(start) | set(end)}
            if "end" in snapshots else {},
            "start_baseline": "Scanned" if "start" in snapshots else
                              "Assumed empty" if "end" in snapshots else None,
            "recorded_at": {phase: row["recorded_at"] for phase, row in snapshots.items()}}


def session_currency_totals():
    """Count positive inventory gains until the map IDs are reset.

    Only each map's latest approved end inventory contributes. A missing start
    scan is treated as empty, so the whole end inventory
    counts. A scanned start takes precedence. Ritual rewards remain offers
    rather than inventory acquisitions.
    """
    with _connect() as db:
        db.execute("BEGIN")
        snapshots, saved_names = {}, {}
        for row in db.execute("SELECT map_id,phase,items_json FROM currency_snapshots ORDER BY map_id,phase"):
            items = {}
            for name, quantity in _load(row["items_json"]).items():
                if not isinstance(name, str) or not name.strip():
                    continue
                key = name.strip().casefold()
                saved_names.setdefault(key, name.strip())
                items[key] = items.get(key, 0) + quantity
            snapshots.setdefault(row["map_id"], {})[row["phase"]] = items
        labels = {}
        for table, kind in (("currency_items", "Currency"), ("item_names", "Item"),
                            ("ritual_names", "Omen")):
            for row in db.execute(f"SELECT name FROM {table} ORDER BY name"):
                key = row[0].casefold()
                if key in saved_names:
                    labels[key] = (row[0], kind)
        # Removing a local reference must not erase an already logged gain or
        # change a saved equipment count into currency. Recover its old kind.
        missing = set(saved_names) - set(labels)
        if missing:
            for row in db.execute("SELECT details_json FROM commits WHERE kind='Currency' ORDER BY number DESC"):
                details = _load(row[0])
                kinds = details.get("item_kinds", {})
                for name in details.get("items", {}):
                    if not isinstance(name, str):
                        continue
                    key = name.strip().casefold()
                    if key in missing:
                        kind = "Item" if kinds.get(name) == "Item" else "Currency"
                        labels[key] = (saved_names[key], kind)
                        missing.remove(key)
                if not missing:
                    break
        totals, counted, assumed_empty, pending_end = {}, 0, 0, 0
        for phases in snapshots.values():
            if "end" not in phases:
                pending_end += 1
                continue
            counted += 1
            if "start" not in phases:
                assumed_empty += 1
            start, end = phases.get("start", {}), phases["end"]
            for key, quantity in end.items():
                gained = quantity - start.get(key, 0)
                if gained > 0:
                    totals[key] = totals.get(key, 0) + gained
        items = []
        for key, quantity in totals.items():
            name, kind = labels.get(key, (saved_names[key], "Currency"))
            items.append({"name": name, "kind": kind, "quantity": quantity})
        return {"items": sorted(items, key=lambda item: item["name"].casefold()),
                "maps_counted": counted, "maps_pending_baseline": 0,
                "maps_pending_end": pending_end, "maps_assumed_empty": assumed_empty}


def export_currency_csv():
    """Export latest inventory phases and signed item deltas with settings and latest phase commit IDs."""
    with _connect() as db:
        db.execute("BEGIN")
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
                     *(f"Start {header}" for header in CONFIG_EXPORT_HEADERS[:-4]),
                     *(f"End {header}" for header in CONFIG_EXPORT_HEADERS[:-4]),
                     *(f"Start {header}" for header in ATLAS_EXPORT_HEADERS),
                     *(f"End {header}" for header in ATLAS_EXPORT_HEADERS),
                     "Start Deli", "Start Wisp", "End Deli", "End Wisp", "Start Baseline",
                     "Session Found Quantity"])
    for map_id, phases in sorted(snapshots.items()):
        start = _load(phases["start"]["items_json"]) if "start" in phases else {}
        end = _load(phases["end"]["items_json"]) if "end" in phases else {}
        start_config = _config_export_values(_load(phases["start"]["snapshot_json"]) if "start" in phases else {})
        end_config = _config_export_values(_load(phases["end"]["snapshot_json"]) if "end" in phases else {})
        # An empty inventory is still an approved snapshot with its own map,
        # timestamp, commit and settings. Keep its metadata in this export too.
        for name in sorted(set(start) | set(end)) or [""]:
            writer.writerow(_csv_row([map_id, name, start.get(name, 0) if name else "",
                             end.get(name, 0) if name and "end" in phases else "",
                             end.get(name, 0) - start.get(name, 0) if name and "end" in phases else "",
                             phases["start"]["recorded_at"] if "start" in phases else "",
                             phases["end"]["recorded_at"] if "end" in phases else "",
                             commits.get((map_id, "start"), "") if "start" in phases else "",
                             commits.get((map_id, "end"), "") if "end" in phases else "",
                             *start_config[:-4], *end_config[:-4],
                             *start_config[-4:-2], *end_config[-4:-2],
                             *start_config[-2:], *end_config[-2:],
                             "Scanned" if "start" in phases else "Assumed empty",
                             max(0, end.get(name, 0) - start.get(name, 0))
                             if name and "end" in phases else 0 if "end" in phases else ""]))
    return output.getvalue().encode("utf-8-sig")


def ritual_names():
    """Return sorted Omen labels filtered through the active local catalog."""
    with _connect() as db:
        return _active_catalog_names(db, "ritual_names")


def add_ritual_name(name):
    """Register or resolve an Omen label in both Ritual and Currency catalogs."""
    from PoE2_Data_Logger.core.review_learning import validate_name
    name = validate_name(str(name or ""), "Omen")
    with _connect() as db:
        return _canonical_registered_name(db, name, "Omen")[0]


def ritual_pages_for_map(map_id):
    """Read current Ritual page contents and timestamps in page-number order."""
    with _connect() as db:
        return [{"page_number": row["page_number"], "items": _load(row["items_json"]),
                 "recorded_at": row["recorded_at"]}
                for row in db.execute("SELECT page_number,items_json,recorded_at FROM ritual_pages "
                                      "WHERE map_id=? ORDER BY page_number", (map_id,))]


def save_ritual_page(items, raw_text="", scan_hash=None, expected_map_id=None,
                     tribute_available=None, rerolls_remaining=None, *,
                     register_names=False, icon_examples=()):
    """Save reviewed Ritual offers and a commit atomically for an active map.

    A repeated capture hash replaces its page; otherwise append a page.
    Optionally register accepted labels and teach their icons.
    """
    if not isinstance(items, list) or len(items) > MAX_RITUAL_REWARDS:
        raise ValueError(f"Review up to {MAX_RITUAL_REWARDS} Ritual rewards on a page.")
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
        if (category not in ("Omen", "Item") or not name or len(name) > 200 or
                any(ord(character) < 32 or ord(character) == 127 for character in name)):
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
        if register_names or icon_examples:
            accepted = {}
            for item in cleaned:
                name, kind = _canonical_registered_name(db, item["name"], item["category"],
                                                        register=register_names)
                item["name"] = name
                if kind == "omen":
                    item["category"] = "Omen"
                prior = accepted.get(name.casefold(), (kind, 0))
                accepted[name.casefold()] = (kind, prior[1] + item["quantity"])
            _save_review_examples(db, icon_examples, accepted)
        context = _bind_atlas_context(db, map_id, _snapshot(_meta(db, "settings")))
        old = (db.execute("SELECT id,page_number FROM ritual_pages WHERE map_id=? AND scan_hash=?",
                          (map_id, scan_hash)).fetchone() if scan_hash else None)
        if old:
            db.execute("UPDATE ritual_pages SET items_json=?,raw_text=?,recorded_at=?,snapshot_json=? WHERE id=?",
                       (_dump(cleaned), raw_text, _now(), _dump(context), old["id"]))
            page = old["page_number"]
        else:
            page = db.execute("SELECT COALESCE(MAX(page_number),0)+1 FROM ritual_pages WHERE map_id=?",
                              (map_id,)).fetchone()[0]
            db.execute("INSERT INTO ritual_pages(map_id,page_number,scan_hash,raw_text,items_json,recorded_at,snapshot_json) "
                       "VALUES(?,?,?,?,?,?,?)", (map_id, page, scan_hash, raw_text, _dump(cleaned), _now(),
                                               _dump(context)))
        commit_number = _record_commit(db, "Ritual", map_id, reference=f"Page {page}",
                                       context=context,
                                       details={"page": page, "items": cleaned, "raw_text": raw_text,
                                                "tribute_available": tribute_available,
                                                "rerolls_remaining": rerolls_remaining})
        return {"map_id": map_id, "page_number": page, "count": len(cleaned),
                "updated": bool(old), "scan_commit_number": commit_number}


def export_ritual_csv():
    """Export current Ritual page offers with saved settings and the latest matching commit metadata."""
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


def _total_kills(kills, unique=None):
    """Sum known kill counts, leaving the result blank when all counts are absent."""
    known = [value for value in [*(kills or []), unique] if value is not None and value != ""]
    return sum(known) if known else ""


def export_maps_csv(*, _db=None):
    """Export one row per created map with stored settings and current kill and expedition totals.

    Recover missing imported settings from remnant rows; reuse a supplied connection or open a read transaction.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headers = ["Map ID", "Tier", "Area Level", "Waystone %", "Map Mods",
                     "+2 Tablet Mods", "Total Mods", "Irradiated", "Atlas Master",
                     "Aldur's Saga", "Normal Kills", "Magic Kills", "Rare Kills",
                     "Expedition 1 Detonated", "Expedition 2 Detonated", "Map Recorded UTC",
                     "Item Rarity %", "Monster Rarity %", "Pack Size %", "Effectiveness %",
                     "Waystone Name", "Waystone Modifiers", "Tablet 1 Modifiers",
                     "Tablet 2 Modifiers", "Tablet 3 Modifiers", "Tablet 4 Modifiers",
                     "Biome", "City Type", "Ocean Map",
                     *[f"Map Mod {n}" for n in range(1, 11)], "Last Commit #",
                     "Perk 1", "Perk 2", "Perk 3", "Perk 4", "Master +Mods", "Base Map Mods",
                     "# +2 Mod Tablets", "Tablets Used", *TABLET_EXPORT_HEADERS, *TABLET_DETAIL_HEADERS,
                     *ATLAS_EXPORT_HEADERS, "Deli", "Wisp"]
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        extra_expeditions = [row[0] for row in db.execute(
            "SELECT DISTINCT number FROM expeditions WHERE number>2 ORDER BY number")]
        writer.writerow([*headers, *(f"Expedition {number} Detonated" for number in extra_expeditions),
                         "Unique Kills", "Total Kills"])
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
            unique = _unique_kills_for_map(db, row["map_id"])
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
                                     *_tablet_detail_values(context), *_atlas_export_values(context),
                                     context.get("deli", ""), context.get("wisp", ""),
                                     *(detonated.get(number) if detonated.get(number) is not None else ""
                                       for number in extra_expeditions),
                                     unique if unique is not None else "", _total_kills(kills, unique)]
            writer.writerow(_csv_row([int(v) if isinstance(v, float) and v.is_integer() else v
                                     for v in values]))
    return output.getvalue().encode("utf-8-sig")


MAP_SUMMARY_SCAN_HEADERS = ("Start Baseline", "Currency Scan Status", "Start Commit #", "End Commit #",
                            "Start Recorded UTC", "End Recorded UTC")


def _map_summary_item_sort(name, kind):
    """Use the dashboard's family/tier order without loading the Qt UI."""
    normalized = name.casefold().replace("’", "'")
    if kind == "Omen" or normalized.startswith("omen of "):
        group = 5
    elif kind == "Item":
        group = 12
    elif "essence of " in normalized:
        group = 4
    elif re.search(r"\brune\b", normalized) or "soul core" in normalized or "talisman" in normalized:
        group = 6
    elif "chaos orb" in normalized:
        group = 0
    elif "exalted orb" in normalized:
        group = 1
    elif "divine orb" in normalized:
        group = 2
    elif "orb" in normalized:
        group = 3
    elif "catalyst" in normalized:
        group = 7
    elif "alloy" in normalized:
        group = 8
    elif "liquid" in normalized or "simulacrum" in normalized:
        group = 9
    elif "artifact" in normalized or "coinage" in normalized:
        group = 10
    else:
        group = 11
    match = re.match(r"^(lesser|greater|perfect|refined|ancient)\s+(.+)$", normalized)
    tier, base = (match.group(1), match.group(2)) if match else ("", normalized)
    natural = tuple((0, int(part)) if part.isdigit() else (1, part)
                    for part in re.split(r"(\d+)", base))
    tiers = {"lesser": 0, "": 1, "greater": 2, "perfect": 3, "refined": 2, "ancient": 2}
    return group, natural, tiers[tier], normalized


def _map_summary_records(db):
    """Decode ordered commit snapshots and latest inventory phases for map-summary projection."""
    commits = [{**dict(row), "details": _load(row["details_json"]),
                "context": _load(row["snapshot_json"])}
               for row in db.execute("SELECT number,kind,map_id,reference,recorded_at,details_json,snapshot_json "
                                     "FROM commits ORDER BY number")]
    snapshots = [{**dict(row), "items": _load(row["items_json"]),
                  "context": _load(row["snapshot_json"])}
                 for row in db.execute("SELECT map_id,phase,items_json,recorded_at,snapshot_json "
                                       "FROM currency_snapshots ORDER BY map_id,phase")]
    return commits, snapshots


def _map_summary_item_columns(db, reserved, commits, snapshots):
    """Build ordered item-count headers from active catalogs and saved names, qualifying collisions."""
    labels = {}
    for table, kind in (("currency_items", "Currency"), ("item_names", "Item"), ("ritual_names", "Omen")):
        for entry in _active_catalog_names(db, table):
            name = entry.strip()
            if name:
                labels[name.casefold()] = (name, kind)
    # Retiring a catalog entry must not erase an already approved field.
    for commit in reversed(commits):
        details = commit["details"]
        if commit["kind"] == "Currency":
            kinds = details.get("item_kinds", {})
            entries = ((name, kinds.get(name, "Currency")) for name in details.get("items", {}))
        elif commit["kind"] == "Ritual":
            entries = ((item.get("name"), item.get("category", "Item")) for item in details.get("items", []))
        else:
            continue
        for name, kind in entries:
            if isinstance(name, str) and name.strip():
                name = name.strip()
                labels.setdefault(name.casefold(), (name, kind))
    for snapshot in snapshots:
        for name in snapshot["items"]:
            if isinstance(name, str) and name.strip():
                name = name.strip()
                labels.setdefault(name.casefold(), (name, "Currency"))
    legacy_headers = [*_meta(db, "export_headers", []), *EXPORT_EXTRA_HEADERS, *ATLAS_EXPORT_HEADERS,
                      *KILL_EXPORT_HEADERS, *CHAIN_EXPORT_HEADERS, "Type", "Map Modifiers"]
    used = {header.casefold() for header in [*reserved, *legacy_headers]}
    columns = []
    for key, (name, kind) in sorted(labels.items(), key=lambda entry: _map_summary_item_sort(*entry[1])):
        header = _csv_row([name])[0]
        # Full names are readable; only collisions need a category qualifier.
        # This also keeps a literal apostrophe distinct from CSV's safety prefix.
        while header.casefold() in used:
            header = f"{kind}: {header}"
        used.add(header.casefold())
        columns.append((key, header))
    return columns


def map_summary_item_headers(*, _db=None):
    """Return the exact numeric item headers used by the map summary CSV."""
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        headers = next(csv.reader(io.StringIO(export_maps_csv(_db=db).decode("utf-8-sig"))))
        commits, snapshots = _map_summary_records(db)
        return [header for _, header in _map_summary_item_columns(
            db, [*headers, *MAP_SUMMARY_SCAN_HEADERS], commits, snapshots)]


def _map_summary_unstarted_row(map_id, context, recorded_at, last_commit):
    """Retain approved start scans made before the map record is created."""
    values = dict(zip(CONFIG_EXPORT_HEADERS, _config_export_values(context)))
    values.update({"Map ID": map_id, "Map Recorded UTC": recorded_at, "Last Commit #": last_commit,
                   "+2 Tablet Mods": context.get("tablet_mods", ""),
                   "Aldur's Saga": context.get("aldur", "")})
    return {header: value if value is not None else "" for header, value in values.items()}


def export_map_summary_csv(*, _db=None):
    """One acquired-item count per map, from its latest approved inventories.

    End-only maps assume an empty starting inventory. A missing end scan leaves
    counts blank; an approved empty end produces zeros. Ritual offers and prior
    approvals remain in History and never inflate these acquisition totals.
    """
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        maps = csv.DictReader(io.StringIO(export_maps_csv(_db=db).decode("utf-8-sig")))
        headers = [*maps.fieldnames, *MAP_SUMMARY_SCAN_HEADERS]
        rows = {row["Map ID"]: row for row in maps}
        commits, snapshots = _map_summary_records(db)
        columns = _map_summary_item_columns(db, headers, commits, snapshots)
        headers.extend(header for _, header in columns)
        latest, inventory_commits = {}, {}
        for commit in commits:
            map_id = commit["map_id"]
            if not map_id:
                continue
            latest[map_id] = commit
            if commit["kind"] == "Currency":
                phase = commit["details"].get("phase") or str(commit["reference"] or "").split(" ", 1)[0].lower()
                if phase in ("start", "end"):
                    inventory_commits[map_id, phase] = commit["number"]
        inventories = {}
        for snapshot in snapshots:
            items = {}
            for name, quantity in snapshot["items"].items():
                if not isinstance(name, str) or not name.strip():
                    continue
                key = name.strip().casefold()
                items[key] = items.get(key, 0) + _integer(quantity, "Saved inventory count", 0)
            inventories.setdefault(snapshot["map_id"], {})[snapshot["phase"]] = {**snapshot, "counts": items}
        for map_id in set(latest) | set(inventories):
            if map_id not in rows:
                commit = latest.get(map_id, {})
                phases = inventories.get(map_id, {})
                snapshot = phases.get("end", phases.get("start", {}))
                rows[map_id] = _map_summary_unstarted_row(map_id,
                    snapshot.get("context", commit.get("context", {})),
                    snapshot.get("recorded_at", commit.get("recorded_at", "")), commit.get("number", ""))
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for map_id, row in sorted(rows.items()):
            phases = inventories.get(map_id, {})
            start, end = phases.get("start"), phases.get("end")
            row.update({"Start Baseline": "Scanned" if start is not None else "Assumed empty" if end is not None else "",
                        "Currency Scan Status": "End scanned" if end is not None else
                                                "Awaiting end scan" if start is not None else "Not scanned"})
            for phase in ("start", "end"):
                snapshot = phases.get(phase)
                row[f"{phase.title()} Commit #"] = inventory_commits.get((map_id, phase), "") if snapshot is not None else ""
                row[f"{phase.title()} Recorded UTC"] = snapshot["recorded_at"] if snapshot is not None else ""
            baseline = start["counts"] if start is not None else {}
            for key, header in columns:
                row[header] = max(0, end["counts"].get(key, 0) - baseline.get(key, 0)) if end is not None else ""
            # Existing map CSV strings are already escaped. Escape only the raw
            # fallback metadata here, and never reinterpret numeric count cells.
            writer.writerow({key: _csv_row([value])[0] for key, value in row.items()})
    return output.getvalue().encode("utf-8-sig")


def get_state():
    """Assemble editable settings, selected IDs and counts, catalogs, current chain and recent remnants for the UI."""
    with _connect() as db:
        config = _meta(db, "settings")
        n = _meta(db, "current_map_number")
        mid = _map_id(n) if n else ""
        eid = _exp_id(mid, config["expedition"]) if n else ""
        pending = _meta(db, "ocr_pending")
        rid = ""
        if mid and pending and pending.get("map_id") == mid and pending.get("expedition_id") == eid:
            rid = pending.get("remnant_id", "")
        elif mid:
            for table in ("new_export", "legacy_export"):
                saved = db.execute(f"SELECT remnant_id FROM {table} WHERE map_id=? AND expedition_id=? "
                                   "AND remnant_id IS NOT NULL AND remnant_id!='' ORDER BY position DESC LIMIT 1",
                                   (mid, eid)).fetchone()
                if saved:
                    rid = saved[0]
                    break
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
            "atlas_settings_target_map_id": _map_id(_atlas_settings_target(db)),
            "current_map_id": mid, "current_expedition_id": eid,
            "current_remnant_id": rid,
            "next_remnant_id": f"R{_meta(db, 'next_remnant_number'):04d}",
            "scan_commit_count": _meta(db, "scan_commit_count", 0),
            "ocr_pending": pending,
            "pending_new_map": _meta(db, "pending_new_map"),
            "kills": _load(k[0]) if k else [None, None, None],
            "unique_kills": _unique_kills_for_map(db, mid),
            "detonated": d[0] if d else None,
            "affixes": [r[0] for r in db.execute("SELECT name FROM affixes ORDER BY rowid")],
            "masters": masters, "families": families, "seed_states": seed_states,
            "recipes": [r[0] for r in db.execute("SELECT name FROM recipes ORDER BY name")],
            "runes": [r[0] for r in db.execute("SELECT DISTINCT seed_rune FROM seed_states WHERE seed_rune!='Unresolved' ORDER BY seed_rune")],
            "chain": chain, "chain_completed": db.execute(
                "SELECT 1 FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone() is not None,
            "recent": recent,
            "counts": {"historical_rows": db.execute("SELECT count(*) FROM legacy_export").fetchone()[0],
                       "new_rows": db.execute("SELECT count(*) FROM new_export").fetchone()[0],
                       "saved_scans": db.execute("SELECT count(*) FROM scans").fetchone()[0],
                       "seed_states": db.execute("SELECT count(*) FROM seed_states").fetchone()[0],
            "rune_references": db.execute("SELECT count(*) FROM reviewed_glyphs").fetchone()[0]},
            "ritual_pages": db.execute("SELECT count(*) FROM ritual_pages").fetchone()[0],
            "export_folder": _meta(db, "export_folder", ""),
        }


def search_catalog(query="", kind="families", limit=100):
    """Filter a selected catalog by text and return a bounded number of entries."""
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
    """Export imported and new recipe/chain rows with commit-linked Atlas fields and current completion status.

    Project unique and total kills once per map; reuse a supplied connection or open a read transaction.
    """
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        writer.writerow([*_meta(db, "export_headers"), *EXPORT_EXTRA_HEADERS[:-2],
                         *ATLAS_EXPORT_HEADERS, *EXPORT_EXTRA_HEADERS[-2:], *KILL_EXPORT_HEADERS,
                         *CHAIN_EXPORT_HEADERS])
        atlas_contexts = {row["number"]: _load(row["snapshot_json"])
                          for row in db.execute("SELECT number,snapshot_json FROM commits")}
        completed = {row[0] for row in db.execute("SELECT expedition_id FROM chain_completions")}
        seen_maps = set()
        for table in ("legacy_export", "new_export"):
            for item in db.execute(f"SELECT row_json FROM {table} ORDER BY position"):
                values = _load(item[0])
                if len(values) in (67, 90, 91, 67 + len(BASE_EXTRA_HEADERS) + len(TABLET_DETAIL_HEADERS)):
                    values.extend([""] * (67 + len(EXPORT_EXTRA_HEADERS) - len(values)))
                if len(values) != 67 + len(EXPORT_EXTRA_HEADERS):
                    raise ValueError("Saved Export row has an invalid width.")
                # Atlas fields are projected on export; fixed spreadsheet row positions remain intact.
                context = atlas_contexts.get(values[67 + len(BASE_EXTRA_HEADERS) - 1], {})
                map_id = values[22]
                if map_id and map_id not in seen_maps:
                    unique = _unique_kills_for_map(db, map_id)
                    kill_values = [unique if unique is not None else "", _total_kills(values[28:31], unique)]
                    seen_maps.add(map_id)
                else:
                    kill_values = ["", ""]
                status = ("Completed" if values[32] in completed else "Open") if values[25] else ""
                writer.writerow(_csv_row([*values[:-2], *_atlas_export_values(context), *values[-2:],
                                          *kill_values, status]))
    return out.getvalue().encode("utf-8-sig")


def export_commits_csv():
    """Export the ordered commit index without expanding event details or configuration snapshots."""
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
KILL_EXPORT_HEADERS = ("Unique Kills (Map)", "Total Kills")
CHAIN_EXPORT_HEADERS = ("Chain Status",)
CHAIN_CORRECTION_HEADERS = ("Previous Rune 1", "Previous Rune 2")
HISTORY_APPEND_HEADERS = (*KILL_EXPORT_HEADERS, "Remnants Detonated (Scan)", "Start Baseline",
                          "Current Inventory Snapshot", "Session Found Quantity", *CHAIN_EXPORT_HEADERS,
                          *CHAIN_CORRECTION_HEADERS)


def _current_inventory_projection(db):
    """Normalize latest inventory phase counts and locate the latest commit for each saved phase."""
    snapshots = {}
    for row in db.execute("SELECT map_id,phase,items_json FROM currency_snapshots"):
        items = {}
        for name, quantity in _load(row["items_json"]).items():
            if isinstance(name, str) and name.strip():
                key = name.strip().casefold()
                items[key] = items.get(key, 0) + quantity
        snapshots[row["map_id"], row["phase"]] = items
    commits = {}
    for row in db.execute("SELECT number,map_id,reference,details_json FROM commits "
                          "WHERE kind='Currency' ORDER BY number"):
        phase = _load(row["details_json"]).get("phase") or str(row["reference"] or "").split(" ", 1)[0].lower()
        if (row["map_id"], phase) in snapshots:
            commits[row["map_id"], phase] = row["number"]
    return snapshots, commits


def export_record_history_csv(*, _db=None):
    """Expand every commit's frozen details and settings into history rows.

    Mark current inventory approvals and credit gains only to the latest end.
    Project current chain completion alongside append and correction history.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Scan Commit #", "Type", "Map ID", "Expedition ID", "Reference", "Recorded UTC",
                     *HISTORY_DETAIL_HEADERS, *CONFIG_EXPORT_HEADERS[:-4], "Tablet Slot Capacity",
                     *(f"{master} Configured Perk {n}" for master in ("Jado", "Doryani", "Hilda")
                       for n in range(1, 5)), *ATLAS_EXPORT_HEADERS, "Deli", "Wisp", *HISTORY_APPEND_HEADERS])
    starts = {}
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        current_inventories, current_commits = _current_inventory_projection(db)
        completed = {row[0] for row in db.execute("SELECT expedition_id FROM chain_completions")}
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
                shared["Start Baseline"] = details.get("start_baseline") or (
                    "Scanned" if phase == "start" or start is not None else "Assumed empty")
                is_current = current_commits.get((commit["map_id"], phase)) == commit["number"]
                shared["Current Inventory Snapshot"] = is_current
                shared["Session Found Quantity"] = 0
                current_start = current_inventories.get((commit["map_id"], "start"), {})
                current_end = current_inventories.get((commit["map_id"], "end"), {})
                credited = set()
                names = set(totals) | (set(start["items"]) if phase == "end" and start else set())
                kinds = {**(start["kinds"] if phase == "end" and start else {}),
                         **details.get("item_kinds", {})}
                for name in sorted(names):
                    is_item = kinds.get(name) == "Item"
                    amount = totals.get(name, 0)
                    key = name.strip().casefold()
                    found = (max(0, current_end.get(key, 0) - current_start.get(key, 0))
                             if is_current and phase == "end" and key not in credited else 0)
                    credited.add(key)
                    entries.append({"Item Name" if is_item else "Currency": name, "Quantity": amount,
                                    "Item Source": "Inventory" if is_item else "",
                                    "Start Count": amount if phase == "start" else
                                                   start["items"].get(name, 0) if start else 0,
                                    "End Count": amount if phase == "end" else "",
                                    "Net Change": amount - start["items"].get(name, 0)
                                                  if phase == "end" and start else
                                                  amount if phase == "end" else "",
                                    "Start Recorded UTC": commit["recorded_at"] if phase == "start" else
                                                          start["recorded_at"] if start else "",
                                    "End Recorded UTC": commit["recorded_at"] if phase == "end" else "",
                                    "Session Found Quantity": found})
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
            elif commit["kind"] in ("Chain", "Chain completion"):
                entries = [{"Chain Step #": item["step"], "Propagation Rune 1": item["rune1"],
                            "Propagation Rune 2": item["rune2"]} for item in details.get("steps", [])]
            elif commit["kind"] == "Chain correction":
                entries = [{"Chain Step #": item["step"], "Propagation Rune 1": item["rune1"],
                            "Propagation Rune 2": item["rune2"], "Previous Rune 1": item["previous_rune1"],
                            "Previous Rune 2": item["previous_rune2"]} for item in details.get("changes", [])]
            elif commit["kind"] == "Propagation":
                runes = details.get("runes") or []
                entries = [{"Recipe": details.get("recipe", ""),
                            "Propagation Rune 1": runes[0] if runes else "",
                            "Propagation Rune 2": runes[1] if len(runes) > 1 else ""}]
            kills = details.get("kills", [])
            shared.update({name: amount for name, amount in zip(HISTORY_DETAIL_HEADERS[16:19], kills)})
            if "detonated" in details:
                shared["Remnants Detonated (Expedition)"] = details["detonated"]
            selections = context.get("master_selections", {})
            configured = [selections.get(master, [""] * 4)[n]
                          for master in ("Jado", "Doryani", "Hilda") for n in range(4)]
            config = _config_export_values(context)
            config_values = [*config[:-4], context.get("tablet_capacity", ""), *configured,
                             *config[-4:-2], *config[-2:]]
            for entry in entries or [{}]:
                values = {**shared, **entry}
                entry_base = [*base]
                if commit["kind"] == "Currency" and entry.get("Item Name"):
                    entry_base[1] = "Item"
                writer.writerow(_csv_row([*entry_base, *(values.get(name, "") for name in HISTORY_DETAIL_HEADERS),
                                           *config_values,
                                           details.get("unique_kills") if details.get("unique_kills") is not None else "",
                                           _total_kills(kills, details.get("unique_kills")),
                                           1 if commit["kind"] == "Propagation" else "",
                                           shared.get("Start Baseline", ""),
                                           shared.get("Current Inventory Snapshot", ""),
                                           values.get("Session Found Quantity", ""),
                                           ("Completed" if commit["expedition_id"] in completed else "Open")
                                           if commit["kind"] in ("Chain", "Chain correction", "Chain completion", "Propagation") else "",
                                           *(values.get(name, "") for name in CHAIN_CORRECTION_HEADERS)]))
    return output.getvalue().encode("utf-8-sig")


def _review_item_export_columns(db):
    """Keep count columns canonical and recover their names from saved records.

    History CSV has already escaped formula-like names. Reading raw saved names
    also distinguishes a literal leading apostrophe from CSV's safety prefix.
    The existing Item columns retain their order; other columns describe names
    actually recorded rather than adding the entire bundled icon catalogue.
    """
    catalogs = {}
    for kind, table in (("Item", "item_names"), ("Currency", "currency_items"), ("Omen", "ritual_names")):
        names = catalogs[kind] = {}
        for row in db.execute(f"SELECT name FROM {table} ORDER BY name"):
            names.setdefault(row[0].casefold(), row[0])
    columns = {("Item", key): f"Item: {name}" for key, name in catalogs["Item"].items()}
    recorded = {}
    starts = {}
    for commit in db.execute("SELECT number,kind,map_id,details_json FROM commits ORDER BY number"):
        details = _load(commit["details_json"])
        entries = []
        if commit["kind"] == "Currency":
            phase = details.get("phase", "")
            items = details.get("items", {})
            start = starts.get(commit["map_id"])
            names = set(items) | (set(start["items"]) if phase == "end" and start else set())
            kinds = {**(start["kinds"] if phase == "end" and start else {}),
                     **details.get("item_kinds", {})}
            entries = [("Item" if kinds.get(name) == "Item" else "Currency", name)
                       for name in sorted(names)]
            if phase == "start":
                starts[commit["map_id"]] = {"items": items, "kinds": kinds}
        elif commit["kind"] == "Ritual":
            entries = [(item["category"], item["name"]) for item in details.get("items", [])]
        fields = []
        for kind, name in entries:
            if kind not in catalogs or not str(name).strip():
                fields.append(None)
                continue
            key = (kind, name.casefold())
            canonical = catalogs[kind].get(key[1], name)
            columns.setdefault(key, f"{kind}: {canonical}")
            fields.append(columns[key])
        if entries:
            recorded[str(commit["number"])] = fields
    item_columns = [column for (kind, _), column in columns.items() if kind == "Item"]
    extra_columns = sorted((column for (kind, _), column in columns.items() if kind != "Item"),
                           key=str.casefold)
    return [*item_columns, *extra_columns], recorded


def export_all_csv(*, _db=None):
    """Join recipe rows with expanded commit history and per-label quantity columns.

    Retain unmatched imported rows and reuse one connection for its source exports.
    """
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        remnant_data = export_csv(_db=db)
        history_data = export_record_history_csv(_db=db)
        item_columns, recorded_items = _review_item_export_columns(db)
    remnant_reader = csv.DictReader(io.StringIO(remnant_data.decode("utf-8-sig")))
    remnant_headers = [name for name in remnant_reader.fieldnames
                       if name not in (*ATLAS_EXPORT_HEADERS, "Deli", "Wisp", *HISTORY_APPEND_HEADERS)]
    remnants = list(remnant_reader)
    history_reader = csv.DictReader(io.StringIO(history_data.decode("utf-8-sig")))
    headers = remnant_headers + [name for name in history_reader.fieldnames
                                if name not in remnant_headers and
                                name not in ("Recipe", *ATLAS_EXPORT_HEADERS, "Deli", "Wisp", *HISTORY_APPEND_HEADERS)]
    headers.extend(item_columns)
    headers.extend([*ATLAS_EXPORT_HEADERS, "Deli", "Wisp", *HISTORY_APPEND_HEADERS])
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
    item_offsets = {}
    for history in history_reader:
        number = history["Scan Commit #"]
        if history["Type"] in ("Currency", "Item", "Ritual"):
            item_index = item_offsets.get(number, 0)
            item_offsets[number] = item_index + 1
            tracked = recorded_items.get(number, [])
            item_column = tracked[item_index] if item_index < len(tracked) else None
            # Unnamed review detections were rejected, including older records.
            if number in recorded_items and not item_column:
                continue
        else:
            item_column = None
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
        if item_column:
            row[item_column] = history.get("New Find Quantity", "") if history["Type"] == "Ritual" else history.get("Quantity", "")
        writer.writerow(row)
    for number, matching in by_commit.items():
        for row in matching[offsets.get(number, 0):]:
            row["Type"] = "Chain" if row.get("Chain Step #") else "Remnant" if row.get("Remnant ID") else "Map totals"
            writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def export_primary_csv(*, _db=None):
    """Extend the recipe layout with current scans and item totals once per map."""
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        legacy = csv.DictReader(io.StringIO(export_csv(_db=db).decode("utf-8-sig")))
        summaries = csv.DictReader(io.StringIO(export_map_summary_csv(_db=db).decode("utf-8-sig")))
        obsolete = {"Map Mods", "# +2 Mod Tablets", "Tablet Mods", "Total Mods", "Master +Mods",
                    "Base Map Mods", "+2 Tablet Mods"}
        legacy_headers = ["Map Modifiers" if header == "Waystone %" else header
                          for header in legacy.fieldnames
                          if header not in obsolete and header != "Waystone Modifiers"]
        item_headers = map_summary_item_headers(_db=db)
        aliases = {"Aldur's Saga": "Aldur's Saga Affix", "Waystone Modifiers": "Map Modifiers",
                   "Last Commit #": "Scan Commit #", "Normal Kills": "Normal Kills (Map)",
                   "Magic Kills": "Magic Kills (Map)", "Rare Kills": "Rare Kills (Map)",
                   "Unique Kills": "Unique Kills (Map)"}
        supplemental = [header for header in summaries.fieldnames
                        if header not in item_headers and header not in legacy_headers
                        and aliases.get(header) not in legacy_headers
                        and header not in obsolete and header != "Waystone %"]
        headers = [*legacy_headers, "Type", *supplemental, *item_headers]
        by_map = {row["Map ID"]: row for row in summaries}
        once_per_map = {header for header in headers if header.startswith(("Tablet ", "Map Mod ", "Expedition "))
                        and header not in ("Expedition #", "Expedition ID")}
        once_per_map.update({"Map Modifiers", "Tablets Used", "Item Rarity %", "Monster Rarity %",
                             "Pack Size %", "Effectiveness %", "Normal Kills (Map)", "Magic Kills (Map)",
                             "Rare Kills (Map)", "Unique Kills (Map)", "Total Kills", "Scan Commit #",
                             "Map Recorded UTC", *MAP_SUMMARY_SCAN_HEADERS, *item_headers})
        rows, counted = [], set()
        for original in legacy:
            row = dict(original)
            row["Map Modifiers"] = original.get("Waystone Modifiers", "")
            row["Type"] = ("Chain" if row.get("Chain Step #") else
                           "Remnant" if row.get("Remnant ID") or row.get("Matched Recipe") else "Map totals")
            map_id = row.get("Map ID")
            if map_id in by_map and map_id not in counted:
                summary = by_map[map_id]
                row.update({header: summary.get(header, "") for header in supplemental})
                row.update({header: summary.get(header, "") for header in item_headers})
                row.update({header: summary.get(header, row.get(header, ""))
                            for header in once_per_map if header not in item_headers})
                for source, target in aliases.items():
                    if target in once_per_map:
                        row[target] = summary.get(source, row.get(target, ""))
                counted.add(map_id)
            elif map_id in counted:
                row.update({header: "" for header in once_per_map})
            rows.append(row)
        # Inventory-only maps have no recipe rows to extend. They still need
        # their single Map ID row, including an approved empty inventory.
        for map_id in sorted(set(by_map) - counted):
            summary = by_map[map_id]
            row = {**summary, "Type": "Map totals", "New Map (X)": "X"}
            row.update({target: summary.get(source, "") for source, target in aliases.items()})
            rows.append(row)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(header, "") for header in headers])
    return output.getvalue().encode("utf-8-sig")


ATLAS_SHEET_HEADERS = ("Atlas Setup ID", "Map IDs", "Atlas Data Version", "Gear Item Rarity %",
                       "Atlas Catalog ID")


def export_atlas_csv(*, _db=None):
    """Export one row per setup with numbered choices and dynamic unchecked nodes.

    Nodes checked in every referenced setup need no allocation column. A node
    absent from a setup's frozen catalog stays blank rather than becoming No.
    Choice numbers follow that catalog's option order; zero means no selection.
    Allocation and remembered choices remain independent, as in the editor.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    with (nullcontext(_db) if _db is not None else _connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        current = _atlas_snapshot(_meta(db, "settings"))
        referenced = {current["atlas_setup_id"]} if current else set()
        map_ids = {}
        for table in ("maps", "commits", "currency_snapshots", "ritual_pages"):
            for row in db.execute(f"SELECT map_id,snapshot_json FROM {table}"):
                setup_id = _load(row["snapshot_json"]).get("atlas_setup_id")
                if setup_id:
                    referenced.add(setup_id)
                    if row["map_id"]:
                        map_ids.setdefault(setup_id, set()).add(row["map_id"])
        catalogs = {row["catalog_id"]: _load(row["catalog_json"])
                    for row in db.execute("SELECT catalog_id,catalog_json FROM atlas_catalogs")}
        setups = []
        columns = {}
        current_nodes = _atlas_catalog_data()["nodes"]
        for setup_id in sorted(referenced):
            saved = db.execute("SELECT catalog_id,settings_json FROM atlas_setups WHERE setup_id=?",
                               (setup_id,)).fetchone()
            if saved is None:
                raise ValueError("A saved Atlas setup is missing from the local database.")
            settings = _load(saved["settings_json"])
            catalog_id = saved["catalog_id"]
            if catalog_id not in catalogs:
                raise ValueError("A saved Atlas dataset is missing from the local database.")
            allocated = set(settings["allocated"])
            nodes = catalogs[catalog_id]["nodes"]
            setups.append((setup_id, catalog_id, settings, allocated, nodes))
            for node_id, node in nodes.items():
                if not node.get("allocatable"):
                    continue
                label = current_nodes.get(node_id, node)
                if node_id not in allocated:
                    columns.setdefault(("Node", node_id), label)
                if node.get("choices"):
                    columns.setdefault(("Choice", node_id), label)
        keys = sorted(columns, key=lambda key: (columns[key]["activity"], key[1], key[0]))
        writer.writerow([*ATLAS_SHEET_HEADERS,
            *(f"Atlas {kind}: {columns[kind, node_id]['activity']} | "
              f"{columns[kind, node_id]['name']} [{node_id}]" for kind, node_id in keys)])
        for setup_id, catalog_id, settings, allocated, nodes in setups:
            values = []
            for kind, node_id in keys:
                node = nodes.get(node_id)
                if not node or not node.get("allocatable"):
                    values.append("")
                elif kind == "Node":
                    values.append("Yes" if node_id in allocated else "No")
                elif not node.get("choices"):
                    values.append("")
                else:
                    option_id = settings["choices"].get(node_id)
                    number = next((index for index, choice in enumerate(node["choices"], 1)
                                   if choice["id"] == option_id), None) if option_id else 0
                    if number is None:
                        raise ValueError("A saved Atlas choice is missing from its dataset.")
                    values.append(number)
            rarity = settings.get("gear_item_rarity")
            writer.writerow(_csv_row([setup_id, ", ".join(sorted(map_ids.get(setup_id, set()))),
                settings["catalog_version"], "" if rarity is None else rarity, catalog_id, *values]))
    return output.getvalue().encode("utf-8-sig")


def _csv_row(values):
    """Prefix formula-like string cells with an apostrophe while leaving numeric cells unchanged."""
    return [("'" + value) if isinstance(value, str) and
            value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values]


def save_export_folder(folder):
    """Validate an existing absolute local export directory and persist its resolved path."""
    if not isinstance(folder, str) or len(folder) > 1024:
        raise ValueError("Choose an existing local export folder.")
    path = Path(folder.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir() or str(path).startswith("\\\\"):
        raise ValueError("Enter an existing local folder's full path.")
    with _connect() as db:
        _set_meta(db, "export_folder", str(path.resolve()))
    return {"export_folder": str(path.resolve())}


def save_reference_export_folder(folder):
    """Validate and persist an existing absolute local directory for reference exports."""
    if not isinstance(folder, str) or len(folder) > 1024:
        raise ValueError("Choose an existing local reference export folder.")
    path = Path(folder.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir() or str(path).startswith("\\\\"):
        raise ValueError("Enter an existing local folder's full path.")
    with _connect() as db:
        _set_meta(db, "reference_export_folder", str(path.resolve()))
    return str(path.resolve())


def export_filename(kind="xlsx"):
    """Build a timestamped export filename in local time with the requested extension."""
    return f"PoE2_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{kind}"


def save_export_file(kind="xlsx"):
    """Write a workbook or related primary, Atlas and history CSV files to the configured directory.

    Read CSV sources in one transaction and choose an unused shared filename suffix, retrying reservation collisions.
    """
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
        contents = {".xlsx": export_xlsx()}
    else:
        with _connect() as db:
            db.execute("BEGIN")
            contents = {".csv": export_primary_csv(_db=db),
                        "_Atlas.csv": export_atlas_csv(_db=db),
                        "_Scan_History.csv": export_all_csv(_db=db)}
    from PoE2_Data_Logger.core.export_files import ExportWriteError, write_export_files
    stem = Path(export_filename(kind)).stem
    with _EXPORT_WRITE_LOCK:
        number = 1
        while True:
            candidate = stem if number == 1 else f"{stem}_{number}"
            destinations = {suffix: directory / (candidate + suffix) for suffix in contents}
            if any(path.exists() or path.is_symlink() for path in destinations.values()):
                number += 1
                continue
            try:
                write_export_files({destinations[suffix]: data for suffix, data in contents.items()},
                                   replace=False)
            except ExportWriteError as error:
                # Another process may reserve a name after the existence check.
                # Its file stays untouched; retry the entire related export.
                if isinstance(error.__cause__, FileExistsError):
                    number += 1
                    continue
                raise
            break
    result = {"path": str(destinations[f".{kind}"]), "bytes": len(contents[f".{kind}"])}
    if kind == "csv":
        result.update(atlas_path=str(destinations["_Atlas.csv"]),
                      atlas_bytes=len(contents["_Atlas.csv"]),
                      history_path=str(destinations["_Scan_History.csv"]),
                      history_bytes=len(contents["_Scan_History.csv"]))
    return result


def _write_export_bytes(destination, data):
    """Write bytes through a temporary file beside the destination, replace it and remove leftovers."""
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix=".PoE2_Data_Export_", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def backup_bytes():
    """Serialize a consistent SQLite backup through an in-memory destination connection."""
    initialize()
    with store._connect() as source:
        destination = sqlite3.connect(":memory:")
        try:
            source.backup(destination)
            data = destination.serialize()
        finally:
            destination.close()
    return data
