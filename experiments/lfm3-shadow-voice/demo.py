"""Offline demonstration of interruption state, not a live model benchmark."""
import json
import sys
from pathlib import Path

# Windows embeddable Python may omit the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shadow import Decision, ShadowController


def run_demo():
    s = ShadowController()
    events = []
    gen = s.on_assistant_audio_started()
    s.on_chunk_played(gen, "Your order", certain=True)
    s.on_chunk_played(gen, "will arrive", certain=False)
    events.append([a.value for a in s.on_voice_activity(True)])
    events.append([a.value for a in s.on_transcript("Wait, I meant tomorrow")])
    snapshot = s.snapshot()
    events.append([a.value for a in s.on_decision(Decision(
        revision=snapshot.revision, interrupt_probability=0.97,
        end_turn_probability=0.1))])
    accepted = s.on_chunk_played(gen, "on Friday", certain=True)
    events.append([a.value for a in s.on_voice_activity(False)])
    events.append([a.value for a in s.on_decision(Decision(
        revision=s.revision, interrupt_probability=0.0,
        end_turn_probability=0.95))])
    report = {"mode": "offline-simulated-controller-events", "events": events,
              "old_generation_playback_accepted": accepted,
              "heard_text": s.snapshot().heard_text,
              "possibly_heard_text": s.snapshot().possibly_heard_text}
    return report


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
