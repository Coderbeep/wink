"""Exercise conservative metadata cleanup without reading or deleting real chats."""
from pathlib import Path
import json, os, select, sqlite3, subprocess, sys, tempfile

A = '80000000-0000-4000-8000-000000000001'
B = '80000000-0000-4000-8000-000000000002'
C = '80000000-0000-4000-8000-000000000003'


class Store:
    def __init__(self, home, ids=(A,), archived=False, rollout_id=None, wal=True):
        self.home = Path(home)
        for folder in ('sessions/2026/11/05', 'archived_sessions', 'thread-writer-locks', 'rollout-migrations'):
            (self.home / folder).mkdir(parents=True, exist_ok=True)
        self.dbpath = self.home / 'state_5.sqlite'
        self.db = sqlite3.connect(self.dbpath, check_same_thread=False)
        if wal:
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0)')
        self.paths = {}
        for thread in ids:
            filename = 'rollout-2026-11-05T12-00-00-' + (rollout_id or thread) + '.jsonl'
            path = self.home / ('archived_sessions' if archived else 'sessions/2026/11/05') / filename
            self.put(thread, path)

    def put(self, thread, path=None):
        path = Path(path or self.paths[thread])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.paths[thread] = path
        self.db.execute('INSERT OR REPLACE INTO threads(id,rollout_path) VALUES (?,?)', (thread, str(path)))
        self.db.commit()

    def remove(self, thread, row=True, file=True):
        if row:
            self.db.execute('DELETE FROM threads WHERE id=?', (thread,))
            self.db.commit()
        if file:
            self.paths[thread].unlink()

    def close(self):
        self.db.close()


class Catalog:
    def __init__(self, exe, home):
        self.proc = subprocess.Popen([str(exe), str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def call(self, op, thread=A):
        self.proc.stdin.write(op + ' ' + thread + '\n')
        self.proc.stdin.flush()
        assert select.select([self.proc.stdout], [], [], 3)[0], 'Metadata check blocked'
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read()
        return json.loads(line)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=3)
        assert self.proc.returncode == 0, self.proc.stderr.read()


def check(exe, mode):
    with tempfile.TemporaryDirectory(prefix='wink-catalog-', dir='/private/tmp') as tmp:
        revert_ids = {
            'revert-distinct': B + '_' + C,
            'revert-invalid-first': 'invalid_' + B,
            'revert-invalid-second': A + '_invalid',
            'revert-invalid-extra': A + '_' + B + '_' + C,
            'revert-invalid-separator': A + '__' + B,
        }
        rollout_id = revert_ids.get(mode, A + '_' + B) if mode.startswith('revert-') else B if mode == 'distinct-rollout' else None
        store = Store(tmp, archived=mode in ('archive-mirror', 'archive-deleted', 'archive-compressed',
                      'revert-archive-deleted', 'revert-active-mirror', 'revert-archive-compressed'), rollout_id=rollout_id,
                      wal=mode != 'busy')
        catalog = Catalog(exe, tmp)
        original = store.paths[A]
        cleanup = []
        try:
            if mode != 'unseen':
                if mode in ('compressed-baseline', 'revert-compressed-baseline', 'revert-archive-compressed'):
                    compressed = original.with_suffix('.jsonl.zst')
                    original.rename(compressed)
                    store.paths[A] = original = compressed
                    store.db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(compressed), A)); store.db.commit()
                if mode == 'missing-baseline-file': original.unlink()
                if mode == 'invalid-schema':
                    store.db.execute('ALTER TABLE threads RENAME TO old_threads')
                    store.db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT)')
                    store.db.execute('INSERT INTO threads SELECT id,rollout_path FROM old_threads'); store.db.commit()
                if mode == 'composite-key':
                    store.db.execute('ALTER TABLE threads RENAME TO old_threads')
                    store.db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, PRIMARY KEY(id,rollout_path))')
                    store.db.execute('INSERT INTO threads SELECT id,rollout_path FROM old_threads'); store.db.commit()
                if mode in ('nul-path', 'long-path'):
                    malformed = str(original) + '\x00suffix' if mode == 'nul-path' else '/' + 'x' * 4096
                    store.db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (malformed, A)); store.db.commit()
                if mode == 'outside-path':
                    outside = Path(tmp).parent / (Path(tmp).name + '-outside.jsonl')
                    outside.touch(); cleanup.append(outside)
                    store.db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(outside), A)); store.db.commit()
                if mode == 'symlink':
                    target = store.home / 'target'; target.touch()
                    original.unlink(); original.symlink_to(target)
                catalog.call('remember')
            if mode == 'present':
                assert catalog.call('orphan') is False
                assert store.db.execute('SELECT id FROM threads').fetchall() == [(A,)]
                return
            if mode == 'row-only': store.remove(A, row=False)
            elif mode == 'file-only': store.remove(A, file=False)
            else:
                store.remove(A, file=original.exists() or original.is_symlink())
            if mode in ('lock', 'journal'):
                path = store.home / ('thread-writer-locks/' + A + '.lock' if mode == 'lock' else 'rollout-migrations/' + A + '.pending')
                path.touch()
            elif mode in ('compressed', 'revert-compressed-retained'):
                original.with_suffix('.jsonl.zst').touch()
            elif mode in ('active-mirror', 'revert-archive-mirror'):
                (store.home / 'archived_sessions' / original.name).touch()
            elif mode in ('archive-mirror', 'revert-active-mirror'):
                (store.home / 'sessions/2026/11/05' / original.name).touch()
            elif mode == 'archive-compressed':
                (store.home / 'sessions/2026/11/05' / (original.name + '.zst')).touch()
            elif mode == 'missing-db':
                store.close(); store.dbpath.rename(store.home / 'saved.sqlite')
            elif mode == 'replacement':
                store.close(); store.dbpath.rename(store.home / 'saved.sqlite')
                replacement = sqlite3.connect(store.dbpath)
                replacement.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0)')
                replacement.close()
            elif mode == 'schema-change':
                store.db.execute('ALTER TABLE threads ADD COLUMN changed INTEGER'); store.db.commit()
            elif mode == 'user-version-change':
                store.db.execute('PRAGMA user_version=1'); store.db.commit()
            elif mode == 'unknown-version':
                (store.home / 'state_6.sqlite').touch()
            elif mode in ('legacy-ambiguous', 'legacy-other-version'):
                (store.home / 'sqlite').mkdir()
                (store.home / 'sqlite' / ('state_4.sqlite' if mode == 'legacy-other-version' else 'state_5.sqlite')).touch()
            elif mode == 'busy':
                store.db.execute('BEGIN EXCLUSIVE')
            elif mode == 'unreadable' and os.geteuid() != 0:
                store.dbpath.chmod(0)
            elif mode == 'forget':
                catalog.call('forget')
            elif mode == 'missing-root':
                (store.home / 'sessions').rename(store.home / 'sessions-offline')
            elif mode == 'replaced-root':
                (store.home / 'sessions').rename(store.home / 'sessions-offline')
                (store.home / 'sessions').mkdir()
            elif mode == 'missing-locks':
                (store.home / 'thread-writer-locks').rmdir()
            expected = mode in ('deleted', 'distinct-rollout', 'archive-deleted', 'compressed-baseline',
                'revert-deleted', 'revert-distinct', 'revert-archive-deleted',
                'revert-compressed-baseline', 'revert-archive-compressed')
            if mode == 'unreadable' and os.geteuid() == 0:
                print('SKIP root permission bypass', flush=True)
                return
            assert catalog.call('orphan') is expected, mode
            if mode == 'busy':
                store.db.rollback()
                assert catalog.call('orphan') is True, 'Busy read must not destroy a valid baseline'
            if mode == 'missing-db':
                assert not store.dbpath.exists(), 'Read-only lookup created a database'
        finally:
            if mode == 'unreadable' and store.dbpath.exists(): store.dbpath.chmod(0o600)
            catalog.close()
            try: store.close()
            except sqlite3.ProgrammingError: pass
            for path in cleanup: path.unlink(missing_ok=True)


