# TradingView Move Prediction DuckDB Transition

This guide documents the first DuckDB-backed conversion of the TradingView move-prediction analysis suite.

The original `run_full_analysis_suite` writes one CSV per profile plus a consensus CSV. That is easy to inspect, but it scales poorly once the universe or daily raw files become very large. The new `run_full_analysis_suite_duckdb` keeps the text reports, replaces the tabular CSV set with one DuckDB database per ISO-week partition, appends each run into that weekly store by `run_id`, exports consolidated Parquet mirrors for columnar reuse, and stores provenance for the model profile configs, TradingView request payload, and code version that produced each run.

## What Changed

New code:

- `src/data_analysis_scripts/trading_view_move_prediction_analysis.py`
  - Adds `run_full_analysis_suite_duckdb`.
  - Adds `run_full_analysis_suite_with_earnings_priority_duckdb`.
  - Writes profile, raw scan, consensus, component, horizon, and performance-tracking tables to a week-level DuckDB database under `iso_year=YYYY/week=WW`.
  - Adds weekly DuckDB preservation tables for earnings-priority consensus/profile outputs while keeping dedicated per-run `earnings_priority/` log files.
  - Stores deterministic profile config snapshots and hashes for every profile used in a run.
  - Accepts either the old mapped row list or the full TradingView response payload. Passing the full response preserves the exact request payload in `run_metadata`.
  - Keeps `.log` report output for human review in a run-specific subfolder.
  - Generated run ids use UTC minute precision plus a short UUID suffix. Reusing the same `run_label` still replaces that run's prior rows instead of duplicating them.
- `src/data_analysis_scripts/trading_view_export_all_tdfields.py`
  - Adds `export_all_tradingview_fields_duckdb`.
  - Writes one DuckDB database per UTC day under `trading_view_all_fields_data/DD_MM_YYYY/`, with multiple intraday runs appended into that day's database by `run_id`.
  - Keeps the legacy CSV exporter unchanged for stopgap workflows.
- `src/db/trading_view_all_fields_duckdb.py`
  - Owns the daily all-fields DuckDB connection, wide-table writes, report registration, optional indexes, Parquet exports, and query helper.
- `src/db/trading_view_move_prediction_duckdb.py`
  - Owns DuckDB connection setup, table creation, typed inserts, append-safe schema growth, profile config snapshots, indexes, Parquet exports, and query helpers.
- `src/data_loaders/api_tradingview_client.py`
  - Attaches `request_payload` and `request_metadata` to move-prediction scan responses so the analysis runner can persist the exact TradingView scanner payload.
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
        profile_config_snapshots.parquet
        run_metadata.parquet
        generated_reports.parquet
        parquet_exports.parquet
      runs/
        move_prediction_20260522_1200_utc_a1b2c3d4/
          _duckdb_run_overview.log
          tradingview_move_prediction__...profile_breakout_long.log
          tradingview_move_prediction_tracking__...profile_breakout_long.log
          tradingview_consensus_aggregator__...profile_consensus.log
