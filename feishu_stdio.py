"""Feishu branch of the shared stdio session manager: fixed launch parameters and Feishu-specific safe codes."""
import os
import tempfile
from contextlib import asynccontextmanager
import feishu_readonly
import stdio_runtime
from stdio_runtime import StdioError,error_code,active,_discovering

LIMIT=stdio_runtime.LIMIT
CODES=('feishu_runtime_unavailable','feishu_capacity','feishu_discovery_busy','feishu_session_unavailable')
MESSAGES={'feishu_runtime_unavailable':'Feishu runtime unavailable','feishu_capacity':'Feishu process limit reached','feishu_discovery_busy':'Feishu discovery already running','feishu_session_unavailable':'Feishu session unavailable'}


class FeishuRuntimeError(StdioError):
    messages=MESSAGES
    def __init__(self,code):
        if code not in CODES:raise ValueError('Unknown Feishu error code')
        super().__init__(code)


def discovery_slot(service_id):
    return stdio_runtime.discovery_slot(service_id,FeishuRuntimeError('feishu_discovery_busy'))


def _fail(error):
    if error=='capacity':return FeishuRuntimeError('feishu_capacity')
    # Upstream text may echo credentials, so failures before use carry only a fixed code.
    if isinstance(error,OSError):return FeishuRuntimeError('feishu_runtime_unavailable')
    return FeishuRuntimeError('feishu_session_unavailable')


@asynccontextmanager
async def session(service):
    if stdio_runtime.active()>=stdio_runtime.LIMIT:raise FeishuRuntimeError('feishu_capacity')
    with tempfile.TemporaryDirectory(prefix='feishu-home-') as home,open(os.devnull,'w') as devnull:
        params=feishu_readonly.stdio_parameters(service,home)
        stdio_runtime.log.info(stdio_runtime.launch_line(service['name'],service['id'],params,[service['app_secret']]))
        async with stdio_runtime.bounded_session(params,devnull,15,_fail) as client:yield client
