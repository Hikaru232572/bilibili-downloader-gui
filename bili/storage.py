"""Durable task state. Every SQLite operation runs on one owning thread."""
from __future__ import annotations

from concurrent.futures import Future
from contextlib import closing
import json
from pathlib import Path
from queue import Queue
import sqlite3
import threading
import time
from uuid import uuid4

from .models import ACTIVE_STATES, TRANSITIONS, OutputSpec, TaskRecord, TaskSpec, TaskState


SCHEMA_VERSION = 2
HISTORY_RETENTION = 7 * 24 * 60 * 60
SUCCESS_LINGER = 10
SCHEMA = '''
CREATE TABLE IF NOT EXISTS batches (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, notification_claimed INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, ended REAL, summary TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_batch ON batches(state) WHERE state='open';
CREATE TABLE IF NOT EXISTS tasks (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 id TEXT NOT NULL UNIQUE, submission_key TEXT NOT NULL UNIQUE,
 spec TEXT NOT NULL, state TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id),
 attempt_id TEXT, error_code TEXT, message TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 queue_hidden INTEGER NOT NULL DEFAULT 0, completed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_task ON tasks((1))
 WHERE state IN ('running','validating','cancel_requested');
CREATE TABLE IF NOT EXISTS outputs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), item_id TEXT NOT NULL,
 component TEXT NOT NULL, original_index INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', path TEXT, temporary_path TEXT,
 evidence TEXT, error_code TEXT, message TEXT,
 UNIQUE(task_id,item_id,component)
);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), started REAL NOT NULL,
 ended REAL, state TEXT NOT NULL, backend_revision TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS completion_history (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), completed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS history_by_completion ON completion_history(completed_at);
'''


class TaskStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._requests = Queue()
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        ready = Future()
        self._thread = threading.Thread(target=self._owner, args=(ready,), name='bili-db')
        self._thread.start()
        try:
            ready.result(timeout=15)
        except Exception:
            self._thread.join(timeout=10)
            raise

    def _owner(self, ready):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise ValueError('Unsupported task database version')
            if version == 0:
                connection.executescript(SCHEMA)
                connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                connection.commit()
            elif version == 1:
                # A consistent SQLite backup includes WAL content. Migration and
                # its version marker commit together; a retry cannot duplicate history.
                with closing(sqlite3.connect(str(self.path) + '.v1.bak')) as backup:
                    connection.backup(backup)
                with connection:
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute('ALTER TABLE tasks ADD COLUMN queue_hidden INTEGER NOT NULL DEFAULT 0')
                    connection.execute('ALTER TABLE tasks ADD COLUMN completed_at REAL')
                    connection.execute('CREATE TABLE completion_history (task_id TEXT PRIMARY KEY '
                                       'REFERENCES tasks(id), completed_at REAL NOT NULL)')
                    connection.execute('CREATE INDEX history_by_completion ON completion_history(completed_at)')
                    connection.execute("UPDATE tasks SET completed_at=COALESCE("
                        "(SELECT MAX(ended) FROM attempts WHERE task_id=tasks.id AND state='succeeded'),"
                        "updated) WHERE state='succeeded'")
                    connection.execute("INSERT INTO completion_history SELECT id,completed_at FROM tasks "
                                       "WHERE state='succeeded' AND completed_at> ?",
                                       (time.time() - HISTORY_RETENTION,))
                    connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            # All old nonterminal work needs explicit user resumption. New jobs
            # submitted after startup cannot silently release this backlog.
            with connection:
                connection.execute("UPDATE tasks SET queue_hidden=1 WHERE state='succeeded'")
                self._prune_history(connection, time.time())
                connection.execute("UPDATE tasks SET state='interrupted',updated=? "
                                   "WHERE state IN ('queued','running','validating','cancel_requested')",
                                   (time.time(),))
                connection.execute("UPDATE attempts SET state='interrupted',ended=? WHERE ended IS NULL",
                                   (time.time(),))
                connection.execute("UPDATE batches SET state='abandoned',ended=? WHERE state='open'",
                                   (time.time(),))
            ready.set_result(None)
            while True:
                request = self._requests.get()
                if request is None:
                    break
                operation, future = request
                try:
                    with connection:
                        result = operation(connection)
                    future.set_result(result)
                except BaseException as exc:
                    future.set_exception(exc)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if connection is not None:
                connection.close()

    def _call(self, operation):
        future = Future()
        with self._lifecycle_lock:
            if self._closed or not self._thread.is_alive():
                raise RuntimeError('Task store is closed')
            self._requests.put((operation, future))
        return future.result(timeout=15)

    def close(self):
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._requests.put(None)
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError('Database writer did not stop')

    @staticmethod
    def _batch(connection):
        row = connection.execute("SELECT id FROM batches WHERE state='open'").fetchone()
        if row:
            return row['id']
        batch = str(uuid4())
        connection.execute('INSERT INTO batches(id,state,created) VALUES(?,?,?)',
                           (batch, 'open', time.time()))
        return batch

    @staticmethod
    def _record(row):
        return TaskRecord(TaskSpec.from_record(json.loads(row['spec'])),
                          TaskState(row['state']), row['sequence'], row['batch_id'],
                          row['attempt_id'], row['error_code'], row['message'], row['completed_at'])

    def submit(self, spec: TaskSpec, outputs: tuple[OutputSpec, ...] = ()) -> TaskRecord:
        def operation(c):
            row = c.execute('SELECT * FROM tasks WHERE submission_key=?',
                            (spec.submission_key,)).fetchone()
            if row:
                if json.loads(row['spec']) != json.loads(json.dumps(spec.record())):
                    raise ValueError('Submission key was reused for a different task')
                return self._record(row)
            batch = self._batch(c)
            now = time.time()
            c.execute('INSERT INTO tasks(id,submission_key,spec,state,batch_id,created,updated) '
                      'VALUES(?,?,?,?,?,?,?)', (spec.task_id, spec.submission_key,
                      json.dumps(spec.record(), ensure_ascii=False), TaskState.QUEUED, batch, now, now))
            for output in outputs:
                if output.task_id != spec.task_id or output.item_id not in spec.selected_item_ids:
                    raise ValueError('Output does not belong to submitted selection')
                c.execute('INSERT INTO outputs(id,task_id,item_id,component,original_index) VALUES(?,?,?,?,?)',
                          (output.output_id, output.task_id, output.item_id, output.component,
                           output.original_index))
            return self._record(c.execute('SELECT * FROM tasks WHERE id=?', (spec.task_id,)).fetchone())
        return self._call(operation)

    def tasks(self) -> tuple[TaskRecord, ...]:
        return self._call(lambda c: tuple(self._record(r) for r in c.execute('SELECT * FROM tasks ORDER BY sequence')))

    @staticmethod
    def _prune_history(c, now):
        return c.execute('DELETE FROM completion_history WHERE completed_at<=?',
                         (now - HISTORY_RETENTION,)).rowcount

    def prune_history(self):
        return self._call(lambda c: self._prune_history(c, time.time()))

    def page_snapshot(self, *, history=False):
        """Read cards and their outputs consistently, without a query per card.

        Execution identities stay durable after clearing presentation history.
        This prevents stale events/submission retries from recreating records.
        None of these presentation operations accesses the output filesystem.
        """
        def operation(c):
            now = time.time()
            self._prune_history(c, now)
            if history:
                source = 'tasks t JOIN completion_history h ON h.task_id=t.id'
                where, args, order = '1=1', (), 'h.completed_at DESC,t.sequence DESC'
            else:
                source = 'tasks t'
                where = "t.queue_hidden=0 AND (t.state<>'succeeded' OR t.completed_at>?)"
                args, order = (now - SUCCESS_LINGER,), 't.sequence'
            records = tuple(self._record(r) for r in c.execute(
                f'SELECT t.* FROM {source} WHERE {where} ORDER BY {order}', args))
            outputs = {r.spec.task_id: [] for r in records}
            for row in c.execute(f'SELECT o.* FROM {source} JOIN outputs o ON o.task_id=t.id '
                                 f'WHERE {where} ORDER BY o.original_index,o.component', args):
                outputs[row['task_id']].append(dict(row))
            return tuple((r, tuple(outputs[r.spec.task_id])) for r in records)
        return self._call(operation)

    def queue_tasks(self):
        return tuple(record for record, _ in self.page_snapshot())

    def completed_tasks(self):
        return tuple(record for record, _ in self.page_snapshot(history=True))

    def clear_history(self):
        return self._call(lambda c: c.execute('DELETE FROM completion_history').rowcount)

    def remove_task(self, task_id):
        def operation(c):
            row = c.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            if TaskState(row['state']) in ACTIVE_STATES or row['state'] == TaskState.QUEUED:
                raise ValueError('Stop a task before removing it')
            return c.execute('UPDATE tasks SET queue_hidden=1 WHERE id=?', (task_id,)).rowcount
        return self._call(operation)

    def task(self, task_id: str) -> TaskRecord:
        def operation(c):
            row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            return self._record(row)
        return self._call(operation)

    def claim_next(self) -> TaskRecord | None:
        def operation(c):
            if c.execute("SELECT 1 FROM tasks WHERE state IN ('running','validating','cancel_requested')").fetchone():
                return None
            row = c.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY sequence LIMIT 1").fetchone()
            if not row:
                return None
            attempt = str(uuid4())
            now = time.time()
            c.execute("UPDATE tasks SET state='running',attempt_id=?,updated=? WHERE id=?",
                      (attempt, now, row['id']))
            c.execute('INSERT INTO attempts(id,task_id,started,state) VALUES(?,?,?,?)',
                      (attempt, row['id'], now, 'running'))
            return self._record(c.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
        return self._call(operation)

    def transition(self, task_id: str, state: TaskState, *, attempt_id: str | None = None,
                   error_code: str | None = None, message: str | None = None,
                   resolve_cancel: bool = False) -> bool:
        def operation(c):
            row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            if attempt_id is not None and row['attempt_id'] != attempt_id:
                return False  # A late callback from a previous attempt.
            previous = TaskState(row['state'])
            # Decide cancellation and terminal state on the owning DB thread.
            # A cancel can arrive after the service last inspected this row.
            target = (TaskState.CANCELLED if resolve_cancel and previous==TaskState.CANCEL_REQUESTED
                      and state in {TaskState.SUCCEEDED,TaskState.PARTIAL,TaskState.FAILED} else state)
            if target == previous:
                return False
            if target not in TRANSITIONS[previous]:
                raise ValueError(f'Invalid transition {previous} -> {target}')
            if target == TaskState.QUEUED:
                raise ValueError('Use retry() to reopen a terminal task')
            if target == TaskState.SUCCEEDED and c.execute(
                "SELECT 1 FROM outputs WHERE task_id=? AND state<>'succeeded' LIMIT 1", (task_id,)).fetchone():
                raise ValueError('Task still has unvalidated or missing outputs')
            now = time.time()
            c.execute('UPDATE tasks SET state=?,error_code=?,message=?,updated=? WHERE id=?',
                      (target, error_code, message, now, task_id))
            if target == TaskState.SUCCEEDED:
                c.execute('UPDATE tasks SET completed_at=? WHERE id=?', (now, task_id))
                c.execute('INSERT INTO completion_history(task_id,completed_at) VALUES(?,?)', (task_id, now))
            if target not in ACTIVE_STATES and row['attempt_id']:
                c.execute('UPDATE attempts SET state=?,ended=? WHERE id=?',
                          (target, now, row['attempt_id']))
            self._finish_batch(c, row['batch_id'])
            return True
        return self._call(operation)

    def request_cancel(self, task_id: str) -> TaskState:
        def operation(c):
            row = c.execute('SELECT state,batch_id,attempt_id,queue_hidden FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            previous = TaskState(row['state'])
            if row['queue_hidden']:
                return previous
            state = (TaskState.CANCELLED if previous in {TaskState.QUEUED,TaskState.FAILED,
                     TaskState.PARTIAL,TaskState.INTERRUPTED} else
                     TaskState.CANCEL_REQUESTED if previous in ACTIVE_STATES else previous)
            if previous != state:
                c.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(state,time.time(),task_id))
                self._finish_batch(c,row['batch_id'])
            return state
        return self._call(operation)

    def abandon_open_batches(self):
        return self._call(lambda c: c.execute("UPDATE batches SET state='abandoned',ended=? WHERE state='open'",
                                              (time.time(),)).rowcount)

    @staticmethod
    def _finish_batch(c, batch_id):
        pending = c.execute("SELECT 1 FROM tasks WHERE batch_id=? AND state IN "
                            "('queued','running','validating','cancel_requested') LIMIT 1", (batch_id,)).fetchone()
        if not pending:
            counts = {r['state']: r['n'] for r in c.execute(
                'SELECT state,COUNT(*) AS n FROM tasks WHERE batch_id=? GROUP BY state', (batch_id,))}
            c.execute("UPDATE batches SET state='ended',ended=?,summary=? WHERE id=? AND state='open'",
                      (time.time(), json.dumps(counts), batch_id))

    def retry(self, task_id: str) -> TaskRecord:
        def operation(c):
            row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row['queue_hidden'] or TaskState.QUEUED not in TRANSITIONS[TaskState(row['state'])]:
                raise ValueError('Task cannot be retried')
            # Retry joins a currently open batch. After a finished batch, create
            # a new one. Existing successes retain their output identity/path.
            batch = self._batch(c)
            sequence = c.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM tasks').fetchone()[0]
            c.execute("UPDATE tasks SET sequence=?,state='queued',batch_id=?,attempt_id=NULL,"
                      "error_code=NULL,message=NULL,updated=? WHERE id=?",
                      (sequence, batch, time.time(), task_id))
            c.execute("UPDATE outputs SET state='pending',error_code=NULL,message=NULL "
                      "WHERE task_id=? AND state<>'succeeded'", (task_id,))
            return self._record(c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
        return self._call(operation)

    def outputs(self, task_id):
        return self._call(lambda c: tuple(dict(r) for r in c.execute(
            'SELECT * FROM outputs WHERE task_id=? ORDER BY original_index,component', (task_id,))))

    def update_output(self, output_id, *, state, path=None, temporary_path=None,
                      evidence=None, error_code=None, message=None, attempt_id=None):
        if state not in {'pending','running','validating','succeeded','failed','missing','cancelled'}:
            raise ValueError('Unknown output state')
        if state == 'succeeded' and (not path or not evidence):
            raise ValueError('Successful output requires path and validation evidence')
        def operation(c):
            row = c.execute('SELECT t.attempt_id,t.state FROM outputs o JOIN tasks t ON o.task_id=t.id WHERE o.id=?',
                            (output_id,)).fetchone()
            if not row:
                raise KeyError(output_id)
            if attempt_id is not None and row['attempt_id'] != attempt_id:
                return False
            if TaskState(row['state']) not in ACTIVE_STATES:
                return False
            c.execute('UPDATE outputs SET state=?,path=COALESCE(?,path),temporary_path=COALESCE(?,temporary_path),'
                      'evidence=COALESCE(?,evidence),error_code=?,message=? WHERE id=?',
                      (state, path, temporary_path, json.dumps(evidence) if evidence is not None else None,
                       error_code, message, output_id))
            return True
        return self._call(operation)

    def claim_notifications(self):
        def operation(c):
            batches = c.execute("SELECT id,summary FROM batches WHERE state='ended' AND notification_claimed=0").fetchall()
            summaries = []
            for row in batches:
                counts = json.loads(row['summary'])
                # Persist before calling the external OS API: at most one
                # attempt, even after a crash. This does not promise delivery.
                c.execute('UPDATE batches SET notification_claimed=1 WHERE id=?', (row['id'],))
                summaries.append({'batch_id': row['id'], 'counts': counts})
            return tuple(summaries)
        return self._call(operation)

    def get_preference(self, key, default=None):
        def operation(c):
            row = c.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default
        return self._call(operation)

    def set_preference(self, key, value):
        return self._call(lambda c: c.execute('INSERT INTO preferences VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, json.dumps(value, ensure_ascii=False))).rowcount)
