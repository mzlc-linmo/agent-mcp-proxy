import unittest
from test_admin_config import ConfigTests
from admin_config import ConfigError

class EndpointTests(ConfigTests):
    def test_v1_migration_preserves_default_and_new_endpoint_is_disabled(self):
        value=self.store.load()
        self.assertEqual(value['schema_version'],2)
        self.assertEqual(value['endpoints'][0]['id'],'default')
        old=value['endpoints'][0]['enabled_tools']
        result=self.store.add_endpoint({'name':'Second','platform':'xiaozhi','endpoint':'wss://api.xiaozhi.me/mcp/?token=SECOND'},1)
        value=self.store.load();new=value['endpoints'][1]
        self.assertFalse(new['enabled']);self.assertEqual(new['enabled_tools'],[])
        self.assertEqual(value['endpoints'][0]['enabled_tools'],old)
        self.assertNotIn('SECOND',str(self.store.public()))
        with self.assertRaises(ConfigError):self.store.add_endpoint({'name':'dup','platform':'xiaozhi','endpoint':new['endpoint']},2)

    def test_update_and_delete_are_versioned_and_preserve_other_endpoint(self):
        self.store.add_endpoint({'name':'Second','platform':'xiaozhi','endpoint':'wss://api.xiaozhi.me/mcp/?token=SECOND'},1)
        eid=self.store.load()['endpoints'][1]['id']
        self.store.update_endpoint(eid,{'enabled':True,'enabled_tools':[]},2)
        self.assertTrue(self.store.load()['endpoints'][1]['enabled'])
        self.store.delete_endpoint(eid,3)
        self.assertEqual(len(self.store.load()['endpoints']),1)
        with self.assertRaises(ConfigError):self.store.delete_endpoint('default',4)

if __name__=='__main__':unittest.main()
