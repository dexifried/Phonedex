# PhoneDex — Experimental Release Status

**Snapshot:** October 25, 2026 — preview.7 baseline plus newer portable updates
**Status:** Source-only experimental prerelease; not production ready.

## What's included

PhoneDex is an experimental, self-hosted Python SIP/TLS and SRTP/RTP bridge
to a Codex-authenticated GPT Live subscription session, with optional
Cloudflare Worker control. The GPT Live subscription endpoint is
undocumented, and availability and permitted use may change. Check the
applicable terms before deployment.

This is a **public portable reference**, not a dump of the private running
Muse gateway. It does not include production credentials, private caller
logs, or a fully verified carrier handoff.

## Portable updates after preview.7

- Optional per-agent phone personas and **passphrase-encrypted local calling
  profiles**, with explicit per-field selection for one manual outbound call.
  No unattended or remote Worker vault access.
- Conservative outbound voicemail re-entry handling for a receptionist who
  answers after a recorded greeting. Offline-tested; not verified on every
  carrier or real voicemail system.
- Concise, task-focused generic call prompts, with optional Windows-first
  onboarding from the separate PhoneDex CLI 0.1.1.
- Caller-profile regression tests included in the public CI workflow.

**Still excluded:** unfinished Jev/Kev observers, unverified human transfer,
private gateway configuration, credentials, live caller data, and the
in-progress dashboard. The source remains an experimental preview.

## What's new in preview.7

- **Simpler onboarding:** opt-in local virtualenv bootstrap, read-only
  prerequisites, mode-aware diagnostics and first-run troubleshooting.
  Cloudflare is optional for local outbound calling.
- **Transcript privacy controls:** configurable `off` (default),
  `local`, `worker` and `both` modes; local transcripts go into a
  plaintext SQLite database with configurable retention and CLI commands.
  The independent opt-in AgentPhone provider transcript exporter remains
  separate; privacy mode `off` does **not** override an explicitly enabled
  provider exporter.
- **Inbound SIP resilience:** rate limiting for repeated very short
  caller-confirmed hangups (three under-five-second calls within 90
  seconds -> 60-second cooldown), global burst throttling, duplicate
  INVITE retransmission handling and completed-dialog replay protection.
  Rejected calls get SIP `Retry-After` rather than a voice announcement.
- **Linux roadmap:** initial Arch/EndeavourOS validation planned; not yet
  tested or advertised as fully supported.

Previously published preview features include caller barge-in, optional
provider transcript export, inbound and outbound audio bridge prototypes,
and experimental outbound voicemail beep screening.

## Verification

- Public branch: 91 general offline Python tests plus 21 focused SIP
  tests passed in local regression checks. Some focused cases overlap
  the general suite, so these are executions, not unique test counts.
- Cloudflare Worker route tests passed offline.
- The GitHub Actions Python and Worker test jobs both passed on
  the preview.7 pull request (recheck after any subsequent commit).
- The heuristic release audit reported zero issues. That is not a
  security audit, penetration test or carrier certification.
- No PSTN calls, provider media tests or new gateway deployments are
  part of the public release verification.

## Known limitations

- No verified universal carrier compatibility, NAT traversal, SIP
  transaction handling, SRTP media handling or load/performance guarantees.
- No verified human call transfer in this public release. The reference
  handoff spec remains in [CALL_HANDOFF_SPEC.md](CALL_HANDOFF_SPEC.md).
- No sound playback announcement is provided for caller cooldowns;
  carrier-provided intercept behavior varies.
- Callers' displayed phone numbers may be spoofable and can cause
  temporary unfair cooldowns; the limiter is process-local and resets
  after restart.
- Local transcript SQLite is **not encrypted at rest**. Remote SIP
  carriers and GPT Live still process the necessary call data.
- No fully automated phone-provider provisioning or public deployment.
- Outbound voicemail detection is heuristic and has not been verified
  end-to-end on a live carrier.

## Safe setup and development

Start with [README](../README.md), [First Run](FIRST_RUN.md),
[Onboarding](ONBOARDING.md), [Transcript Privacy](TRANSCRIPT_PRIVACY.md),
[SIP Hardening](SIP_HARDENING.md), and [Security](../SECURITY.md).
Running setup checks or a dial dry run never places a paid phone call.

PhoneDex is MIT-licensed with upstream attribution preserved in
[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md). It is **not affiliated with
or endorsed by** OpenAI, Codex, or AgentPhone.
