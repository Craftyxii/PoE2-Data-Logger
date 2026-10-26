"""A Ritual or general key pressed while another capture runs takes its own screenshot at once.

Held screenshots wait in press order and become Ritual pages; one that shows no Ritual
window is discarded with a note. Recognition is injected; these tests check delivery only.
"""

import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey

COLOURS = ("tan", "navy", "olive", "maroon", "teal")


def wait_for(condition, seconds=5):
    """Wait for a background capture thread to reach a state."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.01)
    return condition()


class HeldRitualCaptureTests(unittest.TestCase):
    """Drive the hotkey manager directly with a blocking first screenshot."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-held-ritual-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        with logger._connect() as db:
            logger._set_meta(db, "ritual_region", {"x": 0, "y": 0, "w": 600, "h": 250})
        self.entered, self.release = threading.Event(), threading.Event()
        self.grabs = 0

        def grab(**_):
            self.grabs += 1
            index = self.grabs
            if index == 1:
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("Blocked test capture was not released")
            return Image.new("RGB", (600, 250), COLOURS[(index - 1) % len(COLOURS)])

        self.manager = hotkey.HotkeyManager(grabber=Mock(side_effect=grab), supported=False)
        self.manager.before_capture = Mock()

    def tearDown(self):
        self.release.set()
        self.manager.cancel_capture()
        wait_for(lambda: not self.manager._capture_lock.locked() and not self.manager._held)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def collect(self):
        """Take every delivered event in order, as the window does."""
        events, after = [], 0
        while (event := self.manager.next_event(after)) is not None:
            events.append((event, self.manager.image(event["id"])))
            self.manager.ack_event(event["id"])
            after = event["id"]
        return events

    @staticmethod
    def colour(raw):
        with Image.open(io.BytesIO(raw)) as picture:
            return picture.convert("RGB").getpixel((5, 5))

    def test_press_during_busy_capture_takes_its_own_screenshot_and_waits_in_order(self):
        self.manager.capture("ritual", background=True)
        self.assertTrue(self.entered.wait(2))
        self.manager.capture("ritual", background=True)
        # The second press grabs at once, while the first screenshot is still being taken.
        self.assertTrue(wait_for(lambda: self.grabs == 2 and self.manager._held[0]["ready"]))
        self.assertEqual(self.manager.status()["held_ritual"], 1)
        self.assertEqual(self.manager.before_capture.call_count, 2)
        self.release.set()
        self.assertTrue(wait_for(lambda: len(self.manager._ritual_events) == 2))
        events = self.collect()
        self.assertEqual([event["mode"] for event, _ in events], ["ritual", "ritual"])
        self.assertEqual([self.colour(raw) for _, raw in events],
                         [Image.new("RGB", (1, 1), name).getpixel((0, 0)) for name in ("tan", "navy")])
        held = events[1][0]["result"]
        self.assertTrue(held["_held"])
        self.assertEqual(held["map_id"], "M0001")
        self.assertEqual(held["_capture_map_id"], "M0001")
        self.assertEqual(self.manager.status()["held_ritual"], 0)

    def test_fourth_press_is_refused_with_a_message(self):
        self.manager.capture("ritual", background=True)
        self.assertTrue(self.entered.wait(2))
        self.manager.capture("ritual", background=True)
        self.manager.capture("ritual", background=True)
        self.assertTrue(wait_for(lambda: self.grabs == 3))
        self.manager.capture("ritual", background=True)
        latest = self.manager.status()["latest"]
        self.assertEqual(latest["error"], hotkey.RITUAL_LIMIT_MESSAGE)
        self.assertEqual(self.grabs, 3)
        self.release.set()
        self.assertTrue(wait_for(lambda: len(self.manager._ritual_events) == 3))
        self.assertEqual([event["mode"] for event, _ in self.collect() if event["result"]], ["ritual"] * 3)

    def test_cancellation_never_drops_a_ritual_page(self):
        """Reject, + New map, Undo and a Currency scan cancel captures; Ritual pages stay."""
        self.manager.capture("ritual", background=True)
        self.assertTrue(self.entered.wait(2))
        self.manager.capture("ritual", background=True)
        self.assertTrue(wait_for(lambda: self.grabs == 2 and self.manager._held[0]["ready"]))
        self.manager.cancel_capture()
        self.release.set()
        self.assertTrue(wait_for(lambda: len(self.manager._ritual_events) == 2))
        self.manager.cancel_capture()
        self.assertEqual(len(self.collect()), 2)

    def test_focus_lost_before_a_held_grab_reports_it(self):
        # The game loses focus between the press and its held grab.
        self.manager.focused = lambda: threading.current_thread().name != "poe2-hotkey-held-capture"
        self.manager.capture("ritual", background=True)
        self.assertTrue(self.entered.wait(2))
        self.manager.capture("ritual", background=True)
        self.assertTrue(wait_for(lambda: self.manager._held and self.manager._held[0]["ready"]))
        self.release.set()
        self.assertTrue(wait_for(lambda: not self.manager._held and not self.manager._capture_lock.locked()))
        events = self.collect()
        self.assertEqual(events[-1][0]["error"], "The game lost focus before capture. Scan again.")
        self.assertEqual(self.grabs, 1)


