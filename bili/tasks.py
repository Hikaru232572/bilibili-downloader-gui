"""FIFO execution independent of the selected page and its Tk variables."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Queue
import threading
from typing import Awaitable, Callable

from .models import ACTIVE_STATES, OutputSpec, TaskRecord, TaskSpec, TaskState
from .storage import TaskStore


@dataclass
class TaskContext:
    record: TaskRecord
    events: Queue
    cleanup: list[Callable[[], None]] = field(default_factory=list)
    result_message: str | None = None
    result_code: str | None = None
    retained_output: dict | None = None

    def emit(self, operation: str, **payload):
        if operation == 'warning':
            self.result_message = payload.get('message')
            self.result_code = payload.get('error_code')
        self.events.put(dict(operation=operation,task_id=self.record.spec.task_id,
                             attempt_id=self.record.attempt_id,**payload))

    def dispose(self):
        for callback in reversed(self.cleanup):
            try:
                callback()
            except Exception:
                self.emit('cleanup_error')
        self.cleanup.clear()


class TaskService:
    def __init__(self, store: TaskStore, executor: Callable[[TaskContext], Awaitable[TaskState]]):
        self.store = store
        self.executor = executor
        self.events = Queue()
        self._closing = False
        self._closed = False
        self._active_id = None
        self._active = None
        ready = Future()
        self._thread = threading.Thread(target=self._thread_main,args=(ready,),name='bili-tasks')
        self._thread.start()
        ready.result(timeout=10)

    def _thread_main(self, ready):
        asyncio.run(self._serve(ready))

    async def _serve(self, ready):
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        ready.set_result(None)
        self._wake.set()
        while not self._closing:
            await self._wake.wait()
            self._wake.clear()
            while not self._closing:
                record = self.store.claim_next()
                if record is None:
                    self._notifications()
                    break
                if self._closing:
                    self.store.transition(record.spec.task_id,TaskState.INTERRUPTED,
                                          attempt_id=record.attempt_id)
                    break
                context = TaskContext(record,self.events)
                self._active_id = record.spec.task_id
                self._active = asyncio.create_task(self.executor(context))
                context.emit('task_started')
                try:
                    result = await self._active
                    if result not in (TaskState.SUCCEEDED,TaskState.PARTIAL,TaskState.FAILED):
                        raise ValueError('Executor returned a nonterminal result')
                    current = self.store.task(record.spec.task_id).state
                    if current == TaskState.CANCEL_REQUESTED:
                        result = TaskState.CANCELLED
                    elif result == TaskState.SUCCEEDED and current == TaskState.RUNNING:
                        self.store.transition(record.spec.task_id,TaskState.VALIDATING,
                                              attempt_id=record.attempt_id)
                    self.store.transition(record.spec.task_id,result,attempt_id=record.attempt_id,
                                          error_code=context.result_code,message=context.result_message)
                except asyncio.CancelledError:
                    terminal = TaskState.INTERRUPTED if self._closing else TaskState.CANCELLED
                    current = self.store.task(record.spec.task_id).state
                    if current in ACTIVE_STATES:
                        if terminal == TaskState.CANCELLED and current != TaskState.CANCEL_REQUESTED:
                            self.store.transition(record.spec.task_id,TaskState.CANCEL_REQUESTED,
                                                  attempt_id=record.attempt_id)
                        self.store.transition(record.spec.task_id,terminal,attempt_id=record.attempt_id)
                except Exception as exc:
                    current = self.store.task(record.spec.task_id).state
                    state = TaskState.CANCELLED if current == TaskState.CANCEL_REQUESTED else TaskState.FAILED
                    # Backend exception strings can contain URLs and headers.
                    # Only controlled codes/class names enter durable state.
                    code = getattr(exc,'code',None) or type(exc).__name__
                    self.store.transition(record.spec.task_id,state,attempt_id=record.attempt_id,
                                          error_code=code)
                finally:
                    context.dispose()
                    context.emit('task_finished',state=self.store.task(record.spec.task_id).state)
                    self._active = None
                    self._active_id = None
                if not self._closing:
                    self._notifications()

    def _notifications(self):
        for summary in self.store.claim_notifications():
            self.events.put(dict(operation='batch_finished',**summary))

    def _signal(self):
        if self._closed or self._closing:
            raise RuntimeError('Task service is stopping')
        self._loop.call_soon_threadsafe(self._wake.set)

    def submit(self, spec: TaskSpec, outputs: tuple[OutputSpec, ...] = ()):
        if self._closing or self._closed:
            raise RuntimeError('Task service is stopping')
        record = self.store.submit(spec,outputs)
        self._signal()
        self.events.put(dict(operation='task_queued',task_id=spec.task_id))
        return record

    def retry(self, task_id):
        if self._closing or self._closed:
            raise RuntimeError('Task service is stopping')
        record = self.store.retry(task_id)
        self._signal()
        return record

    def cancel(self, task_id):
        state = self.store.request_cancel(task_id)
        if state == TaskState.CANCELLED:
            self._signal()
        elif state == TaskState.CANCEL_REQUESTED:
            def cancel_current():
                if self._active_id == task_id and self._active is not None:
                    self._active.cancel()
            self._loop.call_soon_threadsafe(cancel_current)

    def close(self, timeout=10):
        if self._closed:
            return
        self._closing = True
        self.store.abandon_open_batches()
        def stop():
            if self._active is not None:
                self._active.cancel()
            self._wake.set()
        try:
            self._loop.call_soon_threadsafe(stop)
        except RuntimeError:
            # The worker can observe _closing and finish between the flag
            # update above and this wake-up. Its already closed loop needs
            # no further cancellation, but durable cleanup still must run.
            if not self._loop.is_closed():
                raise
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError('Task executor did not stop within cleanup deadline')
        # Queued work was never claimed during shutdown. Preserve it for the
        # next startup's manual recovery instead of losing it.
        for record in self.store.tasks():
            if record.state == TaskState.QUEUED:
                self.store.transition(record.spec.task_id,TaskState.INTERRUPTED)
        self._closed = True
