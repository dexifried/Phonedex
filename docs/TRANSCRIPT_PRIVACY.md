# Transcript privacy and self-hosted storage

PhoneDex separates **voice transport**, **transcript storage**, and **remote call control**.
You can use an SIP provider and GPT Live without sending model-generated
conversation transcripts to Cloudflare Workers KV or AgentPhone's Calls dashboard.

This is a data-location feature, **not end-to-end private AI or a promise
that the carrier/model provider cannot see call content**.

## What remains external

- The SIP carrier (for example AgentPhone) handles the call and can process
  call media and metadata under the provider's own practices. PhoneDex cannot
  prevent the provider from generating its own call records.
- GPT Live processes the voice audio to generate speech and transcripts; it
  is a remotely hosted model even when PhoneDex runs on your computer.
- If enabled, the remote Cloudflare Worker stores queued dial requests,
  recipient phone numbers, operator prompts, call-state updates and commands
  in your Cloudflare KV namespace. This control data is **not** a transcript,
  but it can still be sensitive.
- Inbound AgentPhone Calls dashboard transcript export is a **separate** option.
  Leave PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED=0 to keep it off.

## Choose your model transcript destination

Set the variable in your **private** local .env or process environment:

| PHONEDEX_TRANSCRIPT_MODE | Local SQLite text | Worker/KV text |
| --- | --- | --- |
| off (default) | No | No |
| local | Yes | No |
| worker | No | Yes |
| both | Yes | Yes |

Set local mode when you want persistent self-hosted history:

```dotenv
PHONEDEX_TRANSCRIPT_MODE=local
PHONEDEX_TRANSCRIPT_RETENTION_DAYS=7
PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED=0
```

The local controller sends **call statuses, steering acknowledgements and
other control metadata** to your Worker as usual when remote control is enabled.
However, only worker and both modes send transcript events there.
The local-mode decision is made before constructing any transcript-upload
request. Disabling the Worker transcript mode **does not disable the Worker
itself** for remotely queued calls.

Provider dashboard export is independent of these four modes. Turning on
PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED=1 with the required private credentials
may send completed inbound transcript text to AgentPhone even if the mode above
is off. To keep transcripts strictly out of AgentPhone's optional dashboard
sync, leave this setting disabled.

**Changing modes affects only future calls.** It does not erase older
transcripts from your Cloudflare namespace, your local disk, or the provider.

## Local files, retention and inspection

In local/both modes, outbound and inbound model-generated transcript fragments
are saved to a SQLite database on your computer, default location:

```text
~/.phonedex/transcripts.sqlite3
```

Set PHONEDEX_DATA_DIR to choose another directory. The database contains
random or relay-generated call IDs, inbound/outbound direction, timestamps,
speaker roles and text fragments; **it does not store phone numbers by design**.
It does not store WAV/PCM/audio recordings or OAuth credentials.

Run these explicitly from the repository root:

```sh
python -m harness.transcript_store list
python -m harness.transcript_store show call_0123456789abcdef0123456789abcdef
python -m harness.transcript_store delete call_0123456789abcdef0123456789abcdef
python -m harness.transcript_store prune
```

Replace the example ID with one returned from list. Each user-initiated
delete removes the call row and its associated local fragments.
In local mode, transcripts can be saved incrementally during an outbound
call, rather than only after it ends. Inbound sessions save their captured
text asynchronously at teardown, so sudden shutdown can lose that session.

The configured retention range is **1–90 days** (default 7). Expired local
sessions are cleaned up during writes, or when you explicitly run prune.
There is **no always-running scheduled deletion service**, so stale records
may remain beyond the retention period until the next write/prune.

### Local data security

The database contains **plaintext**, not encrypted-at-rest transcripts.
On Unix-like systems PhoneDex attempts directory permissions 0700 and
database permissions 0600. On Windows, security depends on the configured
NTFS directory ACL and the Windows user account. Anyone able to read the
database or your backups may read the text. Prefer OS full-disk encryption,
a private account, appropriately restricted backups, and only short retention
for sensitive calls.

Model-generated transcript content can contain errors and should not be
treated as a verbatim legal record. Observe applicable consent/notice laws
before collecting or sharing other people's conversations.

## What is *not* included

- No local speech-to-text model and no guarantee that audio never leaves the
  user's machine: GPT Live is remote.
- No carrier-side retention controls and no deletion through the provider API.
- No removal of historical Worker KV transcript entries when you change mode.
- No local-at-rest transcript encryption, user management UI or cloud backup.
- No change to outbound call allowlists, billing, SIP/RTP, GPT Live connection,
  or audio recording settings.

The privacy modes are entirely local Python controller choices and require no
Cloudflare Worker redeployment or paid feature. The existing Worker transcript
GET endpoint remains available and may return older uploaded records until
their KV TTL expires.
