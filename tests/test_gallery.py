import asyncio
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock

from bili.auth import AuthService, Credentials
from bili.gallery import GalleryExecutor, output_plan, protect_work
from bili.models import Candidate, Component, MediaItem, Platform, TaskSpec, TaskState, Work
from bili.parser import ParserError
from bili.secrets import SecretVault
from bili.storage import TaskStore
from bili.tasks import TaskService
from bili.transfer import TransferError


class TestProtector:
    def protect(self, value):
        return b'test-encrypted:'+value
    def unprotect(self, value):
        return value.removeprefix(b'test-encrypted:')


async def validator(context, path, component, **kwargs):
    value = Path(path).read_bytes()
    if len(value) != kwargs.get('expected_length', len(value)) or value.startswith(b'bad'):
        raise ValueError('non_media_response')
    return dict(size=len(value), sha256=hashlib.sha256(value).hexdigest(),
                extension='.png' if component == Component.STILL else '.mp4')


def work(live=False, missing=False, count=1):
    return Work(Platform.XIAOHONGSHU, '0123456789abcdef01234567', 'gallery', tuple(
        MediaItem('item-'+str(index), index, 'live' if live else 'image',
            still_candidates=(Candidate('https://sns-img-bd.xhscdn.com/'+str(index), Component.STILL, 'test'),),
            motion_candidates=(Candidate('https://sns-video.xhscdn.com/'+str(index), Component.MOTION, 'test'),)
                if live and not missing else (),
            missing_components=(Component.MOTION,) if live and missing else ())
        for index in range(1, count+1)), title='中文标题')


class GalleryExecutorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = TaskStore(self.root/'state/tasks.sqlite3')
        self.vault = SecretVault(self.root/'state/secrets', TestProtector())
        self.auth = AuthService(self.store)
        self.parser = type('Parser', (), {})()
        self.parser.authenticate = AsyncMock(return_value=Credentials(Platform.XIAOHONGSHU, 0, CookieJar()))
        self.parser.parse = AsyncMock(side_effect=ParserError('needs_auth_or_permission'))
        self.calls = []
        self.fail_motion = False
        self.slow_motion = False
        self.motion_started = threading.Event()
        async def download(candidate, platform, temporary, **kwargs):
            self.calls.append(candidate.url)
            if candidate.component == Component.MOTION:
                self.motion_started.set()
                if self.slow_motion:
                    await asyncio.sleep(60)
                if self.fail_motion:
                    raise TransferError('http_failure', status=404)
            value = b'valid-'+candidate.component.value.encode()
            Path(temporary).write_bytes(value)
            await kwargs['progress'](len(value), len(value))
            return {'bytes':len(value)}
        transfer = type('Transfer', (), {'download':staticmethod(download)})()
        self.executor = GalleryExecutor(self.store, self.vault, self.auth, self.parser,
                                       transfer=transfer, validator=validator)
        self.service = TaskService(self.store, self.executor)

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.directory.cleanup()

    def submit(self, media, selected=None, components=(Component.STILL, Component.MOTION)):
        spec = TaskSpec(media.platform, media.work_id, media.title,
            self.vault.put('https://www.xiaohongshu.com/explore/'+media.work_id), str(self.root),
            tuple(selected or [item.item_id for item in media.items]), components,
            work_ref=protect_work(self.vault, media))
        outputs = output_plan(spec, media)
        self.service.submit(spec, outputs)
        return spec, outputs

    def wait(self, predicate):
        deadline = time.monotonic()+5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('gallery executor timed out')
            time.sleep(.01)

    def finished(self, spec):
        self.wait(lambda:self.store.task(spec.task_id).state in (
            TaskState.SUCCEEDED, TaskState.PARTIAL, TaskState.FAILED, TaskState.CANCELLED))
        return self.store.task(spec.task_id)

    def test_selection_preserves_original_index_and_flat_output(self):
        spec, _ = self.submit(work(count=3), ('item-3',))
        self.assertEqual(self.finished(spec).state, TaskState.SUCCEEDED)
        row, = self.store.outputs(spec.task_id)
        self.assertIn('_003_still.png', row['path'])
        self.assertEqual(Path(row['path']).parent, self.root)
        self.assertEqual(len(self.calls), 1)

    def test_live_pair_uses_same_collision_suffix_and_does_not_overwrite(self):
        media = work(live=True)
        old = self.root/('小红书_中文标题_'+media.work_id+'_001_still.png')
        old.write_bytes(b'previous-file')
        spec, _ = self.submit(media)
        self.assertEqual(self.finished(spec).state, TaskState.SUCCEEDED)
        paths = [Path(row['path']) for row in self.store.outputs(spec.task_id)]
        self.assertTrue(all(' (1)_' in path.name for path in paths))
        self.assertEqual(old.read_bytes(), b'previous-file')
        self.assertEqual([url.split('/')[2] for url in self.calls], ['sns-img-bd.xhscdn.com','sns-video.xhscdn.com'])

    def test_missing_motion_is_partial_and_still_is_saved(self):
        spec, _ = self.submit(work(live=True, missing=True))
        record = self.finished(spec)
        self.assertEqual(record.state, TaskState.PARTIAL)
        self.assertEqual(record.error_code, 'missing_media_component')
        self.assertEqual({row['state'] for row in self.store.outputs(spec.task_id)}, {'succeeded','missing'})

    def test_static_only_scope_does_not_request_motion_or_mark_it_as_failed(self):
        spec,_=self.submit(work(live=True,missing=True),components=(Component.STILL,))
        record=self.finished(spec)
        self.assertEqual(record.state,TaskState.SUCCEEDED)
        self.assertIn('动态部分未提取',record.message)
        self.assertEqual(self.calls,['https://sns-img-bd.xhscdn.com/1'])
        rows=self.store.outputs(spec.task_id)
        self.assertEqual([row['component'] for row in rows],['still'])

    def test_retry_reuses_verified_still_and_adds_matching_motion(self):
        self.fail_motion = True
        spec, _ = self.submit(work(live=True))
        self.assertEqual(self.finished(spec).state, TaskState.PARTIAL)
        still = next(row for row in self.store.outputs(spec.task_id) if row['component'] == 'still')
        self.fail_motion = False
        self.service.retry(spec.task_id)
        self.assertEqual(self.finished(spec).state, TaskState.SUCCEEDED)
        rows = self.store.outputs(spec.task_id)
        self.assertEqual(next(row['path'] for row in rows if row['component'] == 'still'), still['path'])
        self.assertEqual(self.calls.count('https://sns-img-bd.xhscdn.com/1'), 1)
        paths = {row['component']:Path(row['path']) for row in rows}
        self.assertEqual(paths['still'].stem.removesuffix('_still'), paths['motion'].stem.removesuffix('_motion'))

    def test_cancel_during_motion_retains_validated_still(self):
        self.slow_motion = True
        spec, _ = self.submit(work(live=True))
        self.assertTrue(self.motion_started.wait(3))
        self.service.cancel(spec.task_id)
        self.assertEqual(self.finished(spec).state, TaskState.CANCELLED)
        still = next(row for row in self.store.outputs(spec.task_id) if row['component'] == 'still')
        self.assertEqual(still['state'], 'succeeded')
        self.assertTrue(Path(still['path']).is_file())
        self.assertFalse(list(self.root.glob('.bili-*.part')))

    def test_changed_success_file_is_redownloaded_without_overwriting(self):
        spec, _ = self.submit(work(live=True, missing=True))
        self.finished(spec)
        still = next(row for row in self.store.outputs(spec.task_id) if row['component'] == 'still')
        Path(still['path']).write_bytes(b'changed-data')
        self.service.retry(spec.task_id)
        self.finished(spec)
        changed = next(row for row in self.store.outputs(spec.task_id) if row['component'] == 'still')
        self.assertNotEqual(changed['path'], still['path'])
        self.assertEqual(Path(still['path']).read_bytes(), b'changed-data')

    def test_retry_can_obtain_previously_missing_motion_by_stable_identity(self):
        original = work(live=True, missing=True)
        spec, _ = self.submit(original)
        self.assertEqual(self.finished(spec).state, TaskState.PARTIAL)
        fresh = work(live=True)
        self.parser.parse = AsyncMock(return_value=('https://www.xiaohongshu.com/explore/'+fresh.work_id, fresh))
        self.service.retry(spec.task_id)
        self.assertEqual(self.finished(spec).state, TaskState.SUCCEEDED)
        self.assertEqual(self.calls.count('https://sns-img-bd.xhscdn.com/1'), 1)
        self.parser.parse.assert_awaited_once()

    def test_refreshed_url_cannot_rebind_to_different_item_position(self):
        original = work()
        fresh = Work(original.platform, original.work_id, 'gallery', (
            MediaItem('item-1', 2, 'image', still_candidates=original.items[0].still_candidates),))
        self.parser.parse = AsyncMock(return_value=('https://www.xiaohongshu.com/explore/'+original.work_id, fresh))
        self.executor.transfer.download = AsyncMock(side_effect=TransferError('http_failure', status=404))
        spec, _ = self.submit(original)
        record = self.finished(spec)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertEqual(record.error_code, 'selected_media_unavailable')
        self.assertEqual(self.executor.transfer.download.await_count, 1)
        self.parser.parse.assert_awaited_once()

    def test_native_validation_failure_never_commits_html(self):
        async def bad_download(candidate, platform, temporary, **kwargs):
            Path(temporary).write_bytes(b'bad-html')
            return {'bytes':8}
        self.executor.transfer.download = bad_download
        spec, _ = self.submit(work())
        self.assertEqual(self.finished(spec).state, TaskState.FAILED)
        self.assertFalse(list(self.root.glob('*.png')))
        self.assertFalse(list(self.root.glob('.bili-*.part')))

    def test_snapshot_reference_backwards_compatible_with_old_task_records(self):
        media = work()
        spec, _ = self.submit(media)
        record = spec.record()
        del record['work_ref']
        self.assertIsNone(TaskSpec.from_record(record).work_ref)
        self.assertEqual(self.finished(spec).state, TaskState.SUCCEEDED)


if __name__ == '__main__':
    unittest.main()
