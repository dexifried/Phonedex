# PhoneDex Control Centre — architecture and implementation

## Investigation (2026-10-25)

The authoritative portable source is `dexifried/Phonedex` main at
`19f29fb`. The existing public-release checkout has unrelated uncommitted
voicemail changes; development therefore uses a fresh public clone on
`feat/control-centre`. The separate CLI repository is `dexifried/Phonedex-CLI`;
its dashboard command is developed on `feat/phonedex-dashboard`.

`phonedex.py` owns offline setup/doctor and guarded outbound dialing.
`onboarding.py` validates configuration. `sip_outbound.py` and
`sip_tls_gateway.py` own SIP/TLS and SRTP; `bridge.py` and
`phonebridge/subscription_live.py` own audio and GPT Live. The public
`harness.agent_controller.CallControl` already exposes status, transcript
events, hangup and private steering. `harness.transcript_store` is opt-in
SQLite transcript storage. `harness.delegation` supports a conservative
business tool adapter; GPT Live delegation does not supply arbitrary tool
arguments. The Worker is optional, token-authenticated remote control.
No dashboard APIs, local authentication, contact/agent registry or vault exist.
Human transfer/conferencing are specifications, not implemented capabilities.

The private archive and custom production gateway are separate products and
are not imported, modified, restarted or connected automatically. No private
configuration, history or secrets are copied. No provider resources or calls
are created during development or tests.

## Design and approach

Use a native optional FastAPI management layer alongside the existing Python
core, React/TypeScript/Vite frontend, local SQLite metadata and resumable SSE.
A separate telephony service would duplicate the core; wrapping the remote
Worker alone would make local mode unnecessarily depend on Cloudflare.
The native layer preserves core ownership and works with no carrier configured.

Visual reference: generated `exec-b32967e8-5f90-43f0-891b-cf754b587c77.png`.
Charcoal `#0b0e14`, sidebar `#0d1119`, cyan `#54d9ef`, violet secondary,
hairline borders, 12px panels, 14px UI typography, accessible focus rings.
Sidebar, top search, quiet health status, four overview metrics, broad activity
chart, system-health rail, recent-call table and activity feed. Required live
detail, vault, forms, dialogs and mobile continuation share those tokens.
All UI is code-native. Empty production workspaces show zero counts and
unknown outcomes/costs. Synthetic fixtures require an explicit isolated demo.

## Security and runtime contract

- Loopback-only binding, strict Host/Origin checks, HttpOnly SameSite=strict
  sessions, CSRF header, password scrypt, login throttling, bounded sessions.
- First-run operator-password setup; reauthentication before revealing vault
  fields, granting disclosure, exporting transcripts or dialing/hangup.
- AES-GCM vault encryption. Windows protects the random key with user DPAPI.
  Linux uses OS keyring where possible or an explicitly supplied passphrase
  to wrap the key with scrypt + AES-GCM; no plaintext key fallback.
- Every vault permission names one field, call, exact recipient, purpose and
  expiry, is revocable, and audits access without values. Runtime supplies
  call/recipient, never the model. No vault added to prompts. Missing fields
  fail closed; no natural-language guessing of IDs. Redact configured values
  before persistence/export. Spoken authorized disclosure remains irreversible.
- Dashboard never starts/restarts the gateway. Opt-in core instrumentation
  writes sanitized call events locally. No raw model reasoning is captured.
- Supported controls: hangup and private instruction, only when an active
  instrumented call advertises them. Transfer, hold, mute and conference stay
  unavailable. Scheduled dialing and messages remain disabled.
- Provider configuration is validated locally using core validators; credentials
  are encrypted and returned only as presence flags. No automatic activation.
- Telephony runs through existing `sip_outbound.dial` after explicit preflight,
  allowlist, duration, single-call concurrency and fresh one-use dial approval.
  Development never invokes that operation against a real provider.

## Shared interfaces

`controlcentre.store.Store(data_dir: Path)` owns `control.sqlite3` migrations.
`record_call(dict)`, `add_event(dict)`, `transcript(call_id, speaker, text, ms,
event_id)`, `queue_command(call_id, kind, payload)`, `pending_commands(call_id)`
and `ack_command(command_id, state)` are the core bridge boundary. SQLite
records contain normalized call objects: id, contact, number_masked, direction,
agent_id, agent_name, provider, state, started_at, ended_at, duration_seconds,
outcome, task_success (nullable), cost (nullable), cost_kind, transcript,
timeline, results, controls. Never infer task success from connection completion.

