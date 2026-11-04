"""Check the foreground-only canonical read lane using an isolated helper/socket."""
from concurrent.futures import ThreadPoolExecutor
import json, os, pathlib, socket, subprocess, sys, tempfile, threading, time
from optimization_check import accept, incoming, respond, snapshot
from robustness_check import frame, state
from snapshot_helpers import latest_phase

A, B, C, D, W, I = ['50000000-0000-4000-8000-' + str(n).zfill(12) for n in range(1, 7)]


def ready(thread, turn='turn-1', started=1):
    body = state(thread, False, turn, started)
    body['hasUnreadTurn'] = True
    return body


def respond_with_roots(conn, obj, states):
    respond(conn, obj)
    if obj['method'] == 'initialize':
        # Native hints avoid delayed startup lock events adding unrelated idle refreshes.
        for thread in states:
            conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'desktop-fixture',
                'method': 'thread-stream-following-changed', 'version': 1,
                'params': {'hostId': 'local', 'conversationId': thread, 'following': True}}))


def run(exe, duration, states, serve):
    stop = threading.Event()
    errors, outputs = [], []
    launched = threading.Event()
    proc = controller = None
    with tempfile.TemporaryDirectory(prefix='wink-read-latency-', dir='/private/tmp') as tmp:
        home = pathlib.Path(tmp)
        for folder in ('ipc', 'sessions', 'thread-writer-locks'):
            (home / folder).mkdir()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(home / 'ipc/ipc.sock'))
        server.listen()
        server.settimeout(2)
        def command(value):
            launched.wait(2)
            proc.stdin.write(value + '\n')
            proc.stdin.flush()
        def worker():
            try:
                with accept(server, stop) as conn:
                    serve(conn, stop, command)
                    assert stop.is_set(), "Fixture connection ended before measurement completed"
            except Exception as error:
                errors.append(repr(error))
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        def finish():
            if stop.wait(duration):
                return
            stop.set()
            try:
                worker_thread.join(timeout=3)
                assert not worker_thread.is_alive(), 'Fixture did not stop before finishing probe'
                command('finish')
            except Exception as error:
                errors.append(repr(error))
        try:
            # The fixture ends measurement and joins its responder before closing the probe.
            proc = subprocess.Popen([str(exe), '--probe', str(duration + 10)],
                env={**os.environ, 'CODEX_HOME': tmp}, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            launched.set()
            controller = threading.Thread(target=finish, daemon=True)
            controller.start()
            for line in proc.stdout:
                outputs.append((time.monotonic(), json.loads(line)))
            proc.wait(timeout=3)
            controller.join(timeout=3)
            assert not controller.is_alive(), "Fixture controller did not stop"
            assert proc.returncode == 0, proc.stderr.read()
            assert not errors, errors
        finally:
            stop.set()
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
            server.close()
            worker_thread.join(timeout=3)
            if controller is not None:
                controller.join(timeout=3)
        assert not worker_thread.is_alive(), 'Fixture did not stop'
    return outputs


def lifecycle(exe):
    states = {A: ready(A), W: state(W), I: state(I, False)}
    revisions = {thread: 1 for thread in states}
    follows, actions = [], {}
    def serve(conn, stop, command):
        began = time.monotonic()
        first_new_check = None
        for obj in incoming(conn, stop):
            now = time.monotonic()
            elapsed = now - began
            if obj is not None:
                if obj.get('type') == 'request':
                    respond_with_roots(conn, obj, states)
                elif obj.get('method') == 'thread-stream-following-changed':
                    thread = obj['params']['conversationId']
                    if obj['params']['following']:
                        follows.append((now, thread))
                        snapshot(conn, thread, states[thread], revisions[thread])
                        if thread == A and 'new' in actions:
                            first_new_check = first_new_check or now
                elif obj.get('type') == 'client-discovery-response':
                    assert obj['response'] == {'canHandle': False}
                else:
                    raise AssertionError('Unexpected message')
            if elapsed >= .2 and 'background' not in actions:
                actions['background'] = now
                command('background')
            if elapsed >= .45 and 'focus' not in actions:
                actions['focus'] = now
                command('foreground')
            if elapsed >= .6 and 'read' not in actions:
                actions['read'] = now
                states[A]['hasUnreadTurn'] = False  # No read broadcast, file event, or pushed state.
                revisions[A] = 2
            if elapsed >= 2.6 and 'new' not in actions:
                actions['new'] = now
                states[A] = ready(A, 'turn-2', 2)
                revisions[A] = 3
                snapshot(conn, A, states[A], revisions[A])
            if first_new_check and now - first_new_check >= .05 and 'last_background' not in actions:
                actions['last_background'] = now
                command('background')
    outputs = run(exe, 5, states, serve)
    phase = lambda output: next((s['phase'] for s in output['sessions'] if s['id'] == A), None)
    idle_at = next((t for t, output in outputs if t >= actions['read'] and phase(output) == 'idle'), None)
    assert idle_at and idle_at - actions['read'] < 1.3, ('Canonical read was not promptly cleared', follows, outputs)
    assert latest_phase(outputs, A, actions['read'] - .02) == 'ready', 'Focus alone cleared an unread answer'
    assert any(actions['focus'] <= t < actions['focus'] + .35 and thread == A for t, thread in follows), 'Foreground activation did not verify a ready answer immediately'
    read_check_at = next(t for t, thread in follows if thread == A and actions['read'] <= t < actions['new'])
    assert actions['new'] - read_check_at >= 1, 'Zero-ready stop window was too short'
    assert not any(read_check_at < t < actions['new'] and thread == A for t, thread in follows), 'Ready timer continued after the last ready answer cleared'
    new_checks = [t for t, thread in follows if thread == A and t >= actions['new']]
    assert new_checks and new_checks[0] - actions['new'] < 1.3, 'A new answer did not rearm canonical read checks'
    assert latest_phase(outputs, A, actions['last_background']) == 'ready', 'New unread answer was suppressed by the previous read'
    end = actions['new'] + 2.2  # End before ordinary five-second reconciliation.
    assert end - actions['last_background'] > 1.05, 'Background-stop window was too short'
    assert not any(actions['last_background'] + .1 < t < end for t, thread in follows), 'Foreground timer kept querying in the background'
    assert all(sum(thread == target for timestamp, thread in follows if timestamp < end) == 1 for target in (W, I)), 'Working or idle sessions were fast-polled'
    print('Read lifecycle passed: canonical clear, focus safety, rearm, background/zero-ready stop', flush=True)


def budget(exe):
    states = {thread: ready(thread) for thread in (A, B, C, D)}
    states.update({W: state(W), I: state(I, False)})
    extra, initial = [], set()
    actions = {}
    stalled = None
    def serve(conn, stop, command):
        nonlocal stalled
        began = time.monotonic()
        for obj in incoming(conn, stop):
            now = time.monotonic()
            if obj is not None:
                if obj.get('type') == 'request':
                    respond_with_roots(conn, obj, states)
                elif obj.get('method') == 'thread-stream-following-changed':
                    thread = obj['params']['conversationId']
                    if obj['params']['following']:
                        if thread in initial:
                            extra.append((now, thread))
                            stalled = stalled or thread
                        else:
                            initial.add(thread)
                        if thread != stalled:
                            snapshot(conn, thread, states[thread])
                elif obj.get('type') == 'client-discovery-response':
                    assert obj['response'] == {'canHandle': False}
                else:
                    raise AssertionError('Unexpected message')
            if now - began >= .25 and 'burst' not in actions:
                actions['burst'] = now
                command('\n'.join(['refresh', 'foreground'] * 15))
    run(exe, 4.8, states, serve)
    assert 4 <= len(extra) <= 5, ('Foreground ready checks lost their bounded cadence', extra)
    assert all(b[0] - a[0] >= .9 for a, b in zip(extra, extra[1:])), ('Many ready sessions bypassed the global one-per-second budget', extra)
    assert not any(thread in (W, I) for _, thread in extra), 'Working or idle sessions received foreground read checks'
    assert sum(thread == stalled for _, thread in extra) == 1, 'A stalled snapshot was queried again while in flight'
    assert set(thread for _, thread in extra) == {A, B, C, D}, 'Oldest-checked scheduling starved a healthy ready owner'
    print('Read budget passed: one global query per second, fairness, one-shot cap, in-flight coalescing', flush=True)


def main():
    exe = pathlib.Path(sys.argv[1]).resolve()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in [pool.submit(check, exe) for check in (lifecycle, budget)]:
            result.result()


if __name__ == '__main__':
    main()
