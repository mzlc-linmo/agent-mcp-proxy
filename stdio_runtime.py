"""Bounded stdio MCP sessions for every local-program service, Feishu included.

The MCP SDK spawns the child in its own process group and TERM/KILLs that group when the context exits,
so cancellation, disconnect and device stop all reap the process; this module counts, logs and maps errors.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.stdio import stdio_client
import local_stdio


def _limit(name,default):
    try:return max(1,int(os.environ.get(name,default)))
    except ValueError:return default


# One shared ceiling for all stdio children (Feishu and local programs); each costs roughly 50–150 MB.
LIMIT=_limit('STDIO_PROCESS_LIMIT',10)
# First discovery may download the package, so local programs get a long initialize window.
START_TIMEOUT=_limit('STDIO_START_TIMEOUT',300)
CODES={'stdio_command_not_found':'Command not found in the proxy container','stdio_capacity':'Stdio process limit reached',
    'stdio_discovery_busy':'Discovery already running for this service','stdio_start_failed':'Local program failed to start or initialize',
    'stdio_timeout':'Local program did not finish installing or starting in time'}
BUSY_CODES=frozenset({'stdio_capacity','stdio_discovery_busy','feishu_capacity','feishu_discovery_busy'})
_active=0
_discovering=set()
_states={}
_ANSI=re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

log=logging.getLogger('agent_mcp_proxy.stdio')
if not log.handlers:
    _handler=logging.StreamHandler(sys.stderr);_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(_handler);log.setLevel(logging.INFO);log.propagate=False


class StdioError(RuntimeError):
    """Fixed code and message; detail is an already-redacted stderr tail or command name."""
    messages=CODES
    def __init__(self,code,detail=''):
        if code not in self.messages:raise ValueError('Unknown stdio error code')
        super().__init__(self.messages[code]);self.code=code;self.detail=detail


def find_error(error):
    """The StdioError inside task or exception groups, if any."""
    if isinstance(error,StdioError):return error
    for inner in getattr(error,'exceptions',()):
        found=find_error(inner)
        if found:return found
    return None


def _first(error,kind):
    """The first exception of a type inside task or exception groups, if any."""
    if isinstance(error,kind):return error
    for inner in getattr(error,'exceptions',()):
        found=_first(inner,kind)
        if found:return found
    return None


def error_code(error):
    found=find_error(error)
    return found.code if found else None


def active():return _active


def state(sid):return dict(_states.get(sid,{'state':'unchecked'}))


def _set_state(sid,value,detail=''):
    _states[sid]={'state':value,'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}|({'detail':detail} if detail else {})


def forget(sid):
    _states.pop(sid,None)


def clear_cache(sid,everything=False):
    """Package caches only by default; the service home (plugin data) survives a reinstall, not a deletion."""
    where=local_stdio.paths(sid)
    for part in (['root'] if everything else local_stdio.CACHE_PARTS):shutil.rmtree(where[part],ignore_errors=True)


class StderrTail:
    """Bounded, non-blocking capture of child stderr; redacts env values before anything is shown."""
    def __init__(self,secrets=(),limit=16384):
        self.secrets=sorted(secrets,key=len,reverse=True);self.limit=limit;self.buffer=bytearray()
        read,write=os.pipe();os.set_blocking(read,False);self.read=read;self.file=os.fdopen(write,'w');self.loop=None
    def __enter__(self):
        self.loop=asyncio.get_running_loop();self.loop.add_reader(self.read,self._drain);return self
    def __exit__(self,*exc):
        self.loop.remove_reader(self.read);self.file.close();os.close(self.read)
    def _drain(self):
        while True:
            try:data=os.read(self.read,65536)
            except (BlockingIOError,OSError):return
            if not data:
                self.loop.remove_reader(self.read);return
            self.buffer.extend(data);del self.buffer[:-self.limit]
    def text(self,lines=20):
        self._drain()
        text=_ANSI.sub('',self.buffer.decode('utf-8','replace'))
        for secret in self.secrets:text=text.replace(secret,'***')
        return '\n'.join(line.rstrip() for line in text.strip().splitlines()[-lines:])[-4000:]


def launch_line(name,sid,params,secrets):
    line='stdio start service=%s id=%s command=%s args=%s'%(json.dumps(name,ensure_ascii=False),sid,json.dumps(params.command),json.dumps(params.args,ensure_ascii=False))
    for secret in secrets:line=line.replace(secret,'***')
    return line


@asynccontextmanager
async def bounded_session(params,errlog,timeout,fail):
    """Spawn, initialize and yield a ClientSession; failures before the yield become fail(error)."""
    global _active
    # Check and reservation happen without an await, so the event loop cannot interleave another reservation.
    if _active>=LIMIT:raise fail('capacity')
    _active+=1;yielded=False
    try:
        try:
            async with stdio_client(params,errlog=errlog) as streams:
                async with ClientSession(*streams) as client:
                    async with asyncio.timeout(timeout):await client.initialize()
                    yielded=True;yield client
        except asyncio.CancelledError:raise
        except StdioError:raise
        except BaseException as error:
            # Upstream text may echo credentials, so callers map failures to fixed codes and redacted detail.
            if yielded or not isinstance(error,Exception):raise
            raise fail(error) from None
    finally:
        # The SDK has closed stdin, waited, then TERM/KILLed the process group before control returns here.
        _active-=1


@asynccontextmanager
async def discovery_slot(sid,busy):
    if sid in _discovering:raise busy
    _discovering.add(sid)
    try:yield
    finally:_discovering.discard(sid)


@asynccontextmanager
async def session(service):
    """Session for a local-program service; the launch is logged without env values."""
    where=local_stdio.paths(service['id']);secrets=local_stdio.secrets(service)
    try:params=local_stdio.stdio_parameters(service,where)
    except OSError:raise StdioError('stdio_start_failed','cache directory not writable: %s'%local_stdio.CACHE_ROOT) from None
    log.info(launch_line(service['name'],service['id'],params,secrets))
    with StderrTail(secrets) as tail:
        def fail(error):
            if error=='capacity':return StdioError('stdio_capacity','limit %d'%LIMIT)
            if isinstance(error,FileNotFoundError) and not shutil.which(params.command,path=params.env['PATH']) and not os.path.exists(params.command):
                return StdioError('stdio_command_not_found',params.command)
            if isinstance(error,OSError):return StdioError('stdio_start_failed','%s: %s'%(params.command,error.strerror or type(error).__name__))
            if _first(error,TimeoutError):return StdioError('stdio_timeout',tail.text())
            return StdioError('stdio_start_failed',tail.text())
        async with bounded_session(params,tail.file,START_TIMEOUT,fail) as client:yield client


@asynccontextmanager
async def tracked_discovery(service,reinstall=False):
    """One discovery per service; the page reads installing/ready/failed with the redacted reason."""
    sid=service['id']
    async with discovery_slot(sid,StdioError('stdio_discovery_busy')):
        if reinstall:clear_cache(sid)
        previous=_states.get(sid);_set_state(sid,'installing')
        try:yield
        except asyncio.CancelledError:
            if previous:_states[sid]=previous
            else:_states.pop(sid,None)
            raise
        except BaseException as error:
            found=find_error(error)
            if found:_set_state(sid,'failed',(found.messages[found.code]+('\n'+found.detail if found.detail else '')))
            elif isinstance(error,TimeoutError):_set_state(sid,'failed',CODES['stdio_timeout'])
            else:_set_state(sid,'failed','Tool discovery failed or exceeded limits')
            raise
        else:_set_state(sid,'ready')
