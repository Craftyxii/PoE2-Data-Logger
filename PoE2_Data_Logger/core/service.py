"""Dispatch desktop API actions to scanning, persistence, hotkeys and exports.

Uploaded screenshots are bounded before OCR; remnant scans recheck their saved
session context after OCR before assigning a pending scan ID.
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image, UnidentifiedImageError

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.core.auto_commit import commit as auto_commit_remnant
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ocr.opened_scan import scan_opened, scan_both
from PoE2_Data_Logger.ocr.scan import scan


MAX_UPLOAD = 16 * 1024 * 1024
HOTKEY = HotkeyManager()


def _image(encoded):
    """Decode bounded base64 PNG/JPEG data and verify screenshot dimensions and pixels."""
    if not isinstance(encoded, str) or len(encoded) > (MAX_UPLOAD + 2) * 4 // 3:
        raise ValueError("Screenshot is over 16 MB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Could not read screenshot data.") from error
    if len(raw) < 1 or len(raw) > MAX_UPLOAD or not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        raise ValueError("Choose a PNG or JPEG screenshot under 16 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise ValueError("Choose a PNG or JPEG screenshot.")
            if (image.width < 200 or image.height < 100 or image.width > 8192 or
                    image.height > 8192 or image.width * image.height > store.MAX_IMAGE_PIXELS):
                raise ValueError("Screenshot must be at least 200×100 and at most 12 megapixels.")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Screenshot is damaged or is not a PNG or JPEG.") from error
    return raw


def dispatch(path, data=None):
    """Route desktop actions, staging OCR uploads and rechecking remnant context.

    Read routes expose state, catalogs and saved images; mutation routes delegate
    validation and writes to the matching logger or hotkey operation. Remnant
    OCR freezes its strictness before decoding or invoking any reader.
    """
    data = data or {}
    target = urlsplit(path)
    q = parse_qs(target.query)
    route = target.path
    if route == "/api/state":
        return logger.get_state()
    if route == "/api/scans":
        return store.list_scans()
    if route == "/api/hotkey":
        if "combo" not in data:
            return HOTKEY.status()
        return HOTKEY.configure(data.get("combo", ""))
    if route == "/api/hotkey/image":
        raw = HOTKEY.image(int(q["id"][0]))
        if raw is None:
            raise ValueError("Captured screen is no longer available.")
        return {"image": base64.b64encode(raw).decode("ascii")}
    if route == "/api/image":
        image = store.image_for(int(q["id"][0]))
        if image is None or not image.is_file():
            raise ValueError("Saved screenshot was not found.")
        return {"image": base64.b64encode(image.read_bytes()).decode("ascii"),
                "type": "image/png" if image.suffix.lower() == ".png" else "image/jpeg"}
    if route == "/api/catalog":
        return logger.search_catalog(q.get("q", [""])[0], q.get("kind", ["families"])[0],
                                     q.get("limit", ["100"])[0])
    if route == "/api/candidates":
        return store.candidates(int(q["sockets"][0]), q["slot"][0], q["rune"][0])
    if route == "/api/scan":
        values = data["ocr_strictness"] if "ocr_strictness" in data else ocr_sensitivity.saved_values()
        if not isinstance(values, dict):
            raise ValueError("OCR strictness settings are invalid.")
        strictness_values = {kind: ocr_sensitivity.validate(values.get(kind, ocr_sensitivity.DEFAULT))
                             for kind, _label in ocr_sensitivity.SCAN_TYPES}
        context = data.get("scan_context") or logger.scan_context()
        logger.validate_remnant_context(context)
        expected_generation = data.get("scan_generation", context["_scan_generation"])
        if expected_generation != context["_scan_generation"]:
            raise ValueError("This scan belongs to the previous session. Scan again.")
        raw = _image(data.get("image"))
        mode = q.get("mode", ["seed"])[0]
        if mode not in ("seed", "opened", "both"):
            raise ValueError("Choose visible seed or opened remnant mode.")
        strictness = strictness_values.get("seed" if mode == "seed" else "remnant", ocr_sensitivity.DEFAULT)
        options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}
        if mode == "both" and strictness_values.get("seed", ocr_sensitivity.DEFAULT) != strictness:
            options["seed_strictness"] = strictness_values.get("seed", ocr_sensitivity.DEFAULT)
        fd, name = tempfile.mkstemp(suffix=".png" if raw.startswith(b"\x89PNG") else ".jpg")
        try:
            with os.fdopen(fd, "wb") as screenshot:
                screenshot.write(raw)
            result = scan_both(Path(name), **options) if mode == "both" else (
                scan_opened(Path(name), **options) if mode == "opened" else scan(Path(name), **options))
            if mode == "both":
                mode = result["mode"]
            selected_strictness = strictness_values.get("seed" if mode == "seed" else "remnant", ocr_sensitivity.DEFAULT)
            if selected_strictness != ocr_sensitivity.DEFAULT:
                result["_ocr_strictness"] = selected_strictness
            result["_ocr_strictness_values"] = strictness_values
            result["mode"] = mode
            logger.validate_remnant_context(context)
            if mode != "seed" or result.get("remnants") or result.get("sockets"):
                if data.get("defer_ocr_id"):
                    result["_target_map_id"] = data.get("map_id")
                else:
                    result.update(logger.assign_ocr_id(mode, data.get("map_id"), expected_generation,
                                                      context["_capture_expedition"]))
            result.update(context)
            return result
        finally:
            Path(name).unlink(missing_ok=True)
    if route == "/api/save-scan":
        raw = _image(data.get("image"))
        return store.save_scan(raw, data.get("filename", "screenshot"), data.get("sockets"),
                               data.get("slot"), data.get("rune"), data.get("family"))
    routes = {
        "/api/hotkey/mode": lambda: HOTKEY.set_mode(data.get("mode")),
        "/api/settings": lambda: logger.save_settings(data),
        "/api/affixes": lambda: logger.add_affix(data.get("name")),
        "/api/recipe": lambda: logger.save_recipe(data),
        "/api/family": lambda: logger.save_family(data),
        "/api/seed-state": lambda: logger.save_seed_state(data),
        "/api/tablets/clear": logger.clear_tablets,
        "/api/ocr/discard": logger.discard_ocr_id,
        "/api/next-chain": logger.start_next_chain,
        "/api/resolve": lambda: logger.resolve(data.get("first"), data.get("next"), data.get("family")),
        "/api/remnants": lambda: logger.commit_remnant(data.get("first"), data.get("next"),
                                              data.get("family"), data.get("scan_id")),
        "/api/remnants/auto": lambda: auto_commit_remnant(
            data.get("opened"), data.get("seed"), data.get("scan_id")),
        "/api/chain": lambda: logger.commit_chain(data.get("rune1"), data.get("rune2")),
        "/api/chain/batch": lambda: logger.commit_chain_runes(data.get("runes")) if "runes" in data
                             else logger.commit_chain_steps(data.get("steps")),
        "/api/export-folder": lambda: logger.save_export_folder(data.get("folder")),
        "/api/export-file": lambda: logger.save_export_file(data.get("kind", "xlsx")),
        "/api/kills": lambda: logger.save_kills(data.get("normal"), data.get("magic"), data.get("rare"),
                                             **({"unique": data["unique"]} if "unique" in data else {})),
        "/api/detonated": lambda: logger.save_detonated(data.get("detonated")),
        "/api/finish-map": lambda: logger.finish_map(data.get("normal"), data.get("magic"),
                                              data.get("rare"),
                                              **{key: data[key] for key in ("detonated", "unique") if key in data}),
        "/api/pending-map": lambda: logger.mark_next_map(data.get("pending")),
        "/api/reset-logger": logger.clear_export_and_reset_ids,
    }
    if route not in routes:
        raise ValueError("Unknown desktop action.")
    return routes[route]()
