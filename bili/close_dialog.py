import tkinter as tk

import ui_kit as ctk


class CloseDialog(tk.Toplevel):
    def __init__(self,parent,choose):
        super().__init__(parent)
        self.title('关闭 Bili')
        self.configure(bg='#F6F5F1')
        self.resizable(False,False)
        self.transient(parent)
        self.geometry('440x210')
        self.remember=tk.BooleanVar(value=False)
        ctk.CTkLabel(self,text='退出应用，还是交给后台继续？',font=('Segoe UI',16,'bold')).pack(pady=(24,10))
        ctk.CTkLabel(self,text='退出会保存未完成任务，下次由你手动继续。\n后台托管会在系统托盘中继续当前队列。',
                     justify='left').pack(pady=(0,12))
        ctk.SoftCheckBox(self,text='保存选择，以后不再询问',variable=self.remember).pack()
        buttons=ctk.CTkFrame(self,fg_color='transparent')
        buttons.pack(pady=16)
        def action(choice):
            remember=self.remember.get()
            self.grab_release()
            self.destroy()
            choose(choice,remember)
        self.exit_button=ctk.RoundedButton(buttons,text='直接退出',command=lambda:action('exit'),width=120,height=36)
        self.exit_button.pack(side='left',padx=6)
        self.tray_button=ctk.RoundedButton(buttons,text='后台托管',command=lambda:action('tray'),width=120,height=36)
        self.tray_button.pack(side='left',padx=6)
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        self.update_idletasks()
        self.grab_set()
