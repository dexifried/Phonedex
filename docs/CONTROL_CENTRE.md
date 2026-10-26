# PhoneDex Control Centre

An optional native, locally hosted management interface for PhoneDex. It uses
the existing Python SIP/TLS/SRTP and GPT Live implementation. No hosted AI
calling service, account provisioning or subscription is added.

## Install and launch

Windows is the verified development target. Python 3.11+ and Node.js 20.19+
or a current supported LTS are required to build the interface. The dashboard
remains optional for all existing core commands.

From the PhoneDex checkout:

```powershell
python scripts/bootstrap.py --dashboard
.\.venv\Scripts\python.exe phonedex.py dashboard
```

Bootstrap asks before installing locally. The optional installer installs
`requirements-dashboard.txt` into this checkout's `.venv`, then builds the
frontend with pnpm (preferred) or npm. It never starts a gateway or places a
call. To install the dashboard after preparing the core:

```powershell
.\.venv\Scripts\python.exe scripts/install_dashboard.py
```

Manual build, using the checked-in pnpm lock:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
cd dashboard
pnpm install --frozen-lockfile
pnpm run build
cd ..
.\.venv\Scripts\python.exe phonedex.py dashboard --headless --port 8787
```

Open **http://127.0.0.1:8787**. First launch asks you to create an operator
password of at least 12 characters. There is no shipped/default password.
Normal launch opens the browser automatically; `--headless` suppresses it.
Use another port if it is occupied. Port conflicts never terminate the
existing listener. Ctrl+C stops the dashboard, leaving existing calls and
gateways running.

For Linux use `.venv/bin/python` in the same commands. Windows behavior is
verified; a native Linux/browser run has not been performed. The browser-test
configuration selects the appropriate interpreter for each platform.

The companion CLI branch also provides:

```powershell
phonedex-cli --checkout C:\path\to\Phonedex dashboard
phonedex-cli --checkout C:\path\to\Phonedex dashboard --headless --port 8787
phonedex-cli --checkout C:\path\to\Phonedex dashboard --install
```

The CLI wizard offers optional dashboard installation when the linked core
contains it. The core checkout must include `controlcentre/`; an older release
returns an actionable error. The CLI delegates to the core, rather than
implementing another calling service.

## Try an isolated synthetic workspace

```powershell
.\.venv\Scripts\python.exe phonedex.py dashboard --demo --port 8789
```

The demo has invented contacts, agents, completed transcripts and estimated
charges, and a persistent visible synthetic-data banner. Dialing is disabled
server-side. A custom `--data-dir` must be empty or already marked as a demo;
the command refuses to mix fixtures with a real workspace. No real credentials,
caller records or carrier connections are needed.

## Connect the public PhoneDex runtime

### Connect an already-running PhoneDex control backend

In Providers & SIP add a `worker` provider with its own HTTPS gateway URL
ending in `/agent/gateway` and the **operator** bearer in the encrypted `token`
field. This is a separate credential from the gateway's polling token. Enable
one SIP/control provider at a time. Settings → Diagnostics shows authenticated
control connectivity and includes **Sync backend**. Configuration validation
remains a local check; synchronization makes actual read-only API requests.

This adapter reuses the existing controller: it never starts or restarts SIP.
It discovers advertised capabilities, subscribes to local SSE for browser
updates, and polls the older upstream API because that protocol has no SSE.
Active calls are checked every five seconds, idle discovery every fifteen;
failures back off. Upstream KV propagation can delay call states, transcripts
and commands. Active calls take priority over historical reconciliation. Recent
terminal calls are reconciled for three minutes to catch final transcript
batches. Old provider records may already have expired; they cannot be
reconstructed. Cached call metadata remains available offline.

Remote transcripts are displayed from a bounded in-memory cache by default;
**Store copies of remote transcripts on this computer** is an explicit provider
option, disabled by default. This option controls importing existing provider
text, independently of native `PHONEDEX_TRANSCRIPT_MODE`. Turning it off does
not erase previously saved copies. Local copies currently have no automatic
pruning. Bulk exports include persisted local records; an individually authorized
call export may include its currently available in-memory transcript. Incoming transcript
fragments are redacted together as well as individually; no secret values or
transcript content enter diagnostic events. Existing upstream retention and
disclosure cannot be undone by local masking.

Optional `PHONEDEX_CONTROL_CALL_INDEX` names a local `dispatched.json` containing
call IDs and timestamps. It is read without importing that runtime's source;
this discovers outbound calls the legacy API cannot enumerate. Inbound event
discovery is used only when advertised. No private installation path or
credential is required by the public source.

An allowed contact and enabled agent template can be reviewed and explicitly
dialed through the selected controller. No SIP credentials are copied into a
second telephony engine. Stable request IDs, persisted pre-dispatch records,
session-bound one-use approval, reauthentication and a shared concurrency lease
prevent accidental retries. An ambiguous response stays unknown and is
reconciled by ID; it is never automatically dialed again. Hangup/private
instruction appear only on freshly observed calls advertising those operations.
Remote scoped vault disclosure is unavailable because this protocol lacks a
profile-field tool. Carrier billing, transfer, conference and messaging remain
unavailable in this interface. Agent records are reusable per-call templates,
not an independent remote agent registry.

### Native public-core event hooks

Historical management works with the gateway disconnected. Starting the
dashboard does **not** connect, start or restart a SIP gateway, controller,
Cloudflare Worker, private production service or model session.

For a future **operator-started public-core process**, opt into events using
the same dashboard data directory:

```powershell
$env:PHONEDEX_DASHBOARD_EVENTS='1'
# Optional when overriding the default location:
$env:PHONEDEX_DASHBOARD_DATA_DIR='C:\path\to\private\dashboard-data'
# Only if you explicitly want local text transcripts:
$env:PHONEDEX_TRANSCRIPT_MODE='local'
```

Then start the normal public-core command yourself at an appropriate time.
Existing running processes cannot acquire these hooks without a separately
authorized future restart. **Never restart a live gateway just to enable the
dashboard.** No private production checkout is imported or controlled.

Instrumented inbound/outbound calls publish state and a heartbeat to local
SQLite. The browser receives resumable SSE revision events, resynchronizes its
snapshot after reconnect, and retains its last loaded history during an outage.
Stale calls become unknown and lose controls; a heartbeat is not proof of
carrier connectivity or an authenticated model session.

The adapter records only allowlisted lifecycle/model/tool event metadata.
Normalized completed utterances are captured from the existing GPT Live item
events. Partial fragments stay in memory until an explicit completion event or
call close, are redacted together, then reach dashboard and legacy collectors.
Hidden model reasoning and raw audio are never captured by the dashboard.
Transcript mode `off` does not persist dashboard transcript text. Provider
export remains a separate core opt-in as documented in TRANSCRIPT_PRIVACY.md.

## Calling and controls

Save a contact and permit that destination explicitly. The number must also
exist in the core `PHONEDEX_ALLOWED_RECIPIENTS` allowlist. Save an agent with
its purpose, supported mode, task instructions and 20–300-second limit.
Configure exactly one enabled outbound SIP provider, or use the public core's
own `.env` configuration. Dashboard credentials are encrypted separately from
the personal vault and supplied only to the approved child process.

**Prepare a call** performs local preflight only. The review shows the
recipient, agent, task, maximum duration, unknown carrier charges and selected
field permissions. Confirm & dial requires fresh operator reauthentication and
consumes a session-bound, single-use approval expiring after two minutes. A
SQLite reservation prevents concurrent dashboard instances from launching two
outbound workers. Actual dialing reuses `sip_outbound.dial`; the dashboard
never calls AgentPhone's hosted-voice endpoint.

Only an answered, freshly observed call can advertise hangup; dialing-phase
CANCEL is unavailable. Private instructions require the actual model sideband.
Queueing and sending are distinct from model acknowledgement or rejection.
Hangup uses the existing SIP teardown, never a process kill. Stopping the
dashboard does not hang up an ongoing call.

## Personal vault

See [vault security](VAULT_SECURITY.md) for encryption, key storage, scope and
deletion details. All field values are masked by default. Revealing, exporting,
granting disclosure and consequential controls require reauthentication.
Only the theme preference is saved in browser localStorage.

Create a profile and encrypted fields, or select fields during pre-call review
with an exact disclosure purpose. Confirmed dialing creates grants for only
those fields, the actual call ID, exact recipient and purpose, with a maximum
five-minute expiry. Values are not placed in the initial prompt.

For an existing call, create/revoke grants in Personal Vault. **Supply to call**
is available only when that call advertises its live sideband capability. It
requests one field through `request_profile_field`, checks the grant against
the runtime's actual recipient and call, and sends only that authorized value
privately to the agent. Revoked, expired, mismatched and missing grants fail
closed. Commands and audit records do not store the value.

Trusted planners can use `DelegationBroker(control).structured_tool_request`
with `request_profile_field` and exactly `{field_id, purpose}`. The runtime,
not the model, supplies call ID and recipient. The undocumented GPT Live
delegation transport does not expose arbitrary structured tool arguments;
automatic inference of identifiers or disclosure purposes from spoken text is
deliberately unavailable. Operator-directed supply is the supported workflow.

Local encryption cannot retract a value once spoken to a third party. Remote
carriers/models process authorized disclosures; redaction cannot guarantee
recognition of paraphrases, spoken digits or copies stored before configuration.
Protect backups and OS accounts. No claim of secure SSD erasure is made.

## Providers and diagnostics

AgentPhone is the first supported SIP configuration preset; generic SIP uses
the same public-core validators. Validation checks format and configuration,
**not** carrier reachability, account balance, phone-number availability, NAT,
SIP/TLS handshake or working SRTP media. No provider API is called automatically.

AgentPhone's official [SIP-trunk reference](https://agentphone.ai/docs/api-reference/sip-trunks/list-sip-trunks-v-1-sip-trunks-get)
documents `/v1/sip-trunks`, transport, media encryption, termination URIs and
credentials. Its [API reference](https://docs.agentphone.ai/api-reference) also
documents usage/call resources. These hosted capabilities are not assumed to
be verified PhoneDex SIP features. This implementation performs no provider
provisioning, number purchases, hosted calls or billing synchronization.

Logs show sanitized local event metadata, severity, component and call ID,
with search, filtering, pause and download. Raw production log ingestion is
unavailable. Port/NAT latency, packet-loss and carrier/media metrics without an
actual measurement are shown as unavailable rather than invented.

## History, results and analytics

History, transcripts and structured result cards use saved local records.
Explicit provenance distinguishes recipient-confirmed values, operator entries
and model inference. Arbitrary nested results are rendered as labelled fields,
not a JSON dump. Core connection completion is never converted into task
success. The public core currently lacks a universal structured task-result
extractor; automatic schema extraction/correction is not implemented.

Analytics include UTC volume/time-of-day, outcomes, duration distributions,
agent activity and recorded errors. Confirmed charges and estimates have
separate totals and coverage; unknown rates/costs remain unknown. Task success
rates use only explicit boolean outcome records. CSV export neutralizes
spreadsheet formulas; JSON/CSV and transcript exports require reauthentication.

Agent templates are local outbound task presets feeding core mode, prompt and
duration. Voice/model selection, autonomous multi-agent routing, per-agent
tool installation and independent retention settings are unavailable. Draft
automations are reusable task preparation; schedules and unattended dialing
are disabled. Messages, hold/mute/pause/resume, transfer and conferencing are
unavailable in this portable core. There is no fabricated working control.

The dashboard does not automatically import old private transcripts or remote
Worker history. Dashboard history begins with explicitly instrumented calls.
Dashboard metadata is retained locally until removed by its operator; automatic
dashboard transcript pruning is not yet implemented. Core transcript retention
continues to apply to the core's separate transcript database.

## Storage and security

Windows defaults to `%LOCALAPPDATA%\PhoneDex\ControlCentre`; Linux defaults to
`$XDG_DATA_HOME/phonedex/control-centre` or `~/.local/share/phonedex/control-centre`.
`--data-dir` or `PHONEDEX_DASHBOARD_DATA_DIR` overrides it. Keep the directory
outside a public repository. Metadata is not fully encrypted; vault values,
grant scopes, SIP credentials and private instructions are encrypted with
separate purpose-bound keys. SQLite schema versioning initializes the local
databases; back up before manual data migrations.

Windows uses current-user DPAPI and a protected user/System DACL. Linux can
use a supported OS keyring (`pip install keyring`) or a strong passphrase
provided privately through `PHONEDEX_VAULT_PASSPHRASE`. No plaintext key
fallback exists. Never put this passphrase in a tracked file or command-line
argument. Lose the key/account or wrapping passphrase and the values cannot be
recovered. Without secure storage, ordinary dashboard/history access works,
while encryption-dependent operations fail closed.

The server binds loopback by default. It validates request peer, Host and Origin,
uses strict HttpOnly cookies, CSRF tokens, scrypt password hashing, throttling,
bounded in-memory sessions and CSP. Sessions expire after eight hours and
reauthentication after two minutes. Public exposure is not supported.

An explicit `--lan --host PRIVATE_IP --lan-subnet PRIVATE_CIDR` enables a
separate HTTPS listener for a restricted private subnet. Example:
`python phonedex.py dashboard --lan --host 192.168.4.144 --lan-subnet 192.168.4.0/22 --port 8808`.
This generates an owner-protected, self-signed local certificate under
`lan-tls/`, uses Secure cookies and rejects clients outside that subnet,
unexpected Host/Origin headers and non-HTTPS requests. Trust that certificate
on your phone or explicitly approve its warning for your own private address.
First-time operator password setup works directly on a phone using a one-time
pairing link. The computer issues a random capability through the loopback-only
`POST /api/auth/pairing` endpoint while no operator password exists. Only its
SHA-256 digest is stored, it expires after one hour, and password creation
consumes it atomically. The UI accepts it from `#pair=...`, immediately clears
the fragment and keeps it only in memory until the HTTPS setup submission.
No password or pairing token is placed in logs or browser storage. Unpaired
LAN setup and replayed/expired links are refused. Windows may require an
administrator-approved, Private-profile inbound rule for that exact TCP port
and subnet. Neither LAN startup nor firewall permission restarts a gateway.
Do not put this app behind a public tunnel or proxy.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install pytest httpx
.\.venv\Scripts\python.exe -m pytest -q
node worker/test-agent.mjs
cd dashboard
pnpm run build
pnpm exec playwright install chromium
pnpm test
```

Browser tests create a temporary synthetic backend and never dial, provision
provider resources, or load real credentials. Tests cover desktop/mobile
navigation, history/results, contact CRUD, vault masking/reauthentication,
unavailable controls and preflight rejection. Python tests cover auth/CSRF,
scoping/revocation/tampering, deduplication, permissions, redaction, approval
replay/concurrency, provider editing, offline events and existing SIP behavior.

The dashboard does not certify the undocumented GPT Live endpoint, carrier
interop or the private running gateway. Live carrier/model calling remains
unverified until the operator separately approves and performs that test.
