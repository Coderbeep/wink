"""Read-only adversarial desktop IPC fixtures; never connects to the real Codex socket."""
import argparse, concurrent.futures, json, os, pathlib, socket, struct, subprocess, tempfile, threading, time

A = '30000000-0000-4000-8000-000000000001'
B = '30000000-0000-4000-8000-000000000002'
DURATIONS = {'missed_discovery': 9, 'discovery_timeout': 19, 'old_snapshot': 13,
             'silent_owner': 22, 'input_array_reply': 8, 'revision_reset': 13,
             'archive_reconnect': 9, 'state_identity_mismatch': 8, 'removed_candidate': 12,
             'acknowledgement_expiry': 13}

def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack('<I', len(data)) + data

def state(thread, active=True, turn='turn-1', started=1):
    return {'id': thread, 'title': 'Adversarial fixture', 'originator': 'codex_work_desktop',
            'source': 'vscode', 'threadSource': 'user', 'resumeState': 'resumed',
            'threadRuntimeStatus': {'type': 'active' if active else 'idle', 'activeFlags': []},
            'hasUnreadTurn': False, 'requests': [], 'turnHistory': {'kind': 'canonical', 'history': {'entitiesByKey': {
                'entity': {'turnId': turn, 'status': 'inProgress' if active else 'completed',
                           'turnStartedAtMs': started, 'items': []}}, 'islands': [{'entries': [{'value': 'entity'}]}]}}}

