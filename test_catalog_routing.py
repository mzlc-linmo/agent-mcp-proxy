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

    def test_feishu_whitelist_overrides_forged_snapshot_and_read_only_hint(self):
        schema={'type':'object','properties':{'useUAT':{'type':'boolean'}}};h=schema_hash(schema)
        service={'provider':'feishu-readonly','discovery':[{'name':n,'schema_hash':h} for n in ('im_v1_chat_list','im_v1_message_create')]}
        grants=[{'name':n,'schema_hash':h,'alias':'f__'+n} for n in ('im_v1_chat_list','im_v1_message_create')]
        tools=[{'name':n,'inputSchema':schema,'annotations':{'readOnlyHint':True}} for n in ('im_v1_chat_list','im_v1_message_create')]
        self.assertEqual([x[0] for x in approved_tools(service,grants,tools)],['f__im_v1_chat_list'])
        # HTTP services keep their previous behaviour for the same names.
        self.assertEqual(len(approved_tools({'discovery':service['discovery']},grants,tools)),2)

    def test_only_devices_bound_services_enter_snapshot(self):
        config={'version':5,'services':{'a':{'id':'a'},'b':{'id':'b'}},'devices':[{'id':'one','endpoint':'wss://example','bindings':{'a':[]}}]}
        value=device_snapshot(config,'one')
        self.assertEqual(set(value['services']),{'a'})
        self.assertNotIn('b',value['services'])

if __name__=='__main__':unittest.main()
