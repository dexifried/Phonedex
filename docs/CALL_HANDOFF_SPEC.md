# PhoneDex — Human Call Transfer and Warm Handoff

**Status:** SPEC ONLY — not implemented
**Last updated:** 2026-10-24
**Priority:** Deferred until the SIP/carrier call-lifecycle work is stable
**Targets:** Portable PhoneDex and, after separate validation, the live Muse gateway

## Objective

When someone asks Muse (GPT Live) to speak to the owner, PhoneDex should connect the caller to the owner's *preapproved personal mobile number*. The owner and original caller should then converse normally. **GPT Live must leave the call completely**: no microphone/RTP feed to the model, no speech generation, no inference, and no lingering WebRTC/sideband session. Once either human hangs up, the human conversation ends — **do not wake Muse to resume the conversation**.

A transfer is not the same as starting an unrelated outbound call to the owner, and a model being muted is not the same as terminating inference.

## Current baseline and confirmed configuration

- Live SIP/TLS inbound calling and GPT Live speech work. The existing end_call/hangup action ends the *telephone call* and therefore is **not** the transfer mechanism.
- Portable PhoneDex has an outbound SIP dialer, but no confirmed human-to-human media bridge, SIP REFER-based call handoff, or three-party conference capability.
- On 2026-10-24, an authenticated AgentPhone SIP trunk inspection returned transfers=sip, transferCallerId=transferee, and signaling transport TLS.
- That configuration **does not establish** that REFER to a Canadian PSTN/mobile number is supported, or that warm conferencing is available. Ask the provider; do not assume.
- The carrier has had unrelated call-record completion and SIP BYE/481 issues. Do not conflate a local model shutdown with confirmed provider call completion.

**This document is not implementation approval.** Do not enable forwarding, change trunk settings, dial a private number, or publish that number or credentials in Git.

## Feature A: cold/blind transfer (first milestone)

1. Caller asks to speak live to the owner. Muse determines whether this is a live-transfer request or a callback/message request. Do not ask unnecessary follow-up questions.
2. Muse says something natural like, “Sure, I'll try connecting you.” Do **not** claim the caller has been transferred.
3. A separate, authorized transfer_call action tells the SIP transfer coordinator to transfer the existing caller to the single owner-approved destination.
4. Prefer provider-managed transfer (e.g., a standards-compliant in-dialog SIP REFER) when proven supported. Do not attempt arbitrary target SIP URIs or phone numbers suggested by callers.
5. While waiting for the transfer, stop unnecessary GPT Live turns. If a holding announcement is needed, use carrier/local media instead of model inference.
6. Observe the *final* transfer outcome, not merely REFER acceptance. A 202/accepted or provisional NOTIFY alone is not proof the owner answered or that the carrier took over.
7. On confirmed takeover, cut off AI audio routing, cancel outstanding model responses and tasks, close GPT Live WebRTC and sideband, release the AI session, and mark the AI task transferred. **Do not issue a BYE that drops the human conversation.**
8. If rejected, unavailable, timed out, or unconfirmed, retain/restore the original caller session if it is still alive; offer to take a message. If the caller has left, cancel further owner dialing. Never falsely report success.

**Blind transfer caution:** A completed dial may lead to voicemail. Do not claim “Austin answered” without a reliable answer/identity mechanism. If human answer is required, use attended transfer.

## Feature B: warm handoff / temporary three-way introduction (second milestone)

1. Muse gets permission to connect the caller with the owner and begins a separate owner-bound SIP leg, retaining the original caller's call.
2. Verify the correct owner has answered; ringing, auto-answer and voicemail are not equivalent to a human taking over.
3. Introduce the two participants briefly, such as: “Austin, I've got someone calling about a project. I'll leave you two to it.”
4. Bridge caller-to-owner media directly or via a provider-managed conference/attended transfer. Immediately exclude Muse from the new human audio path.
5. Fully terminate GPT Live's WebRTC/sideband/model task; do not leave the session running silently.
6. If the carrier assumes ownership, release gateway media resources only when safe. If PhoneDex must keep a local two-human RTP bridge, that bridge continues *without* AI.
7. The humans can talk and hang up without Muse returning. On either human hangup, end/clean up the human call and all billable legs as appropriate. No surprise callback or automatic new AI call.

Warm handoff may require carrier conferencing/attended transfer or a local two-dialog RTP/SRTP bridge. There is no verified implementation yet.

## Separate call ownership and transfer state machine

AI_ACTIVE -> HANDOFF_REQUESTED -> OWNER_RINGING/TRANSFER_PENDING -> HANDOFF_CONFIRMED -> AI_DETACHED -> HUMAN_CALL_ENDED -> CLOSED

On failure: OWNER_RINGING/TRANSFER_PENDING -> HANDOFF_FAILED -> AI_ACTIVE or MESSAGE_OFFER **only if** original caller dialog is still active.

Important invariants:

