# First run in PhoneDex

**Goal:** get a new contributor from a clean clone to a useful readiness report,
without needing Cloudflare, spending AgentPhone credits, or making a call.

## 1. Prepare your computer

From a terminal:

```sh
git clone https://github.com/dexifried/Phonedex.git
cd Phonedex
python scripts/bootstrap.py
```

The bootstrap explains its proposed changes and asks before doing them. It
creates an ignored `.venv` under this repository and installs
`requirements.txt` **only** into that environment. It doesn't configure the
router, sign into accounts, deploy Workers, or place calls.

**Windows:** if `python` isn't found, install the official full Python 3.11+
distribution and reopen the terminal. Embedded Python builds may lack
`venv`, which `bootstrap.py --check` explicitly reports. For macOS/Linux,
use `python3` instead of `python` if required by your environment.

A completely read-only first check is:

```sh
python scripts/bootstrap.py --check
```

The `--yes` flag permits just the local environment/dependency installation
in a noninteractive terminal. It does not approve calls, network changes, or
other external actions.

## 2. Sign into your own Codex CLI

Install the Codex CLI separately, then run:

```sh
codex login
```

This opens the normal local authentication flow. **Never share your
`auth.json`, tokens, or passwords**. PhoneDex uses an undocumented
subscription-backed GPT Live endpoint rather than the public OpenAI Realtime
API; access and permitted usage are not guaranteed. Check terms first.

## 3. Check what remains

Bootstrap prints the private venv Python path. Use that interpreter for all
further PhoneDex commands:

| Windows PowerShell | macOS/Linux |
| --- | --- |
| `.venv\Scripts\python.exe phonedex.py setup --check` | `.venv/bin/python phonedex.py setup --check` |
| `.venv\Scripts\python.exe phonedex.py setup` | `.venv/bin/python phonedex.py setup` |
| `.venv\Scripts\python.exe phonedex.py doctor --mode local` | `.venv/bin/python phonedex.py doctor --mode local` |

The first command is read-only. The second is an interactive configuration
wizard for **your own** SIP provider termination address, public IPv4, approved
caller ID, SIP credentials, and destination allowlist. It writes a private
`.env` only after approval and refuses to overwrite an existing one. SIP
passwords are entered without terminal echo, but `.env` is still plaintext
on disk and must be protected.

Cloudflare is **not** necessary for local outbound SIP calling. Remote control
needs a separately deployed Worker and KV store. Only then use
`phonedex.py setup --mode remote --check` or
`phonedex.py doctor --mode remote`.

## 4. Safe first-call checklist

- Verify your carrier supports SIP over TLS and the media profile used by
  PhoneDex (SDES-SRTP with PCMU) before assuming the gateway is compatible.
- Configure your permitted source caller ID and explicitly allowlist
  **only** a recipient you're authorized to call.
- Confirm your own public UDP media port mapping before setting
  `PHONEDEX_MEDIA_PORT_FORWARDED=1`. Neither the wizard nor its checks can
  validate NAT traversal from a real SIP carrier.
- Run a **dry run** (no paid phone call):
  ```sh
  .venv/bin/python phonedex.py dial --to +12025550123
  ```
  On Windows replace `.venv/bin/python` with
  `.venv\Scripts\python.exe`. Replace the example number with an
  allowlisted recipient you control.
- Add `--place-call` only when **you explicitly intend** to place a real
  provider-billed call and understand the model endpoint's limitations.

No setup or dry-run command automatically buys minutes or deploys services.

## Troubleshooting in one glance

| Problem | What to check |
| --- | --- |
| `ModuleNotFoundError: venv` | Use a standard full Python installation, not an embedded/stripped runtime |
| `pip` or wheel install failed | Confirm supported Python version and run bootstrap again; no global packages were modified |
| Codex login not recognized | Confirm Codex CLI installed and sign in locally using `codex login` |
| Worker/token missing | Choose **local** mode unless you're explicitly setting up remote control |
| Can't call after setup | Check allowlist, verified RTP port mapping, provider credentials and SIP/SRTP compatibility; offline checks are not carrier tests |
| Trying to reach an existing mobile number via forwarding | Human call transfer is currently a specification in this public release, not a verified, shipped feature |

If you file a setup issue, include your OS, Python version, the stage that
failed, and only sanitized `setup --check` / `doctor` output.
Do **not** publish `.env`, API keys, bearer secrets, Codex authentication
files, call recordings, or full SIP traffic captures.

For advanced options see [onboarding](ONBOARDING.md),
[release status](RELEASE_STATUS.md) and the [security guide](../SECURITY.md).
