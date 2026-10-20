"""Persist transaction row changes for atomic undo of saved logging actions."""

from contextlib import contextmanager
import gzip
import json
import threading
import uuid


TABLES = (
    "meta", "legacy_export", "new_export", "maps", "map_unique_kills", "kill_count_snapshots", "expeditions",
    "chain_completions", "chain_step_provenance", "scan_links", "commits",
    "currency_snapshots", "ritual_pages", "currency_items", "item_names", "ritual_names",
    "currency_icons", "item_icons", "omen_icons", "review_icon_examples", "affixes",
    "recipes", "families", "aliases", "alias_keys", "alias_conflicts", "seed_states",
    "master_perks", "reviewed_glyphs", "scans", "tablet_sets",
    "ritual_rites", "ritual_rite_map_settings", "ritual_rite_completions",
)
META_KEYS = frozenset((
    "settings", "current_map_number", "next_remnant_number", "pending_new_map",
    "previous_expedition", "previous_waystone_settings", "previous_biome", "prepared_waystone_setup",
    "reopened_map_id",
    "scan_commit_count", "tablet_auto_next", "selected_tablet_set_id", "finalized_tablet_map_id",
    "active_retired_currency_names",
    "selected_ritual_rite_id",
))
HISTORY_LIMIT = 100
_LOCAL = threading.local()


def initialize(db):
    """Create the persistent undo stack without capturing bootstrap or catalog migrations."""
    db.execute("CREATE TABLE IF NOT EXISTS undo_actions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
               "token TEXT NOT NULL UNIQUE,label TEXT NOT NULL,changes BLOB NOT NULL)")


def _quote(name):
    """Quote schema identifiers obtained from the fixed journal table allowlist."""
    return '"' + name.replace('"', '""') + '"'


def _schema(db, table):
    """Read column order and primary-key columns for reversible row operations."""
    rows = list(db.execute(f"PRAGMA table_info({_quote(table)})"))
    columns = [row["name"] for row in rows]
    keys = [row["name"] for row in sorted(rows, key=lambda row: row["pk"]) if row["pk"]]
    if not columns or not keys:
        raise ValueError("An undo table is missing its primary key.")
    return columns, keys


def _pack(entries):
    """Compress JSON row images, encoding learned image bytes without executable serialization."""
    return gzip.compress(json.dumps(entries, separators=(",", ":"),
                                   default=lambda value: {"blob": value.hex()}).encode(), mtime=0)


def _unpack(raw):
    """Decode compressed row images and restore only explicitly tagged binary values."""
    return json.loads(gzip.decompress(raw), object_hook=lambda value:
                      bytes.fromhex(value["blob"]) if set(value) == {"blob"} else value)


@contextmanager
def group(label):
    """Combine consecutive saves made by one UI command into one undo stack entry."""
    previous = getattr(_LOCAL, "group", None)
    if previous is None:
        _LOCAL.group = (uuid.uuid4().hex, label)
    try:
        yield
    finally:
        _LOCAL.group = previous


