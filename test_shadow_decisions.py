"""Offline System One adapter tests; never contact TypeSafe or Kev."""
import json
import unittest
from unittest.mock import patch
from phonebridge.shadow_decisions import (
    CHOICES, QUESTIONS, deterministic_decisions, normalize_answers,
    report_from_decisions, run_backend, systemone_payload
)

SAMPLE = [{"speaker":"caller","text":"For billing press 2"}]
def response(winner="ivr", probability=.91):
    others=(1-probability)/(len(CHOICES)-1)
    answers={
        "call_state":{"type":"choice","choice":winner,
                      "probabilities":{k:(probability if k==winner else others) for k in CHOICES}},
    }
    for key in ("goal_complete","handoff_requested","retry_needed","questions_unanswered"):
        answers[key]={"type":"noul","noul":.96 if key=="questions_unanswered" else .05}
    return {"model":"kev-latest","answers":answers}

class BackendTests(unittest.TestCase):
    def test_full_transcript_and_typed_questions_sent(self):
        events=[{"speaker":"caller","text":"My dog needs a room"},
                {"speaker":"assistant","text":"I will check"},
                {"speaker":"caller","text":"Tomorrow evening"}]
        payload=systemone_payload(events,"Ask about dog fee","kev-latest",True)
        self.assertEqual(payload["model"],"kev-latest")
        self.assertIn("Tomorrow evening",payload["state"]["conversation"])
        self.assertIn("My dog needs",payload["state"]["conversation"])
        self.assertEqual(payload["questions"]["call_state"]["type"],"choice")
        self.assertEqual(payload["questions"]["handoff_requested"]["type"],"noul")
        self.assertEqual(payload["state"]["audit_phase"],"final")

    def test_probabilities_normalized_and_thresholded(self):
        parsed=normalize_answers(response())
        self.assertEqual(parsed["call_state"]["value"],"ivr")
        self.assertIs(parsed["goal_complete"]["value"],False)
        self.assertIs(parsed["questions_unanswered"]["value"],True)
        self.assertEqual(report_from_decisions(parsed,"Find prices","kev")["recommendation"],"continue")

    def test_uncertain_results_abstain(self):
        data=response(probability=.42)
        data["answers"]["handoff_requested"]["noul"]=.51
        parsed=normalize_answers(data)
        self.assertEqual(parsed["call_state"]["value"],"unknown")
        self.assertIsNone(parsed["handoff_requested"]["value"])

    def test_invalid_provider_response_fails_closed(self):
        with self.assertRaises(ValueError):
            normalize_answers({"answers":{}})
        with self.assertRaises(ValueError):
            normalize_answers({**response(), "truncated":True})
        data=response()
        data["answers"]["goal_complete"]["noul"]=1.6
        with self.assertRaises(ValueError):
            normalize_answers(data)

    def test_rule_engine_handles_automated_menu_and_handoff(self):
        self.assertEqual(deterministic_decisions(SAMPLE)["call_state"]["value"],"ivr")
        events=[{"speaker":"assistant","text":"I can transfer you to the manager."},
                {"speaker":"caller","text":"No thanks, continue."}]
        self.assertIs(deterministic_decisions(events)["handoff_requested"]["value"],False)
        events.append({"speaker":"caller","text":"Actually, I want to speak to a real person."})
        self.assertIs(deterministic_decisions(events)["handoff_requested"]["value"],True)

    def test_rules_only_never_dials_models(self):
        with patch("phonebridge.shadow_decisions.run_systemone",side_effect=AssertionError("network")):
            value=run_backend(SAMPLE,"Find hours",False,backend="rules",url="",
                              model="",api_key="",timeout=1)
        self.assertEqual(value["engine"],"rules")
        self.assertTrue(value["goals"][0]["status"] in {"pending","answered","unclear"})

    def test_mocked_kev_and_jev_use_same_questions(self):
        from phonebridge import shadow_decisions as sd
        class FakeResp:
            def __enter__(self):return self
            def __exit__(self,*exc):return False
            def read(self,n):return json.dumps(response()).encode()
        class FakeOpener:
            def open(self, request, timeout):
                self.request=request
                self.timeout=timeout
                return FakeResp()
        opener=FakeOpener()
        with patch.object(sd,"build_opener",return_value=opener):
            for engine in ("jev","kev"):
                got=run_backend(SAMPLE,"Find hours",True,backend=engine,
                         url="https://api.typesafe.ai/v1/systemone",
                         model="jev-latest" if engine=="jev" else "kev-latest",
                         api_key="test-key",timeout=1)
                self.assertEqual(got["call_state"],"ivr")
                self.assertEqual(got["engine"],engine)
                self.assertEqual(json.loads(opener.request.data)["questions"],QUESTIONS)

if __name__=="__main__":unittest.main()
