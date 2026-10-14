"""Public target resolution with a constrained fake-IP tunnel fallback."""
from __future__ import annotations

import ipaddress
import socket
import threading
import time

import httpx


FAKE_IP=ipaddress.ip_network('198.18.0.0/15')


class PublicResolver:
    def __init__(self,system=socket.getaddrinfo,lookup=None):
        self.system=system
        self.lookup=lookup or self._lookup
        self.cache={}
        self.lock=threading.Lock()

    @staticmethod
    def _lookup(host):
        response=httpx.get('https://dns.google/resolve',params={'name':host,'type':'A'},
                           timeout=5,follow_redirects=False)
        response.raise_for_status()
        data=response.json()
        if data.get('Status')!=0:
            raise ValueError('Public DNS verification failed')
        return [a['data'] for a in data.get('Answer',[]) if a.get('type')==1]

    def __call__(self,host,port,**kwargs):
        addresses=self.system(host,port,**kwargs)
        parsed=[ipaddress.ip_address(a[4][0]) for a in addresses]
        if not any(ip.version==4 and ip in FAKE_IP for ip in parsed):
            return addresses
        # Do not generalize this exception to loopback/private/link-local DNS.
        if any(not ip.is_global and not (ip.version==4 and ip in FAKE_IP) for ip in parsed):
            return addresses
        with self.lock:
            entry=self.cache.get(host)
            if entry and entry[0]>time.monotonic():
                ips=entry[1]
            else:
                ips=self.lookup(host)
                if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                    raise ValueError('Target has no independently verified public address')
                self.cache[host]=(time.monotonic()+120,ips)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in ips]


public_resolver=PublicResolver()
