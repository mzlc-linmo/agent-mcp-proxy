"""Private configuration; the public projection never contains credential material."""
import copy
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from policy import ALLOWED

class ConfigError(ValueError):
    def __init__(self, message='Invalid configuration', status=422, code=None):
        super().__init__(message)
        self.status=status
        self.code=code

class ConfigStore:
    def __init__(self, path: Path, allowed_origin: str):
        self.path=Path(path)
        self.origin=allowed_origin.rstrip('/')
        self.lock=threading.RLock()
        self.save_warning=None

    def initialize(self, initial: dict):
        with self.lock:
            if self.path.exists():
                self.load()
                return
            value=copy.deepcopy(initial);value['version']=1
            if 'schema_version' not in value:
                value['schema_version']=2
                value['endpoints']=[{'id':'default','name':'Default Xiaozhi','platform':'xiaozhi','endpoint':value['endpoint'],'enabled':True,'enabled_tools':value['enabled_tools']}]
            self._validate(value);self._write(value)

    def import_legacy(self, source: Path):
        if self.path.exists():return
        try:
            old=json.loads(Path(source).read_text())
            initial={'endpoint':Path(old['endpoint_file']).read_text().strip(),'enabled_tools':sorted(set().union(*ALLOWED.values())),'upstreams':{}}
            for domain in ALLOWED:
                entry=old['upstreams'][domain]
                initial['upstreams'][domain]={'url':entry['url'],'key':Path(entry['key_file']).read_text().strip() if entry.get('key_file') else ''}
            self.initialize(initial)
        except Exception:
            raise ConfigError('Configuration initialization failed') from None

    def load(self):
        with self.lock:
            try:
                value=json.loads(self.path.read_text());self._validate(value)
                if value.get('schema_version')!=2:
                    value['schema_version']=2
                    value['endpoints']=[{'id':'default','name':'Default Xiaozhi','platform':'xiaozhi','endpoint':value['endpoint'],'enabled':True,'enabled_tools':value['enabled_tools']}]
                    self._write(value)
                return value
            except Exception:raise ConfigError('Private configuration unavailable',503) from None

    def public(self):
        value=self.load()
        return {'save_warning':self.save_warning,'version':value['version'],'endpoint_configured':bool(value['endpoint']),'upstreams':{d:{'url':v['url'],'key_configured':bool(v['key'])} for d,v in value['upstreams'].items()},'enabled_tools':value['enabled_tools'],'available_tools':self.available_tools(value),'endpoints':[{k:v for k,v in e.items() if k!='endpoint'} | {'endpoint_configured':bool(e['endpoint'])} for e in value['endpoints']]}

    def save(self, patch: dict, expected_version: int):
        with self.lock:
            if not isinstance(patch,dict) or set(patch)-{'endpoint','clear_endpoint','upstreams','enabled_tools'}:raise ConfigError()
            value=self.load()
            if type(expected_version) is not int or expected_version!=value['version']:raise ConfigError('Configuration version conflict',409)
            if patch.get('clear_endpoint'):raise ConfigError('Required endpoint cannot be cleared')
            if 'endpoint' in patch:
                if not isinstance(patch['endpoint'],str):raise ConfigError()
                if patch['endpoint']:value['endpoint']=patch['endpoint']
            if 'enabled_tools' in patch:value['enabled_tools']=patch['enabled_tools']
            if 'upstreams' in patch:
                if not isinstance(patch['upstreams'],dict) or set(patch['upstreams'])-set(value['upstreams']):raise ConfigError()
                for domain,entry in patch['upstreams'].items():
                    if not isinstance(entry,dict) or set(entry)-{'url','key','clear_key'}:raise ConfigError()
                    if 'clear_key' in entry and type(entry['clear_key']) is not bool:raise ConfigError()
                    if 'key' in entry and (not isinstance(entry['key'],str) or len(entry['key'])>8192):raise ConfigError()
                    if entry.get('clear_key') and entry.get('key'):raise ConfigError()
                    if entry.get('clear_key'):value['upstreams'][domain]['key']=''
                    elif entry.get('key'):value['upstreams'][domain]['key']=entry['key']
                    if 'url' in entry:value['upstreams'][domain]['url']=entry['url']
            for e in value.get('endpoints',[]):
                if e['id']=='default':e.update(endpoint=value['endpoint'],enabled_tools=value['enabled_tools'])
            value['version']+=1;self._validate(value);self._write(value)
            return value['version']

    def _endpoint_change(self,expected_version,change):
        with self.lock:
            value=self.load()
            if type(expected_version) is not int or expected_version!=value['version']:raise ConfigError('Configuration version conflict',409)
            change(value)
            default=next(e for e in value['endpoints'] if e['id']=='default')
            value['endpoint']=default['endpoint'];value['enabled_tools']=default['enabled_tools']
            value['version']+=1;self._validate(value);self._write(value)
            return value['version']

    def add_endpoint(self,entry,version):
        if not isinstance(entry,dict) or set(entry)!={'name','platform','endpoint'}:raise ConfigError()
        eid=str(uuid.uuid4())
        saved=self._endpoint_change(version,lambda v:v['endpoints'].append(dict(entry,id=eid,enabled=False,enabled_tools=[])))
        return {'id':eid,'saved_version':saved}

    def update_endpoint(self,eid,patch,version):
        if not isinstance(patch,dict) or set(patch)-{'name','platform','endpoint','enabled','enabled_tools'}:raise ConfigError()
        def change(value):
            e=next((e for e in value['endpoints'] if e['id']==eid),None)
            if e is None:raise ConfigError('Endpoint not found',404)
            update=dict(patch)
            if update.get('endpoint')=='':update.pop('endpoint')
            e.update(update)
        return self._endpoint_change(version,change)

    def delete_endpoint(self,eid,version):
        if eid=='default':raise ConfigError('Default endpoint may be disabled but not deleted')
        def change(value):
            if not any(e['id']==eid for e in value['endpoints']):raise ConfigError('Endpoint not found',404)
            value['endpoints']=[e for e in value['endpoints'] if e['id']!=eid]
        return self._endpoint_change(version,change)

    def add_upstream(self,entry,version):
        import re
        if not isinstance(entry,dict) or set(entry)!={'id','url','key','tools'}:raise ConfigError()
        domain=entry['id']
        if not isinstance(domain,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}',domain):raise ConfigError()
        def change(value):
            if domain in value['upstreams'] or len(value['upstreams'])>=20:raise ConfigError('Duplicate or excessive upstream')
            value['upstreams'][domain]={k:entry[k] for k in ('url','key','tools')}
        return self._endpoint_change(version,change)

    def available_tools(self,value):
        return {d:sorted(ALLOWED[d]) if d in ALLOWED else e.get('tools',[]) for d,e in value['upstreams'].items()}

    def _validate(self,value):
        try:
            endpoint=value['endpoint'];p=urlsplit(endpoint)
            if not isinstance(endpoint,str) or len(endpoint)>8192 or p.scheme!='wss' or p.hostname!='api.xiaozhi.me' or p.port not in (None,443) or p.username or p.password or p.path!='/mcp/' or p.fragment:raise ValueError()
            query=parse_qs(p.query,keep_blank_values=True)
            if set(query)!={'token'} or len(query['token'])!=1 or not query['token'][0]:raise ValueError()
            tools=value['enabled_tools'];allowed={t for tools in self.available_tools(value).values() for t in tools}
            if not isinstance(tools,list) or any(not isinstance(t,str) for t in tools) or len(tools)!=len(set(tools)) or not set(tools)<=allowed:raise ValueError()
            if not set(ALLOWED)<=set(value['upstreams']) or len(value['upstreams'])>20:raise ValueError()
            for domain,entry in value['upstreams'].items():
                import re
                if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}',domain):raise ValueError()
                if entry['url']!=self.origin+'/mcp/'+domain:raise ValueError()
                if domain not in ALLOWED:
                    names=entry.get('tools')
                    if not isinstance(names,list) or len(names)>50 or len(names)!=len(set(names)) or any(not isinstance(t,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',t) for t in names):raise ValueError()
                    other={t for d,e in value['upstreams'].items() if d!=domain for t in (ALLOWED[d] if d in ALLOWED else e.get('tools',[]))}
                    if set(names)&other:raise ValueError()
                key=entry['key']
                if not isinstance(key,str) or len(key)>8192 or any(c in key for c in '\r\n'):raise ValueError()
            if not value['upstreams']['ops-readonly']['key']:raise ValueError()
            if type(value['version']) is not int or value['version']<1:raise ValueError()
            if value.get('schema_version')==2:
                endpoints=value['endpoints']
                if not isinstance(endpoints,list) or not 1<=len(endpoints)<=10:raise ValueError()
                if len({e['id'] for e in endpoints})!=len(endpoints) or len({e['endpoint'] for e in endpoints})!=len(endpoints):raise ValueError()
                if not any(e['id']=='default' for e in endpoints):raise ValueError()
                for e in endpoints:
                    if set(e)!={'id','name','platform','endpoint','enabled','enabled_tools'}:raise ValueError()
                    if not isinstance(e['name'],str) or not 1<=len(e['name'])<=80 or e['platform']!='xiaozhi' or type(e['enabled']) is not bool:raise ValueError()
                    u=urlsplit(e['endpoint']);q=parse_qs(u.query,keep_blank_values=True)
                    if u.scheme!='wss' or u.hostname!='api.xiaozhi.me' or u.port not in (None,443) or u.username or u.password or u.fragment or u.path!='/mcp/' or set(q)!={'token'} or len(q['token'])!=1 or not q['token'][0] or len(e['endpoint'])>8192:raise ValueError()
                    t=e['enabled_tools']
                    if not isinstance(t,list) or any(not isinstance(n,str) for n in t) or len(t)!=len(set(t)) or not set(t)<=allowed:raise ValueError()
        except Exception:raise ConfigError() from None

    def _write(self,value):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.path.parent.chmod(0o700)
        name=None
        try:
            fd,name=tempfile.mkstemp(prefix='.config-',dir=self.path.parent)
            with os.fdopen(fd,'w') as stream:
                os.fchmod(stream.fileno(),0o600);json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path);name=None
            # Atomic replace is the commit point. Post-commit durability failure isn't a rollback.
            self.save_warning=None
            try:
                fd=os.open(self.path.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            except OSError:self.save_warning='durability_unconfirmed'
        except Exception:raise ConfigError('Configuration could not be saved',503) from None
        finally:
            if name is not None:
                target=Path(name)
                if target.parent.resolve()==self.path.parent.resolve() and target.name.startswith('.config-'):target.unlink(missing_ok=True)
