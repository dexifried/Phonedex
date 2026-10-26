"""Client-delegation adapter: spoken caller transcript -> private tool -> Live result.

This deliberately implements a conservative read-only first pass. GPT-Live's
delegation event includes an ID but *no task text or tool arguments*. The only
safe writes are structured capture_lead calls once another planner collects
and validates caller details, including explicit consent.
"""
from __future__ import annotations
import json
import re
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any
from .tools import ToolError, invoke_tool

BUSINESS_TERMS = re.compile(r"\b(services?|software|automation|websites?|develop(?:ment|er)?|integrations?|business|phonedex|what do you do)\b", re.I)
PRICING = re.compile(r"\b(cost|price|pricing|rate|quote|charge|how much|estimate)\b", re.I)
CONTACT = re.compile(r"\b(contact|email|website|phone number|reach you)\b", re.I)
HOURS = re.compile(r"\b(hours|open|closing|when do you work)\b", re.I)
AREA = re.compile(r"\b(where are you|location|serve|area|remote|coverage)\b", re.I)
CAL = re.compile(r"\b(calendar|availability|available|free on|appointment|schedule|meeting|book|time slot)\b", re.I)
LEAD = re.compile(r"\b(call me|call back|callback|leave my info|leave a message|lead|my contact|reach out|follow up)\b", re.I)

@dataclass(frozen=True)
class ToolDecision:
    name: str | None
    args: dict[str, Any]
    message: str | None = None

def route_request(transcript: str) -> ToolDecision:
    """Deterministic low-risk routing; never extract private details by guessing."""
    t = transcript[-1800:].strip()
    if not t:
        return ToolDecision(None, {}, "I didn't catch the question. Could you repeat it?")
    if CAL.search(t):
        return ToolDecision(None, {}, "I can check a calendar once a read-only calendar connection is configured. I can't confirm appointment availability or book a time yet.")
    if LEAD.search(t):
        return ToolDecision(None, {}, "I can take down your details for the operator. Please provide your name, a callback number or email, what you need, and explicitly confirm that we may save your information and contact you. I won't claim it was sent until the lead is actually recorded.")
    if PRICING.search(t):
        return ToolDecision("business_info", {"topic": "pricing"})
    if CONTACT.search(t):
        return ToolDecision("business_info", {"topic": "contact"})
    if HOURS.search(t):
        return ToolDecision("business_info", {"topic": "hours"})
    if AREA.search(t):
        return ToolDecision("business_info", {"topic": "service_area"})
    if BUSINESS_TERMS.search(t):
        return ToolDecision("business_info", {"topic": "services"})
    return ToolDecision(None, {}, "I can help with Phonedex services, contact details, a project inquiry, or appointment availability. Could you tell me what you need?")

def result_to_speech(decision: ToolDecision) -> str:
    if decision.message is not None:
        return decision.message
    try:
        result=invoke_tool(decision.name or "", decision.args)
    except (ToolError, OSError, ValueError):
        return "I couldn't verify that information right now, so I won't guess."
    if result.get("status") != "ok":
        return "I don't have verified information for that question. I can take an inquiry for follow-up."
    topic=decision.args.get("topic")
    if topic=="pricing":
        return result["pricing_policy"]
    if topic=="contact":
        data=result["contact"]
        return "Phonedex's website is phonedex.com. The business contact email is " + data["email"] + "."
    if topic=="hours":
        return result["hours_policy"]
    if topic=="service_area":
        return result["service_area_note"]
    if topic=="services":
        return "Phonedex works on " + ", ".join(s["name"] for s in result["services"]) + ". What are you looking to build?"
    return "Could you tell me a little more about your request?"

class DelegationBroker:
    def __init__(self,control=None):
        self.control=control
        self.fragments: deque[tuple[float, str]] = deque(maxlen=1000)
        self.seen: set[str] = set()

    def add_transcript(self, event: dict) -> None:
        if event.get("type") != "session.input_transcript.delta":
            return
        delta=event.get("delta")
        if not isinstance(delta, str) or len(delta)>4000:
            return
        timestamp=event.get("end_ms",event.get("start_ms"))
        if not isinstance(timestamp,(int,float)):
            return
        self.fragments.append((float(timestamp),delta))

    def delegated_result(self, event: dict) -> dict | None:
        if event.get("type") != "session.delegation.created":
            return None
        delegation=event.get("delegation")
        if not isinstance(delegation,dict) or delegation.get("target")!="client":
            return None
        delegation_id=delegation.get("id")
        at=event.get("offset_ms")
        if not isinstance(delegation_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}",delegation_id):
            return None
        if not isinstance(at,(int,float)) or delegation_id in self.seen:
            return None
        self.seen.add(delegation_id)
        # Use the recent window around this delegation, not an entire call.
        fragments=[part for timestamp,part in self.fragments if at-12000<=timestamp<=at+1500]
        utterance="".join(fragments).strip()
        decision=route_request(utterance)
        result=result_to_speech(decision)
        return {"type":"session.commentary.append",
                "event_id":"phonedex_tool_"+uuid.uuid4().hex[:16],
                "delegation_id":delegation_id,
                "content":result[:1800]}

    def structured_tool_request(self, name: str, arguments: dict, *, database=None) -> dict:
        """A trusted backend planner uses this entry point after gathering fields.

        The voice transcript alone cannot authorize capture_lead; caller consent
        must already be independently confirmed in the backend workflow.
        """
        if name=='request_profile_field':
            if (self.control is None or not hasattr(self.control,'request_profile_field') or
                not isinstance(arguments,dict) or set(arguments)!={'field_id','purpose'}):
                raise ToolError('A trusted active call and one explicitly authorized field are required')
            value=self.control.request_profile_field(arguments['field_id'],arguments['purpose'])
            return {'status':'authorized','value':value}
        return invoke_tool(name, arguments, database=database)
