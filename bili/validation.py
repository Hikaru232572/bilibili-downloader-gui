from __future__ import annotations

import hashlib
import json
from pathlib import Path
from queue import Empty
import subprocess
import time
import asyncio
import os

from .models import Component
from .preview import PreviewHost


IMAGE_EXTENSIONS = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','GIF':'.gif',
                    'HEIF':'.heic','AVIF':'.avif','TIFF':'.tiff','BMP':'.bmp'}


def validate_media(path: Path, component: Component, *, ffprobe: Path | None = None,
                   expected_length: int | None = None, timeout=30, image_worker=True) -> dict:
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise ValueError('empty_or_missing_media')
    size = path.stat().st_size
    if expected_length is not None and size != expected_length:
        raise ValueError('response_length_mismatch')
    with path.open('rb') as stream:
        header = stream.read(512).lstrip().lower()
        if header.startswith((b'<!doctype html',b'<html',b'{"error',b'{"status',
                              b'#extm3u',b'ffconcat',b'<?xml',b'<mpd')):
            raise ValueError('non_media_response')
    if component == Component.STILL:
        if not image_worker:
            from .native_worker import decode_image
            result=decode_image({'path':str(path),'max_size':1})
            extension=IMAGE_EXTENSIONS.get(result['format'])
            if not extension:
                raise ValueError('unsupported_image_format')
            evidence={'format':result['format'],'extension':extension,
                      'width':result['width'],'height':result['height']}
        else:
            evidence=_validate_image_worker(path,timeout)
    else:
        evidence=_validate_streams(path,component,ffprobe,timeout)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    return dict(evidence, size=size, sha256=digest)


def _validate_image_worker(path,timeout):
    host = PreviewHost()
    try:
        request = host.send('decode', path=str(path), max_size=1)
        deadline = time.monotonic()+timeout
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('image_validation_timeout')
            try:
                event = host.events.get(timeout=remaining)
            except Empty:
                raise TimeoutError('image_validation_timeout') from None
            if event.get('operation') == 'error':
                raise ValueError('image_decode_failed')
            if event.get('request_id') == request and event.get('operation') == 'decoded':
                format_ = event['format']
                extension = IMAGE_EXTENSIONS.get(format_)
                if not extension:
                    raise ValueError('unsupported_image_format')
                evidence = {'format':format_,'extension':extension,
                            'width':event['width'],'height':event['height']}
                break
    finally:
        host.close()

    return evidence


def _validate_streams(path,component,ffprobe,timeout):
    if ffprobe is None or not Path(ffprobe).is_file():
        raise ValueError('missing_ffprobe')
    result = subprocess.run([str(ffprobe),'-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format',
                             '-of','json',str(path)], capture_output=True, text=True,
                            encoding='utf-8', timeout=timeout, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode != 0:
        raise ValueError('media_probe_failed')
    info = json.loads(result.stdout)
    streams = info.get('streams') or []
    desired = 'audio' if component == Component.AUDIO else 'video'
    relevant = [s for s in streams if s.get('codec_type') == desired]
    if not relevant:
        raise ValueError('missing_'+desired+'_track')
    duration = float((info.get('format') or {}).get('duration') or 0)
    if duration <= 0:
        raise ValueError('invalid_media_duration')
    format_ = (info.get('format') or {}).get('format_name') or ''
    extension = ('.m4a' if desired == 'audio' else '.mp4') if 'mp4' in format_ else {
        'mp3':'.mp3','wav':'.wav','aac':'.aac','flac':'.flac','ogg':'.ogg',
        'matroska,webm':'.mka' if desired == 'audio' else '.mkv',
        'mpegts':'.ts','avi':'.avi'}.get(format_)
    if not extension:
        raise ValueError('unsupported_media_container')
    evidence = {'format':format_,'extension':extension,'duration':duration,
                'streams':[{k:s.get(k) for k in ('codec_type','codec_name','width','height','sample_rate')}
                           for s in streams]}
    return evidence


async def async_validate_media(context,path,component,*,ffprobe=None,expected_length=None,timeout=35):
    host=PreviewHost()
    cleanup=host.close
    context.cleanup.append(cleanup)
    try:
        request=host.send('validate',path=str(path),component=component.value,
                          ffprobe=str(ffprobe) if ffprobe is not None else None,
                          expected_length=expected_length,task_id=context.record.spec.task_id,
                          attempt_id=context.record.attempt_id)
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                event=await asyncio.to_thread(host.events.get,True,.1)
            except Empty:
                continue
            if event.get('operation')=='error':
                raise ValueError(event.get('error_code','validation_failed'))
            if event.get('request_id')==request and event.get('operation')=='validated':
                return event['evidence']
        raise TimeoutError('validation_timeout')
    finally:
        cleanup()
        context.cleanup.remove(cleanup)
