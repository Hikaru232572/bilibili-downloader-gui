from __future__ import annotations

import ctypes
import asyncio
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
from urllib.parse import urlsplit
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from bili.auth import AuthConfig, AuthService
from bili.bilibili import BilibiliExecutor, DownloadTools, stop_process_tree
from bili.models import Component, OutputSpec, Platform, TaskSpec, TaskState
from bili.secrets import SecretVault
from bili.storage import TaskStore
from bili.tasks import TaskService
from bili.task_page import TaskPage
from bili.close_dialog import CloseDialog
from bili.windows import NotificationService, SingleInstance, TrayService, application_data_dir
from bili.messages import error_text
from bili.gallery import GalleryExecutor, output_plan, protect_work
from bili.features import LIVE_PHOTO_ENABLED
from bili.douyin_video import DouyinVideoExecutor
from bili.gallery_page import GalleryPage
from bili.link_dialog import LinkDialog
from bili.links import extract_links
from bili.media_preview import MediaPreviewService
from bili.parser import ParserHost
from bili.video_page import DouyinVideoPage

import ui_kit as ctk

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    ImageOps = None
    ImageTk = None


APP_NAME = "Bilibili Downloader"
APP_VERSION = "1.1.0-rc.1"
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
    def __init__(self, *, data_dir: Path | None = None) -> None:
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
        self.parsed_info: dict[str, Any] = {}
        self._view_generation = 0
        self._destroying = False
        self._page_task_id: str | None = None
        self._last_submission = None
        self._task_progress: dict[str, dict] = {}
        self._close_dialog = None
        self._link_dialog = None
        self.current_work = None
        self._parsed_input = None
        self.instance_gate = None

        self.ytdlp_path = find_tool("yt-dlp")
        self.aria2_path = find_tool("aria2c")
        self.ffmpeg_path = find_tool("ffmpeg")
        self.ffprobe_path = find_tool("ffprobe")

        self.data_dir = data_dir or Path(os.environ.get("BILI_DATA_DIR") or
                                        str(Path(os.environ.get("LOCALAPPDATA", str(app_dir()))) / "Bili"))
        self.task_store = TaskStore(self.data_dir / "tasks.sqlite3")
        self.vault = SecretVault(self.data_dir / "secrets")
        self.auth_service = AuthService(self.task_store, revoked=self._auth_revoked)
        self.parser_host = ParserHost(self.auth_service)
        self.media_preview = MediaPreviewService(self.data_dir/'preview-cache',self.auth_service,
                                                 self.parser_host,self.ffprobe_path)
        self.task_service = TaskService(self.task_store, self._execute_task)
        tray_icon = app_dir()/APP_ICON_FILE_NAME
        if not tray_icon.is_file():
            tray_icon = resource_dir()/APP_ICON_FILE_NAME
        self.tray = TrayService(lambda action: self.queue.put(("windows_action",action)), tray_icon)
        self.notifications = NotificationService(lambda: self.queue.put(("windows_action","tasks")))

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
        bundle = Path(getattr(sys,'_MEIPASS',str(app_dir())))
        self.gallery_page = GalleryPage(self.main_page,self.media_preview,self.download_gallery,
                                        self._view_generation,bundle/'bin/vlc')
        self.douyin_video_page=DouyinVideoPage(self.main_page,self.download_gallery)
        self.task_page = TaskPage(self, self.task_service, self.task_store,
                                  self.show_main_page, self._open_task_folder,self._task_progress)
        apply_native_window_style(self)
        self.url_var.trace_add("write", self._reset_options_on_url_change)
        self._set_busy(False)
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self.request_close)

    def _auth_revoked(self, platform, epoch):
        if hasattr(self,'parser_host'):
            self.parser_host.revoke(platform)
            self.media_preview.cancel(platform=platform)
            self.queue.put(('auth_revoked',platform))
        if hasattr(self,"task_service"):
            for record in self.task_store.tasks():
                if record.spec.platform == platform and record.state in {TaskState.RUNNING,TaskState.VALIDATING}:
                    self.task_service.cancel(record.spec.task_id)

    def request_close(self):
        behavior = self.task_store.get_preference("close_behavior","ask")
        if behavior in {"exit","tray"}:
            self._apply_close_choice(behavior,False)
        elif self._close_dialog is None or not self._close_dialog.winfo_exists():
            self._close_dialog = CloseDialog(self,self._apply_close_choice)
            apply_native_window_style(self._close_dialog)

    def _apply_close_choice(self, choice, remember):
        if choice == "tray":
            try:
                self.tray.start()
            except Exception as exc:
                self._set_status(str(exc),error=True)
                return
            if remember:
                self.task_store.set_preference("close_behavior","tray")
            self.withdraw()
        else:
            if remember:
                self.task_store.set_preference("close_behavior","exit")
            self.destroy()

    def restore_window(self, page="home"):
        self.deiconify()
        if page == "tasks":
            self.show_task_page()
        elif page == "settings":
            self.show_settings_page()
        else:
            self.show_main_page()
        self.lift()
        self.focus_force()

    async def _execute_task(self, context):
        if context.record.spec.platform != Platform.BILIBILI:
            executor_type=DouyinVideoExecutor if any(component in context.record.spec.components
                          for component in (Component.VIDEO,Component.AUDIO)) else GalleryExecutor
            kwargs={'ffmpeg':self.ffmpeg_path} if executor_type==DouyinVideoExecutor else {}
            executor = executor_type(self.task_store,self.vault,self.auth_service,self.parser_host,
                                     ffprobe=self.ffprobe_path,**kwargs)
            return await executor(context)
        tools = DownloadTools(self.ytdlp_path, self.aria2_path, self.ffmpeg_path, self.ffprobe_path)
        executor = BilibiliExecutor(self.task_store, self.vault, self.auth_service, tools,
                                    self._build_task_command)
        return await executor(context)

    @staticmethod
    def _build_task_command(spec, tools, auth_args, directory, url):
        return build_download_command(ytdlp_path=tools.ytdlp, aria2_path=tools.aria2,
            ffmpeg_path=tools.ffmpeg, auth_args=auth_args, output_dir=directory, url=url,
            mode="audio" if Component.AUDIO in spec.components else "video",
            selected_format=FormatChoice("任务所选画质", "task", spec.format_selector or "bv*+ba/b"),
            audio_format=spec.audio_format or "mp3", embed_subtitles=bool(spec.subtitle_language),
            has_subtitles=bool(spec.subtitle_language))

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True
        try:
            if self.current_process is not None:
                stop_process_tree(self.current_process)
            self.parser_host.close()
            self.gallery_page.reset()
            self.media_preview.close()
            widgets = []
            def collect(widget):
                widgets.append(widget)
                for child in widget.winfo_children():
                    collect(child)
            collect(self)
            for callback in self.tk.splitlist(self.tk.call("after", "info")):
                script = self.tk.call("after", "info", callback)[0]
                command = self.tk.splitlist(script)[0]
                owner = next((w for w in widgets if command in (w._tclCommands or [])), self)
                owner.after_cancel(callback)
            self.task_service.close()
            self.tray.stop()
            self.task_store.close()
        finally:
            super().destroy()

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
        self.tasks_button = ctk.RoundedButton(header, text="任务", width=70, height=36,
            corner_radius=18, fg_color="#EFEEEA", hover_color="#E3E1DB", text_color="#292825",
            bg_color="#F6F5F1", command=self.show_task_page)
        self.tasks_button.grid(row=0, column=2, rowspan=2, sticky=tk.E, padx=(10,0))

        self.url_bar = ctk.URLInputBar(
            outer,
            variable=self.url_var,
            command=self.fetch_options,
            placeholder="粘贴 B站、抖音、小红书链接或分享文案…",
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
            text="把一个作品放进来",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#201F1C",
        ).grid(row=1, column=0, pady=(18, 0))
        ctk.CTkLabel(
            self.empty_state,
            text="支持 B站视频、抖音视频与图文、小红书图文",
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
        return self.data_dir / SETTINGS_FILE_NAME

    def _load_settings(self) -> dict[str, Any]:
        try:
            path = self._settings_path()
            if not path.exists():
                legacy = app_dir()/SETTINGS_FILE_NAME
                if legacy.exists() and legacy != path:
                    path = legacy
            data = json.loads(path.read_text(encoding="utf-8"))
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
        self._sync_bilibili_auth()

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
        close_var = tk.StringVar(value=self.task_store.get_preference("close_behavior","ask"))
        platform_var = tk.StringVar(value=Platform.BILIBILI.value)
        drafts = {platform:self.auth_service.config(platform) for platform in Platform}
        active_platform = [Platform.BILIBILI]
        def capture_auth():
            return AuthConfig('guest' if auth_var.get()=='none' else auth_var.get(),browser_var.get(),
                              profile_var.get().strip(),cookie_var.get().strip())
        drafts[Platform.BILIBILI] = capture_auth()
        def load_auth(config):
            auth_var.set('none' if config.mode=='guest' else config.mode)
            browser_var.set(config.browser);profile_var.set(config.profile);cookie_var.set(config.cookie_path)
        def switch_platform(*_args):
            drafts[active_platform[0]] = capture_auth()
            active_platform[0] = Platform(platform_var.get())
            load_auth(drafts[active_platform[0]])
        platform_var.trace_add('write',switch_platform)

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
            text=f"Download & Login Preferences · v{APP_VERSION}",
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
        platform_control = ctk.SegmentedControl(cookie_inner,
            options=[('B站','bilibili'),('抖音','douyin'),('小红书','xiaohongshu')],
            variable=platform_var,bg_color='#EFEEEA')
        platform_control.grid(row=0,column=0,sticky=tk.EW,pady=(0,8))
        ctk.CTkLabel(
            cookie_inner,
            text="Cookie",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=10),
            text_color="#201F1C",
            anchor="w",
        ).grid(row=1, column=0, sticky=tk.W)

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
                cookie_card.configure(height=228)
                browser_content.grid(row=3, column=0, sticky=tk.EW, pady=(8, 0))
            elif mode == "cookie_file":
                cookie_card.configure(height=184)
                cookie_content.grid(row=3, column=0, sticky=tk.EW, pady=(8, 0))
            else:
                cookie_card.configure(height=162)
                none_content.grid(row=3, column=0, sticky=tk.EW, pady=(8, 0))
            settings_error_var.set("")

        auth_var.trace_add("write", update_auth_widgets)
        auth_control = ctk.SegmentedControl(
            cookie_inner,
            options=[("浏览器", "browser"), ("cookies.txt", "cookie_file"), ("不使用", "none")],
            variable=auth_var,
            command=update_auth_widgets,
            bg_color="#EFEEEA",
        )
        auth_control.grid(row=2, column=0, sticky=tk.EW, pady=(6, 0))
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
        def clear_auth():
            load_auth(AuthConfig())
            settings_error_var.set('保存后清除此平台的认证引用；外部 Cookie 文件和浏览器登录状态保留。')
        clear_button = tk.Button(help_row,text='清除此平台认证',command=clear_auth,bg='#F6F5F1',
                                 fg='#706D67',relief='flat',cursor='hand2',font=('Segoe UI',9))
        clear_button.grid(row=1,column=2,sticky=tk.W,pady=(3,0))
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
        close_row = ctk.CTkFrame(footer,fg_color="transparent")
        close_row.grid(row=1,column=0,columnspan=2,sticky=tk.EW,pady=(12,0))
        ctk.CTkLabel(close_row,text="关闭窗口",font=("Segoe UI",10)).pack(side=tk.LEFT,padx=(0,10))
        close_control = ctk.SegmentedControl(close_row,
            options=[("每次询问","ask"),("直接退出","exit"),("后台托管","tray")],
            variable=close_var,bg_color="#F6F5F1")
        close_control.pack(side=tk.LEFT,fill=tk.X,expand=True)

        def save() -> None:
            drafts[active_platform[0]] = capture_auth()
            for platform, config in drafts.items():
                if config.mode=='cookie_file' and (not config.cookie_path or not Path(config.cookie_path).is_file()):
                    settings_error_var.set("请选择有效的 cookies.txt 文件。")
                    return
            old_auth = (
                self.auth_mode_var.get(),
                self.browser_var.get(),
                self.browser_profile_var.get(),
                self.cookie_path_var.get(),
            )
            self.output_dir_var.set(output_var.get().strip() or str(app_dir() / "downloads"))
            bili_auth = drafts[Platform.BILIBILI]
            self.auth_mode_var.set('none' if bili_auth.mode=='guest' else bili_auth.mode)
            self.browser_var.set(bili_auth.browser)
            self.browser_profile_var.set(bili_auth.profile)
            self.cookie_path_var.set(bili_auth.cookie_path)
            try:
                self._save_settings()
                self.task_store.set_preference("close_behavior",close_var.get())
                for platform,config in drafts.items():
                    if config != self.auth_service.config(platform):self.auth_service.configure(platform,config)
            except OSError as exc:
                settings_error_var.set(f"设置保存失败：{exc}")
                return
            new_auth = (
                self.auth_mode_var.get(),
                self.browser_var.get(),
                self.browser_profile_var.get(),
                self.cookie_path_var.get(),
            )
            if self.options_loaded and self.current_work is None and old_auth != new_auth:
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
            'platform_var':platform_var,'drafts':drafts,'active_platform':active_platform,
            'platform_control':platform_control,'clear_button':clear_button,
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
            "close_var": close_var,
            "close_control": close_control,
        }

    def _reset_settings_drafts(self) -> None:
        state = self.settings_state
        state['drafts'].update({platform:self.auth_service.config(platform) for platform in Platform})
        state['active_platform'][0] = Platform.BILIBILI
        state['platform_var'].set('bilibili')
        state["output_var"].set(self.output_dir_var.get())
        state["auth_var"].set(self.auth_mode_var.get())
        state["browser_var"].set(self.browser_var.get())
        state["profile_var"].set(self.browser_profile_var.get())
        state["cookie_var"].set(self.cookie_path_var.get())
        state["error_var"].set("")
        state["close_var"].set(self.task_store.get_preference("close_behavior","ask"))

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
        self.task_page.pack_forget()
        self.settings_page.pack(fill=tk.BOTH, expand=True)
        self.settings_page.tkraise()

    def show_main_page(self) -> None:
        if self.main_page.winfo_manager():
            return
        self.settings_page.pack_forget()
        self.task_page.pack_forget()
        self.main_page.pack(fill=tk.BOTH, expand=True, padx=42, pady=34)
        self.main_page.tkraise()

    def show_task_page(self):
        self.main_page.pack_forget()
        self.settings_page.pack_forget()
        self.task_page.refresh()
        self.task_page.pack(fill=tk.BOTH, expand=True)

    def _open_task_folder(self, directory):
        path = Path(directory)
        try:
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open",str(path)])
            else:
                subprocess.Popen(["xdg-open",str(path)])
        except OSError:
            self.task_page.message.set("无法打开该保存目录，请检查目录是否仍可访问。")

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
        if self._parsed_input == self.url_var.get() or normalize_url(self.url_var.get()) == self.parsed_url:
            return
        self._cancel_preview_result_animation()
        self._view_generation += 1
        self._page_task_id = None
        self.parsed_info = {}
        self.current_work = None
        self._parsed_input = None
        if hasattr(self,'gallery_page'):
            self.gallery_page.reset()
            self.gallery_page.grid_remove()
            self.douyin_video_page.work=None
            self.douyin_video_page.grid_remove()
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
        if hasattr(self,'gallery_page'):
            self.gallery_page.set_busy(busy)
            self.douyin_video_page.set_busy(busy)
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
            for _ in range(100):
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "fetch_done":
                    self._handle_fetch_done(payload)
                elif kind == "windows_action":
                    if payload == "exit":
                        self.destroy()
                        return
                    self.restore_window(payload)
                elif kind == "fetch_result":
                    generation, info = payload
                    if generation == self._view_generation:
                        self._handle_fetch_done(info)
                elif kind == "fetch_error":
                    generation, error = payload
                    if generation == self._view_generation:
                        self._handle_error(error)
                elif kind == 'work_result':
                    generation, source, work = payload
                    if generation == self._view_generation:
                        self._handle_work_done(source,work)
                elif kind == 'auth_revoked':
                    if self.current_work and self.current_work.platform == payload:
                        self.gallery_page.reset()
                        self.gallery_page.grid_remove()
                        self.douyin_video_page.work=None
                        self.douyin_video_page.grid_remove()
                        self.current_work = None
                        self.options_loaded = False
                        self._set_status('认证已更改，请重新解析该平台作品。')
                    self.media_preview.revoke(payload)
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
        try:
            for _ in range(100):self.gallery_page.handle(self.media_preview.events.get_nowait())
        except queue.Empty:pass
        try:
            for _ in range(100):
                self._handle_task_event(self.task_service.events.get_nowait())
        except queue.Empty:
            pass
        if self.instance_gate and self.instance_gate.activation_pending():
            self.restore_window()
        self.after(100, self._drain_queue)

    def _handle_task_event(self, event):
        if event["operation"] == "batch_finished":
            self.task_page.refresh()
            self.notifications.show(event)
            return
        task_id = event.get("task_id")
        if not task_id:
            return
        record = self.task_store.task(task_id)
        if event.get("attempt_id") and event["attempt_id"] != record.attempt_id:
            return
        operation = event["operation"]
        if operation=="task_started":
            self._task_progress[task_id] = {"attempt_id":record.attempt_id,"percent":None,"phase":"downloading"}
        elif operation in {"progress","phase"}:
            progress = self._task_progress.setdefault(task_id,{"attempt_id":record.attempt_id})
            if operation=="progress":
                progress["percent"] = event["percent"]
            else:
                progress["phase"] = event["phase"]
        self.task_page.refresh()
        if task_id != self._page_task_id:
            return
        if self.current_work:
            page=self.gallery_page if self.current_work.content_type=='gallery' else self.douyin_video_page
            page.task_state(record,self._task_progress.get(task_id))
            return
        if operation == "task_started":
            self.active_output_dir = Path(record.spec.output_dir)
            self._show_download_progress()
            self.cancel_button.configure(state="normal")
            self.download_state_title_var.set("正在下载")
            self.download_state_detail_var.set(record.spec.title)
            self._set_busy(self.operation == "analysis")
            self.cancel_button.configure(state="normal")
            self.download_percent_var.set("正在连接")
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        elif operation == "progress":
            self._handle_progress(event["percent"])
        elif operation == "phase":
            self.download_state_title_var.set({"downloading":"正在下载","processing":"正在处理",
                                               "validating":"正在校验文件"}[event["phase"]])
            if event["phase"] in {"processing","validating"}:
                self.download_percent_var.set("处理中" if event["phase"] == "processing" else "校验中")
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
        elif operation == "task_finished":
            if record.state == TaskState.SUCCEEDED:
                self._handle_download_done("下载完成。")
            elif record.state == TaskState.CANCELLED:
                self._handle_cancelled()
            elif record.state == TaskState.PARTIAL:
                self._show_download_result("error", "部分成功", record.message or "已保存可用文件，请在任务页查看未完成部分。")
            else:
                self._show_download_result("error", "下载失败", error_text(record.error_code))
            self._set_busy(self.operation == "analysis")

    def fetch_options(self) -> None:
        if self.operation is not None:
            return
        links = extract_links(self.url_var.get())
        if not links:
            self._set_status('请输入 B站、抖音或小红书的单个作品链接或分享文案。',error=True)
            return
        if len(links)>1:
            if self._link_dialog is None or not self._link_dialog.winfo_exists():
                self._link_dialog = LinkDialog(self,links,self._fetch_link)
                apply_native_window_style(self._link_dialog)
            return
        self._fetch_link(links[0])

    def _fetch_link(self,link) -> None:
        try:
            url = link.url
            command = self._base_ytdlp_command() + [
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--socket-timeout",
                "30",
                "--no-playlist",
                *self._auth_args(),
                url,
            ] if link.platform == Platform.BILIBILI else None
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        self.operation = "analysis"
        self._view_generation += 1
        self._page_task_id = None
        self.options_loaded = False
        self.current_work = None
        self._parsed_input = self.url_var.get()
        self.gallery_page.reset()
        self.gallery_page.grid_remove()
        self.douyin_video_page.grid_remove()
        self.douyin_video_page.work=None
        self.parsed_url = url
        self._cancel_preview_result_animation()
        self.preview_card.grid_remove()
        self.progress_frame.grid_remove()
        self.empty_state.grid_remove()
        self._set_status("正在解析作品…")
        self._set_busy(True)
        self._start_parse_loading()
        if command is not None:
            threading.Thread(target=self._run_fetch,args=(command,self._view_generation),daemon=True).start()
        else:
            threading.Thread(target=self._run_platform_fetch,args=(link,self._view_generation),daemon=True).start()

    def _run_platform_fetch(self,link,generation):
        try:
            source,work = asyncio.run(self.parser_host.parse(link,view_generation=generation))
            self.queue.put(('work_result',(generation,source,work)))
        except Exception as exc:
            code = getattr(exc,'code',None) or 'parser_failed'
            self.queue.put(('fetch_error',(generation,error_text(code))))

    def _handle_work_done(self,source,work):
        if work.content_type == 'unsupported':
            self._handle_error('本期不支持小红书普通视频笔记。')
            return
        self.current_work = work
        self.parsed_url = source
        self.options_loaded = True
        self.operation = None
        self._stop_parse_loading()
        self.empty_state.grid_remove()
        self.preview_card.grid_remove()
        if work.content_type=='gallery':
            self.gallery_page.show_work(work,self._view_generation)
        else:
            self.douyin_video_page.show_work(work)
        self._set_busy(False)
        self.url_bar.show_start()
        self._set_status('')

    def download_gallery(self):
        source_ref = work_ref = None
        try:
            work = self.current_work
            if not self.options_loaded or not work or self._parsed_input != self.url_var.get():
                raise ValueError('请先解析当前作品。')
            selected = self.gallery_page.selection() if work.content_type=='gallery' else tuple(item.item_id for item in work.items)
            gallery_components = (Component.STILL,Component.MOTION) if LIVE_PHOTO_ENABLED else (Component.STILL,)
            components = gallery_components if work.content_type=='gallery' else self.douyin_video_page.components()
            if not selected:raise ValueError('请先选择至少一张图片。')
            if not components:raise ValueError('请至少选择视频或音频。')
            audio_format=self.douyin_video_page.audio_policy.get() if Component.AUDIO in components else None
            page=self.gallery_page if work.content_type=='gallery' else self.douyin_video_page
            directory = Path(self.output_dir_var.get().strip() or app_dir()/'downloads').resolve()
            signature = (self._view_generation,work.work_id,str(directory),selected,components,audio_format)
            if self._last_submission and self._last_submission[0] == signature:
                previous = self.task_store.task(self._last_submission[1])
                if previous.state in {TaskState.QUEUED,TaskState.RUNNING,TaskState.VALIDATING}:
                    self._page_task_id = previous.spec.task_id
                    page.status_var.set('该任务已在队列中。')
                    return
            source_ref = self.vault.put(self.parsed_url)
            work_ref = protect_work(self.vault,work)
            spec = TaskSpec(work.platform,work.work_id,work.title or work.work_id,source_ref,str(directory),
                            selected,components,audio_format=audio_format,work_ref=work_ref,auth_ref=work.platform.value)
            self.task_service.submit(spec,output_plan(spec,work))
            self._page_task_id = spec.task_id
            self._last_submission = (signature,spec.task_id)
            page.status_var.set('已加入队列，下载期间可以继续解析其他作品。')
        except Exception as exc:
            for reference in (source_ref,work_ref):
                if reference:self.vault.delete(reference)
            self._set_status(str(exc) if isinstance(exc,ValueError) else error_text(getattr(exc,'code',None)),error=True)

    def _run_fetch(self, command: list[str], generation=None) -> None:
        generation = self._view_generation if generation is None else generation
        try:
            command = list(command)
            source_url = command.pop()
            command += ["--batch-file","-"]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=WINDOWS_CREATION_FLAGS,
            )
            self.current_process = process
            try:
                stdout, stderr = process.communicate(input=source_url+"\n",timeout=60)
            except subprocess.TimeoutExpired:
                stop_process_tree(process)
                process.communicate()
                raise RuntimeError("解析超时，请重试。")
            self.current_process = None
            if process.returncode != 0:
                code = "needs_auth_or_permission" if any(marker in stderr.lower() for marker in
                        ("cookie","403","401","login","sign in")) else "parser_failed"
                raise RuntimeError(error_text(code))
            self.queue.put(("fetch_result", (generation,json.loads(stdout))))
        except Exception as exc:
            self.current_process = None
            message = str(exc) if isinstance(exc,RuntimeError) else error_text("parser_failed")
            self.queue.put(("fetch_error", (generation,message)))

    def _handle_fetch_done(self, info: dict[str, Any]) -> None:
        self.current_work = None
        self.gallery_page.reset()
        self.gallery_page.grid_remove()
        self.douyin_video_page.grid_remove()
        self.douyin_video_page.work=None
        item, _used_first_entry = extract_info_item(info)
        self.parsed_info = item
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

    def _sync_bilibili_auth(self):
        config = AuthConfig(mode="guest" if self.auth_mode_var.get() == "none" else self.auth_mode_var.get(),
            browser=self.browser_var.get(), profile=self.browser_profile_var.get().strip(),
            cookie_path=self.cookie_path_var.get().strip())
        if config != self.auth_service.config(Platform.BILIBILI):
            self.auth_service.configure(Platform.BILIBILI, config)

    def download(self) -> None:
        try:
            url = self.parsed_url
            if not self.options_loaded or not url or (self._parsed_input is not None and self._parsed_input != self.url_var.get()):
                raise ValueError("请先解析当前视频链接。")
            output_dir = Path(self.output_dir_var.get().strip() or app_dir() / "downloads").resolve()
            self._ensure_download_tools()
            self._auth_args()
            self._sync_bilibili_auth()
            mode = self.download_mode_var.get()
            audio_format = self.audio_format_var.get().lower()
            if mode == "audio" and audio_format not in {"mp3", "wav"}:
                raise ValueError("请选择 MP3 或 WAV。")
            component = Component.AUDIO if mode == "audio" else Component.VIDEO
            signature = (self._view_generation,url,str(output_dir),mode,
                         self._selected_format().format_string if mode=="video" else audio_format,
                         self.embed_subtitles_var.get() if mode=="video" else False)
            if self._last_submission and self._last_submission[0] == signature:
                previous = self.task_store.task(self._last_submission[1])
                if previous.state in {TaskState.QUEUED,TaskState.RUNNING,TaskState.VALIDATING}:
                    self._page_task_id = previous.spec.task_id
                    self._set_status("该任务已在队列中，可在任务页查看。")
                    return
            work_id = str(self.parsed_info.get("id") or urlsplit(url).path.rstrip("/").split("/")[-1])
            source_ref = self.vault.put(url)
            spec = TaskSpec(Platform.BILIBILI, work_id, self.video_title_var.get(), source_ref,
                str(output_dir), (work_id,), (component,),
                format_selector=self._selected_format().format_string if mode == "video" else None,
                audio_format=audio_format if mode == "audio" else None,
                subtitle_language="all" if mode == "video" and self.embed_subtitles_var.get()
                    and len(self.subtitle_choices)>1 else None,
                auth_ref=Platform.BILIBILI.value)
            output = OutputSpec(spec.task_id,work_id,component,1)
            try:
                self.task_service.submit(spec,(output,))
            except Exception:
                self.vault.delete(source_ref)
                raise
            self._page_task_id = spec.task_id
            self._last_submission = (signature,spec.task_id)
            self.active_download_mode = mode
            self.active_output_dir = output_dir
            self._show_download_progress()
            self.download_state_title_var.set("已加入下载队列")
            self.download_state_detail_var.set("下载期间可以继续解析其他作品")
            self.download_percent_var.set("排队中")
            self.cancel_button.configure(state="normal")
            self._set_busy(False)
            self.cancel_button.configure(state="normal")
            self.task_page.refresh()
        except Exception as exc:
            self._set_status(str(exc),error=True)

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
        if self._page_task_id:
            record = self.task_store.task(self._page_task_id)
            if record.state in {TaskState.QUEUED, TaskState.RUNNING, TaskState.VALIDATING, TaskState.CANCEL_REQUESTED}:
                self.task_service.cancel(self._page_task_id)
                self.download_state_title_var.set("正在取消")
                self.cancel_button.configure(state="disabled")



if __name__ == "__main__":
    if "--parser-worker" in sys.argv:
        from bili.parser_worker import main as parser_worker_main
        parser_worker_main()
        raise SystemExit(0)
    if "--native-worker" in sys.argv:
        from bili.native_worker import main as native_worker_main
        native_worker_main()
        raise SystemExit(0)
    instance = None
    try:
        if os.name == "nt":
            instance = SingleInstance(application_data_dir())
            if not instance.owner:
                instance.close()
                raise SystemExit(0)
        app = BiliDownloaderApp()
        app.instance_gate = instance
        if os.environ.get("BILIBILI_DOWNLOADER_SMOKE_TEST") == "1":
            app.withdraw()
            app.update_idletasks()
            app.destroy()
        else:
            app.mainloop()
    except Exception:
        crash_log(traceback.format_exc())
        raise
    finally:
        if instance is not None:
            instance.close()
