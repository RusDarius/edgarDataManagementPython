---
name: journal
description: Decision journal - one small DuckDB that records, per session, the market regime, a point-in-time snapshot of every covered name (pack reads - bands, kill, model decile, peer gap, letters, warn level, lists), the agent's calls only when a course / grade / plan changes (reason text stored once), and dated notes (OUTSIDE news, guidance, sentiment, lessons); then measures what followed on the panel store at 1 day to 6 months (raw, excess vs the median store name, signed by the call's direction, kill / band / target touches). Use when the user asks how past calls or recommendations played, to go back to what was decided on a name and why, how covered / listed names behaved in similar conditions, to log a news / sentiment insight on a name, for a track record or lessons, or pastes JOURNAL. Not a Daily budget, not a 0-100.
---

# Journal

**What did we decide and see at the time, and what happened next?** Deterministic recording and scoring; the lessons are judgment.

## Commands

```powershell
python src/run_journal.py                        # record newest pack + day judgment, review, render journal-YYYYMMDD
python src/run_journal.py --backfill             # first run / after a gap: every priors file, judgment and pack on disk
python src/run_journal.py --history NASDAQ:MU,NASDAQ:GOOG
python src/generic_utils/tv_scan_cli.py journal note --ids NASDAQ:INTU --kind outside --text "Guidance cut ... (OUTSIDE)" --source URL [--tone -1]
python src/generic_utils/tv_scan_cli.py journal review --line      # scorecard lines; writes PACK/journal (journal_review.duckdb)
python src/generic_utils/tv_scan_cli.py journal stats | compact
```

It records itself: the refresh suite `journal` (record + review, after board / sentiment / lane_book) and `priors` (records the judgment's calls under the pack it was reviewed on; `--no-journal` to skip). Page: `page --template journal` (in `templates_all`). Code `generic_utils/journal.py`, recipe `config/generic_utils/journal.json` (coverage condition, snapshot fields, horizons, course classes, buckets, studies, text caps), store `logs/tradingview_analysis/journal/journal.duckdb`.

## Storage

- `snaps`: one row per (day, covered name), REAL numbers; a later session the same day overwrites. Coverage = pack lists + Book + named + W0 / W1 reporters + every judged name (about 250-650 a day).
- `calls`: change-only (the call in force at a time is the newest row at or before it); `texts` holds each reason once (capped, "Earlier:" tail cut).
- `outcomes` is a temp table rebuilt from the panel store on every review / history: nothing derived is stored.
- About 7 MB for 48 days; `journal compact` rewrites the file. Raw TradingView fields are not copied (the panel store holds them).

## How to read it

1. **Scorecard** (calls by class): `fx` = excess over the median store name; `sfx` = sign x fx (+1 held / bought, -1 sold / passed / trimmed): positive = the class was right. `_now` runs to the newest close, mixed lengths; `_7d` / `_28d` / `_91d` / `_182d` are fixed windows (91 / 182 days fill from Dec 2026 / Mar 2027).
2. **Lists and regimes**: one observation per name per day, overlapping windows: read `n_names` and `n_days` beside `n`. A list that lagged in one regime is a question for the next Daily, not a ban.
3. **Similar now**: today's names with their cell's earlier record (list; decile x market month; band state). A record of the cell, not a forecast.
4. **Calls / history**: what was decided, the reason then, and what it did since. Use it before re-ruling a name: cite the previous call and its outcome.
5. Notes are OUTSIDE context: they change timing or Grade, never a sort key or size. Log news, guidance, sentiment or a lesson the moment it matters (`journal note`), with the source.

## Do not

- Write notes as a substitute for the judgment's courses, or hand-edit the DuckDB.
- Read the journal as a score: classes, lists and cells stay separate columns.
