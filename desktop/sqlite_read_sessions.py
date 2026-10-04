"""Thread confined, bounded leases of immutable SQLite projections.

Each nesting depth has its own TEMP namespace. A lease ends in autocommit,
so an idle reader cannot hold a transaction across UI events or a writer.
Only source size/mtime (including WAL) are checked; no national data is hashed.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import local


class ReadSessions:
    def __init__(self, path, initialize=None, *, scratch_tables=(), max_idle=4):
        self.path = Path(path).resolve()
        self.initialize = initialize
        self.scratch_tables = tuple(scratch_tables)
        self.max_idle = max_idle
        self._local = local()

    def fingerprint(self):
        def stat(path):
            try:
                value = path.stat()
                return value.st_size, value.st_mtime_ns
            except FileNotFoundError:
                return None
        return stat(self.path), stat(Path(str(self.path) + '-wal'))

    @contextmanager
    def connect(self):
        state = self._local
        if not hasattr(state, 'slots'):
            state.slots, state.depth = [], 0
        depth, signature = state.depth, self.fingerprint()
        if depth < len(state.slots):
            db, previous = state.slots[depth]
            if previous != signature:
                db.close()
                state.slots[depth] = None, None
                db = None
        else:
            db = None
        if db is None:
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                 isolation_level=None)
            try:
                db.execute('PRAGMA cache_size=-4096')
                if self.initialize:
                    self.initialize(db)
            except BaseException:
                db.close()
                raise
            if depth < self.max_idle:
                if depth == len(state.slots):
                    state.slots.append((db, signature))
                else:
                    state.slots[depth] = db, signature
        state.depth += 1
        try:
            yield db
        finally:
            state.depth -= 1
            try:
                if db.in_transaction:
                    db.rollback()
                present = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_temp_master WHERE type='table'")} if self.scratch_tables else set()
                for name in set(self.scratch_tables) & present:
                    # Names are internal constants, never user input.
                    db.execute('DROP TABLE IF EXISTS temp."' + name.replace('"', '""') + '"')
            finally:
                if depth >= self.max_idle:
                    db.close()

    def close_current_thread(self):
        state = self._local
        if getattr(state, 'depth', 0):
            raise RuntimeError('Cannot close an active SQLite read lease')
        for db, _ in getattr(state, 'slots', ()):
            if db is not None:
                db.close()
        state.slots = []
