from http.cookiejar import Cookie, CookieJar
from pathlib import Path
import tempfile
import threading
import unittest

from bili.auth import AuthConfig,AuthError,AuthService
from bili.models import Platform
from bili.storage import TaskStore


def cookie(domain,name='session',value='test-secret'):
    return Cookie(0,name,value,None,False,domain,True,domain.startswith('.'),'/',True,True,
                  None,True,None,None,{},False)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = TaskStore(self.root/'tasks.sqlite3')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_cookie_file_filters_domains_and_clear_keeps_external_file(self):
        path = self.root/'cookies.txt'
        path.write_text('# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tsession\tdouyin-secret\n'
                        '.xiaohongshu.com\tTRUE\t/\tTRUE\t0\tsession\txhs-secret\n'
                        '.douyin.com\tTRUE\t/\tTRUE\t1\texpired\texpired-secret\n',encoding='utf-8')
        auth = AuthService(self.store)
        auth.configure(Platform.DOUYIN,AuthConfig(mode='cookie_file',cookie_path=str(path)))
        credentials = auth.load(Platform.DOUYIN)
        self.assertNotIn('douyin-secret',repr(credentials))
        self.assertEqual(credentials.header_for('https://www.douyin.com/video/1'),'session=douyin-secret')
        self.assertEqual(credentials.header_for('https://sns-img-bd.xhscdn.com/image'),'')
        self.assertEqual(credentials.header_for('https://www.xiaohongshu.com/explore/id'),'')
        auth.clear(Platform.DOUYIN)
        self.assertTrue(path.exists())
        with self.assertRaises(AuthError):
            auth.check(credentials)
        self.assertEqual(auth.config(Platform.DOUYIN).cookie_path,'')

    def test_guest_never_reads_browser_and_no_cookie_is_written_to_database(self):
        def unexpected(*args):
            self.fail('Guest mode read browser state')
        auth = AuthService(self.store,browser_reader=unexpected)
        self.assertEqual(list(auth.load(Platform.XIAOHONGSHU).cookies),[])
        self.assertNotIn(b'test-secret',(self.root/'tasks.sqlite3').read_bytes())

    def test_clear_during_browser_read_invalidates_result(self):
        started,release = threading.Event(),threading.Event()
        def reader(*args):
            started.set()
            release.wait(3)
            jar=CookieJar()
            jar.set_cookie(cookie('.douyin.com'))
            return jar
        auth = AuthService(self.store,browser_reader=reader)
        auth.configure(Platform.DOUYIN,AuthConfig(mode='browser'))
        errors=[]
        def read():
            try: auth.load(Platform.DOUYIN)
            except AuthError as e: errors.append(e.code)
        thread=threading.Thread(target=read)
        thread.start()
        self.assertTrue(started.wait(3))
        auth.clear(Platform.DOUYIN)
        release.set()
        thread.join(3)
        self.assertEqual(errors,['authentication_revoked'])

    def test_platform_auth_configuration_and_revocation_are_independent(self):
        changes=[]
        auth=AuthService(self.store,revoked=lambda platform,epoch:changes.append(platform))
        auth.configure(Platform.DOUYIN,AuthConfig(mode='browser',browser='edge'))
        self.assertEqual(auth.config(Platform.XIAOHONGSHU).mode,'guest')
        auth.clear(Platform.XIAOHONGSHU)
        self.assertEqual(auth.config(Platform.DOUYIN).mode,'browser')
        self.assertEqual(changes,[Platform.DOUYIN,Platform.XIAOHONGSHU])
