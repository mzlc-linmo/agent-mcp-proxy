"""Generic local-program (stdio) services: import formats, admin API, shared process runtime and the Xiaozhi bridge.

The child is the synthetic server in tests/fixtures; stdio_client/ClientSession are the real SDK, nothing touches the network.
"""
import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import local_stdio
import stdio_runtime
import feishu_stdio
from admin import create_app
from admin_auth import Auth,password_hash
from admin_config import ConfigStore
from catalog import CatalogStore
from catalog_runtime import bridge_catalog,device_snapshot
from endpoint_manager import EndpointManager
from policy import ALLOWED
from service_import import parse_import,resolve,preview,ImportError
from service_transport import discover_service,service_session
from stdio_runtime import StdioError
from test_feishu_stdio import FixtureRuntime,alive,reaped,service as feishu_service
from test_feishu_bridge import Xiaozhi,mcp_client

FIXTURE=str(Path(__file__).parent/'tests'/'fixtures'/'feishu_stdio_server.py')
SECRET='synthetic-local-secret-value'
TOOLS='demo.echo,demo.read'
NAMES={'demo_echo','demo_read','im_v1_message_create','unknown_tool'}


class CacheRoot:
    """Points the per-service cache/home root at a temporary directory."""
    def __init__(self,case):
        self.root=Path(tempfile.mkdtemp(prefix='stdio-cache-'));case.addCleanup(lambda:__import__('shutil').rmtree(self.root,ignore_errors=True))
        p=patch('local_stdio.CACHE_ROOT',self.root);p.start();case.addCleanup(p.stop)
        case.addCleanup(stdio_runtime._states.clear)
    def home(self,sid):return local_stdio.paths(sid)['home']
    def mode(self,sid,**mode):
        self.home(sid).mkdir(parents=True,exist_ok=True);(self.home(sid)/'mode.json').write_text(json.dumps(mode))
    def pids(self,sid):return sorted(int(p.name[4:]) for p in self.home(sid).glob('pid-*')) if self.home(sid).exists() else []
    def launch(self,sid,pid):return json.loads((self.home(sid)/('launch-%d.json'%pid)).read_text())
    def calls(self,sid):
        f=self.home(sid)/'calls.jsonl'
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    async def wait_file(self,sid,prefix,timeout=10):
        async with asyncio.timeout(timeout):
            while not list(self.home(sid).glob(prefix+'*')):await asyncio.sleep(.02)
        return int(next(self.home(sid).glob(prefix+'*')).name[len(prefix):])


def local(sid='loc1',command=sys.executable,args=None,env=None):
    return {'id':sid,'name':'本地演示','transport':'stdio','command':command,'args':[FIXTURE,'--tools',TOOLS] if args is None else args,
        'env':{'APP_SECRET':SECRET,'FLAG':'1'} if env is None else env,'discovery':[],'legacy_profile':None}


