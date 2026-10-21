import unittest
from service_import import parse_import,ImportError

class ImportTests(unittest.TestCase):
    def test_connections_export_api_key_and_bearer(self):
        rows=parse_import({'connections':[{'name':'ops-actions','serverName':'ops-actions','transport':'streamable-http','url':'http://192.168.1.8:8080/mcp/ops-actions','allowInsecurePrivateNetwork':True,'headers':{},'authMode':'api-key','apiKeyHeader':'X-Internal-Token','apiKeyValue':'synthetic'}, {'name':'GitHub','url':'https://example.com/mcp','authMode':'bearer','bearerToken':'synthetic-bearer'}]})
        self.assertEqual(rows[0]['auth'],{'mode':'api-key','header':'X-Internal-Token','value':'synthetic'})
        self.assertEqual(rows[1]['auth'],{'mode':'bearer','value':'synthetic-bearer'})
    def test_market_auth_applies_to_service_not_descriptor_request(self):
        rows=parse_import({'mcpServers':{'vendor':{'url':'https://example.com/mcp'}}},auth_override={'mode':'bearer','value':'synthetic'})
        self.assertEqual(rows[0]['auth']['value'],'synthetic')
    def test_bearer_import_and_sse(self):
        rows=parse_import({'mcpServers':{'one':{'url':'https://example.com/mcp','type':'streamable-http','headers':{'Authorization':'Bearer synthetic'}},'two':{'url':'https://example.com/sse','type':'sse'}}})
        self.assertEqual(rows[0]['auth'],{'mode':'bearer','value':'synthetic'})
        self.assertEqual(rows[1]['transport'],'sse')
    def test_commands_extra_headers_and_unconfirmed_private_rejected(self):
        for entry in [{'command':'npx','args':['bad']},{'url':'https://example.com','headers':{'Host':'evil'}},{'url':'http://10.0.0.1/mcp'}]:
            with self.subTest(entry=entry),self.assertRaises(ImportError):parse_import({'mcpServers':{'x':entry}})
    def test_private_confirmation_and_descriptor(self):
        rows=parse_import({'name':'Vendor','mcpServers':{'vendor':{'url':'http://10.0.0.1/mcp','allowInsecurePrivateNetwork':True}}})
        self.assertTrue(rows[0]['private_network'])

if __name__=='__main__':unittest.main()
