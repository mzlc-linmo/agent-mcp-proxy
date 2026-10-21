import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import proxy
from mcp import types


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_session_filters_and_never_dispatches_write(self):
        calls = []
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def initialize(self): pass
            async def list_tools(self):
                return SimpleNamespace(tools=[types.Tool(name=n, description=n, inputSchema={'type':'object','properties':{}}) for n in ['ops_decisions_first_page','ops_decision_adopt']])
            async def call_tool(self, name, arguments):
                calls.append(name)
                return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps({'code':0,'data':{'records':[],'total':0}}))])
        @asynccontextmanager
        async def transport(*args, **kwargs): yield (None, None, None)
        class Socket:
            def __init__(self): self.sent=[];self.queue=asyncio.Queue()
            async def send(self, text): self.sent.append(json.loads(text))
            def __aiter__(self): return self
            async def __anext__(self): return await self.queue.get()
        socket=Socket()
        @asynccontextmanager
        async def connection(*args, **kwargs): yield socket
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'endpoint').write_text('wss://api.xiaozhi.me/mcp/?token=synthetic');(p/'key').write_text('synthetic')
            config={'endpoint_file':str(p/'endpoint'),'upstreams':{'ops-readonly':{'url':'http://higress:8080/mcp/ops-readonly','key_file':str(p/'key')},'ops-ai':{'url':'http://higress:8080/mcp/ops-ai','key_file':None}}}
            with patch.object(proxy,'streamable_http_client',transport),patch.object(proxy,'ClientSession',lambda *a:Session()),patch.object(proxy,'connect',connection):
                task=asyncio.create_task(proxy.bridge(config))
                async def request(message, expected_id):
                    await socket.queue.put(json.dumps(message))
                    async with asyncio.timeout(5):
                        while not any(v.get('id')==expected_id for v in socket.sent): await asyncio.sleep(.01)
                    return next(v for v in socket.sent if v.get('id')==expected_id)
                try:
                    await request({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},1)
                    await socket.queue.put(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}))
                    result=await request({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},2)
                    self.assertEqual([t['name'] for t in result['result']['tools']],['ops_decisions_first_page'])
                    await request({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'ops_decisions_first_page','arguments':{}}},3)
                    denied=await request({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'ops_decision_adopt','arguments':{}}},4)
                    self.assertTrue(denied['result']['isError'])
                    self.assertEqual(calls,['ops_decisions_first_page'])
                finally:
                    task.cancel()
                    try: await task
                    except asyncio.CancelledError: pass

if __name__=='__main__': unittest.main()
