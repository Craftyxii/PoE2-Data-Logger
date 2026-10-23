"""Save optional map-local Rite modifiers and explicit, completed-map inheritance.

Named Rites are user-declared groups. Only a completed prior map in the same
group supplies Foretold Proliferation sources; their occurrences and wording
remain separate observations, without calculating bonuses or stacking.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from PoE2_Data_Logger.core.ritual_rites_catalog import normalize_affixes


CONTEXT_FIELDS = ("ritual_rite_id", "ritual_rite_name", "ritual_rite_map_ordinal",
                  "ritual_rite_affixes", "ritual_rite_inherited_sources")


def _dump(value):
    """Encode Rite metadata as compact JSON while preserving Unicode wording."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _meta(db, key, default=None):
    """Read one JSON metadata entry from the caller's connection, or return its default."""
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set_meta(db, key, value):
    """Upsert JSON-encoded Rite metadata within the caller's transaction."""
    db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, _dump(value)))


def initialize(db):
    """Create optional metadata tables without changing existing settings or snapshots."""
    db.execute("CREATE TABLE IF NOT EXISTS ritual_rites (rite_id TEXT PRIMARY KEY,"
               "name TEXT NOT NULL COLLATE NOCASE UNIQUE,created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS ritual_rite_map_settings (map_id TEXT PRIMARY KEY,"
               "affixes_json TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS ritual_rite_completions (map_id TEXT PRIMARY KEY,"
               "map_number INTEGER NOT NULL,rite_id TEXT,ordinal INTEGER,snapshot_json TEXT NOT NULL,"
               "completed_at TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS ritual_rite_completion_idx "
               "ON ritual_rite_completions(rite_id,map_number)")


def target_map_id(db):
    """Bind editor fields to the current or upcoming map, including pre-map inventory."""
    number = _meta(db, "current_map_number", 0)
    if not number or _meta(db, "pending_new_map", False):
        number += 1
    return f"M{number:04d}"


def _selected_rite(db):
    """Resolve the selected Rite ID to its saved name and identity, or return None."""
    selected = _meta(db, "selected_ritual_rite_id")
    return db.execute("SELECT rite_id,name FROM ritual_rites WHERE rite_id=?", (selected,)).fetchone()


def _inherited_sources(db, rite_id, map_id):
    """Carry only each prior origin's own proliferation rows, retaining full provenance."""
    sources = []
    if rite_id is None:
        return sources
    for prior in db.execute("SELECT * FROM ritual_rite_completions WHERE rite_id=? AND map_number<? "
                            "ORDER BY map_number,map_id", (rite_id, int(map_id[1:]))):
        snapshot = json.loads(prior["snapshot_json"])
        for position, affix in enumerate(snapshot.get("ritual_rite_affixes", [])):
            if affix.get("kind") == "proliferation":
                sources.append({"origin_map_id": prior["map_id"], "origin_rite_id": rite_id,
                                "origin_map_ordinal": prior["ordinal"], "origin_affix_position": position + 1,
                                "affix_id": affix["id"], "text": affix.get("inherited_text") or affix["text"],
                                "count": affix.get("count", 1), "affix": affix})
    return sources


def _live_state(db, map_id):
    """Assemble this map's Rite draft, ordinal and completed-map sources without writing.

    Rites apply only while Ritual is the selected Activity Type; other maps keep
    the remembered selection but join no Rite stage and inherit nothing.
    """
    ritual = (_meta(db, "settings") or {}).get("activity_type") == "Ritual"
    rite = _selected_rite(db)
    selected = rite["rite_id"] if rite else None
    ordinal = None
    if selected is not None and ritual:
        prior = db.execute("SELECT ordinal FROM ritual_rite_completions WHERE map_id=? AND rite_id=?",
                           (map_id, selected)).fetchone()
        ordinal = prior[0] if prior else db.execute(
            "SELECT COALESCE(MAX(ordinal),0)+1 FROM ritual_rite_completions WHERE rite_id=? AND map_number<?",
            (selected, int(map_id[1:]))).fetchone()[0]
    draft = db.execute("SELECT affixes_json FROM ritual_rite_map_settings WHERE map_id=?", (map_id,)).fetchone()
    return {"selected_rite_id": selected, "selected_rite_name": rite["name"] if rite else "",
            "map_id": map_id, "map_ordinal": ordinal, "session_generation": _meta(db, "session_generation", 0),
            "current_affixes": json.loads(draft[0]) if draft else [], "ritual_activity": ritual,
            "inherited_sources": _inherited_sources(db, selected, map_id) if ritual else [],
            "available_rites": [{"id": row["rite_id"], "name": row["name"]}
                                for row in db.execute("SELECT rite_id,name FROM ritual_rites ORDER BY rowid")]}


def state(db):
    """Read the editor and its explicit source observations from the caller's snapshot."""
    return _live_state(db, target_map_id(db))


def _context_from_state(value):
    """Project optional Rite state into observation fields, omitting wholly empty metadata."""
    if not value["ritual_activity"] or not (value["selected_rite_id"] or value["current_affixes"] or
                                             value["inherited_sources"]):
        return {}
    return {"ritual_rite_id": value["selected_rite_id"], "ritual_rite_name": value["selected_rite_name"],
            "ritual_rite_map_ordinal": value["map_ordinal"], "ritual_rite_affixes": value["current_affixes"],
            "ritual_rite_inherited_sources": value["inherited_sources"]}


