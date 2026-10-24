"""Synthetic, offline tests: outbound human/voicemail detection and silent startup."""
from __future__ import annotations
import asyncio
import math
import struct
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from phonebridge.voicemail import VoicemailScreen, tone_frequency
from bridge import RTPBridge
from phonebridge.audio import RTPPacket, pcm16_to_ulaw, ulaw_to_pcm16

def sample_tone(hz=1000, amplitude=5600):
    return struct.pack("<160h",*[int(amplitude*math.sin(2*math.pi*hz*n/8000)) for n in range(160)])

def speech_frame(index):
    # A deterministic multitone speech-like signal, not a steady beep.
    return struct.pack("<160h",*[int(
        1600*math.sin(2*math.pi*195*(n+index*160)/8000)
       +1300*math.sin(2*math.pi*615*(n+index*160)/8000)
       +1100*math.sin(2*math.pi*1360*(n+index*160)/8000)
    ) for n in range(160)])

SILENCE=bytes(320)

def feed(screen, amount, make):
    for i in range(amount):
        screen.feed(make(i))

class SignalTests(unittest.TestCase):
    def test_beep_frequency_detects_1000hz(self):
        self.assertEqual(tone_frequency(sample_tone(1000)),1000)
    def test_beep_frequency_detects_650hz(self):
        self.assertEqual(tone_frequency(sample_tone(650)),650)
    def test_voice_and_silence_are_not_beep(self):
        self.assertIsNone(tone_frequency(SILENCE))
        self.assertIsNone(tone_frequency(speech_frame(1)))
    def test_short_human_hello_releases_promptly(self):
        screen=VoicemailScreen()
        feed(screen,22,speech_frame)
        feed(screen,65,lambda i:SILENCE)
        self.assertEqual(screen.decision,'human')
        self.assertFalse(screen.machine)
        self.assertTrue(screen.event.is_set())
    def test_voicemail_waits_through_long_greeting_to_end_of_beep(self):
        screen=VoicemailScreen()
        feed(screen,160,speech_frame)
        self.assertTrue(screen.machine)
        self.assertIsNone(screen.decision)
        feed(screen,15,lambda _:SILENCE)
        self.assertIsNone(screen.decision)
        feed(screen,10,lambda _:sample_tone())
        self.assertIsNone(screen.decision) # tone still ongoing: never speak over beep
        feed(screen,3,lambda _:SILENCE)
        self.assertIsNone(screen.decision)
        screen.feed(SILENCE)
        self.assertEqual(screen.decision,'voicemail_beep')
    def test_real_pcmu_companding_keeps_voicemail_beep_detectable(self):
        screen=VoicemailScreen()
        for i in range(155):
            screen.feed(ulaw_to_pcm16(pcm16_to_ulaw(speech_frame(i))))
        self.assertTrue(screen.machine)
        for _ in range(12):
            screen.feed(ulaw_to_pcm16(pcm16_to_ulaw(SILENCE)))
        for _ in range(12):
            screen.feed(ulaw_to_pcm16(pcm16_to_ulaw(sample_tone(1000))))
        self.assertIsNone(screen.decision)
        for _ in range(4):
            screen.feed(ulaw_to_pcm16(pcm16_to_ulaw(SILENCE)))
        self.assertEqual(screen.decision,'voicemail_beep')

    def test_short_voicemail_beep_still_prevents_human_classification(self):
        screen=VoicemailScreen()
        feed(screen,55,speech_frame)
        feed(screen,12,lambda _:SILENCE)
        feed(screen,10,lambda _:sample_tone(950))
        feed(screen,4,lambda _:SILENCE)
        self.assertEqual(screen.decision,'voicemail_beep')
    def test_machine_without_beep_never_releases_speech(self):
        screen=VoicemailScreen()
        feed(screen,180,speech_frame)
        self.assertTrue(screen.machine)
        feed(screen,1300,lambda _:SILENCE)
        self.assertEqual(screen.decision,'no_beep')
        self.assertEqual(screen.no_beep_reason,'machine_greeting_without_beep')
    def test_unknown_silence_treated_as_human(self):
        screen=VoicemailScreen()
        feed(screen,165,lambda _:SILENCE)
        self.assertEqual(screen.decision,'human')
    def test_progress_tone_alone_cannot_trigger_voicemail(self):
        screen=VoicemailScreen()
        feed(screen,200,lambda _:sample_tone(1000))
        self.assertNotEqual(screen.decision,'voicemail_beep')
    def test_single_spurious_beep_tone_is_ignored(self):
        screen=VoicemailScreen()
        feed(screen,160,speech_frame)
        feed(screen,3,lambda _:sample_tone())
        feed(screen,10,lambda _:SILENCE)
        self.assertIsNone(screen.decision)

