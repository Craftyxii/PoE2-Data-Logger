"""Smoke coverage for rendered text recognition and bundled socket/rune models, including damaged model rejection."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import rapidocr

from PoE2_Data_Logger.ocr import item_ocr, opened_scan, runehelper_ocr, scan


ROOT = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger"


class AppSmokeTests(unittest.TestCase):
    """Smoke-test rendered-text OCR and bundled rune/socket model assets."""
    def test_general_ocr_recognizes_rendered_text(self):
        """Verify general OCR recognizes Chaos Orb text rendered with the bundled font."""
        opened_scan.verify_models(Path(rapidocr.__file__).parent / "models")
        image = Image.new("RGB", (600, 150), "white")
        font = ImageFont.truetype(str(ROOT / "fonts" / "DejaVuSans.ttf"), 36)
        ImageDraw.Draw(image).text((20, 40), "Chaos Orb", font=font, fill="black")
        rows = item_ocr.ocr_lines(image)
        self.assertTrue(any("Chaos Orb" in row["text"] for row in rows), rows)

    def test_runehelper_model_runs(self):
        """Verify the Rune Helper model returns no text or confidence for a blank row."""
        text, confidence = runehelper_ocr._read_row(np.full((32, 160), 180, dtype=np.uint8))
        self.assertEqual(text, "")
        self.assertEqual(confidence, 0)

    def test_socket_model_and_reference_gallery_load(self):
        """Verify socket assets have finite 1296-value vectors, two templates, and a callable model."""
        model, names, vectors, templates = scan._assets()
        self.assertEqual(len(names), len(vectors))
        self.assertEqual(vectors.shape[1], 1296)
        self.assertTrue(np.isfinite(vectors).all())
        self.assertEqual(len(templates), 2)
        self.assertTrue(callable(model.predict))

    def test_saved_model_damage_is_rejected(self):
        """Verify model verification rejects a directory without the required model files."""
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "missing or damaged"):
                opened_scan.verify_models(directory)


if __name__ == "__main__":
    unittest.main()
