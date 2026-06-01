# TradingView Move Prediction DuckDB Transition

This guide documents the first DuckDB-backed conversion of the TradingView move-prediction analysis suite.

The original `run_full_analysis_suite` writes one CSV per profile plus a consensus CSV. That is easy to inspect, but it scales poorly once the universe or daily raw files become very large. The new `run_full_analysis_suite_duckdb` keeps the text reports, replaces the tabular CSV set with one DuckDB database per ISO-week partition, appends each run into that weekly store by `run_id`, and exports consolidated Parquet mirrors for columnar reuse.

## What Changed

New code:

- `src/data_analysis_scripts/trading_view_move_prediction_analysis.py`
  - Adds `run_full_analysis_suite_duckdb`.
  - Writes profile, raw scan, consensus, component, horizon, and performance-tracking tables to a week-level DuckDB database under `iso_year=YYYY/week=WW`.
  - Keeps `.log` report output for human review in a run-specific subfolder.
  - Reusing the same `run_label` replaces that run's prior rows instead of duplicating them.
- `src/db/trading_view_move_prediction_duckdb.py`
  - Owns DuckDB connection setup, table creation, typed inserts, append-safe tabular schema growth for wide preservation tables, indexes, Parquet exports, and query helpers.
- `requirements.txt`
  - Adds `duckdb`.

Default output shape:

```text
logs/tradingview_analysis/prediction_analysis/duckdb_runs/
  iso_year=2026/
    week=22/
      move_prediction_2026_W22.duckdb
      parquet/
        raw_scan_rows.parquet
        profile_prediction_rows.parquet
        profile_components.parquet
        profile_horizon_scores.parquet
        profile_performance_tracking.parquet
        consensus_rows.parquet
        consensus_components.parquet
        consensus_horizon_scores.parquet
        consensus_profile_horizon_scores.parquet
        run_metadata.parquet
        generated_reports.parquet
        parquet_exports.parquet
      runs/
        move_prediction_20260522_120000/
          _duckdb_run_overview.log
          tradingview_move_prediction__...profile_breakout_long.log
          tradingview_move_prediction_tracking__...profile_breakout_long.log
          tradingview_consensus_aggregator__...profile_consensus.log
```

The run id is generated as `move_prediction_YYYYMMDD_HHMMSS` unless you pass `run_label`. The weekly folder is derived from the run timestamp in UTC using ISO calendar year/week semantics, so year-end weeks land in the correct ISO year.

## How To Run

Install dependencies first:

```powershell
python -m pip install -r requirements.txt
```

Use it exactly like the existing suite, with the `_duckdb` suffix:

```python
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)

result = run_full_analysis_suite_duckdb(
    scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
        min_market_cap_usd=1_000_000_000,
        markets=PREFERRED_MARKETS,
    ).get("data", []),
    min_market_cap_usd=1_000_000_000,
    include_blind_spot_sections=True,
)

print(result["_duckdb_database"])
print(result["_duckdb_parquet_dir"])
```

Optional parameters:

| Parameter | Purpose |
| --- | --- |
| `output_dir` | Override the root DuckDB output directory. If omitted, storage is created under `LOG_DIR / duckdb_runs/iso_year=YYYY/week=WW`. |
| `database_path` | Put the weekly `.duckdb` file somewhere specific. |
| `parquet_dir` | Put the consolidated weekly Parquet mirrors somewhere specific. |
| `run_label` | Human-controlled run id. It is slugified and used in the default DB filename. |
| `export_parquet` | Defaults to `True`. Set `False` if you only want the `.duckdb` database. |
| `create_indexes` | Defaults to `False`. Set `True` only when you want maintained point-lookup indexes after the write. DuckDB can scan these weekly analytical tables quickly without them. |

Behavior notes:

- Text reports stay per run under `runs/<run_id>/`, so human review files do not overwrite each other.
- Tabular data accumulates inside the week-level DuckDB database and weekly Parquet snapshots.
- If the raw scan introduces a new column later in the week, the wide preservation table grows to include it and earlier rows keep `NULL` for that new field.
- If you rerun with the same `run_label`, the store deletes the old rows for that `run_id` before inserting the replacement run.

