# TradingView Move Prediction — Pattern Discovery Reference

Skeptical research workflow for joining prediction scores, raw catalog fields, and historical calibration. Implements Phase 5 of the [active manager plan](improvements_plans/active_manager_profile_and_pattern_discovery_plan.md).

There are **two complementary workflows**:

| Workflow | When | Module | Needs all-fields DB? |
| --- | --- | --- | --- |
| **Single-week discovery** | Thursday after a fresh DuckDB run | `trading_view_move_prediction_pattern_discovery.py` | Yes (for join preview / field autopsy) |
| **Multi-week batch calibration** | Monthly or before promoting a profile | `trading_view_move_prediction_batch_pattern_analysis.py` | No (prediction DuckDB only) |

Use batch calibration to decide *whether a profile lens is trustworthy over time*; use single-week discovery to decide *what raw features explain this week's leaders*.

---

## Three-database model

| DB | Path pattern | Answers |
| --- | --- | --- |
| **A — Prediction** | `prediction_analysis/duckdb_runs/iso_year=*/week=*/move_prediction_*.duckdb` | Which profile/score combos rank well this week? |
| **B — All-fields** | `trading_view_all_fields_data/<dd_mm_yyyy>/tradingview_all_fields_*.duckdb` | What raw features separate winners from losers? |
| **C — History** | `batch_pattern_analysis/batch_*/calibration/.../historical_prediction_analysis.duckdb` or `historical_prediction_analysis/runs/*/historical_prediction_analysis.duckdb` | What persisted over ≥2 snapshots? |

**Join key:** `symbol` with exchange-prefix normalization (same rule as `trading_view_investment_shortlist_pipeline.py`).

---

## Workflow A — Single-week pattern discovery (Thursday)

**File:** `src/data_analysis_scripts/trading_view_move_prediction_pattern_discovery.py`

### Methods

| Function | What it does | Requires |
| --- | --- | --- |
| `discover_latest_prediction_db()` | Finds newest `move_prediction_*.duckdb` | Prediction root |
| `discover_latest_all_fields_db()` | Finds newest all-fields export | All-fields root |
| `discover_latest_history_db()` | Finds newest history aggregation DB | History root |
| `run_prediction_all_fields_join_preview()` | Top profile leaders + CMF/Donchian/RSI confirmation columns | A + B |
| `run_profile_cohort_field_comparison()` | Top-decile vs bottom-decile mean/median for any catalog field | A + B |
| `run_false_positive_field_autopsy()` | Score-up/price-down cohort joined to downside field signatures | A + B + C |
| `run_weekly_calibration_report()` | Alignment ratios, persistent winners, false-positive counts | C |
| `run_pattern_discovery_suite()` | Runs the standard Thursday export bundle | A (B/C optional) |

### Usage (Python)

```python
from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    PatternDiscoveryPaths,
    run_pattern_discovery_suite,
    run_weekly_calibration_report,
    discover_latest_prediction_db,
    discover_latest_all_fields_db,
    discover_latest_history_db,
)

# One-shot Thursday bundle — auto-discovers latest A/B/C databases
exports = run_pattern_discovery_suite(profile_name="breakout_long")
print(exports["overview_log"])

# Manual path control when you know the exact DB paths
paths = PatternDiscoveryPaths(
    prediction_db=discover_latest_prediction_db(),
    all_fields_db=discover_latest_all_fields_db(),
    history_db=discover_latest_history_db(),
)
calibration = run_weekly_calibration_report(paths, profile_name="breakout_long")
```

### Export layout (single-week)

```
<prediction_weekly_db>/pattern_discovery/<timestamp>/
  join_preview.csv
  cohort_field_comparison__ChaikinMoneyFlow.csv
  alignment_stats.csv              # if history DB present
  persistent_winners.csv
  false_positive_counts.csv
  false_positive_field_autopsy.csv # if A+B+C present
  pattern_discovery_overview.log
```

### What to look for (single-week)

