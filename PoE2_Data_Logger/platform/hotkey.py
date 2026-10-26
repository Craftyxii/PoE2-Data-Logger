"""Manage Windows shortcuts and serialize context-bound scans without blocking HUD key events."""

from __future__ import annotations

import base64
import io
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from PIL import ImageGrab

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.core import ocr_sensitivity, ocr_workers
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
CAPTURE_TIMEOUT_SECONDS = 90
KILL_COUNT_TIMEOUT_SECONDS = 5
DEDICATED = ("remnant", "waystone", "tablet", "currency", "ritual", "propagation", "overlay", "kills")
GENERAL_MODES = ("seed", "opened", "both")
# One Ritual page being taken plus two waiting; a further press is refused with this message.
RITUAL_HOLD_LIMIT = 3
RITUAL_LIMIT_MESSAGE = "Two Ritual pages are already waiting. Approve or reject the current page, then scan again."
DISCARDED_MESSAGE = "Nothing to scan in that capture — discarded."
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
    """Map a Windows key code to a supported label, retaining unknown keys as hexadecimal VK
    names.
    """
    if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A:
        return chr(code)
    if 0x70 <= code <= 0x87:
        return f"F{code - 0x70 + 1}"
    return next((KEY_LABELS[name] for name, number in KEY_CODES.items() if number == code),
                f"VK_{code:02X}")


