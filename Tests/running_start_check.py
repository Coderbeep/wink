"""Verify prompt running discovery without pinning closed or unrelated idle chats."""
from concurrent.futures import ThreadPoolExecutor
import pathlib, sys, time
from optimization_check import accept, fixture, incoming, respond
from robustness_check import frame, state
from snapshot_helpers import latest_phase

A, B, C, D = ['60000000-0000-4000-8000-' + str(n).zfill(12) for n in range(1, 5)]


def hint(conn, thread=A, method='thread-stream-following-changed', source='owner', value=True, host='local', version=1):
    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': source, 'method': method, 'version': version,
        'params': {'hostId': host, 'conversationId': thread, 'following': value}}))


def snapshot(conn, thread, active=False, owner='owner', revision=1):
    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': owner,
        'method': 'thread-stream-state-changed', 'version': 11, 'params': {
        'hostId': 'local', 'conversationId': thread, 'change': {'type': 'snapshot',
        'revision': revision, 'conversationState': state(thread, active, 'turn-' + str(revision), revision)}}}))


def request(conn, obj, trace, owner='owner'):
    assert obj['method'] in ('initialize', 'thread-owner-discovery'), ('Unexpected mutation', obj)
    if obj['method'] == 'thread-owner-discovery':
        assert obj['params']['hostId'] == 'local' and obj['params']['conversationId'] in (A, B, C, D), obj
    trace.append((time.monotonic(), 'request', obj['method'], obj.get('requestId')))
    respond(conn, obj, owner=owner)


def valid_follow(obj):
    assert obj.get('type') == 'broadcast' and obj.get('method') == 'thread-stream-following-changed', ('Unexpected mutation', obj)
    assert obj['version'] == 1 and obj['params']['hostId'] == 'local', obj
    assert obj['params']['conversationId'] in (A, B, C, D) and type(obj['params']['following']) is bool, obj
    return obj['params']['conversationId'], obj['params']['following']


def first_working(outputs, thread, after):
    return next((t for t, out in outputs if t >= after and any(s['id'] == thread and s['phase'] == 'working' for s in out['sessions'])), None)


def new_root(exe):
    trace, times = [], {}
    def serve(server, home, stop):
        with accept(server, stop) as conn:
            start = time.monotonic()
            for obj in incoming(conn, stop):
                now = time.monotonic()
                if obj is not None:
                    if obj.get('type') == 'request':
                        if obj['method'] != 'initialize':
                            assert 'hint' in times, ('Invalid hint started discovery', trace)
                            times['discovery'] = now
                        request(conn, obj, trace)
                        if obj['method'] == 'initialize':
                            for host, thread, version, source in (('remote', A, 1, 'owner'), ('local', A, 2, 'owner'),
                                    ('local', 'invalid', 1, 'owner'), ('local', A, 1, 'observer'), ('local', A, 1, '')):
                                hint(conn, thread, 'thread-stream-following-status-requested', source, host=host, version=version)
                    else:
                        thread, following = valid_follow(obj)
                        if following:
                            times.setdefault('follow', now)
                if now - start >= .6 and 'hint' not in times:
                    times['hint'] = now
                    hint(conn, method='thread-stream-following-status-requested')
                if 'follow' in times and now - times['follow'] >= .35 and 'canonical' not in times:
                    times['canonical'] = now
                    snapshot(conn, A, True)
    outputs = fixture(exe, 3, (), serve)
    assert times.get('discovery', float('inf')) - times['hint'] < .75, ('New owner hint was ignored', times, trace)
    seen = first_working(outputs, A, times['canonical'])
    assert seen is not None and seen - times['canonical'] < 1, ('Canonical start was delayed', times, outputs)
    assert not any(s['phase'] == 'working' for t, out in outputs if t < times['canonical'] for s in out['sessions']), 'Hint alone fabricated a working count'
    print('New-root start passed: validated ownership hint, prompt canonical state, no guessed count', flush=True)


