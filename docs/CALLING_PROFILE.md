# Optional caller profiles and assistant personas

PhoneDex can now keep a **local encrypted personal calling profile** and
**independent, opt-in assistant personas**. None of this is required for basic
SIP calls. Nothing is enabled by default.

## Features available in this branch

| Capability | Status | Notes |
| --- | --- | --- |
| Named AI assistant, accent preference and answering style | Implemented | Optional global or per-agent persona; injected into inbound and outbound prompt only when that persona is enabled |
| Encrypted personal details | Implemented | Local AES-256-GCM vault with passphrase-derived scrypt key, no stored passphrase |
| Per-field disclosure | Implemented | Explicit local confirmation plus passphrase, **only for one manual outbound call** |
| Calls from your own verified caller ID | **Not implemented** | Requires carrier-supported proof of number ownership; changing a SIP From header does not prove caller ID |
| Human callback / press 1 to join / press * to return to AI | **Not implemented in public PhoneDex** | Requires compatible carrier consultation/bridging or REFER/NOTIFY transaction handling; not simulated |
| Transferring back after verification | **Not implemented** | Do not promise resumption unless carrier-level end-to-end tests succeed |

### Enable a simple default persona

```sh
python phonedex.py settings show
python phonedex.py settings set assistant_name Muse
python phonedex.py settings set caller_accent "Canadian English"
python phonedex.py settings set answering_instructions "Keep it friendly, concise, and task-oriented"
python phonedex.py settings set enabled on
```

All values here are **non-secret preferences**. They are stored in
`~/.phonedex/calling-settings.json`, which is not uploaded to the Worker.
A preference is **not** a guarantee that the model can switch voices or accents
through an undocumented voice endpoint; it is a spoken-style instruction.

### Per-agent personas

Each agent gets its own independent opt-in settings:

```sh
python phonedex.py settings --agent muse set assistant_name Muse
python phonedex.py settings --agent muse set enabled on
python phonedex.py settings --agent muse show
```

For **manual outbound** calls, select `--agent muse`. For the standalone
**inbound** gateway, select `--calling-agent muse` when launching it.
For headless remote outbound calls, persona selection has **not yet been
exposed to the Worker controller**, so it uses the default (global) persona.
All agent identifiers must be lowercase alphanumeric with optional `-` or
`_` and are limited to 32 characters.

To turn off personalization:

```sh
python phonedex.py settings set enabled off
python phonedex.py settings --agent muse set enabled off
```

### Create an encrypted personal profile

Install the project requirements (including `cryptography`), then:

```sh
python phonedex.py profile fields
python phonedex.py profile set full_name
python phonedex.py profile set phone
python phonedex.py profile set dental_insurance_carrier
```

Each `profile set` operation prompts **privately** for the vault passphrase
and field value. The very first edit asks you to enter the new passphrase
twice. The passphrase must be at least 12 characters. Never enter actual
profile values in shell arguments or commit them to Git.

```sh
python phonedex.py profile show
```

`profile show` deliberately displays decrypted details **only in your
local terminal**, after you enter the passphrase. Avoid screen sharing or
copying that output. Enter an empty value during `profile set` to remove a
field. Keep a backup of your passphrase: PhoneDex cannot recover it.

The vault is `~/.phonedex/calling-profile.enc.json` by default, or under
`PHONEDEX_DATA_DIR` when set. It contains only the encryption version,
random salt, random nonce and ciphertext—not plaintext personal details or
the passphrase. AES-256-GCM authenticates the contents. The scrypt key is
re-derived on demand; there is no unattended vault-unlock mechanism. Local
OS access controls, disk encryption, security updates and backups are still
your responsibility.

### Share particular fields on one manual outbound call

After authorizing the E.164 recipient in `PHONEDEX_ALLOWED_RECIPIENTS` and
configuring SIP securely:

```sh
python phonedex.py dial --to +17055550123 --mode normal --place-call \
  --agent muse --share-fields full_name,dental_insurance_carrier \
  --prompt "Ask whether the clinic accepts my coverage"
```

The command displays the selected **field names**, asks for the exact
confirmation word `SHARE`, then requests your vault passphrase. It sends
only the selected, non-empty fields to the voice model for that one call.
No other stored fields are retrieved into the prompt. Regular outbound
calls, inbound callers, and Worker-driven remote calls do **not** unlock or
read the vault.

**Disclosure to the model/provider is real disclosure.** Only choose fields
you actually want the other party and the voice service to know; do not use
this feature to share passwords, one-time verification codes, payment cards
or credentials. The model is instructed to share data only as needed, but
its compliance is not guaranteed. Confirm the recipient before disclosure.

**The opt-in may cause a billable carrier call.** The CLI still requires
`--place-call`, SRTP forwarding readiness, a recipient allowlist match
and SIP credentials. A normal dry run never unlocks the vault.

### Carrier-dependent items: not shipped yet

**Verified caller ID / callbacks:** A service can display a personal phone
number only if the SIP carrier supports that number as a verified identity.
Changing the `From` header without provider approval is not sufficient.
Until integrated, the existing configured SIP identity is unchanged.
Business callbacks using your personal number therefore are not guaranteed.

**Human handoff and return:** Press-1 bridging and press-* handback require
a carrier-supported multi-leg session and verified signaling. They have
not been introduced by these changes. Current PhoneDex behavior should
not be presented as a successful human transfer. The intended design is
an optional preapproved owner destination, strict limits, a human acceptance
step, and stateful cleanup of the AI leg—without opening arbitrary dial or
shell commands to the model.

## Test locally

```sh
python test_calling_profile.py -v
python -m unittest test_agent_controller test_harness_live_adapter test_sip_teardown \
  test_onboarding test_barge_in test_agentphone_transcripts test_voicemail
```

These are offline tests and do not place telephone calls. Voice quality,
carrier verified caller ID, and live handoff remain separate end-to-end checks.
