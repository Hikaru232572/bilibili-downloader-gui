from __future__ import annotations

from pathlib import Path
from queue import Empty
import tkinter as tk
from tkinter import ttk

import ui_kit as ctk

from .gallery import preferred_candidates
from .features import LIVE_PHOTO_ENABLED
from .messages import error_text, task_error_text
from .models import Candidate, Component, TaskState
from .preview import PreviewHost
from .task_page import LABELS


class OriginalPreview(tk.Toplevel):
    def __init__(self, page, item):
        super().__init__(page.winfo_toplevel())
        self.page, self.item = page, item
        self.title(f'第 {item.original_index} 张 · 原图预览')
        self.geometry('900x690')
        self.configure(bg='#F6F5F1')
        self.requests = set()
        self.player = None
        self.photo = None
        self.status = tk.StringVar(value='正在取得原图…')
        controls = tk.Frame(self, bg='#F6F5F1')
        controls.pack(fill='x', padx=18, pady=14)
        ttk.Button(controls, text='上一张', command=lambda:self.navigate(-1)).pack(side='left', padx=(0,8))
        ttk.Button(controls, text='下一张', command=lambda:self.navigate(1)).pack(side='left', padx=(0,8))
        for text, command in [('适应窗口',lambda:self.load_still(800)), ('原尺寸',lambda:self.load_still(8192))]:
            ttk.Button(controls, text=text, command=command).pack(side='left', padx=(0,8))
        self.motion_controls = tk.Frame(controls, bg='#F6F5F1')
        ttk.Button(self.motion_controls, text='播放动态', command=self.load_motion).pack(side='left', padx=(0,8))
        for text, operation in [('暂停','pause'),('继续','play'),('重播','replay')]:
            ttk.Button(self.motion_controls, text=text, command=lambda op=operation:self.control(op)).pack(side='left', padx=(0,8))
        if LIVE_PHOTO_ENABLED and item.kind == 'live':
            self.motion_controls.pack(side='left')
        tk.Label(self, textvariable=self.status, bg='#F6F5F1', fg='#706D67', wraplength=850).pack(fill='x', padx=18)
        body = tk.Frame(self, bg='#E4E2DC')
        body.pack(fill='both', expand=True, padx=18, pady=18)
        body.rowconfigure(0, weight=1);body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg='#E4E2DC', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.canvas.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        vertical = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.surface = tk.Frame(body, bg='black')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.load_still(800)
        self._poll_job = self.after(100, self.poll_player)

    def navigate(self, offset):
        items = self.page.work.items
        position = next(index for index,item in enumerate(items) if item.item_id == self.item.item_id)
        position += offset
        if not 0 <= position < len(items):
            return
        if self.player:
            self.player.close()
            self.player = None
        self.item = items[position]
        self.title(f'第 {self.item.original_index} 张 · 原图预览')
        self.motion_controls.pack_forget()
        if LIVE_PHOTO_ENABLED and self.item.kind == 'live':
            self.motion_controls.pack(side='left')
        self.load_still(800)

    def request(self, candidate, max_size=800):
        self.page.service.cancel(self.requests)
        for identity in self.requests:self.page.callbacks.pop(identity,None)
        self.requests.clear()
        identity = self.page.service.request(self.page.work.platform, candidate, self.page.generation, max_size=max_size)
        self.requests.add(identity)
        self.page.callbacks[identity] = self.handle

    def load_still(self, size):
        self.control('stop')
        self.surface.grid_remove()
        self.canvas.grid()
        candidates = preferred_candidates(self.item.still_candidates)
        if not candidates:
            self.status.set('未取得静态图。')
            return
        self.status.set('正在取得原始图片…')
        self.request(candidates[0], size)

    def load_motion(self):
        if not LIVE_PHOTO_ENABLED:
            self.status.set('动态图文暂未支持，当前仅提供静态图片预览和保存。')
            return
        candidates = preferred_candidates(self.item.motion_candidates)
        if not candidates:
            self.status.set('未取得动态部分，静态图片仍可查看和保存。')
            return
        self.status.set('正在取得动态部分…')
        self.request(candidates[0])

    def handle(self, event):
        if not self.winfo_exists():
            return
        if event['operation'] == 'error':
            self.status.set(error_text(event['error_code']))
        elif event['operation'] == 'decoded':
            self.surface.grid_remove()
            self.canvas.grid()
            self.photo = tk.PhotoImage(data=event['image'])
            self.canvas.delete('all')
            self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
            self.canvas.configure(scrollregion=(0,0,event['display_width'],event['display_height']))
            self.status.set(f"源图 {event['width']} × {event['height']} · {event['format']} · 已加载实际源图")
        elif event['operation'] == 'motion_ready':
            self.canvas.grid_remove()
            self.surface.grid(row=0, column=0, sticky='nsew')
            self.update_idletasks()
            try:
                self.player = self.player or PreviewHost()
                self.player.send('load', path=event['path'], hwnd=self.surface.winfo_id(),
                                 libvlc_dir=str(self.page.vlc_dir))
                self.status.set('正在播放动态部分')
            except Exception:
                self.status.set('动态播放不可用，文件仍可在下载后使用外部播放器查看。')

    def control(self, operation):
        if self.player:
            try:self.player.send(operation)
            except Exception:self.status.set('播放进程不可用。')

    def poll_player(self):
        if self.player:
            try:
                while True:
                    event = self.player.events.get_nowait()
                    if event.get('operation') == 'error':
                        self.status.set('动态播放失败，可点击“播放动态”重新加载。')
                        if event.get('error_code') == 'worker_exited':
                            self.player.close()
                            self.player = None
                            break
                    elif event.get('operation') == 'playback' and event.get('state') == 'State.Ended':
                        self.status.set('播放完毕，可点击重播。')
            except Empty:pass
        self._poll_job = self.after(100, self.poll_player)

    def close(self):
        self.after_cancel(self._poll_job)
        self.page.service.cancel(self.requests)
        for identity in self.requests:self.page.callbacks.pop(identity, None)
        if self.player:self.player.close()
        self.page.popups.discard(self)
        self.destroy()


