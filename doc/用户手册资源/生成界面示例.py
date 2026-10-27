"""Capture the current UI with synthetic responses only; never read live configuration."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


async def main():
    tools = [{'name': n, 'description': d, 'inputSchema': {'type': 'object'}, 'schema_hash': 'demo'} for n, d in [('get_me', '演示：查询当前服务账号'), ('get_file_contents', '演示：读取仓库文件'), ('create_issue', '演示：创建议题，具有真实副作用')]]
    service = {'id': 'github-demo', 'name': 'GitHub（演示）', 'url': 'https://api.githubcopilot.com/mcp/', 'transport': 'streamable-http', 'auth': {'mode': 'bearer', 'configured': True, 'header': ''}, 'private_network': False, 'discovery': tools}
    cfg = {'version': 8, 'save_warning': None, 'services': {service['id']: service}, 'devices': [{'id': 'device-demo', 'name': '会议室小智（演示）', 'platform': 'xiaozhi', 'enabled': True, 'endpoint_configured': True, 'bindings': {'github-demo': [{'name': 'get_me', 'schema_hash': 'demo', 'alias': 'github_demo__get_me'}]}}]}
    status = {'endpoints': {'device-demo': {'state': 'connected', 'applied_version': 8, 'connected_at': '2026-10-24T03:00:00Z', 'reconnect_count': 0, 'published_tools': ['github_demo__get_me'], 'upstreams': {'github-demo': {'state': 'discovered', 'discovered': 3, 'published': 1}}, 'events': []}}}
    authenticated = False
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 1080}, device_scale_factor=1.5)
            await page.add_init_script("localStorage.setItem('language','zh');localStorage.setItem('theme','light');")

            async def handle(route):
                url = route.request.url
                if not url.startswith('http://manual.local/'):
                    return await route.abort()
                if '/api/' in url:
                    path = url.split('/api/', 1)[1]
                    if path == 'session':
                        return await route.fulfill(status=200 if authenticated else 401, json={'username': '演示管理员', 'csrf': 'synthetic-session'} if authenticated else {'error': 'Authentication required'})
                    if path == 'config': return await route.fulfill(json=cfg)
                    if path == 'status': return await route.fulfill(json=status)
                    return await route.fulfill(status=404, json={'error': 'Synthetic example only'})
                filename = 'index.html' if url.split('#')[0] == 'http://manual.local/' else url.split('/static/', 1)[-1]
                target = ROOT / 'static' / filename
                if target.parent != ROOT / 'static' or not target.is_file(): return await route.abort()
                await route.fulfill(path=str(target))

            await page.route('**/*', handle)
            await page.goto('http://manual.local/')
            await expect(page.locator('#loginSection')).to_be_visible()
            await page.locator('#loginSection').screenshot(path=str(OUT / '01-登录示例.png'))
            authenticated = True
            for route, filename in [('devices', '02-设备管理示例.png'), ('services', '03-服务目录示例.png'), ('services/new', '04-手动配置示例.png'), ('devices/device-demo', '05-工具授权示例.png')]:
                await page.goto('http://manual.local/#/' + route)
                await page.reload()
                await expect(page.locator('#workspace')).to_be_visible()
                await expect(page.locator('#view section')).to_be_visible()
                if route == 'services': await expect(page.get_by_role('img', name='发现成功')).to_be_visible()
                if route == 'services/new':
                    await page.get_by_role('textbox', name='名称', exact=True).fill('GitHub（演示）')
                    await page.get_by_role('textbox', name='MCP URL（已有地址留空保留）', exact=True).fill(service['url'])
                    await page.get_by_role('combobox', name='鉴权方式', exact=True).select_option('bearer')
                await page.locator('#view section').screenshot(path=str(OUT / filename))
                print('Captured synthetic UI:', filename)
            # Feishu read-only service: synthetic App ID only; the secret is never part of the public projection.
            feishu_tools = [{'name': n, 'description': d, 'inputSchema': {'type': 'object'}, 'schema_hash': 'demo', 'annotations': {'readOnlyHint': True}} for n, d in [('im_v1_chat_list', '演示：列出应用所在的群'), ('docx_v1_document_rawContent', '演示：读取文档纯文本')]]
            cfg['services']['feishu-demo'] = {'id': 'feishu-demo', 'name': '飞书只读（演示）', 'provider': 'feishu-readonly', 'transport': 'stdio', 'app_id': 'cli_demo_app', 'app_secret_configured': True, 'discovery': feishu_tools}
            await page.goto('http://manual.local/#/services/feishu-demo')
            await page.reload()
            await expect(page.get_by_role('textbox', name='App ID', exact=True)).to_have_value('cli_demo_app')
            await page.locator('#view section').screenshot(path=str(OUT / '06-飞书只读示例.png'))
            print('Captured synthetic UI:', '06-飞书只读示例.png')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
