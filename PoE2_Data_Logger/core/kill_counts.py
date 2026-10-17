"""Save pixel-read kill counters as independent, undoable map observations.

These readings do not replace manual map totals, create game activities or
derive increments. A chain is an optional association, never a save prerequisite.
"""

from contextlib import nullcontext
import csv
import hashlib
import io
import json
import uuid

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store


SCOPES = frozenset(("map_stage", "chain_stage", "map_final"))
CATEGORIES = ("normal", "magic", "rare", "unique")
CSV_HEADERS = (
    "Kill Snapshot ID", "Map ID", "Expedition ID", "Kill Count Scope",
    "Normal Kills (Observed)", "Magic Kills (Observed)", "Rare Kills (Observed)",
    "Unique Kills (Observed)", "Total Kills (Observed)", "Count Interpretation",
    "Scan Commit #", "Source Mode", "Captured UTC", "Recorded UTC", "Request ID",
    "Image SHA256", "OCR JSON", "Capture Context JSON",
)
COUNT_INTERPRETATION = "Observed counter totals; not increments"
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _json(value, label):
    """Freeze JSON-compatible provenance without executable serialization."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"The kill-count {label} must contain valid JSON values.") from error


def _source(value):
    """Normalize the scanner identifier while preserving an empty manual source."""
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("The kill-count source mode is invalid.")
    return value.strip().lower()


def capture_context(source_mode=""):
    """Freeze map/session ownership and optional chain association before capture.

    The default expedition created with every new map is not, by itself, a
    remnant chain. A remnant/propagation capture or saved chain parts establish
    an optional association. Saving still independently requires an active map.
    """
    source_mode = _source(source_mode)
    with logger._connect() as db:
        db.execute("BEGIN")
        number = logger._meta(db, "current_map_number", 0)
        mid = logger._map_id(number) if number else ""
        expedition = logger._meta(db, "settings")["expedition"]
        eid = logger._exp_id(mid, expedition) if mid else ""
        associated = bool(mid and (source_mode in ("propagation", "remnant") or
                                  logger._saved_chain_rows(db, eid)))
        return {
            "_scan_generation": logger._meta(db, "session_generation", 0),
            "_capture_map_id": mid,
            "_capture_map_pending": bool(logger._meta(db, "pending_new_map", False)),
            "_capture_expedition": expedition,
            "_capture_chain_revision": logger._chain_revision(db, eid) if eid else 0,
            "_capture_chain_associated": associated,
            "source_mode": source_mode,
            "captured_at": logger._now(),
        }


def _validate_context(db, context, *, allow_final=False):
    """Reject delayed callbacks after a map transition, reset, discard or Undo."""
    if (not isinstance(context, dict) or
            type(context.get("_scan_generation")) is not int or
            type(context.get("_capture_expedition")) is not int or
            context["_capture_expedition"] < 1 or
            type(context.get("_capture_chain_revision")) is not int or
            type(context.get("_capture_chain_associated")) is not bool or
            type(context.get("_capture_map_pending")) is not bool or
            not isinstance(context.get("captured_at"), str) or not context["captured_at"]):
        raise ValueError("The kill-count capture context is invalid. Scan again.")
    number = logger._meta(db, "current_map_number", 0)
    if not number:
        raise ValueError("Start a map before saving kill-count readings.")
    mid = logger._map_id(number)
    pending = bool(logger._meta(db, "pending_new_map", False))
    if pending and not allow_final:
        raise ValueError("Start the next map before saving kill-count readings.")
    expected = {
        "_scan_generation": logger._meta(db, "session_generation", 0),
        "_capture_map_id": mid, "_capture_map_pending": pending,
    }
    if any(context.get(key) != value for key, value in expected.items()):
        raise ValueError("The map or session changed. Scan kill counts again.")
    if not db.execute("SELECT 1 FROM maps WHERE map_id=?", (mid,)).fetchone():
        raise ValueError("The captured map no longer exists. Scan kill counts again.")
    return mid


def _image_evidence(image_bytes):
    """Keep supplied PNG evidence byte-for-byte and hash its actual contents."""
    if image_bytes is None:
        return None, ""
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise ValueError("Kill-count screenshot evidence must be PNG bytes.")
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Kill-count screenshot evidence must be at most 16 MiB.")
    raw = bytes(image_bytes)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG":
                raise ValueError("Kill-count screenshot evidence must be PNG bytes.")
            if not 0 < image.width * image.height <= store.MAX_IMAGE_PIXELS:
                raise ValueError("Kill-count screenshot evidence exceeds the image pixel limit.")
            image.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("Kill-count screenshot evidence is not a valid PNG.") from error
    return raw, hashlib.sha256(raw).hexdigest()


def _saved_result(row, *, already_saved):
    """Return stable identifiers and exact observed values for retry callbacks."""
    counts = {"normal": row["normal"], "magic": row["magic"],
              "rare": row["rare"], "unique": row["unique_kills"]}
    return {"snapshot_id": row["snapshot_id"], "map_id": row["map_id"],
            "expedition_id": row["expedition_id"] or "", "scope": row["scope"],
            "scan_commit_number": row["scan_commit_number"], "request_id": row["request_id"],
            "already_saved": already_saved, **counts, "total": sum(counts.values())}


def save_snapshot(result, *, context, request_id, source_mode="", scope="map_stage", image_bytes=None):
    """Atomically save one complete observed reading without changing manual totals.

    A request UUID deduplicates callbacks; reusing it for different pixels,
    ownership or OCR results is rejected. Incomplete or noninteger readings are
    never converted to zero. A chain-stage request without a captured chain
    association remains a valid map-stage reading.
    """
    if not isinstance(result, dict) or result.get("complete") is not True:
        raise ValueError("All four kill counters must be read before saving.")
    counts = [result.get(category) for category in CATEGORIES]
    if any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in counts):
        raise ValueError("All four kill counters must be explicit nonnegative whole numbers.")
    if not isinstance(request_id, str):
        raise ValueError("The kill-count request ID must be a UUID.")
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("The kill-count request ID must be a UUID.") from error
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError("The kill-count scope is invalid.")
    ocr_json = _json(result, "OCR result")
    context_json = _json(context, "capture context")
    context = json.loads(context_json)
    if not isinstance(context, dict):
        raise ValueError("The kill-count capture context is invalid. Scan again.")
    captured_source = _source(context.get("source_mode", ""))
    source_mode = _source(source_mode) or captured_source
    if source_mode != captured_source:
        raise ValueError("The kill-count source changed after capture. Scan again.")
    image_png, image_sha256 = _image_evidence(image_bytes)
    payload = _json({"ocr": result, "context": context, "source_mode": source_mode,
                     "scope": scope, "image_sha256": image_sha256}, "request")
    payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    with logger._connect(undo_label="Kill count OCR") as db:
        db.execute("BEGIN IMMEDIATE")
        mid = _validate_context(db, context, allow_final=scope == "map_final")
        existing = db.execute("SELECT * FROM kill_count_snapshots WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            if existing["payload_sha256"] != payload_sha256:
                raise ValueError("This kill-count request ID was already used for a different reading.")
            return _saved_result(existing, already_saved=True)
        associated = context["_capture_chain_associated"]
        if scope == "chain_stage" and not associated:
            scope = "map_stage"
        eid = None
        if scope == "chain_stage":
            expedition = context["_capture_expedition"]
            eid = logger._exp_id(mid, expedition)
            if (logger._meta(db, "settings")["expedition"] != expedition or
                    logger._chain_revision(db, eid) != context["_capture_chain_revision"]):
                raise ValueError("The captured chain changed. Scan kill counts again.")
        recorded_at = logger._now()
        map_context = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (mid,)).fetchone()[0])
        number = logger._record_commit(db, "Kill count OCR", mid, eid or "",
            reference=f"{scope}: {sum(counts)} observed kills", context=map_context,
            details={"kill_count_reading": {**dict(zip(CATEGORIES, counts)), "scope": scope,
                     "interpretation": COUNT_INTERPRETATION, "source_mode": source_mode,
                     "request_id": request_id, "captured_at": context["captured_at"],
                     "image_sha256": image_sha256}})
        cursor = db.execute("INSERT INTO kill_count_snapshots("
            "request_id,session_generation,map_id,expedition_id,scope,normal,magic,rare,unique_kills,"
            "scan_commit_number,source_mode,captured_at,recorded_at,image_sha256,image_png,ocr_json,context_json,payload_sha256) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                request_id, context["_scan_generation"], mid, eid, scope, *counts, number, source_mode,
                context["captured_at"], recorded_at, image_sha256, image_png, ocr_json, context_json, payload_sha256))
        row = db.execute("SELECT * FROM kill_count_snapshots WHERE snapshot_id=?", (cursor.lastrowid,)).fetchone()
        return _saved_result(row, already_saved=False)


def export_csv(*, _db=None):
    """Export independent observed stages and finals without summing across readings."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    with (nullcontext(_db) if _db is not None else logger._connect()) as db:
        if _db is None:
            db.execute("BEGIN")
        for row in db.execute("SELECT * FROM kill_count_snapshots ORDER BY snapshot_id"):
            counts = [row[key] for key in ("normal", "magic", "rare", "unique_kills")]
            writer.writerow(logger._csv_row([
                row["snapshot_id"], row["map_id"], row["expedition_id"] or "", row["scope"],
                *counts, sum(counts), COUNT_INTERPRETATION, row["scan_commit_number"], row["source_mode"],
                row["captured_at"], row["recorded_at"], row["request_id"], row["image_sha256"],
                row["ocr_json"], row["context_json"],
            ]))
    return output.getvalue().encode("utf-8-sig")
