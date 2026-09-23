from __future__ import annotations

import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import BiliDownloaderApp
import ui_kit as ctk


def metadata(*, with_subtitles: bool) -> dict[str, object]:
    return {
        "title": "测试视频",
        "uploader": "测试 UP 主",
        "duration": 754,
        "id": "BV1TEST12345",
        "formats": [
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
        ],
        "subtitles": {"zh-Hans": [{"ext": "json3"}]} if with_subtitles else {},
    }


class AppUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = BiliDownloaderApp()
        self.app.withdraw()
        self.app.update_idletasks()

    def tearDown(self) -> None:
        for child in self.app.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        for callback_id in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(callback_id)
        self.app.destroy()

    def pump_events(self, duration: float = 0.26) -> None:
        deadline = time.perf_counter() + duration
        while time.perf_counter() < deadline:
            self.app.update()
            time.sleep(0.005)
        self.app.update()

    def open_settings(self) -> dict[str, object]:
        geometry = self.app.geometry()
        self.app.settings_button.invoke()
        self.app.update_idletasks()
        windows = [child for child in self.app.winfo_children() if isinstance(child, tk.Toplevel)]
        self.assertEqual(windows, [])
        self.assertEqual(self.app.geometry(), geometry)
        self.assertEqual(self.app.title(), "Bili")
        self.assertEqual(self.app.main_page.winfo_manager(), "")
        self.assertEqual(self.app.settings_page.winfo_manager(), "pack")
        return self.app.settings_state

    def test_initial_home_is_minimal_and_preview_is_hidden(self) -> None:
        self.assertEqual(self.app.title(), "Bili")
        self.assertFalse(self.app.overrideredirect())
        self.assertEqual(tuple(bool(value) for value in self.app.resizable()), (True, True))
        self.assertEqual(self.app.preview_card.winfo_manager(), "")
        self.assertEqual(self.app.progress_frame.winfo_manager(), "")
        self.assertEqual(self.app.empty_state.winfo_manager(), "grid")
        self.assertEqual(self.app.status_label.winfo_manager(), "")
        self.assertEqual(self.app.fetch_button.cget("text"), "解析 →")
        self.assertEqual(self.app.settings_button.cget("text"), "")
        self.assertEqual(self.app.settings_button.cget("icon"), "gear")
        self.assertEqual(self.app.cget("bg"), "#F6F5F1")
        self.assertEqual(self.app.main_page.winfo_manager(), "pack")
        self.assertEqual(self.app.settings_page.winfo_manager(), "")
        self.assertIsInstance(self.app.home_doodles, ctk.PaperDoodles)
        self.assertEqual(self.app.home_doodles.variant, "home")
        self.assertGreater(len(self.app.home_doodles.find_withtag("doodle")), 10)
        self.assertEqual(self.app.home_doodles.line_color, "#D5CEBF")
        home_bounds = self.app.home_doodles.bbox("doodle")
        self.assertIsNotNone(home_bounds)
        assert home_bounds is not None
        self.assertGreater(home_bounds[2] - home_bounds[0], 570)
        self.assertGreater(home_bounds[3] - home_bounds[1], 60)

    def test_parse_button_keeps_existing_callback(self) -> None:
        self.app.fetch_button.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.status_var.get(), "请输入 Bilibili 视频链接或 BV 号。")
        self.assertEqual(self.app.status_label.winfo_manager(), "grid")

    def test_parse_loading_animates_and_stops_on_success_and_failure(self) -> None:
        self.app.url_var.set("BV1TEST12345")
        with (
            patch.object(self.app, "_base_ytdlp_command", return_value=["yt-dlp"]),
            patch.object(self.app, "_auth_args", return_value=[]),
            patch("app.threading.Thread") as thread_type,
        ):
            self.app.fetch_options()

        thread_type.assert_called_once()
        thread_type.return_value.start.assert_called_once()
        self.assertEqual(self.app.operation, "analysis")
        self.assertEqual(self.app.fetch_button.cget("state"), "disabled")
        self.assertEqual(self.app.fetch_button.cget("text"), "解析中.")
        self.pump_events(0.36)
        self.assertIn(self.app.fetch_button.cget("text"), ("解析中..", "解析中..."))

        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.assertEqual(self.app.fetch_button.cget("text"), "解析 →")
        self.assertEqual(self.app.fetch_button.cget("state"), "normal")
        self.assertIsNone(self.app._parse_loading_job)

        self.app.operation = "analysis"
        self.app._set_busy(True)
        self.app._start_parse_loading()
        self.app._handle_error("解析失败")
        self.assertEqual(self.app.fetch_button.cget("text"), "解析 →")
        self.assertEqual(self.app.fetch_button.cget("state"), "normal")
        self.assertIsNone(self.app._parse_loading_job)
        self.assertEqual(self.app.status_var.get(), "解析失败")
        self.app.update_idletasks()

    def test_preview_result_enters_as_one_non_blocking_group(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.update()

        initial_pady = self.app.preview_card.grid_info()["pady"]
        initial_top = int(initial_pady[0] if isinstance(initial_pady, tuple) else str(initial_pady).split()[0])
        self.assertGreaterEqual(initial_top, 12)
        self.assertIsNotNone(self.app._preview_animation)
        self.assertTrue(self.app._preview_fade_snapshot)

        self.pump_events()
        final_pady = self.app.preview_card.grid_info()["pady"]
        final_top = int(final_pady[0] if isinstance(final_pady, tuple) else str(final_pady).split()[0])
        self.assertEqual(final_top, 12)
        self.assertIsNone(self.app._preview_animation)
        self.assertEqual(self.app._preview_fade_snapshot, [])

    def test_long_url_view_returns_to_start_without_changing_value(self) -> None:
        long_url = "https://www.bilibili.com/video/BV1RXhk6REwc?vd_source=" + "a" * 120
        self.app.url_var.set(long_url)
        self.app.url_entry.icursor(tk.END)
        self.app.url_entry.xview_moveto(1)
        self.app.update_idletasks()
        self.assertGreater(self.app.url_entry.xview()[0], 0)

        self.app.url_bar._on_focus_out()
        self.app.update_idletasks()
        self.assertEqual(self.app.url_entry.xview()[0], 0)
        self.assertEqual(self.app.url_var.get(), long_url)

        self.app.url_entry.xview_moveto(1)
        self.app.parsed_url = long_url
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.update_idletasks()
        self.assertEqual(self.app.url_entry.xview()[0], 0)
        self.assertEqual(self.app.url_var.get(), long_url)

    def test_parsed_video_shows_title_and_quality_but_hides_missing_subtitles(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.update_idletasks()

        self.assertEqual(self.app.video_title_var.get(), "测试视频")
        self.assertEqual(tuple(self.app.quality_combo.cget("values")), ("1080P 60 帧", "720P"))
        self.assertEqual(self.app.preview_card.winfo_manager(), "grid")
        self.assertEqual(self.app.empty_state.winfo_manager(), "")
        self.assertEqual(self.app.video_meta_var.get(), "测试 UP 主 · 12:34 · BV1TEST12345")
        self.assertEqual([chip.cget("text") for chip in self.app.quality_chips], ["1080P 60 帧", "720P"])
        self.assertEqual(self.app.more_options_button.winfo_manager(), "")
        self.assertEqual(self.app.subtitle_options.winfo_manager(), "")

        self.app.quality_chips[1].invoke()
        self.assertEqual(self.app.quality_var.get(), "720P")
        self.assertEqual(self.app._selected_format().format_string, self.app.format_choices[1].format_string)

        download_callback = self.app.download_button.cget("command")
        self.assertIs(download_callback.__func__, BiliDownloaderApp.download)

    def test_subtitle_option_only_appears_for_video_with_native_subtitles(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=True))
        self.app.update_idletasks()
        self.assertEqual(self.app.more_options_button.winfo_manager(), "grid")
        self.assertEqual(self.app.subtitle_options.winfo_manager(), "")

        self.app.more_options_button.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.subtitle_options.winfo_manager(), "grid")
        self.assertEqual(self.app.subtitle_check.winfo_manager(), "grid")
        self.assertIsInstance(self.app.subtitle_check, ctk.SoftCheckBox)
        self.app.subtitle_check.invoke()
        self.assertTrue(self.app.embed_subtitles_var.get())

        self.app.audio_mode_card.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.video_options.winfo_manager(), "")
        self.assertEqual(self.app.audio_options.winfo_manager(), "grid")
        self.assertEqual(self.app.more_options_button.winfo_manager(), "")
        self.assertEqual(self.app.download_button.cget("text"), "提取音频")

        self.app.wav_chip.invoke()
        self.assertEqual(self.app.audio_format_var.get(), "wav")

    def test_page_navigation_preserves_parsed_home_state(self) -> None:
        url = "https://www.bilibili.com/video/BVTEST"
        self.app.url_var.set(url)
        self.app.parsed_url = url
        self.app._handle_fetch_done(metadata(with_subtitles=True))
        self.app.quality_chips[1].invoke()
        self.app.audio_mode_card.invoke()
        self.app.wav_chip.invoke()
        self.app.update_idletasks()

        main_page_id = str(self.app.main_page)
        preview_id = str(self.app.preview_card)
        expected = {
            "url": self.app.url_var.get(),
            "title": self.app.video_title_var.get(),
            "meta": self.app.video_meta_var.get(),
            "mode": self.app.download_mode_var.get(),
            "quality": self.app.quality_var.get(),
            "audio": self.app.audio_format_var.get(),
        }

        self.open_settings()
        self.app.settings_back_button.invoke()
        self.app.update_idletasks()

        self.assertEqual(str(self.app.main_page), main_page_id)
        self.assertEqual(str(self.app.preview_card), preview_id)
        self.assertEqual(self.app.url_var.get(), expected["url"])
        self.assertEqual(self.app.video_title_var.get(), expected["title"])
        self.assertEqual(self.app.video_meta_var.get(), expected["meta"])
        self.assertEqual(self.app.download_mode_var.get(), expected["mode"])
        self.assertEqual(self.app.quality_var.get(), expected["quality"])
        self.assertEqual(self.app.audio_format_var.get(), expected["audio"])
        self.assertEqual(self.app.preview_card.winfo_manager(), "grid")

    def test_page_navigation_switches_immediately_without_transition(self) -> None:
        self.app.settings_button.invoke()
        self.app.settings_button.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.settings_page.winfo_manager(), "pack")
        self.assertEqual(self.app.main_page.winfo_manager(), "")
        self.assertFalse(hasattr(self.app, "_page_transition_active"))

        self.app.settings_back_button.invoke()
        self.app.settings_back_button.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.main_page.winfo_manager(), "pack")
        self.assertEqual(self.app.settings_page.winfo_manager(), "")

    def test_page_navigation_preserves_active_download_state(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.operation = "download"
        self.app.download_state_title_var.set("正在下载")
        self.app.download_state_detail_var.set("1080P · MP4")
        self.app._show_download_progress()
        self.app._handle_progress(68)
        self.app.update_idletasks()

        self.app.show_settings_page()
        self.app.update_idletasks()
        self.app.show_main_page()
        self.app.update_idletasks()

        self.assertEqual(self.app.operation, "download")
        self.assertEqual(self.app.download_state_title_var.get(), "正在下载")
        self.assertEqual(self.app.download_state_detail_var.get(), "1080P · MP4")
        self.assertEqual(self.app.download_percent_var.get(), "68%")
        self.assertAlmostEqual(self.app.progress.cget("value"), 0.68)
        self.assertEqual(self.app.progress_frame.winfo_manager(), "grid")

    def test_settings_page_opens_inside_root_and_returns_to_main(self) -> None:
        state = self.open_settings()
        self.assertEqual(self.app.settings_page.cget("bg"), "#F6F5F1")
        self.assertIsInstance(state["auth_control"], ctk.SegmentedControl)
        self.assertIsInstance(state["settings_doodles"], ctk.PaperDoodles)
        self.assertEqual(state["settings_doodles"].variant, "settings")
        self.assertEqual(state["settings_doodles"].line_color, "#D5CEBF")
        self.assertGreater(len(state["settings_doodles"].find_withtag("doodle")), 12)

        descendants: list[tk.Widget] = []
        pending = list(self.app.settings_page.winfo_children())
        while pending:
            child = pending.pop()
            descendants.append(child)
            pending.extend(child.winfo_children())
        self.assertFalse(any(isinstance(child, ctk.CTkRadioButton) for child in descendants))
        self.assertFalse(any(isinstance(child, ctk.CTkOptionMenu) for child in descendants))

        saved_auth_mode = self.app.auth_mode_var.get()
        state["auth_control"].invoke("none")
        self.app.settings_back_button.invoke()
        self.app.update_idletasks()
        self.assertEqual(self.app.settings_page.winfo_manager(), "")
        self.assertEqual(self.app.main_page.winfo_manager(), "pack")
        self.assertEqual(self.app.title(), "Bili")
        self.assertEqual(self.app.auth_mode_var.get(), saved_auth_mode)
        reset_state = self.open_settings()
        self.assertEqual(reset_state["auth_var"].get(), saved_auth_mode)

    def test_native_titlebar_palette_and_fallback_are_safe(self) -> None:
        self.assertEqual(
            app_module._colorref("#F6F5F1"),
            246 | (245 << 8) | (241 << 16),
        )
        self.assertIsInstance(app_module.apply_windows_titlebar(self.app), bool)
        with patch.object(app_module.ctypes, "windll", object()):
            self.assertFalse(app_module.apply_windows_titlebar(self.app))

    def test_settings_modes_switch_without_system_form_controls(self) -> None:
        state = self.open_settings()
        auth_control = state["auth_control"]

        auth_control.invoke("cookie_file")
        self.app.update_idletasks()
        self.assertEqual(state["cookie_content"].winfo_manager(), "grid")
        self.assertEqual(state["browser_content"].winfo_manager(), "")
        self.assertEqual(int(state["cookie_card"].cget("height")), 146)

        auth_control.invoke("none")
        self.app.update_idletasks()
        self.assertEqual(state["none_content"].winfo_manager(), "grid")
        self.assertEqual(state["cookie_content"].winfo_manager(), "")
        self.assertEqual(int(state["cookie_card"].cget("height")), 124)

        auth_control.invoke("browser")
        self.app.update_idletasks()
        self.assertEqual(state["browser_content"].winfo_manager(), "grid")
        self.assertEqual(int(state["cookie_card"].cget("height")), 190)
        original_browser = state["browser_var"].get()
        state["browser_choice"].invoke()
        self.assertNotEqual(state["browser_var"].get(), original_browser)

    def test_settings_file_pickers_save_cancel_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            settings_path = temporary_path / "settings.json"
            output_path = temporary_path / "videos"
            cookie_path = temporary_path / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            self.app._settings_path = lambda: settings_path  # type: ignore[method-assign]

            state = self.open_settings()
            with patch("app.filedialog.askdirectory", return_value=str(output_path)):
                state["output_row"].invoke()
            with patch("app.filedialog.askopenfilename", return_value=str(cookie_path)):
                state["cookie_file_row"].invoke()
            self.assertEqual(state["output_var"].get(), str(output_path))
            self.assertEqual(state["cookie_var"].get(), str(cookie_path))
            self.assertEqual(state["auth_var"].get(), "cookie_file")
            state["browser_var"].set("firefox")
            state["profile_var"].set("Profile 1")
            state["save_button"].invoke()
            self.app.update_idletasks()
            self.assertEqual(self.app.settings_page.winfo_manager(), "")
            self.assertEqual(self.app.main_page.winfo_manager(), "pack")

            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["output_dir"], str(output_path))
            self.assertEqual(loaded["auth_mode"], "cookie_file")
            self.assertEqual(loaded["browser"], "firefox")
            self.assertEqual(loaded["browser_profile"], "Profile 1")
            self.assertEqual(loaded["cookie_path"], str(cookie_path))

            reopened_state = self.open_settings()
            self.assertEqual(reopened_state["output_var"].get(), str(output_path))
            self.assertEqual(reopened_state["auth_var"].get(), "cookie_file")
            self.assertEqual(reopened_state["browser_var"].get(), "firefox")
            self.assertEqual(reopened_state["profile_var"].get(), "Profile 1")
            reopened_state["auth_control"].invoke("none")
            reopened_state["cancel_button"].invoke()
            self.app.update_idletasks()
            self.assertEqual(self.app.settings_page.winfo_manager(), "")
            self.assertEqual(self.app.main_page.winfo_manager(), "pack")
            self.assertEqual(self.app.auth_mode_var.get(), "cookie_file")

            reset_state = self.open_settings()
            self.assertEqual(reset_state["auth_var"].get(), "cookie_file")

    def test_invalid_cookie_file_uses_inline_error(self) -> None:
        state = self.open_settings()
        state["auth_control"].invoke("cookie_file")
        state["cookie_var"].set("Z:/missing/cookies.txt")
        with patch("app.messagebox.showerror") as showerror:
            state["save_button"].invoke()
        self.app.update_idletasks()
        showerror.assert_not_called()
        self.assertEqual(self.app.settings_page.winfo_manager(), "pack")
        self.assertEqual(self.app.main_page.winfo_manager(), "")
        self.assertEqual(state["error_var"].get(), "请选择有效的 cookies.txt 文件。")

    def test_settings_are_written_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "settings.json"
            self.app._settings_path = lambda: settings_path  # type: ignore[method-assign]
            self.app.output_dir_var.set("D:/Videos")
            self.app.auth_mode_var.set("cookie_file")
            self.app.browser_var.set("firefox")
            self.app.browser_profile_var.set("Profile 1")
            self.app.cookie_path_var.set("D:/cookies.txt")

            self.app._save_settings()
            loaded = self.app._load_settings()

            self.assertEqual(loaded["output_dir"], "D:/Videos")
            self.assertEqual(loaded["auth_mode"], "cookie_file")
            self.assertEqual(loaded["browser"], "firefox")
            self.assertEqual(loaded["browser_profile"], "Profile 1")
            self.assertEqual(loaded["cookie_path"], "D:/cookies.txt")
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), loaded)

    def test_download_progress_uses_embedded_warm_state_area(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.operation = "download"
        self.app.download_state_title_var.set("正在下载")
        self.app.download_state_detail_var.set("1080P · MP4")
        self.app._show_download_progress()
        self.app._set_busy(True, cancellable=True)
        self.app._handle_progress(68)
        self.app.update_idletasks()

        self.assertIsInstance(self.app.progress, ctk.WarmProgressBar)
        self.assertEqual(self.app.progress_frame.winfo_manager(), "grid")
        self.assertEqual(self.app.download_button.winfo_manager(), "")
        self.assertEqual(self.app.download_progress_content.winfo_manager(), "grid")
        self.assertEqual(self.app.download_percent_var.get(), "68%")
        self.assertAlmostEqual(self.app.progress.cget("value"), 0.68)
        self.assertEqual(self.app.status_label.winfo_manager(), "")

    def test_completion_is_inline_without_success_messagebox(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.operation = "download"
        self.app.active_output_dir = Path("D:/Videos")
        self.app._show_download_progress()

        with patch("app.messagebox.showinfo") as showinfo:
            self.app._handle_download_done("下载完成。")
        self.app.update_idletasks()

        showinfo.assert_not_called()
        self.assertEqual(self.app.download_result_title_var.get(), "下载完成")
        self.assertIn("D:\\Videos", self.app.download_result_detail_var.get())
        self.assertEqual(self.app.download_result_content.winfo_manager(), "grid")
        self.assertEqual(self.app.open_folder_button.winfo_manager(), "grid")
        self.assertEqual(self.app.download_button.winfo_manager(), "")

    def test_completed_video_can_switch_to_and_download_audio_without_reparse(self) -> None:
        url = "https://www.bilibili.com/video/BVTEST"
        self.app.url_var.set(url)
        self.app.parsed_url = url
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        expected_formats = list(self.app.format_choices)
        expected_title = self.app.video_title_var.get()
        expected_meta = self.app.video_meta_var.get()

        self.app.operation = "download"
        self.app.active_download_mode = "video"
        self.app._show_download_progress()
        self.app._handle_download_done("下载完成。")
        self.assertEqual(self.app.download_state, "completed")

        with patch.object(self.app, "fetch_options") as fetch_options:
            self.app.audio_mode_card.invoke()
        fetch_options.assert_not_called()
        self.assertTrue(self.app.options_loaded)
        self.assertEqual(self.app.parsed_url, url)
        self.assertEqual(self.app.format_choices, expected_formats)
        self.assertEqual(self.app.video_title_var.get(), expected_title)
        self.assertEqual(self.app.video_meta_var.get(), expected_meta)
        self.assertEqual(self.app.audio_options.winfo_manager(), "grid")
        self.assertEqual(self.app.progress_frame.winfo_manager(), "")
        self.assertEqual(self.app.download_button.winfo_manager(), "grid")
        self.assertEqual(self.app.download_button.cget("text"), "提取音频")
        self.assertEqual(self.app.download_state, "idle")

        with tempfile.TemporaryDirectory() as temporary_directory:
            self.app.output_dir_var.set(temporary_directory)
            self.app.auth_mode_var.set("none")
            self.app.ytdlp_path = Path("yt-dlp.exe")
            self.app.aria2_path = Path("aria2c.exe")
            self.app.ffmpeg_path = Path("ffmpeg.exe")
            with (
                patch.object(self.app, "_ensure_download_tools"),
                patch("app.threading.Thread") as thread_type,
            ):
                self.app.download_button.invoke()
            thread_type.return_value.start.assert_called_once()
            self.assertEqual(self.app.operation, "download")
            self.assertEqual(self.app.active_download_mode, "audio")
            self.assertEqual(self.app.download_state, "downloading")
            self.app._handle_download_done("下载完成。")
            self.assertEqual(self.app.download_state, "completed")
        self.app.update_idletasks()

    def test_completed_audio_can_switch_to_video_and_toggle_repeatedly(self) -> None:
        url = "https://www.bilibili.com/video/BVTEST"
        self.app.url_var.set(url)
        self.app.parsed_url = url
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.audio_mode_card.invoke()
        self.app.operation = "download"
        self.app.active_download_mode = "audio"
        self.app._show_download_progress()
        self.app._handle_download_done("下载完成。")

        for expected_mode, card, expected_text in (
            ("video", self.app.video_mode_card, "下载视频"),
            ("audio", self.app.audio_mode_card, "提取音频"),
            ("video", self.app.video_mode_card, "下载视频"),
            ("audio", self.app.audio_mode_card, "提取音频"),
            ("video", self.app.video_mode_card, "下载视频"),
        ):
            card.invoke()
            self.assertEqual(self.app.download_mode_var.get(), expected_mode)
            self.assertEqual(self.app.download_button.cget("text"), expected_text)
            self.assertEqual(self.app.download_button.winfo_manager(), "grid")
            self.assertEqual(self.app.progress_frame.winfo_manager(), "")
            self.assertEqual(self.app.download_state, "idle")
            self.assertTrue(self.app.options_loaded)
            self.assertEqual(self.app.parsed_url, url)

        self.assertEqual(self.app.video_options.winfo_manager(), "grid")
        self.assertEqual(self.app.audio_options.winfo_manager(), "")
        self.app.update_idletasks()

    def test_mode_switch_during_download_keeps_active_download_state(self) -> None:
        url = "https://www.bilibili.com/video/BVTEST"
        self.app.url_var.set(url)
        self.app.parsed_url = url
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.operation = "download"
        self.app.active_download_mode = "video"
        self.app.download_state_detail_var.set("1080P · MP4")
        self.app._show_download_progress()
        self.app._handle_progress(42)

        self.app.audio_mode_card.invoke()

        self.assertEqual(self.app.download_mode_var.get(), "video")
        self.assertEqual(self.app.operation, "download")
        self.assertEqual(self.app.active_download_mode, "video")
        self.assertEqual(self.app.download_state, "downloading")
        self.assertEqual(self.app.download_percent_var.get(), "42%")
        self.assertEqual(self.app.download_state_detail_var.get(), "1080P · MP4")
        self.assertEqual(self.app.progress_frame.winfo_manager(), "grid")
        self.assertEqual(self.app.download_button.winfo_manager(), "")
        self.assertEqual(self.app.video_options.winfo_manager(), "grid")
        self.assertEqual(self.app.audio_options.winfo_manager(), "")
        self.app.update_idletasks()

    def test_cancel_and_download_error_are_inline(self) -> None:
        self.app.parsed_url = "https://www.bilibili.com/video/BVTEST"
        self.app._handle_fetch_done(metadata(with_subtitles=False))
        self.app.operation = "download"
        self.app.current_process = None
        self.app._show_download_progress()
        self.app._set_busy(True, cancellable=True)

        self.app.cancel_button.invoke()
        self.assertTrue(self.app.cancel_requested)
        self.assertEqual(self.app.download_state_title_var.get(), "正在取消")
        self.app._handle_cancelled()
        self.assertEqual(self.app.download_result_title_var.get(), "下载已取消")

        self.app.operation = "download"
        self.app._handle_error("line one\n" + "x" * 240)
        self.app.update_idletasks()
        self.assertEqual(self.app.download_result_title_var.get(), "下载失败")
        self.assertLessEqual(len(self.app.download_result_detail_var.get()), 150)
        self.assertEqual(self.app.status_label.winfo_manager(), "")

    def test_open_folder_reuses_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.app.active_output_dir = Path(temporary_directory)
            with patch("app.os.startfile", create=True) as startfile:
                self.app._open_output_directory()
            startfile.assert_called_once_with(temporary_directory)


if __name__ == "__main__":
    unittest.main()
