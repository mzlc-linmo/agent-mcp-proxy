import asyncio
import unittest
from endpoint_manager import EndpointManager
from lifecycle import BridgeManager

class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failing_endpoint_does_not_stop_healthy_endpoint(self):
        async def runner(config,report):
            if config['endpoint']=='bad':raise ValueError('synthetic failure')
            report('connected',{'tools':[]})
            await asyncio.Event().wait()
        manager=EndpointManager(lambda:BridgeManager(runner=runner))
        cfg={'version':1,'upstreams':{},'endpoints':[{'id':i,'endpoint':url,'enabled':True,'enabled_tools':[]} for i,url in [('a','bad'),('b','good')]]}
        try:
            await manager.apply(cfg);await asyncio.sleep(.02)
            self.assertEqual(manager.snapshot()['endpoints']['a']['state'],'error')
            self.assertEqual(manager.snapshot()['endpoints']['b']['state'],'connected')
            await manager.remove_endpoint('a')
            self.assertEqual(manager.snapshot()['endpoints']['b']['state'],'connected')
        finally:await manager.stop()

    async def test_concurrent_apply_never_overlaps_and_disabled_endpoint_stays_off(self):
        active=0;maximum=0
        async def runner(config,report):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            try:await asyncio.Event().wait()
            finally:active-=1
        manager=EndpointManager(lambda:BridgeManager(runner=runner))
        cfg={'version':1,'upstreams':{},'endpoints':[{'id':'a','endpoint':'x','enabled':True,'enabled_tools':[]},{'id':'b','endpoint':'y','enabled':False,'enabled_tools':[]}]}
        try:
            await manager.apply(cfg);await asyncio.sleep(0)
            await asyncio.gather(manager.apply_endpoint(cfg,'a'),manager.apply_endpoint(cfg,'a'))
            await asyncio.sleep(0)
            self.assertEqual(maximum,1)
            self.assertEqual(manager.snapshot()['endpoints']['b']['state'],'stopped')
        finally:await manager.stop()

if __name__=='__main__':unittest.main()
