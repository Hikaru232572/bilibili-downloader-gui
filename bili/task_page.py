from __future__ import annotations

from datetime import datetime
import math
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

import ui_kit as ctk

from .models import ACTIVE_STATES, TaskState
from .messages import task_error_text
from .storage import SUCCESS_LINGER


LABELS = {'queued':'排队中', 'running':'下载中', 'validating':'校验中', 'succeeded':'已完成',
          'partial':'部分成功', 'failed':'下载失败', 'cancel_requested':'正在取消',
          'cancelled':'已取消', 'interrupted':'等待继续'}
RETRY_STATES = {TaskState.PARTIAL, TaskState.FAILED, TaskState.CANCELLED, TaskState.INTERRUPTED}
PAGE_BG = '#F6F5F1'
CARD_BG = '#EFEEEA'


def cancel_widget_callbacks(widget):
    """Dynamic cards can disappear before the UI kit's initial idle draws."""
    owners = {}
    def collect(item):
        owners.update({command:item for command in (item._tclCommands or ())})
        for child in item.winfo_children():
            collect(child)
    collect(widget)
    for timer in widget.tk.splitlist(widget.tk.call('after','info')):
        script = widget.tk.call('after','info',timer)[0]
        owner = owners.get(widget.tk.splitlist(script)[0])
        if owner:
            owner.after_cancel(timer)


