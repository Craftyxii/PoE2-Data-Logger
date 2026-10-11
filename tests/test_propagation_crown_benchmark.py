"""Check that physical-mark audit denominators and pixel transformations remain trustworthy."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

from tools import audit_propagation_crowns as audit


class PropagationCrownBenchmarkTests(unittest.TestCase):
    """Protect annotation independence and normalized-pixel phase in the accuracy tool."""

    def test_physical_denominators_ignore_uncertain_labels_and_duplicate_boxes(self):
        """Count each physical socket once and avoid counting independent uncertainty as an error."""
        positions = audit.box_positions([(71, 10, 38, 38), (71.2, 11, 38, 38),
                                         (112, 10, 38, 38), (194, 10, 38, 38)], (71, 41))
        counts = audit.finalized_counts(audit.counts_for([1, 3], positions, [4]))
        self.assertEqual((counts["tp"], counts["fp"], counts["fn"]), (1, 1, 1))
        self.assertEqual((counts["precision"], counts["recall"]), (.5, .5))
        empty = audit.finalized_counts(audit.counts_for([], []))
        self.assertIsNone(empty["precision"])
        self.assertIsNone(empty["recall"])
        self.assertEqual(empty["exact_rows"], 1)

    def test_real_fixture_manifest_has_independent_263_full_and_zero_partial_marks(self):
        """Validate every strip and retain zero natural partials as a material accuracy limitation."""
        manifest = audit.load_manifest(audit.ROOT / "tests/fixtures/propagation_crowns")
        self.assertEqual(manifest["counts"], dict(panels=61, rows=251, full_marks=263,
                                                  partial_marks=0, uncertain_marks=0))

    def test_reconstruction_preserves_fractional_vertical_resampling_phase(self):
        """Normalize the whole original panel, preventing short-strip rounding from changing evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pixels = np.zeros((17, 549, 3), dtype=np.uint8)
            pixels[3:9] = np.arange(6, dtype=np.uint8)[:, None, None] * 41
            source_image = Image.fromarray(pixels)
            source_image.crop((0, 3, 549, 9)).save(directory / "strip.png")
            manifest = {"sources": [{"index": 0, "label_view_dimensions": [549, 17]}],
                        "rows": [{"source_index": 0, "row": 1, "image": "strip.png",
                                  "crop_box": [0, 3, 549, 9], "tile_top": 2,
                                  "first_x": 53, "pitch": 40.6, "sockets": 1}]}
            _, normalized, rows, scale = next(audit.reconstructed_panels(directory, manifest))
            expected = source_image.resize((575, round(17 * 575 / 549)), Image.Resampling.LANCZOS)
            np.testing.assert_array_equal(np.asarray(normalized), np.asarray(expected))
            regions, layout, size = audit.geometry_for(rows[0], scale)
            self.assertAlmostEqual(regions[0]["y1"] - layout[1], 5 * 575 / 549)
            self.assertAlmostEqual(size, 40.6 * 575 / 549 * 38 / 41)

    def test_tip_erasure_preserves_frame_other_peaks_and_source(self):
        """Controlled partials may erase selected tips but must retain the actual frame and source pixels."""
        image = Image.new("RGB", (575, 54), (176, 161, 130))
        draw = ImageDraw.Draw(image)
        gold = (242, 213, 144)
        draw.rectangle((52, 12, 89, 49), outline=gold, width=2)
        for offset in (-9, 0, 9):
            draw.polygon(((71 + offset, 7), (69 + offset, 11), (73 + offset, 11)), fill=gold)
        source_pixels = np.asarray(image).copy()
        damaged, changed = audit.erase_tips(image, (71, 12, 38, 38), (0,))
        self.assertIsNotNone(damaged)
        self.assertTrue(changed.any())
        actual = np.asarray(damaged)
        np.testing.assert_array_equal(np.asarray(image), source_pixels)
        np.testing.assert_array_equal(actual[12:], source_pixels[12:])
        np.testing.assert_array_equal(actual[:, 67:], source_pixels[:, 67:])
        np.testing.assert_array_equal(actual[~changed], source_pixels[~changed])
        self.assertFalse(np.array_equal(actual[7:12, 59:66], source_pixels[7:12, 59:66]))

    def test_approximate_anchor_does_not_leave_a_tip_stub_above_perforated_real_frame(self):
        """Erase through a captured tip's final row even when its detector anchor is two pixels early."""
        directory = audit.ROOT / "tests/fixtures/propagation_crowns"
        manifest = audit.load_manifest(directory)
        _, panel, rows, scale = next(audit.reconstructed_panels(directory, manifest))
        region, layout, _ = audit.geometry_for(rows[3], scale)
        top = region[0]["y1"] - layout[1]
        y0 = max(0, int(top) - 12)
        image = panel.crop((0, y0, panel.width, min(panel.height, int(top + 39.411) + 4)))
        # Independent captured geometry: frame row10; the helper's historical
        # anchor was row8, which would leave a flat third-tip stub in row8/9.
        damaged, changed = audit.erase_tips(image, (56.125, 8, 39.411, 39.411), (0,))
        self.assertIsNotNone(damaged)
        self.assertEqual(int(np.flatnonzero(changed.any(axis=1))[-1]), 9)
        np.testing.assert_array_equal(np.asarray(damaged)[10:], np.asarray(image)[10:])
        self.assertTrue(changed[8, 46])
        self.assertTrue(changed[9, 46])

    def test_duplicate_row_annotations_are_rejected_before_scoring(self):
        """A repeated row cannot inflate ground-truth marks or independent fragment evidence."""
        original = audit.load_manifest(audit.ROOT / "tests/fixtures/propagation_crowns")
        manifest = deepcopy(original)
        manifest["rows"] = [manifest["rows"][0], manifest["rows"][0]]
        manifest["sources"] = [manifest["sources"][0]]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            row = manifest["rows"][0]
            fixture = audit.ROOT / "tests/fixtures/propagation_crowns" / row["image"]
            (directory / row["image"]).write_bytes(fixture.read_bytes())
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique identities"):
                audit.load_manifest(directory)


if __name__ == "__main__":
    unittest.main()
