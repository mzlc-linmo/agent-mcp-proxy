"""Runs inside the test image with network; prints one JSON report for test_stdio_image.

Installs two pinned public MCP servers through the production local-program path (npx and uvx) into the
cache volume, lists their tools, calls one harmless tool each, reinstalls one and tries a missing command.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
sys.path.insert(0,'/app')
import local_stdio
import stdio_runtime
from service_transport import discover_service,service_session

TOKEN='synthetic-image-token-1234'
EVERYTHING='@modelcontextprotocol/server-everything@2.0.1'
TIME='mcp-server-time==2026.10.19'


def service(sid,command,args,env=None):
    return {'id':sid,'name':sid,'transport':'stdio','command':command,'args':args,'env':env or {},'discovery':[],'legacy_profile':None}


def children():
    me=os.getpid();found=[]
    for pid in filter(str.isdigit,os.listdir('/proc')):
        if int(pid)==me:continue
        try:found.append(open('/proc/%s/cmdline'%pid,'rb').read().replace(b'\0',b' ').decode().strip())
        except OSError:continue
    return [c for c in found if c]


def size(path):
    total=0
    for root,_,files in os.walk(path):
        for name in files:
            try:total+=os.lstat(os.path.join(root,name)).st_size
            except OSError:pass
    return total


async def discover(s,reinstall=False):
    start=time.monotonic()
    try:tools=await discover_service(s,reinstall)
    except Exception as error:return {'error':stdio_runtime.error_code(error) or type(error).__name__,'state':stdio_runtime.state(s['id'])}
    return {'tools':sorted(t['name'] for t in tools),'seconds':round(time.monotonic()-start,1),'state':stdio_runtime.state(s['id'])['state']}


async def call(s,name,arguments):
    async with service_session(s) as session:
        result=await session.call_tool(name,arguments)
    text=''.join(getattr(c,'text','') for c in result.content)
    return {'is_error':result.isError,'types':sorted({c.type for c in result.content}),'bytes':len(text.encode()),'text':text[:200]}


async def main():
    report={'uid':os.getuid(),'npx':shutil.which('npx',path=local_stdio.PATH),'uvx':shutil.which('uvx',path=local_stdio.PATH),
        'node':subprocess.run(['/opt/feishu-node/bin/node','--version'],capture_output=True,text=True).stdout.strip(),
        'uv':subprocess.run(['uvx','--version'],capture_output=True,text=True).stdout.strip(),'limit':stdio_runtime.LIMIT,
        'cache_root':str(local_stdio.CACHE_ROOT),'app_writable':os.access('/app',os.W_OK)}
    everything=service('everything','npx',['-y',EVERYTHING])
    clock=service('time','uvx',[TIME,'--local-timezone','UTC'],{'SYNTH_TOKEN':TOKEN})
    report['everything']=await discover(everything)
    report['everything_cached']=await discover(everything)
    report['everything_call']=await call(everything,'echo',{'message':'hello'})
    report['everything_npm_bytes']=size(local_stdio.paths('everything')['npm'])
    report['time']=await discover(clock)
    report['time_call']=await call(clock,'get_current_time',{'timezone':'UTC'})
    report['time_uv_bytes']=size(local_stdio.paths('time')['uv'])
    marker=local_stdio.paths('everything')['home']/'kept';marker.write_text('x')
    report['reinstall']=await discover(everything,reinstall=True)
    report['reinstall_home_kept']=marker.exists()
    report['missing']=await discover(service('missing','no-such-mcp-command',[]))
    await asyncio.sleep(0.5)
    report['after']=[c for c in children() if 'stdio_image_probe' not in c];report['active_after']=stdio_runtime.active()
    print(json.dumps(report,ensure_ascii=False))


asyncio.run(main())
