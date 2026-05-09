from __future__ import annotations

import tkinter as tk
from tkinter import ttk
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
        anchor: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text or "",
            textvariable=textvariable,
            font=font,
            fg=text_color,
            bg=_parent_bg(master),
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
        border_color: str = "#cbd5e1",
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
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="flat",
            highlightthickness=1,
            highlightbackground=border_color,
            highlightcolor="#2563eb",
            disabledbackground="#eef2f7",
            disabledforeground="#64748b",
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
        fg_color: str = "#2563eb",
        hover_color: str | None = None,
        text_color: str = "#ffffff",
        border_width: int = 0,
        border_color: str = "#2563eb",
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
            kwargs.setdefault("bg", "#e5e7eb")
            kwargs.setdefault("fg", "#94a3b8")
            kwargs.setdefault("activebackground", "#e5e7eb")
            kwargs.setdefault("activeforeground", "#94a3b8")
        elif state == "normal":
            kwargs.setdefault("bg", self._normal_bg)
            kwargs.setdefault("fg", self._normal_fg)
            kwargs.setdefault("activebackground", self._normal_bg)
            kwargs.setdefault("activeforeground", self._normal_fg)
        super().configure(cnf or {}, **kwargs)


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
            fg="#0f172a",
            selectcolor="#ffffff",
            activebackground=_parent_bg(master),
            activeforeground="#0f172a",
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
        fg_color: str = "#0f172a",
        text_color: str = "#dbeafe",
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
