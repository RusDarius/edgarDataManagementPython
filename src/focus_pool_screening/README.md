# Focus Pool Screening

Deterministic wide-pool screener that replaces the ad-hoc "AI agent picks
through the runs" workflow with a tunable, repeatable pipeline.

It combines three existing data sources into one scored pool:

| Source | What it provides | Default resolution |
|---|---|---|
| Move-prediction run (weekly DuckDB) | Base universe + raw scan rows, consensus/profile/conviction scores | latest `run_metadata.created_at_utc` in the newest `iso_year=*/week=*/move_prediction_*.duckdb` |
| All-fields snapshot (daily DuckDB) | ~3.5k TradingView fields (valuation, fundamentals, technicals, analyst targets) | latest `tradingview_all_fields_*.duckdb` (mtime), latest run inside |
| Edge research (latest parent run) | `symbol_unified_highlights` overlay (unified edge score, forward valuation upside, historical validation, safety) | newest `edge_research_tools/runs/*` dir with `parent_run_manifest.json`, `aggregate/edge_unified_highlights/edge_unified_highlights.duckdb` inside |

## Run it

```bash
python src/focus_pool_screening/example_entry.py
python src/focus_pool_screening/example_entry.py --top-n 25
python src/focus_pool_screening/example_entry.py --config src/focus_pool_screening/configs/default_wide_pool.json --no-csv
```

From Python (e.g. `src/main.py`):

```python
from focus_pool_screening import run_focus_pool_screening
result = run_focus_pool_screening()  # or config_path=Path(...)
print(result["overview_log"])
```

## Outputs

`logs/tradingview_analysis/focus_pool_screening/runs/focus_pool_<YYYYMMDD_HHMM>_utc_<id>/`

- `focus_pool_screening.duckdb` — the focus output map:
  - `focus_pool_rows` — one row per symbol: identity, all configured raw
    metrics, derived metrics, family scores (`value_score`,
    `fundamental_score`, `technical_score`, `momentum_score` + `*_coverage`),
    prediction overlay (`pred_consensus_*`, `pred_profile_<name>_score`,
    conviction), edge overlay (`edge_*`), `focus_score`, `focus_rank`,
    `lanes` (comma list) and per-lane `lane_<id>` / `lane_<id>_rank`.
  - `run_manifest` — config id/hash, resolved source DBs + run ids, counts.
  - `field_dictionary` — per-field fill rate, included/excluded + reason,
    per-source hit counts.
  - `lane_summary` — member counts and average scores per lane.
  - Views: `v_focus_top`, `v_lane_<lane_id>` per enabled lane.
- `focus_pool_rows.csv` — same rows for quick viewing (disable with `--no-csv`).
- `focus_pool__overview.log` — human-readable report: sources, universe
  funnel, excluded fields, lane summary, top-focus and per-lane tables.

Example queries:

```sql
-- cheapest droppers with analyst upside
SELECT symbol, close, perf_1m, pe_ttm, ev_ebitda, price_target_upside_pct, value_score
FROM v_lane_value_drops WHERE price_target_upside_pct > 30;

-- top movers with strong prediction consensus
SELECT symbol, perf_1m, focus_score, pred_consensus_score, pred_manager_action_signal
FROM v_lane_top_movers WHERE pred_consensus_score >= 0.75 ORDER BY focus_rank;
```

## How scoring works (deterministic)

1. **Universe**: latest prediction run `raw_scan_rows`, filtered by
   `universe` (market-cap range, markets list = `PREFERRED_MARKETS` when null,
   min close, min 10d dollar-traded, exclusions).
2. **Field values**: per configured field, coalesce across `sources`
   priority (`all_fields` first, then `prediction_raw` by default), then
   `valid_range` (out-of-range → missing) and `clip` (winsorize).
3. **Field scores**: percentile rank within the screened universe (0–100;
   ties share average rank; `lower_better` inverts). `band` fields score 100
   inside `[low, high]` with linear decay over `decay_span` (default: band
   width). Fields with fill rate below `min_fill` are excluded for the whole
   run and logged in `field_dictionary`.
4. **Family scores**: weighted mean of available field scores per family;
   `*_coverage` records how much of the family weight had data.
5. **Overlays**: prediction consensus score (clamped to
   `[score_min, score_max]`, rescaled 0–100) and edge unified score
   (0–1 × 100).
6. **focus_score**: weighted mean over family + overlay components
   (weights renormalize over available components). Rows below
   `output.min_composite_coverage` get `focus_score = NULL` and no rank.
7. **Lanes**: config-defined condition trees (`all`/`any` of
   field/op/value leaves; ops `>= <= > < == != between`) over any row column;
   per-lane `order_by` produces `lane_<id>_rank`.

Same inputs + same config ⇒ identical scores, ranks, and lane membership.
Percentiles are unit-agnostic, so `clip` bounds are the only unit-sensitive
knobs.

## Tuning (first iteration knobs)

Copy `configs/default_wide_pool.json` and adjust:

- `families.<name>.fields[]` — add/remove TradingView columns
  (`savedData/trading_view_stock_fields.csv` for the full catalog), change
  `weight`, `direction`, `clip`, `valid_range`, `band`.
- `families.<name>.weight` / `overlays.*.weight` — component blend.
- `sources.move_prediction.consensus_horizon` — `days|weeks|months|years`.
- `sources.move_prediction.profile_columns` — extra per-profile score columns.
- `sources.*.database_path` / `run_id` / `day_label` / `parent_run_dir` —
  pin any source instead of auto-discovering latest.
- `universe.*` — pool breadth.
- `lanes` — your own lanes; any `focus_pool_rows` column is addressable in
  conditions and `order_by`.
- `output.min_composite_coverage` — data-quality floor for a focus score.

Known data caveat: fields that TradingView returns empty for a snapshot
(e.g. `price_earnings_forward_fy`, `dividend_yield_recent` in the 27_08_2026
export) are auto-excluded and listed in the log / `field_dictionary`; they
re-enter automatically once populated again.

## Layout

```
src/focus_pool_screening/
  __init__.py            public API
  config.py              schema validation + loader (focus_pool_screening_v1)
  discovery.py           latest-run resolution for the three sources
  scoring.py             pure deterministic primitives (percentile/band/clip/lanes)
  engine.py              extract -> merge -> derive -> score -> lanes
  output.py              DuckDB focus map + views, CSV, overview log
  example_entry.py       CLI entry point
  configs/default_wide_pool.json
```
