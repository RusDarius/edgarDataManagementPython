---
name: signal-lab
description: Backward evidence on the all-fields day history (panel store since 2026-03-29, US $1B+) - which features and move-prediction scores led later excess returns at 1d / 7d / 14d / 28d / 56d and how reliably (overlap-corrected t, industry-neutral, halves, up vs down market), what prints did before and after (surprise, reaction, run-up, drift, hold-through), and how each briefing-pack list (radar, earnings lanes, short book, movers classes, course buckets) did afterwards. Use when the user asks for backtesting, what works, which signal or screener predicts, pattern discovery, performance tracking of scans or lists, post-earnings drift, or pastes SIGNAL LAB. Not the forecast (alpha-model), not a gate, not a 0-100.
---

# Signal lab

**What has led later excess returns in this data, how reliably, and did the pack's own lists deliver?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py panel-store --quiet                            # append new day files + prediction runs (incremental)
python src/generic_utils/tv_scan_cli.py panel-store --derived --quiet                  # rebuild prints / labels / features only (after a measure change)
python src/generic_utils/tv_scan_cli.py signal-lab --pack PACK --no-update --line      # all studies -> PACK/signal_lab + digest
python src/generic_utils/tv_scan_cli.py signal-lab --pack none --studies prints        # one study, stamped run folder
python src/generic_utils/tv_scan_cli.py signal-lab --pack PACK --first-day 2026-07-01  # a sub-period (regime check)
python src/run_signal_lab.py --page                                                    # no-arg runner, + data-only page
```

The daily refresh runs `panel_store` as a producer (before compile) and `signal_lab` as an optional suite; `page-all` renders the `signal_lab` template. Read `PACK/signal_lab/highlights.log` first (a few KB); grep `queries.txt` before any SQL.

## Winners anatomy (the event view)

`tv_scan_cli.py anatomy --pack PACK --line` (`generic_utils/winners_anatomy.py`, recipe `config/generic_utils/anatomy.json`) answers "what did the big movers look like before they moved": top / bottom 5% tails per day, single legs and pairs of extreme legs by lift and `asym_up` (lift_up / lift_dn), a hold-out on the last window, the winner mix (print in window, industry wave), named patterns by month (`--test 'a:lo & b:hi & dte<=28'`), and today's names with pattern letters joined to the outlook (`candidates`, `short_candidates`). Order up-patterns by asymmetry, not lift (raw lift is volatility), and trust a pattern only if its month table holds through a bad month. Use it when the ask is pattern discovery, "obvious in hindsight" winners, or short-move alpha.

## Data

- Store `logs/tradingview_analysis/signal_lab/panel_store.duckdb` (`generic_utils.panel_store`, knobs `config/generic_utils/panel_store.json`): `panel` (one row per as_of and id, newest run per file, $300M ingest floor), `pred` (move-prediction scores of the newest run before the scan), `series` (split flags), `prints` (one row per detected print), `labels` (`fwd_<h>`), `features` (the recipe's SQL expressions, point-in-time).
- Lab `PACK/signal_lab/signal_lab.duckdb` (`generic_utils.signal_lab`, knobs `config/generic_utils/signal_lab.json`): `factor_summary`, `factor_days`, `factor_month`, `feature_corr`, `print_events`, `print_cells`, `scan_summary`, `scan_days`, `scan_obs`, `scan_packs`, `market`.
- A new feature is a SQL expression in `panel_store.json` `features` (then `panel-store` rebuilds `features`); a new pack list is a SQL line in `signal_lab.json` `scans.lists`. No script.

## How to read it

1. IC is the daily Spearman between a feature and the later excess return (minus that day's US $1B+ median). Sign says the direction: `lower` means low values led.
2. Trust `ic_t_indep` (non-overlapping periods) over `ic_t_nw`; the verdict uses the smaller. `n_indep` is how many independent periods back the number. 56d has one to three: direction only.
3. About 5% of tests clear |t| 2 by chance (`expected_false_at_t2` in the spec). STRONG over CONSISTENT; REGIME means it flips across halves or with the market (`ic_up_mkt` / `ic_down_mkt`).
4. `ic_ind` is the industry-neutral twin: a feature that only works across industries (sector momentum) shows a high `ic_mean` and a low `ic_ind`.
5. `feature_corr` shows which features rank names the same way (do not count them as separate confirmation).
6. Prints: `react` is the reaction session, `react_fx` against the market, `drift_fx_<h>` from the reaction close, `hold_fx_<h>` from the close before the print (the pre-print entry). Cells treat prints as independent; earnings season clusters them.
7. Scans: entry is the close the pack saw; excess is over the same pack's US $2B tape median. `side short` lists work when the excess is negative. Few pack days: the 28d rows are early.

## Using it in judgment

- A pointer from the lab can change Grade or the order you read a table in. It never filters a list, sizes a ticket, or creates a second NEW / ADD budget.
- Quote the number with its t and `n_indep` ("p_def 7d IC +0.06, t_indep 2.4, 14 independent periods"), not "works".
- Re-run after a regime change (`--first-day`) before leaning on a factor that was STRONG over the whole sample.

## Generic vs domain

The store, labels, features, IC, cells and list returns are generic (zero cutoffs beyond the floors in the recipes). The verdict bands are statistical conventions in the recipe. What to do with a pointer is judgment.
