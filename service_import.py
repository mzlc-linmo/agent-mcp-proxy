"""Normalize HTTP MCP configuration only; never execute commands or trust descriptor instructions."""
import asyncio
import json
import httpx
from admin_config import ConfigError
from service_transport import validate_target,auth_headers,GuardedTransport

class ImportError(ConfigError):pass

def parse_import(document,auth_override=None):
    if not isinstance(document,dict):raise ImportError('Expected mcpServers or connections')
    if 'mcpServers' in document and 'connections' in document:raise ImportError('Use one import format')
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
        if set(entry)-{'url','type','transport','transportType','headers','allowInsecurePrivateNetwork','disabled','authMode','apiKeyHeader','apiKeyValue','bearerToken'}:raise ImportError('Unsupported MCP configuration fields')
        transport=entry.get('type',entry.get('transport',entry.get('transportType','streamable-http'))).lower()
        transport={'http':'streamable-http','streamablehttp':'streamable-http'}.get(transport,transport)
        if transport not in ('streamable-http','sse'):raise ImportError('Only Streamable HTTP and SSE are supported')
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
        rows.append({'name':name,'url':entry['url'],'transport':transport,'auth':auth,'private_network':private})
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
