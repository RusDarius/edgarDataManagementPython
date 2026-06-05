# Move Prediction History Aggregation — Weekly DuckDB Workflow

## Purpose

This workflow is the DuckDB-native historical aggregator for the weekly move-prediction store.
It consumes the weekly `move_prediction_*.duckdb` files already produced by the main analysis
pipeline and writes a DuckDB-first historical dataset plus a readable overview log.

Default behavior is now:

1. No duplicated historical CSV exports.
2. One per-analysis DuckDB file containing the historical tables and starter views.
3. One `_aggregation_overview.log` with the human-readable run summary and highlights.

Optional compatibility flags still exist if you explicitly want them:

1. `write_legacy_csv_outputs=True` to also emit the old CSV artifact set.
2. `export_parquet=True` to export the DuckDB tables to a `parquet/` sidecar folder.

Use this guide when your source data already lives under the weekly DuckDB store:

```text
logs/tradingview_analysis/prediction_analysis/duckdb_runs/
  iso_year=2026/
    week=22/
      move_prediction_2026_W22.duckdb
    week=23/
      move_prediction_2026_W23.duckdb
```

Use the legacy guide when your source data is still a tree of dated `marketOpen` / `marketClose`
CSV folders.

## What The DuckDB Workflow Produces

Each aggregation run writes a new folder under:

```text
logs/tradingview_analysis/prediction_analysis/duckdb_runs/historical_prediction_analysis/runs/<analysis_run_id>/
```

Default contents:

```text
historical_prediction_analysis/
  runs/
    history_aggregation_20260605_1430_utc_ab12cd34/
      _aggregation_overview.log
      historical_prediction_analysis.duckdb
```

If `export_parquet=True`, a `parquet/` folder is added.
If `write_legacy_csv_outputs=True`, the old CSV artifact set is also written beside the DuckDB file.

## Input Expectations

The DuckDB history aggregation reads two weekly-store tables:

1. `run_metadata`
2. `profile_prediction_rows`

Each `run_id` inside a weekly DuckDB file becomes one synthetic history snapshot in the aggregation.
Snapshot labels are generated from the weekly run timestamp and look like:

```text
2026-06-01 run_1419_utc_aaa11111
```

That synthetic session label replaces the old `marketOpen` / `marketClose` session name in the
history outputs because the weekly DuckDB store is organized by run, not by raw folder session.

## How To Run It

The main example lives in [src/main.py](../src/main.py) and uses week-folder discovery plus the
DuckDB aggregation entry point.

Minimal Python pattern:

```python
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    build_move_prediction_history_duckdb_inputs_from_week_folders,
    run_move_prediction_history_aggregation_duckdb,
)

duckdb_history_root = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\duckdb_runs\iso_year=2026"
)

input_paths = build_move_prediction_history_duckdb_inputs_from_week_folders(
    base_dir=duckdb_history_root,
    folder_names=["week=22", "week=23"],
)

result = run_move_prediction_history_aggregation_duckdb(
    input_paths=input_paths,
    output_dir=duckdb_history_root.parent / "historical_prediction_analysis",
    write_legacy_csv_outputs=False,
    export_parquet=False,
    # include_profiles=["breakout_long", "quality_value_compounder"],
)
```

Important operational points:

1. `base_dir` must point at one `iso_year=YYYY` directory.
2. `folder_names` can be `week=22` style or plain numeric strings like `22`.
3. `include_profiles` is optional; omit it to aggregate every profile found in the source runs.
4. The output root should be the sibling `historical_prediction_analysis` folder, not a weekly folder.
5. Default output mode is DuckDB-only. Turn on CSV duplication only when you need backward compatibility with old downstream tooling.

## What To Query Inside historical_prediction_analysis.duckdb

The analysis DuckDB stores the historical dataset directly, rather than writing CSV files and then mirroring them.

Core metadata tables:

1. `historical_analysis_runs`: one row per aggregation run, including `tabular_output_mode`, `legacy_csv_outputs_enabled`, `parquet_exports_enabled`, and `parquet_dir`.
2. `historical_analysis_input_runs`: one row per weekly DuckDB source run that fed the aggregation.
3. `run_metadata` and `generated_reports`: standard writer metadata registered through the DuckDB store.

Core historical tables:

1. `aggregation_manifest`
2. `all_profiles_history`
3. `all_profiles_summary`
4. `all_profiles_price_progression`
5. `all_profiles_score_progression__days`
6. `all_profiles_score_progression__weeks`
7. `all_profiles_score_progression__months`
8. `all_profiles_score_progression__years`
9. `all_profiles_cross_comparison`
10. Per-profile tables such as `profile_breakout_long__history`, `profile_breakout_long__summary`, and per-horizon progression tables.

