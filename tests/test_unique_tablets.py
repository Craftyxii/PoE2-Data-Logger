"""Unique Tablets: real tooltips, number-free modifiers, affix learning and the logged set."""

import csv
from functools import lru_cache
import io
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, flag_modifier, new_affix_names
from PoE2_Data_Logger.ocr.item_ocr import ocr_lines, parse_tablet
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


FIXTURES = Path(__file__).parent / "fixtures" / "unique_tablets"
MODIFIERS = {
    "freedom_of_faith.png": ["favours at ritual altars in area costs 11 increased tribute",
                             "can reroll favours at ritual altars in your maps twice as many times"],
    "visions_of_paradise.png": ["if map was not previously irradiated completing map adds irradiation instead"],
    "mastered_domain_swamp.png": ["map also counts as a swamp map"],
    "mastered_domain_desert.png": ["map also counts as a desert map"],
}
LEARNED = {
    "Favours at Ritual Altars in Area costs increased Tribute": "%",
    "Can reroll Favours at Ritual Altars in your maps twice as many times": "flag",
    "If Map was not previously irradiated, completing Map adds irradiation instead": "flag",
    "Map also counts as a swamp Map": "flag",
    "Map also counts as a desert Map": "flag",
}
CATALOG = [item["name"] for item in affix_catalog()]


@lru_cache(maxsize=1)
def captures():
    """Run OCR once per capture for every test class."""
    result = {}
    for name in MODIFIERS:
        with Image.open(FIXTURES / name) as source:
            image = source.convert("RGB")
        result[name] = (image, ocr_lines(image))
    return result


def plain(text):
    """Compare OCR wording without case, punctuation or percent signs."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def recolour(image, rows, colour):
    """Repaint the orange ink of the given rows, keeping its brightness."""
    pixels = np.asarray(image).copy()
    for row in rows:
        x, y, right, bottom = (round(row[key]) for key in ("x", "y", "right", "bottom"))
        box = pixels[y:bottom, x:right].astype(float)
        ink = (box.max(axis=2) > 90) & (box[:, :, 0] > box[:, :, 2] * 1.4)
        box[ink] = box[ink][:, :1] * np.array(colour, dtype=float)
        pixels[y:bottom, x:right] = np.clip(box, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels)


class UniqueTabletCaptureTests(unittest.TestCase):
    """Read the four captured unique Tablets with the bundled catalog only."""

    @classmethod
    def setUpClass(cls):
        """Share the captures' OCR rows."""
        cls.captures = captures()

    def read(self, name, image=None):
        """Read one capture, optionally with repainted pixels."""
        original, rows = self.captures[name]
        return item_text.read_screen_tooltip(image or original, CATALOG,
                                             ocr_rows=[dict(row) for row in rows])

    def test_every_unique_modifier_is_read_between_usage_and_flavour_text(self):
        """Wrapped endings rejoin; title, implicit, flavour and footer text never become modifiers."""
        for name, expected in MODIFIERS.items():
            with self.subTest(name=name):
                result = self.read(name)
                self.assertEqual((result["kind"], result["unique"], result["bounded"]), ("tablet", True, True))
                self.assertEqual([plain(text) for text in result["mods"]], expected)
                self.assertEqual(result["status"], "review")

    def test_grey_footer_and_orange_text_are_not_retried(self):
        """Low-confidence footer and flavour rows hold no rolls, so no slow upscaled retry runs."""
        self.assertTrue(any(row["score"] < .9 for row in self.captures["visions_of_paradise.png"][1]))
        with patch.object(item_ocr, "ocr_lines", side_effect=AssertionError("retried a rollless row")):
            for name in MODIFIERS:
                with self.subTest(name=name):
                    self.assertTrue(self.read(name)["mods"])

    def test_number_free_modifiers_become_flags_once_learned(self):
        """Learned names match their own readings; the % roll keeps its value."""
        for name in MODIFIERS:
            with self.subTest(name=name):
                result = self.read(name)
                matches = parse_tablet(result["ocr_rows"], [*CATALOG, *LEARNED], flags=True)
                self.assertEqual(matches["status"], "ready")
                self.assertEqual([(LEARNED[item["affix"]], item["value"]) for item in matches["matches"]],
                                 [("%", 11) if "%" in raw else ("flag", 1) for raw in result["mods"]])

    def test_rare_coloured_title_keeps_the_existing_reader(self):
        """Yellow rare titles are not unique: number-free text is neither read nor learned."""
        image, rows = self.captures["mastered_domain_desert.png"]
        titles = [row for row in rows if "MASTERED" in row["text"] or "IRRADIATED TABLET" in row["text"]]
        self.assertIsNone(self.read("mastered_domain_desert.png", recolour(image, titles, (1, 1, .55))))
        image, rows = self.captures["freedom_of_faith.png"]
        titles = [row for row in rows if row["text"] in ("FREEDOM OF FAITH", "RITUAL TABLET")]
        result = self.read("freedom_of_faith.png", recolour(image, titles, (1, 1, .55)))
        self.assertNotIn("unique", result)
        self.assertEqual([plain(text) for text in result["mods"]], MODIFIERS["freedom_of_faith.png"][:1])

    def test_missing_flavour_boundary_holds_review_without_flags(self):
        """A unique title without its flavour boundary never reads flags or auto-saves."""
        image, rows = self.captures["freedom_of_faith.png"]
        flavour = [row for row in rows if "PRAY" in row["text"] or "DARKNESS" in row["text"]]
        self.assertEqual(len(flavour), 3)
        result = self.read("freedom_of_faith.png", recolour(image, flavour, (.6, .6, 1.2)))
        self.assertEqual((result["unique"], result["bounded"], result["status"]), (True, False, "review"))
        self.assertEqual([plain(text) for text in result["mods"]], MODIFIERS["freedom_of_faith.png"][:1])


