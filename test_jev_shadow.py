"""Offline tests for optional whole-call Jev monitoring; no model endpoint required."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from phonebridge.jev_shadow import (
    JevShadowObserver, CallEventFanout, validated_result, validated_url
)

ID = "call_" + "a" * 32
ANSWER = {
    "call_state": "completed",
    "goals": [{"goal": "Price", "status": "answered", "answer": "$103",
               "evidence": "It is $103."}],
    "missing_questions": [], "handoff_requested": False,
    "recommendation": "wrap_up", "summary": "Price confirmed."
}


class SettingsTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(JevShadowObserver.from_env(ID, "Ask a question"))

    def test_requires_local_endpoint_by_default(self):
        for address in ("http://attacker.example/v1/chat/completions",
                        "http://localhost.evil/v1/chat/completions",
                        "https://user:pass@localhost/v1/chat/completions",
                        "file:///tmp/secret", "http://127.0.0.1/v1/other"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                validated_url(address)
        self.assertEqual(
            validated_url("http://127.0.0.1:1234/v1/chat/completions"),
            "http://127.0.0.1:1234/v1/chat/completions")

    def test_explicit_opt_in_and_separate_privacy_setting(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_URL": "http://localhost:1234/v1/chat/completions",
            "PHONEDEX_TRANSCRIPT_MODE": "off",
            "PHONEDEX_DATA_DIR": temp,
        }, clear=True):
            observer = JevShadowObserver.from_env(ID, "Get a quote")
            self.assertIsNotNone(observer)
            self.assertEqual(observer.goal, "Get a quote")
            self.assertEqual(observer.data_dir, Path(temp) / "jev-shadow")

    def test_output_is_validated_and_actions_are_advisory(self):
        result = validated_result(ANSWER)
        self.assertEqual(result["goals"][0]["evidence"], "It is $103.")
        with self.assertRaises(ValueError):
            validated_result({**ANSWER, "recommendation": "execute_transfer"})


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_context_replayed_then_final_report_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = JevShadowObserver(
                ID, "Ask for the price", url="http://localhost/v1/chat/completions",
                data_dir=Path(temp))
            snapshots = []

            def simulated_jev(dialogue, final):
                snapshots.append((list(dialogue), final))
                return validated_result(ANSWER)

            with patch.object(monitor, "_request", side_effect=simulated_jev):
                monitor.on_live_event({
                    "type": "session.input_transcript.delta", "event_id": "e1",
                    "delta": "How much?", "end_ms": 10})
                monitor.on_live_event({
                    "type": "session.output_transcript.delta", "event_id": "e2",
                    "delta": "I'll ask.", "end_ms": 20})
                await monitor._analyze(False)
                monitor.on_live_event({
                    "type": "session.input_transcript.delta", "event_id": "e3",
                    "delta": "It is $103.", "end_ms": 30})
                report = await monitor.finish("ended")
            self.assertEqual([len(x[0]) for x in snapshots], [2, 3])
            self.assertEqual([x[1] for x in snapshots], [False, True])
            self.assertEqual(snapshots[-1][0][0]["text"], "How much?")
            self.assertTrue(report["full_transcript_processed"])
            self.assertTrue(report["advisory_only"])
            self.assertEqual(report["result"]["goals"][0]["answer"], "$103")
            persisted = json.loads((Path(temp) / (ID + ".json")).read_text())
            self.assertEqual(persisted["call_id"], ID)
            self.assertNotIn("dialogue", persisted)
            self.assertNotIn("How much?", json.dumps(persisted))

    async def test_dedup_does_not_depend_on_transcript_storage(self):
        monitor = JevShadowObserver(ID, "Goal", url="http://localhost/v1/chat/completions")
        event = {"type": "session.input_transcript.delta", "event_id": "e1", "delta": "Hello"}
        monitor.on_live_event(event)
        monitor.on_live_event(event)
        monitor.on_live_event({"type": "session.output_transcript.delta", "delta": "Hi"})
        self.assertEqual(len(monitor.events), 2)
        monitor.on_live_event({"type": "session.output_audio.delta", "delta": "not text"})
        self.assertEqual(len(monitor.events), 2)
        with patch.object(monitor, "_request", return_value=validated_result(ANSWER)):
            await monitor.finish()
        monitor.on_live_event({"type": "session.output_transcript.delta", "delta": "Too late"})
        self.assertEqual(len(monitor.events), 2)

    async def test_failure_is_not_reported_as_success(self):
        monitor = JevShadowObserver(ID, "Goal", url="http://localhost/v1/chat/completions")
        monitor.on_live_event({"type": "session.input_transcript.delta", "delta": "Hi"})
        with patch.object(monitor, "_request", side_effect=TimeoutError("unavailable")):
            result = await monitor.finish()
        self.assertFalse(result["full_transcript_processed"])
        self.assertEqual(result["observer_error"], "TimeoutError")

    async def test_fanout_and_outbound_opt_in_with_transcript_mode_off(self):
        from harness.agent_controller import CallControl

        class FakeGateway:
            def event(self, item):
                return {"ok": True}

        with patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_URL": "http://localhost/v1/chat/completions",
            "PHONEDEX_TRANSCRIPT_MODE": "off"
        }, clear=True):
            call = CallControl(FakeGateway(), {
                "call_id": ID, "mode": "normal", "prompt": "Ask for price"})
            self.assertIsNotNone(call.shadow)
            receiver = type("Receiver", (), {"events": [], "on_live_event": lambda self, e: self.events.append(e)})()
            stream = CallEventFanout(receiver, call)
            stream.on_live_event({"type": "session.input_transcript.delta",
                                  "event_id": "e1", "delta": "Any rooms?"})
            self.assertEqual(len(receiver.events), 1)
            self.assertEqual(len(call.shadow.events), 1)
            self.assertEqual(len(call.received), 0)
            self.assertEqual(len(call.local_received), 0)


if __name__ == "__main__":
    unittest.main()