## Tables

The database stores both wide preservation tables and narrow query tables.

The preservation tables are the direct transition path from the old CSV outputs: the old CSV columns are kept, with `run_id` and `row_number` added to all preservation tables and `profile_name` added to profile-level rows. The old repeated raw-data CSVs are intentionally collapsed into one `raw_scan_rows` table per run because every profile uses the same API scan payload.

Preservation tables:

| Table | Purpose |
| --- | --- |
| `raw_scan_rows` | Equivalent of the old raw-data CSV output for the input scan rows. |
| `profile_prediction_rows` | Equivalent of each old per-profile scored CSV, with `run_id` and `profile_name` added. |
| `consensus_rows` | Equivalent of the old consensus aggregator CSV. |

Query tables:

| Table | Purpose |
| --- | --- |
| `profile_components` | One row per symbol/profile with attention, event, momentum, trend, quality, valuation, safety, and scale. |
| `profile_horizon_scores` | One row per symbol/profile/horizon. This is the main table for profile-level ranking. |
| `profile_performance_tracking` | One row per symbol/profile/horizon/trailing performance field where both model score and realized trailing return exist. |
| `consensus_components` | One row per symbol with cross-profile average component scores. |
| `consensus_horizon_scores` | One row per symbol/horizon. This is the main table for consensus ranking. |
| `consensus_profile_horizon_scores` | One row per symbol/profile/horizon inside the consensus run, useful for disagreement analysis. |
| `run_metadata` | Run-level parameters and provenance. |
| `generated_reports` | Paths to the text `.log` reports written for the run. |
| `parquet_exports` | Paths and row counts for generated Parquet files. |

## Querying From Python

The project includes a small query helper:

```python
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

db_path = r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\duckdb_runs\iso_year=2026\week=22\move_prediction_2026_W22.duckdb"

rows = query_move_prediction_duckdb(
    db_path,
    """
    SELECT
        symbol,
        company,
        sector,
        industry,
        score,
        risk_adjusted_score,
        risk_tier,
        manager_action_signal
    FROM consensus_horizon_scores
    WHERE horizon_name = 'weeks'
      AND score IS NOT NULL
    ORDER BY risk_adjusted_score DESC NULLS LAST
    LIMIT 50
    """,
)

for row in rows:
    print(row)
```

## Querying From DuckDB CLI

Install DuckDB, then open the database:

```powershell
duckdb "D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\duckdb_runs\iso_year=2026\week=22\move_prediction_2026_W22.duckdb"
```

Useful commands:

```sql
.tables
DESCRIBE consensus_horizon_scores;

SELECT symbol, company, score, risk_adjusted_score, manager_action_signal
FROM consensus_horizon_scores
WHERE horizon_name = 'weeks'
ORDER BY risk_adjusted_score DESC NULLS LAST
LIMIT 25;
```

You can also query the Parquet files directly without opening the `.duckdb` file:

```sql
SELECT symbol, company, profile_name, horizon_name, score
FROM read_parquet('D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=22/parquet/profile_horizon_scores.parquet')
WHERE profile_name = 'breakout_long'
  AND horizon_name = 'weeks'
ORDER BY score DESC NULLS LAST
LIMIT 25;
```

## GUI Options

Practical options for browsing/querying the database:

- VS Code: use SQLTools plus a DuckDB driver extension, or a dedicated DuckDB extension if you already prefer one.
- DBeaver: supports DuckDB through the DuckDB JDBC driver and is a good choice for browsing tables visually.
- DuckDB CLI: fastest lightweight option for ad hoc SQL.
- Python helper: best for scripted scans and report generation inside this repo.

## Performance Notes

DuckDB is an analytical database, so the main speed wins come from vectorized execution, column pruning, compression, and Parquet zone maps rather than MySQL-style secondary indexes.

