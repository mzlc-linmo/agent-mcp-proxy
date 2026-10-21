from test_admin_config import ConfigTests
from admin_config import ConfigError

class UpstreamTests(ConfigTests):
    def test_additional_upstream_is_versioned_and_does_not_auto_grant_tools(self):
        self.store.add_upstream({'id':'extra','url':'http://higress:8080/mcp/extra','key':'PRIVATE','tools':['extra_lookup']},1)
        value=self.store.load()
        self.assertIn('extra',value['upstreams'])
        self.assertNotIn('extra_lookup',value['endpoints'][0]['enabled_tools'])
        self.assertEqual(self.store.public()['available_tools']['extra'],['extra_lookup'])
        self.assertNotIn('PRIVATE',str(self.store.public()))
        with self.assertRaises(ConfigError):self.store.add_upstream({'id':'bad','url':'http://169.254.169.254/','key':'','tools':[]},2)
