"""Random Ritual sessions: every saved page matches an independent tally after every step.

The regular suite runs 25 sessions; the release gate runs 200 with
POE2_RITUAL_SESSIONS=200 (POE2_RITUAL_FIRST_SEED splits them across processes).

Each session mixes new pages, repeated pages, presses while a page is read or held,
held pages approved or rejected, failed readings, deferred-only pages, + New map in the
middle of a reading and Undo. A small model of the rules the user set predicts which
pages each map holds; the logger's database must agree after every step.

Rules the model follows:
- A page being read or held is never replaced; up to two more wait, a third press is refused.
- Waiting pages are read in capture order.
- A page is saved to the map it was captured on, also after + New map.
- A page whose roll count equals its map's last saved page is a repeat and is not saved.
- Undo reverses the last saved action (a page, or + New map's two steps). Unsaved pages taken
  on a finished map that still exists are kept (a held review stays open, a page being read is
  read again; not a failed reading, which has no capture left); the rest are dropped.
"""
from concurrent.futures import Future
import gc
import io
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import native_desktop as ui

SESSIONS = int(os.environ.get("POE2_RITUAL_SESSIONS", "25"))
FIRST_SEED = int(os.environ.get("POE2_RITUAL_FIRST_SEED", "0"))
STEPS = 40


class RetainedFuture(Future):
    def cancel(self):
        return False


class Model:
    """The expected outcome, kept apart from the app's own state."""

    def __init__(self):
        self.maps = ["M0001"]
        self.pages = {"M0001": []}
        self.active = None       # {"map", "phase": reading|held|failed, "future"}
        self.waiting = []        # map IDs, oldest first
        self.undo = []           # "Ritual approval" (with its map), "Finalize map" or "New map"
        self.started = 0         # recognitions submitted

    @property
    def current(self):
        return self.maps[-1]

    def last_rolls(self, map_id):
        return self.pages[map_id][-1][1] if self.pages[map_id] else None

    def start(self, map_id):
        self.active = {"map": map_id, "phase": "reading", "future": self.started}
        self.started += 1

    def save(self, map_id, quantity, rolls):
        self.pages[map_id].append((quantity, rolls))
        self.undo.append(("Ritual approval", map_id))


class RandomRitualSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open_session(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-random-")
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({"ocr_auto_commit": True, "activity_type": "Ritual"})
        self.manager = hotkey.HotkeyManager(supported=False)
        self.hotkey_patch = patch.object(service, "HOTKEY", self.manager)
        self.hotkey_patch.start()
        self.window = ui.LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()
        self.futures = []
        self.submit_patch = patch.object(ui.ocr_workers, "submit", side_effect=self.submit)
        self.submit_patch.start()
        self.images = 0

    def close_session(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        # Free each session's window so 200 sessions fit in one process.
        self.window.deleteLater()
        self.app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window = None
        self.futures.clear()
        gc.collect()
        self.submit_patch.stop()
        self.hotkey_patch.stop()
        logger._READY = False
        self.temporary.cleanup()

    def setUp(self):
        self.previous = store.DATA_DIR

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False

    def submit(self, request):
        future = RetainedFuture()
        self.futures.append(future)
        return future

    def capture(self):
        """A fresh screenshot each press, so no two pages share an image."""
        self.images += 1
        raw = io.BytesIO()
        Image.new("RGB", (120, 50), (self.images % 256, self.images // 256, 77)).save(raw, format="PNG")
        result = {"captured": True, "map_id": logger.get_state()["current_map_id"], **logger.scan_context(),
                  "_ocr_strictness_values": {"ritual": 50}}
        with self.manager._lock:
            self.manager._publish_event({"mode": "ritual", "result": result, "error": ""}, raw.getvalue())
        self.window.poll()
        self.app.processEvents()

    @staticmethod
    def reading(quantity, rolls, *, held=False, deferred_only=False):
        return {"items": [{"category": "Item", "name": "Ritual Delivery Reward", "quantity": quantity,
                           "tribute": 100, "source": "text OCR", "deferred": deferred_only,
                           "score": .99, "name_match": 1, "needs_review": held}],
                "raw_text": f"page {quantity}", "reward_count": 1, "needs_review": held,
                "coverage_uncertain": False,
                "tribute_available": None if deferred_only else 5000,
                "rerolls_remaining": None if deferred_only else rolls}

    def app_pages(self, map_id):
        return [(page["items"][0]["quantity"], None) for page in logger.ritual_pages_for_map(map_id)]

    def check(self, model, step):
        state = logger.get_state()
        where = f"step {step}"
        self.assertEqual(state["current_map_id"], model.current, where)
        for map_id in model.maps:
            self.assertEqual([quantity for quantity, _ in self.app_pages(map_id)],
                             [quantity for quantity, _ in model.pages[map_id]], f"{where} {map_id}")
        self.assertEqual(len(self.futures), model.started, where)
        self.assertEqual([page["context"]["_capture_map_id"] for page in self.window._ritual_waiting],
                         model.waiting, where)
        self.assertEqual(self.window.pending_review_kind == "ritual", model.active is not None, where)
        if model.active is not None:
            self.assertEqual(self.window._ritual_reading is not None, model.active["phase"] == "reading", where)

    def run_session(self, seed):
        rng = random.Random(seed)
        model = Model()
        quantity = 0
        rolls = {}  # roll counter per map, as the game would show it
        for step in range(STEPS):
            choices = ["capture", "capture", "capture", "poll"]
            if model.active and model.active["phase"] == "reading":
                choices += ["finish"] * 4
            if model.active and model.active["phase"] == "held":
                choices += ["approve", "approve", "reject"]
            if model.active and model.active["phase"] == "failed":
                choices += ["reject"]
            choices += ["new map"]
            if model.undo:
                choices += ["undo"]
            action = rng.choice(choices)
            if action == "capture":
                busy = model.active is not None
                self.capture()
                if not busy and not model.waiting:
                    model.start(model.current)
                elif len(model.waiting) < 2:
                    model.waiting.append(model.current)
                elif busy:
                    self.assertEqual(self.window.statusBar().currentMessage(),
                                     "Two Ritual pages are already waiting. Approve or reject the current page, "
                                     "then scan again.", f"seed {seed} step {step}")
            elif action == "poll":
                self.window.poll()
                self.app.processEvents()
            if action in ("capture", "poll") and model.active is None and model.waiting:
                # Each window tick reads the oldest waiting page once nothing else is open.
                model.start(model.waiting.pop(0))
            elif action == "finish":
                map_id = model.active["map"]
                outcome = rng.choice(["clear", "clear", "clear", "repeat", "held", "failed", "deferred"])
                quantity += 1
                last = model.last_rolls(map_id)
                if outcome == "repeat" and last is not None:
                    page_rolls = last
                else:
                    # Rolls only go down, so a new page never matches the page saved before it.
                    rolls[map_id] = rolls.get(map_id, 10) - 1
                    page_rolls = rolls[map_id]
                future = self.futures[model.active["future"]]
                if outcome == "failed":
                    future.set_exception(TimeoutError("OCR timed out after 90 seconds"))
                    model.active["phase"] = "failed"
                elif outcome == "deferred":
                    future.set_result(self.reading(quantity, None, deferred_only=True))
                    model.active = None
                elif page_rolls == last:
                    future.set_result(self.reading(quantity, page_rolls))
                    model.active = None
                elif outcome == "held":
                    future.set_result(self.reading(quantity, page_rolls, held=True))
                    model.active.update(phase="held", quantity=quantity, rolls=page_rolls)
                else:
                    future.set_result(self.reading(quantity, page_rolls))
                    model.save(map_id, quantity, page_rolls)
                    model.active = None
                self.app.processEvents()
            elif action == "approve":
                for row in range(self.window.ritual_table.rowCount()):
                    self.window.review_ritual_row(self.window.ritual_table.cellWidget(row, 6), True)
                self.window.approve_scan_button.click()
                self.app.processEvents()
                model.save(model.active["map"], model.active["quantity"], model.active["rolls"])
                model.active = None
            elif action == "reject":
                self.window.reject_scan_button.click()
                self.app.processEvents()
                model.active = None
            elif action == "new map":
                self.window.finish_map()
                self.app.processEvents()
                new = f"M{len(model.maps) + 1:04d}"
                model.maps.append(new)
                model.pages[new] = []
                # + New map finalizes the map, then starts the next: two Undo steps, newest first.
                model.undo += [("Finalize map", None), ("New map", None)]
            elif action == "undo":
                stale = model.active
                before = model.current
                saved = self.window.undo_map()
                self.app.processEvents()
                kind, map_id = model.undo.pop()
                self.assertEqual(saved["action"], kind, f"seed {seed} step {step}")
                if kind == "Ritual approval":
                    model.pages[map_id].pop()
                elif kind == "New map":
                    dropped = model.maps.pop()
                    del model.pages[dropped]
                    rolls.pop(dropped, None)
                # Unsaved pages taken on a finished map that still exists are kept: a held review
                # stays open, a page being read or waiting is read again. The rest are dropped
                # with a message; a failed reading has no capture left.
                def survives(map_id):
                    return map_id != before and map_id in model.pages
                active_kept = bool(model.active and model.active["phase"] != "failed"
                                   and survives(model.active["map"]))
                waiting_kept = [page for page in model.waiting if survives(page)]
                dropped = int(bool(model.active) and not active_kept) + len(model.waiting) - len(waiting_kept)
                kept = int(active_kept) + len(waiting_kept)
                if dropped:
                    self.assertIn(f"{dropped} unsaved Ritual page", self.window.review_summary.text(),
                                  f"seed {seed} step {step}")
                if kept:
                    self.assertIn(f"{kept} Ritual page{'s' if kept > 1 else ''} taken on",
                                  self.window.review_summary.text(), f"seed {seed} step {step}")
                if active_kept and model.active["phase"] == "held":
                    model.waiting = waiting_kept
                else:
                    model.waiting = ([model.active["map"]] if active_kept else []) + waiting_kept
                    model.active = None
                if stale and stale["phase"] == "reading" and rng.random() < .5:
                    # A reading that finishes after Undo is ignored.
                    quantity += 1
                    self.futures[stale["future"]].set_result(self.reading(quantity, 1))
                    self.app.processEvents()
            self.check(model, f"{step} ({action}) seed {seed}")

    def test_random_sessions_match_the_independent_tally(self):
        for seed in range(FIRST_SEED, FIRST_SEED + SESSIONS):
            with self.subTest(seed=seed):
                self.open_session()
                try:
                    self.run_session(seed)
                finally:
                    self.close_session()


if __name__ == "__main__":
    unittest.main()
