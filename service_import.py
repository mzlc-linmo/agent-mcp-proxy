"""Normalize imported MCP configuration; nothing is executed here.

HTTP/SSE entries become remote services. Local-program entries (mcpServers `command`, or an MCP Registry server.json
npm/PyPI package) become stdio templates: values the administrator still has to supply are listed as inputs and
filled in by resolve(); the program first runs at tool discovery.
"""
import asyncio
import json
import re
import httpx
import local_stdio
from admin_config import ConfigError
from service_transport import validate_target,auth_headers,GuardedTransport

class ImportError(ConfigError):pass

HTTP_FIELDS={'url','type','transport','transportType','headers','allowInsecurePrivateNetwork','disabled','authMode','apiKeyHeader','apiKeyValue','bearerToken'}
# Client-only keys of Claude Desktop / Cursor / ModelScope cards that have no meaning for the proxy are ignored.
STDIO_FIELDS={'command','args','env','type','transport','transportType','disabled','autoApprove','alwaysAllow','timeout','description'}
# "<your key>" or "${input:token}" in a card means the administrator must supply the value.
_PLACEHOLDER=re.compile(r'<[^<>]*>|\$\{[^{}]*\}')
_VARIABLE=re.compile(r'\{([^{}]+)\}')
RUNTIMES={'npm':'npx','pypi':'uvx'}


class Template:
    """One argument or env value: literal text and input references; optional ones are dropped when left blank."""
    def __init__(self,parts,optional=False):self.parts=parts;self.optional=optional


def _input(inputs,key,label,description='',required=False,secret=False,default=None):
    if not isinstance(default,(str,type(None))):default=None if isinstance(default,(dict,list)) else str(default)
    inputs.append({'key':key,'label':str(label)[:120],'description':str(description or '')[:400],'required':required is True,'secret':secret is True,'default':default})
    return {'input':key}


def _scalar(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,str):return value
    raise ImportError('Arguments and environment values must be text')


def _stdio_entry(name,entry):
    if set(entry)-STDIO_FIELDS:
        if 'url' in entry:raise ImportError('Use either command or url in one entry')
        if {'cwd','envFile'}&set(entry):raise ImportError('cwd and envFile are not supported; put values in env')
        raise ImportError('Unsupported MCP configuration fields')
    kind=entry.get('type',entry.get('transport',entry.get('transportType','stdio')))
    if not isinstance(kind,str) or kind.lower()!='stdio':raise ImportError('Command entries must use stdio')
    command,args,env=entry['command'],entry.get('args',[]),entry.get('env',{})
    if not isinstance(args,list) or not isinstance(env,dict):raise ImportError('Invalid command, arguments or environment')
    inputs=[];environment={}
    for key,value in env.items():
        value=_scalar(value)
        if value=='' or _PLACEHOLDER.fullmatch(value.strip()):
            environment[key]=Template([_input(inputs,'env:'+str(key),key,value,required=True,secret=True)])
        else:environment[key]=Template([value])
    row={'name':name,'transport':local_stdio.TRANSPORT,'command':command,'args':[Template([_scalar(a)]) for a in args],'env':environment,'inputs':inputs}
    _check(row);return row


def _parts(value,variables,key,inputs,secret=False):
    """Split '{name}' references in a registry value into inputs; unknown braces stay literal."""
    value=_scalar(value);parts=[];position=0;optional=False
    variables=variables if isinstance(variables,dict) else {}
    for match in _VARIABLE.finditer(value):
        spec=variables.get(match[1])
        if not isinstance(spec,dict):continue
        parts.append(value[position:match.start()]);position=match.end()
        parts.append(_input(inputs,key+':'+match[1],match[1],spec.get('description'),spec.get('isRequired',spec.get('is_required')),secret or spec.get('isSecret',spec.get('is_secret')),spec.get('default')))
        optional=optional or spec.get('isRequired',spec.get('is_required')) is not True
    parts.append(value[position:])
    return [p for p in parts if p!=''],optional


