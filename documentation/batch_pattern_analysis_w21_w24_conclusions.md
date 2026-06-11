# Batch Pattern Analysis — Weeks 21–24 Run Conclusions

Run folder: `logs/tradingview_analysis/prediction_analysis/batch_pattern_analysis/batch_20260610_152329_utc`

Triggered from `src/main.py` via `run_batch_prediction_pattern_analysis(iso_year=2026, start_week=21, end_week=24)`.

---

## Executive summary

This run successfully built a **multi-week score-vs-price calibration layer** over 18 daily snapshots (4 ISO weeks). It answers: *“Do profile scores move with price over time, and which names recur as leaders?”*

It does **not** yet answer: *“Which raw catalog fields (all-fields scan) or signal components explain those moves?”* — that requires the separate Thursday `run_pattern_discovery_suite` workflow or manual SQL joins documented below.

**Bottom line for promotion decisions:** No profile passes calibration gates in this window. Do not promote weight changes off this run alone. The tactical exports (validated leaders, multi-lens agree, fragility conflicts) are still useful for weekly watchlists.

---

## What ran

| Metric | Value |
| --- | --- |
| Wall clock | ~19.9 min (99.7% in history aggregation) |
| Weekly DuckDBs | 4 (weeks 21–24) |
| Inventory runs | 30 |
| Selected full-coverage runs | 18 (one per calendar day) |
| Profiles in history build | 10 baseline profiles |
| History DB symbols (breakout_long / weeks) | 11,049 progression rows |
| Primary lens | `breakout_long` / `weeks` |

### Universe mix (important for interpretation)

| Weeks | Run type | Typical `scan_data_count` |
| --- | --- | --- |
| 21–22 | Mostly CSV backfill | ~10,500 |
| 23–24 | Mostly live API scans | ~3,900 |

The history DB merges **different universe sizes**. Symbols appearing only in backfill weeks have fewer snapshots; symbols in both eras show up in the `snapshots_seen` distribution (peaks at 11 and 18). Treat cross-era alignment stats as **noisy** until you filter to a consistent universe (`min_scan_data_count=8000` or live-only weeks).

This run used `min_scan_data_count=3000`, so live runs with ~3.9k symbols were included alongside ~10.5k backfill runs.

### History build mode

The produced history DB contains **full** materialization (wide progression tables, per-profile table families, cross-comparison). This run predates or bypassed `calibration_detail="gates_only"`. Future runs should use slim mode unless you need notebook-level SQL on `snap__*` columns.

---

## Calibration findings (score vs price)

Source: `calibration/reports/calibration_gate_summary.csv` and history DB view `vw_profile_horizon_alignment_stats`.

### Gate results — all profiles fail

| Profile (weeks horizon) | `aligned_positive_ratio` | `score_price_corr` | `false_positive_rate` | Passes gates? |
| --- | ---: | ---: | ---: | --- |
| `breakout_long` | 0.339 | **0.557** | **0.125** | **No** |
| `early_momentum_inflection` | 0.337 | **0.548** | **0.122** | **No** |
| `fragility_short` | 0.330 | **0.513** | **0.129** | **No** |
| `sector_relative_outperformer` | 0.299 | **0.531** | **0.091** | **No** |
| `quality_value_compounder` | 0.268 | 0.412 | **0.093** | **No** |
| `asymmetric_value` | 0.084 | **−0.248** | 0.193 | **No** |

Gate thresholds: `aligned_positive_ratio > 0.55`, `score_price_corr > 0.15`, `false_positive_rate < 0.35`.

### How to read this

**What passes:** Correlation and false-positive rate. For `breakout_long` / weeks, when scores and prices co-move, they co-move strongly (corr ≈ 0.56). Only ~12.5% of the tracked universe is “score up, price down.”

**What fails:** `aligned_positive_ratio`. Only **33.9%** of all tracked symbols had both score improvement and positive price return across the window. The gate requires **55%**.

Cohort breakdown for `breakout_long` / weeks (`vw_profile_horizon_progression_core`):

| Cohort | Count | Share of 11,049 |
| --- | ---: | ---: |
| Score improved (`score_delta_total > 0`) | 5,190 | 47% |
| Score improved **and** price up (aligned) | 3,741 | 34% |
| Score improved **and** price down (false positive) | 1,376 | 12% |
| Score did not improve | 5,676 | 51% |

Among symbols whose scores **did** improve, ~72% also saw price up — but the gate divides by the **entire** tracked universe, not just the score-improved cohort. That makes the alignment ratio look worse than the conditional hit rate.

