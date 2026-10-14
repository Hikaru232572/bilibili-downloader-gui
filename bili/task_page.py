from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import ui_kit as ctk

from .models import ACTIVE_STATES,TaskState
from .messages import error_text


LABELS = {'queued':'排队中','running':'下载中','validating':'校验中','succeeded':'已完成',
          'partial':'部分成功','failed':'失败','cancel_requested':'正在取消','cancelled':'已取消',
          'interrupted':'等待手动继续'}


class TaskPage(ctk.CTkFrame):
    def __init__(self,master,service,store,back,open_folder,progress=None):
        super().__init__(master,fg_color='#F6F5F1')
        self.service,self.store,self.open_folder = service,store,open_folder
        self.progress=progress if progress is not None else {}
        self.columnconfigure(0,weight=1)
        self.rowconfigure(2,weight=1)
        header=ctk.CTkFrame(self,fg_color='transparent')
        header.grid(row=0,column=0,sticky='ew',padx=30,pady=(24,12))
        ctk.RoundedButton(header,text='← 返回',command=back,width=92,height=34).pack(side='left')
        ctk.CTkLabel(header,text='下载任务',font=('Segoe UI',20,'bold')).pack(side='left',padx=18)
        self.message=tk.StringVar(value='任务按提交顺序下载；重启后请手动继续未完成任务。')
        ctk.CTkLabel(self,textvariable=self.message,anchor='w',wraplength=690).grid(
            row=1,column=0,sticky='ew',padx=30,pady=(0,12))
        container=ctk.CTkFrame(self,fg_color='transparent')
        container.grid(row=2,column=0,sticky='nsew',padx=30)
        container.rowconfigure(0,weight=1)
        container.columnconfigure(0,weight=1)
        style=ttk.Style(self)
        style.configure('Bili.Treeview',rowheight=40,font=('Segoe UI',10),
                        background='#FFFFFF',fieldbackground='#FFFFFF',foreground='#292825')
        self.tree=ttk.Treeview(container,columns=('work','state','files'),show='headings',
                              style='Bili.Treeview',selectmode='browse')
        for name,text,width in [('work','作品',350),('state','状态',135),('files','文件',95)]:
            self.tree.heading(name,text=text)
            self.tree.column(name,width=width,minwidth=65,stretch=name=='work')
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(container,orient='vertical',command=self.tree.yview)
        scroll.grid(row=0,column=1,sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>',lambda event:self._selection())
        self.detail=tk.StringVar()
        ctk.CTkLabel(self,textvariable=self.detail,anchor='w',justify='left',wraplength=690).grid(
            row=3,column=0,sticky='ew',padx=30,pady=12)
        actions=ctk.CTkFrame(self,fg_color='transparent')
        actions.grid(row=4,column=0,sticky='ew',padx=30,pady=(0,24))
        self.cancel_button=ctk.RoundedButton(actions,text='取消任务',command=self.cancel,width=110,height=36)
        self.cancel_button.pack(side='left')
        self.retry_button=ctk.RoundedButton(actions,text='继续 / 重试',command=self.retry,width=120,height=36)
        self.retry_button.pack(side='left',padx=10)
        ctk.RoundedButton(actions,text='打开目录',command=self.folder,width=110,height=36).pack(side='left')
        ctk.RoundedButton(actions,text='继续所有未完成',command=self.resume_all,width=150,height=36).pack(side='right')

    def refresh(self):
        current=set(self.tree.get_children())
        for record in self.store.tasks():
            task_id=record.spec.task_id
            outputs=self.store.outputs(task_id)
            complete=sum(o['state']=='succeeded' for o in outputs)
            status=LABELS[record.state]
            progress=self.progress.get(task_id,{})
            if record.state==TaskState.RUNNING and progress.get('attempt_id')==record.attempt_id:
                if progress.get('phase')=='processing':
                    status='正在处理'
                elif progress.get('percent') is not None:
                    status=f"下载 {progress['percent']:.0f}%"
            values=(record.spec.title or record.spec.work_id,status,f'{complete}/{len(outputs)}')
            if task_id in current:
                self.tree.item(task_id,values=values)
                current.remove(task_id)
            else:
                self.tree.insert('', 'end',iid=task_id,values=values)
        for task_id in current:
            self.tree.delete(task_id)
        self._selection()

    def _selected(self):
        selection=self.tree.selection()
        return selection[0] if selection else None

    def _selection(self):
        task_id=self._selected()
        if not task_id:
            self.cancel_button.configure(state='disabled')
            self.retry_button.configure(state='disabled')
            self.detail.set('选择任务查看结果。')
            return
        record=self.store.task(task_id)
        self.cancel_button.configure(state='normal' if record.state in ACTIVE_STATES or record.state==TaskState.QUEUED else 'disabled')
        self.retry_button.configure(state='normal' if record.state in {TaskState.PARTIAL,TaskState.FAILED,TaskState.CANCELLED,TaskState.INTERRUPTED} else 'disabled')
        lines=[record.spec.output_dir]
        if record.message:
            lines.append(record.message)
        if record.error_code:
            if error_text(record.error_code) not in lines:
                lines.append(error_text(record.error_code))
        for output in self.store.outputs(task_id):
            if output['path']:
                lines.append(output['path'])
            if output['error_code']:
                lines.append(output['component']+'：'+error_text(output['error_code']))
        self.detail.set('\n'.join(lines))

    def cancel(self):
        if task_id:=self._selected():
            self.service.cancel(task_id)
            self.refresh()

    def retry(self):
        if task_id:=self._selected():
            self.service.retry(task_id)
            self.refresh()

    def resume_all(self):
        for record in self.store.tasks():
            if record.state==TaskState.INTERRUPTED:
                self.service.retry(record.spec.task_id)
        self.refresh()

    def folder(self):
        if task_id:=self._selected():
            self.open_folder(self.store.task(task_id).spec.output_dir)
