"""Image acceptance for local-program (stdio) services; needs network and runs only when STDIO_TEST_IMAGE names a built image.

STDIO_TEST_IMAGE=linmo/agent-mcp-proxy:stdio-test PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest test_stdio_image
Mirrors: set STDIO_TEST_NPM_REGISTRY / STDIO_TEST_PYPI_INDEX / STDIO_TEST_HTTPS_PROXY to pass them to the container.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

IMAGE=os.environ.get('STDIO_TEST_IMAGE')
PROBE_DIR=Path(__file__).parent/'tests'/'image'
TOKEN='synthetic-image-token-1234'


def run_probe():
    mirrors=[]
    for source,target in (('STDIO_TEST_NPM_REGISTRY','STDIO_NPM_REGISTRY'),('STDIO_TEST_PYPI_INDEX','STDIO_PYPI_INDEX'),('STDIO_TEST_HTTPS_PROXY','STDIO_HTTPS_PROXY')):
        if os.environ.get(source):mirrors+=['-e','%s=%s'%(target,os.environ[source])]
    # Same hardening as docker-compose; the cache volume is a throwaway tmpfs that allows executing installed packages.
    command=['docker','run','--rm','--read-only','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--tmpfs','/tmp:size=16m','--tmpfs','/var/cache/agent-mcp-proxy:rw,exec,size=1g,uid=65534,gid=65534,mode=0700',*mirrors,
        '--mount','type=bind,source=%s,target=/tests,readonly'%PROBE_DIR,IMAGE,'python','/tests/stdio_image_probe.py']
    done=subprocess.run(command,capture_output=True,text=True,timeout=900)
    if done.returncode:raise AssertionError('probe exited %d: %s'%(done.returncode,done.stderr[-3000:]))
    return done


@unittest.skipUnless(IMAGE,'set STDIO_TEST_IMAGE (and allow network) to run the stdio image acceptance test')
class StdioImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done=run_probe();cls.report=json.loads(cls.done.stdout)

    def test_npx_and_uvx_install_pinned_servers_and_list_tools(self):
        r=self.report
        self.assertEqual(r['uid'],65534);self.assertFalse(r['app_writable']);self.assertEqual(r['limit'],10)
        self.assertEqual(r['npx'],'/opt/feishu-node/bin/npx');self.assertEqual(r['uvx'],'/usr/local/bin/uvx')
        for key in ('everything','everything_cached','time','reinstall'):
            with self.subTest(key=key):
                self.assertNotIn('error',r[key],r[key]);self.assertEqual(r[key]['state'],'ready')
        self.assertIn('echo',r['everything']['tools'],r['everything'])
        self.assertEqual(r['everything_cached']['tools'],r['everything']['tools'])
        self.assertEqual(r['time']['tools'],['convert_time','get_current_time'])
        self.assertGreater(r['everything_npm_bytes'],0);self.assertGreater(r['time_uv_bytes'],0)
        self.assertEqual(r['reinstall']['tools'],r['everything']['tools']);self.assertTrue(r['reinstall_home_kept'])

    def test_tool_calls_return_text(self):
        r=self.report
        self.assertFalse(r['everything_call']['is_error']);self.assertIn('hello',r['everything_call']['text'])
        self.assertFalse(r['time_call']['is_error']);self.assertEqual(r['time_call']['types'],['text'])
        self.assertIn('UTC',r['time_call']['text'])

    def test_missing_command_logging_and_cleanup(self):
        r=self.report
        self.assertEqual(r['missing']['error'],'stdio_command_not_found')
        self.assertEqual(r['missing']['state']['state'],'failed');self.assertIn('no-such-mcp-command',r['missing']['state']['detail'])
        self.assertEqual(r['after'],[]);self.assertEqual(r['active_after'],0)
        log=self.done.stderr
        self.assertIn('stdio start service="everything" id=everything command="npx"',log)
        self.assertIn('stdio start service="time" id=time command="uvx"',log)
        self.assertNotIn(TOKEN,log);self.assertNotIn(TOKEN,self.done.stdout)


if __name__=='__main__':unittest.main()
