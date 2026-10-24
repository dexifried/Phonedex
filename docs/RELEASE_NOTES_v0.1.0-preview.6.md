# PhoneDex v0.1.0-preview.6 — Outbound Voicemail Beep Screening

**Experimental source prerelease.** No carrier-compatibility certification
or official OpenAI, Codex or AgentPhone affiliation.

## Changes since preview.5

- During **outbound** answered calls, the RTP bridge sends silence while an
  answering-machine detector distinguishes a short live greeting from a
  sustained prerecorded greeting.
- A stable narrowband voicemail beep must be recognized **and finished**
  before the assistant's GPT Live session is released to speak.
- If the greeting appears to be voicemail but no suitable beep is detected
  before the timeout, PhoneDex disconnects without leaving an incomplete or
  premature message.
- A local private 24kHz mono PCM16 wake clip may be configured to prompt
  GPT Live to begin speaking after the beep. No audio clip is bundled.
- `PHONEDEX_VOICEMAIL_MODE=auto|off` can enable or bypass screening.
- Adds 17 completely offline simulated-tone, human-response, media-gating
  and timeout tests. Current Python suite: 74 tests.

## Limitations and privacy

Audio-only heuristics can mistake human greetings or miss atypical voicemail
tones. A real voicemail delivery has **not yet been verified** on a live carrier.
GPT Live's undocumented subscription endpoint can change. Calls may incur
carrier charges; do not auto-dial without permission and comply with calling
rules. No call-recording feature, raw audio logs or production credentials
are added to this release.

For details, see [README](../README.md), [technical notes](TECHNICAL_NOTES.md),
[security](../SECURITY.md) and [release status](RELEASE_STATUS.md).