def parse_combo(value):
    """Normalize one key plus distinct modifiers into Windows registration values, reserving
    Ctrl+C for item copy.
    """
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
    """Own shortcut registration, latest results and one Remnant/Propagation pair with
    cancellation revisions and bounded physical capture adapters.
    """
    def __init__(self, grabber=None, readers=None, supported=None, hover_reader=None,
                 tooltip_grabber=None, focused=None, *, ordered_delivery=True):
        """Initialize injectable capture/read/focus adapters, synchronization locks and
        independent scan/HUD event counters.
        """
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
        # Cancellation retires the logical scan immediately, but an external
        # adapter cannot be killed safely. Keep one physical owner until it
        # returns so repeated retries cannot create abandoned capture threads.
        self._capture_adapter_owner = None
        self._capture_revision = 0
        self._capture_owner = None
        self._capture_watchdog = None
        self._capture_deadline = None
        self._ocr_capture_future = None
        self._ocr_capture_revision = None
        self._active_capture_mode = None
        self._active_capture_kind = None
        self._active_strictness_values = None
        self._remnant_capture = None
        self._deferred_propagation = None
        self._active_pair_capture = False
        # Only an accepted Remnant -> Propagation pair retains two results.
        # All ordinary scans retain the previous latest-result replacement rule.
        self._events = {}
        # Ritual pages are never replaced: each stays until the window takes it. A Ritual or
        # general key pressed while another capture runs takes its screenshot at once, and
        # the held screenshots are read in press order once the capture slot is free.
        self._ritual_events = {}
        self._held = []
        self._ritual_only = None
        self._ordered_delivery = ordered_delivery
        self._acknowledged_event = 0
        self._latest = None
        self._image = None
        self._sequence = 0
        self._overlay_sequence = 0
        self.before_capture = None
        self.on_event = None

    def _notify_event(self):
        """Notify the optional UI bridge without letting callback errors escape the
        capture/listener path.
        """
        callback = self.on_event
        if callback:
            try:
                callback()
            except Exception:
                pass

    def start(self):
        """Load saved shortcut preferences and register supported combinations, retaining a
        registration failure as status.
        """
        with store._connect() as db:
            settings = logger._meta(db, "ocr_shortcut", {"combo": "", "mode": "opened"})
        configured_mode = settings.get("mode")
        self.mode = (configured_mode if configured_mode in GENERAL_MODES and configured_mode in self.readers
                     else "opened")
        self.combo = settings.get("combo", "")
        self.combos = {name: str((settings.get("combos") or {}).get(name, ""))
                       for name in DEDICATED}
        if self.supported and (self.combo or any(self.combos.values())):
            try:
                self._replace(self.combo, self.combos, persist=False)
            except ValueError as exc:
                self.error = str(exc)

    def status(self):
        """Return a lock-protected snapshot of registration, current mode, errors and latest
        scan/HUD events.
        """
        with self._lock:
            return {"supported": self.supported, "combo": self.combo,
                    "combos": dict(self.combos),
                    "registered": bool(self._thread and self._thread.is_alive()),
                    "mode": self.mode, "error": self.error, "latest": self._latest,
                    "overlay_sequence": self._overlay_sequence,
                    "held_ritual": len(self._held) + sum(ident > self._acknowledged_event
                                                         for ident in self._ritual_events)}

    def set_mode(self, mode):
        """Validate and persist the general remnant scan mode without replacing dedicated
        shortcut assignments.
        """
        if mode not in GENERAL_MODES or mode not in self.readers:
            raise ValueError("Choose visible seed or opened remnant mode.")
        with self._lock:
            self.mode = mode
            self._persist()
            return self.status()

    def configure(self, combo, persist=True):
        """Normalize the general shortcut and serialize registration replacement against other
        configuration changes.
        """
        with self._configuration_lock:
            canonical, _, _ = parse_combo(combo)
            return self._replace(canonical, self.combos, persist)

    def configure_for(self, kind, combo, persist=True):
        """Validate and replace one dedicated scan/HUD shortcut while retaining the others."""
        if kind not in DEDICATED:
            raise ValueError("Choose a supported scan type.")
        with self._configuration_lock:
            canonical, _, _ = parse_combo(combo)
            combos = {**self.combos, kind: canonical}
            return self._replace(self.combo, combos, persist)

    def _replace(self, combo, combos, persist=True):
        """Reject conflicting shortcuts, replace listener registrations and attempt restoration
        of old registrations after failure.
        """
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
        """Store shortcut names, scan mode and dedicated combinations in local database
        metadata.
        """
        with store._connect() as db:
            logger._set_meta(db, "ocr_shortcut", {"combo": self.combo, "mode": self.mode,
                                                  "combos": self.combos})

    def _register(self, shortcuts):
        """Start the Windows listener and wait for a bounded registration handshake before
        exposing it as ready.
        """
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
        """Signal the listener to stop, post WM_QUIT when possible and reject replacement if
        its thread fails to exit.
        """
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
        """Report listener setup/runtime errors and release the readiness waiter even on
        failure.
        """
        try:
            self._listen(shortcuts, ready, outcome, stop)
        except Exception as error:
            outcome["error"] = f"Could not run the shortcut listener: {error}"
            with self._lock:
                self.error = outcome["error"]
        finally:
            ready.set()

    def _listen(self, shortcuts, ready, outcome, stop):
        """Own Win32 key registrations and focus polling; dispatch scans to background capture
        while keeping HUD shortcuts available.
        """
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
            while not stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise ValueError("Could not read shortcut messages from Windows.")
                if not result or stop.is_set():
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

    def capture(self, kind="default", background=False, *, _cached_capture=None, _held=None):
        """Reserve a bounded scan or report a retired adapter that has not yet returned.

        A Ritual or general key pressed while the slot is taken grabs its screenshot now
        and waits in press order instead of being dropped.
        """
        if kind == "overlay":
            with logger._connect() as db:
                enabled = bool(logger._meta(db, "hud_overlay", False))
            if enabled:
                with self._lock:
                    self._overlay_sequence += 1
                self._notify_event()
            return
        if _cached_capture is None and _held is None and self.focused is not None and not self.focused():
            return
        with self._lock:
            mode = self.mode if kind in ("default", "remnant") else kind
            if kind in ("default", "remnant") and mode not in GENERAL_MODES:
                mode = "opened"
            if _held is not None:
                # A held screenshot becomes a Ritual page or is discarded; it is never a remnant.
                mode = "ritual"
                if not self._capture_lock.acquire(blocking=False):
                    self._held.insert(0, _held)
                    return
            elif (_cached_capture is None and kind in ("ritual", "default") and
                    (self._held or self._events or self._capture_lock.locked() or
                     self._capture_adapter_owner is not None)):
                self._hold(kind)
                return
            if _cached_capture is None and _held is None and self._events:
                # The accepted pair must finish delivery before another key
                # can replace either result or reserve another request.
                return
            if _held is None and not self._capture_lock.acquire(blocking=False):
                if (kind == "propagation" and self._active_capture_kind in ("default", "remnant")
                        and self._active_capture_mode in GENERAL_MODES):
                    # One subsequent propagation request belongs to this exact
                    # remnant image, including a key pressed before the grab returns.
                    if self._deferred_propagation is None:
                        try:
                            context = logger.scan_context()
                        except Exception as exc:
                            self._deferred_error(f"Screen scan failed: {exc}")
                            self._notify_event()
                            return
                        pending = {"revision": self._capture_owner, "context": context,
                                   "strictness_values": self._active_strictness_values,
                                   "deadline": time.monotonic() + CAPTURE_TIMEOUT_SECONDS}
                        timer = threading.Timer(CAPTURE_TIMEOUT_SECONDS,
                                                self._expire_deferred_propagation, args=(pending,))
                        timer.daemon = True
                        pending["timer"] = timer
                        self._deferred_propagation = pending
                        try:
                            timer.start()
                        except Exception as exc:
                            self._deferred_propagation = None
                            self._deferred_error(f"Screen scan failed: {exc}")
                            self._notify_event()
                return
            if (kind == "propagation" and _cached_capture is None and self._remnant_capture is not None
                    and self._capture_adapter_owner is None):
                captured, self._remnant_capture = self._remnant_capture, None
                try:
                    logger.validate_scan_context(captured["context"])
                except ValueError:
                    # A stale completed image never changes the destination of
                    # a fresh independent propagation keypress.
                    pass
                except Exception as exc:
                    self._capture_lock.release()
                    self._publish_event({"mode": mode, "result": None,
                                         "error": f"Screen scan failed: {exc}"}, None)
                    self._notify_event()
                    return
                else:
                    captured["_paired"] = True
                    if (self._ordered_delivery and self._latest is not None and
                            self._latest["id"] == captured.get("event_id") and
                            self._latest["id"] > self._acknowledged_event):
                        self._events[self._latest["id"]] = (self._latest, self._image)
                    _cached_capture = captured
            if self._capture_adapter_owner is not None:
                self._capture_lock.release()
                self._publish_event({"mode": mode, "result": None,
                                "error": "The previous screen capture adapter is still busy. "
                                         "Wait for it to finish, then scan again."}, None)
                busy = True
            else:
                busy = False
            if not busy:
                self._capture_revision += 1
                revision = self._capture_revision
                self._capture_owner = revision
                self._capture_adapter_owner = revision
                self._active_capture_mode = mode
                self._active_capture_kind = kind
                self._active_strictness_values = None
                self._active_pair_capture = bool(_cached_capture and _cached_capture.get("_paired"))
                if kind != "propagation":
                    self._remnant_capture = None
                self._capture_deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
        if busy:
            self._notify_event()
            return
        try:
            with self._lock:
                if self._capture_owner != revision:
                    self._release_capture_adapter(revision)
                    return
                watchdog = threading.Timer(CAPTURE_TIMEOUT_SECONDS, self._expire_capture, args=(mode, revision))
                watchdog.daemon = True
                self._capture_watchdog = watchdog
                watchdog.start()
            try:
                strictness_values = (dict(_cached_capture["strictness_values"]) if _cached_capture
                                     else dict(_held["strictness_values"]) if _held is not None
                                     else dict(ocr_sensitivity.saved_values()))
                with self._lock:
                    if revision == self._capture_revision:
                        self._active_strictness_values = dict(strictness_values)
                    if self._deferred_propagation and self._deferred_propagation["revision"] == revision:
                        self._deferred_propagation["strictness_values"] = dict(strictness_values)
            finally:
                self._release_capture_adapter(revision)
            if self._capture_cancelled(revision):
                return
            if background:
                # Reserve the settings as well as the capture slot before
                # scheduling, so changes cannot alter an already queued scan.
                arguments = (kind, mode, revision, strictness_values)
                if _cached_capture is not None:
                    arguments += (_cached_capture,)
                threading.Thread(target=self._capture, args=arguments,
                                 kwargs={"held": _held} if _held is not None else {},
                                 daemon=True, name="poe2-hotkey-capture").start()
            else:
                if _held is not None:
                    self._capture(kind, mode, revision, strictness_values, held=_held)
                elif _cached_capture is None:
                    self._capture(kind, mode, revision, strictness_values)
                else:
                    self._capture(kind, mode, revision, strictness_values, _cached_capture)
        except Exception as exc:
            self._release_capture_adapter(revision)
            self._finish_capture({"mode": mode, "result": None,
                                  "error": f"Screen scan failed: {exc}"}, None, revision)

    def _capture_cancelled(self, revision):
        """Check request ownership between adapters that may block during cancellation."""
        with self._lock:
            return revision != self._capture_revision

    def _capture(self, kind, mode, revision, strictness_values=None, cached_capture=None, *, held=None):
        """Bound physical capture work independently of its cancellable logical reservation."""
        with self._lock:
            if revision != self._capture_owner or revision != self._capture_revision:
                return
            self._capture_adapter_owner = revision
        try:
            if held is not None:
                self._read_held(revision, held)
            elif cached_capture is None:
                self._perform_capture(kind, mode, revision, strictness_values)
            else:
                self._read_cached_propagation(revision, cached_capture)
        finally:
            self._release_capture_adapter(revision)

    def _release_capture_adapter(self, revision):
        """Retire only the adapter that actually returned, preserving another request's owner."""
        with self._lock:
            if self._capture_adapter_owner == revision:
                self._capture_adapter_owner = None
            self._run_deferred_propagation()
            self._run_held_captures()

    def _hold(self, kind):
        """Take a press's screenshot now while another capture runs; it waits in press order.

        One page being taken plus two waiting is the limit; a further press is refused
        with a message. Call with the lock held.
        """
        active = int(self._capture_lock.locked() and self._active_capture_kind in ("ritual", "default"))
        if len(self._held) + len(self._ritual_events) + active >= RITUAL_HOLD_LIMIT:
            self._publish_event({"mode": "ritual", "result": None, "error": RITUAL_LIMIT_MESSAGE}, None)
            self._notify_event()
            return
        entry = {"kind": kind, "ready": False}
        self._held.append(entry)
        threading.Thread(target=self._grab_held, args=(entry,), daemon=True,
                         name="poe2-hotkey-held-capture").start()
        self._notify_event()

    def _grab_held(self, entry):
        """Freeze one held press like a normal capture: hide the HUD, require the game in
        front, then grab and keep the map, session and strictness of that moment.
        """
        try:
            if self.before_capture:
                self.before_capture()
            if self.focused is not None and not self.focused():
                entry["error"] = "The game lost focus before capture. Scan again."
            else:
                entry.update(self._held_snapshot(entry["kind"]))
        except Exception as exc:
            entry["error"] = f"Screen scan failed: {exc}"
        finally:
            with self._lock:
                entry["ready"] = True
                self._run_held_captures()

    def _held_snapshot(self, kind):
        """Grab a held press's pixels with the capture context a normal capture would freeze."""
        with logger._connect() as db:
            number = logger._meta(db, "current_map_number", 0)
            pending_map = bool(logger._meta(db, "pending_new_map", False))
            capture_map = logger._map_id(number) if number else None
            expedition = 1 if not number or pending_map else logger._meta(db, "settings")["expedition"]
            context = {"_scan_generation": logger._meta(db, "session_generation", 0),
                       "_capture_map_id": capture_map, "_capture_expedition": expedition,
                       "_capture_map_pending": pending_map,
                       "_capture_chain_revision": (logger._chain_revision(db, logger._exp_id(capture_map, expedition))
                                                   if capture_map else 0)}
            region = logger._meta(db, "ritual_region", None)
        snapshot = {"context": context, "strictness_values": dict(ocr_sensitivity.saved_values())}
        bounds = None
        if self.supported and sys.platform == "win32":
            from PoE2_Data_Logger.platform.hover_copy import _tooltip_bounds
            from PoE2_Data_Logger.ui.region_select import region_for, validate_capture_resolution
            bounds = _tooltip_bounds()
            validate_capture_resolution(bounds)
            if kind == "ritual":
                region = region_for("ritual_region", bounds)
        if kind == "ritual":
            if not region:
                raise ValueError("Select the ritual capture region in Scan settings first.")
            image = self._grab(region)
            self._check_image(image)
            snapshot["image"] = image.convert("RGB").copy()
            return snapshot
        # The general key recognises a Ritual window from the whole game window, as it does
        # without a held press; anything else it shows is discarded.
        if not self.supported:
            return snapshot
        if self.tooltip_grabber is not None:
            tooltip = self.tooltip_grabber()
        else:
            tooltip = self.grabber(bbox=bounds, all_screens=True)
        self._check_image(tooltip)
        tooltip = tooltip.convert("RGB").copy()
        snapshot["tooltip"] = tooltip
        from PoE2_Data_Logger.ui.region_select import region_for
        frozen = bounds if bounds is not None else (0, 0, tooltip.width, tooltip.height)
        try:
            selected = region_for("ritual_region", frozen)
            snapshot["crop"] = (selected["x"] - frozen[0], selected["y"] - frozen[1],
                                selected["x"] + selected["w"] - frozen[0], selected["y"] + selected["h"] - frozen[1])
        except ValueError:
            pass
        return snapshot

    def _run_held_captures(self):
        """Start the oldest held press once its screenshot is taken and the capture slot is free.

        Call with the lock held. A held press whose grab failed reports its error.
        """
        while (self._held and self._held[0]["ready"] and self._capture_owner is None and
               self._capture_adapter_owner is None and not self._capture_lock.locked() and
               self._deferred_propagation is None):
            entry = self._held.pop(0)
            if entry.get("error"):
                self._publish_event({"mode": "ritual" if entry["kind"] == "ritual" else self.mode,
                                     "result": None, "error": entry["error"]}, None)
                self._notify_event()
                continue
            self.capture(entry["kind"], background=True, _held=entry)
            return

    def _read_held(self, revision, entry):
        """Turn a held screenshot into a Ritual page for the window, or discard it with a note.

        A general-key screenshot counts only when it shows the Ritual window, by the same
        rule the general key uses without a held press.
        """
        event = raw = None
        try:
            picture = entry.get("image")
            if picture is None and entry.get("tooltip") is not None:
                from PoE2_Data_Logger.ocr import item_ocr
                rows = self._ocr_capture_call(revision, item_ocr.ocr_lines, entry["tooltip"])
                if item_ocr.is_ritual_page(rows):
                    box = entry.get("crop")
                    picture = entry["tooltip"].crop(box) if box else entry["tooltip"]
            if picture is None:
                event = {"mode": "discarded", "result": None, "error": "", "message": DISCARDED_MESSAGE}
            else:
                memory = io.BytesIO()
                picture.convert("RGB").save(memory, format="PNG")
                raw = memory.getvalue()
                context = entry["context"]
                event = {"mode": "ritual", "error": "",
                         "result": {"captured": True, "map_id": context["_capture_map_id"], "_held": True,
                                    **context, "_ocr_strictness_values": dict(entry["strictness_values"])}}
        except Exception as exc:
            event, raw = {"mode": "ritual", "result": None, "error": f"Screen scan failed: {exc}"}, None
        self._finish_capture(event, raw, revision, adapter_complete=True)

    def _expire_deferred_propagation(self, pending):
        """Bound a retained keypress even when a cancelled physical adapter never returns."""
        with self._lock:
            if self._deferred_propagation is not pending:
                return
            pending["timer"].cancel()
            self._deferred_propagation = self._remnant_capture = None
            self._publish_event({"mode": "propagation", "result": None,
                                 "error": "Queued propagation scan timed out. Scan again."}, None, paired=True)
            self._run_held_captures()
        self._notify_event()

    def _run_deferred_propagation(self):
        """Transfer one retained request only after its physical predecessor has returned."""
        pending = self._deferred_propagation
        if (pending is None or self._capture_owner is not None or
                self._capture_adapter_owner is not None or self._capture_lock.locked()):
            return
        if time.monotonic() >= pending["deadline"]:
            self._expire_deferred_propagation(pending)
            return
        captured = self._remnant_capture
        self._deferred_propagation = self._remnant_capture = None
        pending["timer"].cancel()
        if captured is None or captured["revision"] != pending["revision"]:
            # Cancellation before the screenshot returned cannot reuse those
            # late pixels. Take a standalone screenshot after the adapter exits,
            # retaining the queued request's original destination and settings.
            captured = {"image": None, "context": pending["context"],
                        "strictness_values": dict(pending["strictness_values"] or {})}
        captured["_paired"] = True
        self.capture("propagation", background=True, _cached_capture=captured)

    def _read_cached_propagation(self, revision, captured):
        """Read a queued propagation request from the independent lossless remnant image."""
        try:
            context = captured["context"]
            logger.validate_scan_context(context)
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision, adapter_complete=True)
                return
            if captured["image"] is None:
                self._perform_capture("propagation", "propagation", revision,
                                      captured["strictness_values"], frozen_context=context)
                return
            self._read_capture("propagation", "propagation", captured["region"],
                               captured["image"], None, **captured["arguments"],
                               revision=revision, strictness_values=captured["strictness_values"])
        except Exception as exc:
            self._finish_capture({"mode": "propagation", "result": None,
                                  "error": f"Screen scan failed: {exc}"}, None, revision,
                                 adapter_complete=True)

    def _perform_capture(self, kind, mode, revision, strictness_values=None, frozen_context=None):
        """Stop canceled requests between GUI preparation, capture, clipboard and recognition."""
        kill_capture = None
        try:
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            if self.before_capture:
                self.before_capture()
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            if self.focused is not None and not self.focused():
                self._finish_capture({"mode": mode, "result": None,
                                      "error": "The game lost focus before capture. Scan again."},
                                     None, revision, adapter_complete=True)
                return
            with logger._connect() as db:
                region_key = {"opened": "live_region", "propagation": "propagation_region",
                              "currency": "inventory_region",
                              "ritual": "ritual_region"}.get(mode)
                region = logger._meta(db, region_key, None) if region_key else None
                number = logger._meta(db, "current_map_number", 0)
                remnant_map = logger._map_id(number + int(not number or logger._meta(db, "pending_new_map", False)))
                capture_map = logger._map_id(number) if number else None
                generation = logger._meta(db, "session_generation", 0)
                pending_map = bool(logger._meta(db, "pending_new_map", False))
                expedition = 1 if not number or logger._meta(db, "pending_new_map", False) else logger._meta(db, "settings")["expedition"]
                chain_revision = logger._chain_revision(db, logger._exp_id(capture_map, expedition)) if capture_map else 0
                phase = logger._meta(db, "inventory_scan_phase", "start")
                kill_enabled = logger._meta(db, "kill_count_scan_enabled", True) is True
                kill_region = logger._meta(db, "kill_count_region", None) if kill_enabled else None
            if frozen_context is not None:
                logger.validate_scan_context(frozen_context)
                capture_map = frozen_context["_capture_map_id"]
                generation = frozen_context["_scan_generation"]
                expedition = frozen_context["_capture_expedition"]
                pending_map = frozen_context["_capture_map_pending"]
                chain_revision = frozen_context["_capture_chain_revision"]
            bounds = None
            if self.supported and sys.platform == "win32":
                from PoE2_Data_Logger.platform.hover_copy import _tooltip_bounds
                from PoE2_Data_Logger.ui.region_select import region_for, validate_capture_resolution
                bounds = _tooltip_bounds()
                # Auto/default scans also need the full-window check before grabbing.
                validate_capture_resolution(bounds)
                region_key = {"opened": "live_region", "propagation": "propagation_region",
                              "seed": "seed_region", "currency": "inventory_region",
                              "ritual": "ritual_region"}.get(mode)
                if region_key:
                    region = region_for(region_key, bounds)
                    if mode == "propagation":
                        # The recipe selector sits outside the panel's left
                        # frame. Retain that game context even if the saved
                        # region starts at the frame or first rune tile.
                        right = region["x"] + region["w"]
                        region = {**region, "x": bounds[0], "w": right - bounds[0]}
                elif mode == "both":
                    selected = [region_for(key, bounds) for key in ("live_region", "seed_region")]
                    left = min(r["x"] for r in selected)
                    top = min(r["y"] for r in selected)
                    right = max(r["x"] + r["w"] for r in selected)
                    bottom = max(r["y"] + r["h"] for r in selected)
                    region = {"x": left, "y": top, "w": right - left, "h": bottom - top}
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            if kind == "kills":
                kill_capture = self._capture_kills(kind, revision, kill_enabled, kill_region, bounds)
            if mode == "kills" and kill_capture is None:
                raise ValueError("Select and enable the Kill counts region in Scan regions first.")
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            image = tooltip = None
            if kind in ("default", "remnant"):
                if kind == "default" and self.supported and self.tooltip_grabber is None:
                    from PoE2_Data_Logger.platform.hover_copy import capture_remnant_context
                    image, tooltip = capture_remnant_context(region, self.grabber)
                else:
                    image = self._grab(region)
                self._check_image(image)
                with self._lock:
                    if revision == self._capture_revision:
                        self._remnant_capture = {
                            "revision": revision, "image": image.convert("RGB").copy(), "region": region,
                            "strictness_values": dict(strictness_values or {}),
                            "context": {"_scan_generation": generation, "_capture_map_id": capture_map,
                                        "_capture_expedition": expedition, "_capture_map_pending": pending_map},
                            "arguments": {"remnant_map": remnant_map, "capture_map": capture_map,
                                          "generation": generation, "expedition": expedition,
                                          "pending_map": pending_map, "chain_revision": chain_revision}}
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
            elif mode == "propagation":
                image = self._grab(region)
                self._check_image(image)
            elif mode in ("currency", "ritual"):
                if not region:
                    raise ValueError(f"Select the {mode} capture region in Scan settings first.")
                image = self._grab(region)
                self._check_image(image)
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            activity_crops = {}
            if kind == "default" and tooltip is not None:
                from PoE2_Data_Logger.ui.region_select import region_for
                frozen_bounds = bounds if self.supported and sys.platform == "win32" else (
                    0, 0, tooltip.width, tooltip.height)
                for activity, key in (("ritual", "ritual_region"),):
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
            if self._capture_cancelled(revision):
                self._finish_capture(None, None, revision)
                return
            args = (kind, mode, region, image, tooltip, remnant_map, capture_map, generation, expedition, activity_crops, copied, phase, pending_map, revision, strictness_values, chain_revision)
            if kill_capture is None:
                self._read_capture(*args)
            else:
                self._read_capture(*args, kill_capture=kill_capture)
        except Exception as exc:
            event = {"mode": mode, "result": None, "error": f"Screen scan failed: {exc}"}
            if kill_capture is not None and not self._capture_cancelled(revision):
                level = (strictness_values or {}).get("kills", ocr_sensitivity.DEFAULT)
                event["kill_counts"] = self._read_kills(kill_capture, revision, level)
            self._finish_capture(event, None, revision, adapter_complete=True)

    def _capture_kills(self, kind, revision, enabled, region, bounds):
        """Freeze only the dedicated kill-key crop and its independent map context."""
        if kind != "kills" or not enabled:
            return None
        payload = {"request_id": str(uuid.uuid4()), "source_mode": kind,
                   "scope": "map_final" if kind == "kills" else "map_stage"}
        try:
            from PoE2_Data_Logger.core.kill_counts import capture_context
            payload["context"] = capture_context(source_mode=kind)
            if bounds is not None:
                from PoE2_Data_Logger.ui.region_select import region_for
                region = region_for("kill_count_region", bounds)
            if not region:
                raise ValueError("Select the Kill counts capture region in Scan regions first.")
            if self._capture_cancelled(revision):
                return payload
            image = self._grab_kill_region(region, bounds)
            self._check_image(image)
            # Keep both recognition input and evidence independent of any
            # adapter reusing its buffer or primary reader mutating an image.
            image = image.convert("RGB").copy()
            memory = io.BytesIO()
            image.save(memory, format="PNG", compress_level=1)
            payload["image_base64"] = base64.b64encode(memory.getvalue()).decode("ascii")
            payload["_image"] = image
        except Exception as exc:
            payload["error"] = f"Kill count capture failed: {exc}"
        return payload

    def _read_kills(self, payload, revision, strictness):
        """Run the dedicated reader within a short remaining capture budget."""
        result = {key: value for key, value in payload.items() if key != "_image"}
        if payload.get("error") or "_image" not in payload:
            return result
        try:
            from PoE2_Data_Logger.ocr.kill_counts import scan as scan_kills
            reader = self.readers.get("kills")
            if reader is None:
                reader = scan_kills
            options = {"is_crop": True} if reader is scan_kills else {}
            result["result"] = self._ocr_capture_call(
                revision, reader, payload["_image"], strictness=strictness,
                _timeout=KILL_COUNT_TIMEOUT_SECONDS, **options)
        except Exception as exc:
            result["error"] = f"Kill count scan failed: {exc}"
        return result

    def _grab_kill_region(self, region, bounds=None):
        """Capture a small native counter ROI without relaxing primary scan limits.

        Counter calibration scales with the game window, so a valid 720p crop
        can be smaller than the legacy 200×100 primary-capture minimum.
        """
        keys = ("x", "y", "w", "h")
        if not isinstance(region, dict) or any(type(region.get(key)) is not int for key in keys):
            raise ValueError("Select a valid Kill counts screen region with whole-pixel coordinates.")
        x, y, width, height = (region[key] for key in keys)
        if not (20 <= width <= 8192 and 20 <= height <= 8192) or width * height > store.MAX_IMAGE_PIXELS:
            raise ValueError("The Kill counts region must be at least 20×20 and at most 12 megapixels.")
        box = (x, y, x + width, y + height)
        if any(abs(value) > 32768 for value in box):
            raise ValueError("The Kill counts screen region is out of range.")
        if bounds is not None:
            if (not isinstance(bounds, (tuple, list)) or len(bounds) != 4 or
                    any(type(value) is not int for value in bounds) or
                    bounds[0] >= bounds[2] or bounds[1] >= bounds[3] or
                    x < bounds[0] or y < bounds[1] or
                    box[2] > bounds[2] or box[3] > bounds[3]):
                raise ValueError("Keep the Kill counts region inside the game window.")
        return self.grabber(bbox=box, all_screens=True)

    def _grab(self, region):
        """Validate an explicit screen rectangle before capture, or grab the primary screen
        when no region is supplied.
        """
        if region:
            from PoE2_Data_Logger.platform.live_watch import validate_region
            region = validate_region(region)
            box = (region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"])
            return self.grabber(bbox=box, all_screens=True)
        return self.grabber(all_screens=False)

    @staticmethod
    def _check_image(image):
        """Reject captured images exceeding the application pixel limit before recognition."""
        if image.width * image.height > store.MAX_IMAGE_PIXELS:
            raise ValueError("Screen capture exceeds 12 megapixels. Use a smaller game resolution.")

    def _read_capture(self, kind, mode, region, image, tooltip, remnant_map=None, capture_map=None, generation=None, expedition=None, activity_crops=None, copied=None, phase="start", pending_map=False, revision=None, strictness_values=None, chain_revision=None, kill_capture=None):
        """Route captured pixels or copied item text to the appropriate reader, preserve
        ownership and publish through the revision guard. Built-in recognition runs
        in bounded killable children with frozen strictness; injected readers retain their API.
        """
        event = None
        raw = None
        try:
            values = ocr_sensitivity.saved_values() if strictness_values is None else strictness_values
            strictness_values = {key: ocr_sensitivity.validate(level) for key, level in values.items()}
            remnant_strictness = strictness_values.get("remnant", ocr_sensitivity.DEFAULT)
            remnant_options = {} if remnant_strictness == ocr_sensitivity.DEFAULT else {"strictness": remnant_strictness}
            if mode == "kills":
                event = {"mode": "kills", "result": None, "error": ""}
                return
            if kind == "propagation":
                from PoE2_Data_Logger.ocr.propagation_scan import scan_propagation
                reader = self.readers.get("propagation")
                if reader is None:
                    reader = scan_propagation
                memory = io.BytesIO()
                image.convert("RGB").save(memory, format="PNG", compress_level=1)
                raw = memory.getvalue()
                level = strictness_values.get("propagation", ocr_sensitivity.DEFAULT)
                options = {"strictness": level} if reader is scan_propagation and level != ocr_sensitivity.DEFAULT else {}
                result = self._ocr_capture_call(revision, reader, image, **options)
                result["mode"] = "propagation"
                event = {"mode": "propagation", "result": result, "error": ""}
                return
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
                native = self._ocr_capture_call(revision, scan_opened, image, allow_fallback=False,
                                         verify_header=True, **remnant_options)
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
                rows = self._ocr_capture_call(revision, item_ocr.ocr_lines, tooltip_image)
                if kind == "default" and item_ocr.is_ritual_page(rows):
                    box = (activity_crops or {}).get("ritual")
                    picture = tooltip_image.crop(box) if box else tooltip_image
                    memory = io.BytesIO()
                    picture.convert("RGB").save(memory, format="PNG")
                    raw = memory.getvalue()
                    event = {"mode": "ritual", "result": {"captured": True, "map_id": capture_map}, "error": ""}
                    return
                # A hovered atlas map lists its Rite modifiers; its "Requires:
                # Waystone" line must not be read as a waystone tooltip.
                rite = item_text.read_rite_tooltip(rows, tooltip_image) if kind == "default" else None
                if rite is not None:
                    event = {"mode": "rite", "result": rite, "error": ""}
                    return
                item_options = {}
                if mode in ("waystone", "tablet"):
                    level = strictness_values.get(mode, ocr_sensitivity.DEFAULT)
                    if level != ocr_sensitivity.DEFAULT:
                        item_options["strictness"] = level
                elif any(strictness_values.get(key, ocr_sensitivity.DEFAULT) != ocr_sensitivity.DEFAULT
                         for key in ("waystone", "tablet")):
                    item_options["strictness"] = dict(strictness_values)
                result = self._ocr_capture_call(revision, item_text.read_screen_tooltip, tooltip_image, affixes,
                                         ocr_rows=rows, **item_options)
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
                    result = self._ocr_capture_call(revision, scan_opened, tooltip_image, ocr_rows=rows, **remnant_options)
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
                result = self._ocr_capture_call(revision, scan_opened, image, **remnant_options)
            else:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
                    temp.write(raw)
                    name = Path(temp.name)
                try:
                    reader = self.readers[mode]
                    options = {}
                    seed_strictness = strictness_values.get("seed", ocr_sensitivity.DEFAULT)
                    if reader is scan and seed_strictness != ocr_sensitivity.DEFAULT:
                        options["strictness"] = seed_strictness
                    elif reader is scan_both:
                        options.update(remnant_options)
                        if seed_strictness != remnant_strictness:
                            options["seed_strictness"] = seed_strictness
                    result = self._ocr_capture_call(revision, reader, name, **options)
                finally:
                    name.unlink(missing_ok=True)
            if mode == "both":
                selected_mode = result["mode"]
                if selected_mode not in ("seed", "opened"):
                    raise ValueError("Choose visible seed or opened remnant mode.")
                mode = selected_mode
            result["mode"] = mode
            if mode != "seed" or result.get("remnants") or result.get("sockets"):
                result["_target_map_id"] = remnant_map
            event = {"mode": mode, "result": result, "error": ""}
        except Exception as exc:
            raw = None
            event = {"mode": mode, "result": None, "error": f"Screen scan failed: {exc}"}
        finally:
            if event and kill_capture is not None and (revision is None or not self._capture_cancelled(revision)):
                level = (strictness_values or {}).get("kills", ocr_sensitivity.DEFAULT)
                event["kill_counts"] = self._read_kills(kill_capture, revision, level)
            if event and event.get("result") is not None and strictness_values is not None:
                event["result"]["_ocr_strictness_values"] = dict(strictness_values)
                key = {"opened": "remnant", "seed": "seed", "propagation": "propagation"}.get(event["mode"])
                if key and strictness_values.get(key, ocr_sensitivity.DEFAULT) != ocr_sensitivity.DEFAULT:
                    event["result"]["_ocr_strictness"] = strictness_values[key]
            if event and event.get("result") is not None and generation is not None:
                event["result"].update({"_scan_generation": generation, "_capture_map_id": capture_map,
                                        "_capture_expedition": expedition, "_capture_map_pending": pending_map})
                if chain_revision is not None:
                    event["result"]["_capture_chain_revision"] = chain_revision
                if event["mode"] == "currency":
                    event["result"]["phase"] = phase
                    event["result"]["map_id"] = remnant_map if phase == "start" else capture_map
            self._finish_capture(event, raw, revision, adapter_complete=True)

    def _ocr_capture_call(self, revision, function, *args, _timeout=None, **kwargs):
        """Run unchanged recognition with the original capture deadline and cancellable native-child ownership."""
        if revision is not None and self._capture_cancelled(revision):
            raise RuntimeError("Screen capture was cancelled.")
        job = ocr_workers.request(function, *args, **kwargs)
        if not isinstance(job, ocr_workers.Request):
            return job()
        with self._lock:
            if revision is not None:
                if revision != self._capture_revision:
                    raise RuntimeError("Screen capture was cancelled.")
                if self._capture_deadline is not None:
                    job = replace(job, deadline=self._capture_deadline)
            if _timeout is not None:
                now = time.monotonic()
                deadline = now + _timeout
                if self._capture_deadline is not None:
                    deadline = min(deadline, self._capture_deadline - .25)
                if deadline <= now:
                    raise TimeoutError("The primary scan used the available capture time. Scan kills again.")
                job = replace(job, deadline=deadline)
            future = ocr_workers.submit(job)
            if revision is not None:
                self._ocr_capture_future = future
                self._ocr_capture_revision = revision
        try:
            return future.result()
        finally:
            with self._lock:
                if self._ocr_capture_future is future:
                    self._ocr_capture_future = self._ocr_capture_revision = None

    def cancel_capture(self, modes=None):
        """Discard selected scan modes without cancelling an unrelated worker.

        With no modes specified, retain the global cancellation used when the
        map, session or application changes. Terminate the matching isolated
        reader and promptly retire its logical reservation and revision. A
        still-running physical adapter retains its separate owner until return,
        so new shortcuts report a busy adapter rather than spawning more work.

        A Ritual page is never cancelled: taken, held or waiting pages are kept,
        and the window checks the map each was taken on when it reads it. A
        general-key capture in progress continues but may only become a Ritual
        page; anything else it finds is discarded with a note.
        """
        modes = None if modes is None else frozenset((modes,) if isinstance(modes, str) else modes)
        with self._lock:
            if modes is None or "propagation" in modes:
                if self._deferred_propagation is not None:
                    self._deferred_propagation["timer"].cancel()
                self._deferred_propagation = None
            if modes is None or modes.intersection(GENERAL_MODES) and self._deferred_propagation is None:
                self._remnant_capture = None
            if modes is None and self._active_capture_kind in ("ritual", "default"):
                if self._active_capture_kind == "default":
                    self._ritual_only = self._capture_owner
            elif modes is None or self._active_capture_mode in modes:
                owner = self._capture_owner
                self._capture_revision += 1
                if self._ocr_capture_future is not None:
                    self._ocr_capture_future.cancel()
                if owner is not None:
                    self._finish_capture(None, None, owner)
            if modes is None or self._latest and self._latest.get("mode") in modes:
                self._latest = self._image = None
            self._events = {key: entry for key, entry in self._events.items()
                            if modes is not None and entry[0].get("mode") not in modes}
            if modes is not None and "ritual" in modes:
                self._ritual_events.clear()
                self._held.clear()

    def _expire_capture(self, mode, revision):
        """Terminate an overdue isolated reader and release only its still-owned capture reservation."""
        with self._lock:
            if self._capture_owner != revision:
                return
            if self._ocr_capture_revision == revision and self._ocr_capture_future is not None:
                self._ocr_capture_future.cancel()
            self._finish_capture({"mode": mode, "result": None,
                                  "error": "Screen scan timed out after 90 seconds. Scan again."},
                                 None, revision, expired=True)

    def _finish_capture(self, event, raw, revision=None, *, expired=False, adapter_complete=False):
        """Suppress canceled results, publish a current event with its image, release the scan
        reservation and notify the UI. Expiration invalidates the reader; a late
        completion cannot release a newer capture's lock or replace its result.
        Returned adapters relinquish physical ownership before notifying the UI;
        cancellation and timeout retain physical ownership until actual return.
        """
        with self._lock:
            if adapter_complete:
                self._release_capture_adapter(revision)
            if revision is not None and revision != self._capture_owner:
                return
            if revision is not None and revision != self._capture_revision:
                event = None
            if (event is not None and revision is not None and revision == self._ritual_only and
                    event.get("mode") != "ritual" and not event.get("error")):
                # A Ritual page started while this general-key capture ran; keep only a Ritual page.
                event = {"mode": "discarded", "result": None, "error": "", "message": DISCARDED_MESSAGE}
            if revision is not None and revision == self._ritual_only:
                self._ritual_only = None
            if expired:
                self._capture_revision += 1
            captured = self._remnant_capture
            if expired:
                if captured and captured["revision"] == revision:
                    self._remnant_capture = None
            elif event is not None and event.get("mode") not in GENERAL_MODES:
                self._remnant_capture = None
                # The general shortcut may identify a Ritual page or item.
                # A request made during that recognition is not a Remnant pair.
                if (self._deferred_propagation is not None and
                        self._deferred_propagation["revision"] == revision):
                    self._deferred_propagation["timer"].cancel()
                    self._deferred_propagation = None
            self._capture_owner = None
            self._active_capture_mode = None
            self._active_capture_kind = None
            self._active_strictness_values = None
            self._capture_deadline = None
            if self._capture_watchdog is not None:
                self._capture_watchdog.cancel()
                self._capture_watchdog = None
            if event is not None:
                paired = (self._active_pair_capture or
                          (self._deferred_propagation is not None and
                           self._deferred_propagation["revision"] == revision))
                self._publish_event(event, raw, paired=paired)
                if captured is not None and captured.get("revision") == revision:
                    captured["event_id"] = event["id"]
            self._active_pair_capture = False
            self._capture_lock.release()
            # Reserve a retained propagation request before another key can
            # claim the slot. Physical ownership still prevents overlap after
            # cancellation or expiry of an external adapter.
            self._run_deferred_propagation()
            self._run_held_captures()
        if event is not None:
            self._notify_event()

    def _deferred_error(self, message):
        """Report a rejected request without building a queue of repeated errors."""
        self._publish_event({"mode": "propagation", "result": None, "error": message}, None)

    def enable_ordered_delivery(self):
        """Preserve an accepted Remnant/Propagation pair for the native consumer."""
        with self._lock:
            self._ordered_delivery = True

    def _publish_event(self, event, raw, *, paired=False):
        """Replace ordinary results; retain the two members of an accepted pair and every
        Ritual page until the window takes it.
        """
        self._sequence += 1
        event["id"] = self._sequence
        self._latest, self._image = event, raw
        if self._ordered_delivery and paired:
            self._events[self._sequence] = (event, raw)
        if event.get("mode") == "ritual" and event.get("result") is not None:
            self._ritual_events[self._sequence] = (event, raw)

    def next_event(self, after_id=0):
        """Deliver retained pair members and Ritual pages oldest first, otherwise only the
        latest result.
        """
        with self._lock:
            consumed = max(after_id, self._acknowledged_event)
            retained = [ident for ident in (*self._events, *self._ritual_events) if ident > consumed]
            if retained:
                ident = min(retained)
                return (self._events.get(ident) or self._ritual_events[ident])[0]
            return self._latest if self._latest and self._latest["id"] > consumed else None

    def ack_event(self, event_id):
        """Release retained result evidence after the UI copied it into its review state."""
        with self._lock:
            self._events.pop(event_id, None)
            self._ritual_events.pop(event_id, None)
            self._acknowledged_event = max(self._acknowledged_event, event_id)

    def image(self, event_id):
        """Return an unconsumed event's own evidence, retaining the legacy latest lookup."""
        with self._lock:
            if event_id in self._events:
                return self._events[event_id][1]
            if event_id in self._ritual_events:
                return self._ritual_events[event_id][1]
            if self._latest and self._latest["id"] == event_id:
                return self._image
        return None
