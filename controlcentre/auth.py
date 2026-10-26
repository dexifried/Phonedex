"""Local operator passwords and bounded in-memory browser sessions."""
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import HTTPException

COOKIE = 'phonedex_session'


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

    def setup(self, password):
        with self.lock:
            if self.configured():
                raise HTTPException(409, 'Operator password already configured')
            if len(password) < 12:
                raise HTTPException(422, 'Use a password of at least 12 characters')
            salt = secrets.token_bytes(16)
            digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
            self.store.set_meta('operator_password', {'salt': salt.hex(), 'digest': digest})

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
