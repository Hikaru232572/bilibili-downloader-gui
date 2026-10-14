import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock,patch

from app import BiliDownloaderApp
from bili.windows import SingleInstance,TrayService


ROOT=Path(__file__).resolve().parents[1]


class CloseUiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.app=BiliDownloaderApp(data_dir=Path(self.directory.name))
        self.app.withdraw()
        self.app.tray=Mock()
        self.app.notifications=Mock()

    def tearDown(self):
        if not self.app._destroying:self.app.destroy()
        self.directory.cleanup()

    def test_close_asks_without_jobs_and_once_choice_keeps_asking(self):
        self.app.request_close()
        prompt=self.app._close_dialog
        self.assertTrue(prompt.winfo_exists())
        prompt.tray_button.invoke()
        self.app.tray.start.assert_called_once()
        self.assertEqual(self.app.task_store.get_preference('close_behavior','ask'),'ask')
        self.app.request_close()
        self.assertTrue(self.app._close_dialog.winfo_exists())
        self.assertIsNot(prompt,self.app._close_dialog)

    def test_remembered_tray_and_settings_can_reset_to_ask(self):
        self.app.request_close()
        self.app._close_dialog.remember.set(True)
        self.app._close_dialog.tray_button.invoke()
        self.assertEqual(self.app.task_store.get_preference('close_behavior'),'tray')
        with patch('app.CloseDialog') as prompt:
            self.app.request_close()
        prompt.assert_not_called()
        self.app.show_settings_page()
        self.app.settings_state['close_control'].invoke('ask')
        self.app.settings_state['save_button'].invoke()
        self.assertEqual(self.app.task_store.get_preference('close_behavior'),'ask')
        self.app.request_close()
        self.assertTrue(self.app._close_dialog.winfo_exists())

    def test_cancel_close_prompt_keeps_application_alive(self):
        self.app.request_close()
        self.app._close_dialog.destroy()
        self.assertFalse(self.app._destroying)
        self.assertEqual(self.app.task_store.get_preference('close_behavior','ask'),'ask')

    def test_explicit_tray_exit_does_not_ask_again(self):
        self.app.task_store.set_preference('close_behavior','tray')
        self.app.queue.put(('windows_action','exit'))
        with patch('app.CloseDialog') as prompt:
            self.app._drain_queue()
        prompt.assert_not_called()
        self.assertTrue(self.app._destroying)
        self.app.tray.stop.assert_called_once()

    def test_batch_notification_without_selected_page_task_is_safe(self):
        self.app._page_task_id=None
        summary={'operation':'batch_finished','batch_id':'test','counts':{'failed':1}}
        self.app._handle_task_event(summary)
        self.app.notifications.show.assert_called_once_with(summary)


@unittest.skipUnless(os.name=='nt','Windows native integration')
class WindowsLifecycleTests(unittest.TestCase):
    def test_second_process_signals_owner_without_acquiring_new_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            gate=SingleInstance(Path(directory))
            try:
                self.assertTrue(gate.owner)
                code='from pathlib import Path;from bili.windows import SingleInstance;import sys;g=SingleInstance(Path(sys.argv[1]));print(g.owner);g.close()'
                result=subprocess.run([sys.executable,'-c',code,directory],cwd=ROOT,
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'False')
                self.assertTrue(gate.activation_pending())
                self.assertFalse(gate.activation_pending())
            finally:gate.close()

    def test_real_tray_initializes_and_menu_actions_only_post_messages(self):
        actions=[]
        tray=TrayService(actions.append,ROOT/'app.ico')
        try:
            tray.start()
            self.assertTrue(tray.icon.visible)
            self.assertTrue(tray.icon._hwnd)
            tray.icon.menu.items[1](tray.icon)
            self.assertEqual(actions,['tasks'])
        finally:tray.stop()
