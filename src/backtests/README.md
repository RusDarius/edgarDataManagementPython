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

Operator join (week/month plan on the current pack, not a second Daily):

```powershell
python src/generic_utils/rank_cli.py --csv logs/tradingview_analysis/backtests/runs/<run_id>/family_metrics.csv --field spearman_ic --top 16 --id-field signal_name --group-field family --cap 3
```

Standing spec: `src/research_prompt/backtest_horizon.txt`.
Skill: `.claude/skills/backtest-horizon/SKILL.md`. Canvas: `horizon-plan-YYYYMMDD`.

Briefing recommendation-stack performance is a different command. It scores published upside ranks and the briefing tape against later pack closes.

```powershell
python src/run_decision_review.py
```

Skill: `.claude/skills/decision-review/SKILL.md`. Read `highlights.log`, then the performance block at the top of `queries.txt`.

## Follow-up context: score the scans for performance, relevance, and accuracy

This section is the brief for a later dedicated reader. It does not add a scorer, a new table, or a new command. The evidence this reader would judge is already what `run_backtests.py` writes.

The command is the same for any window. `--start-day` and `--end-day` only choose which all-fields days enter the artifact calendar. The columns, the horizons, and the way a reader should judge them stay the same when those flags are omitted.

```powershell
$env:PYTHONPATH='src;.'
python src/run_backtests.py
python src/run_backtests.py --start-day 01_06_2026 --end-day 30_08_2026
python src/run_backtests.py --config src/backtests/configs/default_v1.json
```

A finished run already holds the raw evidence under `logs/tradingview_analysis/backtests/runs/<run_id>/`:

| Artifact | What a performance reader uses it for |
|---|---|
| `family_metrics.csv` | One row per family, signal, and horizon. Rank agreement, quintile spread, hit rate, and top-N lift. |
| `field_patterns.csv` | Mean forward return inside each quintile, so a spread can be checked against the buckets. |
| `top_20_*` and `top_50_*` columns on `family_metrics` | Average forward return of the top 20 and top 50 versus the universe that day. |
| `overlay_comparison.csv` | `picks_only` against `timing_filter`, `timing_weight`, `projection_filter`, and `picks_timing_projection`. |
| `coverage_ledger.csv` | Which days actually had prediction, edge, upside, timing, and projection. |
| `evidence_report.md` | A short IC ranking. Useful as a headline. Too thin to judge a family on its own. |
| `backtests.duckdb` | The query surface for the tables above. Large `signal_rows` / `outcome_rows` may be CSV-backed views. |

Three words, each a column, kept separate. Do not average them into one score.

**Performance.** Does a higher score line up with a higher forward return, and did the top of the list capture more than the universe.

- `spearman_ic` — rank agreement between the signal and the forward return on that horizon. Positive means the high scores had the higher returns. Read `day_count` beside it. A large IC on a handful of days is a thin sample (`evaluation.min_pairs_for_metric`, default 8).
- `quintile_spread_pct` — mean forward return of the top quintile minus the bottom quintile (`evaluation.quintile_count`, default 5).
- `top_20_avg_return_pct`, `top_50_avg_return_pct`, and the matching `*_lift_pct` — mean forward return of that top-N minus the mean of the names scored that day.
- `avg_winner_pct` and `avg_loser_pct` — mean of the positive outcomes and mean of the rest. They sit beside `hit_rate`. `hit_rate` is the share of scored outcomes that finished up, not a test of the score's sign.

`top_20_lift_pct` on a raw all-fields field such as ATRP can be an extreme outlier. Prefer `spearman_ic` with `day_count`, and use the lift only when the quintile buckets agree.

**Relevance.** Does the family have something to score on the calendar the run actually covered.

- `coverage_ledger.csv` flags `has_prediction`, `has_edge_parent`, `has_upside_scan`, `has_timing_run`, `has_projection_growth`, `has_projection_price`.
- `pair_count` and `day_count` on `family_metrics` say how many scored pairs survived the join.
- A family with a wide calendar and a near-zero IC is present and not discriminating. A family missing from the ledger is a coverage gap, reported as a gap.

**Accuracy.** Whether the scored names finished up, and whether an overlay improves the picks-only baseline. The engine does not yet store a sign-agreement rate (score direction versus return direction). Rank agreement is `spearman_ic` under Performance.

- `hit_rate` — share of scored pairs whose forward return is positive. It is the up-rate of the scored set. It does not test the sign of the score.
- `avg_winner_pct` and `avg_loser_pct` — mean of the positive outcomes and mean of the non-positive outcomes in that same set.
- `spearman_ic_lift_vs_picks_only` and `top_N_avg_return_pct_lift_vs_picks_only` on `overlay_comparison.csv` — the overlay minus `picks_only` on the same horizon. The baseline row is `variant = picks_only`.

A later pass that wants true directional accuracy would add a column for score-sign versus return-sign. Until that column exists, judge direction with `spearman_ic` and judge payoff with the quintile spread and the top-N lift.

Horizons in `default_v1.json` are 1, 5, 10, 20, and 60 trading days. Judge a family on the horizon the question asks about. A 1-day IC and a 60-day IC are two rows.

The later reader, when it is built, should follow the same folder contract as decision review:

1. `highlights.log` — the judgment page. Coverage counts. Families whose IC points with the forward return, with `day_count` and quintile spread on the same line. Families whose IC points against it. Overlay lift versus `picks_only`. Thin samples called out by `day_count`. No canvas required.
2. `queries.txt` — copy-paste SQL above the field dump. At least: rank `family_metrics` by `spearman_ic` within a horizon; the same rows where `day_count` is thin; quintile buckets for one signal from `field_patterns`; overlay lift versus `picks_only`; coverage flags by day.
3. The existing CSVs, `evidence_report.md`, and `backtests.duckdb` stay the source. The reader formats them. It does not rescore `signal_rows`.

Example judgment queries the follow-up should emit, against that run's DuckDB:

```sql
SELECT family, signal_name, horizon_days, day_count, spearman_ic,
       quintile_spread_pct, hit_rate, top_20_lift_pct
FROM family_metrics
WHERE horizon_days = 20
ORDER BY spearman_ic DESC NULLS LAST;

SELECT horizon_days, variant, spearman_ic, spearman_ic_lift_vs_picks_only
FROM overlay_comparison
ORDER BY horizon_days, variant;

SELECT family, signal_name, horizon_days, quintile, pair_count, avg_forward_return_pct
FROM field_patterns
WHERE horizon_days = 20
ORDER BY family, signal_name, quintile;

SELECT as_of_date, has_prediction, has_edge_parent, has_upside_scan,
       has_timing_run, has_projection_growth
FROM coverage_ledger
ORDER BY as_of_date;
```

Column names follow the writers in `src/backtests/analysis.py` and `src/backtests/reporting.py`. Confirm them with `information_schema.columns` if a config rename moves a field.

This follow-up scores signal families and scan profiles (move-prediction, all-fields, upside, timing, projection, overlays). The briefing recommendation stack — published top 100, operator NEW / ADD / TRIM / EXIT, runners, and broken pumps — stays on `python src/run_decision_review.py`. Passing `--family-metrics` there only attaches a few IC rows as support.

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
