# Security

Phonedex is an experimental reference implementation, not a hardened SIP
border controller. Do not publish a management port on your local machine.
Use only HTTPS for Cloudflare control routes. Use independent random secrets
for agent/operator access and the local gateway. Protect Codex OAuth and SIP
credentials with OS user permissions and do not print them in logs.

**Outbound calls:** the controller refuses all destinations until
`PHONEDEX_ALLOWED_RECIPIENTS` is explicitly populated. A successful API
dispatch acknowledgement only queues a task. The SIP trunk can charge for
phone calls even though there is no automatic OpenAI API billing fallback.
Default rate limit is 12 calls/day and 1 active outbound call.

**Consent:** disclose that calls are handled by AI; comply with applicable
telemarketing, privacy, and call-recording/transcription laws. Obtain consent
where needed, honor opt-out requests and do not cold-call indiscriminately.
Transcripts are stored in Worker KV for a bounded time (24h).
The Cloudflare Worker does not store raw audio.

**Outbound voicemail detection:** Only for authorized outbound calls. A
false positive can cause silence toward a person; an unrecognized beep
causes the call to end without delivering a voicemail. Comply with applicable
calling, consent, disclosure and telemarketing rules. The detector never
saves raw audio and does not enable recording.

**Optional AgentPhone transcript sync:** Disabled by default. Enabling
`PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED` sends text containing possible
personally identifiable details to a third party (AgentPhone) using the
user's own private API key. No raw call audio or optional WAV recording
module is shipped. Protect the token, restrict call access, provide required
notice/consent, and review the provider's retention policy.

**Credential boundary:** `AGENT_CONTROL_TOKEN` is for the calling agent;
`VOICE_GATEWAY_TOKEN` stays in the local controller. Never commit `.env`,
private logs, business profiles, Codex login files or provider keys.

**Undocumented provider:** the subscription-backed GPT Live endpoint is not a
published OpenAI API. It can change, have access restrictions or be unavailable.
Compatibility does not by itself confer authorization; review applicable
service terms. No warranty or official affiliation is implied.

Report vulnerabilities privately to the repository maintainers; avoid
opening public issues with credentials or call transcripts.
