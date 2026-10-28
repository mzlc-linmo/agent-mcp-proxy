import asyncio
import json
import logging
import os
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
import feishu_readonly
import feishu_stdio
from feishu_stdio import FeishuRuntimeError
from service_transport import discover_service,service_session

FIXTURE=str(Path(__file__).parent/'tests'/'fixtures'/'feishu_stdio_server.py')
SECRET='synthetic-feishu-secret-stdio'


class FixtureRuntime:
    """Points only the fixed launch parameters at the synthetic server; stdio_client/ClientSession stay real."""
    def __init__(self,case):
        self.dir=Path(tempfile.mkdtemp(prefix='feishu-fixture-'));case.addCleanup(lambda:__import__('shutil').rmtree(self.dir,ignore_errors=True))
        original=feishu_readonly.stdio_parameters
        def parameters(service,home):
            real=original(service,home)
            return real.model_copy(update={'command':self.command,'args':[FIXTURE]+real.args[1:],'cwd':str(self.dir)})
        self.command=sys.executable
        p=patch('feishu_readonly.stdio_parameters',parameters);p.start();case.addCleanup(p.stop)
    def mode(self,**mode):(self.dir/'mode.json').write_text(json.dumps(mode))
    def pids(self):return sorted(int(p.name[4:]) for p in self.dir.glob('pid-*'))
    def launch(self,pid):return json.loads((self.dir/('launch-%d.json'%pid)).read_text())
    def calls(self):
        f=self.dir/'calls.jsonl'
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    async def wait_file(self,prefix,timeout=10):
        async with asyncio.timeout(timeout):
            while not list(self.dir.glob(prefix+'*')):await asyncio.sleep(.02)
        return int(next(self.dir.glob(prefix+'*')).name[len(prefix):])


def alive(pid):
    """True until the operating system has reaped the process; zombies count as not yet reaped."""
    try:return Path('/proc/%d/stat'%pid).exists()
    except OSError:return False


async def reaped(pids,timeout=10):
    async with asyncio.timeout(timeout):
        while any(alive(p) for p in pids):await asyncio.sleep(.05)
    return True


def service(sid='fs1',secret=SECRET):
    return {'id':sid,'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':secret,'discovery':[],'legacy_profile':None}


class StdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.runtime=FixtureRuntime(self)
    def tearDown(self):self.assertEqual(feishu_stdio.active(),0);self.assertEqual(feishu_stdio._discovering,set())

    async def test_stdio_discovery_and_cleanup_on_cancel(self):
        polluted={'NODE_OPTIONS':'--require /tmp/evil.js','USER_ACCESS_TOKEN':'u-synthetic','HTTPS_PROXY':'http://proxy.invalid','HTTP_PROXY':'http://proxy.invalid','LARK_DOMAIN':'https://evil.example','APP_SECRET':'parent-secret'}
        with patch.dict(os.environ,polluted):
            tools=await discover_service(service())
        names=[t['name'] for t in tools]
        # Both pages were read; the write and unknown tools still reach the catalog filter, which drops them.
        self.assertEqual(set(names),feishu_readonly.WIRE_TOOL_NAMES|{'im_v1_message_create','unknown_tool'})
        self.assertEqual(sorted(t['name'] for t in tools if feishu_readonly.tool_allowed(service(),t['name'])),sorted(feishu_readonly.WIRE_TOOL_NAMES))
        [pid]=self.runtime.pids();self.assertTrue(await reaped([pid]))
        launch=self.runtime.launch(pid)
        self.assertFalse(launch['secret_in_argv'])
        self.assertIn('tenant_access_token',launch['argv']);self.assertNotIn('--oauth',launch['argv'])
        self.assertTrue(set(launch['env'])>={'APP_ID','APP_SECRET','HOME','PATH'})
        for key in polluted:
            if key!='APP_SECRET':self.assertNotIn(key,launch['env'])
        # Cancellation while the server hangs in tools/list kills and reaps the child, then frees the slot.
        self.runtime.mode(hang_list=True)
        task=asyncio.create_task(discover_service(service()))
        pid=await self.runtime.wait_file('listing-')
        self.assertTrue(alive(pid));self.assertEqual(feishu_stdio.active(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(await reaped([pid]));self.assertEqual(feishu_stdio.active(),0)
        self.runtime.mode()
        self.assertEqual(len(await discover_service(service())),8)

    async def test_capacity_busy_failures_and_termination_do_not_leak(self):
        async with AsyncExitStack() as stack:
            for i in range(feishu_stdio.LIMIT):await stack.enter_async_context(service_session(service('s%d'%i)))
            self.assertEqual(feishu_stdio.active(),10)
            with self.assertRaises(FeishuRuntimeError) as caught:
                async with service_session(service('s10')):pass
            self.assertEqual(caught.exception.code,'feishu_capacity')
            # Discovery counts against the same process limit.
            with self.assertRaises(FeishuRuntimeError) as caught:await discover_service(service('s11'))
            self.assertEqual(caught.exception.code,'feishu_capacity')
            pids=self.runtime.pids();self.assertEqual(len(pids),10)
        self.assertTrue(await reaped(pids));self.assertEqual(feishu_stdio.active(),0)
        # One managed discovery per service; a second one is refused at once, other services proceed.
        self.runtime.mode(hang_list=True)
        first=asyncio.create_task(discover_service(service('busy')))
        pid=await self.runtime.wait_file('listing-')
        with self.assertRaises(FeishuRuntimeError) as caught:await discover_service(service('busy'))
        self.assertEqual(caught.exception.code,'feishu_discovery_busy')
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertTrue(await reaped([pid]))
        # A discovery timeout also reaps the hanging child.
        for p in self.runtime.dir.glob('listing-*'):p.unlink()
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(1.5):await discover_service(service('busy'))
        self.assertTrue(await reaped(self.runtime.pids()))
        self.runtime.mode()
        self.assertEqual(len(await discover_service(service('busy'))),8)
        # Missing runtime, crash with the secret on stderr and a stubborn process all fail safely.
        logs=[];handler=logging.Handler();handler.emit=lambda record:logs.append(record.getMessage());logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler,handler)
        self.runtime.command='/opt/feishu-node/bin/missing-node'
        with self.assertRaises(FeishuRuntimeError) as caught:await discover_service(service('missing'))
        self.assertEqual(caught.exception.code,'feishu_runtime_unavailable')
        self.runtime.command=sys.executable
        self.runtime.mode(crash=True)
        with self.assertRaises(FeishuRuntimeError) as caught:await discover_service(service('crash'))
        self.assertEqual(caught.exception.code,'feishu_session_unavailable')
        self.assertNotIn(SECRET,str(caught.exception)+repr(caught.exception));self.assertIsNone(caught.exception.__cause__)
        self.runtime.mode(stubborn=True)
        before=set(self.runtime.pids())
        async with service_session(service('stubborn')) as session:
            await session.list_tools()
            [pid]=set(self.runtime.pids())-before
        self.assertTrue(await reaped([pid]))
        self.assertFalse(any(SECRET in line for line in logs))
        self.assertTrue(await reaped(self.runtime.pids()))


class AdminMappingTests(unittest.TestCase):
    def test_discovery_errors_map_to_safe_status_and_keep_snapshot(self):
        from test_feishu_catalog import FeishuCatalogTests
        case=FeishuCatalogTests('test_feishu_crud_redaction_and_rotation_requires_review');case.setUp();self.addCleanup(case.doCleanups)
        sid=case.add().json()['id'];self.assertEqual(case.discover(sid).status_code,200)
        before=case.store.load()['services'][sid]['discovery']
        for code,status in (('feishu_discovery_busy',429),('feishu_capacity',429),('feishu_runtime_unavailable',503),('feishu_session_unavailable',503)):
            with self.subTest(code=code),patch('service_transport.discover_service',new=AsyncMock(side_effect=FeishuRuntimeError(code))):
                r=case.client.post('/api/services/'+sid+'/discover',json={'version':case.version()},headers=case.headers)
                self.assertEqual(r.status_code,status);self.assertEqual(r.json()['error_code'],code)
                self.assertEqual(case.store.load()['services'][sid]['discovery'],before)
        with patch('service_transport.discover_service',new=AsyncMock(side_effect=ExceptionGroup('x',[FeishuRuntimeError('feishu_capacity')]))):
            self.assertEqual(case.client.post('/api/services/'+sid+'/discover',json={'version':case.version()},headers=case.headers).status_code,429)
        with patch('service_transport.discover_service',new=AsyncMock(side_effect=ValueError('upstream '+SECRET))):
            r=case.client.post('/api/services/'+sid+'/discover',json={'version':case.version()},headers=case.headers)
            self.assertEqual(r.status_code,502);self.assertNotIn(SECRET,r.text)

    def test_management_test_reports_safe_feishu_code(self):
        from endpoint_manager import EndpointManager
        async def run():
            with patch('service_transport.discover_service',new=AsyncMock(side_effect=FeishuRuntimeError('feishu_capacity'))):
                return await EndpointManager().test({'schema_version':3,'services':{'fs':service('fs')}})
        self.assertEqual(asyncio.run(run()),{'fs':{'state':'unavailable','error_code':'feishu_capacity'}})


if __name__=='__main__':unittest.main()
