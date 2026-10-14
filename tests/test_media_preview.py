import asyncio
import os
from pathlib import Path
from queue import Queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from bili.media_preview import MediaPreviewService
from bili.models import Platform
from bili.preview import PreviewHost
from bili.tasks import TaskContext


class MediaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = None

    def tearDown(self):
        if self.service:
            self.service.close()
        self.temporary.cleanup()

    def start(self):
        self.service = MediaPreviewService(self.root, SimpleNamespace(), SimpleNamespace())
        return self.service

    def cache(self, platform, digit):
        path = self.root / (platform.value+'-'+digit*64+'.cache')
        path.write_bytes(b'fixture')
        return path

    def run_async(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.service.loop).result(timeout=8)

    def test_restart_removes_owned_partial_and_expired_cache_only(self):
        partial = self.root / ('.preview-'+str(uuid4())+'.part')
        partial.write_bytes(b'incomplete')
        expired = self.cache(Platform.DOUYIN, 'a')
        os.utime(expired, (0,0))
        external = self.root / 'user.part'
        external.write_bytes(b'keep')
        young = self.cache(Platform.XIAOHONGSHU, 'b')
        self.start()
        self.assertFalse(partial.exists())
        self.assertFalse(expired.exists())
        self.assertTrue(external.exists())
        self.assertTrue(young.exists())

    def test_revoke_waits_for_platform_jobs_then_purges_only_its_cache(self):
        service = self.start()
        dy = self.cache(Platform.DOUYIN, 'a')
        xhs = self.cache(Platform.XIAOHONGSHU, 'b')
        cleaned = []
        async def install():
            async def running():
                try:
                    await asyncio.sleep(60)
                finally:
                    cleaned.append(True)
            job = asyncio.create_task(running())
            service.jobs['fixture'] = (Platform.DOUYIN, job)
            job.add_done_callback(lambda _job:service.jobs.pop('fixture',None))
            service.leases['fixture'] = (Platform.DOUYIN, dy)
            await asyncio.sleep(0)
        self.run_async(install())
        service.revoke(Platform.DOUYIN).result(timeout=5)
        self.assertEqual(cleaned, [True])
        self.assertFalse(dy.exists())
        self.assertTrue(xhs.exists())
        self.assertFalse(service.leases)

    def test_playing_cache_survives_expiry_until_player_releases_it(self):
        service = self.start()
        active = self.cache(Platform.DOUYIN, 'a')
        os.utime(active, (0,0))
        async def sweep():
            service.leases['player'] = (Platform.DOUYIN, active)
            service._prune()
        self.run_async(sweep())
        self.assertTrue(active.exists())
        service.cancel(['player'])
        async def sweep_again():
            service._prune()
        self.run_async(sweep_again())
        self.assertFalse(active.exists())

    def test_native_decode_crash_isolated_and_next_preview_recovers(self):
        service = self.start()
        image = self.root / 'source.png'
        Image.new('RGB',(17,11),'green').save(image)
        record = SimpleNamespace(spec=SimpleNamespace(task_id='fixture'), attempt_id=None)
        context = TaskContext(record, Queue())
        broken = PreviewHost()
        broken.process.kill()
        broken.process.wait(timeout=3)
        broken._reader.join(timeout=3)
        try:
            with patch('bili.media_preview.PreviewHost', return_value=broken):
                with self.assertRaises(RuntimeError):
                    self.run_async(service._decode(context,image,180))
            decoded = self.run_async(service._decode(context,image,180))
            self.assertEqual((decoded['width'],decoded['height']), (17,11))
            self.assertFalse(context.cleanup)
        finally:
            broken.close()
            context.dispose()
