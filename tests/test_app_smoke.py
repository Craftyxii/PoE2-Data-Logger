from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import rapidocr

from PoE2_Data_Logger.ocr import item_ocr, opened_scan, runehelper_ocr, scan


ROOT = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger"


class AppSmokeTests(unittest.TestCase):
    def test_general_ocr_recognizes_rendered_text(self):
        opened_scan.verify_models(Path(rapidocr.__file__).parent / "models")
        image = Image.new("RGB", (600, 150), "white")
        font = ImageFont.truetype(str(ROOT / "fonts" / "DejaVuSans.ttf"), 36)
        ImageDraw.Draw(image).text((20, 40), "Chaos Orb", font=font, fill="black")
        rows = item_ocr.ocr_lines(image)
        self.assertTrue(any("Chaos Orb" in row["text"] for row in rows), rows)

    def test_runehelper_model_runs(self):
        text, confidence = runehelper_ocr._read_row(np.full((32, 160), 180, dtype=np.uint8))
        self.assertEqual(text, "")
        self.assertEqual(confidence, 0)

    def test_socket_model_and_reference_gallery_load(self):
        model, names, vectors, templates = scan._assets()
        self.assertEqual(len(names), len(vectors))
        self.assertEqual(vectors.shape[1], 1296)
        self.assertTrue(np.isfinite(vectors).all())
        self.assertEqual(len(templates), 2)
        self.assertTrue(callable(model.predict))

    def test_saved_model_damage_is_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "missing or damaged"):
                opened_scan.verify_models(directory)


if __name__ == "__main__":
    unittest.main()
