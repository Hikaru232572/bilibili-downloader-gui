from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import traceback
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import ui_kit as ctk

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    ImageOps = None
    ImageTk = None


APP_NAME = "Bilibili Downloader"
WINDOW_TITLE = "Bili"
APP_ICON_FILE_NAME = "app.ico"
WINDOWS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ARIA2_ARGS = "aria2c:--async-dns=false -x 16 -s 16 -k 1M"
SETTINGS_FILE_NAME = "settings.json"
THUMBNAIL_SIZE = (220, 124)
CHROME_COOKIE_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/get-cookiestxt-locally/"
    "cclelndahbckbenkjhflpdbgdldlbecc"
)
FIREFOX_COOKIE_EXTENSION_URL = "https://addons.mozilla.org/firefox/addon/cookies-txt/"


def crash_log(message: str) -> None:
    try:
        log_path = app_dir() / "BilibiliDownloader-crash.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


@dataclass
class FormatChoice:
    label: str
    format_id: str
    format_string: str


@dataclass
class SubtitleChoice:
    label: str
    language: str | None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir())).resolve()


def _colorref(hex_color: str) -> int:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


def _apply_window_icon(window: tk.Misc) -> None:
    root = window._root()
    candidates = (app_dir() / APP_ICON_FILE_NAME, resource_dir() / APP_ICON_FILE_NAME)
    for candidate in candidates:
        if candidate.exists():
            try:
                window.iconbitmap(default=str(candidate))
                root._bili_icon_path = str(candidate)  # type: ignore[attr-defined]
                window._bili_icon_path = str(candidate)  # type: ignore[attr-defined]
                return
            except tk.TclError:
                continue
    try:
        shared_icon = getattr(root, "_bili_icon_reference", None)
        if shared_icon is None:
            shared_icon = tk.PhotoImage(master=root, width=32, height=32)
            root._bili_icon_reference = shared_icon  # type: ignore[attr-defined]
            root.iconphoto(True, shared_icon)
        window.iconphoto(False, shared_icon)
        window._bili_icon_reference = shared_icon  # type: ignore[attr-defined]
    except tk.TclError:
        pass


def apply_windows_titlebar(window: tk.Misc) -> bool:
    """Blend a native Windows caption with the app palette; safely no-op elsewhere."""
    if os.name != "nt":
        return False
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

        child_handle = int(window.winfo_id())
        parent_handle = user32.GetParent(ctypes.c_void_p(child_handle))
        handle = ctypes.c_void_p(parent_handle or child_handle)

        light_mode = ctypes.c_int(0)
        dark_mode_result = dwmapi.DwmSetWindowAttribute(
            handle,
            20,
            ctypes.byref(light_mode),
            ctypes.sizeof(light_mode),
        )
        if dark_mode_result < 0:
            dwmapi.DwmSetWindowAttribute(
                handle,
                19,
                ctypes.byref(light_mode),
                ctypes.sizeof(light_mode),
            )

        applied = False
        for attribute, color in (
            (34, "#F6F5F1"),
            (35, "#F6F5F1"),
            (36, "#292825"),
        ):
            color_value = ctypes.c_uint(_colorref(color))
            result = dwmapi.DwmSetWindowAttribute(
                handle,
                attribute,
                ctypes.byref(color_value),
                ctypes.sizeof(color_value),
            )
            applied = result >= 0 or applied
        return applied
    except (AttributeError, OSError, tk.TclError, ValueError):
        return False


def apply_native_window_style(window: tk.Misc) -> bool:
    _apply_window_icon(window)
    return apply_windows_titlebar(window)