def _argument(arg,key,inputs):
    """Registry Argument: positional -> one token; named -> flag then value as separate tokens (`--port 8080`)."""
    if not isinstance(arg,dict):raise ImportError('Invalid package argument')
    kind=arg.get('type','positional');required=arg.get('isRequired',arg.get('is_required')) is True;secret=arg.get('isSecret',arg.get('is_secret')) is True
    if kind not in ('positional','named'):raise ImportError('Unsupported package argument type')
    flag=arg.get('name') if kind=='named' else None
    if kind=='named' and (not isinstance(flag,str) or not flag):raise ImportError('Named argument requires a name')
    description=arg.get('description') or ''
    if arg.get('choices'):description=(description+' ' if description else '')+'('+' / '.join(map(str,arg['choices']))+')'
    if 'value' in arg:
        parts,optional=_parts(arg['value'],arg.get('variables'),key,inputs,secret)
        value=Template(parts,optional and not required)
    else:
        value=Template([_input(inputs,key,flag or arg.get('valueHint') or arg.get('value_hint') or key,description,required,secret,arg.get('default'))],not required)
    if flag is None:return [value]
    # A (flag, value) pair: the flag disappears together with an optional value left blank.
    return [(flag,value)]


def _package(name,package):
    registry=str(package.get('registryType',package.get('registry_type',''))).lower()
    identifier=package.get('identifier',package.get('name'));version=package.get('version')
    if not isinstance(identifier,str) or not identifier or identifier!=identifier.strip():raise ImportError('Package identifier missing')
    command=RUNTIMES[registry];hint=package.get('runtimeHint',package.get('runtime_hint'))
    spec=identifier if not isinstance(version,str) or version in ('','latest') else identifier+('@' if registry=='npm' else '==')+version
    inputs=[];args=[Template(['-y'])] if registry=='npm' else []
    # Runtime arguments are written for the hinted runtime; only honour them when that is the runtime used here.
    runtime_args=package.get('runtimeArguments',package.get('runtime_arguments',[])) if hint in (None,command) else []
    package_args=package.get('packageArguments',package.get('package_arguments',[]))
    if not isinstance(runtime_args,list) or not isinstance(package_args,list):raise ImportError('Invalid package arguments')
    for index,arg in enumerate(runtime_args):
        tokens=_argument(arg,'runtime:%d'%index,inputs)
        if registry=='npm' and len(tokens)==1 and isinstance(tokens[0],Template) and tokens[0].parts in (['-y'],['--yes']):continue
        args.extend(tokens)
    args.append(Template([spec]))
    for index,arg in enumerate(package_args):args.extend(_argument(arg,'arg:%d'%index,inputs))
    environment={}
    variables=package.get('environmentVariables',package.get('environment_variables',[]))
    if not isinstance(variables,list):raise ImportError('Invalid environment variables')
    for variable in variables:
        if not isinstance(variable,dict) or not isinstance(variable.get('name'),str):raise ImportError('Invalid environment variable')
        key=variable['name'];required=variable.get('isRequired',variable.get('is_required')) is True
        if 'value' in variable:
            parts,optional=_parts(variable['value'],variable.get('variables'),'env:'+key,inputs,True);environment[key]=Template(parts,optional and not required)
        else:
            # Every env value is treated as a credential: kept out of previews, logs and later API responses.
            environment[key]=Template([_input(inputs,'env:'+key,key,variable.get('description'),required,True,variable.get('default'))],not required)
    row={'name':name,'transport':local_stdio.TRANSPORT,'command':command,'args':args,'env':environment,'inputs':inputs}
    _check(row);return row