class UniqueTabletTextTests(unittest.TestCase):
    """Bound copied unique text and keep value-bearing wording out of flags."""

    COPIED = "\n".join(["Item Class: Tablet", "Rarity: Unique", "Freedom of Faith", "Ritual Tablet",
                        "--------", "Uses Remaining: 5", "--------", "Item Level: 80", "--------",
                        "Adds Ritual Altars to a Map (implicit)", "--------",
                        "{ Unique Modifier }",
                        "Favours at Ritual Altars in Area costs 11(10-20)% increased Tribute",
                        "{ Unique Modifier }",
                        "Can Reroll Favours at Ritual Altars in your Maps twice as many times",
                        "--------", "When darkness comes, we pray for light.",
                        "When darkness is all that there is,", "we pray for death.", "--------",
                        "Can be used in a Personal Map Device to add modifiers to a Map."])

    def test_copied_unique_text_reads_modifiers_but_not_flavour(self):
        """Labelled and unlabelled copies both stop before the flavour section."""
        for text in (self.COPIED, self.COPIED.replace("{ Unique Modifier }\n", "")):
            with self.subTest(labelled="{" in text):
                result = item_text.parse_item_text(text, CATALOG)
                self.assertEqual((result["unique"], result["bounded"]), (True, True))
                self.assertEqual(result["mods"],
                                 ["Favours at Ritual Altars in Area costs 11(10-20)% increased Tribute",
                                  "Can Reroll Favours at Ritual Altars in your Maps twice as many times"])
                self.assertEqual(new_affix_names(result["uncertain"], CATALOG, flags=True), [
                    "Favours at Ritual Altars in Area costs increased Tribute",
                    "Can Reroll Favours at Ritual Altars in your Maps twice as many times"])

    def test_copied_unique_without_a_flavour_section_stays_unbounded(self):
        """One body section cannot prove which lines are modifiers."""
        text = "\n".join(line for line in self.COPIED.split("\n")[:-6] if "{" not in line)
        result = item_text.parse_item_text(text + "\n--------\nCan be used in a Personal Map Device.", CATALOG)
        self.assertEqual((result["unique"], result["bounded"]), (True, False))

    def test_value_wording_and_usage_prose_are_never_flags(self):
        """A dropped roll, usage text and short fragments stay out of the flag reader."""
        for text in ("Favours at Ritual Altars in Area costs increased Tribute",
                     "Map has additional Rare Monsters", "Adds Irradiated to a Map", "1 use remaining",
                     "Can be used in a Personal Map Device to add", "MODIFIERS TO A MAP.", "TIMES", "Tablet"):
            with self.subTest(text=text):
                self.assertIsNone(flag_modifier(text))
        result = parse_tablet(["FAVOURS AT RITUAL ALTARS IN AREA COSTS INCREASED TRIBUTE"],
                              [*CATALOG, *LEARNED], flags=True)
        self.assertEqual((result["matches"], result["status"]), ([], "review"))


