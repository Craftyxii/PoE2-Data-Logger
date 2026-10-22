"""Keep confirmed gray equipment whole using original pixels and source-art controls.

The original capture is independent game evidence. The generated catalog control
reproduces a gray-art false corner; it establishes grouping, not naming accuracy.
"""

import io
import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid


ROOT = Path(__file__).resolve().parents[1]
GRAY_CAPTURE = ROOT / "tests/fixtures/ritual_grayscale/264_tribute.png"
CATALOG = ROOT / "PoE2_Data_Logger/unique_item_assets"


def capture_variants(image):
    """Exercise normal resizing, modest dimming and JPEG without changing item boundaries."""
    yield "original", image
    for scale in (.75, 1.25, 2):
        yield str(scale), image.resize((round(image.width * scale), round(image.height * scale)),
                                      Image.Resampling.LANCZOS)
    yield "dim", ImageEnhance.Brightness(image).enhance(.7)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    output.seek(0)
    with Image.open(output) as compressed:
        yield "JPEG85", compressed.convert("RGB")


def framed_catalog_item(gray):
    """Render the source sprite on blue backing with independently placed ornate corners."""
    entry = next(row for row in json.loads((CATALOG / "manifest.json").read_text())["items"]
                 if row["name"] == "Tetzlapokal's Desire")
    image = Image.new("RGB", (708, 640), (9, 7, 7))
    draw = ImageDraw.Draw(image)
    pitch, left, top = 54, 30, 50
    for row in range(10):
        for column in range(12):
            x, y = left + column * pitch, top + row * pitch
            draw.rectangle((x, y, x + pitch, y + pitch), fill=(4, 4, 4))
            draw.ellipse((x + 18, y + 18, x + 36, y + 36), outline=(15, 15, 15), width=2)
    for column in range(13):
        draw.line((left + column * pitch, top, left + column * pitch, top + 10 * pitch),
                  fill=(70, 56, 30))
    for row in range(11):
        draw.line((left, top + row * pitch, left + 12 * pitch, top + row * pitch),
                  fill=(70, 56, 30))
    x, y = left, top + 6 * pitch
    width, height = entry["columns"] * pitch, entry["rows"] * pitch
    item = Image.new("RGBA", (width - 1, height - 1), (4, 4, 28, 255))
    with Image.open(CATALOG / entry["icon"]) as source:
        source = ImageOps.contain(source, item.size, Image.Resampling.LANCZOS)
        if gray:
            alpha = source.getchannel("A")
            source = ImageOps.grayscale(source).convert("RGBA")
            source.putalpha(alpha)
        item.alpha_composite(source, ((item.width - source.width) // 2,
                                      (item.height - source.height) // 2))
    image.paste(item.convert("RGB"), (x + 1, y + 1))
    draw.rectangle((x, y, x + width, y + height), outline=(105, 72, 35), width=1)
    assets = ROOT / "PoE2_Data_Logger/ocr/ritual_assets"
    for name, position in (("corner_top_left.png", (x, y)),
                           ("corner_bottom_right.png", (x + width - 8, y + height - 8))):
        with Image.open(assets / name) as corner:
            image.paste(corner.convert("RGB").resize((8, 8)), position)
    return image


class RitualGrayGroupingRegressions(unittest.TestCase):
    """Assert complete frame grouping without bypassing detection or replacing pixels."""

    def test_original_gray_armor_stays_one_2x2_and_neighbors_stay_separate(self):
        """Restore the user-confirmed armor while retaining the capture's ten other rewards."""
        expected = [[1], [2], [13], [14], [25], [37], [49], [61],
                    [73, 74, 85, 86], [97], [109]]
        with Image.open(GRAY_CAPTURE) as source:
            image = source.convert("RGB")
        for variant, capture in capture_variants(image):
            with self.subTest(variant=variant):
                grid = detect_reward_grid(capture)
                self.assertIsNotNone(grid)
                self.assertEqual([reward["slots"] for reward in grid["rewards"]], expected)

    def test_gray_body_detail_cannot_close_the_2x3_frame_at_its_first_cell(self):
        """A gray artwork detail must not replace the independently drawn lower corner."""
        for gray in (False, True):
            for variant, capture in capture_variants(framed_catalog_item(gray)):
                with self.subTest(gray=gray, variant=variant):
                    grid = detect_reward_grid(capture)
                    self.assertIsNotNone(grid)
                    self.assertEqual([reward["slots"] for reward in grid["rewards"]],
                                     [[73, 74, 85, 86, 97, 98]])


if __name__ == "__main__":
    unittest.main()
