"""Exercise the real executable against an isolated local desktop-protocol fixture."""
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time

ROOT = '10000000-0000-4000-8000-000000000001'
CLI = '10000000-0000-4000-8000-000000000002'
HEALTHY = '10000000-0000-4000-8000-000000000003'
exe = pathlib.Path(sys.argv[1]).resolve()
errors, received, frames = [], [], []

def encode(obj):
    b = json.dumps(obj).encode()
    return struct.pack('<I', len(b)) + b

def snapshot(status, runtime, revision, turn='turn-1', unread=True, origin='codex_work_desktop'):
    return {'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
            'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': ROOT,
            'change': {'type': 'snapshot', 'revision': revision, 'conversationState': {
                'id': ROOT, 'title': 'Fixture', 'originator': origin, 'source': 'vscode', 'threadSource': 'user',
                'threadRuntimeStatus': {'type': runtime}, 'hasUnreadTurn': unread, 'resumeState': 'resumed', 'requests': [],
                'turnHistory': {'kind': 'canonical', 'history': {'islands': [{'newerBoundary': {'status': 'exhausted'}, 'entries': [{'value': 'entity'}]}], 'entitiesByKey': {'entity': {
                    'turnId': turn, 'status': status, 'turnStartedAtMs': 1 if turn == 'turn-1' else 2,
                    'items': [{'type': 'error', 'willRetry': False, 'message': 'Fixture error', 'errorInfo': None}] if status == 'failed' else []}}}}}}}}

