"""Read-only, on-demand AgentPhone call history for the authenticated owner.

Never persist caller numbers, transcripts or API credentials. Provider rows are
kept distinct from PhoneDex control IDs because suffix/time matching is unsafe.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re

import httpx

BASE = "https://api.agentphone.ai/v1"
CALL_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")


class HistoryUnavailable(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _win_unprotect(payload: bytes) -> str:
    if os.name != "nt":
        raise HistoryUnavailable("Provider history credential not configured")
    source_buffer = ctypes.create_string_buffer(payload)
    source = _Blob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise HistoryUnavailable("Provider history credential unavailable")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8").strip()
    finally:
        kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _token() -> str:
    # Portable installations may configure an environment secret; this Windows
    # install already has the token protected by user-scoped DPAPI.
    token = os.environ.get("PHONEDEX_AGENTPHONE_HISTORY_API_KEY", "").strip()
    if not token:
        path = Path(os.environ.get("APPDATA", "")) / "DEXIFYZ" / "agentphone.dpapi"
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
            raise HistoryUnavailable("AgentPhone history credential not configured")
        token = _win_unprotect(path.read_bytes())
    if not 16 <= len(token) <= 8192 or any(ch.isspace() for ch in token):
        raise HistoryUnavailable("AgentPhone history credential invalid")
    return token


def _get(path: str, params: dict | None = None) -> dict:
    # All requests use fixed provider origin and controlled paths, without redirects.
    try:
        with httpx.Client(timeout=7, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", BASE + path,
                               params=params,
                               headers={"Authorization": "Bearer " + _token(),
                                        "Accept": "application/json"}) as response:
                if response.status_code in (401, 403):
                    raise HistoryUnavailable("AgentPhone access denied")
                if response.status_code != 200:
                    raise HistoryUnavailable("AgentPhone history temporarily unavailable")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise HistoryUnavailable("AgentPhone response too large")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise HistoryUnavailable("Unexpected AgentPhone response")
                return result
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise HistoryUnavailable("AgentPhone connection unavailable") from None


def _public_call(record: dict) -> dict | None:
    identifier = record.get("id")
    if not isinstance(identifier, str) or not CALL_ID.fullmatch(identifier):
        return None
    direction = record.get("direction")
    if direction not in ("inbound", "outbound"):
        return None
    # Caller ID is provider-reported, not an authenticated identity.
    number = record.get("fromNumber") if direction == "inbound" else record.get("toNumber")
    number = number if isinstance(number, str) and E164.fullmatch(number) else None
    stamp = record.get("startedAt")
    if not isinstance(stamp, str) or len(stamp) > 64:
        return None
    seconds = record.get("durationSeconds")
    seconds = seconds if type(seconds) in (int, float) and 0 <= seconds <= 86400 else None
    status = record.get("status")
    return {
        "id": identifier, "direction": direction, "number": number,
        "started_at": stamp, "duration_seconds": seconds,
        "status": str(status)[:60] if status else "unknown",
        "has_snippet": bool(record.get("lastTranscriptSnippet")),
    }


def list_calls() -> list[dict]:
    combined: dict[str, dict] = {}
    for direction in ("inbound", "outbound"):
        result = _get("/calls", {"limit": 30, "direction": direction})
        rows = result.get("data", [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise HistoryUnavailable("Unexpected AgentPhone call list")
        for item in rows:
            if isinstance(item, dict):
                parsed = _public_call(item)
                if parsed:
                    combined[parsed["id"]] = parsed
    return sorted(combined.values(), key=lambda row: row["started_at"], reverse=True)[:60]


def call_transcript(call_id: str) -> list[dict]:
    if not CALL_ID.fullmatch(call_id):
        raise HistoryUnavailable("Invalid call identifier")
    data = _get("/calls/" + call_id + "/transcript")
    items = data.get("transcript", data.get("transcripts", []))
    if not isinstance(items, list) or len(items) > 1000:
        raise HistoryUnavailable("Unexpected AgentPhone transcript")
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role", item.get("speaker"))
        if role not in ("user", "agent", "caller", "assistant"):
            continue
        text = item.get("content", item.get("text"))
        if isinstance(text, str) and text.strip():
            result.append({"speaker": "Caller" if role in ("user", "caller") else "Agent",
                           "text": text[:8000]})
    return result[:250]