class ImportFormatTests(unittest.TestCase):
    def test_mcp_servers_stdio_and_http_mixed(self):
        document={'mcpServers':{
            'files':{'command':'npx','args':['-y','@modelcontextprotocol/server-filesystem@2025.8.21','/data'],'env':{'LOG_LEVEL':'info','API_KEY':''},'autoApprove':[],'disabled':False},
            'time':{'type':'stdio','command':'uvx','args':['mcp-server-time==2025.9.25'],'env':{'TOKEN':'<your token>'}},
            'remote':{'type':'sse','url':'https://example.com/sse'}}}
        rows=parse_import(document)
        self.assertEqual([r['transport'] for r in rows],['stdio','stdio','sse'])
        shown=preview(rows[0],{},'0:')
        self.assertEqual(shown['command'],'npx');self.assertEqual(shown['args'],['-y','@modelcontextprotocol/server-filesystem@2025.8.21','/data'])
        # Env values never appear in a preview, only names; blank or placeholder values become required secret inputs.
        self.assertEqual(shown['env_keys'],['API_KEY','LOG_LEVEL']);self.assertNotIn('info',json.dumps(shown))
        self.assertEqual(shown['missing'],['0:env:API_KEY']);self.assertTrue(shown['inputs'][0]['secret'])
        self.assertEqual(preview(rows[1],{},'1:')['missing'],['1:env:TOKEN'])
        entry,missing=resolve(rows[0],{'0:env:API_KEY':SECRET},'0:')
        self.assertEqual(missing,[]);self.assertEqual(entry,{'name':'files','transport':'stdio','command':'npx','args':['-y','@modelcontextprotocol/server-filesystem@2025.8.21','/data'],'env':{'LOG_LEVEL':'info','API_KEY':SECRET}})
        self.assertNotIn(SECRET,json.dumps(preview(rows[0],{'0:env:API_KEY':SECRET},'0:'),ensure_ascii=False))
        for bad in ({'command':'npx','url':'https://example.com/mcp'},{'command':'npx','cwd':'/x'},{'command':'npx','envFile':'.env'},{'command':'npx','type':'http'},{'command':'','args':[]},{'command':'npx','args':[{'x':1}]},{'command':'npx','env':{'BAD-NAME':'v'}}):
            with self.subTest(entry=bad),self.assertRaises(ImportError):parse_import({'mcpServers':{'x':bad}})

    def test_registry_npm_and_pypi_packages(self):
        npm={'$schema':'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json','name':'io.github.modelcontextprotocol/filesystem','title':'Filesystem','version':'1.0.2',
            'packages':[{'registryType':'oci','identifier':'docker.io/mcp/filesystem:1.0.2','transport':{'type':'stdio'}},
                {'registryType':'npm','identifier':'@modelcontextprotocol/server-filesystem','version':'1.0.2','transport':{'type':'stdio'},'runtimeHint':'npx',
                 'runtimeArguments':[{'type':'positional','value':'-y'}],
                 'packageArguments':[{'type':'positional','valueHint':'target_dir','description':'Path to access','default':'/srv/data','isRequired':True},
                    {'type':'named','name':'--mode','default':'local','choices':['local','cached']},
                    {'type':'named','name':'--label','description':'optional'},
                    {'type':'named','name':'--root','value':'{base}/files','variables':{'base':{'isRequired':True,'description':'Base path'}}}],
                 'environmentVariables':[{'name':'LOG_LEVEL','default':'info'},{'name':'EXTRA'}]}]}
        [row]=parse_import({'server':npm})
        self.assertEqual(row['name'],'Filesystem');self.assertEqual(row['command'],'npx')
        shown=preview(row,{},'0:')
        self.assertEqual(shown['missing'],['0:arg:3:base'])
        self.assertEqual(shown['args'],['-y','@modelcontextprotocol/server-filesystem@1.0.2','/srv/data','--mode','local','--root','<base>/files'])
        entry,missing=resolve(row,{'0:arg:3:base':'/srv','0:arg:2':'demo'},'0:')
        self.assertEqual(missing,[])
        self.assertEqual(entry['args'],['-y','@modelcontextprotocol/server-filesystem@1.0.2','/srv/data','--mode','local','--label','demo','--root','/srv/files'])
        # Optional env left blank is omitted; a default counts as filled.
        self.assertEqual(entry['env'],{'LOG_LEVEL':'info'})
        pypi={'name':'io.github.example/weather-mcp','version':'0.5.0','packages':[{'registryType':'pypi','identifier':'weather-mcp-server','version':'0.5.0','runtimeHint':'uvx','transport':{'type':'stdio'},
            'environmentVariables':[{'name':'WEATHER_API_KEY','description':'API key','isRequired':True,'isSecret':True},{'name':'WEATHER_UNITS','default':'celsius'}]}]}
        [row]=parse_import(pypi)
        shown=preview(row,{},'0:')
        self.assertEqual((row['name'],shown['command'],shown['args']),('io.github.example/weather-mcp','uvx',['weather-mcp-server==0.5.0']))
        self.assertEqual(shown['missing'],['0:env:WEATHER_API_KEY'])
        # Env inputs are secrets: no defaults echoed, only whether one exists.
        self.assertTrue(all(i['default'] is None for i in shown['inputs']));self.assertEqual([i['has_default'] for i in shown['inputs']],[False,True])
        entry,missing=resolve(row,{'0:env:WEATHER_API_KEY':SECRET},'0:')
        self.assertEqual((missing,entry['env']),([],{'WEATHER_API_KEY':SECRET,'WEATHER_UNITS':'celsius'}))
        # Registry API list wrapper, remote-only fallback and unsupported package types.
        self.assertEqual(len(parse_import({'servers':[{'server':pypi},{'server':npm}]})),2)
        [remote]=parse_import({'name':'x/remote','remotes':[{'type':'streamable-http','url':'https://{tenant}.example.com/mcp'},{'type':'sse','url':'https://example.com/sse'}]},auth_override={'mode':'bearer','value':'synthetic'})
        self.assertEqual((remote['transport'],remote['url'],remote['auth']['mode']),('sse','https://example.com/sse','bearer'))
        with self.assertRaises(ImportError):parse_import({'name':'x/oci','packages':[{'registryType':'oci','identifier':'docker.io/x/y'}]})
        with self.assertRaises(ImportError):parse_import({'packages':[{'registryType':'npm','identifier':'x'}]})


