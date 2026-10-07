import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from PoE2_Data_Logger.core import logger_store as logger, review_learning, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid


class ReviewLearningBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def art(self, size=(50, 50), seed=73):
        return Image.fromarray(np.random.default_rng(seed).integers(
            35, 220, (size[1], size[0], 3), dtype=np.uint8))

    def example(self, name, category="Currency", size=(50, 50), columns=1, rows=1):
        return {"name": name, "category": category, "image": self.art(size),
                "columns": columns, "rows": rows}

    def inventory(self, cell, slot=20):
        grid = Image.new("RGB", (600, 250), (8, 8, 8))
        grid.paste(cell, (((slot - 1) % 12) * 50, ((slot - 1) // 12) * 50))
        grid.info["poe2_inventory_aligned"] = True
        return grid

    def scan_inventory(self, image):
        return item_ocr.scan_inventory_grid(image, logger.inventory_icons(), read=lambda _: [])

    def scan_ritual(self, cell, slots=(1,), columns=1, rows=1):
        image = Image.new("RGB", (columns * 50 + 1, rows * 50 + 1), "black")
        image.paste(cell, (1, 1))
        grid = {"rewards": [{"box": (0, 0, image.width, image.height), "slots": list(slots)}]}
        labels = {0: {"count": 1, "count_verified": True}}
        with patch.object(item_ocr, "_ritual_cell_labels", return_value=labels):
            return item_ocr._ritual_grid_items(image, grid, [], [], logger.ritual_names(),
                                             logger.ritual_icons())[0]

    def test_inventory_learning_is_durable_and_shared_with_ritual_without_restart(self):
        example = self.example("Custom Ember")
        logger.save_currency_snapshot("end", [{"name": "Custom Ember", "quantity": 3}],
                                      register_names=True, icon_examples=[example])
        reader = currency_ocr.get_reader()
        grid = self.inventory(example["image"])
        self.assertEqual(self.scan_inventory(grid)["items"][0]["name"], "Custom Ember")
        self.assertIs(currency_ocr.get_reader(), reader)
        reward = self.scan_ritual(example["image"])
        self.assertEqual((reward["name"], reward["category"]), ("Custom Ember", "Item"))
        logger._READY = False
        logger.initialize()
        self.assertEqual(self.scan_inventory(grid)["items"][0]["name"], "Custom Ember")
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Custom Ember": 3})

    def test_omen_learning_is_shared_with_inventory_and_remains_omen_in_ritual(self):
        example = self.example("Omen of Local Testing", "Omen")
        logger.save_ritual_page([{"name": example["name"], "category": "Omen", "quantity": 1}],
                                register_names=True, icon_examples=[example])
        self.assertIn(example["name"], logger.currency_names())
        self.assertIn(example["name"], logger.ritual_names())
        self.assertEqual(self.scan_inventory(self.inventory(example["image"]))["items"][0]["name"],
                         example["name"])
        reward = self.scan_ritual(example["image"])
        self.assertEqual((reward["name"], reward["category"]), (example["name"], "Omen"))

    def test_full_gear_footprint_does_not_teach_a_one_cell_currency(self):
        example = self.example("Local Armour", "Item", (100, 150), columns=2, rows=3)
        logger.save_ritual_page([{"name": "Local Armour", "category": "Item", "quantity": 1}],
                                register_names=True, icon_examples=[example])
        self.assertIn("Local Armour", logger.item_names())
        self.assertNotIn("Local Armour", logger.currency_names())
        reward = self.scan_ritual(example["image"], (1, 2, 13, 14, 25, 26), 2, 3)
        self.assertEqual((reward["name"], reward["quantity"]), ("Local Armour", 1))
        inventory = self.scan_inventory(self.inventory(example["image"].crop((0, 0, 50, 50))))
        self.assertNotIn("Local Armour", [item["name"] for item in inventory["items"]])

    def test_duplicate_and_relabelled_artwork_has_one_active_label(self):
        example = self.example("Old Local Name")
        logger.save_currency_snapshot("end", [{"name": "Old Local Name", "quantity": 1}],
                                      register_names=True, icon_examples=[example, example])
        self.assertEqual(len(logger.review_icons()), 1)
        example = {**example, "name": "Correct Local Name"}
        logger.save_currency_snapshot("end", [{"name": "Correct Local Name", "quantity": 1}],
                                      register_names=True, icon_examples=[example])
        self.assertEqual([row["name"] for row in logger.review_icons()], ["Correct Local Name"])
        self.assertEqual(self.scan_inventory(self.inventory(example["image"]))["items"][0]["name"],
                         "Correct Local Name")
        logger.delete_review_icon(logger.review_icons()[0]["id"])
        self.assertEqual(logger.review_icons(), [])

    def test_unicode_casefold_deduplicates_names_and_totals(self):
        logger.save_currency_snapshot("end", [{"name": "Straße Token", "quantity": 2},
                                              {"name": "STRASSE TOKEN", "quantity": 3}],
                                      register_names=True)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Straße Token": 5})
        self.assertEqual(sum(name.casefold() == "strasse token" for name in logger.currency_names()), 1)

    def test_invalid_examples_roll_back_new_names_and_log(self):
        example = self.example("Uncommitted Icon")
        example["image"] = Image.new("RGB", (50, 50), "black")
        with self.assertRaisesRegex(ValueError, "empty captured"):
            logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                          register_names=True, icon_examples=[example])
        self.assertNotIn(example["name"], logger.currency_names())
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})

    def test_only_accepted_positive_quantity_rows_can_teach_examples(self):
        example = self.example("Not Accepted")
        with self.assertRaisesRegex(ValueError, "Only accepted"):
            logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 1}],
                                          icon_examples=[example])
        logger.save_currency_snapshot("end", [{"name": "Not Accepted", "quantity": 0}],
                                      register_names=True, icon_examples=[example])
        self.assertEqual(logger.review_icons(), [])

    def test_omen_cannot_promote_registered_item_or_multicell_art(self):
        logger.add_item_name("Local Armour")
        with self.assertRaisesRegex(ValueError, "Item database"):
            logger.save_ritual_page([{"name": "LOCAL ARMOUR", "category": "Omen"}], register_names=True)
        example = self.example("Bad Currency Footprint", "Currency", (100, 150), 2, 3)
        with self.assertRaisesRegex(ValueError, "one complete"):
            logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                          register_names=True, icon_examples=[example])
        self.assertNotIn(example["name"], logger.currency_names())

    def test_stale_map_rejection_does_not_register_or_learn(self):
        example = self.example("Stale Example")
        with self.assertRaisesRegex(ValueError, "belongs to"):
            logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                          expected_map_id="M9999", register_names=True, icon_examples=[example])
        self.assertNotIn(example["name"], logger.currency_names())

    def test_make_example_requires_actual_corrected_capture(self):
        image = self.inventory(self.art())
        original = {"slot": 20, "name": "Old Name", "quantity": 1}
        self.assertIsNotNone(review_learning.make_example(image, original, "Correct Name", "Currency", "currency"))
        self.assertIsNone(review_learning.make_example(image, original, "old name", "Currency", "currency"))
        self.assertIsNone(review_learning.make_example(image, {}, "Manual Name", "Currency", "currency"))
        self.assertIsNone(review_learning.make_example(image, {**original, "rejected": True},
                                                       "Correct Name", "Currency", "currency"))
        self.assertIsNone(review_learning.make_example(Image.new("RGB", (600, 250), "black"),
                                                       original, "Correct Name", "Currency", "currency"))
        self.assertIsNone(review_learning.make_example(image, {"box": (0, 0, 20, 20), "grid_slots": [1, 3]},
                                                       "Bad Footprint", "Item", "ritual"))

    def test_manual_names_log_without_teaching_and_bad_control_names_reject(self):
        logger.save_currency_snapshot("end", [{"name": "Manual Name", "quantity": 1}], register_names=True)
        self.assertIn("Manual Name", logger.currency_names())
        self.assertEqual(logger.review_icons(), [])
        for name in ("", "bad\nname", "bad\x00name", "x" * 121):
            with self.assertRaises(ValueError):
                review_learning.validate_name(name, "Currency")

    def test_review_correction_overrides_an_existing_wrong_catalog_match(self):
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
        entry = next(entry for entry in entries if "Omen of Amelioration" in entry["members"])
        cell = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        cell.alpha_composite(Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4)))
        cell = cell.convert("RGB").resize((50, 50))
        self.assertIn("Omen of Amelioration", currency_ocr.get_reader().icon(cell)["members"])
        example = {"name": "Confirmed Custom Artwork", "category": "Currency", "image": cell}
        logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                      register_names=True, icon_examples=[example])
        found = self.scan_inventory(self.inventory(cell))["items"]
        self.assertEqual([row["name"] for row in found], [example["name"]])
        self.assertEqual(self.scan_ritual(cell)["name"], example["name"])

    def test_real_ritual_gear_can_be_labelled_and_recognized_again(self):
        path = Path(__file__).parent / "fixtures" / "ritual_rewards" / "01.png"
        with Image.open(path) as source:
            page = source.convert("RGB")
        grid = ritual_grid.detect_reward_grid(page)
        reward = next(reward for reward in grid["rewards"] if len(reward["slots"]) > 1)
        original = {"name": "", "category": "Item", "box": reward["box"], "grid_slots": reward["slots"]}
        example = review_learning.make_example(page, original, "Reviewed Real Gear", "Item", "ritual")
        self.assertIsNotNone(example)
        logger.save_ritual_page([{"name": example["name"], "category": "Item", "quantity": 1}],
                                register_names=True, icon_examples=[example])
        with patch.object(item_ocr, "_ritual_cell_labels", return_value={}):
            items = item_ocr._ritual_grid_items(page, grid, [], [], logger.ritual_names(), logger.ritual_icons())
        found = next(item for item in items if item["grid_slots"] == reward["slots"])
        self.assertEqual(found["name"], "Reviewed Real Gear")

    def test_real_empty_inventory_art_cannot_be_taught(self):
        path = Path(item_ocr.__file__).resolve().parent.parent / "region_examples" / "inventory.jpg"
        with Image.open(path) as source:
            aligned = item_ocr.inventory_grid(source.convert("RGB").crop((1153, 657, 1804, 937)))
        cell = item_ocr.inventory_cell(aligned, 59)
        self.assertTrue(currency_ocr.get_reader().icon(cell).get("empty"))
        with self.assertRaisesRegex(ValueError, "empty captured"):
            review_learning.encode_example({"image": cell})

    def test_learned_stack_art_is_recognized_after_quantity_changes(self):
        base = self.art()
        one, many = base.copy(), base.copy()
        font = ImageFont.truetype(str(Path(item_ocr.__file__).resolve().parent.parent /
                                     "fonts" / "DejaVuSans.ttf"), 15)
        ImageDraw.Draw(one).text((0, -2), "1", font=font, fill="white", stroke_fill="black", stroke_width=1)
        ImageDraw.Draw(many).text((0, -2), "18", font=font, fill="white", stroke_fill="black", stroke_width=1)
        example = {"name": "Changing Stack", "category": "Currency", "image": one}
        logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                      register_names=True, icon_examples=[example])
        found = self.scan_inventory(self.inventory(many))["items"]
        self.assertEqual([item["name"] for item in found], [example["name"]])

    def test_learning_shared_currency_art_preserves_the_existing_tier_resolver(self):
        example = self.example("Reviewed Shared Icon")
        logger.save_currency_snapshot("end", [{"name": example["name"], "quantity": 1}],
                                      register_names=True, icon_examples=[example])
        grid = self.inventory(example["image"])
        reader = currency_ocr.get_reader()
        icon = {"family": "shared", "members": ["Chaos Orb", "Greater Chaos Orb", "Perfect Chaos Orb"],
                "score": .99}
        with patch.object(reader, "icon", return_value=icon):
            result = item_ocr.scan_inventory_grid(grid, logger.inventory_icons(),
                                                  read=lambda _: [{"text": "III", "score": 1}])
        self.assertEqual([item["name"] for item in result["items"]], ["Perfect Chaos Orb"])
        with patch.object(reader, "icon", return_value=icon):
            reward = self.scan_ritual(example["image"])
        self.assertEqual(reward["name"], "Chaos Orb")


if __name__ == "__main__":
    unittest.main()