`controlcentre.vault.Vault(data_dir: Path, passphrase: str | None = None)` owns
encrypted fields and grants. `profiles()`, `create_profile(name)`,
`put_field(profile_id, key, label, value, sensitivity)`, `fields(profile_id)`,
`reveal(field_id)`, `delete_field(field_id)`, `delete_profile(profile_id)`,
`delete_all()`, `grant(field_id, call_id, recipient, purpose, expires_seconds)`,
`revoke(grant_id)`, `grants()`, `request_profile_field(field_id, call_id,
recipient, purpose)`, `audit()` and `redact(text)`.

`create_app(data_dir, core_root, *, demo=False)` returns FastAPI. JSON API under
`/api`, same-origin production static frontend. Authentication: GET auth/status,
POST auth/setup, auth/login, auth/logout, auth/reauth; GET auth/session returns
`{authenticated, csrf, reauth_until}`. POST returns JSON; errors `{detail}`.
GET workspace returns `{health, stats, calls, agents, contacts, providers,
tasks, logs, activity, capabilities}`; GET calls/{id}; GET events is SSE with
numeric id, event `change`, JSON `{revision}` and reconnect snapshot support.
GET analytics?days=30, GET export?format=csv|json. CRUD agents/contacts/providers/
tasks via POST collection, PATCH item, DELETE item. GET vault/profiles,
POST vault/profiles `{name}`; GET/POST vault/profiles/{id}/fields; POST
vault/fields/{id}/reveal, DELETE vault/fields/{id}; GET/POST vault/grants,
DELETE vault/grants/{id}; GET vault/audit; DELETE vault (fresh reauth required).
POST providers/{id}/validate. POST calls/{id}/control `{kind,payload,confirm}`.
POST preflight `{contact_id,agent_id,duration_seconds,prompt}` returns review;
POST dial `{approval_id,confirm:true}` consumes exact preflight once. Missing
runtime capabilities return explicit 409/501, never fake success.

## Implementation plan

> For agentic workers: use the subagent-driven-development and
> dispatching-parallel-agents skills for independent frontend/backend/vault
> units. Shared contracts above are binding; do not edit another unit's files.

1. [x] Backend (`controlcentre/{store,models,auth,app,analytics}.py`,
   `test_controlcentre_api.py`): write failing auth/state/security/API tests;
   implement migrations, API, SSE replay, offline summaries, CRUD, provider
   validation, preflight and commands. Verify offline tests.
2. [x] Vault (`controlcentre/{crypto,vault}.py`, `test_controlcentre_vault.py`):
   failing encryption, tampering, scope, expiry, revocation and redaction tests;
   implement protected keys, fields and audit. Verify offline tests.
3. [x] Frontend (`dashboard/`): React components and typed API; complete all
   requested navigation, real overview, calls/transcript/detail, vault CRUD and
   approval, agents/providers/contacts, analytics/logs/settings. Create browser
   tests with synthetic data; typecheck/build and verify real interactions.
4. [x] Core integration (parent-owned `controlcentre/{integration,runtime,
   launch,fixtures}.py`, existing core/CLI entrypoints and hooks): opt-in
   lifecycle/utterance events, command consumption, safe local dial adapter,
   CLI dashboard launch/headless/graceful shutdown, optional setup and demo.
   Write failing fake-transport tests; verify no live calls/process changes.
5. [x] Integration and review: run existing Python/Worker suites, dashboard
   tests, type generation, production frontend build, Playwright desktop/mobile
   workflows and screenshot comparison. Fix findings; document security,
   installation, public/private boundary, capability limits and launch commands.

## Review focus

Reconnect duplicates; stale calls after crashed gateway; transcript storage off;
grants used for another recipient/purpose/session; credential-bearing validation
errors; CSV formula injection; expired/replayed dialing approvals; no hidden
reasoning; non-loopback/DNS rebinding; empty workspaces and mobile overflow.
