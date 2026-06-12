# Breakout Active Manager Lens — Usage Guide

This document describes the enhanced `breakout_long_v1` profile and supporting infrastructure for active portfolio management based on the dual-horizon conviction + entry gate system.

---

## What Changed

### 1. Dual-Gate Active Management (`trading_view_move_prediction_analysis.py`)

**New Constants:**
```python
BREAKOUT_WEEKS_CONVICT_THRESHOLD = 1.10      # STRONG_UP - primary conviction gate
BREAKOUT_WEEKS_HIGH_CONVICT_THRESHOLD = 1.50 # VERY_HIGH - high conviction tier
BREAKOUT_DAYS_ENTRY_THRESHOLD = 0.35         # DIRECTIONAL - minimum entry timing
BREAKOUT_DAYS_STRONG_ENTRY_THRESHOLD = 0.75  # Strong entry timing
BREAKOUT_COVERAGE_FLOOR = 0.70
BREAKOUT_CONFIDENCE_FLOOR = 60.0
```

**New Action Signals:**
- `add_long_breakout_high_conviction` — passes weeks>=1.50 OR days>=0.75 with tape confirms
- `watch_breakout_await_tape` — strong components but weak tape (volume/ADX)
- `watch_conviction_await_entry` — weeks conviction but days hasn't triggered
- `watch_momentum_await_conviction` — days momentum but weeks hasn't triggered

### 2. Investment Story Layer

Each breakout candidate now carries a structured **investment story**:

| Field | Description |
|-------|-------------|
| `conviction_tier` | `high_conviction` (≥1.50), `actionable` (≥1.10), `watch` (≥0.35), `none` |
| `entry_readiness` | `ready_now`, `ready`, `await_tape`, `await_entry`, `watch`, `none` |
| `thesis_bullets` | Auto-generated narrative: what components drive the signal, tape confirmations |
| `tape_pass` | Boolean: 3/5 confirms passed (volume_trend>1.05, rel_vol>1.3, ADX>0, Aroon>20, not_coiled) |
| `risk_flags` | false_breakout, fragility_conflict, earnings_within_7d, extended_tape, low_coverage, low_confidence |
| `size_tier` | `full`, `half`, `watchlist`, `reject` — maps conviction × composite risk |
| `composite_risk_score` | 0-100 composite (safety 25%, extended tape 25%, event 20%, false breakout 20%, fragility 10%) |
| `composite_risk_label` | `low`, `moderate`, `elevated`, `severe` |

**Tape Confirmations Checked:**
- `volume_trend > 1.05` (sustained accumulation)
- `relative_volume_10d_calc > 1.3` (above-average participation)
- `adx_directional_spread > 0` (ADX+DI > ADX-DI)
- `aroon_spread > 20` (new uptrend formation)
- `bb_width_percentile >= 25` (not coiled — excludes tight squeezes)
- `chaikin_money_flow_signal > 0` (secondary)
- `donchian_position > 0.5` (secondary)

### 3. Composite Risk Score

The risk model for breakouts is now multi-factor:

```
Composite Risk Score =
  Safety Component Risk × 25% +
  Extended Tape Exhaustion × 25% +
  Event/Catalyst Risk × 20% +
  False Breakout Pattern × 20% +
  Cross-Profile Conflict × 10%
```

**Size Tier Mapping:**

| Conviction | Risk Label | Entry Readiness | Size Tier |
|------------|------------|-----------------|-----------|
| High (≥1.50) | Low/Moderate | Ready/Ready Now | Full (earnings→Half) |
| Actionable (≥1.10) | Low/Moderate | Ready/Ready Now | Full (earnings→Half) |
| Actionable (≥1.10) | Elevated | Ready | Half |
| Any | Severe | — | Reject |
| Extended Tape | — | — | Half |
| False Breakout or Fragility | — | — | Watchlist |

### 4. Pool Analyzer Scale Fixes (`trading_view_move_prediction_pool_analyzer.py`)

**Fixed Bugs:**

| Before (Wrong) | After (Correct) | Scale |
|----------------|-----------------|-------|
| `score >= 60/70/80` | `score >= 0.35/1.10/1.50` | -3 to +3 |
| `confidence >= 0.6/0.7` | `confidence >= 60/70` | 5-99 |
| `direction > 0` | `direction IN ('Strong Up', 'Up')` | Categorical |
| `AVG(direction)` | `bullish_direction_ratio` via CASE | — |

