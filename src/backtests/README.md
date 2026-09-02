# Generic Backtests (`src/backtests`)

First-iteration framework to run a **generic evidence pass** across:

- move-prediction outputs
- daily all-fields snapshots
- upside opportunity scans
- market timing policy outputs
- financial projection outputs

The goal is not to replace existing specialized backtests. It creates one
repeatable place to answer:

- Which signals are actually discriminating forward returns?
- Does timing overlay improve picks vs picks-only?
- Is projection data relevant as a filter?
- Where is data coverage missing?

## What this package does

1. Builds an artifact calendar by `as_of_date` from daily all-fields snapshots.
2. Resolves nearest non-lookahead artifacts (prediction/timing/edge parents).
3. Creates forward labels for 1/5/10/20/60 trading-day horizons.
4. Loads signal families via adapter modules.
5. Runs rank diagnostics (IC, quintiles, top-N lift).
6. Builds overlay variants (`picks_only`, `timing_filter`, `timing_weight`,
   `projection_filter`, `picks_timing_projection`).
7. Writes a DuckDB + CSV + markdown evidence report.

## File layout (consolidated)

- `contracts.py` - shared data contracts
- `config.py` - config model + loader
- `timeline.py` - artifact calendar + forward labels
- `signals.py` - all signal-family loaders in one module
- `analysis.py` - overlay builder + rank stats + ablation
- `reporting.py` - CSV/DuckDB/report writers
- `engine.py` - orchestrator

## Quick start

```powershell
$env:PYTHONPATH='src;.'
python src/run_backtests.py
python src/run_backtests.py --start-day 01_06_2026 --end-day 30_08_2026
python src/run_backtests.py --config src/backtests/configs/default_v1.json
```

## Outputs

Default output root:

`logs/tradingview_analysis/backtests/runs/<run_id>/`

Artifacts:

- `backtests.duckdb` (small summary tables are materialized; `signal_rows` /
  `outcome_rows` are CSV-backed views if those files exceed
  `output.duckdb_materialize_max_bytes`, default 64MB)
- `artifact_calendar.csv`
- `signal_rows.csv`
- `outcome_rows.csv`
- `family_metrics.csv`
- `overlay_comparison.csv`
- `field_patterns.csv`
- `timing_calibration.csv`
- `coverage_ledger.csv`
- `evidence_report.md`
- `run_manifest.json`

The markdown report and CSVs are written **before** DuckDB ingest. If DuckDB
runs out of memory, the scored CSVs and `evidence_report.md` are still kept.
Recover an incomplete run without re-scoring:

```powershell
python src/run_backtests.py --finalize-run logs/tradingview_analysis/backtests/runs/<run_id>
```

Set `"persist_raw_row_tables": false` in the config to skip writing the large
`signal_rows.csv` / `outcome_rows.csv` files (metrics and the report still write).

## Data assumptions

- Daily snapshots come from `export_all_tradingview_fields_duckdb()`.
- Move-prediction comes from weekly DuckDB (`iso_year=.../week=...`).
- Upside/timing/projection are loaded from already-written artifacts by default.
- No historical replay execution is triggered by this package.

## Scope boundaries (v1)

- No transaction cost/slippage/FX/capacity modeling.
- No portfolio allocator/equity curve simulation.
- No catalyst-event sleeve scoring in final lift metrics (adapter stub only).
- Upside fallback reconstruction only covers momentum proxy when full scan
  artifacts are missing.

## Expandability

### Add a new signal family adapter

1. Add a loader class or function in `src/backtests/signals.py`.
2. Emit `SignalRow` records through the `AdapterOutput` contract.
3. Register in `engine.py` using `config.families`.
4. Add fields/flags to `configs/default_v1.json`.

### Add a new overlay rule

1. Add a variant in `src/backtests/analysis.py` (`build_overlay_signal_rows`).
2. Include its name in `overlay.variants` in config.
3. It will automatically flow into the same evaluator/report writer.

### Add new evaluation metrics

1. Implement in `src/backtests/analysis.py`.
2. Include any extra output table rows in `reporting.py`.
3. Mention interpretation guidance in `evidence_report.md`.
