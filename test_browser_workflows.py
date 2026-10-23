"""Optional headless browser regression. Synthetic API only; never touches live devices."""
import asyncio
import json
from pathlib import Path
import unittest
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright=None

@unittest.skipIf(async_playwright is None,'Optional Playwright browser environment not installed')
class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_wss_has_mask_and_unchanged_save_preserves_secret(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();saved=[]
            cfg={'version':1,'services':{},'devices':[{'id':'one','name':'Device one','enabled':False,'endpoint_configured':True,'bindings':{}}]}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                if '/api/devices/one' in url:
                    saved.append(route.request.post_data_json);return await route.fulfill(json={'saved_version':2})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            page.on('dialog',lambda d:d.accept())
            await page.goto('http://test.local/#/devices/one')
            endpoint=page.get_by_label('小智 WSS',exact=False)
            await endpoint.wait_for()
            self.assertEqual(await endpoint.get_attribute('placeholder'),'••••••••••••')
            self.assertEqual(await endpoint.input_value(),'')
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_timeout(100)
            self.assertEqual(saved[0]['endpoint'],'')

    async def test_bulk_tool_selection_is_service_scoped_and_saved(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();saved=[];errors=[]
            def service(sid,names):
                return {'id':sid,'name':sid,'url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False,'discovery':[{'name':name,'inputSchema':{},'schema_hash':'current'} for name in names]}
            cfg={'version':1,'services':{'github':service('github',['read','write','stale']),'other':service('other',['read']),'empty':service('empty',[])},'devices':[{'id':'one','name':'Device one','enabled':True,'endpoint_configured':True,'bindings':{'github':[{'name':'read','schema_hash':'current','alias':'read'},{'name':'stale','schema_hash':'old','alias':'stale'}],'other':[{'name':'read','schema_hash':'current','alias':'other_read'}]}}]}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                if '/api/devices/one' in url:
                    self.assertEqual(route.request.method,'PUT')
                    self.assertTrue(url.endswith('/api/devices/one'))
                    saved.append(route.request.post_data_json);return await route.fulfill(json={'saved_version':2})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            page.on('dialog',lambda d:d.accept())
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://test.local/#/devices/one')
            group=page.get_by_role('group',name='github',exact=True)
            await group.wait_for()
            self.assertEqual(await group.get_by_role('button',name='全选',exact=True).count(),1)
            self.assertEqual(await page.get_by_role('group',name='empty',exact=True).get_by_role('button',name='全选',exact=True).count(),0)
            other=page.get_by_role('group',name='other',exact=True).get_by_role('checkbox')
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[True,False,False])
            await group.get_by_role('button',name='反选',exact=True).click()
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[False,True,True])
            self.assertTrue(await other.is_checked());self.assertEqual(saved,[])
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(saved[-1]['bindings'],{'github':['write','stale']})
            await group.get_by_role('button',name='全选',exact=True).click()
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[True,True,True])
            self.assertTrue(await other.is_checked());self.assertEqual(len(saved),1)
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(saved[-1]['bindings'],{'github':['read','write','stale']})
            await group.get_by_role('button',name='全选',exact=True).click()
            await group.get_by_role('button',name='反选',exact=True).click()
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[False,False,False])
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(saved[-1]['bindings'],{'github':[]})
            self.assertEqual(len(saved),3);self.assertEqual(errors,[])
            await page.locator('#language').select_option('en')
            await page.get_by_role('group',name='github',exact=True).get_by_role('button',name='Select all',exact=True).wait_for()
            self.assertEqual(await page.get_by_role('group',name='github',exact=True).get_by_role('button',name='Invert selection',exact=True).count(),1)

    async def test_device_table_help_and_row_delete(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();deleted=[];dialogs=[]
            cfg={'version':1,'services':{'s':{'name':'Synthetic MCP'}},'devices':[{'id':'one','name':'Device one','enabled':False,'bindings':{'s':[{'name':'x'}]}}]}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                if '/api/devices/one' in url:
                    deleted.append(route.request.post_data_json);cfg['devices']=[];return await route.fulfill(json={'saved_version':2})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/devices')
            await page.get_by_role('table').wait_for(timeout=3000)
            help_button=page.locator('.toolbar .help-trigger')
            self.assertEqual(await help_button.inner_text(),'?')
            self.assertEqual(await help_button.evaluate('(e)=>getComputedStyle(e).borderRadius'),'50%')
            await help_button.hover()
            self.assertTrue(await page.locator('.toolbar [role=tooltip]').is_visible())
            await page.mouse.move(0,0);await help_button.focus()
            self.assertTrue(await page.locator('.toolbar [role=tooltip]').is_visible())
            async def accept(dialog):dialogs.append(dialog.message);await dialog.accept()
            page.on('dialog',accept)
            await page.get_by_role('row').filter(has_text='Device one').get_by_role('button',name='删除',exact=True).click()
            await page.wait_for_function("document.querySelectorAll('tbody tr').length === 0")
            self.assertEqual(len(dialogs),2);self.assertTrue(deleted[0]['confirm_bound'])

    async def test_device_and_service_add_links_share_toolbar_style(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page()
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json={'version':1,'services':{},'devices':[]})
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/devices')
            device=page.locator('.toolbar > a[href="#/devices/new"]')
            await device.wait_for(timeout=3000)
            device_style=await device.evaluate('(e)=>[getComputedStyle(e).backgroundColor,getComputedStyle(e).padding,getComputedStyle(e).borderRadius]')
            self.assertEqual(await device.inner_text(),'+ 新增')
            await page.get_by_role('link',name='MCP 服务目录',exact=True).click()
            service=page.locator('.toolbar > a[href="#/services/new"]')
            await service.wait_for()
            self.assertEqual(device_style,await service.evaluate('(e)=>[getComputedStyle(e).backgroundColor,getComputedStyle(e).padding,getComputedStyle(e).borderRadius]'))

    async def test_service_url_is_plain_editable_and_api_key_is_hidden_replaceable(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();saved=[]
            service={'id':'s','name':'Example','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'api-key','header':'X-Api-Key','configured':True},'private_network':False,'discovery':[]}
            cfg={'version':1,'services':{'s':service},'devices':[]}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                if '/api/services/s' in url:
                    saved.append(route.request.post_data_json);return await route.fulfill(json={'saved_version':2})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/services/s')
            url_input=page.get_by_label('MCP URL',exact=False)
            await url_input.wait_for()
            self.assertEqual(await url_input.get_attribute('type'),'text')
            self.assertEqual(await url_input.input_value(),'https://example.com/mcp')
            key=page.get_by_label('API Key',exact=True)
            self.assertEqual(await key.get_attribute('type'),'password')
            self.assertEqual(await key.input_value(),'')
            await url_input.fill('https://example.com/updated')
            await key.fill('synthetic-replacement')
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_timeout(100)
            self.assertEqual(saved[0]['url'],'https://example.com/updated')
            self.assertEqual(saved[0]['auth']['value'],'synthetic-replacement')

    async def test_brand_sidebar_breadcrumb_and_mobile_shell(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page(viewport={'width':1440,'height':1000})
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json={'version':1,'services':{},'devices':[]})
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/devices')
            await page.locator('#workspace').wait_for(state='visible')
            self.assertEqual(await page.locator('#accountName').inner_text(),'operator')
            self.assertIn('Agent MCP Proxy',await page.locator('.sidebar-brand').inner_text())
            self.assertEqual(await page.title(),'Agent MCP Proxy')
            self.assertNotIn('Linmo',await page.locator('body').inner_text())
            await page.get_by_role('link',name='MCP 服务目录',exact=True).click()
            await page.wait_for_function("document.getElementById('breadcrumb').textContent === 'MCP 服务目录'")
            self.assertEqual(await page.locator('#breadcrumb').inner_text(),'MCP 服务目录')
            self.assertEqual(await page.locator('nav a[aria-current="page"]').get_attribute('href'),'#/services')
            await page.locator('#language').select_option('en')
            await page.wait_for_timeout(50)
            self.assertEqual(await page.locator('#breadcrumb').inner_text(),'MCP Services')
            await page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    async def test_binding_cards_distinguish_ids_and_ignore_empty_grants(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page()
            service={'id':'s','name':'Synthetic MCP','transport':'sse','auth':{'mode':'none'},'url':'https://example.com/sse','private_network':False,'discovery':[]}
            cfg={'version':1,'services':{'s':service},'devices':[{'id':eid,'name':'Same name','bindings':{'s':grants}} for eid,grants in [('default',[{'name':'x'}]),('other-id',[{'name':'x'}]),('empty',[])]]}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                if '/api/status' in url:return await route.fulfill(json={'endpoints':{}})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/services')
            links=page.locator('.service-card .actions a[href^="#/devices/"]')
            await links.first.wait_for()
            self.assertEqual(await links.count(),2)
            self.assertNotEqual(await links.nth(0).inner_text(),await links.nth(1).inner_text())

    async def test_direct_import_and_bound_device_double_confirmation(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            self.addAsyncCleanup(browser.close)
            page=await browser.new_page()
            config={'version':1,'services':{},'devices':[]}
            deletes=[];dialogs=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            async def handler(route):
                url=route.request.url
                if '/api/' not in url:
                    filename='index.html' if url=='http://test.local/' else url.split('/static/')[-1]
                    return await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
                path=url.split('/api/')[1]
                if path=='session':return await route.fulfill(json={'csrf':'synthetic','username':'admin'})
                if path=='config':return await route.fulfill(json=config)
                if path=='status':return await route.fulfill(json={'endpoints':{}})
                if path=='services/import':
                    body=route.request.post_data_json
                    if body.get('preview'):return await route.fulfill(json={'entries':[{'name':'example'}]})
                    config['services']['new']={'id':'new','name':'example','transport':'streamable-http','auth':{'mode':'none'},'url':'https://example.com/mcp','private_network':False,'discovery':[]}
                    config['version']=2
                    return await route.fulfill(json={'ids':['new'],'saved_version':2})
                if path=='devices/device1' and route.request.method=='DELETE':
                    deletes.append(route.request.post_data_json);config['devices']=[];config['version']+=1
                    return await route.fulfill(json={'saved_version':config['version']})
                return await route.fulfill(status=404,json={})
            await page.route('http://test.local/**',handler)
            async def confirm(dialog):dialogs.append(dialog.message);await dialog.accept()
            page.on('dialog',confirm)
            await page.goto('http://test.local/')
            await page.get_by_role('link',name='MCP 服务目录').click()
            await page.get_by_role('link',name='+ 新增').click()
            await page.get_by_role('tab',name='导入 JSON').click()
            await page.locator('textarea').fill(json.dumps({'mcpServers':{'example':{'url':'https://example.com/mcp'}}}))
            await page.get_by_role('button',name='确认导入').click()
            await page.wait_for_url('**/#/services')
            self.assertIn('new',config['services'])
            self.assertEqual(len(dialogs),1)
            config['devices']=[{'id':'device1','name':'Synthetic device','enabled':False,'bindings':{'new':[{'name':'lookup','schema_hash':'x','alias':'x'}]}}]
            await page.get_by_role('button',name='刷新',exact=True).click()
            await page.get_by_role('link',name='设备管理',exact=True).click()
            await page.get_by_role('link',name='Synthetic device',exact=True).click()
            await page.get_by_role('button',name='删除',exact=True).click()
            await page.wait_for_url('**/#/devices')
            self.assertEqual(len(dialogs),3)
            self.assertIn('Synthetic device',dialogs[-1]);self.assertIn('example',dialogs[-1])
            self.assertEqual(deletes,[{'version':2,'confirm_bound':True}])
            self.assertEqual(config['devices'],[]);self.assertEqual(errors,[])

if __name__=='__main__':unittest.main()
