from __future__ import annotations

import unittest
from pathlib import Path

from app import FormatChoice, build_download_command


class DownloadCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.common = {
            "ytdlp_path": Path("bin/yt-dlp.exe"),
            "aria2_path": Path("bin/aria2c.exe"),
            "ffmpeg_path": Path("bin/ffmpeg.exe"),
            "auth_args": ["--cookies", "cookies.txt"],
            "output_dir": Path("downloads"),
            "url": "https://www.bilibili.com/video/BV1TEST",
        }

    def test_video_command_preserves_downloader_and_mp4_pipeline(self) -> None:
        command = build_download_command(
            **self.common,
            mode="video",
            selected_format=FormatChoice("1080P", "v1080", "v1080+ba/b"),
        )

        self.assertEqual(command[0], str(self.common["ytdlp_path"]))
        self.assertIn("--downloader", command)
        self.assertIn(str(self.common["aria2_path"]), command)
        self.assertIn("--async-dns=false", command[command.index("--downloader-args") + 1])
        self.assertIn("--ffmpeg-location", command)
        self.assertIn("v1080+ba/b", command)
        self.assertEqual(command[command.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(command[command.index("--remux-video") + 1], "mp4")
        self.assertEqual(command[-1], self.common["url"])

    def test_subtitles_are_only_added_when_requested_and_available(self) -> None:
        without_subtitles = build_download_command(
            **self.common,
            mode="video",
            embed_subtitles=True,
            has_subtitles=False,
        )
        with_subtitles = build_download_command(
            **self.common,
            mode="video",
            embed_subtitles=True,
            has_subtitles=True,
        )

        self.assertNotIn("--embed-subs", without_subtitles)
        self.assertIn("--write-subs", with_subtitles)
        self.assertEqual(with_subtitles[with_subtitles.index("--sub-langs") + 1], "all")
        self.assertEqual(with_subtitles[with_subtitles.index("--convert-subs") + 1], "srt")
        self.assertIn("--embed-subs", with_subtitles)

    def test_mp3_command_extracts_best_audio_at_high_quality(self) -> None:
        command = build_download_command(**self.common, mode="audio", audio_format="mp3")

        self.assertEqual(command[command.index("--format") + 1], "ba/b")
        self.assertIn("--extract-audio", command)
        self.assertEqual(command[command.index("--audio-format") + 1], "mp3")
        self.assertEqual(command[command.index("--audio-quality") + 1], "0")
        self.assertNotIn("--merge-output-format", command)

    def test_wav_command_does_not_apply_mp3_quality_setting(self) -> None:
        command = build_download_command(**self.common, mode="audio", audio_format="wav")

        self.assertEqual(command[command.index("--audio-format") + 1], "wav")
        self.assertNotIn("--audio-quality", command)
        self.assertNotIn("--merge-output-format", command)

    def test_rejects_unsupported_audio_format(self) -> None:
        with self.assertRaisesRegex(ValueError, "MP3.*WAV"):
            build_download_command(**self.common, mode="audio", audio_format="flac")

    def test_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            build_download_command(**self.common, mode="images")


if __name__ == "__main__":
    unittest.main()
