import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shadow import Action, Decision, ShadowController
from d1_adapter import D1OmniAdapter
from demo import run_demo


class ControllerTests(unittest.TestCase):
    def test_ducks_before_shadow_finishes_and_cancels_without_wait(self):
        s = ShadowController()
        generation = s.on_assistant_audio_started()
        self.assertEqual(s.on_voice_activity(True), [Action.DUCK_AUDIO])
        s.on_transcript("Wait")
        self.assertEqual(s.on_decision(Decision(s.revision, interrupt_probability=.92)),
                         [Action.CANCEL_PLAYBACK])
        self.assertFalse(s.on_chunk_played(generation, "never heard", certain=True))
        self.assertFalse(s.assistant_speaking)

    def test_stale_decision_never_cancels_current_output(self):
        s = ShadowController()
        s.on_assistant_audio_started()
        s.on_voice_activity(True)
        old_rev = s.revision
        s.on_transcript("No actually")
        self.assertEqual(s.on_decision(Decision(old_rev, interrupt_probability=1)), [])
        self.assertTrue(s.assistant_speaking)

    def test_short_background_noise_does_not_auto_cancel(self):
        s = ShadowController()
        s.on_assistant_audio_started()
        self.assertEqual(s.on_voice_activity(True), [Action.DUCK_AUDIO])
        self.assertEqual(s.on_decision(Decision(s.revision, interrupt_probability=.98,
                                                ignore_probability=.99)), [])
        self.assertEqual(s.on_voice_activity(False), [Action.RESTORE_AUDIO])
        self.assertTrue(s.assistant_speaking)

    def test_unconfirmed_output_not_counted_as_heard(self):
        s = ShadowController()
        generation = s.on_assistant_audio_started()
        self.assertTrue(s.on_chunk_played(generation, "hello", certain=True))
        self.assertTrue(s.on_chunk_played(generation, "there", certain=False))
        self.assertEqual(s.snapshot().heard_text, "hello")
        self.assertEqual(s.snapshot().possibly_heard_text, "there")

    def test_speculation_invalidated_on_transcript_update(self):
        s = ShadowController()
        s.on_transcript("Check the package")
        rev = s.revision
        self.assertEqual(s.on_decision(Decision(rev, end_turn_probability=.95)),
                         [Action.PREPARE_REPLY])
        self.assertEqual(s.on_transcript("Check the package tomorrow"),
                         [Action.DISCARD_SPECULATION])
        self.assertEqual(s.commit_speculation(rev), [Action.DISCARD_SPECULATION])

    def test_talk_while_user_speaks_does_not_commit(self):
        s = ShadowController()
        s.on_voice_activity(True)
        s.on_transcript("hello")
        self.assertEqual(s.on_decision(Decision(s.revision, end_turn_probability=.99)), [])

    def test_adapter_uses_typed_system_one_schema(self):
        class FakeModel:
            def system_one(self, state, questions, media=None):
                self.state, self.questions, self.media = state, questions, media
                return {"answers": {k: {"noul": .8} for k in questions}}
        model = FakeModel()
        s = ShadowController()
        s.on_transcript("hey")
        got = D1OmniAdapter(model).classify(s.snapshot(), "audio.wav")
        self.assertEqual(got.revision, s.revision)
        self.assertEqual(got.interrupt_probability, .8)
        self.assertEqual(model.media, [{"type": "audio", "path": "audio.wav"}])

    def test_demo_is_explicitly_simulated(self):
        result = run_demo()
        self.assertEqual(result["mode"], "offline-simulated-controller-events")
        self.assertEqual(result["heard_text"], "Your order")
        self.assertFalse(result["old_generation_playback_accepted"])


if __name__ == "__main__":
    unittest.main()
