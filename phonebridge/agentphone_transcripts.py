"""Optional AgentPhone transcript export for PhoneDex (disabled by default).

Exports live *text* to the authorized AgentPhone account using the caller and
call-start time to resolve exactly one completed provider record. Requires an
explicit opt-in and API key via environment; does not record audio.

There is intentionally no plaintext disk spool. Delivery is best effort and
may be lost if the process exits or the provider is unavailable. A POST is
attempted at most once after a read-before-write check; uncertain responses
are never blindly repeated.
"""
from __future__ import annotations
import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

LOG = logging.getLogger("phonedex.agentphone")
API = "https://api.agentphone.ai/v1"
MAX_TURNS = 100
MAX_FRAGMENTS = 1000
MAX_MATCH_SECONDS = 100
MAX_WAIT_SECONDS = 90
ROLE = {"caller": "user", "assistant": "agent", "user": "user", "agent": "agent"}
E164 = re.compile(r"^\+[1-9]\d{6,14}$")
PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


class TranscriptExportError(RuntimeError):
    pass


def e164(value):
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[\s().-]+", "", value)
    return cleaned if E164.fullmatch(cleaned) else None


def utc_time(value):
    if not isinstance(value, str):
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if out.tzinfo is None:
            return None
        return out.astimezone(timezone.utc)
    except ValueError:
        return None


def from_sip_identity(header):
    """Extract a safely formatted E.164 identity from SIP From/To."""
    if not isinstance(header, str):
        return None
    match = re.search(r"(?:sips?:|\b)(\+[1-9]\d{6,14})(?:@|[;>])", header, re.I)
    return e164(match.group(1)) if match else None


def make_turns(fragments):
    if len(fragments) > MAX_FRAGMENTS:
        raise TranscriptExportError("Transcript too long for provider export")
    turns = []
    for item in fragments:
        if not isinstance(item, dict):
            continue
        role = ROLE.get(item.get("speaker", item.get("role")))
        content = item.get("text", item.get("content"))
        if role is None or not isinstance(content, str):
            continue
        content = " ".join(content.split())
        if not content:
            continue
        if len(content) > 8000:
            raise TranscriptExportError("Oversized transcript event")
        if turns and turns[-1]["role"] == role and len(turns[-1]["content"]) + len(content) < 12000:
            turns[-1]["content"] += " " + content
        else:
            turns.append({"role": role, "content": content})
    if len(turns) > MAX_TURNS:
        raise TranscriptExportError("More than 100 separate turns")
    if len(json.dumps({"turns": turns}, ensure_ascii=False).encode()) > 240000:
        raise TranscriptExportError("Transcript payload exceeds cap")
    return turns


def find_unique_call(calls, *, caller, started_at, phone_number_id):
    """Never choose among ambiguous matches, even if one is a closer timestamp."""
    caller = e164(caller)
    started = utc_time(started_at)
    if not caller or started is None or not phone_number_id:
        return None
    matches = []
    for row in calls:
        if not isinstance(row, dict):
            continue
        if row.get("direction") != "inbound" or row.get("status") != "completed":
            continue
        if row.get("phoneNumberId") != phone_number_id:
            continue
        if e164(row.get("fromNumber")) != caller:
            continue
        remote_start = utc_time(row.get("startedAt"))
        if remote_start is None:
            continue
        if abs((remote_start - started).total_seconds()) > MAX_MATCH_SECONDS:
            continue
        candidate = row.get("id")
        if isinstance(candidate, str) and PROVIDER_ID.fullmatch(candidate):
            matches.append(candidate)
    # Reject duplicates and zero matches. Preserve old data on ambiguous calls.
    return matches[0] if len(set(matches)) == 1 and len(matches) == 1 else None


