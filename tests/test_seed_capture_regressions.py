"""Real pre-open bars retain their slot geometry and learn only after approval."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import numpy as np
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import scan
from PoE2_Data_Logger.ocr.glyph_eval import vector
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


CAPTURE = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/seed.jpg"


class SeedCaptureRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.capture_files = tempfile.TemporaryDirectory(prefix="poe2-real-seed-captures-")
        cls.readings = {}
        cls.paths = {}
        previous = store.DATA_DIR
        try:
            store.DATA_DIR = Path(cls.capture_files.name) / "untrained-data"
            logger._READY = False
            logger.initialize()
            with Image.open(CAPTURE) as source:
                source = source.convert("RGB")
                cls.native_size = source.size
                for scale in (1, .75, .85, 1.15, 1.25):
                    image = source.resize((round(source.width * scale), round(source.height * scale)),
                                          Image.Resampling.LANCZOS)
                    path = Path(cls.capture_files.name) / f"seed-{scale}.png"
                    image.save(path)
                    cls.paths[scale] = path
                    cls.readings[scale] = scan.scan(path)
        finally:
            store.DATA_DIR = previous
            logger._READY = False

    @classmethod
    def tearDownClass(cls):
        cls.capture_files.cleanup()

    def setUp(self):
        self.data = tempfile.TemporaryDirectory(prefix="poe2-seed-review-learning-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.data.name)
        logger._READY = False
        logger.initialize()
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            self.window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.data.cleanup()

    def review_capture(self):
        self.window = LoggerWindow()
        self.window._poll.stop()
        result = {**copy.deepcopy(self.readings[1]), **logger.scan_context(), "mode": "seed"}
        self.window._scan_done("seed", result, self.paths[1].read_bytes())
        self.assertEqual(self.window.pending_review_kind, "seed")
        self.assertEqual(self.window.seed_table.rowCount(), 4)
        return self.window

    def test_actual_capture_keeps_four_bars_and_known_slots_across_scales(self):
        # The first glyph is not established by this capture's ground truth;
        # its suggestion must stay held rather than inventing a family.
        expected = [(4, "P1", "Adaptive"), (3, "P2", "Arcane"), (4, "P1", "Oath")]
        for scale, result in self.readings.items():
            with self.subTest(scale=scale):
                bars = result.get("remnants", [])
                self.assertEqual(len(bars), 4, result)
                self.assertEqual((bars[0]["sockets"], bars[0]["seed_slot"]), (4, "P1"))
                self.assertFalse(bars[0].get("can_commit"), bars[0])
                self.assertIsNone(bars[0].get("family"), bars[0])
                self.assertEqual([(bar["sockets"], bar["seed_slot"], bar["seed_rune"])
                                  for bar in bars[1:]], expected)
                for bar in bars:
                    bounds = bar["bar_bounds"]
                    self.assertGreater(bounds["width"], 0)
                    self.assertGreater(bounds["height"], 0)
                    self.assertGreaterEqual(bounds["x"], 0)
                    self.assertGreaterEqual(bounds["y"], 0)
                    self.assertLessEqual(bounds["x"] + bounds["width"], round(self.native_size[0] * scale))
                    self.assertLessEqual(bounds["y"] + bounds["height"], round(self.native_size[1] * scale))
        # This exact upscale previously turned the first bar into a confident
        # P2 Rebirth and silently committed the wrong remnant family.
        first = self.readings[1.25]["remnants"][0]
        self.assertEqual(first["seed_slot"], "P1")
        self.assertFalse(first.get("can_commit"))

    def test_manual_approval_saves_linked_reference_used_by_later_recognition(self):
        model, names, vectors, templates = scan._assets()
        keep = names != "Adaptive"
        without_adaptive = (model, names[keep], vectors[keep], templates)
        # Remove only this rune's bundled gallery entries to prove that the
        # reviewed capture, rather than an existing reference, supplies it.
        with patch.object(scan, "_assets", return_value=without_adaptive):
            before = scan.scan(self.paths[1])["remnants"][1]
        self.assertNotEqual(before.get("seed_rune"), "Adaptive")
        self.assertEqual(store.reviewed_glyphs(), [])
        window = self.review_capture()
        window.seed_table.setCurrentCell(1, 2)
        self.assertTrue(window.approve_scan_button.isEnabled())
        window.approve_scan_button.click()
        self.app.processEvents()
        self.assertTrue(window._seed_readings[1].get("saved"), window.scan_status.text())
        with logger._connect() as db:
            references = db.execute("SELECT id,seed_rune,family FROM scans").fetchall()
            links = db.execute("SELECT remnant_id,scan_id FROM scan_links").fetchall()
            self.assertEqual(len(references), 1)
            self.assertEqual((references[0]["seed_rune"], references[0]["family"]), ("Adaptive", "Family 3"))
            self.assertEqual([tuple(link) for link in links], [(window._seed_readings[1]["saved"]["remnant_id"],
                                                               references[0]["id"])])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commits WHERE kind='Remnant'").fetchone()[0], 1)
        learned = store.reviewed_glyphs()
        self.assertEqual([rune for rune, _ in learned], ["Adaptive"])
        # Compare the saved feature directly with each independently cropped
        # visible seed. A neighboring book must not select another bar's glyph.
        with Image.open(self.paths[1]) as image:
            direct_scores = []
            for bar in self.readings[1]["remnants"]:
                center = bar["seed_center"]
                glyph = image.crop((center["x"] - 18, center["y"] - 18,
                                    center["x"] + 18, center["y"] + 18))
                direct_scores.append(float(learned[0][1] @ vector(glyph, "gray")))
        self.assertEqual(int(np.argmax(direct_scores)), 1, direct_scores)
        self.assertGreater(direct_scores[1], .8, direct_scores)
        with patch.object(scan, "_assets", return_value=without_adaptive):
            after = scan.scan(self.paths[1])["remnants"][1]
        self.assertEqual((after["sockets"], after["seed_slot"], after["seed_rune"], after["family"]),
                         (4, "P1", "Adaptive", "Family 3"))

    def test_rejecting_real_seed_rows_never_adds_training_or_commits(self):
        window = self.review_capture()
        for row in range(4):
            window.seed_table.setCurrentCell(row, 2)
            window.reject_scan_button.click()
            self.assertTrue(window._seed_readings[row].get("rejected"))
        self.assertIsNone(window.pending_review_kind)
        self.assertEqual(store.reviewed_glyphs(), [])
        with logger._connect() as db:
            for table in ("scans", "scan_links", "commits"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)


if __name__ == "__main__":
    unittest.main()
