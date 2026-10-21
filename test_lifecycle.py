import asyncio
import unittest
from lifecycle import BridgeManager

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_closes_old_runner_before_replacement(self):
        events=[]
        async def runner(config,report):
            events.append(('start',config['version']));report('connected',{'tools':[]})
            try:await asyncio.Event().wait()
            finally:events.append(('stop',config['version']))
        manager=BridgeManager(runner=runner)
        try:
            await manager.apply({'version':1});await asyncio.sleep(0)
            self.assertEqual(manager.snapshot()['state'],'connected')
            await manager.apply({'version':2});await asyncio.sleep(0)
            self.assertEqual(events[:3],[('start',1),('stop',1),('start',2)])
        finally:await manager.stop()
        self.assertEqual(manager.snapshot()['state'],'stopped')

    async def test_test_only_uses_supplied_discovery_and_never_runner(self):
        called=[]
        async def runner(*a):raise AssertionError('WSS started by test')
        async def discover(config):called.append(config['version']);return {'ops-ai':{'state':'discovered'}}
        manager=BridgeManager(runner=runner,discover=discover)
        result=await manager.test({'version':1})
        self.assertEqual(called,[1]);self.assertEqual(result['ops-ai']['state'],'discovered')
        self.assertEqual(manager.snapshot()['state'],'stopped')

if __name__=='__main__':unittest.main()