**New Function:**

```python
from trading_view_move_prediction_pool_analyzer import export_profile_threshold_calibration

# Export threshold ladder for a profile
export_profile_threshold_calibration(
    database_path=".../pooled_move_prediction_runs.duckdb",
    profile_name="breakout_long",
    horizon_name="weeks",
    output_path=".../breakout_long_weeks_threshold_calibration.json",
)
```

This generates JSON with:
- `ALL` baseline correlation and hit rate
- `BULLISH_UP` (≥0.35) lift vs baseline
- `STRONG_UP` (≥1.10) lift vs baseline
- `VERY_HIGH` (≥1.50) lift vs baseline
- `ELITE` (≥2.00) lift vs baseline
- `recommended_threshold_slice` — auto-selected based on hit-rate improvement

### 5. Calibration Loop Enhancements (`trading_view_move_prediction_batch_pattern_analysis.py`)

**New Gate:** `conditional_hit_rate_min = 0.65`

The calibration system now supports **dual gate evaluation**:

1. **Aligned Positive Ratio Gate** (original): 55% of all tracked symbols must have both score improvement AND price up
2. **Conditional Hit Rate Gate** (new): 65% of symbols whose scores improved must have had price up

The conditional gate is more lenient but better matches the question: *"When this lens speaks up (score improved), does it point correctly?"*

The `passes_calibration_gates` boolean now passes if **either** gate is satisfied (plus correlation and false-positive gates).

---

## Running the Enhanced Suite

### Weekly Active Manager Workflow

```bash
# Step 1: Run the full suite with breakout_long_v1 profile
python -m src.main \
    --suite active_manager_v1 \
    --run-label "week_26_2026_breakout"

# Step 2: Export threshold calibration from accumulated pool data
python -c "
from src.data_analysis_scripts.trading_view_move_prediction_pool_analyzer import export_profile_threshold_calibration
export_profile_threshold_calibration(
    'logs/tradingview_analysis/prediction_analysis/multi_run_pool/runs/LATEST/pooled_move_prediction_runs.duckdb',
    profile_name='breakout_long',
    horizon_name='weeks',
)
"

# Step 3: Check generated breakout narrative in log
# Location: logs/tradingview_analysis/prediction_analysis/iso_year=YYYY/week=WW/RUN_ID/
# Files: tradingview_move_prediction_scan__breakout_long_v1.log
# Sections:
#   - BREAKOUT ACTIVE MANAGEMENT — VALIDATED LEADERS
#   - BREAKOUT ACTIVE MANAGEMENT — REJECTED NEAR-MISSES
```

### Programmatic Access to Stories

```python
from src.data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)

result = run_full_analysis_suite_duckdb(
    scan_data=scan_rows,
    profile_suite_path="config/move_prediction_profiles/suites/active_manager_v1.json",
    run_label="2026_w26_live",
)

# Access breakout stories via DuckDB:
# SELECT * FROM breakout_investment_stories WHERE profile_name = 'breakout_long_v1'
```

### DuckDB Story Table Schema

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | VARCHAR | Run identifier |
| `profile_name` | VARCHAR | `breakout_long_v1` |
| `symbol` | VARCHAR | Ticker |
| `conviction_tier` | VARCHAR | `high_conviction`, `actionable`, `watch`, `none` |
| `entry_readiness` | VARCHAR | `ready_now`, `ready`, `await_tape`, `await_entry`, `watch`, `none` |
| `tape_pass` | BOOLEAN | 3/5 confirmations passed |
| `composite_risk_score` | INTEGER | 0-100 composite risk |
| `composite_risk_label` | VARCHAR | `low`, `moderate`, `elevated`, `severe` |
| `size_tier` | VARCHAR | `full`, `half`, `watchlist`, `reject` |
| `thesis_1`, `thesis_2` | VARCHAR | Auto-generated narrative bullets |
| `volume_trend_ok`, `relative_volume_ok`, `adx_ok`, `aroon_ok`, `not_coiled`, `cmf_ok`, `donchian_ok` | BOOLEAN | Individual confirmations |

---

