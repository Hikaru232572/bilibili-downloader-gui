"""Bounded local preview cache and owned asynchronous image/player preparation."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import hashlib
from pathlib import Path
from queue import Empty, Queue
import re
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx

from .models import Component
from .preview import PreviewHost
from .tasks import TaskContext
from .transfer import HTTPTransfer
from .validation import async_validate_media


class MediaPreviewService:
    def __init__(self, directory, auth, parser, ffprobe=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.auth, self.parser, self.ffprobe = auth, parser, ffprobe
        self.events = Queue()
        self.jobs = {}
        self.leases = {}
        self.closed = False
        self._recover()
        ready = Future()
        self.thread = threading.Thread(target=self._thread, args=(ready,), name='bili-media-preview')
        self.thread.start()
        ready.result(timeout=5)

    def _thread(self, ready):
        asyncio.run(self._serve(ready))

    async def _serve(self, ready):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        self.limit = asyncio.Semaphore(2)
        ready.set_result(None)
        await self.stop.wait()
        jobs = [job for _, job in self.jobs.values()]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def request(self, platform, candidate, generation, *, max_size=180):
        if self.closed:
            raise RuntimeError('Preview service is stopping')
        identity = str(uuid4())
        epoch = self.auth.epoch(platform)
        def start():
            if self.closed or self.auth.epoch(platform) != epoch:
                return
            job = asyncio.create_task(self._load(identity, platform, candidate, generation, epoch, max_size))
            self.jobs[identity] = (platform, job)
            job.add_done_callback(lambda _job:self.jobs.pop(identity, None))
        self.loop.call_soon_threadsafe(start)
        return identity

    def cancel(self, identities=(), *, platform=None):
        wanted = set(identities)
        def cancel_jobs():
            for identity, (current, _path) in tuple(self.leases.items()):
                if identity in wanted or platform is not None and current == platform:
                    self.leases.pop(identity, None)
            for identity, (current, job) in tuple(self.jobs.items()):
                if identity in wanted or platform is not None and current == platform:
                    job.cancel()
        if not self.closed:
            self.loop.call_soon_threadsafe(cancel_jobs)

    def revoke(self, platform):
        """Call after the UI has closed this platform's players."""
        async def purge():
            jobs = [job for current, job in tuple(self.jobs.values()) if current == platform]
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            for identity, (current, _path) in tuple(self.leases.items()):
                if current == platform:
                    self.leases.pop(identity, None)
            for path in self.directory.iterdir():
                if self._owned_cache(path) and path.name.startswith(platform.value+'-'):
                    self._remove(path)
        if not self.closed:
            return asyncio.run_coroutine_threadsafe(purge(), self.loop)

    @staticmethod
    def _owned_cache(path):
        return not path.is_symlink() and re.fullmatch(r'(douyin|xiaohongshu)-[a-f0-9]{64}\.cache', path.name)

    @staticmethod
    def _remove(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A decoder/player may still be releasing its local file. Retry
            # on the next sweep without changing an already delivered result.
            pass

    def _recover(self):
        for path in self.directory.iterdir():
            if not path.is_symlink() and re.fullmatch(r'\.preview-[a-f0-9-]{36}\.part', path.name):
                self._remove(path)
        self._prune()

    def _prune(self):
        entries = []
        pinned = {path for _platform, path in self.leases.values()}
        for path in self.directory.iterdir():
            if not self._owned_cache(path):
                continue
            stat = path.stat()
            if path not in pinned and time.time()-stat.st_mtime > 24*60*60:
                self._remove(path)
            else:
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in entries)
        for _, size, path in sorted(entries):
            if total <= 512*1024*1024:
                break
            if path in pinned:
                continue
            self._remove(path)
            total -= size

    async def _load(self, identity, platform, candidate, generation, epoch, max_size):
        record = SimpleNamespace(spec=SimpleNamespace(task_id=identity), attempt_id=None)
        context = TaskContext(record, Queue())
        temporary = self.directory/('.preview-'+identity+'.part')
        try:
            async with self.limit:
                credentials = await self.parser.authenticate(platform, context=context)
                self.auth.check(credentials)
                if credentials.epoch != epoch:
                    return
                digest = hashlib.sha256((str(epoch)+candidate.url).encode()).hexdigest()
                cached = self.directory/(platform.value+'-'+digest+'.cache')
                if cached.is_symlink():
                    raise ValueError('unexpected_cache_link')
                if not cached.is_file():
                    async def progress(size, total):
                        self.auth.check(credentials)
                        if size > 512*1024*1024 or total and total > 512*1024*1024:
                            raise ValueError('preview_too_large')
                    for attempt in range(2):
                        try:
                            result = await asyncio.wait_for(HTTPTransfer().download(candidate, platform, temporary,
                                progress=progress, cookies=credentials.cookies), timeout=60)
                            break
                        except (httpx.TransportError,asyncio.TimeoutError):
                            if attempt:raise
                            await asyncio.sleep(1)
                    await async_validate_media(context, temporary, candidate.component,
                        ffprobe=self.ffprobe, expected_length=result['bytes'])
                    self.auth.check(credentials)
                    if not cached.exists():
                        temporary.rename(cached)
                    else:
                        temporary.unlink(missing_ok=True)
                if candidate.component == Component.STILL:
                    payload = await self._decode(context, cached, max_size)
                    operation = 'decoded'
                else:
                    self.leases[identity] = (platform, cached)
                    payload = {'path':str(cached)}
                    operation = 'motion_ready'
                self.auth.check(credentials)
                if self.auth.epoch(platform) == epoch:
                    self.events.put(dict(request_id=identity, generation=generation, operation=operation, **payload))
                self._prune()
        except asyncio.CancelledError:
            self.leases.pop(identity, None)
        except Exception as exc:
            self.leases.pop(identity, None)
            self.events.put(dict(request_id=identity, generation=generation, operation='error',
                                 error_code=getattr(exc, 'code', None) or type(exc).__name__))
        finally:
            context.dispose()
            temporary.unlink(missing_ok=True)

    async def _decode(self, context, cached, max_size):
        host = PreviewHost()
        context.cleanup.append(host.close)
        try:
            request = host.send('decode', path=str(cached), max_size=max_size)
            end = time.monotonic()+30
            while time.monotonic() < end:
                try:
                    event = await asyncio.to_thread(host.events.get, True, .1)
                except Empty:
                    continue
                if event.get('operation') == 'error':
                    raise ValueError('image_decode_failed')
                if event.get('request_id') == request:
                    return {key:event[key] for key in ('image','width','height','display_width','display_height','format')}
            raise TimeoutError('preview_timeout')
        finally:
            host.close()
            context.cleanup.remove(host.close)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError('Preview service cleanup timed out')
