# Backwards Prediction Analysis Guide

## Purpose

Backwards prediction analysis compares the **current** move-prediction DuckDB run against selected **anchor** runs from the past. It answers questions like:

- How did today's scores change vs yesterday?
- Which tickers moved the most since last week or last month?
- Did score changes align with price moves between anchors?

Unlike the multi-run pool aggregator (which unions entire ISO weeks) or the history aggregator (which builds full snapshot progressions), this workflow only loads the runs you specify and writes a compact comparison database.

## Output Location

Each analysis run writes:

```text
logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/
  runs/
    backwards_prediction_analysis_YYYYMMDD_HHMM_utc_<8hex>/
      backwards_prediction_analysis.duckdb
      _backwards_analysis_overview.log
      parquet/                    # when export_parquet=True
```

## Source Data

Reads from the standard weekly move-prediction store:

```text
logs/tradingview_analysis/prediction_analysis/duckdb_runs/
  iso_year=2026/
    week=25/
      move_prediction_2026_W25.duckdb
```

Tables used per source run:

- `run_metadata` — run discovery and anchor resolution
- `profile_horizon_scores` — primary score comparison
- `consensus_horizon_scores` — optional consensus deltas
- `profile_components` — optional component deltas
- `raw_scan_rows` — `close`, `Perf.5D`, `Perf.W`, `Perf.1M`, `Perf.YTD`

## How To Run

```python
from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    run_backwards_prediction_analysis,
)

result = run_backwards_prediction_analysis(
    # current_run_id=None  -> latest run across duckdb_runs
    anchors=[
        AnchorSpec.preset("yesterday"),
        AnchorSpec.preset("last_week"),
        AnchorSpec.preset("last_month"),
        AnchorSpec.preset("oldest"),
        AnchorSpec(run_id="move_prediction_20260520_1200_utc_deadbeef"),
    ],
    include_consensus=True,
    include_components=True,
    min_scan_data_count=3000,
)

print(result["database_path"])
print(result["overview_log"])
```

A commented example is also available in [src/main.py](../src/main.py).

## Anchor Specification

| Preset / spec | Resolution |
|---------------|------------|
| `yesterday` | Latest run on or before current − 1 day |
| `last_week` | Latest run on or before current − 7 days |
| `last_month` | Latest run on or before current − 30 days |
| `oldest` | Earliest run in the index (excluding current) |
| `previous_run` | Immediately prior run by `created_at_utc` |
| `offset_days=N` | Latest run on or before current − N days |
| `runs_back=N` | Nth prior run by time (1 = previous) |
| `run_id="..."` | Exact run |
| `target_date="YYYY-MM-DD"` | Latest run on or before that date |

Default anchors when `anchors=None`: `yesterday`, `last_week`, `last_month`, `oldest`.

## Output Tables

| Table | Description |
|-------|-------------|
| `backwards_analysis_runs` | Analysis metadata and current run reference |
| `backwards_analysis_anchors` | Resolved anchor runs and resolution method |
| `backwards_profile_horizon_deltas` | Current vs anchor profile × horizon × symbol deltas |
| `backwards_consensus_horizon_deltas` | Consensus score deltas |
| `backwards_profile_component_deltas` | Eight component pillar deltas |
| `backwards_anchor_snapshots` | Long-format scores at current and each anchor |

### Profile family matching

Profile comparisons join on **profile family** (version suffix stripped), not exact profile name. Example: current `breakout_long_v1` matches anchor `breakout_long` from an older weekly run.

- `profile_name` — canonical name from the current run
- `profile_family` — shared family key (e.g. `breakout_long`)
- `anchor_profile_name` — actual profile name used in the anchor run
- `profile_version_exact_match` — `true` when both sides use the same versioned name

The overview log lists `profile_family_bindings` per anchor (`exact` vs `family_fallback` vs `missing`).

## Starter Views

| View | Use |
|------|-----|
| `vw_backwards_input_runs` | Current + anchor run inventory |
| `vw_backwards_top_score_movers` | Largest absolute `score_delta` per anchor/profile/horizon |
| `vw_backwards_price_score_alignment` | Score delta vs price return correlation by cohort |
| `vw_backwards_universe_drift` | Symbols added, dropped, or retained vs each anchor |

## Example Queries

Top score improvers vs yesterday (weeks horizon):

```sql
SELECT symbol, company, profile_name, score_delta, close_delta_pct, current_score, anchor_score
FROM vw_backwards_top_score_movers
WHERE anchor_name = 'yesterday'
  AND horizon_name = 'weeks'
  AND mover_rank <= 25
ORDER BY mover_rank;
```

Universe drift vs last week:

```sql
SELECT *
FROM vw_backwards_universe_drift
WHERE anchor_name = 'last_week'
  AND horizon_name = 'weeks';
```

Component momentum shift for a ticker:

```sql
SELECT anchor_name, momentum_delta, attention_delta, valuation_delta
FROM backwards_profile_component_deltas
WHERE symbol = 'NASDAQ:AAPL'
ORDER BY anchor_name;
```

## Related Workflows

- Weekly source runs: [tradingview_move_prediction_duckdb_transition.md](tradingview_move_prediction_duckdb_transition.md)
- Full history progression: [move_prediction_history_duckdb_weekly_workflow_guide.md](move_prediction_history_duckdb_weekly_workflow_guide.md)
- Multi-week pooling: `trading_view_move_prediction_multi_run_pool_aggregator.py`

## Scout Report (human-readable highlights)

After a backwards analysis DB exists, generate scouting output with:

```python
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    run_backwards_prediction_scout_report,
)

scout_result = run_backwards_prediction_scout_report(
    run_folder_pattern="backwards_prediction_analysis_20260617_2111_utc_ecceec3f",
    profile_name="breakout_long_v1",              # or ["breakout_long_v1", "value_recovery_v1"]
    horizon_name="weeks",                         # or ["weeks", "months"]
    primary_anchor_name="last_week",
    top_n=50,
)
print(scout_result["highlights_log"])             # single combination
# for combo in scout_result["results"]: print(combo["highlights_log"])  # multiple
```

### Scout output location

```text
{backwards_run_dir}/scout_reports/{profile_name}__{horizon_name}/
  backwards_scout_report.log
  top_progressors__last_week.csv
  top_progressors__yesterday.csv
  significant_movers__last_week.csv
  persistent_risers.csv
  price_aligned__last_week.csv
  price_divergent__last_week.csv
  profile_correlation_overview.csv
  profile_move_overview.csv
```

### Scout report sections

- **Profile correlation overview** — profiles ranked by score/price correlation (`last_week` by default)
- **Profiles with largest score moves** — profiles with highest `|avg_score_delta|`
- **Per anchor** (`yesterday`, `last_week`, `last_month`, `oldest`): top progressors, significant movers
- **Primary anchor only**: price-aligned improvers and price-divergent names
- **Persistent risers** — symbols with positive `score_delta` on 2+ anchors
- **Universe drift** — symbols added/dropped per anchor

Parameters: `profile_name`, `horizon_name` (default `weeks`), `primary_anchor_name` (default `last_week`), `top_n` (default `50`), `min_abs_score_delta` (default `0.35`).
