import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from admin import create_app
from admin_auth import Auth,password_hash
from admin_config import ConfigStore,ConfigError
from catalog import CatalogStore,schema_hash
from catalog_runtime import approved_tools
from policy import ALLOWED

SECRET='synthetic-feishu-secret-A'
ROTATED='synthetic-feishu-secret-B'
SCHEMA={'type':'object','properties':{'page_size':{'type':'integer'}}}
TOOLS=[{'name':'im_v1_chat_list','description':'群列表','inputSchema':SCHEMA},{'name':'docx_v1_document_rawContent','inputSchema':SCHEMA},{'name':'im_v1_message_create','inputSchema':SCHEMA,'annotations':{'readOnlyHint':True}}]

class Manager:
    def __init__(self):self.removed=[]
    def snapshot(self):return {'endpoints':{}}
    async def remove_endpoint(self,eid):self.removed.append(eid)
    async def apply_endpoint(self,c,eid):pass

class FeishuCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'data'/'config.json';old=ConfigStore(path,'http://higress:8080')
        old.initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=SECRET','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'SECRET'} for d in ALLOWED},'enabled_tools':[]})
        self.store=CatalogStore(path);self.store.load();self.manager=Manager()
        self.client=TestClient(create_app(self.store,self.manager,Auth(password_hash('long-test-password'),'admin'),'http://testserver'))
        result=self.client.post('/api/login',json={'username':'admin','password':'long-test-password'},headers={'Origin':'http://testserver'})
        self.headers={'Origin':'http://testserver','X-CSRF-Token':result.json()['csrf']}
    def version(self):return self.store.load()['version']
    def add(self,**extra):
        body={'version':self.version(),'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':SECRET}|extra
        return self.client.post('/api/services',json=body,headers=self.headers)
    def discover(self,sid,tools=TOOLS):
        with patch('service_transport.discover_service',new=AsyncMock(return_value=[dict(t,schema_hash=schema_hash(t['inputSchema'])) for t in tools])):
            return self.client.post('/api/services/'+sid+'/discover',json={'version':self.version()},headers=self.headers)
    def grant(self,eid,sid,names):
        return self.client.put('/api/devices/'+eid,json={'version':self.version(),'bindings':{sid:names}},headers=self.headers)

    def test_feishu_crud_redaction_and_rotation_requires_review(self):
        r=self.add();self.assertEqual(r.status_code,200,r.text);sid=r.json()['id']
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode),0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path.parent).st_mode),0o700)
        saved=self.store.load()['services'][sid]
        self.assertEqual(saved,{'id':sid,'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':SECRET,'discovery':[],'legacy_profile':None})
        for url in ('/api/services','/api/services/'+sid,'/api/config','/api/devices'):
            response=self.client.get(url);self.assertEqual(response.status_code,200,url);self.assertNotIn(SECRET,response.text)
        public=self.client.get('/api/services/'+sid).json()['service']
        self.assertEqual(public,{'id':sid,'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret_configured':True,'discovery':[]})
        # Discovery never binds a device automatically and keeps only the fixed list.
        self.assertEqual(self.discover(sid).status_code,200)
        value=self.store.load()
        self.assertEqual([t['name'] for t in value['services'][sid]['discovery']],['im_v1_chat_list','docx_v1_document_rawContent'])
        self.assertTrue(all(sid not in d['bindings'] for d in value['devices']))
        other=self.client.post('/api/devices',json={'version':self.version(),'name':'Second','endpoint':'wss://api.xiaozhi.me/mcp/?token=OTHER'},headers=self.headers).json()['id']
        self.assertEqual(self.grant('default',sid,['im_v1_chat_list','docx_v1_document_rawContent']).status_code,200)
        self.assertEqual(self.grant(other,sid,['im_v1_chat_list']).status_code,200)
        self.store.update_device('default',{'bindings':{'ops-actions':['ops_decision_adopt']}},self.version())
        before=self.store.load();ops_grants=before['devices'][0]['bindings']['ops-actions']
        # Empty secret edit keeps the private value and keeps grants.
        r=self.client.put('/api/services/'+sid,json={'version':self.version(),'name':'飞书只读2','app_secret':''},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);self.assertNotIn(SECRET,r.text)
        value=self.store.load();self.assertEqual(value['services'][sid]['app_secret'],SECRET);self.assertEqual(value['services'][sid]['name'],'飞书只读2')
        self.assertEqual(len(value['services'][sid]['discovery']),2)
        self.assertEqual(value['devices'][0]['bindings'][sid],before['devices'][0]['bindings'][sid])
        # Rotation clears the snapshot and marks every grant of this service for review on every device.
        r=self.client.put('/api/services/'+sid,json={'version':self.version(),'app_secret':ROTATED},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(sorted(r.json()['affected_device_ids']),sorted(['default',other]));self.assertNotIn(ROTATED,r.text)
        value=self.store.load();service=value['services'][sid]
        self.assertEqual(service['discovery'],[]);self.assertEqual(service['app_secret'],ROTATED)
        for device in value['devices']:self.assertTrue(all(g['schema_hash']=='review-required' for g in device['bindings'][sid]))
        self.assertEqual(value['devices'][0]['bindings']['ops-actions'],ops_grants)
        # Same schema rediscovered under the new app is still not publishable until a device save.
        self.assertEqual(self.discover(sid).status_code,200)
        value=self.store.load();service=value['services'][sid];grants=value['devices'][0]['bindings'][sid]
        actual=[{'name':t['name'],'inputSchema':t['inputSchema']} for t in TOOLS]
        self.assertEqual(approved_tools(service,grants,actual),[])
        self.assertEqual(self.grant('default',sid,['im_v1_chat_list']).status_code,200)
        value=self.store.load();grants=value['devices'][0]['bindings'][sid]
        self.assertEqual(grants[0]['schema_hash'],schema_hash(SCHEMA))
        self.assertEqual([a for a,_ in approved_tools(value['services'][sid],grants,actual)],[grants[0]['alias']])
        self.assertTrue(all(g['schema_hash']=='review-required' for g in value['devices'][1]['bindings'][sid]))
        # App ID rotation behaves the same; version conflicts do not write; bound services cannot be deleted.
        self.assertEqual(self.client.put('/api/services/'+sid,json={'version':self.version(),'app_id':'cli_other'},headers=self.headers).status_code,200)
        self.assertEqual(self.store.load()['devices'][0]['bindings'][sid][0]['schema_hash'],'review-required')
        raw=self.store.path.read_bytes()
        self.assertEqual(self.client.put('/api/services/'+sid,json={'version':1,'app_secret':'conflict-secret'},headers=self.headers).status_code,409)
        self.assertEqual(self.store.path.read_bytes(),raw)
        self.assertEqual(self.client.request('DELETE','/api/services/'+sid,json={'version':self.version()},headers=self.headers).status_code,409)
        for eid in ('default',other):self.assertEqual(self.grant(eid,sid,[]).status_code,200)
        self.assertEqual(self.client.request('DELETE','/api/services/'+sid,json={'version':self.version()},headers=self.headers).status_code,200)
        self.assertNotIn(sid,self.store.load()['services'])

    def test_feishu_rejects_mixed_execution_config_and_forged_grants(self):
        mixed=[{'command':'/bin/sh'},{'args':['-c','id']},{'env':{'NODE_OPTIONS':'x'}},{'tools':['im.v1.message.create']},{'user_access_token':'u-1'},{'url':'https://example.com/mcp'},{'auth':{'mode':'none'}},{'private_network':False},{'domain':'https://evil.example'},{'transport':'streamable-http'},{'transport':'sse'},{'provider':'feishu'},{'app_secret':''},{'app_id':''},{'app_id':'a\nb'},{'app_secret':'x'*8193},{'app_id':'x'*257}]
        for extra in mixed:
            with self.subTest(extra=extra):
                r=self.add(**extra);self.assertEqual(r.status_code,422,r.text);self.assertNotIn(SECRET,r.text)
        http={'name':'Stdio','url':'https://example.com/mcp','transport':'stdio','auth':{'mode':'none'},'private_network':False}
        self.assertEqual(self.client.post('/api/services',json={'version':self.version()}|http,headers=self.headers).status_code,422)
        self.assertEqual(self.client.post('/api/services',json={'version':self.version(),'provider':'feishu-readonly'}|http|{'transport':'streamable-http'},headers=self.headers).status_code,422)
        sid=self.add().json()['id']
        # Type switching in either direction is rejected.
        for patch_body in ({'url':'https://example.com/mcp'},{'transport':'streamable-http'},{'provider':'other'},{'auth':{'mode':'none'}},{'command':'/bin/sh'},{'env':{}},{'tools':[]},{'app_secret':None},{'app_id':''}):
            with self.subTest(patch=patch_body):self.assertEqual(self.client.put('/api/services/'+sid,json={'version':self.version()}|patch_body,headers=self.headers).status_code,422)
        for patch_body in ({'provider':'feishu-readonly'},{'transport':'stdio'},{'app_id':'cli_x'},{'app_secret':'s'}):
            with self.subTest(http_patch=patch_body):self.assertEqual(self.client.put('/api/services/ops-ai',json={'version':self.version()}|patch_body,headers=self.headers).status_code,422)
        # Grants outside the fixed list are rejected even if they appear in a stored snapshot.
        self.assertEqual(self.discover(sid).status_code,200)
        self.assertEqual(self.grant('default',sid,['im_v1_message_create']).status_code,422)
        raw=json.loads(self.store.path.read_text())
        raw['services'][sid]['discovery'].append({'name':'im_v1_message_create','inputSchema':SCHEMA,'annotations':{'readOnlyHint':True},'schema_hash':schema_hash(SCHEMA)})
        self.store.path.write_text(json.dumps(raw))
        with self.assertRaises(ConfigError):self.store.load()
        with self.assertRaises(ConfigError):self.store.update_device('default',{'bindings':{sid:['im_v1_message_create']}},raw['version'])
        raw['services'][sid]['discovery'].pop();raw['services'][sid]['command']='/bin/sh';self.store.path.write_text(json.dumps(raw))
        with self.assertRaises(ConfigError):self.store.load()
        raw['services'][sid].pop('command');self.store.path.write_text(json.dumps(raw));self.store.load()
        # Import cannot create Feishu entries or stdio without a command (plain command imports are allowed since CMP-395).
        for document in ({'mcpServers':{'lark':{'type':'stdio','url':'https://example.com/mcp'}}},{'connections':[{'name':'lark','transport':'stdio','url':'https://example.com/mcp'}]},{'mcpServers':{'lark':{'url':'https://example.com/mcp','provider':'feishu-readonly'}}}):
            with self.subTest(document=document):self.assertEqual(self.client.post('/api/services/import',json={'version':self.version(),'document':document},headers=self.headers).status_code,422)
        entries=[{'name':'lark','provider':'feishu-readonly','transport':'stdio','app_id':'a','app_secret':'b'}]
        with self.assertRaises(ConfigError):self.store.import_services(entries,self.version())
        # Global limits still apply: 21st service and 11th device fail.
        for i in range(20-len(self.store.load()['services'])):self.assertEqual(self.add(name='f%d'%i).status_code,200)
        r=self.add(name='overflow');self.assertEqual(r.status_code,422);self.assertNotIn(SECRET,r.text)
        for i in range(10-len(self.store.load()['devices'])):
            self.assertEqual(self.client.post('/api/devices',json={'version':self.version(),'name':'d%d'%i,'endpoint':'wss://api.xiaozhi.me/mcp/?token=T%d'%i},headers=self.headers).status_code,200)
        self.assertEqual(self.client.post('/api/devices',json={'version':self.version(),'name':'over','endpoint':'wss://api.xiaozhi.me/mcp/?token=OVER'},headers=self.headers).status_code,422)

    def test_failed_atomic_write_keeps_previous_file_and_hides_secret(self):
        before=self.store.path.read_bytes()
        with patch('os.replace',side_effect=OSError('disk full '+SECRET)):
            r=self.add()
        self.assertGreaterEqual(r.status_code,500);self.assertNotIn(SECRET,r.text)
        self.assertEqual(self.store.path.read_bytes(),before)

if __name__=='__main__':unittest.main()
