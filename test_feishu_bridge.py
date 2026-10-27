import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
import anyio
from mcp import ClientSession,types
from mcp.shared.message import SessionMessage
from websockets.asyncio.server import serve
from admin_config import ConfigStore
from catalog import CatalogStore,schema_hash
from catalog_runtime import bridge_catalog,device_snapshot,approved_tools,FEISHU_HINT
from endpoint_manager import EndpointManager
from policy import ALLOWED
from service_transport import discover_service
import feishu_stdio
from test_feishu_stdio import FixtureRuntime,alive,reaped

SECRET='synthetic-feishu-secret-bridge'


class Xiaozhi:
    """Local ws:// stand-in for the Xiaozhi endpoint; the MCP client on it is the real SDK ClientSession."""
    def __init__(self):self.connections=asyncio.Queue()
    async def __aenter__(self):
        async def handler(ws):
            closed=asyncio.Event();await self.connections.put((ws,ws.request.path,closed))
            await ws.wait_closed();closed.set()
        self.server=await serve(handler,'127.0.0.1',0);self.port=self.server.sockets[0].getsockname()[1];return self
    async def __aexit__(self,*exc):self.server.close();await self.server.wait_closed()
    def url(self,token):return 'ws://127.0.0.1:%d/mcp/?token=%s'%(self.port,token)
    async def accept(self,timeout=10):
        async with asyncio.timeout(timeout):return await self.connections.get()


