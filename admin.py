"""Local proxy administration API. No business tools are executed by management requests."""
import hmac
from urllib.parse import urlsplit
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,Response
from admin_auth import AuthError
from admin_config import ConfigError


def create_app(store,manager,auth,allowed_origin):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    assets=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=assets),name='static')

    @app.get('/')
    async def index():return FileResponse(assets/'index.html')
    host=urlsplit(allowed_origin).netloc

    @app.middleware('http')
    async def boundary(request:Request,call_next):
        def reject(status,message):return JSONResponse({'error':message},status_code=status,headers={'Cache-Control':'no-store'})
        if request.headers.get('host')!=host:return reject(403,'Origin rejected')
        if request.method not in ('GET','HEAD') and request.headers.get('origin')!=allowed_origin:return reject(403,'Origin rejected')
        length=request.headers.get('content-length')
        try:
            if length and int(length)>65536:return reject(413,'Request too large')
        except ValueError:return reject(400,'Invalid request')
        if request.method not in ('GET','HEAD'):
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>65536:return reject(413,'Request too large')
            request._body=bytes(body)
        if request.url.path.startswith('/api/') and request.url.path!='/api/login':
            try:
                session=auth.authenticate(request.cookies.get('linmo_admin'))
                request.state.session=session
                if request.method not in ('GET','HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):return reject(403,'CSRF rejected')
            except AuthError as e:return reject(e.status,'Authentication required')
        if hasattr(store,'add_service') and (request.url.path=='/api/upstreams' or request.url.path.startswith('/api/endpoints') or (request.url.path=='/api/config' and request.method=='PUT')):
            return reject(410,'Use device and service management APIs')
        try:response=await call_next(request)
        except Exception:return reject(500,'Management request failed')
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'",'X-Frame-Options':'DENY'})
        return response

    @app.exception_handler(ConfigError)
    async def config_error(request,error):return JSONResponse({'error':str(error),**({'error_code':error.code} if error.code else {})},status_code=error.status)

    async def payload(request):
        try:
            value=await request.json()
            if not isinstance(value,dict):raise ValueError()
            return value
        except Exception:raise ConfigError('Invalid request') from None

    @app.post('/api/login')
    async def login(request:Request):
        body=await payload(request)
        if set(body)!={'username','password'} or not all(isinstance(v,str) for v in body.values()):raise ConfigError('Invalid request')
        try:token,csrf=auth.login(body['username'],body['password'],request.client.host)
        except AuthError as error:return JSONResponse({'error':'Login rejected'},status_code=error.status)
        response=JSONResponse({'csrf':csrf});response.set_cookie('linmo_admin',token,max_age=86400,httponly=True,samesite='strict',secure=False,path='/');return response

    @app.post('/api/logout')
    async def logout(request:Request):
        auth.logout(request.cookies.get('linmo_admin'));response=Response(status_code=204);response.delete_cookie('linmo_admin',path='/');return response

    @app.get('/api/session')
    async def session(request:Request):return {k:request.state.session[k] for k in ('username','csrf')}

    @app.get('/api/config')
    async def config():return store.public()

    @app.put('/api/config')
    async def save(request:Request):
        body=await payload(request);version=body.pop('version',None)
        saved=store.save(body,version)
        return {'saved_version':saved,'requires_apply':True,'save_warning':store.save_warning}

    @app.get('/api/status')
    async def status():return manager.snapshot()

    async def current(request):
        body=await payload(request)
        if set(body)!={'version'}:raise ConfigError('Invalid request')
        config=store.load()
        if type(body['version']) is not int or body['version']!=config['version']:raise ConfigError('Configuration version conflict',409)
        return config

    @app.post('/api/test')
    async def test(request:Request):return await manager.test(await current(request))

    @app.post('/api/apply',status_code=202)
    async def apply(request:Request):
        await manager.apply(await current(request));return {'state':'applying'}

    @app.post('/api/upstreams')
    async def add_upstream(request:Request):
        body=await payload(request);version=body.pop('version',None)
        return {'saved_version':store.add_upstream(body,version),'requires_apply':True}

    @app.get('/api/endpoints')
    async def endpoints():return {'version':store.public()['version'],'endpoints':store.public()['endpoints']}

    @app.post('/api/endpoints')
    async def add_endpoint(request:Request):
        body=await payload(request);version=body.pop('version',None)
        return store.add_endpoint(body,version)

    @app.put('/api/endpoints/{eid}')
    async def update_endpoint(eid:str,request:Request):
        body=await payload(request);version=body.pop('version',None)
        return {'saved_version':store.update_endpoint(eid,body,version),'requires_apply':True}

    @app.delete('/api/endpoints/{eid}')
    async def delete_endpoint(eid:str,request:Request):
        body=await payload(request)
        if set(body)!={'version'}:raise ConfigError()
        version=store.delete_endpoint(eid,body['version'])
        await manager.remove_endpoint(eid)
        return {'saved_version':version}

    @app.post('/api/endpoints/{eid}/apply',status_code=202)
    async def apply_endpoint(eid:str,request:Request):
        await manager.apply_endpoint(await current(request),eid);return {'state':'applying'}

    @app.get('/api/services')
    async def services():
        value=store.public();return {'version':value['version'],'services':value['services']}

    @app.post('/api/services/import')
    async def import_services(request:Request):
        body=await payload(request)
        if set(body)-{'version','document','descriptor_url','preview','service_auth'} or ('document' in body)==('descriptor_url' in body):raise ConfigError('Invalid import request')
        from service_import import parse_import,fetch_descriptor
        override=body.get('service_auth')
        if override is not None:
            from service_transport import auth_headers
            if 'descriptor_url' not in body or not isinstance(override,dict) or set(override)-{'mode','header','value'}:raise ConfigError('Invalid service authentication')
            try:auth_headers(override)
            except ValueError:raise ConfigError('Invalid service authentication') from None
        try:entries=await fetch_descriptor(body['descriptor_url'],auth_override=override) if 'descriptor_url' in body else parse_import(body['document'])
        except ConfigError:raise
        except Exception:raise ConfigError('Descriptor unavailable or rejected',502) from None
        if body.get('preview',False):
            from urllib.parse import urlsplit
            return {'entries':[{'name':e['name'],'transport':e['transport'],'auth_mode':e['auth']['mode'],'private_network':e['private_network'],'origin':urlsplit(e['url']).scheme+'://'+urlsplit(e['url']).netloc} for e in entries]}
        return store.import_services(entries,body.get('version'))

    @app.get('/api/services/{sid}')
    async def service(sid:str):
        value=store.public()
        if sid not in value['services']:raise ConfigError('Service not found',404)
        return {'version':value['version'],'service':value['services'][sid]}

    @app.post('/api/services')
    async def add_service(request:Request):
        body=await payload(request);version=body.pop('version',None);return store.add_service(body,version)

    @app.put('/api/services/{sid}')
    async def update_service(sid:str,request:Request):
        body=await payload(request);version=body.pop('version',None);return store.update_service(sid,body,version)

    @app.delete('/api/services/{sid}')
    async def delete_service(sid:str,request:Request):
        body=await payload(request)
        if set(body)!={'version'}:raise ConfigError()
        return {'saved_version':store.delete_service(sid,body['version'])}

    @app.post('/api/services/{sid}/discover')
    async def discover_service(sid:str,request:Request):
        config=await current(request)
        if sid not in config['services']:raise ConfigError('Service not found',404)
        from service_transport import discover_service
        from feishu_stdio import error_code,MESSAGES
        try:tools=await discover_service(config['services'][sid])
        except Exception as error:
            code=error_code(error)
            if code:raise ConfigError(MESSAGES[code],429 if code in ('feishu_discovery_busy','feishu_capacity') else 503,code) from None
            raise ConfigError('Service discovery unavailable or rejected',502) from None
        return {'saved_version':store.record_discovery(sid,tools,config['version']),'discovered':len(tools)}

    @app.get('/api/devices')
    async def devices():
        value=store.public();return {'version':value['version'],'devices':value['devices']}

    @app.get('/api/devices/{eid}')
    async def device(eid:str):
        value=store.public();found=next((e for e in value['devices'] if e['id']==eid),None)
        if found is None:raise ConfigError('Device not found',404)
        return {'version':value['version'],'device':found}

    @app.post('/api/devices')
    async def add_device(request:Request):
        body=await payload(request);version=body.pop('version',None);return store.add_device(body,version)

    @app.put('/api/devices/{eid}')
    async def update_device(eid:str,request:Request):
        body=await payload(request);version=body.pop('version',None)
        saved=store.update_device(eid,body,version)
        if body.get('enabled') is False:await manager.remove_endpoint(eid)
        return {'saved_version':saved,'requires_apply':True}

    @app.delete('/api/devices/{eid}')
    async def delete_device(eid:str,request:Request):
        body=await payload(request)
        if set(body)-{'version','confirm_bound'} or 'version' not in body:raise ConfigError()
        value=store.load();device=next((d for d in value['devices'] if d['id']==eid),None)
        if device is None:raise ConfigError('Device not found',404)
        if any(device['bindings'].values()) and body.get('confirm_bound') is not True:raise ConfigError('Confirm deletion of device with bound MCP services',409)
        saved=store.delete_device(eid,body['version']);await manager.remove_endpoint(eid)
        return {'saved_version':saved}

    @app.post('/api/devices/{eid}/apply',status_code=202)
    async def apply_device(eid:str,request:Request):
        await manager.apply_endpoint(await current(request),eid);return {'state':'applying'}

    return app
