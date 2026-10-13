"""Keep captured inventory evidence visible while avoiding repeated PNG/grid rendering."""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class InventoryPreviewReuseTests(unittest.TestCase):
    """Use real Qt previews and capture callbacks with only OCR submission held."""

    @classmethod
    def setUpClass(cls):
        """Share the application required by the native screenshot labels."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger and retain recognition callbacks for explicit delivery."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-preview-reuse-")
        self.previous_data = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append(done)
        self.review_patch = patch.object(self.window, "_show_review_capture",
                                         wraps=self.window._show_review_capture)
        self.grid_patch = patch.object(self.window, "_show_inventory_preview",
                                       wraps=self.window._show_inventory_preview)
        self.review_render = self.review_patch.start()
        self.grid_render = self.grid_patch.start()

    def tearDown(self):
        """Retire widgets and restore the caller's profile and capture mode."""
        self.grid_patch.stop()
        self.review_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def grid(self, offset=0):
        """Make every inventory slot identifiable without an OCR stand-in image."""
        image = Image.new("RGB", (651, 280))
        image.info["poe2_inventory_aligned"] = True
        draw = ImageDraw.Draw(image)
        for slot in range(1, 61):
            column, row = (slot - 1) % 12, (slot - 1) // 12
            draw.rectangle((round(column * image.width / 12), round(row * image.height / 5),
                            round((column + 1) * image.width / 12) - 1,
                            round((row + 1) * image.height / 5) - 1),
                           fill=((slot * 37 + offset) % 256, (slot * 67 + offset) % 256,
                                 (slot * 101 + offset) % 256))
        return image

    def capture(self, image, *, live=False):
        """Run the production capture preparation and return its completion callback."""
        self.window._inventory_captured(image, live=live)
        return self.jobs[-1]

    def result(self, *, uncertain=False):
        """Keep result data constant while exercising manual and automatic review behavior."""
        return {"items": [{"slot": 14, "name": "Chaos Orb", "quantity": 7,
                           "count_needs_review": uncertain}], "unknown": []}

    def pictures(self):
        """Read actual Qt pixels, including the rendered grid and numbered slots."""
        return (self.window.preview.pixmap().toImage(),
                self.window.inventory_preview.pixmap().toImage())

    def assert_review_visible(self):
        """Reveal the review screenshot and preserve the settings tool's numbered evidence."""
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertTrue(self.window.preview.isVisible())
        self.assertFalse(self.window.inventory_preview.pixmap().isNull())

    def test_completion_reuses_identical_review_and_numbered_grid_pixels(self):
        """Render once, preserve pixels and row evidence, then save only after approval."""
        image = self.grid()
        before_image = image.tobytes()
        done = self.capture(image)
        before = self.pictures()
        done(self.result())

        self.assertEqual(self.review_render.call_count, 1)
        self.assertEqual(self.grid_render.call_count, 1)
        self.assertEqual(self.pictures(), before)
        self.assertEqual(image.tobytes(), before_image)
        self.assert_review_visible()
        self.assertIsNone(self.window._inventory_reading)
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        cell = self.window.inventory_table.item(0, 0)
        self.assertEqual(cell.data(Qt.ItemDataRole.UserRole), 14)
        self.assertFalse(cell.icon().isNull())
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(self.window.inventory_table.item(0, 2).text(), "7")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(self.pictures(), before)

    def test_changed_tab_mode_hidden_preview_and_resize_restore_capture_evidence(self):
        """Return from other UI content at a new size with the original currency screenshots."""
        done = self.capture(self.grid())
        before = self.pictures()
        unrelated = io.BytesIO()
        Image.new("RGB", (500, 400), "magenta").save(unrelated, format="PNG")
        self.window.review_kind.setProperty("scanKind", "remnant")
        self.window._show_image(unrelated.getvalue())
        self.window.preview.hide()
        self.window.inventory_preview.clear()
        self.window.mode = "opened"
        select(self.window.mode_select, "opened")
        self.window.tabs.setCurrentIndex(4)
        self.window.resize(960, 720)
        self.app.processEvents()
        done(self.result())

        self.assertEqual(self.review_render.call_count, 1)
        self.assertEqual(self.grid_render.call_count, 1)
        self.assertEqual(self.pictures(), before)
        self.assertEqual(self.window.preview.maximumHeight(), 182)
        self.assertEqual(self.window.inventory_preview.size(), before[1].size())
        self.assertEqual(self.window.review_kind.property("scanKind"), "currency")
        self.assert_review_visible()

    def test_superseded_callbacks_cannot_restore_older_screenshots_or_rows(self):
        """Retain each capture independently and ignore older completions before and after the latest."""
        older = self.capture(self.grid())
        older_pictures = self.pictures()
        newer_image = self.grid(51)
        newer = self.capture(newer_image)
        newer_pictures = self.pictures()
        self.assertNotEqual(newer_pictures, older_pictures)
        older(self.result())
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertEqual(self.pictures(), newer_pictures)
        newer(self.result())
        older(self.result(uncertain=True))
        self.assertEqual(self.pictures(), newer_pictures)
        self.assertIs(self.window._inventory_capture, newer_image)
        self.assertEqual(self.window.inventory_table.rowCount(), 1)
        self.assertEqual(self.window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "approved")
        self.assertEqual(self.review_render.call_count, 2)
        self.assertEqual(self.grid_render.call_count, 2)

    def test_rescanning_same_image_renders_for_each_new_capture(self):
        """Icon-learning retries receive new capture ownership even with the same image object."""
        image = self.grid()
        self.capture(image)(self.result())
        first = self.window._inventory_capture_context
        self.capture(image)(self.result())
        self.assertIsNot(self.window._inventory_capture_context, first)
        self.assertEqual(self.review_render.call_count, 2)
        self.assertEqual(self.grid_render.call_count, 2)
        self.assert_review_visible()

    def test_capture_without_retained_pixmaps_keeps_existing_render_path(self):
        """Present captures admitted without the optional UI evidence pair."""
        done = self.capture(self.grid())
        before = self.pictures()
        del self.window._inventory_capture_context["_preview_pixmaps"]
        done(self.result())
        self.assertEqual(self.review_render.call_count, 2)
        self.assertEqual(self.grid_render.call_count, 2)
        self.assertEqual(self.pictures(), before)
        self.assert_review_visible()

    def test_replaced_capture_image_renders_new_pixels(self):
        """Fall back to the existing renderer if a retained capture's image object changes."""
        done = self.capture(self.grid())
        before = self.pictures()
        replacement = self.grid(83)
        self.window._inventory_capture_context["image"] = replacement
        done(self.result())
        self.assertEqual(self.review_render.call_count, 2)
        self.assertEqual(self.grid_render.call_count, 2)
        self.assertNotEqual(self.pictures(), before)
        self.assertIs(self.window._inventory_capture, replacement)
        self.assert_review_visible()

    def test_failed_render_does_not_retain_partial_pair_and_retry_renders_normally(self):
        """Retire either screenshot failure and render a complete new pair on the next scan."""
        image = self.grid()
        for display in ("_show_review_capture", "_show_inventory_preview"):
            with self.subTest(display=display):
                with patch.object(self.window, display, side_effect=OSError("Preview unavailable")), \
                        patch.object(self.window, "_scan_failed", wraps=self.window._scan_failed) as failed:
                    with self.assertRaisesRegex(OSError, "Preview unavailable"):
                        self.window._inventory_captured(image, live=False)
                failed_capture = failed.call_args.args[1]
                self.assertNotIn("_preview_pixmaps", failed_capture)
                self.assertIsNone(self.window._inventory_reading)
                self.assertIsNone(self.window._inventory_capture_context)
                self.assertTrue(self.window._failed_review)
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                done = self.capture(image)
                before = self.pictures()
                self.assertIn("_preview_pixmaps", self.window._inventory_capture_context)
                done(self.result())
                self.assertFalse(self.window._failed_review)
                self.assertEqual(self.pictures(), before)
                self.assert_review_visible()

    def test_live_auto_commit_still_holds_uncertain_and_saves_clear_results_once(self):
        """Use the existing certainty gate and save semantics with retained screenshot pixels."""
        self.window.set_auto_all(True)
        image = self.grid()
        done = self.capture(image, live=True)
        before = self.pictures()
        done(self.result(uncertain=True))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertEqual(self.window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "pending")
        self.assertEqual(self.pictures(), before)
        self.window.reject_review()
        done = self.capture(image, live=True)
        done(self.result())
        done(self.result())
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(self.pictures(), before)
        self.assertEqual(self.review_render.call_count, 2)
        self.assertEqual(self.grid_render.call_count, 2)


if __name__ == "__main__":
    unittest.main()
