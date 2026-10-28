"""Local-program (stdio) services: command, arguments and secret env as configured by the administrator.

No command allow-list: anything installed in the container may run, and a missing command fails at discovery.
Env values are credentials; they never leave the private configuration except into the child process.
"""
import os
import re
from pathlib import Path
from mcp import StdioServerParameters

TRANSPORT='stdio'
SERVICE_KEYS=frozenset({'id','name','transport','command','args','env','discovery','legacy_profile'})
INPUT_KEYS=frozenset({'name','transport','command','args','env'})
CACHE_ROOT=Path(os.environ.get('STDIO_CACHE_DIR','/var/cache/agent-mcp-proxy'))
PATH='/opt/feishu-node/bin:/usr/local/bin:/usr/bin:/bin'
# Proxy and package-mirror settings reach the child only when set; per-service env wins. The STDIO_* names
# configure the programs without also routing the proxy's own HTTP/SSE upstream traffic through that proxy.
PASS_ENV={'HTTPS_PROXY':'STDIO_HTTPS_PROXY','https_proxy':'STDIO_HTTPS_PROXY','HTTP_PROXY':'STDIO_HTTP_PROXY','http_proxy':'STDIO_HTTP_PROXY',
    'NO_PROXY':'STDIO_NO_PROXY','no_proxy':'STDIO_NO_PROXY','NPM_CONFIG_REGISTRY':'STDIO_NPM_REGISTRY','UV_DEFAULT_INDEX':'STDIO_PYPI_INDEX'}
CACHE_PARTS=('npm','uv','python','tmp')
_ENV_NAME=re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')


def is_local(service):
    return isinstance(service,dict) and service.get('transport')==TRANSPORT and 'provider' not in service


def _text(value,limit,newlines=False):
    if not isinstance(value,str) or len(value)>limit or '\0' in value:return False
    return newlines or not any(ord(c)<32 or ord(c)==127 for c in value)


def validate_launch(command,args,env):
    if not _text(command,4096) or not command.strip() or command!=command.strip():raise ValueError('Invalid command')
    if not isinstance(args,list) or len(args)>64 or any(not _text(a,4096) for a in args):raise ValueError('Invalid arguments')
    if not isinstance(env,dict) or len(env)>64:raise ValueError('Invalid environment')
    for key,value in env.items():
        if not isinstance(key,str) or not _ENV_NAME.fullmatch(key) or not _text(value,8192,newlines=True):raise ValueError('Invalid environment')


def validate_service(service):
    if not isinstance(service,dict) or set(service)!=SERVICE_KEYS or service['transport']!=TRANSPORT or service['legacy_profile'] is not None:raise ValueError('Invalid stdio service')
    if not isinstance(service['name'],str) or not 1<=len(service['name'])<=80:raise ValueError('Invalid stdio service')
    validate_launch(service['command'],service['args'],service['env'])
    if not isinstance(service['discovery'],list) or len(service['discovery'])>200:raise ValueError('Invalid stdio service')


def public(service):
    """Administrator view: env names only, never values."""
    return {'id':service['id'],'name':service['name'],'transport':TRANSPORT,'command':service['command'],'args':list(service['args']),
        'env_keys':sorted(service['env']),'discovery':service['discovery']}


def paths(sid):
    root=CACHE_ROOT/sid
    return {'root':root,'home':root/'home',**{part:root/part for part in CACHE_PARTS}}


def secrets(service):
    # Very short values (flags such as "1" or "true") would blank out unrelated log text.
    return [v for v in service['env'].values() if len(v)>=4]


def stdio_parameters(service,where):
    for part in ('home',*CACHE_PARTS):where[part].mkdir(parents=True,exist_ok=True,mode=0o700)
    env={key:value for key,source in PASS_ENV.items() if (value:=os.environ.get(source) or os.environ.get(key))}
    env.update({'PATH':PATH,'HOME':str(where['home']),'TMPDIR':str(where['tmp']),'LANG':'C.UTF-8','NO_COLOR':'1','PYTHONUNBUFFERED':'1',
        'npm_config_cache':str(where['npm']),'npm_config_prefer_offline':'true','npm_config_update_notifier':'false','npm_config_fund':'false','npm_config_audit':'false','npm_config_yes':'true','NO_UPDATE_NOTIFIER':'1',
        'UV_CACHE_DIR':str(where['uv']),'UV_PYTHON_INSTALL_DIR':str(where['python']),'UV_NO_PROGRESS':'1'})
    env.update(service['env'])
    return StdioServerParameters(command=service['command'],args=list(service['args']),cwd=str(where['home']),env=env)
