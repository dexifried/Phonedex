# PhoneDex call-shadow backends (experimental)

PhoneDex can shadow-monitor the **entire text transcript** of each GPT Live
call, without sharing the audio path. Every snapshot includes the conversation
from the beginning. The shadow is advisory: it never transfers, dials, steers
GPT Live, or hangs up calls. It monitors both inbound and outbound calls.

## Important API distinction

**TypeSafe Jev and Jared Palmer's Kev models are decision models, NOT chat
completion generators.** Both expose `POST /v1/systemone` with `state` and
typed `questions` (`choice`, `noul`, `score`). The prior chat/completions
prototype was incompatible and has been replaced. Their responses contain
probabilities, not free-form factual summaries or price extraction. Our
structured outputs contain classifications, pending goals and confidence,
but MUST NOT pretend to extract verbatim answers automatically.

## Choose a backend

| Mode | Backend | Hardware / credentials |
| --- | --- | --- |
| `rules` | CPU-only deterministic regex state classifier | no model or API key |
| `kev` | Local Kev-4B, Kev-0.8B or other compatible serving checkpoint | local System One endpoint |
| `jev` | Hosted TypeSafe Jev | TypeSafe API key (bring your own) |

Kev-4B's maintained model card recommends approximately 16 GB of available
GPU RAM for CUDA. Kev-0.8B can run on a 4 GB GPU but has lower published
out-of-domain accuracy. Kev is open-source Apache-2.0; PhoneDex DOES NOT
bundle Kev weights or license an external model automatically.

Enable the observer only in a process whose calls are authorized to be
transcribed and evaluated. For all backends:

    PHONEDEX_JEV_SHADOW_ENABLED=1
    PHONEDEX_JEV_SHADOW_BACKEND=rules

For hosted Jev set `PHONEDEX_JEV_SHADOW_BACKEND=jev` and `TYPESAFE_API_KEY` in
your process environment. The HTTPS endpoint is fixed at
`https://api.typesafe.ai/v1/systemone`, model `jev-latest` by default.
PhoneDex never includes a shared TypeSafe key.

If you previously saved the key through JevResearch's Windows DPAPI
vault, you can reuse it **without copying or displaying the key**:

    PHONEDEX_JEV_SHADOW_ENABLED=1
    PHONEDEX_JEV_SHADOW_BACKEND=jev
    PHONEDEX_JEV_USE_JEVRESEARCH_VAULT=1

PhoneDex will attempt to decrypt
`%APPDATA%\JevResearch\provider-key.dpapi` only inside the locally
running process under the **same Windows user** who originally saved it.
This is OFF by default; `TYPESAFE_API_KEY` or
`PHONEDEX_JEV_SHADOW_API_KEY` takes priority when already supplied.
It does not call the remote PC connector to read the vault and never
prints or writes the recovered secret. Unlock failure disables the
observer rather than interrupting a phone call. Presence of the file
does not prove a valid TypeSafe subscription or network access.

For local Kev:

    PHONEDEX_JEV_SHADOW_BACKEND=kev
    PHONEDEX_JEV_SHADOW_URL=http://127.0.0.1:8009/v1/systemone
    PHONEDEX_JEV_SHADOW_MODEL=kev-latest

Start Kev from `https://github.com/jaredpalmer/kev` following the project's
instructions. Requires Python 3.12/3.13 and `uv` in the Kev environment.
Example (in Kev repo):

    uv sync --extra serve
    uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b@v1.0 --port 8009

Smaller local alternative: replace the run argument with
`jaredpalmer/kev-0.8b@v1.0`. Kev-4B may not fit a GPU with 8 GB VRAM;
hardware benchmarks must measure load time and VRAM as well as inference.

`PHONEDEX_JEV_SHADOW_API_KEY` optionally sets a bearer token for a local
Kev server with `KEV_API_KEY` configured, or for Jev if `TYPESAFE_API_KEY`
is not set. Never commit keys or include them in dashboard HTML.

Settings `PHONEDEX_JEV_SHADOW_INTERVAL=6` and
`PHONEDEX_JEV_SHADOW_TIMEOUT=5` control snapshot debounce and request
timeout. Only local Kev HTTP(S) is accepted by default. Explicit
`PHONEDEX_JEV_SHADOW_ALLOW_REMOTE=1` allows a remote HTTPS Kev endpoint;
that uploads call text to the configured server. HTTP redirects are blocked.

## Behavior and limitations

- Every Jev/Kev request replays the full conversation and asks the SAME five
  typed questions: current call state; overall goal complete; human handoff
  requested; retry needed; and unanswered questions.
- Decision probabilities are retained in local reports, together with
  recommendation, call state history, and the final audit status.
- Only probability >=0.72 counts as positive. Probability <=0.28 counts
  as negative; other results abstain (unverified).
- The deterministic engine does not provide calibrated model probabilities.
  Its confidence value represents a rule firing, **not measured reliability**.
- The deterministic baseline uses regular-expression patterns, and can
  mistake normal mentions of voicemail or hold for actual call states.
- No backend extracts prices, check-in times or other arbitrary values
  reliably here yet: goals are high-level states and proposed next actions.
- Jev/Kev may decline oversized state inputs; those calls receive an
  incomplete final audit, not a silently truncated report. Kev-4B's
  validated context for consistent accuracy is around 8,192 tokens, so
  long-call performance requires separate measurement.
- The observer cannot see tone, interruptions, hold music or raw SIP events
  unless future dedicated audio/event integrations are built.
- Reports are saved under `PHONEDEX_DATA_DIR/jev-shadow` and pruned after
  seven days upon the next saved report; protect them appropriately.

**Privacy:** enabling any shadow mode processes call text even if
`PHONEDEX_TRANSCRIPT_MODE=off`. `jev` sends text to TypeSafe; `kev` sends it
to the configured local/remote server; `rules` processes it locally only.
Get informed consent as applicable. The feature is OFF unless enabled.

## Benchmark apples-to-apples

Run deterministic baseline with synthetic labeled development cases:

    python scripts/bench_shadow.py --backends rules

Run all three only after explicitly configuring Jev credentials and Kev:

    python scripts/bench_shadow.py --backends rules,kev,jev --output results.json

The harness reports per-question correct / incorrect / abstained, coverage,
and request latency p50/p95. It uses identical cases for every backend.
**Current sample transcripts are synthetic and development-labeled, not
held-out real calls. Do not present their results as production accuracy.**

Next research step: obtain consented real-call transcripts, label with
independent adjudication, freeze train/dev/test splits, then compare
accuracy, false-positive human transfers, missed voicemail, coverage,
confidence calibration, latency, throughput, cost and memory requirements.

An offline unit test suite and benchmark smoke run are included in CI.
Live Jev or Kev inference has not been verified in this PR.
