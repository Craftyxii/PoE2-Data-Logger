from __future__ import annotations

import io
import sys
import tempfile
import threading
from pathlib import Path

from PIL import ImageGrab

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.ocr.opened_scan import scan_opened, scan_both
from PoE2_Data_Logger.ocr.scan import scan
from PoE2_Data_Logger.platform.hover_copy import read_hovered_text
from PoE2_Data_Logger.ocr.item_text import parse_item_text


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_TIMER = 0x0113
WM_QUIT = 0x0012
HOTKEY_ID = 0x4A31
DEDICATED = ("remnant", "waystone", "tablet", "currency", "ritual", "overlay")
KEY_CODES = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "PAUSE": 0x13,
    "CAPSLOCK": 0x14, "ESCAPE": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21,
    "PAGEDOWN": 0x22, "END": 0x23, "HOME": 0x24, "LEFT": 0x25,
    "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "PRINTSCREEN": 0x2C,
    "INSERT": 0x2D, "DELETE": 0x2E, "MENU": 0x5D, "NUMMULTIPLY": 0x6A,
    "NUMADD": 0x6B, "NUMSUBTRACT": 0x6D, "NUMDECIMAL": 0x6E,
    "NUMDIVIDE": 0x6F, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "VOLUMEMUTE": 0xAD, "VOLUMEDOWN": 0xAE, "VOLUMEUP": 0xAF,
    "MEDIANEXT": 0xB0, "MEDIAPREVIOUS": 0xB1, "MEDIASTOP": 0xB2,
    "MEDIAPLAYPAUSE": 0xB3, "SEMICOLON": 0xBA, "EQUAL": 0xBB,
    "COMMA": 0xBC, "MINUS": 0xBD, "PERIOD": 0xBE, "SLASH": 0xBF,
    "BACKTICK": 0xC0, "BRACKETLEFT": 0xDB, "BACKSLASH": 0xDC,
    "BRACKETRIGHT": 0xDD, "APOSTROPHE": 0xDE,
}
KEY_CODES.update({f"NUM{i}": 0x60 + i for i in range(10)})
KEY_ALIASES = {
    "ESC": "ESCAPE", "RETURN": "ENTER", "PGUP": "PAGEUP", "PGDOWN": "PAGEDOWN",
    "PGDN": "PAGEDOWN", "DEL": "DELETE", "INS": "INSERT", "PLUS": "EQUAL",
    ";": "SEMICOLON", "=": "EQUAL", ",": "COMMA", "-": "MINUS", ".": "PERIOD",
    "/": "SLASH", "`": "BACKTICK", "[": "BRACKETLEFT", "\\": "BACKSLASH",
    "]": "BRACKETRIGHT", "'": "APOSTROPHE",
}
KEY_LABELS = {name: name.title() for name in KEY_CODES}
KEY_LABELS.update({"PAGEUP": "PageUp", "PAGEDOWN": "PageDown", "CAPSLOCK": "CapsLock",
                   "PRINTSCREEN": "PrintScreen", "NUMLOCK": "NumLock", "SCROLLLOCK": "ScrollLock"})


def virtual_key_name(code):
    if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A:
        return chr(code)
    if 0x70 <= code <= 0x87:
        return f"F{code - 0x70 + 1}"
    return next((KEY_LABELS[name] for name, number in KEY_CODES.items() if number == code),
                f"VK_{code:02X}")