def witnessed_original(exe):
    with tempfile.TemporaryDirectory(prefix='wink-catalog-revert-', dir='/private/tmp') as tmp:
        store, catalog = Store(tmp), Catalog(exe, tmp)
        try:
            original = store.paths[A]
            catalog.call('remember')
            reverted = original.with_name(original.stem + '_' + B + '.jsonl')
            store.put(A, reverted)
            catalog.call('remember')
            store.remove(A)
            assert catalog.call('orphan') is False, 'A witnessed original rollout must still veto cleanup after revert'
            original.unlink()
            assert catalog.call('orphan') is True, 'Cleanup should work once all witnessed rollout variants disappear'
        finally:
            catalog.close(); store.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    modes = ('present', 'deleted', 'distinct-rollout', 'unseen', 'missing-baseline-file', 'row-only',
        'file-only', 'lock', 'journal', 'compressed', 'active-mirror', 'archive-mirror', 'missing-db',
        'replacement', 'schema-change', 'unknown-version', 'legacy-ambiguous', 'busy', 'unreadable',
        'outside-path', 'symlink', 'forget', 'missing-root', 'replaced-root', 'missing-locks',
        'archive-deleted', 'compressed-baseline', 'archive-compressed', 'invalid-schema', 'user-version-change',
        'composite-key', 'nul-path', 'long-path', 'legacy-other-version',
        'revert-deleted', 'revert-distinct', 'revert-archive-deleted', 'revert-compressed-baseline',
        'revert-archive-compressed', 'revert-compressed-retained', 'revert-archive-mirror', 'revert-active-mirror',
        'revert-invalid-first', 'revert-invalid-second', 'revert-invalid-extra', 'revert-invalid-separator')
    for mode in modes:
        check(exe, mode)
        print('Catalog guard passed: ' + mode, flush=True)
    witnessed_original(exe)
    print('Catalog guard passed: original witnessed before revert', flush=True)
    print(str(len(modes) + 1) + ' metadata guard cases passed', flush=True)


if __name__ == '__main__':
    main()
