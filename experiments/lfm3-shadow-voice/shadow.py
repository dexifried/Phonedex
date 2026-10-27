"""Nonblocking conversation shadow/controller; stdlib only.

The decision worker reads immutable snapshots and never blocks the media path.
The controller does not execute phone tools or control the microphone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    DUCK_AUDIO = "duck_audio"
    RESTORE_AUDIO = "restore_audio"
    CANCEL_PLAYBACK = "cancel_playback"
    PREPARE_REPLY = "prepare_reply"
    COMMIT_REPLY = "commit_reply"
    DISCARD_SPECULATION = "discard_speculation"


@dataclass(frozen=True)
class Decision:
    """A prediction by d1/Jev; not an instruction to execute side effects."""
    revision: int
    interrupt_probability: float = 0.0
    end_turn_probability: float = 0.0
    ignore_probability: float = 0.0

    def __post_init__(self) -> None:
        for v in (self.interrupt_probability, self.end_turn_probability, self.ignore_probability):
            if not 0.0 <= v <= 1.0:
                raise ValueError("decision probabilities must be in [0, 1]")


@dataclass(frozen=True)
class Snapshot:
    revision: int
    partial_transcript: str
    user_speaking: bool
    assistant_speaking: bool
    heard_text: str
    possibly_heard_text: str


@dataclass
class ShadowController:
    """Keeps prediction/cancellation off the synchronous audio path.

    Audio ducking occurs immediately on observed speech. A qualifying shadow
    decision cancels generated audio; a short sound without transcript can be
    restored. Never equate generated words with words actually played.
    """
    interrupt_threshold: float = 0.75
    end_turn_threshold: float = 0.85
    revision: int = 0
    partial_transcript: str = ""
    user_speaking: bool = False
    assistant_speaking: bool = False
    audio_ducked: bool = False
    response_generation: int = 0
    speculative_revision: Optional[int] = None
    heard_chunks: list[str] = field(default_factory=list)
    uncertain_chunks: list[str] = field(default_factory=list)
    cancelled_responses: set[int] = field(default_factory=set)

    def snapshot(self) -> Snapshot:
        return Snapshot(self.revision, self.partial_transcript, self.user_speaking,
                        self.assistant_speaking, " ".join(self.heard_chunks),
                        " ".join(self.uncertain_chunks))

    def on_voice_activity(self, speaking: bool) -> list[Action]:
        if speaking == self.user_speaking:
            return []
        self.user_speaking = speaking
        self.revision += 1
        if speaking and self.assistant_speaking and not self.audio_ducked:
            self.audio_ducked = True
            return [Action.DUCK_AUDIO]
        if not speaking and self.audio_ducked:
            self.audio_ducked = False
            return [Action.RESTORE_AUDIO]
        return []

    def on_transcript(self, partial: str) -> list[Action]:
        if partial == self.partial_transcript:
            return []
        self.partial_transcript = partial
        self.revision += 1
        if self.speculative_revision is not None:
            self.speculative_revision = None
            return [Action.DISCARD_SPECULATION]
        return []

    def on_decision(self, decision: Decision) -> list[Action]:
        # A prediction about an earlier transcript/audio frame must not affect
        # a newer conversation turn, even if inference finishes out of order.
        if decision.revision != self.revision:
            return []
        actions: list[Action] = []
        if self.assistant_speaking and self.user_speaking and (
            decision.interrupt_probability >= self.interrupt_threshold
        ) and decision.ignore_probability < 0.5:
            self.assistant_speaking = False
            self.response_generation += 1
            self.cancelled_responses.add(self.response_generation - 1)
            actions.append(Action.CANCEL_PLAYBACK)
            if self.audio_ducked:
                self.audio_ducked = False
            return actions
        if self.partial_transcript and (
            decision.end_turn_probability >= self.end_turn_threshold
            and not self.user_speaking
            and self.speculative_revision != self.revision
        ):
            self.speculative_revision = self.revision
            actions.append(Action.PREPARE_REPLY)
        return actions

    def on_assistant_audio_started(self) -> int:
        self.response_generation += 1
        self.assistant_speaking = True
        return self.response_generation

    def on_chunk_played(self, response_generation: int, text: str, *, certain: bool) -> bool:
        """Record delivery acknowledgments from the playback boundary only.

        `certain=True` means the *whole aligned chunk* was confirmed played,
        not merely enqueued or synthesized. If timing is unknown use False.
        """
        if response_generation != self.response_generation or not self.assistant_speaking:
            return False
        if text:
            (self.heard_chunks if certain else self.uncertain_chunks).append(text)
        return True

    def on_assistant_audio_finished(self, response_generation: int) -> None:
        if response_generation == self.response_generation:
            self.assistant_speaking = False

    def commit_speculation(self, revision: int) -> list[Action]:
        if revision == self.revision == self.speculative_revision and not self.user_speaking:
            self.speculative_revision = None
            return [Action.COMMIT_REPLY]
        return [Action.DISCARD_SPECULATION]