- AI model-session lifetime is **independent of** telephone session lifetime.
- end_call hangs up the telephone and must never be the successful-transfer API. Define separate transfer_call and optionally warm_handoff actions.
- Correlate by a single canonical call ID, SIP dialog identity and transfer attempt ID; reject duplicate requests, late NOTIFYs and stale callbacks.
- Correctly handle provisional, final-success, final-failure, rejected, no-answer, timeout, caller disconnect, owner disconnect, SIP 481 and transport interruption.
- Only the confirmed handoff transitions to AI_DETACHED. For a local two-human bridge the gateway and human media stay alive, while *all* model resources are gone.
- **Never transition from AI_DETACHED to AI_ACTIVE within the same transferred call.**
- Do not mark success merely because a REFER request got accepted, a new INVITE returned provisional status, or the owner leg started ringing.

## Model and media shutdown contract

At AI_DETACHED:

- Stop forwarding any inbound RTP/PCM or transcripts to GPT Live.
- Stop transmitting GPT Live-generated audio toward either human participant.
- Cancel the current model response and prevent subsequent turns.
- Close WebRTC peer connection, sideband connection, subscriptions, inference/model task and related tool/delegation workers.
- Keep human-to-human audio connected, either carrier-owned or in a separate local bridge.
- Mark status as transferred and record only non-sensitive operational metrics.
- Model frames *after* detachment must be exactly zero. A muted model is a bug, not success.
- Do not record/transcribe the humans' later conversation through the AI agent, and honor applicable consent rules.
- A successful handoff must not cause a BYE to be sent to a dialog that now belongs to the carrier or to the surviving human bridge.

For a local bridge, both SIP legs have their own call IDs, crypto negotiation, RTP destination, SRTP keys, media counters and teardown ownership. Preserve encryption policy; do not silently downgrade SRTP to ordinary RTP.

## Security, privacy and billing

- Fixed private owner destination configured locally (proposed name: PHONEDEX_OWNER_TRANSFER_NUMBER). No real number, private credentials or owner personal details committed to Git.
- Feature gate defaults off (proposed name: PHONEDEX_ENABLE_OWNER_HANDOFF=0). Explicit activation only after carrier capability confirmation and successful offline testing.
- Destination must be E.164, approved by owner and allowlisted. Caller speech and model suggestions must **never** choose arbitrary phone destinations, premium-rate numbers or unrestricted SIP URIs.
- No unauthorized dial. Limit one active owner-ring attempt per original call, enforce ring and total-call timeouts, daily dialing caps and safe failure cleanup.
- Don't expose the owner's private number or live metadata through public API, prompts, transcripts or logs.
- Notify participants and obtain any required consent for joining/recording calls.
- **Inference billing and telecom minutes are separate.** GPT Live stops inference after takeover; SIP/PSTN minutes and possible two-leg charges may continue. Confirm charging and transfer behavior with AgentPhone.

## Provider questions before any real-world rollout

Ask Meet:

> Our trunk currently shows transfers=sip and transferCallerId=transferee. Can our inbound SIP gateway initiate in-dialog REFER to transfer an existing PSTN caller to a normal Canadian mobile number, or would we need transfers=all? What NOTIFY/final signal proves takeover? Can we close our originating SIP/TLS gateway connection after success without dropping the humans? Is attended transfer or a provider conference supported? How is each call leg billed and what happens when the owner does not answer?

Record the provider's actual answer and test results here, rather than inferring support from the trunk setting.

## Implementation plan (deferred)

1. Provider capability confirmation; decide carrier REFER vs. two-leg local bridge. Do not modify live trunk settings.
2. Implement a per-call handoff coordinator/state machine with approved destination config, transfer/failure statuses, and offline SIP REFER/NOTIFY or mock bridge tests. Keep the feature disabled.
3. Cold transfer: validate final successful handoff, fallback behavior, original caller survival, correct BYE ownership and **fully terminated inference**.
4. Warm handoff: independent owner leg, voicemail/answer policy, safe short introduction, human bridge/attended transfer, then permanent AI detach.
5. Run explicit opt-in end-to-end tests with the owner; publish documentation and toggle on only after success.

## Required test scenarios / definition of done

- Offline: transfer accepted but later fails; final success; missing/lost NOTIFY; SIP 481; no-answer, busy, rejection, owner voicemail; caller hangs up during ringing; owner hangs up just after answering; simultaneous hangup/transfer; duplicate commands; race against GPT Live audio completion; unexpected TLS closure; RTP timeout.
- Security: unapproved destination never dials; no phone number or SRTP secrets leak; no transfer attempt crosses calls.
- Real test (only after confirmation): caller requests owner, owner phone rings, humans connect with usable two-way audio, Muse says its brief goodbye/intro if required, then GPT Live closes entirely. Caller and owner continue to speak normally; hanging up ends the call and **Muse never returns**.
- Instrument model_active, model_closed_at, model_input_frames_after_handoff, model_output_frames_after_handoff, transfer_confirmed_at, and human_call_ended_at. Both post-handoff model-frame counters must remain zero.
- If anything is unverified, Muse must honestly say: “I can't transfer you live right now, but I can take a message.”

## Guardrail

**Do not implement this as “call the owner, mute GPT Live, and let everyone keep talking.”** The humans must genuinely be connected, and GPT Live's inference and audio access must terminate. Only a confirmed, safely owned handoff counts as success.