with tempfile.TemporaryDirectory(prefix='codexdot-test-', dir='/private/tmp') as tmp:
    home = pathlib.Path(tmp)
    for folder in ('ipc', 'thread-writer-locks', 'sessions'):
        (home / folder).mkdir()
    (home / 'thread-writer-locks' / (ROOT + '.lock')).touch()
    (home / 'thread-writer-locks' / (CLI + '.lock')).touch()
    (home / 'thread-writer-locks' / (HEALTHY + '.lock')).touch()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(home / 'ipc' / 'ipc.sock')); server.listen(); server.settimeout(30)
    stop = threading.Event()
    def serve():
        try:
            for connection in range(3):
                conn, _ = server.accept(); conn.settimeout(.05)
                buf = b''; followed = False; stage = 0; tick = time.monotonic(); revision = 1
                while not stop.is_set():
                    try:
                        chunk = conn.recv(65536)
                        if not chunk: break
                        buf += chunk
                    except socket.timeout: pass
                    while len(buf) >= 4:
                        n = struct.unpack('<I', buf[:4])[0]
                        if len(buf) < 4+n: break
                        obj = json.loads(buf[4:4+n]); buf = buf[4+n:]; received.append({**obj, '_test_connection': connection})
                        method = obj.get('method')
                        if obj.get('type') == 'request':
                            assert method in ('initialize', 'thread-owner-discovery'), method
                            response = {'type': 'response', 'requestId': obj['requestId'], 'resultType': 'success',
                                        'handledByClientId': 'owner', 'result': {'clientId': 'observer'}}
                            conn.sendall(encode(response))
                            if method == 'initialize':
                                conn.sendall(encode({'type': 'client-discovery-request', 'requestId': 'decline-me'}))
                        elif method == 'thread-stream-following-changed' and obj['params']['following']:
                            if obj['params']['conversationId'] == CLI:
                                msg = snapshot('completed', 'idle', 1, origin='codex_cli')
                                msg['params']['conversationId'] = CLI; msg['params']['change']['conversationState']['id'] = CLI
                            elif obj['params']['conversationId'] == HEALTHY:
                                msg = snapshot('inProgress', 'active', 1, unread=False)
                                msg['params']['conversationId'] = HEALTHY; msg['params']['change']['conversationState']['id'] = HEALTHY
                            else:
                                if connection == 2 and followed:
                                    continue  # Keep sending patches, but never satisfy the resync.
                                followed = True; tick = time.monotonic()
                                msg = snapshot('inProgress' if stage == 0 else 'completed', 'active' if stage == 0 else 'idle', revision)
                            b = encode(msg)
                            conn.sendall(b[:2]); conn.sendall(b[2:19]); conn.sendall(b[19:])
                        elif method == 'thread-stream-following-changed': pass
                        elif obj.get('type') == 'client-discovery-response':
                            assert obj['response'] == {'canHandle': False}
                        else: raise AssertionError('Unexpected write: ' + str(method))
                    if not followed or time.monotonic()-tick < .25: continue
                    tick = time.monotonic()
                    if connection == 2:
                        msg = {'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
                               'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': ROOT,
                               'change': {'type': 'patches', 'baseRevision': 999, 'revision': 1000, 'patches': []}}}
                        conn.sendall(encode(msg)); stage += 1
                        continue
                    if connection == 1:
                        if stage == 0:
                            msg = snapshot('completed', 'idle', 2); msg['version'] = 12
                            conn.sendall(encode(msg)); stage += 1
                        elif stage == 1:
                            conn.close(); break
                        continue
                    revision += 1
                    if stage == 0:
                        msg = snapshot('inProgress', 'active', revision)
                        msg['params']['change']['conversationState']['threadRuntimeStatus']['activeFlags'] = ['waitingOnApproval']
                        msg['params']['change']['conversationState']['requests'] = [{'method': 'item/permissions/requestApproval', 'params': {'turnId': 'turn-1'}}]
                    elif stage == 1: msg = snapshot('failed', 'idle', revision)
                    elif stage == 2: msg = snapshot('interrupted', 'idle', revision, unread=False)
                    elif stage == 3: msg = snapshot('inProgress', 'active', revision, turn='turn-2')
                    elif stage == 4: msg = snapshot('completed', 'idle', revision, turn='turn-2')
                    elif stage == 5:
                        msg = {'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
                               'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': ROOT,
                               'change': {'type': 'patches', 'baseRevision': 999, 'revision': 1000, 'patches': []}}}
                    elif stage == 6:
                        msg = {'type': 'broadcast', 'method': 'client-status-changed', 'params': {'clientId': 'owner', 'status': 'disconnected'}}
                    else: conn.close(); break
                    conn.sendall(encode(msg)); stage += 1
                conn.close()
        except Exception as e:
            if not stop.is_set(): errors.append(repr(e))
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    proc = subprocess.Popen([str(exe), '--probe', '26'], env={**os.environ, 'CODEX_HOME': tmp}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = proc.communicate(timeout=30)
    stop.set(); server.close(); thread.join(timeout=1)
    assert proc.returncode == 0, stderr
    assert not errors, errors
    outputs = [json.loads(line) for line in stdout.splitlines()]
    phases = [s['phase'] for output in outputs for s in output['sessions'] if s['id'] == ROOT]
    assert all(x in phases for x in ['working', 'approval', 'failed', 'idle', 'ready']), phases
    assert all(s['id'] != CLI for output in outputs for s in output['sessions']), 'CLI leaked'
    assert any(not x['connected'] for x in outputs), 'Disconnect not surfaced'
    assert any('format' in x['message'] for x in outputs), 'Protocol upgrade not rejected'
    assert any(x['connected'] and any(s['id'] == ROOT and s['phase'] == 'unknown' for s in x['sessions'])
               and any(s['id'] == HEALTHY and s['phase'] == 'working' for s in x['sessions']) for x in outputs), 'One stalled root invalidated healthy counters'
    follows = [x for x in received if x.get('method') == 'thread-stream-following-changed']
    assert sum(x['params']['following'] and x['params']['conversationId'] == ROOT for x in follows) >= 3, 'Resync/reconnect not exercised'
    assert any(not x['params']['following'] for x in follows), 'No unsubscribe cleanup'
    stalled_follows = [x for x in follows if x['_test_connection'] == 2 and x['params']['conversationId'] == ROOT and x['params']['following']]
    assert 2 <= len(stalled_follows) <= 3, 'Snapshot requests were not coalesced: ' + str(len(stalled_follows))
    assert any(x.get('type') == 'client-discovery-response' for x in received), 'Capability request not declined'
    print('IPC integration checks passed: discovery, fragmented frames, desktop filtering, work/wait/fail/interrupt/ready, resync, disconnect/reconnect, incompatible version, coalesced bounded resync, cleanup')
