---
name: lane-book
description: Lane book - the month's road for the free capital. Lanes of opportunity (each one SQL condition on the panel store - quality repairing from depth, cheap to peers and turning, washed-out quality, street deep value, low bar or build-up into a print, beat sold, AI quality dip; and avoid lanes - chasing big days, momentum at the high, cash burners after a move, rich run-ups into a print) tested on the store's history (raw / excess at 7 / 14 / 28 days, odds of +10% / -10%, block t, halves, months, AI cluster vs the rest) and read on today's names with band, kill, target and catalysts; premium vs discount by catalyst timing (when a discount pays); industry rotation quadrants with their record; the dated timeline by week; the cash above the floor. Use when the user asks for a broad scan of opportunities and catalysts forming, the best trades for this week / next week / the month, where 10%+ trades can come from, whether huge upside is brewing, capital rotation or deployment of free cash, premium vs discount and when it matters, a custom month book, or pastes LANE BOOK. Not the Daily budget, not a 0-100.
---

# Lane book

**Which kinds of trade have paid on this store, which names are in them today, when in the catalyst cycle a discount pays, where the month's dated events fall, and what the cash above the floor can buy.**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py lane-book --pack PACK --ids SNDK,BE --line      # lanes (split all) + Book / named rows + summary
python src/generic_utils/tv_scan_cli.py lane-book --pack PACK --quiet                   # the refresh suite (after board)
python src/generic_utils/tv_scan_cli.py page --template lane_book --day YYYYMMDD --pack PACK --manual --out-dir RUN_DIR --strict     # data-only page
python src/generic_utils/tv_scan_cli.py page --template lane_book --day YYYYMMDD --pack PACK --judgment J --out-dir RUN_DIR --strict  # with the judgment
python src/generic_utils/tv_scan_cli.py capital-plan --judgment J --key capital_plan_lane --pack PACK/briefing_pack.json --out-dir PACK/capital_plan_lane
```

Code `generic_utils/lane_book.py`, recipe `config/generic_utils/lane_book.json` (floor, horizons, the big-move mark, `defs` macros, `lanes`, `when`, `rotation`, `timeline`, `capital`), page `config/generic_utils/pages/lane_book.json`, test `src/tests/test_lane_book.py`. The refresh runs it after `board` (suite `lane_book`); `page-all --set all` renders it. Read `PACK/lane_book/highlights.log` first (about 6 KB); never print `names.csv`.

`PACK/lane_book/lane_book.duckdb` (grep `queries.txt`):

- `lanes`: lane x split (all / ai = the AI cluster / other): `n_h`, `fwd_h`, `med_h`, `xs_h` (excess over the day's floor median), `hit_h`, `p_up_h` / `p_dn_h` (raw odds of +10% / -10%), `q10_h` / `q90_h`, `t_h` (t across calendar blocks of h days, `n_blk_h`), `xs_h_h1` / `_h2` (store halves), `n_live`, `live_top`, `where`, `why`.
- `lane_months`: lane x split x month of entry (one good month is one episode).
- `names`: every live long / avoid lane name plus the Book (off-floor lines keep a row) and named ids: `lanes`, `avoid`, `l_<key>` flags, `best_lane` (the long lane with the highest measured `xs_28` for the name's cluster) with its record, `vq` / `timing` (today's value quintile x catalyst timing) and that cell's record, store features, board joins and `entry_px` / `month_up_pct` / `risk_pct` / `payoff` (the focus-book formulas).
- `when_value` / `when_spread`: value field (`ey_fwd_ri`, `sales_yield_ri`, `pt_up`) x split (all / strong / weak) x quintile x timing bucket; the spread is Q5 minus Q1.
- `rotation` (every past industry-day: quadrant and 1-month excess quintile -> the industry's next 7 / 14 / 28 days) and `industries` (today's quadrant, breadth, revisions, value level, the Book's NAV share, lane names).
- `timeline` / `weeks`: PACK/outlook events plus calendar rows the pack has not seen, with the lanes of the touched name; one row per week.
- `capital`: NAV, cash, the floor (`cash_floor_pct`), cash above it and the starters / adds it buys at the hint sizes.

## How to read it

1. **Base first.** Mean excess over a median is positive for every name (skew): the `base` row's `xs_28` (about +0.6) is the zero line, and its `p_up_28` / `p_dn_28` (about 19% / 16%) are the odds to beat.
2. **A lane earns capital only when t, both halves and most months agree.** On 11 Nov the non-AI lanes with block t 2+ were `discount_turn` (+4.8, -10% odds 7.9%), `oversold_quality`, `street_deep_value`, `quality_repair`; `discount_turn` and `oversold_quality` were positive every month Jun-Oct. Not edges: `unpriced_revisions`, `beat_both_gap`, `quiet_accumulation`.
3. **The AI cluster reads apart.** The same rule can flip inside the cluster (quality from depth: 25.6% odds of -10% in AI vs 11.3% outside). AI names wait for the cluster triggers (deep-dive AI rebound plan); stages replace unfilled names.
4. **Premium vs discount has a clock.** Read `when_spread` for the strong split: the discount paid most 8-21 days before a print (+5.6 pp, 11 Nov) and nothing after it. Build-ups are bought before the print or not at all.
5. **Rotation is a context column unless its record says otherwise.** On 11 Nov industry quadrants did not persist (Leading 0.0 vs Lagging +0.8): rotate by name lanes, not by industry momentum.
6. **A name in a long and an avoid lane** (a big mover in a quality lane) is read by the avoid lane until it is back in its band.
7. `best_lane` is the lane's record, not a forecast for the name; lanes overlap, so `n_lanes` is a count, never a score.
8. The record is in-sample (lanes written from earlier findings on the same store), overlapping windows, mostly one regime: direction, not proof. Re-run after a regime turn and when a month's labels land.

## Judgment (`pages.lane_book`)

`ids` (the tickets' reading order), `blocks.stats`, `read`, `critical` (callouts), `road` `{rows: [[Week, Dates, What happens, Lanes in play, Capital action, Condition / trigger]]}`, `tickets` `{rows: [[#, Symbol, Lane, Course, When, Buy band, Kill (close), Target, Up %, Risk %, Up / risk, Lane record, Catalyst, Why (data)]]}`, `stages` (list), per-tab callouts (`lanes_read`, `months_read`, `names_read`, `when_read`, `timeline_read`, `rotation_read`, `book_read`, `outside_read`, `critical_read`), `book` `{rows: [[Symbol, Value, NAV %, Lanes now, Course, Levels, Why (data)]]}`, `outside` `{rows: [[Date, Item, Names, What it changes, Checked in data, Source]]}`, `wrong_if` / `corrections` / `rules` / `sources` (lists). Build ticket levels from `names` (band, kill, `min(b28d_q80, street_px)`), never retyped. Cash for the month plan is a `capital_plan.capital_plan_lane` scenario run through `capital-plan --key capital_plan_lane`; the page shows it beside the Daily's three plans. Worked example: `logs/canvases/20261111/1731_full/lane-book-20261111.html`.

Rules: one budget (the Daily's NEW / ADD / event slots; everything else is a queue or WATCH line); about $5,000 starters, about $2,500 adds; keep about 20% cash; size by the kill (a kill more than about 13% under the band top waits); OUTSIDE news changes timing or grade only; EXCHANGE:TICKER everywhere; add dated catalysts to `config/generic_utils/catalyst_calendar.json` (the timeline reads it directly).

## Generic vs domain

Lane evaluation, cell statistics, the value x timing grid, rotation quadrants, the timeline merge and the capital arithmetic are generic (`lane_book.py`: zero cutoffs in code; every condition and threshold is a recipe knob). The lanes chosen, which lane gets capital and when, the courses and the sizes are judgment. A lane that keeps earning capital and a condition reused three times belong in the recipe, not in page text.
