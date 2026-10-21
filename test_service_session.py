import unittest
import json
from unittest.mock import patch
import httpx
from service_transport import service_session

class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_sse_initialization_and_discovery_with_api_key(self):
        import asyncio
        messages=asyncio.Queue();seen=[]
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'event: endpoint\ndata: /messages\n\n'
                while True:yield await messages.get()
            async def aclose(self):pass
        async def handle(request):
            self.assertEqual(request.headers['X-Api-Key'],'synthetic')
            if request.method=='GET':return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream())
            message=json.loads(request.content);seen.append(message['method'])
            if 'id' in message:
                result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'mock','version':'1'}} if message['method']=='initialize' else {'tools':[{'name':'sse_lookup','inputSchema':{'type':'object'}}]}
                await messages.put(('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result})+'\n\n').encode())
            return httpx.Response(202)
        with patch('service_transport.GuardedTransport',lambda *a:httpx.MockTransport(handle)):
            async with asyncio.timeout(5):
                async with service_session({'url':'https://example.com/sse','auth':{'mode':'api-key','header':'X-Api-Key','value':'synthetic'},'transport':'sse'}) as session:
                    self.assertEqual((await session.list_tools()).tools[0].name,'sse_lookup')
        self.assertEqual(seen,['initialize','notifications/initialized','tools/list'])

    async def test_streamable_initialization_and_discovery_without_auth(self):
        seen=[]
        async def handle(request):
            if request.method=='DELETE':return httpx.Response(200,json={})
            message=json.loads(request.content);seen.append(message['method'])
            self.assertNotIn('authorization',request.headers)
            if 'id' not in message:return httpx.Response(202)
            result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'mock','version':'1'}} if message['method']=='initialize' else {'tools':[{'name':'lookup','inputSchema':{'type':'object'}}]}
            return httpx.Response(200,json={'jsonrpc':'2.0','id':message['id'],'result':result})
        with patch('service_transport.GuardedTransport',lambda *a:httpx.MockTransport(handle)):
            async with service_session({'url':'https://example.com/mcp','auth':{'mode':'none'},'transport':'streamable-http'}) as session:
                page=await session.list_tools();self.assertEqual(page.tools[0].name,'lookup')
        self.assertEqual(seen,['initialize','notifications/initialized','tools/list'])

if __name__=='__main__':unittest.main()