| Export | Look for | Interpretation |
| --- | --- | --- |
| `join_preview.csv` | CMF, Donchian position, Hull MA alignment on top leaders | Confirms the *story* behind high scores — not proof of forward edge |
| `cohort_field_comparison__*.csv` | Large top-vs-bottom decile gaps on a field you hypothesized | Candidate feature for a shortlist gate; re-test in batch calibration before promoting |
| `alignment_stats.csv` | `aligned_positive_ratio > 0.55`, `score_price_corr > 0.15` for your profile | Profile lens is historically aligned with price; if below gates, do not change weights |
| `persistent_winners.csv` | Names with `presence_ratio >= 0.5` and positive `close_return_pct_total` | Names that stayed ranked *and* moved — stronger than a one-day leader |
| `false_positive_field_autopsy.csv` | Shared field signatures among score-up/price-down names | Explains *why* the profile misfired; use to tighten filters |

---

## Workflow B — Multi-week batch pattern analysis

**File:** `src/data_analysis_scripts/trading_view_move_prediction_batch_pattern_analysis.py`

Batch analysis inventories weekly prediction DuckDBs, selects one full-coverage run per calendar day, exports per-run leader/pattern CSVs, then builds a multi-week history DB and calibration gate reports. Designed for the active-manager promotion ladder: **observe over weeks before changing weights**.

### Three phases

```text
Phase 1  inventory + per-run CSV exports     (~seconds; ~0.3% of runtime)
Phase 2  history aggregation (staging + SQL)  (~minutes–hours; dominates)
Phase 3  calibration gate CSV exports       (~sub-second)
```

Phase 2 splits into:

- **Staging** — attach weekly DuckDBs, copy selected runs into `stg_input_*` (near-linear with row count).
- **Materialization** — build history tables/views (`_materialize_sql_native_history_tables`). This is the 52-week bottleneck unless you use slim mode.

`batch_overview.log` now records `history_staging_seconds` and `history_materialize_seconds` separately so you can project scale-up cost from materialize seconds per staged row, not total wall clock.

### Usage from `main.py`

The end-to-end entry point in `src/main.py` is the recommended starting point:

```python
from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
    run_batch_prediction_pattern_analysis,
)

# ── Example 1: First pass — recent 4 weeks (matches main.py) ──────────────
result = run_batch_prediction_pattern_analysis(
    iso_year=2026,
    start_week=21,
    end_week=24,                       # omit to use current ISO week
    primary_profile_name="breakout_long",
    horizon_name="weeks",
    min_scan_data_count=3000,          # 8000+ restricts to backfill universe only
    show_progress=True,                # stderr week bar, ETA, per-phase timings
)
print(result["overview_log"])
# Key result keys: output_dir, history_database_path, timing_seconds,
#   history_staging_seconds, history_materialize_seconds
```

**What to do after Example 1:**

1. Open `batch_overview.log` — confirm week selection, timing split, history DB path.
2. Read `selected_runs_all.csv` — one row per chosen run; verify `scan_data_count` and `selection_reason`.
3. For each week, open `by_week/week=NN/runs/<run_id>/profile_leaders__breakout_long__weeks.csv`.
4. Compare `pattern__breakout_long_validated__weeks.csv` across weeks — names that repeat with strong volume/ADX confirmation are higher conviction.
5. Read `calibration/reports/calibration_gate_summary.csv` — only promote rules when `passes_calibration_gates=true`.

```python
# ── Example 2: Slim calibration (default) — best for 13–52 week runs ───────
# calibration_detail="gates_only" is the DEFAULT; skips wide snap__* pivot tables
# and per-profile duplicate table families. Builds only what export_calibration_reports needs.
result = run_batch_prediction_pattern_analysis(
    iso_year=2026,
    start_week=13,
    end_week=52,
    calibration_detail="gates_only",   # default — use "full" only for notebook research
    include_profiles=[                 # optional: subset for exploratory sweeps
        "breakout_long",
        "early_momentum_inflection",
        "fragility_short",
    ],
)
```

**When to use `gates_only` vs `full`:**

| Mode | Builds | Use when |
| --- | --- | --- |
| `gates_only` (default) | `all_profiles_history`, summary, calibration views | Promotion gate decisions, 52-week calibration |
| `full` | Above + wide progression matrices, cross-comparison, 10× per-profile tables | Deep SQL research in DBCode; expect 4–10+ hours at 52 weeks |

