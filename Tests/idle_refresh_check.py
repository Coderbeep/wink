"""Keep two working chats counted when an old idle lease overlaps a refresh."""
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time

A = '20000000-0000-4000-8000-000000000001'
B = '20000000-0000-4000-8000-000000000002'
exe = pathlib.Path(sys.argv[1]).resolve()


def frame(obj):
    body = json.dumps(obj).encode()
    return struct.pack('<I', len(body)) + body


def snapshot(thread, active, revision):
    return {'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
            'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': thread,
            'change': {'type': 'snapshot', 'revision': revision, 'conversationState': {
                'id': thread, 'title': 'Fixture', 'originator': 'codex_work_desktop', 'source': 'vscode',
                'threadSource': 'user', 'threadRuntimeStatus': {'type': 'active' if active else 'idle'},
                'hasUnreadTurn': False, 'turnHistory': {'kind': 'canonical', 'history': {'islands': [{'newerBoundary': {'status': 'exhausted'}, 'entries': [{'value': 'turn'}]}], 'entitiesByKey': {'turn': {
                    'turnId': thread + str(revision), 'status': 'inProgress' if active else 'completed',
                    'turnStartedAtMs': revision}}}}}}}}


def check(mode):
    errors, follows = [], []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix='codexdot-idle-', dir='/private/tmp') as tmp:
        home = pathlib.Path(tmp)
        for folder in ('ipc', 'thread-writer-locks', 'sessions'):
            (home / folder).mkdir()
        for thread in (A, B):
            (home / 'thread-writer-locks' / (thread + '.lock')).touch()
        rollout = home / 'sessions' / ('rollout-' + B + '.jsonl')
        rollout.touch()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(home / 'ipc' / 'ipc.sock')); server.listen(); server.settimeout(5)

        def serve():
            try:
                with server.accept()[0] as conn:
                    conn.settimeout(.05)
                    buf = b''; start = time.monotonic(); triggered = False; active_sent = False
                    while not stop.is_set():
                        elapsed = time.monotonic() - start
                        if elapsed >= 8 and not triggered:
                            triggered = True
                            if mode == 'file':
                                rollout.write_text('{"event":"new-turn"}\n')
                            else:
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
                                    'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': B,
                                    'change': {'type': 'patches', 'baseRevision': 999, 'revision': 1000, 'patches': []}}}))
                        if elapsed >= 11.2 and not active_sent:
                            active_sent = True
                            conn.sendall(frame(snapshot(B, True, 2)))
                        try:
                            chunk = conn.recv(65536)
                            if not chunk: break
                            buf += chunk
                        except socket.timeout: continue
                        while len(buf) >= 4:
                            size = struct.unpack('<I', buf[:4])[0]
                            if len(buf) < size + 4: break
                            obj = json.loads(buf[4:size+4]); buf = buf[size+4:]
                            if obj.get('type') == 'request':
                                assert obj['method'] in ('initialize', 'thread-owner-discovery')
                                conn.sendall(frame({'type': 'response', 'requestId': obj['requestId'], 'resultType': 'success',
                                                    'handledByClientId': 'owner', 'result': {'clientId': 'observer'}}))
                            elif obj.get('method') == 'thread-stream-following-changed':
                                params = obj['params']; thread = params['conversationId']
                                follows.append((thread, params['following'], elapsed))
                                if params['following'] and not (thread == B and triggered):
                                    conn.sendall(frame(snapshot(thread, thread == A, 1)))
                            elif obj.get('type') == 'client-discovery-response':
                                assert obj['response'] == {'canHandle': False}
                            else: raise AssertionError('Unexpected write: ' + str(obj.get('method')))
            except Exception as error:
                if not stop.is_set(): errors.append(repr(error))

        worker = threading.Thread(target=serve, daemon=True); worker.start()
        try:
            result = subprocess.run([str(exe), '--probe', '14'], env={**os.environ, 'CODEX_HOME': tmp},
                                    capture_output=True, text=True, timeout=20)
        finally:
            stop.set(); server.close(); worker.join(timeout=1)
        assert result.returncode == 0, result.stderr
        assert not errors, errors
        outputs = [json.loads(line) for line in result.stdout.splitlines()]
        assert any({s['id'] for s in out['sessions'] if s['phase'] == 'working'} == {A, B} for out in outputs), (
            mode, 'late active snapshot was lost', outputs)
        assert any(thread == B and following and 8 <= elapsed < 11.2 for thread, following, elapsed in follows), (
            mode, 'refresh was not requested before the old idle deadline', follows)
        assert not any(thread == B and not following and elapsed < 11.2 for thread, following, elapsed in follows), (
            mode, 'old idle timer cancelled the refresh', follows)
        print('Idle refresh regression passed:', mode, flush=True)


for scenario in ('resync', 'file'):
    check(scenario)
