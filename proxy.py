"""Outbound-only Xiaozhi bridge with explicit tool scope; no automatic call replay."""
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.shared.message import SessionMessage
from websockets.asyncio.client import connect as WebSocketConnect

class NoRedirectConnect(WebSocketConnect):
    def process_redirect(self, exc):
        return exc

connect = NoRedirectConnect
from policy import ALLOWED, compact, permitted

LOG = logging.getLogger('xiaozhi_bridge')


def secure_logging():
    # Third-party transports can include authenticated URLs or reflected payloads in errors.
    for name in ('httpx', 'httpcore', 'websockets', 'mcp'):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)
    # Explicit transport loggers may carry their own levels/handlers.
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith(('mcp.', 'websockets.', 'httpx.', 'httpcore.')):
            logger = logging.getLogger(name)
            logger.handlers = [logging.NullHandler()]
            logger.propagate = False
            logger.setLevel(logging.CRITICAL + 1)


def secret(path):
    value = Path(path).read_text().strip()
    if not value:
        raise ValueError('empty secret')
    return value


async def bridge(config, report=lambda *args: None):
    secure_logging()
    endpoint = config.get('endpoint') or secret(config['endpoint_file'])
    tool_sets = {d: set(ALLOWED[d]) if d in ALLOWED else set(e.get('tools', [])) for d,e in config['upstreams'].items()}
    enabled = set(config.get('enabled_tools', set().union(*ALLOWED.values())))
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'wss' or parsed.hostname != 'api.xiaozhi.me' or parsed.username:
        raise ValueError('unapproved WSS destination')
    server = Server('linmo-voice')
    tools, routes, sessions = [], {}, {}
    async with AsyncExitStack() as stack:
        for domain, allowed in tool_sets.items():
            if not allowed.intersection(enabled):
                report('upstream', {'domain': domain, 'state': 'disabled'})
                continue
            entry = config['upstreams'].get(domain, {})
            key = entry.get('key') if 'key' in entry else (secret(entry['key_file']) if entry.get('key_file') and Path(entry['key_file']).is_file() else '')
            if not key:
                if domain == 'ops-readonly':
                    raise ValueError('readonly credential required')
                report('upstream', {'domain': domain, 'state': 'credential_missing'})
                LOG.warning('Optional domain unavailable: %s', domain)
                continue
            url = urlsplit(entry['url'])
            if url.scheme not in ('http', 'https') or url.username or url.query or url.fragment:
                raise ValueError('invalid upstream URL')
            client = await stack.enter_async_context(httpx.AsyncClient(
                headers={'X-Internal-Token': key}, timeout=20, follow_redirects=False))
            streams = await stack.enter_async_context(streamable_http_client(entry['url'], http_client=client))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            available = await session.list_tools()
            sessions[domain] = session
            report('upstream', {'domain': domain, 'state': 'discovered', 'discovered': len(available.tools)})
            for tool in available.tools:
                if tool.name in allowed and tool.name in enabled:
                    if tool.name in routes:
                        raise ValueError('duplicate tool name')
                    routes[tool.name] = domain
                    description = tool.description or tool.name
                    if domain in ('ops-actions', 'ops-collection'):
                        description = '真实副作用：先向用户明确目标与影响并取得确认；超时结果未知不得重试。此连接使用服务账号，不代表语音说话人的身份。' + description
                    tools.append(types.Tool(name=tool.name, description=description,
                                            inputSchema=tool.inputSchema,
                                            annotations=types.ToolAnnotations(readOnlyHint=domain not in ('ops-actions', 'ops-collection'),
                                                                             destructiveHint=domain in ('ops-actions', 'ops-collection'))))
        # An explicitly empty enabled list publishes zero tools, never falls back to defaults.

        @server.list_tools()
        async def list_tools():
            return tools

        @server.call_tool()
        async def call_tool(name, arguments):
            domain = routes.get(name)
            if domain is None or name not in tool_sets.get(domain, set()) or name not in enabled:
                return types.CallToolResult(isError=True, content=[types.TextContent(type='text', text='Tool not allowed')])
            try:
                async with asyncio.timeout(20):
                    result = await sessions[domain].call_tool(name, arguments)
                if result.isError:
                    raise ValueError('upstream rejected')
                text = next(c.text for c in result.content if isinstance(c, types.TextContent))
                if domain in ALLOWED:
                    output = compact(name, json.loads(text))
                else:
                    if len(text.encode('utf-8')) > 1024:
                        raise ValueError('custom response too large')
                    return types.CallToolResult(isError=False, content=[types.TextContent(type='text', text=text)])
                failed = not json.loads(output).get('ok', False)
                return types.CallToolResult(isError=failed, content=[types.TextContent(type='text', text=output)])
            except Exception:
                # Never include upstream exceptions: URLs and headers can contain secrets.
                return types.CallToolResult(isError=True, content=[types.TextContent(type='text', text='Upstream unavailable or request rejected; no automatic replay')])

        async with connect(endpoint, max_size=65536, max_queue=16, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
            incoming_send, incoming_receive = anyio.create_memory_object_stream(16)
            outgoing_send, outgoing_receive = anyio.create_memory_object_stream(16)

            async def receive():
                async with incoming_send:
                    async for message in ws:
                        parsed_message = types.JSONRPCMessage.model_validate_json(message)
                        await incoming_send.send(SessionMessage(parsed_message))
                raise ConnectionError('WSS closed')

            async def send():
                async with outgoing_receive:
                    async for message in outgoing_receive:
                        await ws.send(message.message.model_dump_json(by_alias=True, exclude_none=True))

            report('connected', {'tools': [t.name for t in tools]})
            LOG.info('Connected to Xiaozhi; approved tool count=%s', len(tools))
            async with anyio.create_task_group() as group:
                group.start_soon(receive)
                group.start_soon(send)
                await server.run(incoming_receive, outgoing_send, server.create_initialization_options())
                group.cancel_scope.cancel()


async def main():
    config = json.loads(Path(os.environ.get('BRIDGE_CONFIG', '/app/config.json')).read_text())
    delay = 1
    while True:
        try:
            await bridge(config)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.warning('Bridge disconnected or configuration unavailable; retry in %ss', delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    LOG.setLevel(logging.INFO)
    # Suppress libraries which may log full authenticated URLs or request metadata.
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('websockets').setLevel(logging.CRITICAL)
    asyncio.run(main())