def _registry(server,auth_override):
    """Official MCP Registry server.json (or its API wrapper): first npm/PyPI stdio package, else a fixed remote URL."""
    if not isinstance(server,dict):raise ImportError('Invalid server.json')
    name=server.get('title') or server.get('name')
    if not isinstance(name,str) or not name.strip():raise ImportError('server.json requires a name')
    name=name.strip()[:80]
    packages=server.get('packages') or [];remotes=server.get('remotes') or []
    if not isinstance(packages,list) or not isinstance(remotes,list):raise ImportError('Invalid server.json')
    for package in packages:
        if not isinstance(package,dict):continue
        transport=package.get('transport') or {'type':'stdio'}
        if str(package.get('registryType',package.get('registry_type',''))).lower() in RUNTIMES and isinstance(transport,dict) and transport.get('type','stdio')=='stdio':
            return _package(name,package)
    for remote in remotes:
        if isinstance(remote,dict) and remote.get('type') in ('streamable-http','sse') and isinstance(remote.get('url'),str) and '{' not in remote['url']:
            return _http_entry(name,{'url':remote['url'],'type':remote['type']},auth_override)
    raise ImportError('server.json has no npm or PyPI stdio package and no fixed remote URL')


def _check(row):
    """Validate the literal shape now; values from inputs are validated again after resolve()."""
    preview,_=resolve(row,{},blank_ok=True)
    try:local_stdio.validate_launch(preview['command'],preview['args'],preview['env'])
    except ValueError:raise ImportError('Invalid command, arguments or environment') from None


def resolve(row,values,prefix='',blank_ok=False):
    """Fill a stdio template; returns (entry, missing required input keys). Blank optional values are dropped."""
    defaults={i['key']:i for i in row['inputs']};missing=[]
    def render(template):
        text='';blank=False
        for part in template.parts:
            if isinstance(part,str):text+=part;continue
            spec=defaults[part['input']];value=values.get(prefix+part['input'])
            if value in (None,''):value=spec['default'] or ''
            if value=='':
                blank=True
                if spec['required'] and part['input'] not in missing:missing.append(part['input'])
            text+=value
        return None if blank and template.optional else text
    args=[]
    for item in row['args']:
        flag,template=item if isinstance(item,tuple) else (None,item)
        value=render(template)
        if value is not None:args+=[flag,value] if flag else [value]
    env={k:v for k,v in ((k,render(t)) for k,t in row['env'].items()) if v is not None}
    entry={'name':row['name'],'transport':local_stdio.TRANSPORT,'command':row['command'],'args':args,'env':env}
    if not blank_ok:
        try:local_stdio.validate_launch(entry['command'],entry['args'],entry['env'])
        except ValueError:raise ImportError('Invalid command, arguments or environment') from None
    return entry,missing


def preview(row,values,prefix=''):
    """What the administrator confirms: command line with secret inputs masked; env names only, never values."""
    secret={i['key'] for i in row['inputs'] if i['secret']};labels={i['key']:i['label'] for i in row['inputs']}
    shown={}
    for i in row['inputs']:
        value=values.get(prefix+i['key'])
        # Optional values left blank are dropped exactly as on import; required ones show their label.
        shown[prefix+i['key']]='***' if (value or i['default']) and i['key'] in secret else value or i['default'] or ('<%s>'%labels[i['key']] if i['required'] else '')
    filled,_=resolve(row,shown,prefix,blank_ok=True)
    _,missing=resolve(row,values,prefix,blank_ok=True)
    return {'name':row['name'],'transport':local_stdio.TRANSPORT,'command':row['command'],'args':filled['args'],'env_keys':sorted(row['env']),
        'inputs':[{k:i[k] for k in ('label','description','required','secret')}|{'key':prefix+i['key'],'default':None if i['secret'] else i['default'],'has_default':bool(i['default'])} for i in row['inputs']],
        'missing':[prefix+k for k in missing]}