def pending_discovery(exe):
    trace, times, requests = [], {}, []
    def serve(server, home, stop):
        with accept(server, stop) as conn:
            start = time.monotonic()
            next_hint = 1.05
            for obj in incoming(conn, stop):
                now = time.monotonic()
                elapsed = now - start
                if obj is not None:
                    if obj.get('type') == 'request':
                        if obj['method'] == 'initialize':
                            request(conn, obj, trace)
                            hint(conn)
                        else:
                            assert obj['method'] == 'thread-owner-discovery' and obj['params'] == {'hostId': 'local', 'conversationId': A}, obj
                            requests.append((now, obj))
                            trace.append((now, 'discovery', len(requests)))
                            if len(requests) == 3:
                                respond(conn, obj)
                                times['verified'] = now
                            else:
                                assert len(requests) < 3, ('Ownership hint storm bypassed cap', trace)
                    else:
                        thread, following = valid_follow(obj)
                        trace.append((now, 'follow', following, obj.get('targetClientIds')))
                        assert obj.get('targetClientIds') == ['owner'], ('Retired discovery reply changed owner', trace)
                        if following:
                            times.setdefault('follow', now)
                            if 'canonical' in times:
                                snapshot(conn, A, True)
                if elapsed >= .2 and 'invalid' not in times:
                    times['invalid'] = now
                    hint(conn, method='thread-stream-following-status-requested', host='remote')
                    hint(conn, method='thread-stream-following-status-requested', version=2)
                    hint(conn, method='thread-stream-following-status-requested', source='observer')
                if elapsed >= .8:
                    assert len(requests) >= 1, ('Initial lookup not exercised', trace)
                    if 'hint' not in times:
                        assert len(requests) == 1, ('Invalid ownership hint replaced lookup', trace)
                if elapsed >= 1 and 'hint' not in times:
                    times['hint'] = now
                    hint(conn, method='thread-stream-following-status-requested')
                if 1.05 <= elapsed < 1.8 and elapsed >= next_hint:
                    next_hint = elapsed + .05
                    hint(conn, method='thread-stream-following-status-requested')
                if elapsed >= 2.6 and len(requests) >= 3 and 'late' not in times:
                    times['late'] = now
                    respond(conn, requests[0][1], absent=True)
                    respond(conn, requests[1][1], owner='stale-owner')
                if elapsed >= 3 and 'storm' not in times:
                    times['storm'] = now
                    for _ in range(20):
                        hint(conn, method='thread-stream-following-status-requested')
                if elapsed >= 4 and 'follow' in times and 'canonical' not in times:
                    times['canonical'] = now
                    snapshot(conn, A, True)
    outputs = fixture(exe, 16, (), serve)
    assert len(requests) == 3, ('Trailing ownership hint was lost or storm was unbounded', trace)
    assert requests[1][0] - times['hint'] < .75, ('Fresh ownership hint remained behind old lookup', trace)
    assert .9 <= requests[2][0] - requests[1][0] <= 1.6, ('Trailing retry was postponed by hint burst or exceeded cap', trace)
    assert sum(kind == 'follow' and value for t, kind, value, *rest in trace if kind == 'follow' and t < times['canonical']) == 1, ('Hints duplicated in-flight snapshots', trace)
    assert first_working(outputs, A, times['canonical']) is not None, ('Canonical start never published', outputs)
    assert latest_phase(outputs, A, times['canonical'] - .01) != 'working', 'Ownership hint fabricated working before snapshot'
    assert latest_phase(outputs, A, requests[0][0] + 14) == 'working', ('Retired lookup timeout damaged verified state', outputs)
    assert not any(t > times['canonical'] and latest_phase(outputs, A, t) == 'unknown' for t, out in outputs), ('Retired lookup changed freshness', outputs)
    print('Pending discovery passed: immediate/trailing bounded retry, stale replies/timeouts, coalesced snapshot', flush=True)


def pending_catalog(exe):
    trace, times, requests = [], {}, []
    def serve(server, home, stop):
        with accept(server, stop) as conn:
            start = time.monotonic()
            for obj in incoming(conn, stop):
                now = time.monotonic()
                elapsed = now - start
                if obj is not None:
                    if obj.get('type') == 'request':
                        if obj['method'] == 'initialize':
                            request(conn, obj, trace)
                        else:
                            assert obj['method'] == 'thread-owner-discovery' and obj['params'] == {'hostId': 'local', 'conversationId': A}, obj
                            requests.append(now)
                            respond(conn, obj, absent=len(requests) == 1)
                    else:
                        thread, following = valid_follow(obj)
                        if following:
                            times['canonical'] = now
                            snapshot(conn, A, True)
                if elapsed >= 4.4 and 'first' not in times:
                    times['first'] = now
                    hint(conn, method='thread-stream-following-status-requested')
                if elapsed >= 4.7 and 'ready' not in times:
                    times['ready'] = now
                    hint(conn, method='thread-stream-following-status-requested')
                if elapsed >= 5.05 and 'catalog' not in times:
                    times['catalog'] = now
                    # Exercise the same catalog scan regardless of the periodic timer's leeway.
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'router', 'method': 'client-status-changed',
                        'version': 0, 'params': {'clientId': 'new-client', 'status': 'connected'}}))
    outputs = fixture(exe, 7, (), serve)
    assert len(requests) == 2, ('Catalog scan discarded a queued ownership retry', times, requests, trace)
    assert .9 <= requests[1] - requests[0] <= 1.6, ('Catalog scan bypassed or postponed bounded retry', requests)
    assert times['ready'] < times['catalog'] < requests[1], ('Pending retry did not cross catalog scan', times, requests)
    seen = first_working(outputs, A, times['ready'])
    assert seen is not None and seen - times['ready'] < 1.5, ('Ready owner was not verified after catalog scan', outputs)
    print('Pending catalog retry passed: queued owner hint survives no-lock pruning', flush=True)


