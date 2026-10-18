"""Apply verified bundled reference corrections without replacing local edits.

Each group has a complete expected before-image and dependency guard. Recorded
activity and screenshot evidence are never part of a reference correction.
"""

from __future__ import annotations

import json
import re

from PoE2_Data_Logger.core import undo_store


FAMILY19_MARKER = "family19_chaos_x10_reference_v1"
UNDO_LABEL = "Update bundled recipe references"
_CADIGAN = "Cadigan's Epiphany"
_ASTRID = "Astrid's Creativity"
_ALDUR = "Aldur's Saga"
_CHAOS = "Chaos Orb x10"
_OLD_MEMBERS = [_CADIGAN, _ASTRID, _ALDUR]
_NEW_MEMBERS = [_CADIGAN, _ASTRID, _CHAOS]
_CHAOS_RECIPE = (_CHAOS, 7, "Wisdom + Vision + Rebirth + Rage + Power + Arcane + Adaptive",
                 "Lv70+", "Currency", "https://poe2db.tw/us/Runeshape_Combinations")
_UNCHANGED_RECIPES = (
    (_CADIGAN, 7, "Wisdom + Sky + Oath + Protective + Power + Soul + Bloodletting",
     "Lv70+", "Rune - Named", "https://poe2db.tw/Runeshape_Combinations"),
    (_ASTRID, 7, "Wisdom + Arcane + Time + Adaptive + Power + Volcanic + Death",
     "Lv70+", "Rune - Named", "https://poe2db.tw/Runeshape_Combinations"),
    (_ALDUR, 7, "Wisdom + Vision + Life + Ward + Power + Arcane + Time",
     "Lv70+", "Flux / Saga / Key", "https://poe2db.tw/Runeshape_Combinations"),
)


def _dump(value):
    """Match reference rows' compact, Unicode-preserving JSON representation."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _names(raw):
    """Treat malformed or non-list locally edited membership as a conflict."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) and all(isinstance(name, str) for name in value) else None


def _recipe(db, name):
    """Read every editable field, preserving metadata-only local edits too."""
    row = db.execute("SELECT name,sockets,combo,level_band,category,source FROM recipes WHERE name=?",
                     (name,)).fetchone()
    return tuple(row) if row is not None else None


def _family19_status(db):
    """Return pending only for the complete untouched legacy Family 19 graph."""
    family = db.execute("SELECT id,top_socket,valid,recipes_json FROM families WHERE id=19").fetchone()
    family = tuple(family) if family is not None else None
    seeds = [tuple(row) for row in db.execute(
        "SELECT family,sockets,seed_slot,seed_rune,rewards_json,status FROM seed_states "
        "WHERE family=19 ORDER BY sockets")]
    new_family = (19, 7, 1, _dump(_NEW_MEMBERS))
    new_seed = (19, 7, "P1", "Wisdom", _dump(list(reversed(_NEW_MEMBERS))), "local")
    if family == new_family and seeds == [new_seed] and _recipe(db, _CHAOS) == _CHAOS_RECIPE:
        return "already_current"
    old_family = (19, 7, 1, _dump(_OLD_MEMBERS))
    old_seed = (19, 7, "P1", "Wisdom", _dump(list(reversed(_OLD_MEMBERS))), "calculator")
    if family != old_family or seeds != [old_seed]:
        return "skipped_custom"
    if any(_recipe(db, row[0]) != row for row in _UNCHANGED_RECIPES):
        return "skipped_custom"
    # A distinct local case variant or an existing recipe is never replaced.
    if any(name.casefold() == _CHAOS.casefold() for name, in db.execute("SELECT name FROM recipes")):
        return "skipped_custom"
    # Adding an exact full name must not silently override an existing shortcut.
    key = re.sub(r"[^a-z0-9]", "", _CHAOS.lower())
    if any(re.sub(r"[^a-z0-9]", "", alias.lower()) == key
           for alias, in db.execute("SELECT alias FROM aliases")):
        return "skipped_custom"
    if (db.execute("SELECT 1 FROM alias_keys WHERE key=?", (key,)).fetchone() or
            db.execute("SELECT 1 FROM alias_conflicts WHERE key=?", (key,)).fetchone()):
        return "skipped_custom"
    from PoE2_Data_Logger.core import logger_store as logger
    expected_id = logger._recipe_identifier(_CHAOS)
    for name, identifier in db.execute("SELECT recipe_name,recipe_id FROM recipe_ids"):
        if ((name.casefold() == _CHAOS.casefold() and (name != _CHAOS or identifier != expected_id)) or
                (identifier == expected_id and name != _CHAOS)):
            return "skipped_custom"
    for table, column in (("families", "recipes_json"), ("seed_states", "rewards_json")):
        for raw, in db.execute(f"SELECT {column} FROM {table}"):
            names = _names(raw)
            if names is None or any(name.casefold() == _CHAOS.casefold() for name in names):
                return "skipped_custom"
    return "pending"


def _apply_family19(db):
    """Change precisely one recipe, family and seed; retain Aldur and its ID."""
    from PoE2_Data_Logger.core import logger_store as logger

    db.execute("INSERT INTO recipes VALUES(?,?,?,?,?,?)", _CHAOS_RECIPE)
    db.execute("UPDATE families SET recipes_json=? WHERE id=19", (_dump(_NEW_MEMBERS),))
    db.execute("UPDATE seed_states SET rewards_json=?,status='local' WHERE family=19 AND sockets=7",
               (_dump(list(reversed(_NEW_MEMBERS))),))
    logger._check_seed_references(db, family_ids=(19,), recipe_names=(_CHAOS,))
    logger._ensure_recipe_id(db, _CHAOS)


# Future evidence-backed corrections get independent guards and once-only
# markers. No unverified replacement seed mappings are inferred here.
_UPDATES = ((FAMILY19_MARKER, _family19_status, _apply_family19),)


def apply_bundled_updates(db):
    """Apply guarded groups atomically and journal them before prior user Undo.

    The caller is real-profile initialization after all journal tables exist.
    Markers are deliberately excluded from undo_store.META_KEYS: Undo restores
    reference rows once without having startup immediately reapply the update.
    Fresh defaults and custom conflicts add no Undo action. A failed correction
    rolls back its rows, identity allocation, markers and Undo entry together.
    """
    from PoE2_Data_Logger.core import logger_store as logger

    db.execute("SAVEPOINT bundled_reference_updates")
    try:
        groups = [(marker, classify(db), apply) for marker, classify, apply in _UPDATES
                  if logger._meta(db, marker) is None]
        pending = [(marker, apply) for marker, status, apply in groups if status == "pending"]
        if pending:
            with undo_store.capture(db, UNDO_LABEL):
                for marker, apply in pending:
                    apply(db)
                    logger._set_meta(db, marker, "applied")
        for marker, status, _ in groups:
            if status != "pending":
                logger._set_meta(db, marker, status)
    except Exception:
        db.execute("ROLLBACK TO bundled_reference_updates")
        db.execute("RELEASE bundled_reference_updates")
        raise
    db.execute("RELEASE bundled_reference_updates")
