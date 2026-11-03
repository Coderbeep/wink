"""Verify native read-state synchronization, scoping, and new-answer rearming."""
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time
EXE = pathlib.Path(sys.argv[1]).resolve()
A, B = ['10000000-0000-4000-8000-00000000000'+str(n) for n in (1,2)]
trace, outputs, errors = [], [], []
stop = threading.Event()
def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack('<I', len(data)) + data
def state(thread):
    return {'id':thread,'title':'Read fixture','originator':'codex_work_desktop','source':'vscode','threadSource':'user','resumeState':'resumed',
            'threadRuntimeStatus':{'type':'idle'},'hasUnreadTurn':True,'requests':[],
            'turnHistory':{'kind':'canonical','history':{'islands':[{'newerBoundary':{'status':'exhausted'},'entries':[{'value':'turn'}]}],'entitiesByKey':{'turn':{'turnId':'turn-1','status':'completed','turnStartedAtMs':1,'items':[]}}}}}
with tempfile.TemporaryDirectory(prefix='dot-read-', dir='/private/tmp') as tmp:
    home = pathlib.Path(tmp)
    for folder in ('ipc','sessions','thread-writer-locks'):
        (home/folder).mkdir()
    for thread in (A,B): (home/'thread-writer-locks'/(thread+'.lock')).touch()
    server = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    server.bind(str(home/'ipc'/'ipc.sock')); server.listen(); server.settimeout(25)
    states = {thread:state(thread) for thread in (A,B)}
    def serve():
        try:
            for connection in range(2):
                conn,_ = server.accept(); conn.settimeout(.05)
                buf=b''; revisions={A:0,B:0}; followed=set(); tick=None; stage=0
                def snapshot(thread):
                    revisions[thread]+=1
                    conn.sendall(frame({'type':'broadcast','sourceClientId':'owner','method':'thread-stream-state-changed','version':11,
                        'params':{'hostId':'local','conversationId':thread,'change':{'type':'snapshot','revision':revisions[thread],'conversationState':states[thread]}}}))
                def read(thread,value,host='local'):
                    trace.append(('read',thread,value,connection))
                    conn.sendall(frame({'type':'broadcast','sourceClientId':'desktop-coordinator','method':'thread-read-state-changed','version':3,
                        'params':{'hostId':host,'conversationId':thread,'hasUnreadTurn':value,'context':{'identity':{},'executionHostKey':'fixture'}}}))
                while not stop.is_set():
                    try:
                        data=conn.recv(65536)
                        if not data:break
                        buf+=data
                    except socket.timeout:pass
                    while len(buf)>=4:
                        size=struct.unpack('<I',buf[:4])[0]
                        if len(buf)<size+4:break
                        obj=json.loads(buf[4:size+4]);buf=buf[size+4:]
                        if obj.get('type')=='request':
                            assert obj['method'] in ('initialize','thread-owner-discovery'), 'Unexpected mutation'
                            conn.sendall(frame({'type':'response','requestId':obj['requestId'],'resultType':'success','handledByClientId':'owner','result':{'clientId':'observer'}}))
                        elif obj.get('method')=='thread-stream-following-changed':
                            thread=obj['params']['conversationId'];value=obj['params']['following']
                            trace.append(('follow',thread,value,connection))
                            if value:
                                followed.add(thread);snapshot(thread)
                                if tick is None:tick=time.monotonic()
                            else:followed.discard(thread)
                    if tick is None:continue
                    elapsed=time.monotonic()-tick
                    if connection==1:
                        if stage==0 and elapsed>1:
                            stage=1;states[A]['hasUnreadTurn']=True;read(A,True)
                        continue
                    if stage==0 and elapsed>11:
                        assert A in followed and B in followed,'Pending ready subscriptions were not reconciled'
                        stage=1;read(A,False)
                        read(B,False,host='remote-fixture')
                        read('10000000-0000-4000-8000-000000000099',False)
                    elif stage==1 and elapsed>12:
                        stage=2
                        # Force a refresh returning a deliberately stale unread snapshot.
                        (home/'sessions'/('rollout-'+A+'.jsonl')).write_text('{}\n')
                    elif stage==2 and elapsed>13:
                        stage=3
                        turn=states[A]['turnHistory']['history']['entitiesByKey']['turn']
                        turn.update(turnId='turn-2',status='inProgress',turnStartedAtMs=2)
                        states[A]['threadRuntimeStatus']={'type':'active','activeFlags':[]};snapshot(A)
                    elif stage==3 and elapsed>13.5:
                        stage=4;read(A,True)
                    elif stage==4 and elapsed>14:
                        stage=5
                        states[A]['turnHistory']['history']['entitiesByKey']['turn']['status']='completed'
                        states[A]['threadRuntimeStatus']={'type':'idle'};snapshot(A)
                    elif stage==5 and elapsed>14.5:
                        stage=6;read(B,False)
                    elif stage==6 and elapsed>15.2:
                        states[A]['hasUnreadTurn']=False;states[B]['hasUnreadTurn']=False
                        conn.close();break
                conn.close()
        except Exception as exc:
            if not stop.is_set():errors.append(repr(exc))
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    proc=subprocess.Popen([str(EXE),'--probe','23'],env={**os.environ,'CODEX_HOME':tmp},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=time.monotonic()
    for line in proc.stdout: outputs.append((time.monotonic()-started,json.loads(line)))
    proc.wait(timeout=3);stderr=proc.stderr.read();stop.set();thread.join(timeout=1);server.close()
    assert proc.returncode==0,stderr
    assert not errors,errors
    def phases(thread,start,end):
        return [s['phase'] for t,o in outputs if start<t<end for s in o['sessions'] if s['id']==thread]
    assert 'ready' in phases(A,0,10) and 'ready' in phases(B,0,10),'Initial ready entries missing'
    assert 'idle' in phases(A,11,12),'Read event after unsubscribe did not clear ready'
    assert 'ready' not in phases(A,12,13),'Late unread snapshot resurrected read answer'
    assert 'ready' in phases(B,11,13),'Remote or other-thread read changed the wrong entry'
    assert 'working' in phases(A,13,14),'Read state suppressed working state'
    assert 'ready' in phases(A,14,15.2),'New completed unread answer did not rearm'
    assert 'idle' in phases(A,20,21.2),'Reconnect ignored canonical read state'
    assert 'ready' in phases(A,21,23),'Native mark-unread event did not rearm completed answer'
    assert any(not obj['connected'] for _,obj in outputs),'Reconnect was not exercised'
    print('Read-state checks passed: native clear, nonowner source, stale snapshot, scope, new answer, reconnect, mark unread')
