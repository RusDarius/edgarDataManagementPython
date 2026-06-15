# TradingView All-Fields Metric Pattern Analyzer — Usage Guide

This document describes the flows in `src/data_analysis_scripts/trading_view_all_fields_metric_pattern_analyzer.py`.

The module correlates **predictor fields** (valuation, momentum, volume, etc.) with **performance fields** (typically `Perf.*` and `change*` columns) inside daily all-fields DuckDB exports. It also supports **cross-run aggregation**, **close-price forward returns**, and optional **history-database forward targets**.

---

## Prerequisites

### Input data

Daily all-fields DuckDB files are produced by `export_all_tradingview_fields_duckdb()` (see `trading_view_export_all_tdfields.py`). Each day folder looks like:

```
logs/tradingview_analysis/trading_view_all_fields_data/
  12_06_2026/
    tradingview_all_fields_12_06_2026.duckdb
```

Each database contains:

| Table | Purpose |
| --- | --- |
| `all_fields_rows` | One row per symbol with all TradingView fields |
| `run_metadata` | Run id, label, scan count, suite name |

Day labels use `DD_MM_YYYY` (e.g. `12_06_2026`).

### Field catalog

Display names and types come from:

```
savedData/trading_view_stock_fields.csv
```

### Default output root

```
logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/
  runs/          # per-run and batch outputs
  aggregates/    # cross-run pools, close returns, stability aggregates
```

---

## Quick Reference — Flows in `main.py`

The **all-view trading view data analysis** section in `src/main.py` wires the main entry points. Uncomment the flow you need:

| Flow | Function | When to use |
| --- | --- | --- |
| Single day | `analyze_all_fields_run_performance_patterns` | One DuckDB file, full pattern scan |
| Date range batch | `run_all_fields_pattern_analysis_batch` | Many daily files + optional auto-aggregate |
| Timing check | `benchmark_all_fields_pattern_analysis` | Same as single run, writes `benchmark_metrics.json` |
| Close returns only | `compute_cross_run_close_returns` | Build forward close-return table across runs |
| Close pattern analysis | `analyze_cross_run_close_performance_patterns` | Correlate predictors with actual price moves |

Example block (from `main.py`):

```python
from pathlib import Path
from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    analyze_all_fields_run_performance_patterns,
    run_all_fields_pattern_analysis_batch,
    benchmark_all_fields_pattern_analysis,
    compute_cross_run_close_returns,
    analyze_cross_run_close_performance_patterns,
)

# Single run (currently active in main.py)
analyze_all_fields_run_performance_patterns(
    database_path=Path(
        r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\12_06_2026\tradingview_all_fields_12_06_2026.duckdb"
    ),
    duckdb_threads=20,
    field_batch_size=80,
    max_parallel_chunks=1,
    duckdb_memory_limit="28GB",
)

# Batch over a date range
# run_all_fields_pattern_analysis_batch(
#     start_day_label="01_03_2026",
#     end_day_label="13_06_2026",
#     duckdb_threads=20,
#     field_batch_size=80,
#     max_parallel_runs=2,
#     max_parallel_chunks=1,
#     duckdb_memory_limit="28GB",
#     max_system_memory_gb=30.0,
#     memory_reserve_gb=4.0,
# )

# Benchmark single run
# benchmark_all_fields_pattern_analysis(
#     database_path=Path(".../tradingview_all_fields_12_06_2026.duckdb"),
#     duckdb_threads=20,
#     field_batch_size=80,
#     max_parallel_chunks=1,
#     duckdb_memory_limit="28GB",
# )

# Close forward returns (standalone)
# close_forward = compute_cross_run_close_returns(
#     start_day_label="01_03_2026",
#     end_day_label="13_06_2026",
#     max_forward_days=40,
# )

# Close forward pattern analysis (full pipeline)
# analyze_cross_run_close_performance_patterns(
#     start_day_label="01_03_2026",
#     end_day_label="13_06_2026",
#     close_forward_days=40,
# )
```

---

## Recommended Parameters (32 GB RAM, i9-class CPU)

These settings target ~12–18 minutes per full day file (~58 performance fields) while staying below OOM risk:

