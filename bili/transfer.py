from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .links import validate_target
from .models import Candidate, Platform


class TransferError(Exception):
    def __init__(self, code: str, *, status=None):
        super().__init__(code)
        self.code = code
        self.status = status


class HTTPTransfer:
    def __init__(self, *, transport=None, validator=validate_target):
        self.transport = transport
        self.validator = validator

    async def download(self, candidate: Candidate, platform: Platform, temporary: Path,
                       *, progress=None, resume_evidence: dict | None = None, cookies=None) -> dict:
        temporary = Path(temporary)
        if temporary.is_symlink():
            raise TransferError('unexpected_temporary_link')
        temporary.parent.mkdir(parents=True, exist_ok=True)
        headers = dict(candidate.headers)
        headers = {k:v for k,v in headers.items() if k.lower() not in ('cookie','authorization','proxy-authorization')}
        headers['Accept-Encoding'] = 'identity'
        offset = temporary.stat().st_size if temporary.exists() else 0
        validator = (resume_evidence or {}).get('etag') or (resume_evidence or {}).get('last_modified')
        if offset and validator:
            headers['Range'] = f'bytes={offset}-'
            headers['If-Range'] = validator
        else:
            offset = 0
        url = candidate.url
        async with httpx.AsyncClient(timeout=httpx.Timeout(30,connect=10), transport=self.transport,
                                     follow_redirects=False, cookies=cookies) as client:
            for jump in range(6):
                await asyncio.to_thread(self.validator, url, platform, media=True)
                async with client.stream('GET',url,headers=headers) as response:
                    if response.is_redirect:
                        if jump == 5 or not response.headers.get('location'):
                            raise TransferError('invalid_media_redirect')
                        url = urljoin(url,response.headers['location'])
                        continue
                    if response.status_code in (401,403):
                        raise TransferError('needs_auth_or_permission',status=response.status_code)
                    if response.status_code == 429:
                        raise TransferError('rate_limited',status=429)
                    if response.status_code not in (200,206):
                        raise TransferError('http_failure',status=response.status_code)
                    etag = response.headers.get('etag')
                    modified = response.headers.get('last-modified')
                    response_range = response.headers.get('content-range','')
                    expected_total = None
                    if response.status_code == 206:
                        import re
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response_range)
                        if (not match or int(match[1]) != offset or not offset or
                            int(match[2]) < offset or int(match[2]) >= int(match[3]) or
                            validator not in (etag,modified)):
                            raise TransferError('invalid_range_response')
                        expected_total = int(match[3])
                    else:
                        offset = 0
                        if response.headers.get('content-length'):
                            expected_total = int(response.headers['content-length'])
                    if response.headers.get('content-encoding','identity').lower() != 'identity':
                        raise TransferError('unexpected_content_encoding')
                    transferred = offset
                    mode = 'ab' if offset else 'wb'
                    with temporary.open(mode) as stream:
                        # Identity encoding is required above, so these bytes
                        # are also the original media response bytes.
                        async for chunk in response.aiter_bytes():
                            stream.write(chunk)
                            transferred += len(chunk)
                            if progress:
                                returned = progress(transferred,expected_total)
                                if inspect.isawaitable(returned):
                                    await returned
                        stream.flush()
                    if expected_total is not None and transferred != expected_total:
                        raise TransferError('response_length_mismatch')
                    return {'bytes':transferred,'total':expected_total,'etag':etag,
                            'last_modified':modified}
        raise TransferError('media_transfer_failed')
