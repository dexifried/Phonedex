"""Offline regression coverage for RTP speech interruptions and media continuity."""
from __future__ import annotations
import asyncio
import math
from pathlib import Path
import struct
import sys
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from phonebridge.barge_in import BargeInDetector
from phonebridge.audio import RTPPacket, pcm16_to_ulaw


def tone(hz=230,amplitude=6500):
    return struct.pack("<160h",*[int(amplitude*math.sin(i*2*math.pi*hz/8000)) for i in range(160)])


class Clock:
    def __init__(self):self.value=10_000_000_000
    def __call__(self):return self.value
    def advance(self,milliseconds):self.value+=int(milliseconds*1_000_000)


class BargeInDetectionTests(unittest.TestCase):
    def test_sustained_caller_voice_interrupts_once(self):
        clock=Clock();d=BargeInDetector(clock)
        d.note_sent(tone(350))
        for _ in range(2):
            self.assertFalse(d.note_caller(tone(230)))
            clock.advance(20)
        self.assertTrue(d.note_caller(tone(230)))
        self.assertTrue(d.suppressed)
        self.assertFalse(d.note_caller(tone(230)))
        self.assertFalse(d.should_forward_model_audio(tone(350)))
        self.assertEqual(d.cut_count,1)

    def test_short_noise_not_barge(self):
        clock=Clock();d=BargeInDetector(clock)
        d.note_sent(tone(350))
        self.assertFalse(d.note_caller(tone(230)))
        clock.advance(20)
        self.assertFalse(d.note_caller(bytes(320)))
        self.assertFalse(d.suppressed)

    def test_identical_echo_rejected(self):
        clock=Clock();d=BargeInDetector(clock)
        sample=tone(230)
        d.note_sent(sample)
        for _ in range(6):
            self.assertFalse(d.note_caller(sample))
            clock.advance(20)
        self.assertFalse(d.suppressed)

    def test_new_model_response_can_resume(self):
        clock=Clock();d=BargeInDetector(clock)
        d.note_sent(tone(390))
        for _ in range(3):
            d.note_caller(tone(210))
            clock.advance(20)
        self.assertTrue(d.suppressed)
        clock.advance(1000)
        self.assertTrue(d.should_forward_model_audio(tone(350)))
        self.assertFalse(d.suppressed)

    def test_reset_between_calls(self):
        clock=Clock();d=BargeInDetector(clock)
        d.note_sent(tone(380))
        for _ in range(3):
            d.note_caller(tone(200));clock.advance(20)
        self.assertTrue(d.suppressed)
        d.reset()
        self.assertTrue(d.should_forward_model_audio(tone(380)))


class BargeInBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rtp_playback_queue_flushed_by_speech(self):
        from bridge import RTPBridge
        b=RTPBridge(SimpleNamespace(backend="echo",prompt="",idle_seconds=45))
        b.peer=("127.0.0.1",44000)
        b.transport=SimpleNamespace(sendto=lambda *_:None)
        b._barge.note_sent(tone(380))
        for _ in range(20):
            b.outbound.put_nowait(bytes([255])*160)
        sample=pcm16_to_ulaw(tone(230))
        for i in range(4):
            b.datagram_received(RTPPacket(i,i*160,777,sample).pack(),b.peer)
        self.assertTrue(b._barge.suppressed)
        self.assertEqual(b.outbound.qsize(),0)
        self.assertEqual(b._barge_cuts,1)

    async def test_suppressed_barge_emits_silence_rtp(self):
        from bridge import RTPBridge
        b=RTPBridge(SimpleNamespace(backend="echo",prompt="",idle_seconds=45))
        b.peer=("127.0.0.1",44000)
        packets=[]
        b.transport=SimpleNamespace(sendto=lambda pkt,_:packets.append(RTPPacket.parse(pkt)))
        b._barge.suppressed=True
        task=asyncio.create_task(b.speaker())
        try:
            await asyncio.sleep(.09)
            self.assertGreaterEqual(len(packets),2)
            self.assertTrue(all(p.payload==bytes([255])*160 for p in packets))
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_reset_clears_suppression_and_epoch(self):
        from bridge import RTPBridge
        b=RTPBridge(SimpleNamespace(backend="echo",prompt="",idle_seconds=45))
        b.peer=("127.0.0.1",44000)
        b._barge.suppressed=True
        old=b._playback_epoch
        await b.reset_call()
        self.assertFalse(b._barge.suppressed)
        self.assertGreater(b._playback_epoch,old)


if __name__=="__main__":
    unittest.main()
