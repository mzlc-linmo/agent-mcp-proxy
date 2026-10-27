"""Optional headless browser regression. Synthetic API only; never touches live devices."""
import asyncio
import json
from pathlib import Path
import unittest
try:
    from playwright.async_api import async_playwright,expect
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

    async def test_lists_filter_before_paging_and_keep_independent_state(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();errors=[];writes=[]
            discovery_started=asyncio.Event();finish_discovery=asyncio.Event()
            cfg={'version':1,'devices':[{'id':f'device-{i}','name':f'{"Alpha " if i in (1,6) else ""}Device {i}','enabled':False,'endpoint_configured':True,'bindings':{}} for i in range(1,7)],'services':{f'service-{i}':{'id':f'service-{i}','name':f'{"Alpha " if i in (1,11) else ""}Service {i}','url':f'https://example.com/visible-{i}','transport':'streamable-http','auth':{'mode':'bearer','configured':True},'private_network':False,'discovery':[]} for i in range(1,12)}}
            async def handler(route):
                url=route.request.url
                if '/api/' in url:
                    path=url.split('/api/')[1]
                    if path=='session':return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                    if path=='config':return await route.fulfill(json=cfg)
                    if path=='status':return await route.fulfill(json={'endpoints':{}})
                    writes.append((route.request.method,path))
                    if path=='services/service-6/discover':
                        discovery_started.set();await finish_discovery.wait()
                        return await route.fulfill(json={'saved_version':cfg['version'],'discovered':0})
                    if route.request.method=='DELETE':
                        kind,item_id=path.split('/')
                        if kind=='devices':cfg['devices']=[d for d in cfg['devices'] if d['id']!=item_id]
                        else:del cfg['services'][item_id]
                        cfg['version']+=1
                        return await route.fulfill(json={'saved_version':cfg['version']})
                    return await route.fulfill(status=404,json={})
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            page.on('dialog',lambda d:d.accept())
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto('http://test.local/#/devices')
            for kind,menu,count,last_page,query,identity,visible_url,selector in [
                ('devices','设备管理',6,2,'device','DEVICE-6',None,'.device-table tbody tr'),
                ('services','MCP 服务目录',11,3,'service','SERVICE-11','VISIBLE-11','.service-card'),
            ]:
                with self.subTest(kind=kind):
                    await page.get_by_role('link',name=menu,exact=True).click()
                    items=page.locator(selector)
                    await items.first.wait_for()
                    for label in await page.locator('.list-controls label').all():
                        aligned=await label.evaluate('(e)=>{const range=document.createRange();range.selectNode(e.firstChild);const text=range.getBoundingClientRect();const control=e.lastElementChild.getBoundingClientRect();return text.right<=control.left&&Math.abs((text.top+text.bottom-control.top-control.bottom)/2)<1;}')
                        self.assertTrue(aligned,'Query/page-size label and control must share a row')
                    self.assertEqual(await items.count(),5)
                    self.assertTrue(await page.get_by_role('button',name='上一页',exact=True).is_disabled())
                    for _ in range(last_page-1):await page.get_by_role('button',name='下一页',exact=True).click()
                    self.assertEqual(await items.count(),1)
                    self.assertTrue(await page.get_by_role('button',name='下一页',exact=True).is_disabled())
                    search=page.get_by_role('searchbox')
                    await search.fill('  aLpHa  ')
                    self.assertEqual(await items.count(),2)
                    self.assertTrue(await page.get_by_role('button',name='上一页',exact=True).is_disabled())
                    self.assertTrue(await page.get_by_role('button',name='下一页',exact=True).is_disabled())
                    await search.fill(identity)
                    self.assertEqual(await items.count(),1)
                    if visible_url:
                        await search.fill(visible_url)
                        self.assertEqual(await items.count(),1)
                        await search.fill('bearer')
                        self.assertEqual(await items.count(),0)  # Authentication metadata is not a searchable field.
                    await search.fill('no-such-record')
                    self.assertEqual(await items.count(),0)
                    self.assertTrue(await page.get_by_text('没有匹配结果',exact=True).is_visible())
                    self.assertTrue(await page.get_by_role('button',name='下一页',exact=True).is_disabled())
                    await search.fill(query)
                    self.assertEqual(await items.count(),5)
                    await page.get_by_role('combobox',name='每页条数',exact=True).select_option('10')
                    self.assertEqual(await items.count(),min(count,10))
                    if kind=='services':
                        await page.get_by_role('button',name='下一页',exact=True).click()
                        self.assertEqual(await items.count(),1)
                    await page.get_by_role('combobox',name='每页条数',exact=True).select_option('20')
                    self.assertEqual(await items.count(),count)
                    self.assertTrue(await page.get_by_role('button',name='上一页',exact=True).is_disabled())
                    await page.get_by_role('combobox',name='每页条数',exact=True).select_option('5')
                    await page.get_by_role('button',name='下一页',exact=True).click()
                    name_link=items.first.locator('a').first
                    await name_link.click()
                    await page.get_by_role('link',name='返回列表',exact=True).click()
                    await expect(search).to_have_value(query)
                    await expect(page.get_by_role('button',name='上一页',exact=True)).to_be_enabled()
                    self.assertEqual(await items.count(),1 if kind=='devices' else 5)
                    await page.get_by_role('button',name='刷新',exact=True).click()
                    await expect(search).to_have_value(query)
                    await expect(page.get_by_role('button',name='上一页',exact=True)).to_be_enabled()
                    if kind=='services':await page.get_by_role('button',name='下一页',exact=True).click()
                    await items.last.get_by_role('button',name='删除',exact=True).click()
                    await page.wait_for_function('(s)=>document.querySelectorAll(s).length===5',arg=selector)
                    await expect(search).to_have_value(query)
                    self.assertEqual(await page.locator('.pagination-summary').inner_text(),f'共 {count-1} 条 · 第 {last_page-1} / {last_page-1} 页')
            await page.get_by_role('link',name='设备管理',exact=True).click()
            await expect(page.get_by_role('searchbox')).to_have_value('device')
            self.assertTrue(await page.get_by_role('button',name='上一页',exact=True).is_disabled())
            await page.get_by_role('combobox',name='每页条数',exact=True).select_option('20')
            await page.get_by_role('link',name='MCP 服务目录',exact=True).click()
            await expect(page.get_by_role('searchbox')).to_have_value('service')
            await expect(page.get_by_role('button',name='上一页',exact=True)).to_be_enabled()
            await page.locator('#language').select_option('en')
            await expect(page.get_by_role('combobox',name='Items per page',exact=True)).to_have_value('5')
            self.assertEqual(await page.locator('.pagination-summary').inner_text(),'Total 10 · Page 2 / 2')
            self.assertTrue(await page.get_by_role('button',name='Next',exact=True).is_disabled())
            await page.get_by_role('link',name='Devices',exact=True).click()
            await expect(page.get_by_role('combobox',name='Items per page',exact=True)).to_have_value('20')
            await expect(page.get_by_role('searchbox')).to_have_value('device')
            await page.get_by_role('link',name='MCP Services',exact=True).click()
            await expect(page.get_by_role('combobox',name='Items per page',exact=True)).to_have_value('5')
            await page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            await page.locator('.service-card').first.get_by_role('button',name='Discover tools (no execution)',exact=True).click()
            await discovery_started.wait()
            try:
                await page.get_by_role('searchbox').fill('service')
                await expect(page.locator('.service-card').first.get_by_role('button',name='Discover tools (no execution)',exact=True)).to_be_disabled()
                await expect(page.get_by_role('button',name='Next',exact=True)).to_be_disabled()
            finally:
                finish_discovery.set()
                await expect(page.get_by_role('button',name='Next',exact=True)).to_be_enabled()
            self.assertEqual(writes,[('DELETE','devices/device-6'),('DELETE','services/service-11'),('POST','services/service-6/discover')])
            self.assertEqual(errors,[])

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
            await expect(page.get_by_text('HTTP 不加密：密码、会话及新密钥可能被监听，仅可信局域网使用。',exact=True)).to_have_count(0)
            await page.get_by_role('link',name='MCP 服务目录',exact=True).click()
            await page.wait_for_function("document.getElementById('breadcrumb').textContent === 'MCP 服务目录'")
            self.assertEqual(await page.locator('#breadcrumb').inner_text(),'MCP 服务目录')
            self.assertEqual(await page.locator('nav a[aria-current="page"]').get_attribute('href'),'#/services')
            await page.locator('#language').select_option('en')
            await page.wait_for_timeout(50)
            self.assertEqual(await page.locator('#breadcrumb').inner_text(),'MCP Services')
            warning=page.get_by_text('HTTP is unencrypted: passwords, sessions and new credentials may be intercepted. Trusted LAN only.',exact=True)
            await expect(warning).to_have_count(0)
            await page.get_by_role('link',name='System notes',exact=True).click()
            await expect(warning).to_be_visible()
            await page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    async def test_discovery_success_uses_accessible_icon(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page()
            service={'id':'found','name':'Found service','transport':'streamable-http','auth':{'mode':'none'},'url':'https://example.com/mcp','private_network':False,'discovery':[{'name':'lookup','inputSchema':{},'schema_hash':'synthetic'}]}
            cfg={'version':1,'devices':[],'services':{'found':service,'pending':dict(service,id='pending',name='Pending service',discovery=[])}}
            async def handler(route):
                url=route.request.url
                if '/api/session' in url:return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if '/api/config' in url:return await route.fulfill(json=cfg)
                filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
            await page.route('http://test.local/**',handler)
            await page.goto('http://test.local/#/services')
            await page.locator('.service-card').first.wait_for()
            for language,label,unchecked in [('zh','发现成功','未检测'),('en','Discovery succeeded','Not checked')]:
                await page.locator('#language').select_option(language)
                icon=page.get_by_role('img',name=label,exact=True)
                await expect(icon).to_have_text('✓')
                await expect(icon).to_have_attribute('title',label)
                await expect(page.locator('.service-card').get_by_text(label,exact=True)).to_have_count(0)
                await expect(page.locator('.service-card').get_by_text(unchecked,exact=True)).to_be_visible()

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

    async def test_feishu_form_redacts_secret_and_requires_explicit_device_apply(self):
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True);self.addAsyncCleanup(browser.close)
            page=await browser.new_page();errors=[];writes=[];secret='synthetic-feishu-ui-secret';failure={}
            schema={'type':'object'}
            http={'id':'http1','name':'HTTP one','url':'https://example.com/mcp','transport':'streamable-http','auth':{'mode':'none'},'private_network':False,'discovery':[{'name':'read','inputSchema':schema,'schema_hash':'h'}]}
            cfg={'version':1,'services':{'http1':http},'devices':[{'id':'one','name':'Device one','enabled':True,'endpoint_configured':True,'bindings':{'http1':[{'name':'read','schema_hash':'h','alias':'http1__read'}]}}]}
            tools=[{'name':n,'description':'只读','inputSchema':schema,'schema_hash':'fh'} for n in ('im_v1_chat_list','docx_v1_document_rawContent')]
            async def handler(route):
                url=route.request.url;method=route.request.method
                if '/api/' not in url:
                    filename='index.html' if url.split('#')[0]=='http://test.local/' else url.split('/static/')[-1]
                    return await route.fulfill(path=str(Path(__file__).parent/'static'/filename))
                path=url.split('/api/')[1]
                if path=='session':return await route.fulfill(json={'csrf':'synthetic','username':'operator'})
                if path=='config':return await route.fulfill(json=cfg)
                if path=='status':return await route.fulfill(json={'endpoints':{}})
                body=route.request.post_data_json;writes.append((method,path,body))
                if failure.get(path):
                    status,code=failure.pop(path);return await route.fulfill(status=status,json={'error':'x','error_code':code})
                cfg['version']+=1;fs=cfg['services'].get('fs')
                if (method,path)==('POST','services'):
                    cfg['services']['fs']={'id':'fs','name':body['name'],'provider':'feishu-readonly','transport':'stdio','app_id':body['app_id'],'app_secret_configured':True,'discovery':[]}
                    return await route.fulfill(json={'id':'fs','saved_version':cfg['version']})
                if (method,path)==('PUT','services/fs'):
                    fs.update(name=body['name'],app_id=body['app_id']);affected=[]
                    if body['app_secret'] or body['app_id']!='cli_synthetic':
                        fs['discovery']=[]
                        for d in cfg['devices']:
                            for g in d['bindings'].get('fs',[]):g['schema_hash']='review-required'
                            if d['bindings'].get('fs'):affected.append(d['id'])
                    return await route.fulfill(json={'saved_version':cfg['version'],'affected_device_ids':affected})
                if (method,path)==('POST','services/fs/discover'):
                    fs['discovery']=[dict(t) for t in tools];return await route.fulfill(json={'saved_version':cfg['version'],'discovered':2})
                if (method,path)==('PUT','devices/one'):
                    for sid,names in body.get('bindings',{}).items():cfg['devices'][0]['bindings'][sid]=[{'name':n,'schema_hash':'fh','alias':sid+'__'+n} for n in names]
                    return await route.fulfill(json={'saved_version':cfg['version'],'requires_apply':True})
                if (method,path)==('POST','devices/one/apply'):return await route.fulfill(status=202,json={'state':'applying'})
                return await route.fulfill(status=500,json={'error':'unexpected'})
            await page.route('http://test.local/**',handler)
            page.on('dialog',lambda d:d.accept());page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://test.local/#/services/new')
            await page.get_by_role('tab',name='飞书只读',exact=True).click()
            await page.get_by_label('App ID',exact=True).wait_for()
            labels=await page.locator('#view form label').all_inner_texts()
            self.assertEqual([l.strip() for l in labels],['名称','App ID','App Secret'])
            await expect(page.locator('#view p',has_text='应用身份 · 仅只读工具 · 运行于代理容器')).to_be_visible()
            self.assertEqual(await page.get_by_label('App Secret',exact=True).get_attribute('type'),'password')
            await page.get_by_label('名称',exact=True).fill('飞书只读')
            await page.get_by_label('App ID',exact=True).fill('cli_synthetic')
            await page.get_by_label('App Secret',exact=True).fill(secret)
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("location.hash==='#/services/fs'")
            self.assertEqual(writes,[('POST','services',{'version':1,'name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic','app_secret':secret})])
            secret_box=page.get_by_label('App Secret',exact=True)
            await secret_box.wait_for()
            self.assertEqual(await secret_box.input_value(),'');self.assertEqual(await secret_box.get_attribute('placeholder'),'凭据已配置，留空保留')
            self.assertNotIn(secret,await page.content())
            # Blank secret edit keeps it; discovery does not grant or apply anything.
            await page.get_by_label('名称',exact=True).fill('飞书只读2')
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(writes[-1],('PUT','services/fs',{'version':2,'name':'飞书只读2','app_id':'cli_synthetic','app_secret':''}))
            await page.get_by_role('button',name='发现工具（不执行工具）',exact=True).click()
            await page.get_by_text('im_v1_chat_list',exact=True).wait_for()
            self.assertEqual([w[:2] for w in writes],[('POST','services'),('PUT','services/fs'),('POST','services/fs/discover')])
            # Busy/capacity/runtime errors show localized text and do not claim success.
            for status,code,text in ((429,'feishu_discovery_busy','该服务正在发现工具'),(429,'feishu_capacity','飞书进程已达上限'),(503,'feishu_runtime_unavailable','飞书运行环境不可用'),(503,'feishu_session_unavailable','飞书连接失败')):
                failure['services/fs/discover']=(status,code)
                await page.get_by_role('button',name='发现工具（不执行工具）',exact=True).click()
                await page.wait_for_function("t=>document.getElementById('message').textContent.includes(t)",arg=text)
                self.assertNotIn('已保存',await page.locator('#message').inner_text())
                await expect(page.get_by_text('im_v1_chat_list',exact=True)).to_be_visible()
            # Cards: identity badges, no URL; App ID is not searchable, name and ID are.
            await page.goto('http://test.local/#/services')
            card=page.locator('.service-card',has_text='飞书只读2')
            for badge in ('飞书应用身份','stdio','只读'):await expect(card.locator('.badge',has_text=badge).first).to_be_visible()
            self.assertNotIn('http',(await card.locator('p').inner_text()).lower())
            search=page.get_by_label('查询服务（名称 / ID / URL）',exact=True)
            for query,count in (('cli_synthetic',0),(secret,0),('飞书只读2',1),('fs',1)):
                await search.fill(query);await expect(page.locator('.service-card')).to_have_count(count)
            await search.fill('')
            # Device grants: select-all only touches the Feishu group; saving and applying are explicit.
            await page.goto('http://test.local/#/devices/one')
            group=page.get_by_role('group',name='飞书只读2',exact=True);await group.wait_for()
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[False,False])
            await group.get_by_role('button',name='全选',exact=True).click()
            self.assertTrue(await page.get_by_role('group',name='HTTP one',exact=True).get_by_role('checkbox').is_checked())
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(writes[-1][2]['bindings'],{'fs':['im_v1_chat_list','docx_v1_document_rawContent']})
            self.assertFalse(any(w[1].endswith('/apply') for w in writes))
            await page.get_by_role('button',name='应用该设备',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('已保存')")
            self.assertEqual(writes[-1][:2],('POST','devices/one/apply'))
            # Rotation names affected devices; after rediscovery grants stay unchecked and marked for review.
            await page.goto('http://test.local/#/services/fs')
            await page.get_by_label('App Secret',exact=True).fill('rotated-secret')
            await page.get_by_role('button',name='保存',exact=True).click()
            await page.wait_for_function("document.getElementById('message').textContent.includes('受影响设备: Device one')")
            await expect(page.get_by_text('尚未发现工具，请先发现。',exact=True)).to_be_visible()
            await page.get_by_role('button',name='发现工具（不执行工具）',exact=True).click()
            await page.get_by_text('im_v1_chat_list',exact=True).wait_for()
            count=len(writes)
            await page.goto('http://test.local/#/devices/one')
            group=page.get_by_role('group',name='飞书只读2',exact=True);await group.wait_for()
            self.assertEqual(await group.get_by_role('checkbox').evaluate_all('(items)=>items.map(e=>e.checked)'),[False,False])
            await expect(group.get_by_text('待复核',exact=False)).to_have_count(2)
            self.assertEqual(len(writes),count)
            # English, and the narrow layout has no root overflow.
            await page.goto('http://test.local/#/services/new')
            await page.get_by_role('tab',name='飞书只读',exact=True).wait_for()
            await page.locator('#language').select_option('en')
            await page.get_by_role('tab',name='Feishu read-only',exact=True).click()
            await page.get_by_label('App Secret',exact=True).wait_for()
            await expect(page.locator('#view p',has_text='App identity · read-only tools only · runs in the proxy container')).to_be_visible()
            await page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            self.assertEqual(errors,[])

if __name__=='__main__':unittest.main()
