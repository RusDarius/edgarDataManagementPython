# TradingView Forward Target Estimation -- Implementation Guide

## Overview

The **trading_view_targets_analysis** module generates forward price and valuation targets for equities using data from the TradingView screener API. It builds on the same data pipeline as the move-prediction analysis but shifts the objective from directional scoring to concrete price targets expressed as **bear / base / bull** ranges across three time horizons.

This revision replaces the original three-lens model with a **seven-lens architecture** that mirrors how institutional pricing desks triangulate fair value: peer multiples, tape structure, forward fundamentals, mean reversion, sell-side consensus, book value, and dividend-discount / yield reversion. Each lens contributes an independent implied price; the blender normalises them to coverage, weights them per horizon, then tilts the scenario fan using component scores, fundamental quality flags, lens dispersion, and analyst spread.

---

## Architecture

### Data Flow

```
TradingView API scan (same payload as prediction module -- now extended with
analyst targets, book value, SGR, Piotroski, dividends, Camarilla pivots)
        |
        v
   Shared pipeline (from move_prediction module):
   _enrich_with_peer_metrics
   _build_derived_metrics
   _build_metric_profiles
   _build_component_scores  (8 components)
        |
        v
   Target-specific pipeline:
   _build_peer_multiple_stats      -> peer valuation stats (10 multiples)
   _cost_of_equity                 -> CAPM discount rate per name
   _build_quality_flags            -> Piotroski / Altman / Debt-EBITDA bias
   _multiple_anchored_targets      -> Lens 1
   _technical_anchored_targets     -> Lens 2
   _trajectory_targets             -> Lens 3  (SGR-capped + CAPM-discounted)
   _range_anchored_targets         -> Lens 4
   _analyst_consensus_targets      -> Lens 5
   _book_value_targets             -> Lens 6
   _yield_dcf_targets              -> Lens 7
   _blend_horizon_target           -> bear / base / bull per horizon
        |
        v
   Report generation (.log + .csv)
```

### Relationship to the Prediction Module

The targets module **imports directly** from `trading_view_move_prediction_analysis`:

| Import | Purpose |
|---|---|
| `_build_derived_metrics` | Compute float turnover, gap severity, MACD spread, RSI centered, trend alignment, etc. |
| `_build_metric_profiles` | Peer-relative distribution stats for robust normalization |
| `_build_component_scores` | Full 8-component scoring (attention, event, momentum, trend, quality, valuation, safety, scale) |
| `_enrich_with_peer_metrics` | Add `_peer_market_cap_share` and `_peer_revenue_share` |
| `ScoringProfile`, `resolve_move_prediction_scoring_profile` | Control which prediction profile's weights and biases inform the component scores used in scenario-band tilting |

The prediction module's component scores and the new quality flags jointly **tilt the scenario bands** (bear/bull width); they do not directly set the blended base price.

---

## Seven Estimation Lenses

### Lens 1 -- Multiple-Anchored Targets

Peer-median reversion across **ten** standard valuation multiples:

- P/E TTM, P/S, P/B, P/FCF, P/OCF
- EV/Revenue, EV/EBIT, EV/EBITDA, EV/FCF, EV/GP

Formula:
```
implied_price = current_price * (peer_median_multiple / company_multiple)
```
Confidence saturates at ~20 peers (`min(1.0, peer_count / 20)`). Negative or missing multiples are silently skipped.

### Lens 2 -- Technical Envelope

A percentile envelope built from short, medium, and long tape structure:

| Horizon | Anchors |
|---|---|
| Near-term | SMA/EMA 10+20, BB.upper/lower, VWAP, 1M high/low, classic pivot middle, **Camarilla S1/S2/R1/R2** |
| Medium-term | SMA/EMA 30+50, VWMA, 3M and 6M high/low |
| Long-term | SMA/EMA 200, 52-week high/low |

Anchors are filtered per horizon and collapsed to bear/base/bull percentiles (tighter on near-term, wider on long-term).

### Lens 3 -- Fundamental Trajectory

Projects EPS, revenue and EBITDA forward using the company's own growth rates (priority: forward EPS implied by `earnings_per_share_forecast_next_fq`, then revenue YoY, then EPS/EBITDA/NI YoY), applies current or peer-median multiples, and derives an implied price.

Two enhancements over the legacy model:

1. **Sustainable-growth cap.** Each projection's growth rate is clamped to `SUSTAINABLE_GROWTH_CAP_MULTIPLIER * sustainable_growth_rate_ttm` (1.5x by default). SGR is TradingView's built-in ROE * retention proxy. Names can't grow meaningfully faster than their capital base supports, so this cap keeps long-horizon projections honest.
2. **CAPM discounting on long horizon.** Projected prices with horizon years >= 1.5 are discounted back by `(1 + k)^(years - 1)` where `k` is the per-name CAPM cost of equity. This prevents mechanically-rich multiples * high-growth * long-years cases from producing unrealistic targets.

Growth is still hard-clamped to `[-80%, +200%]` to shield against data glitches.

### Lens 4 -- Range / Mean Reversion