def find_tool(name: str) -> Path | None:
    exe_name = f"{name}.exe" if os.name == "nt" and not name.endswith(".exe") else name
    candidates = [
        app_dir() / "bin" / exe_name,
        resource_dir() / "bin" / exe_name,
        app_dir().parent / "_tools" / "aria2-1.37.0-win-64bit-build1" / exe_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    found = shutil.which(name)
    if found:
        return Path(found)
    return None


def normalize_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        return value
    url_match = re.search(
        r"https?://(?:(?:www|m)\.bilibili\.com/video/|b23\.tv/)[^\s\]\)>]+",
        value,
        flags=re.IGNORECASE,
    )
    if url_match:
        return url_match.group(0).rstrip(".,，。")
    bv_match = re.search(r"\bBV[0-9A-Za-z]+\b", value, flags=re.IGNORECASE)
    if bv_match:
        return f"https://www.bilibili.com/video/{bv_match.group(0)}"
    return value


def human_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


def format_label(fmt: dict[str, Any]) -> str:
    format_id = str(fmt.get("format_id") or "unknown")
    height = fmt.get("height")
    fps = fmt.get("fps")
    ext = fmt.get("ext") or "?"
    vcodec = fmt.get("vcodec") or "video"
    filesize = fmt.get("filesize") or fmt.get("filesize_approx")
    bitrate = fmt.get("tbr")

    parts: list[str] = []
    if height:
        parts.append(f"{height}p")
    elif fmt.get("resolution"):
        parts.append(str(fmt["resolution"]))
    else:
        parts.append("Video")

    if fps:
        try:
            parts[-1] = f"{parts[-1]}{int(float(fps))}"
        except (TypeError, ValueError):
            parts.append(f"{fps}fps")

    if bitrate:
        try:
            parts.append(f"{int(float(bitrate))}kbps")
        except (TypeError, ValueError):
            pass

    size_text = human_size(filesize)
    if size_text:
        parts.append(size_text)

    parts.append(ext)
    parts.append(str(vcodec).split(".")[0])
    parts.append(f"id {format_id}")
    return " - ".join(parts)


def extract_info_item(info: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if info.get("formats"):
        return info, False
    entries = info.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("formats"):
                return entry, True
    return info, False


def parse_formats(info: dict[str, Any]) -> list[FormatChoice]:
    choices = [
        FormatChoice(
            label="Best available video + best audio",
            format_id="best",
            format_string="bv*+ba/b",
        )
    ]

    seen: set[str] = set()
    formats = info.get("formats") or []
    video_formats = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        format_id = str(fmt.get("format_id") or "")
        if not format_id or format_id in seen:
            continue
        if fmt.get("vcodec") in (None, "none"):
            continue
        seen.add(format_id)
        video_formats.append(fmt)

    video_formats.sort(
        key=lambda item: (
            int(item.get("height") or 0),
            float(item.get("fps") or 0),
            float(item.get("tbr") or 0),
        ),
        reverse=True,
    )

    for fmt in video_formats:
        format_id = str(fmt.get("format_id"))
        has_audio = fmt.get("acodec") not in (None, "none")
        choices.append(
            FormatChoice(
                label=format_label(fmt),
                format_id=format_id,
                format_string=format_id if has_audio else f"{format_id}+ba/b",
            )
        )
    return choices


def parse_video_qualities(info: dict[str, Any]) -> list[FormatChoice]:
    """Return one simple, playback-friendly choice per resolution/fps tier."""
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict) or fmt.get("vcodec") in (None, "none"):
            continue
        format_id = str(fmt.get("format_id") or "")
        height = int(fmt.get("height") or 0)
        if not format_id or not height:
            continue

        try:
            fps = int(round(float(fmt.get("fps") or 0)))
        except (TypeError, ValueError):
            fps = 0
        fps_tier = fps if fps >= 50 else 0
        key = (height, fps_tier)

        codec = str(fmt.get("vcodec") or "").lower()
        extension = str(fmt.get("ext") or "").lower()
        compatibility = int(extension == "mp4") * 2 + int(codec.startswith(("avc", "h264"))) * 3
        score = (
            compatibility,
            float(fmt.get("tbr") or 0),
            float(fmt.get("filesize") or fmt.get("filesize_approx") or 0),
        )
        current = candidates.get(key)
        if current is None or score > current["_simple_score"]:
            selected = dict(fmt)
            selected["_simple_score"] = score
            candidates[key] = selected

    choices: list[FormatChoice] = []
    for (height, fps_tier), fmt in sorted(candidates.items(), reverse=True):
        format_id = str(fmt["format_id"])
        has_audio = fmt.get("acodec") not in (None, "none")
        label = f"{height}P"
        if fps_tier:
            label += f" {fps_tier} 帧"
        choices.append(
            FormatChoice(
                label=label,
                format_id=format_id,
                format_string=format_id if has_audio else f"{format_id}+ba/b",
            )
        )

    if not choices:
        choices.append(FormatChoice("最佳可用画质", "best", "bv*+ba/b"))
    return choices


def thumbnail_url(info: dict[str, Any]) -> str | None:
    direct = info.get("thumbnail")
    if isinstance(direct, str) and direct:
        return direct
    thumbnails = info.get("thumbnails") or []
    for item in reversed(thumbnails):
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            return item["url"]
    return None


def parse_subtitles(info: dict[str, Any]) -> list[SubtitleChoice]:
    choices = [SubtitleChoice(label="No subtitles", language=None)]
    subtitles = info.get("subtitles") or {}
    if not isinstance(subtitles, dict):
        return choices

    for language in sorted(subtitles.keys(), key=str.casefold):
        tracks = subtitles.get(language) or []
        if not tracks:
            continue
        extensions = sorted(
            {
                str(track.get("ext"))
                for track in tracks
                if isinstance(track, dict) and track.get("ext")
            }
        )
        detail = f" ({', '.join(extensions)})" if extensions else ""
        choices.append(SubtitleChoice(label=f"{language}{detail}", language=language))
    return choices


def build_download_command(
    *,
    ytdlp_path: Path,
    aria2_path: Path,
    ffmpeg_path: Path,
    auth_args: list[str],
    output_dir: Path,
    url: str,
    mode: str,
    selected_format: FormatChoice | None = None,
    audio_format: str = "mp3",
    embed_subtitles: bool = False,
    has_subtitles: bool = False,
) -> list[str]:
    command = [
        str(ytdlp_path),
        "--no-playlist",
        "--newline",
        "--socket-timeout",
        "30",
        *auth_args,
        "--downloader",
        str(aria2_path),
        "--downloader-args",
        ARIA2_ARGS,
        "--ffmpeg-location",
        str(ffmpeg_path.parent),
        "--paths",
        str(output_dir),
        "--output",
        "%(title).200B [%(id)s].%(ext)s",
    ]

    if mode == "audio":
        normalized_audio_format = audio_format.lower()
        if normalized_audio_format not in {"mp3", "wav"}:
            raise ValueError("请选择 MP3 或 WAV。")
        command += [
            "--format",
            "ba/b",
            "--extract-audio",
            "--audio-format",
            normalized_audio_format,
        ]
        if normalized_audio_format == "mp3":
            command += ["--audio-quality", "0"]
    elif mode == "video":
        choice = selected_format or FormatChoice("最佳可用画质", "best", "bv*+ba/b")
        command += [
            "--format",
            choice.format_string,
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
        ]
        if embed_subtitles and has_subtitles:
            command += [
                "--write-subs",
                "--sub-langs",
                "all",
                "--convert-subs",
                "srt",
                "--embed-subs",
            ]
    else:
        raise ValueError(f"Unsupported download mode: {mode}")

    command.append(url)
    return command


class BiliDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("780x680")
        self.minsize(700, 620)

        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.operation: str | None = None
        self.cancel_requested = False
        self.download_state = "idle"
        self.active_download_mode: str | None = None
        self.options_loaded = False
        self.parsed_url = ""
        self.format_choices: list[FormatChoice] = []
        self.subtitle_choices: list[SubtitleChoice] = []
        self.activity_log: list[str] = []
        self.thumbnail_photo: Any = None

        self.ytdlp_path = find_tool("yt-dlp")
        self.aria2_path = find_tool("aria2c")
        self.ffmpeg_path = find_tool("ffmpeg")
        self.ffprobe_path = find_tool("ffprobe")

        settings = self._load_settings()
        self.url_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(settings.get("output_dir") or app_dir() / "downloads"))
        self.auth_mode_var = tk.StringVar(value=str(settings.get("auth_mode") or "browser"))
        self.browser_var = tk.StringVar(value=str(settings.get("browser") or "edge"))
        self.browser_profile_var = tk.StringVar(value=str(settings.get("browser_profile") or ""))
        self.cookie_path_var = tk.StringVar(value=str(settings.get("cookie_path") or ""))
        self.download_mode_var = tk.StringVar(value="video")
        self.quality_var = tk.StringVar(value="")
        self.audio_format_var = tk.StringVar(value="mp3")
        self.embed_subtitles_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self.video_title_var = tk.StringVar(value="")
        self.video_meta_var = tk.StringVar(value="")
        self.download_state_title_var = tk.StringVar(value="正在下载")
        self.download_state_detail_var = tk.StringVar(value="")
        self.download_percent_var = tk.StringVar(value="0%")
        self.download_result_title_var = tk.StringVar(value="")
        self.download_result_detail_var = tk.StringVar(value="")
        self.active_output_dir: Path | None = None
        self._subtitle_options_expanded = False
        self.quality_chips: list[ctk.ChoiceChip] = []
        self._parse_loading_job: str | None = None
        self._parse_loading_step = 0
        self._preview_animation: dict[str, Any] | None = None
        self._preview_fade_snapshot: list[tuple[tk.Widget, str, str]] = []

        self._configure_style()
        self._build_ui()
        apply_native_window_style(self)
        self.url_var.trace_add("write", self._reset_options_on_url_change)
        self._set_busy(False)
        self.after(100, self._drain_queue)

    def _configure_style(self) -> None:
        ctk.set_appearance_mode("light")
        self.configure(bg="#F6F5F1")

    def _build_ui(self) -> None:
        self.main_page = ctk.CTkFrame(self, fg_color="#F6F5F1")
        self.main_page.pack(fill=tk.BOTH, expand=True, padx=42, pady=34)
        outer = self.main_page
        outer.columnconfigure(0, weight=1)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky=tk.EW)
        header.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Bili",
            font=ctk.CTkFont(family="Georgia", size=31, weight="bold"),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)
        ctk.CTkLabel(
            header,
            text="Video & Audio Downloader",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#706D67",
            anchor="w",
        ).grid(row=1, column=0, sticky=tk.W, pady=(1, 0))
        self.settings_button = ctk.RoundedButton(
            header,
            icon="gear",
            width=36,
            height=36,
            corner_radius=18,
            fg_color="#EFEEEA",
            hover_color="#E3E1DB",
            text_color="#292825",
            bg_color="#F6F5F1",
            command=self.show_settings_page,
        )
        self.settings_button.grid(row=0, column=1, rowspan=2, sticky=tk.E)

        self.url_bar = ctk.URLInputBar(
            outer,
            variable=self.url_var,
            command=self.fetch_options,
            placeholder="粘贴 Bilibili 视频链接或 BV 号…",
            height=56,
            corner_radius=18,
            fill="#FFFFFF",
            border="#D8D5CE",
            page_bg="#F6F5F1",
            width=620,
        )
        self.url_bar.grid(row=1, column=0, pady=(30, 0))
        self.url_entry = self.url_bar.entry
        self.fetch_button = self.url_bar.button

        self.status_label = ctk.CTkLabel(
            outer,
            textvariable=self.status_var,
            text_color="#706D67",
            anchor="w",
            justify=tk.LEFT,
            wraplength=700,
        )
        self.status_label.grid(row=2, column=0, sticky=tk.EW, pady=(10, 0))

        self.empty_state = ctk.CTkFrame(outer, fg_color="transparent")
        self.empty_state.grid(row=4, column=0, sticky=tk.EW, pady=(40, 0))
        self.empty_state.columnconfigure(0, weight=1)
        ctk.VideoEmptyIcon(self.empty_state, bg="#F6F5F1").grid(row=0, column=0)
        ctk.CTkLabel(
            self.empty_state,
            text="把一个 Bilibili 视频放进来",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#201F1C",
        ).grid(row=1, column=0, pady=(18, 0))
        ctk.CTkLabel(
            self.empty_state,
            text="解析后可以选择视频画质或仅提取音频",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#706D67",
        ).grid(row=2, column=0, pady=(7, 0))
        self.home_doodles = ctk.PaperDoodles(self.empty_state, variant="home", bg="#F6F5F1")
        self.home_doodles.grid(row=3, column=0, pady=(24, 0))

        self.preview_card = ctk.CTkFrame(outer, fg_color="transparent")
        self.preview_card.grid(row=4, column=0, sticky=tk.EW, pady=(12, 0))
        self.preview_card.columnconfigure(0, weight=1)

        self.video_info_card = ctk.RoundedPanel(
            self.preview_card,
            height=154,
            corner_radius=20,
            fill="#EFEEEA",
            border="#D8D5CE",
            page_bg="#F6F5F1",
            padding=14,
        )
        self.video_info_card.grid(row=0, column=0, sticky=tk.EW)
        video_info = self.video_info_card.content
        video_info.columnconfigure(1, weight=1)

        thumbnail_box = ctk.CTkFrame(
            video_info,
            fg_color="#E4E2DC",
            width=THUMBNAIL_SIZE[0],
            height=THUMBNAIL_SIZE[1],
        )
        thumbnail_box.grid(row=0, column=0, rowspan=2, sticky=tk.NW)
        thumbnail_box.grid_propagate(False)
        thumbnail_box.rowconfigure(0, weight=1)
        thumbnail_box.columnconfigure(0, weight=1)
        self.thumbnail_label = ctk.CTkLabel(
            thumbnail_box,
            text="封面",
            fg_color="#E4E2DC",
            text_color="#706D67",
        )
        self.thumbnail_label.grid(row=0, column=0, sticky="nsew")

        self.video_title_label = ctk.CTkLabel(
            video_info,
            textvariable=self.video_title_var,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=18),
            text_color="#201F1C",
            anchor="w",
            justify=tk.LEFT,
            wraplength=390,
        )
        self.video_title_label.grid(row=0, column=1, sticky=tk.EW, padx=(18, 4), pady=(12, 4))

        self.video_meta_label = ctk.CTkLabel(
            video_info,
            textvariable=self.video_meta_var,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#706D67",
            anchor="w",
            justify=tk.LEFT,
            wraplength=390,
        )
        self.video_meta_label.grid(row=1, column=1, sticky=tk.NW, padx=(18, 4), pady=(2, 10))

        mode_row = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky=tk.EW, pady=(12, 0))
        mode_row.columnconfigure(0, weight=1)
        mode_row.columnconfigure(1, weight=1)
        self.video_mode_card = ctk.ModeSelectionCard(
            mode_row,
            title="下载视频",
            subtitle="选择画质并保存 MP4",
            icon="play",
            value="video",
            variable=self.download_mode_var,
            command=self._update_download_mode,
        )
        self.video_mode_card.grid(row=0, column=0, sticky=tk.EW, padx=(0, 6))
        self.audio_mode_card = ctk.ModeSelectionCard(
            mode_row,
            title="提取音频",
            subtitle="MP3 / WAV",
            icon="music",
            value="audio",
            variable=self.download_mode_var,
            command=self._update_download_mode,
        )
        self.audio_mode_card.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))

        self.video_options = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        self.video_options.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))
        self.video_options.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.video_options,
            text="视频清晰度",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#706D67",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)
        self.quality_chips_frame = ctk.CTkFrame(self.video_options, fg_color="transparent")
        self.quality_chips_frame.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))
        self.quality_combo = ctk.CTkOptionMenu(
            self.video_options,
            variable=self.quality_var,
            values=[],
            state="disabled",
        )

        self.audio_options = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        self.audio_options.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))
        self.audio_options.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.audio_options,
            text="音频格式",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#706D67",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)
        audio_chips = ctk.CTkFrame(self.audio_options, fg_color="transparent")
        audio_chips.grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.mp3_chip = ctk.ChoiceChip(
            audio_chips,
            text="MP3",
            value="mp3",
            variable=self.audio_format_var,
            page_bg="#F6F5F1",
        )
        self.mp3_chip.grid(row=0, column=0, padx=(0, 8))
        self.wav_chip = ctk.ChoiceChip(
            audio_chips,
            text="WAV",
            value="wav",
            variable=self.audio_format_var,
            page_bg="#F6F5F1",
        )
        self.wav_chip.grid(row=0, column=1)
        self.audio_combo = ctk.CTkOptionMenu(
            self.audio_options,
            variable=self.audio_format_var,
            values=["mp3", "wav"],
            state="normal",
        )
        self.audio_options.grid_remove()

        self.more_options_button = tk.Button(
            self.preview_card,
            text="更多选项  ▾",
            command=self._toggle_more_options,
            font=("Segoe UI", 10),
            fg="#706D67",
            bg="#F6F5F1",
            activeforeground="#201F1C",
            activebackground="#F6F5F1",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            cursor="hand2",
        )
        self.more_options_button.grid(row=3, column=0, sticky=tk.W, pady=(7, 0))

        self.subtitle_options = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        self.subtitle_options.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))
        self.subtitle_check = ctk.SoftCheckBox(
            self.subtitle_options,
            text="将全部字幕嵌入视频",
            variable=self.embed_subtitles_var,
            bg_color="#F6F5F1",
        )
        self.subtitle_check.grid(row=0, column=0, sticky=tk.W)

        self.download_button = ctk.RoundedButton(
            self.preview_card,
            text="下载视频",
            width=230,
            height=50,
            corner_radius=25,
            fg_color="#292825",
            hover_color="#403E39",
            text_color="#FFFFFF",
            bg_color="#F6F5F1",
            font=("Segoe UI", 11, "bold"),
            command=self.download,
        )
        self.download_button.grid(row=5, column=0, pady=(10, 0))

        self.progress_frame = ctk.CTkFrame(self.preview_card, fg_color="transparent")
        self.progress_frame.grid(row=5, column=0, sticky=tk.EW, pady=(8, 0))
        self.progress_frame.columnconfigure(0, weight=1)

        self.download_progress_content = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self.download_progress_content.grid(row=0, column=0, sticky=tk.EW)
        self.download_progress_content.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.download_progress_content,
            textvariable=self.download_state_title_var,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)
        ctk.CTkLabel(
            self.download_progress_content,
            textvariable=self.download_percent_var,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#201F1C",
            anchor="e",
        ).grid(row=0, column=1, sticky=tk.E)
        ctk.CTkLabel(
            self.download_progress_content,
            textvariable=self.download_state_detail_var,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#706D67",
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(2, 7))
        self.progress = ctk.WarmProgressBar(
            self.download_progress_content,
            percentage_variable=self.download_percent_var,
            height=10,
            track_color="#E1DFD9",
            progress_color="#292825",
            bg_color="#F6F5F1",
            mode="determinate",
        )
        self.progress.grid(row=2, column=0, columnspan=2, sticky=tk.EW)
        self.cancel_button = tk.Button(
            self.download_progress_content,
            text="×  取消",
            command=self.cancel_current,
            font=("Segoe UI", 10),
            fg="#706D67",
            disabledforeground="#AAA69D",
            bg="#F6F5F1",
            activeforeground="#292825",
            activebackground="#F6F5F1",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            cursor="hand2",
        )
        self.cancel_button.grid(row=3, column=1, sticky=tk.E, pady=(5, 0))
        self.cancel_button.bind("<Enter>", lambda _event: self.cancel_button.configure(fg="#292825"), add="+")
        self.cancel_button.bind("<Leave>", lambda _event: self.cancel_button.configure(fg="#706D67"), add="+")

        self.download_result_content = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self.download_result_content.grid(row=0, column=0, sticky=tk.EW)
        self.download_result_content.columnconfigure(1, weight=1)
        self.download_result_icon = ctk.StatusIcon(self.download_result_content, bg="#F6F5F1")
        self.download_result_icon.grid(row=0, column=0, rowspan=2, sticky=tk.W, padx=(0, 10))
        self.download_result_title_label = ctk.CTkLabel(
            self.download_result_content,
            textvariable=self.download_result_title_var,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=16),
            text_color="#201F1C",
            anchor="w",
        )
        self.download_result_title_label.grid(row=0, column=1, sticky=tk.EW)
        self.download_result_detail_label = ctk.CTkLabel(
            self.download_result_content,
            textvariable=self.download_result_detail_var,
            font=ctk.CTkFont(family="Segoe UI", size=9),
            text_color="#706D67",
            anchor="w",
            justify=tk.LEFT,
            wraplength=410,
        )
        self.download_result_detail_label.grid(row=1, column=1, sticky=tk.EW, pady=(2, 0))
        self.open_folder_button = ctk.RoundedButton(
            self.download_result_content,
            text="打开文件夹",
            width=116,
            height=38,
            corner_radius=19,
            fg_color="#EFEEEA",
            hover_color="#E1DFD9",
            text_color="#292825",
            bg_color="#F6F5F1",
            font=("Segoe UI", 10),
            command=self._open_output_directory,
        )
        self.open_folder_button.grid(row=0, column=2, rowspan=2, sticky=tk.E, padx=(16, 0))

        self.more_options_button.grid_remove()
        self.subtitle_options.grid_remove()

        self.preview_card.grid_remove()
        self.progress_frame.grid_remove()
        self.download_result_content.grid_remove()
        self.status_label.grid_remove()
        self._build_settings_page()

    def _settings_path(self) -> Path:
        return app_dir() / SETTINGS_FILE_NAME

    def _load_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._settings_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        data = {
            "output_dir": self.output_dir_var.get().strip() or str(app_dir() / "downloads"),
            "auth_mode": self.auth_mode_var.get(),
            "browser": self.browser_var.get(),
            "browser_profile": self.browser_profile_var.get().strip(),
            "cookie_path": self.cookie_path_var.get().strip(),
        }
        target = self._settings_path()
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _build_settings_page(self) -> None:
        self.settings_page = ctk.CTkFrame(self, fg_color="#F6F5F1")
        output_var = tk.StringVar(value=self.output_dir_var.get())
        auth_var = tk.StringVar(value=self.auth_mode_var.get())
        browser_var = tk.StringVar(value=self.browser_var.get())
        profile_var = tk.StringVar(value=self.browser_profile_var.get())
        cookie_var = tk.StringVar(value=self.cookie_path_var.get())
        output_name_var = tk.StringVar()
        output_detail_var = tk.StringVar()
        cookie_name_var = tk.StringVar()
        cookie_detail_var = tk.StringVar()
        settings_error_var = tk.StringVar()

        content = ctk.CTkFrame(self.settings_page, fg_color="#F6F5F1")
        content.pack(fill=tk.BOTH, expand=True, padx=92, pady=20)
        content.columnconfigure(0, weight=1)

        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky=tk.EW)
        header.columnconfigure(1, weight=1)
        self.settings_back_button = ctk.RoundedButton(
            header,
            text="←",
            width=36,
            height=36,
            corner_radius=18,
            fg_color="#EFEEEA",
            hover_color="#E3E1DB",
            text_color="#292825",
            bg_color="#F6F5F1",
            font=("Segoe UI Symbol", 16),
            command=self.show_main_page,
        )
        self.settings_back_button.grid(row=0, column=0, rowspan=2, sticky=tk.W, padx=(0, 12))
        ctk.CTkLabel(
            header,
            text="设置",
            font=ctk.CTkFont(family="Georgia", size=27, weight="bold"),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=1, sticky=tk.W)
        ctk.CTkLabel(
            header,
            text="Download & Login Preferences",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#706D67",
            anchor="w",
        ).grid(row=1, column=1, sticky=tk.W, pady=(1, 0))

        def sync_output_display(*_args: object) -> None:
            raw = output_var.get().strip()
            path = Path(raw) if raw else app_dir() / "downloads"
            output_name_var.set(path.name or str(path))
            output_detail_var.set(str(path))

        def sync_cookie_display(*_args: object) -> None:
            raw = cookie_var.get().strip()
            path = Path(raw) if raw else None
            cookie_name_var.set(path.name if path else "未选择文件")
            cookie_detail_var.set(str(path) if path else "选择由浏览器导出的 cookies.txt")

        output_var.trace_add("write", sync_output_display)
        cookie_var.trace_add("write", sync_cookie_display)
        sync_output_display()
        sync_cookie_display()

        def pick_output() -> None:
            selected = filedialog.askdirectory(parent=self, initialdir=output_var.get() or str(app_dir()))
            if selected:
                output_var.set(selected)

        download_card = ctk.RoundedPanel(
            content,
            height=92,
            corner_radius=20,
            fill="#EFEEEA",
            border="#D8D5CE",
            page_bg="#F6F5F1",
            padding=8,
        )
        download_card.grid(row=1, column=0, sticky=tk.EW, pady=(14, 0))
        download_inner = download_card.content
        download_inner.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            download_inner,
            text="下载位置",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=10),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)
        output_row = ctk.ActionSettingRow(
            download_inner,
            title="保存到",
            value_variable=output_name_var,
            detail_variable=output_detail_var,
            action_text="更改 →",
            command=pick_output,
            height=50,
            bg_color="#EFEEEA",
        )
        output_row.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))

        cookie_card = ctk.RoundedPanel(
            content,
            height=190,
            corner_radius=20,
            fill="#EFEEEA",
            border="#D8D5CE",
            page_bg="#F6F5F1",
            padding=10,
        )
        cookie_card.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))
        cookie_inner = cookie_card.content
        cookie_inner.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cookie_inner,
            text="Cookie",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=10),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W)

        browser_content = ctk.CTkFrame(cookie_inner, fg_color="transparent")
        browser_content.columnconfigure(0, weight=1)
        browser_choice = ctk.ChoiceSettingRow(
            browser_content,
            title="浏览器",
            variable=browser_var,
            values=["edge", "chrome", "firefox", "brave", "vivaldi", "opera"],
            display_names={
                "edge": "Edge",
                "chrome": "Chrome",
                "firefox": "Firefox",
                "brave": "Brave",
                "vivaldi": "Vivaldi",
                "opera": "Opera",
            },
            bg_color="#EFEEEA",
        )
        browser_choice.grid(row=0, column=0, sticky=tk.EW)
        profile_row = ctk.SettingEntryRow(
            browser_content,
            title="配置文件",
            variable=profile_var,
            placeholder="Default",
            bg_color="#EFEEEA",
        )
        profile_row.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))

        cookie_content = ctk.CTkFrame(cookie_inner, fg_color="transparent")
        cookie_content.columnconfigure(0, weight=1)

        def pick_cookie() -> None:
            selected = filedialog.askopenfilename(
                parent=self,
                title="选择 cookies.txt",
                filetypes=[("Cookie text files", "*.txt"), ("All files", "*.*")],
                initialdir=str(app_dir()),
            )
            if selected:
                cookie_var.set(selected)
                auth_var.set("cookie_file")
                update_auth_widgets()

        cookie_file_row = ctk.ActionSettingRow(
            cookie_content,
            title="Cookie 文件",
            value_variable=cookie_name_var,
            detail_variable=cookie_detail_var,
            action_text="选择文件 →",
            command=pick_cookie,
            height=54,
            bg_color="#EFEEEA",
        )
        cookie_file_row.grid(row=0, column=0, sticky=tk.EW)

        none_content = ctk.CTkFrame(cookie_inner, fg_color="transparent")
        ctk.CTkLabel(
            none_content,
            text="将以游客身份解析可访问内容",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#706D67",
            anchor="w",
        ).grid(row=0, column=0, sticky=tk.W, pady=(12, 0))

        def update_auth_widgets(*_args: object) -> None:
            browser_content.grid_remove()
            cookie_content.grid_remove()
            none_content.grid_remove()
            mode = auth_var.get()
            if mode == "browser":
                cookie_card.configure(height=190)
                browser_content.grid(row=2, column=0, sticky=tk.EW, pady=(8, 0))
            elif mode == "cookie_file":
                cookie_card.configure(height=146)
                cookie_content.grid(row=2, column=0, sticky=tk.EW, pady=(8, 0))
            else:
                cookie_card.configure(height=124)
                none_content.grid(row=2, column=0, sticky=tk.EW, pady=(8, 0))
            settings_error_var.set("")

        auth_var.trace_add("write", update_auth_widgets)
        auth_control = ctk.SegmentedControl(
            cookie_inner,
            options=[("浏览器", "browser"), ("cookies.txt", "cookie_file"), ("不使用", "none")],
            variable=auth_var,
            command=update_auth_widgets,
            bg_color="#EFEEEA",
        )
        auth_control.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))
        update_auth_widgets()

        help_row = ctk.CTkFrame(content, fg_color="transparent")
        help_row.grid(row=3, column=0, sticky=tk.EW, pady=(8, 0))
        help_row.columnconfigure(2, weight=1)
        ctk.CTkLabel(
            help_row,
            text="帮助获取 Cookie",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=9),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        def make_help_link(text: str, url: str, column: int) -> tk.Button:
            link = tk.Button(
                help_row,
                text=text,
                command=lambda: webbrowser.open(url),
                font=("Segoe UI", 9),
                fg="#706D67",
                bg="#F6F5F1",
                activeforeground="#292825",
                activebackground="#F6F5F1",
                relief="flat",
                bd=0,
                highlightthickness=0,
                padx=0,
                pady=0,
                cursor="hand2",
            )
            link.grid(row=1, column=column, sticky=tk.W, padx=(0, 20), pady=(3, 0))
            link.bind("<Enter>", lambda _event, button=link: button.configure(fg="#292825"), add="+")
            link.bind("<Leave>", lambda _event, button=link: button.configure(fg="#706D67"), add="+")
            return link

        chrome_help = make_help_link("Chrome / Edge", CHROME_COOKIE_EXTENSION_URL, 0)
        firefox_help = make_help_link("Firefox", FIREFOX_COOKIE_EXTENSION_URL, 1)
        settings_doodles = ctk.PaperDoodles(help_row, variant="settings", bg="#F6F5F1")
        settings_doodles.grid(row=0, column=3, rowspan=2, sticky=tk.E)

        footer = ctk.CTkFrame(content, fg_color="transparent")
        footer.grid(row=4, column=0, sticky=tk.EW, pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            textvariable=settings_error_var,
            font=ctk.CTkFont(family="Segoe UI", size=9),
            text_color="#A95A58",
            anchor="w",
            wraplength=330,
        ).grid(row=0, column=0, sticky=tk.W)
        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=0, column=1, sticky=tk.E)

        def save() -> None:
            if auth_var.get() == "cookie_file":
                cookie_path = cookie_var.get().strip()
                if not cookie_path or not Path(cookie_path).exists():
                    settings_error_var.set("请选择有效的 cookies.txt 文件。")
                    return
            old_auth = (
                self.auth_mode_var.get(),
                self.browser_var.get(),
                self.browser_profile_var.get(),
                self.cookie_path_var.get(),
            )
            self.output_dir_var.set(output_var.get().strip() or str(app_dir() / "downloads"))
            self.auth_mode_var.set(auth_var.get())
            self.browser_var.set(browser_var.get())
            self.browser_profile_var.set(profile_var.get().strip())
            self.cookie_path_var.set(cookie_var.get().strip())
            try:
                self._save_settings()
            except OSError as exc:
                settings_error_var.set(f"设置保存失败：{exc}")
                return
            new_auth = (
                self.auth_mode_var.get(),
                self.browser_var.get(),
                self.browser_profile_var.get(),
                self.cookie_path_var.get(),
            )
            if self.options_loaded and old_auth != new_auth:
                self.options_loaded = False
                self.parsed_url = ""
                self.format_choices = []
                self.subtitle_choices = []
                self.preview_card.grid_remove()
                self.download_button.configure(state="disabled")
                self._set_status("Cookie 设置已更改，请重新解析视频。")
            else:
                self._set_status("设置已保存。")
            self.show_main_page()

        cancel_button = ctk.RoundedButton(
            actions,
            text="取消",
            width=84,
            height=38,
            corner_radius=19,
            fg_color="#EFEEEA",
            hover_color="#E1DFD9",
            text_color="#292825",
            bg_color="#F6F5F1",
            font=("Segoe UI", 10),
            command=self.show_main_page,
        )
        cancel_button.grid(row=0, column=0)
        save_button = ctk.RoundedButton(
            actions,
            text="保存",
            width=96,
            height=38,
            corner_radius=19,
            fg_color="#292825",
            hover_color="#403E39",
            text_color="#FFFFFF",
            bg_color="#F6F5F1",
            font=("Segoe UI", 10, "bold"),
            command=save,
        )
        save_button.grid(row=0, column=1, padx=(10, 0))

        self.settings_state = {
            "output_var": output_var,
            "auth_var": auth_var,
            "browser_var": browser_var,
            "profile_var": profile_var,
            "cookie_var": cookie_var,
            "error_var": settings_error_var,
            "auth_control": auth_control,
            "cookie_card": cookie_card,
            "browser_content": browser_content,
            "cookie_content": cookie_content,
            "none_content": none_content,
            "browser_choice": browser_choice,
            "profile_row": profile_row,
            "output_row": output_row,
            "cookie_file_row": cookie_file_row,
            "save_button": save_button,
            "cancel_button": cancel_button,
            "chrome_help": chrome_help,
            "firefox_help": firefox_help,
            "settings_doodles": settings_doodles,
        }

    def _reset_settings_drafts(self) -> None:
        state = self.settings_state
        state["output_var"].set(self.output_dir_var.get())
        state["auth_var"].set(self.auth_mode_var.get())
        state["browser_var"].set(self.browser_var.get())
        state["profile_var"].set(self.browser_profile_var.get())
        state["cookie_var"].set(self.cookie_path_var.get())
        state["error_var"].set("")

    @staticmethod
    def _smoothstep(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def _animate(
        self,
        duration_ms: int,
        update: Any,
        complete: Any = None,
    ) -> dict[str, Any]:
        """Run a small, non-blocking animation on Tk's event loop."""
        animation: dict[str, Any] = {"cancelled": False, "after_id": None}
        started_at = time.perf_counter()

        def tick() -> None:
            if animation["cancelled"]:
                return
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            raw_progress = min(1.0, elapsed_ms / max(1, duration_ms))
            update(self._smoothstep(raw_progress))
            if raw_progress < 1.0:
                animation["after_id"] = self.after(16, tick)
            elif complete is not None:
                complete()

        tick()
        return animation

    def _cancel_animation(self, animation: dict[str, Any] | None) -> None:
        if animation is None:
            return
        animation["cancelled"] = True
        callback_id = animation.get("after_id")
        if callback_id:
            try:
                self.after_cancel(callback_id)
            except tk.TclError:
                pass

    @staticmethod
    def _blend_hex(background: str, foreground: str, amount: float) -> str:
        try:
            background_rgb = tuple(int(background[index : index + 2], 16) for index in (1, 3, 5))
            foreground_rgb = tuple(int(foreground[index : index + 2], 16) for index in (1, 3, 5))
        except (TypeError, ValueError):
            return foreground
        amount = max(0.0, min(1.0, amount))
        values = [round(start + (end - start) * amount) for start, end in zip(background_rgb, foreground_rgb)]
        return "#" + "".join(f"{value:02X}" for value in values)

    def _capture_text_fade(self, root: tk.Widget) -> list[tuple[tk.Widget, str, str]]:
        snapshot: list[tuple[tk.Widget, str, str]] = []
        pending = [root]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if not isinstance(widget, (tk.Label, tk.Button)):
                continue
            try:
                foreground = str(widget.cget("fg"))
                background = str(widget.cget("bg"))
            except tk.TclError:
                continue
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", foreground) and re.fullmatch(
                r"#[0-9A-Fa-f]{6}", background
            ):
                snapshot.append((widget, foreground, background))
        return snapshot

    def _apply_text_fade(self, snapshot: list[tuple[tk.Widget, str, str]], visibility: float) -> None:
        for widget, foreground, background in snapshot:
            try:
                widget.configure(fg=self._blend_hex(background, foreground, visibility))
            except tk.TclError:
                continue

    @staticmethod
    def _restore_text_fade(snapshot: list[tuple[tk.Widget, str, str]]) -> None:
        for widget, foreground, _background in snapshot:
            try:
                widget.configure(fg=foreground)
            except tk.TclError:
                continue

    def show_settings_page(self) -> None:
        if self.settings_page.winfo_manager():
            return
        self._reset_settings_drafts()
        self.main_page.pack_forget()
        self.settings_page.pack(fill=tk.BOTH, expand=True)
        self.settings_page.tkraise()

    def show_main_page(self) -> None:
        if self.main_page.winfo_manager():
            return
        self.settings_page.pack_forget()
        self.main_page.pack(fill=tk.BOTH, expand=True, padx=42, pady=34)
        self.main_page.tkraise()

    def _set_status(self, message: str, error: bool = False) -> None:
        self.status_var.set(message)
        self.status_label.configure(fg="#A23B32" if error else "#706D67")
        if self.operation == "download":
            self.status_label.grid_remove()
        elif message:
            self.status_label.grid()
        else:
            self.status_label.grid_remove()

    def _paste_url(self) -> None:
        try:
            self.url_var.set(self.clipboard_get().strip())
        except tk.TclError:
            self._set_status("剪贴板为空。", error=True)

    def _reset_options_on_url_change(self, *_args: object) -> None:
        if not hasattr(self, "preview_card") or self.operation:
            return
        if normalize_url(self.url_var.get()) == self.parsed_url:
            return
        self._cancel_preview_result_animation()
        self.options_loaded = False
        self.parsed_url = ""
        self.format_choices = []
        self.subtitle_choices = []
        self.thumbnail_photo = None
        self.download_state = "idle"
        self.active_download_mode = None
        self.preview_card.grid_remove()
        self.progress_frame.grid_remove()
        self.empty_state.grid()
        self.download_button.configure(state="disabled")
        self._set_status("")

    def _rebuild_quality_chips(self) -> None:
        for child in self.quality_chips_frame.winfo_children():
            child.destroy()
        self.quality_chips = []
        for index, choice in enumerate(self.format_choices):
            chip = ctk.ChoiceChip(
                self.quality_chips_frame,
                text=choice.label,
                value=choice.label,
                variable=self.quality_var,
                page_bg="#F6F5F1",
            )
            chip.grid(
                row=index // 4,
                column=index % 4,
                sticky=tk.W,
                padx=(0, 8),
                pady=(0, 6) if index // 4 == 0 and len(self.format_choices) > 4 else 0,
            )
            self.quality_chips.append(chip)

    @staticmethod
    def _video_metadata_text(item: dict[str, Any]) -> str:
        parts: list[str] = []
        uploader = item.get("uploader") or item.get("channel")
        if uploader:
            parts.append(str(uploader))
        duration = item.get("duration")
        try:
            total_seconds = max(0, int(float(duration)))
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            parts.append(f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}")
        except (TypeError, ValueError):
            pass
        video_id = item.get("id") or item.get("display_id")
        if video_id and str(video_id).upper().startswith("BV"):
            parts.append(str(video_id))
        return " · ".join(parts)

    def _toggle_more_options(self) -> None:
        if self.download_mode_var.get() != "video" or len(self.subtitle_choices) <= 1:
            return
        self._subtitle_options_expanded = not self._subtitle_options_expanded
        if self._subtitle_options_expanded:
            self.more_options_button.configure(text="更多选项  ▴")
            self.subtitle_options.grid()
        else:
            self.more_options_button.configure(text="更多选项  ▾")
            self.subtitle_options.grid_remove()

    def _show_download_progress(self) -> None:
        self.download_state = "downloading"
        self.download_button.grid_remove()
        self.download_result_content.grid_remove()
        self.download_progress_content.grid()
        self.progress_frame.grid()
        self.download_percent_var.set("0%")
        self.progress.configure(mode="determinate")
        self.progress.set(0)

    def _show_download_result(self, kind: str, title: str, detail: str) -> None:
        self.download_state = "completed" if kind == "success" else kind
        self.download_button.grid_remove()
        self.download_progress_content.grid_remove()
        self.download_result_content.grid()
        self.progress_frame.grid()
        self.download_result_icon.set_kind(kind)
        self.download_result_title_var.set(title)
        self.download_result_detail_var.set(detail)
        error = kind == "error"
        self.download_result_title_label.configure(fg="#A95A58" if error else "#201F1C")
        self.download_result_detail_label.configure(fg="#A95A58" if error else "#706D67")
        if kind == "success":
            self.open_folder_button.grid()
        else:
            self.open_folder_button.grid_remove()

    def _restore_download_action(self) -> None:
        if self.operation is None and self.options_loaded:
            self.download_state = "idle"
            self.progress_frame.grid_remove()
            self.download_progress_content.grid_remove()
            self.download_result_content.grid_remove()
            self.download_button.grid()
            self.download_button.configure(state="normal")

    @staticmethod
    def _short_download_error(message: str) -> str:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        brief = lines[-1] if lines else "下载过程中出现错误。"
        return brief if len(brief) <= 150 else brief[:147] + "…"

    def _open_output_directory(self) -> None:
        target = self.active_output_dir or Path(self.output_dir_var.get().strip() or app_dir() / "downloads")
        try:
            target.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            self._set_status(f"无法打开下载目录：{exc}", error=True)

    def _selected_format(self) -> FormatChoice:
        current = self.quality_var.get()
        for choice in self.format_choices:
            if choice.label == current:
                return choice
        return self.format_choices[0] if self.format_choices else FormatChoice("最佳可用画质", "best", "bv*+ba/b")

    def _update_download_mode(self) -> None:
        if self.operation == "download":
            if self.active_download_mode:
                self.download_mode_var.set(self.active_download_mode)
            return
        if self.download_mode_var.get() == "audio":
            self.video_options.grid_remove()
            self.audio_options.grid()
            self.more_options_button.grid_remove()
            self.subtitle_options.grid_remove()
            self.download_button.configure(text="提取音频")
        else:
            self.audio_options.grid_remove()
            self.video_options.grid()
            if len(self.subtitle_choices) > 1:
                self.more_options_button.grid()
                if self._subtitle_options_expanded:
                    self.subtitle_options.grid()
                else:
                    self.subtitle_options.grid_remove()
            else:
                self.more_options_button.grid_remove()
                self.subtitle_options.grid_remove()
            self.download_button.configure(text="下载视频")
        if self.options_loaded:
            self._restore_download_action()

    def _auth_args(self) -> list[str]:
        mode = self.auth_mode_var.get()
        if mode == "none":
            return []
        if mode == "browser":
            browser = self.browser_var.get().strip()
            profile = self.browser_profile_var.get().strip()
            source = f"{browser}:{profile}" if profile else browser
            return ["--cookies-from-browser", source]
        cookie_path = self.cookie_path_var.get().strip()
        if not cookie_path:
            raise ValueError("请在设置中选择 cookies.txt，或切换 Cookie 模式。")
        if not Path(cookie_path).exists():
            raise ValueError(f"Cookie 文件不存在：{cookie_path}")
        return ["--cookies", cookie_path]

    def _ensure_analysis_tool(self) -> None:
        if not self.ytdlp_path:
            raise RuntimeError("缺少 yt-dlp，请将 yt-dlp.exe 放入程序的 bin 目录。")

    def _ensure_download_tools(self) -> None:
        missing = []
        if not self.ytdlp_path:
            missing.append("yt-dlp")
        if not self.aria2_path:
            missing.append("aria2c")
        if not self.ffmpeg_path:
            missing.append("ffmpeg")
        if not self.ffprobe_path:
            missing.append("ffprobe")
        if missing:
            raise RuntimeError("缺少下载工具：" + "、".join(missing))

    def _base_ytdlp_command(self) -> list[str]:
        self._ensure_analysis_tool()
        assert self.ytdlp_path is not None
        return [str(self.ytdlp_path)]

    def _set_busy(self, busy: bool, cancellable: bool = False) -> None:
        normal_state = "disabled" if busy else "normal"
        self.url_entry.configure(state=normal_state)
        self.fetch_button.configure(state=normal_state)
        self.settings_button.configure(state=normal_state)
        self.download_button.configure(state="normal" if self.options_loaded and not busy else "disabled")
        if cancellable:
            self.progress_frame.grid()
            self.cancel_button.configure(state="normal")
            self.progress.stop()
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        elif not busy:
            self.cancel_button.configure(state="disabled")
            self.progress.stop()

    def _start_parse_loading(self) -> None:
        self._stop_parse_loading()
        self._parse_loading_step = 0

        def advance() -> None:
            if self.operation != "analysis":
                self._parse_loading_job = None
                return
            dots = "." * (self._parse_loading_step % 3 + 1)
            self.fetch_button.configure(text=f"解析中{dots}")
            self._parse_loading_step += 1
            self._parse_loading_job = self.after(320, advance)

        advance()

    def _stop_parse_loading(self) -> None:
        if self._parse_loading_job is not None:
            try:
                self.after_cancel(self._parse_loading_job)
            except tk.TclError:
                pass
        self._parse_loading_job = None
        if hasattr(self, "fetch_button"):
            self.fetch_button.configure(text="解析 →")

    def _cancel_preview_result_animation(self) -> None:
        self._cancel_animation(self._preview_animation)
        self._preview_animation = None
        if self._preview_fade_snapshot:
            self._restore_text_fade(self._preview_fade_snapshot)
            self._preview_fade_snapshot = []
        if hasattr(self, "preview_card"):
            self.preview_card.grid_configure(pady=(12, 0))

    def _animate_preview_result_in(self) -> None:
        self._cancel_preview_result_animation()
        distance = 10
        self._preview_fade_snapshot = self._capture_text_fade(self.preview_card)
        self._apply_text_fade(self._preview_fade_snapshot, 0.35)

        def update_preview(progress: float) -> None:
            offset = round(distance * (1.0 - progress))
            self.preview_card.grid_configure(pady=(12 + offset, 0))
            self._apply_text_fade(self._preview_fade_snapshot, 0.35 + 0.65 * progress)

        def complete_preview() -> None:
            self.preview_card.grid_configure(pady=(12, 0))
            self._restore_text_fade(self._preview_fade_snapshot)
            self._preview_fade_snapshot = []
            self._preview_animation = None

        self._preview_animation = self._animate(200, update_preview, complete_preview)

    def _log(self, message: str) -> None:
        self.activity_log.append(message.rstrip())
        if len(self.activity_log) > 500:
            del self.activity_log[:100]

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "fetch_done":
                    self._handle_fetch_done(payload)
                elif kind == "thumbnail_done":
                    self._handle_thumbnail_done(payload)
                elif kind == "thumbnail_error":
                    video_url, _error = payload
                    if video_url == self.parsed_url:
                        self.thumbnail_label.configure(text="封面不可用", image="")
                elif kind == "progress":
                    self._handle_progress(float(payload))
                elif kind == "download_done":
                    self._handle_download_done(str(payload))
                elif kind == "cancelled":
                    self._handle_cancelled()
                elif kind == "error":
                    self._handle_error(str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def fetch_options(self) -> None:
        if self.operation is not None:
            return
        try:
            url = normalize_url(self.url_var.get())
            if not url:
                raise ValueError("请输入 Bilibili 视频链接或 BV 号。")
            command = self._base_ytdlp_command() + [
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--socket-timeout",
                "30",
                "--no-playlist",
                *self._auth_args(),
                url,
            ]
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        self.operation = "analysis"
        self.options_loaded = False
        self.parsed_url = url
        self._cancel_preview_result_animation()
        self.preview_card.grid_remove()
        self.progress_frame.grid_remove()
        self.empty_state.grid_remove()
        self._set_status("正在解析视频…")
        self._log("Fetching metadata with yt-dlp.")
        self._set_busy(True)
        self._start_parse_loading()
        threading.Thread(target=self._run_fetch, args=(command,), daemon=True).start()

    def _run_fetch(self, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=WINDOWS_CREATION_FLAGS,
            )
            self.current_process = process
            stdout, stderr = process.communicate()
            self.current_process = None
            if process.returncode != 0:
                raise RuntimeError(stderr.strip() or "yt-dlp 无法解析这个视频。")
            self.queue.put(("fetch_done", json.loads(stdout)))
        except Exception as exc:
            self.current_process = None
            self.queue.put(("error", exc))

    def _handle_fetch_done(self, info: dict[str, Any]) -> None:
        item, _used_first_entry = extract_info_item(info)
        self.format_choices = parse_video_qualities(item)
        self.subtitle_choices = parse_subtitles(item)
        self.quality_combo.configure(values=[choice.label for choice in self.format_choices], state="normal")
        self.quality_var.set(self.format_choices[0].label)
        self._rebuild_quality_chips()
        self.embed_subtitles_var.set(False)
        self._subtitle_options_expanded = False
        self.more_options_button.configure(text="更多选项  ▾")

        title = item.get("title") or info.get("title") or "未命名视频"
        self.video_title_var.set(str(title))
        self.video_meta_var.set(self._video_metadata_text(item))
        if self.video_meta_var.get():
            self.video_meta_label.grid()
        else:
            self.video_meta_label.grid_remove()
        self.thumbnail_photo = None
        self.thumbnail_label.configure(text="正在加载封面…", image="")
        self.empty_state.grid_remove()
        self.preview_card.grid()
        self.progress_frame.grid_remove()
        self.download_button.grid()
        self.options_loaded = True
        self.operation = None
        self._update_download_mode()
        self._stop_parse_loading()
        self._set_busy(False)
        self.url_bar.show_start()
        self._set_status("")
        self._animate_preview_result_in()

        cover = thumbnail_url(item) or thumbnail_url(info)
        if cover and Image is not None:
            threading.Thread(target=self._load_thumbnail, args=(cover, self.parsed_url), daemon=True).start()
        elif Image is None:
            self.thumbnail_label.configure(text="缺少 Pillow\n无法显示封面")
        else:
            self.thumbnail_label.configure(text="无封面")

    def _load_thumbnail(self, url: str, video_url: str) -> None:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("Thumbnail is too large")
            self.queue.put(("thumbnail_done", (video_url, data)))
        except Exception as exc:
            self._log(f"Thumbnail error: {exc}")
            self.queue.put(("thumbnail_error", (video_url, exc)))

    def _handle_thumbnail_done(self, payload: tuple[str, bytes]) -> None:
        video_url, data = payload
        if video_url != self.parsed_url:
            return
        if Image is None or ImageDraw is None or ImageOps is None or ImageTk is None:
            return
        try:
            image = Image.open(BytesIO(data)).convert("RGB")
            resampling = getattr(Image, "Resampling", Image)
            image = ImageOps.fit(image, THUMBNAIL_SIZE, method=resampling.LANCZOS)
            image = image.convert("RGBA")
            mask = Image.new("L", THUMBNAIL_SIZE, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, THUMBNAIL_SIZE[0] - 1, THUMBNAIL_SIZE[1] - 1),
                radius=13,
                fill=255,
            )
            image.putalpha(mask)
            self.thumbnail_photo = ImageTk.PhotoImage(image)
            self.thumbnail_label.configure(image=self.thumbnail_photo, text="")
        except Exception as exc:
            self._log(f"Thumbnail decode error: {exc}")
            self.thumbnail_label.configure(text="封面不可用", image="")

    def download(self) -> None:
        try:
            url = normalize_url(self.url_var.get())
            if not self.options_loaded or url != self.parsed_url:
                raise ValueError("请先解析当前视频链接。")
            output_dir = Path(self.output_dir_var.get().strip() or app_dir() / "downloads")
            output_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_download_tools()
            assert self.aria2_path is not None
            assert self.ffmpeg_path is not None

            mode = self.download_mode_var.get()
            selected_format = self._selected_format() if mode == "video" else None
            audio_format = self.audio_format_var.get().lower()
            command = build_download_command(
                ytdlp_path=self.ytdlp_path,
                aria2_path=self.aria2_path,
                ffmpeg_path=self.ffmpeg_path,
                auth_args=self._auth_args(),
                output_dir=output_dir,
                url=url,
                mode=mode,
                selected_format=selected_format,
                audio_format=audio_format,
                embed_subtitles=self.embed_subtitles_var.get(),
                has_subtitles=len(self.subtitle_choices) > 1,
            )
            description = (
                f"{audio_format.upper()} 音频"
                if mode == "audio"
                else f"{selected_format.label if selected_format else '最佳可用画质'} 视频"
            )
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        self.operation = "download"
        self.active_download_mode = mode
        self.cancel_requested = False
        self.active_output_dir = output_dir
        if mode == "audio":
            self.download_state_title_var.set("正在提取音频")
            self.download_state_detail_var.set(audio_format.upper())
        else:
            self.download_state_title_var.set("正在下载")
            quality = selected_format.label if selected_format else "最佳可用画质"
            self.download_state_detail_var.set(f"{quality} · MP4")
        self._show_download_progress()
        self._set_status(f"正在下载 {description}…")
        self._log(f"Downloading to: {output_dir}")
        self._set_busy(True, cancellable=True)
        threading.Thread(target=self._run_download, args=(command,), daemon=True).start()

    def _run_download(self, command: list[str]) -> None:
        try:
            creationflags = WINDOWS_CREATION_FLAGS
            if os.name == "nt":
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            self.current_process = process
            if self.cancel_requested:
                process.terminate()
            assert process.stdout is not None
            for line in process.stdout:
                cleaned = line.rstrip()
                if not cleaned:
                    continue
                self.queue.put(("log", cleaned))
                match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", cleaned)
                if match is None:
                    match = re.search(r"\((\d+(?:\.\d+)?)%\)", cleaned)
                if match:
                    self.queue.put(("progress", float(match.group(1))))
            return_code = process.wait()
            self.current_process = None
            if self.cancel_requested:
                self.queue.put(("cancelled", None))
            elif return_code != 0:
                raise RuntimeError(f"下载失败，退出码 {return_code}。")
            else:
                self.queue.put(("download_done", "下载完成。"))
        except Exception as exc:
            self.current_process = None
            if self.cancel_requested:
                self.queue.put(("cancelled", None))
            else:
                self.queue.put(("error", exc))

    def _handle_progress(self, percent: float) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(max(0.0, min(1.0, percent / 100)))
        self._set_status(f"正在下载… {percent:.1f}%")

    def _handle_download_done(self, message: str) -> None:
        self.operation = None
        self.active_download_mode = None
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1)
        self._set_busy(False)
        output_dir = self.active_output_dir or Path(self.output_dir_var.get().strip() or app_dir() / "downloads")
        self._show_download_result("success", "下载完成", f"文件已保存到 {output_dir}")
        self._set_status("")

    def _handle_cancelled(self) -> None:
        self.operation = None
        self.active_download_mode = None
        self.cancel_requested = False
        self._set_busy(False)
        self._show_download_result("cancelled", "下载已取消", "当前下载任务已经停止。")
        self._set_status("")
        self.after(1800, self._restore_download_action)

    def _handle_error(self, message: str) -> None:
        failed_operation = self.operation
        self.operation = None
        if failed_operation == "download":
            self.active_download_mode = None
        if failed_operation == "analysis":
            self._stop_parse_loading()
            self._cancel_preview_result_animation()
            self.options_loaded = False
            self.parsed_url = ""
            self.preview_card.grid_remove()
            self.empty_state.grid()
        self._set_busy(False)
        self._log(f"Error: {message}")
        if failed_operation == "download":
            self._show_download_result("error", "下载失败", self._short_download_error(message))
            self._set_status("")
            self.after(5000, self._restore_download_action)
        else:
            self.progress_frame.grid_remove()
            self._set_status(message, error=True)

    def cancel_current(self) -> None:
        if self.operation != "download":
            return
        self.cancel_requested = True
        self.cancel_button.configure(state="disabled")
        self.download_state_title_var.set("正在取消")
        self.download_state_detail_var.set("正在停止当前下载任务…")
        self._set_status("正在取消下载…")
        process = self.current_process
        if not process or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=WINDOWS_CREATION_FLAGS,
                    check=False,
                )
            else:
                process.terminate()
        except OSError:
            process.terminate()


if __name__ == "__main__":
    try:
        app = BiliDownloaderApp()
        if os.environ.get("BILIBILI_DOWNLOADER_SMOKE_TEST") == "1":
            app.withdraw()
            app.update_idletasks()
            app.destroy()
        else:
            app.mainloop()
    except Exception:
        crash_log(traceback.format_exc())
        raise
