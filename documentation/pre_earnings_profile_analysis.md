# Pre-Earnings Profile Analysis
## Scoring Profiles, Component Weights, and Best-Fit Selection for Earnings Catalysts

> Generated: May 2026  
> Source: `trading_view_move_prediction_analysis.py` — `PRESET_SCORING_PROFILES`, `DEFAULT_HORIZON_WEIGHTS`, `CONSENSUS_PROFILE_WEIGHTS`, `run_full_analysis_suite_with_earnings_priority`

---

## 1. System Architecture Overview

The scoring system operates on **8 components** computed for every stock, then combined by weighted average per horizon. Each component is built from **raw and derived signals**, all normalized via robust median/MAD z-scoring (clamped to ±3.0).

### 1.1 Components and What They Measure

| Component | Measures | Key signals |
|-----------|----------|-------------|
| **attention** | Unusual volume / market interest / coiled energy | `relative_volume_10d_calc`, `float_turnover`, `dollar_turnover_intensity`, `volume_trend`, `bb_squeeze` (inverted), `range_compression` (inverted), `volatility_contraction` (inverted) |
| **event** | Short-term catalyst pressure (gap, pre/post-market, earnings history) | `premarket_change`, `postmarket_change`, `gap`, `gap_severity`, `event_intensity`, `eps_surprise_percent_fq` |
| **momentum** | Directional price movement across timeframes | `change`, `Perf.5D/W/1M/3M/6M/YTD/Y`, `ROC`, `Mom`, `macd_spread`, `rsi_centered`, `aroon_spread`, `adx_directional_spread`, `stoch_rsi_crossover` |
| **trend** | Structural price position vs moving averages | `close_vs_SMA/EMA 10/20/30/50/200`, `close_vs_vwap/vwma`, `trend_alignment`, `bb_position`, `short_trend_emergence`, `pivot_distance` |
| **quality** | Profitability, growth rates, capital efficiency | `revenue/ebitda/net_income/FCF growth` (YoY & QoQ), `gross/operating/after-tax margin`, `ROIC`, `ROE`, `ROA`, `FCF_margin`, `eps_forward_growth`, `buyback_yield` |
| **valuation** | Relative cheapness vs peers | `P/E`, `PEG`, `P/S`, `P/B`, `P/FCF`, `EV/Revenue`, `EV/EBIT`, `EV/EBITDA`, `earnings_yield`, `distance_from_52w_high`, `range_position_52w` |
| **safety** | Balance sheet resilience, downside protection | `current_ratio`, `quick_ratio`, `cash_ratio`, `altman_z_score`, `debt_to_equity`, `debt_to_revenue`, `net_debt`, `short_term_cash_coverage`, `beta` |
| **scale** | Size relative to universe peers | `market_cap`, `enterprise_value`, `total_revenue`, `employees`, peer market-cap/revenue share |

### 1.2 Derived Signals (computed from raw fields)

| Derived signal | Formula / logic |
|---------------|-----------------|
| `float_turnover` | `volume / float_shares_outstanding` |
| `dollar_turnover_intensity` | `AvgValue.Traded_10d / market_cap` |
| `gap_severity` | `gap / ATRP` (gap size relative to ATR %) |
| `event_intensity` | `premarket_volume / average_volume_10d` |
| `macd_spread` | `MACD.macd − MACD.signal` |
| `rsi_centered` / `rsi7_centered` | `RSI − 50` / `RSI7 − 50` |
| `aroon_spread` | `Aroon.Up − Aroon.Down` |
| `adx_directional_spread` | `ADX+DI − ADX-DI` |
| `bb_position` | `(close − BB.lower) / (BB.upper − BB.lower)` |
| `bb_squeeze` | `(BB.upper − BB.lower) / close` (inverted: tighter = coiled) |
| `range_compression` | `(High.3M − Low.3M) / close` (inverted: tighter = coiled) |
| `volatility_contraction` | `Volatility.D / Volatility.M` (inverted: daily < monthly = contraction) |
| `short_trend_emergence` | Average of close vs SMA10/20/30 + EMA10/20/30 |
| `trend_alignment` | Average of close vs SMA50/200 + EMA50/200 + VWAP + VWMA |
| `eps_forward_growth` | `(EPS_forecast_next_fq − EPS_actual_fq) / abs(EPS_actual_fq)` |
| `stoch_rsi_centered` | `Stoch.RSI.K − 50` |
| `stoch_rsi_crossover` | `Stoch.RSI.K − Stoch.RSI.D` |
| `volume_trend` | `avg_volume_10d / avg_volume_30d` |
| `intraday_momentum` | `change_from_open` |
| `pivot_distance` | `(close − Pivot.M.Classic.Middle) / close` |
| `revenue_per_employee` | `total_revenue / number_of_employees` |
| `distance_from_52w_high` | `(close − price_52w_high) / abs(price_52w_high)` |
| `range_position_52w` | `(close − price_52w_low) / (price_52w_high − price_52w_low)` |

