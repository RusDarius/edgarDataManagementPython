---
name: backtracking
description: Build and analyze weekly point-in-time investing runs from existing prediction, edge, and all-fields artifacts. Use when the user asks for backtracking, retrospective investing runs, weekly pick-and-score loops, or BACKTRACK copy-paste flow.
---

# Backtracking weekly run

Use this for retrospective weekly investing runs that must stay point-in-time.
It is separate from Daily, Movers, Book risk, and Horizon.

## Contract

- Build one run folder under `logs/tradingview_analysis/backtracking_analysis/runs/`.
- Decision cadence is weekly (latest prediction run in each ISO week).
- Universe comes from prediction raw rows, then joins weeks scores, optional edge highlights, and raw all-fields fields.
- Selection happens from `panel_rows` only.
- Scoring uses later closes after picks are already frozen.

Do not:

- invent a blended 0-100 score
- leak score outputs into pick selection
- replace Daily / Movers / Risk workflows

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_backtracking.py prepare --start-day 01_04_2026 --end-day 01_08_2026
python src/run_backtracking.py select --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id> --selector program_bo --field bo --top 15 --group-field industry --cap 3
python src/run_backtracking.py score --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id> --picks picks_program_bo.csv
python src/run_backtracking.py days --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id>
python src/run_backtracking.py best --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id> --max-names 20
```

`prepare` also writes `daily_closes` (session_date, symbol, close) for panel symbols. `best` is a hindsight ceiling of one entry and one exit per symbol. It is not a point-in-time selector, and it does not write a forward return onto `panel_rows`.

Optional wider raw layer:

```powershell
python src/run_backtracking.py prepare --start-day 01_04_2026 --end-day 01_08_2026 --raw-preset focused
```

## Files to read

- `backtracking.duckdb` (`decision_weeks`, `panel_rows`, `coverage_ledger`)
- `run_manifest.json`
- `queries.txt`
- `picks_schema.json`

After score:

- `name_returns.csv`
- `book_weeks.csv`
- `investing_run.md`

## Picks schema

Required: `decision_date`, `symbol`, `selector`.

Recommended: `weight`, `source_layer`, `cited_fields`, `note`.

Keep `source_layer` explicit (`prediction`, `edge`, `raw`, or `mixed`) and cite exact panel columns in `cited_fields`.

## Output style

- Default output is files in the run folder.
- No canvas in v1 unless user explicitly asks.