class GalleryPage(ctk.CTkFrame):
    def __init__(self, master, service, download, generation, vlc_dir):
        super().__init__(master, fg_color='transparent')
        self.service, self.download, self.generation, self.vlc_dir = service, download, generation, Path(vlc_dir)
        self.work = None
        self.selected, self.photos, self.callbacks, self.popups = {}, {}, {}, set()
        self.columnconfigure(0, weight=1)
        self.title_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        self.status_var = tk.StringVar()
        ctk.CTkLabel(self, textvariable=self.title_var, font=('Segoe UI Semibold',18),
                     text_color='#201F1C', wraplength=700, anchor='w').grid(row=0,column=0,sticky='ew')
        ctk.CTkLabel(self, textvariable=self.meta_var, text_color='#706D67', anchor='w').grid(row=1,column=0,sticky='ew',pady=(5,10))
        toolbar = ctk.CTkFrame(self,fg_color='transparent')
        toolbar.grid(row=2,column=0,sticky='ew',pady=(0,10))
        ttk.Button(toolbar,text='全选',command=lambda:self.select(True)).pack(side='left',padx=(0,8))
        ttk.Button(toolbar,text='取消全选',command=lambda:self.select(False)).pack(side='left')
        self.download_button = ctk.RoundedButton(toolbar,text='下载所选图片',width=160,height=38,
            fg_color='#292825',hover_color='#45433D',text_color='#FFFFFF',bg_color='#F6F5F1',command=download)
        self.download_button.pack(side='right')
        self.canvas = tk.Canvas(self,height=280,bg='#F6F5F1',highlightthickness=0)
        self.canvas.grid(row=3,column=0,sticky='ew')
        scroll = ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        scroll.grid(row=3,column=1,sticky='ns')
        self.canvas.configure(yscrollcommand=scroll.set)
        self.cards = tk.Frame(self.canvas,bg='#F6F5F1')
        self.window_id = self.canvas.create_window(0,0,window=self.cards,anchor='nw')
        self.cards.bind('<Configure>',lambda _event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda event:self.canvas.itemconfigure(self.window_id,width=event.width))
        self.cards.bind('<MouseWheel>',self.scroll)
        self.canvas.bind('<MouseWheel>',self.scroll)
        ctk.CTkLabel(self,textvariable=self.status_var,wraplength=700,anchor='w',text_color='#706D67').grid(row=4,column=0,sticky='ew',pady=(12,0))

    def scroll(self,event):
        self.canvas.yview_scroll(-int(event.delta/120),'units')
        return 'break'

    def select(self,value):
        for variable in self.selected.values():variable.set(value)

    def selection(self):
        return tuple(item.item_id for item in self.work.items if self.selected[item.item_id].get())

    def reset(self):
        for popup in tuple(self.popups):popup.close()
        self.service.cancel(self.callbacks.keys())
        self.callbacks.clear();self.photos.clear();self.selected.clear()
        for child in self.cards.winfo_children():child.destroy()
        self.work = None

    def show_work(self, work, generation):
        self.reset()
        self.work, self.generation = work, generation
        self.title_var.set(work.title or '未命名图文')
        count = str(work.declared_item_count) if work.declared_item_count is not None else '未知'
        platform = '抖音' if work.platform.value == 'douyin' else '小红书'
        self.meta_var.set(f'{platform} · {work.author or "作者未知"} · 已提取 {len(work.items)} 项 · 作品总数 {count}')
        self.status_var.set('点击小图查看源图；'+('动态项目会保存可取得的静态图和动态部分。'
            if LIVE_PHOTO_ENABLED else '当前仅保存静态图片，动态图文暂未支持。'))
        for column in range(4):self.cards.columnconfigure(column,weight=1)
        for position,item in enumerate(work.items):
            card = tk.Frame(self.cards,bg='#EFEEEA',bd=0)
            card.grid(row=position//4,column=position%4,sticky='nsew',padx=5,pady=5)
            label = tk.Label(card,text='加载小图…',bg='#E4E2DC',fg='#706D67',width=20,height=8,cursor='hand2')
            label.pack(fill='x',padx=5,pady=5)
            card.bind('<MouseWheel>',self.scroll)
            label.bind('<MouseWheel>',self.scroll)
            label.bind('<Button-1>',lambda _event,current=item:self.open_preview(current))
            variable = tk.BooleanVar(value=True)
            self.selected[item.item_id] = variable
            detail = f'第 {item.original_index} 张'
            if item.kind == 'live':
                detail += ' · 动态' if LIVE_PHOTO_ENABLED else ' · 仅静态'
            if Component.STILL in item.missing_components:detail += ' · 静态缺失'
            elif LIVE_PHOTO_ENABLED and item.missing_components:detail += ' · 部分缺失'
            checkbox=tk.Checkbutton(card,text=detail,variable=variable,bg='#EFEEEA',fg='#292825',activebackground='#EFEEEA')
            checkbox.pack(anchor='w')
            checkbox.bind('<MouseWheel>',self.scroll)
            candidates = item.still_candidates
            if candidates:
                candidate = (Candidate(item.preview_url,Component.STILL,'preview')
                             if item.preview_url and item.preview_url.startswith('https://') else candidates[0])
                identity = self.service.request(work.platform,candidate,generation,max_size=140)
                self.callbacks[identity] = lambda event,current=label,item_id=item.item_id:self.thumbnail(event,current,item_id)
            else:label.configure(text='未取得静态图')
        self.grid(row=4,column=0,sticky='ew',pady=(12,0))

    def thumbnail(self,event,label,item_id):
        if event['operation'] == 'decoded':
            self.photos[item_id] = tk.PhotoImage(data=event['image'])
            label.configure(image=self.photos[item_id],text='',width=155,height=145)
        else:label.configure(text='小图不可用\n点击尝试原图')

    def open_preview(self,item):
        popup = OriginalPreview(self,item)
        self.popups.add(popup)

    def handle(self,event):
        callback = self.callbacks.pop(event['request_id'],None)
        if callback and event['generation'] == self.generation:callback(event)

    def set_busy(self,busy):
        self.download_button.configure(state='disabled' if busy or not self.work else 'normal')

    def task_state(self,record,progress=None):
        percent = (progress or {}).get('percent')
        phase = (progress or {}).get('phase')
        detail = (f' · 当前下载 {percent:.0f}%' if percent is not None and record.state == TaskState.RUNNING
                  and phase not in {'processing','validating'} else '')
        message = (task_error_text(record.error_code) if record.state in {TaskState.PARTIAL,TaskState.FAILED}
                   else '可在任务页查看进度与操作。')
        self.status_var.set(LABELS[record.state]+detail+' · '+message)
