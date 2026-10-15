from pathlib import Path
import gc
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from bili.models import Component, OutputSpec, Platform, TaskSpec, TaskState
from bili.storage import SUCCESS_LINGER, TaskStore
from bili.task_page import TaskPage


class TaskCardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root_path = Path(self.directory.name)
        self.store = TaskStore(self.root_path/'tasks.sqlite3')
        self.root = tk.Tk()
        self.root.geometry('780x690')
        self.errors = []
        self.root.report_callback_exception = lambda *args:self.errors.append(args)
        self.service = Mock()
        self.service.cancel.side_effect = self.store.request_cancel
        self.service.retry.side_effect = self.store.retry
        self.folder = Mock()
        self.progress = {}
        self.page = TaskPage(self.root,self.service,self.store,Mock(),self.folder,self.progress)
        self.page.pack(fill='both',expand=True)
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        finally:
            self.store.close()
            self.directory.cleanup()
        self.page = self.root = None
        gc.collect()
        self.assertEqual(self.errors,[])

    def make(self, name, state=TaskState.QUEUED):
        spec = TaskSpec(Platform.BILIBILI,name,name,'source-ref',str(self.root_path),('item',),(Component.VIDEO,))
        output = OutputSpec(spec.task_id,'item',Component.VIDEO,1)
        self.store.submit(spec,(output,))
        if state!=TaskState.QUEUED:
            record = self.store.claim_next()
            self.assertEqual(record.spec.task_id,spec.task_id)
            if state==TaskState.SUCCEEDED:
                file = self.root_path/(spec.task_id+'.mp4')
                file.write_bytes(b'user-download')
                self.store.update_output(output.output_id,state='succeeded',path=str(file),
                    evidence={'size':file.stat().st_size},attempt_id=record.attempt_id)
                self.store.transition(spec.task_id,TaskState.VALIDATING,attempt_id=record.attempt_id)
            if state!=TaskState.RUNNING:
                self.store.transition(spec.task_id,state,attempt_id=record.attempt_id)
        self.page.refresh()
        self.root.update()
        return spec

    def test_empty_few_and_many_cards_use_content_height_then_scroll(self):
        self.assertTrue(self.page.empty.winfo_ismapped())
        self.assertLessEqual(self.page.canvas.winfo_height(),150)
        self.assertEqual(self.page.scroll.winfo_manager(),'')
        a = self.make('one')
        self.assertFalse(self.page.empty.winfo_ismapped())
        one_height = self.page.canvas.winfo_height()
        self.assertLess(one_height,220)
        self.make('two')
        self.assertGreater(self.page.canvas.winfo_height(),one_height)
        self.assertEqual(self.page.scroll.winfo_manager(),'')
        for i in range(30):
            self.make('more-'+str(i))
        self.assertEqual(self.page.scroll.winfo_manager(),'grid')
        self.assertLessEqual(self.page.canvas.winfo_height(),480)
        self.page.canvas.yview_moveto(1)
        self.root.update()
        last = list(self.page.cards.values())[-1]
        button = last.buttons['folder']
        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),
                             self.page.canvas.winfo_rooty()+self.page.canvas.winfo_height())
        self.assertGreater(self.page.canvas.yview()[0],0)
        self.assertFalse(hasattr(self.page,'tree'))
        self.assertFalse(hasattr(self.page,'detail'))
        self.page.folder(a.task_id)
        self.folder.assert_called_once_with(str(self.root_path))

    def test_long_title_is_ellipsized_and_hover_shows_complete_title(self):
        title = '这是一段很长的标题，用于检查窗口中的省略显示。'*10
        spec = self.make(title)
        card = self.page.cards[spec.task_id]
        self.assertTrue(card.title_label.cget('text').endswith('…'))
        self.assertLess(len(card.title_label.cget('text')),len(title))
        card.title_label.event_generate('<Enter>')
        deadline = time.monotonic()+1
        while card.tooltip.window is None and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNotNone(card.tooltip.window)
        self.assertEqual(card.tooltip.text,title)
        self.page.tabs.invoke('history')
        self.root.update()
        self.assertIsNone(card.tooltip.window)

    def test_metrics_are_measured_current_transfer_and_processing_has_no_percent(self):
        spec = self.make('progress',TaskState.RUNNING)
        record = self.store.task(spec.task_id)
        card = self.page.cards[spec.task_id]
        self.assertNotIn('%',card.meta_var.get())
        self.assertEqual(card.bar.cget('mode'),'indeterminate')
        self.progress[spec.task_id] = dict(attempt_id=record.attempt_id,percent=42,
            bytes=21*1024*1024,total=50*1024*1024,speed=2*1024*1024,phase='downloading')
        self.page.refresh()
        self.assertIn('当前下载 42%',card.meta_var.get())
        self.assertIn('21.0 MB / 50.0 MB',card.meta_var.get())
        self.assertIn('2.0 MB/s',card.meta_var.get())
        self.assertAlmostEqual(card.bar.cget('value'),.42)
        self.progress[spec.task_id]['phase'] = 'processing'
        self.page.refresh()
        self.assertEqual(card.status_var.get(),'正在处理')
        self.assertNotIn('%',card.meta_var.get())
        self.assertNotIn('MB/s',card.meta_var.get())
        self.assertEqual(card.bar.cget('mode'),'indeterminate')
        self.page.tabs.invoke('history')
        self.page.tabs.invoke('queue')
        self.assertIn(spec.task_id,self.progress)

    def test_success_is_in_both_tabs_then_leaves_queue_even_when_page_hidden(self):
        spec = self.make('success',TaskState.SUCCEEDED)
        completed = self.store.task(spec.task_id).completed_at
        self.assertIn(spec.task_id,self.page.cards)
        self.page.tabs.invoke('history')
        self.assertIn(spec.task_id,self.page.cards)
        self.assertTrue(self.page.cards[spec.task_id].history)
        self.assertEqual(self.page.cards[spec.task_id].buttons['retry'].winfo_manager(),'')
        self.page.tabs.invoke('queue')
        self.page.pack_forget()
        with patch('bili.storage.time.time',return_value=completed+SUCCESS_LINGER):
            deadline = time.monotonic()+2
            while spec.task_id in self.page.cards and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
            self.assertNotIn(spec.task_id,self.page.cards)
            self.assertEqual(len(self.store.completed_tasks()),1)

    def test_history_clear_keeps_files_and_later_success_is_visible(self):
        spec = self.make('success',TaskState.SUCCEEDED)
        file = Path(self.store.outputs(spec.task_id)[0]['path'])
        self.page.tabs.invoke('history')
        self.page.clear_button.invoke()
        self.root.update()
        self.assertEqual(self.page.cards,{})
        self.assertEqual(file.read_bytes(),b'user-download')
        self.assertEqual(self.page.clear_button.cget('state'),'disabled')
        later = self.make('later',TaskState.SUCCEEDED)
        self.assertIn(later.task_id,self.page.cards)
        self.assertNotIn(spec.task_id,self.page.cards)

    def test_failure_cancel_resume_and_remove_controls_keep_record_bound_to_card(self):
        spec = self.make('failed',TaskState.FAILED)
        card = self.page.cards[spec.task_id]
        card.buttons['cancel'].invoke()
        self.assertEqual(self.store.task(spec.task_id).state,TaskState.CANCELLED)
        card.buttons['retry'].invoke()
        self.assertEqual(self.store.task(spec.task_id).state,TaskState.QUEUED)
        card.buttons['cancel'].invoke()
        card.buttons['remove'].invoke()
        self.assertNotIn(spec.task_id,self.page.cards)
        self.assertEqual(self.store.completed_tasks(),())

    def test_resume_all_skips_removed_and_cancelled_tasks(self):
        removed = self.make('removed',TaskState.INTERRUPTED)
        self.store.remove_task(removed.task_id)
        interrupted = self.make('interrupted',TaskState.INTERRUPTED)
        cancelled = self.make('cancelled')
        self.store.request_cancel(cancelled.task_id)
        self.page.refresh()
        self.page.resume_button.invoke()
        self.service.retry.assert_called_once_with(interrupted.task_id)
        self.assertEqual(self.store.task(removed.task_id).state,TaskState.INTERRUPTED)
        self.assertEqual(self.store.task(cancelled.task_id).state,TaskState.CANCELLED)

    def test_stale_metrics_and_unknown_totals_do_not_show_invented_percentage(self):
        spec = self.make('unknown-total',TaskState.RUNNING)
        record = self.store.task(spec.task_id)
        card = self.page.cards[spec.task_id]
        self.progress[spec.task_id] = dict(attempt_id='old-attempt',percent=91,phase='downloading')
        self.page.refresh()
        self.assertNotIn('91%',card.meta_var.get())
        self.progress[spec.task_id] = dict(attempt_id=record.attempt_id,bytes=1024,total=None,
                                         percent=None,speed=512,measured_at=None,phase='downloading')
        self.page.refresh()
        self.assertIn('已下载 1.0 KB',card.meta_var.get())
        self.assertNotIn('%',card.meta_var.get())
        deadline = time.monotonic()+1.5
        while self.progress[spec.task_id]['speed'] is not None and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNone(self.progress[spec.task_id]['speed'])

    def test_known_failure_reason_is_brief_and_raw_messages_are_hidden(self):
        spec = self.make('failed')
        task = self.store.claim_next()
        self.store.transition(spec.task_id,TaskState.FAILED,attempt_id=task.attempt_id,
            error_code='RuntimeError',message=str(self.root_path/'raw-debug-path'))
        self.page.refresh()
        card = self.page.cards[spec.task_id]
        self.assertNotIn(str(self.root_path),card.note_var.get())
        self.assertNotIn('RuntimeError',card.note_var.get())
