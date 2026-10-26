# Connect the running PhoneDex backend

The existing dashboard already has local metadata, authentication, vault and
SIP-worker adapters. The running installation uses PhoneDex's existing HTTPS
operator control API and a separate inbound gateway/outbound controller. Their
protocol is defined by `worker/src/agent_routes.mjs`; optional inbound event
discovery is used only when advertised by that installation. No new carrier
API, SIP implementation or gateway restart is needed.

1. Add an optional operator API adapter with encrypted provider credentials,
   bounded HTTPS requests, explicit capability discovery, adaptive background
   synchronization, deduplicated redacted transcripts, cached history and
   observable connection errors. An optional local dispatched-call index
   discovers outbound IDs without importing private source.
2. Route reviewed calls, hangup and steering through the selected backend.
   Preserve contact permission, duration, fresh reauthentication, session-bound
   single-use approvals and concurrency leases. Use stable request IDs and never
   automatically retry an ambiguous dispatch. Remote vault disclosure stays
   unavailable because this protocol has no scoped profile-field tool.
3. Surface connection details and manual synchronization in Settings; wire
   lifecycle startup/shutdown, configure this machine in private local storage,
   and restart only the dashboard. Verify synthetic HTTP/browser workflows and
   read-only production connectivity; never place a test call.

Tests cover unauthorized/redirect/error responses, bounded input, offline cached
history, transcript deduplication/redaction, stale call controls, provider
selection, remote approvals, one-time dispatch, failed/ambiguous dispatch,
capability limits, authentication and cleanup. Private endpoints, credentials,
numbers, call records and machine paths must remain outside this repository.

## Completion ledger

- Implemented the optional operator adapter and encrypted provider binding.
- Connected this installation with authenticated read-only API requests. Kept
  machine-specific configuration and all real records outside this checkout.
- Added memory-only remote transcripts by default and explicit local caching.
- Verified single-use dispatch, durable ambiguous-call recovery, concurrency,
  delayed transcript reconciliation, active-call priority, redaction, cache
  policy changes and safe adapter shutdown with synthetic tests.
- Desktop/mobile diagnostics, recovery, navigation, vault, contacts and approval
  UI checks passed. Screenshots were inspected outside the repository.
- The legacy RTP keepalive timing test once missed its 90ms window during
  concurrent browser load; the full Python suite passed when rerun separately.
- No production calls, carrier mutations, gateway restart, public deployment or
  changes to the private runtime were performed.