```python
# ── Example 3: Chunked 52-week run with resume after interrupt ─────────────
batch_dir = None  # set after first chunk completes

# Chunk 1: weeks 13–16
r1 = run_batch_prediction_pattern_analysis(
    iso_year=2026,
    start_week=13,
    end_week=52,
    chunk_weeks=4,
    output_dir="logs/tradingview_analysis/prediction_analysis/batch_pattern_analysis/batch_2026_w13_w52",
)
batch_dir = r1["output_dir"]

# Chunk 2+: resume skips completed chunks via history_chunk_manifest.json
r2 = run_batch_prediction_pattern_analysis(
    iso_year=2026,
    start_week=13,
    end_week=52,
    chunk_weeks=4,
    resume_from_batch_dir=batch_dir,
)
```

**Chunk/resume behavior:**

- `chunk_weeks=4` splits phase 2 into sequential history builds (13–16, 17–20, …).
- Each chunk appends to a **rolling** history DB under `calibration/rolling/` (auto-enabled when chunking or resuming).
- `history_chunk_manifest.json` tracks completed chunks; re-run with `resume_from_batch_dir` to skip finished work after sleep/interrupt.
- Optional: set `rolling_history_output_dir` to a fixed path (e.g. `calibration/rolling/`) so incremental week adds (21–24, then 25–28) merge into one canonical DB.

```python
# ── Example 4: Incremental week add onto existing rolling DB ─────────────
result = run_batch_prediction_pattern_analysis(
    iso_year=2026,
    start_week=25,
    end_week=28,
    rolling_history_output_dir=(
        "logs/tradingview_analysis/prediction_analysis/"
        "batch_pattern_analysis/batch_2026_w13_w52/calibration/rolling"
    ),
)
```

### Parameters reference

| Parameter | Default | Notes |
| --- | --- | --- |
| `primary_profile_name` | `breakout_long` | Lens for leader exports and primary gate JSON |
| `horizon_name` | `weeks` | Also exports months horizon slices when present |
| `min_scan_data_count` | `3000` | Filters to full-universe runs; raise to ~8000 for backfill-only |
| `include_profiles` | 10 baseline profiles | Multiplies staged rows ~10×; subset for fast sweeps |
| `calibration_detail` | `gates_only` | `full` for research notebooks only |
| `chunk_weeks` | `None` (single build) | e.g. `4` for resumable 52-week chunks |
| `resume_from_batch_dir` | `None` | Prior batch dir with `history_chunk_manifest.json` |
| `rolling_history_output_dir` | auto | Fixed path for incremental merges |
| `aggregate_history` | `True` | Set `False` for phase-1 exports only |
| `show_progress` | `True` | Live stderr progress (Git Bash friendly) |

Execution defaults are tuned for a 32 GB / high-core desktop (`memory_gb=28`, `duckdb_threads=20`). `history_parallel_workers` is logged but incremental staging forces `max_parallel_workers=1` for attach safety.

### Batch export layout

```
logs/tradingview_analysis/prediction_analysis/batch_pattern_analysis/
  batch_<timestamp>_utc/
    batch_overview.log              ← start here
    run_inventory_all.csv
    selected_runs_all.csv
    history_chunk_manifest.json     ← resume checkpoint (when chunking)
    by_week/
      week=21/
        run_inventory.csv
        selected_runs.csv
        week_summary.log
        runs/
          <run_id>/
            run_metadata.json
            profile_leaders__breakout_long__weeks.csv
            pattern__breakout_long_validated__weeks.csv
            pattern__multi_lens_agree__weeks.csv
            pattern__fragility_long_conflict__weeks.csv
            pattern__breakout_long_decile_summary__weeks.csv
            consensus_leaders__weeks.csv
            manager_action_counts__weeks.csv
    calibration/
      rolling/                        ← when chunking/resuming/incremental
        historical_prediction_analysis.duckdb
      reports/
        alignment_stats.csv
        persistent_winners.csv
        false_positive_counts.csv
        calibration_gate_summary.csv
        primary_profile_gate_summary__breakout_long.json
```

### What to look for (batch per-run CSVs)