**Interpretation:** Profiles rank and discriminate; they are not yet “calibrated for promotion” under the current strict gate definition across a mixed backfill/live universe. Before changing weights, either (a) extend to 13–24+ weeks on a **consistent** universe, or (b) revisit whether the gate denominator should be score-improved symbols only.

### Persistent winners caveat

`persistent_winners.csv` is dominated by **TSX:CURA** (+222% over 18 snapshots, all profiles). Single-name outliers inflate “persistence” reads. Always inspect sector/industry and corporate actions before treating persistent winners as a pattern.

---

## Per-run pattern findings (week 24 live snapshot)

Latest run: `move_prediction_20260610_1437_utc_698d1f59` (~3,877 symbols).

### Manager action regime

From `manager_action_counts__weeks.csv`:

| Signal | Symbols | Avg risk-adj score |
| --- | ---: | ---: |
| `neutral_watch` | 3,412 | 0.05 |
| `avoid_value_trap` | 216 | 0.04 |
| `hold_quality_long` | 143 | 0.54 |
| `add_long_breakout` | 50 | **0.97** |

Most of the universe is neutral. Actionable breakout adds are a **small tail** (~50 names) with materially higher risk-adjusted scores.

### High-conviction names (multi-lens)

`pattern__multi_lens_agree__weeks.csv` — consensus score ≥ 0.35, agreement ≥ 0.60, ≥ 3 opinions. Examples: INCY, NTES, TROW, GPROFUT (10/10 profiles agree). These are stronger candidates than raw profile leaders alone.

### Hedge flags (fragility conflict)

`pattern__fragility_long_conflict__weeks.csv` — 50+ names score ≥ 0.35 on **both** `breakout_long` and `fragility_short`. Examples: INCY, CASY, GPROFUT, OSCR, AMAT, KLAC.

**Do not size these as pure longs without a hedge lens** — the model itself disagrees on direction.

### Validated breakout subset

`pattern__breakout_long_validated__weeks.csv` applies liquidity/momentum filters (coverage ≥ 0.70, relative volume > 1.3, volume trend > 1.05, ADX spread > 0). Only ~18 names vs 50 `add_long_breakout` — use this as the ** tighter** action list.

---

## What the batch script covers vs your request

| Your goal | Covered by batch? | Where / how |
| --- | --- | --- |
| Score vs price comparison over time | **Yes** | History DB views: `vw_profile_horizon_progression_core`, `vw_profile_horizon_alignment_stats`; CSVs under `calibration/reports/` |
| Per-run leader rankings | **Yes** | `by_week/.../profile_leaders__*.csv`, `consensus_leaders__*.csv` |
| Cross-profile agreement / conflict | **Yes** | `pattern__multi_lens_agree__*.csv`, `pattern__fragility_long_conflict__*.csv` |
| Raw scan fields at scoring time | **Partial** | `raw_scan_rows` in weekly DuckDB; batch exports **3 fields** in validated pattern (relative volume, volume trend, ADX spread) |
| Profile score **components** (attention, momentum, …) | **Not exported** | Stored in weekly DuckDB table `profile_components` — query manually (see reference doc) |
| Individual derived **signals** (donchian, CMF, …) | **No** | Not persisted as columns; only baked into scores |
| **All-fields scan** (`tradingview_all_fields_*.duckdb`) | **No** | Batch is prediction-only by design; use `run_pattern_discovery_suite` or SQL ATTACH |
| Field-level winner/loser autopsy | **No** | Requires A+B join via pattern discovery module |
| Automated promotion | **Blocked** | All gates fail in this window |

---

## Recommended next steps

1. **Re-run with universe consistency** — `min_scan_data_count=8000` for backfill-only, or restrict to weeks 23–24 live runs only, then compare gates.
2. **Extend window** — weeks 13–24 or 13–52 with `calibration_detail="gates_only"` and `chunk_weeks=4` before any weight promotion.
3. **Thursday all-fields join** — for names in `multi_lens_agree` or `validated`, run `run_pattern_discovery_suite` to see CMF/Donchian/RSI on the same symbols.
4. **Manual component drill-down** — query `profile_components` on weekly DuckDB for top leaders to see which **pillars** (momentum vs quality vs valuation) drive the score.
5. **Do not promote** profile weights from this run — gates fail; use exports for watchlists only.

---

## Related docs

- [Pattern discovery reference](tradingview_move_prediction_pattern_discovery_reference.md) — investigation SQL and workflow map
- [Active manager usage](active_manager_implementation_usage.md)
- [Batch runtime plan](../.cursor/plans/batch_runtime_analysis_a4828190.plan.md)
