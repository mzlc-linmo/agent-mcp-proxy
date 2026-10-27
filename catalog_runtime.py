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
import feishu_readonly
from feishu_stdio import error_code

FEISHU_HINT='[飞书应用身份·只读] '
FEISHU_ERRORS={'rejected':'Feishu read-only call rejected or failed (check app permission and resource visibility); no automatic replay',
    'unsupported':'Feishu returned a non-text result; no automatic replay',
    'too_large':'Feishu result exceeds 1024 bytes; narrow the query (smaller page_size or range); no automatic replay'}


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
        # The fixed Feishu list is enforced here as well, so forged snapshots or grants never publish.
        if not feishu_readonly.tool_allowed(service,grant['name']):continue
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
            if feishu_readonly.is_feishu(service):
                # Grants awaiting review or outside the fixed list never start a Feishu process.
                grants=[g for g in grants if feishu_readonly.tool_allowed(service,g['name']) and g['schema_hash']!='review-required']
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
            except Exception as error:
                await child.aclose()
                code=error_code(error)
                report('upstream',{'domain':sid,'state':'unavailable'}|({'error_code':code} if code else {}));continue
            approved=approved_tools(service,grants,tools)
            report('upstream',{'domain':sid,'state':'discovered','discovered':len(tools),'published':len(approved)})
            for alias,tool in approved:
                if alias in routes:raise ValueError('Duplicate publication alias')
                routes[alias]=(session,tool['name'],service.get('legacy_profile'),service)
                description=tool.get('description',tool['name'])
                if feishu_readonly.is_feishu(service):description=FEISHU_HINT+description
                published.append(types.Tool(name=alias,description=description,inputSchema=tool['inputSchema'],annotations=types.ToolAnnotations(**tool['annotations']) if tool.get('annotations') else None))

        @server.list_tools()
        async def list_tools():return published

        async def call_feishu(session,service,original,arguments):
            def failure(kind):return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text=FEISHU_ERRORS[kind])])
            # Checked again immediately before forwarding; the hint in descriptions is not the guard.
            if not feishu_readonly.tool_allowed(service,original):return failure('rejected')
            try:
                async with asyncio.timeout(20):result=await session.call_tool(original,arguments)
            except Exception:return failure('rejected')
            # Upstream error text can reflect request data, so it is never forwarded.
            if result.isError:return failure('rejected')
            if not result.content or any(not isinstance(c,types.TextContent) for c in result.content):return failure('unsupported')
            text='\n'.join(c.text for c in result.content)
            if len(text.encode())>1024:return failure('too_large')
            return types.CallToolResult(isError=False,content=[types.TextContent(type='text',text=text)])

        @server.call_tool()
        async def call_tool(name,arguments):
            if name not in routes:return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='Tool not authorized')])
            session,original,profile,service=routes[name]
            if feishu_readonly.is_feishu(service):return await call_feishu(session,service,original,arguments)
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
