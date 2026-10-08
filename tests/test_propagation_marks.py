"""Pixel geometry checks requiring the three propagation peaks rather than accepting a surrounding frame alone."""

from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageEnhance

from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr
from PoE2_Data_Logger.ocr.propagation_marks import supported_peak_boxes, three_peak_boxes


class PropagationPeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg"
        with Image.open(path) as source:
            cls.source = runehelper_ocr.default_frame(source.convert("RGB"))

    def masks(self, image):
        for factor in (1, .9, 1.1, .8, 1.2, 1.35, 1.5, .95, 1.05, 1.25):
            yield factor, propagation_scan._gold_mask(ImageEnhance.Brightness(image).enhance(factor))

    def markers(self, image, rows, layout, tile_size=38):
        found = supported_peak_boxes(self.masks(image), rows, tile_layout=layout, tile_size=tile_size)
        return sorted(found, key=lambda box: box[1])

    def crown(self, offsets, frame=False):
        image = Image.new("RGB", (575, 200), (176, 161, 130))
        draw = ImageDraw.Draw(image)
        if frame:
            draw.rectangle((52, 68, 89, 104), outline=(242, 213, 144), width=2)
        for offset in offsets:
            draw.polygon(((71 + offset, 63), (69 + offset, 67), (73 + offset, 67)),
                         fill=(242, 213, 144))
        return image

    def test_three_crown_peaks_do_not_require_a_frame(self):
        for frame in (False, True):
            with self.subTest(frame=frame):
                boxes = self.markers(self.crown((-9, 0, 9), frame), [{"y1": 109}], (71, 41))
                self.assertEqual(len(boxes), 1)
                self.assertAlmostEqual(boxes[0][0], 71, delta=1)
                self.assertAlmostEqual(boxes[0][1], 68, delta=2)

    def test_ordinary_highlight_and_one_or_two_peaks_never_qualify(self):
        for offsets in ((), (0,), (-9, 9)):
            for frame in (False, True):
                with self.subTest(offsets=offsets, frame=frame):
                    self.assertEqual(self.markers(self.crown(offsets, frame), [{"y1": 109}], (71, 41)), [])

    def test_rune_artwork_strokes_in_the_same_band_do_not_qualify(self):
        image = self.crown(())
        draw = ImageDraw.Draw(image)
        for centre in (62, 71, 80):
            draw.rectangle((centre - 2, 63, centre + 2, 92), fill=(242, 213, 144))
        self.assertEqual(self.markers(image, [{"y1": 109}], (71, 41)), [])

    def test_missing_recipe_rows_never_searches_unrelated_gold_text(self):
        mask = propagation_scan._gold_mask(self.crown((-9, 0, 9)))
        self.assertEqual(three_peak_boxes(mask, [], tile_layout=(71, 41)), [])

    def test_real_recipe_crowns_have_no_extra_marks_from_glyphs_or_text(self):
        boxes = self.markers(self.source, [{"y1": y, "sockets": sockets}
                                            for y, sockets in ((109, 4), (189, 3), (270, 3))], (71, 41))
        self.assertEqual(len(boxes), 3)
        self.assertEqual([round((box[0] - 71) / 41) + 1 for box in boxes], [3, 3, 3])

    def test_real_crowns_survive_removed_frames_crops_scales_and_exposure(self):
        frameless = self.source.copy()
        draw = ImageDraw.Draw(frameless)
        # These independently observed crown positions belong to the three
        # visible recipes. Remove their frame, retaining every peak above it.
        for top in (68, 149, 230):
            for box in ((134, top, 171, top + 1),
                        (134, top, 135, top + 38),
                        (170, top, 171, top + 38),
                        (134, top + 36, 171, top + 38)):
                draw.rectangle(box, fill=(170, 160, 145))
        for left in (0, 20, 35, 46):
            cropped = frameless.crop((left, 0, frameless.width, frameless.height))
            for scale in (.75, 1, 1.25):
                resized = cropped.resize((round(cropped.width * scale), round(cropped.height * scale)),
                                         Image.Resampling.LANCZOS)
                normalized = resized.resize((575, round(resized.height * 575 / resized.width)),
                                            Image.Resampling.LANCZOS)
                factor = 575 / cropped.width
                rows = [{"y1": y * factor, "sockets": sockets}
                        for y, sockets in ((109, 4), (189, 3), (270, 3))]
                layout = ((71 - left) * factor, 41 * factor)
                for brightness in (.85, 1, 1.15):
                    with self.subTest(left=left, scale=scale, brightness=brightness):
                        image = ImageEnhance.Brightness(normalized).enhance(brightness)
                        boxes = self.markers(image, rows, layout, 38 * factor)
                        self.assertEqual(len(boxes), 3)
                        self.assertEqual([round((box[0] - layout[0]) / layout[1]) + 1
                                          for box in boxes], [3, 3, 3])
                        for box, top in zip(boxes, (68, 149, 230)):
                            self.assertAlmostEqual(box[1], top * factor, delta=3)


if __name__ == "__main__":
    unittest.main()
