from __future__ import annotations

import json
import os
from queue import Queue
import subprocess
import sys
import threading
from uuid import uuid4

from .native_worker import PROTOCOL_VERSION


class PreviewHost:
    def __init__(self, command=None):
        self.events = Queue()
        command = command or ([sys.executable, '--native-worker'] if getattr(sys, 'frozen', False)
                              else [sys.executable, '-m', 'bili.native_worker'])
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self._write_lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read, daemon=True, name='bili-preview-events')
        self._reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                    if message.get('protocol_version') != PROTOCOL_VERSION:
                        raise ValueError('Unexpected preview protocol')
                    self.events.put(message)
                except ValueError:
                    self.events.put({'operation':'error', 'error_code':'invalid_worker_message'})
        finally:
            if not self._closed:
                self.events.put({'operation':'error', 'error_code':'worker_exited'})

    def send(self, operation, *, task_id=None, attempt_id=None, view_generation=None, **parameters):
        request_id = str(uuid4())
        message = dict(protocol_version=PROTOCOL_VERSION, request_id=request_id,
                       task_id=task_id, attempt_id=attempt_id, view_generation=view_generation,
                       operation=operation, **parameters)
        with self._write_lock:
            if self._closed or self.process.poll() is not None:
                raise RuntimeError('Preview worker is unavailable')
            self.process.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            self.process.stdin.flush()
        return request_id

    def close(self):
        if self._closed:
            return
        if self.process.poll() is None:
            try:
                self.send('close')
                self.process.wait(timeout=1)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW,check=False,timeout=3)
                else:
                    self.process.kill()
                self.process.wait(timeout=2)
        self._closed = True
        if self.process.stdin:
            self.process.stdin.close()
        self._reader.join(timeout=2)
        if self.process.stdout:
            self.process.stdout.close()
