"""Private IPC only. Work payloads contain transient signed resource URLs."""
from dataclasses import asdict
from http.cookiejar import Cookie,CookieJar

from .auth import Credentials
from .links import ENTRY_DOMAINS,matches_domain

from .models import Candidate,Component,MediaItem,Platform,Watermark,Work


def work_to_wire(work):
    return asdict(work)


def work_from_wire(payload):
    payload=dict(payload)
    payload['platform']=Platform(payload['platform'])
    items=[]
    for raw in payload['items']:
        raw=dict(raw)
        for field in ('still_candidates','motion_candidates','video_candidates','audio_candidates'):
            candidates=[]
            for candidate in raw.get(field,[]):
                candidate=dict(candidate)
                candidate['component']=Component(candidate['component'])
                candidate['watermark']=Watermark(candidate.get('watermark','unknown'))
                candidate['headers']=tuple(tuple(v) for v in candidate.get('headers',[]))
                candidates.append(Candidate(**candidate))
            raw[field]=tuple(candidates)
        raw['missing_components']=tuple(Component(v) for v in raw.get('missing_components',[]))
        raw['warnings']=tuple(raw.get('warnings',[]))
        items.append(MediaItem(**raw))
    payload['items']=tuple(items)
    payload['warnings']=tuple(payload.get('warnings',[]))
    return Work(**payload)


COOKIE_FIELDS=('version','name','value','port','port_specified','domain','domain_specified',
               'domain_initial_dot','path','path_specified','secure','expires','discard',
               'comment','comment_url','rfc2109')


def credentials_to_wire(credentials):
    return [dict({key:getattr(cookie,key) for key in COOKIE_FIELDS},rest=cookie._rest)
            for cookie in credentials.cookies]


def credentials_from_wire(platform,epoch,payload):
    jar=CookieJar()
    for raw in payload:
        cookie=Cookie(**raw)
        if (matches_domain(cookie.domain.lstrip('.').lower(),ENTRY_DOMAINS[platform])
                and not cookie.is_expired()):
            jar.set_cookie(cookie)
    return Credentials(platform,epoch,jar)
