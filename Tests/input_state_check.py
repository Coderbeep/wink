import json,os,pathlib,socket,struct,subprocess,sys,tempfile,threading,time
EXE=pathlib.Path(sys.argv[1]).resolve();ROOT='10000000-0000-4000-8000-000000000001'
trace=[];errors=[];outputs=[];stop=threading.Event()
def frame(o):
 b=json.dumps(o).encode();return struct.pack('<I',len(b))+b
def reply(item='ask-1'):
 q=json.dumps(['request_user_input_async',item,0],separators=(',',':'))
 return '<send_user_message_question_reply>\n'+json.dumps([{'questionItemId':q,'question':'Fixture question','answer':'Fixture answer'}])+'\n</send_user_message_question_reply>'
with tempfile.TemporaryDirectory(prefix='dot-input-',dir='/private/tmp') as tmp:
 home=pathlib.Path(tmp)
 for d in ['ipc','sessions','thread-writer-locks']:(home/d).mkdir()
 (home/'thread-writer-locks'/(ROOT+'.lock')).touch()
 server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(home/'ipc'/'ipc.sock'));server.listen();server.settimeout(10)
 state={'id':ROOT,'title':'Input fixture','originator':'codex_work_desktop','source':'vscode','threadSource':'user','resumeState':'resumed','hasUnreadTurn':False,'threadRuntimeStatus':{'type':'active','activeFlags':[]},'requests':[],
        'turnHistory':{'kind':'canonical','history':{'islands':[{'newerBoundary':{'status':'exhausted'},'entries':[{'value':'entity'}]}],'entitiesByKey':{'entity':{'turnId':'turn-1','status':'inProgress','turnStartedAtMs':1,'items':[{'id':'ask-1','type':'agentMessage','questions':[{'title':'Fixture question','options':[]}]}]}}}}}
 turn=state['turnHistory']['history']['entitiesByKey']['entity']
 def serve():
  try:
   conn,_=server.accept();conn.settimeout(.05);buf=b'';revision=0;started=None;stage=0
   def snapshot():
    nonlocal revision
    revision+=1;trace.append(('snapshot',stage))
    conn.sendall(frame({'type':'broadcast','sourceClientId':'owner','method':'thread-stream-state-changed','version':11,'params':{'hostId':'local','conversationId':ROOT,'change':{'type':'snapshot','revision':revision,'conversationState':state}}}))
   def patch(path,value,op='replace'):
    nonlocal revision
    base=revision;revision+=1
    conn.sendall(frame({'type':'broadcast','sourceClientId':'owner','method':'thread-stream-state-changed','version':11,'params':{'hostId':'local','conversationId':ROOT,'change':{'type':'patches','baseRevision':base,'revision':revision,'patches':[{'op':op,'path':path,'value':value}]}}}))
   while not stop.is_set():
    try:
     chunk=conn.recv(65536)
     if not chunk:break
     buf+=chunk
    except socket.timeout:pass
    while len(buf)>=4:
     n=struct.unpack('<I',buf[:4])[0]
     if len(buf)<4+n:break
     o=json.loads(buf[4:4+n]);buf=buf[4+n:]
     if o.get('type')=='request':
      assert o['method'] in ['initialize','thread-owner-discovery']
      conn.sendall(frame({'type':'response','requestId':o['requestId'],'resultType':'success','handledByClientId':'owner','result':{'clientId':'observer'}}))
     elif o.get('method')=='thread-stream-following-changed':
      trace.append(('follow',o['params']['following'],stage))
      if o['params']['following']:
       snapshot()
       if started is None:started=time.monotonic()
    if started is None:continue
    elapsed=time.monotonic()-started
    if stage==0 and elapsed>1:
     stage=1
     item={'id':'answer-1','type':'steeringUserMessage','status':'pending','input':[{'type':'text','text':reply()}]}
     turn['items'].append(item);patch(['turnHistory','history','entitiesByKey','entity','items',1],item,'add')
    elif stage==1 and elapsed>2:
     stage=2;turn['items'][1]['status']='accepted';patch(['turnHistory','history','entitiesByKey','entity','items',1,'status'],'accepted')
    elif stage==2 and elapsed>3:
     stage=3
     item={'id':'ask-2','type':'agentMessage','questions':[{'title':'Another fixture question','options':[]}]}
     turn['items'].append(item);patch(['turnHistory','history','entitiesByKey','entity','items',2],item,'add')
    elif stage==3 and elapsed>4:
     stage=4
     item={'id':'answer-2','type':'userMessage','content':[{'type':'text','text':reply('ask-2')}]}
     turn['items'].append(item);patch(['turnHistory','history','entitiesByKey','entity','items',3],item,'add')
    elif stage==4 and elapsed>5:
     stage=5
     state['requests']=[{'id':'native-input','method':'item/tool/requestUserInput','params':{'turnId':'turn-1','questions':[]}}]
     patch(['requests'],state['requests'])
    elif stage==5 and elapsed>6:
     stage=6;state['requests']=[];patch(['requests'],[])
   conn.close()
  except Exception as e:
   if not stop.is_set():errors.append(repr(e))
 thread=threading.Thread(target=serve,daemon=True);thread.start()
 proc=subprocess.Popen([str(EXE),'--probe','8'],env={**os.environ,'CODEX_HOME':tmp},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 started=time.monotonic()
 for line in proc.stdout:
  o=json.loads(line);outputs.append((time.monotonic()-started,o))
 proc.wait(timeout=3);stderr=proc.stderr.read();stop.set();thread.join(timeout=1);server.close()
 assert proc.returncode==0,stderr
 assert not errors,errors
 def phases(start,end):
  return [s['phase'] for t,o in outputs if start<t<end for s in o['sessions'] if s['id']==ROOT]
 assert 'waiting' in phases(0,1), 'Unanswered async question missed'
 assert 'working' not in phases(1,2), 'Unaccepted steering reply cleared input early'
 assert 'working' in phases(2,3), 'Accepted steering reply failed to clear input'
 assert 'waiting' in phases(3,4), 'New question was not detected from patch'
 assert 'working' in phases(4,5), 'Accepted user reply failed to clear input'
 assert 'waiting' in phases(5,6), 'Native input request missed'
 assert 'working' in phases(6,8), 'Removed native request failed to clear'
 print('Input IPC regression checks passed')