class TranscriptCapture:
    """Per-call bounded transcript collector for normalized GPT Live events."""
    def __init__(self):
        self._events = []
        self._seen = set()
        self._seen_order = deque()
        self.closed = False
        self.overflowed = False

    def on_live_event(self, event):
        if self.closed or not isinstance(event, dict):
            return
        typ = event.get("type")
        speaker = {"session.input_transcript.delta": "caller",
                   "session.output_transcript.delta": "assistant"}.get(typ)
        if speaker is None:
            return
        fragment = event.get("delta")
        if not isinstance(fragment, str) or not fragment or len(fragment) > 1200:
            return
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in self._seen:
                return
            self._seen.add(event_id)
            self._seen_order.append(event_id)
            if len(self._seen_order) > 4096:
                self._seen.remove(self._seen_order.popleft())
        if len(self._events) >= MAX_FRAGMENTS:
            self.overflowed = True
            return
        offset = event.get("end_ms", event.get("start_ms", 0))
        if not isinstance(offset, (int,float)):
            offset = 0
        self._events.append({"speaker": speaker, "text": fragment,
                             "ms": max(0,min(3600000,int(offset)))})

    def copy_for_local(self):
        """Return a bounded copy before provider normalization clears the buffer."""
        return [dict(item) for item in self._events] if not self.closed else []

    def close(self):
        self.closed = True
        try:
            if self.overflowed:
                LOG.warning("Transcript exceeds bounded exporter capacity; not uploading")
                return []
            return make_turns(self._events)
        except TranscriptExportError as exc:
            LOG.warning("Transcript export skipped: %s", str(exc))
            return []
        finally:
            self._events.clear()
            self._seen.clear()
            self._seen_order.clear()


class AgentPhonePublisher:
    """Opt-in HTTP publisher, never stores transcript or API key on disk."""
    def __init__(self, api_key, phone_number_id, *, timeout=8):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("AgentPhone API key required")
        if not isinstance(phone_number_id, str) or not PROVIDER_ID.fullmatch(phone_number_id):
            raise ValueError("AgentPhone phone-number ID required")
        self._key = api_key
        self.phone_number_id = phone_number_id
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        if os.getenv("PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED") != "1":
            return None
        return cls(os.environ.get("PHONEDEX_AGENTPHONE_API_KEY", ""),
                   os.environ.get("PHONEDEX_AGENTPHONE_NUMBER_ID", ""))

    def _request(self, method, path, *, params=None, payload=None):
        url = API + path
        if params:
            url += "?" + urlencode(params)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False,
                                                     separators=(",", ":")).encode()
        if body and len(body) > 240000:
            raise TranscriptExportError("Provider transcript exceeds upload size")
        request = Request(url, data=body, method=method, headers={
            "Authorization": "Bearer " + self._key, "Accept": "application/json",
            "User-Agent": "PhoneDex-transcript-export/1.0",
            **({"Content-Type": "application/json"} if body is not None else {})
        })
        try:
            with urlopen(request, timeout=self.timeout) as reply:
                raw = reply.read(400000)
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise TranscriptExportError("AgentPhone HTTP " + str(exc.code)) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise TranscriptExportError("AgentPhone transport or response failure") from None

    def list_completed_inbound(self):
        rows = []
        for page in range(4):
            response = self._request("GET", "/calls", params={
                "limit": 100, "offset": page * 100,
                "direction": "inbound", "status": "completed"})
            batch = response.get("data")
            if not isinstance(batch, list):
                raise TranscriptExportError("Unexpected AgentPhone call response")
            rows.extend(batch)
            if response.get("hasMore") is False:
                return rows
            if not batch:
                raise TranscriptExportError("Call list could not be fully checked")
            if len(batch) < 100 and response.get("hasMore") is not True:
                return rows
        raise TranscriptExportError("Too many calls to guarantee an unambiguous match")

    def submit_once(self, call_id, turns):
        if not PROVIDER_ID.fullmatch(call_id) or not turns:
            raise TranscriptExportError("Invalid export call ID or empty transcript")
        detail = self._request("GET", "/calls/" + quote(call_id, safe="") + "/transcript")
        existing = detail.get("transcript")
        if not isinstance(existing, list):
            raise TranscriptExportError("Unexpected AgentPhone transcript response")
        if existing:
            LOG.info("Provider already has transcript; not overwriting")
            return False
        # Intentionally do not retry this POST after any error: the remote
        # service might have accepted an upload before the connection failed.
        self._request("POST", "/calls/" + quote(call_id, safe="") + "/transcript",
                      payload={"turns": turns})
        LOG.info("AgentPhone accepted transcript upload (%d turns)", len(turns))
        return True

    async def deliver(self, *, caller, started_at, turns, pause=5.0):
        """Resolve completion with bounded polling; no plaintext persistence."""
        if not turns or not e164(caller) or utc_time(started_at) is None:
            return False
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                records = await asyncio.to_thread(self.list_completed_inbound)
                matched = find_unique_call(records, caller=caller,
                                           started_at=started_at,
                                           phone_number_id=self.phone_number_id)
                if matched:
                    return await asyncio.to_thread(self.submit_once, matched, turns)
            except TranscriptExportError as exc:
                LOG.warning("AgentPhone transcript export failed: %s", str(exc))
                return False
            await asyncio.sleep(pause)
        LOG.info("No unique completed AgentPhone call found; transcript not exported")
        return False
