"""Image acceptance for the pinned Feishu runtime; runs only when FEISHU_TEST_IMAGE names a built image.

FEISHU_TEST_IMAGE=linmo/agent-mcp-proxy:feishu-test PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest test_feishu_image
"""
import json
import os
import subprocess
import unittest
from pathlib import Path
import feishu_readonly
from catalog_runtime import FEISHU_ERRORS

IMAGE=os.environ.get('FEISHU_TEST_IMAGE')
PROBE_DIR=Path(__file__).parent/'tests'/'image'
SECRET='synthetic-image-secret-0000'
FORBIDDEN=('create','delete','update','patch','send','oauth','login')


def run_probe():
    command=['docker','run','--rm','--network','none','--read-only','--user','65534:65534','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--tmpfs','/tmp:size=16m',
        '--mount','type=bind,source=%s,target=/tests,readonly'%PROBE_DIR,IMAGE,'python','/tests/feishu_image_probe.py']
    done=subprocess.run(command,capture_output=True,text=True,timeout=180)
    if done.returncode:raise AssertionError('probe exited %d: %s'%(done.returncode,done.stderr[-2000:]))
    return done.stdout


@unittest.skipUnless(IMAGE,'set FEISHU_TEST_IMAGE to run the image acceptance test')
class FeishuImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stdout=run_probe();cls.report=json.loads(cls.stdout)

    def test_official_package_initializes_six_tools_without_network(self):
        r=self.report
        self.assertNotIn('official_error',r,r.get('official_error'))
        self.assertEqual(r['uid'],65534);self.assertFalse(r['root_writable']);self.assertFalse(r['app_writable'])
        o=r['official']
        self.assertEqual(o['tools'],sorted(feishu_readonly.WIRE_TOOL_NAMES))
        self.assertFalse([t for t in o['tools'] if any(word in t.lower() for word in FORBIDDEN)])
        [p]=o['processes']
        self.assertEqual(p['argv'][0],feishu_readonly.CLI);self.assertEqual(p['cwd'],feishu_readonly.WORKDIR);self.assertEqual(p['uid'],65534)
        self.assertIn('tenant_access_token',p['argv']);self.assertNotIn('--oauth',p['argv']);self.assertFalse(p['secret_in_argv'])
        self.assertEqual(p['argv'][p['argv'].index('--tools')+1],','.join(feishu_readonly.TOOL_NAMES))
        self.assertTrue(p['home'].startswith('/tmp/feishu-home-'))
        self.assertFalse({'NODE_OPTIONS','USER_ACCESS_TOKEN','HTTPS_PROXY','HTTP_PROXY','LARK_DOMAIN'}&set(p['env_keys']))
        self.assertEqual(o['listening'],[])
        self.assertEqual(o['after'],[]);self.assertEqual(o['active_after'],0);self.assertTrue(o['home_removed'])
        self.assertNotIn(SECRET,self.stdout)

    def test_official_identity_paths_and_errors_through_synthetic_http(self):
        r=self.report
        self.assertNotIn('stubbed_error',r,r.get('stubbed_error'))
        s=r['stubbed']
        # tenant mode ignores useUAT: the request carries the tenant token obtained from the fixed domain.
        chat=s['chat_list_useUAT']
        self.assertFalse(chat['official_is_error']);self.assertFalse(chat['proxy_is_error'])
        self.assertEqual(json.loads(chat['proxy_text'])['items'][0]['chat_id'],'oc_synthetic')
        keys=[q['key'] for q in s['requests']]
        # Each case is called once directly and once through the proxy guard; the write tool never reaches HTTP.
        self.assertEqual(keys,['POST /open-apis/auth/v3/tenant_access_token/internal']+[k for k in ('GET /open-apis/im/v1/chats',
            'GET /open-apis/docx/v1/documents/doc_forbidden/raw_content','GET /open-apis/docx/v1/documents/doc_business/raw_content') for _ in range(2)])
        self.assertEqual({q['host'] for q in s['requests']},{'open.feishu.cn'})
        self.assertEqual({q['auth'] for q in s['requests'] if 'tenant_access_token' not in q['key']},{'Bearer t-synthetic-tenant-token'})
        # HTTP 4xx is isError in the official package; HTTP 200 with a non-zero code is not, so the proxy rejects it.
        self.assertTrue(s['doc_http_400']['official_is_error'])
        self.assertFalse(s['doc_http_200_code']['official_is_error'])
        for label in ('doc_http_400','doc_http_200_code','write_tool'):
            with self.subTest(label=label):
                self.assertTrue(s[label]['proxy_is_error'])
                self.assertEqual(s[label]['proxy_text'],FEISHU_ERRORS['rejected'])
        self.assertTrue(s['write_tool'].get('official_raised') or s['write_tool'].get('official_is_error'))
        self.assertEqual(s['after'],[]);self.assertEqual(s['active_after'],0)
        self.assertNotIn(SECRET,self.stdout)


if __name__=='__main__':unittest.main()