| Parameter | Single run | Batch (2 parallel runs) |
| --- | --- | --- |
| `duckdb_threads` | `20` | `20` (split across workers) |
| `field_batch_size` | `80` | `80` |
| `max_parallel_chunks` | `1` | `1` |
| `duckdb_memory_limit` | `"28GB"` | `"28GB"` |
| `max_parallel_runs` | — | `2` |
| `max_system_memory_gb` | — | `30.0` |
| `memory_reserve_gb` | — | `4.0` |

Notes:

- **`max_parallel_chunks > 1`** runs multiple DuckDB chunk workers inside one file. Useful on very large CPU counts, but increases peak RAM. Start with `1`.
- Batch mode calls `_resolve_pattern_analysis_worker_resources()` to cap parallel runs from available memory (`estimated_run_memory_gb` defaults to 8 GB per run).
- Point `duckdb_temp_directory` at a fast SSD if spill-to-disk occurs.

---

## Flow 1 — Single-Run Pattern Analysis

**Function:** `analyze_all_fields_run_performance_patterns`

Correlates every eligible predictor against each performance field for one daily DuckDB run.

### What it does

1. Loads field catalog and classifies columns into predictors vs performance fields.
2. Filters predictors by fill rate and numeric parse rate.
3. Runs pairwise Spearman correlation + quintile spread analysis (single-scan SQL per chunk).
4. Ranks patterns by `pattern_score` and writes exports.

### Auto-detected performance fields

Columns matching prefixes `Perf.`, `change`, or `change_abs`, plus defaults like `Perf.W`, `Perf.1M`, etc.

### Key parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `database_path` | required | Path to daily all-fields DuckDB |
| `run_id` | latest run | Specific run inside the database |
| `performance_fields` | auto | Override performance column list |
| `predictor_fields` | auto | Override predictor column list |
| `min_fill_rate` | `0.15` | Minimum non-null share for a predictor |
| `min_numeric_parse_rate` | `0.90` | Minimum parseable numeric share |
| `min_pair_n` | `50` | Minimum valid predictor/performance pairs |
| `quintile_count` | `5` | Quintile buckets for spread analysis |
| `field_batch_size` | `40` | Predictors processed per SQL chunk |
| `duckdb_threads` | `8` | DuckDB thread count |
| `max_parallel_chunks` | `1` | ProcessPool workers for within-run chunks |
| `duckdb_memory_limit` | `None` | DuckDB memory cap (e.g. `"28GB"`) |
| `write_exports` | `True` | Write CSV/Parquet/JSON outputs |

### Outputs

Under `pattern_analysis/runs/<analysis_run_id>/`:

```
per_run/<run_id>/
  field_performance_patterns.csv
  field_performance_patterns.parquet
  run_context.json
_pattern_analysis_overview.log
```

`run_context.json` records eligible/skipped predictors, performance fields, and tuning params.

### Suppress progress (tests, batch workers, logs)

```python
analyze_all_fields_run_performance_patterns(..., show_progress=False)
```

Default is `None` → progress prints only when `stdout` is a TTY (Git Bash terminal). Force on with `show_progress=True`.

Progress prints per pairwise SQL chunk with ASCII bar, actual `query=` seconds, cumulative `phase=` wall time, and ETA from observed throughput (`phase_elapsed / completed * remaining`). With `max_parallel_chunks > 1`, the old per-line seconds were misleading (they tracked time since pool submit, not query duration); ETA is now based on completion rate.

---

## Flow 2 — Batch Analysis Over a Date Range

**Function:** `run_all_fields_pattern_analysis_batch`

Discovers all daily DuckDB files between `start_day_label` and `end_day_label`, runs pattern analysis on each qualifying run, and optionally aggregates stability across runs.

### What it does

1. `discover_all_fields_daily_databases()` finds files under `all_fields_root`.
2. `inventory_all_fields_runs()` lists runs; skips runs below `min_scan_data_count`.
3. Runs analyses in parallel (`max_parallel_runs`) with memory-aware worker sizing.
4. If `aggregate_after=True`, calls `aggregate_all_fields_pattern_summaries()` on successful run summaries.

### Key parameters

