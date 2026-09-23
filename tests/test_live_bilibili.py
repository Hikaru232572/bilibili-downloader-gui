from __future__ import annotations

import json
import subprocess
import unittest
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

from app import extract_info_item, parse_video_qualities, thumbnail_url


ROOT = Path(__file__).resolve().parents[1]
YTDLP = ROOT / "bin" / "yt-dlp.exe"
TEST_URL = "https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=e85c406f3379b52fcf3ce4ad55d0891e"


class LiveBilibiliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                str(YTDLP),
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--socket-timeout",
                "30",
                "--no-playlist",
                TEST_URL,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"yt-dlp live analysis failed:\n{result.stderr}")
        cls.info = json.loads(result.stdout)
        cls.item, _ = extract_info_item(cls.info)

    def test_live_metadata_contains_title_and_formats(self) -> None:
        self.assertTrue(str(self.item.get("title") or "").strip())
        self.assertGreater(len(self.item.get("formats") or []), 0)

    def test_live_formats_produce_quality_choices(self) -> None:
        choices = parse_video_qualities(self.item)
        self.assertGreater(len(choices), 0)
        self.assertTrue(all(choice.format_string for choice in choices))

    def test_live_thumbnail_can_be_downloaded_and_decoded(self) -> None:
        cover = thumbnail_url(self.item) or thumbnail_url(self.info)
        self.assertTrue(cover)
        request = urllib.request.Request(str(cover), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(10 * 1024 * 1024 + 1)
        self.assertLessEqual(len(data), 10 * 1024 * 1024)
        image = Image.open(BytesIO(data))
        self.assertGreater(image.width, 0)
        self.assertGreater(image.height, 0)


if __name__ == "__main__":
    unittest.main()
