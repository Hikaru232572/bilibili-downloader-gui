import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from bili.models import Component, Platform, TaskSpec, TaskState
from bili.storage import HISTORY_RETENTION, TaskStore


class TaskMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root/'tasks.sqlite3'
        self.now = time.time()
        self.store = None
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript((Path(__file__).parent/'fixtures/tasks_v1.sql').read_text())
            c.execute("INSERT INTO batches(id,state,created) VALUES('batch','abandoned',?)",(self.now-100,))

    def tearDown(self):
        if self.store:
            self.store.close()
        self.directory.cleanup()

    def legacy(self, name, state, updated, ended=None):
        spec = TaskSpec(Platform.BILIBILI,name,name,'protected-ref',str(self.root),('item',),(Component.VIDEO,))
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('INSERT INTO tasks(id,submission_key,spec,state,batch_id,attempt_id,created,updated) '
                'VALUES(?,?,?,?,?,?,?,?)',(spec.task_id,spec.submission_key,json.dumps(spec.record()),
                state,'batch','attempt-'+name,updated-10,updated))
            if ended is not None:
                c.execute('INSERT INTO attempts(id,task_id,started,ended,state) VALUES(?,?,?,?,?)',
                    ('attempt-'+name,spec.task_id,ended-1,ended,state))
        return spec

    def test_migration_uses_original_completion_and_never_requeues_success(self):
        recent = self.legacy('recent','succeeded',self.now-20,self.now-30)
        expired = self.legacy('expired','succeeded',self.now-10,self.now-HISTORY_RETENTION-1)
        fallback = self.legacy('fallback','succeeded',self.now-40)
        unfinished = self.legacy('unfinished','running',self.now-10)
        cancelled = self.legacy('cancelled','cancelled',self.now-10)
        file = self.root/'existing.mp4'
        file.write_bytes(b'preserve-user-download')
        self.store = TaskStore(self.path)
        history = self.store.completed_tasks()
        self.assertEqual([r.spec.task_id for r in history],[recent.task_id,fallback.task_id])
        self.assertEqual(history[0].completed_at,self.now-30)
        self.assertEqual(history[1].completed_at,self.now-40)
        queue = {r.spec.task_id:r.state for r in self.store.queue_tasks()}
        self.assertEqual(queue,{unfinished.task_id:TaskState.INTERRUPTED,cancelled.task_id:TaskState.CANCELLED})
        self.assertEqual(self.store.task(expired.task_id).state,TaskState.SUCCEEDED)
        self.assertIsNone(self.store.claim_next())
        with closing(sqlite3.connect(str(self.path)+'.v1.bak')) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],5)
        self.store.clear_history()
        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.completed_tasks(),())
        self.assertEqual(file.read_bytes(),b'preserve-user-download')

    def test_failed_migration_rolls_back_schema_and_can_be_retried(self):
        self.legacy('recent','succeeded',self.now-20)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('CREATE TABLE completion_history (invalid TEXT)')
        with self.assertRaises(sqlite3.OperationalError):
            TaskStore(self.path)
        with closing(sqlite3.connect(self.path)) as c, c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('queue_hidden',[r[1] for r in c.execute('PRAGMA table_info(tasks)')])
            c.execute('DROP TABLE completion_history')
        self.store = TaskStore(self.path)
        self.assertEqual(len(self.store.completed_tasks()),1)