| File | Filters / content | How to interpret |
| --- | --- | --- |
| `profile_leaders__<profile>__weeks.csv` | Top 50 by `risk_adjusted_score` | Raw ranking for your primary lens — compare symbol overlap week-over-week |
| `pattern__<profile>_validated__weeks.csv` | `coverage >= 0.70`, `relative_volume_10d > 1.3`, volume trend > 1.05, ADX spread > 0 | **Actionable subset** — leaders that pass liquidity + momentum confirmation; fewer names, higher conviction |
| `pattern__multi_lens_agree__weeks.csv` | Consensus score ≥ 0.35, `agreement_ratio >= 0.60`, ≥ 3 profile opinions | Multi-profile agreement — good for core book candidates |
| `pattern__fragility_long_conflict__weeks.csv` | Both `fragility_short` and primary profile scores ≥ 0.35 | **Hedge flag** — long and fragility lenses disagree on the same name; size down or require extra confirmation |
| `pattern__<profile>_decile_summary__weeks.csv` | Top vs bottom decile averages | Sanity check that score separates cohorts (top decile should have higher avg score/coverage) |
| `manager_action_counts__weeks.csv` | Counts by `manager_action_signal` | Regime read — e.g. many `trim_extended_long` signals suggests extended market |

### What to look for (batch calibration reports)

| File | Key columns | Promotion rule |
| --- | --- | --- |
| `calibration_gate_summary.csv` | `aligned_positive_ratio`, `score_price_corr`, `false_positive_rate`, `passes_calibration_gates` | **Must be `true`** for the profile/horizon you want to promote |
| `alignment_stats.csv` | Same metrics, all profiles | Rank profiles by alignment before picking a new lens |
| `persistent_winners.csv` | `presence_ratio`, `snapshots_seen`, `close_return_pct_total` | Names that stayed ranked across snapshots *and* gained — watchlist for Friday sizing |
| `false_positive_counts.csv` | Count of score-up/price-down symbols per profile | High counts = profile fires often without follow-through; tighten filters before weight changes |
| `primary_profile_gate_summary__*.json` | Gate thresholds + pass/fail for primary profile | Machine-readable summary for automation |

Gate thresholds (same as single-week):

| Metric | Minimum | Source view |
| --- | --- | --- |
| `aligned_positive_ratio` | > 0.55 | `vw_profile_horizon_alignment_stats` |
| `score_price_corr` | > 0.15 (weeks/months) | same |
| False-positive rate | < 35% (`false_positive_count / symbol_count`) | `vw_profile_horizon_alignment_stats` |
| Persistence | `presence_ratio >= 0.5` over ≥2 snapshots | progression core view |

### Runtime expectations (from weeks 21–24 benchmark)

| Phase | 4 weeks (actual) | 52 weeks (estimate, unmodified full mode) |
| --- | --- | --- |
| Phase 1 exports | ~3 s | ~1–2 min |
| Phase 2 staging | ~9 s | ~2–3 min |
| Phase 2 materialize | ~20 min | ~4–10+ hours (superlinear with snapshot columns) |
| Phase 3 calibration | ~0.2 s | ~1 s |

With `calibration_detail="gates_only"`, materialization time drops sharply because wide `snap__*` pivot tables and 10× per-profile duplicate builds are skipped. Use `history_materialize_seconds / staged_rows` from `batch_overview.log` to extrapolate after each tuning change.

### Worked example conclusions (weeks 21–24)

See [batch_pattern_analysis_w21_w24_conclusions.md](batch_pattern_analysis_w21_w24_conclusions.md) for findings from the first `main.py` batch run: all profiles failed alignment gates in a mixed backfill/live universe, while score–price correlation remained strong (~0.55 for `breakout_long`).

---

## How to investigate batch results

The batch produces **CSVs for screening** and a **history DuckDB for SQL**. Use CSVs for quick weekly review; open the history DB in DBCode for score–price drill-down. Raw catalog fields and all-fields scans require a **separate join** (not automatic in batch).

### Investigation map

