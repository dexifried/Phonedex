# PhoneDex setup — progressive onboarding

**Status:** Initial offline setup wizard; full zero-touch deployment is future work.
**Safety:** Running this wizard never purchases minutes, dials a telephone number,
installs software, configures a router, creates cloud resources, or deploys code.

## Start here

Start with the [first-run quickstart](FIRST_RUN.md). From the project
directory, prepare a private Python environment:

```sh
python scripts/bootstrap.py            # asks before local dependency installation
python scripts/bootstrap.py --check    # read-only alternative
```

After that, use the `.venv` Python executable printed by the bootstrap:

```sh
# These examples assume your shell is already using the project .venv:
python phonedex.py setup --check
python phonedex.py setup
```

Outside an activated environment, use `.venv/Scripts/python.exe` on Windows
or `.venv/bin/python` on macOS/Linux instead of `python`.

The first command prints a **read-only checklist**. The second interactively
writes a new private `.env` (only when explicitly confirmed). Use a normal
terminal for interactive setup. Existing `.env` files are never overwritten.

The wizard asks only for:

- SIP or SIPS termination URI provided by your carrier
- Publicly routable IPv4 address for the media endpoint
- Provider-approved outbound caller ID (E.164, like +12025550123)
- SIP username and password (password entered without echo)
- Approved outbound recipients (E.164, default-deny)

PhoneDex stores secrets in a local `.env` file. This file is Git-ignored.
Treat it as sensitive, protect your Windows/OS account and disk, and **never
share it**. Do not use credentials copied from someone else's installation.

**The wizard leaves PHONEDEX_MEDIA_PORT_FORWARDED=0.** You must personally
confirm the correct UDP media port forwarding before setting it to 1.
The wizard cannot tell whether your ISP/router forwards inbound UDP packets
and does not open Windows Firewall or alter your router.

## Choose a path

| Path | Command | Infrastructure |
|---|---|---|
| **Local outbound calls** | `python phonedex.py setup` | Python, Codex login, compatible SIP provider and UDP media forwarding |
| **Remote agent control** | `python phonedex.py setup --mode remote` | Above, plus Cloudflare Worker, KV and separate bearer secrets |
| **Inbound calls** | Manual setup in README | Advanced: provider inbound route, trusted TLS, carrier IP allowlist, SRTP and public media routing |

**Cloudflare is not required for local outbound calls.** Do not make users
deploy a Worker before they can test local setup.

Run `python phonedex.py setup --check --mode remote` to see the
additional Worker requirements.

## Finish local setup

1. Install a full Python 3.11+ distribution; run `python scripts/bootstrap.py` to install dependencies into an ignored `.venv`, not system Python.
2. Install the Codex CLI and run `codex login` with an authorized account.
3. Complete the wizard with your provider's information, manually configure
   and test UDP forwarding for the port in `.env`, then change
   `PHONEDEX_MEDIA_PORT_FORWARDED` to `1`.
4. Check readiness with `python phonedex.py setup --check`.
5. Use `python phonedex.py dial --to +12025550123 --duration 45` to verify
   the local **dry run**; add `--place-call` only if you explicitly want
   a real call to a recipient you are authorized to contact.

Even after all checks show OK, a SIP carrier may reject the call or charge
minutes. It is not a substitute for an end-to-end authorized call test.

## Finish remote control (optional)

Remote control remains a manual, separately authenticated Cloudflare setup:

1. Create Cloudflare KV, copy `worker/wrangler.toml.example` to
   `worker/wrangler.toml`, and supply your own KV namespace ID.
2. Deploy with Wrangler and set independent `AGENT_CONTROL_TOKEN` and
   `VOICE_GATEWAY_TOKEN` Worker secrets. Never publish them.
3. Store the gateway Worker URL and the matching gateway-only secret in
   your private `.env`, then run `python phonedex.py setup --mode remote --check`.

The wizard does **not** deploy the Worker and doesn't generate or transmit
secrets on your behalf. Cloudflare account login and paying for telecom minutes
are separately controlled steps.

## Known onboarding gaps / prioritized improvements

1. **Dependency bootstrap (implemented):** `scripts/bootstrap.py` checks Python/venv, asks before installing project-local packages, and reports missing standard `venv` support. Wheel availability is platform-dependent.
2. **Guided Cloudflare deployment:** With explicit Cloudflare login/permission,
   provision KV, set two independent secrets, deploy Worker and verify HTTPS
   health. Keep private credentials local and never log them.
3. **Network preflight:** Perform safe UDP/SRTP connectivity checks against a
   documented carrier test endpoint. Neither guessed public IPs nor a user-set
   port-forwarding flag prove that inbound UDP actually works.
4. **Private credential storage:** Move SIP credentials and Worker gateway
   secrets from plaintext `.env` to an OS-specific credential vault, retaining
   an explicit export/migration path.
5. **Package distribution:** Signed releases or installer binaries for Windows,
   Linux and macOS, with a tested update/uninstall workflow.
6. **First-call verification:** Show model/SIP readiness, call progress, hangup
   completion and estimated telecom billing, without revealing caller data.

Do not claim one-click installation, automatic compatibility with every SIP
provider, working call transfers, zero billing, or permission to use an
undocumented GPT Live endpoint.

## Acceptance criteria for the initial wizard

- Works with the Python standard library before dependencies are installed.
- A read-only check places no calls and prints no passwords or OAuth tokens.
- Validates SIP target, public IPv4, caller ID and destination allowlist.
- Refuses to overwrite an existing `.env`; no silent router/firewall changes.
- Separates local calling and optional Cloudflare remote control.
- Respects default-deny outbound calling. All live calls require explicit
  `--place-call` and provider-supplied credentials.
- Remains separate from the live DEXIFYZ/Muse production gateway.
