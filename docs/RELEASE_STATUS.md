# PhoneDex — Experimental Release Status

**Snapshot:** 2026-10-24 · v0.1.0-preview.5 candidate
**Status:** Open-source experimental reference; not production ready.

## What is provided

PhoneDex is a portable Python + optional Cloudflare Worker sample combining
SIP/TLS signaling, an RTP/WebRTC audio bridge, and a Codex-authenticated GPT Live
subscription session. It is a source release, not the private live Muse deployment.

The subscription-backed GPT Live endpoint is undocumented, not an official
public OpenAI Realtime API, and its availability and authorized uses are NOT
guaranteed. Confirm service terms before deploying or making calls.

## Verification

- The initial public source snapshot was copied exclusively from tracked files
  of a previously tested private preview and initialized as a new Git root.
  No old Git commit history, private credentials, or private runtime logs were imported.
- The initial public preview contained 36 tests; the updated suite includes 57 offline Python tests covering barge-in and optional AgentPhone transcript export.
  The suites passed the private source GitHub Actions workflow before migration.
  Public-repository CI is the authoritative check for the newly published repository.
- Cloudflare Worker route tests pass offline; they do not make real phone calls.
- The release audit checks committed filenames/text for common secret patterns,
  private user paths, and image EXIF metadata. This heuristic cannot prove the
  absence of all possible sensitive data or vulnerabilities.
- No carrier calls or production deployments are part of the offline tests.

## Added after the initial public preview

- Portable caller barge-in with queue flushing, RTP silence keepalive, and
  immediate caller input priority over the synthetic greeting.
- Optional AgentPhone transcript export using an explicit private API key and
  phone-number ID; fail-closed matching, no WAV/audio recording, no disk spool,
  no partial uploads or blind retry after an ambiguous POST.
- 21 additional offline tests (57 total), run in CI.

The live private gateway's call confirmed interruptions and provider
transcript upload, but this portable implementation has only offline
validation and is not a certification of third-party carrier behavior.

## Unverified and limitations

- Inbound/outbound calls across arbitrary carriers, NATs and routers.
- Security hardening, broad SIP fuzzing, or a certified SBC.
- Human transfer, warm handoff, or automatic model detach after transfer;
  see [planned handoff spec](CALL_HANDOFF_SPEC.md).
- Fully unattended setup on Windows/Linux/macOS; real SIP credentials,
  networking and Cloudflare provisioning remain manual.
- Users are responsible for SIP billing, disclosure and consent.

## Safe setup

Read [README](../README.md), [ONBOARDING](ONBOARDING.md) and
[SECURITY](../SECURITY.md). Run the guided check and wizard from the README.
No setup check dials a phone number.

The source has no affiliation or endorsement from OpenAI, Codex or AgentPhone.

## Offline checks

Install the Python dependencies in an isolated environment, then run the
Python unittest files, the Node Worker test, and the release audit per README.

## Attribution

PhoneDex is MIT licensed with attribution to Hermes Talk preserved in
[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md) and the upstream license.
