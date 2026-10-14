"""Existing yt-dlp/aria2/FFmpeg flow executed from an immutable task."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import shutil

from .auth import AuthConfig
from .files import OutputFiles, name_stem
from .models import Component, Platform, TaskState
from .validation import async_validate_media
from .messages import ERRORS,error_text


@dataclass(frozen=True)
class DownloadTools:
    ytdlp: Path
    aria2: Path
    ffmpeg: Path
    ffprobe: Path


class DownloadError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def auth_arguments(config: AuthConfig) -> list[str]:
    if config.mode == 'guest':
        return []
    if config.mode == 'browser':
        source = config.browser + (':' + config.profile if config.profile else '')
        return ['--cookies-from-browser', source]
    if not config.cookie_path or not Path(config.cookie_path).is_file():
        raise DownloadError('cookie_file_missing')
    return ['--cookies',config.cookie_path]


def stop_process_tree(process):
    if process.returncode is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW,check=False,timeout=5)
        else:
            process.terminate()
    except (ProcessLookupError,OSError,subprocess.TimeoutExpired):
        try:
            process.kill()
        except ProcessLookupError:
            pass


class BilibiliExecutor:
    def __init__(self,store,vault,auth,tools: DownloadTools,command_builder):
        self.store,self.vault,self.auth = store,vault,auth
        self.tools,self.command_builder = tools,command_builder
        self.files = OutputFiles()

    async def __call__(self,context):
        try:
            return await self._execute(context)
        except BaseException as exc:
            if isinstance(exc,ValueError) and str(exc) in ERRORS:
                exc=DownloadError(str(exc))
            elif isinstance(exc,FileNotFoundError):
                exc=DownloadError('missing_tool')
            retained=context.retained_output
            if retained and not isinstance(exc,asyncio.CancelledError):
                try:
                    proof=await async_validate_media(context,Path(retained['path']),Component.VIDEO,
                        ffprobe=self.tools.ffprobe,expected_length=retained['evidence']['size'])
                except (ValueError,TimeoutError):
                    proof=None
                if proof and proof['sha256']==retained['evidence']['sha256']:
                    self.store.update_output(retained['id'],state='succeeded',path=retained['path'],
                        evidence=retained['evidence'],attempt_id=context.record.attempt_id)
                    code=getattr(exc,'code',None) or type(exc).__name__
                    context.emit('warning',error_code=code,
                        message='已有视频保留，本次补字幕未完成。'+error_text(code))
                    return TaskState.PARTIAL
            for output in self.store.outputs(context.record.spec.task_id):
                if output['state'] != 'succeeded':
                    self.store.update_output(output['id'],
                        state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',
                        error_code=getattr(exc,'code',None) or type(exc).__name__,
                        attempt_id=context.record.attempt_id)
            raise exc

    async def _execute(self,context):
        spec = context.record.spec
        if spec.platform != Platform.BILIBILI:
            raise DownloadError('unsupported_executor_platform')
        outputs = self.store.outputs(spec.task_id)
        if len(outputs)!=1:
            raise DownloadError('invalid_bilibili_output_plan')
        output = outputs[0]
        component = Component(output['component'])
        previous_evidence = json.loads(output['evidence']) if output['evidence'] else None
        previous = Path(output['path']) if output['path'] else None
        subtitle_source = None
        if previous and previous_evidence and previous.is_file() and not previous.is_symlink():
            verified=None
            if previous.stat().st_size == previous_evidence.get('size'):
                try:
                    verified=await async_validate_media(context,previous,component,ffprobe=self.tools.ffprobe,
                                                        expected_length=previous_evidence['size'])
                except ValueError:
                    pass
            if verified and verified['sha256']==previous_evidence.get('sha256'):
                has_subtitles = any(s.get('codec_type')=='subtitle' for s in previous_evidence.get('streams',[]))
                if not spec.subtitle_language or has_subtitles:
                    self.store.update_output(output['id'],state='succeeded',path=str(previous),
                                             evidence=previous_evidence,attempt_id=context.record.attempt_id)
                    context.emit('output_reused',path=str(previous))
                    return TaskState.SUCCEEDED
                subtitle_source = previous
                context.retained_output={'id':output['id'],'path':str(previous),'evidence':previous_evidence}
        if output['state']=='succeeded':
            context.emit('output_missing',message='原成功文件已缺失或改变，将重新下载该输出')
        url = self.vault.get(spec.source_ref)
        config = self.auth.config(spec.platform)
        epoch = self.auth.epoch(spec.platform)
        directory = Path(spec.output_dir)
        directory.mkdir(parents=True,exist_ok=True)
        extension = '.'+spec.audio_format if component==Component.AUDIO else '.mp4'
        staging_stem = '.bili-'+output['id']+'.part'
        if output['temporary_path']:
            registered=Path(output['temporary_path'])
            if registered.parent==directory and registered.name in {
                '.bili-'+output['id']+extension,staging_stem+extension}:
                staging_stem=registered.stem
        temporary = directory/(staging_stem+extension)
        if subtitle_source is not None:
            if temporary.is_symlink():
                raise DownloadError('output_permission_denied')
            await asyncio.to_thread(shutil.copyfile,subtitle_source,temporary)
            context.emit('output_reused',path=str(subtitle_source),message='复用已有视频，补充请求的字幕')
        self.store.update_output(output['id'],state='running',temporary_path=str(temporary),
                                 attempt_id=context.record.attempt_id)
        command = self.command_builder(spec,self.tools,auth_arguments(config),directory,url)
        output_index = command.index('--output')+1
        command[output_index] = staging_stem+'.%(ext)s'
        # Avoid the source URL in process command lines. yt-dlp accepts batch
        # input over its private stdin; the URL is kept in the protected vault.
        command.remove(url)
        command += ['--no-overwrites','--batch-file','-']
        if hasattr(self,'test_sections'):
            command += ['--download-sections',self.test_sections,'--force-keyframes-at-cuts']
        context.emit('phase',phase='downloading')
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process = await spawn
            stop_process_tree(process)
            await process.wait()
            raise
        cleanup = lambda:stop_process_tree(process)
        context.cleanup.append(cleanup)
        try:
            process.stdin.write((url+'\n').encode('utf-8'))
            await process.stdin.drain()
            process.stdin.close()
            permission_failure = False
            failure_code = 'downloader_failed'
            for_line = process.stdout
            while line := await for_line.readline():
                text = line.decode('utf-8',errors='replace')
                if self.auth.epoch(spec.platform)!=epoch:
                    raise DownloadError('authentication_revoked')
                # CLI text can expose URLs and browser details. Only measured
                # progress and controlled phase/error codes leave this worker.
                if any(marker in text.lower() for marker in ('cookie','login','sign in','403','401')):
                    permission_failure = permission_failure or 'error' in text.lower()
                if any(marker in text.lower() for marker in ('no space left','disk full','not enough space','errno 28')):
                    failure_code = 'disk_full'
                elif 'permission denied' in text.lower():
                    failure_code = 'output_permission_denied'
                match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%',text)
                if not match:
                    match = re.search(r'\((\d+(?:\.\d+)?)%\)',text)
                if match:
                    context.emit('progress',percent=min(float(match[1]),99.0))
                if any(marker in text for marker in ('[Merger]','[ExtractAudio]','[VideoRemuxer]','[EmbedSubtitle]')):
                    context.emit('phase',phase='processing')
            code = await process.wait()
            if self.auth.epoch(spec.platform)!=epoch:
                raise DownloadError('authentication_revoked')
            if code!=0:
                raise DownloadError('needs_auth_or_permission' if permission_failure else failure_code)
        finally:
            cleanup()
            if process.returncode is None:
                await process.wait()
            context.cleanup.remove(cleanup)
        context.emit('phase',phase='validating')
        self.store.transition(spec.task_id,TaskState.VALIDATING,attempt_id=context.record.attempt_id)
        evidence = await async_validate_media(context,temporary,component,ffprobe=self.tools.ffprobe)
        if subtitle_source is not None and not any(s.get('codec_type')=='subtitle' for s in evidence.get('streams',[])):
            temporary.unlink()
            self.store.update_output(output['id'],state='succeeded',path=str(subtitle_source),
                                     evidence=previous_evidence,attempt_id=context.record.attempt_id)
            context.emit('warning',message='已有视频保留，但本次仍未取得请求的字幕',error_code='subtitle_missing')
            return TaskState.PARTIAL
        # Preserve a previous reservation on retry. If an external file took
        # that name, report conflict rather than overwriting it.
        if previous is not None and not previous.exists():
            destination = previous
        else:
            stem = name_stem(directory,'B站',spec.title,spec.work_id,output['original_index'])
            destination = self.files.reserve(directory,stem,{component.value:evidence['extension']}).path(component.value)
        self.store.update_output(output['id'],state='validating',path=str(destination),
                                 evidence=evidence,attempt_id=context.record.attempt_id)
        self.files.commit(temporary,destination)
        self.store.update_output(output['id'],state='succeeded',path=str(destination),
                                 evidence=evidence,attempt_id=context.record.attempt_id)
        context.emit('output_saved',path=str(destination))
        if subtitle_source is not None:
            context.result_message = '已补入字幕，原视频仍保留在原位置。'
        if spec.subtitle_language and not any(s.get('codec_type')=='subtitle' for s in evidence.get('streams',[])):
            context.emit('warning',message='视频已保存，但请求的字幕未嵌入',error_code='subtitle_missing')
            return TaskState.PARTIAL
        return TaskState.SUCCEEDED