---

## 2. Default Horizon Component Weights

The **default weights** apply when a profile does not override them. Profiles override per-horizon component allocations to express their specific thesis.

| Component | **Days** | **Weeks** | **Months** | **Years** |
|-----------|----------|-----------|------------|-----------|
| attention | 0.16 | 0.12 | 0.06 | 0.02 |
| event | 0.12 | 0.06 | 0.03 | 0.02 |
| momentum | 0.20 | 0.20 | 0.14 | 0.04 |
| trend | 0.20 | 0.22 | 0.22 | 0.12 |
| quality | 0.12 | 0.14 | 0.24 | 0.32 |
| valuation | 0.06 | 0.08 | 0.14 | 0.24 |
| safety | 0.08 | 0.12 | 0.12 | 0.20 |
| scale | 0.06 | 0.06 | 0.05 | 0.04 |

**Pattern**: Shorter horizons are dominated by attention, event, and momentum. Longer horizons shift weight to quality, valuation, and safety. This is by design — the system assumes the market is efficient over long horizons (fundamentals dominate) but can be predicted in the short run via technical and activity signals.

---

## 3. All Scoring Profiles — Complete Reference

### 3.1 Consensus Weights (relative influence in consensus aggregator)

| Profile | Consensus weight | Primary thesis |
|---------|-----------------|----------------|
| `quality_value_compounder` | **0.18** | Durable quality at reasonable valuation |
| `sector_relative_outperformer` | **0.15** | Best-in-class operator, market recognition |
| `forward_edge_active` | **0.15** | Improving fundamentals before price reflects it |
| `asymmetric_value` | **0.12** | Overlooked quality, market disconnect |
| `breakout_long` | **0.10** | Tape-reading, trend continuation |
| `early_momentum_inflection` | **0.10** | Nascent moves, pre-breakout coiling |
| `value_recovery` | **0.10** | Turnaround candidates, QoQ improvement |
| `fragility_short` | **0.10** | Hedging / short thesis (inverted scoring) |

---

### 3.2 `breakout_long` — Trend Following

**What it does:** Pure tape-reading. Rewards strong volume, confirmed momentum across timeframes, and MA alignment. Fundamentals carry near-zero weight.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.30 | 0.20 | 0.10 | 0.03 |
| event | 0.18 | 0.10 | 0.06 | 0.03 |
| momentum | 0.30 | 0.28 | 0.22 | 0.12 |
| trend | 0.16 | 0.28 | 0.30 | 0.22 |
| quality | 0.00 | 0.02 | 0.10 | 0.18 |
| valuation | 0.00 | 0.00 | 0.04 | 0.12 |
| safety | 0.06 | 0.12 | 0.18 | 0.30 |

**Top signal weights:**
- `adx_directional_spread` = 1.50, `aroon_spread` = 1.45, `volume_trend` = 1.40, `Perf.5D` = 1.40, `relative_volume_10d_calc` = 1.50
- Long-duration momentum crushed: `Perf.Y` = 0.20, `Perf.YTD` = 0.45, `Perf.6M` = 0.40
- Short MAs prioritized: `close_vs_SMA10/EMA10` = 1.30; 200-day MA damped to 0.55