The DuckDB runner does more work than the legacy CSV runner: it still writes the human `.log` reports, then also appends raw scan rows, per-profile rows, normalized component/horizon tables, consensus rows, and weekly Parquet mirrors. Index creation is off by default; keep it that way for normal daily runs unless you specifically need point-lookup indexes. For a single interactive run where you only need the reports and the `.duckdb` database, prefer:

```python
run_full_analysis_suite_duckdb(
  scan_data=scan_data,
  min_market_cap_usd=1_000_000_000,
  include_blind_spot_sections=True,
  export_parquet=False,
)
```

The store bulk-loads rows through DuckDB `COPY` and drops/recreates the known analysis indexes around the write phase when `create_indexes=True`. That avoids repeated weekly runs paying index-maintenance cost for every inserted row. A `.duckdb.wal` file while the run is active is normal; it is DuckDB's write-ahead log and should settle when the writer closes/checkpoints.

This implementation still creates indexes for common point-lookups:

- `profile_prediction_rows(run_id, profile_name, symbol)`
- `profile_horizon_scores(run_id, profile_name, horizon_name, symbol)`
- `consensus_horizon_scores(run_id, horizon_name, symbol)`
- `consensus_profile_horizon_scores(run_id, profile_name, horizon_name, symbol)`

For big analytical questions, prefer the narrow tables over the wide preservation tables. For example, rank from `consensus_horizon_scores` or `profile_horizon_scores` rather than scanning `profile_prediction_rows`.

## Recommended Query Patterns

Top weekly consensus longs:

```sql
SELECT symbol, company, sector, industry, score, risk_adjusted_score, risk_tier
FROM consensus_horizon_scores
WHERE horizon_name = 'weeks'
ORDER BY risk_adjusted_score DESC NULLS LAST
LIMIT 50;
```

Top weekly shorts or hedges:

```sql
SELECT symbol, company, sector, industry, score, risk_adjusted_score, risk_tier
FROM consensus_horizon_scores
WHERE horizon_name = 'weeks'
ORDER BY risk_adjusted_score ASC NULLS LAST
LIMIT 50;
```

Find profiles disagreeing on a symbol:

```sql
SELECT profile_name, horizon_name, score, direction, confidence
FROM consensus_profile_horizon_scores
WHERE symbol = 'NVDA'
ORDER BY horizon_name, profile_name;
```

Compare profile components for one symbol:

```sql
SELECT profile_name, attention, momentum, trend, quality, valuation, safety, scale
FROM profile_components
WHERE symbol = 'NVDA'
ORDER BY profile_name;
```

Tracking alignment by profile and horizon:

```sql
SELECT
    profile_name,
    horizon_name,
    performance_field,
    COUNT(*) AS rows_tracked,
    AVG(score) AS avg_score,
    AVG(performance_value) AS avg_performance
FROM profile_performance_tracking
GROUP BY profile_name, horizon_name, performance_field
ORDER BY profile_name, horizon_name, performance_field;
```

## Current Boundaries

This first iteration converts the standard `run_full_analysis_suite` path. It does not yet convert every other CSV-producing workflow in the project.

Still CSV-backed today:

- `run_full_analysis_suite_with_earnings_priority`
- raw all-fields export in `trading_view_export_all_tdfields.py`
- raw CSV backscan snapshots
- target scan and cross-scanner aggregate outputs

The store module is intentionally reusable so those can follow the same pattern.

## Next Iterations

Recommended follow-ups:

1. Convert `run_full_analysis_suite_with_earnings_priority` so earnings-priority CSVs become DuckDB tables.
2. Convert the all-fields daily export directly from TradingView chunks into DuckDB/Parquet instead of writing a 2.5 GB CSV first.
3. Add a multi-day catalog database that attaches/query-unions several daily run databases or Parquet folders.
4. Partition long-term Parquet storage by `run_date` and possibly `profile_name` once there are many days of data.
5. Add reusable SQL views for common screens such as `weekly_consensus_top_longs`, `weekly_consensus_top_shorts`, and `profile_disagreement_by_symbol`.