def attach_context(db, map_id, context):
    """Freeze optional metadata on a new observation without rewriting earlier snapshots."""
    if not map_id:
        return context
    if map_id == target_map_id(db):
        selected = _context_from_state(_live_state(db, map_id))
    else:
        prior = db.execute("SELECT snapshot_json FROM ritual_rite_completions WHERE map_id=?", (map_id,)).fetchone()
        if prior is None:
            return context
        selected = json.loads(prior[0])
    result = dict(context)
    for key in CONTEXT_FIELDS:
        result.pop(key, None)
    result.update(selected)
    return result


def finalize_map(db, map_id):
    """Record one replaceable completion per map and attach its exact local/source metadata."""
    saved = db.execute("SELECT snapshot_json FROM ritual_rite_completions WHERE map_id=?", (map_id,)).fetchone()
    if saved is not None and map_id != target_map_id(db):
        return json.loads(saved[0])
    selected = _context_from_state(_live_state(db, map_id))
    if not selected and saved is None:
        return {}
    rite_id = selected.get("ritual_rite_id")
    ordinal = selected.get("ritual_rite_map_ordinal")
    db.execute("INSERT INTO ritual_rite_completions VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(map_id) DO UPDATE SET rite_id=excluded.rite_id,ordinal=excluded.ordinal,"
               "snapshot_json=excluded.snapshot_json,completed_at=excluded.completed_at",
               (map_id, int(map_id[1:]), rite_id, ordinal, _dump(selected),
                datetime.now(timezone.utc).isoformat(timespec="seconds")))
    row = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
    if row:
        context = json.loads(row[0])
        for key in CONTEXT_FIELDS:
            context.pop(key, None)
        context.update(selected)
        db.execute("UPDATE maps SET snapshot_json=? WHERE map_id=?", (_dump(context), map_id))
    return selected


def clear_session(db):
    """Retain the declared library/selection but remove carry and drafts before Map IDs repeat."""
    db.execute("DELETE FROM ritual_rite_map_settings")
    db.execute("DELETE FROM ritual_rite_completions")


def discard_completion(db, map_id):
    """A map whose logs were discarded is no longer a completed inheritance source."""
    db.execute("DELETE FROM ritual_rite_completions WHERE map_id=?", (map_id,))


def ritual_rite_state():
    """Return a consistent JSON-shaped optional editor state."""
    from PoE2_Data_Logger.core import logger_store as logger
    with logger._connect() as db:
        db.execute("BEGIN")
        return state(db)


def new_ritual_rite(name=None):
    """Create/select an explicit named Rite without guessing how many maps it will contain."""
    from PoE2_Data_Logger.core import logger_store as logger
    with logger._connect(undo_label="New Ritual Rite") as db:
        db.execute("BEGIN IMMEDIATE")
        if name is None:
            number = 1
            while db.execute("SELECT 1 FROM ritual_rites WHERE name=? COLLATE NOCASE", (f"Rite {number}",)).fetchone():
                number += 1
            name = f"Rite {number}"
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("Enter a Rite name between 1 and 80 characters.")
        name = name.strip()
        if db.execute("SELECT 1 FROM ritual_rites WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            raise ValueError("A Rite with that name already exists.")
        rite_id = "RITE-" + uuid.uuid4().hex
        db.execute("INSERT INTO ritual_rites VALUES(?,?,?)", (rite_id, name,
                   datetime.now(timezone.utc).isoformat(timespec="seconds")))
        _set_meta(db, "selected_ritual_rite_id", rite_id)
        return state(db)


def select_ritual_rite(rite_id):
    """Declare this map's optional Rite; None explicitly disables Rite membership/carry."""
    from PoE2_Data_Logger.core import logger_store as logger
    with logger._connect(undo_label="Select Ritual Rite") as db:
        db.execute("BEGIN IMMEDIATE")
        rite_id = rite_id or None
        if rite_id is not None and db.execute("SELECT 1 FROM ritual_rites WHERE rite_id=?", (rite_id,)).fetchone() is None:
            raise ValueError("Choose an existing Rite or None.")
        _set_meta(db, "selected_ritual_rite_id", rite_id)
        return state(db)


def save_ritual_rite_affixes(values, *, expected_map_id=None, expected_generation=None):
    """Save map-local affixes, preserving custom wording and rejecting stale map editors."""
    from PoE2_Data_Logger.core import logger_store as logger
    normalized = normalize_affixes(values)
    with logger._connect(undo_label="Ritual Rite modifiers") as db:
        db.execute("BEGIN IMMEDIATE")
        target = target_map_id(db)
        if expected_generation is not None and expected_generation != _meta(db, "session_generation", 0):
            raise ValueError("The session changed. Review Rite modifiers for the current map again.")
        if expected_map_id is not None and expected_map_id != target:
            raise ValueError("The map changed. Review Rite modifiers for the current map again.")
        db.execute("INSERT INTO ritual_rite_map_settings VALUES(?,?) ON CONFLICT(map_id) "
                   "DO UPDATE SET affixes_json=excluded.affixes_json", (target, _dump(normalized)))
        return state(db)
