---
name: decision-review
description: Review published briefing top-100 ranks against later pack closes and write a KEEP or CHANGE matrix. Use when the user asks how briefing decisions or rankings captured returns, what should change, or for a decision-review starter. Not a 0-100, not a second Daily budget, and not a rerun of src/backtests.
---

# Decision review

**Did the published top 100, and the calls made on those days, capture the later close? What slice should change?**

`brief_history` scores the operator note file and stops at 40 names. This review scores the upside-100 list on every briefing day. Daily still decides ADD versus WAIT.

## Command

```powershell
$env:PYTHONPATH='src;.'
python src/run_decision_review.py
python src/run_decision_review.py --top 100 --head 20 --tail 20 --min-n 12 --gap-pct 1
```

No-arg knobs live in `run_decision_review_local_main` inside `src/run_decision_review.py`. The same knobs are in `config/generic_utils/decision_review.json`.

Folder: `logs/tradingview_analysis/operator_briefing/runs/decision_review_<stamp>/`

| File | What it is |
|---|---|
| `highlights.log` | The matrix. Read this. It does not paste the 100 names. |
| `decision_review.duckdb` | `ranks`, `coverage`, `quality`, `decisions`, `matrix`, `matrix_to_latest`, `signal_support` |
| `queries.txt` | Open before SQL. One day is `SELECT * FROM ranks WHERE day = 'YYYY-MM-DD'`. |

`min_n` and `gap_pct` decide KEEP versus CHANGE versus THIN. They do not drop a name from the top 100.

## What the matrix is allowed to say

The next-day matrix is the comparable step. `matrix_to_latest` repeats the same levers on the path through the newest pack. `ret_pct` on `ranks` is that longer path.

| Call | Meaning |
|---|---|
| KEEP | The favored slice's median later return beat the other side by at least the gap. |
| CHANGE | The other side won by at least the gap. The suggestion line says what to revisit. |
| FLAT | The medians sit inside the gap. |
| THIN | One side has fewer than `min_n` priced names. |

Levers are list head versus tail, street quote versus `edge_only`, unused range versus paid range, setup DISCOUNT versus PREMIUM, and live `bo` versus a flat `bo`. `bo` stays a tie-break. It is not added to `rr`.

## Backtests

`python src/run_backtests.py` scores signal families (prediction, all-fields, edge, timing, projection) with Spearman IC and top-N lift. It does not score a briefing pack's top 100. This review attaches the newest `family_metrics.csv` when one exists, IC only, horizons 10 and 20, three signals per family. Do not rerun the multi-hour backtest for this review. Extreme top-20 lift is left out on purpose.

An edge scan or a prediction profile can feed the same scorer later by yielding the same rank rows. That is another loader, not another scoring module.

## Coverage

Each briefing day keeps `sleeves.radar_upside_100` in published order. One folder per calendar day, the latest stamp. A day short of `--top` is filled from that pack's `us2b_tape` by `rr`, and `rank_source` says `tape_fill`. A day that still cannot reach 100 stays in `coverage` with a shortfall.
