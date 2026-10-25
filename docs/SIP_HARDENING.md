# SIP resilience and rapid-redial protection

PhoneDex includes basic **in-memory**, pre-model guards against repeated
short calls, SIP INVITE retransmissions, and unusual bursts of incoming calls.

| Trigger | Default | SIP response |
| --- | --- | --- |
| Same known caller hangs up three times with each call under five seconds within 90 seconds | 60-second caller cooldown | `480 Temporarily Unavailable`, `Retry-After: <remaining seconds>` |
| More than 18 allowed new INVITEs in 10 seconds (across callers) | 5-second global retry suggestion | `503 Service Unavailable`, `Retry-After: 5` |
| Duplicate INVITE for an active, identical dialog | Re-send the cached initial INVITE response | Original `200 OK` (no second model session) |
| INVITE reusing a recently completed Call-ID | Reject for 120 seconds | `481 Call/Transaction Does Not Exist` |
| Remote TCP/TLS closes unexpectedly, or PhoneDex ends the call | No per-caller penalty | Normal cleanup |

**Why reject at SIP rather than playing a voice clip?** A spoken announcement
requires answering and streaming media to the caller, which consumes a call
leg and may incur carrier charges. SIP rejection happens before GPT Live starts
and typically prompts the carrier to play its own error announcement.
PhoneDex **does not currently play** a synthesized or prerecorded
“Please call back in 60 seconds” message; carrier announcements vary.

## Fairness, privacy and limits

- Short-call strikes are recorded only on matching remote BYE/CANCEL
  signaling from the allowlisted carrier. SIP errors, failed call setup,
  ordinary disconnects, and deliberate AI/controller hangups don't count.
- Calls lasting five seconds or longer never count as short-call strikes.
- Anonymous or unparseable caller IDs are not all lumped into one shared
  identity. The global burst cap is a separate safety backstop.
- Caller keys and completed SIP dialog IDs are hashed using a random
  per-process key. They are neither logged nor persisted. Diagnostic logs
  contain only the reason and retry duration.
- Limits reset if the gateway process restarts. This is **not** a distributed
  rate limiter, spam-identification service, or a guarantee against Caller ID
  spoofing. A malicious caller spoofing another caller's ID could temporarily
  trigger a cooldown for that number.
- Windows/Linux behavior should be verified on real carrier traffic. An
  inbound SIP allowlist, TLS, sound networking limits and monitoring are
  still required. Resource limits protect one gateway process, not an entire
  SIP trunk or upstream carrier.
- The global burst limit is deliberately loose to avoid penalizing modest
  legitimate use; tune and monitor it for deployments that legitimately
  serve many simultaneous callers.

## Test without placing a call

```sh
python -m unittest test_inbound_abuse_guard test_sip_teardown
```

The tests simulate SIP invitations, duplicate dialogs, successful short
hangups, malformed/withheld caller IDs, normal calls, TCP drops and global
bursts. No actual PSTN minutes, recordings or credentials are needed.
