import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest

from app import BiliDownloaderApp
from bili.models import TaskState
from tests.test_ui import metadata


class TaskUiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.app=BiliDownloaderApp(data_dir=Path(self.directory.name)/'state')
        self.app.withdraw()
        self.app.auth_mode_var.set('none')
        self.app.output_dir_var.set(self.directory.name)
        self.release=threading.Event()
        self.started=[]
        async def execute(context):
            self.started.append(context.record.spec)
            while not self.release.is_set():await asyncio.sleep(.01)
            return TaskState.FAILED
        self.app.task_service.executor=execute

    def tearDown(self):
        self.app.destroy()
        self.directory.cleanup()

    def pump(self,condition):
        end=time.monotonic()+5
        while not condition():
            self.app.update()
            if time.monotonic()>end:self.fail('UI task timed out')
            time.sleep(.01)

    def parse(self,name):
        url='https://www.bilibili.com/video/'+name
        self.app.url_var.set(url)
        self.app.parsed_url=url
        info=metadata(with_subtitles=False)
        info['id']=name
        info['title']=name+' title'
        self.app._handle_fetch_done(info)

    def test_download_a_then_parse_and_queue_b_preserves_a_and_fifo(self):
        self.parse('A')
        self.app.download()
        self.pump(lambda:len(self.started)==1)
        a=self.started[0]
        self.assertEqual(self.app.url_entry.cget('state'),'normal')
        self.assertEqual(self.app.fetch_button.cget('state'),'normal')
        self.assertEqual(self.app.settings_button.cget('state'),'normal')
        self.parse('B')
        current=self.app.task_store.task(a.task_id)
        self.app._handle_task_event({'operation':'progress','task_id':a.task_id,
                                    'attempt_id':current.attempt_id,'percent':42})
        self.assertEqual(self.app.task_page.tree.item(a.task_id,'values')[1],'下载 42%')
        self.app.output_dir_var.set(str(Path(self.directory.name)/'second'))
        self.app.audio_mode_card.invoke()
        self.app.download()
        tasks=self.app.task_store.tasks()
        self.assertEqual([t.spec.work_id for t in tasks],['A','B'])
        self.assertEqual(tasks[0].spec,a)
        self.assertNotEqual(tasks[0].spec.output_dir,tasks[1].spec.output_dir)
        self.app.show_task_page()
        self.assertEqual(len(self.app.task_page.tree.get_children()),2)
        self.release.set()
        self.pump(lambda:len(self.started)==2)
        self.assertEqual([s.work_id for s in self.started],['A','B'])
        self.assertEqual(self.app.video_title_var.get(),'B title')

    def test_stale_parse_response_does_not_replace_new_page(self):
        self.parse('A')
        old_generation=self.app._view_generation
        self.parse('B')
        info=metadata(with_subtitles=False)
        info['title']='stale A'
        self.app.queue.put(('fetch_result',(old_generation,info)))
        self.app._drain_queue()
        self.assertEqual(self.app.video_title_var.get(),'B title')

    def test_tasks_settings_home_navigation_is_one_window(self):
        self.app.show_task_page()
        self.assertEqual(self.app.task_page.winfo_manager(),'pack')
        self.app.show_settings_page()
        self.assertEqual(self.app.task_page.winfo_manager(),'')
        self.app.show_main_page()
        self.assertEqual(self.app.settings_page.winfo_manager(),'')
        self.assertEqual(self.app.main_page.winfo_manager(),'pack')

    def test_double_click_does_not_submit_duplicate_active_task(self):
        self.parse('A')
        self.app.download()
        self.app.download()
        self.pump(lambda:len(self.started)==1)
        self.assertEqual(len(self.app.task_store.tasks()),1)
