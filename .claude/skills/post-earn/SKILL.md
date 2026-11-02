---
name: post-earn
description: >-
  Print-day path from an afternoon all-fields scan to the later cash close,
  after-hours, and premarket. Use when the user asks about post-earn moves,
  earnings gaps, premarket after a print, or a name sold into an intraday
  earnings dip. Not the daily leftover briefing and not tape movers.
---

# Post-earn session

**Canvas = operator HTML page** (JSON spec in `logs/canvases/` rendered by `tv_scan_cli.py page`, see `.claude/skills/canvas-ledger/SKILL.md`). Not a Cursor `.canvas.tsx`.

**What did the print do after the earlier scan?** Daily answers leftover. This page answers the path: afternoon mark, cash close, after-hours, and whether premarket was refreshed.

No 0–100. No second NEW budget. `pre_repeat` true means `pre_chg` is still the prior morning. Do not call it today's premarket.

A held book that did not report uses the same flag without the earnings date:

```powershell
python src/generic_utils/tv_scan_cli.py session --ids NASDAQ:MU,NASDAQ:WDC --prior-db PRIOR.duckdb --current-db CURRENT.duckdb --out-dir RUN/premarket
```

`pre_px` is the cash close adjusted by `pre_chg` only when `pre_repeat` is false.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
python src/generic_utils/tv_scan_cli.py session --prior-db PRIOR.duckdb --current-db CURRENT.duckdb --prior-run RUN --current-run RUN --earn-on YYYY-MM-DD --out-dir logs/tradingview_analysis/operator_briefing/runs/post_earn_<stamp>
```

`--exchanges NASDAQ,NYSE,AMEX` is the default. Pass `--exchanges ""` for every listing. The only other filter is `--earn-on`. There is no leftover or market-cap cutoff.

`afternoon_*` is the prior scan. On a print day that scan can be an intraday mark. `session_*` is the later scan. `vs_afternoon` is the later close versus that mark. `when_label` is BMO (`earn_time` -1), AMC (`earn_time` 1), or UNSPEC.

## Work order

1. Name both run ids. Cite `pre_repeat`. If it is true, Wednesday (or the next morning) premarket is outside the file.
2. Read `post_earn.csv`. Sort on the page by `session_day` or `post_chg` is fine. Do not blend them into a score.
3. Outside headlines only after the table, tagged `OUTSIDE`. They can time a line. They do not reorder the dump.
4. A full sale into the intraday hole is a different ticket from the cash close and from the after-hours print. Say that once when a book name is in the cohort. Do not build the page around one fill.
5. Canvas `post-earn-YYYYMMDD` with Prints / After hours / Lesson / Ledger. One Ledger.
6. On the print night, put each name on the measured cells (PACK/week_plays `plays`, `phase` post): `setrx_*` (set-up x reaction drift), `big_*`, `ah_q_*` (next close vs the after-hours read), and check `rev_posted` before reading the mix. Rules (earnings_playbook "How to use", 4-7): decide at the cash close, not after hours; a hold-through's kill does not fire on the reaction close in the set-ups whose sell-offs drift (re-armed at the reaction session's low from the second close, unless `thesis_kill`); ran into + previous print up is the exception.

A post-close UPDATE (`run_daily_refresh.py --update-day D --no-producers` after a fresh focused scan) flags these names in `update.md` (`after-hours ±x% (printed after the close)`); the session diff for the page is `session --prior-run <the pack's intraday run> --current-run <the post-close run> --earn-on D`. Example: `logs/canvases/20261028/2141_update/post-earn-20261028.json`.

## Generic vs domain

`session` is generic. Which sale was too large, and whether to buy the gap back, stays here. The SNX lesson still applies: do not buy a reopened leftover the same day the price gaps down.
