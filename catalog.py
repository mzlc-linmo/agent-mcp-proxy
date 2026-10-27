"""Version-three shared service catalog and device grants; no runtime connections here."""
import copy
import hashlib
import json
import threading
import uuid
from pathlib import Path
from admin_config import ConfigError,ConfigStore
from policy import ALLOWED
from service_transport import validate_target,auth_headers
import feishu_readonly
import local_stdio


def schema_hash(schema):
    return hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def tool_alias(sid,name):
    base=sid+'__'+name
    return base if len(base)<=64 else base[:47]+'_'+hashlib.sha256(base.encode()).hexdigest()[:16]

class CatalogStore:
    def __init__(self,path):
        self.path=Path(path);self.lock=threading.RLock();self.disk=ConfigStore(self.path,'http://higress:8080')
    @property
    def save_warning(self):return self.disk.save_warning
    def load(self):
        with self.lock:
            try:
                value=json.loads(self.path.read_text())
                if value.get('schema_version')!=3:
                    import os
                    original=self.path.read_bytes()
                    value=self._migrate(value);self._validate(value)
                    backup=self.path.with_suffix('.v2-backup.json')
                    if not backup.exists():
                        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'wb') as stream:stream.write(original);stream.flush();os.fsync(stream.fileno())
                    self.disk._write(value)
                self._validate(value);return value
            except ConfigError:raise
            except Exception:raise ConfigError('Catalog unavailable',503) from None
    def _migrate(self,old):
        if old.get('schema_version') not in (None,2):raise ConfigError('Unsupported configuration version')
        services={}
        for sid,entry in old['upstreams'].items():
            names=sorted(ALLOWED[sid]) if sid in ALLOWED else entry.get('tools',[])
            tools=[{'name':name,'description':name,'inputSchema':{},'schema_hash':'legacy-review'} for name in names]
            services[sid]={'id':sid,'name':sid,'url':entry['url'],'transport':'streamable-http','auth':{'mode':'api-key' if entry['key'] else 'none','header':'X-Internal-Token','value':entry['key']},'private_network':True,'discovery':tools,'legacy_profile':sid if sid in ALLOWED else None}
        devices=[]
        for e in old.get('endpoints',[dict(id='default',name='Default Xiaozhi',endpoint=old['endpoint'],enabled=True,enabled_tools=old['enabled_tools'])]):
            bindings={sid:[{'name':t['name'],'schema_hash':t['schema_hash'],'alias':t['name']} for t in service['discovery'] if t['name'] in e['enabled_tools']] for sid,service in services.items()}
            devices.append({k:e[k] for k in ('id','name','endpoint','enabled')}|{'platform':'xiaozhi','bindings':bindings})
        return {'schema_version':3,'version':old['version'],'services':services,'devices':devices}
    def public(self):
        v=self.load();services={}
        for sid,s in v['services'].items():
            if feishu_readonly.is_feishu(s):
                services[sid]={k:copy.deepcopy(s[k]) for k in ('id','name','provider','transport','app_id','discovery')}|{'app_secret_configured':bool(s['app_secret'])}
                continue
            if local_stdio.is_local(s):
                services[sid]=copy.deepcopy(local_stdio.public(s));continue
            from urllib.parse import urlsplit
            u=urlsplit(s['url']);safe_url=s['url'] if not u.query else u.scheme+'://'+u.netloc+'/…'
            services[sid]={k:copy.deepcopy(s[k]) for k in ('id','name','transport','private_network','discovery')}
            services[sid].update(url=safe_url,url_has_secret_query=bool(u.query),auth={'mode':s['auth']['mode'],'header':s['auth'].get('header',''),'configured':bool(s['auth'].get('value'))})
        devices=[{k:copy.deepcopy(e[k]) for k in ('id','name','platform','enabled','bindings')}|{'endpoint_configured':bool(e['endpoint'])} for e in v['devices']]
        return {'version':v['version'],'services':services,'devices':devices,'save_warning':self.save_warning}
    def _change(self,version,fn):
        with self.lock:
            v=self.load()
            if type(version) is not int or version!=v['version']:raise ConfigError('Configuration version conflict',409)
            fn(v);v['version']+=1;self._validate(v);self.disk._write(v);return v['version']
    def add_service(self,entry,version):
        if not isinstance(entry,dict):raise ConfigError()
        if 'provider' in entry:return self._add_feishu(entry,version)
        if entry.get('transport')==local_stdio.TRANSPORT:return self._add_local(entry,version)
        if set(entry)!={'name','url','transport','auth','private_network'}:raise ConfigError()
        sid=uuid.uuid4().hex[:12]
        saved=self._change(version,lambda v:v['services'].update({sid:copy.deepcopy(entry)|{'id':sid,'discovery':[],'legacy_profile':None}}))
        return {'id':sid,'saved_version':saved}
    def _add_feishu(self,entry,version):
        if set(entry)!={'name','provider','transport','app_id','app_secret'} or entry['provider']!=feishu_readonly.PROVIDER or entry['transport']!=feishu_readonly.TRANSPORT:raise ConfigError()
        self._feishu_credentials(entry['app_id'],entry['app_secret'])
        sid=uuid.uuid4().hex[:12]
        saved=self._change(version,lambda v:v['services'].update({sid:copy.deepcopy(entry)|{'id':sid,'discovery':[],'legacy_profile':None}}))
        return {'id':sid,'saved_version':saved}

    @staticmethod
    def _local_entry(entry):
        if not isinstance(entry,dict) or set(entry)!=local_stdio.INPUT_KEYS or entry['transport']!=local_stdio.TRANSPORT:raise ConfigError()
        if not isinstance(entry['name'],str) or not 1<=len(entry['name'])<=80:raise ConfigError()
        # Fixed messages: env values are credentials and never reach errors.
        try:local_stdio.validate_launch(entry['command'],entry['args'],entry['env'])
        except ValueError:raise ConfigError('Invalid command, arguments or environment',422,'invalid_stdio_launch') from None
        return copy.deepcopy(entry)

    def _add_local(self,entry,version):
        clean=self._local_entry(entry);sid=uuid.uuid4().hex[:12]
        saved=self._change(version,lambda v:v['services'].update({sid:clean|{'id':sid,'discovery':[],'legacy_profile':None}}))
        return {'id':sid,'saved_version':saved}

    @staticmethod
    def _feishu_credentials(app_id,app_secret):
        # Validation messages are fixed; credential values never reach errors.
        try:feishu_readonly.validate_credentials(app_id,app_secret)
        except ValueError:raise ConfigError('Invalid Feishu application credentials',422,'invalid_feishu_credentials') from None

    def import_services(self,entries,version):
        if not isinstance(entries,list) or not entries:raise ConfigError()
        ids=[]
        def change(v):
            for entry in entries:
                if isinstance(entry,dict) and entry.get('transport')==local_stdio.TRANSPORT:entry=self._local_entry(entry)
                elif not isinstance(entry,dict) or set(entry)!={'name','url','transport','auth','private_network'}:raise ConfigError()
                sid=uuid.uuid4().hex[:12];ids.append(sid)
                v['services'][sid]=copy.deepcopy(entry)|{'id':sid,'discovery':[],'legacy_profile':None}
        saved=self._change(version,change)
        return {'ids':ids,'saved_version':saved}

    def record_discovery(self,sid,tools,version):
        def change(v):
            service=self._service(v,sid);snapshot=[];names=set()
            # The fixed read-only list filters snapshots before they can become grantable.
            items=[t for t in tools if isinstance(t,dict) and t.get('name') in feishu_readonly.WIRE_TOOL_NAMES] if feishu_readonly.is_feishu(service) else tools
            for tool in items:
                if not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or tool['name'] in names or not isinstance(tool.get('inputSchema'),dict):raise ConfigError('Invalid discovery result')
                names.add(tool['name']);item={k:copy.deepcopy(tool[k]) for k in ('name','description','inputSchema','annotations') if k in tool}
                item['schema_hash']=schema_hash(tool['inputSchema']);snapshot.append(item)
            if len(snapshot)>200 or len(json.dumps(snapshot).encode())>2*1024*1024:raise ConfigError('Discovery limit exceeded')
            service['discovery']=snapshot
        return self._change(version,change)

    def _service(self,v,sid):
        if sid not in v['services']:raise ConfigError('Service not found',404)
        return v['services'][sid]

    def _update_feishu(self,v,s,patch):
        if set(patch)-{'name','app_id','app_secret'}:raise ConfigError()
        update=copy.deepcopy(patch)
        if update.get('app_secret')=='':update.pop('app_secret')
        app_id=update.get('app_id',s['app_id']);app_secret=update.get('app_secret',s['app_secret'])
        self._feishu_credentials(app_id,app_secret)
        rotated=app_id!=s['app_id'] or app_secret!=s['app_secret']
        s.update(update)
        if rotated:
            # A new application must never inherit grants just because schemas match.
            s['discovery']=[]
            for device in v['devices']:
                for grant in device['bindings'].get(s['id'],[]):grant['schema_hash']='review-required'

    def _update_local(self,v,s,patch):
        if set(patch)-{'name','command','args','env'}:raise ConfigError()
        update=copy.deepcopy(patch)
        if 'env' in update:
            env=update['env']
            if not isinstance(env,dict):raise ConfigError()
            # The form sends every kept name; an empty value keeps the stored secret, a missing name removes it.
            update['env']={k:(s['env'][k] if val=='' and k in s['env'] else val) for k,val in env.items()}
        entry={k:update.get(k,s[k]) for k in ('name','command','args','env')}|{'transport':local_stdio.TRANSPORT}
        self._local_entry(entry)
        changed=any(entry[k]!=s[k] for k in ('command','args','env'))
        s.update({k:entry[k] for k in ('name','command','args','env')})
        if changed:
            # A different program or credential must never inherit grants just because schemas match.
            s['discovery']=[]
            for device in v['devices']:
                for grant in device['bindings'].get(s['id'],[]):grant['schema_hash']='review-required'

    def update_service(self,sid,patch,version):
        if not isinstance(patch,dict) or set(patch)-{'name','url','transport','auth','private_network','app_id','app_secret','command','args','env'}:raise ConfigError()
        affected=[]
        def change(v):
            s=self._service(v,sid)
            if feishu_readonly.is_feishu(s):
                self._update_feishu(v,s,patch)
                affected.extend(d['id'] for d in v['devices'] if sid in d['bindings']);return
            if local_stdio.is_local(s):
                self._update_local(v,s,patch)
                affected.extend(d['id'] for d in v['devices'] if sid in d['bindings']);return
            if set(patch)-{'name','url','transport','auth','private_network'}:raise ConfigError()
            before=copy.deepcopy(s)
            update=copy.deepcopy(patch)
            if update.get('url')=='':update.pop('url')
            if 'auth' in update:
                a=update['auth']
                if not isinstance(a,dict) or set(a)-{'mode','header','value','clear'}:raise ConfigError()
                if a.get('clear') and a.get('value'):raise ConfigError()
                mode=a.get('mode',s['auth']['mode']);value='' if a.get('clear') or mode=='none' else a.get('value') or s['auth'].get('value','')
                update['auth']={'mode':mode,'header':a.get('header',s['auth'].get('header','X-Api-Key')),'value':value}
            if update.get('url',s['url'])!=s['url'] and update.get('private_network',s['private_network']) and patch.get('private_network') is not True:
                raise ConfigError('Confirm private network access for the new target')
            s.update(update)
            if any(before[k]!=s[k] for k in ('url','transport','auth','private_network')):
                s['discovery']=[]
                for device in v['devices']:
                    for grant in device['bindings'].get(sid,[]):
                        if grant['schema_hash']=='legacy-review':grant['schema_hash']='review-required'
            affected.extend(d['id'] for d in v['devices'] if sid in d['bindings'])
        saved=self._change(version,change)
        return {'saved_version':saved,'affected_device_ids':affected}

    def delete_service(self,sid,version):
        def change(v):
            self._service(v,sid)
            if any(sid in d['bindings'] for d in v['devices']):raise ConfigError('Unbind service from devices first',409)
            del v['services'][sid]
        return self._change(version,change)

    def _device_input(self,value,entry,eid=None):
        clean=copy.deepcopy(entry)
        if 'name' in clean:
            if not isinstance(clean['name'],str) or not 1<=len(clean['name'].strip())<=80:raise ConfigError('Invalid device name',422,'invalid_device_name')
            clean['name']=clean['name'].strip()
        if 'endpoint' in clean:
            if not isinstance(clean['endpoint'],str):raise ConfigError('Invalid Xiaozhi WSS endpoint',422,'invalid_wss_endpoint')
            clean['endpoint']=clean['endpoint'].strip()
            if clean['endpoint'] or eid is None:
                try:
                    from urllib.parse import urlsplit,parse_qs
                    endpoint=clean['endpoint'];u=urlsplit(endpoint);q=parse_qs(u.query,keep_blank_values=True)
                    if len(endpoint)>8192 or any(c.isspace() for c in endpoint) or u.scheme!='wss' or u.hostname!='api.xiaozhi.me' or u.path!='/mcp/' or u.port not in (None,443) or u.username or u.password or u.fragment or set(q)!={'token'} or len(q['token'])!=1 or not q['token'][0]:raise ValueError()
                except Exception:raise ConfigError('Invalid Xiaozhi WSS endpoint',422,'invalid_wss_endpoint') from None
                if any(d['id']!=eid and d['endpoint']==endpoint for d in value['devices']):raise ConfigError('WSS endpoint already assigned to another device',409,'duplicate_endpoint')
        return clean

    def add_device(self,entry,version):
        if not isinstance(entry,dict) or set(entry)!={'name','endpoint'}:raise ConfigError()
        eid=str(uuid.uuid4())
        def change(v):
            clean=self._device_input(v,entry)
            if len(v['devices'])>=10:raise ConfigError('Device limit reached',422,'device_limit')
            v['devices'].append(dict(clean,id=eid,platform='xiaozhi',enabled=False,bindings={}))
        saved=self._change(version,change)
        return {'id':eid,'saved_version':saved}

    def update_device(self,eid,patch,version):
        if not isinstance(patch,dict) or set(patch)-{'name','endpoint','enabled','bindings'}:raise ConfigError()
        def change(v):
            device=next((d for d in v['devices'] if d['id']==eid),None)
            if device is None:raise ConfigError('Device not found',404)
            update=self._device_input(v,copy.deepcopy(patch),eid)
            if update.get('endpoint')=='':update.pop('endpoint')
            if 'bindings' in update:
                bindings=update.pop('bindings');grants=copy.deepcopy(device['bindings'])
                if not isinstance(bindings,dict):raise ConfigError()
                for sid,names in bindings.items():
                    service=self._service(v,sid);known={t['name']:t for t in service['discovery'] if feishu_readonly.tool_allowed(service,t['name'])}
                    if not isinstance(names,list) or any(not isinstance(n,str) for n in names) or len(names)!=len(set(names)) or not set(names)<=set(known):raise ConfigError('Discover tools before granting access')
                    if not names:
                        grants.pop(sid,None)
                        continue
                    old={g['name']:g for g in device['bindings'].get(sid,[])}
                    grants[sid]=[]
                    for name in names:
                        alias=old[name]['alias'] if name in old else tool_alias(sid,name)
                        grants[sid].append({'name':name,'schema_hash':known[name]['schema_hash'],'alias':alias})
                update['bindings']=grants
            device.update(update)
        return self._change(version,change)

    def delete_device(self,eid,version):
        def change(v):
            if not any(d['id']==eid for d in v['devices']):raise ConfigError('Device not found',404)
            v['devices']=[d for d in v['devices'] if d['id']!=eid]
        return self._change(version,change)

    def _validate(self,v):
        try:
            if v['schema_version']!=3 or type(v['version']) is not int or not 1<=v['version']:raise ValueError()
            if not isinstance(v['services'],dict) or len(v['services'])>20 or not 0<=len(v['devices'])<=10:raise ValueError()
            ids=set()
            for sid,s in v['services'].items():
                if feishu_readonly.is_feishu(s):
                    if sid!=s['id']:raise ValueError()
                    feishu_readonly.validate_feishu_service(s);continue
                if local_stdio.is_local(s):
                    if sid!=s['id']:raise ValueError()
                    local_stdio.validate_service(s);continue
                if 'provider' in s or sid!=s['id'] or not isinstance(s['name'],str) or not 1<=len(s['name'])<=80 or s['transport'] not in ('streamable-http','sse') or type(s['private_network']) is not bool:raise ValueError()
                validate_target(s['url'],s['private_network']);auth_headers(s['auth'])
                if not isinstance(s['discovery'],list) or len(s['discovery'])>200:raise ValueError()
            urls=set()
            for d in v['devices']:
                if d['id'] in ids or d['endpoint'] in urls:raise ValueError()
                ids.add(d['id']);urls.add(d['endpoint'])
                from urllib.parse import urlsplit,parse_qs
                u=urlsplit(d['endpoint']);q=parse_qs(u.query,keep_blank_values=True)
                if u.scheme!='wss' or u.hostname!='api.xiaozhi.me' or u.path!='/mcp/' or u.port not in (None,443) or u.username or u.password or u.fragment or set(q)!={'token'} or len(q['token'])!=1 or not q['token'][0]:raise ValueError()
                if not isinstance(d['name'],str) or not 1<=len(d['name'])<=80 or type(d['enabled']) is not bool or d['platform']!='xiaozhi':raise ValueError()
                if not isinstance(d['bindings'],dict) or not set(d['bindings'])<=set(v['services']):raise ValueError()
                aliases=set()
                for sid,grants in d['bindings'].items():
                    if not isinstance(grants,list) or len(grants)>200:raise ValueError()
                    for g in grants:
                        # Saved grants may become stale after re-discovery; publication checks hash intersection.
                        if set(g)!={'name','schema_hash','alias'} or not all(isinstance(g[k],str) for k in g) or not 1<=len(g['alias'])<=64 or g['alias'] in aliases:raise ValueError()
                        aliases.add(g['alias'])
        except Exception:raise ConfigError('Invalid catalog configuration') from None
