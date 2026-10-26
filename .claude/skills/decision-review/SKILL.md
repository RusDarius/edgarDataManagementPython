---
name: decision-review
description: Review published briefing top-100 ranks against later pack closes and write a KEEP or CHANGE matrix. Use when the user asks how briefing decisions or rankings captured returns, what should change, or for a decision-review starter. Not a 0-100, not a second Daily budget, and not a rerun of src/backtests.
---

# Decision review

**Did the published top 100, and the calls made on those days, capture the later close? What slice should change?**

`brief_history` scores the operator note file and stops at 40 names. This review keeps the upside-100 matrix and also rolls every name on the briefing tape. Daily still decides ADD versus WAIT.

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
| `highlights.log` | The judgment. Next-day matrix, through-latest matrix, recommendation stack, then a short preview of runners, tape moves, broken pumps, and high-rank fades. |
| `decision_review.duckdb` | `name_path` (one row per symbol), `tape` (every name every day), `ranks`, `decisions`, `matrix`, `matrix_to_latest` |
| `queries.txt` | Read the top first (items 1–10 are the performance SQL; use a line limit). Grep the field dump below it; do not read it whole. |
| `overview.log` | Run header, row counts, and the notes. |

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

## Coverage

The matrix still uses `sleeves.radar_upside_100`. The name rollup uses the whole `us2b_tape` plus book names that tape does not carry. One folder per calendar day, the latest stamp.

| Flag | Rule |
|---|---|
| RUNNER | First close to last close is at least `performer_pct` (15). |
| TAPE_RUN | A week, 5-day, month, or 3-month field on the tape cleared its band, and the path itself did not. |
| PUMP_BROKE | Peak gain from the first close cleared `pump_pct` (20), then the last close gave back at least `break_pct` (12) from that peak. |
| HIGH_RANK_BAD | Best published upside rank was inside `high_rank` (25) and the path is at or below -8%. |

`ranked_bad` is the wider set: best rank inside the top 100 and the same -8% path. Thresholds live in `config/generic_utils/decision_review.json`. They label the rollup. They do not drop a name from `name_path` or `tape`.

Signal-family backtests are not part of this run. Pass `--family-metrics` only when that file should be attached.