def parse_combo(value):
    value = str(value or "").strip()
    if not value:
        return "", 0, 0
    if value == "+":
        value = "Plus"
    elif value.endswith("++"):
        value = value[:-1] + "Plus"
    parts = [part.strip().upper() for part in value.split("+")]
    aliases = {"CONTROL": "CTRL", "OPTION": "ALT"}
    parts = [aliases.get(part, part) for part in parts]
    modifiers = {"CTRL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}
    chosen = [part for part in parts if part in modifiers]
    if len(chosen) != len(parts)-1 or len(chosen) != len(set(chosen)):
        raise ValueError("Choose one keyboard key, optionally with Ctrl, Alt, Shift, or Win.")
    key = KEY_ALIASES.get(parts[-1], parts[-1])
    if key == "C" and "CTRL" in chosen:
        raise ValueError("Ctrl+C is PoE2's item copy key. Choose another hotkey.")
    if key in modifiers:
        raise ValueError("Press a keyboard key with the selected modifiers.")
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        virtual_key = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
    elif key in KEY_CODES:
        virtual_key = KEY_CODES[key]
        key = KEY_LABELS[key]
    elif key.startswith("VK_") and len(key) == 5:
        try:
            virtual_key = int(key[3:], 16)
        except ValueError:
            virtual_key = 0
        if not 1 <= virtual_key <= 0xFE:
            raise ValueError("This keyboard key is not supported by Windows.")
    else:
        raise ValueError("Choose a keyboard key or key combination.")
    canonical = "+".join([name for name in ("CTRL", "ALT", "SHIFT", "WIN") if name in chosen] + [key])
    return canonical.replace("CTRL", "Ctrl").replace("ALT", "Alt").replace("SHIFT", "Shift").replace("WIN", "Win"), (
        sum(modifiers[name] for name in chosen) | MOD_NOREPEAT), virtual_key


class HotkeyManager:
    def __init__(self, grabber=None, readers=None, supported=None, hover_reader=None,
                 tooltip_grabber=None, focused=None):
        self.supported = sys.platform == "win32" if supported is None else supported
        self.grabber = grabber or ImageGrab.grab
        self.readers = readers or {"seed": scan, "opened": scan_opened, "both": scan_both}
        self.hover_reader = hover_reader or read_hovered_text
        self.tooltip_grabber = tooltip_grabber
        if focused is None and self.supported and sys.platform == "win32":
            from PoE2_Data_Logger.platform.live_watch import game_foreground
            focused = game_foreground
        self.focused = focused
        self.mode = "opened"
        self.combo = ""
        self.combos = {name: "" for name in DEDICATED}
        self.error = ""
        self._thread = None
        self._thread_id = None
        self._lock = threading.RLock()
        self._configuration_lock = threading.RLock()
        self._listener_stop = None
        self._capture_lock = threading.Lock()
        self._capture_revision = 0
        self._latest = None
        self._image = None
        self._sequence = 0
        self._overlay_sequence = 0
        self.before_capture = None
        self.on_event = None

    def _notify_event(self):
        callback = self.on_event
        if callback:
            try:
                callback()
            except Exception:
                pass

    def start(self):
        with store._connect() as db:
            settings = logger._meta(db, "ocr_shortcut", {"combo": "", "mode": "opened"})
        self.mode = settings.get("mode") if settings.get("mode") in self.readers else "opened"
        self.combo = settings.get("combo", "")
        self.combos = {name: str((settings.get("combos") or {}).get(name, ""))
                       for name in DEDICATED}
        if self.supported and (self.combo or any(self.combos.values())):
            try:
                self._replace(self.combo, self.combos, persist=False)
            except ValueError as exc:
                self.error = str(exc)

    def status(self):
        with self._lock:
            return {"supported": self.supported, "combo": self.combo,
                    "combos": dict(self.combos),
                    "registered": bool(self._thread and self._thread.is_alive()),
                    "mode": self.mode, "error": self.error, "latest": self._latest,
                    "overlay_sequence": self._overlay_sequence}

    def set_mode(self, mode):
        if mode not in self.readers:
            raise ValueError("Choose visible seed or opened remnant mode.")
        with self._lock:
            self.mode = mode
            self._persist()
            return self.status()

    def configure(self, combo, persist=True):
        with self._configuration_lock:
            canonical, _, _ = parse_combo(combo)
            return self._replace(canonical, self.combos, persist)

    def configure_for(self, kind, combo, persist=True):
        if kind not in DEDICATED:
            raise ValueError("Choose a supported scan type.")
        with self._configuration_lock:
            canonical, _, _ = parse_combo(combo)
            combos = {**self.combos, kind: canonical}
            return self._replace(self.combo, combos, persist)

    def _replace(self, combo, combos, persist=True):
        if not self.supported:
            raise ValueError("Screen capture shortcuts are available on Windows.")
        with self._configuration_lock:
            definitions = {"default": combo, **combos}
            used = [value for value in definitions.values() if value]
            if len(used) != len(set(used)):
                raise ValueError("Each scan shortcut needs a different key combination.")
            parsed = {name: parse_combo(value)[1:] for name, value in definitions.items() if value}
            if combo == self.combo and combos == self.combos and self._thread and self._thread.is_alive():
                return self.status()
            old, old_combos = self.combo, dict(self.combos)
            self._unregister()
            try:
                if parsed:
                    self._register(parsed)
            except ValueError:
                if old or any(old_combos.values()):
                    try:
                        original = {"default": old, **old_combos}
                        self._register({name: parse_combo(value)[1:] for name, value in original.items() if value})
                    except ValueError:
                        pass
                raise
            with self._lock:
                self.combo = combo
                self.combos = dict(combos)
                self.error = ""
                if persist:
                    self._persist()
            return self.status()

    def _persist(self):
        with store._connect() as db:
            logger._set_meta(db, "ocr_shortcut", {"combo": self.combo, "mode": self.mode,
                                                  "combos": self.combos})

    def _register(self, shortcuts):
        ready = threading.Event()
        stop = threading.Event()
        outcome = {}
        thread = threading.Thread(target=self._message_loop,
                                  args=(shortcuts, ready, outcome, stop), daemon=True)
        with self._lock:
            self._thread = thread
            self._listener_stop = stop
        thread.start()
        if not ready.wait(3):
            self._thread_id = outcome.get("thread_id")
            self._unregister()
            raise ValueError("Could not start the shortcut listener.")
        if outcome.get("error"):
            thread.join(2)
            self._thread = None
            self._listener_stop = None
            raise ValueError(outcome["error"])
        with self._lock:
            self._thread_id = outcome["thread_id"]

    def _unregister(self):
        with self._configuration_lock:
            with self._lock:
                thread, thread_id = self._thread, self._thread_id
                if self._listener_stop is not None:
                    self._listener_stop.set()
            if thread and thread.is_alive():
                if thread_id is not None:
                    import ctypes
                    from ctypes import wintypes
                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT,
                                                          wintypes.WPARAM, wintypes.LPARAM)
                    user32.PostThreadMessageW.restype = wintypes.BOOL
                    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                thread.join(2)
                if thread.is_alive():
                    raise ValueError("Could not replace the active shortcut. Restart the logger.")
            with self._lock:
                self._thread = None
                self._thread_id = None
                self._listener_stop = None

    def _message_loop(self, shortcuts, ready, outcome, stop):
        try:
            self._listen(shortcuts, ready, outcome, stop)
        except Exception as error:
            outcome["error"] = f"Could not run the shortcut listener: {error}"
            with self._lock:
                self.error = outcome["error"]
        finally:
            ready.set()

    def _listen(self, shortcuts, ready, outcome, stop):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT)
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                        wintypes.UINT, wintypes.UINT, wintypes.UINT)
        user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        outcome["thread_id"] = kernel32.GetCurrentThreadId()
        registered = {}
        timer = 0
        try:
            for offset, (kind, (modifiers, key)) in enumerate(shortcuts.items()):
                ident = HOTKEY_ID + offset
                if not user32.RegisterHotKey(None, ident, modifiers, key):
                    outcome["error"] = f"{kind.title()} shortcut is unavailable or already in use. Choose another combination."
                    break
                registered[ident] = kind
            definitions = dict(registered)
            if not outcome.get("error") and self.focused is not None:
                timer = user32.SetTimer(None, 0, 100, None)
                if not timer:
                    outcome["error"] = "Could not watch the active game window."
                elif not self.focused():
                    for ident, kind in list(registered.items()):
                        if kind != "overlay":
                            user32.UnregisterHotKey(None, ident)
                            del registered[ident]
            ready.set()
            if outcome.get("error"):
                return
            while not stop.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if stop.is_set():
                    break
                if msg.message == WM_TIMER and msg.wParam == timer:
                    active = self.focused()
                    for ident, kind in definitions.items():
                        enabled = kind == "overlay" or active
                        if not enabled and ident in registered:
                            user32.UnregisterHotKey(None, ident)
                            del registered[ident]
                        elif enabled and ident not in registered:
                            modifiers, key = shortcuts[kind]
                            if user32.RegisterHotKey(None, ident, modifiers, key):
                                registered[ident] = kind
                            else:
                                with self._lock:
                                    self.error = f"{kind.title()} shortcut is unavailable or already in use."
                elif msg.message == WM_HOTKEY and msg.wParam in registered:
                    kind = registered[msg.wParam]
                    self.capture(kind, background=True)
        finally:
            if timer:
                user32.KillTimer(None, timer)
            for ident in registered:
                user32.UnregisterHotKey(None, ident)

    def capture(self, kind="default", background=False):
        if kind == "overlay":
            with logger._connect() as db:
                enabled = bool(logger._meta(db, "hud_overlay", False))
            if enabled:
                with self._lock:
                    self._overlay_sequence += 1
                self._notify_event()
            return
        if self.focused is not None and not self.focused():
            return
        if not self._capture_lock.acquire(blocking=False):
            return
        with self._lock:
            mode = self.mode if kind in ("default", "remnant") else kind
            self._capture_revision += 1
            revision = self._capture_revision
        try:
            if self.before_capture:
                self.before_capture()
            if self.focused is not None and not self.focused():
                self._capture_lock.release()
                return
            with logger._connect() as db:
                region_key = {"opened": "live_region", "currency": "inventory_region",
                              "ritual": "ritual_region"}.get(mode)
                region = logger._meta(db, region_key, None) if region_key else None
                number = logger._meta(db, "current_map_number", 0)
                remnant_map = logger._map_id(number + int(not number or logger._meta(db, "pending_new_map", False)))
                capture_map = logger._map_id(number) if number else None
                generation = logger._meta(db, "session_generation", 0)
                pending_map = bool(logger._meta(db, "pending_new_map", False))
                expedition = 1 if not number or logger._meta(db, "pending_new_map", False) else logger._meta(db, "settings")["expedition"]
                phase = logger._meta(db, "inventory_scan_phase", "start")
            if self.supported and sys.platform == "win32":
                from PoE2_Data_Logger.platform.hover_copy import _tooltip_bounds
                from PoE2_Data_Logger.ui.region_select import region_for
                bounds = _tooltip_bounds()
                region_key = {"opened": "live_region", "seed": "seed_region", "currency": "inventory_region",
                              "ritual": "ritual_region"}.get(mode)
                if region_key:
                    region = region_for(region_key, bounds)
                elif mode == "both":
                    selected = [region_for(key, bounds) for key in ("live_region", "seed_region")]
                    left = min(r["x"] for r in selected)
                    top = min(r["y"] for r in selected)
                    right = max(r["x"] + r["w"] for r in selected)
                    bottom = max(r["y"] + r["h"] for r in selected)
                    region = {"x": left, "y": top, "w": right - left, "h": bottom - top}
            image = tooltip = None
            if kind in ("default", "remnant"):
                if kind == "default" and self.supported and self.tooltip_grabber is None:
                    from PoE2_Data_Logger.platform.hover_copy import capture_remnant_context
                    image, tooltip = capture_remnant_context(region, self.grabber)
                else:
                    image = self._grab(region)
                self._check_image(image)
                if mode == "opened" and not region:
                    from PoE2_Data_Logger.ocr.runehelper_ocr import default_frame
                    image = default_frame(image)
                if kind == "default" and self.supported:
                    if tooltip is None:
                        from PoE2_Data_Logger.platform.hover_copy import capture_near_cursor
                        tooltip = (self.tooltip_grabber or capture_near_cursor)()
                    self._check_image(tooltip)
            elif mode in ("waystone", "tablet"):
                try:
                    from PoE2_Data_Logger.platform.hover_copy import capture_near_cursor
                    tooltip = (self.tooltip_grabber or capture_near_cursor)()
                    if self.tooltip_grabber is None and sys.platform == "win32":
                        from PoE2_Data_Logger.ui.region_select import region_for
                        selected = region_for(mode + "_region", bounds)
                        tooltip = tooltip.crop((selected["x"] - bounds[0], selected["y"] - bounds[1],
                                                selected["x"] + selected["w"] - bounds[0],
                                                selected["y"] + selected["h"] - bounds[1]))
                    self._check_image(tooltip)
                except Exception as exc:
                    tooltip = exc
            elif mode in ("currency", "ritual"):
                if not region:
                    raise ValueError(f"Select the {mode} capture region in Scan settings first.")
                image = self._grab(region)
                self._check_image(image)
            activity_crops = {}
            if kind == "default" and tooltip is not None:
                from PoE2_Data_Logger.ui.region_select import region_for
                frozen_bounds = bounds if self.supported and sys.platform == "win32" else (
                    0, 0, tooltip.width, tooltip.height)
                for activity, key in (("ritual", "ritual_region"), ("currency", "inventory_region")):
                    try:
                        selected = region_for(key, frozen_bounds)
                        activity_crops[activity] = (selected["x"] - frozen_bounds[0], selected["y"] - frozen_bounds[1],
                                                   selected["x"] + selected["w"] - frozen_bounds[0],
                                                   selected["y"] + selected["h"] - frozen_bounds[1])
                    except ValueError:
                        continue
            copied = None
            if self.hover_reader is not None and self.supported and kind in ("default", "waystone", "tablet"):
                copied = self.hover_reader()
            args = (kind, mode, region, image, tooltip, remnant_map, capture_map, generation, expedition, activity_crops, copied, phase, pending_map, revision)
            if background:
                threading.Thread(target=self._read_capture, args=args,
                                 daemon=True, name="poe2-hotkey-ocr").start()
            else:
                self._read_capture(*args)
        except Exception as exc:
            self._finish_capture({"mode": mode, "result": None,
                                  "error": f"Screen scan failed: {exc}"}, None, revision)

    def _grab(self, region):
        if region:
            from PoE2_Data_Logger.platform.live_watch import validate_region
            region = validate_region(region)
            box = (region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"])
            return self.grabber(bbox=box, all_screens=True)
        return self.grabber(all_screens=False)

    @staticmethod
    def _check_image(image):
        if image.width * image.height > store.MAX_IMAGE_PIXELS:
            raise ValueError("Screen capture exceeds 12 megapixels. Use a smaller game resolution.")

    def _read_capture(self, kind, mode, region, image, tooltip, remnant_map=None, capture_map=None, generation=None, expedition=None, activity_crops=None, copied=None, phase="start", pending_map=False, revision=None):
        event = None
        raw = None
        try:
            if copied:
                with logger._connect() as db:
                    affixes = [row[0] for row in db.execute("SELECT name FROM affixes ORDER BY rowid")]
                item = parse_item_text(copied, affixes)
                readable = bool(item) and (item["kind"] != "tablet" or
                                          bool(item.get("matches") or item.get("uncertain")))
                if readable and (kind == "default" or item["kind"] == kind):
                    event = {"mode": "item", "result": item, "error": ""}
                    if tooltip is not None and not isinstance(tooltip, Exception):
                        memory = io.BytesIO()
                        tooltip.convert("RGB").save(memory, format="JPEG", quality=92)
                        raw = memory.getvalue()
                    return
            native = None
            if (kind == "default" and mode == "opened" and not copied and
                    self.readers.get("opened") is scan_opened):
                from PoE2_Data_Logger.core.auto_commit import candidate
                native = scan_opened(image, allow_fallback=False, verify_header=True)
                if not candidate(native)["ready"]:
                    native = None
            if native and not copied:
                native["_target_map_id"] = remnant_map
                memory = io.BytesIO()
                image.convert("RGB").save(memory, format="JPEG", quality=92)
                raw = memory.getvalue()
                event = {"mode": mode, "result": native, "error": ""}
                return
            if mode in ("waystone", "tablet") or (kind == "default" and self.supported):
                from PoE2_Data_Logger.platform.hover_copy import capture_near_cursor
                from PoE2_Data_Logger.ocr import item_ocr
                from PoE2_Data_Logger.ocr import item_text
                if isinstance(tooltip, Exception):
                    raise tooltip
                tooltip_image = tooltip if tooltip is not None else (self.tooltip_grabber or capture_near_cursor)()
                with logger._connect() as db:
                    affixes = [row[0] for row in db.execute("SELECT name FROM affixes ORDER BY rowid")]
                rows = item_ocr.ocr_lines(tooltip_image)
                if kind == "default" and item_ocr.is_ritual_page(rows):
                    box = (activity_crops or {}).get("ritual")
                    picture = tooltip_image.crop(box) if box else tooltip_image
                    memory = io.BytesIO()
                    picture.convert("RGB").save(memory, format="PNG")
                    raw = memory.getvalue()
                    event = {"mode": "ritual", "result": {"captured": True, "map_id": capture_map}, "error": ""}
                    return
                result = item_text.read_screen_tooltip(tooltip_image, affixes, ocr_rows=rows)
                if result and (kind == "default" or result["kind"] == mode):
                    memory = io.BytesIO()
                    tooltip_image.convert("RGB").save(memory, format="JPEG", quality=92)
                    raw = memory.getvalue()
                    event = {"mode": "item", "result": result, "error": ""}
                    return
                if (kind == "default" and mode == "opened" and not copied and
                        self.readers.get("opened") is scan_opened and
                        any("runeshapecombinations" in "".join(c for c in row["text"].lower()
                            if c.isalnum()) for row in rows)):
                    result = scan_opened(tooltip_image, ocr_rows=rows)
                    result["_target_map_id"] = remnant_map
                    memory = io.BytesIO()
                    tooltip_image.convert("RGB").save(memory, format="JPEG", quality=92)
                    raw = memory.getvalue()
                    event = {"mode": mode, "result": result, "error": ""}
                    return
                if mode in ("waystone", "tablet"):
                    raise ValueError(f"No {mode} tooltip found near the cursor.")
                if copied:
                    raise ValueError("The hovered item is not a waystone or tablet.")
                box = (activity_crops or {}).get("currency")
                if kind == "default" and box:
                    inventory = item_ocr.inventory_grid(tooltip_image.crop(box))
                    if inventory.info.get("poe2_inventory_aligned"):
                        memory = io.BytesIO()
                        inventory.convert("RGB").save(memory, format="PNG")
                        raw = memory.getvalue()
                        event = {"mode": "currency", "result": {"captured": True, "map_id": capture_map}, "error": ""}
                        return
            if mode in ("currency", "ritual") and not region:
                raise ValueError(f"Select the {mode} capture region in Scan settings first.")
            if image is None:
                image = self._grab(region)
            self._check_image(image)
            memory = io.BytesIO()
            if mode in ("currency", "ritual"):
                image.convert("RGB").save(memory, format="PNG")
            else:
                image.convert("RGB").save(memory, format="JPEG", quality=92)
            raw = memory.getvalue()
            if mode in ("currency", "ritual"):
                event = {"mode": mode, "result": {"captured": True, "map_id": capture_map}, "error": ""}
                return
            if mode == "opened" and self.readers.get(mode) is scan_opened:
                result = scan_opened(image)
            else:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
                    temp.write(raw)
                    name = Path(temp.name)
                try:
                    result = self.readers[mode](name)
                finally:
                    name.unlink(missing_ok=True)
            if mode == "both":
                mode = result["mode"]
            result["mode"] = mode
            if mode != "seed" or result.get("remnants") or result.get("sockets"):
                result["_target_map_id"] = remnant_map
            event = {"mode": mode, "result": result, "error": ""}
        except Exception as exc:
            raw = None
            event = {"mode": mode, "result": None, "error": f"Screen scan failed: {exc}"}
        finally:
            if event and event.get("result") is not None and generation is not None:
                event["result"].update({"_scan_generation": generation, "_capture_map_id": capture_map,
                                        "_capture_expedition": expedition, "_capture_map_pending": pending_map})
                if event["mode"] == "currency":
                    event["result"]["phase"] = phase
                    event["result"]["map_id"] = remnant_map if phase == "start" else capture_map
            self._finish_capture(event, raw, revision)

    def cancel_capture(self):
        with self._lock:
            self._capture_revision += 1
            self._latest = self._image = None

    def _finish_capture(self, event, raw, revision=None):
        with self._lock:
            if revision is not None and revision != self._capture_revision:
                event = None
            if event is not None:
                self._sequence += 1
                event["id"] = self._sequence
                self._latest = event
                self._image = raw
        self._capture_lock.release()
        if event is not None:
            self._notify_event()

    def image(self, event_id):
        with self._lock:
            if self._latest and self._latest["id"] == event_id:
                return self._image
        return None
