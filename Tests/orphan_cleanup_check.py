"""Remove proven stale observations while preserving and restoring temporary owner loss."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json, os, socket, subprocess, sys, tempfile, threading, time
from catalog_check import Store
from optimization_check import incoming, respond
from robustness_check import frame, state

A, B, C = ['80000000-0000-4000-8000-' + str(n).zfill(12) for n in (1, 2, 3)]


def hint(conn, thread, source='owner'):
    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': source, 'version': 1,
        'method': 'thread-stream-following-status-requested',
        'params': {'hostId': 'local', 'conversationId': thread}}))


def snapshot(conn, thread, body, owner='owner'):
    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': owner, 'version': 11,
        'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': thread,
        'change': {'type': 'snapshot', 'revision': 1, 'conversationState': body}}}))


def phases(output):
    return {row['id']: row['phase'] for row in output['sessions']}


def monitor(exe, ids, serve, observe, duration=9):
    stop, launched = threading.Event(), threading.Event()
    outputs, errors = [], []
    proc = controller = None
    with tempfile.TemporaryDirectory(prefix='wink-orphan-', dir='/private/tmp') as tmp:
        store = Store(tmp, ids)
        (store.home / 'ipc').mkdir()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(store.home / 'ipc/ipc.sock')); server.listen(); server.settimeout(.05)
        def command(value):
            assert launched.wait(3), 'Probe did not launch'
            proc.stdin.write(value + '\n'); proc.stdin.flush()
        def worker():
            try: serve(server, store, stop, command)
            except Exception as error: errors.append(repr(error))
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        def finish():
            if stop.wait(duration): return
            stop.set()
            try:
                worker_thread.join(timeout=3)
                assert not worker_thread.is_alive(), 'Orphan responder did not stop'
                command('finish')
            except Exception as error: errors.append(repr(error))
        try:
            proc = subprocess.Popen([str(exe), '--probe', str(duration + 10)],
                env={**os.environ, 'CODEX_HOME': tmp}, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            launched.set()
            controller = threading.Thread(target=finish, daemon=True); controller.start()
            for line in proc.stdout:
                output = json.loads(line)
                outputs.append((time.monotonic(), output)); observe(output)
            proc.wait(timeout=3); controller.join(timeout=3)
            assert not controller.is_alive(), 'Orphan controller did not stop'
            assert proc.returncode == 0, proc.stderr.read()
            assert not errors, (errors, outputs)
        finally:
            stop.set()
            if proc is not None and proc.poll() is None:
                proc.terminate(); proc.wait(timeout=3)
            worker_thread.join(timeout=3)
            if controller is not None: controller.join(timeout=3)
            server.close(); store.close()
        assert not worker_thread.is_alive(), 'Orphan responder survived cleanup'
    return outputs


def accept(server, stop):
    while not stop.is_set():
        try: return server.accept()[0]
        except socket.timeout: pass
    return None


def follow(obj, trace):
    assert obj.get('type') == 'broadcast' and obj.get('method') == 'thread-stream-following-changed', obj
    assert obj['version'] == 1 and obj['params']['hostId'] == 'local', obj
    thread, value = obj['params']['conversationId'], obj['params']['following']
    assert thread in (A, B, C) and type(value) is bool, obj
    trace.append(('follow', thread, value, obj.get('targetClientIds')))
    return thread, value


def eviction_and_restore(exe):
    initial, evicted, restored = threading.Event(), threading.Event(), threading.Event()
    trace, actions, negative = [], {}, set()
    def observe(output):
        values = phases(output)
        if values.get(A) == values.get(C) == 'working' and values.get(B) == 'ready': initial.set()
        if initial.is_set() and output['connected'] and A not in values and B not in values and values.get(C) == 'unknown': evicted.set()
        if 'restore' in actions and values.get(A) == values.get(C) == 'working' and B not in values: restored.set()
    def serve(server, store, stop, command):
        conn = accept(server, stop)
        assert conn is not None
        with conn:
            requests = {}
            for obj in incoming(conn, stop):
                if obj is not None:
                    if obj.get('type') == 'request':
                        method = obj['method']; assert method in ('initialize', 'thread-owner-discovery'), obj
                        thread = obj.get('params', {}).get('conversationId')
                        if method == 'initialize':
                            respond(conn, obj); command('background')
                            for target in (A, B, C): hint(conn, target)
                        else:
                            assert thread in (A, B, C) and obj['params']['hostId'] == 'local', obj
                            requests.setdefault(thread, obj)
                            absent = 'delete' in actions and ('restore' not in actions or thread == B)
                            if absent: negative.add(thread)
                            respond(conn, obj, owner='new-owner' if 'restore' in actions else 'owner', absent=absent)
                    elif obj.get('type') == 'client-discovery-response':
                        assert obj['response'] == {'canHandle': False}, obj
                    else:
                        thread, value = follow(obj, trace)
                        if value:
                            expected_owner = 'new-owner' if 'restore' in actions else 'owner'
                            assert obj.get('targetClientIds') == [expected_owner], ('Stale reply changed owner', trace)
                            body = state(thread, thread != B)
                            if thread == B: body['hasUnreadTurn'] = True
                            snapshot(conn, thread, body, expected_owner)
                if initial.is_set() and 'delete' not in actions:
                    actions['delete'] = time.monotonic()
                    store.remove(A); store.remove(B)
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'version': 0,
                        'method': 'client-status-changed', 'params': {'clientId': 'owner', 'status': 'disconnected'}}))
                if evicted.is_set() and C in negative and 'restore' not in actions:
                    actions['restore'] = time.monotonic()
                    # A deleted observation is not blacklisted; a later native owner can reuse the ID.
                    store.put(A)
                    respond(conn, requests[B], owner='stale-owner')
                    stale = state(B, False); stale['hasUnreadTurn'] = True
                    snapshot(conn, B, stale)
                    hint(conn, A, 'new-owner'); hint(conn, C, 'new-owner')
    outputs = monitor(exe, (A, B, C), serve, observe)
    detail = (actions, negative, trace, outputs)
    assert initial.is_set() and evicted.is_set() and restored.is_set(), detail
    assert {A, B, C}.issubset(negative), ('Explicit owner misses were not exercised', detail)
    assert any(t >= actions['delete'] and phases(out).get(C) == 'unknown' for t, out in outputs), detail
    assert not any(t >= actions['delete'] and out['connected'] and C not in phases(out) for t, out in outputs), ('Temporary owner loss removed a valid chat', detail)
    assert not any(t >= actions['restore'] and B in phases(out) for t, out in outputs), ('Late replies resurrected a deleted observation', detail)
    print('Orphan eviction passed: working/ready removed, temporary owner retained, later activity restored, stale replies ignored', flush=True)


def reconnect(exe, deleted):
    initial, returned = threading.Event(), threading.Event()
    trace, actions = [], {}
    def observe(output):
        values = phases(output)
        if 'closed' not in actions and values.get(A) == 'working': initial.set()
        if 'negative' in actions and output['connected']:
            if deleted and A not in values: returned.set()
            if not deleted and values.get(A) == 'working': returned.set()
    def serve(server, store, stop, command):
        for connection in range(2):
            conn = accept(server, stop)
            assert conn is not None
            with conn:
                for obj in incoming(conn, stop):
                    if obj is not None:
                        if obj.get('type') == 'request':
                            method = obj['method']; assert method in ('initialize', 'thread-owner-discovery'), obj
                            if method == 'initialize':
                                respond(conn, obj); command('background'); hint(conn, A)
                            else:
                                assert obj['params'] == {'hostId': 'local', 'conversationId': A}, obj
                                absent = connection == 1 and 'restore' not in actions
                                if absent: actions['negative'] = time.monotonic()
                                respond(conn, obj, absent=absent)
                        elif obj.get('type') == 'client-discovery-response':
                            assert obj['response'] == {'canHandle': False}, obj
                        else:
                            thread, value = follow(obj, trace)
                            if value: snapshot(conn, A, state(A))
                    if connection == 0 and initial.is_set():
                        actions['closed'] = time.monotonic()
                        if deleted: store.remove(A)
                        conn.shutdown(socket.SHUT_RDWR)
                        break
                    if connection == 1 and not deleted and 'negative' in actions and 'restore' not in actions:
                        actions['restore'] = time.monotonic(); hint(conn, A)
    outputs = monitor(exe, (A,), serve, observe, duration=8)
    detail = (deleted, actions, trace, outputs)
    assert initial.is_set() and 'negative' in actions and returned.is_set(), detail
    if not deleted:
        assert any(t >= actions['closed'] and out['connected'] and phases(out).get(A) == 'unknown' for t, out in outputs), ('Reconnect hid existing persisted chat', detail)
    print('Orphan reconnect passed: ' + ('offline deletion cleaned' if deleted else 'valid chat restored'), flush=True)


def main():
    exe = Path(sys.argv[1]).resolve()
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(eviction_and_restore, exe)] + [pool.submit(reconnect, exe, deleted) for deleted in (False, True)]
        for job in jobs: job.result()


if __name__ == '__main__':
    main()