Same tuning params as single-run, plus:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `start_day_label` / `end_day_label` | required | Inclusive `DD_MM_YYYY` range |
| `all_fields_root` | default root | Override data root |
| `max_parallel_runs` | `4` | Max concurrent day/run workers |
| `aggregate_after` | `True` | Build cross-run stability aggregate |
| `min_scan_data_count` | `3000` | Skip small scans |
| `max_system_memory_gb` | `30.0` | Budget for worker sizing |
| `memory_reserve_gb` | `4.0` | Headroom kept free |
| `estimated_run_memory_gb` | `8.0` | Per-run memory estimate |

### Outputs

```
pattern_analysis/runs/<batch_id>/
  per_run/<run_id>/...          # same as single-run exports
  _batch_overview.json          # successes, failures, effective worker counts
pattern_analysis/aggregates/<aggregate_id>/   # if aggregate_after=True
  all_fields_pattern_aggregate.duckdb
  cross_run_field_stability.parquet
  top_patterns_report.csv
  _aggregate_overview.log
```

Check `_batch_overview.json` for `effective_parallel_runs`, `per_worker_threads`, and any `failures`.

---

## Flow 3 — Benchmark Single Run

**Function:** `benchmark_all_fields_pattern_analysis`

Wrapper around `analyze_all_fields_run_performance_patterns` that records elapsed time and config into `benchmark_metrics.json` inside the run output directory.

Use this to validate tuning on a representative file (e.g. `12_06_2026`) before launching a full batch.

### Output

`pattern_analysis/runs/<analysis_run_id>/benchmark_metrics.json`:

```json
{
  "elapsed_seconds": 847.2,
  "elapsed_minutes": 14.12,
  "performance_fields_count": 58,
  "eligible_predictor_fields_count": 412,
  "rows_emitted": 18500,
  "duckdb_threads": 20,
  "field_batch_size": 80,
  "max_parallel_chunks": 1,
  "duckdb_memory_limit": "28GB"
}
```

---

## Flow 4 — Cross-Run Close Forward Returns

**Function:** `compute_cross_run_close_returns`

Standalone step: extract `close` prices from multiple daily DuckDB files and compute **forward returns** to the next scan in the series (within `max_forward_days`).

### What it does

1. Resolves input databases by date range or explicit `input_paths`.
2. Builds symbol-level close series ordered by run date.
3. Computes `close_forward_return_pct = ((next_close / close) - 1) * 100`.
4. Chooses daily vs weekly period granularity based on scan spacing.

### Key parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `start_day_label` / `end_day_label` | optional | Date range when `input_paths` omitted |
| `input_paths` | optional | Explicit list of DuckDB paths |
| `max_forward_days` | `20` | Max calendar days to next scan |
| `min_valid_close` | `0.000001` | Minimum valid close price |

### Outputs

```
pattern_analysis/aggregates/<close_forward_id>/
  cross_run_close_returns.duckdb
  cross_run_close_returns.parquet
  _close_forward_overview.json
```

Table `cross_run_close_returns` columns include `symbol`, `close_price`, `next_close_price`, `forward_day_delta`, `close_forward_return_pct`.

---

## Flow 5 — Close Forward Pattern Analysis

**Function:** `analyze_cross_run_close_performance_patterns`

Full pipeline: pool predictor data across runs, attach close forward returns, then run pattern analysis with `close_forward_return_pct` as the performance target.

### What it does

1. `aggregate_all_fields_run_pool()` — merges predictor columns from all input databases.
2. `compute_cross_run_close_returns()` — builds forward return targets.
3. Joins pool + close returns into `close_forward_analysis_input.duckdb`.
4. Runs `analyze_all_fields_run_performance_patterns()` per run with `performance_fields=["close_forward_return_pct"]`.
5. Writes combined summary CSV/Parquet.

### Key parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `close_forward_days` | `20` | Passed to close return computation |
| `include_predictor_fields` | auto | Restrict predictor set |
| `quintile_count` | `5` | Quintile buckets |
| `max_parallel_runs` | `1` | Per-run analysis worker count (memory-budgeted) |
| `max_system_memory_gb` | `30.0` | Total RAM budget used for worker capping |
| `memory_reserve_gb` | `4.0` | RAM kept free when sizing workers |
| `estimated_run_memory_gb` | `8.0` | Estimated RAM consumed per per-run analysis |

