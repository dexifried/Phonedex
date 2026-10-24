"""Mock the GPT-Live sideband event stream; no network or call."""
from __future__ import annotations
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parent/"vendor"))
from harness.delegation import DelegationBroker
from phonebridge.subscription_live import SubscriptionLive

class Sideband:
    def __init__(self,events):
        self.events=iter(events)
        self.sent=[]
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:return __import__('json').dumps(next(self.events))
        except StopIteration:raise StopAsyncIteration
    async def send(self,message):
        self.sent.append(__import__('json').loads(message))

class TestLiveHarness(unittest.IsolatedAsyncioTestCase):
    async def test_model_delegation_to_business_tool(self):
        with patch.dict(os.environ,{"PHONEDEX_AGENT_HARNESS":"1"}):
            live=SubscriptionLive(lambda *_:None,"test")
        sideband=Sideband([
            {"type":"session.input_transcript.delta","start_ms":1000,"end_ms":1500,"delta":"How much does Phonedex charge?"},
            {"type":"session.delegation.created","offset_ms":1600,
             "delegation":{"id":"item_call_123","target":"client"}},
            {"type":"session.delegation.created","offset_ms":1600,
             "delegation":{"id":"item_call_123","target":"client"}},
        ])
        live.sideband=sideband
        await live._watch_sideband()
        self.assertEqual(live.tool_calls_submitted,1)
        self.assertEqual(sideband.sent[0]["delegation_id"],"item_call_123")
        self.assertIn("won't guess",sideband.sent[0]["content"])
    async def test_lead_prompt_never_saves(self):
        with patch.dict(os.environ,{"PHONEDEX_AGENT_HARNESS":"1"}):
            live=SubscriptionLive(lambda *_:None,"test")
        sideband=Sideband([
            {"type":"session.input_transcript.delta","end_ms":1500,"delta":"Can you call me back?"},
            {"type":"session.delegation.created","offset_ms":1600,
             "delegation":{"id":"item_call_lead","target":"client"}},
        ])
        live.sideband=sideband
        await live._watch_sideband()
        self.assertIn("explicitly confirm",sideband.sent[0]["content"])
        self.assertEqual(live.tool_calls_submitted,1)

if __name__=="__main__":unittest.main()
