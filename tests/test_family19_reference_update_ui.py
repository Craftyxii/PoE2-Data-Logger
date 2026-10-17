"""Controlled reward detections test the real resolver and existing Qt selectors.

Composed rune pixels and injected reward text do not measure screenshot OCR.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton
from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from tests import test_propagation_ocr as fixtures

PACK = Path(__file__).resolve().parents[1] / "docs/reference_updates/family19-chaos-x10/Family19_ChaosOrb_x10_reference_proposal.zip"


class Family19SelectorProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="family19-selector-proof-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        self.fixture = fixtures.PropagationOCRTests()
        self.fixture.glyph = Image.new("RGB", (29, 29), "black")
        self.image, self.rows = self.fixture.panel(
            ["Cadigan's Epiphany", "Astrid's Creativity", "Aldur's Saga"],
            selected=0, marks={0: [1], 1: [1], 2: [1]})
        self.rows[2]["text"] = "10x Chaos Orb"

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temp.cleanup()

    def test_unregistered_quantity_blocks_family_resolution_without_fabrication(self):
        result = self.fixture.scan(self.image, self.rows)
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["choices"][2]["selected_recipe"])
        self.assertFalse(result["choices"][2]["can_use"])
        self.assertEqual(result["choices"][2]["runes"], [])
        self.assertEqual(logger.get_state()["chain"], [])
        print("BASELINE:", json.dumps({"candidates": result["candidates"],
              "choices": [{key: choice[key] for key in
                  ("selected_recipe", "positions", "runes", "can_use")}
                  for choice in result["choices"]]}))

    def test_explicit_reference_import_fills_existing_selectors_and_still_requires_approve(self):
        counts = reference_pack.import_pack(PACK, replace_existing=True)
        self.assertEqual(tuple(counts[key] for key in ("recipes", "families", "seed_states")), (1, 1, 1))
        result = {**self.fixture.scan(self.image, self.rows), **logger.scan_context()}
        self.assertEqual(result["candidates"], [19])
        self.assertEqual([choice["selected_recipe"] for choice in result["choices"]],
                         ["Cadigan's Epiphany", "Astrid's Creativity", "Chaos Orb x10"])
        for choice in result["choices"]:
            self.assertTrue(choice["can_use"])
            self.assertEqual(choice["positions"], [1])
            self.assertEqual(choice["runes"], ["Wisdom"])
        window = LoggerWindow()
        window._poll.stop()
        try:
            raw = io.BytesIO()
            self.image.save(raw, format="PNG")
            before = logger.get_state()
            window._propagation_read(result, raw.getvalue())
            self.app.processEvents()
            self.assertEqual(logger.get_state(), before)
            self.assertFalse(window.preview.pixmap().isNull())
            self.assertEqual(window.propagation_recipe_table.rowCount(), 3)
            self.assertEqual(window.propagation_recipe_table.columnCount(), 3)
            for fields in window._propagation_row_inputs:
                self.assertEqual([field.currentText() for field in fields], ["Wisdom", ""])
                self.assertEqual([field.currentData() for field in fields], [0, None])
            window.propagation_recipe_table.cellWidget(2, 2).findChild(
                QPushButton, "approvePropagationRecipe").click()
            self.app.processEvents()
            saved = logger.get_state()
            self.assertEqual(saved["chain"], [{"step": 1, "rune1": "Wisdom", "rune2": ""}])
            self.assertEqual(saved["chain_provenance"][0]["recipe"], "Chaos Orb x10")
            self.assertEqual(saved["chain_provenance"][0]["family"], 19)
            self.assertEqual(saved["detonated"], (before["detonated"] or 0) + 1)
            print("AFTER EXPLICIT IMPORT: three Wisdom selectors; saved once only on Approve")
        finally:
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main(defaultTest="Family19SelectorProof", verbosity=2)
