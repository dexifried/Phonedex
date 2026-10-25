"""Offline privacy-mode regression tests. No network, SIP calls, or carrier account."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import os
import sqlite3
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from harness.agent_controller import CallControl
from harness.transcript_store import LocalTranscriptStore, transcript_mode
from phonebridge.agentphone_transcripts import TranscriptCapture


def call_request(number="a"):
    return {"call_id":"call_"+number*32, "mode":"normal"}

class FakeGateway:
    def __init__(self):
        self.events=[]
    def event(self, data):
        self.events.append(data)
        return {"ok":True}

class PrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=patch.dict(os.environ,{"PHONEDEX_DATA_DIR":self.tmp.name,
                                  "PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED":"0",
                                  "PHONEDEX_TRANSCRIPT_MODE":"off"})
        p.start()
        self.addCleanup(p.stop)
        self.store=LocalTranscriptStore()
    def transcript(self, control, content="Synthetic test"):
        control.on_live_event({
            "type":"session.input_transcript.delta",
            "event_id":"evt_01",
            "delta":content,
            "end_ms":501,
        })

    async def test_default_off_does_not_keep_text_or_publish(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(transcript_mode(),"off")
        gateway=FakeGateway()
        control=CallControl(gateway,call_request("a"))
        self.transcript(control)
        await control.flush()
        self.assertEqual(control.received_deltas,0)
        self.assertEqual(len(control.received),0)
        self.assertEqual(len(control.local_received),0)
        self.assertEqual([x for x in gateway.events if x.get("kind")=="transcript"],[])
        self.assertFalse(self.store.path.exists())

    async def test_local_mode_only_writes_self_hosted_sqlite(self):
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"local"}):
            gateway=FakeGateway()
            control=CallControl(gateway,call_request("b"))
            self.transcript(control)
            await control.flush()
            self.assertEqual(control.local_saved_deltas,1)
            self.assertEqual(control.delivered_deltas,0)
            self.assertEqual([x for x in gateway.events if x.get("kind")=="transcript"],[])
            self.assertEqual(self.store.get_call(control.id)[0]["text"],"Synthetic test")
            self.assertEqual(self.store.get_call(control.id)[0]["ms"],501)
            self.assertTrue(self.store.delete(control.id))
            self.assertEqual(self.store.get_call(control.id),[])

    async def test_local_database_error_is_logged_without_crashing_dispatch(self):
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"local"}):
            gateway=FakeGateway()
            control=CallControl(gateway,call_request("b"))
            self.transcript(control)
            with patch.object(control.local_store, "append",
                              side_effect=sqlite3.OperationalError("simulated disk issue")):
                await control.flush()
            self.assertEqual(control.local_saved_deltas,0)
            self.assertEqual(len(control.local_received),1)
            self.assertEqual([x for x in gateway.events if x.get("kind")=="transcript"],[])

    async def test_worker_mode_only_uploads_when_explicitly_enabled(self):
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"worker"}):
            gateway=FakeGateway()
            control=CallControl(gateway,call_request("c"))
            self.transcript(control)
            await control.flush()
            tx=[x for x in gateway.events if x.get("kind")=="transcript"]
            self.assertEqual(len(tx),1)
            self.assertEqual(tx[0]["items"][0]["text"],"Synthetic test")
            self.assertFalse(self.store.path.exists())

    async def test_both_mode_persists_and_uploads(self):
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"both"}):
            gateway=FakeGateway()
            control=CallControl(gateway,call_request("d"))
            self.transcript(control)
            await control.flush()
            self.assertEqual(control.local_saved_deltas,1)
            self.assertEqual(control.delivered_deltas,1)
            self.assertEqual(self.store.get_call(control.id)[0]["text"],"Synthetic test")
            self.assertEqual(len([x for x in gateway.events if x.get("kind")=="transcript"]),1)

    async def test_inbound_local_capture_without_agentphone_api(self):
        from sip_tls_gateway import SipsGateway
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"local"}):
            gw=SipsGateway(SimpleNamespace(allow_source=["127.0.0.1/32"],backend="subscription"))
            self.assertIsNone(gw.transcript_publisher)
            self.assertIsNotNone(gw.local_transcript_store)
            # Simulate a completed inbound call; no SIP socket or provider API.
            active=SimpleNamespace(active=True)
            gw.call=active
            gw.model=SimpleNamespace(opts=SimpleNamespace(on_media_idle=None,call_control=None),
                                     reset_call=AsyncMock())
            gw.transcript_capture=TranscriptCapture()
            gw.transcript_capture.on_live_event({
                "type":"session.input_transcript.delta",
                "event_id":"event-x","delta":"Local inbound data","end_ms":700})
            gw.local_transcript_id="call_"+"e"*32
            gw.transcript_started=datetime.now(timezone.utc).isoformat()
            self.assertIsNotNone(gw.local_transcript_store)
            self.assertEqual(gw.transcript_capture.copy_for_local()[0]["text"],"Local inbound data")
            with patch.object(gw.local_transcript_store, "append",
                              wraps=gw.local_transcript_store.append) as save:
                await gw._finish_call(active)
                if gw.transcript_tasks:
                    await asyncio.gather(*list(gw.transcript_tasks))
                self.assertTrue(save.called, "Local store was never scheduled")
            self.assertEqual(self.store.get_call("call_"+"e"*32)[0]["text"],"Local inbound data")
            self.assertIsNone(gw.transcript_capture)

    async def test_inbound_provider_export_is_separate_explicit_opt_in(self):
        from sip_tls_gateway import SipsGateway
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"off",
                                    "PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED":"0"}):
            gw=SipsGateway(SimpleNamespace(allow_source=["127.0.0.1/32"],backend="subscription"))
            self.assertIsNone(gw.transcript_publisher)
            self.assertIsNone(gw.local_transcript_store)

    async def test_retention_and_invalid_mode(self):
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"oops"}):
            with self.assertRaises(ValueError): transcript_mode()
        with patch.dict(os.environ,{"PHONEDEX_TRANSCRIPT_MODE":"local"}):
            self.store.append("call_"+"f"*32,"outbound",
                              datetime.now(timezone.utc).isoformat(),0,
                              [{"speaker":"caller","ms":0,"text":"Temporary"}])
            with closing(sqlite3.connect(self.store.path)) as db, db:
                db.execute("UPDATE calls SET updated_epoch=updated_epoch-900000")
                db.commit()
            self.assertEqual(self.store.prune(),1)
            self.assertEqual(self.store.get_call("call_"+"f"*32),[])

if __name__=="__main__":unittest.main()
