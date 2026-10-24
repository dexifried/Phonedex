"""Outbound answering-machine / voicemail-beep screening for PhoneDex.

Uses short 20ms 8kHz PCM16 blocks, no uploaded audio or call recordings.
Conservative: never claim voicemail based solely on a long greeting, and
never play a message before a plausible terminal beep has *ended*.

Audio heuristics are imperfect: carrier tones, music, accents and greetings
vary widely. Timeout returns 'no_beep', not 'voicemail'.
"""
from __future__ import annotations

import asyncio
import math
import struct

SAMPLES = 160
RATE = 8000
FRAME_MS = 20
VOICE_RMS = 670
TONE_MIN_RMS = 500
TONE_RATIO = 0.83
TONE_FRAMES = 8                    # >=160 ms, plus post-tone trailing guard
TONE_END_FRAMES = 4               # 80ms after tone ends
# Avoid dial/ringback progress tones: do not consider a beep until we've
# received at least 600 ms of non-tonal voice before it.
BEFORE_BEEP_VOICE_FRAMES = 30
MACHINE_GREETING_MS = 2400
HUMAN_SILENCE_MS = 1250
INITIAL_SILENCE_MS = 3200
MAX_SCREEN_MS = 25000
BEEP_FREQUENCIES = tuple(range(400, 1651, 50))
COEFFICIENTS = tuple((hz, 2*math.cos(2*math.pi*hz/RATE)) for hz in BEEP_FREQUENCIES)


def tone_frequency(frame: bytes) -> int | None:
    """Return dominant continuous narrow-band beep frequency, not raw loudness."""
    if len(frame) != SAMPLES * 2:
        return None
    data = struct.unpack("<160h", frame)
    mean = sum(data) / SAMPLES
    centered = tuple(x - mean for x in data)
    energy = sum(v*v for v in centered)
    if energy < SAMPLES*TONE_MIN_RMS*TONE_MIN_RMS:
        return None
    best_frequency = None
    best_ratio = 0.0
    for freq, coefficient in COEFFICIENTS:
        q0=q1=q2=0.0
        for value in centered:
            q0=coefficient*q1-q2+value
            q2=q1
            q1=q0
        power=q1*q1+q2*q2-coefficient*q1*q2
        ratio=(2.0*power)/(SAMPLES*energy)
        if ratio>best_ratio:
            best_ratio=ratio
            best_frequency=freq
    return best_frequency if best_ratio >= TONE_RATIO else None


class VoicemailScreen:
    """Per-outbound-call analysis state; a decision event signals safe release."""
    def __init__(self):
        self.event = asyncio.Event()
        self.decision: str | None = None
        self.frames=0
        self.spoken=0
        self.segment_voice=0
        self.longest_segment=0
        self.gap=0
        self.machine=False
        self.tone_run=0
        self.tone_gap=0
        self.tone_frequency=None
        self.tone_confirmed=False
        self.no_beep_reason=None

    def decide(self, decision: str) -> None:
        if self.decision is None:
            if decision not in ("human","voicemail_beep","no_beep"):
                raise ValueError("Unknown voicemail screen result")
            self.decision=decision
            self.event.set()

    def feed(self, frame: bytes) -> str | None:
        if self.decision is not None:
            return self.decision
        if len(frame)!=SAMPLES*2:
            return None
        self.frames+=1
        elapsed=self.frames*FRAME_MS
        tone=tone_frequency(frame)
        samples=struct.unpack("<160h",frame)
        rms=math.sqrt(sum(x*x for x in samples)/SAMPLES)
        # Only continuous tonal energy is a beep candidate. Speech and
        # synthesized DTMF/ring tones can resemble a beep, so speech history
        # is required and output waits for tone termination.
        if tone is not None and self.spoken >= BEFORE_BEEP_VOICE_FRAMES and elapsed>850:
            if self.tone_frequency is None or abs(tone-self.tone_frequency)<=100:
                self.tone_run+=1
            else:
                self.tone_run=1
            self.tone_frequency=tone
            self.tone_gap=0
            if self.tone_run>=TONE_FRAMES:
                self.tone_confirmed=True
        elif self.tone_confirmed:
            self.tone_gap+=1
            if self.tone_gap>=TONE_END_FRAMES:
                self.decide("voicemail_beep")
                return self.decision
        else:
            self.tone_run=0
            self.tone_frequency=None

        if tone is None and rms>=VOICE_RMS:
            self.spoken+=1
            self.segment_voice+=1
            self.gap=0
            self.longest_segment=max(self.longest_segment,self.segment_voice)
            if self.segment_voice*FRAME_MS>=MACHINE_GREETING_MS:
                self.machine=True
            # Multiple phrases over a lengthy greeting usually mean an IVR
            # rather than a one-word human 'hello'.
            if elapsed>=4000 and self.spoken*FRAME_MS>=2700:
                self.machine=True
        elif tone is None:
            self.gap+=1
            if self.gap>=15:
                self.segment_voice=0

        if self.tone_confirmed:
            return None

        if self.machine:
            if elapsed>=MAX_SCREEN_MS:
                self.no_beep_reason="machine_greeting_without_beep"
                self.decide("no_beep")
            return self.decision

        if self.spoken==0 and elapsed>=INITIAL_SILENCE_MS:
            self.decide("human")
        elif self.spoken>0 and self.gap*FRAME_MS>=HUMAN_SILENCE_MS:
            self.decide("human")
        elif elapsed>=MAX_SCREEN_MS:
            self.no_beep_reason="ambiguous_no_beep"
            self.decide("no_beep")
        return self.decision


HUMAN_PROMPT = (
    "\n\n[OUTBOUND ANSWER SCREEN: PERSON ANSWERED] "
    "This is a live phone conversation. State the purpose of the call briefly "
    "and naturally. Don't announce any detection or mention voicemail."
)

VOICEMAIL_PROMPT = (
    "\n\n[OUTBOUND ANSWER SCREEN: VOICEMAIL BEEP CONFIRMED] "
    "The destination is an answering machine, not a live human. Its greeting "
    "has ended and the recording beep has already sounded. Start speaking "
    "now in a natural but concise voicemail message, ideally under 20 seconds. "
    "State who is calling, the reason for the call, and an appropriate callback "
    "or next step only if you actually know it. Do not invent phone numbers, "
    "names or details. Do not ask questions or wait for a response. "
    "Close with a simple goodbye and hang up when your message is complete."
)