def owner_views(exe):
    trace, times, followed = [], {}, set()
    owners = {thread: 'owner-' + thread[-1] for thread in (A, B, C, D)}
    def serve(server, home, stop):
        with accept(server, stop) as conn:
            start = time.monotonic()
            for obj in incoming(conn, stop):
                now = time.monotonic()
                elapsed = now - start
                if obj is not None:
                    if obj.get('type') == 'request':
                        target = obj.get('params', {}).get('conversationId')
                        request(conn, obj, trace, owners.get(target, 'router'))
                        if obj['method'] == 'initialize':
                            for thread in (A, B, C):
                                hint(conn, thread, source=owners[thread])
                            hint(conn, D, source='another-observer')
                    else:
                        thread, following = valid_follow(obj)
                        trace.append((now, 'follow', thread, following))
                        if following:
                            followed.add(thread)
                            snapshot(conn, thread, thread == A and 'start' in times, owners[thread], 2 if thread == A and 'start' in times else 1)
                        else:
                            followed.discard(thread)
                            times.setdefault('released-' + thread, now)
                if elapsed >= 1 and 'closed' not in times:
                    times['closed'] = now
                    hint(conn, B, source='another-observer')
                    hint(conn, B, source=owners[B], value=False)
                    conn.sendall(frame({'type': 'broadcast', 'sourceClientId': owners[C], 'method': 'client-status-changed',
                        'version': 0, 'params': {'clientId': owners[C], 'status': 'disconnected'}}))
                if elapsed >= 12 and 'start' not in times:
                    times['start'] = now
                    assert A in followed, ('Owner view lost subscription before next turn', trace)
                    snapshot(conn, A, True, owners[A], 2)
    outputs = fixture(exe, 18, (), serve)
    seen = first_working(outputs, A, times['start'])
    assert seen is not None and seen - times['start'] < 1, ('Existing owner start delayed', outputs, trace)
    assert latest_phase(outputs, A, times['start'] - .01) == 'idle', 'View hint guessed a running chat'
    assert 9.5 <= times['released-' + B] - times['closed'] <= 11.5, ('Owner false did not release despite unrelated observer', trace)
    assert 9.5 <= times['released-' + D] - trace[0][0] <= 12, ('Unrelated observer pinned idle history', trace)
    assert 'released-' + C in times and times['released-' + C] - times['closed'] < 16, ('Disconnected owner interest survived re-verification', trace)
    print('Owner view start passed: preserved real push, owner false/disconnect release, unrelated observer excluded', flush=True)


def cleanup(exe, reconnect):
    trace, times = [], {}
    def serve(server, home, stop):
        connections = 2 if reconnect else 1
        for connection in range(connections):
            with accept(server, stop) as conn:
                start = time.monotonic()
                for obj in incoming(conn, stop):
                    now = time.monotonic()
                    elapsed = now - start
                    if obj is not None:
                        if obj.get('type') == 'request':
                            request(conn, obj, trace)
                            if obj['method'] == 'initialize':
                                hint(conn, source='owner' if connection == 0 else 'another-observer')
                        else:
                            thread, following = valid_follow(obj)
                            trace.append((now, 'follow', connection, following))
                            if following:
                                if reconnect and connection == 1 or not reconnect and 'unarchive' in times:
                                    times.setdefault('new-follow', now)
                                snapshot(conn, A)
                            elif 'new-follow' in times:
                                times.setdefault('released', now)
                    if elapsed >= 1 and 'closed' not in times:
                        times['closed'] = now
                        if reconnect:
                            conn.shutdown(2)
                            break
                        conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-archived',
                            'version': 2, 'params': {'hostId': 'local', 'conversationId': A}}))
                    if not reconnect and elapsed >= 2 and 'unarchive' not in times:
                        times['unarchive'] = now
                        conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner', 'method': 'thread-unarchived',
                            'version': 1, 'params': {'hostId': 'local', 'conversationId': A}}))
    fixture(exe, 15, (), serve)
    assert 'released' in times and 9.5 <= times['released'] - times['new-follow'] <= 11.5, ('Old owner interest survived reconnect/archive', reconnect, times, trace)
    print(('Reconnect' if reconnect else 'Archive') + ' interest cleanup passed', flush=True)


def main():
    exe = pathlib.Path(sys.argv[1]).resolve()
    with ThreadPoolExecutor(max_workers=6) as pool:
        checks = [pool.submit(check, exe) for check in (new_root, pending_discovery, pending_catalog, owner_views)]
        checks += [pool.submit(cleanup, exe, reconnect) for reconnect in (False, True)]
        for check in checks:
            check.result()


if __name__ == '__main__':
    main()