class HeldGeneralKeyTests(unittest.TestCase):
    """A held general-key screenshot becomes a Ritual page only when it shows the Ritual window."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-held-general-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = Image.new("RGB", (1000, 800), "black")
        self.window.paste(Image.new("RGB", (600, 250), "navy"), (100, 200))
        self.manager = hotkey.HotkeyManager(supported=True, focused=lambda: True, hover_reader=lambda: None,
                                            tooltip_grabber=lambda: self.window.copy())

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def read_held(self, rows):
        entry = {"kind": "default", "ready": True}
        entry.update(self.manager._held_snapshot("default"))
        with self.manager._lock:
            self.assertTrue(self.manager._capture_lock.acquire(blocking=False))
            self.manager._capture_revision = self.manager._capture_owner = 7
        with patch.object(self.manager, "_ocr_capture_call", return_value=rows):
            self.manager._read_held(7, entry)
        return self.manager.status()["latest"], self.manager.image(self.manager.status()["latest"]["id"])

    def test_ritual_window_becomes_a_page_cropped_to_the_ritual_region(self):
        with patch("PoE2_Data_Logger.ui.region_select.region_for",
                   return_value={"x": 100, "y": 200, "w": 600, "h": 250}):
            event, raw = self.read_held([{"text": "1,162 Tribute", "score": .99}])
        self.assertEqual(event["mode"], "ritual")
        self.assertTrue(event["result"]["_held"])
        with Image.open(io.BytesIO(raw)) as picture:
            self.assertEqual(picture.size, (600, 250))
            self.assertEqual(picture.convert("RGB").getpixel((5, 5)), (0, 0, 128))

    def test_other_window_is_discarded_with_a_note(self):
        event, raw = self.read_held([{"text": "WAYSTONE (TIER 15)", "score": .99}])
        self.assertEqual((event["mode"], event["message"], raw), ("discarded", hotkey.DISCARDED_MESSAGE, None))

    def test_general_capture_running_when_a_page_starts_may_only_become_a_ritual_page(self):
        with self.manager._lock:
            self.assertTrue(self.manager._capture_lock.acquire(blocking=False))
            self.manager._capture_revision = self.manager._capture_owner = 3
            self.manager._active_capture_kind, self.manager._active_capture_mode = "default", "opened"
        self.manager.cancel_capture()
        self.assertEqual(self.manager._capture_revision, 3)
        self.manager._finish_capture({"mode": "item", "result": {"kind": "waystone"}, "error": ""}, b"x", 3,
                                     adapter_complete=True)
        latest = self.manager.status()["latest"]
        self.assertEqual((latest["mode"], latest["message"]), ("discarded", hotkey.DISCARDED_MESSAGE))
        self.assertIsNone(self.manager._ritual_only)


if __name__ == "__main__":
    unittest.main()