Close-forward analysis now uses `_resolve_pattern_analysis_worker_resources()` the same way batch mode does, so effective parallel workers are capped by memory and threads are divided per worker.

### Outputs

```
pattern_analysis/runs/scan_period_close_forward_tracking_{scope}_{run_lifecycle_id}/
  _scan_period_close_forward_tracking.json
  close_forward/
    pool/
    close_returns/
    close_forward_analysis_input.duckdb
    close_forward_field_performance_patterns.csv
    close_forward_field_performance_patterns.parquet
    _close_forward_pattern_overview.json
  aggregates/
    all_fields_pattern_aggregate.duckdb
    cross_run_field_stability.parquet
```

When calling `run_scan_period_close_forward_predictor_tracking`, one `run_lifecycle_id` is
created at the top and every downstream writer uses that same id under one output root.

### Outputs (standalone `analyze_cross_run_close_performance_patterns`)

```
pattern_analysis/aggregates/<analysis_id>/
  pool/                         # pooled predictors DuckDB
  close_returns/                # close forward DuckDB + parquet
  close_forward_analysis_input.duckdb
  close_forward_field_performance_patterns.csv
  close_forward_field_performance_patterns.parquet
  _close_forward_pattern_overview.json
```

### Standalone wrapper

**Function:** `build_close_forward_pattern_summary`

Re-runs pattern analysis when you already have a `cross_run_close_returns.duckdb` from Flow 4. It reads source database paths from the close-returns table and delegates to `analyze_cross_run_close_performance_patterns`.

---

## Faster Pilots / Reduced Work

If runtime matters more than full coverage, reduce work first before deeper algorithm changes.

| Knob | Parameter(s) | Default | Faster pilot setting |
| --- | --- | --- | --- |
| Predictor subset | `predictor_fields` / `include_predictor_fields` | All eligible fields | Restrict to explicit high-signal subset (for example 20–50 fields) |
| Fill-rate gate | `min_fill_rate` | `0.10` (close-forward), `0.15` (single-run) | Raise to `0.15`–`0.20` |
| Pair minimum | `min_pair_n` | `20` (close-forward), `50` (single-run) | Raise to `50`–`100` |
| Numeric parse gate | `min_numeric_parse_rate` | `0.80` (close-forward), `0.90` (single-run) | Raise to `0.90` |
| Date window | `start_day_label` / `end_day_label` | Full selected range | Run a half-window smoke pass first |
| SQL chunk width | `field_batch_size` | `100` (scan period) / `80` (single-run) | Keep high (`80`–`100`) to reduce chunk count |

Trade-off: stricter gates and narrower windows reduce runtime linearly but may suppress weaker or sparse predictor signals.

---

## Flow 6 — Cross-Run Stability Aggregation

**Function:** `aggregate_all_fields_pattern_summaries`

Aggregates multiple per-run pattern summary files (CSV or Parquet) into cross-run stability metrics.

### What it does

Builds `cross_run_field_stability` with per `(predictor_field, performance_field)` stats:

- `runs_seen`, `mean_pearson`, `median_pearson`, `sign_consistency_ratio`
- `rank_stability_score` — median quintile spread × sign consistency

Filters pairs with `runs_seen >= min_runs_for_stability` and `sign_consistency_ratio >= require_sign_consistency`.

### Close-forward integration

Pass close-forward summaries into the same aggregate database:

```python
aggregate_all_fields_pattern_summaries(
    per_run_summary_dirs=[Path("pattern_analysis/runs/<batch_id>/per_run")],
    output_dir=Path("pattern_analysis/aggregates"),
    min_runs_for_stability=3,
    require_sign_consistency=0.6,
    include_close_forward_return=True,
    close_forward_return_paths=[
        Path("pattern_analysis/aggregates/<id>/close_forward_field_performance_patterns.parquet"),
    ],
)
```

This lets close-price patterns appear alongside trailing `Perf.*` patterns in one stability report.

---

## Flow 7 — Raw Pool (Advanced)

**Function:** `aggregate_all_fields_run_pool`

