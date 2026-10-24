"""Provider-neutral phone transcript export tests; completely offline."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from phonebridge.agentphone_transcripts import (
 AgentPhonePublisher, TranscriptCapture, TranscriptExportError, find_unique_call,
 make_turns, from_sip_identity
)
STAMP=datetime.now(timezone.utc).isoformat()
CALLER="+12025550123"
NUMBER_ID="cmexample0000000000000001"

def rec(**kwargs):
    item={"id":"cmcall000000000000000001","phoneNumberId":NUMBER_ID,
          "fromNumber":CALLER,"startedAt":STAMP,"direction":"inbound","status":"completed"}
    item.update(kwargs)
    return item

class TranscriptTests(unittest.TestCase):
    def test_normalizes_and_merges_adjacent_fragments(self):
        items=[{"speaker":"caller","text":"Hello"},{"speaker":"caller","text":"there"},
               {"speaker":"assistant","text":"Good day."},{"speaker":"caller","text":"Thanks"}]
        self.assertEqual(make_turns(items),[{"role":"user","content":"Hello there"},
                                           {"role":"agent","content":"Good day."},
                                           {"role":"user","content":"Thanks"}])
    def test_excessive_turns_fail_safely(self):
        with self.assertRaises(TranscriptExportError):
            make_turns([{"speaker":"caller" if i%2 else "assistant","text":"test"} for i in range(101)])
    def test_bounded_collector_deduplicates_and_closes(self):
        cap=TranscriptCapture()
        event={"type":"session.input_transcript.delta","event_id":"e1","delta":"Hello"}
        cap.on_live_event(event);cap.on_live_event(event)
        cap.on_live_event({"type":"session.output_transcript.delta","event_id":"e2","delta":"Hi"})
        self.assertEqual(len(cap.close()),2)
        cap.on_live_event({"type":"session.input_transcript.delta","delta":"Late"})
        self.assertEqual(cap._events,[])
    def test_invalid_and_unknown_roles_ignored(self):
        self.assertEqual(make_turns([{"speaker":"error","text":"broken"}]),[])
    def test_resolve_single_provider_call(self):
        self.assertEqual(find_unique_call([rec()],caller=CALLER,started_at=STAMP,
                                         phone_number_id=NUMBER_ID),rec()["id"])
    def test_ambiguity_never_guessed(self):
        another=rec(id="cmcall000000000000000002")
        self.assertIsNone(find_unique_call([rec(),another],caller=CALLER,started_at=STAMP,
                                           phone_number_id=NUMBER_ID))
    def test_rejects_other_person_wrong_number_and_status(self):
        self.assertIsNone(find_unique_call([rec()],caller="+12025550199",started_at=STAMP,
                                           phone_number_id=NUMBER_ID))
        self.assertIsNone(find_unique_call([rec(phoneNumberId="cmother1234567890")],
                                           caller=CALLER,started_at=STAMP,phone_number_id=NUMBER_ID))
        self.assertIsNone(find_unique_call([rec(status="in_progress")],caller=CALLER,
                                           started_at=STAMP,phone_number_id=NUMBER_ID))
    def test_out_of_window_rejected(self):
        older=(datetime.now(timezone.utc)-timedelta(seconds=300)).isoformat()
        self.assertIsNone(find_unique_call([rec(startedAt=older)],caller=CALLER,
                                           started_at=STAMP,phone_number_id=NUMBER_ID))
    def test_number_parsing(self):
        self.assertEqual(from_sip_identity("<sip:+12025550123@example.invalid>;tag=abc"),CALLER)
        self.assertIsNone(from_sip_identity("<sip:not-a-phone@example.invalid>"))
    def test_opt_in_default_off(self):
        with patch.dict("os.environ",{},clear=True):
            self.assertIsNone(AgentPhonePublisher.from_env())
        with patch.dict("os.environ",{"PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED":"1",
                                    "PHONEDEX_AGENTPHONE_API_KEY":"test-key",
                                    "PHONEDEX_AGENTPHONE_NUMBER_ID":NUMBER_ID}):
            self.assertIsNotNone(AgentPhonePublisher.from_env())
    def test_read_before_post_and_no_overwrite(self):
        pub=AgentPhonePublisher("fake-key",NUMBER_ID)
        with patch.object(pub,"_request",side_effect=[{"transcript":[]},{}]) as request:
            self.assertTrue(pub.submit_once(rec()["id"],[{"role":"user","content":"Synthetic"}]))
            self.assertEqual(request.call_count,2)
            self.assertEqual(request.call_args.args[0],"POST")
            self.assertEqual(request.call_args.kwargs["payload"]["turns"][0]["role"],"user")
        with patch.object(pub,"_request",return_value={"transcript":[{"role":"user","content":"Existing"}]}) as request:
            self.assertFalse(pub.submit_once(rec()["id"],[{"role":"user","content":"Synthetic"}]))
            self.assertEqual(request.call_count,1)
    def test_missing_call_does_not_upload(self):
        pub=AgentPhonePublisher("fake-key",NUMBER_ID)
        with patch.object(pub,"list_completed_inbound",return_value=[rec(id="cmcall000000000000000002",
                                                                   fromNumber="+12025550101")]),\
             patch.object(pub,"submit_once") as posted:
            found=find_unique_call(pub.list_completed_inbound(),caller=CALLER,
                                   started_at=STAMP,phone_number_id=NUMBER_ID)
            self.assertIsNone(found)
            posted.assert_not_called()

class GatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_cleanup_schedules_async_export_without_blocking_sip(self):
        from sip_tls_gateway import SipsGateway
        with patch.dict("os.environ",{"PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED":"1",
                     "PHONEDEX_AGENTPHONE_API_KEY":"test-only",
                     "PHONEDEX_AGENTPHONE_NUMBER_ID":NUMBER_ID}):
            gw=SipsGateway(SimpleNamespace(allow_source=["127.0.0.1/32"],backend="subscription"))
        gw.call=active=SimpleNamespace(active=True)
        gw.model=SimpleNamespace(opts=SimpleNamespace(on_media_idle=None,call_control=None),
                                  reset_call=AsyncMock())
        gw.transcript_capture=TranscriptCapture()
        gw.transcript_capture.on_live_event({"type":"session.input_transcript.delta","delta":"Synthetic"})
        gw.transcript_from=CALLER
        gw.transcript_started=STAMP
        with patch.object(gw.transcript_publisher,"deliver",new_callable=AsyncMock) as send:
            await gw._finish_call(active)
            await asyncio.sleep(.01)
            send.assert_awaited_once()
            self.assertEqual(send.await_args.kwargs["turns"][0]["content"],"Synthetic")
        self.assertIsNone(gw.model.opts.call_control)
        self.assertIsNone(gw.call)

if __name__=="__main__":
    unittest.main()
