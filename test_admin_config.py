import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from admin_config import ConfigStore, ConfigError
from policy import ALLOWED

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'private'/'config.json'
        self.store=ConfigStore(self.path,'http://higress:8080')
        self.initial={'endpoint':'wss://api.xiaozhi.me/mcp/?token=TOPSECRET','upstreams':{d:{'url':f'http://higress:8080/mcp/{d}','key':'KEYSECRET'} for d in ALLOWED},'enabled_tools':sorted(set().union(*ALLOWED.values()))}
        self.store.initialize(self.initial)

    def test_post_commit_directory_sync_failure_is_not_reported_as_rollback(self):
        import os
        real_sync=os.fsync
        calls=[]
        def sync(fd):
            calls.append(fd)
            if len(calls)==2:raise OSError('directory sync failed')
            return real_sync(fd)
        with patch('admin_config.os.fsync',side_effect=sync):
            version=self.store.save({'enabled_tools':[]},1)
        self.assertEqual(version,2)
        self.assertEqual(self.store.load()['version'],2)
        self.assertEqual(self.store.public()['save_warning'],'durability_unconfirmed')

    def test_same_version_concurrent_save_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        def save():
            try:self.store.save({'enabled_tools':[]},1);return 'saved'
            except ConfigError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:save(),range(2)))
        self.assertCountEqual(results,['saved',409])
        self.assertEqual(self.store.load()['version'],2)

    def test_redacted_view_and_empty_preserve(self):
        self.assertNotIn('SECRET',json.dumps(self.store.public()))
        self.store.save({'endpoint':'','upstreams':{'ops-readonly':{'key':''}}},1)
        self.assertEqual(self.store.load()['upstreams']['ops-readonly']['key'],'KEYSECRET')
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777,0o700)

    def test_conflict_and_atomic_failure_preserve_old_bytes(self):
        before=self.path.read_bytes()
        with self.assertRaises(ConfigError):self.store.save({},0)
        with patch('admin_config.os.replace',side_effect=OSError('private error')):
            with self.assertRaises(ConfigError):self.store.save({'enabled_tools':[]},1)
        self.assertEqual(before,self.path.read_bytes())

    def test_url_and_field_boundaries(self):
        for update in [{'endpoint':'wss://evil.example/mcp/?token=x'}, {'upstreams':{'ops-ai':{'url':'http://169.254.169.254/mcp/ops-ai'}}}, {'enabled_tools':['shell']}, {'filename':'/etc/passwd'}, {'clear_endpoint':True}, {'upstreams':{'ops-readonly':{'clear_key':True}}}]:
            with self.subTest(update=update),self.assertRaises(ConfigError):self.store.save(update,1)

    def test_explicit_clear_and_no_tool_fallback(self):
        self.store.save({'enabled_tools':[],'upstreams':{'ops-ai':{'clear_key':True}}},1)
        value=self.store.load();self.assertEqual(value['enabled_tools'],[]);self.assertEqual(value['upstreams']['ops-ai']['key'],'')
        self.store.initialize(self.initial)
        self.assertEqual(self.store.load()['version'],2)

if __name__=='__main__':unittest.main()
