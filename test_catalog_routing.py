import unittest
from catalog_runtime import device_snapshot,approved_tools
from catalog import schema_hash

class RoutingTests(unittest.TestCase):
    def test_stale_schema_and_ungranted_tools_are_not_published(self):
        schema={'type':'object','properties':{}}
        tools=[{'name':'lookup','inputSchema':schema},{'name':'new_tool','inputSchema':schema}]
        service={'discovery':[{'name':'lookup','schema_hash':schema_hash(schema)}]}
        grants=[{'name':'lookup','schema_hash':schema_hash(schema),'alias':'s__lookup'}]
        self.assertEqual([x[0] for x in approved_tools(service,grants,tools)],['s__lookup'])
        tools[0]['inputSchema']={'type':'object','required':['new']}
        self.assertEqual(approved_tools(service,grants,tools),[])

    def test_only_devices_bound_services_enter_snapshot(self):
        config={'version':5,'services':{'a':{'id':'a'},'b':{'id':'b'}},'devices':[{'id':'one','endpoint':'wss://example','bindings':{'a':[]}}]}
        value=device_snapshot(config,'one')
        self.assertEqual(set(value['services']),{'a'})
        self.assertNotIn('b',value['services'])

if __name__=='__main__':unittest.main()