def run(executable, case, duration=None):
    duration = duration or DURATIONS[case]
    stop = threading.Event(); errors = []; trace = []; outputs = []
    with tempfile.TemporaryDirectory(prefix='dot-adversarial-', dir='/private/tmp') as tmp:
        home = pathlib.Path(tmp)
        for folder in ('ipc', 'sessions', 'thread-writer-locks'):
            (home / folder).mkdir()
        threads = (A, B) if case in ('missed_discovery', 'discovery_timeout', 'silent_owner', 'state_identity_mismatch', 'removed_candidate') else (A,)
        for thread in threads:
            (home / 'thread-writer-locks' / (thread + '.lock')).touch()
        states = {thread: state(thread) for thread in threads}
        revisions = {thread: 1 for thread in threads}
        if case == 'old_snapshot':
            states[A] = state(A, False, turn='turn-new', started=2); revisions[A] = 10
        if case == 'revision_reset': revisions[A] = 10
        if case == 'acknowledgement_expiry':
            states[A] = state(A, False)
            states[A]['hasUnreadTurn'] = True
        if case == 'input_array_reply':
            turn = states[A]['turnHistory']['history']['entitiesByKey']['entity']
            turn['items'] = [
                {'type': 'agentMessage', 'id': 'ask', 'questions': [{'title': 'Fixture', 'options': []}]},
                {'type': 'steeringUserMessage', 'status': 'accepted', 'input': []}]
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(home / 'ipc' / 'ipc.sock')); server.listen(); server.settimeout(2)
        began = time.monotonic()
        discovery_attempts = {thread: 0 for thread in threads}
        snapshot_attempts = {thread: 0 for thread in threads}
        action_sent = False
        def serve():
            nonlocal action_sent
            try:
                while not stop.is_set():
                    try: conn, _ = server.accept()
                    except socket.timeout: continue
                    conn.settimeout(.03); buf = b''
                    def snapshot(thread):
                        snapshot_attempts[thread] += 1
                        trace.append([round(time.monotonic()-began, 2), 'snapshot-request', thread])
                        if case == 'silent_owner' and thread == B and time.monotonic()-began > 1: return
                        body = states[thread]
                        if case == 'state_identity_mismatch' and thread == B:
                            body = {**body, 'id': A}
                        conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                            'method': 'thread-stream-state-changed', 'version': 11,
                            'params': {'hostId': 'local', 'conversationId': thread, 'change': {
                                'type': 'snapshot', 'revision': revisions[thread], 'conversationState': body}}}))
                    while not stop.is_set():
                        elapsed = time.monotonic()-began
                        if elapsed > 1 and not action_sent:
                            action_sent = True
                            if case == 'removed_candidate':
                                (home / 'thread-writer-locks' / (B + '.lock')).unlink()
                                trace.append([round(elapsed, 2), 'lock-removed', B])
                            elif case == 'acknowledgement_expiry':
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'desktop-coordinator',
                                    'method': 'thread-read-state-changed', 'version': 3,
                                    'params': {'hostId': 'local', 'conversationId': A, 'hasUnreadTurn': False}}))
                                # Canonical true could be stale, or a mark-unread whose event was lost.
                                # No canonical false or global true is ever delivered.
                                snapshot(A)
                            elif case == 'archive_reconnect':
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                                    'method': 'thread-archived', 'version': 2,
                                    'params': {'hostId': 'local', 'conversationId': A}}))
                                # User unarchives offline; same lock persists and no unarchive event is delivered.
                                conn.shutdown(socket.SHUT_RDWR); conn.close(); break
                            elif case == 'old_snapshot':
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                                    'method': 'thread-stream-state-changed', 'version': 11,
                                    'params': {'hostId': 'local', 'conversationId': A, 'change': {
                                        'type': 'snapshot', 'revision': 9, 'conversationState': state(A)}}}))
                            elif case == 'revision_reset':
                                states[A] = state(A, False); revisions[A] = 1
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                                    'method': 'thread-stream-state-changed', 'version': 11,
                                    'params': {'hostId': 'local', 'conversationId': A, 'change': {
                                        'type': 'snapshot', 'revision': 1, 'conversationState': states[A]}}}))
                            elif case == 'input_array_reply':
                                key = json.dumps(['request_user_input_async', 'ask', 0], separators=(',', ':'))
                                text = '<send_user_message_question_reply>\n' + json.dumps([{
                                    'questionItemId': key, 'question': 'Fixture', 'answer': 'Done'}]) + '\n</send_user_message_question_reply>'
                                parts = [{'type': 'text', 'text': text}]
                                states[A]['turnHistory']['history']['entitiesByKey']['entity']['items'][1]['input'] = parts
                                conn.sendall(frame({'type': 'broadcast', 'sourceClientId': 'owner',
                                    'method': 'thread-stream-state-changed', 'version': 11,
                                    'params': {'hostId': 'local', 'conversationId': A, 'change': {
                                        'type': 'patches', 'baseRevision': 1, 'revision': 2, 'patches': [{
                                            'op': 'replace', 'path': ['turnHistory', 'history', 'entitiesByKey', 'entity', 'items', 1, 'input'], 'value': parts}]}}}))
                                revisions[A] = 2
                        try:
                            chunk = conn.recv(65536)
                            if not chunk: break
                            buf += chunk
                        except socket.timeout: continue
                        while len(buf) >= 4:
                            size = struct.unpack('<I', buf[:4])[0]
                            if len(buf) < size+4: break
                            obj = json.loads(buf[4:size+4]); buf = buf[size+4:]
                            kind = obj.get('type'); method = obj.get('method')
                            if kind == 'request':
                                assert method in ('initialize', 'thread-owner-discovery'), ('unexpected mutation', method)
                                if method == 'initialize':
                                    conn.sendall(frame({'type': 'response', 'requestId': obj['requestId'],
                                        'resultType': 'success', 'handledByClientId': 'router', 'result': {'clientId': 'observer'}}))
                                else:
                                    thread = obj['params']['conversationId']; discovery_attempts[thread] += 1
                                    trace.append([round(elapsed, 2), 'discovery', thread, discovery_attempts[thread]])
                                    if case == 'discovery_timeout' and thread == B and discovery_attempts[thread] == 1: continue
                                    absent = thread == B and (case == 'removed_candidate' or case == 'missed_discovery' and elapsed < 1)
                                    conn.sendall(frame({'type': 'response', 'requestId': obj['requestId'],
                                        'resultType': 'error' if absent else 'success', 'error': 'no-client-found' if absent else None,
                                        'handledByClientId': 'owner', 'result': {}}))
                            elif method == 'thread-stream-following-changed':
                                trace.append([round(elapsed, 2), 'follow', obj['params']['conversationId'], obj['params']['following']])
                                if obj['params']['following']: snapshot(obj['params']['conversationId'])
                            elif kind == 'client-discovery-response':
                                assert obj['response'] == {'canHandle': False}
                            else: raise AssertionError(('unexpected write', method))
                    conn.close()
            except Exception as exc:
                if not stop.is_set(): errors.append(repr(exc))
        worker = threading.Thread(target=serve, daemon=True); worker.start()
        proc = subprocess.Popen([str(executable), '--probe', str(duration)],
            env={**os.environ, 'CODEX_HOME': tmp}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            outputs.append([round(time.monotonic()-began, 2), json.loads(line)])
        proc.wait(timeout=3); stderr = proc.stderr.read(); stop.set(); server.close(); worker.join(timeout=1)
        assert proc.returncode == 0, stderr
        assert not errors, errors
    def latest(thread, cutoff):
        found = None
        for timestamp, output in outputs:
            if timestamp > cutoff: break
            found = next((row['phase'] for row in output['sessions'] if row['id'] == thread), None)
            if not output['connected']: found = 'disconnected'
        return found
    expected = True; reason = ''
    if case in ('missed_discovery', 'discovery_timeout'):
        expected = latest(B, duration) == 'working'; reason = 'Background desktop owner must be found after a missed discovery signal'
    elif case == 'removed_candidate':
        after_catalog = [row for row in trace if row[1] == 'discovery' and row[2] == B and row[0] >= 4.5]
        # Allow one delayed filesystem event or in-flight lookup at the first catalog tick.
        expected = latest(A, duration) == 'working' and len(after_catalog) <= 1
        reason = 'A deleted unvalidated candidate must not keep recurring owner queries forever'
    elif case == 'acknowledgement_expiry':
        expected = latest(A, .9) == 'ready' and latest(A, 5.5) == 'idle' and latest(A, duration) == 'ready'
        reason = 'Read acknowledgement must protect its grace period, then yield to fresh canonical truth'
    elif case == 'old_snapshot':
        expected = latest(A, duration) == 'idle'; reason = 'Older same-owner snapshot must be re-verified against current authoritative state'
    elif case == 'silent_owner':
        expected = latest(B, duration) in ('unknown', 'disconnected') and latest(A, duration) == 'working'; reason = 'Silent B must lose its authoritative working count while healthy A stays working'
    elif case == 'archive_reconnect':
        expected = latest(A, duration) == 'working'; reason = 'Archive tombstone must not hide an offline-unarchived working owner forever'
    elif case == 'state_identity_mismatch':
        expected = latest(A, duration) == 'working' and latest(B, duration) != 'working'; reason = 'One invalid root snapshot must not repeatedly drop a healthy root'
    elif case == 'revision_reset':
        expected = latest(A, duration) == 'idle'; reason = 'A same-client revision reset must recover through an explicit current snapshot'
    elif case == 'input_array_reply':
        expected = latest(A, duration) == 'working'; reason = 'Accepted reply content-array replacement must clear pending input'
    return {'case': case, 'passed': expected, 'reason': reason, 'lastA': latest(A, duration), 'lastB': latest(B, duration),
            'discoveryAttempts': discovery_attempts, 'snapshotAttempts': snapshot_attempts, 'trace': trace, 'outputs': outputs}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=pathlib.Path)
    parser.add_argument('--case', choices=list(DURATIONS), help='Run one scenario instead of the full suite')
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        parser.error('executable does not exist: ' + str(executable))
    cases = [args.case] if args.case else list(DURATIONS)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(cases))) as pool:
        futures = {pool.submit(run, executable, case): case for case in cases}
        for future in concurrent.futures.as_completed(futures):
            case = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {'case': case, 'passed': False, 'error': repr(error)}
            results.append(result)
            if result['passed']:
                print('PASS ' + case, flush=True)
            else:
                print('FAIL ' + case + ': ' + json.dumps(result, separators=(',', ':')), flush=True)
    print(str(sum(result['passed'] for result in results)) + '/' + str(len(results)) + ' robustness checks passed', flush=True)
    return 0 if all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
