import os
import unittest
from unittest.mock import patch
from mcp.client.stdio import get_default_environment

SECRET='synthetic-feishu-secret'

def service(**extra):
    return {'id':'f1','name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':SECRET,'discovery':[],'legacy_profile':None}|extra

class ReadonlyPolicyTests(unittest.TestCase):
    def test_exact_readonly_policy_and_sanitized_parameters(self):
        import feishu_readonly as f
        self.assertEqual(f.PROVIDER,'feishu-readonly')
        self.assertEqual(f.TOOL_NAMES,('im.v1.chat.list','im.v1.message.list','docx.v1.document.rawContent','bitable.v1.appTable.list','bitable.v1.appTableField.list','contact.v3.user.batchGetId'))
        self.assertEqual(f.WIRE_TOOL_NAMES,frozenset({'im_v1_chat_list','im_v1_message_list','docx_v1_document_rawContent','bitable_v1_appTable_list','bitable_v1_appTableField_list','contact_v3_user_batchGetId'}))
        s=service()
        self.assertTrue(f.is_feishu(s));self.assertFalse(f.is_feishu({'url':'https://example.com/mcp'}))
        for name in f.WIRE_TOOL_NAMES:self.assertTrue(f.tool_allowed(s,name))
        # Upstream annotations never widen the fixed list; case and dotted names are exact.
        for name in ('im_v1_message_create','im.v1.chat.list','IM_V1_CHAT_LIST','docx_v1_document_rawcontent','preset.default'):self.assertFalse(f.tool_allowed(s,name),name)
        self.assertTrue(f.tool_allowed({'id':'h','url':'https://example.com/mcp'},'anything'))
        f.validate_feishu_service(s)
        bad=[service(app_id=''),service(app_id='x'*257),service(app_id='cli\nx'),service(app_secret=''),service(app_secret='x'*8193),service(app_secret='a\x00b'),service(app_id=1),
             service(url='https://example.com/mcp'),service(auth={'mode':'none'}),service(private_network=False),service(command='/bin/sh'),service(args=['x']),service(env={'A':'B'}),service(tools=['im.v1.message.create']),
             service(user_access_token='u'),service(domain='https://evil.example'),service(transport='streamable-http'),service(provider='feishu'),service(legacy_profile='ops-actions'),service(name=''),
             service(discovery=[{'name':'im_v1_message_create','inputSchema':{},'schema_hash':'h'}])]
        for value in bad:
            with self.subTest(value={k:value[k] for k in value if k!='app_secret'}):
                with self.assertRaises(ValueError) as caught:f.validate_feishu_service(value)
                self.assertNotIn(SECRET,str(caught.exception))
        planted={'NODE_OPTIONS':'--require /tmp/evil.js','USER_ACCESS_TOKEN':'u-synthetic','HTTPS_PROXY':'http://proxy.invalid','HTTP_PROXY':'http://proxy.invalid','LARK_TOKEN_MODE':'user_access_token','LARK_TOOLS':'preset.default','APP_SECRET':'parent-secret','HOME':'/root','PATH':'/tmp/evil:/usr/bin','SHELL':'/bin/bash'}
        with patch.dict(os.environ,planted):
            p=f.stdio_parameters(s,'/tmp/feishu-home-test')
            env={**get_default_environment(),**p.env}
        self.assertEqual(p.command,'/opt/feishu-node/bin/node');self.assertEqual(p.cwd,'/opt/feishu-mcp')
        self.assertEqual(p.args,['/opt/feishu-mcp/node_modules/@larksuiteoapi/lark-mcp/dist/cli.js','mcp','--mode','stdio','--token-mode','tenant_access_token','--tool-name-case','snake','--language','zh','--domain','https://open.feishu.cn','--tools',','.join(f.TOOL_NAMES)])
        argv=' '.join([p.command,*p.args])
        for forbidden in (SECRET,'--oauth','npx','--debug','-u','--user-access-token','preset.','auto','--config'):self.assertNotIn(forbidden,p.args if forbidden.startswith('-') else argv)
        self.assertEqual(env['APP_ID'],'cli_synthetic');self.assertEqual(env['APP_SECRET'],SECRET)
        self.assertEqual(env['HOME'],'/tmp/feishu-home-test');self.assertEqual(env['PATH'],'/opt/feishu-node/bin:/usr/bin:/bin')
        for key in ('NODE_OPTIONS','USER_ACCESS_TOKEN','HTTPS_PROXY','HTTP_PROXY','LARK_TOKEN_MODE','LARK_TOOLS'):self.assertNotIn(key,env)
        self.assertNotEqual(env.get('SHELL'),'/bin/bash')
        self.assertEqual(set(env),{'PATH','HOME','APP_ID','APP_SECRET','LOGNAME','USER','SHELL','TERM'})

if __name__=='__main__':unittest.main()
