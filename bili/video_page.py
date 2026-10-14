import tkinter as tk

import ui_kit as ctk

from .gallery_page import GalleryPage
from .models import Component


class DouyinVideoPage(ctk.CTkFrame):
    def __init__(self,master,download):
        super().__init__(master,fg_color='transparent')
        self.work=None
        self.columnconfigure(0,weight=1)
        self.title_var=tk.StringVar()
        self.meta_var=tk.StringVar()
        self.status_var=tk.StringVar()
        self.video_var=tk.BooleanVar(value=True)
        self.audio_var=tk.BooleanVar(value=False)
        self.audio_policy=tk.StringVar(value='original')
        ctk.CTkLabel(self,textvariable=self.title_var,font=('Segoe UI Semibold',18),wraplength=680,
                     anchor='w',text_color='#201F1C').grid(row=0,column=0,sticky='ew',pady=(8,4))
        ctk.CTkLabel(self,textvariable=self.meta_var,wraplength=680,anchor='w',
                     text_color='#706D67').grid(row=1,column=0,sticky='ew',pady=(0,20))
        outputs=ctk.CTkFrame(self,fg_color='transparent')
        outputs.grid(row=2,column=0,sticky='ew',pady=(0,20))
        for text,variable in [('保存视频',self.video_var),('保存完整音频',self.audio_var)]:
            tk.Checkbutton(outputs,text=text,variable=variable,bg='#F6F5F1',fg='#292825',
                           activebackground='#F6F5F1',font=('Segoe UI',13)).pack(side='left',padx=(0,28))
        ctk.CTkLabel(self,text='音频保存方式',anchor='w',text_color='#706D67').grid(row=3,column=0,sticky='w',pady=(0,8))
        ctk.SegmentedControl(self,options=[('保留原始编码','original'),('转码 MP3','mp3'),('转码 WAV','wav')],
                             variable=self.audio_policy,bg_color='#F6F5F1').grid(row=4,column=0,sticky='ew')
        ctk.CTkLabel(self,text='优先提取可用播放资源；水印状态无法确认时保留实际取得的版本。\n'
                     '原始音频按实际编码保存；选择 MP3/WAV 会转码。',text_color='#706D67',
                     anchor='w',wraplength=680).grid(row=5,column=0,sticky='ew',pady=(16,16))
        self.download_button=ctk.RoundedButton(self,text='加入下载队列',width=180,height=44,
            fg_color='#292825',hover_color='#45433D',text_color='#FFFFFF',bg_color='#F6F5F1',command=download)
        self.download_button.grid(row=6,column=0,pady=(8,12))
        ctk.CTkLabel(self,textvariable=self.status_var,wraplength=680,anchor='w',
                     text_color='#706D67').grid(row=7,column=0,sticky='ew')

    def show_work(self,work):
        self.work=work
        self.title_var.set(work.title or '未命名视频')
        item=work.items[0]
        dimensions={(candidate.width,candidate.height) for candidate in item.video_candidates if candidate.width and candidate.height}
        quality=' / '.join(f'{width} × {height}' for width,height in sorted(dimensions)) if dimensions else '清晰度未知'
        self.meta_var.set(f'抖音 · {work.author or "作者未知"} · {quality}')
        self.video_var.set(True);self.audio_var.set(False);self.audio_policy.set('original')
        self.status_var.set('可分别选择视频、音频，也可以同时保存。')
        self.grid(row=4,column=0,sticky='ew',pady=(12,0))

    def components(self):
        return tuple(component for chosen,component in ((self.video_var.get(),Component.VIDEO),
                                                        (self.audio_var.get(),Component.AUDIO)) if chosen)

    def set_busy(self,busy):
        self.download_button.configure(state='disabled' if busy or not self.work else 'normal')

    task_state=GalleryPage.task_state
