"""Check reconnect efficiency and discovery/patch safeguards with isolated IPC."""
from concurrent.futures import ThreadPoolExecutor
import json, os, pathlib, socket, struct, subprocess, sys, tempfile, threading, time
from robustness_check import frame, state

A, B, C = ['40000000-0000-4000-8000-00000000000' + str(n) for n in (1, 2, 3)]


def incoming(conn, stop):
    buf = b''
    conn.settimeout(.025)
    while not stop.is_set():
        try:
            data = conn.recv(65536)
            if not data:
                return
            buf += data
        except socket.timeout:
            yield None
            continue
        while len(buf) >= 4:
            size = struct.unpack('<I', buf[:4])[0]
            if len(buf) < size + 4:
                break
            yield json.loads(buf[4:size + 4])
            buf = buf[size + 4:]


def accept(server, stop):
    while not stop.is_set():
        try:
            return server.accept()[0]
        except socket.timeout:
            continue
    raise RuntimeError('Fixture stopped')


def respond(conn, obj, owner='owner', absent=False):
    assert obj['method'] in ('initialize', 'thread-owner-discovery'), 'Unexpected mutation'
    conn.sendall(frame({'type': 'response', 'requestId': obj['requestId'],
        'resultType': 'error' if absent else 'success', 'error': 'no-client-found' if absent else None,
        'handledByClientId': owner, 'result': {'clientId': 'observer'}}))


def snapshot(conn, thread, body, revision=1):
    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'version': 11,
        'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': thread,
        'change': {'type': 'snapshot', 'revision': revision, 'conversationState': body}}}))