## Interpreting Log Output

### Validated Leaders Section

```
Rank  Symbol   Company              Weeks   Days    RAS    Risk Entry        Size    Composite Risk
1     AAPL     Apple Inc.          1.45   0.82   1.38    earn ready_now      half    45/moderate
      • momentum, attention, trend dominate; quality/valuation near baseline
      • Tape confirms: sustained volume; above-avg participation; ADX trend strength
2     NVDA     NVIDIA Corp         1.62   0.91   1.55    -      ready_now      full    18/low
      • momentum, attention dominate; quality/valuation near baseline
      • Tape confirms: sustained volume; ADX trend strength; Aroon uptrend formation
```

**Risk flag abbreviations:**
- `earn` = earnings_within_7d
- `ext` = extended_tape
- `cov` = low_coverage
- `conf` = low_confidence

### Rejected Near-Misses Section

```
Rank  Symbol   Company              Weeks   Days    RAS    Risk Reject Reason               Entry
1     XYZ      Example Corp        1.25   0.45   1.12    55   await_entry, weak_tape       await_entry
```

**Reject reasons:**
- `false_breakout` — high days score but weak weeks/safety structure
- `fragility` — `fragility_short` profile also flags this symbol
- `weak_tape` — volume/ADX/Aroon confirms not met
- `await_tape` — days entry present but tape not confirming
- `await_entry` — weeks conviction but days entry not triggered

---

## Success Metrics to Track

| Metric | How to Measure | Target |
|--------|---------------|--------|
| Dual-gate hit rate | `bullish_hit_rate` at `STRONG_UP` slice | ≥ 0.90 |
| False breakout rate | % of `full`/`half` sized names that fail tape within 5 days | < 0.20 |
| Story coverage | % of validated leaders with complete thesis bullets | 100% |
| Composite risk accuracy | Backtest: severe risk names should underperform low risk by > 50% | Validate |
| Tape confirm efficacy | Compare `tape_pass=True` vs `tape_pass=False` returns | +10% weekly |

---

## Profile Tuning Roadmap (v1.1)

After accumulating 4+ weeks of dual-gate story data:

1. **Review threshold calibration JSON** — confirm 1.10 is optimal or adjust to 1.50 if VERY_HIGH shows better lift
2. **Examine rejection patterns** — if many strong RAS names fail on `weak_tape`, consider lowering tape confirms to 2/5
3. **Tune exhaustion dampeners** — if `extended_tape` flag predicts poor returns, strengthen `Perf.1M` weight reduction
4. **Update `breakout_long_v1.json`** → `breakout_long_v1.1.json` with confirmed weights
5. **Update `active_manager_v1.json`** suite reference to v1.1

---

## What We Explicitly Did NOT Do

- **No blanket reweighting** of all signals to maximize correlation — pool data shows corr ~0.01 at ALL slice; hit rate is the right metric
- **No promotion from single 5-week pool** — gates require consistent validation across multiple ISO weeks
- **No replacement of manager judgment** — dual gate + risk tier + story flags preserve human-readable rationale for position sizing

---

## Key Files Changed

| File | Change Summary |
|------|--------------|
| `src/data_analysis_scripts/trading_view_move_prediction_analysis.py` | Dual-gate constants, `_breakout_tape_confirms()`, `_breakout_composite_risk_score()`, `_build_breakout_investment_story()`, `_log_breakout_narrative_sections()` |
| `src/data_analysis_scripts/trading_view_move_prediction_pool_analyzer.py` | Scale bug fixes (score 0.35-2.00, confidence 60-70, categorical direction), `export_profile_threshold_calibration()` |
| `src/data_analysis_scripts/trading_view_move_prediction_batch_pattern_analysis.py` | `conditional_hit_rate_min` gate, dual gate evaluation logic |

---

## Questions or Issues

If `strongest_corr` shows `NaN` in threshold exports, ensure you run the **fixed** query version that filters `isfinite(corr_value)` — the original query had a bug where `direction_vs_perf_w_corr` with zero variance returned NaN but passed `IS NOT NULL`.

For operational issues with the story layer, check:
- `breakout_investment_stories` table exists in DuckDB (auto-created on first run)
- `include_blind_spot_sections=True` in your suite config (adds context from other profiles)
