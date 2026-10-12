---
name: canvas-ledger
description: Requires operator canvases to include exactly one Ledger menu that explains every abbreviation and, for coded columns, every allowed value. Use when writing or editing daily-focus, movers, book-risk, playbook, horizon-plan, or other .canvas.tsx briefings, or when the user mentions rng, rr, leftover, mesh, glossary, abbreviations, or ledger.
---

# Canvas ledger

Every operator canvas must be readable without the chat. Abbreviations are not optional decoration.

## Required

1. **Exactly one menu named Ledger** (a top-level tab / pill, not a footnote). It lists **every** abbreviation, ticker-status word, and symbol used anywhere on that canvas — tape, value, actions, events, sources.
2. **Do not repeat ledgers on other sections.** Plan, Book, Radar, Value, and the rest stay data. Duplicating Abbr. / Meaning tables on every tab is a defect.
3. If a new abbreviation is introduced in any section, add it to the **one** Ledger menu in the same edit. Do not add a local glossary to that section.
4. **A code is not explained until its values are.** Any column whose cells are abbreviations, letters, or a string of letters needs, on that one Ledger:
   - the **field**: what the cell is, the allowed character set, the order letters are written, and the empty value
   - **one row per value** that can appear, with the rule that turns it on and what absence of that value means
5. A count or filter built from a code (`nmesh`, `Mesh 3+`) is its own row: it is the number of letters in that field, and the filter threshold is stated. It is not a 0–100 score.
6. "The letters above", "see the header", and "same as the other canvas" are defects. Other tabs do not restate the code key. A header may name the column and point at Ledger. The definition lives only on Ledger.
7. **A bin label is not the size of the gap.** Any table that shows `ABOVE` / `BELOW`, `DISCOUNT` / `PREMIUM` / `INLINE`, or `UNUSED` / `MID` / `PAID` also shows the percent (or the 0–100 range position) in the next column. DuckDB already stores them as separate fields. On a canvas, add the column. Width is fine; the reader can zoom out. This applies to a short decision table and to a data-list table. A label alone covers too wide a band.

Do not skip Ledger because "the operator already knows rr". Do not paste a short ledger at the top of each tab.

## Coded fields (Mesh is the pattern)

`Mesh` is a string of letters, not a score. Copy this shape when a canvas invents a similar column. Do not invent a second on/off rule for these letters.

| Field or value | Meaning |
|---|---|
| Mesh | Letters written in order `F` `C` `P` `D` `T`, only the ones that are on. Example `FCPD` means F, C, P, and D are on and T is off. `-` means none of the five are on. |
| F | On: pack forming sleeve, or weeks `early` ≥ 0.4 with `rng` under 55. Off: neither. |
| C | On: earnings `dte` ≤ 45. Off: no print inside 45 days, or `dte` missing. |
| P | On: 25-week `Δbo` ≥ 0.15 and at least 8 weeks in the span. Off: breakout flat, down, or too few weeks. |
| D | On: setup Peer is `DISCOUNT` (`val_vs_ind` ≤ −5). Off: `PREMIUM`, `INLINE`, `THIN`, `NA`, or no peer. `pe_peer` and `pe_fwd_peer` do not turn D on. |
| T | On: `tech_sma` is `ABOVE` and `rng` < 85. Off: below the 50-day, or range already paid. |
| nmesh / Mesh 3+ | Count of letters in `Mesh` (0–5). `Mesh 3+` is the filter `nmesh` ≥ 3. |

## Standing meanings (copy these; do not invent a second definition)

