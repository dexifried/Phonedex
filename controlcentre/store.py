"""Thread-safe SQLite boundary shared by the dashboard and opt-in core hooks."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from .crypto import private_directory, private_file, _reject_links


def now():
    return datetime.now(timezone.utc).isoformat()


CALL_DEFAULTS = dict(contact='', number_masked='', direction='outbound', agent_id=None,
                     agent_name='', provider='', state='unknown', started_at=None,
                     ended_at=None, duration_seconds=None, outcome=None, task_success=None,
                     cost=None, cost_kind=None, transcript=[], timeline=[], results=[], controls=[])
TERMINAL = {'completed', 'failed', 'cancelled', 'ended', 'rejected', 'busy', 'no_answer', 'interrupted'}
PROGRESS = {'unknown': 0, 'created': 1, 'queued': 1, 'dialing': 2, 'ringing': 3, 'connected': 4, 'active': 4, 'answered': 4, 'ending': 5}
CALL_FIELDS = set(CALL_DEFAULTS) | {'id', 'updated_at', 'error_code', 'transcript_enabled', 'heartbeat_at', 'backend_provider_id'}
EVENT_FIELDS = {'id', 'call_id', 'kind', 'type', 'message', 'at', 'level', 'source', 'state', 'code'}


class Store:
    def __init__(self, data_dir: Path):
        self.path = private_directory(Path(data_dir)) / 'control.sqlite3'
        private_file(self.path, create=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS changes(revision INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS transcript(call_id TEXT,event_id TEXT,speaker TEXT,text TEXT,ms INTEGER,at TEXT,PRIMARY KEY(call_id,event_id));
                CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,call_id TEXT,kind TEXT,payload TEXT,state TEXT,at TEXT);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connect(self):
        private_file(self.path)
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                # SQLite-created sidecars inherit the protected directory ACL.
                # Rewriting the ACL while SQLite deletes one races Windows
                # delete-pending handles. Validate links without rewriting it.
                _reject_links(Path(str(self.path) + suffix),missing=True,check_owner=True)
            except FileNotFoundError:
                # SQLite removes optional sidecars on the last connection close.
                # ACL and ownership failures still fail closed.
                pass
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _change(self, db):
        db.execute('INSERT INTO changes(at) VALUES(?)', (now(),))
        db.execute('DELETE FROM changes WHERE revision < (SELECT COALESCE(MAX(revision),0)-1000 FROM changes)')

    def put(self, kind, data, object_id=None):
        item = dict(data)
        item['id'] = object_id or item.get('id') or uuid.uuid4().hex
        with self._lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?)', (kind, item['id'], json.dumps(item, allow_nan=False)))
            self._change(db)
        return item

    def get(self, kind, object_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM objects WHERE kind=? AND id=?', (kind, object_id)).fetchone()
        return json.loads(row['data']) if row else None

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM objects WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def delete(self, kind, object_id):
        with self._lock, self.connect() as db:
            found = db.execute('DELETE FROM objects WHERE kind=? AND id=?', (kind, object_id)).rowcount
            if found:
                self._change(db)
        return bool(found)

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT data FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['data']) if row else default

    def set_meta(self, key, value):
        with self._lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, allow_nan=False)))
            self._change(db)

    def record_call(self, data):
        if not data.get('id'):
            raise ValueError('Call id required')
        with self._lock, self.connect() as db:
            # Hooks and dashboard run in separate processes. Serialize the full
            # read/merge/write transaction, not only this instance's threads.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT data FROM objects WHERE kind='calls' AND id=?", (data['id'],)).fetchone()
            old = json.loads(row['data']) if row else json.loads(json.dumps(CALL_DEFAULTS))
            item = {k: v for k, v in data.items() if k in CALL_FIELDS}
            if (old['state'] in TERMINAL or
                    (item.get('state') not in TERMINAL and PROGRESS.get(item.get('state'), 0) < PROGRESS.get(old['state'], 0))):
                item.pop('state', None)
            old.update(item)
            old['updated_at'] = now()
            old['id'] = data['id']
            db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?)', ('calls', data['id'], json.dumps(old, allow_nan=False)))
            self._change(db)
        return self.get_call(data['id'])

    def get_call(self, call_id):
        item = self.get('calls', call_id)
        if item is None:
            return None
        with self.connect() as db:
            rows = db.execute('SELECT event_id,speaker,text,ms,at FROM transcript WHERE call_id=? ORDER BY ms,rowid', (call_id,)).fetchall()
        if rows:
            item['transcript'] = [dict(row) for row in rows]
        return item

    def calls(self):
        return [self.get_call(c['id']) for c in self.list('calls')]

    def add_event(self, data):
        item = {k: v for k, v in data.items() if k in EVENT_FIELDS and isinstance(v, (str, int, float, bool, type(None)))}
        item.setdefault('at', now())
        return self.put('events', item)

    def events(self, limit=100):
        return self.list('events')[:limit]

    def transcript(self, call_id, speaker, text, ms, event_id):
        if speaker not in {'user', 'assistant', 'recipient', 'agent', 'system', 'operator'}:
            return False
        if not self.get('calls', call_id):
            self.record_call({'id': call_id})
        with self._lock, self.connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO transcript VALUES(?,?,?,?,?,?)', (call_id, str(event_id), speaker, text, int(ms), now())).rowcount
            if inserted:
                self._change(db)
        return bool(inserted)

    def queue_command(self, call_id, kind, payload):
        item = dict(id=uuid.uuid4().hex, call_id=call_id, kind=kind, payload=payload, state='pending', at=now())
        with self._lock, self.connect() as db:
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)', (item['id'], call_id, kind, json.dumps(payload), 'pending', item['at']))
            self._change(db)
        return item

    def pending_commands(self, call_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM commands WHERE call_id=? AND state='pending' ORDER BY rowid", (call_id,)).fetchall()
        return [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]

    def ack_command(self, command_id, state):
        if state not in {'completed', 'failed', 'rejected', 'acknowledged', 'sent'}:
            raise ValueError('Unsupported command state')
        with self._lock, self.connect() as db:
            db.execute('UPDATE commands SET state=? WHERE id=?', (state, command_id))
            self._change(db)

    def revision(self):
        with self.connect() as db:
            return db.execute('SELECT COALESCE(MAX(revision),0) FROM changes').fetchone()[0]

    def changes_since(self, revision, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT revision,at FROM changes WHERE revision>? ORDER BY revision LIMIT ?', (revision, limit))]