@asynccontextmanager
async def mcp_client(ws):
    read_send,read_receive=anyio.create_memory_object_stream(16)
    write_send,write_receive=anyio.create_memory_object_stream(16)
    async def reader():
        async with read_send:
            async for text in ws:await read_send.send(SessionMessage(types.JSONRPCMessage.model_validate_json(text)))
    async def writer():
        async with write_receive:
            async for message in write_receive:await ws.send(message.message.model_dump_json(by_alias=True,exclude_none=True))
    async with anyio.create_task_group() as group:
        group.start_soon(reader);group.start_soon(writer)
        async with ClientSession(read_receive,write_send) as session:
            await session.initialize();yield session
        group.cancel_scope.cancel()


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime=FixtureRuntime(self)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'data'/'config.json'
        ConfigStore(path,'http://higress:8080').initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=A','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'K'} for d in ALLOWED},'enabled_tools':[]})
        self.store=CatalogStore(path);value=self.store.load()
        # Only Feishu bindings are under test; the migrated HTTP grants are cleared.
        self.store.update_device('default',{'bindings':{sid:[] for sid in value['devices'][0]['bindings']}},value['version'])
        self.sid=self.store.add_service({'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':SECRET},self.v())['id']
        self.idle=self.store.add_service({'name':'飞书未授权','provider':'feishu-readonly','transport':'stdio','app_id':'cli_idle','app_secret':SECRET},self.v())['id']
        for sid in (self.sid,self.idle):
            service=self.store.load()['services'][sid]
            self.store.record_discovery(sid,await discover_service(service),self.v())
        self.started=set(self.runtime.pids())
        self.assertTrue(await reaped(self.started))
    def v(self):return self.store.load()['version']
    def new_pids(self):return set(self.runtime.pids())-self.started
    def tearDown(self):self.assertEqual(feishu_stdio.active(),0)

    async def test_xiaozhi_sdk_lists_and_calls_only_approved_feishu_tools(self):
        self.store.update_device('default',{'bindings':{self.sid:['im_v1_chat_list','docx_v1_document_rawContent','bitable_v1_appTable_list']}},self.v())
        config=self.store.load()
        service=config['services'][self.sid];grants=config['devices'][0]['bindings'][self.sid]
        aliases={g['name']:g['alias'] for g in grants}
        # Forged private snapshot: a write tool claiming readOnlyHint, already hashed and granted.
        schema=next(t['inputSchema'] for t in service['discovery'])
        service['discovery'].append({'name':'im_v1_message_create','inputSchema':schema,'annotations':{'readOnlyHint':True},'schema_hash':schema_hash(schema)})
        grants.append({'name':'im_v1_message_create','schema_hash':schema_hash(schema),'alias':self.sid+'__im_v1_message_create'})
        # A grant whose stored hash no longer matches the live schema is not published either.
        next(g for g in grants if g['name']=='bitable_v1_appTable_list')['schema_hash']=schema_hash({'type':'object'})
        self.assertNotIn('im_v1_message_create',[t['name'] for _,t in approved_tools(service,grants,[{'name':'im_v1_message_create','inputSchema':schema}])])
        snapshot=device_snapshot(config,'default')
        async with Xiaozhi() as xiaozhi:
            snapshot['endpoint']=xiaozhi.url('A');reports=[]
            task=asyncio.create_task(bridge_catalog(snapshot,lambda e,d:reports.append((e,d))))
            ws,path,_=await xiaozhi.accept()
            self.assertEqual(path,'/mcp/?token=A')
            async with mcp_client(ws) as client:
                listed=(await client.list_tools()).tools
                self.assertEqual(sorted(t.name for t in listed),sorted([aliases['im_v1_chat_list'],aliases['docx_v1_document_rawContent']]))
                self.assertTrue(all(t.description.startswith(FEISHU_HINT) for t in listed))
                # Only the bound service started a process; the unbound one never did.
                [pid]=self.new_pids();self.assertTrue(alive(pid))
                self.assertNotIn(self.idle,snapshot['services']);self.assertNotIn(self.idle,[d['domain'] for e,d in reports if e=='upstream'])
                async def call(alias,**arguments):return await client.call_tool(alias,arguments)
                result=await call(aliases['im_v1_chat_list'],useUAT=True,page_size=5)
                self.assertFalse(result.isError)
                data=json.loads(result.content[0].text)
                # Plain data without code=0 is success; identity stays tenant even when useUAT is requested.
                self.assertEqual(data['token_mode'],'tenant_access_token');self.assertNotIn('code',data)
                for alias in (self.sid+'__im_v1_message_create',aliases['bitable_v1_appTable_list'],'unknown'):
                    result=await call(alias,page_size=1);self.assertTrue(result.isError);self.assertEqual(result.content[0].text,'Tool not authorized')
                for mode,size,ok in (('bytes',1024,True),('bytes',1025,False),('chinese',341,True),('chinese',342,False),('image',0,False),('denied',0,False)):
                    with self.subTest(mode=mode,size=size):
                        before=len(self.runtime.calls())
                        result=await call(aliases['docx_v1_document_rawContent'],mode=mode,page_size=size)
                        self.assertEqual(result.isError,not ok)
                        text=result.content[0].text
                        self.assertNotIn(SECRET,text)
                        if not ok:self.assertIn('no automatic replay',text)
                        if size==1025 or size==342:self.assertIn('narrow the query',text)
                        # Exactly one upstream call, never replayed.
                        self.assertEqual(len(self.runtime.calls()),before+1)
                names={c['name'] for c in self.runtime.calls()}
                self.assertEqual(names,{'im_v1_chat_list','docx_v1_document_rawContent'})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(await reaped([pid]))

    async def test_device_reapply_and_disconnect_reap_stdio(self):
        other=self.store.add_device({'name':'Second','endpoint':'wss://api.xiaozhi.me/mcp/?token=B'},self.v())['id']
        for eid in ('default',other):self.store.update_device(eid,{'enabled':True,'bindings':{self.sid:['im_v1_chat_list']}},self.v())
        async with Xiaozhi() as xiaozhi:
            def config():
                value=copy.deepcopy(self.store.load())
                for d in value['devices']:d['endpoint']=xiaozhi.url(d['id'])
                return value
            async def connection(eid):
                ws,path,closed=await xiaozhi.accept()
                self.assertEqual(path,'/mcp/?token='+eid);return ws,closed
            async def wait_new(known):
                async with asyncio.timeout(10):
                    while not (self.new_pids()-known):await asyncio.sleep(.02)
                return self.new_pids()-known
            manager=EndpointManager()
            try:
                await manager.apply(config())
                first={};seen=set()
                for _ in range(2):
                    ws,path,closed=await xiaozhi.accept();first[path.rsplit('=',1)[1]]=(ws,closed)
                async with asyncio.timeout(10):
                    while len(self.new_pids())<2 or any(manager.snapshot()['endpoints'][e]['state']!='connected' for e in ('default',other)):await asyncio.sleep(.02)
                pids=self.new_pids();seen|=pids
                # Re-apply one device: its old process is reaped before the new one runs; the other is untouched.
                old=set(pids)
                await manager.apply_endpoint(config(),'default')
                ws_a,_=await connection('default')
                new=await wait_new(seen);seen|=new
                [new_a]=new
                self.assertEqual(sum(alive(p) for p in old),1)
                survivor=next(p for p in old if alive(p))
                # WSS disconnect of the second device: its process is reaped and the bridge reconnects.
                await first[other][0].close()
                self.assertTrue(await reaped([survivor]))
                ws_b,_=await connection(other)
                [new_b]=await wait_new(seen);seen.add(new_b)
                self.assertEqual(manager.snapshot()['endpoints'][other]['reconnect_count'],1)
                self.assertTrue(alive(new_a))
                # Disabling (save then stop) and deleting stop only that device's process.
                await manager.remove_endpoint('default')
                self.assertTrue(await reaped([new_a]));self.assertTrue(alive(new_b))
                await manager.remove_endpoint(other)
                self.assertTrue(await reaped([new_b]))
                # After credential rotation, even an identical rediscovered schema publishes nothing and starts nothing.
                self.store.update_service(self.sid,{'app_secret':'rotated-secret'},self.v())
                self.store.record_discovery(self.sid,await discover_service(self.store.load()['services'][self.sid]),self.v())
                seen|=self.new_pids()
                reports=[];snapshot=device_snapshot(config(),'default')
                task=asyncio.create_task(bridge_catalog(snapshot,lambda e,d:reports.append((e,d))))
                ws,_=await connection('default')
                async with mcp_client(ws) as client:self.assertEqual((await client.list_tools()).tools,[])
                self.assertIn(('upstream',{'domain':self.sid,'state':'disabled'}),reports)
                self.assertEqual(self.new_pids()-seen,set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            finally:await manager.stop()
            self.assertTrue(await reaped(self.new_pids()))


if __name__=='__main__':unittest.main()
