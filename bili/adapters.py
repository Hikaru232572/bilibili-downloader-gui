"""Translate raw, per-item platform data without downloading or renumbering."""
from __future__ import annotations

from .models import Candidate, Component, MediaItem, Platform, Work


def _urls(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    result = []
    for url in value:
        if isinstance(url, str):
            if url.startswith('//'):
                url = 'https:' + url
            # Known platform HTTP media references can be upgraded, but only
            # the transfer service can authorize the resulting CDN target.
            if url.startswith('http://'):
                url = 'https://' + url[7:]
            if url.startswith('https://') and url not in result:
                result.append(url)
    return tuple(result)


def _candidates(urls, component, source, **metadata):
    return tuple(Candidate(url, component, source, **metadata) for url in _urls(urls))


def _positive(value):
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _play_candidates(video, component, source):
    if not isinstance(video, dict):
        return ()
    candidates = []
    rates = video.get('bit_rate') or []
    for rate in rates:
        if not isinstance(rate, dict):
            continue
        address = rate.get('play_addr') or {}
        candidates.extend(_candidates(address.get('url_list'), component, source,
                                      bitrate=_positive(rate.get('bit_rate')),
                                      codec=rate.get('gear_name')))
    address = video.get('play_addr') or {}
    candidates.extend(_candidates(address.get('url_list'), component, source))
    # Do not use a download_addr watermark variant unless playback alternatives
    # are missing. Its watermark state still needs actual evidence.
    download = video.get('download_addr') or {}
    candidates.extend(_candidates(download.get('url_list'), component, source + ':download'))
    return tuple({c.url: c for c in reversed(candidates)}.values())[::-1]


def douyin_work(response: dict, revision: str) -> Work:
    raw = response.get('aweme_detail')
    if not isinstance(raw, dict) or not raw.get('aweme_id'):
        raise ValueError('Response has no identifiable Douyin work')
    work_id = str(raw['aweme_id'])
    items = []
    images = raw.get('images')
    if isinstance(images, list) and images:
        for index, image in enumerate(images, 1):
            image = image if isinstance(image, dict) else {}
            identity = image.get('uri') or image.get('image_id')
            item_id = str(identity) if identity else f'{work_id}:position:{index}'
            still = _candidates(image.get('url_list'), Component.STILL, 'douyin:images.url_list')
            video = image.get('video')
            motion = _play_candidates(video, Component.MOTION, 'douyin:images.video')
            live = bool(video) or bool(image.get('is_live_photo'))
            missing = tuple(c for c, candidates in ((Component.STILL, still), (Component.MOTION, motion))
                            if not candidates and (live or c == Component.STILL))
            items.append(MediaItem(item_id, index, 'live' if live else ('image' if still else 'missing'),
                                   still_candidates=still, motion_candidates=motion,
                                   preview_url=still[0].url if still else None,
                                   missing_components=missing))
        content_type = 'gallery'
        count = _positive(raw.get('image_count'))
    else:
        video = _play_candidates(raw.get('video'), Component.VIDEO, 'douyin:video')
        music = raw.get('music') or {}
        # Background music is not necessarily the video's complete audio track.
        # Extract the video's audio when no explicit full audio-track URL exists.
        audio_data = (raw.get('video') or {}).get('audio') or {}
        audio = _candidates(audio_data.get('url_list'), Component.AUDIO, 'douyin:video.audio')
        items.append(MediaItem(work_id+':video', 1, 'video', video_candidates=video,
                               audio_candidates=audio,
                               missing_components=() if video else (Component.VIDEO,)))
        content_type = 'video'
        count = 1
    author = raw.get('author') or {}
    return Work(Platform.DOUYIN, work_id, content_type, tuple(items),
                title=raw.get('desc') or None, author=author.get('nickname'),
                declared_item_count=count, backend_revision=revision)


def xiaohongshu_work(raw: dict, revision: str, *, auto_urls: dict[int, tuple[str, ...]] | None = None) -> Work:
    work_id = str(raw.get('noteId') or '')
    if not work_id:
        raise ValueError('Response has no identifiable Xiaohongshu note')
    # Ordinary video notes remain outside the confirmed product scope. Do not
    # reinterpret a video cover as a downloadable gallery or live photo.
    if raw.get('type') == 'video':
        return Work(Platform.XIAOHONGSHU, work_id, 'unsupported', (),
                    title=raw.get('title') or None, warnings=('本期不支持小红书普通视频笔记',),
                    backend_revision=revision)
    images = raw.get('imageList')
    if not isinstance(images, list):
        raise ValueError('Response has no image list')
    items = []
    for index, image in enumerate(images, 1):
        image = image if isinstance(image, dict) else {}
        identity = image.get('fileId') or image.get('imageId')
        item_id = str(identity) if identity else f'{work_id}:position:{index}'
        urls = list((auto_urls or {}).get(index, ()))
        for info in image.get('infoList') or []:
            if isinstance(info, dict) and info.get('imageScene') not in ('WB_PRV', 'preview'):
                urls.extend(_urls(info.get('url')))
        urls.extend(_urls(image.get('urlDefault')))
        urls.extend(_urls(image.get('url')))
        still = _candidates(urls, Component.STILL, 'xhs:imageList')
        motion = []
        for codec, streams in (image.get('stream') or {}).items():
            if not isinstance(streams, list):
                continue
            for stream in streams:
                if not isinstance(stream, dict):
                    continue
                sources = list(_urls(stream.get('masterUrl')))
                sources.extend(_urls(stream.get('backupUrls')))
                motion.extend(_candidates(sources, Component.MOTION, 'xhs:imageList.stream',
                                          width=_positive(stream.get('width')),
                                          height=_positive(stream.get('height')), codec=codec,
                                          bitrate=_positive(stream.get('avgBitrate'))))
        live = bool(image.get('livePhoto')) or bool(image.get('isLivePhoto')) or bool(motion)
        missing = tuple(c for c, available in ((Component.STILL, still), (Component.MOTION, motion))
                        if not available and (live or c == Component.STILL))
        previews = _urls(image.get('urlPre') or image.get('urlDefault'))
        items.append(MediaItem(item_id, index, 'live' if live else ('image' if still else 'missing'),
                               still_candidates=still, motion_candidates=tuple(motion),
                               preview_url=previews[0] if previews else (still[0].url if still else None),
                               missing_components=missing))
    return Work(Platform.XIAOHONGSHU, work_id, 'gallery', tuple(items),
                title=raw.get('title') or raw.get('desc') or None,
                author=(raw.get('user') or {}).get('nickname'),
                declared_item_count=_positive(raw.get('imageCount')),
                backend_revision=revision)
