"""Optional d1-Omni adapter following Liquid AI's published `system_one` API.

Loads models lazily. The audio path can be supplied as a 16-bit PCM WAV file.
This worker should run in a separate process from the low-latency audio path.
"""
from __future__ import annotations

from shadow import Decision, Snapshot

QUESTIONS = {
    "interrupt": {
        "type": "noul",
        "instructions": "Is the human speaking now intentionally interrupting the assistant rather than briefly acknowledging it?",
    },
    "end_turn": {
        "type": "noul",
        "instructions": "Has the human finished their utterance and handed the conversational turn to the assistant?",
    },
    "ignore": {
        "type": "noul",
        "instructions": "Is the audible speech probably unrelated background speech or noise?",
    },
}


class D1OmniAdapter:
    def __init__(self, model, *, model_name: str = "LiquidAI/d1-Omni-2B"):
        self.model = model
        self.model_name = model_name

    def classify(self, snapshot: Snapshot, audio_path: str | None = None) -> Decision:
        context = {
            "partial_transcript": snapshot.partial_transcript,
            "user_speaking": snapshot.user_speaking,
            "assistant_speaking": snapshot.assistant_speaking,
            "heard_assistant_text": snapshot.heard_text,
            "possibly_heard_text": snapshot.possibly_heard_text,
        }
        media = [{"type": "audio", "path": audio_path}] if audio_path else None
        # Official model API: returns typed yes/no probabilities under 'noul'.
        result = self.model.system_one(context, QUESTIONS, media=media)
        answers = result["answers"]
        return Decision(snapshot.revision,
                        interrupt_probability=float(answers["interrupt"]["noul"]),
                        end_turn_probability=float(answers["end_turn"]["noul"]),
                        ignore_probability=float(answers["ignore"]["noul"]))
