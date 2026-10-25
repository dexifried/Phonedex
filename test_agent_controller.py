"""Tests for Muse remote controller, offline only (no phone calls)."""
import asyncio
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parent/"vendor"))
from harness import agent_controller as ac
from phonebridge.subscription_live import SubscriptionLive
from sip_outbound import terminate_target

class FakeGateway:
 def __init__(self): self.events=[]
 def event(self,item): self.events.append(item);return {"ok":True}
 def poll(self,active=None): return {"calls":[],"actions":[]}

class ControllerTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  # Legacy transcript tests deliberately opt into cloud transcript delivery.
  # Production defaults to off; tests must not imply automatic upload.
  patcher=patch.dict("os.environ",{"PHONEDEX_TRANSCRIPT_MODE":"worker"})
  patcher.start()
  self.addCleanup(patcher.stop)

 async def test_call_state_hangup_and_transcript(self):
  gw=FakeGateway()
  req={"call_id":"call_"+"1"*32,"mode":"normal","to":"+12025550123",
       "prompt":"Say hello","duration_seconds":45}
  call=ac.CallControl(gw,req)
  call.on_live_event({"type":"session.input_transcript.delta","delta":"Hello","end_ms":1800})
  call.on_live_event({"type":"session.output_transcript.delta","delta":"Hey there","end_ms":1900})
  await call.flush()
  self.assertEqual(gw.events[-1]["items"][0]["speaker"],"caller")
  self.assertEqual(gw.events[-1]["items"][1]["speaker"],"assistant")
  cmd="cmd_"+"a"*32
  await call.send_steer({"command_id":cmd,"type":"hangup"})
  self.assertTrue(call.hangup.is_set())
  self.assertEqual(gw.events[-1]["state"],"completed")

 async def test_steer_sent_once_and_acknowledged(self):
  gw=FakeGateway();req={"call_id":"call_"+"2"*32,"mode":"business"}
  call=ac.CallControl(gw,req)
  backend=type("Backend",(),{})()
  backend.sideband=object()
  backend.send_private_steer=AsyncMock()
  call.model=type("Model",(),{"backend":backend})()
  cid="cmd_"+"b"*32
  await call.send_steer({"command_id":cid,"type":"steer","instruction":"Use a softer voice."})
  await call.send_steer({"command_id":cid,"type":"steer","instruction":"Use a softer voice."})
  backend.send_private_steer.assert_awaited_once()
  self.assertEqual(gw.events[-1]["state"],"submitted")
  call.on_live_event({"type":"session.instructions.appended","client_event_id":cid})
  await call.flush()
  self.assertEqual(gw.events[-1]["state"],"acknowledged")

 async def test_steer_waits_until_backend_connected(self):
  gw=FakeGateway();req={"call_id":"call_"+"3"*32,"mode":"normal"}
  call=ac.CallControl(gw,req)
  await call.send_steer({"command_id":"cmd_"+"c"*32,"type":"steer","instruction":"Be brief."})
  self.assertEqual(len(gw.events),0)

 async def test_dispatch_persistent_dedup(self):
  with tempfile.TemporaryDirectory() as tmp:
   seen=Path(tmp)/"dispatched.json"
   fake=FakeGateway()
   request={"call_id":"call_"+"4"*32,"mode":"normal","to":"+12025550123",
            "prompt":"Discuss the weather","duration_seconds":60}
   with patch.object(ac,"SEEN",seen),patch.object(ac,"HOME",Path(tmp)):
    controller=ac.Controller(fake)
    controller.last_dispatch=0
    fake_run=AsyncMock()
    with patch.object(controller,"run_call",fake_run):
     await controller.start(request)
     self.assertTrue(seen.exists())
     await controller.start(request)
     self.assertEqual(len(controller.seen),1)
     await asyncio.sleep(0)
     if controller.task: await controller.task

 async def test_normal_business_modes_are_distinct(self):
  source=(ac.ROOT/"normal-prompt.txt").read_text(encoding="utf-8")
  self.assertIn("NORMAL MODE",source)
  business=(ac.ROOT/"business-prompt.txt").read_text(encoding="utf-8")
  self.assertIn("AI receptionist",business)

 async def test_transcript_keeps_spaces_and_deduplicates(self):
  gw=FakeGateway()
  request={"call_id":"call_"+"5"*32,"mode":"normal"}
  call=ac.CallControl(gw,request)
  for event in [
   {"type":"session.input_transcript.delta","event_id":"evt_1","delta":"Hello","end_ms":100},
   {"type":"session.input_transcript.delta","event_id":"evt_2","delta":" there","end_ms":200},
   {"type":"session.input_transcript.delta","event_id":"evt_2","delta":" there","end_ms":200},
   {"type":"session.output_transcript.delta","event_id":"evt_3","delta":"Hi!","end_ms":350}
  ]:call.on_live_event(event)
  self.assertEqual(call.received_deltas,3)
  await call.flush()
  self.assertEqual(call.delivered_deltas,3)
  self.assertEqual([x["text"] for x in gw.events[-1]["items"]],["Hello"," there","Hi!"])

 async def test_failed_transcript_post_is_retried_without_data_loss(self):
  class FailingGateway(FakeGateway):
   def __init__(self):super().__init__();self.attempts=0
   def event(self,item):
    self.attempts+=1
    if self.attempts==1:raise RuntimeError("synthetic network failure")
    return super().event(item)
  gw=FailingGateway()
  call=ac.CallControl(gw,{"call_id":"call_"+"6"*32,"mode":"normal"})
  call.on_live_event({"type":"session.input_transcript.delta","event_id":"evt_retry","delta":"Recover me","end_ms":250})
  await call.flush()
  self.assertEqual(len(call.received),1)
  self.assertEqual(call.batch,0)
  self.assertEqual(call.failed_uploads,1)
  await call.flush()
  self.assertEqual(len(call.received),0)
  self.assertEqual(call.batch,1)
  self.assertEqual(call.delivered_deltas,1)
  self.assertEqual(gw.events[0]["items"][0]["text"],"Recover me")

 async def test_primary_and_sideband_deduplicate_same_transcript_event(self):
  req={"call_id":"call_"+"7"*32,"mode":"normal"}
  gateway=FakeGateway();control=ac.CallControl(gateway,req)
  live=SubscriptionLive(lambda *_:None,"test",control=control)
  event={"type":"session.output_transcript.delta","event_id":"evt_same",
         "delta":"I can help.","start_ms":0,"end_ms":900}
  await live._observe_event(event,"primary")
  await live._observe_event(event,"sideband")
  self.assertEqual(control.received_deltas,1)
  await control.flush()
  self.assertEqual(gateway.events[0]["items"][0]["speaker"],"assistant")
  self.assertEqual(live.transcript_sources["primary"],1)
  self.assertEqual(live.transcript_sources["sideband"],0)

 async def test_experimental_subscription_transcript_items(self):
  gw=FakeGateway()
  control=ac.CallControl(gw,{"call_id":"call_"+"8"*32,"mode":"normal"})
  live=SubscriptionLive(lambda *_:None,"test",control=control)
  spoken=[
   {"type":"input_transcript.added","item":{"id":"input_1","type":"input_transcript","text":"Hey there"},
    "start_ms":1000,"end_ms":1400},
   {"type":"output_transcript.added","item":{"id":"output_1","type":"output_transcript","text":"Hello back!"},
    "start_ms":1500,"end_ms":2100},
  ]
  for source in ("primary","sideband"):
   for e in spoken:
    await live._observe_event(e,source)
  await control.flush()
  self.assertEqual(control.received_deltas,2)
  self.assertEqual(control.delivered_deltas,2)
  self.assertEqual([(v["speaker"],v["text"]) for v in gw.events[-1]["items"]],
                  [("caller","Hey there"),("assistant","Hello back!")])
  self.assertEqual(live.transcript_sources["primary"],2)
  self.assertEqual(live.transcript_sources["sideband"],0)

 async def test_documented_format_still_supported(self):
  gw=FakeGateway()
  control=ac.CallControl(gw,{"call_id":"call_"+"9"*32,"mode":"normal"})
  live=SubscriptionLive(lambda *_:None,"test",control=control)
  await live._observe_event({"type":"session.output_transcript.delta","event_id":"evt_old",
                             "delta":"Old format","end_ms":1200},"sideband")
  await control.flush()
  self.assertEqual(gw.events[-1]["items"][0]["text"],"Old format")

 async def test_normal_mode_does_not_get_business_tools(self):
  from types import SimpleNamespace
  import os
  with patch.dict(os.environ,{"PHONEDEX_AGENT_HARNESS":"1"}):
   normal=SubscriptionLive(lambda *_:None,"Normal call",control=SimpleNamespace(mode="normal"))
   business=SubscriptionLive(lambda *_:None,"Business call",control=SimpleNamespace(mode="business"))
  self.assertIsNone(normal.tool_broker)
  self.assertIsNotNone(business.tool_broker)

 async def test_steering_uses_private_instruction_event(self):
  b=SubscriptionLive(lambda *_:None,"hello")
  class Sock:
   def __init__(self):self.sent=[]
   async def send(self,s):self.sent.append(__import__("json").loads(s))
  sock=Sock()
  b.sideband=sock
  b.connected.set()
  ident="cmd_"+"d"*32
  await b.send_private_steer(ident,"Ask about the project deadline")
  item=sock.sent[0]
  self.assertEqual(item["type"],"session.instructions.append")
  self.assertIsNone(item["delegation_id"])
  self.assertEqual(item["event_id"],ident)
  self.assertIn("Do not speak",item["content"])

if __name__=="__main__":unittest.main()
