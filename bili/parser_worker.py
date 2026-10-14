"""One-request parser process. Credential/config input and Work output use pipes."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import sys
from urllib.parse import urljoin,urlsplit

from .auth import AuthConfig,AuthService
from .adapters import douyin_work,xiaohongshu_work
from .links import classify_url,validate_target
from .models import Platform
from .wire import credentials_to_wire,work_to_wire


class ParseFailure(Exception):
    def __init__(self,code):self.code=code


class RequestPreferences:
    def __init__(self,platform,config,epoch):
        self.values={f'auth:{platform}':config,f'auth_epoch:{platform}':epoch}
    def get_preference(self,key,default=None):return self.values.get(key,default)


async def fetch_page(session,url,platform,credentials,headers=None,*,resolve_only=False):
    for jump in range(6):
        validate_target(url,platform)
        if resolve_only and classify_url(url).work_id:
            return url,None
        request_headers={key:value for key,value in (headers or {}).items()
                         if key.lower() not in ('cookie','authorization','proxy-authorization')}
        cookie=credentials.header_for(url)
        if cookie:request_headers['Cookie']=cookie
        response=await session.get(url,headers=request_headers,allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            if jump==5 or not response.headers.get('location'):raise ParseFailure('invalid_media_redirect')
            url=urljoin(url,response.headers['location'])
            continue
        if response.status_code in (401,403):raise ParseFailure('needs_auth_or_permission')
        if response.status_code==429:raise ParseFailure('rate_limited')
        if response.status_code==404:raise ParseFailure('work_unavailable')
        if response.status_code!=200:raise ParseFailure('http_failure')
        return url,response
    raise ParseFailure('invalid_media_redirect')


async def parse_xhs(session,link,credentials):
    from bili_vendor.xhs.converter import Converter
    from bili_vendor.xhs.namespace import Namespace
    from bili_vendor.xhs.image import Image
    final,response=await fetch_page(session,link.url,link.platform,credentials,
        {'accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
         'accept-language':'zh-CN,zh;q=0.9,en;q=0.8'})
    target=classify_url(final)
    if not target.work_id:raise ParseFailure('work_identity_unavailable')
    converter=Converter()
    data=converter._convert_object(converter._extract_object(response.text))
    if not isinstance(data,dict):raise ParseFailure('needs_auth_or_permission')
    raw=Converter.deep_get(data,('note','noteDetailMap',target.work_id,'note'))
    if not raw:
        raw=converter._filter_object(data)
    if not isinstance(raw,dict) or not raw.get('noteId'):raise ParseFailure('needs_auth_or_permission')
    if raw['noteId']!=target.work_id:raise ParseFailure('work_identity_mismatch')
    urls={}
    for index,image in enumerate(raw.get('imageList') or [],1):
        if not isinstance(image,dict):continue
        try:
            still,_=Image.get_image_link(Namespace({'imageList':[image]}),'auto')
            urls[index]=tuple(u for u in still if u and urlsplit(u).path.strip('/'))
        except (ValueError,TypeError,AttributeError):
            pass
    work=xiaohongshu_work(raw,'xhs:3261312721f0b37c705ba6515885bc7f34349f2f',auto_urls=urls)
    return final,work


async def parse_douyin(session,link,credentials):
    if not link.work_id:
        final,_=await fetch_page(session,link.url,link.platform,credentials,resolve_only=True)
        link=classify_url(final)
    if not link.work_id:raise ParseFailure('work_identity_unavailable')
    # Do not read upstream per-user/project configs, launch its CLI, or create
    # an implicit guest token request outside this single-work operation.
    import f2.utils.config.user_config as user_config
    user_config.user_config_paths=lambda *args,**kwargs:[]
    from f2.apps.douyin.model import PostDetail
    from f2.apps.douyin.utils import ABogusManager,ClientConfManager,GatewayHeaderManager
    from f2.apps.douyin.api import DouyinAPIEndpoints
    from http.cookies import SimpleCookie
    base='https://www.douyin.com/aweme/v1/web/aweme/detail/'
    cookie=credentials.header_for(base)
    parsed=SimpleCookie();parsed.load(cookie)
    token=parsed['msToken'].value if 'msToken' in parsed else ''
    params=PostDetail(aweme_id=link.work_id,msToken=token)
    headers=GatewayHeaderManager.merge_headers(ClientConfManager.headers(),cookie)
    endpoint=ABogusManager.model_2_endpoint(headers.get('User-Agent'),DouyinAPIEndpoints.POST_DETAIL,params.model_dump())
    _,response=await fetch_page(session,endpoint,Platform.DOUYIN,credentials,headers)
    if not response.content:raise ParseFailure('needs_auth_or_permission')
    try:data=response.json()
    except ValueError:raise ParseFailure('needs_auth_or_permission') from None
    if not isinstance(data,dict) or not isinstance(data.get('aweme_detail'),dict):
        raise ParseFailure('work_unavailable')
    work=douyin_work(data,'f2:f6be8c0ffba9a127075bbeafe4838716650b6325')
    if work.work_id!=link.work_id:raise ParseFailure('work_identity_mismatch')
    return link.url,work


async def parse(request):
    from curl_cffi.requests import AsyncSession
    if request.get('protocol_version')!=1:raise ParseFailure('parser_worker_failed')
    platform=Platform(request['platform'])
    preferences=RequestPreferences(platform,request.get('auth',{}),request.get('auth_epoch',0))
    credentials=AuthService(preferences).load(platform)
    if request.get('operation')=='credentials':
        return {'cookies':credentials_to_wire(credentials)}
    link=classify_url(request['url'])
    if link.platform!=platform:raise ParseFailure('work_identity_mismatch')
    async with AsyncSession(impersonate='chrome146',verify=True,timeout=15,allow_redirects=False) as session:
        if link.platform==Platform.XIAOHONGSHU:
            final,work=await parse_xhs(session,link,credentials)
        elif link.platform==Platform.DOUYIN:
            final,work=await parse_douyin(session,link,credentials)
        else:raise ParseFailure('unsupported_executor_platform')
    return {'source_url':final,'work':work_to_wire(work)}


def main():
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    request=json.loads(sys.stdin.read(1024*1024))
    result={key:request.get(key) for key in ('request_id','task_id','attempt_id','view_generation','auth_epoch')}
    result['protocol_version']=1
    capture=io.StringIO()
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
            payload=asyncio.run(parse(request))
        result.update(operation='credentials' if request.get('operation')=='credentials' else 'parsed',**payload)
    except Exception as exc:
        result.update(operation='error',error_code=getattr(exc,'code',None) or type(exc).__name__)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