def fixture(exe, duration, roots, serve):
    errors, outputs = [], []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix='wink-optimization-', dir='/private/tmp') as tmp:
        home = pathlib.Path(tmp)
        for folder in ('ipc', 'sessions', 'thread-writer-locks'):
            (home / folder).mkdir()
        for thread in roots:
            (home / 'thread-writer-locks' / (thread + '.lock')).touch()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(home / 'ipc/ipc.sock'))
        server.listen()
        server.settimeout(2)
        def worker():
            try:
                serve(server, home, stop)
            except Exception as error:
                if not stop.is_set():
                    errors.append(repr(error))
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        proc = None
        try:
            proc = subprocess.Popen([str(exe), '--probe', str(duration)],
                env={**os.environ, 'CODEX_HOME': tmp}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in proc.stdout:
                outputs.append((time.monotonic(), json.loads(line)))
            proc.wait(timeout=3)
            stderr = proc.stderr.read()
            assert proc.returncode == 0, stderr
            assert not errors, errors
        finally:
            stop.set()
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
            server.close()
            thread.join(timeout=3)
        assert not thread.is_alive(), 'Fixture did not stop'
    return outputs


def reconnect(exe):
    accepted, closed = [], []
    def serve(server, home, stop):
        for index in range(7):
            with accept(server, stop) as conn:
                accepted.append(time.monotonic())
                began = None
                for obj in incoming(conn, stop):
                    if obj is not None:
                        if obj.get('type') == 'request':
                            respond(conn, obj)
                        elif obj.get('method') == 'thread-stream-following-changed':
                            if obj['params']['following']:
                                snapshot(conn, A, state(A))
                                began = began or time.monotonic()
                        elif obj.get('type') == 'client-discovery-response':
                            assert obj['response'] == {'canHandle': False}
                        else:
                            raise AssertionError('Unexpected message')
                    # Briefly initialized peers must not reset the failure backoff.
                    lifetime = 11 if index == 5 else .15
                    if index < 6 and began and time.monotonic() - began >= lifetime:
                        closed.append(time.monotonic())
                        conn.shutdown(socket.SHUT_RDWR)
                        break
    outputs = fixture(exe, 31, (A,), serve)
    assert len(accepted) == 7 and len(closed) == 6, (accepted, closed)
    delays = [accepted[i + 1] - closed[i] for i in range(6)]
    for delay, expected in zip(delays, [.5, 1, 2, 4, 5, .5]):
        assert expected * .75 <= delay <= expected + 1, ('Reconnect backoff/reset', delays)
    assert sum(not o['connected'] for _, o in outputs) >= 6, 'Failures were hidden'
    assert any(o['connected'] and any(s['phase'] == 'working' for s in o['sessions'])
               for t, o in outputs if t >= accepted[-1]), 'Final reconnect did not restore a validated count'
    print('Reconnect efficiency passed: quick retry, bounded backoff after initialized peers, healthy reset', flush=True)


def discovery(exe):
    trace, negative_at, first_requests = [], {}, {}
    times = {}
    states = {thread: state(thread) for thread in (A, B, C)}
    states[A]['turnHistory']['history']['entitiesByKey']['entity']['items'] = [{'type': 'agentMessage', 'summary': ['']}]
    def serve(server, home, stop):
        with accept(server, stop) as conn:
            start = time.monotonic()
            hinted = burst = gap = False
            silent = False
            for obj in incoming(conn, stop):
                now = time.monotonic()
                elapsed = now - start
                if obj is not None:
                    method = obj.get('method')
                    if obj.get('type') == 'request':
                        if method == 'initialize':
                            respond(conn, obj)
                        else:
                            assert method == 'thread-owner-discovery'
                            target = obj['params']['conversationId']
                            trace.append((now, 'discover', target))
                            if target != A and target not in first_requests:
                                first_requests[target] = obj
                            else:
                                respond(conn, obj)
                    elif method == 'thread-stream-following-changed':
                        target = obj['params']['conversationId']
                        trace.append((now, 'follow', target, obj['params']['following'], obj.get('targetClientIds')))
                        if obj['params']['following'] and not (target == A and silent):
                            snapshot(conn, target, states[target])
                    elif obj.get('type') == 'client-discovery-response':
                        assert obj['response'] == {'canHandle': False}
                    else:
                        raise AssertionError('Unexpected message')
                for target, offset in ((C, 4.2), (B, 4.7)):
                    if elapsed >= offset and target in first_requests and target not in negative_at:
                        negative_at[target] = time.monotonic()
                        respond(conn, first_requests[target], absent=True)
                if C in negative_at and now >= negative_at[C] + .1 and not hinted:
                    hinted = True
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'desktop-observer', 'method': 'thread-stream-following-changed', 'version': 1,
                        'params': {'hostId': 'local', 'conversationId': C, 'following': True}}))
                if elapsed >= 10.4 and not burst:
                    burst = True
                    times['burst'] = time.monotonic()
                    for revision in range(2, 32):
                        conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-stream-state-changed', 'version': 11,
                            'params': {'hostId': 'local', 'conversationId': A, 'change': {'type': 'patches', 'baseRevision': revision - 1, 'revision': revision,
                            'patches': [{'op': 'replace', 'path': ['turnHistory', 'history', 'entitiesByKey', 'entity', 'items', 0, 'summary', 0], 'value': 'chunk'}]}}}))
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-stream-state-changed', 'version': 11,
                        'params': {'hostId': 'local', 'conversationId': A, 'change': {'type': 'patches', 'baseRevision': 31, 'revision': 32,
                        'patches': [{'op': 'replace', 'path': ['title'], 'value': 'Updated title'}]}}}))
                    times['ready'] = time.monotonic()
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-stream-state-changed', 'version': 11,
                        'params': {'hostId': 'local', 'conversationId': A, 'change': {'type': 'patches', 'baseRevision': 32, 'revision': 33,
                        'patches': [{'op': 'replace', 'path': ['threadRuntimeStatus'], 'value': {'type': 'idle'}},
                                    {'op': 'replace', 'path': ['hasUnreadTurn'], 'value': True},
                                    {'op': 'replace', 'path': ['turnHistory', 'history', 'entitiesByKey', 'entity', 'status'], 'value': 'completed'}]}}}))
                    # A duplicate old discovery reply must not replace the current owner.
                    respond(conn, first_requests[C], owner='stale-owner')
                if elapsed >= 11.3 and not gap:
                    gap = True
                    silent = True
                    times['gap'] = time.monotonic()
                    for _ in range(10):
                        conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-stream-state-changed', 'version': 11,
                            'params': {'hostId': 'local', 'conversationId': A, 'change': {'type': 'patches', 'baseRevision': 999, 'revision': 1000, 'patches': []}}}))
    outputs = fixture(exe, 13, (A, B, C), serve)
    requests = {target: [t for t, kind, thread, *rest in trace if kind == 'discover' and thread == target] for target in (B, C)}
    assert len(requests[B]) == 2 and requests[B][1] - negative_at[B] >= .9, ('Negative cooldown did not prevent same-tick retry', trace)
    assert requests[B][1] - negative_at[B] < 6.5, 'Cooldown lost bounded fallback owner discovery'
    assert len(requests[C]) == 2 and requests[C][1] - negative_at[C] < .9, ('Specific following hint did not bypass cooldown', trace)
    assert not any(kind == 'follow' and rest[-1] == ['stale-owner'] for _, kind, _, *rest in trace), 'Late discovery reply replaced a fresh owner'
    phase = lambda o, target: next((s['phase'] for s in o['sessions'] if s['id'] == target), None)
    ready_at = next((t for t, o in outputs if t >= times['ready'] and phase(o, A) == 'ready'), None)
    assert ready_at is not None, 'Ignored text patches broke revision continuity or later status publication'
    assert any(times['burst'] <= t < ready_at and phase(o, A) == 'working' for t, o in outputs), 'Title changes stopped publishing after ignored text patches'
    assert any(t >= times['gap'] and phase(o, A) == 'unknown' and phase(o, B) == phase(o, C) == 'working' for t, o in outputs), 'Revision gap did not isolate unavailable status'
    assert sum(kind == 'follow' and target == A and rest[0] and t >= times['gap'] for t, kind, target, *rest in trace) == 1, 'Repeated gaps bypassed resnapshot coalescing'
    print('Discovery efficiency passed: negative cooldown, specific hint bypass, fallback, stale reply, patch burst/revision gap', flush=True)


def main():
    exe = pathlib.Path(sys.argv[1]).resolve()
    with ThreadPoolExecutor(max_workers=2) as pool:
        checks = [pool.submit(check, exe) for check in (reconnect, discovery)]
        for check in checks:
            check.result()
    print('Optimization checks passed', flush=True)


if __name__ == '__main__':
    main()
