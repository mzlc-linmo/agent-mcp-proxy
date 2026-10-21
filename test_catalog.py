import unittest
import tempfile
import json
from pathlib import Path
from admin_config import ConfigStore
from catalog import CatalogStore
from policy import ALLOWED

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/'data'/'config.json'
        old=ConfigStore(p,'http://higress:8080')
        old.initialize({'endpoint':'wss://api.xiaozhi.me/mcp/?token=PRIVATE','upstreams':{d:{'url':'http://higress:8080/mcp/'+d,'key':'PRIVATE'} for d in ALLOWED},'enabled_tools':sorted(set().union(*ALLOWED.values()))})
        self.store=CatalogStore(p)
    def test_empty_optional_key_migrates_and_original_is_backed_up(self):
        raw=json.loads(self.store.path.read_text());raw['upstreams']['ops-ai']['key']='';self.store.path.write_text(json.dumps(raw))
        original=self.store.path.read_bytes();value=self.store.load()
        self.assertEqual(value['services']['ops-ai']['auth']['mode'],'none')
        self.assertEqual(self.store.path.with_suffix('.v2-backup.json').read_bytes(),original)

    def test_service_change_requires_fresh_private_confirmation_and_revokes_legacy_bypass(self):
        from admin_config import ConfigError
        from catalog_runtime import approved_tools
        self.store.load()
        with self.assertRaises(ConfigError):self.store.update_service('ops-readonly',{'url':'http://10.0.0.8/mcp'},1)
        self.store.update_service('ops-readonly',{'auth':{'mode':'api-key','value':'NEW'}},1)
        value=self.store.load();s=value['services']['ops-readonly'];grants=value['devices'][0]['bindings']['ops-readonly']
        tools=[{'name':'ops_decisions_first_page','inputSchema':{}}]
        self.assertEqual(approved_tools(s,grants,tools),[])

    def test_unrelated_device_edit_preserves_stale_grants_until_explicit_review(self):
        self.store.load()
        original=self.store.load()['devices'][0]['bindings']
        self.store.update_service('ops-readonly',{'auth':{'mode':'api-key','value':'NEW'}},1)
        self.store.update_device('default',{'name':'Renamed'},2)
        grants=self.store.load()['devices'][0]['bindings']
        self.assertEqual(grants['ops-actions'],original['ops-actions'])
        self.assertEqual(grants['ops-readonly'][0]['schema_hash'],'review-required')

    def test_binding_patch_preserves_unmentioned_unavailable_service(self):
        self.store.load()
        self.store.update_service('ops-readonly',{'auth':{'mode':'api-key','value':'NEW'}},1)
        before=self.store.load()['devices'][0]['bindings']['ops-readonly']
        self.store.update_device('default',{'bindings':{'ops-actions':[]}},2)
        bindings=self.store.load()['devices'][0]['bindings']
        self.assertEqual(bindings['ops-readonly'],before)
        self.assertNotIn('ops-actions',bindings)

    def test_v2_migration_preserves_six_tools_and_private_credentials(self):
        value=self.store.load()
        self.assertEqual(value['schema_version'],3)
        self.assertEqual(len(value['devices'][0]['bindings']),4)
        self.assertEqual(sum(len(v) for v in value['devices'][0]['bindings'].values()),6)
        self.assertNotIn('PRIVATE',json.dumps(self.store.public()))
    def test_discovery_requires_explicit_grant_and_blocks_bound_service_deletion(self):
        from admin_config import ConfigError
        value=self.store.load()
        sid=self.store.add_service({'name':'Market','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False},1)['id']
        tools=[{'name':'lookup','description':'lookup','inputSchema':{'type':'object','properties':{}}}]
        self.store.record_discovery(sid,tools,2)
        self.assertNotIn(sid,self.store.load()['devices'][0]['bindings'])
        self.store.update_device('default',{'bindings':{sid:['lookup']}},3)
        with self.assertRaises(ConfigError):self.store.delete_service(sid,4)
        grant=self.store.load()['devices'][0]['bindings'][sid][0]
        self.assertTrue(grant['alias'].startswith(sid+'__'))
        self.assertEqual(len(grant['schema_hash']),64)

    def test_device_creation_is_disabled_and_version_conflicts_do_not_overwrite(self):
        from admin_config import ConfigError
        self.store.load()
        result=self.store.add_device({'name':'Second','endpoint':'wss://api.xiaozhi.me/mcp/?token=SECOND'},1)
        device=self.store.load()['devices'][1]
        self.assertFalse(device['enabled']);self.assertEqual(device['bindings'],{})
        with self.assertRaises(ConfigError):self.store.update_device(result['id'],{'enabled':True},1)

    def test_external_service_registration_has_no_device_auto_grant(self):
        self.store.load()
        result=self.store.add_service({'name':'Market service','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False},1)
        value=self.store.load();self.assertNotIn(result['id'],value['devices'][0]['bindings'])
        self.assertEqual(value['services'][result['id']]['discovery'],[])

if __name__=='__main__':unittest.main()
