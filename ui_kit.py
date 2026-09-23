from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from typing import Any


def set_appearance_mode(_mode: str) -> None:
    return


def set_default_color_theme(_theme: str) -> None:
    return


def _parent_bg(master: Any, fallback: str = "#ffffff") -> str:
    try:
        return master.cget("bg")
    except Exception:
        return fallback


def _state_for_combobox(state: str | None) -> str:
    if state == "normal":
        return "readonly"
    if state == "disabled":
        return "disabled"
    return state or "readonly"


class CTkFont(tuple):
    def __new__(cls, family: str = "Segoe UI", size: int = 10, weight: str | None = None):
        return tuple.__new__(cls, (family, size, "bold" if weight == "bold" else "normal"))


class CTk(tk.Tk):
    def configure(self, cnf: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if "fg_color" in kwargs:
            kwargs["bg"] = kwargs.pop("fg_color")
        super().configure(cnf or {}, **kwargs)


class CTkFrame(tk.Frame):
    def __init__(
        self,
        master: Any = None,
        fg_color: str = "#ffffff",
        corner_radius: int | None = None,
        border_width: int = 0,
        border_color: str = "#dbe3ef",
        **kwargs: Any,
    ) -> None:
        del corner_radius
        bg = _parent_bg(master) if fg_color == "transparent" else fg_color
        super().__init__(
            master,
            bg=bg,
            highlightthickness=border_width,
            highlightbackground=border_color,
            highlightcolor=border_color,
            bd=0,
            **kwargs,
        )


class CTkLabel(tk.Label):
    def __init__(
        self,
        master: Any = None,
        text: str | None = None,
        textvariable: tk.Variable | None = None,
        font: Any = None,
        text_color: str = "#111827",
        fg_color: str | None = None,
        anchor: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text or "",
            textvariable=textvariable,
            font=font,
            fg=text_color,
            bg=fg_color or _parent_bg(master),
            anchor=anchor or "center",
            **kwargs,
        )


class CTkEntry(tk.Entry):
    def __init__(
        self,
        master: Any = None,
        textvariable: tk.Variable | None = None,
        height: int | None = None,
        corner_radius: int | None = None,
        border_color: str = "#D8D5CE",
        placeholder_text: str | None = None,
        font: Any = None,
        **kwargs: Any,
    ) -> None:
        del corner_radius, placeholder_text
        super().__init__(
            master,
            textvariable=textvariable,
            font=font,
            bg="#ffffff",
            fg="#201F1C",
            insertbackground="#201F1C",
            relief="flat",
            highlightthickness=1,
            highlightbackground=border_color,
            highlightcolor="#AAA69D",
            disabledbackground="#EFEEEA",
            disabledforeground="#706D67",
            **kwargs,
        )
        self._ipady = max(3, int((height or 34) / 7) - 2)

    def grid(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("ipady", self._ipady)
        return super().grid(*args, **kwargs)


class CTkButton(tk.Button):
    def __init__(
        self,
        master: Any = None,
        text: str = "",
        textvariable: tk.Variable | None = None,
        width: int | None = None,
        height: int | None = None,
        corner_radius: int | None = None,
        fg_color: str = "#292825",
        hover_color: str | None = None,
        text_color: str = "#ffffff",
        border_width: int = 0,
        border_color: str = "#292825",
        command: Any = None,
        **kwargs: Any,
    ) -> None:
        del corner_radius, hover_color
        self._normal_bg = fg_color
        self._normal_fg = text_color
        super().__init__(
            master,
            text=text,
            textvariable=textvariable,
            command=command,
            bg=fg_color,
            fg=text_color,
            activebackground=fg_color,
            activeforeground=text_color,
            relief="flat",
            bd=0,
            highlightthickness=border_width,
            highlightbackground=border_color,
            padx=10,
            pady=6,
            width=max(1, int((width or 90) / 10)),
            height=max(1, int((height or 36) / 28)),
            **kwargs,
        )

    def configure(self, cnf: dict[str, Any] | None = None, **kwargs: Any) -> None:
        state = kwargs.get("state")
        if state == "disabled":
            kwargs.setdefault("bg", "#DEDCD6")
            kwargs.setdefault("fg", "#9B9891")
            kwargs.setdefault("activebackground", "#DEDCD6")
            kwargs.setdefault("activeforeground", "#9B9891")
        elif state == "normal":
            kwargs.setdefault("bg", self._normal_bg)
            kwargs.setdefault("fg", self._normal_fg)
            kwargs.setdefault("activebackground", self._normal_bg)
            kwargs.setdefault("activeforeground", self._normal_fg)
        super().configure(cnf or {}, **kwargs)


def _draw_rounded_shape(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    *,
    fill: str,
    outline: str = "",
    width: int = 1,
    tags: str | tuple[str, ...] = (),
) -> int:
    radius = max(1, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )
    return canvas.create_polygon(
        points,
        smooth=True,
        splinesteps=24,
        fill=fill,
        outline=outline,
        width=width,
        tags=tags,
    )


class RoundedButton(tk.Canvas):
    """Small canvas-backed button used by the redesigned home header."""

    def __init__(
        self,
        master: Any = None,
        text: str = "",
        icon: str | None = None,
        width: int = 90,
        height: int = 40,
        corner_radius: int = 18,
        fg_color: str = "#292825",
        hover_color: str = "#3B3935",
        text_color: str = "#FFFFFF",
        disabled_color: str = "#DEDCD6",
        disabled_text_color: str = "#9B9891",
        bg_color: str | None = None,
        font: Any = ("Segoe UI", 11, "bold"),
        command: Any = None,
        **kwargs: Any,
    ) -> None:
        self._text = text
        self._icon = icon
        self._command = command
        self._state = "normal"
        self._normal_bg = fg_color
        self._hover_bg = hover_color
        self._normal_fg = text_color
        self._disabled_bg = disabled_color
        self._disabled_fg = disabled_text_color
        self._corner_radius = corner_radius
        self._font = font
        self._hovering = False
        self._pixel_width = width
        self._pixel_height = height
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", self._on_click, add="+")
        self.after_idle(self._redraw)

    def _redraw(self, _event: Any = None) -> None:
        self.delete("button")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        disabled = self._state == "disabled"
        fill = self._disabled_bg if disabled else (self._hover_bg if self._hovering else self._normal_bg)
        text_color = self._disabled_fg if disabled else self._normal_fg
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            self._corner_radius,
            fill=fill,
            tags="button",
        )
        if self._icon == "gear":
            self._draw_gear(canvas_width / 2, canvas_height / 2, text_color)
        else:
            self.create_text(
                canvas_width / 2,
                canvas_height / 2,
                text=self._text,
                fill=text_color,
                font=self._font,
                tags="button",
            )

    def _draw_gear(self, center_x: float, center_y: float, color: str) -> None:
        for index in range(8):
            angle = math.radians(index * 45)
            inner_x = center_x + math.cos(angle) * 7
            inner_y = center_y + math.sin(angle) * 7
            outer_x = center_x + math.cos(angle) * 10
            outer_y = center_y + math.sin(angle) * 10
            self.create_line(
                inner_x,
                inner_y,
                outer_x,
                outer_y,
                fill=color,
                width=2,
                capstyle=tk.ROUND,
                tags="button",
            )
        self.create_oval(
            center_x - 7,
            center_y - 7,
            center_x + 7,
            center_y + 7,
            outline=color,
            width=2,
            tags="button",
        )
        self.create_oval(
            center_x - 2.5,
            center_y - 2.5,
            center_x + 2.5,
            center_y + 2.5,
            outline=color,
            width=2,
            tags="button",
        )

    def _on_enter(self, _event: Any = None) -> None:
        if self._state != "disabled":
            self._hovering = True
            self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def _on_click(self, _event: Any = None) -> None:
        self.invoke()

    def invoke(self) -> Any:
        if self._state != "disabled" and self._command:
            return self._command()
        return None

    def configure(self, cnf: dict[str, Any] | None = None, **kwargs: Any) -> None:
        options = dict(cnf or {})
        options.update(kwargs)
        redraw = False
        for name, attr in (
            ("text", "_text"),
            ("icon", "_icon"),
            ("state", "_state"),
            ("command", "_command"),
            ("fg_color", "_normal_bg"),
            ("hover_color", "_hover_bg"),
            ("text_color", "_normal_fg"),
        ):
            if name in options:
                setattr(self, attr, options.pop(name))
                redraw = True
        if "cursor" not in options and redraw:
            options["cursor"] = "arrow" if self._state == "disabled" else "hand2"
        if options:
            super().configure(**options)
        if redraw:
            self._redraw()

    config = configure

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "icon":
            return self._icon
        if key == "state":
            return self._state
        if key == "command":
            return self._command
        return super().cget(key)


class URLInputBar(tk.Canvas):
    """A rounded URL field with an embedded action button and real placeholder."""

    def __init__(
        self,
        master: Any,
        variable: tk.StringVar,
        command: Any,
        placeholder: str,
        *,
        height: int = 56,
        corner_radius: int = 18,
        fill: str = "#FFFFFF",
        border: str = "#D8D5CE",
        focus_border: str = "#AAA69D",
        page_bg: str = "#F6F5F1",
        **kwargs: Any,
    ) -> None:
        self._variable = variable
        self._height = height
        self._corner_radius = corner_radius
        self._fill = fill
        self._border = border
        self._focus_border = focus_border
        self._focused = False
        super().__init__(
            master,
            height=height,
            bg=page_bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.entry = tk.Entry(
            self,
            textvariable=variable,
            font=("Segoe UI", 12),
            bg=fill,
            fg="#201F1C",
            insertbackground="#201F1C",
            disabledbackground=fill,
            disabledforeground="#9B9891",
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.placeholder = tk.Label(
            self,
            text=placeholder,
            font=("Segoe UI", 12),
            bg=fill,
            fg="#706D67",
            anchor="w",
            cursor="xterm",
        )
        self.button = RoundedButton(
            self,
            text="解析 →",
            width=102,
            height=40,
            corner_radius=20,
            fg_color="#292825",
            hover_color="#403E39",
            text_color="#FFFFFF",
            bg_color=fill,
            command=command,
        )
        self._entry_window = self.create_window(20, height / 2, window=self.entry, anchor="w")
        self._placeholder_window = self.create_window(20, height / 2, window=self.placeholder, anchor="w")
        self._button_window = self.create_window(0, height / 2, window=self.button, anchor="center")
        self.tag_lower("all")
        self.bind("<Configure>", self._layout, add="+")
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.placeholder.bind("<Button-1>", lambda _event: self.entry.focus_set(), add="+")
        self._trace_id = variable.trace_add("write", self._update_placeholder)
        self.after_idle(self._layout)
        self.after_idle(self._update_placeholder)

    def _draw_shell(self) -> None:
        self.delete("shell")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            self._corner_radius,
            fill=self._fill,
            outline=self._focus_border if self._focused else self._border,
            width=1,
            tags="shell",
        )
        self.tag_lower("shell")

    def _layout(self, _event: Any = None) -> None:
        canvas_width = max(240, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        button_width = 102
        button_margin = 9
        available = max(60, canvas_width - 20 - button_width - button_margin - 12)
        center_y = canvas_height / 2
        self.itemconfigure(self._entry_window, width=available)
        self.itemconfigure(self._placeholder_window, width=available)
        self.coords(self._entry_window, 20, center_y)
        self.coords(self._placeholder_window, 20, center_y)
        self.coords(self._button_window, canvas_width - button_margin - button_width / 2, center_y)
        self._draw_shell()

    def _on_focus_in(self, _event: Any = None) -> None:
        self._focused = True
        self._update_placeholder()
        self._draw_shell()

    def _on_focus_out(self, _event: Any = None) -> None:
        self._focused = False
        self._update_placeholder()
        self._draw_shell()
        self.show_start()

    def _update_placeholder(self, *_args: Any) -> None:
        visible = not self._variable.get() and not self._focused
        self.itemconfigure(self._placeholder_window, state="normal" if visible else "hidden")

    def show_start(self) -> None:
        """Reset only the horizontal viewport without changing the URL value."""
        self.entry.xview_moveto(0)


class VideoEmptyIcon(tk.Canvas):
    """Restrained monochrome video glyph for the initial empty state."""

    def __init__(self, master: Any, *, bg: str = "#F6F5F1", **kwargs: Any) -> None:
        super().__init__(master, width=68, height=56, bg=bg, highlightthickness=0, bd=0, **kwargs)
        _draw_rounded_shape(
            self,
            9,
            8,
            59,
            48,
            12,
            fill=bg,
            outline="#706D67",
            width=2,
        )
        self.create_polygon(
            30,
            21,
            30,
            35,
            42,
            28,
            fill=bg,
            outline="#706D67",
            width=2,
            joinstyle="round",
        )


class PaperDoodles(tk.Canvas):
    """Very quiet hand-drawn marks that sit in otherwise empty paper space."""

    def __init__(
        self,
        master: Any,
        *,
        variant: str = "home",
        bg: str = "#F6F5F1",
        color: str = "#D5CEBF",
        **kwargs: Any,
    ) -> None:
        self.variant = variant
        self.line_color = color
        self.faint_color = "#E0D9CD"
        width, height = (600, 72) if variant == "home" else (148, 42)
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        if variant == "home":
            self._draw_home()
        else:
            self._draw_settings()

    def _line(self, *points: float, width: int = 2, smooth: bool = False) -> int:
        return self.create_line(
            *points,
            fill=self.line_color,
            width=width,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
            smooth=smooth,
            splinesteps=18,
            tags="doodle",
        )

    def _faint_line(self, *points: float, width: int = 1, smooth: bool = False) -> int:
        return self.create_line(
            *points,
            fill=self.faint_color,
            width=width,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
            smooth=smooth,
            splinesteps=18,
            tags="doodle",
        )

    def _star(self, x: int, y: int, size: int = 5) -> None:
        # Intentionally off-centre and uneven, like a quick four-stroke sparkle.
        self._line(x - size, y + 1, x + size - 1, y - 1, width=1)
        self._line(x + 1, y - size, x - 1, y + size + 1, width=1)
        self._line(x - 3, y - 2, x + 2, y + 3, width=1)
        self._line(x + 3, y - 3, x - 2, y + 2, width=1)

    def _draw_home(self) -> None:
        # Left, deliberately lower and heavier: a lopsided little television.
        self._line(9, 29, 13, 23, 31, 21, 54, 19, 82, 20, 94, 24, 96, 42, 94, 58, 88, 63, 61, 64, 35, 66, 15, 64, 9, 59, 8, 43, 9, 29, width=2)
        self._faint_line(12, 27, 24, 24, 51, 22, 79, 22, 91, 25, width=1)
        self._faint_line(15, 61, 40, 63, 67, 61, 90, 59, width=1)
        self._line(31, 21, 23, 12, 18, 9, width=1)
        self._line(35, 21, 44, 10, 50, 8, width=1)
        self._line(27, 65, 22, 70, 19, 70, width=1)
        self._line(75, 64, 80, 69, 85, 68, width=1)
        self._line(42, 32, 43, 50, 49, 48, 61, 40, 54, 36, 42, 32, width=2)
        self._faint_line(45, 34, 46, 47, 57, 40, 45, 34, width=1)
        self._star(109, 22, 4)
        self._line(104, 35, 116, 32, width=1)
        self._line(106, 44, 120, 47, width=1)
        self._line(99, 56, 111, 58, width=1)
        self._faint_line(5, 70, 28, 68, 50, 70, 77, 68, 105, 70, smooth=True, width=1)

        # Right, a little higher and looser: headphones with floating sound marks.
        self._line(449, 45, 451, 31, 457, 18, 468, 9, 482, 5, 496, 7, 507, 14, 514, 27, 516, 42, width=2, smooth=True)
        self._faint_line(453, 42, 454, 30, 461, 18, 472, 11, 485, 8, 498, 11, 507, 20, 511, 34, width=1, smooth=True)
        self._line(451, 38, 445, 40, 443, 51, 447, 57, 454, 56, 456, 45, 451, 38, width=2)
        self._faint_line(446, 43, 446, 52, 452, 53, width=1)
        self._line(515, 37, 521, 38, 525, 45, 523, 56, 517, 59, 512, 54, 512, 43, 515, 37, width=2)
        self._line(523, 57, 530, 61, 536, 59, 541, 63, width=1, smooth=True)
        self._line(544, 17, 546, 42, width=1)
        self._line(546, 20, 557, 17, 558, 22, width=1)
        self._line(538, 43, 542, 39, 548, 39, 551, 43, 548, 47, 542, 48, 538, 43, width=1)
        self._line(571, 10, 569, 32, width=1)
        self._line(568, 12, 580, 16, width=1)
        self._line(562, 33, 566, 29, 571, 29, 574, 33, 570, 37, 565, 37, 562, 33, width=1)
        self._line(548, 55, 558, 52, 566, 54, width=1, smooth=True)
        self._line(551, 62, 561, 60, 574, 63, 584, 59, 594, 61, width=1, smooth=True)
        self._star(586, 27, 3)
        self._line(583, 43, 590, 40, 595, 42, width=1)

    def _draw_settings(self) -> None:
        # One cohesive sketch: a soft folder holding a page and a crooked arrow.
        self._line(5, 16, 9, 10, 33, 9, 40, 14, 78, 13, 88, 16, 87, 34, 82, 38, 13, 39, 7, 35, 5, 16, width=2)
        self._faint_line(10, 17, 32, 16, 55, 17, 80, 16, 85, 19, width=1)
        self._line(29, 13, 31, 5, 61, 6, 68, 11, 68, 14, width=1)
        self._line(52, 18, 51, 31, width=1)
        self._line(45, 26, 51, 32, 58, 25, width=1)
        self._faint_line(16, 34, 34, 35, 56, 33, 79, 35, width=1, smooth=True)
        self._line(99, 13, 111, 9, width=1)
        self._line(98, 22, 115, 21, width=1)
        self._line(101, 30, 114, 34, width=1)
        self._star(126, 12, 3)
        self._line(125, 26, 130, 22, 136, 23, 140, 28, 136, 32, 130, 32, 125, 26, width=1)
        self._faint_line(93, 40, 108, 38, 123, 40, 142, 37, smooth=True, width=1)


class RoundedPanel(tk.Canvas):
    """A warm, border-only rounded surface with a frame for ordinary Tk children."""

    def __init__(
        self,
        master: Any,
        *,
        height: int,
        width: int = 1,
        corner_radius: int = 20,
        fill: str = "#EFEEEA",
        border: str = "#D8D5CE",
        page_bg: str | None = None,
        padding: int = 14,
        **kwargs: Any,
    ) -> None:
        self._corner_radius = corner_radius
        self._fill = fill
        self._border = border
        self._padding = padding
        super().__init__(
            master,
            width=width,
            height=height,
            bg=page_bg or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.content = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self._content_window = self.create_window(padding, padding, window=self.content, anchor="nw")
        self.bind("<Configure>", self._layout, add="+")
        self.after_idle(self._layout)

    def _layout(self, _event: Any = None) -> None:
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        self.delete("panel")
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            self._corner_radius,
            fill=self._fill,
            outline=self._border,
            width=1,
            tags="panel",
        )
        self.tag_lower("panel")
        self.coords(self._content_window, self._padding, self._padding)
        self.itemconfigure(
            self._content_window,
            width=max(1, canvas_width - self._padding * 2),
            height=max(1, canvas_height - self._padding * 2),
        )


class ModeSelectionCard(tk.Canvas):
    """Clickable radio-card appearance backed by an existing StringVar."""

    def __init__(
        self,
        master: Any,
        *,
        variable: tk.StringVar,
        value: str,
        title: str,
        subtitle: str,
        icon: str,
        command: Any,
        height: int = 68,
        width: int = 280,
        page_bg: str = "#F6F5F1",
        **kwargs: Any,
    ) -> None:
        self._variable = variable
        self._value = value
        self._title = title
        self._subtitle = subtitle
        self._icon = icon
        self._command = command
        self._hovering = False
        super().__init__(
            master,
            width=width,
            height=height,
            bg=page_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", lambda _event: self.invoke(), add="+")
        self._trace_id = variable.trace_add("write", self._redraw)
        self.after_idle(self._redraw)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def _redraw(self, *_args: Any) -> None:
        self.delete("card")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        selected = self._variable.get() == self._value
        fill = "#E1DFD9" if selected else ("#EBE9E4" if self._hovering else "#F0EFEB")
        border = "#6F6C65" if selected else "#D8D5CE"
        _draw_rounded_shape(
            self,
            1.5,
            1.5,
            canvas_width - 1.5,
            canvas_height - 1.5,
            17,
            fill=fill,
            outline=border,
            width=2 if selected else 1,
            tags="card",
        )
        icon_x = 28
        icon_y = canvas_height / 2
        if self._icon == "play":
            self.create_polygon(
                icon_x - 5,
                icon_y - 8,
                icon_x - 5,
                icon_y + 8,
                icon_x + 8,
                icon_y,
                fill="",
                outline="#292825",
                width=2,
                joinstyle="round",
                tags="card",
            )
        else:
            self.create_line(
                icon_x + 4,
                icon_y - 10,
                icon_x + 4,
                icon_y + 5,
                fill="#292825",
                width=2,
                capstyle=tk.ROUND,
                tags="card",
            )
            self.create_line(
                icon_x + 4,
                icon_y - 10,
                icon_x + 12,
                icon_y - 7,
                fill="#292825",
                width=2,
                capstyle=tk.ROUND,
                tags="card",
            )
            self.create_oval(
                icon_x - 4,
                icon_y + 2,
                icon_x + 5,
                icon_y + 10,
                outline="#292825",
                width=2,
                tags="card",
            )
        self.create_text(
            52,
            23,
            text=self._title,
            fill="#201F1C",
            font=("Segoe UI Semibold", 11),
            anchor="w",
            tags="card",
        )
        self.create_text(
            52,
            46,
            text=self._subtitle,
            fill="#706D67",
            font=("Segoe UI", 9),
            anchor="w",
            tags="card",
        )

    def invoke(self) -> Any:
        self._variable.set(self._value)
        if self._command:
            return self._command()
        return None

    def cget(self, key: str) -> Any:
        if key == "value":
            return self._value
        return super().cget(key)

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class ChoiceChip(tk.Canvas):
    """Compact segmented choice bound directly to an existing StringVar."""

    def __init__(
        self,
        master: Any,
        *,
        text: str,
        value: str,
        variable: tk.StringVar,
        command: Any = None,
        height: int = 34,
        page_bg: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._text = text
        self._value = value
        self._variable = variable
        self._command = command
        self._hovering = False
        chip_font = ("Segoe UI", 10, "bold")
        measured_width = tkfont.Font(master=master, font=chip_font).measure(text) + 30
        super().__init__(
            master,
            width=max(62, measured_width),
            height=height,
            bg=page_bg or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self._font = chip_font
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", lambda _event: self.invoke(), add="+")
        self._trace_id = variable.trace_add("write", self._redraw)
        self.after_idle(self._redraw)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def _redraw(self, *_args: Any) -> None:
        self.delete("chip")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        selected = self._variable.get() == self._value
        fill = "#292825" if selected else ("#DEDCD6" if self._hovering else "#E8E6E1")
        text_color = "#FFFFFF" if selected else "#474540"
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            17,
            fill=fill,
            tags="chip",
        )
        self.create_text(
            canvas_width / 2,
            canvas_height / 2,
            text=self._text,
            fill=text_color,
            font=self._font,
            tags="chip",
        )

    def invoke(self) -> Any:
        self._variable.set(self._value)
        if self._command:
            return self._command()
        return None

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "value":
            return self._value
        return super().cget(key)

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class WarmProgressBar(tk.Canvas):
    """Rounded determinate/indeterminate progress bar in the warm UI palette."""

    def __init__(
        self,
        master: Any,
        *,
        percentage_variable: tk.StringVar | None = None,
        height: int = 10,
        track_color: str = "#E1DFD9",
        progress_color: str = "#292825",
        bg_color: str | None = None,
        mode: str = "determinate",
        **kwargs: Any,
    ) -> None:
        self._percentage_variable = percentage_variable
        self._track_color = track_color
        self._progress_color = progress_color
        self._mode = mode
        self._value = 0.0
        self._running = False
        self._phase = 0.0
        self._after_id: str | None = None
        super().__init__(
            master,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.after_idle(self._redraw)

    def _redraw(self, _event: Any = None) -> None:
        self.delete("progress")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        radius = canvas_height / 2
        _draw_rounded_shape(
            self,
            0,
            0,
            canvas_width,
            canvas_height,
            radius,
            fill=self._track_color,
            tags="progress",
        )
        if self._mode == "indeterminate":
            segment_width = max(32, canvas_width * 0.24)
            start = (canvas_width + segment_width) * self._phase - segment_width
            end = start + segment_width
            visible_start = max(0, start)
            visible_end = min(canvas_width, end)
            if visible_end > visible_start:
                _draw_rounded_shape(
                    self,
                    visible_start,
                    0,
                    visible_end,
                    canvas_height,
                    radius,
                    fill=self._progress_color,
                    tags="progress",
                )
        elif self._value > 0:
            completed_width = max(canvas_height, canvas_width * self._value)
            _draw_rounded_shape(
                self,
                0,
                0,
                min(canvas_width, completed_width),
                canvas_height,
                radius,
                fill=self._progress_color,
                tags="progress",
            )

    def _tick(self) -> None:
        if not self._running:
            return
        self._phase = (self._phase + 0.035) % 1.0
        self._redraw()
        self._after_id = self.after(45, self._tick)

    def start(self, _interval: int | None = None) -> None:
        if self._running:
            return
        self._running = True
        self._phase = 0.0
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def set(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        if self._percentage_variable is not None:
            self._percentage_variable.set(f"{self._value * 100:.0f}%")
        self._redraw()

    def configure(self, cnf: dict[str, Any] | None = None, **kwargs: Any) -> None:
        options = dict(cnf or {})
        options.update(kwargs)
        if "mode" in options:
            self._mode = str(options.pop("mode"))
        if "progress_color" in options:
            self._progress_color = str(options.pop("progress_color"))
        if "track_color" in options:
            self._track_color = str(options.pop("track_color"))
        if options:
            super().configure(**options)
        if hasattr(self, "_value"):
            self._redraw()

    config = configure

    def cget(self, key: str) -> Any:
        if key == "mode":
            return self._mode
        if key == "value":
            return self._value
        return super().cget(key)

    def destroy(self) -> None:
        self.stop()
        super().destroy()


class StatusIcon(tk.Canvas):
    """Small monochrome result mark for completion, cancellation, and errors."""

    def __init__(self, master: Any, *, bg: str = "#F6F5F1", **kwargs: Any) -> None:
        self._kind = "success"
        super().__init__(master, width=28, height=28, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self.after_idle(self._redraw)

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        color = "#A95A58" if self._kind == "error" else "#292825"
        self.create_oval(2, 2, 26, 26, outline=color, width=2)
        if self._kind == "success":
            self.create_line(8, 14, 12, 18, 20, 10, fill=color, width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif self._kind == "error":
            self.create_line(9, 9, 19, 19, fill=color, width=2, capstyle=tk.ROUND)
            self.create_line(19, 9, 9, 19, fill=color, width=2, capstyle=tk.ROUND)
        else:
            self.create_line(8, 14, 20, 14, fill=color, width=2, capstyle=tk.ROUND)


class SegmentedControl(tk.Canvas):
    """Rounded multi-option control backed by an existing StringVar."""

    def __init__(
        self,
        master: Any,
        *,
        options: list[tuple[str, str]],
        variable: tk.StringVar,
        command: Any = None,
        height: int = 38,
        bg_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._segment_options = options
        self._variable = variable
        self._command = command
        self._hovered_index: int | None = None
        super().__init__(
            master,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Motion>", self._on_motion, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", self._on_click, add="+")
        self._trace_id = variable.trace_add("write", self._redraw)
        self.after_idle(self._redraw)

    def _index_at(self, x: float) -> int:
        segment_width = max(1, self.winfo_width()) / max(1, len(self._segment_options))
        return max(0, min(len(self._segment_options) - 1, int(x / segment_width)))

    def _on_motion(self, event: Any) -> None:
        index = self._index_at(event.x)
        if index != self._hovered_index:
            self._hovered_index = index
            self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovered_index = None
        self._redraw()

    def _on_click(self, event: Any) -> None:
        self.invoke(self._segment_options[self._index_at(event.x)][1])

    def invoke(self, value: str) -> Any:
        if value not in {option_value for _, option_value in self._segment_options}:
            raise ValueError(f"Unknown segmented value: {value}")
        self._variable.set(value)
        if self._command:
            return self._command()
        return None

    def _redraw(self, *_args: Any) -> None:
        self.delete("segment")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        _draw_rounded_shape(
            self,
            0,
            0,
            canvas_width,
            canvas_height,
            canvas_height / 2,
            fill="#E4E2DC",
            tags="segment",
        )
        segment_width = canvas_width / max(1, len(self._segment_options))
        selected_value = self._variable.get()
        for index, (label, value) in enumerate(self._segment_options):
            left = index * segment_width + 2
            right = (index + 1) * segment_width - 2
            selected = value == selected_value
            if selected:
                fill = "#292825"
            elif self._hovered_index == index:
                fill = "#D8D5CE"
            else:
                fill = "#E4E2DC"
            _draw_rounded_shape(
                self,
                left,
                2,
                right,
                canvas_height - 2,
                (canvas_height - 4) / 2,
                fill=fill,
                tags="segment",
            )
            self.create_text(
                left + (right - left) / 2,
                canvas_height / 2,
                text=label,
                fill="#FFFFFF" if selected else "#474540",
                font=("Segoe UI", 10, "bold" if selected else "normal"),
                tags="segment",
            )

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class ActionSettingRow(tk.Canvas):
    """Soft content row with title, current value, detail, and a light action."""

    def __init__(
        self,
        master: Any,
        *,
        title: str,
        value_variable: tk.StringVar,
        detail_variable: tk.StringVar | None = None,
        action_text: str,
        command: Any,
        height: int = 54,
        bg_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._title = title
        self._value_variable = value_variable
        self._detail_variable = detail_variable
        self._action_text = action_text
        self._command = command
        self._hovering = False
        super().__init__(
            master,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", lambda _event: self.invoke(), add="+")
        self._value_trace = value_variable.trace_add("write", self._redraw)
        self._detail_trace = detail_variable.trace_add("write", self._redraw) if detail_variable is not None else None
        self.after_idle(self._redraw)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def invoke(self) -> Any:
        return self._command() if self._command else None

    def _redraw(self, *_args: Any) -> None:
        self.delete("row")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            15,
            fill="#F2F0EB" if self._hovering else "#F6F5F1",
            outline="#D8D5CE",
            width=1,
            tags="row",
        )
        self.create_text(14, 17, text=self._title, fill="#706D67", font=("Segoe UI", 9), anchor="w", tags="row")
        self.create_text(
            86,
            17,
            text=self._value_variable.get(),
            fill="#201F1C",
            font=("Segoe UI Semibold", 10),
            anchor="w",
            tags="row",
        )
        detail = self._detail_variable.get() if self._detail_variable is not None else ""
        if detail:
            available = max(12, canvas_width - 28)
            self.create_text(
                14,
                39,
                text=detail,
                fill="#706D67",
                font=("Segoe UI", 8),
                anchor="w",
                width=available - 110,
                tags="row",
            )
        self.create_text(
            canvas_width - 14,
            canvas_height / 2,
            text=self._action_text,
            fill="#292825",
            font=("Segoe UI", 9, "bold"),
            anchor="e",
            tags="row",
        )

    def destroy(self) -> None:
        try:
            self._value_variable.trace_remove("write", self._value_trace)
            if self._detail_variable is not None and self._detail_trace is not None:
                self._detail_variable.trace_remove("write", self._detail_trace)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class ChoiceSettingRow(tk.Canvas):
    """Soft setting row that cycles through a fixed list of choices."""

    def __init__(
        self,
        master: Any,
        *,
        title: str,
        variable: tk.StringVar,
        values: list[str],
        display_names: dict[str, str] | None = None,
        height: int = 40,
        bg_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._title = title
        self._variable = variable
        self._values = values
        self._display_names = display_names or {}
        self._hovering = False
        super().__init__(
            master,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", lambda _event: self.invoke(), add="+")
        self._trace_id = variable.trace_add("write", self._redraw)
        self.after_idle(self._redraw)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def invoke(self) -> None:
        current = self._variable.get()
        try:
            index = self._values.index(current)
        except ValueError:
            index = -1
        self._variable.set(self._values[(index + 1) % len(self._values)])

    def _redraw(self, *_args: Any) -> None:
        self.delete("row")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            14,
            fill="#F2F0EB" if self._hovering else "#F6F5F1",
            outline="#D8D5CE",
            width=1,
            tags="row",
        )
        current = self._variable.get()
        display = self._display_names.get(current, current.title())
        self.create_text(14, canvas_height / 2, text=self._title, fill="#706D67", font=("Segoe UI", 9), anchor="w", tags="row")
        self.create_text(
            canvas_width - 30,
            canvas_height / 2,
            text=display,
            fill="#201F1C",
            font=("Segoe UI Semibold", 10),
            anchor="e",
            tags="row",
        )
        self.create_text(canvas_width - 14, canvas_height / 2, text="›", fill="#706D67", font=("Segoe UI", 15), tags="row")

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class SettingEntryRow(tk.Canvas):
    """Rounded label/input row used for editable settings such as browser profiles."""

    def __init__(
        self,
        master: Any,
        *,
        title: str,
        variable: tk.StringVar,
        placeholder: str = "",
        height: int = 40,
        bg_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._title = title
        self._variable = variable
        self._placeholder_text = placeholder
        self._hovering = False
        self._focused = False
        self._surface = "#F6F5F1"
        super().__init__(
            master,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.entry = tk.Entry(
            self,
            textvariable=variable,
            font=("Segoe UI Semibold", 10),
            bg=self._surface,
            fg="#201F1C",
            insertbackground="#201F1C",
            justify="right",
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.placeholder = tk.Label(
            self,
            text=placeholder,
            font=("Segoe UI", 10),
            bg=self._surface,
            fg="#9A968E",
            anchor="e",
            cursor="xterm",
        )
        self._entry_window = self.create_window(0, height / 2, window=self.entry, anchor="e")
        self._placeholder_window = self.create_window(0, height / 2, window=self.placeholder, anchor="e")
        self.bind("<Configure>", self._layout, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.placeholder.bind("<Button-1>", lambda _event: self.entry.focus_set(), add="+")
        self._trace_id = variable.trace_add("write", self._update_placeholder)
        self.after_idle(self._layout)
        self.after_idle(self._update_placeholder)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._draw_shell()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._draw_shell()

    def _on_focus_in(self, _event: Any = None) -> None:
        self._focused = True
        self._update_placeholder()
        self._draw_shell()

    def _on_focus_out(self, _event: Any = None) -> None:
        self._focused = False
        self._update_placeholder()
        self._draw_shell()

    def _draw_shell(self) -> None:
        self.delete("shell")
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        _draw_rounded_shape(
            self,
            1,
            1,
            canvas_width - 1,
            canvas_height - 1,
            14,
            fill="#F2F0EB" if self._hovering else self._surface,
            outline="#AAA69D" if self._focused else "#D8D5CE",
            width=1,
            tags="shell",
        )
        self.tag_lower("shell")
        self.create_text(14, canvas_height / 2, text=self._title, fill="#706D67", font=("Segoe UI", 9), anchor="w", tags="shell")

    def _layout(self, _event: Any = None) -> None:
        canvas_width = max(2, self.winfo_width())
        canvas_height = max(2, self.winfo_height())
        field_width = max(80, canvas_width - 140)
        self.itemconfigure(self._entry_window, width=field_width)
        self.itemconfigure(self._placeholder_window, width=field_width)
        self.coords(self._entry_window, canvas_width - 16, canvas_height / 2)
        self.coords(self._placeholder_window, canvas_width - 16, canvas_height / 2)
        self._draw_shell()

    def _update_placeholder(self, *_args: Any) -> None:
        visible = not self._variable.get() and not self._focused
        self.itemconfigure(self._placeholder_window, state="normal" if visible else "hidden")

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class SoftCheckBox(tk.Canvas):
    """Monochrome checkbox that keeps the existing BooleanVar contract."""

    def __init__(
        self,
        master: Any,
        *,
        text: str,
        variable: tk.BooleanVar,
        command: Any = None,
        width: int = 210,
        height: int = 28,
        bg_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._text = text
        self._variable = variable
        self._command = command
        self._hovering = False
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg_color or _parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonRelease-1>", lambda _event: self.invoke(), add="+")
        self._trace_id = variable.trace_add("write", self._redraw)
        self.after_idle(self._redraw)

    def _on_enter(self, _event: Any = None) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event: Any = None) -> None:
        self._hovering = False
        self._redraw()

    def invoke(self) -> Any:
        self._variable.set(not self._variable.get())
        if self._command:
            return self._command()
        return None

    def _redraw(self, *_args: Any) -> None:
        self.delete("check")
        selected = self._variable.get()
        border = "#292825" if selected or self._hovering else "#AAA69D"
        _draw_rounded_shape(
            self,
            1,
            5,
            19,
            23,
            5,
            fill="#292825" if selected else "#F6F5F1",
            outline=border,
            width=1,
            tags="check",
        )
        if selected:
            self.create_line(6, 14, 10, 18, 16, 10, fill="#FFFFFF", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND, tags="check")
        self.create_text(
            28,
            14,
            text=self._text,
            fill="#474540" if selected else "#706D67",
            font=("Segoe UI", 10),
            anchor="w",
            tags="check",
        )

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        return super().cget(key)

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class CTkRadioButton(tk.Radiobutton):
    def __init__(
        self,
        master: Any = None,
        text: str = "",
        value: str | None = None,
        variable: tk.Variable | None = None,
        command: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text,
            value=value,
            variable=variable,
            command=command,
            bg=_parent_bg(master),
            fg="#201F1C",
            selectcolor="#EFEEEA",
            activebackground=_parent_bg(master),
            activeforeground="#201F1C",
            **kwargs,
        )


class CTkCheckBox(tk.Checkbutton):
    def __init__(
        self,
        master: Any = None,
        text: str = "",
        variable: tk.Variable | None = None,
        command: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text,
            variable=variable,
            command=command,
            bg=_parent_bg(master),
            fg="#201F1C",
            selectcolor="#EFEEEA",
            activebackground=_parent_bg(master),
            activeforeground="#201F1C",
            **kwargs,
        )


class CTkOptionMenu(ttk.Combobox):
    def __init__(
        self,
        master: Any = None,
        values: list[str] | None = None,
        variable: tk.Variable | None = None,
        state: str = "normal",
        height: int | None = None,
        corner_radius: int | None = None,
        fg_color: str | None = None,
        button_color: str | None = None,
        button_hover_color: str | None = None,
        text_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        del height, corner_radius, fg_color, button_color, button_hover_color, text_color
        super().__init__(master, textvariable=variable, values=values or [], state=_state_for_combobox(state), **kwargs)

    def configure(self, cnf: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if "state" in kwargs:
            kwargs["state"] = _state_for_combobox(kwargs["state"])
        unsupported = {"fg_color", "button_color", "button_hover_color", "text_color", "corner_radius", "height"}
        for key in unsupported:
            kwargs.pop(key, None)
        super().configure(cnf or {}, **kwargs)


class CTkProgressBar(ttk.Progressbar):
    def __init__(
        self,
        master: Any = None,
        mode: str = "determinate",
        progress_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        del progress_color
        super().__init__(master, mode=mode, maximum=100, **kwargs)

    def set(self, value: float) -> None:
        self.configure(value=max(0, min(100, value * 100)))


class CTkTextbox(tk.Text):
    def __init__(
        self,
        master: Any = None,
        fg_color: str = "#292825",
        text_color: str = "#F6F5F1",
        corner_radius: int | None = None,
        **kwargs: Any,
    ) -> None:
        del corner_radius
        super().__init__(
            master,
            bg=fg_color,
            fg=text_color,
            insertbackground=text_color,
            bd=0,
            padx=12,
            pady=12,
            **kwargs,
        )
