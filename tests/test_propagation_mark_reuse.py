"""Reuse crown evidence only for the accepted, unchanged propagation capture."""

import hashlib
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.ocr import propagation_scan
from tests import test_propagation_ocr as fixtures


class PropagationMarkReuseTests(unittest.TestCase):
    """Guard accepted candidates, reframing, capture isolation and review holds."""

    def setUp(self):
        """Use the existing isolated recipe database and synthetic glyph fixture."""
        self.fixture = fixtures.PropagationOCRTests()
        self.fixture.setUp()

    def tearDown(self):
        """Remove the isolated database without changing user data."""
        self.fixture.tearDown()

    def test_accepted_crown_panel_is_read_once_and_preserves_source(self):
        """One crown still fills its family while its identical pixels are read once."""
        image, rows = self.fixture.family_panel({0: [2]})
        pixels = image.tobytes()
        info = dict(image.info)
        with patch.object(propagation_scan, "_marked_image", wraps=propagation_scan._marked_image) as marks:
            result = self.fixture.scan(image, rows)
        self.fixture.assert_group_slots(result, [2])
        self.assertEqual(marks.call_count, 1)
        self.assertEqual(image.tobytes(), pixels)
        self.assertEqual(image.info, info)

    def test_heading_accepted_unmarked_panel_retains_manual_hold(self):
        """An accepted heading reuses empty mark evidence without inventing a crown."""
        image, rows = self.fixture.panel(["Divine Orb"], marks={})
        with patch.object(propagation_scan, "_marked_image", wraps=propagation_scan._marked_image) as marks:
            result = self.fixture.scan(image, rows)
        self.assertEqual(marks.call_count, 1)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["runes"], [])

    def test_reframed_panel_reads_changed_pixels_instead_of_inherited_marks(self):
        """A crop cannot inherit marks from a rejected candidate or the source info."""
        image = Image.new("RGB", (800, 400), (90, 90, 90))
        image.info["propagation_marks"] = (None, [(123, 123, 38, 38)], [])
        title = {"text": "Runeshape Combinations", "score": .99,
                 "x1": 185, "x2": 390, "y1": 32, "y2": 61}
        hashes = []
        original = propagation_scan._marked_image

        def record(picture):
            """Record the actual pixels seen by the unchanged crown recognizer."""
            hashes.append(hashlib.sha256(picture.tobytes()).hexdigest())
            return original(picture)

        with patch.object(propagation_scan, "_marked_image", side_effect=record), \
                patch.object(propagation_scan, "_read_panel_rows", side_effect=[([], None), ([], title)]), \
                patch.object(propagation_scan.runehelper_ocr, "_find_panel", return_value=(0, 30, 300, 370)):
            result = propagation_scan.scan_propagation(image)
        self.assertEqual(len(hashes), 2)
        self.assertEqual(len(set(hashes)), 2)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["runes"], [])
        self.assertEqual(image.info["propagation_marks"][1], [(123, 123, 38, 38)])

    def test_successive_captures_keep_independent_socket_evidence(self):
        """A second capture's different socket cannot reuse the first capture's marks."""
        first, first_rows = self.fixture.family_panel({0: [2]})
        second, second_rows = self.fixture.family_panel({0: [5]})
        with patch.object(propagation_scan, "_marked_image", wraps=propagation_scan._marked_image) as marks:
            first_result = self.fixture.scan(first, first_rows)
            second_result = self.fixture.scan(second, second_rows)
        self.assertEqual(marks.call_count, 2)
        self.fixture.assert_group_slots(first_result, [2])
        self.fixture.assert_group_slots(second_result, [5])

    def test_rejected_heading_does_not_produce_a_reviewable_recipe(self):
        """An accepted crown image still requires a recognized recipe-panel heading."""
        image, _ = self.fixture.family_panel({0: [2]})
        with patch.object(propagation_scan, "_marked_image", wraps=propagation_scan._marked_image) as marks, \
                patch.object(propagation_scan, "_read_panel_rows", return_value=([], None)):
            result = propagation_scan.scan_propagation(image)
        self.assertEqual(marks.call_count, 1)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["choices"], [])


if __name__ == "__main__":
    unittest.main()
