from __future__ import annotations

import csv
from contextlib import contextmanager
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RUNESHAPE_SCAN_DATA_DIR", str(Path.home() / "Runeshape Scan")))
MAX_IMAGE_PIXELS = 12_000_000
STATES = json.loads((HERE / "catalog.json").read_text())["states"]


def states() -> list[dict]:
    from PoE2_Data_Logger.core import logger_store
    logger_store.initialize()
    with _connect() as db:
        rows = db.execute("SELECT s.family,s.sockets,s.seed_slot,s.seed_rune,s.rewards_json,s.status "
                          "FROM seed_states s JOIN families f ON f.id=s.family "
                          "WHERE f.valid=1 ORDER BY s.family,s.sockets").fetchall()
    return [{"family": r[0], "sockets": r[1], "seed_slot": r[2], "seed_rune": r[3],
             "rewards": json.loads(r[4]), "status": r[5]} for r in rows]


def candidates(sockets: int, slot: str, rune: str) -> list[dict]:
    return [s for s in states() if s["sockets"] == sockets and
            s["seed_slot"] == slot and s["seed_rune"] == rune]


def validate(sockets, slot, rune, family=None):
    from PoE2_Data_Logger.core.logger_store import _integer
    depth = _integer(sockets, "Socket count", 3, 10)
    if slot not in [f"P{i}" for i in range(1, depth + 1)]:
        raise ValueError("Visible slot must be inside the socket bar.")
    if not rune or len(rune) > 80:
        raise ValueError("Enter the visible rune name.")
    matches = candidates(depth, slot, rune)
    if family not in (None, ""):
        family_number = _integer(family, "Family", 1, 9999)
        selected = next((s for s in matches if s["family"] == family_number), None)
        if selected is None:
            raise ValueError("That family does not match the reviewed seed and socket stage.")
    elif len(matches) == 1:
        selected = matches[0]
    elif matches:
        raise ValueError("Several families match. Choose one before saving.")
    else:
        selected = None
    return depth, selected


@contextmanager
def _connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "scans.sqlite3", timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY,
            recorded_at TEXT NOT NULL,
            file_name TEXT NOT NULL,
            image_name TEXT NOT NULL,
            image_sha256 TEXT NOT NULL UNIQUE,
            sockets INTEGER NOT NULL,
            seed_slot TEXT NOT NULL,
            seed_rune TEXT NOT NULL,
            family TEXT NOT NULL,
            rewards_json TEXT NOT NULL,
            mapping_status TEXT NOT NULL
        )""")
        with db:
            yield db
    finally:
        db.close()


def save_scan(raw: bytes, file_name: str, sockets, slot: str, rune: str, family=None):
    depth, selected = validate(sockets, slot, rune, family)
    suffix = ".png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
    if suffix == ".jpg" and not raw.startswith(b"\xff\xd8\xff"):
        raise ValueError("Choose a PNG or JPEG screenshot.")
    digest = hashlib.sha256(raw).hexdigest()
    safe_name = Path(file_name.replace("\\", "/")).name[:200] or f"screenshot{suffix}"
    images = DATA_DIR / "images"
    images.mkdir(parents=True, exist_ok=True)
    image_name = digest + suffix
    destination = images / image_name
    glyph = _reviewed_vector(raw, depth, slot)
    if not destination.exists():
        fd, name = tempfile.mkstemp(dir=images, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
    rewards = selected["rewards"] if selected else []
    status = selected["status"] if selected else "unresolved"
    family_text = f"Family {selected['family']}" if selected else ""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as db:
        db.execute("""INSERT INTO scans (recorded_at,file_name,image_name,image_sha256,
                     sockets,seed_slot,seed_rune,family,rewards_json,mapping_status)
                     VALUES (?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(image_sha256) DO UPDATE SET
                       file_name=excluded.file_name, sockets=excluded.sockets,
                       seed_slot=excluded.seed_slot, seed_rune=excluded.seed_rune,
                       family=excluded.family, rewards_json=excluded.rewards_json,
                       mapping_status=excluded.mapping_status""",
                   (now, safe_name, image_name, digest, depth, slot, rune,
                    family_text, json.dumps(rewards), status))
        row = db.execute("SELECT * FROM scans WHERE image_sha256=?", (digest,)).fetchone()
        if glyph is not None:
            db.execute("INSERT INTO reviewed_glyphs VALUES(?,?,?) ON CONFLICT(image_sha256) "
                       "DO UPDATE SET seed_rune=excluded.seed_rune,vector=excluded.vector",
                       (digest, rune, glyph))
    result = _record(row)
    result["reference_added"] = glyph is not None
    return result


def _reviewed_vector(raw: bytes, sockets: int, slot: str):
    import numpy as np
    from PIL import Image
    from PoE2_Data_Logger.ocr.glyph_eval import vector
    from PoE2_Data_Logger.ocr.prototype import center_for, crop_at, ncc_find

    with Image.open(io.BytesIO(raw)) as source:
        im = source.convert("RGB")
    scores = []
    for name in ("book_bronze.png", "book_purple.png"):
        with Image.open(HERE / name) as template:
            scores.append(ncc_find(im, template))
    bx, by, score = max(scores, key=lambda found: found[2])
    if score < .65:
        return None
    cx, cy = center_for(bx, by, sockets, int(slot[1:]))
    if cx < 18 or cy < 18 or cx + 18 > im.width or cy + 18 > im.height:
        return None
    return np.asarray(vector(crop_at(im, cx, cy, 36, 36), "gray"), dtype=np.float32).tobytes()


def reviewed_glyphs(runes=None):
    import numpy as np
    from PoE2_Data_Logger.core import logger_store
    logger_store.initialize()
    with _connect() as db:
        if runes is None:
            rows = db.execute("SELECT seed_rune,vector FROM reviewed_glyphs").fetchall()
        else:
            choices = sorted(set(runes))
            rows = db.execute("SELECT seed_rune,vector FROM reviewed_glyphs WHERE seed_rune IN ("+
                              ",".join("?" for _ in choices)+")", choices).fetchall() if choices else []
    result = [(r[0], np.frombuffer(r[1], dtype=np.float32)) for r in rows]
    return [(name, values) for name, values in result if values.shape == (1296,)]


def _record(row):
    result = dict(row)
    result["rewards"] = json.loads(result.pop("rewards_json"))
    result.pop("image_name")
    result.pop("image_sha256")
    return result


def list_scans(limit=200):
    with _connect() as db:
        rows = db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_record(row) for row in rows]


def image_for(scan_id):
    with _connect() as db:
        row = db.execute("SELECT image_name FROM scans WHERE id=?", (scan_id,)).fetchone()
    if row is None:
        return None
    return DATA_DIR / "images" / row["image_name"]


def export_csv():
    from PoE2_Data_Logger.core.logger_store import _csv_row
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["Scan ID", "Recorded at (UTC)", "Screenshot", "Sockets",
                     "Visible slot", "Seed rune", "Family", "Stage rewards", "Mapping"])
    with _connect() as db:
        for row in db.execute("SELECT * FROM scans ORDER BY id"):
            writer.writerow(_csv_row([row["id"], row["recorded_at"], row["file_name"],
                                      row["sockets"], row["seed_slot"], row["seed_rune"],
                                      row["family"], " | ".join(json.loads(row["rewards_json"])),
                                      row["mapping_status"]]))
    return out.getvalue().encode("utf-8-sig")
