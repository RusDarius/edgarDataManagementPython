---
name: post-earn
description: >-
  Print-day path from an afternoon all-fields scan to the later cash close,
  after-hours, and premarket. Use when the user asks about post-earn moves,
  earnings gaps, premarket after a print, or a name sold into an intraday
  earnings dip. Not the daily leftover briefing and not tape movers.
---

# Post-earn session

**What did the print do after the earlier scan?** Daily answers leftover. This page answers the path: afternoon mark, cash close, after-hours, and whether premarket was refreshed.

No 0–100. No second NEW budget. `pre_repeat` true means `pre_chg` is still the prior morning. Do not call it today's premarket.

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

## Generic vs domain

`session` is generic. Which sale was too large, and whether to buy the gap back, stays here. The SNX lesson still applies: do not buy a reopened leftover the same day the price gaps down.
