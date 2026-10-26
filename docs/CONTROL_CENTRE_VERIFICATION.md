# Control Centre verification — Windows, 2026-10-25

- Public source isolated on `feat/control-centre`; CLI companion on
  `feat/phonedex-dashboard`. No changes were made to the private gateway.
- Python: 189 passed, 2 skipped, 28 subtests passed. Skips require Windows
  symlink privileges; one Starlette/httpx deprecation warning remains.
- TypeScript and production Vite build pass.
- Playwright: all 10 desktop/mobile tests pass using a temporary, synthetic
  backend. Browser plugin was not available; Playwright was explicitly requested.
- Worker route tests pass. No live calls or provider resources were exercised.
- Chromium visual check: 1536×1024 desktop and 390×844 mobile, no page errors,
  no horizontal document overflow. Call detail and navigation were inspected.

## Visual fidelity ledger

Reference: generated `exec-b32967e8-5f90-43f0-891b-cf754b587c77.png`.
Reference and final screenshots were both inspected with `view_image`.

| Comparison | Verification / intentional difference |
| --- | --- |
| Shell | Fixed collapsible sidebar, all thirteen requested labels, top search and status |
| Hierarchy | Broad activity chart, system health rail, recent call table and activity feed |
| Palette | Charcoal surfaces, subtle hairlines, cyan primary actions, violet accents |
| Typography | Local Inter fonts, deliberate control sizes and consistent line spacing |
| Icons | Consistent Lucide outline geometry; waveform brand glyph |
| Responsive | Mobile drawer, stacked panels, scroll-contained tables and focus-safe dialogs |
| Copy/data | Requested labels preserved. “Confirmed spend” separates estimates; “Unverified” avoids claiming a failed gateway without evidence |

Synthetic screenshots intentionally include the explicit demo banner and invented
historical chart data instead of the empty reference state. Local workspace
starts empty. No production records appear in visual evidence. Screenshots and
test traces remain outside committed product source or are git-ignored.

Review fixes verified include completed-utterance redaction for fragmented
identifiers, real model acknowledgements, protected filesystem race handling,
credential-preserving edits, session-bound one-use approvals, cross-instance
call reservations and optional instrumentation failure isolation.

See CONTROL_CENTRE.md for capability limitations. Automatic transcript schema
extraction, dashboard retention pruning, autonomous model-selected vault tools,
carrier billing sync and advanced call controls remain unavailable. Live
carrier/model interoperability and native Linux execution were not verified.