```text
Question                              Start here                          Tool
─────────────────────────────────────────────────────────────────────────────────
Did the run complete correctly?       batch_overview.log                  read
Which runs were selected?             selected_runs_all.csv               read
Who leads this week?                  by_week/.../profile_leaders__*.csv  read
Who passes momentum filters?          by_week/.../pattern__*_validated__* read
Do profiles agree?                    pattern__multi_lens_agree__*.csv    read
Long vs fragility conflict?           pattern__fragility_long_conflict__* read
Does profile X calibrate over time?   calibration/reports/*.csv           read
Score vs price for symbol Y?          history DB → progression_core       SQL
Which pillar drives the score?        weekly DuckDB → profile_components  SQL
What CMF/RSI/Donchian on leaders?     pattern discovery suite (A+B join)  Python/SQL
Why did score-up names fall?          history DB + all-fields DB          SQL ATTACH
```

### Step 1 — Validate the run (always first)

Open `batch_overview.log`:

- Confirm `selected_full_coverage_runs` matches expected trading days.
- Note `scan_data_count` per week — mixed ~10.5k backfill vs ~3.9k live universes distort alignment stats.
- Copy `history_database_path` for SQL sessions.

### Step 2 — Score vs price (history DB)

Point DBCode at the history DB from `batch_overview.log`:

```
.../batch_<timestamp>_utc/calibration/runs/history_aggregation_*/historical_prediction_analysis.duckdb
```

**Profile-level gate check** (same logic as `calibration_gate_summary.csv`):

```sql
SELECT profile_name,
    horizon_name,
    symbol_count,
    ROUND(aligned_positive_ratio, 4) AS aligned_positive_ratio,
    ROUND(score_price_corr, 4) AS score_price_corr,
    ROUND(false_positive_count * 1.0 / NULLIF(symbol_count, 0), 4) AS false_positive_rate
FROM vw_profile_horizon_alignment_stats
WHERE horizon_name = 'weeks'
ORDER BY aligned_positive_ratio DESC;
```

**Symbol-level score vs price path** (one name across snapshots):

```sql
SELECT symbol,
    snapshots_seen,
    first_score,
    last_score,
    score_delta_total,
    close_return_pct_total,
    presence_ratio,
    score_price_alignment_flag
FROM vw_profile_horizon_progression_core
WHERE profile_name = 'breakout_long'
  AND horizon_name = 'weeks'
  AND symbol = 'INCY'
ORDER BY close_return_pct_total DESC;
```

**False-positive autopsy cohort** (score improved, price fell):

```sql
SELECT symbol,
    score_delta_total,
    close_return_pct_total,
    snapshots_seen,
    presence_ratio
FROM vw_profile_horizon_progression_core
WHERE profile_name = 'breakout_long'
  AND horizon_name = 'weeks'
  AND score_delta_total > 0
  AND close_return_pct_total < 0
ORDER BY score_delta_total DESC
LIMIT 50;
```

**Per-snapshot deltas** (only when history built with `calibration_detail="full"`):

```sql
SELECT symbol, snapshot_label, score_delta, close_return_pct
FROM vw_profile_horizon_snapshot_deltas
WHERE profile_name = 'breakout_long'
  AND horizon_name = 'weeks'
  AND symbol = 'INCY'
ORDER BY snapshot_label;
```

### Step 3 — Score components (weekly prediction DuckDB)

Batch CSVs export **final scores**, not pillar breakdown. The weekly DuckDB (path in `selected_runs_all.csv` → `database_path`) stores `profile_components` with eight pillars:

`attention`, `event`, `momentum`, `trend`, `quality`, `valuation`, `safety`, `scale`

```sql
-- ATTACH or open the weekly move_prediction_*.duckdb directly
SELECT p.symbol,
    p.score,
    p.risk_adjusted_score,
    c.momentum,
    c.trend,
    c.quality,
    c.valuation,
    c.attention
FROM profile_horizon_scores p
JOIN profile_components c
    ON p.run_id = c.run_id
   AND p.profile_name = c.profile_name
   AND p.symbol = c.symbol
WHERE p.run_id = 'move_prediction_20260610_1437_utc_698d1f59'
  AND p.profile_name = 'breakout_long'
  AND p.horizon_name = 'weeks'
ORDER BY p.risk_adjusted_score DESC
LIMIT 25;
```