Uses 6-month + 52-week ranges. Computes where the close sits in each range (`extremity` = distance from midpoint / half-range), pulls the price back to the midpoint by `extremity * horizon_pull` (0.30 near, 0.45 medium, 0.55 long). Strong near extremes, weak near midpoint. Caps overextension on momentum names and supports oversold names softly.

### Lens 5 -- Analyst Consensus  *(new)*

Uses TradingView's bundled sell-side targets directly:

- `price_target_low`  -> bear anchor
- `price_target_median` or `price_target_average` -> base anchor
- `price_target_high` -> bull anchor
- `price_target_1y` -> long-horizon anchor

Confidence is scaled by `AnalystRating` (Strong Buy ~1.00 down to Sell ~0.55). Silent when no targets are published. Analyst-spread (`high - low / base`) is reused by the blender to widen the scenario fan when the street itself disagrees.

### Lens 6 -- Book Value / Graham  *(new)*

Two anchors, both silent for negative-equity firms:

1. **Graham number** -- `sqrt(22.5 * EPS * BVPS)`. Classic value floor for asset-heavy, earnings-positive businesses.
2. **Peer-median P/B reversion applied to BVPS** -- `bvps * peer_median_PB`. Book-anchored analog of Lens 1.

### Lens 7 -- Yield / Dividend Discount  *(new)*

Only fires for income payers. Two anchors:

1. **Gordon DDM** -- `P = D1 / (k - g)` with `D1 = annual_DPS * (1 + g)`, `k` from CAPM, and `g` clamped so it always sits below `k - 1%`.
2. **Shareholder-yield reversion** -- when `dividend_yield_recent` is available, reverts a combined-yield proxy to `k` (`yield_$ / k`). This captures buyback-heavy payers whose dividend alone understates the full return.

---

## Cost of Equity (CAPM)

Shared by Lens 3 and Lens 7:

```
k = risk_free + beta * equity_risk_premium
```

- `risk_free = 4.5%`, `ERP = 5.5%` (defaults)
- Beta pulled from `beta_1_year`, falling back to `beta_3_year`, then `beta_5_year`; clamped to `[-0.5, 3.0]`
- `k` clamped to `[6%, 20%]` so extreme betas can't produce pathological discount rates

---

## Fundamental Quality Flags

Driven by three TradingView fields:

| Flag | Threshold | Bias delta |
|---|---|---|
| `piotroski_f_score_ttm >= 7` | strong | +1 |
| `piotroski_f_score_ttm <= 3` | weak | -1 |
| `altman_z_score >= 3.0` | safe | +1 |
| `altman_z_score <= 1.8` | distress | -1 |
| `total_debt_to_ebitda_fq > 5.0` | over-levered | -1 |

The aggregate `quality_bias` sums these and feeds into the blender's scenario-band tilt (see below).

---

## Blending and Scenario Bands

### Per-horizon lens weights (each row sums to 1.0)

| Horizon | Tech | Mult | Traj | Range | Analyst | BookVal | Yield/DDM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Near-term   | 42% | 16% | 6%  | 14% | 16% | 3% | 3% |
| Medium-term | 16% | 28% | 20% | 8%  | 18% | 5% | 5% |
| Long-term   | 4%  | 20% | 40% | 6%  | 18% | 6% | 6% |

*Near-term is tape-heavy with moderate analyst anchoring; medium-term is valuation-led; long-term is trajectory + analyst dominant with meaningful book-value and yield contribution.*

Each configured weight is multiplied by a per-lens **global confidence** (`LENS_GLOBAL_CONFIDENCE`) before normalisation, so lenses with lower empirical reliability (yield/DDM, book-value) carry less weight even inside their configured slots.

### Blend pipeline (condensed)

1. Within each lens, per-anchor estimates are collapsed into a single confidence-weighted price.
2. Horizon-tagged lenses (technical, trajectory, range, analyst-1y) are filtered so only horizon-appropriate anchors contribute.
3. Lens weights * global confidence are normalised across lenses that actually fired, so missing lenses don't bias the result.
4. The base price is the weighted mean of the per-lens prices.
5. Lens dispersion = `(max_lens - min_lens) / base` across contributing lenses.

### Scenario-band tilt

Starting from `bear=0.85, bull=1.18`, the blender adjusts:

| Source | Effect |
|---|---|
| Component scores | Quality+Safety tightens bear; Momentum+Trend widens bull |
| Quality bias | +1 unit -> ~3pp tighter bear, ~1.5pp tighter bull; -1 unit -> reverse |
| Lens dispersion | `dispersion * 0.15` added to both ways (cap +/-10pp) |
| Analyst spread | `(high - low) / base * 0.10` added to both ways (cap +/-8pp) |

### Opportunity score

```
opportunity_score = base_upside_% * (0.6 + 0.4 * lens_coverage)
```

| Score | Label |
|---|---|
| >= +25% | Strong Opportunity |
| >= +10% | Moderate Opportunity |
| -10% .. +10% | Fair Value Range |
| <= -10% | Moderate Overvaluation |
| <= -25% | Strong Overvaluation |

---

## Report Output

### Log File Sections

