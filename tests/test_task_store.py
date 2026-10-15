from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bili.models import Component, OutputSpec, Platform, TaskSpec, TaskState
from bili.storage import HISTORY_RETENTION, SUCCESS_LINGER, TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = TaskStore(self.root / 'tasks.sqlite3')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def spec(self, name='work'):
        return TaskSpec(Platform.BILIBILI, name, name, 'protected-source-ref', str(self.root),
                        ('item-1',), (Component.VIDEO,), format_selector='best')

    def finish(self, task, state=TaskState.SUCCEEDED):
        self.store.transition(task.spec.task_id, TaskState.VALIDATING, attempt_id=task.attempt_id)
        self.store.transition(task.spec.task_id, state, attempt_id=task.attempt_id)

    def test_double_submit_is_idempotent_and_only_one_concurrent_claim_wins(self):
        spec = self.spec()
        with ThreadPoolExecutor(8) as pool:
            rows = list(pool.map(lambda _: self.store.submit(spec), range(8)))
            claimed = list(pool.map(lambda _: self.store.claim_next(), range(8)))
        self.assertEqual({r.spec.task_id for r in rows}, {spec.task_id})
        self.assertEqual(sum(c is not None for c in claimed), 1)

    def test_fifo_and_new_submission_does_not_change_running_task_settings(self):
        a = self.spec('A')
        b = self.spec('B')
        self.store.submit(a)
        running = self.store.claim_next()
        self.store.submit(b)
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(self.store.task(a.task_id).spec, a)
        self.finish(running)
        self.assertEqual(self.store.claim_next().spec.task_id, b.task_id)

    def test_restart_backlog_requires_manual_resume_even_when_new_job_is_submitted(self):
        a, b = self.spec('A'), self.spec('B')
        self.store.submit(a)
        self.store.submit(b)
        self.store.claim_next()
        self.store.close()
        self.store = TaskStore(self.root / 'tasks.sqlite3')
        self.assertTrue(all(t.state == TaskState.INTERRUPTED for t in self.store.tasks()))
        self.assertIsNone(self.store.claim_next())
        c = self.spec('C')
        self.store.submit(c)
        self.assertEqual(self.store.claim_next().spec.task_id, c.task_id)
        self.store.retry(a.task_id)
        self.assertEqual(self.store.task(b.task_id).state, TaskState.INTERRUPTED)

    def test_retry_keeps_success_and_rejects_previous_attempt_callback(self):
        spec = self.spec()
        outputs = tuple(OutputSpec(spec.task_id, 'item-1', c, 1)
                        for c in (Component.STILL, Component.MOTION))
        self.store.submit(spec, outputs)
        running = self.store.claim_next()
        self.store.update_output(outputs[0].output_id, state='succeeded', path=str(self.root/'still.heic'),
                                 evidence={'sha256':'example'}, attempt_id=running.attempt_id)
        self.store.update_output(outputs[1].output_id, state='failed', attempt_id=running.attempt_id)
        self.finish(running, TaskState.PARTIAL)
        self.store.retry(spec.task_id)
        retry = self.store.claim_next()
        self.assertFalse(self.store.transition(spec.task_id, TaskState.FAILED, attempt_id=running.attempt_id))
        self.assertFalse(self.store.update_output(outputs[0].output_id, state='failed', attempt_id=running.attempt_id))
        result = {o['component']: o for o in self.store.outputs(spec.task_id)}
        self.assertEqual(result['still']['state'], 'succeeded')
        self.assertEqual(result['motion']['state'], 'pending')
        self.assertNotEqual(running.attempt_id, retry.attempt_id)

    def test_retry_joins_open_batch_and_finished_summary_is_immutable(self):
        a, b = self.spec('A'), self.spec('B')
        self.store.submit(a)
        self.store.submit(b)
        first = self.store.claim_next()
        self.finish(first, TaskState.FAILED)
        retry = self.store.retry(a.task_id)
        self.assertEqual(retry.batch_id, first.batch_id)
        self.assertEqual(self.store.claim_notifications(), ())
        second = self.store.claim_next()
        self.assertEqual(second.spec.task_id, b.task_id)
        self.finish(second)
        self.finish(self.store.claim_next(), TaskState.FAILED)
        new_retry = self.store.retry(a.task_id)
        self.assertNotEqual(new_retry.batch_id, first.batch_id)
        summary, = self.store.claim_notifications()
        self.assertEqual(summary['counts'], {'failed':1, 'succeeded':1})
        self.assertEqual(self.store.claim_notifications(), ())
        self.store.close()
        self.store = TaskStore(self.root / 'tasks.sqlite3')
        self.assertEqual(self.store.claim_notifications(), ())

    def test_cancelled_queue_finishes_batch_and_invalid_success_is_rejected(self):
        spec = self.spec()
        self.store.submit(spec)
        with self.assertRaises(ValueError):
            self.store.transition(spec.task_id, TaskState.SUCCEEDED)
        self.store.transition(spec.task_id, TaskState.CANCELLED)
        summary, = self.store.claim_notifications()
        self.assertEqual(summary['counts'], {'cancelled':1})

    def test_bad_outputs_roll_back_submission_and_batch(self):
        spec = self.spec()
        with self.assertRaises(ValueError):
            self.store.submit(spec, (OutputSpec(spec.task_id, 'different-item', Component.VIDEO, 1),))
        self.assertEqual(self.store.tasks(), ())
        self.assertIsNone(self.store.claim_next())

    def test_database_does_not_persist_candidate_credentials_or_signed_url(self):
        self.store.submit(self.spec())
        self.store.close()
        data = (self.root/'tasks.sqlite3').read_bytes()
        self.assertNotIn(b'https://', data)
        self.assertNotIn(b'Cookie', data)

    def test_success_is_immediately_in_history_and_lingers_for_exactly_ten_seconds(self):
        with patch('bili.storage.time.time', return_value=1000000):
            spec = self.spec()
            self.store.submit(spec)
            self.finish(self.store.claim_next())
            self.assertEqual([r.spec.task_id for r in self.store.completed_tasks()], [spec.task_id])
            self.assertEqual([r.spec.task_id for r in self.store.queue_tasks()], [spec.task_id])
        with patch('bili.storage.time.time', return_value=1000000+SUCCESS_LINGER-.01):
            self.assertEqual(len(self.store.queue_tasks()), 1)
        with patch('bili.storage.time.time', return_value=1000000+SUCCESS_LINGER):
            self.assertEqual(self.store.queue_tasks(), ())
            self.assertEqual(len(self.store.completed_tasks()), 1)

    def test_success_and_history_survive_restart_without_returning_to_queue(self):
        spec = self.spec()
        self.store.submit(spec)
        self.finish(self.store.claim_next())
        completed = self.store.completed_tasks()[0].completed_at
        self.store.close()
        self.store = TaskStore(self.root/'tasks.sqlite3')
        self.assertEqual(self.store.queue_tasks(), ())
        self.assertEqual(self.store.completed_tasks()[0].completed_at, completed)
        self.assertIsNone(self.store.claim_next())
        self.store.submit(spec)
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(len(self.store.completed_tasks()), 1)

    def test_history_expires_from_completion_time_at_seven_days(self):
        file = self.root/'preserved.mp4'
        file.write_bytes(b'user-file-after-history-expiry')
        with patch('bili.storage.time.time', return_value=1000000):
            spec = self.spec()
            output = OutputSpec(spec.task_id,'item-1',Component.VIDEO,1)
            self.store.submit(spec,(output,))
            record = self.store.claim_next()
            self.store.update_output(output.output_id,state='succeeded',path=str(file),
                evidence={'size':file.stat().st_size},attempt_id=record.attempt_id)
            self.finish(record)
        with patch('bili.storage.time.time', return_value=1000000+HISTORY_RETENTION-.01):
            self.assertEqual(len(self.store.completed_tasks()), 1)
        with patch('bili.storage.time.time', return_value=1000000+HISTORY_RETENTION):
            self.assertEqual(self.store.prune_history(), 1)
            self.assertEqual(self.store.completed_tasks(), ())
            self.assertEqual(self.store.queue_tasks(), ())
            self.assertEqual(file.read_bytes(),b'user-file-after-history-expiry')

    def test_clear_and_expiry_preserve_files_and_do_not_recreate_history(self):
        spec = self.spec()
        file = self.root/'download.mp4'
        file.write_bytes(b'actual-user-file')
        output = OutputSpec(spec.task_id,'item-1',Component.VIDEO,1)
        self.store.submit(spec,(output,))
        task = self.store.claim_next()
        self.store.update_output(output.output_id,state='succeeded',path=str(file),
            evidence={'size':file.stat().st_size},attempt_id=task.attempt_id)
        self.finish(task)
        self.assertEqual(self.store.clear_history(), 1)
        self.assertFalse(self.store.transition(spec.task_id,TaskState.SUCCEEDED,attempt_id=task.attempt_id))
        self.store.close()
        self.store = TaskStore(self.root/'tasks.sqlite3')
        self.store.submit(spec,(output,))
        self.assertEqual(self.store.completed_tasks(), ())
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(file.read_bytes(), b'actual-user-file')
        self.assertEqual(self.store.outputs(spec.task_id)[0]['path'], str(file))

    def test_partial_and_failure_stay_in_queue_retry_records_only_final_success(self):
        spec = self.spec()
        self.store.submit(spec)
        first = self.store.claim_next()
        self.finish(first,TaskState.PARTIAL)
        self.assertEqual(self.store.completed_tasks(), ())
        self.assertEqual(self.store.queue_tasks()[0].state, TaskState.PARTIAL)
        self.store.retry(spec.task_id)
        second = self.store.claim_next()
        self.finish(second,TaskState.FAILED)
        self.assertEqual(self.store.queue_tasks()[0].state, TaskState.FAILED)
        self.store.retry(spec.task_id)
        third = self.store.claim_next()
        self.assertFalse(self.store.transition(spec.task_id,TaskState.SUCCEEDED,attempt_id=first.attempt_id))
        self.finish(third)
        self.assertEqual(len(self.store.completed_tasks()), 1)

    def test_success_validation_failure_cannot_create_history(self):
        spec = self.spec()
        self.store.submit(spec,(OutputSpec(spec.task_id,'item-1',Component.VIDEO,1),))
        task = self.store.claim_next()
        self.store.transition(spec.task_id,TaskState.VALIDATING,attempt_id=task.attempt_id)
        with self.assertRaises(ValueError):
            self.store.transition(spec.task_id,TaskState.SUCCEEDED,attempt_id=task.attempt_id)
        self.assertEqual(self.store.completed_tasks(), ())
        self.assertEqual(self.store.task(spec.task_id).state, TaskState.VALIDATING)

    def test_cancel_failed_work_then_resume_or_remove_without_deleting_files(self):
        file = self.root/'saved-image.webp'
        file.write_bytes(b'keep-this-file')
        spec = self.spec()
        self.store.submit(spec)
        with self.assertRaises(ValueError):
            self.store.remove_task(spec.task_id)
        self.finish(self.store.claim_next(),TaskState.FAILED)
        self.assertEqual(self.store.request_cancel(spec.task_id),TaskState.CANCELLED)
        self.assertEqual(len(self.store.queue_tasks()),1)
        self.store.retry(spec.task_id)
        self.store.request_cancel(spec.task_id)
        self.store.remove_task(spec.task_id)
        self.assertEqual(self.store.queue_tasks(),())
        self.assertEqual(self.store.completed_tasks(),())
        with self.assertRaises(ValueError):
            self.store.retry(spec.task_id)
        self.store.close()
        self.store = TaskStore(self.root/'tasks.sqlite3')
        self.assertEqual(self.store.queue_tasks(),())
        self.assertEqual(file.read_bytes(),b'keep-this-file')

    def test_clearing_history_does_not_change_pending_work_or_later_completions(self):
        a,b = self.spec('A'),self.spec('B')
        self.store.submit(a)
        self.store.submit(b)
        self.finish(self.store.claim_next())
        self.store.clear_history()
        self.assertEqual(self.store.task(b.task_id).state,TaskState.QUEUED)
        self.finish(self.store.claim_next())
        self.assertEqual([r.spec.task_id for r in self.store.completed_tasks()],[b.task_id])


if __name__ == '__main__':
    unittest.main()
