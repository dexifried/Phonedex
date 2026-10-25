"""Bounded, privacy-preserving inbound SIP rapid-redial guard.

Policy:
- Only penalize repeated, *confirmed remote* hangups on very short calls.
- Unknown caller IDs are not grouped under one key (risk shared lockouts).
- Carrier-side identity can be spoofed; don't use this as authentication.
- Monotonic timing, O(1) amortized eviction, bounded maps.
- No transcription or GPT Live required for rejection.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import re
import secrets
import time

SHORT_CALL_SECONDS = 5.0
STRIKES = 3
STRIKE_WINDOW_SECONDS = 90.0
COOLDOWN_SECONDS = 60.0
GLOBAL_WINDOW_SECONDS = 10.0
GLOBAL_LIMIT = 18
GLOBAL_RETRY_SECONDS = 5
MAX_CALLERS = 2048
RECENT_DIALOG_SECONDS = 120.0
MAX_RECENT_DIALOGS = 2048

# Accept a caller *identity* from the carrier-provided From URI only,
# never a random number embedded elsewhere in SIP headers.
_URI = re.compile(r"(?:<)?(?:sips?:|tel:)(\+[1-9][0-9]{6,14})(?:[@;>]|$)", re.I)


def caller_identity(from_header: str) -> str | None:
    if not isinstance(from_header, str) or len(from_header) > 1024:
        return None
    m = _URI.search(from_header)
    return m.group(1) if m else None


@dataclass
class _Caller:
    short_ends: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    seen_at: float = 0.0


class InboundAbuseGuard:
    def __init__(self, *, clock=time.monotonic, secret: bytes | None = None):
        self._clock=clock
        self._secret=secret if secret is not None else secrets.token_bytes(32)
        if len(self._secret) < 16:raise ValueError("Key too short")
        self._callers: dict[str,_Caller] = {}
        self._global_starts: deque[float] = deque()
        self._recent_dialogs: dict[str,float] = {}

    def _key(self, from_header: str) -> str | None:
        identity=caller_identity(from_header)
        if identity is None:return None
        return hmac.new(self._secret,identity.encode("ascii"),hashlib.sha256).hexdigest()

    def _prune(self,now:float) -> None:
        while self._global_starts and now-self._global_starts[0]>=GLOBAL_WINDOW_SECONDS:
            self._global_starts.popleft()
        for key,row in list(self._callers.items()):
            while row.short_ends and now-row.short_ends[0]>=STRIKE_WINDOW_SECONDS:
                row.short_ends.popleft()
            if not row.short_ends and row.blocked_until<=now and now-row.seen_at>STRIKE_WINDOW_SECONDS:
                del self._callers[key]
        for key,seen in list(self._recent_dialogs.items()):
            if now-seen>=RECENT_DIALOG_SECONDS:
                del self._recent_dialogs[key]
        if len(self._callers)>MAX_CALLERS:
            # Oldest entries first; do not allow a burst to grow memory indefinitely.
            for k,_ in sorted(self._callers.items(),key=lambda item:item[1].seen_at)[:len(self._callers)-MAX_CALLERS]:
                del self._callers[k]

    def check_invite(self, from_header:str) -> tuple[str|None,int]:
        """Return (reason, retry_seconds) or (None,0); record allowed starts."""
        import math
        now=self._clock()
        self._prune(now)
        key=self._key(from_header)
        row=self._callers.get(key) if key is not None else None
        if row is not None and row.blocked_until>now:
            return "repeat_short_calls",max(1,math.ceil(row.blocked_until-now))
        if len(self._global_starts)>=GLOBAL_LIMIT:
            return "global_burst",GLOBAL_RETRY_SECONDS
        self._global_starts.append(now)
        if row is not None:row.seen_at=now
        return None,0

    def record_remote_end(self, from_header:str, *, elapsed:float) -> bool:
        """Record only genuine remote BYE/CANCEL; return True if new cooldown."""
        if elapsed<0 or elapsed>=SHORT_CALL_SECONDS:
            return False
        key=self._key(from_header)
        if key is None:return False
        now=self._clock()
        self._prune(now)
        row=self._callers.setdefault(key,_Caller())
        row.seen_at=now
        row.short_ends.append(now)
        while row.short_ends and now-row.short_ends[0]>=STRIKE_WINDOW_SECONDS:
            row.short_ends.popleft()
        if len(row.short_ends)>=STRIKES:
            if row.blocked_until<=now:
                row.blocked_until=now+COOLDOWN_SECONDS
                row.short_ends.clear()
                return True
        self._prune(now)
        return False

    def _dialog_key(self, call_id:str) -> str|None:
        if not isinstance(call_id,str) or not 1<=len(call_id)<=256 or '\r' in call_id or '\n' in call_id:
            return None
        return hmac.new(self._secret,b'dialog:'+call_id.encode('utf-8'),hashlib.sha256).hexdigest()

    def record_completed_dialog(self,call_id:str) -> None:
        key=self._dialog_key(call_id)
        if key is None:return
        now=self._clock()
        self._recent_dialogs[key]=now
        if len(self._recent_dialogs)>MAX_RECENT_DIALOGS:
            for k,_ in sorted(self._recent_dialogs.items(),
                              key=lambda item:item[1])[:len(self._recent_dialogs)-MAX_RECENT_DIALOGS]:
                del self._recent_dialogs[k]

    def is_replayed_dialog(self,call_id:str) -> bool:
        key=self._dialog_key(call_id)
        if key is None:return False
        now=self._clock()
        then=self._recent_dialogs.get(key)
        if then is None:return False
        if now-then>=RECENT_DIALOG_SECONDS:
            del self._recent_dialogs[key]
            return False
        return True

    def counts(self) -> dict[str,int]:
        """No caller IDs, phone numbers or hashes in diagnostic output."""
        now=self._clock()
        self._prune(now)
        return {"tracked_callers":len(self._callers),
                "cooldowns":sum(1 for x in self._callers.values() if x.blocked_until>now),
                "global_window":len(self._global_starts),
                "recent_dialogs":len(self._recent_dialogs)}
