from __future__ import annotations

import unittest

from app import (
    extract_info_item,
    normalize_url,
    parse_subtitles,
    parse_video_qualities,
    thumbnail_url,
)


class UrlNormalizationTests(unittest.TestCase):
    def test_keeps_full_bilibili_url_with_query(self) -> None:
        url = "https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=abc"
        self.assertEqual(normalize_url(url), url)

    def test_extracts_url_from_share_text(self) -> None:
        raw = "【测试标题】 https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=abc 更多内容"
        self.assertEqual(
            normalize_url(raw),
            "https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=abc",
        )

    def test_expands_bare_bv_id(self) -> None:
        self.assertEqual(
            normalize_url("BV1RXhk6REwc"),
            "https://www.bilibili.com/video/BV1RXhk6REwc",
        )

    def test_keeps_b23_short_link(self) -> None:
        self.assertEqual(normalize_url("https://b23.tv/abcd"), "https://b23.tv/abcd")


class MetadataParsingTests(unittest.TestCase):
    def test_quality_list_is_simple_sorted_and_prefers_compatible_codec(self) -> None:
        info = {
            "formats": [
                {
                    "format_id": "1080-av1",
                    "height": 1080,
                    "fps": 60,
                    "vcodec": "av01",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 6500,
                },
                {
                    "format_id": "1080-h264",
                    "height": 1080,
                    "fps": 60,
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 5000,
                },
                {
                    "format_id": "720-h264",
                    "height": 720,
                    "fps": 30,
                    "vcodec": "avc1.64001f",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 2500,
                },
                {
                    "format_id": "audio",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "ext": "m4a",
                },
            ]
        }

        choices = parse_video_qualities(info)

        self.assertEqual([choice.label for choice in choices], ["1080P 60 帧", "720P"])
        self.assertEqual(choices[0].format_string, "1080-h264+ba/b")
        self.assertEqual(choices[1].format_string, "720-h264+ba/b")

    def test_quality_list_has_safe_fallback(self) -> None:
        choices = parse_video_qualities({"formats": []})
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0].format_string, "bv*+ba/b")

    def test_only_native_subtitles_are_exposed(self) -> None:
        info = {
            "subtitles": {
                "zh-Hans": [{"ext": "json3"}],
                "en": [{"ext": "srt"}],
            },
            "automatic_captions": {"ja": [{"ext": "vtt"}]},
        }
        choices = parse_subtitles(info)
        self.assertEqual([choice.language for choice in choices], [None, "en", "zh-Hans"])

    def test_thumbnail_prefers_direct_value_then_falls_back(self) -> None:
        self.assertEqual(thumbnail_url({"thumbnail": "direct.jpg"}), "direct.jpg")
        self.assertEqual(
            thumbnail_url({"thumbnails": [{"url": "small.jpg"}, {"url": "large.jpg"}]}),
            "large.jpg",
        )

    def test_extracts_first_entry_when_needed(self) -> None:
        entry = {"title": "Part 1", "formats": [{"format_id": "1"}]}
        item, used_first_entry = extract_info_item({"entries": [None, entry]})
        self.assertIs(item, entry)
        self.assertTrue(used_first_entry)


if __name__ == "__main__":
    unittest.main()