| Abbr. | Meaning |
|---|---|
| opm | Operating margin, %. Quality of the current business. Not a score. |
| pe / pe_fwd / peg | Trailing TradingView PE (`pe`), projection forward PE (`pe_fwd` / `fp_pe_y1`), PE versus growth. `pe_fwd_tv` stays empty. `pe_fwd` is a derivative and can be off. |
| fp_terminal_px / fp_primary_upside_pct / fp_rev_cagr_own | Model terminal price, model upside %, own-lane revenue CAGR. Separate from street and pack. |
| left / leftover | Unused upside %, `max(street PT / close − 1, edge forward upside)`. Not mix. |
| street_left | Street target versus close, %. Separate from `left`. |
| edge_only | `leftover_carrier`: `street_left` < 0 and `left` > 0. Pack leftover is the edge number. The street target is below the price. |
| both | `leftover_carrier`: `street_left` > 0 and `left` is above it. Edge lifted the pack number. |
| street | `leftover_carrier`: `street_left` > 0 and `left` is not above it. |
| none | `leftover_carrier`: `left` is missing or not positive, or `street_left` is missing. |
| street_px | Analyst PT in dollars, or close × (1 + street leftover %). |
| target_px | Pack leftover in dollars: close × (1 + leftover %). Edge leftover can sit above street. |
| rr | leftover / max(52w range used, 15). High = unused upside vs already-paid range. Not a 0–100 score. |
| rng | Position in the 52-week range, 0–100. High (~85+) = paid. Low = unused range. |
| RSI | Relative strength index, 0–100. |
| bo | Weeks `breakout_long_v1` score. Δbo = change vs the prior pred run. |
| cont | Weeks `quality_continuation_v1`. |
| fwd | Weeks `forward_edge_active_v2`. |
| vs50 | Close vs the 50-day average, %. Required next to ABOVE / BELOW. |
| vs200 | Close vs the 200-day average, %. Required next to the 200-day ABOVE / BELOW label. |
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
| pe_fwd | Projection forward PE (`fp_pe_y1`). A derivative. Can be off. `pe_fwd_tv` is the empty TradingView forward PE. |
| PEG | PE / earnings growth. |
| EV/EBITDA | Enterprise value / trailing EBITDA. |
| evrev_vs_ind / pe_vs_ind | % vs industry median multiple. Negative = cheaper (discount). Peer set is US $2B industry, min n=6. |
| left_vs_ind / d5_vs_ind | Leftover or 5D vs industry median. Positive leftover = more unused upside than peers. |
| Val | Tape column: pack `val_label` + `val` (generic_utils.setup). Not always EV/Rev. |
| Peer | Tape column: the bin on the one multiple setup quoted (`val_field`). Always show `val_vs_ind` beside it. Not a substitute for the trailing TV bins or the forward bins. |
| val_vs_ind | Percent versus the industry median of `val_field`. Negative = cheaper. The peer label uses this column. |
| val_ind_med | The industry median of `val_field`. |
| val_vs_avg | Percent versus the industry average of `val_field`. A different column from `val_vs_ind`. It does not change DISCOUNT / PREMIUM / INLINE. |
| val_ind_avg | The industry average of `val_field`. |
| eff_vs_ind / eff_vs_avg | Percent versus the industry median, and versus the industry average, of the efficiency field. Two columns. |
| FIELD_vs_ind / FIELD_vs_avg | Same pair for each relative metric (pe, pe_fwd, peg, evrev, evebitda, pb, evfcf, opm, left, d5, and the rest on the row). Median percent and average percent stay separate. |
| Proj | Tape column: leftover + weeks bo / cont / fwd. |
| Tech | Tape column: `tech_sma` plus `vs50`, and `tech_rng` plus `rng`. RSI and ADX stay separate. |
| IS_SUPPORTED | Up-tape: leftover / weeks / Val-Peer still justify the print. |
| CHASE | Up-tape: paid range, leftover gone, premium vs peers. Do not add. |
| UNSUPPORTED | Up-tape: price moved without leftover / weeks / value support. |
| MIXED | Neither clean case (up or down). Canvas may omit down-MIXED. |
| BOUNCE | Down-tape: leftover / unused range / cheap vs industry / live profile can justify a bounce. |
| CONTINUE_DOWN | Down-tape: limited leftover + deterioration — likely to keep falling. |
| down_class | Pack/CLI field on movers laggards: BOUNCE / CONTINUE_DOWN / MIXED. |
| DISCOUNT | That column's percent versus the industry median is ≤ −5. At least 5% cheaper. |
| PREMIUM | That column's percent versus the industry median is ≥ 5. At least 5% richer. |
| INLINE | That column's percent is between −5 and 5. Close to the median. |
| pe_peer | DISCOUNT / PREMIUM / INLINE / THIN / NA on TradingView trailing PE (`pe`), taken as published. The percent beside it is `pe_vs_ind`. |
| evebitda_peer | Same bins on TradingView trailing EV/EBITDA (`evebitda`). The percent is `evebitda_vs_ind`. |
| pb_peer / evfcf_peer | Same bins on TradingView price/book and EV/FCF. Percents are `pb_vs_ind` and `evfcf_vs_ind`. |
| pe_fwd_peer | Same bins on the projection forward PE (`pe_fwd`). The percent is `pe_fwd_vs_ind`. Derivative. Can be off. Not the same word as `pe_peer`. |
| evebitda_fwd_peer | Same bins on the projection forward EV/EBITDA (`evebitda_fwd`). The percent is `evebitda_fwd_vs_ind`. Derivative. Can be off. |
| THIN / NA | Peer skipped: industry n < 6, or no quoteable multiple (ETF, missing native field). No percent. |
| val_field / val | Multiple `generic_utils.setup` chose to quote. Not EV/Rev unless `val_field` is evrev. |
| ABOVE / BELOW | `tech_sma`: close versus the 50-day average. ABOVE is `vs50` ≥ 0. BELOW is `vs50` < 0. The percent is `vs50`. `tech_sma200` uses `vs200` the same way. |
| UNUSED / MID / PAID | `tech_rng`. UNUSED is `rng` under 40. PAID is `rng` at least 85. MID is between. The position is `rng`. |
| SMA50 | 50-day simple moving average. If vs50 > 0, first stop below. If vs50 < 0, bounce above — stop is ATR or c15. |
| next below | Nearest structure print still under today's close (SMA50, ATR, or c15). |
| Path 8w | First→last all-fields span (close / EV/Rev / street PT / ATRP). Not a 0–100. |
| close_pct / pt_pct | First→last % change in close and street PT from `series_span`. Older notes say pt_chg for the same PT change. |
| OPENING | All-fields span: price did not consume the target (`close_pct` ≤ 5 and `pt_pct` ≥ −2, with pack leftover still live). |
| DYING | All-fields span: price ran and the target did not (`close_pct` ≥ 8 and `pt_pct` ≤ 2). Rank these below ASYM. |
| CHEAPER | That name's own `val_field` fell on the span (`val_pct` < −5). Not EV/Rev unless `val_field` is evrev. |
| THIN_HIST | Span has fewer than 8 all-fields days. Not a rank. |
| ASYM | Order, not a 0–100. Payoff shortlist that is OPENING or CHEAPER, sorted by `rr`, tie-break `bo`. |
| CONVICTION | Same survivors sorted by weeks `bo`. A second order. Do not add it to `rr`. |
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
