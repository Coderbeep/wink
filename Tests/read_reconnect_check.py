"""Reconnect trusts canonical unread after an acknowledgement event was missed offline."""
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time

exe = pathlib.Path(sys.argv[1]).resolve()
manual = '--manual' in sys.argv[2:]
replace_owner = '--owner' in sys.argv[2:]
A, B = ['10000000-0000-4000-8000-00000000000' + str(n) for n in (1, 2)]
outputs, errors = [], []
stop = threading.Event()

def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack('<I', len(data)) + data

def state(thread):
    return {'id': thread, 'title': 'Read reconnect fixture', 'originator': 'codex_work_desktop',
            'source': 'vscode', 'threadSource': 'user', 'resumeState': 'resumed',
            'threadRuntimeStatus': {'type': 'idle'}, 'hasUnreadTurn': True, 'requests': [],
            'turnHistory': {'kind': 'canonical', 'history': {'islands': [{'newerBoundary': {'status': 'exhausted'}, 'entries': [{'value': 'turn'}]}], 'entitiesByKey': {'turn': {
                'turnId': 'turn-1', 'status': 'completed', 'turnStartedAtMs': 1, 'items': []}}}}}

with tempfile.TemporaryDirectory(prefix='dot-read-reconnect-', dir='/private/tmp') as tmp:
    home = pathlib.Path(tmp)
    for folder in ('ipc', 'sessions', 'thread-writer-locks'):
        (home / folder).mkdir()
    for thread in (A, B):
        (home / 'thread-writer-locks' / (thread + '.lock')).touch()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(home / 'ipc' / 'ipc.sock'))
    server.listen()
    server.settimeout(13)
    states = {thread: state(thread) for thread in (A, B)}

    def serve():
        try:
            for connection in range(1 if replace_owner else 2):
                conn, _ = server.accept()
                conn.settimeout(.03)
                buf, started, stage = b'', None, 0
                revisions = {thread: 1 for thread in states}
                owner = 'owner'

                def snapshot(thread):
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': owner,
                        'method': 'thread-stream-state-changed', 'version': 11,
                        'params': {'hostId': 'local', 'conversationId': thread,
                                   'change': {'type': 'snapshot', 'revision': revisions[thread], 'conversationState': states[thread]}}}))

                def read(thread, value):
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'desktop-coordinator',
                        'method': 'thread-read-state-changed', 'version': 3,
                        'params': {'hostId': 'local', 'conversationId': thread, 'hasUnreadTurn': value}}))

                while not stop.is_set():
                    try:
                        data = conn.recv(65536)
                        if not data:
                            break
                        buf += data
                    except socket.timeout:
                        pass
                    while len(buf) >= 4:
                        size = struct.unpack('<I', buf[:4])[0]
                        if len(buf) < size + 4:
                            break
                        obj = json.loads(buf[4:size + 4])
                        buf = buf[size + 4:]
                        if obj.get('type') == 'request':
                            assert obj['method'] in ('initialize', 'thread-owner-discovery'), 'Unexpected mutation'
                            conn.sendall(frame({'type': 'response', 'requestId': obj['requestId'],
                                'resultType': 'success', 'handledByClientId': owner, 'result': {'clientId': 'observer'}}))
                        elif obj.get('method') == 'thread-stream-following-changed' and obj['params']['following']:
                            started = started or time.monotonic()
                            snapshot(obj['params']['conversationId'])
                    if started is None or connection == 1:
                        continue
                    elapsed = time.monotonic() - started
                    if stage == 0 and elapsed > .6:
                        stage = 1
                        read(A, False)
                        read(B, False)
                    elif stage == 1 and elapsed > 1:
                        stage = 2
                        snapshot(A)  # Stale canonical true leaves unread=true while readTurn suppresses ready.
                        snapshot(B)
                    elif stage == 2 and elapsed > 1.5:
                        stage = 3
                        turn = states[B]['turnHistory']['history']['entitiesByKey']['turn']
                        turn.update(turnId='turn-2', status='inProgress', turnStartedAtMs=2)
                        states[B]['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': []}
                        revisions[B] += 1
                        snapshot(B)
                    elif stage == 3 and elapsed > 2:
                        stage = 4
                        states[B]['turnHistory']['history']['entitiesByKey']['turn']['status'] = 'completed'
                        states[B]['threadRuntimeStatus'] = {'type': 'idle'}
                        revisions[B] += 1
                        snapshot(B)  # Harness manually clears B while its old turn-1 read marker remains.
                    elif stage == 4 and elapsed > 3:
                        stage = 5
                        if replace_owner:
                            owner = 'replacement-owner'
                            conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'desktop-coordinator',
                                'method': 'client-status-changed', 'version': 1,
                                'params': {'status': 'disconnected', 'clientId': 'owner'}}))
                        else:
                            conn.close()
                            break
                conn.close()
        except Exception as exc:
            if not stop.is_set():
                errors.append(repr(exc))

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    proc = subprocess.Popen([str(exe), '--probe', '11'], env={**os.environ, 'CODEX_HOME': tmp},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    for line in proc.stdout:
        outputs.append((time.monotonic() - started, json.loads(line)))
    proc.wait(timeout=2)
    stderr = proc.stderr.read()
    stop.set()
    worker.join(timeout=1)
    server.close()
    assert proc.returncode == 0, stderr
    assert not errors, errors

    def phases(thread, start, end):
        return [s['phase'] for t, obj in outputs if start < t < end for s in obj['sessions'] if s['id'] == thread]

    assert 'idle' in phases(A, .6, 3), 'Native read did not clear A'
    assert 'ready' not in phases(A, 1, 3), 'Stale same-turn snapshot resurrected acknowledged answer'
    if not replace_owner:
        assert any(not obj['connected'] for _, obj in outputs), 'Disconnect was not exercised'
    resumed = 3 if replace_owner else 7.5
    assert 'ready' in phases(A, resumed, 11), 'Fresh unread on reconnect remained suppressed by an old acknowledgement'
    if manual:
        assert 'idle' in phases(B, 2, 3), 'Harness failed to manually clear newer turn'
        assert 'idle' in phases(B, resumed, 11), 'Newer turn manual clear was lost on reconnect'
        assert 'ready' not in phases(B, resumed, 11), 'Rebasing an older read marker rearmed manually cleared newer turn'
    print(('Read owner replacement passed' if replace_owner else 'Read reconnect passed') + ': stale acknowledgement rebased' + (', newer-turn manual clear preserved' if manual else ''))
