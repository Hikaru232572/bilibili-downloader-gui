from __future__ import annotations

import json
import os
import queue
import traceback
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any


import ui_kit as ctk


APP_NAME = "Bilibili Downloader"
DEFAULT_COOKIE_PATH = Path("D:/Claude/cookies.txt")
WINDOWS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ARIA2_ARGS = "aria2c:-x 16 -s 16 -k 1M"
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
    if value.startswith("BV") and "/" not in value:
        return f"https://www.bilibili.com/video/{value}"
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


class BiliDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1020x760")
        self.minsize(880, 680)

        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.format_choices: list[FormatChoice] = []
        self.subtitle_choices: list[SubtitleChoice] = []

        self.ytdlp_path = find_tool("yt-dlp")
        self.aria2_path = find_tool("aria2c")
        self.ffmpeg_path = find_tool("ffmpeg")

        self.url_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(app_dir() / "downloads"))
        self.auth_mode_var = tk.StringVar(value="cookie_file" if DEFAULT_COOKIE_PATH.exists() else "browser")
        self.browser_var = tk.StringVar(value="edge")
        self.browser_profile_var = tk.StringVar()
        self.cookie_path_var = tk.StringVar(value=str(DEFAULT_COOKIE_PATH) if DEFAULT_COOKIE_PATH.exists() else "")
        self.quality_var = tk.StringVar(value="Best available video + best audio")
        self.subtitle_var = tk.StringVar(value="No subtitles")
        self.status_var = tk.StringVar(value="Ready")
        self.video_title_var = tk.StringVar(value="Paste a Bilibili link or BV id to begin.")
        self.options_hint_var = tk.StringVar(value="Analyze the video to load quality and subtitle choices.")
        self.activity_button_var = tk.StringVar(value="Show activity")
        self.progress_var = tk.DoubleVar(value=0)
        self.activity_visible = False
        self.options_loaded = False
        self.url_var.trace_add("write", self._reset_options_on_url_change)

        self._configure_style()
        self._build_ui()
        self._set_busy(False)
        self.after(100, self._drain_queue)

    def _configure_style(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(bg="#eef2f7")

    def _build_ui(self) -> None:
        self.geometry("920x760")
        self.minsize(820, 700)

        outer = ctk.CTkFrame(self, fg_color="#eef2f7")
        outer.pack(fill=tk.BOTH, expand=True, padx=28, pady=24)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 16))
        ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=ctk.CTkFont(family="Segoe UI", size=30, weight="bold"),
            text_color="#0f172a",
        ).pack(anchor=tk.W)
        ctk.CTkLabel(
            header,
            text="Paste a video link, load options, download. Native subtitles only.",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color="#64748b",
        ).pack(anchor=tk.W, pady=(3, 0))

        card = ctk.CTkFrame(outer, fg_color="#ffffff", corner_radius=18, border_width=1, border_color="#dbe3ef")
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)

        self._build_main_card(card)

    def _build_main_card(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            parent,
            text="Video",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky=tk.W, padx=24, pady=(22, 0))
        ctk.CTkLabel(
            parent,
            textvariable=self.video_title_var,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#64748b",
        ).grid(row=1, column=0, sticky=tk.W, padx=24, pady=(4, 12))

        input_row = ctk.CTkFrame(parent, fg_color="transparent")
        input_row.grid(row=2, column=0, sticky=tk.EW, padx=24)
        input_row.columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(
            input_row,
            textvariable=self.url_var,
            height=44,
            corner_radius=10,
            border_color="#cbd5e1",
            placeholder_text="https://www.bilibili.com/video/BV...",
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self.url_entry.grid(row=0, column=0, sticky=tk.EW)
        ctk.CTkButton(
            input_row,
            text="Paste",
            width=96,
            height=44,
            corner_radius=10,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=self._paste_url,
        ).grid(row=0, column=1, padx=(10, 0))

        save_row = ctk.CTkFrame(parent, fg_color="transparent")
        save_row.grid(row=3, column=0, sticky=tk.EW, padx=24, pady=(16, 0))
        save_row.columnconfigure(0, weight=1)
        self._field_label(save_row, "Save to").grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
        self.output_entry = ctk.CTkEntry(
            save_row,
            textvariable=self.output_dir_var,
            height=40,
            corner_radius=10,
            border_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.output_entry.grid(row=1, column=0, sticky=tk.EW)
        ctk.CTkButton(
            save_row,
            text="Choose",
            width=96,
            height=40,
            corner_radius=10,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=self._pick_output_dir,
        ).grid(row=1, column=1, padx=(10, 0))

        self._build_cookie_strip(parent)
        self._build_options_strip(parent)
        self._build_status_strip(parent)
        self._build_log_panel(parent)

    def _build_cookie_strip(self, parent: ctk.CTkFrame) -> None:
        cookies = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=14)
        cookies.grid(row=4, column=0, sticky=tk.EW, padx=24, pady=(16, 0))
        cookies.columnconfigure(1, weight=1)

        ctk.CTkLabel(cookies, text="Cookies", text_color="#334155", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky=tk.W, padx=(16, 0), pady=(14, 0)
        )
        mode_row = ctk.CTkFrame(cookies, fg_color="transparent")
        mode_row.grid(row=0, column=1, sticky=tk.EW, padx=(18, 16), pady=(14, 0))
        mode_row.columnconfigure(4, weight=1)

        ctk.CTkRadioButton(
            mode_row,
            text="File",
            value="cookie_file",
            variable=self.auth_mode_var,
            command=self._update_auth_state,
        ).grid(row=0, column=0, sticky=tk.W, padx=(0, 12))

        ctk.CTkRadioButton(
            mode_row,
            text="Browser",
            value="browser",
            variable=self.auth_mode_var,
            command=self._update_auth_state,
        ).grid(row=0, column=1, sticky=tk.W, padx=(0, 12))

        ctk.CTkRadioButton(
            mode_row,
            text="None",
            value="none",
            variable=self.auth_mode_var,
            command=self._update_auth_state,
        ).grid(row=0, column=2, sticky=tk.W)

        self.cookie_row = ctk.CTkFrame(cookies, fg_color="transparent")
        self.cookie_row.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=16, pady=(12, 0))
        self.cookie_row.columnconfigure(1, weight=1)

        self.cookie_browse_button = ctk.CTkButton(
            self.cookie_row,
            text="Pick cookies.txt",
            height=38,
            corner_radius=10,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=self._pick_cookie_file,
        )
        self.cookie_browse_button.grid(row=0, column=0, sticky=tk.W)
        self.cookie_entry = ctk.CTkEntry(
            self.cookie_row,
            textvariable=self.cookie_path_var,
            height=38,
            corner_radius=10,
            border_color="#cbd5e1",
        )
        self.cookie_entry.grid(row=0, column=1, sticky=tk.EW, padx=(10, 0))

        self.browser_row = ctk.CTkFrame(cookies, fg_color="transparent")
        self.browser_row.grid(row=2, column=0, columnspan=2, sticky=tk.EW, padx=16, pady=(10, 0))
        self.browser_row.columnconfigure(3, weight=1)
        ctk.CTkLabel(self.browser_row, text="Browser", text_color="#64748b").grid(row=0, column=0, sticky=tk.W)
        self.browser_combo = ctk.CTkOptionMenu(
            self.browser_row,
            values=["edge", "chrome", "firefox", "brave", "vivaldi", "opera"],
            variable=self.browser_var,
            width=130,
            height=38,
            corner_radius=10,
        )
        self.browser_combo.grid(row=0, column=1, sticky=tk.W, padx=(10, 0))
        ctk.CTkLabel(self.browser_row, text="Profile", text_color="#64748b").grid(row=0, column=2, sticky=tk.W, padx=(16, 0))
        self.browser_profile_entry = ctk.CTkEntry(
            self.browser_row,
            textvariable=self.browser_profile_var,
            height=38,
            corner_radius=10,
            border_color="#cbd5e1",
            placeholder_text="Default or Profile 1",
        )
        self.browser_profile_entry.grid(row=0, column=3, sticky=tk.EW, padx=(10, 0))

        hint = (
            "Logged-in videos may need cookies. If browser mode fails, export cookies.txt and pick it here."
        )
        help_row = ctk.CTkFrame(cookies, fg_color="transparent")
        help_row.grid(row=3, column=0, columnspan=2, sticky=tk.EW, padx=16, pady=(10, 14))
        help_row.columnconfigure(0, weight=1)
        ctk.CTkLabel(help_row, text=hint, text_color="#64748b", anchor="w").grid(row=0, column=0, sticky=tk.W)
        ctk.CTkButton(
            help_row,
            text="Chrome/Edge",
            width=104,
            height=34,
            corner_radius=9,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=lambda: webbrowser.open(CHROME_COOKIE_EXTENSION_URL),
        ).grid(
            row=0, column=1, padx=(10, 0)
        )
        ctk.CTkButton(
            help_row,
            text="Firefox",
            width=86,
            height=34,
            corner_radius=9,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=lambda: webbrowser.open(FIREFOX_COOKIE_EXTENSION_URL),
        ).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._update_auth_state()

    def _build_options_strip(self, parent: ctk.CTkFrame) -> None:
        options = ctk.CTkFrame(parent, fg_color="transparent")
        options.grid(row=5, column=0, sticky=tk.EW, padx=24, pady=(16, 0))
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)

        self._field_label(options, "Quality").grid(row=0, column=0, sticky=tk.W)
        self._field_label(options, "Subtitles").grid(row=0, column=1, sticky=tk.W, padx=(14, 0))
        self.quality_combo = ctk.CTkOptionMenu(
            options,
            variable=self.quality_var,
            state="disabled",
            values=[self.quality_var.get()],
            height=40,
            corner_radius=10,
            fg_color="#f8fafc",
            button_color="#cbd5e1",
            button_hover_color="#94a3b8",
            text_color="#0f172a",
        )
        self.quality_combo.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))
        self.subtitle_combo = ctk.CTkOptionMenu(
            options,
            variable=self.subtitle_var,
            state="disabled",
            values=["No native subtitles found"],
            height=40,
            corner_radius=10,
            fg_color="#f8fafc",
            button_color="#cbd5e1",
            button_hover_color="#94a3b8",
            text_color="#0f172a",
        )
        self.subtitle_combo.grid(row=1, column=1, sticky=tk.EW, padx=(14, 0), pady=(6, 0))
        ctk.CTkLabel(options, textvariable=self.options_hint_var, text_color="#64748b").grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 0)
        )

    def _build_status_strip(self, parent: ctk.CTkFrame) -> None:
        status = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=14)
        status.grid(row=6, column=0, sticky=tk.EW, padx=24, pady=(18, 0))
        status.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            status,
            textvariable=self.status_var,
            text_color="#334155",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky=tk.W, padx=16, pady=(14, 0))
        self.progress = ctk.CTkProgressBar(status, mode="determinate", progress_color="#10b981")
        self.progress.grid(row=1, column=0, columnspan=4, sticky=tk.EW, padx=16, pady=(10, 14))
        self.progress.set(0)

        self.fetch_button = ctk.CTkButton(
            status,
            text="Analyze",
            width=104,
            height=42,
            corner_radius=10,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=self.fetch_options,
        )
        self.fetch_button.grid(row=0, column=1, padx=(12, 0), pady=(14, 0))
        self.download_button = ctk.CTkButton(
            status,
            text="Download",
            width=124,
            height=42,
            corner_radius=10,
            command=self.download,
        )
        self.download_button.grid(row=0, column=2, padx=(10, 0), pady=(14, 0))
        self.cancel_button = ctk.CTkButton(
            status,
            text="Cancel",
            width=96,
            height=42,
            corner_radius=10,
            fg_color="#fff1f2",
            hover_color="#ffe4e6",
            text_color="#be123c",
            border_width=1,
            border_color="#fb7185",
            command=self.cancel_current,
        )
        self.cancel_button.grid(row=0, column=3, padx=(10, 16), pady=(14, 0))

    def _build_log_panel(self, parent: ctk.CTkFrame) -> None:
        activity_header = ctk.CTkFrame(parent, fg_color="transparent")
        activity_header.grid(row=7, column=0, sticky=tk.EW, padx=24, pady=(12, 22))
        activity_header.columnconfigure(0, weight=1)
        ctk.CTkButton(
            activity_header,
            textvariable=self.activity_button_var,
            width=116,
            height=36,
            corner_radius=10,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color="#0f172a",
            command=self._toggle_activity,
        ).grid(
            row=0, column=1, sticky=tk.E
        )

        self.activity_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.activity_frame.grid(row=8, column=0, sticky="nsew", padx=24, pady=(0, 22))
        self.activity_frame.columnconfigure(0, weight=1)
        self.activity_frame.rowconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(
            self.activity_frame,
            height=8,
            wrap=tk.WORD,
            fg_color="#0f172a",
            text_color="#dbeafe",
            corner_radius=12,
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.activity_frame.grid_remove()
        self._log("Ready. Paste a Bilibili URL or BV id, then click Analyze.")

    def _tool_status_text(self) -> str:
        lines = []
        for label, path in [
            ("yt-dlp", self.ytdlp_path),
            ("aria2c", self.aria2_path),
            ("ffmpeg", self.ffmpeg_path),
        ]:
            lines.append(f"{label}: {path if path else 'missing'}")
        return "\n".join(lines)

    def _paste_url(self) -> None:
        try:
            self.url_var.set(self.clipboard_get().strip())
        except tk.TclError:
            messagebox.showinfo(APP_NAME, "Clipboard is empty.")

    def _toggle_activity(self) -> None:
        self.activity_visible = not self.activity_visible
        if self.activity_visible:
            self.activity_frame.grid()
            self.activity_button_var.set("Hide activity")
        else:
            self.activity_frame.grid_remove()
            self.activity_button_var.set("Show activity")

    def _reset_options_on_url_change(self, *_args: object) -> None:
        if not hasattr(self, "quality_combo") or self.current_process:
            return
        self.options_loaded = False
        self.video_title_var.set("Analyze this video to load quality and subtitle choices.")
        self.options_hint_var.set("Analyze the video to load quality and subtitle choices.")
        self.format_choices = []
        self.subtitle_choices = []
        self.quality_var.set("Best available video + best audio")
        self.subtitle_var.set("No subtitles")
        self.quality_combo.configure(values=[self.quality_var.get()], state="disabled")
        self.subtitle_combo.configure(values=["No native subtitles found"], state="disabled")
        self._set_busy(False)

    def _field_label(self, parent: Any, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#334155",
        )

    def _update_auth_state(self) -> None:
        mode = self.auth_mode_var.get()
        browser_state = "normal" if mode == "browser" else "disabled"
        profile_state = "normal" if mode == "browser" else "disabled"
        cookie_state = "normal" if mode == "cookie_file" else "disabled"
        self.browser_combo.configure(state=browser_state)
        self.browser_profile_entry.configure(state=profile_state)
        self.cookie_entry.configure(state=cookie_state)
        self.cookie_browse_button.configure(state=cookie_state)
        if mode == "cookie_file":
            self.cookie_row.grid()
            self.browser_row.grid_remove()
        elif mode == "browser":
            self.cookie_row.grid_remove()
            self.browser_row.grid()
        else:
            self.cookie_row.grid_remove()
            self.browser_row.grid_remove()

    def _pick_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(app_dir()))
        if selected:
            self.output_dir_var.set(selected)

    def _pick_cookie_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose cookies.txt",
            filetypes=[("Cookie text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(DEFAULT_COOKIE_PATH.parent if DEFAULT_COOKIE_PATH.parent.exists() else app_dir()),
        )
        if selected:
            self.cookie_path_var.set(selected)
            self.auth_mode_var.set("cookie_file")
            self._update_auth_state()

    def _selected_format(self) -> FormatChoice:
        current = self.quality_var.get()
        for choice in self.format_choices:
            if choice.label == current:
                return choice
        return FormatChoice("Best available video + best audio", "best", "bv*+ba/b")

    def _selected_subtitle(self) -> SubtitleChoice:
        current = self.subtitle_var.get()
        for choice in self.subtitle_choices:
            if choice.label == current:
                return choice
        return SubtitleChoice("No subtitles", None)

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
            raise ValueError("Choose a cookies.txt file or switch cookie mode.")
        if not Path(cookie_path).exists():
            raise ValueError(f"Cookie file not found: {cookie_path}")
        return ["--cookies", cookie_path]

    def _ensure_tools(self) -> None:
        missing = []
        if not self.ytdlp_path:
            missing.append("yt-dlp")
        if not self.aria2_path:
            missing.append("aria2c")
        if not self.ffmpeg_path:
            missing.append("ffmpeg")
        if missing:
            raise RuntimeError("Missing required bundled tools: " + ", ".join(missing))

    def _base_ytdlp_command(self) -> list[str]:
        self._ensure_tools()
        assert self.ytdlp_path is not None
        return [str(self.ytdlp_path)]

    def _set_busy(self, busy: bool, cancellable: bool = False) -> None:
        fetch_state = "disabled" if busy else "normal"
        download_state = "disabled" if busy or not self.options_loaded else "normal"
        self.fetch_button.configure(state=fetch_state)
        self.download_button.configure(state=download_state)
        self.cancel_button.configure(state="normal" if cancellable else "disabled")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _queue_log(self, message: str) -> None:
        self.queue.put(("log", message))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "fetch_done":
                    self._handle_fetch_done(payload)
                elif kind == "download_done":
                    self._handle_download_done(payload)
                elif kind == "error":
                    self._handle_error(str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def fetch_options(self) -> None:
        try:
            url = normalize_url(self.url_var.get())
            if not url:
                raise ValueError("Enter a Bilibili URL or BV id.")
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
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.status_var.set("Fetching available qualities and native subtitles...")
        self._log("Fetching metadata with yt-dlp.")
        self._set_busy(True, cancellable=True)
        thread = threading.Thread(target=self._run_fetch, args=(command,), daemon=True)
        thread.start()

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
                raise RuntimeError(stderr.strip() or "yt-dlp could not fetch metadata.")
            info = json.loads(stdout)
            self.queue.put(("fetch_done", info))
        except Exception as exc:
            self.current_process = None
            self.queue.put(("error", exc))

    def _handle_fetch_done(self, info: dict[str, Any]) -> None:
        item, used_first_entry = extract_info_item(info)
        self.format_choices = parse_formats(item)
        self.subtitle_choices = parse_subtitles(item)

        self.quality_combo.configure(values=[choice.label for choice in self.format_choices], state="normal")
        self.quality_var.set(self.format_choices[0].label)

        subtitle_values = [choice.label for choice in self.subtitle_choices]
        self.subtitle_combo.configure(values=subtitle_values, state="normal" if len(subtitle_values) > 1 else "disabled")
        self.subtitle_var.set(subtitle_values[0])

        title = item.get("title") or info.get("title") or "video"
        native_count = max(0, len(self.subtitle_choices) - 1)
        self.video_title_var.set(str(title))
        self.options_hint_var.set(
            f"{len(self.format_choices)} quality choices. "
            f"{native_count} native subtitle track{'s' if native_count != 1 else ''}."
        )
        self._log(f"Found: {title}")
        self._log(f"Video quality choices: {len(self.format_choices)}")
        self._log(f"Native subtitle tracks: {native_count}")
        if used_first_entry:
            self._log("This looks like a multi-entry page. The selectors are based on the first entry.")
        self.status_var.set("Options loaded. Choose quality/subtitles, then download.")
        self.options_loaded = True
        self._set_busy(False)

    def download(self) -> None:
        try:
            url = normalize_url(self.url_var.get())
            if not url:
                raise ValueError("Enter a Bilibili URL or BV id.")
            output_dir = Path(self.output_dir_var.get().strip() or app_dir() / "downloads")
            output_dir.mkdir(parents=True, exist_ok=True)
            selected_format = self._selected_format()
            selected_subtitle = self._selected_subtitle()

            self._ensure_tools()
            assert self.aria2_path is not None
            assert self.ffmpeg_path is not None
            command = self._base_ytdlp_command() + [
                "--no-playlist",
                "--socket-timeout",
                "30",
                *self._auth_args(),
                "--downloader",
                str(self.aria2_path),
                "--downloader-args",
                ARIA2_ARGS,
                "--ffmpeg-location",
                str(self.ffmpeg_path.parent),
                "--format",
                selected_format.format_string,
                "--merge-output-format",
                "mp4",
                "--paths",
                str(output_dir),
                "--output",
                "%(title).200B [%(id)s].%(ext)s",
            ]
            if selected_subtitle.language:
                command += ["--write-subs", "--sub-langs", selected_subtitle.language]
            command.append(url)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.status_var.set("Downloading with aria2c...")
        self._log(f"Downloading to: {output_dir}")
        self._log(f"Quality: {selected_format.label}")
        if selected_subtitle.language:
            self._log(f"Subtitle: {selected_subtitle.label}")
        else:
            self._log("Subtitle: none")
        self._set_busy(True, cancellable=True)
        thread = threading.Thread(target=self._run_download, args=(command,), daemon=True)
        thread.start()

    def _run_download(self, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=WINDOWS_CREATION_FLAGS,
            )
            self.current_process = process
            assert process.stdout is not None
            for line in process.stdout:
                cleaned = line.rstrip()
                if cleaned:
                    self._queue_log(cleaned)
            return_code = process.wait()
            self.current_process = None
            if return_code != 0:
                raise RuntimeError(f"Download failed with exit code {return_code}.")
            self.queue.put(("download_done", "Download complete."))
        except Exception as exc:
            self.current_process = None
            self.queue.put(("error", exc))

    def _handle_download_done(self, message: str) -> None:
        self.status_var.set(message)
        self._log(message)
        self._set_busy(False)
        messagebox.showinfo(APP_NAME, message)

    def _handle_error(self, message: str) -> None:
        self.status_var.set("Stopped.")
        self._log(f"Error: {message}")
        self._set_busy(False)
        messagebox.showerror(APP_NAME, message)

    def cancel_current(self) -> None:
        process = self.current_process
        if not process or process.poll() is not None:
            return
        self._log("Cancelling current operation...")
        process.terminate()


if __name__ == "__main__":
    try:
        app = BiliDownloaderApp()
        app.mainloop()
    except Exception:
        crash_log(traceback.format_exc())
        raise