@contextmanager
def capture(db, label, *, append_to_latest=False):
    """Capture changed rows on this connection and save their inverses in the same transaction.

    Connection-local triggers avoid whole-database snapshots. Failed transactions
    and transactions with unchanged final row images never add an undo entry;
    reads and OCR reservations are excluded. REPLACE deletions are captured
    along with their insertions. Grouped UI saves retain one chronological entry.
    Implicit discovery can append its inverses to the latest saved user action
    without replacing its label; with no prior action, discovery has no Undo.
    """
    entries = []
    row_images = {}

    def remember(table, operation, *values):
        """Collect native SQLite row values before and after each actual row mutation."""
        width = len(schemas[table][0])
        old = list(values[:width]) if operation != "insert" else None
        new = list(values[width:]) if operation == "update" else list(values) if operation == "insert" else None
        if table != "meta" or (new or old)[0] in META_KEYS:
            entries.append([table, old, new])
            columns, keys = schemas[table]
            identity = (table, tuple((new or old)[columns.index(key)] for key in keys))
            if identity not in row_images:
                row_images[identity] = [old, new]
            else:
                row_images[identity][1] = new
        return 0

    schemas = {table: _schema(db, table) for table in TABLES}
    db.execute("PRAGMA recursive_triggers=ON")
    db.create_function("undo_remember", -1, remember)
    for table, (columns, _) in schemas.items():
        old = ",".join("OLD." + _quote(column) for column in columns)
        new = ",".join("NEW." + _quote(column) for column in columns)
        changed = " OR ".join(f"OLD.{_quote(column)} IS NOT NEW.{_quote(column)}" for column in columns)
        for operation, values in (("insert", new), ("delete", old), ("update", old + "," + new)):
            condition = " WHEN " + changed if operation == "update" else ""
            db.execute(f"CREATE TEMP TRIGGER {_quote('undo_' + table + '_' + operation)} "
                       f"AFTER {operation.upper()} ON main.{_quote(table)}{condition} "
                       f"BEGIN SELECT undo_remember('{table}','{operation}',{values}); END")
    yield
    if not entries or all(old == new for old, new in row_images.values()):
        return
    if append_to_latest:
        # The caller's write transaction serializes discovery with other writers.
        # Recording even reinsertion prevents an earlier deletion inverse from
        # finding an unexpected row when the user undoes a reference reset.
        prior = db.execute("SELECT id,changes FROM undo_actions ORDER BY id DESC LIMIT 1").fetchone()
        if prior:
            entries = _unpack(prior["changes"]) + entries
            db.execute("UPDATE undo_actions SET changes=? WHERE id=?", (_pack(entries), prior["id"]))
        return
    token, shown_label = getattr(_LOCAL, "group", None) or (uuid.uuid4().hex, label)
    prior = db.execute("SELECT id,changes FROM undo_actions WHERE token=?", (token,)).fetchone()
    if prior:
        # A competing writer may have committed while a multi-save UI command
        # was between transactions. Preserve global chronology instead of
        # merging that earlier entry across the competing action.
        latest = db.execute("SELECT MAX(id) FROM undo_actions").fetchone()[0]
        if prior["id"] == latest:
            entries = _unpack(prior["changes"]) + entries
            db.execute("UPDATE undo_actions SET changes=? WHERE id=?", (_pack(entries), prior["id"]))
            return
        token = uuid.uuid4().hex
    db.execute("INSERT INTO undo_actions(token,label,changes) VALUES(?,?,?)", (token, shown_label, _pack(entries)))
    db.execute("DELETE FROM undo_actions WHERE id NOT IN "
               "(SELECT id FROM undo_actions ORDER BY id DESC LIMIT ?)", (HISTORY_LIMIT,))


def latest(db):
    """Return the latest saved action's label without loading session catalogs or row images."""
    row = db.execute("SELECT label FROM undo_actions ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else ""


def restore(db):
    """Reverse the latest action in the caller's write transaction, refusing intervening row edits.

    Repeated changes to a row are reversed in order. Metadata is restored by
    field; transient context invalidation is handled by the logger after undo.
    """
    action = db.execute("SELECT id,label,changes FROM undo_actions ORDER BY id DESC LIMIT 1").fetchone()
    if action is None:
        raise ValueError("There is no saved action to undo.")
    for table, old, new in reversed(_unpack(action["changes"])):
        if table not in TABLES:
            raise ValueError("The undo record contains an unsupported table.")
        columns, keys = _schema(db, table)
        if any(row is not None and len(row) != len(columns) for row in (old, new)):
            raise ValueError("The saved undo record has an invalid row width.")
        where = " AND ".join(_quote(key) + " IS ?" for key in keys)
        key_values = [(new or old)[columns.index(key)] for key in keys]
        current = db.execute(f"SELECT * FROM {_quote(table)} WHERE {where}", key_values).fetchone()
        # Reservations and completion markers can be retired by Undo itself.
        # Session rows still require an exact match to prevent deleting later work.
        if table != "meta" and (list(current) if current is not None else None) != new:
            raise ValueError("This action has later changes that must be undone first.")
        db.execute(f"DELETE FROM {_quote(table)} WHERE {where}", key_values)
        if old is not None:
            db.execute(f"INSERT INTO {_quote(table)} ({','.join(map(_quote, columns))}) "
                       f"VALUES ({','.join('?' for _ in columns)})", old)
    db.execute("DELETE FROM undo_actions WHERE id=?", (action["id"],))
    return action["label"]
