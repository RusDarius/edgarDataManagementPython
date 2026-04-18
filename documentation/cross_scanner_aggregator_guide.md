# Cross-Scanner Aggregator — Implementation Guide

## Overview

The **trading_view_cross_scanner_aggregator** module merges the outputs of two independent analysis pipelines — the **move-prediction suite** (all 8 preset profiles) and the **forward-target scanner** — into a single conviction-ranked report.

The aggregator runs **every prediction profile independently**, then computes a weighted consensus prediction score per horizon using `CONSENSUS_PROFILE_WEIGHTS` (same weights as the prediction module's consensus aggregator).  This weighted score is cross-referenced with the target module's upside estimates to produce conviction scores, divergence flags, and composite rankings.

Neither pipeline alone captures the full picture. The aggregator cross-references them to surface:
- Names where **all profiles and targets agree** (high conviction)
- Names where they **diverge** (manual review flags)
- **Coverage blind spots** where one module is confident but the other is data-starved
- **Profile agreement ratio** — what fraction of the 8 profiles agree on direction

---

## Blindspots Addressed

| # | Blindspot | What Was Missing | How the Aggregator Fixes It |
|---|-----------|------------------|-----------------------------|
| 1 | Directional-price divergence | Prediction says "Strong Up" but target shows the stock already trades above fair value | Flags names where prediction direction and target upside have opposite signs |
| 2 | Coverage gap asymmetry | Prediction has high confidence from momentum/trend data while targets have zero trajectory coverage (fundamentals missing) | Penalises names where `abs(pred_coverage − target_coverage) ≥ 40%` |
| 3 | Horizon misalignment | Prediction "weeks" bullish while targets "near_term" bearish (technical resistance) | Detects when short-term and long-term conviction scores disagree on direction |
| 4 | Profile-target agreement | A name ranked #1 by quality_value_compounder but "Strong Overvaluation" in targets | Signal agreement score exposes momentum-disguised-as-quality |
| 5 | Missing short-side target context | Prediction fragility_short flags names but has no price target for downside | Target module's bear price fills that gap in the merged view |
| 6 | Earnings-proximity blind spot | Prediction penalises confidence near earnings but target module ignores event timing | Flags names within 14 days of earnings with low prediction confidence |
| 7 | Single peer-group limitation | Both modules treat entire scan as one peer group | Sector conviction summary surfaces per-sector deltas |

---

## Architecture

```
TradingView API scan data
        │
        ▼
 Shared first-stage pipeline (run ONCE):
 ├── _enrich_with_peer_metrics()
 ├── _build_derived_metrics()
 └── _build_metric_profiles()
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
 Prediction pipeline (×8 profiles)        Target pipeline
 ├── _build_prediction_rows()             ├── _build_peer_multiple_stats()
 │   per profile → score, direction,      ├── _build_company_targets()
 │   confidence, coverage per horizon     │   → bear/base/bull prices
 │                                        │   → opportunity scores
 │   Weighted aggregation across          │   → valuation summary
 │   profiles using CONSENSUS_            │
 │   PROFILE_WEIGHTS                      │
 └────────────────┬───────────────────────┘
                  ▼
           Cross-reference
      ├── signal agreement
      ├── conviction score
      ├── divergence flags
      └── composite ranking
             │
             ▼
      Report (.log + .csv)
```

---

## Horizon Cross-Mapping

The prediction module uses 4 horizons; the target module uses 3. The aggregator maps them:

| Target Horizon | Prediction Horizon Used |
|---------------|------------------------|
| near_term (1–4 weeks) | weeks |
| medium_term (1–6 months) | months |
| long_term (6–24 months) | years |

---

## Signal Agreement

Measures how well prediction direction and target upside agree. Range: `[-1.0, +1.0]`.

```
pred_direction = clamp(prediction_score / 2.0, -1.0, 1.0)
target_direction = clamp(base_upside_pct / 50.0, -1.0, 1.0)
agreement = pred_direction × target_direction
magnitude = min(|pred_direction|, |target_direction|)
signal_agreement = clamp(agreement × (0.5 + 0.5 × magnitude), -1.0, 1.0)
```

| Agreement | Label |
|-----------|-------|
| ≥ 0.80 | Strong Agreement |
| ≥ 0.50 | Moderate Agreement |
| ≥ 0.00 | Weak Agreement |
| ≥ -0.20 | Mild Divergence |
| < -0.20 | Divergent |

---

## Conviction Score

Weighted composite of 5 signal sources, normalised to `[-2.0, +2.0]`:

| Source | Weight | Normalisation |
|--------|--------|---------------|
| Prediction score | 30% | score / 2.0 → [-1, 1] |
| Target upside | 25% | upside% / 50.0 → [-1, 1] |
| Signal agreement | 20% | already [-1, 1] |
| Coverage quality | 15% | avg(pred_cov, target_cov) × 2 − 1 |
| Component floor | 10% | (quality + safety) / 3.0 |

### Conviction Labels

| Score | Label |
|-------|-------|
| ≥ 1.20 | Strong Buy |
| ≥ 0.60 | Buy |
| ≥ 0.25 | Lean Long |
| ≥ -0.25 | Neutral |
| ≥ -0.60 | Lean Short |
| ≥ -1.20 | Sell |
| < -1.20 | Strong Sell |

### Aggregate Conviction

Weighted average across horizons:

| Horizon | Weight |
|---------|--------|
| near_term | 20% |
| medium_term | 45% |
| long_term | 35% |

---

## Divergence Flags

### 1. Directional-Price Divergence
Prediction says ≥ 0.35 (Up) but target upside < -10%, or prediction says ≤ -0.35 (Down) but target upside > +10%.

### 2. Coverage Asymmetry
`abs(prediction_coverage − target_coverage) ≥ 40%`

### 3. Horizon Conflict
Near-term and long-term conviction scores have opposite signs, both with magnitude > 0.15.

### 4. Earnings Proximity
Within 14 days of earnings AND prediction confidence < 45%.

---

## Report Output

### Log File Sections

1. **Methodology** — explains the aggregation approach
2. **Conviction ranking per horizon** — top opportunities and most overvalued (×3 horizons × 2 directions = 6 tables)
3. **Divergence alerts** — 4 alert types with affected names
4. **Sector conviction summary** — per-sector average conviction, agreement, and flag counts
5. **High-conviction names** — long and short candidates with clean signal alignment

### CSV Output

Each row contains:
- Identity fields (symbol, company, industry, sector, market_cap, close)
- Aggregate conviction score and label
- Coverage diagnostics (prediction avg, target avg, gap)
- 4 divergence flags (boolean)
- Per-horizon (×3): prediction score/direction/confidence/coverage, base/bear/bull price, upside%, opportunity score/label, target coverage, signal agreement/label, conviction score/label
- Component scores (×8)

---

## API Usage

### Cross-scanner aggregate (all preset profiles, default)

```python
from data_analysis_scripts.trading_view_cross_scanner_aggregator import (
    run_cross_scanner_aggregate,
)

# All 8 preset profiles are evaluated and weighted-aggregated.
log_file = run_cross_scanner_aggregate(
    scan_data=scan_data,
    industries=["Semiconductors"],
    min_market_cap_usd=1_000_000_000,
)
```

### Cross-scanner aggregate (specific profile subset)

```python
log_file = run_cross_scanner_aggregate(
    scan_data=scan_data,
    scoring_profiles=["quality_value_compounder", "forward_edge_active", "asymmetric_value"],
    min_market_cap_usd=1_000_000_000,
)
```

### Cross-scanner aggregate (single profile — backwards compatible)

```python
log_file = run_cross_scanner_aggregate(
    scan_data=scan_data,
    scoring_profiles="quality_value_compounder",
)
```

### Full combined suite (prediction + targets + cross-scanner)

```python
from data_analysis_scripts.trading_view_cross_scanner_aggregator import (
    run_full_combined_suite,
)

all_logs = run_full_combined_suite(
    scan_data=scan_data,
    industries=["Semiconductors"],
    min_market_cap_usd=1_000_000_000,
    include_blind_spot_sections=True,
)

# all_logs keys:
# - profile names (breakout_long, quality_value_compounder, ...)
# - "_consensus_aggregator"
# - "_targets"
# - "_cross_scanner"
```

---

## Output Location

Reports are written to:
```
logs/tradingview_analysis/cross_scanner/
```

---

## What Was Added vs What Remains

### Added in This Implementation

1. **`trading_view_cross_scanner_aggregator.py`** — new module with:
   - `CrossScannerResult` and `HorizonCrossResult` dataclasses
   - Signal agreement calculation (`_compute_signal_agreement`)
   - Conviction scoring (`_compute_conviction_score`)
   - 4 divergence detectors (directional-price, coverage asymmetry, horizon conflict, earnings proximity)
   - Full report generation (conviction rankings, divergence alerts, sector summary, high-conviction names)
   - CSV export with all cross-referenced fields
   - `run_cross_scanner_aggregate()` — standalone cross-scanner
   - `run_full_combined_suite()` — runs all three layers (prediction suite + targets + cross-scanner)

2. **This documentation file** (`cross_scanner_aggregator_guide.md`)

### Remaining / Future Work

1. ~~Per-profile cross-scanner runs~~ **Done** — aggregator now runs all profiles by default, weighted via `CONSENSUS_PROFILE_WEIGHTS`, with `fragility_short` sign-inverted.

2. **Sector-segmented peer groups** — both underlying modules treat the scan as one peer group; implementing per-sector peer comparisons would tighten valuation accuracy for the target module's multiple-anchored lens.

3. **Historical conviction tracking** — store cross-scanner conviction scores over time to measure forecast accuracy and calibrate conviction weights.

4. **Risk-adjusted conviction** — incorporate beta, volatility, and max drawdown into the conviction score to distinguish between high-conviction-high-risk and high-conviction-low-risk names.

5. **Position sizing integration** — connect conviction scores to the position sizing framework documented in `position_sizing_distribution_framework.md`.

6. **Analyst consensus overlay** — future data source to cross-check forward EPS projections against analyst estimates for the trajectory lens.

7. **Industry-scoped combined suite** — equivalent of `run_full_analysis_suite_by_industry` but including the cross-scanner layer per industry.

8. **Conviction score calibration backtest** — track which conviction levels correspond to which actual returns across historical runs to tune the weight constants.
