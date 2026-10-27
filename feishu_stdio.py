"""Bounded Feishu stdio sessions: the MCP SDK spawns and reaps; this module only counts and maps safe errors."""
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.stdio import stdio_client
import feishu_readonly

LIMIT=10
CODES=('feishu_runtime_unavailable','feishu_capacity','feishu_discovery_busy','feishu_session_unavailable')
MESSAGES={'feishu_runtime_unavailable':'Feishu runtime unavailable','feishu_capacity':'Feishu process limit reached','feishu_discovery_busy':'Feishu discovery already running','feishu_session_unavailable':'Feishu session unavailable'}
_active=0
_discovering=set()


class FeishuRuntimeError(RuntimeError):
    def __init__(self,code):
        if code not in CODES:raise ValueError('Unknown Feishu error code')
        super().__init__(MESSAGES[code]);self.code=code


def error_code(error):
    """Safe code of a FeishuRuntimeError, also when wrapped by task or exception groups."""
    if isinstance(error,FeishuRuntimeError):return error.code
    for inner in getattr(error,'exceptions',()):
        code=error_code(inner)
        if code:return code
    return None


def active():return _active


@asynccontextmanager
async def discovery_slot(service_id):
    if service_id in _discovering:raise FeishuRuntimeError('feishu_discovery_busy')
    _discovering.add(service_id)
    try:yield
    finally:_discovering.discard(service_id)


@asynccontextmanager
async def session(service):
    global _active
    # Check and reservation happen without an await, so the event loop cannot interleave another reservation.
    if _active>=LIMIT:raise FeishuRuntimeError('feishu_capacity')
    _active+=1;yielded=False
    try:
        with tempfile.TemporaryDirectory(prefix='feishu-home-') as home,open(os.devnull,'w') as devnull:
            try:
                async with stdio_client(feishu_readonly.stdio_parameters(service,home),errlog=devnull) as streams:
                    async with ClientSession(*streams) as client:
                        async with asyncio.timeout(15):await client.initialize()
                        yielded=True;yield client
            except asyncio.CancelledError:raise
            except FeishuRuntimeError:raise
            except OSError as error:
                if yielded:raise
                raise FeishuRuntimeError('feishu_runtime_unavailable') from None
            except BaseException as error:
                # Upstream text may echo credentials, so failures before use carry only a fixed code.
                if yielded or not isinstance(error,Exception):raise
                raise FeishuRuntimeError('feishu_session_unavailable') from None
    finally:
        # The SDK has closed stdin, waited, then TERM/KILLed the process group before control returns here.
        _active-=1