**Directional bias:** momentum upside ×1.40, downside ×0.65. Strongly asymmetric — amplifies bullish signals, suppresses bearish ones.

**Confidence multiplier:** 1.07 (highest in system alongside `fragility_short`)

---

### 3.3 `early_momentum_inflection` — Pre-Breakout Coiling

**What it does:** Finds names BEFORE confirmation. Core: `bb_squeeze`, `range_compression`, `volatility_contraction` (coiled-energy suite) + early directional indicators (`aroon_spread`, `stoch_rsi_crossover`). Established momentum is aggressively crushed.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.26 | 0.20 | 0.12 | 0.02 |
| event | 0.10 | 0.06 | 0.04 | 0.02 |
| momentum | 0.30 | 0.28 | 0.22 | 0.08 |
| trend | 0.26 | 0.32 | 0.28 | 0.18 |
| quality | 0.02 | 0.04 | 0.12 | 0.24 |
| valuation | 0.01 | 0.02 | 0.06 | 0.18 |
| safety | 0.05 | 0.08 | 0.16 | 0.28 |

**Top signal weights:**
- `aroon_spread` = 1.65 (highest in system), `adx_directional_spread` = 1.55, `bb_squeeze` = 1.55, `range_compression` = 1.50, `stoch_rsi_crossover` = 1.40, `short_trend_emergence` = 1.55
- `Perf.Y` = 0.10, `Perf.YTD` = 0.15, `Perf.6M` = 0.20 — prior run is invisible

**Directional bias:** momentum upside ×1.40 / downside ×0.65. Most aggressive momentum upside in system.

---

### 3.4 `quality_value_compounder` — Durable Quality

**What it does:** Multi-dimensional quality profiling. FCF margin, ROIC, operating margin, buyback yield, revenue per employee. Short-term momentum nearly zeroed. Quality/safety/valuation dominate across all horizons.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.04 | 0.03 | 0.02 | 0.01 |
| event | 0.01 | 0.01 | 0.01 | 0.00 |
| momentum | 0.04 | 0.05 | 0.04 | 0.01 |
| trend | 0.16 | 0.14 | 0.12 | 0.06 |
| quality | 0.35 | 0.34 | 0.36 | 0.42 |
| valuation | 0.16 | 0.18 | 0.20 | 0.24 |
| safety | 0.24 | 0.25 | 0.25 | 0.26 |

**Top signal weights (quality):** `ROIC` = 1.40, `operating_margin` = 1.30, `FCF_growth_yoy` = 1.25, `FCF_margin` = 1.25, `EPS_diluted_growth` = 1.15

**Directional bias:** attention INVERTED (positive ×0.70, negative ×1.50). Missing quality penalty is harshest: –1.30 at years horizon.

---

### 3.5 `sector_relative_outperformer` — Best-in-Class Operator

**What it does:** Rewards RELATIVE strength via long-duration momentum plus fundamental excellence. `revenue_per_employee` = 1.30 (highest in system). `volume_trend` = 1.50 (highest in system) detects sustained institutional accumulation.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.04 | 0.03 | 0.02 | 0.01 |
| event | 0.02 | 0.02 | 0.01 | 0.01 |
| momentum | 0.08 | 0.08 | 0.06 | 0.03 |
| trend | 0.20 | 0.18 | 0.14 | 0.07 |
| quality | 0.32 | 0.32 | 0.35 | 0.40 |
| valuation | 0.14 | 0.17 | 0.22 | 0.26 |
| safety | 0.20 | 0.20 | 0.20 | 0.22 |

**Key differences from `quality_value_compounder`:** Long-duration momentum retained (`Perf.Y` = 1.30, `Perf.6M` = 1.20) to confirm market recognition of quality. `eps_forward_growth` = 1.35.

---

### 3.6 `forward_edge_active` — Forward-Looking Improvement

