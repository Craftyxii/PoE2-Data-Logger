"""Capture/composite propagation checks for cursor rows, three-marked positions, cropped layouts and uncertain review holds."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageEnhance

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr


ROOT = Path(__file__).resolve().parents[1]


class PropagationOCRTests(unittest.TestCase):
    """Exercise propagation recipe, cursor and crown recognition across synthetic and captured panels."""
    def setUp(self):
        """Create an isolated database and load the bundled opened-panel image and rune glyph."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.source = runehelper_ocr.default_frame(Image.open(
            ROOT / "PoE2_Data_Logger/region_examples/opened.jpg").convert("RGB"))
        self.glyph = self.source.crop((57, 73, 86, 102))

    def tearDown(self):
        """Restore the logger data directory and remove temporary scan storage."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def cursor(self, image, y):
        """Draw a gold selection cursor at the requested vertical position."""
        draw = ImageDraw.Draw(image)
        draw.polygon([(1, y), (15, y - 12), (37, y), (15, y + 12)], fill=(242, 209, 124))
        draw.polygon([(5, y), (15, y - 8), (31, y), (15, y + 8)],
                     outline=(215, 180, 90), width=2)

    def panel(self, recipes, selected=0, marks=None, scale=1):
        """Render database-backed reward rows with rune tiles, optional crowns and a selected cursor."""
        image = Image.new("RGB", (575, 720), (176, 161, 130))
        draw = ImageDraw.Draw(image)
        rows = []
        with logger._connect() as db:
            for row_index, recipe in enumerate(recipes):
                y = 86 + row_index * 81
                sockets = db.execute("SELECT sockets FROM recipes WHERE name=?", (recipe,)).fetchone()[0]
                for position in range(1, sockets + 1):
                    cx = 71 + 41 * (position - 1)
                    draw.rectangle((cx - 19, y - 18, cx + 18, y + 18), fill=(194, 178, 146),
                                   outline=(92, 63, 43), width=2)
                    image.paste(self.glyph, (cx - 14, y - 14))
                    if position in (marks or {}).get(row_index, []):
                        draw.rectangle((cx - 19, y - 18, cx + 18, y + 18),
                                       outline=(242, 213, 144), width=2)
                        for offset in (-9, 0, 9):
                            draw.polygon([(cx + offset, y - 23), (cx + offset - 2, y - 19),
                                          (cx + offset + 2, y - 19)], fill=(242, 213, 144))
                quantity = propagation_scan.opened_scan._quantity(recipe)
                name = recipe.rsplit(" x", 1)[0] if quantity > 1 else recipe
                text = f"{quantity}x {name}" if not name.startswith("Unique ") else name
                rows.append({"text": text, "score": .99, "x1": 230, "x2": 540,
                             "y1": y + 23, "y2": y + 51})
        if selected is not None:
            self.cursor(image, 86 + selected * 81)
        if scale != 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)),
                                 Image.Resampling.LANCZOS)
        return image, rows

    def scan(self, image, rows):
        """Scan a panel while supplying controlled OCR reward rows and title geometry."""
        title = {"text": "Runeshape Combinations", "score": .99, "x1": 185, "x2": 390,
                 "y1": 32, "y2": 61}
        with patch.object(propagation_scan, "_read_panel_rows", return_value=(rows, title)):
            return propagation_scan.scan_propagation(image)

    def test_cursor_selects_only_its_row_even_when_every_row_has_marks(self):
        """Verify the cursor selects only its row even when other rows also contain crown marks."""
        recipes = ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"]
        image, rows = self.panel(recipes, 1, {0: [3], 1: [2], 2: [3]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Arcane"])
        self.assertEqual(result["positions"], [2])

    def test_two_marks_preserve_left_to_right_order(self):
        """Verify two marked runes are returned in their displayed left-to-right order."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [5, 1]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Rage", "Time"])
        self.assertEqual(result["positions"], [1, 5])

    def test_adjacent_marks(self):
        """Verify adjacent crown marks select the two corresponding rune positions."""
        image, rows = self.panel(["Divine Orb x2"], marks={0: [2, 3]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Soul", "Power"])

    def test_repeated_rune_uses_marked_position_only(self):
        """Verify repeated rune names resolve only from the marked tile position."""
        image, rows = self.panel(["Swift Alloy"], marks={0: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Rebirth"])
        self.assertEqual(result["positions"], [4])

    def test_long_single_recipe_does_not_need_unique_family(self):
        """Verify a long recipe resolves without requiring a unique-family reward."""
        image, rows = self.panel(["Perfect Exalted Orb x3"], marks={0: [7]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Power"])

    def test_crop_with_partial_family_list(self):
        """Verify a cropped partial reward list still resolves the selected recipe's marked rune."""
        image, rows = self.panel(["Greater Exalted Orb", "Greater Regal Orb"], 0, {0: [4], 1: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Prismatic"])

    def test_quantity_is_preserved(self):
        """Verify recipe quantity selects the matching rune sequence."""
        image, rows = self.panel(["Greater Exalted Orb x3"], marks={0: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Greater Exalted Orb x3")
        self.assertEqual(result["runes"], ["Electrocuting"])

    def test_explicit_level_is_preserved(self):
        """Verify an explicit recipe level selects the matching level-specific rune sequence."""
        image, rows = self.panel(["Thaumaturgic Flux (Level 18)"], marks={0: [2]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Thaumaturgic Flux (Level 18)")
        self.assertEqual(result["runes"], ["Ward"])

    def test_missing_level_cannot_use_a_different_level_recipe(self):
        """Verify missing level text cannot silently select a level-specific recipe."""
        image, rows = self.panel(["Thaumaturgic Flux (Level 18)"], marks={0: [2]})
        rows[0]["text"] = "1x Thaumaturgic Flux"
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_missing_cursor_offers_single_recipe_for_explicit_approval(self):
        """Verify a cursorless single recipe remains a validated choice requiring explicit approval."""
        image, rows = self.panel(["Medved's Saga"], None, {0: [1]})
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["selected_recipe"], None)
        self.assertIn("approve", result["status"])
        self.assertEqual(len(result["choices"]), 1)
        self.assertTrue(result["choices"][0]["can_use"])
        self.assertEqual(result["choices"][0]["runes"], ["Rage"])

    def test_crop_that_omits_outside_cursor_keeps_recipe_and_rune_choice(self):
        """Verify cropping away the cursor preserves a usable recipe choice without automatic acceptance."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
        result = self.scan(image.crop((46, 0, image.width, image.height)), rows)
        self.assertFalse(result["can_use"])
        self.assertIsNone(result["selected_recipe"])
        self.assertEqual(result["runes"], [])
        self.assertEqual(result["choices"][0]["selected_recipe"], "Medved's Saga")
        self.assertTrue(result["choices"][0]["can_use"], result)
        self.assertEqual(result["choices"][0]["runes"], ["Rage"])

    def test_no_arrow_multiple_marked_rows_keep_each_recipe_in_display_order(self):
        """Verify cursorless marked rows remain separate approval choices in display order."""
        image, rows = self.panel(["Medved's Saga", "Greater Regal Orb x3"], None,
                                 {0: [1, 5], 1: [1, 5]})
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])
        self.assertEqual([choice["selected_recipe"] for choice in result["choices"]],
                         ["Medved's Saga", "Greater Regal Orb x3"])
        self.assertTrue(all(choice["can_use"] for choice in result["choices"]), result)
        self.assertEqual(result["choices"][0]["runes"], ["Rage", "Time"])

    def test_no_arrow_unclear_marks_still_list_recipe_for_manual_entry(self):
        """Verify unclear marks keep the recipe available for manual rune entry."""
        image, rows = self.panel(["Medved's Saga"], None, {0: []})
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(len(result["choices"]), 1)
        self.assertEqual(result["choices"][0]["selected_recipe"], "Medved's Saga")
        self.assertFalse(result["choices"][0]["can_use"])
        self.assertEqual(result["choices"][0]["runes"], [])
        self.assertIn("manually", result["choices"][0]["status"])

    def test_multiple_cursors_are_ambiguous(self):
        """Verify multiple visible cursors prevent automatic rune selection."""
        image, rows = self.panel(["Medved's Saga", "Greater Regal Orb x3"], 0, {0: [1]})
        self.cursor(image, 167)
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_no_marks_and_three_marks_are_not_accepted(self):
        """Verify zero or three marked rune positions cannot be automatically accepted."""
        for marks in ([], [1, 2, 3]):
            image, rows = self.panel(["Medved's Saga"], marks={0: marks})
            result = self.scan(image, rows)
            self.assertFalse(result["can_use"], result)
            self.assertEqual(result["runes"], [])

    def test_gold_highlight_without_three_peak_marker_is_not_a_mark(self):
        """Verify a gold frame without a three-peak crown is not accepted as a mark."""
        image, rows = self.panel(["Medved's Saga"], marks={0: []})
        ImageDraw.Draw(image).rectangle((52, 68, 89, 104), outline=(242, 213, 144), width=2)
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_one_or_two_crown_peaks_are_not_a_mark(self):
        """Verify one- or two-peak crowns remain unusable for automatic or choice approval."""
        for offsets in ((0,), (-9, 9)):
            image, rows = self.panel(["Medved's Saga"], marks={0: []})
            draw = ImageDraw.Draw(image)
            draw.rectangle((52, 68, 89, 104), outline=(242, 213, 144), width=2)
            for offset in offsets:
                draw.polygon([(71 + offset, 63), (69 + offset, 67), (73 + offset, 67)],
                             fill=(242, 213, 144))
            result = self.scan(image, rows)
            self.assertFalse(result["can_use"], result)
            self.assertFalse(result["choices"][0]["can_use"], result)

    def test_unrelated_visible_recipe_does_not_block_the_selected_recipe(self):
        """Verify an unrelated visible recipe does not invalidate the selected marked recipe."""
        image, rows = self.panel(["Medved's Saga", "Divine Orb"], marks={0: [1]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Medved's Saga")
        self.assertEqual(result["runes"], ["Rage"])
        self.assertFalse(result["choices"][1]["can_use"])

    def test_unreadable_other_reward_does_not_block_clear_cursor_recipe(self):
        """Verify an unreadable unselected reward does not block a clear cursor recipe."""
        image, rows = self.panel(["Medved's Saga", "Greater Regal Orb x3"], marks={0: [1]})
        rows[1]["text"] = "1x Unreadable ?? reward"
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Medved's Saga")
        self.assertEqual(result["runes"], ["Rage"])
        self.assertFalse(result["choices"][1]["can_use"])

    def test_visible_socket_count_cannot_be_overridden_by_a_different_quantity_recipe(self):
        """Verify visible socket geometry prevents quantity text from selecting an incompatible recipe."""
        for recipes, selected in ((["Greater Exalted Orb x3"], 0),
                                   (["Medved's Saga", "Greater Exalted Orb x3", "Greater Regal Orb x3"], 1)):
            with self.subTest(shared_geometry=len(recipes) > 1):
                image, rows = self.panel(recipes, selected, {index: [4] for index in range(len(recipes))})
                rows[selected]["text"] = "1x Greater Exalted Orb"
                result = self.scan(image, rows)
                self.assertFalse(result["can_use"], result)
                self.assertEqual(result["selected_recipe"], "Greater Exalted Orb")
                self.assertEqual(result["runes"], [])

    def test_selected_reward_low_confidence_is_not_accepted(self):
        """Verify low-confidence selected reward text prevents automatic acceptance."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
        rows[0]["score"] = .7
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_screen_scale_changes_preserve_order(self):
        """Verify resizing the panel preserves marked rune ordering."""
        for scale in (.7, 1.5, 2):
            image, rows = self.panel(["Gemcutter's Prism x2"], marks={0: [1, 2]}, scale=scale)
            result = self.scan(image, rows)
            self.assertTrue(result["can_use"], (scale, result))
            self.assertEqual(result["runes"], ["Prismatic", "Celestial"])

    def test_short_wide_panel_preserves_reward_text_and_marked_positions_at_multiple_scales(self):
        """Verify short wide panels retain selected rewards and tile positions across scales."""
        recipes = ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"]
        for scale in (.7, 1, 1.5, 2, 3):
            with self.subTest(scale=scale):
                image, rows = self.panel(recipes, 1, {0: [3], 1: [2], 2: [3]})
                image = image.crop((0, 0, 575, 300)).resize((round(575 * scale), round(300 * scale)),
                                                        Image.Resampling.LANCZOS)
                prepared = propagation_scan._panel(image)
                self.assertEqual(prepared.size, (575, 300))
                result = self.scan(image, rows)
                self.assertTrue(result["can_use"], result)
                self.assertEqual(result["selected_recipe"], "Regal Orb x3")
                self.assertEqual(result["runes"], ["Arcane"])
                self.assertEqual(result["positions"], [2])

    def test_short_wide_panel_without_cursor_offers_validated_choice(self):
        """Verify a short cursorless panel exposes a validated choice for explicit approval."""
        image, rows = self.panel(["Medved's Saga"], selected=None, marks={0: [1, 5]})
        result = self.scan(image.crop((0, 0, 575, 300)), rows)
        self.assertFalse(result["can_use"])
        self.assertIsNone(result["selected_recipe"])
        self.assertEqual(result["runes"], [])
        self.assertEqual(result["choices"][0]["runes"], ["Rage", "Time"])
        self.assertTrue(result["choices"][0]["can_use"])

    def test_short_wide_geometry_does_not_bypass_minimum_capture_size(self):
        """Verify short-panel normalization still rejects captures below the minimum size."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [1, 5]})
        result = self.scan(image.crop((0, 0, 575, 300)).resize((115, 60)), rows)
        self.assertFalse(result["can_use"])
        self.assertIn("too small", result["status"])
        self.assertEqual(result["runes"], [])

    def test_scan_does_not_create_remnants_or_commit_records(self):
        """Verify propagation recognition never writes remnant or commit records."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [1, 5]})
        with logger._connect() as db:
            before = list(db.iterdump())
        self.assertTrue(self.scan(image, rows)["can_use"])
        with logger._connect() as db:
            after = list(db.iterdump())
        self.assertEqual(before, after)

    def test_real_bundled_ocr_and_marks_with_controlled_cursor(self):
        """Verify bundled OCR and captured crowns resolve the controlled cursor's Regal Orb reward."""
        self.cursor(self.source, 167)
        result = propagation_scan.scan_propagation(self.source)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Tidal"])

    def scaled_content(self, scale, brightness):
        # Scale the actual panel inside a larger capture, preserving the
        # original crown and glyph pixels rather than drawing new markers.
        """Resize captured panel content, adjust brightness and draw a cursor at its scaled reward row."""
        image = Image.new("RGB", self.source.size, (176, 161, 130))
        image.paste(self.source.resize((round(self.source.width * scale), round(self.source.height * scale)),
                                       Image.Resampling.LANCZOS))
        image = ImageEnhance.Brightness(image).enhance(brightness)
        y = round(167 * scale)
        ImageDraw.Draw(image).polygon([(1, y), (15, y - 10), (35, y), (15, y + 10)],
                                     fill=(242, 209, 124))
        return image

    def test_real_expanded_crown_does_not_shift_the_third_rune_to_the_second(self):
        """Verify an expanded captured crown remains at the third rune position."""
        result = propagation_scan.scan_propagation(self.scaled_content(.72, 1.2))
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["positions"], [3])
        self.assertEqual(result["runes"], ["Tidal"])
        self.assertEqual([choice["positions"] for choice in result["choices"]], [[3], [3], [3]])

    def test_real_crown_with_unproven_first_tile_holds_instead_of_saving_the_second_rune(self):
        """Verify uncertain first-tile geometry holds a captured crown without inventing a rune index."""
        for scale, brightness in ((.74, .9), (.74, 1.1), (.75, 1.2)):
            with self.subTest(scale=scale, brightness=brightness):
                result = propagation_scan.scan_propagation(self.scaled_content(scale, brightness))
                self.assertEqual(result["selected_recipe"], "Regal Orb x3", result)
                self.assertFalse(result["can_use"], result)
                self.assertEqual(result["positions"], [])
                self.assertEqual(result["runes"], [])
                self.assertIn("first rune position", result["status"])

    def test_second_reward_alchemy_three_crown_selects_tidal_at_the_third_position(self):
        """Verify the second Alchemy reward selects Tidal from its third marked tile."""
        recipes = ["Cyclonic Alloy", "Orb of Alchemy x3", "Glassblower's Bauble x3",
                   "Expansive Alloy", "Glassblower's Bauble x2", "Regal Orb x3", "Exalted Orb x2"]
        image, rows = self.panel(recipes, selected=1, marks={index: [3] for index in range(len(recipes))})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Orb of Alchemy x3")
        self.assertEqual(result["positions"], [3])
        self.assertEqual(result["runes"], ["Tidal"])

    def test_real_bundled_ocr_reads_short_wide_panel_with_controlled_cursor(self):
        """Verify bundled OCR reads the selected reward from a short wide captured panel."""
        self.cursor(self.source, 167)
        image = self.source.crop((0, 0, self.source.width, 300))
        result = propagation_scan.scan_propagation(image)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Tidal"])

    def test_full_screen_panel_search_still_keeps_controlled_cursor_and_reward(self):
        """Verify full-screen panel search preserves the controlled cursor and selected reward."""
        with Image.open(ROOT / "PoE2_Data_Logger/region_examples/opened.jpg") as source:
            image = source.convert("RGB")
        window = image.crop((0, 0, round(image.height * .70), image.height))
        gray = propagation_scan.cv2.cvtColor(propagation_scan.np.asarray(window),
                                            propagation_scan.cv2.COLOR_RGB2GRAY)
        _, top, _, _ = runehelper_ocr._find_panel(gray)
        self.cursor(image, top + 167)
        result = propagation_scan.scan_propagation(image)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Tidal"])

    def test_real_screenshot_without_cursor_reads_all_recipes_for_explicit_approval(self):
        """Verify a captured cursorless panel lists every validated recipe for explicit approval."""
        result = propagation_scan.scan_propagation(self.source)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])
        self.assertEqual([choice["selected_recipe"] for choice in result["choices"]],
                         ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"])
        self.assertEqual([choice["runes"] for choice in result["choices"]],
                         [["Cyclonic"], ["Tidal"], ["Tidal"]])
        self.assertTrue(all(choice["can_use"] for choice in result["choices"]), result)

    def test_real_tight_crop_preserves_header_and_adjusts_rune_positions(self):
        """Verify tight left crops retain the header and correct absolute rune positions."""
        for left in (20, 35, 46):
            with self.subTest(left=left):
                image = self.source.crop((left, 0, self.source.width, self.source.height))
                result = propagation_scan.scan_propagation(image)
                self.assertFalse(result["can_use"], result)
                self.assertEqual(len(result["choices"]), 3, result)
                self.assertEqual(result["choices"][0]["runes"], ["Cyclonic"])
                self.assertEqual(result["choices"][1]["runes"], ["Tidal"])
                self.assertEqual(result["choices"][0]["positions"], [3])
                self.assertEqual(result["choices"][1]["positions"], [3])
                self.assertTrue(result["choices"][0]["can_use"], result)
                self.assertTrue(result["choices"][1]["can_use"], result)

    def test_real_crowns_survive_exposure_and_tight_crops_on_every_row(self):
        """Verify captured crown positions survive exposure changes and left crops on every reward row."""
        for left in (0, 20, 35, 46):
            for brightness in (.85, 1, 1.15):
                with self.subTest(left=left, brightness=brightness):
                    image = self.source.crop((left, 0, self.source.width, self.source.height))
                    result = propagation_scan.scan_propagation(
                        ImageEnhance.Brightness(image).enhance(brightness))
                    self.assertFalse(result["can_use"], result)
                    self.assertEqual([choice["runes"] for choice in result["choices"]],
                                     [["Cyclonic"], ["Tidal"], ["Tidal"]], result)
                    self.assertEqual([choice["positions"] for choice in result["choices"]],
                                     [[3], [3], [3]], result)

    def remove_gold_frame(self, image, centre_x, top, width=38, height=38):
        """Erase a tile's gold frame while retaining its crown evidence."""
        left, right = round(centre_x - width / 2), round(centre_x + width / 2) - 1
        draw = ImageDraw.Draw(image)
        for box in ((left, top, right, top + 2),
                    (left, top, left + 3, top + height - 1),
                    (right - 3, top, right, top + height - 1),
                    (left, top + height - 4, right, top + height - 1)):
            draw.rectangle(box, fill=(176, 161, 130))

    def test_frameless_crown_with_erased_predecessors_cannot_assume_the_first_tile_position(self):
        """Verify erasing preceding tiles leaves a frameless crown's rune position unproven."""
        image, rows = self.panel(["Greater Exalted Orb x3"], marks={0: [4]})
        self.remove_gold_frame(image, 71 + 41 * 3, 68)
        # A crown proves propagation, but erasing every preceding tile makes
        # its index ambiguous. The database cannot supply the image origin.
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 68, 173, 105), fill=(176, 161, 130))
        self.assertEqual(propagation_scan._marked_boxes(propagation_scan._gold_mask(image))[0], [])
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"], result)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["runes"], [])
        self.assertIn("first rune position", result["status"])

    def test_frameless_marks_without_arrow_keep_explicit_recipe_approval(self):
        """Verify frameless crowns without a cursor retain explicit recipe approval."""
        image, rows = self.panel(["Medved's Saga"], selected=None, marks={0: [1, 5]})
        for position in (1, 5):
            self.remove_gold_frame(image, 71 + 41 * (position - 1), 68)
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])
        self.assertTrue(result["choices"][0]["can_use"], result)
        self.assertEqual(result["choices"][0]["runes"], ["Rage", "Time"])

    def test_frameless_one_or_two_marks_hold_for_manual_entry(self):
        """Verify frameless one- and two-peak crown fragments require manual entry."""
        for offsets in ((0,), (-9, 9)):
            with self.subTest(offsets=offsets):
                image, rows = self.panel(["Medved's Saga"], marks={0: []})
                draw = ImageDraw.Draw(image)
                for offset in offsets:
                    draw.polygon(((71 + offset, 63), (69 + offset, 67), (73 + offset, 67)),
                                 fill=(242, 213, 144))
                result = self.scan(image, rows)
                self.assertFalse(result["can_use"], result)
                self.assertFalse(result["choices"][0]["can_use"], result)

    def test_clear_frameless_crown_cannot_hide_an_incomplete_second_crown(self):
        """Verify a complete frameless crown cannot mask a second incomplete crown."""
        for offsets in ((0,), (-9, 9)):
            with self.subTest(offsets=offsets):
                image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
                self.remove_gold_frame(image, 71, 68)
                centre = 71 + 41 * 4
                draw = ImageDraw.Draw(image)
                for offset in offsets:
                    draw.polygon(((centre + offset, 63), (centre + offset - 2, 67),
                                  (centre + offset + 2, 67)), fill=(242, 213, 144))
                self.assertEqual(propagation_scan._marked_boxes(propagation_scan._gold_mask(image))[0], [])
                result = self.scan(image, rows)
                self.assertFalse(result["can_use"], result)
                self.assertFalse(result["choices"][0]["can_use"], result)
                self.assertEqual(result["runes"], [])

    def test_isolated_gold_glint_does_not_block_a_clear_frameless_crown(self):
        """Verify an isolated gold glint does not invalidate a complete frameless crown."""
        image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
        self.remove_gold_frame(image, 71, 68)
        centre = 71 + 41 * 4
        ImageDraw.Draw(image).line((centre - 9, 67, centre - 8, 67), fill=(242, 213, 144))
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Rage"])

    def test_real_frameless_short_panel_preserves_header_recipe_cursor_and_three_marks(self):
        """Verify a short captured panel with erased frames retains cursor, recipe and crown positions."""
        image = self.source.copy()
        for top in (68, 149, 230):
            self.remove_gold_frame(image, 153, top)
        self.assertEqual(propagation_scan._marked_boxes(propagation_scan._gold_mask(image))[0], [])
        self.cursor(image, 167)
        result = propagation_scan.scan_propagation(image.crop((0, 0, image.width, 300)))
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["positions"], [3])
        self.assertEqual(result["runes"], ["Tidal"])

    def test_partial_second_crown_cannot_be_hidden_by_a_clear_first_mark(self):
        """Verify a partial second crown prevents acceptance despite a clear first mark across exposures."""
        for brightness in (.85, 1, 1.15):
            image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
            draw = ImageDraw.Draw(image)
            cx, y = 71 + 41 * 4, 86
            draw.rectangle((cx - 19, y - 18, cx + 18, y + 18),
                           outline=(242, 213, 144), width=2)
            for offset in (-9, 9):
                draw.polygon([(cx + offset, y - 23), (cx + offset - 2, y - 19),
                              (cx + offset + 2, y - 19)], fill=(242, 213, 144))
            result = self.scan(ImageEnhance.Brightness(image).enhance(brightness), rows)
            self.assertFalse(result["can_use"], result)
            self.assertFalse(result["choices"][0]["can_use"], result)


if __name__ == "__main__":
    unittest.main()
