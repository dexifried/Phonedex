"""PhoneDex shadow-engine benchmark: same labeled cases for rules, Kev, Jev.

Synthetic development labels are for smoke testing, NOT scientific evidence of
generalization. No provider calls happen unless explicitly selecting a backend.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phonebridge.jev_shadow import validated_url
from phonebridge.shadow_decisions import run_backend

FIELDS = ("call_state", "goal_complete", "handoff_requested",
          "retry_needed", "questions_unanswered")


def score_cases(cases, backend, *, endpoint="", model="", api_key="", timeout=10.0):
    predictions = []
    measurements = []
    failed = 0
    for case in cases:
        beginning = time.perf_counter()
        try:
            result = run_backend(case["events"], case["goal"], True,
                                 backend=backend, url=endpoint, model=model,
                                 api_key=api_key, timeout=timeout)
            choices = result["decisions"]
            predicted = {field: choices[field]["value"] for field in FIELDS}
        except (ValueError, TypeError, OSError, TimeoutError, KeyError, IndexError) as exc:
            failed += 1
            predicted = {}
            error = type(exc).__name__
        else:
            error = None
        ms = (time.perf_counter() - beginning) * 1000
        measurements.append(ms)
        predictions.append({"id": case["id"], "predicted": predicted,
                            "expected": case["expected"], "elapsed_ms": round(ms, 3),
                            "error": error})
    correct = {field: 0 for field in FIELDS}
    abstained = {field: 0 for field in FIELDS}
    incorrect = {field: 0 for field in FIELDS}
    for item in predictions:
        for field in FIELDS:
            actual = item["predicted"].get(field)
            if actual is None:
                abstained[field] += 1
            elif actual == item["expected"][field]:
                correct[field] += 1
            else:
                incorrect[field] += 1
    def percentile(items, quantile):
        ordered = sorted(items)
        if not ordered:
            return None
        return round(ordered[min(len(ordered)-1, int((len(ordered)-1)*quantile))], 3)
    return {
        "backend": backend, "cases": len(cases), "request_failures": failed,
        "latency_ms": {"p50": percentile(measurements, .50),
                       "p95": percentile(measurements, .95),
                       "max": round(max(measurements), 3) if measurements else None},
        "per_field": {
            field: {
                "correct": correct[field], "incorrect": incorrect[field],
                "abstained": abstained[field],
                "accuracy_including_abstentions": round(correct[field]/len(cases), 4) if cases else None,
                "coverage": round((len(cases)-abstained[field])/len(cases), 4) if cases else None,
            } for field in FIELDS
        },
        "results": predictions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline synthetic call-state benchmarks; optional live model comparisons")
    parser.add_argument("--fixtures", type=Path, default=ROOT/"benchmarks"/"call_states_dev.json")
    parser.add_argument("--backends", default="rules",
                        help="comma-separated: rules,kev,jev (default rules only)")
    parser.add_argument("--kev-url", default="http://127.0.0.1:8009/v1/systemone")
    parser.add_argument("--kev-model", default="kev-latest")
    parser.add_argument("--jev-model", default="jev-latest")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path, help="optional output JSON report path")
    args = parser.parse_args(argv)
    cases = json.loads(args.fixtures.read_text(encoding="utf-8"))["cases"]
    selections = [x.strip() for x in args.backends.split(",")]
    if not cases or not selections or any(x not in ("rules", "kev", "jev") for x in selections):
        parser.error("No fixtures or invalid --backends")
    records = []
    for name in selections:
        endpoint, key, model = "", "", ""
        if name == "jev":
            key = os.environ.get("TYPESAFE_API_KEY", "")
            if not key:
                parser.error("TYPESAFE_API_KEY required to benchmark hosted Jev")
            endpoint, model = "https://api.typesafe.ai/v1/systemone", args.jev_model
        elif name == "kev":
            endpoint = validated_url(args.kev_url, False)
            key, model = os.environ.get("KEV_API_KEY", ""), args.kev_model
        report = score_cases(cases, name, endpoint=endpoint, model=model,
                             api_key=key, timeout=args.timeout)
        records.append(report)
        print(f"{name}: {report['cases']} cases | errors={report['request_failures']} | "
              f"p50={report['latency_ms']['p50']}ms | p95={report['latency_ms']['p95']}ms")
        for field, metrics in report["per_field"].items():
            print(f"  {field}: {metrics['correct']}/{report['cases']} correct, "
                  f"{metrics['incorrect']} incorrect, {metrics['abstained']} abstained")
    if args.output:
        args.output.write_text(json.dumps({
            "disclaimer": "Synthetic development fixtures; not independently held-out real-call evidence.",
            "reports": records
        }, indent=2), encoding="utf-8")
        print(f"Wrote benchmark report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
