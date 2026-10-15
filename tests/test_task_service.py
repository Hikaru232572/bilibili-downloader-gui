import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bili.models import Component,Platform,TaskSpec,TaskState
from bili.storage import TaskStore
from bili.tasks import TaskService


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = TaskStore(self.root/'tasks.sqlite3')
        self.service = None

    def tearDown(self):
        if self.service:
            self.service.close()
        self.store.close()
        self.directory.cleanup()

    def spec(self,name):
        return TaskSpec(Platform.BILIBILI,name,name,'source-ref',str(self.root),('item',),(Component.VIDEO,))

    def wait(self,predicate):
        deadline = time.monotonic()+5
        while not predicate():
            if time.monotonic()>deadline:
                self.fail('Task service timed out')
            time.sleep(.01)

    def test_failed_work_does_not_block_fifo_and_batch_has_one_result(self):
        order = []
        release = threading.Event()
        async def execute(context):
            order.append(context.record.spec.work_id)
            while not release.is_set():
                await asyncio.sleep(.01)
            if context.record.spec.work_id=='A':
                raise RuntimeError('unsafe-cookie-and-signed-url')
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,execute)
        a,b = self.spec('A'),self.spec('B')
        self.service.submit(a)
        self.service.submit(b)
        release.set()
        self.wait(lambda:self.store.task(b.task_id).state==TaskState.SUCCEEDED)
        self.assertEqual(order,['A','B'])
        self.assertEqual(self.store.task(a.task_id).error_code,'RuntimeError')
        self.assertIsNone(self.store.task(a.task_id).message)
        self.wait(lambda:any(e['operation']=='batch_finished' for e in list(self.service.events.queue)))
        results = [e for e in list(self.service.events.queue) if e['operation']=='batch_finished']
        self.assertEqual(len(results),1)

    def test_cancel_reaps_executor_cleanup_and_next_work_runs(self):
        started,cleaned = threading.Event(),threading.Event()
        async def execute(context):
            if context.record.spec.work_id=='A':
                context.cleanup.append(cleaned.set)
                started.set()
                await asyncio.sleep(60)
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,execute)
        a,b = self.spec('A'),self.spec('B')
        self.service.submit(a)
        self.assertTrue(started.wait(3))
        self.service.submit(b)
        self.service.cancel(a.task_id)
        self.wait(lambda:self.store.task(b.task_id).state==TaskState.SUCCEEDED)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.store.task(a.task_id).state,TaskState.CANCELLED)

    def test_exit_does_not_start_remaining_jobs_and_next_start_is_manual(self):
        started = threading.Event()
        async def execute(context):
            started.set()
            await asyncio.sleep(60)
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,execute)
        a,b = self.spec('A'),self.spec('B')
        self.service.submit(a)
        self.assertTrue(started.wait(3))
        self.service.submit(b)
        self.service.close()
        self.assertTrue(all(t.state==TaskState.INTERRUPTED for t in self.store.tasks()))
        self.store.close()
        self.store = TaskStore(self.root/'tasks.sqlite3')
        called = []
        async def next_executor(context):
            called.append(context.record.spec.work_id)
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,next_executor)
        c = self.spec('C')
        self.service.submit(c)
        self.wait(lambda:self.store.task(c.task_id).state==TaskState.SUCCEEDED)
        self.assertEqual(called,['C'])

    def test_exit_finishes_durable_cleanup_when_loop_closes_before_wakeup(self):
        async def execute(context):
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,execute)
        self.wait(lambda:not self.service._wake.is_set())
        pending = self.spec('pending')
        self.store.submit(pending)
        original = self.service._loop.call_soon_threadsafe
        def finish_before_signal(callback):
            original(self.service._wake.set)
            self.service._thread.join(timeout=3)
            self.assertFalse(self.service._thread.is_alive())
            return original(callback)
        with patch.object(self.service._loop,'call_soon_threadsafe',side_effect=finish_before_signal):
            self.service.close()
        self.assertEqual(self.store.task(pending.task_id).state,TaskState.INTERRUPTED)
        self.assertTrue(self.service._closed)
        self.service.close()

    def test_consecutive_successes_write_history_without_ui_consuming_events(self):
        async def execute(context):
            return TaskState.SUCCEEDED
        self.service = TaskService(self.store,execute)
        specs = [self.spec(str(i)) for i in range(5)]
        for spec in specs:
            self.service.submit(spec)
        self.wait(lambda:len(self.store.completed_tasks())==5)
        self.assertEqual({r.spec.task_id for r in self.store.completed_tasks()},
                         {s.task_id for s in specs})
        self.assertTrue(all(r.state==TaskState.SUCCEEDED for r in self.store.tasks()))

    def test_cancel_arriving_at_success_commit_does_not_write_false_history(self):
        async def execute(context):
            return TaskState.SUCCEEDED
        original = self.store.transition
        def cancel_before_commit(task_id,state,**kwargs):
            if state==TaskState.SUCCEEDED:
                self.store.request_cancel(task_id)
            return original(task_id,state,**kwargs)
        self.store.transition = cancel_before_commit
        self.service = TaskService(self.store,execute)
        spec = self.spec('race')
        self.service.submit(spec)
        self.wait(lambda:self.store.task(spec.task_id).state==TaskState.CANCELLED)
        self.assertEqual(self.store.completed_tasks(),())
        self.assertEqual(len(self.store.queue_tasks()),1)

    def test_cancel_during_exception_commit_does_not_stop_the_worker_or_next_task(self):
        release = threading.Event()
        async def execute(context):
            if context.record.spec.work_id=='failing':
                while not release.is_set():
                    await asyncio.sleep(.01)
                raise RuntimeError('backend failure')
            return TaskState.SUCCEEDED
        original = self.store.transition
        def cancel_before_failure_commit(task_id,state,**kwargs):
            if state==TaskState.FAILED:
                self.store.request_cancel(task_id)
            return original(task_id,state,**kwargs)
        self.store.transition = cancel_before_failure_commit
        self.service = TaskService(self.store,execute)
        failing, next_task = self.spec('failing'), self.spec('next')
        self.service.submit(failing)
        self.service.submit(next_task)
        release.set()
        self.wait(lambda:self.store.task(next_task.task_id).state==TaskState.SUCCEEDED)
        self.assertEqual(self.store.task(failing.task_id).state,TaskState.CANCELLED)
        self.assertEqual([r.spec.task_id for r in self.store.completed_tasks()],[next_task.task_id])
