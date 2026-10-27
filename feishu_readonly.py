"""Fixed Feishu read-only provider: six tenant-identity tools, constant launch, no generic stdio."""
from mcp import StdioServerParameters

PROVIDER='feishu-readonly'
TRANSPORT='stdio'
TOOL_NAMES=('im.v1.chat.list','im.v1.message.list','docx.v1.document.rawContent','bitable.v1.appTable.list','bitable.v1.appTableField.list','contact.v3.user.batchGetId')
WIRE_TOOL_NAMES=frozenset(name.replace('.','_') for name in TOOL_NAMES)
NODE='/opt/feishu-node/bin/node'
CLI='/opt/feishu-mcp/node_modules/@larksuiteoapi/lark-mcp/dist/cli.js'
WORKDIR='/opt/feishu-mcp'
DOMAIN='https://open.feishu.cn'
PATH='/opt/feishu-node/bin:/usr/bin:/bin'
SERVICE_KEYS=frozenset({'id','name','provider','transport','app_id','app_secret','discovery','legacy_profile'})
# The SDK merges HOME/LOGNAME/PATH/SHELL/TERM/USER from the parent; every inherited key is overridden.
_FIXED_ENV={'PATH':PATH,'LOGNAME':'nobody','USER':'nobody','SHELL':'/usr/sbin/nologin','TERM':'dumb'}


def is_feishu(service):
    return isinstance(service,dict) and service.get('provider')==PROVIDER


def _text(value,limit):
    return isinstance(value,str) and 1<=len(value)<=limit and not any(ord(c)<32 or ord(c)==127 for c in value)


def validate_credentials(app_id,app_secret):
    if not _text(app_id,256):raise ValueError('Invalid Feishu App ID')
    if not _text(app_secret,8192):raise ValueError('Invalid Feishu App Secret')


def validate_feishu_service(service):
    if not isinstance(service,dict) or set(service)!=SERVICE_KEYS:raise ValueError('Invalid Feishu service')
    if service['provider']!=PROVIDER or service['transport']!=TRANSPORT or service['legacy_profile'] is not None:raise ValueError('Invalid Feishu service')
    if not isinstance(service['name'],str) or not 1<=len(service['name'])<=80:raise ValueError('Invalid Feishu service')
    validate_credentials(service['app_id'],service['app_secret'])
    discovery=service['discovery']
    if not isinstance(discovery,list) or len(discovery)>len(TOOL_NAMES) or any(not isinstance(t,dict) or t.get('name') not in WIRE_TOOL_NAMES for t in discovery):raise ValueError('Invalid Feishu service')


def tool_allowed(service,name):
    return name in WIRE_TOOL_NAMES if is_feishu(service) else True


def stdio_parameters(service,home):
    args=[CLI,'mcp','--mode','stdio','--token-mode','tenant_access_token','--tool-name-case','snake','--language','zh','--domain',DOMAIN,'--tools',','.join(TOOL_NAMES)]
    return StdioServerParameters(command=NODE,args=args,cwd=WORKDIR,env=_FIXED_ENV|{'HOME':home,'APP_ID':service['app_id'],'APP_SECRET':service['app_secret']})
