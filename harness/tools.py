"""Phonedex private, in-process business tools. No outbound messaging or booking."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path(os.environ.get("PHONEDEX_BUSINESS_PROFILE",str(Path(__file__).with_name("business_profile.json")))).expanduser()
DATA = Path(os.environ.get("PHONEDEX_DATA_DIR",str(Path.home()/".phonedex"))).expanduser()
LEADS = DATA / "leads.sqlite3"
CALENDAR = DATA / "calendar_freebusy.json"
UTC = dt.timezone.utc
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

class ToolError(ValueError):
    pass

TOOL_SCHEMAS = [
    {
        "name": "business_info",
        "description": "Look up verified Phonedex services, contact details, pricing policy, hours or booking policy.",
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "services, contact, pricing, hours, service_area, booking, overview"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        "side_effects": False,
    },
    {
        "name": "check_availability",
        "description": "Read-only calendar free/busy; never claim availability when missing or stale.",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "RFC3339 start with timezone"},
                "end": {"type": "string", "description": "RFC3339 end with timezone"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        "side_effects": False,
    },
    {
        "name": "capture_lead",
        "description": "Save a consented caller's lead to operator's private local inbox, without sending messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "service": {"type": "string"},
                "summary": {"type": "string"},
                "consent_save": {"type": "boolean"},
                "consent_contact": {"type": "boolean"},
                "call_id": {"type": "string"},
            },
            "required": ["name", "service", "summary", "consent_save", "consent_contact", "call_id"],
            "additionalProperties": False,
        },
        "side_effects": "local_database",
    },
]

def _datetime(s: str) -> dt.datetime:
    if not isinstance(s, str) or len(s) > 45:
        raise ToolError("A valid timestamp with timezone offset is required.")
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("Invalid RFC3339 datetime.") from None
    if parsed.tzinfo is None:
        raise ToolError("Timezone offset is required.")
    return parsed.astimezone(UTC)

def _text(value: Any, label: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ToolError(label + " must be text.")
    v = " ".join(value.split())
    if (not v and not allow_empty) or len(v) > limit:
        raise ToolError(label + " is empty or too long.")
    return v

def _validate_args(name: str, args: dict) -> None:
    schema = next((x for x in TOOL_SCHEMAS if x["name"] == name), None)
    if schema is None:
        raise ToolError("Unknown tool.")
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be an object.")
    allowed = schema["parameters"]["properties"]
    if set(args) - set(allowed):
        raise ToolError("Unknown tool argument.")
    if set(schema["parameters"]["required"]) - set(args):
        raise ToolError("Missing required tool argument.")
    for key, val in args.items():
        typ = allowed[key]["type"]
        if typ == "string" and not isinstance(val, str):
            raise ToolError(key + " must be a string.")
        if typ == "boolean" and type(val) is not bool:
            raise ToolError(key + " must be a boolean.")

def business_info(topic: str) -> dict:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    topic = _text(topic, "topic", 32).lower().replace(" ", "_")
    basic = {"business": profile["name"], "disclosure": profile["disclosure"]}
    selectors = {
        "overview": lambda: {"description": profile["description"], "services": [s["name"] for s in profile["services"]]},
        "services": lambda: {"services": profile["services"]},
        "contact": lambda: {"contact": profile["contact"]},
        "pricing": lambda: {"pricing_policy": profile["pricing_policy"]},
        "hours": lambda: {"hours_policy": profile["hours_policy"]},
        "service_area": lambda: {"service_area_note": profile["service_area_note"]},
        "booking": lambda: {"booking_policy": profile["booking_policy"]},
    }
    if topic not in selectors:
        return {**basic, "status": "unknown_topic", "known_topics": list(selectors)}
    return {**basic, "status": "ok", **selectors[topic]()}

def check_availability(start: str, end: str) -> dict:
    beginning, finish = _datetime(start), _datetime(end)
    now = dt.datetime.now(UTC)
    if beginning >= finish or (finish - beginning) > dt.timedelta(hours=8):
        raise ToolError("Availability window must be between 1 minute and 8 hours.")
    if beginning < now - dt.timedelta(minutes=5) or beginning > now + dt.timedelta(days=90):
        raise ToolError("Availability may only be checked in the next 90 days.")
    if not CALENDAR.exists():
        return {"status": "not_connected", "message": "Calendar read-only free/busy feed is not configured; no availability claim can be made."}
    try:
        doc = json.loads(CALENDAR.read_text(encoding="utf-8"))
        updated = _datetime(doc["updated_at"])
        through = _datetime(doc["covers_until"])
        busy = doc["busy"]
        if not isinstance(busy, list) or len(busy) > 1000:
            raise ValueError("Invalid calendar snapshot")
        if updated > now + dt.timedelta(minutes=2) or now - updated > dt.timedelta(minutes=30) or finish > through:
            return {"status": "stale", "message": "Calendar availability data is too old or doesn't cover that time."}
        overlapping = 0
        for item in busy:
            a, b = _datetime(item["start"]), _datetime(item["end"])
            if a < finish and b > beginning:
                overlapping += 1
        return {"status": "ok", "available": overlapping == 0, "conflicting_busy_blocks": overlapping,
                "start": beginning.isoformat(), "end": finish.isoformat(), "source": "read_only_freebusy_snapshot",
                "booking_performed": False}
    except (KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return {"status": "invalid", "message": "Calendar snapshot cannot be verified. Do not promise availability."}

def capture_lead(name: str, service: str, summary: str, consent_save: bool,
                 consent_contact: bool, call_id: str, phone: str = "", email: str = "",
                 *, database: Path | None = None) -> dict:
    if consent_save is not True or consent_contact is not True:
        return {"status": "consent_required", "saved": False,
                "message": "Ask the caller for explicit permission to save their details and to be contacted."}
    name = _text(name, "name", 100)
    service = _text(service, "service", 100)
    summary = _text(summary, "summary", 1200)
    call_id = _text(call_id, "call_id", 128)
    phone = _text(phone, "phone", 25, allow_empty=True)
    email = _text(email, "email", 254, allow_empty=True)
    if not phone and not email:
        raise ToolError("At least one contact method is required.")
    if phone and not E164.fullmatch(phone):
        raise ToolError("Phone must be E.164, for example +17055551234.")
    if email and not EMAIL.fullmatch(email):
        raise ToolError("Email address format is invalid.")
    destination = database or LEADS
    destination.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(
        json.dumps([call_id, name.casefold(), phone, email.casefold(), service.casefold()],
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with closing(sqlite3.connect(destination, timeout=4.0)) as conn:
        conn.execute("PRAGMA busy_timeout=4000")
        conn.execute("""CREATE TABLE IF NOT EXISTS leads(
            id TEXT PRIMARY KEY, unique_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
            name TEXT NOT NULL, phone TEXT, email TEXT, service TEXT NOT NULL,
            summary TEXT NOT NULL, call_id TEXT NOT NULL, consent_save INTEGER NOT NULL,
            consent_contact INTEGER NOT NULL, notify_status TEXT NOT NULL DEFAULT 'queued'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tool_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            tool TEXT NOT NULL, outcome TEXT NOT NULL, lead_id TEXT
        )""")
        exists = conn.execute("SELECT id FROM leads WHERE unique_key=?", (fingerprint,)).fetchone()
        if exists:
            lead_id, status = exists[0], "already_saved"
        else:
            lead_id, status = "lead_" + uuid.uuid4().hex[:20], "saved"
            conn.execute("""INSERT INTO leads
                (id,unique_key,created_at,name,phone,email,service,summary,call_id,consent_save,consent_contact)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (lead_id, fingerprint, dt.datetime.now(UTC).isoformat(), name, phone, email,
                 service, summary, call_id, 1, 1))
        conn.execute("INSERT INTO tool_events (created_at,tool,outcome,lead_id) VALUES (?,?,?,?)",
                     (dt.datetime.now(UTC).isoformat(), "capture_lead", status, lead_id))
        conn.commit()
    return {"status": status, "saved": True, "lead_id": lead_id,
            "owner_notification": "queued_locally_not_sent",
            "message": "Lead saved in operator's local inbox. No external notification has been delivered."}

def invoke_tool(name: str, args: dict, *, database: Path | None = None) -> dict:
    _validate_args(name, args)
    if name == "business_info":
        return business_info(**args)
    if name == "check_availability":
        return check_availability(**args)
    if name == "capture_lead":
        return capture_lead(**args, database=database)
    raise ToolError("Unknown tool.")
