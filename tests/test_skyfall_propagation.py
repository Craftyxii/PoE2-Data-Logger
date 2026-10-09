"""Check Skyfall positions using composed real tile pixels, not the user's capture.

The copied glyphs test geometry; they do not establish Skyfall's native glyph order.
"""

import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw, ImageEnhance
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


ROOT = Path(__file__).resolve().parents[1]
RECIPES = ("Skyfall", "Triskelion Cascade", "Refutation", "Runic Reprieve", "Leylines", "Animus Exchange")


class SkyfallPropagationTests(unittest.TestCase):
    """Check composed Skyfall crown geometry and idempotent Celestial propagation saves."""
    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application for propagation window checks."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Initialize temporary logger data and load real panel pixels for composition."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        with Image.open(ROOT / "PoE2_Data_Logger/region_examples/opened.jpg") as source:
            self.source = runehelper_ocr.default_frame(source.convert("RGB"))

    def tearDown(self):
        """Restore the data directory and remove temporary propagation data."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def composite(self, scale=1, left=0, brightness=1):
        """Compose six tiles with an authentic crown at slot two and stub reward OCR."""
        image = self.source.copy()
        strip = self.source.crop((48, 64, 543, 145))
        ordinary = self.source.crop((52, 64, 90, 106))
        crowned = self.source.crop((134, 145, 172, 187))
        rows = []
        for index, name in enumerate(RECIPES):
            y = 86 + 81 * index
            image.paste(strip, (48, 64 + 81 * index))
            for position in range(1, 7):
                image.paste(crowned if position == 2 else ordinary,
                            (52 + 41 * (position - 1), 64 + 81 * index))
            rows.append({"text": "Skill Level 20: " + name, "score": .99,
                         "x1": 230 * scale - left, "x2": 540 * scale - left,
                         "y1": (y + 23) * scale, "y2": (y + 51) * scale})
        # Keep the capture size while changing the panel's content size.
        canvas = Image.new("RGB", image.size, (176, 161, 130))
        canvas.paste(image.resize((round(image.width * scale), round(image.height * scale)),
                                  Image.Resampling.LANCZOS))
        image = ImageEnhance.Brightness(canvas).enhance(brightness)
        y = round(86 * scale)
        ImageDraw.Draw(image).polygon([(1, y), (15, y - 10), (35, y), (15, y + 10)],
                                     fill=(242, 209, 124))
        image = image.crop((left, 0, image.width, image.height))
        title = {"text": "Runeshape Combinations", "score": .99,
                 "x1": 180 * scale - left, "x2": 485 * scale - left,
                 "y1": 32 * scale, "y2": 61 * scale}
        if left:
            factor = 575 / image.width
            for row in rows + [title]:
                for key in ("x1", "x2", "y1", "y2"):
                    row[key] *= factor
        with patch.object(propagation_scan, "_read_panel_rows", return_value=(rows, title)):
            return image, propagation_scan.scan_propagation(image)

    def test_clear_six_socket_skyfall_second_mark_is_celestial(self):
        """Verify the composed six-socket Skyfall slot-two crown resolves to Celestial."""
        _, result = self.composite()
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Skyfall (Level 20)")
        self.assertEqual((result["positions"], result["runes"]), ([2], ["Celestial"]))

    def test_scaled_and_left_cropped_skyfall_never_uses_tempest_for_second_mark(self):
        """Verify changed capture geometry yields Celestial or review without a Tempest guess."""
        variants = ((.72, 0, 1.2), (.74, 0, 1.2), (.75, 0, 1.2),
                    (1, 20, 1), (1, 35, 1), (1, 46, 1))
        for scale, left, brightness in variants:
            with self.subTest(scale=scale, left=left, brightness=brightness):
                _, result = self.composite(scale, left, brightness)
                skyfall = result["choices"][0]
                self.assertEqual(skyfall["selected_recipe"], "Skyfall (Level 20)")
                if skyfall["can_use"]:
                    self.assertEqual((skyfall["positions"], skyfall["runes"]), ([2], ["Celestial"]))
                else:
                    self.assertEqual((skyfall["positions"], skyfall["runes"]), ([], []))
                self.assertNotIn("Tempest", result["runes"])

    def test_clear_skyfall_saves_celestial_once_without_advancing_expedition(self):
        """Hold real Skyfall OCR until approval, then deduplicate its accepted callback."""
        image, result = self.composite()
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        window = LoggerWindow()
        window._poll.stop()
        try:
            result.update(logger.scan_context())
            expedition = logger.get_state()["current_expedition_id"]
            window._propagation_read(result, raw.getvalue())
            window._propagation_read(result, raw.getvalue())
            self.assertEqual(logger.get_state()["chain"], [])
            self.assertIsNone(logger.get_state()["detonated"])
            window.approve_propagation_recipe(window.propagation_recipe_table.currentRow())
            window._propagation_read(result, raw.getvalue())
            state = logger.get_state()
            self.assertEqual([(part["rune1"], part["rune2"]) for part in state["chain"]],
                             [("Celestial", "")])
            self.assertEqual(state["detonated"], 1)
            self.assertEqual(state["current_expedition_id"], expedition)
            with logger._connect() as db:
                receipts = db.execute("SELECT expedition_id,payload_json FROM chain_append_receipts").fetchall()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["expedition_id"], expedition)
            self.assertEqual(json.loads(receipts[0]["payload_json"])["runes"], ["Celestial"])
            records = list(csv.DictReader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
            saved = [record for record in records if record["Type"] == "Propagation"]
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["Recipe"], "Skyfall (Level 20)")
            self.assertEqual(saved[0]["Propagation Rune 1"], "Celestial")
            self.assertEqual(saved[0]["Propagation Rune 2"], "")
            self.assertEqual(saved[0]["Expedition ID"], expedition)
            self.assertEqual(saved[0]["Remnants Detonated (Scan)"], "1")
            self.assertNotIn("Tempest", saved[0]["Propagation Rune 1"])
        finally:
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