Individual derived signals (`donchian_position`, `chaikin_money_flow_signal`, etc.) are **not persisted** — they are folded into pillar scores at analysis time. To inspect them you need the **all-fields scan** (below).

### Step 4 — Raw scan fields at scoring time

`raw_scan_rows` in the weekly DuckDB holds the move-prediction scan payload (hundreds of columns). Batch validated-pattern exports only surface three: `relative_volume_10d_calc`, volume trend, ADX spread.

```sql
SELECT r.symbol,
    r.relative_volume_10d_calc,
    r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend,
    r."ADX+DI" - r."ADX-DI" AS adx_spread,
    r."RSI",
    r."Perf.1M"
FROM raw_scan_rows r
WHERE r.run_id = '<run_id_from_selected_runs_all.csv>'
ORDER BY r.relative_volume_10d_calc DESC NULLS LAST
LIMIT 25;
```

Use `DESCRIBE raw_scan_rows` to list all available scan columns for a given run.

### Step 5 — All-fields scan join (NOT in batch — manual)

The batch does **not** attach `tradingview_all_fields_*.duckdb`. That is the **B database** in the three-database model. Run after batch screening to explain *why* leaders look good:

**Option A — Python (Thursday suite):**

```python
from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    PatternDiscoveryPaths,
    run_prediction_all_fields_join_preview,
    run_profile_cohort_field_comparison,
    run_false_positive_field_autopsy,
)

paths = PatternDiscoveryPaths(
    prediction_db=r".../duckdb_runs/iso_year=2026/week=24/move_prediction_2026_W24.duckdb",
    all_fields_db=r".../trading_view_all_fields_data/<dd_mm_yyyy>/tradingview_all_fields_*.duckdb",
    history_db=r".../batch_.../calibration/.../historical_prediction_analysis.duckdb",
)
run_prediction_all_fields_join_preview(paths, profile_name="breakout_long")
run_false_positive_field_autopsy(paths, profile_name="breakout_long")
```

**Option B — SQL (DBCode, two ATTACH):**

```sql
ATTACH '<WEEKLY_PREDICTION_DB>' AS pred (READ_ONLY);
ATTACH '<ALL_FIELDS_DB>' AS fields (READ_ONLY);

SELECT h.symbol, h.score, h.risk_adjusted_score,
    a."ChaikinMoneyFlow", a."DonchCh20.Upper", a."DonchCh20.Lower",
    a."RSI", a."HullMA9"
FROM pred.profile_horizon_scores h
JOIN fields.all_fields_rows a
    ON h.symbol = a.symbol
    OR h.symbol LIKE '%:' || a.symbol
    OR a.symbol LIKE '%:' || h.symbol
WHERE h.profile_name = 'breakout_long'
  AND h.horizon_name = 'weeks'
  AND h.run_id = '<run_id>'
ORDER BY h.risk_adjusted_score DESC
LIMIT 50;
```

See `@block` templates in `sql_connections_space/move_prediction_pattern_discovery.session.sql`.

### Step 6 — Cross-week symbol recurrence (CSV or SQL)

**CSV approach** — compare `pattern__breakout_long_validated__weeks.csv` across days in `by_week/week=NN/runs/`. Names appearing on ≥2 consecutive days with rising `risk_adjusted_score` are higher conviction than one-day spikes.

**SQL approach** — symbols seen in many snapshots with positive returns:

```sql
SELECT symbol,
    snapshots_seen,
    presence_ratio,
    score_delta_total,
    close_return_pct_total
FROM vw_profile_horizon_progression_core
WHERE profile_name = 'breakout_long'
  AND horizon_name = 'weeks'
  AND snapshots_seen >= 4
  AND presence_ratio >= 0.5
  AND close_return_pct_total > 0
ORDER BY close_return_pct_total DESC
LIMIT 50;
```

Exclude single-name outliers (e.g. extreme returners dominating `persistent_winners.csv`) before drawing pattern rules.

### Coverage matrix — what batch does vs what you still need

