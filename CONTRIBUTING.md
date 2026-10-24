# Contributing

Thanks for helping improve PhoneDex. The repository is an experimental reference implementation; keep changes narrow, document provider assumptions, and distinguish implemented behavior from planned work.

## Local checks

Install the Python dependencies from `requirements.txt`, then run from the repository root:

```sh
python -m unittest test_agent_controller test_harness_live_adapter test_sip_teardown
node worker/test-agent.mjs
```

These suites are offline. They must not make phone calls or contact a live SIP provider.

## Safety and privacy

- Never include `.env`, Codex authentication files, SIP credentials, private business profiles, local databases, logs, phone numbers, or live call transcripts in a commit or issue.
- Keep outbound calling default-deny and preserve the explicit `--place-call` gate.
- Do not use a live call or production gateway as a test fixture.
- Treat `docs/CALL_HANDOFF_SPEC.md` as planned behavior, not as approval to enable a transfer.

For a security concern, follow the private reporting guidance in [SECURITY.md](SECURITY.md). Preserve upstream attribution and notices when changing adapted code; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
