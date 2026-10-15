from types import SimpleNamespace
import unittest

from bili.models import TaskState
from bili.task_summary import summarize_tasks


def record(state, task_id='task', attempt='current', completed_at=None):
    return SimpleNamespace(state=state, spec=SimpleNamespace(task_id=task_id),
                           attempt_id=attempt, completed_at=completed_at)


class TaskSummaryTests(unittest.TestCase):
    def test_empty_and_old_completed_tasks_are_hidden(self):
        self.assertFalse(summarize_tasks([], {}, now=100).visible)
        self.assertFalse(summarize_tasks([record(TaskState.SUCCEEDED, completed_at=89)], {}, now=100).visible)

    def test_active_queue_shows_actual_progress_and_waiting_count(self):
        summary = summarize_tasks([record(TaskState.RUNNING), record(TaskState.QUEUED, 'next')],
            {'task': {'attempt_id': 'current', 'percent': 42, 'phase': 'downloading'}}, now=100)
        self.assertTrue(summary.visible)
        self.assertEqual(summary.count, 2)
        self.assertEqual(summary.fraction, .42)
        self.assertIn('42%', summary.text)
        self.assertIn('1 个排队', summary.text)

    def test_unknown_stale_and_invalid_progress_do_not_invent_percent(self):
        for progress in ({}, {'attempt_id': 'old', 'percent': 99},
                         {'attempt_id': 'current', 'percent': None},
                         {'attempt_id': 'current', 'percent': float('nan')},
                         {'attempt_id': 'current', 'percent': float('inf')}):
            with self.subTest(progress=progress):
                summary = summarize_tasks([record(TaskState.RUNNING)], {'task': progress}, now=100)
                self.assertIsNone(summary.fraction)
                self.assertNotIn('%', summary.text)

    def test_processing_and_validation_hide_old_transfer_percent(self):
        for state, phase in ((TaskState.RUNNING, 'processing'), (TaskState.VALIDATING, 'validating')):
            summary = summarize_tasks([record(state)],
                {'task': {'attempt_id': 'current', 'percent': 100, 'phase': phase}}, now=100)
            self.assertIsNone(summary.fraction)
            self.assertNotIn('100%', summary.text)

    def test_failures_remain_actionable_and_cancelled_tasks_are_quiet(self):
        summary = summarize_tasks([record(TaskState.FAILED), record(TaskState.PARTIAL, 'partial')], {}, now=100)
        self.assertIn('2 项需处理', summary.text)
        self.assertTrue(summary.visible)
        self.assertFalse(summarize_tasks([record(TaskState.CANCELLED)], {}, now=100).visible)

    def test_success_is_visible_briefly(self):
        summary = summarize_tasks([record(TaskState.SUCCEEDED, completed_at=99)], {}, now=100)
        self.assertTrue(summary.visible)
        self.assertIn('已完成', summary.text)
        self.assertEqual(summary.fraction, 1)


if __name__ == '__main__':
    unittest.main()
