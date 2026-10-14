import asyncio
from http.cookiejar import Cookie, CookieJar
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bili.adapters import xiaohongshu_work
from bili.auth import AuthConfig, AuthService, Credentials
from bili.links import classify_url, validate_target
from bili.models import Platform
from bili.network import PublicResolver
from bili.parser import ParserError, ParserHost
from bili.parser_worker import ParseFailure, fetch_page, parse_xhs
from bili.wire import work_from_wire, work_to_wire


def address(ip):
    return [(2, 1, 6, '', (ip, 443))]


class PublicResolverTests(unittest.TestCase):
    def test_fake_ip_uses_public_verification_and_short_cache(self):
        lookup = unittest.mock.Mock(return_value=['8.8.8.8'])
        resolver = PublicResolver(lambda *a, **k: address('198.18.0.3'), lookup)
        self.assertEqual(resolver('www.douyin.com', 443)[0][4][0], '8.8.8.8')
        resolver('www.douyin.com', 443)
        lookup.assert_called_once_with('www.douyin.com')

    def test_private_and_mixed_dns_never_use_fallback(self):
        for ips in (['127.0.0.1'], ['198.18.0.3', '10.0.0.1']):
            lookup = unittest.mock.Mock()
            resolver = PublicResolver(lambda *a, **k: sum([address(ip) for ip in ips], []), lookup)
            with self.assertRaises(ValueError):
                validate_target('https://www.douyin.com/video/123', Platform.DOUYIN, resolver=resolver)
            lookup.assert_not_called()

    def test_public_dns_result_must_be_nonempty_and_all_public(self):
        for ips in ([], ['8.8.8.8', '127.0.0.1']):
            resolver = PublicResolver(lambda *a, **k: address('198.18.0.3'), lambda host: ips)
            with self.assertRaises(ValueError):
                resolver('www.douyin.com', 443)

    def test_unlisted_domain_is_rejected_before_resolution(self):
        resolver = unittest.mock.Mock()
        with self.assertRaises(ValueError):
            validate_target('https://evil.invalid/file', Platform.DOUYIN, resolver=resolver)
        resolver.assert_not_called()


def credentials():
    jar = CookieJar()
    jar.set_cookie(Cookie(0, 'session', 'private-cookie', None, False, '.douyin.com', True, True,
                         '/', True, True, None, True, None, None, {}, False))
    return Credentials(Platform.DOUYIN, 0, jar)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_recomputes_cookie_scope_and_discards_raw_auth_headers(self):
        session = SimpleNamespace(get=AsyncMock(side_effect=[
            SimpleNamespace(status_code=302, headers={'location':'https://www.iesdouyin.com/landing'}),
            SimpleNamespace(status_code=200, headers={})]))
        with patch('bili.parser_worker.validate_target'):
            await fetch_page(session, 'https://www.douyin.com/initial', Platform.DOUYIN,
                             credentials(), {'Cookie':'wrong', 'Authorization':'wrong'})
        requests = session.get.call_args_list
        self.assertEqual(requests[0].kwargs['headers']['Cookie'], 'session=private-cookie')
        self.assertNotIn('Cookie', requests[1].kwargs['headers'])
        self.assertNotIn('Authorization', requests[0].kwargs['headers'])

    async def test_resolving_share_stops_before_fetching_identified_page(self):
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
            status_code=302, headers={'location':'https://www.iesdouyin.com/share/note/123/'})))
        with patch('bili.parser_worker.validate_target'):
            url, response = await fetch_page(session, 'https://v.douyin.com/share/', Platform.DOUYIN,
                                            credentials(), resolve_only=True)
        self.assertTrue(url.endswith('/123/'))
        self.assertIsNone(response)
        self.assertEqual(session.get.await_count, 1)

    async def test_redirect_validation_precedes_second_request(self):
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
            status_code=302, headers={'location':'https://evil.invalid/file'})))
        with patch('bili.parser_worker.validate_target', side_effect=[None, ValueError('blocked')]):
            with self.assertRaises(ValueError):
                await fetch_page(session, 'https://v.douyin.com/share/', Platform.DOUYIN, credentials())
        self.assertEqual(session.get.await_count, 1)

    async def test_target_note_wins_over_last_note_and_missing_image_stays_missing(self):
        note_id = '0123456789abcdef01234567'
        raw = {'noteId':note_id, 'type':'normal', 'imageList':[{}, {
            'fileId':'b', 'urlDefault':'https://sns-img-bd.xhscdn.com/token/image-b'}]}
        state = {'note':{'noteDetailMap':{note_id:{'note':raw}, 'wrong':{
            'note':{'noteId':'wrong', 'type':'normal', 'imageList':[]}}}}}
        html = '<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>'
        link = classify_url('https://www.xiaohongshu.com/explore/'+note_id)
        with patch('bili.parser_worker.fetch_page', new=AsyncMock(return_value=(
                link.url, SimpleNamespace(text=html)))):
            _, work = await parse_xhs(None, link, None)
        self.assertEqual(work.work_id, note_id)
        self.assertEqual(len(work.items), 2)
        self.assertEqual(work.items[0].still_candidates, ())
        self.assertEqual(work.items[1].item_id, 'b')