When CSV output is disabled, `generated_reports.file_path` stores DuckDB table locators in the form:

```text
<analysis_database_path>::<table_name>
```

That is the easiest way to inspect which logical outputs were created without looking for files on disk.

## Starter Views

Every analysis DuckDB creates five reusable views.

1. `vw_analysis_input_runs`: source weekly run inventory with snapshot labels and profile counts.
2. `vw_profile_horizon_progression_core`: long-form one-row-per-profile-symbol-horizon rollup with score delta, last rank, price return, drawdown, and alignment flags.
3. `vw_profile_horizon_alignment_stats`: profile and horizon level hit-rate and correlation summary.
4. `vw_profile_horizon_current_leaders`: leaderboard-ready ranking for the latest symbols inside each profile and horizon.
5. `vw_profile_horizon_snapshot_deltas`: one row per profile, symbol, snapshot, and horizon for score-delta versus realized snapshot-return drill-down.

These views are the main surface for score-trend versus price-performance analysis. They save you
from manually unioning the four horizon progression tables or reshaping history rows yourself.

## Recommended Analysis Workflow

Start with the run inventory:

1. Query `historical_analysis_runs` to pick the analysis run you want.
2. Query `generated_reports` to see the overview log and the table locators registered for that run.
3. Query `vw_analysis_input_runs` to confirm which weekly runs and snapshot labels were included.

Then move to score-versus-price diagnostics:

1. Use `vw_profile_horizon_alignment_stats` to find the profiles and horizons with the best alignment ratio and correlation.
2. Use `vw_profile_horizon_progression_core` to rank symbols by `score_delta_total`, `close_return_pct_total`, `last_rank`, or `max_drawdown_pct`.
3. Use `vw_profile_horizon_current_leaders` to inspect the model's latest leaderboard per profile and horizon.
4. Use `vw_profile_horizon_snapshot_deltas` to study false positives, lagging price response, or snapshot-to-snapshot reversals.

Useful interpretation rules:

1. `score_price_alignment_flag = 1` means score trend and price trend moved in the same direction.
2. `score_price_alignment_flag = -1` means the model and realized price moved against each other.
3. `rank_improvement_total > 0` means the symbol climbed the leaderboard over the window.
4. `close_return_pct_total` is realized window performance; `Perf.*_delta_total` remains a delta of TradingView trailing-return fields, not realized P&L.
5. `presence_ratio` matters. Low-coverage names can look extreme because they only appeared in a few snapshots.

## SQLTools Workflow

The starter query pack is [sql_connections_space/tradingview_move_prediction_history_analysis.session.sql](../sql_connections_space/tradingview_move_prediction_history_analysis.session.sql).

Recommended workflow:

1. Open the `historical_prediction_analysis.duckdb` file from the specific run folder you want to analyze.
2. Open the SQL session file above in SQLTools.
3. Run one `@block` at a time.
4. Disconnect the DuckDB connection before rerunning Python aggregation on Windows; otherwise the file will remain locked.

The query pack covers:

1. Table and column inventory.
2. Aggregation run inventory and output mode flags.
3. Input-run provenance.
4. Generated report and table-locator inventory.
5. Optional Parquet export inventory.
6. Alignment summary by profile and horizon.
7. Score-up and price-up leaders.
8. False positives where scores improved but price fell.
9. Current leaders per profile and horizon.
10. Snapshot-level disagreement analysis.
11. Single-symbol drill-down.

## Relationship To The Legacy CSV Guide

The old guide still explains the semantics of the manifest, history, summary, and progression structures.
The difference is storage, not the analytical meaning of the rows.

1. Old workflow input: dated `marketOpen` / `marketClose` CSV folders.
2. New workflow input: weekly `move_prediction_*.duckdb` files.
3. Old workflow default output: CSV and log.
4. New workflow default output: DuckDB and log.
5. New workflow optional compatibility mode: DuckDB plus legacy CSV sidecars when `write_legacy_csv_outputs=True`.

## Caveats

1. Older weekly DuckDB files may not contain the newest `run_metadata` columns like `run_label`; the loader is schema-tolerant, but keep mixed-vintage weeks in mind while comparing metadata.
2. Snapshot ordering inside the history run is driven by `created_at_utc` and the synthetic run-session token, not by old raw folder names.
3. DuckDB on Windows is file-locking sensitive. Close SQLTools and any other readers before rerunning the writer.
4. If you enable `export_parquet=True`, the run writes a `parquet/` sidecar directory and populates the `parquet_exports` table; when disabled, that table remains empty.