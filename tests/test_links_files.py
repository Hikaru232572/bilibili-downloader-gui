from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from bili.files import OutputFiles, clean_name, name_stem, utf16_length
from bili.links import classify_url, extract_links, resolve_link, validate_target
from bili.models import Platform


class LinkTests(unittest.TestCase):
    def test_share_text_and_required_queries_survive(self):
        links = extract_links('文案 https://v.douyin.com/jSILL12zIqI/ 复制；'
                              'https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=abc%2Bdef&xsec_source=pc_share')
        self.assertEqual(len(links), 2)
        self.assertEqual(links[1].platform, Platform.XIAOHONGSHU)
        self.assertIn('xsec_token=abc%2Bdef', links[1].url)

    def test_bv_and_page_dedup_does_not_collapse_distinct_parts(self):
        links = extract_links('BV1RXhk6REwc https://www.bilibili.com/video/BV1RXhk6REwc?p=1 '
                              'https://www.bilibili.com/video/BV1RXhk6REwc?p=2')
        self.assertEqual(len(links), 2)

    def test_real_douyin_intermediate_share_domain(self):
        link = classify_url('https://www.iesdouyin.com/share/note/7694946220390201714/')
        self.assertEqual(link.work_id, '7694946220390201714')

    def test_redirect_is_validated_before_next_request(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={'location':'http://127.0.0.1/private'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch('bili.links.validate_target', side_effect=[None, ValueError('private')]):
                with self.assertRaises(ValueError):
                    resolve_link(classify_url('https://v.douyin.com/sample/'), client=client)
        self.assertEqual(len(calls), 1)

    def test_host_suffix_and_private_dns_are_rejected(self):
        for url in ('https://douyin.com.evil.invalid/video/123', 'https://www.douyin.com@evil.invalid/video/123'):
            with self.assertRaises(ValueError):
                classify_url(url)
        with self.assertRaises(ValueError):
            validate_target('https://www.douyin.com/video/123', Platform.DOUYIN,
                            resolver=lambda *a, **kw: [(2,1,6,'',('127.0.0.1',443))])


class FileTests(unittest.TestCase):
    def test_external_collision_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            files = OutputFiles()
            reservation = files.reserve(Path(directory), '标题_ID_001', {'still':'.heic','motion':'.mp4'})
            destination = reservation.path('still')
            destination.write_bytes(b'external media')
            temporary = files.temporary(Path(directory), str(uuid4()))
            temporary.write_bytes(b'new media')
            with self.assertRaises(FileExistsError):
                files.commit(temporary, destination)
            self.assertEqual(destination.read_bytes(), b'external media')
            self.assertEqual(temporary.read_bytes(), b'new media')

    def test_pair_shares_suffix_and_reservation_reuses_neither_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'title_ID_003_still.heic').write_bytes(b'existing')
            files = OutputFiles()
            first = files.reserve(root, 'title_ID_003', {'still':'.heic','motion':'.mp4'})
            second = files.reserve(root, 'title_ID_003', {'still':'.heic','motion':'.mp4'})
            self.assertEqual(first.stem, 'title_ID_003 (1)')
            self.assertEqual(second.stem, 'title_ID_003 (2)')
            self.assertEqual(first.path('motion').stem, first.stem+'_motion')

    def test_successful_commit_and_cleanup_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = str(uuid4())
            temporary = OutputFiles.temporary(root, identity)
            temporary.write_bytes(b'validated media')
            destination = root/'output.webp'
            OutputFiles.commit(temporary, destination)
            self.assertTrue(destination.exists())
            self.assertFalse(temporary.exists())
            with self.assertRaises(ValueError):
                OutputFiles.remove_temporary(destination, root, identity)
            self.assertTrue(destination.exists())

    def test_name_safety_and_full_path_budget(self):
        self.assertEqual(clean_name('CON.txt'), '_CON.txt')
        self.assertEqual(clean_name('a/b: .'), 'a_b_')
        with tempfile.TemporaryDirectory() as directory:
            name = name_stem(Path(directory), '小红书', '中文😀'*200, 'ID', 3)
            self.assertLessEqual(utf16_length(str(Path(directory)/name))+32, 240)
            self.assertTrue(name.endswith('_ID_003'))