class MediaBridgeTests(unittest.IsolatedAsyncioTestCase):
    def options(self,screen):
        return SimpleNamespace(backend='echo',prompt='Generic test prompt',idle_seconds=45,
                               outbound_answer_screen=screen)
    async def test_holds_gpt_until_screen_release(self):
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        self.assertTrue(model._screen_pending)
        self.assertIsNone(model.model_task)
        self.assertFalse(model.release_answer_screen('no_beep'))
        self.assertTrue(model._screen_pending)
        self.assertTrue(model.release_answer_screen('human'))
        self.assertFalse(model._screen_pending)
        self.assertIn('PERSON ANSWERED',model.opts.prompt)
        self.assertIsNotNone(model.model_task)
        await model.reset_call()
    async def test_voicemail_beep_clears_old_inbound_and_sets_message_prompt(self):
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        model.in_pcm.extend(b'hello')
        model.inbound.put_nowait(b'unrelated greeting')
        model.received_voice_frames=18
        self.assertTrue(model.release_answer_screen('voicemail_beep'))
        self.assertIn('VOICEMAIL BEEP CONFIRMED',model.opts.prompt)
        self.assertEqual(model.inbound.qsize(),0)
        self.assertEqual(len(model.in_pcm),0)
        self.assertEqual(model.received_voice_frames,0)
        await model.reset_call()
    async def test_real_rtp_frames_screen_without_starting_model(self):
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        dest=('127.0.0.1',44000)
        for i in range(140):
            frame=pcm16_to_ulaw(speech_frame(i))
            model.datagram_received(RTPPacket(i,i*160,1234,frame).pack(),dest)
        self.assertTrue(screen.machine)
        self.assertIsNone(model.model_task)
        self.assertTrue(model._screen_pending)
        await model.reset_call()
    async def test_silent_rtp_keepalive_during_screen(self):
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        packets=[]
        model.transport=SimpleNamespace(sendto=lambda raw,_:packets.append(RTPPacket.parse(raw).payload))
        task=asyncio.create_task(model.speaker())
        try:
            await asyncio.sleep(.09)
            self.assertGreaterEqual(len(packets),2)
            self.assertTrue(all(frame==bytes([255])*160 for frame in packets))
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            await model.reset_call()
    async def test_supervisor_stops_on_no_beep(self):
        from sip_outbound import supervise_answer_screen
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        cancel=asyncio.Event()
        worker=asyncio.create_task(supervise_answer_screen(model,screen,cancel))
        screen.decide('no_beep')
        result=await asyncio.wait_for(worker,1)
        self.assertEqual(result,'no_beep')
        self.assertTrue(cancel.is_set())
        self.assertIsNone(model.model_task)
        await model.reset_call()
    async def test_supervisor_starts_voicemail_only_on_beep(self):
        from sip_outbound import supervise_answer_screen
        screen=VoicemailScreen()
        model=RTPBridge(self.options(screen))
        model.activate_inbound(44000)
        cancel=asyncio.Event()
        worker=asyncio.create_task(supervise_answer_screen(model,screen,cancel))
        self.assertIsNone(model.model_task)
        screen.decide('voicemail_beep')
        result=await asyncio.wait_for(worker,1)
        self.assertEqual(result,'voicemail_beep')
        self.assertFalse(cancel.is_set())
        self.assertIsNotNone(model.model_task)
        await model.reset_call()

if __name__=='__main__':
    unittest.main(verbosity=2)