def byte_text(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


class TitleTooltip:
    def __init__(self, label):
        self.label = label
        self.text = ''
        self.timer = self.window = None
        label.bind('<Enter>', self.schedule)
        label.bind('<Leave>', self.hide)
        label.bind('<ButtonPress>', self.hide)
        label.bind('<Unmap>', self.hide)
        label.bind('<Destroy>', self.hide)

    def schedule(self, _event=None):
        self.hide()
        self.timer = self.label.after(500, self.show)

    def show(self):
        self.timer = None
        if not self.text or not self.label.winfo_ismapped():
            return
        self.window = tk.Toplevel(self.label)
        self.window.withdraw()
        self.window.overrideredirect(True)
        tk.Label(self.window, text=self.text, wraplength=460, justify='left',
                 bg='#292825', fg='white', padx=12, pady=8, font=('Segoe UI', 10)).pack()
        self.window.update_idletasks()
        x = min(self.label.winfo_rootx(), self.window.winfo_screenwidth()-self.window.winfo_reqwidth()-12)
        y = min(self.label.winfo_rooty()+self.label.winfo_height()+5,
                self.window.winfo_screenheight()-self.window.winfo_reqheight()-12)
        self.window.geometry(f'+{max(0,x)}+{max(0,y)}')
        self.window.deiconify()

    def hide(self, _event=None):
        if self.timer:
            self.label.after_cancel(self.timer)
            self.timer = None
        if self.window:
            self.window.destroy()
            self.window = None


class TaskCard(ctk.RoundedPanel):
    def __init__(self, master, page, task_id, history):
        super().__init__(master, height=166, corner_radius=16, fill=CARD_BG,
                         page_bg=PAGE_BG, padding=16)
        self.page, self.task_id, self.history = page, task_id, history
        self.content.columnconfigure(0, weight=1)
        heading = tk.Frame(self.content, bg=CARD_BG)
        heading.grid(row=0, column=0, sticky='ew')
        heading.columnconfigure(0, weight=1)
        self.title_label = tk.Label(heading, anchor='w', bg=CARD_BG, fg='#292825',
                                    font=('Segoe UI', 11, 'bold'), width=1)
        self.title_label.grid(row=0, column=0, sticky='ew')
        self.status_var = tk.StringVar()
        tk.Label(heading, textvariable=self.status_var, bg=CARD_BG, fg='#625F58',
                 font=('Segoe UI', 10)).grid(row=0, column=1, padx=(12,0))
        self.tooltip = TitleTooltip(self.title_label)
        self.title_label.bind('<Configure>', self._fit_title)
        self.meta_var = tk.StringVar()
        tk.Label(self.content, textvariable=self.meta_var, anchor='w', bg=CARD_BG, fg='#625F58',
                 font=('Segoe UI', 10)).grid(row=1, column=0, sticky='ew', pady=(8,8))
        self.bar = ctk.WarmProgressBar(self.content, height=7, bg_color=CARD_BG)
        self.bar.grid(row=2, column=0, sticky='ew')
        self.bar.bind('<Map>',lambda e: self.bar.start() if self.bar.cget('mode')=='indeterminate' else None)
        self.bar.bind('<Unmap>',lambda e: self.bar.stop())
        self.note_var = tk.StringVar()
        self.note = tk.Label(self.content, textvariable=self.note_var, anchor='w',
                             bg=CARD_BG, fg='#625F58', font=('Segoe UI', 9), width=1)
        self.note.grid(row=3, column=0, sticky='ew', pady=(7,5))
        self.note.bind('<Configure>', lambda e: self.note.configure(wraplength=max(1,e.width)))
        actions = tk.Frame(self.content, bg=CARD_BG)
        actions.grid(row=4, column=0, sticky='e')
        self.buttons = {}
        for key, text, width, command in (
            ('cancel', '取消', 62, lambda: page.cancel(task_id)),
            ('retry', '重试', 76, lambda: page.retry(task_id)),
            ('remove', '移除任务', 86, lambda: page.remove(task_id)),
            ('folder', '打开目录', 90, lambda: page.folder(task_id)),
        ):
            button = ctk.RoundedButton(actions, text=text, width=width, height=30,
                corner_radius=12, bg_color=CARD_BG, font=('Segoe UI', 10, 'bold'), command=command)
            button.configure(takefocus=True)
            button.bind('<Return>', lambda e, b=button: b.invoke())
            button.bind('<space>', lambda e, b=button: b.invoke())
            self.buttons[key] = button
        self.full_title = ''
        self._signature = None
        page.bind_wheel(self)

    def _fit_title(self, _event=None):
        available = max(1, self.title_label.winfo_width()-4)
        font = tkfont.Font(font=self.title_label.cget('font'))
        title = self.full_title.replace('\n', ' ').replace('\r', ' ')
        if font.measure(title) > available:
            lo, hi = 0, len(title)
            while lo < hi:
                mid = (lo+hi+1)//2
                if font.measure(title[:mid]+'…') <= available:
                    lo = mid
                else:
                    hi = mid-1
            title = title[:lo]+'…'
        self.title_label.configure(text=title)
        self.tooltip.text = self.full_title

    def update_task(self, record, outputs, progress):
        signature = (record, tuple((o['state'],o['error_code']) for o in outputs), repr(progress))
        if signature == self._signature:
            return
        self._signature = signature
        self.full_title = record.spec.title or record.spec.work_id
        self._fit_title()
        state = record.state
        current = progress if progress.get('attempt_id') == record.attempt_id else {}
        phase = current.get('phase')
        status = LABELS[state]
        if state == TaskState.RUNNING:
            status = {'processing':'正在处理', 'validating':'校验中'}.get(phase, status)
        self.status_var.set(status)
        complete = sum(o['state']=='succeeded' for o in outputs)
        meta = [f'文件 {complete}/{len(outputs)}']
        note = ''
        percent = current.get('percent')
        transferring = state == TaskState.RUNNING and phase not in {'processing','validating'}
        if self.history:
            meta.append(datetime.fromtimestamp(record.completed_at).strftime('%m月%d日 %H:%M 完成'))
        elif transferring:
            if percent is not None:
                meta.append(f'当前下载 {percent:.0f}%')
            size, total, speed = (byte_text(current.get(k)) for k in ('bytes','total','speed'))
            if size:
                meta.append(f'{size} / {total}' if total else f'已下载 {size}')
            if speed:
                meta.append(speed+'/s')
        self.meta_var.set('  ·  '.join(meta))
        self.bar.stop()
        self.bar.configure(mode='determinate')
        if state == TaskState.SUCCEEDED:
            self.bar.set(1)
            note = '已保存，可打开目录查看。' if self.history else '已完成，稍后将从进行中移除。'
        elif transferring and percent is not None:
            self.bar.set(percent/100)
        elif state in ACTIVE_STATES:
            self.bar.configure(mode='indeterminate')
            if self.winfo_ismapped():
                self.bar.start()
        else:
            # A file count is not a byte percentage.
            self.bar.set(0)
        if state in {TaskState.FAILED,TaskState.PARTIAL}:
            code = record.error_code or next((o['error_code'] for o in outputs if o['error_code']), None)
            note = task_error_text(code)
        elif state == TaskState.INTERRUPTED:
            note = '上次下载已中断，点击继续恢复。'
        elif state == TaskState.CANCELLED:
            note = '下载已停止，已保存的文件保留。'
        self.note_var.set(note)
        for button in self.buttons.values():
            button.pack_forget()
        visible = []
        if not self.history:
            if state in RETRY_STATES:
                self.buttons['retry'].configure(text='继续' if state in {TaskState.INTERRUPTED,TaskState.CANCELLED} else '重试')
                visible.append('retry')
            if state not in {TaskState.CANCELLED,TaskState.SUCCEEDED}:
                self.buttons['cancel'].configure(state='disabled' if state==TaskState.CANCEL_REQUESTED else 'normal')
                visible.append('cancel')
            if state not in ACTIVE_STATES and state!=TaskState.QUEUED:
                visible.append('remove')
        for key in visible+['folder']:
            self.buttons[key].pack(side='left', padx=(7,0))

    def destroy(self):
        self.tooltip.hide()
        self.bar.stop()
        cancel_widget_callbacks(self)
        super().destroy()
        # Release Tcl variables on the UI thread, even when a background
        # downloader later triggers Python's cyclic garbage collector.
        self.status_var = self.meta_var = self.note_var = None


class TaskPage(ctk.CTkFrame):
    def __init__(self, master, service, store, back, open_folder, progress=None, on_refresh=None):
        super().__init__(master, fg_color=PAGE_BG)
        self.service, self.store, self.open_folder = service, store, open_folder
        self.on_refresh = on_refresh
        self.progress = progress if progress is not None else {}
        self.cards = {}
        self._refresh_timer = self._layout_timer = self._linger_timer = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self.header = header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0,column=0,sticky='ew',padx=42,pady=(34,12))
        ctk.RoundedButton(header,text='← 返回',command=back,width=92,height=34).pack(side='left')
        ctk.CTkLabel(header,text='下载任务',font=('Segoe UI',20,'bold')).pack(side='left',padx=18)
        self.message = tk.StringVar(value='按提交顺序下载；重启后可手动继续未完成任务。')
        ctk.CTkLabel(self,textvariable=self.message,anchor='w',text_color='#625F58').grid(
            row=1,column=0,sticky='ew',padx=30,pady=(0,16))
        tabs = ctk.CTkFrame(self,fg_color='transparent')
        tabs.grid(row=2,column=0,sticky='ew',padx=30,pady=(0,14))
        self.tab = tk.StringVar(value='queue')
        self.tabs = ctk.SegmentedControl(tabs, options=[('进行中','queue'),('最近完成','history')],
            variable=self.tab,command=self.switch_tab,width=230,height=38)
        self.tabs.pack(side='left')
        self.count_var = tk.StringVar()
        ctk.CTkLabel(tabs,textvariable=self.count_var,text_color='#625F58').pack(side='right')
        self.container = ctk.CTkFrame(self,fg_color='transparent')
        self.container.grid(row=3,column=0,sticky='ew',padx=30)
        self.container.columnconfigure(0,weight=1)
        self.canvas = tk.Canvas(self.container,bg=PAGE_BG,highlightthickness=0,height=132)
        self.canvas.grid(row=0,column=0,sticky='ew')
        self.scroll = ttk.Scrollbar(self.container,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set,yscrollincrement=24)
        self.body = tk.Frame(self.canvas,bg=PAGE_BG)
        self.body.columnconfigure(0,weight=1)
        self.body_window = self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.canvas.bind('<Configure>',self._canvas_resize)
        self.body.bind('<Configure>',self._schedule_layout)
        self.bind('<Configure>',self._schedule_layout)
        self.empty = ctk.RoundedPanel(self.body,height=132,corner_radius=16,fill=CARD_BG,padding=22)
        self.empty_title = tk.StringVar()
        self.empty_hint = tk.StringVar()
        ctk.CTkLabel(self.empty.content,textvariable=self.empty_title,font=('Segoe UI',13,'bold')).pack(pady=(3,8))
        ctk.CTkLabel(self.empty.content,textvariable=self.empty_hint,text_color='#625F58').pack()
        self.actions = ctk.CTkFrame(self,fg_color='transparent')
        self.actions.grid(row=4,column=0,sticky='ew',padx=30,pady=(14,24))
        self.resume_button = ctk.RoundedButton(self.actions,text='继续所有未完成',command=self.resume_all,width=150,height=34)
        self.clear_button = ctk.RoundedButton(self.actions,text='清空完成记录',command=self.clear_history,width=132,height=34)
        self.bind_wheel(self.canvas)
        self.bind_wheel(self.empty)
        self.bind('<Map>',lambda e: self.request_refresh())
        self._maintenance_timer = self.after(1000,self._maintenance)
        self.refresh()

    def bind_wheel(self, widget):
        widget.bind('<MouseWheel>',self._wheel,add='+')
        for child in widget.winfo_children():
            self.bind_wheel(child)

    def _wheel(self, event):
        if self.scroll.winfo_manager():
            self.canvas.yview_scroll(-int(event.delta/120) or (-1 if event.delta>0 else 1),'units')
        return 'break'

    def _canvas_resize(self, event):
        self.canvas.itemconfigure(self.body_window,width=event.width)
        self._schedule_layout()

    def _schedule_layout(self, _event=None):
        if self._layout_timer is None:
            self._layout_timer = self.after_idle(self._fit_list)

    def _fit_list(self):
        self._layout_timer = None
        content = self.body.winfo_reqheight()
        available = max(132,self.winfo_height()-self.container.winfo_y()-self.actions.winfo_reqheight()-42)
        height = min(content,480,available)
        if int(float(self.canvas.cget('height'))) != height:
            self.canvas.configure(height=height)
        self.canvas.configure(scrollregion=(0,0,self.canvas.winfo_width(),content))
        if content>height:
            self.scroll.grid(row=0,column=1,sticky='ns',padx=(8,0))
        else:
            self.scroll.grid_remove()
            self.canvas.yview_moveto(0)

    def request_refresh(self):
        if self._refresh_timer is None:
            self._refresh_timer = self.after(50,self.refresh)

    def _maintenance(self):
        for progress in self.progress.values():
            if time.monotonic()-(progress.get('measured_at') or 0)>3:
                progress['speed'] = None
        # Keep expiry working even while the task page is hidden.
        self.refresh()
        self._maintenance_timer = self.after(1000,self._maintenance)

    def switch_tab(self):
        for card in self.cards.values():
            card.destroy()
        self.cards.clear()
        self.canvas.yview_moveto(0)
        self.refresh()

    def refresh(self):
        if self._refresh_timer:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None
        if self._linger_timer:
            self.after_cancel(self._linger_timer)
            self._linger_timer = None
        history = self.tab.get()=='history'
        entries = self.store.page_snapshot(history=history)
        wanted = {r.spec.task_id for r,_ in entries}
        for task_id in set(self.cards)-wanted:
            self.cards.pop(task_id).destroy()
            self.progress.pop(task_id,None)
        self.empty.grid_remove()
        self.count_var.set(f'{len(entries)} 条记录' if history else f'{len(entries)} 个任务')
        for i,(record,outputs) in enumerate(entries):
            task_id = record.spec.task_id
            if task_id not in self.cards:
                self.cards[task_id] = TaskCard(self.body,self,task_id,history)
            card = self.cards[task_id]
            card.grid(row=i,column=0,sticky='ew',pady=(0,10))
            card.update_task(record,outputs,self.progress.get(task_id,{}))
        if not entries:
            self.empty_title.set('暂无完成记录' if history else '暂无进行中的任务')
            self.empty_hint.set('下载完成后会显示在这里，保留 7 天。' if history else '添加下载后，可在这里查看进度。')
            self.empty.grid(row=0,column=0,sticky='ew')
        self.resume_button.pack_forget()
        self.clear_button.pack_forget()
        if history:
            self.clear_button.configure(state='normal' if entries else 'disabled')
            self.clear_button.pack(side='right')
        elif any(r.state==TaskState.INTERRUPTED for r,_ in entries):
            self.resume_button.pack(side='right')
        completions = [r.completed_at for r,_ in entries if r.state==TaskState.SUCCEEDED]
        if not history and completions:
            # Recompute the remaining time, so progress from another task
            # cannot restart a success card's ten-second grace period.
            delay = max(1,math.ceil((min(completions)+SUCCESS_LINGER-time.time())*1000))
            self._linger_timer = self.after(delay,self.refresh)
        self._schedule_layout()

        if self.on_refresh:
            self.on_refresh()

    def cancel(self, task_id):
        self.service.cancel(task_id)
        state = self.store.task(task_id).state
        self.message.set('正在取消任务…' if state==TaskState.CANCEL_REQUESTED else
                         '任务已取消，已保存的文件保留。' if state==TaskState.CANCELLED else '任务状态已更新。')
        self.refresh()

    def retry(self, task_id):
        self.service.retry(task_id)
        self.progress.pop(task_id,None)
        self.message.set('已加入下载队列。')
        self.refresh()

    def remove(self, task_id):
        self.store.remove_task(task_id)
        self.message.set('任务记录已移除，已下载文件保留。')
        self.refresh()

    def clear_history(self):
        self.store.clear_history()
        self.message.set('完成记录已清空，已下载文件保留。')
        self.refresh()

    def resume_all(self):
        for record in self.store.queue_tasks():
            if record.state==TaskState.INTERRUPTED:
                self.service.retry(record.spec.task_id)
        self.message.set('未完成任务已加入队列。')
        self.refresh()

    def folder(self, task_id):
        self.open_folder(self.store.task(task_id).spec.output_dir)

    def destroy(self):
        cancel_widget_callbacks(self)
        super().destroy()
