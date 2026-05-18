# Raw TradingView CSV Backscan Suite Guide

## Purpose

Use `run_full_analysis_suite_from_raw_csv_folders` when you want to replay the current move-prediction model against previously saved TradingView all-fields CSV exports.

This answers a different question from the normal daily suite:

- normal suite: "What does the model rank today from live TradingView scan data?"
- raw CSV backscan: "How would the current model rank old saved market universes?"

The backscan is useful after tuning profiles, adding fields, or testing a new model iteration against daily market snapshots you already saved.

## Expected Input

The runner expects a list of dated folders. Each folder should contain one raw all-fields CSV export, normally created by `trading_view_export_all_tdfields.py`.

Example input structure:

```text
logs/tradingview_analysis/trading_view_all_fields_data/
  01_04_2026/
    tradingview_global_all_tdfields_01_04_2026.csv
  02_04_2026/
    tradingview_global_all_tdfields_02_04_2026.csv
```

Default CSV glob:

```text
tradingview_global_all_tdfields_*.csv
```

## Example Usage

```python
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_from_raw_csv_folders,
)

run_full_analysis_suite_from_raw_csv_folders(
    raw_data_folders=[
        Path(
            r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\01_04_2026"
        ),
        Path(
            r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\02_04_2026"
        ),
    ],
    profile_names=["durable_value_compounder"],
    horizon_names=["months", "years"],
    min_market_cap_usd=1_000_000_000,
    include_blind_spot_sections=False,
)
```

If `profile_names` is omitted, the default move-prediction profile suite is replayed, including `durable_value_compounder`.

## Output Structure

By default, output is written under:

```text
logs/tradingview_analysis/prediction_analysis/raw_csv_backscan/<run_label>/
```

The output is arranged by profile, horizon, and scanned day:

```text
raw_csv_backscan/<run_label>/
  profile_durable_value_compounder/
    all_horizons/
      01_04_2026/
        tradingview_move_prediction_durable_value_compounder__global_...log
        tradingview_move_prediction_durable_value_compounder__global_...csv
    months/
      01_04_2026/
        tradingview_move_prediction_backscan__profile_durable_value_compounder__horizon_months__01_04_2026.csv
    years/
      01_04_2026/
        tradingview_move_prediction_backscan__profile_durable_value_compounder__horizon_years__01_04_2026.csv
  _consensus_aggregator/
    months/
      01_04_2026/
        tradingview_move_prediction_backscan__profile_consensus__horizon_months__01_04_2026.csv
  _raw_csv_backscan_manifest.csv
  _raw_csv_backscan_overview.log
```

The `all_horizons` folders contain the normal full profile output. The horizon folders contain narrow ranked snapshots designed for day-by-day comparison.

## Main Parameters

| Parameter | Use |
| --- | --- |
| `raw_data_folders` | List of folders to replay. Required. |
| `profile_names` | Optional list of profile names. Defaults to the standard profile suite. |
| `horizon_names` | Optional list from `days`, `weeks`, `months`, `years`. Defaults to all horizons. |
| `min_market_cap_usd` | Optional lower market-cap filter applied after loading the raw CSV. |
| `max_market_cap_usd` | Optional upper market-cap filter applied after loading the raw CSV. |
| `include_blind_spot_sections` | Adds blind-spot sections to full profile logs. Keep off for large backscans unless needed. |
| `output_root` | Optional custom output root. |
| `run_label` | Optional subfolder name for the run. |
| `csv_glob` | Optional alternate CSV filename glob. |
| `include_consensus` | Whether to write consensus backscan snapshots. Defaults to true. |

## Storage Guidance

For occasional one-off replays, raw CSV folders are fine.

For repeated research and multi-month scans, convert the daily CSVs to a columnar analytical store. DuckDB over Parquet is usually the best fit for this project because the TradingView export is wide and most analysis reads only a subset of columns.

Rough size math for 2.5 GB raw CSV per day:

- 30 days: about 75 GB raw CSV
- 365 days: about 912.5 GB raw CSV
- compressed Parquet: often 3x to 10x smaller depending on repeated strings and null density, roughly 250-800 MB per day and 90-300 GB per year

SQLite can work for simple indexing, but DuckDB plus Parquet is better for analytical scans across many wide daily snapshots.