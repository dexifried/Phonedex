# PhoneDex v0.1.0-preview.5 — Barge-in and optional transcript export

**Experimental source preview.** Not a production-ready SIP service, and not an
official or endorsed OpenAI, Codex, or AgentPhone integration.

## What changed

- **Caller barge-in:** the RTP bridge recognizes sustained near-end caller
  speech, discards queued AI speech, sends quiet RTP to maintain telephone media,
  and starts forwarding caller input without waiting for synthetic greeting audio.
  Basic echo similarity checks reduce accidental cuts.
- **Optional AgentPhone call-history text sync:** inbound GPT Live transcript
  turns can be attached to a uniquely matched completed call in a user's
  own AgentPhone dashboard. Opt-in configuration requires the user's own
  AgentPhone API key and phone-number ID, supplied locally outside Git.
- **Safety:** zero-or-multiple provider matches cause no upload; existing
  transcripts are never replaced. No audio recording feature is shipped. The
  optional export has no persistent plaintext spool or historical backfill.
- **Verification:** 57 offline Python tests, Node Worker tests, syntax checks,
  and a tracked-file privacy audit in CI.

## Limitations

The barge-in feature **cuts phone playback locally**, but does not guarantee
that GPT Live's undocumented subscription session cancels its internal
generation. Actual interruption timing depends on echo, SIP carrier media,
and GPT Live turn detection.

The optional transcript exporter is **off by default**. It sends personal
conversation text to an external provider when enabled, and requires lawful
disclosure/consent where applicable. Delivery is best effort while the process
is running; shutdown, caller-ID mismatch, duplicated provider records, or
provider errors can prevent export.

The code has been checked with offline tests. The private deployment's live
behavior does not guarantee compatibility for other carriers or systems.

## Getting started

See the [README](../README.md), [technical notes](TECHNICAL_NOTES.md),
[release status](RELEASE_STATUS.md), [security guidance](../SECURITY.md), and
[third-party notices](../THIRD_PARTY_NOTICES.md).

No bundled tests make chargeable phone calls.
