"""MCP transport boundary: validate every resolved destination and pin TCP connections."""
import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import ssl
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client

class TargetError(ValueError):pass

_PRIVATE=[ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7')]

def address_allowed(value,private):
    ip=ipaddress.ip_address(value)
    if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:ip=ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:return False
    if any(ip.version==n.version and ip in n for n in _PRIVATE):return private
    return ip.is_global

def validate_target(url,private):
    if not isinstance(url,str) or len(url)>8192 or any(c in url for c in '\r\n'):raise TargetError('Invalid MCP URL')
    try:
        u=urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment or u.port==0:raise ValueError()
        try:
            ip=ipaddress.ip_address(u.hostname)
            if not address_allowed(str(ip),private):raise TargetError('Destination is not allowed')
            if u.scheme=='http' and not any(ip.version==n.version and ip in n for n in _PRIVATE):raise TargetError('Public HTTP is not allowed')
        except ValueError as e:
            if isinstance(e,TargetError):raise
            if u.scheme=='http' and (not private or '.' in u.hostname):raise TargetError('HTTP requires confirmed private target')
        return u
    except TargetError:raise
    except Exception:raise TargetError('Invalid MCP URL') from None

def auth_headers(auth):
    mode=auth.get('mode','none')
    if mode=='none':return {}
    value=auth.get('value','')
    if not isinstance(value,str) or not value or len(value)>8192 or any(c in value for c in '\r\n'):raise TargetError('Invalid credential')
    if mode=='bearer':return {'Authorization':'Bearer '+value}
    header=auth.get('header','X-Api-Key')
    if mode!='api-key' or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',header):raise TargetError('Invalid authentication')
    if header.lower() in {'host','cookie','connection','content-length','transfer-encoding','authorization','from','origin','referer','proxy-authorization'} or header.lower().startswith(('x-forwarded-','sec-')):raise TargetError('Reserved authentication header')
    return {header:value}

class PinnedBackend(AnyIOBackend):
    def __init__(self,private,scheme):self.private=private;self.scheme=scheme
    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        async with asyncio.timeout(timeout or 10):
            answers=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
            addresses=list(dict.fromkeys(a[4][0] for a in answers))
            if not addresses or any(not address_allowed(a,self.private) for a in addresses):raise TargetError('Resolved destination rejected')
            if self.scheme=='http' and any(ipaddress.ip_address(a).is_global for a in addresses):raise TargetError('Public HTTP rejected')
            # The original HTTP Host and TLS server_hostname remain in httpcore's connection layer.
            return await super().connect_tcp(addresses[0],port,timeout,local_address,socket_options)

class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self,url,private):
        u=validate_target(url,private);self.origin=(u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80))
        self.pool=httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(),network_backend=PinnedBackend(private,u.scheme),retries=0)
    async def handle_async_request(self,request):
        origin=(request.url.scheme,request.url.host,request.url.port or (443 if request.url.scheme=='https' else 80))
        if origin!=self.origin:raise TargetError('Cross-origin MCP request rejected')
        req=httpcore.Request(method=request.method,url=httpcore.URL(scheme=request.url.raw_scheme,host=request.url.raw_host,port=request.url.port,target=request.url.raw_path),headers=request.headers.raw,content=request.stream,extensions=request.extensions)
        response=await self.pool.handle_async_request(req)
        if 300<=response.status<400:
            await response.aclose()
            raise TargetError('MCP redirects are not allowed')
        return httpx.Response(response.status,headers=response.headers,stream=CoreStream(response.stream),extensions=response.extensions)
    async def aclose(self):await self.pool.aclose()

class CoreStream(httpx.AsyncByteStream):
    def __init__(self,stream):self.stream=stream
    async def __aiter__(self):
        async for chunk in self.stream:yield chunk
    async def aclose(self):await self.stream.aclose()

@asynccontextmanager
async def service_session(service):
    url=service['url'];private=service.get('private_network',False);headers=auth_headers(service.get('auth',{'mode':'none'}))
    if service.get('transport','streamable-http')=='sse':
        def factory(headers=None,timeout=None,auth=None):
            return httpx.AsyncClient(headers=headers,timeout=timeout or 20,auth=auth,transport=GuardedTransport(url,private),follow_redirects=False,trust_env=False)
        async with sse_client(url,headers=headers,httpx_client_factory=factory) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize();yield session
    else:
        async with httpx.AsyncClient(headers=headers,timeout=20,transport=GuardedTransport(url,private),follow_redirects=False,trust_env=False) as client:
            async with streamable_http_client(url,http_client=client) as streams:
                async with ClientSession(streams[0],streams[1]) as session:
                    await session.initialize();yield session

async def discover_service(service):
    from proxy import secure_logging
    secure_logging();tools=[];size=0;cursor=None
    async with asyncio.timeout(30):
        async with service_session(service) as session:
            for _ in range(20):
                page=await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    item=tool.model_dump(mode='json',exclude_none=True)
                    size+=len(json.dumps(item).encode())
                    if len(tools)>=200 or size>2*1024*1024:raise TargetError('Tool discovery limit exceeded')
                    item['schema_hash']=hashlib.sha256(json.dumps(item['inputSchema'],sort_keys=True,separators=(',',':')).encode()).hexdigest();tools.append(item)
                cursor=page.nextCursor
                if not cursor:return tools
            raise TargetError('Too many tool pages')
