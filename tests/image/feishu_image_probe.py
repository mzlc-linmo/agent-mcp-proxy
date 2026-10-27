"""Runs inside the test image from a read-only mount; prints one JSON report for test_feishu_image.

Uses the production modules in /app with synthetic credentials only. Phase "official" is the unmodified
launch; phase "stubbed" adds only a test preload that answers HTTP from a fixed table.
"""
import asyncio
import json
import os
import sys
sys.path.insert(0,'/app')
import feishu_readonly
import feishu_stdio
from catalog_runtime import call_feishu

SECRET='synthetic-image-secret-0000'
SERVICE={'id':'fs-image','name':'飞书只读','provider':'feishu-readonly','transport':'stdio','app_id':'cli_synthetic_image','app_secret':SECRET,'discovery':[],'legacy_profile':None}
STUB='/tests/feishu_http_stub.js'


def listening():
    """TCP sockets in LISTEN (0A) and bound UDP sockets (07) in this network namespace."""
    found=[]
    for name,state in (('tcp','0A'),('tcp6','0A'),('udp','07'),('udp6','07')):
        try:rows=open('/proc/net/'+name).read().splitlines()[1:]
        except FileNotFoundError:continue
        found+=[name+' '+r.split()[1] for r in rows if r.split()[3]==state]
    return found


def node_processes():
    found=[]
    for pid in filter(str.isdigit,os.listdir('/proc')):
        try:
            argv=open('/proc/%s/cmdline'%pid,'rb').read().split(b'\0')
            if argv[0]!=feishu_readonly.NODE.encode():continue
            env=dict(e.split(b'=',1) for e in open('/proc/%s/environ'%pid,'rb').read().split(b'\0') if b'=' in e)
            found.append({'pid':int(pid),'argv':[a.decode() for a in argv[1:] if a],'env_keys':sorted(k.decode() for k in env),
                'home':env.get(b'HOME',b'').decode(),'cwd':os.readlink('/proc/%s/cwd'%pid),'secret_in_argv':SECRET.encode() in b' '.join(argv),'uid':os.stat('/proc/'+pid).st_uid})
        except OSError:continue
    return found


async def list_all(session):
    tools=[];cursor=None
    while True:
        page=await session.list_tools(cursor=cursor);tools+=page.tools;cursor=page.nextCursor
        if not cursor:return tools


async def official():
    report={}
    async with feishu_stdio.session(SERVICE) as session:
        tools=await list_all(session)
        report['tools']=sorted(t.name for t in tools)
        report['processes']=node_processes();report['listening']=listening()
        report['home_listing']=sorted(os.listdir(report['processes'][0]['home'])) if report['processes'] else None
    report['after']=node_processes();report['active_after']=feishu_stdio.active()
    report['home_removed']=not any(os.path.exists(p['home']) for p in report['processes'])
    return report


async def stubbed():
    original=feishu_readonly.stdio_parameters
    def parameters(service,home):
        real=original(service,home)
        return real.model_copy(update={'args':['--require',STUB]+real.args})
    feishu_readonly.stdio_parameters=parameters
    report={}
    try:
        async with feishu_stdio.session(SERVICE) as session:
            [process]=node_processes();home=process['home']
            calls={'chat_list_useUAT':('im_v1_chat_list',{'params':{'page_size':5},'useUAT':True}),
                'doc_http_400':('docx_v1_document_rawContent',{'path':{'document_id':'doc_forbidden'}}),
                'doc_http_200_code':('docx_v1_document_rawContent',{'path':{'document_id':'doc_business'}}),
                'write_tool':('im_v1_message_create',{'data':{'content':'x'}})}
            for label,(name,arguments) in calls.items():
                try:
                    raw=await session.call_tool(name,arguments)
                    entry={'official_is_error':bool(raw.isError),'official_text':''.join(getattr(c,'text','') for c in raw.content)}
                except Exception as error:entry={'official_raised':type(error).__name__}
                guarded=await call_feishu(session,SERVICE,name,arguments)
                report[label]=entry|{'proxy_is_error':bool(guarded.isError),'proxy_text':''.join(getattr(c,'text','') for c in guarded.content)}
            log=os.path.join(home,'stub-requests.jsonl')
            report['requests']=[json.loads(line) for line in open(log)] if os.path.exists(log) else []
    finally:feishu_readonly.stdio_parameters=original
    report['after']=node_processes();report['active_after']=feishu_stdio.active()
    return report


async def main():
    result={'uid':os.getuid(),'root_writable':os.access('/',os.W_OK),'app_writable':os.access('/app',os.W_OK)}
    try:result['official']=await official()
    except Exception as error:result['official_error']=type(error).__name__+': '+str(error)
    if 'official' in result:
        try:result['stubbed']=await stubbed()
        except Exception as error:result['stubbed_error']=type(error).__name__+': '+str(error)
    print(json.dumps(result,ensure_ascii=False))


asyncio.run(main())
