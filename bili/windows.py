from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import threading


def application_data_dir() -> Path:
    return Path(os.environ.get('BILI_DATA_DIR') or str(Path(os.environ['LOCALAPPDATA'])/'Bili'))


class SingleInstance:
    def __init__(self,directory: Path):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype=wintypes.HANDLE
        self.kernel.SetEvent.argtypes=[wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
        key=hashlib.sha256(str(directory.resolve()).casefold().encode('utf-8')).hexdigest()[:24]
        self.mutex=self.kernel.CreateMutexW(None,True,'Local\\Bili-'+key)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.owner=ctypes.get_last_error()!=183
        self.event=self.kernel.CreateEventW(None,False,False,'Local\\Bili-activate-'+key)
        if not self.event:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.owner:
            self.kernel.SetEvent(self.event)

    def activation_pending(self):
        return self.owner and self.kernel.WaitForSingleObject(self.event,0)==0

    def close(self):
        if getattr(self,'event',None):
            self.kernel.CloseHandle(self.event)
            self.event=None
        if getattr(self,'mutex',None):
            if self.owner:
                self.kernel.ReleaseMutex(self.mutex)
            self.kernel.CloseHandle(self.mutex)
            self.mutex=None


class TrayService:
    def __init__(self,post,icon_path: Path):
        self.post,self.icon_path=post,icon_path
        self.icon=None

    def start(self):
        if self.icon is not None:
            return
        import pystray
        from PIL import Image
        ready=threading.Event()
        with Image.open(self.icon_path) as source:
            image=source.convert('RGBA')
        image.thumbnail((64,64))
        def action(name):
            return lambda icon,item:self.post(name)
        icon=pystray.Icon('Bili',image,'Bili · 后台任务继续运行',pystray.Menu(
            pystray.MenuItem('打开主窗口',action('home'),default=True),
            pystray.MenuItem('下载任务',action('tasks')),
            pystray.MenuItem('设置',action('settings')),
            pystray.MenuItem('退出',action('exit'))))
        def setup(current):
            current.visible=True
            ready.set()
        icon.run_detached(setup)
        if not ready.wait(2):
            icon.stop()
            raise RuntimeError('托盘启动失败，请保持窗口打开后重试。')
        self.icon=icon

    def stop(self):
        if self.icon is not None:
            self.icon.stop()
            self.icon=None


class NotificationService:
    def __init__(self,activate):
        self.activate=activate
        self.toaster=None

    def show(self,summary):
        try:
            from windows_toasts import Toast,WindowsToaster
            if self.toaster is None:
                self.toaster=WindowsToaster('Bili')
            counts=summary['counts']
            detail='，'.join(f'{counts.get(state,0)} 个{label}' for state,label in (
                ('succeeded','成功'),('partial','部分成功'),('failed','失败'),('cancelled','取消')))
            toast=Toast(['下载队列已结束',detail],on_activated=lambda event:self.activate())
            toast.tag=summary['batch_id'][:16]
            self.toaster.show_toast(toast)
            return True
        except Exception:
            # OS policy can suppress desktop notifications. Durable task
            # results remain available; do not turn a download into failure.
            return False
