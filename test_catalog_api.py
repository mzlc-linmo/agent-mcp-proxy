import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from admin import create_app
from admin_auth import Auth,password_hash
from admin_config import ConfigStore
from catalog import CatalogStore
from policy import ALLOWED

class Manager:
    def snapshot(self):return {'endpoints':{}}
    async def remove_endpoint(self,eid):pass
    async def apply_endpoint(self,c,eid):pass

class CatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'config.json';old=ConfigStore(path,'http://higress:8080')
        old.initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=SECRET','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'SECRET'} for d in ALLOWED},'enabled_tools':[]})
        self.store=CatalogStore(path)
        self.client=TestClient(create_app(self.store,Manager(),Auth(password_hash('long-test-password'),'admin'),'http://testserver'))
        result=self.client.post('/api/login',json={'username':'admin','password':'long-test-password'},headers={'Origin':'http://testserver'})
        self.headers={'Origin':'http://testserver','X-CSRF-Token':result.json()['csrf']}
    def test_json_import_preview_then_save_and_device_delete(self):
        document={'mcpServers':{'example':{'url':'https://example.com/mcp','type':'streamable-http','headers':{'Authorization':'Bearer synthetic'}}}}
        preview=self.client.post('/api/services/import',json={'version':1,'document':document,'preview':True},headers=self.headers)
        self.assertEqual(preview.status_code,200,preview.text)
        saved=self.client.post('/api/services/import',json={'version':1,'document':document},headers=self.headers)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(len(self.store.load()['services']),5)
        deleted=self.client.request('DELETE','/api/devices/default',json={'version':2},headers=self.headers)
        self.assertEqual(deleted.status_code,200,deleted.text)
        self.assertEqual(self.store.load()['devices'],[])

    def test_device_creation_normalizes_paste_and_reports_safe_specific_errors(self):
        valid='wss://api.xiaozhi.me/mcp/?token=NEW-SYNTHETIC'
        result=self.client.post('/api/devices',json={'version':1,'name':'  New device  ','endpoint':'  '+valid+'\n'},headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        value=self.store.load();self.assertEqual(value['devices'][1]['name'],'New device')
        duplicate=self.client.post('/api/devices',json={'version':2,'name':'Duplicate','endpoint':valid},headers=self.headers)
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(duplicate.json()['error_code'],'duplicate_endpoint')
        self.assertNotIn('NEW-SYNTHETIC',duplicate.text)
        invalid=self.client.post('/api/devices',json={'version':2,'name':'Invalid','endpoint':'https://example.com/'},headers=self.headers)
        self.assertEqual(invalid.status_code,422)
        self.assertEqual(invalid.json()['error_code'],'invalid_wss_endpoint')

    def test_market_import_persists_service_auth_without_echoing_secret(self):
        entries=[{'name':'Vendor','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'bearer','value':'synthetic-market-secret'},'private_network':False}]
        with patch('service_import.fetch_descriptor',new=AsyncMock(return_value=entries)) as fetch:
            response=self.client.post('/api/services/import',json={'version':1,'descriptor_url':'https://example.com/descriptor.json','service_auth':{'mode':'bearer','value':'synthetic-market-secret'}},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('synthetic-market-secret',response.text)
        sid=response.json()['ids'][0]
        self.assertEqual(self.store.load()['services'][sid]['auth']['mode'],'bearer')
        self.assertEqual(fetch.await_args.kwargs['auth_override']['value'],'synthetic-market-secret')

    def test_bound_device_requires_explicit_deletion_confirmation(self):
        self.store.load()
        self.store.update_device('default',{'bindings':{'ops-actions':['ops_decision_adopt']}},1)
        response=self.client.request('DELETE','/api/devices/default',json={'version':2},headers=self.headers)
        self.assertEqual(response.status_code,409)
        self.assertEqual(len(self.store.load()['devices']),1)
        response=self.client.request('DELETE','/api/devices/default',json={'version':2,'confirm_bound':True},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.load()['devices'],[])

    def test_removed_legacy_routes_return_explicit_migration_response(self):
        self.assertEqual(self.client.get('/api/endpoints').status_code,410)
        self.assertEqual(self.client.put('/api/config',json={'version':1},headers=self.headers).status_code,410)

    def test_service_and_device_routes_are_separate_and_authenticated(self):
        r=self.client.get('/api/devices');self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['devices']),1);self.assertNotIn('SECRET',r.text)
        r=self.client.post('/api/services',json={'version':1,'name':'External','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False},headers=self.headers)
        self.assertEqual(r.status_code,200);sid=r.json()['id']
        with patch('service_transport.discover_service',new=AsyncMock(return_value=[{'name':'lookup','inputSchema':{'type':'object'}}])):
            r=self.client.post('/api/services/'+sid+'/discover',json={'version':2},headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/services/'+sid).json()['service']['discovery'][0]['name'],'lookup')
        self.assertEqual(self.client.get('/api/devices/missing').status_code,404)
        self.assertEqual(self.client.put('/api/devices/default',json={'version':3,'enabled':False},headers={'Origin':'http://testserver'}).status_code,403)

if __name__=='__main__':unittest.main()