**What it does:** Answers "what is getting better?" — not "what has been good?". QoQ dominates YoY. `eps_forward_growth` = 1.75 (highest single weight in entire system). `eps_surprise_percent_fq` = 1.60.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.05 | 0.04 | 0.03 | 0.02 |
| event | 0.08 | 0.06 | 0.03 | 0.01 |
| momentum | 0.14 | 0.12 | 0.08 | 0.03 |
| trend | 0.26 | 0.22 | 0.16 | 0.10 |
| quality | 0.22 | 0.26 | 0.30 | 0.32 |
| valuation | 0.10 | 0.14 | 0.18 | 0.22 |
| safety | 0.15 | 0.16 | 0.22 | 0.30 |

**Top signal weights:**
- `eps_forward_growth` = 1.75, `eps_surprise_percent_fq` = 1.60, `FCF_qoq_growth` = 1.45, `ebitda_qoq` = 1.40, `revenue_qoq` = 1.35, `FCF_margin` = 1.35, `short_trend_emergence` = 1.65

**Directional bias:** attention maximally inverted (positive ×0.50, negative ×1.50). Safety negative ×1.40. Anti-crowd, forward-focused.

---

### 3.7 `asymmetric_value` — Overlooked Fundamentals

**What it does:** Market-disconnect detector. Strong quality + cheap valuation where the market has NOT rewarded it. Value dominates the score; momentum is only a small recovery hint.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.04 | 0.03 | 0.03 | 0.01 |
| event | 0.01 | 0.02 | 0.01 | 0.00 |
| momentum | 0.03 | 0.04 | 0.02 | 0.01 |
| trend | 0.07 | 0.06 | 0.04 | 0.02 |
| quality | 0.23 | 0.25 | 0.28 | 0.31 |
| valuation | **0.38** | **0.37** | **0.39** | **0.40** |
| safety | 0.24 | 0.23 | 0.23 | 0.25 |

**Top valuation weights:** `earnings_yield` = 1.55, `enterprise_value_to_free_cash_flow_ttm` = 1.50, `book_value_discount` = 1.40, `price_target_upside_median` = 1.35, `peer_revenue_value_gap` = 1.35.  
**Momentum bias:** positive ×0.70, negative ×0.45 — weak tape is forgiven, but cheapness and durable fundamentals carry the thesis.

---

### 3.8 `value_recovery` — Turnaround Trajectory

**What it does:** Rewards active improvement (QoQ) + cheap valuation. `eps_forward_growth` = 1.40 confirms recovery thesis. `stoch_rsi_crossover` detects oversold-to-recovery technical turn. Momentum bias inverted for downside (×0.60) so recent weakness doesn't punish recovering names.

**Horizon component weights:**

| Component | Days | Weeks | Months | Years |
|-----------|------|-------|--------|-------|
| attention | 0.06 | 0.06 | 0.04 | 0.02 |
| event | 0.04 | 0.04 | 0.03 | 0.01 |
| momentum | 0.04 | 0.06 | 0.05 | 0.01 |
| trend | 0.12 | 0.14 | 0.14 | 0.07 |
| quality | 0.24 | 0.22 | 0.28 | 0.30 |
| valuation | **0.26** | **0.25** | **0.26** | **0.32** |
| safety | **0.24** | **0.23** | **0.20** | **0.27** |

---

### 3.9 `fragility_short` — Hedging / Short Identification

**What it does:** Inverted scoring. Negative event pressure, deteriorating momentum, breaking trend, and weak safety are all REWARDED. Safety at years horizon = 0.57 (highest single component weight in system).

**Key note:** In the earnings-priority context, a HIGH fragility score for an upcoming earnings release signals potential downside dislocation — the name is structurally fragile going into its catalyst.

---

## 4. Scoring Engine — How Scores Are Computed

