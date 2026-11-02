---
name: earnings-week
description: Earnings week scan - every name that prints this week and next (US + Europe, $1B floor, named ids always in) with its slot (BMO / AMC, the session the move shows), tape into the print, the coming quarter against the last, value to peers and to its own history, past print reactions from the day files, and the day's suites joined (pack tape, GMV lens, prediction earnings consensus, edge). Use when the user asks which names report this week, for pre-earnings or event plays, earnings catalysts, names reporting tonight, or pastes EARNINGS WEEK. Not the Daily budget, not post-earn (that is the print-day path), not a 0-100.
---

# Earnings week

**Who prints this week, what has the stock done into it, and is there a value and history case for holding through the event?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py earnings-week --pack PACK --ids RMBS,NPK,SIX:LOGN --line   # named rows + one summary line
python src/generic_utils/tv_scan_cli.py name-levels --pack PACK --ids <focus> --out-dir PACK/earnings_week/name_levels --quiet
python src/generic_utils/tv_scan_cli.py page-run --day YYYYMMDD --mode ide --origin "earnings week"   # RUN_DIR
python src/generic_utils/tv_scan_cli.py judgment-skeleton --day YYYYMMDD --templates earnings_week --out RUN_DIR/judgment.json
python src/generic_utils/tv_scan_cli.py page --template earnings_week --day YYYYMMDD --judgment RUN_DIR/judgment.json --out-dir RUN_DIR --strict
```

The daily refresh runs `earnings-week` as an optional suite into `PACK/earnings_week`, and `page-all` renders the template with the other pages. Knobs (markets, the market-cap floor, weeks, reaction windows, joins) are in `config/generic_utils/earnings_week.json`. Code: `generic_utils/earnings_week.py`.

Tables in `PACK/earnings_week/earnings_week.duckdb` (grep its `queries.txt`):

- `calendar`: one row per name. `week` is W0 / W1. `slot` is BMO / AMC (TradingView code), or BMO~ / AMC~ inferred from the UTC hour. `react_on` is the session the move shows in. `printed` means the print time is before the scan.
- `prints`: one row per past print: `react`, `react_px`, `react_relvol`, `runup`, `drift`, `eps_surprise`, `rev_surprise`.
- `by_day`: one row per day and slot.
- `by_industry`: one row per week and industry, with medians of runup, street upside, P/E against peers and against its own history, and revisions.

## How to read it

1. Each order is its own table: `runup`, `pt_up`, `pe_nfy_vs_own`, `pe_nfy_vs_ind`, `rerate_floor`, `cons_w`, `react_abs_mean`. Never blend them.
2. The event case is an intersection, not a score: sold into the print (`runup` < 0), previous print sold off (`react_last` < 0), and revisions under the priced top quintile (`eps_rev_3m` < +6.6 on 2026-10-27). That is the signal lab's best pre-print cell (+3.1% hold-through, t 4.5); the older "every past print up" rule showed no edge and is retired. Re-read `print_cells` after each lab run: the cell edges move.
3. A cheap name whose measured prints both sold off waits for the reaction. Its buy-in-red band from `name-levels` is the post-print entry.
4. `ran into the print` (`runup` high) plus street at or under the price is sell-the-news exposure for a held line.
5. `pt` is `price_target_1y`, which is in the quote currency. Outside the US, TradingView's `price_target_average` is in USD; the scan rescales low and high by the same ratio. `pe_nfy` is TradingView P/E × EPS ttm / next-FY EPS, so it is currency-free.
6. `eps_q_step` compares the coming quarter's consensus with the last reported quarter. A one-off gain last quarter (a surprise in the hundreds of percent) makes it read as a collapse.
7. `printed` true with no reaction in the tape means TradingView's date may be stale. Check the date OUTSIDE.
8. OUTSIDE news (dates, guidance, consensus) is tagged OUTSIDE and changes timing or Grade only.
9. `rev_surprise` is the last quarter's revenue against its forecast and `surprise_mix` the EPS / revenue beat-miss pair (B/B, B/M, M/B, M/M). Before calling a pre-print set-up, read signal-lab `print_cells` (`run-up x previous reaction`, `earnings-week set-up`, `surprise mix`): it says which set-ups held through and which reactions kept drifting on this data. On 2026-10-27 the "every past print up" set-up showed no hold-through edge, a previous print sold off came before better hold-through, and B/B drifted after the print while B/M did not.

## Week plays (short-term trades for the rest of the week)

When the ask is "plays for this week", "short-term trades this earnings season" or "verdicts on the Book for the week", run the join and render its page instead of joining CSVs by hand:

```powershell
python src/generic_utils/tv_scan_cli.py week-plays --pack PACK --ids STRL,GOOG,AIT --line   # rows for the named ids + summary
python src/generic_utils/tv_scan_cli.py page --template week_plays --day YYYYMMDD --judgment J --out-dir RUN_DIR --strict
```

`PACK/week_plays/week_plays.duckdb` (`generic_utils/week_plays.py`, knobs `config/generic_utils/week_plays.json`; the refresh runs it after signal_lab):

- `plays`: every calendar name, `phase` pre / post, with the alpha model's `dec_<h>` / `pred_<h>` / `b<h>_q20..q80`, the entry book (50-day band, kill), the safety band (`bounce_low..bounce_high`, `bounce_kill`, `mos_*`, for W0 and the Book), the balance sheet (`nd_ebitda`, `fcf_margin`, `opm`, `vol_d`), and the signal-lab cell columns `<col>_cell / _mean / _t / _n` (`setup`, `rev_q`, `runup_q`, `ptup_q` before the print; `mix`, `rx_q`, `big` after it). Also: `setup_rx_*` (the set-up's reaction-night mean and `_q10` / `_q90` tail) with `rx_tail_usd` (the line times that q10: size into a print by it); on the print night `setrx_*` (set-up x reaction, 28-day drift from the reaction close) and `ah_q_*` (next close vs the after-hours read), with the after-hours change standing in for the reaction; `rev_posted` false = the revenue actual is not in the scan yet, so `surprise_mix` has no revenue side and no mix cell is used (`earnings_week.revenue_posted`).
- `book`: every Book line with the same columns (fundamentals from the day file, lenses from the GMV book lens).
- `cells`: the measured cells those columns point at.

The page (`config/generic_utils/pages/week_plays.json`) owns the screens (best cell, sell-the-news cell, post-print by `react_fx`); the judgment fills `pages.week_plays.play_ids` (reading order), `blocks.plays` `[[Symbol, Print, Course, Size, Bounce band, Kill, Upside, Financial risk, Evidence, Why]]`, `blocks.notes` `[[Symbol, Thesis, What breaks it, Trigger, Read-through]]`, `blocks.verdicts` `[[Symbol, NAV %, Verdict, How to proceed, Levels, Why (data)]]`, the `stats` / `read` / `cash` / `macro` / `book_read` callouts and the `sequence` / `rules` lists. Cash for a funded starter is a `capital_plan.capital_plan_week` scenario run through `capital-plan --key capital_plan_week`, never a hand sum.

Reading: kill distance (`bkill_vs_bmid_pct`) and volatility set the size; the cell mean and t set the Grade; the alpha decile is the reading order. Never add them.

Also in `week_plays.duckdb`: `month` (every alpha live row with its 28d / 56d forecast, bands, drivers and GMV profile) and `month_ind` (one row per industry: median `pred_28d`, mean decile, decile 10 / 1 shares, median trailing month and `p_def`, the three highest `pred_28d` names, Book names). The page's Month leaders tab reads them as a regime guide for the month ahead (which industries and profiles the forecast favours), not as a buy list; a name from it still needs a band, a kill and a course.

### Yesterday's calls (play review)

```powershell
python src/generic_utils/tv_scan_cli.py play-review --pack PACK --line          # prior judgment (newest before the scan day) vs this pack
python src/generic_utils/tv_scan_cli.py play-review --pack PACK --judgment logs/canvases/YYYYMMDD/judgment.json --quiet
```

`PACK/play_review/play_review.duckdb` (`generic_utils/play_review.py`; the refresh runs it after week_plays): `calls`, one row per prior course with `ret_pct`, `ret_fx` (against the median of both US $2B tapes), the published bounce band / kill and 50-day band, the day-file `path` (opens and closes; there is no session low or high, so a fill is checked on opens and closes only), `open_state` / `min_state` / `close_state` / `entry_state` (ABOVE_BAND, IN_BAND, UNDER_BAND, UNDER_KILL) and the print state now (`now_phase`, `now_react_fx`, `now_surprise_mix`); `by_course`, `ret_fx` per prior course plus the week-plays list. Fill `blocks.review_read` (callout) and `blocks.review_lessons` (list) from it: what filled, what ran away above the band, which kill printed, and what that changes in today's reading order. One day is noise; the lessons are about the levels (did the band fill, was the kill too tight), not about the course being right.

Beyond next week (prints 3-6 weeks out, ex-dividend, lockups, macro and regulatory dates) and for overlooked names: the `catalyst-outlook` skill (`tv_scan_cli.py outlook`, PACK/outlook).

## Judgment (run-local)

Start from `judgment-skeleton --templates earnings_week`; it lists every block and its shape (`picks` is `{rows: [[Symbol, Print, Course, Size, Entry, Kill, Why]]}`, `sources` a list). Fill:

- `courses` for every named or focus name;
- `pages.earnings_week.named_ids` and `focus_ids`;
- `capital_plan` rows, previewed with `capital-plan --judgment J --pack PACK/briefing_pack.json --out-dir RUN_DIR/capital_plan_event` (never the pack's own folder).

Event plays use the Daily's event budget (0-2). There is no second NEW budget. Size is advice: about $5,000 for a starter, about $1,000 for an add. Buy the event only as a limit inside the buy-in-red band, with a kill on a close. Book lines keep the Daily's course (sticky DERISK, polar rule).

## Generic vs domain

The calendar, reactions, joins and summaries are generic (`earnings_week.py`, no cutoff beyond the universe floor). BUY_PRE / WAIT / PASS, the intersection used on the page, and sizing are judgment.
