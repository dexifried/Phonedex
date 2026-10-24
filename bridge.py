"""Standalone local PCMU RTP bridge; --backend echo or experimental --backend codex.

This is not a SIP registrar, call router, or production telephony service.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
from pathlib import Path
import secrets
import time
import wave

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from phonebridge.audio import RTPPacket, pcm16_to_ulaw, resample_pcm16, ulaw_to_pcm16, rms_pcm16
from phonebridge.codex_live import CodexLive
from phonebridge.subscription_live import SubscriptionLive
from phonebridge.barge_in import BargeInDetector

LOG = logging.getLogger('phonebridge')
PHONE_FRAME = 160                 # samples / 20ms / G.711 PCMU at 8kHz
LIVE_CHUNK_BYTES = 4800          # 100ms PCM16 mono at 24kHz

class Echo:
    def __init__(self, output, prompt=None):
        self.output = output
    async def start(self):
        LOG.info('Local echo backend ready (zero AI/network spend)')
    async def push(self, audio):
        self.output(audio, 24000)
    async def close(self):
        pass

class RTPBridge(asyncio.DatagramProtocol):
    def __init__(self, opts):
        self.opts = opts
        self.transport = None
        self.peer = None
        self.last_input = 0.0
        self.seq = secrets.randbelow(65536)
        self.timestamp = secrets.randbits(32)
        self.ssrc = secrets.randbits(32)
        self.in_pcm = bytearray()
        self.out_pcm = bytearray()
        self.outbound = asyncio.Queue(maxsize=30)  # <= 600ms playback backlog
        self.inbound = asyncio.Queue(maxsize=10)   # <= 1s input backlog
        self.backend = None
        self.received_rtp = 0
        self.received_non_silent_rtp = 0
        self.received_voice_frames = 0
        self.model_audio_callbacks = 0
        self.sent_rtp = 0
        self.sent_non_silent_rtp = 0
        self.model_task = None
        self.speaker_task = None
        self.idle_task = None
        self._greeting_task = None
        self._barge = BargeInDetector()
        self._playback_epoch = 0
        self._barge_cuts = 0

    def connection_made(self, transport):
        self.transport = transport
        LOG.info('RTP endpoint on %s, accepts only localhost', transport.get_extra_info('sockname'))
        self.speaker_task = asyncio.create_task(self.speaker())
        self.idle_task = asyncio.create_task(self.idle())

    def datagram_received(self, data, address):
        if address[0] != '127.0.0.1' or (self.peer is not None and address != self.peer):
            return
        try:
            packet = RTPPacket.parse(data)
        except ValueError:
            return
        if len(packet.payload) != PHONE_FRAME:
            return  # only 20ms PCMU packets are supported
        self.received_rtp += 1
        if any(b not in (0xFF, 0x7F) for b in packet.payload):
            self.received_non_silent_rtp += 1
        if self.peer is None:
            self.peer = address
            LOG.info('Local RTP sender pinned: %s:%d', *address)
            self.model_task = asyncio.create_task(self.model_loop())
        self.last_input = time.monotonic()
        decoded = ulaw_to_pcm16(packet.payload)
        if rms_pcm16(decoded) >= 1200:
            self.received_voice_frames += 1
        if (self.received_voice_frames >= 3 and self._greeting_task is not None
                and not self._greeting_task.done()):
            self._greeting_task.cancel()
        if self._barge.note_caller(decoded, output_waiting=not self.outbound.empty()):
            self._playback_epoch += 1
            self._barge_cuts += 1
            self.out_pcm.clear()
            while not self.outbound.empty():
                self.outbound.get_nowait()
            LOG.info('Caller barge-in cut %s; buffered audio discarded', self._barge_cuts)
        pcm24 = resample_pcm16(decoded, 8000, 24000)
        self.in_pcm.extend(pcm24)
        while len(self.in_pcm) >= LIVE_CHUNK_BYTES:
            frame = bytes(self.in_pcm[:LIVE_CHUNK_BYTES])
            del self.in_pcm[:LIVE_CHUNK_BYTES]
            if self.inbound.full():
                self.inbound.get_nowait()  # never build arbitrary input delay
            self.inbound.put_nowait(frame)

    def activate_inbound(self, media_port: int):
        """Start the model as soon as SIP answers, before carrier RTP arrives."""
        if not 1024 <= media_port <= 65535:
            raise ValueError('Invalid inbound media gateway port')
        if self.peer is not None:
            return
        self.peer = ('127.0.0.1', media_port)
        self.last_input = time.monotonic()
        self._barge.reset()
        self._playback_epoch += 1
        if self.model_task is None or self.model_task.done():
            self.model_task = asyncio.create_task(self.model_loop())
        LOG.info('Inbound SIP answered: starting GPT Live before caller RTP')

    def on_model_audio(self, pcm: bytes, sample_rate: int):
        if not pcm or not self.peer or len(pcm) % 2 or sample_rate < 8000:
            return
        self.model_audio_callbacks += 1
        if self.model_audio_callbacks == 1:
            LOG.info('First GPT Live response audio received by RTP bridge')
        self.out_pcm.extend(resample_pcm16(pcm, sample_rate, 8000))
        while len(self.out_pcm) >= PHONE_FRAME * 2:
            chunk = bytes(self.out_pcm[:PHONE_FRAME * 2])
            del self.out_pcm[:PHONE_FRAME * 2]
            if not self._barge.should_forward_model_audio(chunk):
                continue
            if self.outbound.full():
                self.outbound.get_nowait()  # stale audio > extra latency
            self.outbound.put_nowait(pcm16_to_ulaw(chunk))

    async def _prime_model_greeting(self, wake_file: Path):
        """Inject a short local 'Hello?' into GPT Live's input *only*.

        Nothing here is sent to the caller. GPT Live itself generates the
        greeting through its existing voice output, avoiding robotic TTS
        on the carrier SRTP leg.
        """
        if not wake_file.is_file() or wake_file.stat().st_size > 256_000:
            raise ValueError('Small model wake WAV not found')
        with wave.open(str(wake_file), 'rb') as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24000):
                raise ValueError('Model wake WAV must be 24 kHz, mono, PCM16')
            pcm = source.readframes(source.getnframes())
        if not 6000 <= len(pcm) <= 160000:
            raise ValueError('Model wake WAV must be a short utterance')
        LOG.info('Priming GPT Live to greet the caller in its own voice')
        # Feed approximately realtime into model audio input.
        for offset in range(0, len(pcm), LIVE_CHUNK_BYTES):
            if not self.peer or self.received_voice_frames >= 3:
                LOG.info('Skipped model wake greeting: caller speaking or disconnected')
                return
            frame = pcm[offset:offset + LIVE_CHUNK_BYTES]
            if len(frame) < LIVE_CHUNK_BYTES:
                frame += bytes(LIVE_CHUNK_BYTES - len(frame))
            await self.backend.push(frame)
            await asyncio.sleep(0.10)
        # An additional brief silent tail lets end-of-utterance detection
        # finalize the greeting, without delaying the inbound model stream.
        for _ in range(5):
            if not self.peer or self.received_voice_frames >= 3:
                return
            await self.backend.push(bytes(LIVE_CHUNK_BYTES))
            await asyncio.sleep(0.10)
        LOG.info('GPT Live wake greeting submitted (input only; no local TTS playback)')

    async def model_loop(self):
        if self.opts.backend == 'echo':
            self.backend = Echo(self.on_model_audio)
        elif self.opts.backend == 'subscription':
            self.backend = SubscriptionLive(self.on_model_audio, self.opts.prompt, control=getattr(self.opts, 'call_control', None))
        else:
            self.backend = CodexLive(self.on_model_audio, self.opts.prompt, self.opts.codex_exe)
        try:
            await self.backend.start()
            if getattr(self.opts, 'model_wake_file', None) and self.opts.backend == 'subscription':
                self._greeting_task = asyncio.create_task(
                    self._prime_model_greeting(Path(self.opts.model_wake_file)))
            while self.peer:
                data = await self.inbound.get()
                await self.backend.push(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error('Live backend unavailable: %s', exc)
        finally:
            if self._greeting_task is not None:
                greeting = self._greeting_task
                self._greeting_task = None
                greeting.cancel()
                await asyncio.gather(greeting, return_exceptions=True)
            if self.backend:
                await self.backend.close()
            self.backend = None

    async def speaker(self):
        next_time = time.monotonic()
        while True:
            try:
                frame = await asyncio.wait_for(self.outbound.get(), timeout=.020)
                synthetic_silence = False
            except asyncio.TimeoutError:
                if not self._barge.suppressed:
                    continue
                frame = bytes([0xFF]) * PHONE_FRAME
                synthetic_silence = True
            epoch = self._playback_epoch
            now = time.monotonic()
            next_time = max(next_time, now)
            await asyncio.sleep(max(0.0, next_time - now))
            if (self.peer and epoch == self._playback_epoch and
                    (synthetic_silence or not self._barge.suppressed)):
                packet = RTPPacket(self.seq, self.timestamp, self.ssrc, frame)
                self.transport.sendto(packet.pack(), self.peer)
                if not synthetic_silence:
                    self._barge.note_sent(ulaw_to_pcm16(frame))
                self.sent_rtp += 1
                if any(b not in (0xFF, 0x7F) for b in frame):
                    self.sent_non_silent_rtp += 1
                self.seq = (self.seq + 1) & 65535
                self.timestamp = (self.timestamp + PHONE_FRAME) & 0xffffffff
            next_time += .02

    async def idle(self):
        while True:
            await asyncio.sleep(1)
            if self.peer and time.monotonic() - self.last_input >= self.opts.idle_seconds:
                LOG.info('Idle RTP sender released')
                self.peer = None
                if self.model_task:
                    self.model_task.cancel()
                    await asyncio.gather(self.model_task, return_exceptions=True)
                    self.model_task = None
                self._barge.reset()
                self._playback_epoch += 1
                self.in_pcm.clear()
                self.out_pcm.clear()
                while not self.outbound.empty():
                    self.outbound.get_nowait()
                while not self.inbound.empty():
                    self.inbound.get_nowait()
                # Do not leave a SIP dialog open when the audio session expires.
                on_idle = getattr(self.opts, 'on_media_idle', None)
                if on_idle is not None:
                    try:
                        result = on_idle()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        LOG.warning('RTP idle signaling failed (%s)', type(exc).__name__)

    async def reset_call(self):
        """Discard a caller's media/model state before accepting another call."""
        self.peer = None
        self._barge.reset()
        self._playback_epoch += 1
        if self.model_task:
            task = self.model_task
            self.model_task = None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.in_pcm.clear()
        self.out_pcm.clear()
        while not self.outbound.empty():
            self.outbound.get_nowait()
        while not self.inbound.empty():
            self.inbound.get_nowait()

    async def close(self):
        LOG.info(
            'AUDIO_DIAG inbound_rtp=%s inbound_non_silent=%s '
            'gpt_pcm_callbacks=%s outbound_rtp=%s outbound_non_silent=%s',
            self.received_rtp, self.received_non_silent_rtp,
            self.model_audio_callbacks, self.sent_rtp, self.sent_non_silent_rtp,
        )
        self.peer = None
        self._barge.reset()
        self._playback_epoch += 1
        for task in (self.speaker_task, self.idle_task, self.model_task):
            if task: task.cancel()
        tasks = [t for t in (self.speaker_task, self.idle_task, self.model_task) if t]
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self.transport: self.transport.close()

async def amain():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['echo', 'codex', 'subscription'], default='echo')
    parser.add_argument('--port', type=int, default=60000)
    parser.add_argument('--idle-seconds', type=float, default=30)
    parser.add_argument('--codex-exe', default=os.environ.get('CODEX_EXECUTABLE', 'codex'))
    parser.add_argument('--prompt-file', default='business-prompt.txt')
    parser.add_argument('--verbose', action='store_true')
    opts = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if opts.verbose else logging.INFO)
    opts.prompt = Path(opts.prompt_file).read_text(encoding='utf-8')
    loop = asyncio.get_running_loop()
    protocol = RTPBridge(opts)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=('127.0.0.1', opts.port))
    LOG.info('Backend: %s; local RTP smoke test: python test_rtp.py', opts.backend)
    try:
        await asyncio.Event().wait()
    finally:
        await protocol.close()

if __name__ == '__main__':
    try: asyncio.run(amain())
    except KeyboardInterrupt: pass
