# TradingView Forward Target Estimation — Implementation Guide

## Overview

The **trading_view_targets_analysis** module generates forward price and valuation targets for equities using data from the TradingView screener API. It builds on the same data pipeline as the move-prediction analysis but shifts the objective from directional scoring to concrete price targets expressed as **bear / base / bull** ranges across three time horizons.

---

## Architecture

### Data Flow

```
TradingView API scan (same payload as prediction module)
        │
        ▼
   Shared pipeline:
   ├── _enrich_with_peer_metrics()
   ├── _build_derived_metrics()
   ├── _build_metric_profiles()
   └── _build_component_scores()  ← reused from prediction module
        │
        ▼
   Target-specific pipeline:
   ├── _build_peer_multiple_stats()     → peer valuation statistics
   ├── _multiple_anchored_targets()     → Lens 1
   ├── _technical_anchored_targets()    → Lens 2
   ├── _trajectory_targets()            → Lens 3
   └── _blend_horizon_target()          → bear / base / bull per horizon
        │
        ▼
   Report generation (.log + .csv)
```

### Relationship to the Prediction Module

The targets module **imports directly** from `trading_view_move_prediction_analysis`:

| Import | Purpose |
|--------|---------|
| `_build_derived_metrics` | Compute float turnover, gap severity, MACD spread, RSI centered, trend alignment, etc. |
| `_build_metric_profiles` | Peer-relative distribution stats for robust normalization |
| `_build_component_scores` | Full 8-component scoring (attention, event, momentum, trend, quality, valuation, safety, scale) |
| `_enrich_with_peer_metrics` | Add `_peer_market_cap_share` and `_peer_revenue_share` |
| `ScoringProfile`, `resolve_move_prediction_scoring_profile` | Control which prediction profile's weights and biases inform the component scores used in scenario-band tilting |

The prediction module's component scores are used to **tilt the scenario bands** (bear/bull width) rather than to directly set the targets.

---

## Three Estimation Lenses

### Lens 1: Multiple-Anchored Targets

**Goal:** Estimate the price that would bring a company's valuation multiple in line with its peer-group median.

**Multiples used:**
- P/E TTM
- P/S (Price/Sales)
- P/B (Price/Book)
- P/FCF (Price/Free Cash Flow)
- P/OCF (Price/Operating Cash Flow)
- EV/Revenue
- EV/EBIT
- EV/EBITDA

**Formula:**
```
implied_price = current_price × (peer_median_multiple / company_multiple)
```

**Confidence weight:** Scales with peer count, saturates at 20 peers (weight = min(1.0, peer_count / 20)).

**Notes:**
- Only positive multiples are used (loss-making companies produce no P/E target).
- EV-based multiples use the same ratio simplification; this is valid when the scan universe is uniform enough that capital-structure differences are second-order.

### Lens 2: Technical-Anchored Targets

**Goal:** Derive a price envelope from moving averages, Bollinger Bands, pivots, and historical price ranges.

**Anchor fields by horizon:**

| Horizon | Anchors |
|---------|---------|
| Near-term | SMA10, SMA20, EMA10, EMA20, BB.upper, BB.lower, Pivot.M.Classic.Middle, High.1M, Low.1M, VWAP |
| Medium-term | SMA30, SMA50, EMA30, EMA50, VWMA, High.3M, Low.3M, High.6M, Low.6M |
| Long-term | SMA200, EMA200, price_52_week_high, price_52_week_low |

**Derivation:** Collected anchor prices are sorted and the bear/base/bull levels are extracted as percentiles (20th/median/80th for near-term, 15th/median/85th for medium-term, 10th/median/90th for long-term).

### Lens 3: Fundamental-Trajectory Targets

**Goal:** Project the company's own fundamentals forward using its growth rates, then apply current or peer multiples to the projected figures.

**Growth rates used (in priority order):**
1. Forward EPS growth (derived from `earnings_per_share_forecast_next_fq` vs `earnings_per_share_fq`)
2. Revenue YoY growth TTM
3. EPS diluted YoY growth TTM
4. EBITDA YoY growth TTM
5. Net income YoY growth TTM

**Projection horizons:**
| Horizon | Years forward | Growth decay |
|---------|--------------|-------------|
| Near-term | 0.25 | 1.00 (no decay) |
| Medium-term | 1.00 | 0.90 |
| Long-term | 2.00 | 0.70 |

**Growth clamping:** All growth rates are clamped to **[-80%, +200%]** to avoid extreme projections.

**Three sub-models:**
- **EPS-based:** projected_eps × applied_PE → implied price
- **Revenue-based:** projected_revenue × applied_P/S × (current_price / market_cap) → implied price
- **EBITDA-based:** (projected_EBITDA × applied_EV/EBITDA − net_debt) × (current_price / market_cap) → implied price

---

## Target Blending

### Lens Weights by Horizon

