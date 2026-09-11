---
name: canvas-ledger
description: Requires operator canvases to include exactly one Ledger menu that explains all abbreviations and symbols used anywhere on that canvas. Use when writing or editing daily-focus, movers-YYYYMMDD, movers-3m, book-risk, or other .canvas.tsx briefings, or when the user mentions rng, rr, leftover, glossary, abbreviations, or ledger.
---

# Canvas ledger

Every operator canvas must be readable without the chat. Abbreviations are not optional decoration.

## Required

1. **Exactly one menu named Ledger** (a top-level tab / pill, not a footnote). It lists **every** abbreviation, ticker-status word, and symbol used anywhere on that canvas — tape, value, actions, events, sources.
2. **Do not repeat ledgers on other sections.** Plan, Book, Radar, Value, and the rest stay data. Duplicating Abbr. / Meaning tables on every tab is a defect.
3. If a new abbreviation is introduced in any section, add it to the **one** Ledger menu in the same edit. Do not add a local glossary to that section.

Do not skip Ledger because "the operator already knows rr". Do not paste a short ledger at the top of each tab.

## Standing meanings (copy these; do not invent a second definition)

| Abbr. | Meaning |
|---|---|
| left / leftover | Unused upside %, `max(street PT / close − 1, edge forward upside)`. Not mix. |
| street_px | Analyst PT in dollars, or close × (1 + street leftover %). |
| target_px | Pack leftover in dollars: close × (1 + leftover %). Edge leftover can sit above street. |
| rr | leftover / max(52w range used, 15). High = unused upside vs already-paid range. Not a 0–100 score. |
| rng | Position in the 52-week range, 0–100. High (~85+) = paid. Low = unused range. |
| RSI | Relative strength index, 0–100. |
| bo | Weeks `breakout_long_v1` score. Δbo = change vs the prior pred run. |
| cont | Weeks `quality_continuation_v1`. |
| fwd | Weeks `forward_edge_active_v2`. |
| vs50 | Close vs SMA50, %. |
| 5D / 1M / day | 5-day / 1-month / session price return, %. |
| w | Week return (`Perf.W`). |
| m3 | 3-month return (`Perf.3M`). Dedicated MOVERS (3M) suite, not the DAILY Tape section. |
| relvol | Relative volume vs 10-day average. |
| dte | Calendar days until next earnings (from the all-fields day folder). |
| mix | `manager_action_signal` (e.g. add_long_breakout, avoid_value_trap). Not operator conviction. |
| MTP | Market-timing-policy action: ENTER_SMALL / ENTER_PROBE / WATCH / AVOID_CHASE. Climate, not a buy list. |
| Support | Pack `suggested_conviction` 0.15–0.90 for a named course. Not a probability. |
| Grade | Operator High / Med / Low after reading conflicts. |
| sleeves | How many pack sleeves confirm the name (unpaid, continuation, forming, …). |
| EV/Rev | Enterprise value / trailing revenue. |
| PE ttm | Trailing price / earnings. |
| pe_fwd | Forward PE (often null — then leftover + PEG). |
| PEG | PE / earnings growth. |
| EV/EBITDA | Enterprise value / trailing EBITDA. |
| evrev_vs_ind / pe_vs_ind | % vs industry median multiple. Negative = cheaper (discount). Peer set is US $2B industry, min n=6. |
| left_vs_ind / d5_vs_ind | Leftover or 5D vs industry median. Positive leftover = more unused upside than peers. |
| Val | Tape column: pack `val_label` + `val` (generic_utils.setup). Not always EV/Rev. |
| Peer | Tape column: `val_vs_ind` vs US $2B industry median on that same field. DISCOUNT / PREMIUM / THIN / NA. Not a 0–100. |
| Proj | Tape column: leftover + weeks bo / cont / fwd. |
| Tech | Tape column: `tech_sma` / `tech_rng` / RSI / vs50 / ADX. |
| IS_SUPPORTED | Up-tape: leftover / weeks / Val-Peer still justify the print. |
| CHASE | Up-tape: paid range, leftover gone, premium vs peers. Do not add. |
| UNSUPPORTED | Up-tape: price moved without leftover / weeks / value support. |
| MIXED | Neither clean case (up or down). Canvas may omit down-MIXED. |
| BOUNCE | Down-tape: leftover / unused range / cheap vs industry / live profile can justify a bounce. |
| CONTINUE_DOWN | Down-tape: limited leftover + deterioration — likely to keep falling. |
| down_class | Pack/CLI field on movers laggards: BOUNCE / CONTINUE_DOWN / MIXED. |
| DISCOUNT / PREMIUM | Agent Peer label on the **primary** multiple (`val_vs_ind`). Discount = cheaper than industry median. Not EV/Rev unless `val_field` is evrev. |
| THIN / NA | Peer skipped: industry n < 6, or no quoteable multiple (ETF, missing native field). |
| val_field / val | Multiple `generic_utils.setup` chose to quote. |
| tech_sma / tech_rng | ABOVE/BELOW SMA50; UNUSED / MID / PAID 52w range. Labels, not a score. |
| SMA50 | 50-day simple moving average. If vs50 > 0, first stop below. If vs50 < 0, bounce above — stop is ATR or c15. |
| next below | Nearest structure print still under today's close (SMA50, ATR, or c15). |
| Path 8w | First→last all-fields span (close / EV/Rev / street PT / ATRP). Not a 0–100. |
| close_pct / pt_chg | Path 8w % change in close and street PT. Distinguishes leftover opening vs dying. Multiple path uses `val_field`, not EV/Rev for every name. |
| RAS | Risk-adjusted score (weeks / months). |
| NAV | Portfolio net asset value = equity mark + cash (`capital.nav_usd`). |
| wt / wt_cost | Cost-basis weight of invested USD. Excludes cash. Not risk weight. |
| wt_nav / wipe_nav_pct | Name as % of NAV. Ceiling if the position goes to zero. |
| m5 / m10 / m15 / m20 | Further drop from **today's mark** (USD and % NAV). |
| c15 | Price 15% below **cost** (continuity thesis-kill zone). |
| atr / atrp | Average True Range ($ / %). Structure stop uses 1.5× ATRP. |
| stress_m10_nav | If every Book name is −10% from here, NAV change. |
| US $2B | US-listed names with market cap ≥ $2B (pack universe). |
| Book | Currently held. New = not held. |

Action words (ADD, HOLD, TRIM, EXIT, HOLD_NO_ADD, NEW, WAIT, PASS, SHORT_WAIT, HOLD_THROUGH, BUY_THE_RUMOUR, SHORT_PRE, WATCH_CATALYST, …) stay in the same Ledger menu with one-line meanings.

## Layout

- One Ledger tab per canvas, per iteration. Grouped tables (tape, value, actions, events, sources). No empty groups.
- Other tabs: no Ledger heading, no Abbr. / Meaning table.
- Chart captions still name the metric and units. The Ledger tab does not replace axis labels.

Daily operator canvases: also read `.cursor/skills/daily-operator-scan/SKILL.md`.
