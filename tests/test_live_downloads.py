from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from app import FormatChoice, build_download_command


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
TEST_URL = "https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=e85c406f3379b52fcf3ce4ad55d0891e"


class LiveDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_command(self, command: list[str], timeout: int = 240) -> str:
        output = ""
        return_code = -1
        for attempt in range(3):
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            output = result.stdout + result.stderr
            return_code = result.returncode
            if return_code == 0:
                return output
            if attempt < 2:
                time.sleep(1)
        self.assertEqual(return_code, 0, output)
        return output

    def probe(self, path: Path) -> dict[str, object]:
        result = subprocess.run(
            [
                str(BIN / "ffprobe.exe"),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def section_command(self, *, mode: str, audio_format: str = "mp3") -> list[str]:
        command = build_download_command(
            ytdlp_path=BIN / "yt-dlp.exe",
            aria2_path=BIN / "aria2c.exe",
            ffmpeg_path=BIN / "ffmpeg.exe",
            auth_args=[],
            output_dir=self.output_dir,
            url=TEST_URL,
            mode=mode,
            selected_format=FormatChoice(
                "最低测试画质",
                "test",
                "worstvideo[ext=mp4]+worstaudio/worst",
            ),
            audio_format=audio_format,
        )
        command[-1:-1] = ["--download-sections", "*0-3", "--force-keyframes-at-cuts"]
        return command

    def only_output(self, suffix: str) -> Path:
        files = list(self.output_dir.glob(f"*{suffix}"))
        self.assertEqual(len(files), 1, [path.name for path in self.output_dir.iterdir()])
        self.assertGreater(files[0].stat().st_size, 0)
        return files[0]

    def test_aria2_can_download_a_real_bilibili_resource(self) -> None:
        metadata_result = subprocess.run(
            [
                str(BIN / "yt-dlp.exe"),
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
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
        self.assertEqual(metadata_result.returncode, 0, metadata_result.stderr)
        cover_url = str(json.loads(metadata_result.stdout).get("thumbnail") or "")
        self.assertTrue(cover_url)
        cover_url = cover_url.replace("http://", "https://", 1)
        destination = self.output_dir / "bilibili-cover.jpg"
        self.run_command(
            [
                str(BIN / "aria2c.exe"),
                "--async-dns=false",
                "--allow-overwrite=true",
                "--auto-file-renaming=false",
                "--summary-interval=0",
                "--console-log-level=warn",
                "--user-agent=Mozilla/5.0",
                f"--dir={self.output_dir}",
                f"--out={destination.name}",
                cover_url,
            ],
            timeout=60,
        )
        with Image.open(destination) as image:
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)

    def test_three_second_mp4_has_video_and_audio_streams(self) -> None:
        self.run_command(self.section_command(mode="video"))
        probe = self.probe(self.only_output(".mp4"))
        codec_types = {stream.get("codec_type") for stream in probe.get("streams", [])}
        self.assertIn("video", codec_types)
        self.assertIn("audio", codec_types)

    def test_three_second_mp3_is_valid_audio(self) -> None:
        self.run_command(self.section_command(mode="audio", audio_format="mp3"))
        probe = self.probe(self.only_output(".mp3"))
        codec_types = {stream.get("codec_type") for stream in probe.get("streams", [])}
        self.assertEqual(codec_types, {"audio"})

    def test_three_second_wav_is_valid_audio(self) -> None:
        self.run_command(self.section_command(mode="audio", audio_format="wav"))
        probe = self.probe(self.only_output(".wav"))
        codec_types = {stream.get("codec_type") for stream in probe.get("streams", [])}
        self.assertEqual(codec_types, {"audio"})


if __name__ == "__main__":
    unittest.main()
