from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
import socket
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from .models import Platform
from .network import public_resolver


ENTRY_DOMAINS = {
    Platform.BILIBILI: ('bilibili.com', 'b23.tv'),
    Platform.DOUYIN: ('douyin.com', 'iesdouyin.com'),
    Platform.XIAOHONGSHU: ('xiaohongshu.com', 'rednote.com', 'xhslink.com', 'xhslink.cn'),
}
MEDIA_DOMAINS = {
    Platform.BILIBILI: ('bilivideo.com', 'bilivideo.cn', 'biliimg.com', 'hdslb.com'),
    Platform.DOUYIN: ('douyinvod.com', 'douyinpic.com', 'byteimg.com', 'ibytedtos.com',
                      'amemv.com', 'ixigua.com', 'snssdk.com', 'bytedance.com', 'douyin.com'),
    Platform.XIAOHONGSHU: ('xhscdn.com', 'xiaohongshu.com', 'rednote.com'),
}
URL_PATTERN = re.compile(r'https?://[^\s\u0080-\uffff<>"\x27`]+', re.IGNORECASE | re.ASCII)
BV_PATTERN = re.compile(r'(?<![A-Za-z0-9])BV[0-9A-Za-z]{10}(?![A-Za-z0-9])', re.IGNORECASE)


@dataclass(frozen=True)
class Link:
    platform: Platform
    url: str = field(repr=False)
    work_id: str | None = None
    identity: str | None = None


def matches_domain(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith('.' + d) for d in domains)


def classify_url(url: str) -> Link:
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().rstrip('.')
    if parts.scheme.lower() not in ('https', 'http') or parts.username or parts.password:
        raise ValueError('Unsupported link protocol or embedded credentials')
    if parts.port not in (None, 80 if parts.scheme == 'http' else 443):
        raise ValueError('Unsupported link port')
    platform = next((p for p, domains in ENTRY_DOMAINS.items() if matches_domain(host, domains)), None)
    if platform is None:
        raise ValueError('Unsupported platform domain')
    normalized = urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, ''))
    work_id = None
    identity = None
    if platform == Platform.BILIBILI:
        match = re.search(r'/video/(BV[0-9A-Za-z]{10}|av\d+)', parts.path, re.IGNORECASE)
        if match:
            work_id = match[1]
            page = parse_qs(parts.query).get('p', ['1'])[0]
            identity = f'{platform}:{work_id.lower()}:p{page}'
    elif platform == Platform.DOUYIN:
        match = re.search(r'/(?:video|note|slides)/(\d+)', parts.path)
        work_id = match[1] if match else parse_qs(parts.query).get('modal_id', [None])[0]
        identity = f'{platform}:{work_id}' if work_id else None
    else:
        match = re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})', parts.path)
        if not match:
            match = re.search(r'/user/profile/[a-fA-F0-9]{24}/([a-fA-F0-9]{24})', parts.path)
        work_id = match[1].lower() if match else None
        identity = f'{platform}:{work_id}' if work_id else None
    return Link(platform, normalized, work_id, identity)


def extract_links(text: str) -> tuple[Link, ...]:
    links = []
    seen = set()
    spans = []
    for match in URL_PATTERN.finditer(text):
        spans.append(match.span())
        url = match[0].rstrip('.,;!?)]}')
        try:
            link = classify_url(url)
        except ValueError:
            continue
        key = link.identity or link.url
        if key not in seen:
            links.append(link)
            seen.add(key)
    for match in BV_PATTERN.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        bv = 'BV' + match[0][2:]
        link = classify_url('https://www.bilibili.com/video/' + bv)
        if link.identity not in seen:
            links.append(link)
            seen.add(link.identity)
    return tuple(links)


def validate_target(url: str, platform: Platform, *, media: bool = False, resolver=public_resolver):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().rstrip('.')
    if parts.username or parts.password or not host:
        raise ValueError('Invalid target')
    if parts.scheme != 'https' and (media or parts.scheme != 'http'):
        raise ValueError('Media must use HTTPS')
    if parts.port not in (None, 443 if parts.scheme == 'https' else 80):
        raise ValueError('Invalid target port')
    domains = MEDIA_DOMAINS[platform] if media else ENTRY_DOMAINS[platform]
    if not matches_domain(host, domains):
        raise ValueError('Target domain is outside platform allowlist')
    addresses = resolver(host, parts.port or (443 if parts.scheme == 'https' else 80),
                         type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Target resolves to a non-public address')
    return url


def resolve_link(link: Link, *, client=None, max_redirects=5) -> Link:
    import httpx
    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False)
    url = link.url
    try:
        for step in range(max_redirects + 1):
            validate_target(url, link.platform)
            if classify_url(url).work_id:
                # Valid direct works do not need a page request to route.
                return classify_url(url)
            with client.stream('GET', url, follow_redirects=False,
                               headers={'User-Agent': 'Mozilla/5.0'}) as response:
                if response.is_redirect:
                    if step == max_redirects:
                        raise ValueError('Too many share redirects')
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('Share redirect has no target')
                    url = urljoin(url, location)
                else:
                    response.raise_for_status()
                    raise ValueError('Link does not identify a supported single work')
        raise ValueError('Share link resolution failed')
    finally:
        if owns_client:
            client.close()
