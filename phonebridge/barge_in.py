"""PhoneDex RTP caller barge-in detector, portable and opt-in-free.

RTP is full duplex; this gate reacts to sustained near-end caller speech,
flushes buffered model speech, and waits for the model's next speech burst.
GPT Live subscription cancellation is undocumented; we do not send invented
sideband commands. Native GPT Live turn-taking still decides model generation.
"""
from __future__ import annotations
from collections import deque
import math
import struct
import time

FRAME_SAMPLES = 160
ENERGY_FLOOR = 1250
ATTACK_FRAMES = 3          # 60ms
RELEASE_NS = 320_000_000   # 320ms to release after speech
OUTPUT_GAP_NS = 420_000_000
MAX_RECOVERY_WAIT_NS = 7_000_000_000
ASSISTANT_ACTIVE_NS = 750_000_000

class BargeInDetector:
    def __init__(self, clock_ns=None):
        self.clock_ns = clock_ns or time.monotonic_ns
        self.voice_run = 0
        self.voice_until = 0
        self.output_until = 0
        self.last_output_voice = 0
        self.suppressed = False
        self.cut_count = 0
        self._recent_assistant = deque(maxlen=16)
        self._quiet_since = 0
        self._last_remote_voice_ns = 0
        self._max_time = 0
        self._saw_remote_gap = False
        self._last_remote_gap_ns = 0

    def reset(self) -> None:
        self.voice_run = 0
        self.voice_until = 0
        self.output_until = 0
        self.last_output_voice = 0
        self.suppressed = False
        self._recent_assistant.clear()
        self._quiet_since = 0
        self._last_remote_voice_ns = 0
        self._max_time = 0
        self._saw_remote_gap = False
        self._last_remote_gap_ns = 0

    def note_sent(self, samples: bytes, *, active=True):
        now = self.clock_ns()
        if len(samples) != FRAME_SAMPLES * 2:
            return
        if active:
            self._recent_assistant.append(samples)
            if self._energy(samples) >= 400:
                self.output_until = now + ASSISTANT_ACTIVE_NS
                self.last_output_voice = now

    @staticmethod
    def _energy(frame: bytes) -> float:
        if len(frame)!=FRAME_SAMPLES*2:
            return 0.0
        nums = struct.unpack("<160h",frame)
        return math.sqrt(sum(x*x for x in nums)/FRAME_SAMPLES)

    def _is_probable_echo(self, frame: bytes, rms: float) -> bool:
        if rms < ENERGY_FLOOR or not self._recent_assistant:
            return False
        incoming = struct.unpack("<160h",frame)
        sum_a = sum(x*x for x in incoming)
        if not sum_a:
            return False
        for previous in self._recent_assistant:
            outbound = struct.unpack("<160h",previous)
            sum_b = sum(y*y for y in outbound)
            if not sum_b:
                continue
            dot = sum(x*y for x,y in zip(incoming,outbound))
            similarity = abs(dot)/math.sqrt(sum_a*sum_b)
            if similarity >= 0.88:
                return True
        return False

    def note_caller(self, frame: bytes, *, output_waiting: bool = False) -> bool:
        """Return True exactly once when sustained speech interrupts playback."""
        if len(frame)!=FRAME_SAMPLES*2:
            return False
        now = self.clock_ns()
        rms=self._energy(frame)
        samples = struct.unpack("<160h",frame)
        crossings=sum((samples[i]>=0)!=(samples[i-1]>=0) for i in range(1,len(samples)))
        talking=rms >= ENERGY_FLOOR and 2 <= crossings <= 145 and not self._is_probable_echo(frame,rms)
        if talking:
            self.voice_run=min(100,self.voice_run+1)
            self.voice_until=now+RELEASE_NS
            self._quiet_since=0
        else:
            self.voice_run=0
            if now >= self.voice_until and not self._quiet_since:
                self._quiet_since=now
        if self.suppressed:
            return False
        if now <= self.output_until or output_waiting:
            if self.voice_run >= ATTACK_FRAMES:
                self.suppressed = True
                self.cut_count += 1
                self._max_time=now+MAX_RECOVERY_WAIT_NS
                self._last_remote_voice_ns=now
                self._saw_remote_gap=False
                self._last_remote_gap_ns=0
                return True
        return False

    def should_forward_model_audio(self, frame: bytes) -> bool:
        """False while output is cancelled locally; resume on fresh speech burst."""
        if not self.suppressed:
            return True
        now=self.clock_ns()
        speaking_now = self._energy(frame)>280
        # A pause in model speech after the interruption is the only
        # observable boundary supported by the undocumented subscription media.
        if speaking_now:
            if self._last_remote_voice_ns and now-self._last_remote_voice_ns >= OUTPUT_GAP_NS:
                self._saw_remote_gap=True
                self._last_remote_gap_ns=now
            self._last_remote_voice_ns=now
        if now < self.voice_until:
            return False
        if self._saw_remote_gap and speaking_now and now >= self.voice_until:
            self.suppressed=False
            self.voice_run=0
            return True
        # Also treat sustained remote silence as a completed turn. Allow the
        # next fresh response after this gap even if the callback stays open.
        if not speaking_now and self._last_remote_voice_ns and now-self._last_remote_voice_ns>=OUTPUT_GAP_NS:
            self._saw_remote_gap=True
        if not speaking_now and self._saw_remote_gap and now>=self.voice_until:
            self.suppressed=False
            return False
        if now>=self._max_time and now>=self.voice_until:
            # Never let an interrupted call become permanently mute.
            self.suppressed=False
            self.voice_run=0
            return not speaking_now
        return False
