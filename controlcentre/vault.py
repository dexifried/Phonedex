"""Isolated personal vault. Only explicit, short-lived grants permit disclosure."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .crypto import ProtectedCipher, VaultIntegrityError, VaultLockedError, private_directory, private_file, _reject_links

_MASK = '••••••••'
_FORCED_SENSITIVE = re.compile(
    r'(government|health|insurance|financial|passport|licen[cs]e|social.?security|social.?insurance|'
    r'\b(sin|ssn|nhs|sinumber|ssnumber|iban|cvv|cvc|pin|ohip|dob)\b|bank|routing|credit|debit|tax|medicare|'
    r'account.?number|card.?number|birth|password|secret|national.?id|identity|patient|medical)', re.I)


def _identifier() -> str:
    return str(uuid.uuid4())


def _text(value, *, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('A nonempty text value within the size limit is required')
    return value


class Vault:
    def __init__(self, data_dir: Path, passphrase: str | None = None):
        self.data_dir = private_directory(Path(data_dir))
        self.path = self.data_dir / 'vault.sqlite3'
        self.cipher = ProtectedCipher(self.data_dir / 'vault-keys', passphrase)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS fields (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    key TEXT NOT NULL, label TEXT NOT NULL, sensitivity TEXT NOT NULL,
                    ciphertext TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(profile_id, key));
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY, field_id TEXT NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
                    scope TEXT NOT NULL, expires_at REAL NOT NULL, revoked_at REAL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    event TEXT NOT NULL, decision TEXT NOT NULL, field_id TEXT, grant_id TEXT);
                CREATE INDEX IF NOT EXISTS grants_field ON grants(field_id);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(fields)')}
            if 'category' not in columns:
                db.execute("ALTER TABLE fields ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")

    @contextmanager
    def _db(self, *, transaction=False):
        private_directory(self.data_dir)
        private_file(self.path, create=True)
        for suffix in ('-journal', '-wal', '-shm'):
            try:_reject_links(Path(str(self.path) + suffix),missing=True,check_owner=True)
            except FileNotFoundError:pass
        db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('PRAGMA journal_mode=DELETE')
        try:
            if transaction:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _audit(db, event, decision='allow', field_id=None, grant_id=None):
        # Deliberately omit caller-supplied scope, labels and values. Audit fields
        # are fixed action names and generated opaque IDs, never secret-bearing text.
        db.execute('INSERT INTO audit(timestamp,event,decision,field_id,grant_id) VALUES(?,?,?,?,?)',
                   (time.time(), event, decision, field_id, grant_id))

    @staticmethod
    def _field(row):
        result = dict(row)
        result.pop('ciphertext', None)
        result['value_masked'] = _MASK
        result['has_value'] = True
        return result

    def profiles(self):
        with self._db() as db:
            return [dict(row) for row in db.execute(
                'SELECT p.*,COUNT(f.id) AS field_count FROM profiles p LEFT JOIN fields f ON f.profile_id=p.id '
                'GROUP BY p.id ORDER BY p.created_at')]

    def create_profile(self, name):
        name = _text(name)
        result = {'id': _identifier(), 'name': name, 'created_at': time.time(), 'field_count': 0}
        with self._db(transaction=True) as db:
            db.execute('INSERT INTO profiles(id,name,created_at) VALUES(?,?,?)',
                       (result['id'], name, result['created_at']))
            self._audit(db, 'create_profile')
        return result

    def put_field(self, profile_id, key, label, value, sensitivity='sensitive'):
        key, label = _text(key), _text(label)
        value = _text(value, maximum=16384)
        categories = ('personal', 'contact', 'address', 'government', 'health', 'insurance', 'financial', 'other')
        category = sensitivity if sensitivity in categories else 'other'
        if sensitivity in categories:
            sensitivity = 'sensitive'
        if sensitivity not in ('ordinary', 'sensitive'):
            raise ValueError('Sensitivity must be ordinary or sensitive')
        normalized = re.sub(r'[_\-]+', ' ', key + ' ' + label)
        if _FORCED_SENSITIVE.search(normalized):
            sensitivity = 'sensitive'
        with self._db(transaction=True) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile_id,)).fetchone():
                raise KeyError('Profile not found')
            existing = db.execute('SELECT id FROM fields WHERE profile_id=? AND key=?', (profile_id, key)).fetchone()
            field_id = existing['id'] if existing else _identifier()
            encrypted = self.cipher.encrypt(value, 'field:' + field_id)
            now = time.time()
            if existing:
                db.execute('UPDATE grants SET revoked_at=? WHERE field_id=? AND revoked_at IS NULL', (now, field_id))
                db.execute('UPDATE fields SET label=?,sensitivity=?,ciphertext=?,updated_at=?,category=? WHERE id=?',
                           (label, sensitivity, encrypted, now, category, field_id))
            else:
                db.execute('INSERT INTO fields(id,profile_id,key,label,sensitivity,ciphertext,created_at,updated_at,category) VALUES(?,?,?,?,?,?,?,?,?)',
                           (field_id, profile_id, key, label, sensitivity, encrypted, now, now, category))
            self._audit(db, 'update_field' if existing else 'create_field', field_id=field_id)
            row = db.execute('SELECT * FROM fields WHERE id=?', (field_id,)).fetchone()
            return self._field(row)

    def fields(self, profile_id):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile_id,)).fetchone():
                raise KeyError('Profile not found')
            return [self._field(row) for row in db.execute(
                'SELECT * FROM fields WHERE profile_id=? ORDER BY created_at', (profile_id,))]

    def reveal(self, field_id):
        error, value = None, None
        with self._db(transaction=True) as db:
            row = db.execute('SELECT ciphertext FROM fields WHERE id=?', (field_id,)).fetchone()
            if row is None:
                self._audit(db, 'reveal', 'deny')
                error = KeyError('Field not found')
            else:
                try:
                    value = self.cipher.decrypt(row['ciphertext'], 'field:' + field_id)
                    self._audit(db, 'reveal', field_id=field_id)
                except (VaultIntegrityError, VaultLockedError) as exc:
                    self._audit(db, 'reveal', 'deny', field_id)
                    error = exc
        if error:
            raise error
        return value

    def delete_field(self, field_id):
        with self._db(transaction=True) as db:
            found = db.execute('SELECT 1 FROM fields WHERE id=?', (field_id,)).fetchone()
            if found is None:
                raise KeyError('Field not found')
            db.execute('DELETE FROM fields WHERE id=?', (field_id,))
            self._audit(db, 'delete_field', field_id=field_id)
        self._compact()
        return {'deleted': True}

    def delete_profile(self, profile_id):
        with self._db(transaction=True) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile_id,)).fetchone():
                raise KeyError('Profile not found')
            db.execute('DELETE FROM profiles WHERE id=?', (profile_id,))
            self._audit(db, 'delete_profile')
        self._compact()
        return {'deleted': True}

    def delete_all(self):
        with self._db(transaction=True) as db:
            # Hold the write transaction across erasure, so grants, mutations and
            # disclosure linearize before or after deletion across all processes.
            self.cipher.destroy()
            db.execute('DELETE FROM profiles')
            self._audit(db, 'delete_all')
        self._compact()
        return {'deleted': True}

    def _compact(self):
        with self._db() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')

    def grant(self, field_id, call_id, recipient, purpose, expires_seconds=120):
        call_id, recipient, purpose = _text(call_id), _text(recipient), _text(purpose, maximum=1024)
        if type(expires_seconds) is not int or not 1 <= expires_seconds <= 300:
            raise ValueError('Grant expiry must be between 1 and 300 seconds')
        with self._db(transaction=True) as db:
            if not db.execute('SELECT 1 FROM fields WHERE id=?', (field_id,)).fetchone():
                raise KeyError('Field not found')
            grant_id, now = _identifier(), time.time()
            scope = {'call_id': call_id, 'recipient': recipient, 'purpose': purpose}
            encrypted = self.cipher.encrypt(json.dumps(scope), 'grant:' + grant_id + ':' + field_id)
            db.execute('INSERT INTO grants VALUES(?,?,?,?,?,?)',
                       (grant_id, field_id, encrypted, now + expires_seconds, None, now))
            self._audit(db, 'grant', field_id=field_id, grant_id=grant_id)
            return dict(id=grant_id, field_id=field_id, **scope, expires_at=now + expires_seconds,
                        revoked_at=None, created_at=now)

    def revoke(self, grant_id):
        with self._db(transaction=True) as db:
            row = db.execute('SELECT field_id FROM grants WHERE id=?', (grant_id,)).fetchone()
            if row is None:
                raise KeyError('Grant not found')
            db.execute('UPDATE grants SET revoked_at=? WHERE id=?', (time.time(), grant_id))
            self._audit(db, 'revoke', field_id=row['field_id'], grant_id=grant_id)
        return {'revoked': True}

    def grants(self):
        with self._db() as db:
            results = []
            for row in db.execute('SELECT * FROM grants ORDER BY created_at DESC'):
                result = dict(row)
                encrypted = result.pop('scope')
                result.update(json.loads(self.cipher.decrypt(encrypted, 'grant:' + row['id'] + ':' + row['field_id'])))
                result['active'] = result['revoked_at'] is None and result['expires_at'] > time.time()
                results.append(result)
            return results

    def request_profile_field(self, field_id, call_id, recipient, purpose):
        # This method is an internal runtime boundary, never expose it as a model
        # or HTTP endpoint accepting model-supplied call/recipient assertions.
        value, permitted = None, False
        with self._db(transaction=True) as db:
            field = db.execute('SELECT ciphertext FROM fields WHERE id=?', (field_id,)).fetchone()
            if field is not None:
                try:
                    for row in db.execute('SELECT * FROM grants WHERE field_id=? AND revoked_at IS NULL '
                                          'AND expires_at>?', (field_id, time.time())):
                        scope = json.loads(self.cipher.decrypt(row['scope'], 'grant:' + row['id'] + ':' + field_id))
                        if scope == {'call_id': call_id, 'recipient': recipient, 'purpose': purpose}:
                            value = self.cipher.decrypt(field['ciphertext'], 'field:' + field_id)
                            # Recheck after decryption, since unlocking can take time.
                            if row['expires_at'] > time.time():
                                permitted = True
                                self._audit(db, 'disclosure', field_id=field_id, grant_id=row['id'])
                            break
                except (VaultIntegrityError, VaultLockedError, ValueError, TypeError):
                    permitted = False
            if not permitted:
                # Unknown caller IDs are deliberately omitted from audit to stop
                # malicious input embedding a field value in an alleged ID.
                self._audit(db, 'disclosure', 'deny', field_id=field_id if field is not None else None)
        if not permitted:
            raise PermissionError('No active disclosure grant matches this request')
        return value

    def audit(self):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')]

    @staticmethod
    def _redact_values(text, values):
        for value in sorted(set(values), key=len, reverse=True):
            if not value:
                continue
            # Bound short values to tokens so an initial or unit number does not
            # destroy unrelated prose. Longer values also redact inside strings.
            literal = re.escape(value)
            if len(value) < 4:
                literal = r'(?<!\w)' + literal + r'(?!\w)'
            text = re.sub(literal, '[REDACTED]', text)
            digits = re.sub(r'[\s\-‐‑‒–—()]+', '', value)
            if len(digits) >= 5 and digits.isascii() and digits.isdigit():
                pattern = r'(?<!\d)' + r'[\s\-‐‑‒–—()]*'.join(map(re.escape, digits)) + r'(?!\d)'
                text = re.sub(pattern, '[REDACTED]', text)
        return text

    def redactor(self):
        """Ephemeral response/utterance snapshot; never cache this across requests."""
        try:
            # A read transaction gives a consistent set of configured values.
            with self._db(transaction=True) as db:
                values = [self.cipher.decrypt(row['ciphertext'], 'field:' + row['id'])
                          for row in db.execute('SELECT id,ciphertext FROM fields')]
                return lambda text:self._redact_values(text,values)
        except (VaultLockedError, VaultIntegrityError, sqlite3.Error, OSError):
            return lambda text:'[redacted: vault unavailable]'

    def redact(self,text):
        if not isinstance(text,str):raise ValueError('Redaction requires text')
        return self.redactor()(text)