1. **Methodology** -- all seven lenses, weight matrix, CAPM/SGR/quality thresholds, scenario-band rules
2. **Peer-group valuation multiple distribution** -- P25/median/P75/mean for each of the ten multiples
3. **Per-horizon target estimates** -- top opportunities and most overvalued
4. **Lens decomposition per horizon** -- per-lens implied price for each top name (direct transparency on how much each lens drove the blend)
5. **Valuation multiple comparison vs peers**
6. **Consensus targets** (all horizons agree)
7. **Analyst-vs-Model divergence** -- long-term blended base vs analyst median, flagged when delta >= 30pp
8. **Fundamental quality flags** -- strongest (positive bias) and weakest (negative bias) names
9. **Target-quality overlay** -- medium-term upside cross-referenced with component scores

### CSV Output

Per row:

- Identity fields (symbol, company, industry, sector, exchange, market), close, market cap
- **`cost_of_equity`** (per-name CAPM rate used for discounting)
- Per-horizon: bear/base/bull price, upside %, opportunity score+label, coverage, **`lens_dispersion`**, and a **per-lens implied price** column for each of the seven lenses
- Company vs peer median for all ten multiples
- All eight component scores
- Analyst snapshot: low/median/average/high/1y/rating/base-upside-pct
- Quality flags: piotroski, altman_z, debt_to_ebitda, quality_bias, flag-list

---

## Cross-Scanner Integration

`trading_view_cross_scanner_aggregator` consumes the targets module unchanged, plus:

- New `HorizonCrossResult` fields: `analyst_base_upside_pct`, `model_vs_analyst_divergence_pct`, `has_analyst_divergence` (threshold `ANALYST_DIVERGENCE_THRESHOLD_PCT = 30.0`)
- New alert section: **Model-vs-street divergence** flags names where the long-term blended base differs from analyst consensus by at least 30pp (manual review recommended)
- `lens_dispersion` continues to feed the signal-quality score

---

## API Usage

### Basic usage (with pre-fetched scan data)

```python
from data_analysis_scripts.trading_view_targets_analysis import analyze_targets_scan

log_file = analyze_targets_scan(
    scan_data=scan_data,               # from api_client.scan_global_market_move_prediction()
    industries=["Semiconductors"],
    min_market_cap_usd=1_000_000_000,
    scoring_profile="balanced",
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

---

## Configuration Constants

| Constant | Default | Description |
|---|---|---|
| `MAX_GROWTH_RATE` / `MIN_GROWTH_RATE` | +2.00 / -0.80 | Hard growth clamp |
| `SUSTAINABLE_GROWTH_CAP_MULTIPLIER` | 1.50 | Soft SGR cap on trajectory growth |
| `DEFAULT_RISK_FREE_RATE` | 0.045 | CAPM risk-free |
| `DEFAULT_EQUITY_RISK_PREMIUM` | 0.055 | CAPM ERP |
| `MIN_DISCOUNT_RATE` / `MAX_DISCOUNT_RATE` | 0.06 / 0.20 | CAPM k clamp |
| `PIOTROSKI_STRONG` / `PIOTROSKI_WEAK` | 7 / 3 | Quality-flag thresholds |
| `ALTMAN_SAFE` / `ALTMAN_DISTRESS` | 3.0 / 1.8 | Safety-flag thresholds |
| `DEBT_TO_EBITDA_HIGH` | 5.0 | Over-leverage threshold |
| `SCENARIO_MULTIPLIERS` | bear=0.85 / bull=1.18 | Base scenario band width |
| `STRONG_OPPORTUNITY_THRESHOLD` / `MODERATE_OPPORTUNITY_THRESHOLD` | 25 / 10 | Label thresholds |
| `DEFAULT_LENS_WEIGHTS` | (table above) | Per-horizon lens blend |
| `LENS_GLOBAL_CONFIDENCE` | tech 1.0, mult 1.0, traj 0.95, range 0.85, analyst 1.0, book 0.80, yield 0.75 | Global down-weighting per lens |
| `ANALYST_DIVERGENCE_THRESHOLD_PCT` (cross-scanner) | 30.0 | Model-vs-street alert threshold |

---

## Output Location

```
logs/tradingview_analysis/targets_analysis/
```

When using `run_targets_scan_by_industry`, each industry gets its own subdirectory.

---

## Limitations and Future Extensions

1. **No historical backtest** -- targets use a current snapshot, not point-in-time data.
2. **Single peer group** -- uses the full scan universe as the peer group; industry/size segmentation is a future extension.
3. **Analyst data coverage** -- sell-side targets are only published for a fraction of the universe; the analyst lens is silent otherwise (coverage automatically normalises).
4. **Single-stage DDM** -- Gordon's single-stage model is deliberately simple; two-stage / H-model variants are a candidate extension for high-growth dividend names.
5. **CAPM simplification** -- beta is taken from TradingView; a levered/unlevered beta adjustment by sector is a potential refinement.
6. **Extensibility** -- new lenses plug in by adding a function, extending `LENS_ORDER`, `LENS_LABELS`, `LENS_GLOBAL_CONFIDENCE`, `DEFAULT_LENS_WEIGHTS`, and threading the estimates list through `_blend_horizon_target`.
