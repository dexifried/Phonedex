# Measured AgentPhone Credit Savings with PhoneDex SIP

**Recorded:** October 24, 2026 · **Project:** [PhoneDex](../README.md) · **Currency:** USD

## Headline result

Using the actual AgentPhone account transactions from October 20–24, 2026:

- **Outbound PhoneDex SIP: ~90.9% lower AgentPhone charges** than AgentPhone's published hosted voice rate ($0.22/min).
- **Inbound PhoneDex SIP: ~98.3% lower AgentPhone charges** than that same hosted voice benchmark.
- **Matched-duration example:** a 23-second hosted call was billed **$0.09000**, while a 23-second SIP call was billed **$0.00768** — **91.5% less**.
- These percentages measure **AgentPhone balance debits/telephony credits only**, which is the metric being compared here.

![AgentPhone cost bar chart: hosted voice $22, webhook voice $13, PhoneDex outbound SIP $1.99, PhoneDex inbound SIP $0.37 per 100 minutes.](../assets/graphs/agentphone-credit-savings.svg)

## Before and after

| Voice path | AgentPhone charge per minute | Cost for 100 call-minutes | Reduction vs. hosted |
| --- | ---: | ---: | ---: |
| AgentPhone **hosted** voice (published) | **$0.22000** | **$22.00** | Baseline |
| AgentPhone **webhook** voice (published) | $0.13000 | $13.00 | 40.9% |
| **PhoneDex outbound SIP** (measured) | **$0.01995** | **$1.99** | **90.9%** |
| **PhoneDex inbound SIP** (measured) | **$0.00370** | **$0.37** | **98.3%** |

AgentPhone's published voice prices: [Usage & Billing](https://docs.agentphone.ai/usage).

## Evidence from actual transactions

| Billing mode | Transactions | Duration from displayed labels | Total AgentPhone debited | Effective cost/min |
| --- | ---: | ---: | ---: | ---: |
| Hosted (Oct 20–22) | 5 | 207s | $0.75 | $0.21739 |
| PhoneDex outbound SIP (Oct 23–24) | 15 | 770s | $0.25597 | $0.01995 |
| PhoneDex inbound SIP (Oct 24, substantive calls) | 4 | 265s | $0.01633 | $0.00370 |

The 15 outbound SIP calls used approximately **12 minutes 50 seconds** and debited **$0.25597** in total. At the published hosted rate, the same time would have cost **$2.82**, a difference of approximately **$2.57**.

**How the rates were calculated:** effective cost/minute = sum of displayed debits ÷ sum of displayed call duration in minutes. Percent saved = `(1 − SIP rate / $0.22) × 100`. Dashboard labels such as “1.4 min” are rounded, so rates are approximate; exact 23-second transactions independently corroborate the outbound result. The four inbound transactions above exclude very short 1-second signaling/test entries so those don't distort the useful per-minute rate.

### Representative same-length comparison

| Path | Date | Duration | AgentPhone debit |
| --- | --- | ---: | ---: |
| Hosted voice | Oct 21, 2026 | 23s | **$0.09000** |
| PhoneDex SIP outbound | Oct 23, 2026 | 23s | **$0.00768** |

That is **91.5% less AgentPhone credit consumed for the same 23-second duration**.

## Anonymized transaction ledger

These are the original dashboard amounts provided by the PhoneDex maintainer. They omit account balances, recipients, caller IDs, call identifiers and transcript content; no private billing tokens are included.

**Hosted voice (before)**

| Date | Dashboard duration | Debit (USD) |
| --- | ---: | ---: |
| 2026-10-22 | 36s | $0.13000 |
| 2026-10-22 | 22s | $0.08000 |
| 2026-10-21 | 23s | $0.09000 |
| 2026-10-21 | 1.5 min | $0.32000 |
| 2026-10-20 | 36s | $0.13000 |

**PhoneDex outbound SIP (after)**

| Date | Dashboard duration | Debit (USD) |
| --- | ---: | ---: |
| 2026-10-24 | 25s | $0.00841 |
| 2026-10-24 | 25s | $0.00839 |
| 2026-10-24 | 28s | $0.00924 |
| 2026-10-24 | 1.4 min | $0.02689 |
| 2026-10-24 | 33s | $0.01108 |
| 2026-10-24 | 44s | $0.01471 |
| 2026-10-24 | 9s | $0.00298 |
| 2026-10-24 | 1.8 min | $0.03633 |
| 2026-10-24 | 2.4 min | $0.04744 |
| 2026-10-24 | 26s | $0.00862 |
| 2026-10-24 | 24s | $0.00824 |
| 2026-10-24 | 1.8 min | $0.03630 |
| 2026-10-24 | 45s | $0.01485 |
| 2026-10-23 | 44s | $0.01481 |
| 2026-10-23 | 23s | $0.00768 |

**PhoneDex inbound SIP (after, excluding 1-second tests)**

| Date | Dashboard duration | Debit (USD) |
| --- | ---: | ---: |
| 2026-10-24 | 50s | $0.00316 |
| 2026-10-24 | 35s | $0.00219 |
| 2026-10-24 | 1.5 min | $0.00544 |
| 2026-10-24 | 1.5 min | $0.00554 |


## What changed

Previously, AgentPhone's **hosted voice** handled each full AI voice call. With PhoneDex, AgentPhone provides **SIP trunk telephone connectivity** while the self-hosted PhoneDex gateway handles the media bridge to the existing GPT Live session. The hosted voice charge is replaced by the markedly lower SIP usage debits above.

**Takeaway:** Based on observed AgentPhone debits, **100 outbound minutes cost about $1.99 instead of $22**, and **100 inbound minutes cost about $0.37 instead of $22**. These are measured AgentPhone credit savings from the October 2026 sample, not hypothetical SIP prices.
