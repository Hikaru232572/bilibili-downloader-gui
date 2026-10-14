from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
import subprocess
import sys
import threading
from uuid import uuid4

from .bilibili import stop_process_tree
from .links import classify_url
from .wire import credentials_from_wire,work_from_wire


class ParserError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


class ParserHost:
    def __init__(self,auth):
        self.auth=auth
        self.processes={}
        self.lock=threading.Lock()
        self.closed=False

    async def parse(self,link,*,view_generation=None,context=None):
        result=await self._request(link.platform,'parse',url=link.url,
                                   view_generation=view_generation,context=context)
        try:
            work=work_from_wire(result['work'])
            source=classify_url(result['source_url'])
            if work.platform!=link.platform or source.platform!=link.platform:
                raise ValueError('Platform mismatch')
            if source.work_id and source.work_id!=work.work_id:
                raise ValueError('Work mismatch')
            if link.work_id and link.work_id!=work.work_id:
                raise ValueError('Work mismatch')
        except (ValueError,TypeError,KeyError):
            raise ParserError('parser_worker_failed') from None
        return source.url,work

    async def authenticate(self,platform,*,context=None):
        result=await self._request(platform,'credentials',context=context)
        try:
            return credentials_from_wire(platform,result['auth_epoch'],result['cookies'])
        except (ValueError,TypeError,KeyError):
            raise ParserError('parser_worker_failed') from None

    async def _request(self,platform,operation,*,url=None,view_generation=None,context=None):
        if self.closed:raise ParserError('parser_worker_failed')
        config,epoch=self.auth.snapshot(platform)
        request=dict(protocol_version=1,request_id=str(uuid4()),platform=platform.value,url=url,operation=operation,
                     auth=asdict(config),auth_epoch=epoch,view_generation=view_generation,
                     task_id=context.record.spec.task_id if context else None,
                     attempt_id=context.record.attempt_id if context else None)
        command=([sys.executable,'--parser-worker'] if getattr(sys,'frozen',False)
                 else [sys.executable,'-m','bili.parser_worker'])
        spawn=asyncio.create_task(asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,
             stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        try:process=await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process=await spawn;stop_process_tree(process);await process.wait();raise
        with self.lock:
            self.processes[request['request_id']]=(platform,process)
        cleanup=lambda:stop_process_tree(process)
        if context:context.cleanup.append(cleanup)
        try:
            if self.closed or self.auth.epoch(platform)!=epoch:
                raise ParserError('authentication_revoked')
            data=json.dumps(request,ensure_ascii=False).encode('utf-8')
            try:stdout,_=await asyncio.wait_for(process.communicate(data),timeout=60)
            except asyncio.TimeoutError:raise ParserError('parser_timeout') from None
            if self.auth.epoch(platform)!=epoch:raise ParserError('authentication_revoked')
            if process.returncode!=0:raise ParserError('parser_worker_failed')
            try:result=json.loads(stdout)
            except ValueError:raise ParserError('parser_worker_failed') from None
            keys=('request_id','task_id','attempt_id','view_generation','auth_epoch')
            if not isinstance(result,dict) or result.get('protocol_version')!=1 or any(result.get(key)!=request[key] for key in keys):
                raise ParserError('parser_worker_failed')
            if result.get('operation')=='error':raise ParserError(result['error_code'])
            if result.get('operation')!=('credentials' if operation=='credentials' else 'parsed'):
                raise ParserError('parser_worker_failed')
            return result
        finally:
            cleanup()
            if process.returncode is None:await process.wait()
            with self.lock:
                self.processes.pop(request['request_id'],None)
            if context:context.cleanup.remove(cleanup)

    def revoke(self,platform=None):
        with self.lock:
            processes=tuple(self.processes.values())
        for current,process in processes:
            if platform is None or current==platform:stop_process_tree(process)

    def close(self):
        self.closed=True
        self.revoke()
