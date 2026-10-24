# PhoneDex v0.1.0-preview.4 — Public Experimental Source Preview

This release starts the publicly available PhoneDex repository with a fresh,
independently created Git history. No legacy private commits or tags are imported.

## Included

- Original PhoneDex branding, hero image and architecture diagram.
- SIP/TLS and PCMU/SDES-SRTP experimental media gateway.
- GPT Live WebRTC audio via local Codex login. The subscription endpoint is
  UNDOCUMENTED, its ongoing availability is unknown, and use is not
  automatically authorized by having a subscription.
- Optional Cloudflare Worker/KV agent control, transcripts and hangup.
- Agent-assisted setup prompt, offline setup/check wizard, sample
  configuration, security guidance and contribution docs.
- Offline Python and Worker tests plus a privacy audit in CI.

## Trying the preview

Start with the [README](../README.md) and [onboarding guide](ONBOARDING.md).
No bundled test command places a real phone call. Live use needs an authorized
SIP provider and networking configuration and may incur charges.

## Limitations

This is not a production-ready telephony system, hardened SBC, or an official
OpenAI/AgentPhone integration. Real provider compatibility is not certified.
Cold/warm human transfers, conferences and complete AI-to-human handoff are
planned but not implemented.

Read [release status](RELEASE_STATUS.md), [security](../SECURITY.md) and
[third-party licenses](../THIRD_PARTY_NOTICES.md) before using this code.
