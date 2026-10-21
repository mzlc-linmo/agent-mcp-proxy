"""Own one WSS runner and expose only sanitized connection evidence."""
import asyncio
import copy
from datetime import datetime, timezone
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from proxy import bridge
from policy import ALLOWED
from admin_config import ConfigError

async def discover(config):
    result={}
    async def domain_test(domain,entry):
        if not entry.get('key'):return {'state':'credential_missing'}
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(headers={'X-Internal-Token':entry['key']},timeout=8,follow_redirects=False) as client:
                    async with streamable_http_client(entry['url'],http_client=client) as streams:
                        async with ClientSession(streams[0],streams[1]) as session:
                            await session.initialize();tools=(await session.list_tools()).tools
                            return {'state':'discovered','discovered':len(tools),'allowed':sum(t.name in (ALLOWED[domain] if domain in ALLOWED else entry.get('tools',[])) and t.name in config['enabled_tools'] for t in tools)}
        except Exception:return {'state':'unavailable'}
    async with asyncio.timeout(30):
        values=await asyncio.gather(*(domain_test(d,e) for d,e in config['upstreams'].items()))
    for domain,value in zip(config['upstreams'],values):result[domain]=value
    return result

class BridgeManager:
    def __init__(self,runner=bridge,discover=discover):
        self.runner=runner;self.discover=discover;self.task=None
        self.lock=asyncio.Lock();self.test_lock=asyncio.Lock()
        self.state={'state':'stopped','applied_version':None,'connected_at':None,'reconnect_count':0,'upstreams':{},'published_tools':[],'events':[]}

    def snapshot(self):return copy.deepcopy(self.state)

    def report(self,event,data):
        if event=='upstream':self.state['upstreams'][data['domain']]={k:v for k,v in data.items() if k!='domain'}
        elif event=='connected':
            self.state.update(state='connected',connected_at=datetime.now(timezone.utc).isoformat(),published_tools=list(data['tools']))
        self.state['events']=(self.state['events']+[{'time':datetime.now(timezone.utc).isoformat(),'category':event}])[-50:]

    async def _run(self,config):
        delay=1
        while True:
            self.state.update(state='connecting',connected_at=None,published_tools=[],upstreams={})
            try:await self.runner(config,self.report)
            except asyncio.CancelledError:raise
            except Exception:pass
            was_connected=self.state['state']=='connected'
            self.state.update(state='error',connected_at=None,published_tools=[])
            self.report('connection_unavailable',{})
            self.state['reconnect_count']+=1
            if was_connected:delay=1
            await asyncio.sleep(delay);delay=min(delay*2,60)

    async def _stop(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
            self.task=None
        self.state.update(state='stopped',connected_at=None,published_tools=[])

    async def apply(self,config):
        async with self.lock:
            self.state['state']='applying'
            await self._stop()
            self.state.update(state='applying',applied_version=config['version'])
            self.task=asyncio.create_task(self._run(copy.deepcopy(config)))

    async def start(self,config):await self.apply(config)
    async def stop(self):
        async with self.lock:await self._stop()

    async def test(self,config):
        if self.test_lock.locked():raise ConfigError('Connection test already running',429)
        async with self.test_lock:
            try:
                async with asyncio.timeout(30):return await self.discover(config)
            except Exception:return {'state':'unavailable'}
