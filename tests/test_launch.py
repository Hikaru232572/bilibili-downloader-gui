from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SourceLauncherTests(unittest.TestCase):
    def test_launch_batch_uses_project_environment_and_preserves_success(self) -> None:
        environment = os.environ.copy()
        environment["BILIBILI_DOWNLOADER_SMOKE_TEST"] = "1"
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "launch.bat"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
