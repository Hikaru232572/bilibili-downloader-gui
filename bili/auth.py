from __future__ import annotations

from dataclasses import asdict, dataclass, field
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path
import os
import re
import threading
from urllib.parse import urlsplit

from .links import ENTRY_DOMAINS, matches_domain
from .models import Platform


class AuthError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuthConfig:
    mode: str = 'guest'
    browser: str = 'edge'
    profile: str = ''
    cookie_path: str = ''

    def __post_init__(self):
        if self.mode not in ('guest','browser','cookie_file'):
            raise ValueError('Invalid authentication mode')
        if self.browser not in ('edge','chrome','firefox','brave','chromium','opera','vivaldi'):
            raise ValueError('Invalid authentication browser')


@dataclass(frozen=True)
class Credentials:
    platform: Platform
    epoch: int
    cookies: CookieJar = field(repr=False)

    def header_for(self, url: str) -> str:
        host = (urlsplit(url).hostname or '').lower()
        if not matches_domain(host, ENTRY_DOMAINS[self.platform]):
            return ''
        path = urlsplit(url).path or '/'
        values = []
        for cookie in self.cookies:
            domain = cookie.domain.lstrip('.').lower()
            matches = host == domain or (cookie.domain_specified and cookie.domain_initial_dot and
                                         host.endswith('.'+domain))
            cookie_path = cookie.path or '/'
            path_matches = path == cookie_path or (path.startswith(cookie_path) and
                           (cookie_path.endswith('/') or path[len(cookie_path):].startswith('/')))
            if matches and path_matches and not cookie.is_expired() and (
                not cookie.secure or urlsplit(url).scheme == 'https'):
                values.append(f'{cookie.name}={cookie.value}')
        return '; '.join(values)


class AuthService:
    def __init__(self, store, *, browser_reader=None, revoked=None):
        self.store = store
        self.browser_reader = browser_reader or self._read_browser
        self.revoked = revoked
        self._lock = threading.RLock()

    def config(self, platform: Platform) -> AuthConfig:
        return AuthConfig(**self.store.get_preference(f'auth:{platform}',{}))

    def epoch(self, platform: Platform) -> int:
        return self.store.get_preference(f'auth_epoch:{platform}',0)

    def snapshot(self, platform: Platform) -> tuple[AuthConfig, int]:
        with self._lock:
            return self.config(platform), self.epoch(platform)

    def configure(self, platform: Platform, config: AuthConfig):
        with self._lock:
            epoch = self.epoch(platform)+1
            self.store.set_preference(f'auth:{platform}',asdict(config))
            self.store.set_preference(f'auth_epoch:{platform}',epoch)
            if self.revoked:
                self.revoked(platform,epoch)

    def clear(self, platform: Platform):
        # Forget the source reference; never delete the user's cookie file or
        # change their browser login session.
        self.configure(platform,AuthConfig())

    def load(self, platform: Platform) -> Credentials:
        with self._lock:
            config,epoch = self.config(platform),self.epoch(platform)
        jar = CookieJar()
        if config.mode == 'cookie_file':
            if not config.cookie_path or not Path(config.cookie_path).is_file():
                raise AuthError('cookie_file_missing')
            source = MozillaCookieJar(config.cookie_path)
            try:
                source.load(ignore_discard=True,ignore_expires=True)
                # Common Netscape exporters encode session cookies as zero;
                # MozillaCookieJar interprets zero as January 1970 instead.
                for cookie in source:
                    if cookie.expires == 0:
                        cookie.expires = None
                        cookie.discard = True
            except Exception:
                raise AuthError('cookie_file_invalid') from None
        elif config.mode == 'browser':
            try:
                source = self.browser_reader(config,platform)
            except AuthError:
                raise
            except Exception as exc:
                error = 'browser_locked_or_requires_admin' if type(exc).__name__ == 'RequiresAdminError' else 'browser_cookie_unavailable'
                raise AuthError(error) from None
        else:
            source = ()
        for cookie in source:
            domain = cookie.domain.lstrip('.').lower()
            if matches_domain(domain,ENTRY_DOMAINS[platform]) and not cookie.is_expired():
                jar.set_cookie(cookie)
        with self._lock:
            if self.epoch(platform) != epoch:
                raise AuthError('authentication_revoked')
        return Credentials(platform,epoch,jar)

    def check(self, credentials: Credentials):
        if credentials.epoch != self.epoch(credentials.platform):
            raise AuthError('authentication_revoked')

    @staticmethod
    def _read_browser(config: AuthConfig, platform: Platform):
        import browser_cookie3
        reader = getattr(browser_cookie3,config.browser)
        domain = ENTRY_DOMAINS[platform][0]
        if config.profile:
            root = Path(config.profile)
            if not root.is_absolute():
                locations={'edge':'Microsoft/Edge/User Data','chrome':'Google/Chrome/User Data',
                    'brave':'BraveSoftware/Brave-Browser/User Data','chromium':'Chromium/User Data',
                    'vivaldi':'Vivaldi/User Data'}
                if config.browser not in locations or not re.fullmatch(r'Default|Profile \d+',config.profile):
                    raise AuthError('browser_profile_missing')
                root=Path(os.environ['LOCALAPPDATA'])/locations[config.browser]/config.profile
            file = root/'cookies.sqlite' if config.browser=='firefox' else root/'Network/Cookies'
            if not file.is_file() and config.browser!='firefox':
                file = root/'Cookies'
            if not file.is_file():
                raise AuthError('browser_profile_missing')
            return reader(cookie_file=str(file),domain_name=domain)
        return reader(domain_name=domain)
