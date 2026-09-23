from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class VisualAssetTests(unittest.TestCase):
    def test_app_icon_png_is_warm_transparent_and_not_a_black_block(self) -> None:
        with Image.open(ROOT / "assets" / "app-icon.png") as image:
            source = image.convert("RGBA")
        self.assertEqual(source.size, (512, 512))
        self.assertEqual(source.getpixel((0, 0))[3], 0)
        paper_pixel = source.getpixel((256, 70))
        self.assertGreater(paper_pixel[0], 220)
        self.assertGreater(paper_pixel[1], 210)
        self.assertGreater(paper_pixel[2], 190)
        self.assertGreater(len(source.getcolors(maxcolors=512 * 512) or []), 32)

    def test_windows_icon_contains_all_required_taskbar_sizes(self) -> None:
        with Image.open(ROOT / "app.ico") as icon:
            self.assertEqual(
                set(icon.info.get("sizes", set())),
                {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)},
            )


if __name__ == "__main__":
    unittest.main()