| Horizon | Technical | Multiple | Trajectory |
|---------|-----------|----------|------------|
| Near-term (1–4 weeks) | 55% | 25% | 20% |
| Medium-term (1–6 months) | 25% | 40% | 35% |
| Long-term (6–24 months) | 10% | 35% | 55% |

**Rationale:** Near-term prices are dominated by technical levels; long-term prices are dominated by fundamental trajectory.

### Scenario Bands

The blended base price is transformed into bear and bull targets:

| Scenario | Base multiplier | Component adjustment |
|----------|----------------|---------------------|
| Bear | 0.85 | +/− 5% based on quality + safety scores |
| Bull | 1.18 | +/− 5/8% based on momentum + trend scores |

**Tilting logic:**
- Strong quality + safety → tighter bear band (less assumed downside risk)
- Strong momentum + trend → wider bull band (more assumed upside room)

### Opportunity Score

```
opportunity_score = base_upside_% × (0.6 + 0.4 × lens_coverage)
```

| Score range | Label |
|------------|-------|
| ≥ +25% | Strong Opportunity |
| ≥ +10% | Moderate Opportunity |
| −10% to +10% | Fair Value Range |
| ≤ −10% | Moderate Overvaluation |
| ≤ −25% | Strong Overvaluation |

---

## Report Output

### Log File Sections

1. **Methodology** — describes all three lenses, blending, and scoring
2. **Peer-group valuation multiple distribution** — P25/median/P75/mean for each multiple
3. **Target estimates per horizon** — top opportunities and most overvalued
4. **Valuation multiple comparison vs peers** — company vs peer median for key multiples
5. **Consensus targets** — companies where all horizons agree on upside or downside
6. **Target-quality overlay** — cross-references medium-term upside with component scores

### CSV Output

Each row contains:
- Identity fields (symbol, company, industry, sector, exchange, market)
- Current market cap and close price
- Bear/base/bull price and upside% for each horizon
- Opportunity score and label for each horizon
- Company and peer-median multiples for all 8 multiple types
- Component scores from the prediction module

---

## API Usage

### Basic usage (with pre-fetched scan data)

```python
from data_analysis_scripts.trading_view_targets_analysis import analyze_targets_scan

log_file = analyze_targets_scan(
    scan_data=scan_data,               # from api_client.scan_global_market_move_prediction()
    industries=["Semiconductors"],
    min_market_cap_usd=1_000_000_000,
    scoring_profile="balanced",         # any prediction-module profile
)
```

### Industry-level batch run

```python
from data_loaders.api_tradingview_client import ApiTradingViewClient
from data_analysis_scripts.trading_view_targets_analysis import run_targets_scan_by_industry

api_client = ApiTradingViewClient(user_agent="...")

logs = run_targets_scan_by_industry(
    api_client=api_client,
    industries=["Semiconductors", "Software - Application"],
    min_market_cap_usd=500_000_000,
    scoring_profile="quality_value_compounder",
)
```

### Convenience alias

```python
from data_analysis_scripts.trading_view_targets_analysis import run_targets_scan

log_file = run_targets_scan(scan_data=scan_data)
```

---

## Configuration Constants

| Constant | Default | Description |
|----------|---------|-------------|
| `MAX_GROWTH_RATE` | 2.00 (200%) | Upper clamp for growth rates |
| `MIN_GROWTH_RATE` | −0.80 (−80%) | Lower clamp for growth rates |
| `SCENARIO_MULTIPLIERS` | bear=0.85, bull=1.18 | Base scenario band width |
| `STRONG_OPPORTUNITY_THRESHOLD` | 25% | Threshold for "Strong Opportunity" label |
| `MODERATE_OPPORTUNITY_THRESHOLD` | 10% | Threshold for "Moderate Opportunity" label |
| `TOP_SECTION_ROWS` | 30 | How many rows to show in each report section |
| `DEFAULT_LENS_WEIGHTS` | (see table above) | Horizon-specific lens blending weights |

---

## Output Location

Reports are written to:
```
logs/tradingview_analysis/targets_analysis/
```

When using `run_targets_scan_by_industry`, each industry gets its own subdirectory:
```
logs/tradingview_analysis/targets_analysis/Semiconductors/
logs/tradingview_analysis/targets_analysis/Software - Application/
```

---

## Limitations and Future Extensions

1. **No historical backtest**: Targets are computed from current snapshot data, not point-in-time historical data.
2. **Ratio-based EV simplification**: EV-to-equity conversion uses a ratio approach rather than explicit share count and net-debt subtraction. This is accurate within a homogeneous peer group but may be less precise for highly levered companies.
3. **Single peer group**: Currently uses all companies in the scan as the peer group. Future versions may segment by industry or sector for tighter peer comparisons.
4. **Growth rate sources**: Only uses TradingView-supplied growth fields. Future versions may incorporate analyst consensus estimates or EDGAR-sourced fundamentals.
5. **Extensibility**: The three-lens architecture is designed to accommodate additional target types (e.g., dividend yield targets, cash-flow yield targets, sector-rotation targets) by adding new lens functions and including them in the blending step.
