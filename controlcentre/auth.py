"""Local operator passwords and bounded in-memory browser sessions."""
import hashlib
import hmac
import json
import secrets
import threading
import time

from fastapi import HTTPException

COOKIE = 'phonedex_session'
PAIRING_TTL_SECONDS = 3600


class Auth:
    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.RLock()

    def configured(self):
        return self.store.get_meta('operator_password') is not None

    def throttle(self, key):
        now = time.time()
        with self.lock:
            attempts = [t for t in self.attempts.get(key, []) if t > now - 60]
            self.attempts[key] = attempts
            if len(attempts) >= 5:
                raise HTTPException(429, 'Too many attempts. Try again later.')
            attempts.append(now)

    def create_pairing(self):
        """Explicit bootstrap capability; retain only its digest for one hour."""
        if self.configured():raise HTTPException(409,'Operator password already configured')
        token=secrets.token_urlsafe(32)
        self.store.set_meta('setup_pairing',{'digest':hashlib.sha256(token.encode()).hexdigest(),'expires':time.time()+PAIRING_TTL_SECONDS})
        return token

    def setup(self, password, *, pairing_token=None, remote=False):
        with self.lock:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM meta WHERE key='operator_password'").fetchone():
                    raise HTTPException(409, 'Operator password already configured')
                if remote:
                    row=db.execute("SELECT data FROM meta WHERE key='setup_pairing'").fetchone()
                    pairing=json.loads(row['data']) if row else {}
                    candidate=hashlib.sha256((pairing_token or '').encode()).hexdigest()
                    if not hmac.compare_digest(candidate,pairing.get('digest','')):
                        raise HTTPException(403,'A valid one-time phone pairing link is required')
                    if pairing.get('expires',0)<=time.time():
                        raise HTTPException(403,'This phone pairing link has expired. Open a fresh setup link from this computer.')
                if len(password)<12:raise HTTPException(422,'Use a password of at least 12 characters')
                salt=secrets.token_bytes(16)
                digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()
                db.execute("INSERT INTO meta(key,data) VALUES('operator_password',?)",(json.dumps({'salt':salt.hex(),'digest':digest}),))
                db.execute("DELETE FROM meta WHERE key='setup_pairing'")
                self.store._change(db)

    def verify(self, password):
        record = self.store.get_meta('operator_password')
        if not record:
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(record['digest'], digest)

    def issue(self):
        now = time.time()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
            if len(self.sessions) >= 8:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            session = dict(csrf=secrets.token_urlsafe(32), expires=now + 8 * 3600, reauth_until=0)
            self.sessions[token] = session
            return token, session

    def session(self, request):
        token = request.cookies.get(COOKIE, '')
        session = self.sessions.get(token)
        if not session or session['expires'] <= time.time():
            self.sessions.pop(token, None)
            raise HTTPException(401, 'Authentication required')
        return session

    def require(self, request, *, fresh=False):
        session = self.session(request)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            supplied = request.headers.get('x-csrf-token', '')
            if not supplied or not hmac.compare_digest(supplied, session['csrf']):
                raise HTTPException(403, 'CSRF token required')
        if fresh and session['reauth_until'] <= time.time():
            raise HTTPException(403, 'Fresh operator reauthentication required')
        return session
