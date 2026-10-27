# AgentPhone call history in PhoneDex Control Centre

The Control Centre now provides a **read-only AgentPhone carrier history** section on
Call History and Transcripts pages, independently of the PhoneDex control-event
history. This prevents the original UI from hiding provider-reported caller numbers,
start times and connected call durations.

## Behavior

- Only authenticated dashboard users can request carrier history or a transcript.
- The carrier-history component retrieves at most 30 inbound and 30 outbound calls,
  sorted by provider start time, and shows the exact provider-reported number when
  available. A known contact name appears **only** if its stored complete phone
  number matches exactly. Caller ID does not prove caller identity.
- Selecting a carrier call fetches its transcript on demand. It is not an audio
  recording; utterances may be inaccurate or absent. No fabricated transcript
  text or estimated duration is displayed.
- Carrier records **are never automatically merged** with PhoneDex control IDs.
  Last-four-digit and timestamp proximity are not strong enough to establish
  identity and could misattribute private conversations.
- Calls and transcripts are not persisted in PhoneDex SQLite, browser local
  storage, or Cloudflare by this adapter. The remote carrier may retain its own
  records under its policies. Local UI state is cleared on navigation/reload.
- There is no outbound calling, transcript upload, call recording or new network
  listener involved.

## Credentials and portability

On the operator's existing Windows installation, the adapter uses the already
configured, current-user-protected DPAPI file
`%APPDATA%/DEXIFYZ/agentphone.dpapi`. It never places its decrypted token in
responses or source files. Other systems can provide an external secret through
`PHONEDEX_AGENTPHONE_HISTORY_API_KEY` in the dashboard process environment.
Do not commit environment values or secrets.

The provider host is fixed to `https://api.agentphone.ai/v1`; requests do not
follow redirects and responses are bounded. API errors expose fixed generic
messages rather than upstream response bodies.

The adapter uses the caller's existing dashboard authentication, does not change
the control backend or carrier routing, and does not affect call concurrency.

## Verification

The synthetic `test_agentphone_history.py` suite covers metadata shape,
caller ID, missing transcript handling, role normalization, unsafe IDs,
redirect rejection and authentication. Run with:

```sh
python -m pytest -q test_agentphone_history.py
cd dashboard && pnpm typecheck
```

A **read-only** live smoke check on October 27, 2026 returned 60 provider call
records (30 inbound), all with number and duration fields. The most recent
inbound record returned three transcript entries. This establishes only that
the available API and local credential work on that installation, not that
every historical conversation can be recovered or that new calls will always
produce transcripts.

## Known limitations

PhoneDex control-event rows may still appear alongside separate AgentPhone
carrier records. The legacy control API does not supply actual carrier duration
or durable transcripts by default; do not treat its configured duration limit
as talk time. There is deliberately no speculative record linking.

Provider call history may have API retention limits. Consult applicable consent
and notice obligations before keeping, exporting or sharing transcripts.