def _http_entry(name,entry,auth_override):
    if set(entry)-HTTP_FIELDS:raise ImportError('Unsupported MCP configuration fields')
    transport=entry.get('type',entry.get('transport',entry.get('transportType','streamable-http')))
    if not isinstance(transport,str):raise ImportError('Invalid transport')
    transport=transport.lower();transport={'http':'streamable-http','streamablehttp':'streamable-http'}.get(transport,transport)
    if transport not in ('streamable-http','sse'):raise ImportError('Only Streamable HTTP, SSE and stdio are supported')
    private=entry.get('allowInsecurePrivateNetwork',False)
    if type(private) is not bool:raise ImportError('Private-network confirmation must be boolean')
    headers=entry.get('headers',{})
    if not isinstance(headers,dict) or len(headers)>1:raise ImportError('Only one authentication header is supported')
    auth={'mode':'none'}
    if headers:
        header,value=next(iter(headers.items()))
        if not isinstance(value,str):raise ImportError('Invalid authentication header')
        if header.lower()=='authorization':
            if not value.startswith('Bearer ') or not value[7:]:raise ImportError('Only Bearer authorization is supported')
            auth={'mode':'bearer','value':value[7:]}
        else:auth={'mode':'api-key','header':header,'value':value}
    if 'authMode' in entry:
        if headers:raise ImportError('Do not combine authMode with authentication headers')
        mode=entry['authMode']
        if mode=='none':auth={'mode':'none'}
        elif mode=='bearer':auth={'mode':'bearer','value':entry.get('bearerToken','')}
        elif mode=='api-key':auth={'mode':'api-key','header':entry.get('apiKeyHeader','X-Api-Key'),'value':entry.get('apiKeyValue','')}
        else:raise ImportError('Unsupported authentication mode')
    if auth_override is not None:auth=dict(auth_override)
    try:validate_target(entry.get('url'),private);auth_headers(auth)
    except ValueError:raise ImportError('Invalid URL, private confirmation or authentication') from None
    return {'name':name,'url':entry['url'],'transport':transport,'auth':auth,'private_network':private}


def is_registry(document):
    return isinstance(document,dict) and ('packages' in document or 'remotes' in document or isinstance(document.get('server'),dict) or isinstance(document.get('servers'),list))


def parse_import(document,auth_override=None):
    if not isinstance(document,dict):raise ImportError('Expected mcpServers, connections or server.json')
    if 'mcpServers' in document and 'connections' in document:raise ImportError('Use one import format')
    if 'mcpServers' not in document and 'connections' not in document and is_registry(document):
        servers=[document['server']] if isinstance(document.get('server'),dict) else [item.get('server',item) if isinstance(item,dict) else item for item in document['servers']] if isinstance(document.get('servers'),list) else [document]
        if not 1<=len(servers)<=20:raise ImportError('Expected 1 to 20 servers')
        return [_registry(server,auth_override) for server in servers]
    if isinstance(document.get('mcpServers'),dict):entries=list(document['mcpServers'].items())
    elif isinstance(document.get('connections'),list):
        entries=[]
        for connection in document['connections']:
            if not isinstance(connection,dict):raise ImportError('Invalid connection entry')
            entry=dict(connection);name=entry.pop('name',entry.get('serverName',''));entry.pop('serverName',None);entries.append((name,entry))
    else:raise ImportError('Expected 1 to 20 mcpServers or connections')
    if not 1<=len(entries)<=20:raise ImportError('Expected 1 to 20 servers')
    rows=[]
    for name,entry in entries:
        if not isinstance(name,str) or not 1<=len(name)<=80 or not isinstance(entry,dict):raise ImportError('Invalid server entry')
        # Service authentication from the market tab applies to remote services only.
        rows.append(_stdio_entry(name,entry) if 'command' in entry else _http_entry(name,entry,auth_override))
    return rows

async def fetch_descriptor(url,auth_override=None):
    # Descriptor fetch never receives service credentials or private-network access.
    validate_target(url,False)
    async with asyncio.timeout(15):
        async with httpx.AsyncClient(transport=GuardedTransport(url,False),follow_redirects=False,trust_env=False,timeout=10) as client:
            async with client.stream('GET',url) as response:
                response.raise_for_status();body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>65536:raise ImportError('Descriptor exceeds 64 KiB')
    try:document=json.loads(body)
    except ValueError:raise ImportError('Descriptor is not JSON') from None
    return parse_import(document,auth_override=auth_override)
