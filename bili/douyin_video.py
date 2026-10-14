"""Video and full audio as independent outputs of one Douyin task."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from .bilibili import DownloadError, stop_process_tree
from .gallery import GalleryExecutor, output_plan
from .links import classify_url
from .messages import error_text
from .models import Component, Platform, TaskState
from .wire import work_from_wire


class DouyinVideoExecutor(GalleryExecutor):
    def __init__(self,*args,ffmpeg=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.ffmpeg=ffmpeg

    async def __call__(self,context):
        spec=context.record.spec
        if spec.platform!=Platform.DOUYIN:
            raise DownloadError('unsupported_executor_platform')
        work=(work_from_wire(json.loads(self.vault.get(spec.work_ref))) if spec.work_ref else
              (await self.parser.parse(classify_url(self.vault.get(spec.source_ref)),context=context))[1])
        if work.content_type!='video' or len(work.items)!=1:
            raise DownloadError('unsupported_work_type')
        expected={(o.item_id,o.component) for o in output_plan(spec,work)}
        rows=self.store.outputs(spec.task_id)
        if {(row['item_id'],Component(row['component'])) for row in rows}!=expected:
            raise DownloadError('invalid_gallery_output_plan')
        credentials=await self.parser.authenticate(spec.platform,context=context)
        self.auth.check(credentials)
        runtime={'work':work,'refreshed':False,'allow_missing_refresh':any(row['path'] or row['temporary_path'] for row in rows)}
        item=work.items[0]
        reused=[]
        source=None
        source_proof=None
        downloaded_source=None
        source_identity=str(uuid4())
        source_path=self.files.temporary(Path(spec.output_dir),source_identity)
        try:
            # A verified existing video is also the input when retrying audio.
            for row in sorted(rows,key=lambda row:row['component']!=Component.VIDEO):
                if await self._reuse(context,row):
                    reused.append(row)
                    if row['component']==Component.VIDEO:
                        source=Path(row['path']);source_proof=json.loads(row['evidence'])
                    continue
                temporary=self.files.temporary(Path(spec.output_dir),row['id'])
                try:
                    if row['component']==Component.VIDEO or source is None:
                        proof,candidate,downloaded=await self._obtain(context,row,credentials,runtime,len(reused),len(rows))
                        if row['component']==Component.VIDEO:
                            self._commit(context,item,[(row,downloaded,proof)],reused)
                            current=next(r for r in self.store.outputs(spec.task_id) if r['id']==row['id'])
                            if current['state']!='succeeded':
                                continue
                            reused.append(current)
                            source=Path(current['path']);source_proof=proof
                            continue
                        downloaded.rename(source_path)
                        source=source_path;downloaded_source=source_path;source_proof=proof
                    self.auth.check(credentials)
                    self.store.update_output(row['id'],state='running',temporary_path=str(temporary),
                                             attempt_id=context.record.attempt_id)
                    policy=spec.audio_format or 'original'
                    await self._extract_audio(context,source,source_proof,temporary,policy)
                    proof=await self.validator(context,temporary,Component.AUDIO,ffprobe=self.ffprobe)
                    self.auth.check(credentials)
                    proof.update(audio_policy='stream_copy' if policy=='original' else 'transcoded_'+policy,
                                 backend_revision=runtime['work'].backend_revision)
                    original_codec=next((s['codec_name'] for s in source_proof.get('streams',[]) if s['codec_type']=='audio'),None)
                    if policy=='original' and not any(s.get('codec_name')==original_codec for s in proof.get('streams',[])):
                        raise DownloadError('audio_codec_changed')
                    self._commit(context,item,[(row,temporary,proof)],reused)
                except (asyncio.CancelledError,KeyboardInterrupt):
                    raise
                except Exception as exc:
                    code=self._code(exc)
                    self.store.update_output(row['id'],state='failed',error_code=code,message=error_text(code),
                                             attempt_id=context.record.attempt_id)
                    if code=='authentication_revoked':raise
                finally:
                    self.files.remove_temporary(temporary,Path(spec.output_dir),row['id'])
        except BaseException as exc:
            for row in self.store.outputs(spec.task_id):
                if row['state'] not in ('succeeded','failed'):
                    self.store.update_output(row['id'],state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',
                                             error_code=self._code(exc),attempt_id=context.record.attempt_id)
            raise
        finally:
            if downloaded_source:
                self.files.remove_temporary(downloaded_source,Path(spec.output_dir),source_identity)
        rows=self.store.outputs(spec.task_id)
        success=sum(row['state']=='succeeded' for row in rows)
        if success!=len(rows):
            code=next((row['error_code'] for row in rows if row['state']!='succeeded'),None)
            context.emit('warning',error_code=code,message=f'已保存 {success} 个文件。'+error_text(code))
        else:
            context.emit('warning',message='已保存实际可取得的版本；未确认视频水印状态。')
        return TaskState.SUCCEEDED if success==len(rows) else (TaskState.PARTIAL if success else TaskState.FAILED)

    async def _extract_audio(self,context,source,proof,temporary,policy):
        if self.ffmpeg is None or not Path(self.ffmpeg).is_file():
            raise DownloadError('missing_tool')
        codec=next((s.get('codec_name') for s in proof.get('streams',[]) if s.get('codec_type')=='audio'),None)
        if codec is None:raise DownloadError('missing_audio_track')
        if temporary.is_symlink():raise DownloadError('output_permission_denied')
        # This registered staging file belongs to this output and may be retried.
        temporary.unlink(missing_ok=True)
        if policy=='original':
            format_={'aac':'ipod','alac':'ipod','mp3':'mp3','opus':'ogg','vorbis':'ogg','flac':'flac',
                     'pcm_s16le':'wav','pcm_s24le':'wav','pcm_f32le':'wav'}.get(codec,'matroska')
            encoder='copy'
        elif policy in ('mp3','wav'):
            format_=policy
            encoder='libmp3lame' if policy=='mp3' else 'pcm_s16le'
        else:raise DownloadError('unsupported_audio_policy')
        command=[str(self.ffmpeg),'-hide_banner','-loglevel','error','-n','-protocol_whitelist','file,pipe','-i',str(source),
                 '-map','0:a:0','-vn','-c:a',encoder,'-f',format_,str(temporary)]
        context.emit('phase',phase='processing')
        spawn=asyncio.create_task(asyncio.create_subprocess_exec(*command,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        try:process=await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process=await spawn;stop_process_tree(process);await process.wait();raise
        cleanup=lambda:stop_process_tree(process)
        context.cleanup.append(cleanup)
        try:
            timeout=min(max(float(proof.get('duration') or 0)*3+60,120),6*60*60)
            await asyncio.wait_for(process.wait(),timeout=timeout)
            if process.returncode!=0:raise DownloadError('audio_conversion_failed')
        finally:
            cleanup()
            if process.returncode is None:await process.wait()
            context.cleanup.remove(cleanup)
