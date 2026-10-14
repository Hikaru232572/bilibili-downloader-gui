"""Native image/player isolation. stdin/stdout are private JSON-line pipes."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
import time


PROTOCOL_VERSION = 1


def emit(request, operation, **payload):
    message = {key: request.get(key) for key in ('request_id','task_id','attempt_id','view_generation')}
    message.update(protocol_version=PROTOCOL_VERSION, operation=operation, **payload)
    print(json.dumps(message, ensure_ascii=False), flush=True)


def decode_image(request):
    from PIL import Image, ImageOps
    import pillow_heif
    pillow_heif.register_heif_opener()
    path = Path(request['path'])
    if not path.is_file():
        raise ValueError('missing_file')
    limit = min(max(int(request.get('max_size', 1600)), 1), 8192)
    with Image.open(path) as image:
        source_size, source_format = image.size, image.format
        image.load()
        preview = ImageOps.exif_transpose(image)
        preview.thumbnail((limit, limit))
        buffer = BytesIO()
        preview.save(buffer, format='PNG')
        return {'width':source_size[0], 'height':source_size[1], 'format':source_format,
                'display_width':preview.width, 'display_height':preview.height,
                'image':base64.b64encode(buffer.getvalue()).decode('ascii')}


def main():
    # Windows' inherited pipe encoding can otherwise be the local ANSI page.
    # The private protocol is UTF-8, including Chinese paths and titles.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    requests = Queue()
    def read():
        try:
            while True:
                line = sys.stdin.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    break
                requests.put(json.loads(line))
        finally:
            requests.put(None)
    threading.Thread(target=read, daemon=True).start()
    instance = player = media = None
    dll_handle = None
    current = {}
    last_report = 0
    try:
        while True:
            try:
                request = requests.get(timeout=0.1)
            except Empty:
                request = {}
            if request is None or request.get('operation') == 'close':
                break
            if request:
                operation = request.get('operation')
                try:
                    if request.get('protocol_version') != PROTOCOL_VERSION:
                        raise ValueError('protocol_version')
                    if operation == 'decode':
                        emit(request, 'decoded', **decode_image(request))
                    elif operation == 'validate':
                        from .validation import validate_media
                        from .models import Component
                        evidence=validate_media(Path(request['path']),Component(request['component']),
                            ffprobe=Path(request['ffprobe']) if request.get('ffprobe') else None,
                            expected_length=request.get('expected_length'),image_worker=False)
                        emit(request,'validated',evidence=evidence)
                    elif operation == 'load':
                        path = Path(request['path']).resolve()
                        if not path.is_file():
                            raise ValueError('missing_file')
                        if instance is None:
                            directory = Path(request['libvlc_dir']).resolve()
                            if not (directory / 'libvlc.dll').is_file():
                                raise ValueError('missing_libvlc')
                            dll_handle = os.add_dll_directory(str(directory))
                            os.environ['PYTHON_VLC_LIB_PATH'] = str(directory / 'libvlc.dll')
                            os.environ['VLC_PLUGIN_PATH'] = str(directory / 'plugins')
                            import vlc
                            instance = vlc.Instance('--no-video-title-show', '--no-metadata-network-access')
                            player = instance.media_player_new()
                        player.stop()
                        if media is not None:
                            media.release()
                        media = instance.media_new_path(str(path))
                        player.set_media(media)
                        player.set_hwnd(int(request['hwnd']))
                        player.audio_set_volume(int(request.get('volume', 80)))
                        if player.play() != 0:
                            raise ValueError('play_failed')
                        current = request
                        emit(request, 'loaded')
                    elif operation in ('play', 'pause', 'replay', 'stop'):
                        if player is None:
                            raise ValueError('no_player')
                        if operation == 'play':
                            player.set_pause(0)
                        elif operation == 'pause':
                            player.set_pause(1)
                        elif operation == 'replay':
                            player.stop()
                            player.play()
                        else:
                            player.stop()
                        emit(request, 'ack')
                    else:
                        raise ValueError('unsupported_operation')
                except Exception as exc:
                    from .messages import ERRORS
                    safe = str(exc) if isinstance(exc, ValueError) and str(exc) in set(ERRORS) | {
                        'missing_file','missing_libvlc','play_failed','no_player',
                        'protocol_version','unsupported_operation'} else type(exc).__name__
                    emit(request, 'error', error_code=safe)
            if player is not None and time.monotonic() - last_report >= 0.25:
                last_report = time.monotonic()
                emit(current, 'playback', state=str(player.get_state()),
                     time_ms=player.get_time(), length_ms=player.get_length(),
                     video_size=player.video_get_size(), audio_tracks=player.audio_get_track_count())
    finally:
        if player is not None:
            player.stop()
            player.release()
        if media is not None:
            media.release()
        if instance is not None:
            instance.release()
        if dll_handle is not None:
            dll_handle.close()


if __name__ == '__main__':
    main()
