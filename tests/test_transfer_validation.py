import asyncio
from pathlib import Path
import tempfile
import unittest

import httpx
from PIL import Image

from bili.models import Candidate, Component, Platform
from bili.transfer import HTTPTransfer, TransferError
from bili.validation import validate_media


class TransferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'.bili-test.part'
        self.candidate = Candidate('https://sns-img-bd.xhscdn.com/source',Component.STILL,'test')

    def tearDown(self):
        self.directory.cleanup()

    async def download(self, handler, **kwargs):
        transfer = HTTPTransfer(transport=httpx.MockTransport(handler),validator=lambda *a, **k:None)
        return await transfer.download(self.candidate,Platform.XIAOHONGSHU,self.path,**kwargs)

    async def test_server_ignoring_range_restarts_instead_of_appending(self):
        self.path.write_bytes(b'old partial')
        result = await self.download(lambda r:httpx.Response(200,headers={'content-length':'4'},content=b'new!'),
                                     resume_evidence={'etag':'"old"'})
        self.assertEqual(self.path.read_bytes(),b'new!')
        self.assertEqual(result['bytes'],4)

    async def test_matching_range_appends_and_wrong_range_preserves_old_partial(self):
        self.path.write_bytes(b'ab')
        def correct(request):
            self.assertEqual(request.headers['range'],'bytes=2-')
            return httpx.Response(206,headers={'content-range':'bytes 2-3/4','etag':'"same"'},content=b'cd')
        await self.download(correct,resume_evidence={'etag':'"same"'})
        self.assertEqual(self.path.read_bytes(),b'abcd')
        self.path.write_bytes(b'ab')
        with self.assertRaises(TransferError):
            await self.download(lambda r:httpx.Response(206,headers={'content-range':'bytes 1-3/4',
                               'etag':'"same"'},content=b'bcd'),resume_evidence={'etag':'"same"'})
        self.assertEqual(self.path.read_bytes(),b'ab')

    async def test_permission_error_is_classified_and_does_not_write_html(self):
        with self.assertRaises(TransferError) as raised:
            await self.download(lambda r:httpx.Response(403,content=b'<html>login</html>'))
        self.assertEqual(raised.exception.code,'needs_auth_or_permission')
        self.assertFalse(self.path.exists())

    async def test_raw_cookie_headers_are_never_forwarded_to_cdn(self):
        self.candidate = Candidate(self.candidate.url,Component.STILL,'test',
                                   headers=(('Cookie','secret'),('Authorization','secret')))
        def handler(request):
            self.assertNotIn('cookie',request.headers)
            self.assertNotIn('authorization',request.headers)
            return httpx.Response(200,content=b'file')
        await self.download(handler)


class ValidationTests(unittest.TestCase):
    def test_html_and_truncated_images_cannot_be_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'media.part'
            path.write_bytes(b'<html>login</html>')
            with self.assertRaises(ValueError):
                validate_media(path,Component.STILL)
            image = Image.new('RGB',(200,100),'red')
            image.save(path,format='PNG')
            path.write_bytes(path.read_bytes()[:40])
            with self.assertRaises(ValueError):
                validate_media(path,Component.STILL)

    def test_webp_is_validated_in_worker_without_reencoding_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'中文原图.part'
            Image.new('RGB',(123,45),'blue').save(path,format='WEBP')
            original = path.read_bytes()
            evidence = validate_media(path,Component.STILL)
            self.assertEqual(evidence['extension'],'.webp')
            self.assertEqual((evidence['width'],evidence['height']),(123,45))
            self.assertEqual(path.read_bytes(),original)