class Manager:
    def snapshot(self):return {'endpoints':{}}
    async def remove_endpoint(self,eid):pass
    async def apply_endpoint(self,c,eid):pass


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.cache=CacheRoot(self)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'data'/'config.json'
        ConfigStore(path,'http://higress:8080').initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=SECRET','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'SECRET'} for d in ALLOWED},'enabled_tools':[]})
        self.store=CatalogStore(path);self.store.load()
        self.client=TestClient(create_app(self.store,Manager(),Auth(password_hash('long-test-password'),'admin'),'http://testserver'))
        result=self.client.post('/api/login',json={'username':'admin','password':'long-test-password'},headers={'Origin':'http://testserver'})
        self.headers={'Origin':'http://testserver','X-CSRF-Token':result.json()['csrf']}
    def version(self):return self.store.load()['version']
    def post(self,path,body):return self.client.post('/api/'+path,json={'version':self.version()}|body,headers=self.headers)
    def put(self,path,body):return self.client.put('/api/'+path,json={'version':self.version()}|body,headers=self.headers)
    def no_secret(self):
        for url in ('/api/services','/api/config','/api/devices'):
            response=self.client.get(url);self.assertEqual(response.status_code,200);self.assertNotIn(SECRET,response.text)

    def test_manual_add_discover_grant_edit_and_delete(self):
        r=self.post('services',{'name':'本地演示','transport':'stdio','command':sys.executable,'args':[FIXTURE,'--tools',TOOLS],'env':{'APP_SECRET':SECRET}})
        self.assertEqual(r.status_code,200,r.text);sid=r.json()['id']
        self.assertEqual(self.store.load()['services'][sid]['env'],{'APP_SECRET':SECRET})
        public=self.client.get('/api/services/'+sid).json()['service']
        self.assertEqual(public,{'id':sid,'name':'本地演示','transport':'stdio','command':sys.executable,'args':[FIXTURE,'--tools',TOOLS],'env_keys':['APP_SECRET'],'discovery':[],'runtime':{'state':'unchecked'}})
        self.no_secret()
        # Real discovery through the admin API: the program starts, lists tools, and is reaped.
        with self.assertLogs('agent_mcp_proxy.stdio','INFO') as logs:
            r=self.post('services/%s/discover'%sid,{})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['discovered'],4)
        self.assertIn('command="%s"'%sys.executable,logs.output[0]);self.assertIn(TOOLS,logs.output[0]);self.assertNotIn(SECRET,'\n'.join(logs.output))
        self.assertEqual(self.client.get('/api/services/'+sid).json()['service']['runtime']['state'],'ready')
        self.assertEqual(self.put('devices/default',{'bindings':{sid:['demo_echo','demo_read']}}).status_code,200)
        # Blank env value keeps the stored secret; name-only edits keep discovery and grants.
        r=self.put('services/'+sid,{'name':'改名','env':{'APP_SECRET':''}});self.assertEqual(r.status_code,200,r.text)
        value=self.store.load();self.assertEqual(value['services'][sid]['env'],{'APP_SECRET':SECRET});self.assertEqual(len(value['services'][sid]['discovery']),4)
        self.assertTrue(all(g['schema_hash']!='review-required' for g in value['devices'][0]['bindings'][sid]))
        # Changing args (or command/env) clears discovery and puts grants under review.
        r=self.put('services/'+sid,{'args':[FIXTURE,'--tools','demo.echo'],'env':{'APP_SECRET':'','NEW_KEY':'v2-value'}})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['affected_device_ids'],['default'])
        value=self.store.load();self.assertEqual(value['services'][sid]['discovery'],[])
        self.assertEqual({g['schema_hash'] for g in value['devices'][0]['bindings'][sid]},{'review-required'})
        self.assertEqual(value['services'][sid]['env'],{'APP_SECRET':SECRET,'NEW_KEY':'v2-value'})
        self.assertEqual(self.client.get('/api/services/'+sid).json()['service']['runtime'],{'state':'unchecked'})
        # Removing an env key from the patch deletes it.
        self.put('services/'+sid,{'env':{'NEW_KEY':''}});self.assertEqual(self.store.load()['services'][sid]['env'],{'NEW_KEY':'v2-value'})
        for bad in ({'command':''},{'command':' npx'},{'args':'-y x'},{'env':{'BAD-NAME':'x'}},{'command':'npx\n'}):
            with self.subTest(patch=bad):
                r=self.put('services/'+sid,bad);self.assertEqual(r.status_code,422);self.assertEqual(r.json().get('error_code'),'invalid_stdio_launch')
        self.assertEqual(self.post('services',{'name':'x','transport':'stdio','command':'npx','args':[],'env':{},'cwd':'/'}).status_code,422)
        # Delete removes the program's cache and home directory.
        self.assertTrue(local_stdio.paths(sid)['root'].exists())
        self.put('devices/default',{'bindings':{sid:[]}})
        self.assertEqual(self.client.request('DELETE','/api/services/'+sid,json={'version':self.version()},headers=self.headers).status_code,200)
        self.assertFalse(local_stdio.paths(sid)['root'].exists())

    def test_discovery_failures_show_redacted_reason(self):
        sid=self.post('services',{'name':'缺命令','transport':'stdio','command':'definitely-missing-mcp-command','args':['--x'],'env':{'APP_SECRET':SECRET}}).json()['id']
        r=self.post('services/%s/discover'%sid,{})
        self.assertEqual(r.status_code,503);self.assertEqual(r.json()['error_code'],'stdio_command_not_found');self.assertEqual(r.json()['detail'],'definitely-missing-mcp-command')
        state=self.client.get('/api/services/'+sid).json()['service']['runtime']
        self.assertEqual(state['state'],'failed');self.assertIn('Command not found',state['detail'])
        # The program crashes and prints the credential on stderr: the page gets the tail with the value masked.
        sid=self.post('services',{'name':'崩溃','transport':'stdio','command':sys.executable,'args':[FIXTURE],'env':{'APP_SECRET':SECRET}}).json()['id']
        self.cache.mode(sid,crash=True)
        r=self.post('services/%s/discover'%sid,{})
        self.assertEqual(r.status_code,503);self.assertEqual(r.json()['error_code'],'stdio_start_failed')
        self.assertIn('fatal: credential *** rejected',r.json()['detail']);self.assertNotIn(SECRET,r.text)
        self.assertNotIn(SECRET,self.client.get('/api/services').text)
        # Reinstall is only for local programs; for them it clears package caches and discovers again.
        self.cache.mode(sid)
        npm=local_stdio.paths(sid)['npm'];(npm/'stale').write_text('x')
        (local_stdio.paths(sid)['home']/'keep').write_text('x')
        r=self.post('services/%s/reinstall'%sid,{});self.assertEqual(r.status_code,200,r.text)
        self.assertFalse((npm/'stale').exists());self.assertTrue((local_stdio.paths(sid)['home']/'keep').exists())
        http=self.post('services',{'name':'h','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False}).json()['id']
        self.assertEqual(self.post('services/%s/reinstall'%http,{}).status_code,422)

    def test_json_and_registry_import_with_inputs(self):
        document={'mcpServers':{'demo':{'command':sys.executable,'args':[FIXTURE,'--tools',TOOLS],'env':{'APP_SECRET':''}},'remote':{'url':'https://example.com/mcp'}}}
        r=self.post('services/import',{'document':document,'preview':True});self.assertEqual(r.status_code,200,r.text)
        entries=r.json()['entries'];self.assertEqual(entries[0]['missing'],['0:env:APP_SECRET']);self.assertEqual(entries[1]['origin'],'https://example.com')
        r=self.post('services/import',{'document':document});self.assertEqual(r.status_code,422);self.assertEqual(r.json()['error_code'],'import_inputs_required')
        r=self.post('services/import',{'document':document,'inputs':{'0:env:APP_SECRET':SECRET},'preview':True})
        self.assertEqual(r.json()['entries'][0]['missing'],[]);self.assertNotIn(SECRET,r.text)
        r=self.post('services/import',{'document':document,'inputs':{'0:env:APP_SECRET':SECRET}});self.assertEqual(r.status_code,200,r.text)
        sid,remote=r.json()['ids']
        self.assertEqual(self.store.load()['services'][sid]['env'],{'APP_SECRET':SECRET});self.assertEqual(self.store.load()['services'][remote]['transport'],'streamable-http')
        self.no_secret()
        registry={'name':'io.github.example/weather','packages':[{'registryType':'pypi','identifier':'weather-mcp-server','version':'0.5.0','environmentVariables':[{'name':'WEATHER_API_KEY','isRequired':True,'isSecret':True}]}]}
        self.assertEqual(self.post('services/import',{'document':registry}).json()['error_code'],'import_inputs_required')
        r=self.post('services/import',{'document':registry,'inputs':{'0:env:WEATHER_API_KEY':SECRET}});self.assertEqual(r.status_code,200,r.text)
        stored=self.store.load()['services'][r.json()['ids'][0]]
        self.assertEqual((stored['command'],stored['args'],stored['env']),('uvx',['weather-mcp-server==0.5.0'],{'WEATHER_API_KEY':SECRET}))
        self.assertEqual(self.post('services/import',{'document':document,'inputs':{'0:env:APP_SECRET':5}}).status_code,422)


class LocalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.cache=CacheRoot(self)
    def tearDown(self):self.assertEqual(stdio_runtime.active(),0);self.assertEqual(stdio_runtime._discovering,set())

    async def test_discovery_environment_cancel_and_reclaim(self):
        polluted={'NODE_OPTIONS':'--require /tmp/evil.js','STDIO_HTTPS_PROXY':'http://proxy.invalid:3128','STDIO_NPM_REGISTRY':'https://mirror.invalid/','AWS_SECRET_ACCESS_KEY':'parent-secret'}
        with patch.dict(os.environ,polluted):tools=await discover_service(local())
        self.assertEqual({t['name'] for t in tools},NAMES)
        [pid]=self.cache.pids('loc1');self.assertTrue(await reaped([pid]))
        launch=self.cache.launch('loc1',pid)
        self.assertEqual(launch['argv'],['--tools',TOOLS])
        # Only the fixed runtime env, proxy/mirror pass-through and the service's own env reach the program.
        self.assertTrue(set(launch['env'])>={'APP_SECRET','FLAG','HOME','PATH','TMPDIR','npm_config_cache','UV_CACHE_DIR','HTTPS_PROXY'})
        self.assertNotIn('NODE_OPTIONS',launch['env']);self.assertNotIn('AWS_SECRET_ACCESS_KEY',launch['env'])
        with patch.dict(os.environ,polluted):env=local_stdio.stdio_parameters(local(),local_stdio.paths('loc1')).env
        self.assertEqual((env['https_proxy'],env['NPM_CONFIG_REGISTRY']),('http://proxy.invalid:3128','https://mirror.invalid/'))
        self.assertEqual(stdio_runtime.state('loc1')['state'],'ready')
        # Cancel while the program hangs in tools/list: the process is killed and reaped, the slot and state restored.
        self.cache.mode('loc1',hang_list=True)
        task=asyncio.create_task(discover_service(local()))
        pid=await self.cache.wait_file('loc1','listing-')
        self.assertEqual(stdio_runtime.state('loc1')['state'],'installing');self.assertEqual(stdio_runtime.active(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(await reaped([pid]));self.assertEqual(stdio_runtime.state('loc1')['state'],'ready')
        # A second discovery of the same service is refused while one runs.
        for p in self.cache.home('loc1').glob('listing-*'):p.unlink()
        task=asyncio.create_task(discover_service(local()))
        pid=await self.cache.wait_file('loc1','listing-')
        with self.assertRaises(StdioError) as caught:await discover_service(local())
        self.assertEqual(caught.exception.code,'stdio_discovery_busy')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(await reaped([pid]))

    async def test_shared_limit_timeout_and_stubborn_child(self):
        # Feishu and local programs share one process limit.
        feishu=FixtureRuntime(self)
        with patch('stdio_runtime.LIMIT',3):
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(service_session(feishu_service('f1')))
                for sid in ('a','b'):await stack.enter_async_context(service_session(local(sid)))
                self.assertEqual(stdio_runtime.active(),3)
                with self.assertRaises(StdioError) as caught:await discover_service(local('c'))
                self.assertEqual(caught.exception.code,'stdio_capacity')
                self.assertEqual(stdio_runtime.state('c')['state'],'failed')
                with self.assertRaises(feishu_stdio.FeishuRuntimeError) as caught:await discover_service(feishu_service('f2'))
                self.assertEqual(caught.exception.code,'feishu_capacity')
                pids=self.cache.pids('a')+self.cache.pids('b')+feishu.pids()
            self.assertTrue(await reaped(pids))
        # A program that never answers initialize times out with the configured start window and is reaped.
        with patch('stdio_runtime.START_TIMEOUT',1):
            with self.assertRaises(StdioError) as caught:await discover_service(local('slow',command='sleep',args=['30'],env={}))
        self.assertEqual(caught.exception.code,'stdio_timeout')
        self.assertIn('did not finish',stdio_runtime.state('slow')['detail'])
        # A child ignoring SIGTERM is still killed when the session ends.
        self.cache.mode('stubborn',stubborn=True)
        async with service_session(local('stubborn')) as session:
            await session.list_tools();[pid]=self.cache.pids('stubborn')
        self.assertTrue(await reaped([pid]))


class LocalBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cache=CacheRoot(self)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'data'/'config.json'
        ConfigStore(path,'http://higress:8080').initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=A','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'K'} for d in ALLOWED},'enabled_tools':[]})
        self.store=CatalogStore(path);value=self.store.load()
        self.store.update_device('default',{'bindings':{sid:[] for sid in value['devices'][0]['bindings']}},value['version'])
        entry={k:v for k,v in local().items() if k in local_stdio.INPUT_KEYS}
        self.sid=self.store.add_service(entry,self.v())['id']
        self.idle=self.store.add_service(entry|{'name':'未授权'},self.v())['id']
        for sid in (self.sid,self.idle):self.store.record_discovery(sid,await discover_service(self.store.load()['services'][sid]),self.v())
        self.assertTrue(await reaped(self.cache.pids(self.sid)+self.cache.pids(self.idle)))
        self.started=set(self.cache.pids(self.sid));self.idle_started=set(self.cache.pids(self.idle))
    def v(self):return self.store.load()['version']
    def tearDown(self):self.assertEqual(stdio_runtime.active(),0)

    async def test_xiaozhi_lists_and_calls_granted_tools_then_review_blocks(self):
        self.store.update_device('default',{'enabled':True,'bindings':{self.sid:['demo_echo','demo_read']}},self.v())
        config=self.store.load();aliases={g['name']:g['alias'] for g in config['devices'][0]['bindings'][self.sid]}
        async with Xiaozhi() as xiaozhi:
            def current():
                value=copy.deepcopy(self.store.load())
                for d in value['devices']:d['endpoint']=xiaozhi.url(d['id'])
                return value
            manager=EndpointManager()
            try:
                await manager.apply(current())
                ws,path,closed=await xiaozhi.accept()
                async with mcp_client(ws) as client:
                    listed=sorted(t.name for t in (await client.list_tools()).tools)
                    self.assertEqual(listed,sorted(aliases.values()))
                    [pid]=set(self.cache.pids(self.sid))-self.started;self.assertTrue(alive(pid))
                    # The unbound service never starts a process.
                    self.assertEqual(set(self.cache.pids(self.idle)),self.idle_started)
                    result=await client.call_tool(aliases['demo_echo'],{'page_size':1})
                    self.assertFalse(result.isError);self.assertIn('测试群',result.content[0].text)
                    # Results keep the generic text-only, 1024-byte rule and are never replayed.
                    for mode,size,ok in (('bytes',1024,True),('bytes',1025,False),('image',0,False),('denied',0,False)):
                        with self.subTest(mode=mode,size=size):
                            before=len(self.cache.calls(self.sid))
                            result=await client.call_tool(aliases['demo_read'],{'mode':mode,'page_size':size})
                            self.assertEqual(result.isError,not ok);self.assertNotIn(SECRET,result.content[0].text)
                            self.assertEqual(len(self.cache.calls(self.sid)),before+1)
                    result=await client.call_tool(self.sid+'__unknown_tool',{});self.assertEqual(result.content[0].text,'Tool not authorized')
                # Disconnect: the program is reaped and the bridge reconnects with a fresh one.
                await ws.close()
                self.assertTrue(await reaped([pid]))
                ws,path,closed=await xiaozhi.accept()
                async with asyncio.timeout(10):
                    while not set(self.cache.pids(self.sid))-self.started-{pid}:await asyncio.sleep(.02)
                [second]=set(self.cache.pids(self.sid))-self.started-{pid}
                # Changing args puts grants under review: after rediscovery and re-apply nothing is published or started.
                self.store.update_service(self.sid,{'args':[FIXTURE,'--tools',TOOLS,'--extra']},self.v())
                self.store.record_discovery(self.sid,await discover_service(self.store.load()['services'][self.sid]),self.v())
                known=set(self.cache.pids(self.sid))
                await manager.apply_endpoint(current(),'default')
                self.assertTrue(await reaped([second]))
                ws,path,closed=await xiaozhi.accept()
                async with mcp_client(ws) as client:
                    self.assertEqual((await client.list_tools()).tools,[])
                self.assertEqual(set(self.cache.pids(self.sid)),known)
            finally:
                await manager.remove_endpoint('default')
        self.assertTrue(await reaped(self.cache.pids(self.sid)+self.cache.pids(self.idle)))


if __name__=='__main__':unittest.main()