class Preferences:
    def get_preference(self, key, default=None):
        return default


class FakeProcess:
    pid = 1
    returncode = 0

    def __init__(self, mutate=None):
        self.mutate = mutate
        self.stdin_data = None

    async def communicate(self, data):
        self.stdin_data = data
        request = json.loads(data)
        work = xiaohongshu_work({'noteId':'0123456789abcdef01234567', 'type':'normal',
                               'imageList':[{'fileId':'a'}]}, 'test')
        result = {k:request[k] for k in ('request_id','task_id','attempt_id','view_generation','auth_epoch')}
        result.update(protocol_version=1, operation='parsed', work=work_to_wire(work),
                      source_url='https://www.xiaohongshu.com/explore/'+work.work_id)
        if self.mutate:
            self.mutate(result)
        return json.dumps(result).encode(), None

    async def wait(self):
        return self.returncode


class ParserHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_pipe_roundtrip_and_no_url_in_process_arguments(self):
        host = ParserHost(AuthService(Preferences()))
        process = FakeProcess()
        with patch('bili.parser.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)) as spawn:
            _, work = await host.parse(classify_url('https://xhslink.cn/o/secret-token'), view_generation=8)
        self.assertEqual(work.work_id, '0123456789abcdef01234567')
        self.assertNotIn('secret-token', repr(spawn.call_args))
        self.assertEqual(json.loads(process.stdin_data)['view_generation'], 8)
        self.assertEqual(host.processes, {})

    async def test_wrong_correlation_and_wrong_source_identity_are_rejected(self):
        for mutate in (lambda r:r.update(view_generation=99),
                       lambda r:r.update(source_url='https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01')):
            host = ParserHost(AuthService(Preferences()))
            with patch('bili.parser.asyncio.create_subprocess_exec', new=AsyncMock(return_value=FakeProcess(mutate))):
                with self.assertRaises(ParserError):
                    await host.parse(classify_url('https://xhslink.cn/o/secret-token'), view_generation=8)

    async def test_revoked_authentication_rejects_late_result(self):
        auth = AuthService(Preferences())
        host = ParserHost(auth)
        process = FakeProcess(lambda r:setattr(auth, 'epoch', lambda platform:1))
        with patch('bili.parser.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)):
            with self.assertRaises(ParserError) as error:
                await host.parse(classify_url('https://xhslink.cn/o/secret-token'))
        self.assertEqual(error.exception.code, 'authentication_revoked')


class WireTests(unittest.TestCase):
    def test_unicode_sparse_live_gallery_preserves_candidates_and_pair_identity(self):
        work = xiaohongshu_work({'noteId':'test', 'title':'中文标题', 'type':'normal', 'imageList':[
            {'fileId':'a', 'urlDefault':'https://sns-img-bd.xhscdn.com/a', 'livePhoto':True},
            {'fileId':'b', 'stream':{'h265':[{'masterUrl':'https://sns-video.xhscdn.com/b.mp4'}]}}]}, 'test')
        self.assertEqual(work_from_wire(json.loads(json.dumps(work_to_wire(work)))), work)


if __name__ == '__main__':
    unittest.main()
