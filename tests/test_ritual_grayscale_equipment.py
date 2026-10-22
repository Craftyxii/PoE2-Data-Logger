"""Keep desaturated equipment whole before review and local artwork learning."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageEnhance
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from tests.test_ritual_grid_geometry import synthetic_grid


FIXTURES = Path(__file__).parent / "fixtures/ritual_rewards"


class RitualGrayscaleEquipmentGeometryTests(unittest.TestCase):
    """Assert actual reward footprints, including adjacent single-cell rewards."""

    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        cls.pages = {}
        for case in cls.cases:
            with Image.open(FIXTURES / case["file"]) as source:
                cls.pages[case["file"]] = source.convert("RGB")

    def assert_footprints(self, image, case):
        grid = detect_reward_grid(image)
        self.assertIsNotNone(grid)
        self.assertEqual([reward["slots"] for reward in grid["rewards"]], case["footprints"])
        if case["highlighted_empty_slot"] is not None:
            self.assertNotIn(case["highlighted_empty_slot"],
                             [slot for reward in grid["rewards"] for slot in reward["slots"]])

    def test_all_real_grayscale_layouts_survive_moderate_dimming_and_jpeg(self):
        """A lost frame colour must not split gear or merge neighbouring Omens."""
        for case in self.cases:
            gray = self.pages[case["file"]].convert("L").convert("RGB")
            buffer = io.BytesIO()
            gray.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            with Image.open(buffer) as compressed:
                variants = (gray, ImageEnhance.Brightness(gray).enhance(.7), compressed.convert("RGB"))
            for variant, image in zip(("gray", "dim", "JPEG85"), variants):
                with self.subTest(capture=case["file"], variant=variant):
                    self.assert_footprints(image, case)

    def test_all_real_grayscale_layouts_preserve_footprints_at_multiple_resolutions(self):
        """Compare complete slot sets after resampling, including eight-cell equipment."""
        for case in self.cases:
            gray = self.pages[case["file"]].convert("L").convert("RGB")
            for scale in (.75, 1.25, 2):
                with self.subTest(capture=case["file"], scale=scale):
                    resized = gray.resize((round(gray.width * scale), round(gray.height * scale)),
                                          Image.Resampling.LANCZOS)
                    self.assert_footprints(resized, case)

    def test_existing_moderately_dimmed_colour_layouts_keep_every_footprint(self):
        for case in self.cases:
            with self.subTest(capture=case["file"]):
                self.assert_footprints(ImageEnhance.Brightness(self.pages[case["file"]]).enhance(.7), case)

    def test_neutral_empty_motifs_cursor_and_adjacent_single_cells_do_not_form_equipment(self):
        for cursor in (False, True):
            with self.subTest(cursor=cursor):
                image = synthetic_grid(cursor=cursor).convert("L").convert("RGB")
                grid = detect_reward_grid(image)
                self.assertIsNotNone(grid)
                self.assertEqual(grid["rewards"], [])
        image = synthetic_grid(range(1, 121)).convert("L").convert("RGB")
        grid = detect_reward_grid(image)
        self.assertIsNotNone(grid)
        self.assertEqual([reward["slots"] for reward in grid["rewards"]],
                         [[slot] for slot in range(1, 121)])


class RitualGrayscaleEquipmentLearningTests(unittest.TestCase):
    """Use real capture, scanner, Qt approval, persisted pixels and repeat recognition."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-gray-gear-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit_scan = lambda label, request, done, *args: self.jobs.append((request, done))
        with Image.open(FIXTURES / "01.png") as source:
            self.image = source.convert("L").convert("RGB")

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def scan(self):
        self.window._ritual_captured(self.image, live=True)
        request, deliver = self.jobs[-1]
        result = request.function(*request.args, **request.kwargs)
        deliver(result)
        self.app.processEvents()
        return result

    def test_approved_2x2_and_2x3_gear_teach_whole_artwork_with_adjacent_omens_independent(self):
        result = self.scan()
        expected = next(case for case in json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
                        if case["file"] == "01.png")
        self.assertEqual([item["grid_slots"] for item in result["items"]], expected["footprints"])
        choices = {
            (1,): ("Omen of Refreshment", "Omen", (1, 1)),
            (2, 3, 14, 15): ("Reviewed grayscale gloves", "Item", (2, 2)),
            (13,): ("Omen of Whittling", "Omen", (1, 1)),
            (25, 26, 37, 38, 49, 50): ("Reviewed grayscale armour", "Item", (2, 3)),
        }
        originals = {tuple(item["grid_slots"]): item for item in result["items"]}
        table = self.window.ritual_table
        for row in range(table.rowCount()):
            slots = tuple(table.item(row, 0).data(Qt.ItemDataRole.UserRole)["grid_slots"])
            if slots in choices:
                name, category, _ = choices[slots]
                table.item(row, 0).setText(category)
                table.item(row, 1).setText(name)
                table.item(row, 2).setText("1")
                table.item(row, 5).setCheckState(Qt.CheckState.Unchecked)
                controls = table.cellWidget(row, 6)
                self.window.review_ritual_row(controls, True)
                self.assertEqual(controls.property("reviewStatus"), "approved")
            else:
                table.item(row, 1).setText("")
        self.assertEqual(logger.review_icons(), [])
        self.window.approve_scan_button.click()
        self.app.processEvents()
        self.assertIsNone(self.window.pending_review_kind)
        saved = logger.ritual_pages_for_map("M0001")
        self.assertEqual(len(saved), 1)
        self.assertEqual([(item["name"], item["category"], item["quantity"]) for item in saved[0]["items"]],
                         [(name, category, 1) for name, category, _ in choices.values()])

        logger._READY = False
        logger.initialize()
        references = {reference["name"]: reference for reference in logger.review_icons()}
        self.assertEqual(set(references), {choice[0] for choice in choices.values()})
        for slots, (name, category, (columns, rows)) in choices.items():
            reference = references[name]
            self.assertEqual((reference["columns"], reference["rows"]), (columns, rows))
            self.assertEqual(reference["kind"], category.lower())
            left, top, right, bottom = originals[slots]["box"]
            expected_pixels = self.image.crop((left + 1, top + 1, right, bottom)).resize(
                (96 * columns, 96 * rows), Image.Resampling.LANCZOS)
            with Image.open(io.BytesIO(reference["image"])) as learned:
                self.assertEqual(learned.size, (96 * columns, 96 * rows))
                self.assertEqual(learned.convert("RGB").tobytes(), expected_pixels.tobytes())

        repeated = self.scan()
        self.assertEqual([item["grid_slots"] for item in repeated["items"]], expected["footprints"])
        by_slots = {tuple(item["grid_slots"]): item for item in repeated["items"]}
        for slots, (name, category, _) in choices.items():
            if len(slots) > 1:
                self.assertEqual((by_slots[slots]["name"], by_slots[slots]["category"], by_slots[slots]["quantity"]),
                                 (name, category, 1))
                self.assertEqual(sum(item["name"] == name for item in repeated["items"]), 1)
        self.assertNotIn(by_slots[(1,)]["name"], {choices[(2, 3, 14, 15)][0],
                                                choices[(25, 26, 37, 38, 49, 50)][0]})
        self.assertNotIn(by_slots[(13,)]["name"], {choices[(2, 3, 14, 15)][0],
                                                 choices[(25, 26, 37, 38, 49, 50)][0]})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)


if __name__ == "__main__":
    unittest.main()
