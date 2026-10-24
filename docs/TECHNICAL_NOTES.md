# PhoneDex technical notes

**Status:** Experimental implementation notes · **Updated:** 2026-10-24

These notes describe the code in this repository. They are not a provider compatibility claim or a production security assessment.

## Media path

1. `sip_tls_gateway.py` accepts SIP over TLS. Its public listener is opt-in and requires an explicit source CIDR allowlist and TLS certificate. The implemented media profile uses PCMU and SDES-SRTP (`AES_CM_128_HMAC_SHA1_80`); plain RTP offers are rejected.
2. The gateway terminates SRTP and forwards the local RTP audio to `bridge.py` / `RTPBridge`. The bridge connects audio to a WebRTC session and returns generated audio to the telephone leg.
3. `sip_outbound.py` initiates outbound SIP/TLS calls. The remote controller invokes it only after checking the local recipient allowlist, daily call cap, and active-call limit.
4. `phonebridge/subscription_live.py` reads the local Codex OAuth cache and negotiates a subscription-backed GPT Live session. The endpoint is undocumented and is not the public Realtime API.

The standalone `bridge.py` and SIP gateway CLIs also expose an echo backend for local development. The application path described above is experimental; neither mode replaces provider-specific SIP validation.

## Control path

- A remote agent calls the Cloudflare Worker with the operator bearer token.
- The Worker validates call arguments and stores queue items, command records, statuses, and transcript text in KV.
- The local Python controller initiates outbound HTTPS polling to the Worker. It does not listen for inbound public control requests and holds the SIP trunk credentials locally.
- A different gateway bearer token authenticates controller polling and event uploads. Do not reuse the operator token.
- Call/control records use a three-hour KV TTL. Transcript batches use a 24-hour TTL. KV consistency can delay when a newly written transcript appears to a reader.

The Worker never receives RTP/SRTP audio or SIP account credentials. It does receive transcript text and limited call metadata.

## RTP interruption and optional transcript publishing

- `phonebridge/barge_in.py` detects sustained near-end speech with a bounded
  energy/zero-crossing heuristic and echo correlation. `bridge.py` invalidates
  queued playout frames, suppresses obsolete model audio and sends quiet RTP
  packets while interrupted. The synthetic wake greeting runs concurrently
  and is cancelled when real caller voice arrives.
- This is a **local audible cutoff**, not a confirmed subscription API
  `response.cancel` implementation. The media bridge only resumes when a
  new model-audio boundary can be inferred; field behavior depends on echo,
  SIP provider buffering, and GPT Live's own turn handling.
- `phonebridge/agentphone_transcripts.py` is disabled unless
  `PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED=1` and both a private API key and
  number ID are supplied. It gathers normalized GPT Live *text* events
  for inbound calls and submits them to the configured user's AgentPhone
  history after a unique provider call match is confirmed.
- Exact caller ID, provider phone-number ID and call-start time are required.
  Zero or multiple provider matches are rejected. Existing provider transcripts
  aren't overwritten. There is no on-disk queue, recorder or audio capture.
- **This public adapter is independent of any private deployment-specific
  recording or credential management.** Its simple in-memory export cannot
  guarantee delivery across restarts or transient API failures.

## Local business data

Business mode can read `harness/business_profile.json` (or the path in `PHONEDEX_BUSINESS_PROFILE`) and a local read-only calendar free/busy snapshot. `harness/tools.py` also contains a consent-gated lead-storage helper that writes to local SQLite under `PHONEDEX_DATA_DIR` (default `~/.phonedex/leads.sqlite3`) and sends no message. That helper is not called by the current live transcript-delegation path; do not describe live lead capture as an available call feature.

Normal mode does not receive the business tools. Neither mode books appointments.

## Local configuration and safety controls

- `phonedex.py` reads simple `PHONEDEX_*` key/value entries from `.env` without evaluating shell syntax.
- `.env`, Codex auth files, local business profiles, SQLite files, logs, and call artifacts are excluded by `.gitignore`.
- Outbound calls are denied when `PHONEDEX_ALLOWED_RECIPIENTS` is empty or the destination is absent from that list.
- `PHONEDEX_MAX_CALLS_PER_DAY` defaults to 12; the controller permits one active outbound call.
- `PHONEDEX_MEDIA_PORT_FORWARDED` must be set to `1` before a real outbound call is placed.

## Code map

| Area | Files |
| --- | --- |
| CLI and config | `phonedex.py` |
| SIP/TLS outbound | `sip_outbound.py` |
| SIP/TLS inbound gateway | `sip_tls_gateway.py` |
| RTP/WebRTC bridge | `bridge.py`, `phonebridge/` |
| Remote call control | `harness/agent_controller.py`, `harness/agent_client.py` |
| Business tools | `harness/tools.py`, `harness/delegation.py` |
| Cloudflare control plane | `worker/src/agent_routes.mjs`, `worker/src/index.mjs` |
| Offline tests | `test_*.py`, `worker/test-agent.mjs` |

## Known boundaries

PhoneDex is not a complete SIP transaction stack, hardened SBC, or provider-certified service. The repository tests use fakes and local fixtures; they do not place calls, test billing, or prove compatibility with a carrier. Human transfer and warm handoff remain a separate, unimplemented [specification](CALL_HANDOFF_SPEC.md).