| Layer | Batch covers | You still need |
| --- | --- | --- |
| Multi-week score ↔ price | Yes — history DB + calibration CSVs | Consistent universe filter; longer window (13–52 wk) |
| Per-run leaders / patterns | Yes — `by_week/.../runs/` CSVs | Cross-week manual comparison or SQL recurrence |
| Profile pillars (momentum, quality, …) | Stored, not exported | SQL on `profile_components` (Step 3) |
| Derived signals (CMF, Donchian, …) | No | All-fields join (Step 5) |
| Full catalog (~3.5k fields) | No | `export_all_tradingview_fields_duckdb` + pattern discovery |
| False-positive field signatures | No | `run_false_positive_field_autopsy` with A+B+C |
| Promotion decision | Gate CSVs produced | Gates must pass; none did in w21–24 run |

---

## SQL session blocks

**File:** `sql_connections_space/move_prediction_pattern_discovery.session.sql`

| Block | Purpose |
| --- | --- |
| `q_pattern_join_preview` | Latest-run leaders joined to Tier-1 all-fields columns |
| `q_pattern_cohort_field_compare` | Top vs bottom decile field means for a profile cohort |
| `q_pattern_false_positive_autopsy` | Historical score-up/price-down joined to raw features |
| `q_pattern_calibration_gates` | `aligned_positive_ratio > 0.55` and `score_price_corr > 0.15` gate check |
| `q_pattern_post_breakout_exhaustion` | Perf.1M top decile + RSI > 70 + fading `volume_trend` |

Replace `<ALL_FIELDS_DB>` and `<HISTORY_DB>` with absolute paths before running in DBCode. For batch runs, point `<HISTORY_DB>` at `calibration/rolling/historical_prediction_analysis.duckdb` (or the path in `batch_overview.log`).

---

## Recommended weekly rhythm

```text
Monday    run_full_analysis_suite_duckdb (+ earnings-priority if catalyst week)
Monday    export_all_tradingview_fields_duckdb
Tuesday   run_investment_shortlist_pipeline
Wednesday run_move_prediction_history_aggregation_duckdb (last 4–8 weeks)  # optional if batch ran recently
Thursday  run_pattern_discovery_suite (single-week A+B join) + SQL calibration blocks
Monthly   run_batch_prediction_pattern_analysis (multi-week gates, chunk if 52 weeks)
Friday    Promote 0–2 rules; size per position_sizing_distribution_framework.md
```

---

## Skepticism rules (enforced by process)

1. **No single-run conclusions** — require `snapshots_seen >= 2`; prefer ≥4 weekly runs before promoting a rule.
2. **Universe relativity** — re-test on all-universe AND per-industry scans.
3. **Look-ahead awareness** — in-run `Perf.*` is trailing; use history DB `close_return_pct_total` for realized moves.
4. **Multiple-testing discipline** — pre-register hypotheses; note FDR when mining without theory.
5. **False-positive taxonomy** — test against `q_hist_false_positives` and `q_hist_snapshot_mismatch` cohorts.
6. **Liquidity reality** — exclude names where intended position > 5% of `AvgValue.Traded_10d`.

---

## Promotion ladder

| Stage | Action | Location |
| --- | --- | --- |
| 1 — Observation | Weekly review log | Personal journal |
| 2 — SQL prototype | `@block` in pattern discovery or investment session | DBCode |
| 3 — Shortlist gate | Automated filter | `trading_view_investment_shortlist_pipeline.py` |
| 4 — Weight change | `PRESET_SCORING_PROFILES` | `trading_view_move_prediction_analysis.py` |
| 5 — New profile | Only if orthogonal to existing lenses | Same + tests + docs |

Batch calibration gate pass (`passes_calibration_gates=true`) is required before stage 4 or 5.

---

## Cross-references

- [Weeks 21–24 batch run conclusions](batch_pattern_analysis_w21_w24_conclusions.md)
- [Implementation & usage guide](active_manager_implementation_usage.md)
- [Profile weighting reference](tradingview_move_prediction_profile_weighting_reference.md)
- [History aggregation workflow](move_prediction_history_duckdb_weekly_workflow_guide.md)
- [Investment SQL blocks](../sql_connections_space/investment_opportunity_queries.session.sql)
