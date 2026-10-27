"""One independent bridge owner per enabled endpoint; no shared MCP sessions."""
import asyncio
import copy
from lifecycle import BridgeManager,discover
from admin_config import ConfigError

def single_config(config,endpoint):
    if config.get('schema_version')==3:
        from catalog_runtime import device_snapshot
        return device_snapshot(config,endpoint['id'])
    return {'version':config['version'],'endpoint':endpoint['endpoint'],'enabled_tools':list(endpoint['enabled_tools']),'upstreams':copy.deepcopy(config['upstreams'])}

class EndpointManager:
    def __init__(self,factory=BridgeManager):
        self.managers={};self.factory=factory;self.lock=asyncio.Lock()
    def snapshot(self):return {'endpoints':{eid:m.snapshot() for eid,m in self.managers.items()}}
    async def start(self,config):await self.apply(config)
    async def apply(self,config):
        async with self.lock:
            keep={e['id'] for e in config.get('devices',config.get('endpoints',[]))}
            for eid in list(self.managers):
                if eid not in keep:await self.managers.pop(eid).stop()
            for e in config.get('devices',config.get('endpoints',[])):await self._apply(config,e)
    async def _apply(self,config,e):
        if e['id'] not in self.managers:
            if config.get('schema_version')==3:
                from catalog_runtime import bridge_catalog
                self.managers[e['id']]=BridgeManager(runner=bridge_catalog)
            else:self.managers[e['id']]=self.factory()
        m=self.managers[e['id']]
        if e['enabled']:await m.apply(single_config(config,e))
        else:await m.stop()
    async def apply_endpoint(self,config,eid):
        async with self.lock:
            e=next((e for e in config.get('devices',config.get('endpoints',[])) if e['id']==eid),None)
            if e is None:raise ConfigError('Endpoint not found',404)
            await self._apply(config,e)
    async def remove_endpoint(self,eid):
        async with self.lock:
            if eid in self.managers:await self.managers.pop(eid).stop()
    async def stop(self):
        async with self.lock:
            for m in self.managers.values():await m.stop()
            self.managers.clear()
    async def test(self,config):
        if config.get('schema_version')==3:
            from service_transport import discover_service
            from feishu_stdio import error_code
            result={}
            for sid,service in config['services'].items():
                try:result[sid]={'state':'discovered','count':len(await discover_service(service))}
                except Exception as error:
                    code=error_code(error)
                    result[sid]={'state':'unavailable'}|({'error_code':code} if code else {})
            return result
        return await discover(config)