1. **Raw field normalization**: Each field is z-scored vs the universe median with robust MAD scaling. Result clamped to ±3.0.
2. **Signal weighting**: Within each component, signals are combined by their profile-specific weights (defaulting to 1.0 for standard signals, 0.0 for extended signals if the profile doesn't activate them).
3. **Directional bias**: The component score is then multiplied by the profile's positive or negative bias multiplier depending on sign.
4. **Horizon aggregation**: Component scores are combined by the horizon's component weights.
5. **Missing data**: If a component has zero valid signals, a profile-specific missing penalty is applied. Absence of data is scored, not ignored.
6. **Confidence multiplier**: Applied to the final horizon score as a global scaling factor.

The score is **relative to the current scan universe**, not absolute. A score of +1.5 means "this name scores roughly 1.5 standard deviations above the universe median on this composite metric."

---

## 5. Pre-Earnings Analysis: What Makes a Profile Relevant?

### 5.1 What earnings events require from a profile

Pre-earnings positioning is fundamentally about **predicting a catalyst reaction**. The key axes are:

1. **Direction of the surprise**: Will the company beat or miss?
2. **Market's pre-positioning**: Is the stock already priced for a beat, making the actual beat a sell-the-news event?
3. **Structural strength going in**: Is the company healthy enough to absorb a miss without catastrophic price collapse?
4. **Pre-existing momentum**: Stocks with strong momentum going into earnings tend to have higher post-earnings upside if they beat (the move is amplified).
5. **Valuation cushion**: Cheap stocks have more protection on the downside if they miss.

The earnings-priority system (`run_full_analysis_suite_with_earnings_priority`) surfaces candidates ranked chronologically by next earnings date, then lets each profile score them on its own terms.

### 5.2 Pre-Earnings Profile Fitness — by scenario

#### Scenario A: High-conviction beat + continuation (large cap, established business)
**Best profile: `forward_edge_active`**

**Reasoning:**
- `eps_forward_growth` = 1.75 directly measures analyst EPS revision direction — the single best predictor of whether a company will beat. If analysts are raising estimates going into earnings, the probability of a beat is structurally higher.
- `eps_surprise_percent_fq` = 1.60 rewards consistent beaters. History of surprises compounds with current analyst sentiment.
- QoQ growth dominance (1.35-1.45) captures sequential acceleration that annual figures lag — the clearest signal of genuine operational improvement.
- Trailing performance is nearly invisible (`Perf.Y` = 0.05), so the profile is not corrupted by stocks that already moved on prior beats.
- Trend short-term signals (`short_trend_emergence` = 1.65, `close_vs_SMA10/EMA10` = 1.50) confirm price is in constructive position to react positively.

**Industry fit:** Technology, consumer discretionary, healthcare (high-growth, analyst-followed names where estimate revisions are informative). NOT suited for utilities, basic materials, or commodity-price-driven sectors where EPS is driven by external factors more than operational execution.

**Company lifecycle fit:** Growth companies, high-margin businesses where analyst models are binding. Transition-stage companies improving margins. NOT value traps or terminal decline.

---

#### Scenario B: Early-stage/small-mid cap ahead of a breakout quarter
**Best profile: `early_momentum_inflection`**

**Reasoning:**
- The coiled-energy suite (`bb_squeeze` = 1.55, `range_compression` = 1.50, `volatility_contraction` = 1.45) identifies stocks that are technically compressed and building tension ahead of a catalyst. Earnings is the most reliable catalyst to break compression.
- `aroon_spread` = 1.65 and `stoch_rsi_crossover` = 1.40 detect the first signs of directional emergence — particularly relevant if the compression is starting to break in the week before earnings.
- `adx_directional_spread` = 1.55 confirms directional strength is building.
- Established momentum is nearly zeroed, so mid-run names don't dominate. The profile specifically surfaces names where the setup is NEW, not ongoing.

**Industry fit:** Technology growth, biotech, consumer discretionary, specialty industrials — sectors where quarterly results can catalyze sharp directional moves from a compressed state. Also relevant for cyclicals at a turning point.

**Company lifecycle fit:** Early-growth companies, transition-stage, or companies approaching a fundamental inflection. Does NOT work well for mature dividend-payers or deeply defensive names where price compression is structural, not pre-catalyst.

**Warning:** This profile has near-zero quality and safety weight. It will surface fragile names alongside strong ones. Must be checked against `fragility_short` score to avoid holding a technically compressed but fundamentally broken stock into earnings.

---

#### Scenario C: Quality compounder — steady beat with no surprise narrative
**Best profile: `quality_value_compounder`**

**Reasoning:**
- Rewards names with persistently excellent fundamentals — these tend to be serial beaters by their nature, not because any single quarter is exceptional.
- ROIC (1.40), operating margin (1.30), FCF margin (1.25) are the three highest quality weights — these are durable business quality indicators that don't change quarter to quarter.
- `buyback_yield` = 0.90 confirms management confidence in their own business.
- High safety weight (24-26% across horizons) acts as a filter — names with hidden leverage that might blow up on a weak quarter are naturally penalized.

**Industry fit:** Consumer staples, healthcare (established pharma/medtech), financial services, industrial compounders, technology mega-caps. Sectors where consistent operational execution is the norm, not the exception.

**Company lifecycle fit:** Mature, profitable businesses with stable or growing margins and capital returns. Explicitly NOT early-stage or turnaround. This profile cannot find a name worth owning pre-earnings if it doesn't already have excellent fundamentals.

**Pre-earnings edge:** A high `quality_value_compounder` score going into earnings is a proxy for **execution credibility** — the company has demonstrated it can deliver, and the balance sheet can absorb even a slight miss.

---

#### Scenario D: Beat + re-rating (cheap quality going into earnings)
**Best profile: `sector_relative_outperformer`**

**Reasoning:**
- Combines quality with MARKET RECOGNITION (`Perf.Y` = 1.30, `Perf.6M` = 1.20). A name scoring well on this profile is already being accumulated by the market.
- `eps_forward_growth` = 1.35 confirms analyst optimism.
- `revenue_per_employee` = 1.30 (highest in system) identifies high-efficiency operators — these tend to generate margin upside surprises because they squeeze more output per cost unit.
- `volume_trend` = 1.50 (highest in system) detects sustained institutional accumulation. Before an earnings catalyst, institutional buying is the signal that smart money is positioning.

**Industry fit:** Technology, industrial, healthcare, consumer discretionary — any sector where operational execution creates durable competitive advantage. Particularly relevant for mid-cap names that the market is re-rating from "cheap quality" to "recognized compounder."

**Company lifecycle fit:** Late-growth to mature phase. Companies transitioning from cheap obscurity to widely recognized quality — the re-rating phase is where the biggest returns occur.

---

#### Scenario E: Turnaround candidate ahead of first inflection quarter
**Best profile: `value_recovery`**

**Reasoning:**
- QoQ growth dominance (FCF QoQ = 1.35, net income QoQ = 1.30, EBITDA QoQ = 1.20) specifically captures whether the sequential trajectory is improving, regardless of where the absolute level started.
- `eps_forward_growth` = 1.40 confirms analyst recovery thesis.
- `stoch_rsi_crossover` = 0.65 detects the technical oversold-to-recovery turn — pre-earnings, a name coming off a technical bottom with improving fundamentals and cheap valuation is a high-conviction setup.
- Momentum bias inverted: negative momentum doesn't penalize recovering names (positive ×0.85, negative ×0.60). This is critical for turnarounds because the stock is usually weak for a reason — the profile needs to see through recent price history.

**Industry fit:** Cyclicals (energy, materials, industrials) in a trough recovery. Consumer discretionary companies emerging from demand weakness. Retail turnarounds. Financial companies recovering from credit cycle stress.

**Company lifecycle fit:** Mature businesses going through a temporary downcycle, NOT terminal decline. The critical distinction: improving QoQ fundamentals distinguish genuine recovery from "dead cat bounce."

**Risk:** High valuation weight means the profile can surface genuinely cheap names — but cheapness requires the safety filter. `altman_z_score` = 1.25 and debt metrics are the hard gate. If safety is weak, avoid regardless of cheapness.

---

#### Scenario F: Hedging / short pre-earnings (structurally fragile going in)
**Best profile: `fragility_short`**

**Reasoning:**
- Safety dominates at longer horizons (57% weight at years). Names with dangerous leverage structures going into earnings face severe downside if they miss — the balance sheet can't absorb the sentiment shock.
- Event = 0.30 at days horizon with `gap_severity` = 1.35 and `eps_surprise_percent_fq` = 1.25 (miss history). This is the profile that finds names most likely to gap down on an earnings miss.
- `beta_1_year` = 1.15 (NOT inverted) — higher beta fragile names are more susceptible to dislocation.
- A HIGH fragility score on the `weeks` or `days` horizon for an earnings candidate is a strong signal to either avoid the name or use it as a short/put hedge.

**Industry fit:** Highly leveraged sectors (real estate, utilities at peak rates, leveraged buyouts), biotech (binary events), tech companies with deteriorating fundamentals still priced for growth, retail with balance sheet stress.

---

## 6. Profile-by-Profile Pre-Earnings Fitness Matrix

| Profile | Beat prediction | Gap potential | Safety net | Lifecycle fit | Best horizon pre-earnings |
|---------|----------------|--------------|------------|--------------|--------------------------|
| `forward_edge_active` | ★★★★★ | ★★★☆☆ | ★★★☆☆ | Growth / improving | Weeks → Days |
| `early_momentum_inflection` | ★★☆☆☆ | ★★★★★ | ★★☆☆☆ | Early growth / inflection | Days |
| `quality_value_compounder` | ★★★★☆ | ★★☆☆☆ | ★★★★★ | Mature compounder | Weeks → Months |
| `sector_relative_outperformer` | ★★★★☆ | ★★★☆☆ | ★★★★☆ | Mid/late growth, re-rating | Weeks |
| `value_recovery` | ★★★☆☆ | ★★★☆☆ | ★★★★☆ | Cyclical / turnaround | Weeks → Days |
| `asymmetric_value` | ★★☆☆☆ | ★★☆☆☆ | ★★★★☆ | Cheap / neglected | Months → Weeks |
| `breakout_long` | ★★★☆☆ | ★★★★★ | ★★☆☆☆ | Any with momentum | Days |
| `fragility_short` | N/A (inverted) | ★★★★★ (downside) | N/A | Leveraged / deteriorating | Days → Weeks |

---

## 7. Industry and Company Type Considerations

### 7.1 Technology (Software / SaaS / Semiconductors)
- Analyst estimate revisions are highly informative → `forward_edge_active` strongest predictor
- Growth names with expanding margins benefit from `early_momentum_inflection` before a beat
- Mature tech (Microsoft, Alphabet) benefits from `quality_value_compounder` / `sector_relative_outperformer`
- Semiconductor cyclicals at trough → `value_recovery`

### 7.2 Healthcare (Pharma / Biotech / MedTech)
- Binary event (drug approval / clinical readout): `early_momentum_inflection` for coiled-energy pre-event compression; `fragility_short` for names with FDA risk and leverage
- Diversified pharma with predictable beats: `quality_value_compounder`
- MedTech in commercial ramp: `forward_edge_active` (QoQ improvement trajectory)

### 7.3 Financials (Banks / Insurance / Asset Management)
- Rate-sensitive, mean-reverting → `value_recovery` and `asymmetric_value` work well at trough valuations
- Quality franchises: `quality_value_compounder` for NIM quality, ROE, book value
- Leveraged financials with credit deterioration: `fragility_short`

### 7.4 Consumer Discretionary / Retail
- Turnaround narratives: `value_recovery` (QoQ improvement in SSS, margin trajectory)
- Premium / brand strength: `sector_relative_outperformer`
- Early momentum recovery: `early_momentum_inflection` when inventory/margin cycle turns

### 7.5 Industrials / Materials
- Deep cyclical recovery: `value_recovery`
- Operational excellence leaders: `sector_relative_outperformer`
- Commodity-levered names mid-cycle: `breakout_long` / `early_momentum_inflection`
- Overleveraged at cycle peak: `fragility_short`

### 7.6 Consumer Staples / Utilities
- Stable, low-growth → `quality_value_compounder` is most relevant; low surprise frequency
- Defensive pre-earnings positioning values safety above all — high safety weight profiles (`quality_value_compounder`, `asymmetric_value`)
- `forward_edge_active` is poorly suited here: EPS revisions are small and mean little

---

## 8. Practical Pre-Earnings Decision Framework

### Step 1: Check `forward_edge_active` score (weeks horizon) first
- This is the highest-signal predictor of a likely beat. `eps_forward_growth` = 1.75 and `eps_surprise_percent_fq` = 1.60 — if this score is strongly positive, the name is analytically set up for a beat.

### Step 2: Check `fragility_short` score (days/weeks horizon) as a veto
- A high fragility score going into earnings is a structural red flag. Even with a strong `forward_edge_active` score, a name with a high fragility rating has balance sheet risk that can override any beat — leverage amplifies negative surprises.

### Step 3: Use `early_momentum_inflection` to gauge technical setup
- If the name shows positive coiling signals (`bb_squeeze`, `range_compression`, `volatility_contraction`) alongside the fundamental improvement, you have convergence of both fundamental and technical pre-catalyst setups. This is the highest-conviction configuration.

### Step 4: Match lifecycle to profile
- Growth company → `forward_edge_active` + `early_momentum_inflection`
- Mature compounder → `quality_value_compounder` + `sector_relative_outperformer`
- Turnaround / cyclical recovery → `value_recovery`
- Cheap, neglected quality → `asymmetric_value`

### Step 5: Validate against consensus score
- A high consensus score (aggregating all profiles at their consensus weights) means multiple lenses agree. Pre-earnings, consensus agreement across quality AND momentum profiles is the strongest composite signal.

---

## 9. Key System Constraints and Limitations

1. **All scores are universe-relative**: A strong score means the name stands out vs the current scan, not vs an absolute standard. In a weak market, a "strong" score may still be mediocre in absolute terms.

2. **`eps_forward_growth` depends on analyst coverage**: For small/micro caps with sparse coverage, the forward EPS signal is noisy or missing. The `forward_edge_active` profile penalizes missing data, so these names naturally rank lower.

3. **QoQ metrics can be gamed by seasonality**: Companies with strong seasonal Q4 will always show QoQ improvement from Q4 → Q1. Cross-check sector seasonality when using `forward_edge_active` or `value_recovery`.

4. **`bb_squeeze` / `range_compression` / `volatility_contraction` are directionally agnostic**: They signal energy release is coming, not which direction. Always pair with directional signals (`aroon_spread`, `adx_directional_spread`) for the inflection profile.

5. **`fragility_short` in the consensus** has 10% weight — it REDUCES the consensus score for names that score well on weakness. This is by design: a name that is simultaneously "forward-improving" and "fragile" gets penalized in consensus.

6. **The earnings priority system uses a ±1 day grace window**: Names whose earnings released yesterday are still included. Post-earnings drift signals are valid for the day after a release.

7. **`earnings_release_next_date` must come from the enriched scan**: `scan_global_market_move_prediction_with_earnings` is required. The standard scan does not include earnings date columns, and names without a parseable date are excluded from all earnings-priority reports.

---

## 10. Conclusion: Recommended Profile Priority for Pre-Earnings

**Primary: `forward_edge_active`** — the only profile that directly weights analyst EPS revision direction and surprise history as its core mechanism. Use at the **weeks horizon** 2-3 weeks before earnings. It answers the most direct pre-earnings question: "Is this company operationally set up to beat?" with the highest signal-to-noise ratio.

**Secondary: `early_momentum_inflection`** — use at the **days horizon** in the final week before earnings to confirm technical compression is resolving in the right direction. The coiled-energy signals are most informative 2-5 trading days before an event.

**Veto filter: `fragility_short`** — always check. A score above the directional threshold (±0.35) on weeks/days for a name appearing in the earnings-priority list is a meaningful structural warning. Consider reducing position size or using options to define risk.

**Industry/lifecycle override:**
- Cyclical or turnaround → substitute `value_recovery` for `forward_edge_active` (QoQ momentum is more relevant than analyst revisions for mean-reverting businesses)
- Mature compounder → substitute or supplement `quality_value_compounder` (serial beaters where quality track record predicts continuation)
- Re-rating opportunity (cheap quality already moving) → `sector_relative_outperformer` adds the institutional accumulation (`volume_trend`) and market-recognition (`Perf.Y/6M`) layer
