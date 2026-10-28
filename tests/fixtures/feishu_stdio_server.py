"""Synthetic Feishu-like MCP stdio server for tests: standard library only, never touches the network.

Behaviour is read from mode.json in the working directory; every start writes pid-<pid>, every call
appends to calls.jsonl, and the received argv/env keys are written to launch-<pid>.json.
"""
import json
import os
import signal
import sys
import time

cwd=os.getcwd()
mode=json.load(open(os.path.join(cwd,'mode.json'))) if os.path.exists(os.path.join(cwd,'mode.json')) else {}
pid=os.getpid()
open(os.path.join(cwd,'pid-%d'%pid),'w').close()
json.dump({'argv':sys.argv[1:],'env':sorted(os.environ),'secret_in_argv':os.environ.get('APP_SECRET','') in ' '.join(sys.argv)},open(os.path.join(cwd,'launch-%d.json'%pid),'w'))
if mode.get('stubborn'):signal.signal(signal.SIGTERM,signal.SIG_IGN)
if mode.get('crash'):
    sys.stderr.write('fatal: credential %s rejected\n'%os.environ.get('APP_SECRET'));sys.stderr.flush();sys.exit(3)

argv=sys.argv[1:]
token_mode=argv[argv.index('--token-mode')+1] if '--token-mode' in argv else None
names=[n.replace('.','_') for n in argv[argv.index('--tools')+1].split(',')] if '--tools' in argv else []
schema={'type':'object','properties':{'page_size':{'type':'integer'},'mode':{'type':'string'},'useUAT':{'type':'boolean'}}}
if mode.get('schema_variant'):schema=dict(schema,required=['page_size'])
tools=[{'name':n,'description':'只读 '+n,'inputSchema':schema,'annotations':{'readOnlyHint':True}} for n in names]
# A write tool marked read-only and an unknown tool must be filtered by the proxy, not trusted.
tools[1:1]=[{'name':'im_v1_message_create','description':'send','inputSchema':schema,'annotations':{'readOnlyHint':True}},{'name':'unknown_tool','inputSchema':schema}]


def send(message):
    sys.stdout.write(json.dumps(message,ensure_ascii=False)+'\n');sys.stdout.flush()


def call(name,arguments):
    with open(os.path.join(cwd,'calls.jsonl'),'a') as f:f.write(json.dumps({'pid':pid,'name':name,'arguments':arguments})+'\n')
    kind=arguments.get('mode','ok')
    if kind=='hang':time.sleep(3600)
    if kind=='denied':return {'isError':True,'content':[{'type':'text','text':'permission denied for app %s'%os.environ.get('APP_SECRET')}]}
    # HTTP 200 with a non-zero Feishu code: the official handler forwards the body without isError.
    if kind=='business':return {'content':[{'type':'text','text':json.dumps({'code':99991672,'msg':'Access denied for %s'%os.environ.get('APP_SECRET')})}]}
    if kind=='image':return {'content':[{'type':'image','data':'AAAA','mimeType':'image/png'}]}
    if kind=='bytes':return {'content':[{'type':'text','text':'a'*int(arguments.get('page_size',0))}]}
    if kind=='chinese':return {'content':[{'type':'text','text':'中'*int(arguments.get('page_size',0))}]}
    return {'content':[{'type':'text','text':json.dumps({'items':[{'chat_id':'oc_1','name':'测试群'}],'token_mode':token_mode,'useUAT':arguments.get('useUAT')},ensure_ascii=False)}]}


for line in sys.stdin:
    message=json.loads(line)
    method=message.get('method');mid=message.get('id')
    if mid is None:continue
    if method=='initialize':
        send({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':message['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'synthetic-feishu','version':'0'}}})
    elif method=='tools/list':
        if mode.get('hang_list'):
            open(os.path.join(cwd,'listing-%d'%pid),'w').close();time.sleep(3600)
        cursor=(message.get('params') or {}).get('cursor')
        page=tools[:3] if cursor is None else tools[3:]
        send({'jsonrpc':'2.0','id':mid,'result':{'tools':page}|({'nextCursor':'p2'} if cursor is None else {})})
    elif method=='tools/call':
        params=message['params'];send({'jsonrpc':'2.0','id':mid,'result':call(params['name'],params.get('arguments') or {})})
    elif method=='ping':send({'jsonrpc':'2.0','id':mid,'result':{}})
    else:send({'jsonrpc':'2.0','id':mid,'error':{'code':-32601,'message':'not found'}})
if mode.get('stubborn'):
    while True:time.sleep(1)