Merges selected predictor and performance columns from multiple daily DuckDB files into one pooled database with SQL views for quintile analysis. Used internally by close-forward analysis; also available standalone for custom SQL.

Outputs land in `pattern_analysis/aggregates/<pool_id>/pooled_all_fields_patterns.duckdb`.

---

## Flow 8 — History Forward Returns (Optional)

**Functions:** `attach_history_performance_targets`, `analyze_all_fields_run_with_forward_returns`

Alternative to close-forward returns when you have the move-prediction history aggregation database:

1. `attach_history_performance_targets()` joins symbols to historical forward returns (`history_forward_return`).
2. `analyze_all_fields_run_with_forward_returns()` runs pattern analysis against that target.

Default history root:

```
logs/tradingview_analysis/prediction_analysis/duckdb_runs/historical_prediction_analysis/
```

---

## Helper Utilities

| Function | Purpose |
| --- | --- |
| `discover_all_fields_daily_databases()` | Find DuckDB files in a date range |
| `inventory_all_fields_runs()` | List runs + scan counts in one database |
| `resolve_run_id()` | Pick explicit or latest run id |
| `load_field_catalog()` | Load field display names from CSV |
| `classify_columns()` | Split column list into predictors vs performance |

---

## Typical Monthly Workflow

Assumes daily DuckDB files already exist (no re-export needed):

```
1. benchmark_all_fields_pattern_analysis()     # optional: tune on one file
2. run_all_fields_pattern_analysis_batch()     # ~20 days, aggregate_after=True
3. analyze_cross_run_close_performance_patterns()  # price move correlation
4. aggregate_all_fields_pattern_summaries(     # optional: merge close + perf patterns
       include_close_forward_return=True,
       close_forward_return_paths=[...],
   )
```

Expected wall time with recommended params: **~1.5–2 hours** for ~20 daily files (2 parallel runs), vs **~8+ hours** before SQL/parallelism optimizations.

---

## Pattern Row Fields (Output CSV)

Each row in `field_performance_patterns.csv` describes one predictor vs one performance field:

| Column | Meaning |
| --- | --- |
| `predictor_field` / `performance_field` | Column names |
| `pearson_corr_adjusted` / `spearman_corr_adjusted` | Direction-adjusted correlations |
| `quintile_spread_adjusted` | Top-minus-bottom quintile performance spread |
| `pattern_score` | Combined ranking score |
| `pair_n` | Valid observation count |
| `predictor_fill_rate` | Data quality for predictor |
| `source_day_label` | Scan date (`DD_MM_YYYY`) |

Rows are sorted by `pattern_score` descending in exports and overview logs.

---

## Troubleshooting

| Issue | Likely cause | Action |
| --- | --- | --- |
| OOM during batch | Too many parallel runs | Lower `max_parallel_runs`; raise `memory_reserve_gb` |
| Slow single run | Default threads/batch too low | Use `duckdb_threads=20`, `field_batch_size=80`, `duckdb_memory_limit="28GB"` |
| No databases discovered | Wrong date labels or path | Confirm folders use `DD_MM_YYYY` and filename `tradingview_all_fields_<label>.duckdb` |
| Empty close-forward rows | Scans too far apart | Increase `max_forward_days` / `close_forward_days` |
| Skipped predictors | Low fill or parse rate | Check `run_context.json` → `predictor_fields_skipped`; lower thresholds if intentional |
| `min_runs_for_stability` filter | Too few runs in aggregate | Lower threshold or run more days before aggregating |

---

## Module Exports

Public API (`__all__`):

- `analyze_all_fields_run_performance_patterns`
- `run_all_fields_pattern_analysis_batch`
- `benchmark_all_fields_pattern_analysis`
- `compute_cross_run_close_returns`
- `analyze_cross_run_close_performance_patterns`
- `build_close_forward_pattern_summary`
- `aggregate_all_fields_pattern_summaries`
- `aggregate_all_fields_run_pool`
- `analyze_all_fields_run_with_forward_returns`
- `attach_history_performance_targets`
- `discover_all_fields_daily_databases`, `inventory_all_fields_runs`, `classify_columns`, `load_field_catalog`, `resolve_run_id`

Related tests: `src/tests/test_trading_view_all_fields_metric_pattern_analyzer.py`
