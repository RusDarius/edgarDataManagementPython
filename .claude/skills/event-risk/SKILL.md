---
name: event-risk
description: Event risk on the Book and the tape - dated policy, election, macro or regulatory events against each line's crowd (its most-correlated names on the panel store), what the crowd's worst week costs the line in dollars, whether an event already repriced the crowd, and a WARN_STRONG / WARN / WATCH level per line from fixed letter rules (E mapped event ahead, P market-wide binary event ahead, R / U event already moved the crowd, S industry shock, B high beta, T gap-prone, V priced for the boom, D letters beyond price, $ stress >= 1% NAV); plus sell / buy candidate lists from the event, industry reactions per event and crash follow-through. Use when the user asks what an election / policy vote / tariff / regulation / macro print does to the Book, which holdings to WARN on, what crashed with a theme and whether it continues, the sell or buy side of an event, or pastes EVENT RISK. Not earnings (beat bar, print night), not book-risk ladders, not a 0-100, not a second budget.
---

# Event risk

**Which Book lines can a dated event take down hard, how many dollars is that, and did an event already reprice the line's crowd?**

Earnings suites cover prints. This covers what they miss: the 4 Nov 2026 Democratic sweep took the AI power / data-center
builders (NASDAQ:STRL -19.9%, NYSE:FIX -16.6%, NYSE:PWR -12.9%, NYSE:GEV -11.1%, NYSE:VRT -12.3%) down in one session
while solar rose (NASDAQ:ENPH +10%, NASDAQ:FSLR +9%). Run on 2 Nov, before the vote, the module put NASDAQ:STRL at
WARN_STRONG (P binary event ahead, B beta 1.5 to its industry, T a -10% step in its last 60, V price over forward value,
$ a bad week = 1.1% of NAV). That check was run after the fact, on rules written after the event, so it is in-sample.
NYSE:RDDT was also at WARN that day and then rose 9.5%: a P warning says the move can be large, not which way.

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py event-risk --pack PACK --line                 # PACK/event_risk (the refresh runs it before board)
python src/generic_utils/tv_scan_cli.py event-risk --pack PACK --ids NYSE:FIX,NYSE:PWR --line   # candidates on the book table
python src/generic_utils/tv_scan_cli.py event-risk --pack PACK --as-of 2026-11-02 --out-dir DIR  # point-in-time (Book weights stay today's)
```

Read `PACK/event_risk/highlights.log` first (~5 KB: Book lines at a level in full, the rest on one line, the first 8
names of each list off the Book). Never read `names.csv` (~780 KB) or the list CSVs whole: `named --db ... --ids` or SQL on
`event_risk.duckdb`. Tables: `book`, `names` (every US $2B+ name, the Book and named ids), `sell_book`, `buy_book` (slim
columns, `sell_buy.fields`), `events`, `reactions`, `crashes`, `follow`, `evidence`. Recipe:
`config/generic_utils/event_risk.json` (crowd size and floor, windows, letter thresholds, level rules).

## The core warning field

`warn_level` / `er_letters` / `stress_5s_usd` / `ev_next` / `ev_recent_z` are a core field: the refresh runs event-risk
right after movers and book_risk (D letters come from the store with the deterioration suite's SQL), and deterioration,
earnings_week, outlook, pathway and board join them through their recipe `joins`. `example_entry.py stance|lookup --line`
ends a flagged name with `warn=WARN_STRONG RBTV$ $-1616 (-1.13% NAV) evz -4.09`; a name without a level gets nothing.
So a named-ticker check needs no extra call: the warning comes with the stance line. Code: `event_risk.warn_token`,
`warn_lookup` (narrow SELECT, reads packs written before the `letters` -> `er_letters` rename).

## What each part is

- **Crowd** = up to 15 US $2B+ names whose store steps correlated most with the line's over the 90 steps that end 5 steps
  before the last file (an event's shared shock must not pick the crowd: built on the last 90 steps, NYSE:UNM's crowd was
  the day's crashers), each row winsorized at 3 sd, corr >= 0.35, share classes / ADR twins skipped; fewer than 5 = the
  industry stands in, `crowd_src`). R / U also need the crowd to move 3%+ on the step; a non-held name whose own print
  reacted on the event step (`ev_recent_print`) stays off the sell / buy lists. The crowd's move today / 5 / 21
  steps, its drawdown and z say whether the line's theme is being repriced, not just the line.
- **Stress** = the worse of (the crowd's worst 5-step move in the store x the line's down-beta to it) and (the line's own worst
  5-step move), in dollars and % of NAV. A measured bad week, not a forecast; `stress_src` says which side set it.
- **Events** come from `config/generic_utils/catalyst_calendar.json` (OUTSIDE). A row touches a line through `symbols`, or an
  `exposure` map `{down|up|either: [themes.json names]}` by membership or by 40%+ of the crowd's cap. A `policy` row with no
  symbols (or `binary: true`) is a market-wide binary event: every line it does not map gets P. `odds_kind` links the
  prediction-market reading in `sentiment_outside.json`. Only policy / binary / mapped events are credited with a crowd's
  move (R / U); a macro print or an index change on the same step is listed in `reactions`, never credited alone.
- **Levels** (rule, not score): WARN_STRONG = E$ | R$ | PBT$ | SB$; WARN = E | R | PB$ | SB | ST$; WATCH = P$ | S | BT | V$ | T$.

## Work order

1. `highlights.log`: the Book lines by level, stress dollars, the event that touches each, the crowd's state.
2. For every **WARN_STRONG** Book line the judgment Reason says what the line does about the event (hold through with the
   kill, trim a third, hedge, exit) and why, with the stress dollars. A **WARN** line names the letter it accepts. The level
   never sets a size and never adds a second budget; course changes go through the Daily's one budget and capital-plan.
3. Before a dated binary event (election, vote, tariff deadline, court ruling, regulation), write its `exposure` map with the
   source (who wins / who loses, e.g. down: [ai_power_build], up: [solar_clean]) and `odds_kind`; re-run event-risk. Without a
   map the event still gives P on every line (size known, direction not).
4. After the event: `reactions` (which industries moved, z, next 5 steps when the store has them), R / U on the lines, the
   `sell_book` (Book WARN lines, then names whose crowd the event repriced) and the `buy_book` (U crowds and mapped up
   sides, never R). Read `follow` before selling into a crash: on this store an industry crash step has been followed by a
   small average bounce over 1-5 steps (+0.5 to +0.8 pp vs the median industry), not a continuation.
5. `evidence` (S / B / T point-in-time, industry as the crowd): the mean excess is not significant (t under 2); the odds of a
   -15% month are the reading: BT about 23% and SBT about 29% against 8% for all names (n_indep 2-5: months, not cycles).

## Limits

- The store starts 29 Mar 2026: one election, a handful of policy shocks. Letters are measured; the event map is judgment.
- A crowd built on 90 steps can mix a theme with a factor (rates, momentum); read `crowd_top` before trusting it.
- Non-US lines (OMXCOP, XETR) have no store steps: event letters only.
