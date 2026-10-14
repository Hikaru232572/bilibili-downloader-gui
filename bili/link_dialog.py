import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit


class LinkDialog(tk.Toplevel):
    def __init__(self, master, links, selected):
        super().__init__(master)
        self.title('选择一个作品')
        self.geometry('650x320')
        self.configure(bg='#F6F5F1')
        self.transient(master)
        self.grab_set()
        self.variable = tk.IntVar(value=0)
        tk.Label(self,text='发现多个作品，请选择本次解析的一个。',bg='#F6F5F1',fg='#292825',
                 font=('Segoe UI',12)).pack(anchor='w',padx=24,pady=(20,12))
        body = tk.Frame(self,bg='#F6F5F1')
        body.pack(fill='both',expand=True,padx=24)
        canvas = tk.Canvas(body,bg='#F6F5F1',highlightthickness=0)
        canvas.pack(side='left',fill='both',expand=True)
        scroll = ttk.Scrollbar(body,orient='vertical',command=canvas.yview)
        scroll.pack(side='right',fill='y')
        canvas.configure(yscrollcommand=scroll.set)
        options = tk.Frame(canvas,bg='#F6F5F1')
        canvas.create_window(0,0,window=options,anchor='nw')
        names={'bilibili':'B站','douyin':'抖音','xiaohongshu':'小红书'}
        for index, link in enumerate(links):
            parts = urlsplit(link.url)
            label = names[link.platform]+' · '+(link.work_id or parts.hostname+parts.path)
            tk.Radiobutton(options,text=label,variable=self.variable,value=index,bg='#F6F5F1',
                           activebackground='#F6F5F1',anchor='w',wraplength=560).pack(anchor='w',pady=6)
        options.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        actions = tk.Frame(self,bg='#F6F5F1')
        actions.pack(fill='x',padx=24,pady=18)
        def choose():
            link = links[self.variable.get()]
            self.destroy()
            selected(link)
        ttk.Button(actions,text='解析所选作品',command=choose).pack(side='right')
        ttk.Button(actions,text='取消',command=self.destroy).pack(side='right',padx=10)
