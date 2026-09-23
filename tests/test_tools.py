from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class BundledToolTests(unittest.TestCase):
    def assert_tool_runs(self, name: str, argument: str, expected: str) -> None:
        path = BIN / name
        self.assertTrue(path.is_file(), f"Missing required tool: {path}")
        result = subprocess.run(
            [str(path), argument],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(expected.lower(), output.lower())

    def test_ytdlp_runs(self) -> None:
        self.assert_tool_runs("yt-dlp.exe", "--version", "2026")

    def test_aria2_runs(self) -> None:
        self.assert_tool_runs("aria2c.exe", "--version", "aria2 version")

    def test_ffmpeg_runs(self) -> None:
        self.assert_tool_runs("ffmpeg.exe", "-version", "ffmpeg version")

    def test_ffprobe_runs(self) -> None:
        self.assert_tool_runs("ffprobe.exe", "-version", "ffprobe version")


if __name__ == "__main__":
    unittest.main()
