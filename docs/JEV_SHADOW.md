# Jev full-call shadow observer (experimental)

PhoneDex can run Jev as a **silent observer of the entire conversation**.
GPT Live still speaks. Jev has no control over SIP, audio playback, dialing,
hangups, transfers, or GPT Live steering. Its recommendations are advisory.

## Features

- Separate in-memory Jev observer for each inbound or outbound SIP call.
- Subscribes to caller and GPT Live text transcript events. It receives the
  complete conversation as events arrive, **not raw audio** or carrier states.
- Every inference is given **the whole transcript from the start of the call**.
  A final full-call audit runs after the call ends.
- Tracks conversation state, goals and evidence, unanswered questions, human
  handoff requests, and suggestions to wrap up or retry.
- Runs off the audio path; Jev inference failure does not interrupt telephony.
- Reports an incomplete audit rather than silently truncating transcripts that
  exceed 5,000 events.

## Enable (off by default)

Requires a running Jev service exposing an OpenAI-compatible nonstreaming
chat-completions endpoint. PhoneDex does not include a Jev model or server.

Set the environment variables in the outbound controller or inbound gateway:

    PHONEDEX_JEV_SHADOW_ENABLED=1
    PHONEDEX_JEV_SHADOW_URL=http://127.0.0.1:1234/v1/chat/completions
    PHONEDEX_JEV_SHADOW_MODEL=jev

The URL is illustrative, **not a discovered or verified Jev endpoint**.
Configure the correct URL and model name for your own deployment.

Optional variables:

    PHONEDEX_JEV_SHADOW_API_KEY=
    PHONEDEX_JEV_SHADOW_INTERVAL=6
    PHONEDEX_JEV_SHADOW_TIMEOUT=5
    PHONEDEX_JEV_SHADOW_ALLOW_REMOTE=0
    PHONEDEX_DATA_DIR=

The default 6-second interval debounces transcript chunks; values from 1 to
30 seconds are allowed. Timeout is bounded to 1–10 seconds.

Only loopback HTTP(S) endpoints are accepted by default. Explicitly setting
PHONEDEX_JEV_SHADOW_ALLOW_REMOTE=1 permits a remote HTTPS model endpoint.
This transmits your conversation text to that server; never enable it without
appropriate privacy safeguards. Nonlocal HTTP is rejected.

## Privacy and reports

**Enabling Jev sends call transcript text to the configured model endpoint
even when PHONEDEX_TRANSCRIPT_MODE=off.** These are deliberately separate,
opt-in processing controls. Obtain applicable consent and disclose any
transcription/processing. This observer does not record raw audio.

A local report with call status, structured findings, and evidence excerpts is
written to PHONEDEX_DATA_DIR/jev-shadow/call_<id>.json. It does **not** include
the complete raw transcript, but the findings may contain sensitive data.
Reports are automatically pruned after seven days when another report is saved.
Protect local files according to your operating system's access controls.
This code does not upload any Jev report to Cloudflare.

The observer object persists per call. If the Jev inference server itself is
stateless, each request replays all prior transcript fragments to preserve
whole-call context. This is not a persistent provider-side Jev session.

Jev's labels (including voicemail, IVR, and hold) are text-model inferences,
not carrier-verified detections. The shadow cannot transfer, hang up, redial,
or steer GPT Live. Such behavior requires a separate, authorized task policy.

## Testing

    python -m unittest test_jev_shadow

Offline tests use a mocked Jev model, not the telephone network. Live Jev
operation requires verifying the endpoint and running a separate authorized
test call. Do not deploy based solely on the mock tests.
