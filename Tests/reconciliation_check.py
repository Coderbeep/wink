"""Reconcile visible counters after missing events, while idle histories are released."""
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time

exe = pathlib.Path(sys.argv[1]).resolve()
A, B, C, D = ['10000000-0000-4000-8000-00000000000' + str(n) for n in range(1, 5)]
outputs, errors, trace = [], [], []
stop = threading.Event()

def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack('<I', len(data)) + data

def state(thread, active=False):
    return {'id': thread, 'title': 'Ready reconciliation fixture',
            'originator': 'codex_work_desktop', 'source': 'vscode', 'threadSource': 'user',
            'resumeState': 'resumed', 'threadRuntimeStatus': {'type': 'active' if active else 'idle', 'activeFlags': []},
            'hasUnreadTurn': not active, 'requests': [], 'turnHistory': {'kind': 'canonical', 'history': {'islands': [{'newerBoundary': {'status': 'exhausted'}, 'entries': [{'value': 'turn'}]}], 'entitiesByKey': {
                'turn': {'turnId': 'turn-1', 'status': 'inProgress' if active else 'completed',
                         'turnStartedAtMs': 1, 'items': []}}}}}

with tempfile.TemporaryDirectory(prefix='dot-ready-reconcile-', dir='/private/tmp') as tmp:
    home = pathlib.Path(tmp)
    for folder in ('ipc', 'sessions', 'thread-writer-locks'):
        (home / folder).mkdir()
    for thread in (A, B, C, D):
        (home / 'thread-writer-locks' / (thread + '.lock')).touch()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(home / 'ipc' / 'ipc.sock'))
    server.listen()
    server.settimeout(27)
    states = {thread: state(thread, active=thread == C) for thread in (A, B, C, D)}

    def serve():
        try:
            conn, _ = server.accept()
            conn.settimeout(.03)
            buf, started, stage = b'', None, 0
            completed_c = False
            revisions = {thread: 1 for thread in states}

            def snapshot(thread):
                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                    'method': 'thread-stream-state-changed', 'version': 11,
                    'params': {'hostId': 'local', 'conversationId': thread,
                               'change': {'type': 'snapshot', 'revision': revisions[thread], 'conversationState': states[thread]}}}))

            def read(thread, value):
                trace.append((time.monotonic() - started, 'read', thread, value))
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
                            'resultType': 'success', 'handledByClientId': 'owner', 'result': {'clientId': 'observer'}}))
                    elif obj.get('method') == 'thread-stream-following-changed':
                        thread = obj['params']['conversationId']
                        following = obj['params']['following']
                        started = started or time.monotonic()
                        trace.append((time.monotonic() - started, 'follow', thread, following))
                        if following:
                            snapshot(thread)
                if started is None:
                    continue
                elapsed = time.monotonic() - started
                if stage == 0 and elapsed > .6:
                    stage = 1
                    read(A, False)
                    read(D, True)
                elif stage == 1 and elapsed > 1:
                    stage = 2
                    snapshot(A)  # Deliberately stale true for the acknowledged turn.
                    states[D]['hasUnreadTurn'] = False
                    revisions[D] += 1
                    snapshot(D)  # Old global true must not override fresh canonical false.
                elif stage == 2 and elapsed > 1.5:
                    stage = 3
                    turn = states[A]['turnHistory']['history']['entitiesByKey']['turn']
                    turn.update(turnId='turn-2', status='inProgress', turnStartedAtMs=2)
                    states[A]['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': []}
                    revisions[A] += 1
                    snapshot(A)
                elif stage == 3 and elapsed > 2:
                    stage = 4
                    states[A]['turnHistory']['history']['entitiesByKey']['turn']['status'] = 'completed'
                    states[A]['threadRuntimeStatus'] = {'type': 'idle'}
                    revisions[A] += 1
                    snapshot(A)  # New answer, without a global unread=true event.
                elif stage == 4 and elapsed > 3:
                    stage = 5
                    states[A]['hasUnreadTurn'] = False  # No push and unchanged revision: reconcile at ~5s.
                    trace.append((elapsed, 'canonical-read-without-event', A, None))
                elif stage == 5 and elapsed > 11.2:
                    stage = 6
                    states[B]['hasUnreadTurn'] = False  # No read event after original idle lease would expire.
                    trace.append((elapsed, 'canonical-read-without-event', B, None))
                if elapsed > 7 and not completed_c:
                    completed_c = True
                    states[C]['threadRuntimeStatus'] = {'type': 'idle'}
                    states[C]['turnHistory']['history']['entitiesByKey']['turn']['status'] = 'completed'
                    revisions[C] += 1
                    trace.append((elapsed, 'completion-without-event', C, None))
            conn.close()
        except Exception as exc:
            if not stop.is_set():
                errors.append(repr(exc))

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    proc = subprocess.Popen([str(exe), '--probe', '27'], env={**os.environ, 'CODEX_HOME': tmp},
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

    assert 'idle' in phases(A, .6, 1.5), 'Native read did not clear A'
    assert 'ready' not in phases(A, 1, 1.5), 'Stale same-turn snapshot resurrected acknowledged answer'
    assert 'ready' in phases(A, 2, 3), 'New turn was suppressed without a global unread=true event'
    assert 'idle' in phases(A, 3, 8), 'Missing read notification was not reconciled within five seconds'
    assert 'idle' in phases(D, 1, 2), 'Old unread=true event overrode canonical read=false'
    assert 'idle' in phases(B, 11.2, 17), 'Read after idle settling did not clear through reconciliation'
    assert sum(thread == C and value for t, kind, thread, value in trace if kind == 'follow' and t > 2) >= 2, 'Working counter was not periodically reconciled'
    assert 'idle' in phases(C, 7, 12), 'Missing final working update left a stale working count'
    assert not any(t > 17 and kind == 'follow' and value
                   for t, kind, thread, value in trace if kind == 'follow'), 'Refresh continued after no ready rows remained'
    assert sum(kind == 'follow' and thread == B and value for _, kind, thread, value in trace if kind == 'follow') >= 3, 'Equal-revision snapshots stopped ready reconciliation'
    assert any(14 < t < 17 and kind == 'follow' and thread == A and not value for t, kind, thread, value in trace if kind == 'follow'), 'Cleared A idle subscription was not released'
    assert any(24 < t < 27 and kind == 'follow' and thread == B and not value for t, kind, thread, value in trace if kind == 'follow'), 'Cleared B idle subscription was not released'
    print('Counter reconciliation passed: missing event, unchanged revision, new turn, canonical false, working/read recovery, stops after all rows become idle')