```

The generated run id is `move_prediction_YYYYMMDD_HHMM_utc_<8-char-uuid>`. The timestamp portion intentionally stops at minutes; the UUID suffix supplies collision resistance without putting milliseconds or microseconds into the folder name. If you pass `run_label`, it is slugified and used as a readable prefix, and the runner still appends the UTC minute and UUID suffix so each run remains unique. The weekly folder is derived from the run timestamp in UTC using ISO calendar year/week semantics, so year-end weeks land in the correct ISO year.

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

scan_response = TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
)

result = run_full_analysis_suite_duckdb(
    scan_data=scan_response,
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
| `run_label` | Human-readable run id prefix. It is slugified and included in the final run id, which still adds UTC minute + UUID to stay unique and avoid accidental overwrite. |
| `api_request_metadata` | Optional explicit request/provenance metadata. Usually not needed if `scan_data` is the full TradingView response from the API client. |
| `export_parquet` | Defaults to `True`. Set `False` if you only want the `.duckdb` database. |
| `create_indexes` | Defaults to `False`. Set `True` only when you want maintained point-lookup indexes after the write. DuckDB can scan these weekly analytical tables quickly without them. |

Behavior notes:

- Text reports stay per run under `runs/<run_id>/`, so human review files do not overwrite each other.
- Tabular data accumulates inside the week-level DuckDB database and weekly Parquet snapshots.
- If the raw scan introduces a new column later in the week, the wide preservation table grows to include it and earlier rows keep `NULL` for that new field.
- If the runner receives a full TradingView scan response, `run_metadata.api_request_json` stores the exact request payload, request URL, timeout, markets, columns, filters, and sort. If the runner receives only a row list, it still works but can only record fallback metadata.
- Each run stores `profile_config_snapshots` with a deterministic hash of the effective profile config. This makes old `breakout_long` runs distinguishable from future `breakout_long` runs after profile tuning.
- The runner creates an atomic `.write.lock` next to the weekly database while writing. A second writer to the same weekly DuckDB file fails fast instead of interleaving writes.
- If you rerun with the same `run_label`, each run is still stored as a new run id because UTC minute + UUID suffix are always appended.

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
| `profile_config_snapshots` | One row per run/profile containing the profile config schema version, config hash, and full canonical JSON config. |
| `run_metadata` | Run-level parameters and provenance, including run date/minute UTC, ISO week, profile config hashes, API request payload metadata, code/git version, and storage paths. |
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

- VS Code / Cursor: use SQLTools (`mtxr.sqltools`) plus the Evidence DuckDB driver (`Evidence.sqltools-duckdb-driver`). Cursor's extension marketplace often does not list that driver even though it exists on the VS Code Marketplace. Install it manually from a VSIX:

```powershell
$vsix = Join-Path $env:TEMP "sqltools-duckdb-driver.vsix"
Invoke-WebRequest -Uri "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/Evidence/vsextensions/sqltools-duckdb-driver/latest/vspackage" -OutFile $vsix
cursor --install-extension $vsix
```

After install, run `scripts/setup_sqltools_duckdb_cursor.cmd` once. That script also installs a Node 24-compatible `duckdb-async@1.4.2` build for the driver. The marketplace VSIX alone ships `duckdb-async@1.0.0`, which often fails on current Node versions with `Cannot read properties of null (reading 'driver')`.

Reload Cursor, open the SQLTools sidebar, and connect to the DuckDB entries in `.vscode/settings.json`. Keep `"sqltools.useNodeRuntime": true`. Evidence driver connections must use `databaseFilePath`, not `database`. Use `${workspaceFolder}/...`, not `${workspaceFolder:folderName}`, when this repo is opened as a single folder.

Windows notes:

- If `npm` fails in PowerShell with an execution-policy error, use `npm.cmd` or run `scripts/setup_sqltools_duckdb_cursor.cmd` from `cmd.exe`.
- Do not install `duckdb-async@0.10.2` manually unless you are on an older Node runtime. Current setups should use `duckdb-async@1.4.2`.
- The standalone `chuckjonas.duckdb` extension is useful for browsing `.duckdb` / Parquet files, but it is not the SQLTools driver used by `sql_connections_space/*.session.sql`.
- DBeaver: supports DuckDB through the DuckDB JDBC driver and is a good choice for browsing tables visually.
- DuckDB CLI: fastest lightweight option for ad hoc SQL.
- Python helper: best for scripted scans and report generation inside this repo.

On Windows, disconnect GUI/CLI DuckDB sessions before running `run_full_analysis_suite_duckdb` against the same weekly `.duckdb` file. DuckDB allows only one writer process, and SQLTools or DBeaver can keep the database locked while the connection is open even if no query is actively running. If the writer reports that the file is open in `node.exe`, that is usually the SQLTools language server holding the connection.

## Historical CSV Backfill

Use `replay_historical_raw_csvs_into_duckdb_runs` to replay saved all-fields CSV snapshots into the weekly DuckDB layout. This is the migration path for older daily CSV exports under `trading_view_all_fields_data/DD_MM_YYYY/`.

Typical usage:

```python
from pathlib import Path
from data_analysis_scripts.trading_view_move_prediction_duckdb_backfill import (
    replay_historical_raw_csvs_into_duckdb_runs,
)

backfill_result = replay_historical_raw_csvs_into_duckdb_runs(
    raw_data_folders_or_csvs=[
        Path("logs/tradingview_analysis/trading_view_all_fields_data"),
    ],
    target_date_labels=["13_04_2026", "14_04_2026", "15_04_2026"],
    min_market_cap_usd=1_000_000_000,
    include_earnings_priority=True,
    parallel_mode=True,
    max_parallel_workers=3,
    max_memory_gb=20.0,
)
```

Behavior notes:

- By default, dated CSV days that already have a completed backfill run under `prediction_analysis/duckdb_runs` are skipped. Other days in the same ISO week are still processed. Use `force_rerun=True` or a custom `output_dir` to replay into a fresh destination.
- `parallel_mode=True` uses a two-stage pipeline:
  - Stage 1: process workers load and filter CSV snapshots in parallel.
  - Stage 2: DuckDB writes stay serialized within each ISO week, while different weeks can write concurrently.
- `max_memory_gb` defaults to `20.0` and enforces a live RAM cap using process-tree RSS sampling (`psutil` recommended). With the cap enabled, backfill keeps at most one prepared CSV snapshot and one active DuckDB write in memory and waits until RSS drops before starting more work.
- If a single day still exceeds the cap during analysis, batch smaller runs with `target_date_labels` (for example 3-10 days at a time) or raise `max_memory_gb` cautiously.
- `parallel_worker_backend='thread'` keeps the older thread-per-week fallback if process startup overhead is a concern on very small test sets.
- Progress and per-day timing land in `prediction_analysis/duckdb_backfill/duckdb_backfill_manifest.csv`.

At roughly 5 minutes per historical day, a 30-day replay is on the order of 2.5 hours serially. Parallel mode mainly helps when multiple ISO weeks or multiple days in the same week can overlap CSV preparation with DuckDB analysis.

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

Latest run provenance:

```sql
SELECT
  run_id,
  created_at_utc,
  run_date_utc,
  iso_year,
  iso_week,
  scan_data_count,
  profile_names_json,
  api_request_markets_json,
  git_commit,
  git_dirty
FROM run_metadata
ORDER BY created_at_utc DESC
LIMIT 10;
```

Inspect the exact TradingView payload for one run:

```sql
SELECT
  run_id,
  json_extract(api_request_json, '$.request_payload.markets') AS markets,
  json_extract(api_request_json, '$.request_payload.filter') AS filters,
  json_extract(api_request_json, '$.request_payload.sort') AS sort_order,
  api_request_payload_sha256
FROM run_metadata
WHERE run_id = 'move_prediction_20260522_1200_utc_a1b2c3d4';
```

Find which profile config produced a score:

```sql
SELECT
  h.symbol,
  h.profile_name,
  h.horizon_name,
  h.score,
  p.profile_config_hash
FROM profile_horizon_scores h
JOIN profile_config_snapshots p
  ON h.run_id = p.run_id
 AND h.profile_name = p.profile_name
WHERE h.run_id = 'move_prediction_20260522_1200_utc_a1b2c3d4'
  AND h.profile_name = 'breakout_long'
  AND h.horizon_name = 'weeks'
ORDER BY h.score DESC NULLS LAST
LIMIT 25;
```

Detect when a profile definition changed across runs:

```sql
SELECT
  profile_name,
  profile_config_hash,
  MIN(run_id) AS first_seen_run,
  MAX(run_id) AS last_seen_run,
  COUNT(DISTINCT run_id) AS run_count
FROM profile_config_snapshots
GROUP BY profile_name, profile_config_hash
ORDER BY profile_name, first_seen_run;
```

Extract source-code fingerprints for audit:

```sql
SELECT
  run_id,
  git_commit,
  git_branch,
  git_dirty,
  json_extract(code_version_json, '$.source_files') AS source_files
FROM run_metadata
ORDER BY created_at_utc DESC
LIMIT 5;
```

## Current Boundaries

The DuckDB transition now covers the standard move-prediction suite, the earnings-priority variant, and the daily raw all-fields export. It does not yet convert every other CSV-producing workflow in the project.

Still CSV-backed today:

- raw CSV backscan snapshots
- target scan and cross-scanner aggregate outputs

The store module is intentionally reusable so those can follow the same pattern.

## Next Iterations

Recommended follow-ups:

1. Add a multi-day catalog database that attaches or query-unions several weekly move-prediction databases and daily all-fields databases.
2. Partition long-term Parquet storage by `run_date_utc`, `run_id`, and possibly `profile_name` once there are many days of data.
3. Add normalized helper tables or SQL views for earnings-priority queries such as imminent-catalyst screens, per-profile catalyst disagreement, and near-term high-conviction setups.
4. Add reusable SQL views for common move-prediction screens such as `weekly_consensus_top_longs`, `weekly_consensus_top_shorts`, `profile_disagreement_by_symbol`, and `profile_config_change_log`.