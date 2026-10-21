import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch
from catalog_runtime import bridge_catalog

class CancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_discovery_closes_initialized_session(self):
        entered=asyncio.Event();closed=asyncio.Event()
        class Session:
            async def list_tools(self,**kwargs):entered.set();await asyncio.Event().wait()
        @asynccontextmanager
        async def session(service):
            try:yield Session()
            finally:closed.set()
        config={'endpoint':'wss://api.xiaozhi.me/mcp/?token=synthetic','services':{'s':{'id':'s'}},'bindings':{'s':[{'name':'x','schema_hash':'h','alias':'x'}]}}
        with patch('catalog_runtime.service_session',session):
            task=asyncio.create_task(bridge_catalog(config,lambda *a:None))
            await asyncio.wait_for(entered.wait(),2);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(closed.is_set())

if __name__=='__main__':unittest.main()
