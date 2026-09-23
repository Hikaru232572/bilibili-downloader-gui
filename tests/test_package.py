from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "BilibiliDownloader"
EXE = DIST / "BilibiliDownloader.exe"


class PortablePackageTests(unittest.TestCase):
    def test_portable_executable_exists(self) -> None:
        self.assertTrue(EXE.is_file(), f"Missing packaged executable: {EXE}")

    def test_all_downloader_tools_are_bundled(self) -> None:
        bundled_names = {path.name.lower() for path in DIST.rglob("*.exe")}
        for required in ("yt-dlp.exe", "aria2c.exe", "ffmpeg.exe", "ffprobe.exe"):
            self.assertIn(required, bundled_names)

    def test_packaged_app_initializes_and_exits_cleanly(self) -> None:
        environment = os.environ.copy()
        environment["BILIBILI_DOWNLOADER_SMOKE_TEST"] = "1"
        result = subprocess.run(
            [str(EXE)],
            cwd=DIST,
            env=environment,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        crash_log = DIST / "BilibiliDownloader-crash.log"
        self.assertFalse(crash_log.exists(), crash_log.read_text(encoding="utf-8", errors="replace") if crash_log.exists() else "")


if __name__ == "__main__":
    unittest.main()
