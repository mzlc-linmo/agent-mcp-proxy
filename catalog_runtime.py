"""Device-specific MCP publication: explicit service/name/schema grants only."""
import asyncio
import copy
import json
from contextlib import AsyncExitStack
import anyio
from mcp import types
from mcp.server import Server
from mcp.shared.message import SessionMessage
from catalog import schema_hash
from admin_config import ConfigError
from policy import compact
from service_transport import service_session
from proxy import connect,secure_logging


def device_snapshot(config,eid):
    device=next((d for d in config['devices'] if d['id']==eid),None)
    if device is None:raise ConfigError('Device not found',404)
    return {'version':config['version'],'endpoint':device['endpoint'],'bindings':copy.deepcopy(device['bindings']),
            'services':{sid:copy.deepcopy(config['services'][sid]) for sid in device['bindings']}}


def approved_tools(service,grants,tools):
    discovered={t['name']:t['schema_hash'] for t in service['discovery']}
    actual={t['name']:t for t in tools}
    result=[]
    for grant in grants:
        tool=actual.get(grant['name'])
        if tool is None:continue
        current=schema_hash(tool['inputSchema'])
        # Migration aliases are restricted to the pre-existing, fixed profile tool names.
        legacy=grant['schema_hash']=='legacy-review' and service.get('legacy_profile') and discovered.get(grant['name'])=='legacy-review'
        if legacy:
            from policy import ALLOWED
            if grant['name'] not in ALLOWED.get(service['legacy_profile'],()):continue
        elif current!=grant['schema_hash'] or discovered.get(grant['name'])!=current:continue
        result.append((grant['alias'],tool))
    return result


async def bridge_catalog(config,report):
    secure_logging();server=Server('linmo-mcp-proxy');published=[];routes={}
    async with AsyncExitStack() as stack:
        for sid,service in config['services'].items():
            grants=config['bindings'].get(sid,[])
            if not grants:
                report('upstream',{'domain':sid,'state':'disabled'});continue
            # Each service gets its own child stack so failed initialization closes partial resources.
            child=await stack.enter_async_context(AsyncExitStack())
            try:
                async with asyncio.timeout(15):
                    session=await child.enter_async_context(service_session(service))
                    tools=[];cursor=None
                    for _ in range(20):
                        page=await session.list_tools(cursor=cursor)
                        tools.extend(t.model_dump(mode='json',exclude_none=True) for t in page.tools)
                        if len(tools)>200 or len(json.dumps(tools).encode())>2*1024*1024:raise ValueError('Discovery limit')
                        cursor=page.nextCursor
                        if not cursor:break
                    else:raise ValueError('Too many pages')
            except Exception:
                await child.aclose()
                report('upstream',{'domain':sid,'state':'unavailable'});continue
            approved=approved_tools(service,grants,tools)
            report('upstream',{'domain':sid,'state':'discovered','discovered':len(tools),'published':len(approved)})
            for alias,tool in approved:
                if alias in routes:raise ValueError('Duplicate publication alias')
                routes[alias]=(session,tool['name'],service.get('legacy_profile'))
                published.append(types.Tool(name=alias,description=tool.get('description',tool['name']),inputSchema=tool['inputSchema'],annotations=types.ToolAnnotations(**tool['annotations']) if tool.get('annotations') else None))

        @server.list_tools()
        async def list_tools():return published

        @server.call_tool()
        async def call_tool(name,arguments):
            if name not in routes:return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='Tool not authorized')])
            session,original,profile=routes[name]
            try:
                async with asyncio.timeout(20):result=await session.call_tool(original,arguments)
                if result.isError:raise ValueError('Upstream rejected')
                if not result.content or any(not isinstance(c,types.TextContent) for c in result.content):raise ValueError('Unsupported result')
                text='\n'.join(c.text for c in result.content)
                if profile:text=compact(original,json.loads(text))
                if len(text.encode())>1024:raise ValueError('Result too large')
                return types.CallToolResult(isError=bool(profile and not json.loads(text).get('ok')),content=[types.TextContent(type='text',text=text)])
            except Exception:return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='Upstream rejected, unavailable or unsupported response; no automatic replay')])

        async with connect(config['endpoint'],max_size=65536,max_queue=16,open_timeout=15,ping_interval=20,ping_timeout=20) as ws:
            incoming_send,incoming_receive=anyio.create_memory_object_stream(16)
            outgoing_send,outgoing_receive=anyio.create_memory_object_stream(16)
            async def receive():
                async with incoming_send:
                    async for text in ws:await incoming_send.send(SessionMessage(types.JSONRPCMessage.model_validate_json(text)))
                raise ConnectionError('Connection closed')
            async def send():
                async with outgoing_receive:
                    async for message in outgoing_receive:await ws.send(message.message.model_dump_json(by_alias=True,exclude_none=True))
            report('connected',{'tools':[t.name for t in published]})
            async with anyio.create_task_group() as group:
                group.start_soon(receive);group.start_soon(send)
                await server.run(incoming_receive,outgoing_send,server.create_initialization_options())
                group.cancel_scope.cancel()
