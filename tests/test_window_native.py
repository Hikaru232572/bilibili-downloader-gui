from __future__ import annotations

import ctypes
import os
import tkinter as tk
import unittest

from app import BiliDownloaderApp, _colorref, apply_windows_titlebar


@unittest.skipUnless(os.name == "nt", "Windows native caption test")
class NativeWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = BiliDownloaderApp()
        self.app.update()

    def tearDown(self) -> None:
        if self.app.winfo_exists():
            for callback_id in self.app.tk.splitlist(self.app.tk.call("after", "info")):
                self.app.after_cancel(callback_id)
            self.app.destroy()

    @staticmethod
    def _native_handle(window: tk.Misc) -> int:
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        child_handle = int(window.winfo_id())
        return int(user32.GetParent(ctypes.c_void_p(child_handle)) or child_handle)

    @classmethod
    def _window_icon_handle(cls, window: tk.Misc) -> int:
        user32 = ctypes.windll.user32
        child_handle = int(window.winfo_id())
        native_handle = cls._native_handle(window)
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.GetClassLongPtrW.restype = ctypes.c_void_p
        for raw_handle in (native_handle, child_handle):
            handle = ctypes.c_void_p(raw_handle)
            for icon_type in (0, 1, 2):
                icon = user32.SendMessageW(handle, 0x007F, icon_type, 0)
                if icon:
                    return int(icon)
            for class_icon in (-14, -34):
                icon = user32.GetClassLongPtrW(handle, class_icon)
                if icon:
                    return int(icon)
        return 0

    def test_caption_colors_are_applied_to_the_native_window(self) -> None:
        self.assertTrue(apply_windows_titlebar(self.app))
        handle = ctypes.c_void_p(self._native_handle(self.app))
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

        for attribute, expected in (
            (34, "#F6F5F1"),
            (35, "#F6F5F1"),
            (36, "#292825"),
        ):
            with self.subTest(attribute=attribute):
                color = ctypes.c_uint(_colorref(expected))
                result = dwmapi.DwmSetWindowAttribute(
                    handle,
                    attribute,
                    ctypes.byref(color),
                    ctypes.sizeof(color),
                )
                self.assertGreaterEqual(result, 0, f"HRESULT {result}")

    def test_native_frame_and_window_state_controls_are_preserved(self) -> None:
        handle = self._native_handle(self.app)
        style = ctypes.windll.user32.GetWindowLongW(ctypes.c_void_p(handle), -16)
        required_styles = {
            "caption": 0x00C00000,
            "resize": 0x00040000,
            "system menu": 0x00080000,
            "minimize": 0x00020000,
            "maximize": 0x00010000,
        }
        for label, flag in required_styles.items():
            self.assertEqual(style & flag, flag, label)

        self.app.state("zoomed")
        self.app.update()
        self.assertEqual(self.app.state(), "zoomed")
        self.app.state("normal")
        self.app.update()
        self.assertEqual(self.app.state(), "normal")
        self.app.iconify()
        self.app.update()
        self.assertEqual(self.app.state(), "iconic")
        self.app.deiconify()
        self.app.update()
        self.assertEqual(self.app.state(), "normal")

    def test_settings_navigation_keeps_one_native_window_and_app_icon(self) -> None:
        native_handle = self._native_handle(self.app)
        self.app.settings_button.invoke()
        self.app.update()
        windows = [
            child
            for child in self.app.winfo_children()
            if isinstance(child, tk.Toplevel)
        ]
        self.assertEqual(windows, [])
        self.assertEqual(self._native_handle(self.app), native_handle)
        self.assertEqual(self.app.title(), "Bili")
        self.assertEqual(self.app.settings_page.winfo_manager(), "pack")
        self.assertGreater(self._window_icon_handle(self.app), 0)
        self.app.settings_back_button.invoke()
        self.app.update()
        self.assertEqual(self._native_handle(self.app), native_handle)
        self.assertEqual(self.app.main_page.winfo_manager(), "pack")
        self.assertEqual(self.app.settings_page.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
