"""Selected gallery resources, paired files and verified retry reuse."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from .bilibili import DownloadError
from .files import OutputFiles, name_stem
from .links import classify_url
from .messages import ERRORS, error_text
from .models import Component, OutputSpec, Platform, TaskState, Watermark
from .parser import ParserError
from .transfer import HTTPTransfer, TransferError
from .validation import async_validate_media
from .wire import work_from_wire, work_to_wire


def protect_work(vault, work):
    return vault.put(json.dumps(work_to_wire(work), ensure_ascii=False))


def output_plan(spec, work):
    if work.platform != spec.platform or work.work_id != spec.work_id:
        raise DownloadError('work_identity_mismatch')
    if work.content_type not in ('gallery','video'):
        raise DownloadError('unsupported_work_type')
    allowed={Component.STILL,Component.MOTION} if work.content_type=='gallery' else {Component.VIDEO,Component.AUDIO}
    if any(component not in allowed for component in spec.components):
        raise DownloadError('unsupported_work_type')
    items = {item.item_id:item for item in work.items}
    if any(item_id not in items for item_id in spec.selected_item_ids):
        raise DownloadError('selected_media_unavailable')
    result = []
    for item in work.items:
        if item.item_id not in spec.selected_item_ids:
            continue
        if work.content_type == 'video':
            for component in (Component.VIDEO,Component.AUDIO):
                if component in spec.components:
                    result.append(OutputSpec(spec.task_id,item.item_id,component,item.original_index))
            continue
        if Component.STILL in spec.components:
            result.append(OutputSpec(spec.task_id, item.item_id, Component.STILL, item.original_index))
        if item.kind == 'live' and Component.MOTION in spec.components:
            result.append(OutputSpec(spec.task_id, item.item_id, Component.MOTION, item.original_index))
    if not result:
        raise DownloadError('selected_media_unavailable')
    return tuple(result)


def preferred_candidates(candidates):
    def key(candidate):
        return ({Watermark.CLEAN:0, Watermark.UNKNOWN:1, Watermark.MARKED:2}[candidate.watermark],
                candidate.source.endswith(':download'),
                -(candidate.width or 0)*(candidate.height or 0), -(candidate.bitrate or 0))
    return tuple(sorted(candidates, key=key))


class GalleryExecutor:
    def __init__(self, store, vault, auth, parser, *, ffprobe=None, transfer=None,
                 validator=async_validate_media):
        self.store, self.vault, self.auth, self.parser = store, vault, auth, parser
        self.ffprobe = ffprobe
        self.transfer = transfer or HTTPTransfer()
        self.validator = validator
        self.files = OutputFiles()

    async def __call__(self, context):
        spec = context.record.spec
        if spec.platform not in (Platform.DOUYIN, Platform.XIAOHONGSHU):
            raise DownloadError('unsupported_executor_platform')
        work = (work_from_wire(json.loads(self.vault.get(spec.work_ref))) if spec.work_ref else
                (await self.parser.parse(classify_url(self.vault.get(spec.source_ref)), context=context))[1])
        expected = {(output.item_id, output.component) for output in output_plan(spec, work)}
        outputs = self.store.outputs(spec.task_id)
        if {(row['item_id'], Component(row['component'])) for row in outputs} != expected:
            raise DownloadError('invalid_gallery_output_plan')
        credentials = await self.parser.authenticate(spec.platform, context=context)
        self.auth.check(credentials)
        directory = Path(spec.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        runtime = {'work':work, 'refreshed':False,
                   'allow_missing_refresh':any(row['path'] or row['temporary_path'] for row in outputs)}
        completed = 0
        warnings = []
        try:
            for item in work.items:
                group = [row for row in outputs if row['item_id'] == item.item_id]
                if not group:
                    continue
                if item.kind == 'live' and Component.MOTION not in spec.components:
                    warnings.append('本任务仅保存静态图片，动态部分未提取。')
                prepared = []
                reused = []
                try:
                    # Still precedes motion, even though the store sorts labels.
                    group.sort(key=lambda row:row['component'] != Component.STILL)
                    for row in group:
                        self.auth.check(credentials)
                        if await self._reuse(context, row):
                            reused.append(row)
                            completed += 1
                            continue
                        try:
                            evidence, candidate, temporary = await self._obtain(
                                context, row, credentials, runtime, completed+len(prepared), len(outputs))
                            prepared.append((row, temporary, evidence))
                            if candidate.watermark == Watermark.MARKED:
                                warnings.append('部分资源带有平台标记，已保存实际可取得的版本。')
                            elif candidate.watermark == Watermark.UNKNOWN:
                                warnings.append('无法确认全部资源的水印状态。')
                        except (TransferError, DownloadError, ParserError, ValueError, TimeoutError, httpx.HTTPError) as exc:
                            code = self._code(exc)
                            self.store.update_output(row['id'], state='missing' if code == 'missing_media_component' else 'failed',
                                error_code=code, message=error_text(code), attempt_id=context.record.attempt_id)
                            if code == 'authentication_revoked':
                                raise
                            context.emit('output_failed', output_id=row['id'], error_code=code)
                finally:
                    # Already validated components survive a later failure or cancellation.
                    completed += self._commit(context, item, prepared, reused)
                    for row in group:
                        temporary = OutputFiles.temporary(directory, row['id'])
                        OutputFiles.remove_temporary(temporary, directory, row['id'])
        except BaseException as exc:
            for row in self.store.outputs(spec.task_id):
                if row['state'] not in ('succeeded', 'failed', 'missing'):
                    self.store.update_output(row['id'], state='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed',
                        error_code=self._code(exc), attempt_id=context.record.attempt_id)
            raise
        rows = self.store.outputs(spec.task_id)
        success = sum(row['state'] == 'succeeded' for row in rows)
        failed = len(rows)-success
        if failed:
            code = next((row['error_code'] for row in rows if row['state'] != 'succeeded'), 'media_transfer_failed')
            context.emit('warning', error_code=code,
                message=f'已保存 {success} 个文件，{failed} 个输出未取得。'+error_text(code))
        elif warnings:
            context.emit('warning', message=' '.join(dict.fromkeys(warnings)))
        return TaskState.PARTIAL if success and failed else (TaskState.SUCCEEDED if success else TaskState.FAILED)

    async def _reuse(self, context, row):
        if not row['path'] or not row['evidence']:
            return False
        previous = Path(row['path'])
        evidence = json.loads(row['evidence'])
        if not previous.is_file() or previous.is_symlink() or previous.stat().st_size != evidence.get('size'):
            return False
        try:
            proof = await self.validator(context, previous, Component(row['component']),
                ffprobe=self.ffprobe, expected_length=evidence.get('size'))
        except (ValueError, OSError, TimeoutError):
            return False
        if proof['sha256'] != evidence.get('sha256'):
            return False
        self.store.update_output(row['id'], state='succeeded', path=str(previous), evidence=evidence,
                                 attempt_id=context.record.attempt_id)
        context.emit('output_reused', output_id=row['id'], path=str(previous))
        return True

    async def _obtain(self, context, row, credentials, runtime, completed, total):
        spec = context.record.spec
        component = Component(row['component'])
        temporary = OutputFiles.temporary(Path(spec.output_dir), row['id'])
        self.store.update_output(row['id'], state='running', temporary_path=str(temporary),
                                 attempt_id=context.record.attempt_id)
        context.emit('phase', phase='downloading')
        last_error = DownloadError('missing_media_component')
        for refresh in range(2):
            item = next((item for item in runtime['work'].items if item.item_id == row['item_id']), None)
            if item is None or item.original_index != row['original_index']:
                raise DownloadError('selected_media_unavailable')
            candidates = getattr(item,component.value+'_candidates')
            if component == Component.AUDIO and not candidates:
                candidates = item.video_candidates
            for position, candidate in enumerate(preferred_candidates(candidates)):
                for retry in range(2):
                    try:
                        self.auth.check(credentials)
                        async def progress(size, length):
                            self.auth.check(credentials)
                            fraction = min(size/length, 1) if length else 0
                            context.emit('progress', percent=min((completed+fraction)/total*100, 99),
                                         output_id=row['id'], bytes=size, total=length)
                        result = await self.transfer.download(candidate, spec.platform, temporary,
                            progress=progress, cookies=credentials.cookies)
                        self.auth.check(credentials)
                        self.store.update_output(row['id'], state='validating', attempt_id=context.record.attempt_id)
                        context.emit('phase', phase='validating')
                        evidence = await self.validator(context, temporary, component,
                            ffprobe=self.ffprobe, expected_length=result.get('bytes'))
                        self.auth.check(credentials)
                        evidence.update(source=candidate.source, watermark=candidate.watermark.value,
                                        watermark_evidence=candidate.evidence,
                                        backend_revision=runtime['work'].backend_revision)
                        if position:
                            context.emit('resource_fallback', output_id=row['id'], message='首选资源不可用，已保存备选资源。')
                        return evidence, candidate, temporary
                    except (TransferError, ValueError, TimeoutError, httpx.HTTPError) as exc:
                        last_error = exc
                        transient = (isinstance(exc, httpx.TransportError) or
                                     isinstance(exc, TransferError) and (exc.status == 429 or (exc.status or 0) >= 500))
                        if not transient or retry:
                            break
                        await asyncio.sleep(1)
            if (runtime['refreshed'] or refresh or ':position:' in row['item_id']
                    or not candidates and not runtime['allow_missing_refresh']
                    or getattr(last_error, 'code', '') == 'authentication_revoked'):
                break
            runtime['refreshed'] = True
            _, fresh = await self.parser.parse(classify_url(self.vault.get(spec.source_ref)), context=context)
            if (fresh.work_id != spec.work_id or fresh.platform != spec.platform
                    or fresh.content_type != runtime['work'].content_type):
                raise DownloadError('work_identity_mismatch')
            runtime['work'] = fresh
            self.auth.check(credentials)
        if not candidates:
            raise DownloadError('missing_media_component')
        raise last_error

    def _commit(self, context, item, prepared, reused):
        if not prepared:
            return 0
        spec = context.record.spec
        directory = Path(spec.output_dir)
        components = {row['component']:evidence['extension'] for row, _, evidence in prepared}
        if reused:
            owner = reused[0]
            previous = Path(owner['path'])
            suffix = '_'+owner['component']+previous.suffix
            if previous.parent != directory or not previous.name.endswith(suffix):
                raise DownloadError('invalid_gallery_output_plan')
            stem = previous.name[:-len(suffix)]
            paths = {component:directory/(stem+'_'+component+extension) for component, extension in components.items()}
        else:
            stem = name_stem(directory, '抖音' if spec.platform == Platform.DOUYIN else '小红书',
                             spec.title, spec.work_id, item.original_index)
            reservation = self.files.reserve(directory, stem, components)
            paths = dict(reservation.paths)
        saved = 0
        for row, temporary, evidence in prepared:
            destination = paths[row['component']]
            try:
                self.store.update_output(row['id'], state='validating', path=str(destination), evidence=evidence,
                                         attempt_id=context.record.attempt_id)
                OutputFiles.commit(temporary, destination)
                self.store.update_output(row['id'], state='succeeded', path=str(destination), evidence=evidence,
                                         attempt_id=context.record.attempt_id)
                context.emit('output_saved', output_id=row['id'], path=str(destination))
                saved += 1
            except OSError as exc:
                self.store.update_output(row['id'], state='failed', error_code=self._code(exc),
                                         attempt_id=context.record.attempt_id)
        return saved

    @staticmethod
    def _code(exc):
        if getattr(exc, 'code', None):
            return exc.code
        if isinstance(exc, ValueError) and str(exc) in ERRORS:
            return str(exc)
        return type(exc).__name__