class UniqueTabletLoggingTests(unittest.TestCase):
    """Scan a mixed set of normal and unique Tablets through the desktop into exports."""

    @classmethod
    def setUpClass(cls):
        """Reuse Qt and read every capture once."""
        cls.app = QApplication.instance() or QApplication([])
        cls.captures = captures()

    def setUp(self):
        """Create an isolated profile with automatic tablet saving."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-unique-tablets-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.set_auto_all(True)
        self.window.show()
        output = io.BytesIO()
        Image.new("RGB", (40, 30), "tan").save(output, format="PNG")
        self.raw = output.getvalue()
        self.app.processEvents()

    def tearDown(self):
        """Close workers before restoring the caller's data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def scan(self, name):
        """Deliver one capture the way a tablet hotkey reading arrives."""
        image, rows = self.captures[name]
        result = item_text.read_screen_tooltip(image, self.window.state["affixes"],
                                               ocr_rows=[dict(row) for row in rows])
        self.window._hover_item_read({**result, **logger.scan_context()}, self.raw)
        self.app.processEvents()
        return self.window.tablet_scan_status.text()

    def test_mixed_normal_and_unique_tablets_learn_save_and_export(self):
        """Unknown unique wording joins Affix DB once; every slot logs its value and unit."""
        before = set(logger.get_state()["affixes"])
        normal = item_text.read_screen_tooltip(
            Image.new("RGB", (400, 300)), self.window.state["affixes"],
            ocr_rows=[{"text": text, "score": 1} for text in
                      ("Precursor Tablet", "Uses Remaining: 10", "25% increased Effectiveness")])
        self.window._hover_item_read({**normal, **logger.scan_context()}, self.raw)
        self.assertIn("Tablet 1 saved with 1 affixes.", self.window.tablet_scan_status.text())
        self.assertIn("Tablet 2 saved with 2 affixes. Added 2 new affix name(s).",
                      self.scan("freedom_of_faith.png"))
        self.assertIn("Tablet 3 saved with 1 affixes. Added 1 new affix name(s).",
                      self.scan("visions_of_paradise.png"))
        self.assertIn("Tablet 4 saved with 1 affixes. Added 1 new affix name(s).",
                      self.scan("mastered_domain_swamp.png"))
        state = logger.get_state()
        self.assertEqual(set(state["affixes"]) - before, set(LEARNED) - {"Map also counts as a desert Map"})
        self.assertIsNone(self.window.pending_review_kind)
        saved = [pair for pair in state["settings"]["tablet_affixes"] if pair["affix"]]
        self.assertEqual([(pair["affix"], pair["value"], pair.get("unit", "%")) for pair in saved], [
            ("Effectiveness", 25, "%"),
            ("Favours at Ritual Altars in Area costs increased Tribute", 11, "%"),
            ("Can reroll Favours at Ritual Altars in your maps twice as many times", 1, "flag"),
            ("If Map was not previously irradiated, completing Map adds irradiation instead", 1, "flag"),
            ("Map also counts as a swamp Map", 1, "flag")])
        self.assertEqual([len(mods) for mods in state["settings"]["tablet_raw_mods"]], [1, 2, 1, 1])
        number = logger.record_commit("Map settings")
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        row = next(row for row in rows if row["Scan Commit #"] == str(number))
        self.assertEqual((row["Tablets Used"], row["Tablet 2 Mod 1 %"], row["Tablet 2 Mod 1 Unit"]), ("4", "11", "%"))
        for tablet, slot, affix in ((2, 2, saved[2]["affix"]), (3, 1, saved[3]["affix"]), (4, 1, saved[4]["affix"])):
            prefix = f"Tablet {tablet} Mod {slot}"
            self.assertEqual((row[prefix + " Affix"], row[prefix + " Value"], row[prefix + " Unit"],
                              row[prefix + " %"]), (affix, "1", "flag", ""))

    def test_a_known_unique_rescans_without_learning_again(self):
        """The second reading of the same unique matches its learned names and saves directly."""
        self.scan("mastered_domain_desert.png")
        self.window.clear_tablets()
        affixes = list(logger.get_state()["affixes"])
        self.assertEqual(self.scan("mastered_domain_desert.png"), "Tablet 1 saved with 1 affixes.")
        self.assertEqual(logger.get_state()["affixes"], affixes)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0],
                         {"affix": "Map also counts as a desert Map", "value": 1, "unit": "flag"})

    def test_hand_picked_learned_flag_keeps_its_unit_and_undo_forgets_it(self):
        """A learned flag chosen in the form saves as a flag; undoing its scan's parent removes both."""
        logger.record_commit("Map settings")
        self.scan("mastered_domain_desert.png")
        name = "Map also counts as a desert Map"
        self.assertEqual(logger.get_affix_units(), {name: "flag"})
        select(self.window.tablets_used, 2)
        select(self.window.tablet_affixes[4], name)
        self.window.tablet_values[4].setText("1")
        self.window.save_tablets()
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][4], {"affix": name, "value": 1, "unit": "flag"})
        for _ in range(3):
            logger.undo_last_action()
        self.assertNotIn(name, logger.get_state()["affixes"])
        self.assertEqual(logger.get_affix_units(), {})


if __name__ == "__main__":
    unittest.main()
