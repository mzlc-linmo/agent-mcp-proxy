import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from admin import create_app
from admin_config import ConfigStore
from admin_auth import Auth,password_hash
from policy import ALLOWED

class Manager:
    def snapshot(self):return {'state':'stopped'}
    async def test(self,config):return {'state':'tested'}
    async def apply(self,config):pass

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        store=ConfigStore(Path(self.tmp.name)/'data'/'config.json','http://higress:8080')
        store.initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=PRIVATE','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'PRIVATE'} for d in ALLOWED},'enabled_tools':[]})
        self.client=TestClient(create_app(store,Manager(),Auth(password_hash('long-test-password'),'admin'),'http://testserver'))
        self.origin={'Origin':'http://testserver'}
    def login(self):
        result=self.client.post('/api/login',json={'username':'admin','password':'long-test-password'},headers=self.origin)
        self.assertEqual(result.status_code,200)
        return {**self.origin,'X-CSRF-Token':result.json()['csrf']}
    def test_auth_csrf_and_redaction(self):
        self.assertEqual(self.client.get('/api/config').status_code,401)
        headers=self.login();r=self.client.get('/api/config');self.assertNotIn('PRIVATE',r.text)
        self.assertEqual(self.client.put('/api/config',json={'version':1,'enabled_tools':[]},headers=self.origin).status_code,403)
        self.assertEqual(self.client.put('/api/config',json={'version':1,'enabled_tools':[]},headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/apply',json={'version':1},headers=headers).status_code,409)
        self.assertEqual(self.client.post('/api/logout',headers=headers).status_code,204)
        self.assertEqual(self.client.get('/api/status').status_code,401)
    def test_cross_origin_body_limit_and_unknown_fields(self):
        self.assertEqual(self.client.post('/api/login',json={},headers={'Origin':'http://evil.example'}).status_code,403)
        headers=self.login()
        self.assertEqual(self.client.put('/api/config',content=b'x'*65537,headers=headers).status_code,413)
        self.assertEqual(self.client.put('/api/config',json={'version':1,'password_file':'/etc/passwd'},headers=headers).status_code,422)
        self.assertEqual(self.client.get('/api/status',headers={'Host':'evil.example'}).status_code,403)

if __name__=='__main__':unittest.main()
