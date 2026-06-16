# TradingView Move Prediction Profile Weighting Reference

## Why This Reference Was Added

The preset profiles were producing too much leaderboard overlap in the March 29, 2026 run located under `logs/tradingview_analysis/prediction_analysis/AllInUniverse_min1bil/29_03_2026`.

Observed overlap from that output set:

- `balanced` vs `quality_value_compounder` top 10 overlap was `10/10` on the `days` horizon.
- `balanced` vs `breakout_long` top 10 overlap was `8/10` on `days`, `7/10` on `weeks`, `8/10` on `months`, and `9/10` on `years`.
- `quality_value_compounder` vs `value_recovery` top 10 overlap was `10/10` on `days`, `9/10` on `months`, and `8/10` on `years`.

That level of agreement is too high for profiles that are supposed to represent meaningfully different opportunity lenses.

The root causes were:

1. `quality_value_compounder` only overrode `months` and `years`, so its `days` and `weeks` behavior was still very close to the default balanced model.
2. Horizon scores were previously renormalized only across present components, so missing `quality`, `valuation`, or `safety` data could let a name rank highly in a fundamental profile anyway.
3. `breakout_long` was more aggressive than balanced, but not aggressive enough to consistently separate tactical names from broad multi-factor leaders.

This reference documents the revised scoring model and the updated profile behavior.

## June 2026 Profile Consolidation — `active_manager_v3` (11 lenses)

Consolidated suite replacing the 17-profile `active_manager_v2` sprawl. Goal: distinct investing-style buckets with less redundant overlap.

| Style lane | Profile | Notes |
| --- | --- | --- |
| Continuation | `breakout_long_v1` | Pure tape |
| Swing | `early_momentum_inflection_v1` | Coiled pre-breakout |
| Forward edge | `forward_edge_active_v2` | Absorbs `pre_earnings_drift_v1` catalyst signals |
| Quality continuation | `quality_continuation_v1` | **New** — tape + ROIC/Piotroski floor |
| Core quality | `quality_value_compounder` | Python preset |
| Quality at value | `durable_value_compounder_v1` | |
| Undervalue | `asymmetric_value` | Static cheapness |
| Structural recovery | `value_recovery_v2` | Absorbs `deep_value_momentum` catalysts |
| Fortress + action | `defensive_fortress_v2` | Absorbs `income_compounder_v1` dividend layer |
| Hedge overlay | `fragility_short` | Inverted |
| Trim overlay | `mean_reversion_exhaustion_v1` | Inverted |

**Consensus weights (v3, sum = 1.00):** breakout 0.17, inflection 0.11, forward_edge_v2 0.11, quality_continuation 0.09, QVC 0.10, DVC 0.09, asymmetric 0.09, value_recovery_v2 0.08, fortress_v2 0.07, fragility 0.06, exhaustion 0.03.

**Suites:** `active_manager_v3.json` (production), `baseline_v2.json` (same profiles for default-suite migration). Frozen `baseline.json` (10 profiles) unchanged for historical DuckDB comparison.

**Overlap tooling:** `scripts/run_move_prediction_profile_overlap_report.py` — critical pairs gated at Jaccard ≤ 0.35 on weeks/months for breakout vs quality_continuation, asymmetric vs value_recovery_v2, forward_edge_v2 vs QVC.

## June 2026 Active-Manager Update — Tier-1 Signals & Six New Profiles

This pass activates under-used TradingView catalog fields and expands the profile suite from 10 to **16** lenses (`active_manager_v1` / `active_manager_v2`; superseded by v3 consolidation above).

### Tier-1 derived signals (new)

| Signal | Source fields | Transform | Component | Notes |
| --- | --- | --- | --- | --- |
| `donchian_position` | `DonchCh20.Upper`, `DonchCh20.Lower`, `close` | `(close - Lower) / (Upper - Lower)` | trend | Channel position; upper = extended |
| `close_vs_psar` | `P.SAR`, `close` | `+1/-1` vs SAR | trend | Parabolic SAR trend confirm |
| `close_vs_hullma9` | `HullMA9`, `close` | `+1/-1` vs Hull MA | trend | Fastest MA confirmation |
| `chaikin_money_flow_signal` | `ChaikinMoneyFlow` | robust normalized | attention | Institutional accumulation |
| `bbpower_divergence` | `BBPower` | robust normalized | momentum | Bull/Bear power; early reversal / exhaustion |
| `recommend_tf_spread` | `Recommend.All\|1W`, `Recommend.All` | weekly minus daily | momentum | Timeframe sentiment disagreement |
| `ebitda_per_employee` | `ebitda`, `number_of_employees` | ratio | quality | Operating efficiency beyond revenue/head |
| `near_52w_high_score` | `distance_from_52w_high` | negated distance | momentum | Exhaustion overlay only |
| `exhaustion_range_position` | `range_position_52w` | raw 0–1 position | momentum | Exhaustion overlay only |

Payload additions in `GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`: `DonchCh20.Upper`, `DonchCh20.Lower`, `P.SAR`, `HullMA9`, `ChaikinMoneyFlow`, `BBPower`, `Recommend.All|1W`.

### Existing profile tuning

| Profile | Key changes |
| --- | --- |
| `breakout_long` | Activated `donchian_position`, `close_vs_psar`, `chaikin_money_flow_signal`; damped RSI/Stoch/Perf.1M; added `price_target_dispersion` safety guard |
| `early_momentum_inflection` | Activated `close_vs_hullma9`, `bbpower_divergence`; emits `promote_to_breakout` when handoff conditions met |
| `forward_edge_active` | Activated `close_vs_hullma9`, `recommend_tf_spread`; boosted `eps_surprise_percent_fq` |
| `durable_value_compounder` | Activated `ebitda_per_employee`; strengthened missing valuation penalties |
| `sector_relative_outperformer` | Activated `chaikin_money_flow_signal`, `ebitda_per_employee` |
| `value_recovery` | Activated `bbpower_divergence` |
| `fragility_short` | `hedge_or_short` requires ≥2 bullish long-profile disagreements |

### Six new profiles

| Profile | Horizon emphasis | Consensus weight | Inverted in consensus? |
| --- | --- | ---: | --- |
| `income_compounder` | months → years | 0.05 | no |
| `pre_earnings_drift` | days → weeks | 0.04 | no |
| `mean_reversion_exhaustion` | days → weeks | 0.02 | **yes** |
| `defensive_fortress` | months → years | 0.04 | no |
| `sector_rotation_momentum` | weeks → months | 0.04 | no |
| `quality_growth_at_reasonable_price` | months | 0.05 | no |

### Consensus weights (June 2026, sum = 1.00)

| Profile | Weight |
| --- | ---: |
| `breakout_long` | 0.15 |
| `early_momentum_inflection` | 0.09 |
| `forward_edge_active` | 0.09 |
| `quality_value_compounder` | 0.08 |
| `durable_value_compounder` | 0.07 |
| `sector_relative_outperformer` | 0.07 |
| `asymmetric_value` | 0.07 |
| `value_recovery` | 0.06 |
| `deep_value_momentum` | 0.03 |
| `fragility_short` | 0.05 |
| `income_compounder` | 0.05 |
| `pre_earnings_drift` | 0.04 |
| `mean_reversion_exhaustion` | 0.02 |
| `defensive_fortress` | 0.04 |
| `sector_rotation_momentum` | 0.04 |
| `quality_growth_at_reasonable_price` | 0.05 |

### New manager action signals

| Signal | Profile context |
| --- | --- |
| `promote_to_breakout` | `early_momentum_inflection` handoff to tactical breakout sleeve |
| `trim_extended_long` | `mean_reversion_exhaustion` overlay |

See [active_manager_implementation_usage.md](active_manager_implementation_usage.md) and [tradingview_move_prediction_pattern_discovery_reference.md](tradingview_move_prediction_pattern_discovery_reference.md) for workflow and discovery tooling.

## May 2026 Active-Manager Update

The latest model pass shifted the suite from a pure score/rank engine toward an active-manager decision tool. The goal is to better separate operating-company value from passive-vehicle cheapness, make value signals horizon-aware, and present the risk behind every recommendation.

Key changes:

- Added `deep_value_momentum`, a value-plus-catalyst profile that requires both deep valuation support and early technical reversal evidence.
- Added `durable_value_compounder`, a quality-led value investing profile for months/years positioning where durable cash generation must be available at disciplined valuation anchors.
- Re-tuned `asymmetric_value` so it still finds overlooked value, but now requires stronger operating-company evidence: forward EPS growth, Piotroski F-score, sustainable growth, EV/FCF, target upside/downside, book value discount, and harsher missing-quality penalties.
- Re-tuned `value_recovery` to make `stoch_rsi_crossover` and `short_trend_emergence` true entry triggers rather than background signals.
- Added risk-adjusted scoring, risk tiers, and `manager_action_signal` labels such as `add_long_breakout`, `accumulate_value_catalyst`, `watch_value_reversal`, `hedge_or_short`, and `avoid_value_trap`.
- Rebalanced consensus toward empirically stronger tactical profiles while adding the durable value lens: `breakout_long=0.20`, `early_momentum_inflection=0.12`, `forward_edge_active=0.11`, and `durable_value_compounder=0.09`.
- The move-prediction TradingView payload now requests `type` and `typespecs` and removes the fund/investment-trust branch from the move-prediction universe so fund-like structures do not dominate operating-company screens.
- Added `run_full_analysis_suite_from_raw_csv_folders` for replaying the current model over saved `tradingview_global_all_tdfields_*.csv` daily exports.

## Score Flow

The move-prediction model works in six layers.

### 1. Raw values and derived values

Each row begins with TradingView scan fields plus internally derived metrics.

### 2. Robust normalization

For most numeric fields, the model computes a scan-relative robust signal:

```text
robust_signal = clamp((value - median) / (MAD * 1.4826), -3.0, 3.0)
```

If `MAD` is `0` or missing, the fallback is a simpler sign comparison versus the median:

- above median -> `+1.0`
- below median -> `-1.0`
- equal median -> `0.0`

### 3. Component score construction

Signals are grouped into eight components:

- `attention`
- `event`
- `momentum`
- `trend`
- `quality`
- `valuation`
- `safety`
- `scale`

Each component starts as a weighted average of its included signals.

```text
component_score = sum(signal_i * signal_weight_i) / sum(signal_weight_i)
```

Unless a profile overrides a signal weight, the default signal weight is `1.00`. Extended signals (added March 30, 2026) default to `0.00` so they only activate when a profile explicitly sets a nonzero weight for them.

### 4. Directional bias

After the component average is computed, the profile can bias positive and negative readings differently.

```text
if component_score > 0:
    biased_component = clamp(component_score * positive_multiplier, -3.0, 3.0)
elif component_score < 0:
    biased_component = clamp(component_score * negative_multiplier, -3.0, 3.0)
else:
    biased_component = 0.0
```

This is how one profile can reward upside momentum while another punishes downside safety failure more aggressively.

### 5. Horizon score construction

Each horizon score is a weighted average of the eight components.

```text
horizon_score = sum(component_value_j * component_weight_j) / sum(used_component_weights)
```

Where `used_component_weights` includes:

- actual component scores when available
- profile-specific missing-component fallback scores when a component is missing and the profile defines a fallback

If a component is missing and no fallback is defined, that component is skipped.

### 6. Coverage and confidence

Coverage is:

```text
coverage = used_component_weight / total_possible_component_weight
```

Confidence is:

```text
confidence = clamp(
    (28 + 18 * abs(horizon_score) + 40 * coverage - event_penalty)
    * confidence_multiplier
    + confidence_offset,
    5,
    99,
)
```

Event penalty depends on how close the next earnings date is.

## Component And Field Mapping

The tables below document how each scored field contributes.

### Attention Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `relative_volume_10d_calc` | raw | robust normalized | 1.00 | more attention |
| `float_turnover` | derived as `volume / float_shares_outstanding` | robust normalized | 1.00 | more attention |
| `dollar_turnover_intensity` | derived as `AvgValue.Traded_10d / market_cap_basic` | robust normalized | 1.00 | more attention |
| `Value.Traded` | raw | robust normalized | 1.00 | more attention |
| `AvgValue.Traded_10d` | raw | robust normalized | 1.00 | more attention |
| `bb_squeeze` | derived as `(BB.upper - BB.lower) / close` | robust normalized, then inverted | 0.00 (extended) | tighter bands = more coiled energy |
| `range_compression` | derived as `(High.3M - Low.3M) / close` | robust normalized, then inverted | 0.00 (extended) | tighter 3-month range = more coiled energy |
| `volatility_contraction` | derived as `Volatility.D / Volatility.M` | robust normalized, then inverted | 0.00 (extended) | daily vol lower than monthly = contraction |
| `volume_trend` | derived as `average_volume_10d_calc / average_volume_30d_calc` | robust normalized | 0.00 (extended) | rising short-term vs medium-term volume = accumulation |
| `intraday_momentum` | raw `change_from_open` | robust normalized | 0.00 (extended) | stronger open-to-current momentum |

### Event Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `premarket_change` | raw | robust normalized | 1.00 | more upside event pressure |
| `postmarket_change` | raw | robust normalized | 1.00 | more upside event pressure |
| `gap` | raw | robust normalized | 1.00 | more upside gap pressure |
| `gap_severity` | derived as `gap / ATRP` | robust normalized | 1.00 | more abnormal gap pressure |
| `event_intensity` | derived as `premarket_volume / average_volume_10d_calc` | robust normalized | 1.00 | stronger event participation |

### Momentum Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `change` | raw | robust normalized | 1.00 | stronger near-term momentum |
| `Perf.5D` | raw | robust normalized | 1.00 | stronger 5-day momentum |
| `Perf.W` | raw | robust normalized | 1.00 | stronger 1-week momentum |
| `Perf.1M` | raw | robust normalized | 1.00 | stronger 1-month momentum |
| `Perf.3M` | raw | robust normalized | 1.00 | stronger 3-month momentum |
| `Perf.YTD` | raw | robust normalized | 1.00 | stronger YTD momentum |
| `Perf.Y` | raw | robust normalized | 1.00 | stronger 1-year momentum |
| `ROC` | raw | robust normalized | 1.00 | stronger rate-of-change momentum |
| `Mom` | raw | robust normalized | 1.00 | stronger momentum |
| `macd_spread` | derived as `MACD.macd - MACD.signal` | robust normalized | 1.00 | stronger MACD confirmation |
| `Recommend.All` | raw | robust normalized | 1.00 | stronger broad model sentiment |
| `Recommend.MA` | raw | robust normalized | 1.00 | stronger moving-average sentiment |
| `Recommend.Other` | raw | robust normalized | 1.00 | stronger non-MA sentiment |
| `rsi_centered` | derived as `RSI - 50` | robust normalized | 1.00 | stronger RSI leadership |
| `rsi7_centered` | derived as `RSI7 - 50` | robust normalized | 1.00 | stronger fast RSI leadership |
| `Perf.6M` | raw | robust normalized | 0.00 (extended) | stronger 6-month momentum |
| `aroon_spread` | derived as `Aroon.Up - Aroon.Down` | robust normalized | 0.00 (extended) | new uptrend forming |
| `adx_directional_spread` | derived as `ADX+DI - ADX-DI` | robust normalized | 0.00 (extended) | bullish directional strength emerging |
| `stoch_rsi_centered` | derived as `Stoch.RSI.K - 50` | robust normalized | 0.00 (extended) | stronger short-term momentum impulse |
| `CCI20` | raw | robust normalized | 0.00 (extended) | stronger directional thrust |
| `stoch_rsi_crossover` | derived as `Stoch.RSI.K - Stoch.RSI.D` | robust normalized | 0.00 (extended) | bullish Stoch RSI crossover signal |

### Trend Component

Trend uses direct binary alignment fields, not robust normalization.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `close_vs_sma50` | derived | `+1` if `close >= SMA50`, else `-1` | 1.00 | stronger trend |
| `close_vs_sma200` | derived | `+1` if `close >= SMA200`, else `-1` | 1.00 | stronger long trend |
| `close_vs_ema50` | derived | `+1` if `close >= EMA50`, else `-1` | 1.00 | stronger trend |
| `close_vs_ema200` | derived | `+1` if `close >= EMA200`, else `-1` | 1.00 | stronger long trend |
| `close_vs_vwap` | derived | `+1` if `close >= VWAP`, else `-1` | 1.00 | stronger tactical trend |
| `close_vs_vwma` | derived | `+1` if `close >= VWMA`, else `-1` | 1.00 | stronger tactical volume-weighted trend |
| `trend_alignment` | derived average of all trend comparisons | average of available `+1/-1` values | 1.00 | stronger broad alignment |
| `bb_position` | derived as `(close - BB.lower) / (BB.upper - BB.lower)` | robust normalized | 0.00 (extended) | closer to upper Bollinger Band |
| `close_vs_sma10` | derived | `+1` if `close >= SMA10`, else `-1` | 0.00 (extended) | above shortest-term average |
| `close_vs_sma20` | derived | `+1` if `close >= SMA20`, else `-1` | 0.00 (extended) | above 20-day average |
| `close_vs_sma30` | derived | `+1` if `close >= SMA30`, else `-1` | 0.00 (extended) | above 30-day average |
| `close_vs_ema10` | derived | `+1` if `close >= EMA10`, else `-1` | 0.00 (extended) | above 10-day EMA |
| `close_vs_ema20` | derived | `+1` if `close >= EMA20`, else `-1` | 0.00 (extended) | above 20-day EMA |
| `close_vs_ema30` | derived | `+1` if `close >= EMA30`, else `-1` | 0.00 (extended) | above 30-day EMA |
| `short_trend_emergence` | derived average of six short-term MA comparisons | average of `+1/-1` values | 0.00 (extended) | more short-term MAs crossed above |
| `pivot_distance` | derived as `(close - Pivot.M.Classic.Middle) / close` | robust normalized | 0.00 (extended) | stock above monthly pivot = support confirmation |
| `close_vs_camarilla_s1` | derived as `(close - Pivot.M.Camarilla.S1) / close` | robust normalized | 0.00 (extended) | stock above monthly Camarilla support S1 |
| `close_vs_camarilla_s2` | derived as `(close - Pivot.M.Camarilla.S2) / close` | robust normalized | 0.00 (extended) | stock above deeper monthly Camarilla support S2 |
| `close_vs_camarilla_r1` | derived as `(close - Pivot.M.Camarilla.R1) / close` | robust normalized | 0.00 (extended) | stock clearing monthly Camarilla resistance R1 |
| `close_vs_camarilla_r2` | derived as `(close - Pivot.M.Camarilla.R2) / close` | robust normalized | 0.00 (extended) | stock clearing stronger monthly Camarilla resistance R2 |

### Quality Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `total_revenue_yoy_growth_ttm` | raw | robust normalized | 1.00 | better growth |
| `total_revenue_qoq_growth_fq` | raw | robust normalized | 1.00 | better short growth |
| `ebitda_yoy_growth_ttm` | raw | robust normalized | 1.00 | better EBITDA growth |
| `ebitda_qoq_growth_fq` | raw | robust normalized | 1.00 | better short EBITDA growth |
| `net_income_yoy_growth_ttm` | raw | robust normalized | 1.00 | better earnings growth |
| `net_income_qoq_growth_fq` | raw | robust normalized | 1.00 | better short earnings growth |
| `free_cash_flow_yoy_growth_ttm` | raw | robust normalized | 1.00 | better FCF growth |
| `free_cash_flow_qoq_growth_fq` | raw | robust normalized | 1.00 | better short FCF growth |
| `gross_margin` | raw | robust normalized | 1.00 | better economics |
| `operating_margin` | raw | robust normalized | 1.00 | better operating quality |
| `after_tax_margin` | raw | robust normalized | 1.00 | better earnings quality |
| `return_on_assets` | raw | robust normalized | 1.00 | better asset efficiency |
| `return_on_equity` | raw | robust normalized | 1.00 | better equity efficiency |
| `return_on_invested_capital` | raw | robust normalized | 1.00 | better capital discipline |
| `free_cash_flow_margin_ttm` | raw | robust normalized | 0.00 (extended) | better cash conversion |
| `buyback_yield` | raw | robust normalized | 0.00 (extended) | stronger shareholder return via buybacks |
| `dividends_yield_current` | raw | robust normalized | 0.00 (extended) | stronger income return |
| `eps_forward_growth` | derived as `(forecast_next_fq - actual_fq) / abs(actual_fq)` | robust normalized | 0.00 (extended) | analysts expect EPS improvement next quarter |
| `revenue_per_employee` | derived as `total_revenue / number_of_employees` | robust normalized | 0.00 (extended) | higher operational efficiency per head |
| `earnings_per_share_diluted_yoy_growth_ttm` | raw | robust normalized | 0.00 (extended) | stronger diluted EPS growth trajectory |
| `gross_profit_margin_fy` | raw | robust normalized | 0.00 (extended) | better annual gross profit margin stability |
| `sustainable_growth_rate_ttm` | raw | robust normalized | 0.00 (extended) | higher internally sustainable growth |
| `piotroski_f_score_ttm` | raw | robust normalized | 0.00 (extended) | stronger accounting quality and balance-sheet health |
| `dividend_yield_recent` | raw | robust normalized | 0.00 (extended) | recent income return support |
| `dps_common_stock_prim_issue_yoy_growth_fy` | raw | robust normalized | 0.00 (extended) | dividend growth support |

### Valuation Component

Valuation uses `invert=True` and `positive_only=True` for all included fields. That means the model only scores positive multiples and treats lower multiples as better.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `price_earnings_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_earnings_growth_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_sales_current` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_book_fq` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_free_cash_flow_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_to_cash_f_operating_activities_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_to_revenue_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_to_ebit_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_ebitda_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `earnings_yield` | raw | robust normalized | 0.00 (extended) | higher earnings yield = cheaper stock |
| `distance_from_52w_high` | derived as `(close - price_52_week_high) / abs(price_52_week_high)` | robust normalized, then inverted | 0.00 (extended) | further below 52-week peak = deeper discount |
| `range_position_52w` | derived as `(close - price_52_week_low) / (price_52_week_high - price_52_week_low)` | robust normalized, then inverted | 0.00 (extended) | lower in 52-week range = more upside room |
| `enterprise_value_to_free_cash_flow_ttm` | raw | robust normalized, then inverted, positive-only | 0.00 (extended) | lower EV/FCF = better cash-flow value |
| `enterprise_value_to_gross_profit_ttm` | raw | robust normalized, then inverted, positive-only | 0.00 (extended) | lower EV/gross profit = better gross-profit value |
| `price_target_upside_average` | derived as `(price_target_average - close) / close` | robust normalized | 0.00 (extended) | higher analyst average target upside |
| `price_target_upside_median` | derived as `(price_target_median - close) / close` | robust normalized | 0.00 (extended) | higher median target upside with less outlier sensitivity |
| `price_target_downside_floor` | derived as `(price_target_low - close) / close` | robust normalized | 0.00 (extended) | better downside floor from lowest target |
| `book_value_discount` | derived as `(book_value_per_share_fq - close) / close` | robust normalized | 0.00 (extended) | larger discount to book value |

### Safety Component

`debt_to_equity`, `debt_to_revenue_ttm`, `net_debt`, and `beta_1_year` are inverted so lower values help safety.

`short_term_cash_coverage` is positive-only, so the model only uses it when the ratio is above zero.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `current_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `quick_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `cash_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `short_term_cash_coverage` | derived as `cash_n_short_term_invest / short_term_debt` using FY first then FQ fallback | robust normalized, positive-only | 1.00 | better short-term solvency |
| `altman_z_score_ttm` | raw | robust normalized | 1.00 | better distress profile |
| `debt_to_equity` | raw | robust normalized, then inverted | 1.00 | lower leverage is better |
| `debt_to_revenue_ttm` | raw | robust normalized, then inverted | 1.00 | lower leverage burden is better |
| `net_debt` | raw | robust normalized, then inverted | 1.00 | lower net debt is better |
| `beta_1_year` | raw | robust normalized, then inverted | 1.00 | lower volatility is safer |
| `total_debt_to_ebitda_fq` | raw | robust normalized, then inverted, positive-only | 0.00 (extended) | lower debt burden vs EBITDA |
| `beta_adjusted_atrp` | derived as `ATRP * beta_1_year` | robust normalized, then inverted, positive-only | 0.00 (extended) | lower volatility after beta adjustment |
| `price_target_dispersion` | derived as `(price_target_high - price_target_low) / close` | robust normalized, then inverted, positive-only | 0.00 (extended) | lower analyst target disagreement = cleaner risk picture |

## Default Horizon Weights

These weights are the starting map for profiles that inherit specific horizons. The `scale` component was added alongside the original seven and carries small weight by default.

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety | Scale |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.16 | 0.12 | 0.20 | 0.20 | 0.12 | 0.06 | 0.08 | 0.06 |
| `weeks` | 0.12 | 0.06 | 0.20 | 0.22 | 0.14 | 0.08 | 0.12 | 0.06 |
| `months` | 0.06 | 0.03 | 0.14 | 0.22 | 0.24 | 0.14 | 0.12 | 0.05 |
| `years` | 0.02 | 0.02 | 0.04 | 0.12 | 0.32 | 0.24 | 0.20 | 0.04 |

## Profile Design Framework

The 10 profiles are organized into three investment ideas:

### Idea 1 — Trend Following (where the market is moving)
- **breakout_long**: confirmed upside continuation — volume-driven tactical momentum
- **early_momentum_inflection**: catches nascent moves before crowd confirmation — coiled-energy detection

### Idea 2 — Real Quality Identification (ahead of time)
- **quality_value_compounder**: durable quality identification — persistent profitability and capital discipline
- **durable_value_compounder**: quality-led value investing — durable cash generators at attractive value anchors
- **sector_relative_outperformer**: best-in-class operators by relative strength — efficiency and accumulation
- **forward_edge_active**: forward-looking "what is getting better?" — QoQ improvement and emerging trend

### Idea 3 — Overlooked Fundamentals (unrewarded quality and hedging)
- **asymmetric_value**: quality NOT rewarded by the market — inverted momentum bias to surface mispricing
- **value_recovery**: turnaround with sequential improvement — QoQ dominates YoY
- **deep_value_momentum**: aggressive value plus catalyst — deep discount with early reversal confirmation
- **fragility_short**: structurally fragile names for short/hedge baskets — all biases inverted to amplify negatives

### Removed profiles
- **balanced**: removed — its role is now covered by `quality_value_compounder` which provides better differentiation. An alias maps `balanced` → `quality_value_compounder` for backward compatibility.
- **backtest_period_ladder**: removed — its retrospective tracking role is now covered by `sector_relative_outperformer`. An alias maps `backtest_period_ladder` → `sector_relative_outperformer` for backward compatibility.

## Profile Overrides

Only the values listed below differ from the default behavior. Any omitted signal weight remains `1.00`. Any omitted directional bias remains `positive=1.00`, `negative=1.00`. Any omitted missing-component fallback means the component is skipped if absent.

### 1. `breakout_long`

Idea: **Trend Following** — confirmed upside continuation

Intent:

- reward names already in motion with volume confirmation
- reward participation and event pressure
- downplay deep valuation and long-duration fundamental purity
- quality weight at 0.00 on days — pure tactical momentum

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.30 | 0.18 | 0.30 | 0.16 | 0.00 | 0.00 | 0.06 |
| `weeks` | 0.20 | 0.10 | 0.28 | 0.28 | 0.02 | 0.00 | 0.12 |
| `months` | 0.10 | 0.06 | 0.22 | 0.30 | 0.10 | 0.04 | 0.18 |
| `years` | 0.03 | 0.03 | 0.12 | 0.22 | 0.18 | 0.12 | 0.30 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `relative_volume_10d_calc=1.50`, `float_turnover=1.35`, `dollar_turnover_intensity=1.25`, `Value.Traded=1.20`, `AvgValue.Traded_10d=1.15`, `volume_trend=1.40`, `intraday_momentum=1.25`, `bb_squeeze=0.60` |
| `event` | `premarket_change=1.20`, `postmarket_change=1.20`, `gap=1.20`, `gap_severity=1.35`, `event_intensity=1.30` |
| `momentum` | `change=1.20`, `Perf.5D=1.40`, `Perf.W=1.30`, `Perf.1M=1.15`, `Perf.3M=1.00`, `Perf.YTD=0.60`, `Perf.Y=0.20`, `ROC=1.15`, `Mom=1.15`, `macd_spread=1.20`, `Recommend.MA=1.15`, `rsi7_centered=1.15`, `adx_directional_spread=1.50`, `aroon_spread=1.45` |
| `trend` | `trend_alignment=1.35`, `close_vs_sma50=1.20`, `close_vs_sma200=0.70`, `close_vs_ema50=1.20`, `close_vs_ema200=0.75`, `close_vs_vwap=1.25`, `close_vs_vwma=1.25`, `close_vs_sma10=1.30`, `close_vs_ema10=1.30` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.35 | 0.70 |
| `event` | 1.25 | 0.75 |
| `momentum` | 1.40 | 0.65 |
| `trend` | 1.30 | 0.75 |
| `quality` | 1.00 | 0.80 |
| `valuation` | 0.90 | 0.75 |
| `safety` | 1.00 | 0.80 |

Interpretation:

- most aggressive upside momentum bias in the system (+1.40x/−0.65x)
- ADX directional spread (1.50) and Aroon spread (1.45) catch emerging directional strength
- quality=0.00 at days means pure tactical scoring
- bb_squeeze dampened to 0.60 — breakouts should already be moving, not coiling

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.85`, `event=-0.55`, `momentum=-0.80`, `trend=-0.50` |
| `weeks` | `attention=-0.60`, `momentum=-0.65`, `trend=-0.45` |
| `months` | `momentum=-0.45`, `trend=-0.40` |

### 2. `early_momentum_inflection`

Idea: **Trend Following** — catches nascent moves before crowd confirmation

Intent:

- catch names at the very beginning of a directional move before the crowd notices
- detect coiled-energy setups via Bollinger Band squeeze and range compression
- use Aroon spread (highest in system at 1.65) for new-trend detection
- de-emphasize established long-duration momentum so names already well into a run do not dominate
- established momentum nearly invisible (Perf.Y=0.10)

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.26 | 0.10 | 0.30 | 0.26 | 0.02 | 0.01 | 0.05 |
| `weeks` | 0.20 | 0.06 | 0.28 | 0.32 | 0.04 | 0.02 | 0.08 |
| `months` | 0.12 | 0.04 | 0.22 | 0.28 | 0.12 | 0.06 | 0.16 |
| `years` | 0.02 | 0.02 | 0.08 | 0.18 | 0.24 | 0.18 | 0.28 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `bb_squeeze=1.55`, `range_compression=1.50`, `volatility_contraction=1.45`, `volume_trend=1.40`, `relative_volume_10d_calc=1.20`, `intraday_momentum=1.10` |
| `event` | `premarket_change=1.15`, `gap_severity=1.25`, `event_intensity=1.20` |
| `momentum` | `aroon_spread=1.65`, `adx_directional_spread=1.55`, `stoch_rsi_crossover=1.40`, `Perf.5D=1.30`, `macd_spread=1.35`, `ROC=1.25`, `Perf.Y=0.10`, `Perf.YTD=0.15`, `Perf.6M=0.30` |
| `trend` | `short_trend_emergence=1.55`, `close_vs_sma10=1.40`, `close_vs_ema10=1.40`, `bb_position=1.30`, `close_vs_vwap=1.25`, `close_vs_vwma=1.20`, `trend_alignment=0.75`, `close_vs_sma200=0.70`, `close_vs_ema200=0.70` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.30 | 0.72 |
| `event` | 1.12 | 0.88 |
| `momentum` | 1.40 | 0.65 |
| `trend` | 1.30 | 0.75 |
| `quality` | 1.00 | 0.82 |
| `safety` | 1.00 | 0.82 |

Interpretation:

- `aroon_spread=1.65` is the highest single momentum signal in the system — when Aroon Up exceeds Aroon Down, a new trend is forming
- coiled-energy signals (`bb_squeeze=1.55`, `range_compression=1.50`) are the highest in the system
- `short_trend_emergence=1.55` detects when multiple short-term MAs are crossing bullish
- established momentum heavily muted (Perf.Y=0.10) so names already far into a run do not crowd out inflection candidates

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.75`, `momentum=-0.80`, `trend=-0.55` |
| `weeks` | `attention=-0.50`, `momentum=-0.60`, `trend=-0.45` |
| `months` | `momentum=-0.40`, `trend=-0.30` |

### 3. `quality_value_compounder`

Idea: **Real Quality** — durable quality identification

Intent:

- reward durable profitability, efficiency, and balance-sheet quality
- quality dominates all horizons (35–42%)
- stop tactical excitement from dominating the leaderboard
- explicitly penalize names with sparse fundamental support (heaviest missing-component penalties)
- attention inverted (0.70x/1.50x) — low-volume names face strongest anti-concentration drag
- activate all extended quality signals (ROIC=1.40, FCF margin, sustainable growth, Piotroski F-score, revenue per employee)
- explicitly zero `scale` so the profile ranks strong earners and balance sheets, not simply large companies

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.01 | 0.04 | 0.16 | 0.35 | 0.16 | 0.24 |
| `weeks` | 0.03 | 0.01 | 0.05 | 0.14 | 0.34 | 0.18 | 0.25 |
| `months` | 0.02 | 0.01 | 0.04 | 0.12 | 0.36 | 0.20 | 0.25 |
| `years` | 0.01 | 0.00 | 0.01 | 0.06 | 0.42 | 0.24 | 0.26 |

`scale=0.00` is explicitly set for every horizon to avoid inherited size bias.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.10`, `Perf.5D=0.10`, `Perf.W=0.15`, `Perf.1M=0.30`, `Perf.3M=0.80`, `Perf.6M=0.90`, `Perf.YTD=1.05`, `Perf.Y=1.25`, `ROC=0.30`, `Mom=0.35`, `macd_spread=0.25`, `rsi_centered=0.15`, `rsi7_centered=0.05` |
| `trend` | `close_vs_sma200=1.40`, `close_vs_ema200=1.40`, `trend_alignment=1.25`, `close_vs_sma50=0.80`, `close_vs_ema50=0.80`, `close_vs_vwap=0.20`, `close_vs_vwma=0.30` |
| `quality` | `return_on_invested_capital=1.40`, `operating_margin=1.35`, `free_cash_flow_margin_ttm=1.35`, `piotroski_f_score_ttm=1.35`, `free_cash_flow_yoy_growth_ttm=1.30`, `sustainable_growth_rate_ttm=1.30`, `ebitda_yoy_growth_ttm=1.20`, `earnings_per_share_diluted_yoy_growth_ttm=1.20`, `gross_profit_margin_fy=1.20`, `revenue_per_employee=1.20`, `return_on_equity=1.15`, `total_revenue_yoy_growth_ttm=1.15`, `after_tax_margin=1.15`, `gross_margin=1.15`, `return_on_assets=1.10`, `buyback_yield=0.90` |
| `valuation` | `price_free_cash_flow_ttm=1.25`, `price_earnings_growth_ttm=1.20`, `enterprise_value_ebitda_ttm=1.15`, `price_earnings_ttm=1.10`, `price_to_cash_f_operating_activities_ttm=1.10` |
| `safety` | `total_debt_to_ebitda_fq=1.35`, `altman_z_score_ttm=1.30`, `debt_to_equity=1.30`, `debt_to_revenue_ttm=1.25`, `short_term_cash_coverage=1.25`, `net_debt=1.15`, `price_target_dispersion=0.70`, `beta_1_year=0.70` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.70 | 1.50 |
| `momentum` | 1.00 | 0.95 |
| `trend` | 1.05 | 1.15 |
| `quality` | 1.28 | 1.38 |
| `valuation` | 1.10 | 1.25 |
| `safety` | 1.20 | 1.35 |

Interpretation:

- **attention bias 0.70x/1.50x is the most aggressive anti-concentration lever** — low-volume names have negative attention amplified by 50%
- quality negative at 1.38x — durable profitability failure is heavily punished
- ROIC=1.40 is the highest quality signal weight — capital discipline is the primary quality filter
- short-term momentum nearly zeroed (change=0.10, rsi7=0.05) — noise elimination
- FCF margin, sustainable growth, and Piotroski F-score are now explicit durability checks
- missing quality at years fallback of −1.30 is the heaviest in the system

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.50`, `quality=-1.00`, `valuation=-0.80`, `safety=-0.75` |
| `weeks` | `attention=-0.40`, `quality=-1.10`, `valuation=-0.85`, `safety=-0.85` |
| `months` | `attention=-0.30`, `quality=-1.20`, `valuation=-0.95`, `safety=-0.90` |
| `years` | `attention=-0.20`, `quality=-1.30`, `valuation=-1.05`, `safety=-1.00` |

### 4. `durable_value_compounder`

Idea: **Quality-Led Value Investing** — durable performers at disciplined prices

Intent:

- identify operating companies that combine quality, value, and safety for months/years positioning
- require visible value support, unlike `quality_value_compounder`, which can justify premium valuations
- avoid relying on tactical reversal, unlike `deep_value_momentum`
- use all current value-oriented scan inputs: FCF yield, operating cash-flow yield, EV/FCF, Graham-value gap, tangible-book gap, cash-per-share gap, Piotroski, sustainable growth, interest cover, Zmijewski, and debt/EBITDA
- explicitly zero `scale` so size does not become a proxy for quality

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.02 | 0.00 | 0.02 | 0.08 | 0.34 | 0.34 | 0.20 |
| `weeks` | 0.02 | 0.00 | 0.03 | 0.08 | 0.34 | 0.33 | 0.20 |
| `months` | 0.01 | 0.00 | 0.02 | 0.07 | 0.36 | 0.36 | 0.18 |
| `years` | 0.00 | 0.00 | 0.01 | 0.04 | 0.38 | 0.38 | 0.19 |

`scale=0.00` is explicitly set for every horizon.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | Short-term price signals are nearly invisible; `Perf.Y=0.45` and `Perf.5Y=0.55` are retained as long-duration proof of performer status. |
| `trend` | Long averages and support anchors are modest confirmation only: `close_vs_sma200=0.85`, `close_vs_ema200=0.85`, `trend_alignment=0.70`, Camarilla support/resistance at 0.25-0.55. |
| `quality` | `piotroski_f_score_ttm=1.50`, `return_on_invested_capital=1.45`, `free_cash_flow_margin_ttm=1.45`, `return_on_capital_employed_fy=1.40`, `sustainable_growth_rate_ttm=1.35`, `operating_margin_ttm=1.35`, `free_cash_flow_cagr_5y=1.35`, `sloan_ratio_ttm=1.15` inverted. |
| `valuation` | `enterprise_value_to_free_cash_flow_ttm=1.55`, `free_cash_flow_yield=1.55`, `earnings_yield=1.50`, `price_free_cash_flow_ttm=1.45`, `graham_value_gap=1.40`, `tangible_book_value_gap=1.35`, `book_value_discount=1.30`, `price_target_downside_floor=1.25`, `ncavps_ratio_current/fq=1.10`. |
| `safety` | `altman_z_score_ttm=1.35`, `total_debt_to_ebitda_fq=1.35`, `net_debt_to_ebitda_fq=1.35`, `interst_cover_ttm=1.25`, `zmijewski_score_ttm=1.25` inverted, cash-to-debt/liability coverage 1.05-1.20. |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.55 | 1.20 |
| `momentum` | 0.80 | 0.55 |
| `trend` | 0.90 | 0.75 |
| `quality` | 1.35 | 1.45 |
| `valuation` | 1.45 | 1.20 |
| `safety` | 1.25 | 1.45 |

Interpretation:

- quality and valuation are co-primary at 67-76% combined horizon weight
- momentum weakness is forgiven, but poor quality or safety is not
- valuation positive at 1.45x makes cheap durable cash generation the main upside driver
- missing quality/valuation evidence is heavily penalized so sparse-data names cannot rank on a single cheap multiple

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.35`, `quality=-1.15`, `valuation=-1.15`, `safety=-0.80` |
| `weeks` | `attention=-0.30`, `quality=-1.20`, `valuation=-1.25`, `safety=-0.90` |
| `months` | `attention=-0.20`, `quality=-1.30`, `valuation=-1.35`, `safety=-1.00` |
| `years` | `quality=-1.40`, `valuation=-1.45`, `safety=-1.10` |

### 5. `sector_relative_outperformer`

Idea: **Real Quality** — best-in-class operators by relative strength

Intent:

- surface best-in-class operators that outperform through operational excellence
- reward efficiency (revenue_per_employee=1.30), capital returns (buyback yield), and durable profitability
- detect volume accumulation through volume trend (1.50 — highest in system; raw attention damped to 0.65–0.85)
- long-duration momentum emphasized (Perf.Y=1.30) while short-term crushed (change=0.15)
- use monthly pivot distance for trend support confirmation

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.02 | 0.08 | 0.20 | 0.32 | 0.14 | 0.20 |
| `weeks` | 0.03 | 0.02 | 0.08 | 0.18 | 0.32 | 0.17 | 0.20 |
| `months` | 0.02 | 0.01 | 0.06 | 0.14 | 0.35 | 0.22 | 0.20 |
| `years` | 0.01 | 0.01 | 0.03 | 0.07 | 0.40 | 0.26 | 0.22 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `volume_trend=1.50`, `relative_volume_10d_calc=0.85`, `float_turnover=0.80`, `dollar_turnover_intensity=0.75`, `Value.Traded=0.70`, `AvgValue.Traded_10d=0.65` |
| `momentum` | `Perf.Y=1.30`, `Perf.6M=1.20`, `Perf.YTD=1.15`, `Perf.3M=1.10`, `Perf.1M=0.50`, `Perf.W=0.25`, `Perf.5D=0.15`, `change=0.15` |
| `trend` | `close_vs_sma200=1.40`, `close_vs_ema200=1.40`, `trend_alignment=1.30`, `pivot_distance=1.15`, `close_vs_sma50=0.85`, `close_vs_ema50=0.85`, `close_vs_vwap=0.30`, `close_vs_vwma=0.40` |
| `quality` | `return_on_invested_capital=1.40`, `eps_forward_growth=1.35`, `revenue_per_employee=1.30`, `free_cash_flow_margin_ttm=1.30`, `operating_margin=1.30`, `free_cash_flow_yoy_growth_ttm=1.25`, `ebitda_yoy_growth_ttm=1.20`, `return_on_equity=1.15`, `earnings_per_share_diluted_yoy_growth_ttm=1.15`, `total_revenue_yoy_growth_ttm=1.15`, `gross_profit_margin_fy=1.10`, `buyback_yield=1.10`, `after_tax_margin=1.10`, `return_on_assets=1.10`, `gross_margin=1.10` |
| `valuation` | `price_earnings_growth_ttm=1.25`, `earnings_yield=1.20`, `price_free_cash_flow_ttm=1.20`, `enterprise_value_ebitda_ttm=1.10`, `price_earnings_ttm=1.05` |
| `safety` | `altman_z_score_ttm=1.20`, `debt_to_equity=1.18`, `debt_to_revenue_ttm=1.12`, `short_term_cash_coverage=1.10`, `net_debt=1.10`, `beta_1_year=0.65` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.70 | 1.40 |
| `momentum` | 1.08 | 0.85 |
| `trend` | 1.12 | 1.12 |
| `quality` | 1.30 | 1.35 |
| `valuation` | 1.18 | 1.12 |
| `safety` | 1.15 | 1.28 |

Interpretation:

- quality bias +1.30x/−1.35x — excellence must be genuine; negative quality hurts more
- trend bias symmetric at 1.12x — both directions carry equal structural weight
- volume_trend=1.50 detects sustained institutional accumulation, not one-day spikes
- raw attention signals dampened because the profile cares about accumulation trend, not absolute volume

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.45`, `quality=-0.90`, `valuation=-0.70`, `safety=-0.70` |
| `weeks` | `attention=-0.35`, `quality=-1.00`, `valuation=-0.80`, `safety=-0.80` |
| `months` | `attention=-0.25`, `quality=-1.10`, `valuation=-0.90`, `safety=-0.90` |
| `years` | `quality=-1.20`, `valuation=-1.00`, `safety=-0.95` |

### 6. `forward_edge_active`

Idea: **Real Quality** — forward-looking "what is getting better?"

Intent:

- target risk-adjusted outperformance on the weeks-to-months timescale as an early catcher
- eps_forward_growth=1.75 is the highest single signal weight in the system
- QoQ growth dominates YoY (1.35–1.45 vs 0.90–1.00) — focus on improvement trajectory
- attention most aggressively inverted (0.50x/1.50x) — the most anti-crowd profile
- coiled-energy detection via bb_squeeze=1.60 and range_compression=1.55
- Perf.Y=0.05 — established momentum nearly invisible

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.05 | 0.08 | 0.14 | 0.26 | 0.22 | 0.10 | 0.15 |
| `weeks` | 0.04 | 0.06 | 0.12 | 0.22 | 0.26 | 0.14 | 0.16 |
| `months` | 0.03 | 0.03 | 0.08 | 0.16 | 0.30 | 0.18 | 0.22 |
| `years` | 0.02 | 0.01 | 0.03 | 0.10 | 0.32 | 0.22 | 0.30 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `bb_squeeze=1.60`, `range_compression=1.55`, `volatility_contraction=1.50`, `volume_trend=1.50`, `relative_volume_10d_calc=0.70`, `float_turnover=0.60`, `dollar_turnover_intensity=0.55`, `Value.Traded=0.50`, `AvgValue.Traded_10d=0.45` |
| `event` | `eps_surprise_percent_fq=1.60` (highest in system), `gap_severity=1.25`, `event_intensity=1.20` |
| `momentum` | `Perf.Y=0.05`, `Perf.YTD=0.15`, `Perf.6M=0.30`, `aroon_spread=1.40`, `macd_spread=1.30`, `adx_directional_spread=1.30`, `Perf.5D=1.20`, `ROC=1.20` |
| `trend` | `short_trend_emergence=1.65` (highest in system), `close_vs_sma10=1.50`, `close_vs_ema10=1.50`, `close_vs_sma20=1.35`, `close_vs_ema20=1.35`, `bb_position=1.25`, `close_vs_vwap=1.30`, `close_vs_vwma=1.25`, `trend_alignment=0.70`, `close_vs_sma200=0.65`, `close_vs_ema200=0.65` |
| `quality` | `eps_forward_growth=1.75` (HIGHEST IN SYSTEM), `free_cash_flow_qoq_growth_fq=1.45`, `net_income_qoq_growth_fq=1.40`, `ebitda_qoq_growth_fq=1.35`, `total_revenue_qoq_growth_fq=1.35`, `free_cash_flow_margin_ttm=1.35`, `return_on_invested_capital=1.20`, `operating_margin=1.15` (YoY metrics at 0.90–1.00) |
| `valuation` | `price_earnings_growth_ttm=1.30`, `earnings_yield=1.25`, `distance_from_52w_high=1.20`, `price_free_cash_flow_ttm=1.15` |
| `safety` | `altman_z_score_ttm=1.20`, `debt_to_equity=1.15`, `net_debt=1.10` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.50 | 1.50 |
| `event` | 1.20 | 0.82 |
| `momentum` | 1.15 | 0.72 |
| `trend` | 1.30 | 0.82 |
| `quality` | 1.22 | 1.35 |
| `valuation` | 1.12 | 0.78 |
| `safety` | 1.10 | 1.40 |

Interpretation:

- attention bias 0.50x/1.50x is the most extreme inversion — crowded trades face maximum drag, undiscovered names get the edge
- quality negative at 1.35x and safety negative at 1.40x — forward quality and solvency must be solid
- trend positive at 1.30x rewards early trend breaks; valuation negative at 0.78x dampens expensive-name penalty (forward edge names may be optically expensive)
- performance tracking periods: days→Perf.W, weeks→Perf.1M, months→Perf.YTD+Perf.1M, years→Perf.Y

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `quality=-0.65`, `safety=-0.65`, `trend=-0.50` |
| `weeks` | `quality=-0.75`, `safety=-0.75`, `trend=-0.45` |
| `months` | `quality=-0.90`, `safety=-0.90`, `valuation=-0.60` |
| `years` | `quality=-1.00`, `safety=-1.00`, `valuation=-0.80` |

### 7. `asymmetric_value`

Idea: **Overlooked Fundamentals** — quality NOT rewarded by the market

Intent:

- surface low-valuation companies with asymmetric upside potential
- combine traditional multiples with operating-company value anchors: earnings_yield=1.55, EV/FCF=1.50, book_value_discount=1.40, target upside/downside, and peer-revenue value gap
- require unrecognized or overlooked fundamentals through cash-flow quality, ROIC, Piotroski F-score, and forward EPS growth
- keep momentum and trend as small recovery hints, not as primary rank drivers
- maintain a strong safety floor across all horizons to avoid value traps
- explicitly zero `scale` so size does not substitute for mispricing evidence

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.01 | 0.03 | 0.07 | 0.23 | 0.38 | 0.24 |
| `weeks` | 0.03 | 0.02 | 0.04 | 0.06 | 0.25 | 0.37 | 0.23 |
| `months` | 0.03 | 0.01 | 0.02 | 0.04 | 0.28 | 0.39 | 0.23 |
| `years` | 0.01 | 0.00 | 0.01 | 0.02 | 0.31 | 0.40 | 0.25 |

`scale=0.00` is explicitly set for every horizon to prevent inherited size tilt.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `stoch_rsi_crossover=1.15`, `Perf.3M=0.80`, `Perf.6M=0.70`, `Perf.YTD=0.65`, `Perf.Y=0.50`, `change=0.12`, `Perf.5D=0.15`, `Perf.W=0.20`, `Perf.1M=0.30` |
| `trend` | `close_vs_sma200=0.90`, `close_vs_ema200=0.90`, `short_trend_emergence=0.85`, `pivot_distance=0.80`, `trend_alignment=0.75`, `close_vs_sma50=0.60`, `close_vs_ema50=0.60` |
| `quality` | `eps_forward_growth=1.55`, `free_cash_flow_margin_ttm=1.45`, `return_on_invested_capital=1.40`, `piotroski_f_score_ttm=1.40`, `free_cash_flow_yoy_growth_ttm=1.40`, `operating_margin=1.25`, `revenue_per_employee=1.25`, `earnings_per_share_diluted_yoy_growth_ttm=1.20`, `buyback_yield=1.20`, `sustainable_growth_rate_ttm=1.15` |
| `valuation` | `earnings_yield=1.55`, `enterprise_value_to_free_cash_flow_ttm=1.50`, `book_value_discount=1.40`, `price_free_cash_flow_ttm=1.40`, `range_position_52w=1.35`, `price_target_upside_median=1.35`, `peer_revenue_value_gap=1.35`, `price_book_fq=1.30`, `distance_from_52w_high=1.30`, `price_target_downside_floor=1.30`, `price_target_upside_average=1.25` |
| `safety` | `altman_z_score_ttm=1.35`, `total_debt_to_ebitda_fq=1.35`, `short_term_cash_coverage=1.30`, `debt_to_equity=1.30`, `debt_to_revenue_ttm=1.25`, `net_debt=1.25`, `price_target_dispersion=0.90`, `beta_adjusted_atrp=0.85` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.65 | 1.45 |
| `momentum` | 0.70 | 0.45 |
| `trend` | 0.90 | 0.70 |
| `quality` | 1.22 | 1.35 |
| `valuation` | 1.45 | 1.10 |
| `safety` | 1.15 | 1.40 |

Interpretation:

- valuation positive at 1.45x is the primary amplifier — deep discount evidence is the profile's center of gravity
- peer_revenue_value_gap rewards companies whose revenue share exceeds market-cap share inside the current scan universe, especially industry scans
- momentum positive at 0.70x and negative at 0.45x means weak tape is forgiven but never becomes the thesis by itself
- safety negative at 1.40x acts as the value-trap guard — balance-sheet deterioration kills the score
- forward EPS growth, Piotroski F-score, FCF margin/growth, EV/FCF, book-value discount, and target upside/downside are the operating-company filters

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.45`, `valuation=-1.10`, `quality=-1.05`, `safety=-0.75` |
| `weeks` | `attention=-0.35`, `valuation=-1.20`, `quality=-1.15`, `safety=-0.85` |
| `months` | `attention=-0.25`, `valuation=-1.30`, `quality=-1.25`, `safety=-0.95` |
| `years` | `valuation=-1.35`, `quality=-1.35`, `safety=-1.05` |

### 8. `value_recovery`

Idea: **Overlooked Fundamentals** — turnaround with sequential improvement

Intent:

- reward cheap names with improving fundamentals and solvency
- QoQ growth dominates YoY (1.20–1.35 vs 0.85–1.10) to detect sequential turnaround
- eps_forward_growth=1.50 — forward-looking recovery conviction
- stoch_rsi_crossover=1.35 and short_trend_emergence=1.20 — recovery turn detection is now a primary entry trigger
- momentum negative bias at 0.60x — recent weakness is forgiven (turnaround candidates often look ugly)
- tolerate imperfect short-term momentum while requiring valuation and safety evidence

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.06 | 0.04 | 0.04 | 0.12 | 0.24 | 0.26 | 0.24 |
| `weeks` | 0.06 | 0.04 | 0.06 | 0.14 | 0.22 | 0.25 | 0.23 |
| `months` | 0.04 | 0.03 | 0.05 | 0.14 | 0.28 | 0.26 | 0.20 |
| `years` | 0.02 | 0.01 | 0.01 | 0.07 | 0.30 | 0.32 | 0.27 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.30`, `Perf.5D=0.25`, `Perf.W=0.30`, `Perf.1M=0.45`, `Perf.3M=1.15`, `Perf.YTD=1.00`, `Perf.Y=0.80`, `ROC=0.45`, `Mom=0.50`, `macd_spread=0.40`, `stoch_rsi_centered=0.55`, `stoch_rsi_crossover=1.35` |
| `trend` | `close_vs_sma200=1.10`, `close_vs_ema200=1.10`, `pivot_distance=1.10`, `trend_alignment=1.05`, `short_trend_emergence=1.20`, `close_vs_sma50=0.85`, `close_vs_ema50=0.85`, `close_vs_vwap=0.50`, `close_vs_vwma=0.60` |
| `quality` | `eps_forward_growth=1.50`, `free_cash_flow_qoq_growth_fq=1.35`, `net_income_qoq_growth_fq=1.30`, `ebitda_qoq_growth_fq=1.25`, `total_revenue_qoq_growth_fq=1.25`, `piotroski_f_score_ttm=1.20`, `free_cash_flow_margin_ttm=1.20`, `sustainable_growth_rate_ttm=1.10`, `operating_margin=1.10` (YoY at 0.85–1.10) |
| `valuation` | `price_earnings_growth_ttm=1.25`, `earnings_yield=1.25`, `price_book_fq=1.25`, `price_target_upside_average=1.20`, `price_target_upside_median=1.20`, `enterprise_value_ebitda_ttm=1.20`, `enterprise_value_to_free_cash_flow_ttm=1.15`, `book_value_discount=1.10` |
| `safety` | `altman_z_score_ttm=1.25`, `total_debt_to_ebitda_fq=1.25`, `net_debt=1.20`, `debt_to_equity=1.20`, `short_term_cash_coverage=1.15`, `debt_to_revenue_ttm=1.10`, `price_target_dispersion=0.65` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.82 | 1.32 |
| `momentum` | 0.85 | 0.60 |
| `trend` | 1.08 | 0.85 |
| `quality` | 1.18 | 1.18 |
| `valuation` | 1.30 | 1.12 |
| `safety` | 1.18 | 1.20 |

Interpretation:

- momentum negative at 0.60x is the most forgiving of all long profiles — turnaround candidates are expected to have ugly recent tape
- quality bias symmetric at 1.18x — improvement is rewarded but decline is penalized equally
- valuation positive at 1.30x amplifies cheap evidence for rerating candidates

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.40`, `valuation=-0.85`, `quality=-0.55`, `safety=-0.65` |
| `weeks` | `attention=-0.30`, `valuation=-0.95`, `quality=-0.65`, `safety=-0.75` |
| `months` | `attention=-0.20`, `valuation=-1.05`, `quality=-0.75`, `safety=-0.85` |
| `years` | `valuation=-1.15`, `quality=-0.85`, `safety=-0.95` |

### 9. `deep_value_momentum`

Idea: **Overlooked Fundamentals** — aggressive value plus catalyst

Intent:

- require deep valuation support and a real catalyst/reversal signal before ranking value names highly
- combine accounting value, cash-flow value, analyst target upside, and downside-floor evidence
- make sequential quality improvement a gate, not an afterthought
- use momentum and trend positively, unlike `asymmetric_value`, because this profile wants proof that value is starting to wake up
- keep value-trap controls active through debt/EBITDA, Piotroski F-score, beta-adjusted ATRP, and target dispersion

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.07 | 0.03 | 0.22 | 0.22 | 0.16 | 0.22 | 0.08 |
| `weeks` | 0.05 | 0.03 | 0.16 | 0.22 | 0.22 | 0.24 | 0.08 |
| `months` | 0.03 | 0.02 | 0.10 | 0.16 | 0.28 | 0.28 | 0.13 |
| `years` | 0.02 | 0.01 | 0.05 | 0.10 | 0.32 | 0.32 | 0.18 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `volume_trend=1.35`, `relative_volume_10d_calc=1.10`, `float_turnover=1.05`, `dollar_turnover_intensity=1.00`, `intraday_momentum=0.80` |
| `momentum` | `stoch_rsi_crossover=1.55`, `aroon_spread=1.35`, `adx_directional_spread=1.30`, `stoch_rsi_centered=1.10`, `macd_spread=0.90`, recent performance active but long trailing performance muted |
| `trend` | `short_trend_emergence=1.40`, short MAs and EMAs at 1.00-1.10, Camarilla support/resistance distances active, long MAs damped to 0.50 |
| `quality` | `eps_forward_growth=1.55`, `free_cash_flow_qoq_growth_fq=1.40`, `net_income_qoq_growth_fq=1.30`, `ebitda_qoq_growth_fq=1.25`, `piotroski_f_score_ttm=1.25`, `sustainable_growth_rate_ttm=1.15` |
| `valuation` | `earnings_yield=1.40`, `price_book_fq=1.35`, `enterprise_value_ebitda_ttm=1.30`, `enterprise_value_to_free_cash_flow_ttm=1.30`, `price_target_upside_median=1.30`, `price_target_upside_average=1.25`, `book_value_discount=1.25`, `price_target_downside_floor=1.15` |
| `safety` | `total_debt_to_ebitda_fq=1.20`, `altman_z_score_ttm=1.20`, `debt_to_equity=1.15`, `net_debt=1.15`, `beta_adjusted_atrp=0.85`, `price_target_dispersion=0.70` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.90 | 1.20 |
| `momentum` | 1.35 | 0.70 |
| `trend` | 1.30 | 0.75 |
| `quality` | 1.20 | 1.20 |
| `valuation` | 1.30 | 1.10 |
| `safety` | 1.10 | 1.30 |

Interpretation:

- this profile is the active-manager bridge between value and momentum: it does not buy cheapness without reversal confirmation, and it does not buy reversal without value support
- `stoch_rsi_crossover=1.55` and `short_trend_emergence=1.40` are the catalyst pair
- `eps_forward_growth=1.55`, sequential FCF/net income/EBITDA improvement, and Piotroski F-score filter out passive vehicles and weak operating companies
- valuation is intentionally broad: book value, earnings yield, EV/FCF, EV/EBITDA, target upside, and downside floor all contribute

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.30`, `quality=-1.00`, `valuation=-0.85`, `safety=-0.60` |
| `weeks` | `attention=-0.25`, `quality=-1.10`, `valuation=-0.95`, `safety=-0.70` |
| `months` | `attention=-0.15`, `quality=-1.20`, `valuation=-1.05`, `safety=-0.80` |
| `years` | `quality=-1.30`, `valuation=-1.15`, `safety=-0.90` |

### 10. `fragility_short`

Idea: **Hedging** — identifies structurally fragile names for short/hedge baskets

Intent:

- penalize downside momentum, downside event pressure, and weak safety harder than any other profile
- all directional biases inverted to amplify negatives — safety bias −1.45x is the most aggressive in the system
- safety weight at 0.57 on years is the highest single-component weight in the entire system
- beta NOT inverted (1.15 weight) — higher beta = more fragile = better short candidate
- reward deterioration evidence via extended quality/safety signals

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.16 | 0.30 | 0.28 | 0.18 | 0.02 | 0.01 | 0.05 |
| `weeks` | 0.08 | 0.20 | 0.28 | 0.24 | 0.04 | 0.02 | 0.14 |
| `months` | 0.04 | 0.08 | 0.20 | 0.22 | 0.14 | 0.04 | 0.28 |
| `years` | 0.00 | 0.02 | 0.06 | 0.12 | 0.18 | 0.05 | 0.57 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `event` | `gap_severity=1.35`, `eps_surprise_percent_fq=1.25`, `event_intensity=1.20`, `premarket_change=1.15`, `postmarket_change=1.15` |
| `momentum` | `Perf.5D=1.25`, `Perf.W=1.25`, `change=1.20`, `Perf.1M=1.15`, `adx_directional_spread=1.20`, `ROC=1.15`, `Perf.3M=1.05` |
| `trend` | `trend_alignment=1.25`, `close_vs_sma200=1.20`, `close_vs_ema200=1.20`, `close_vs_sma50=1.12`, `close_vs_ema50=1.12`, `close_vs_vwap=1.10`, `close_vs_vwma=1.10` |
| `quality` | `free_cash_flow_margin_ttm=1.20`, `free_cash_flow_yoy_growth_ttm=1.20`, `operating_margin=1.15`, `eps_forward_growth=1.15`, `return_on_invested_capital=1.10` |
| `safety` | `debt_to_equity=1.35`, `altman_z_score_ttm=1.35`, `debt_to_revenue_ttm=1.30`, `net_debt=1.25`, `beta_1_year=1.15` (NOT inverted — higher beta = more fragile), `short_term_cash_coverage=1.10` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.00 | 1.00 |
| `event` | 0.85 | 1.30 |
| `momentum` | 0.80 | 1.30 |
| `trend` | 0.85 | 1.35 |
| `quality` | 0.85 | 1.25 |
| `valuation` | 0.90 | 1.20 |
| `safety` | 0.80 | 1.45 |

Interpretation:

- ALL biases invert to amplify negatives — events, momentum, trend, quality, valuation, and safety all punish downside more than they reward upside
- safety bias at −1.45x is the most aggressive single directional multiplier in the system
- attention is neutral (1.00/1.00) — fragility can occur in any volume regime
- combined with safety weight=0.57 at years, this creates maximum sensitivity to balance-sheet deterioration

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `event=-0.45`, `momentum=-0.50`, `trend=-0.40` |
| `weeks` | `momentum=-0.55`, `trend=-0.45`, `safety=-0.50` |
| `months` | `trend=-0.50`, `safety=-0.70`, `quality=-0.40` |
| `years` | `safety=-0.90`, `quality=-0.45`, `valuation=-0.30` |

## How To Read The Profiles Now

### If you want tactical upside continuation

Use `breakout_long`.

You should expect the leaders to have:

- abnormal activity with volume trend confirmation (1.40) and intraday momentum (1.25)
- strong gap or event pressure with gap severity amplified (1.35)
- strong short-term momentum — ADX directional spread (1.50) and Aroon spread (1.45) detect directional strength
- clean tactical trend confirmation — short-term MAs favored, 200-day dampened
- quality=0.00 at days — pure tactical scoring with no fundamental noise

You should not expect it to prefer the cheapest or safest names. Momentum bias at +1.40x/−0.65x is the most aggressive upside bet in the system.

### If you want to catch early trend inflections

Use `early_momentum_inflection`.

You should expect the leaders to have:

- coiled-energy setups: bb_squeeze=1.55 and range_compression=1.50 (highest in system)
- emerging directional shift: aroon_spread=1.65 (highest single momentum signal), ADX directional spread=1.55
- fresh volume accumulation with volume_trend=1.40
- muted legacy momentum: Perf.Y=0.10, Perf.YTD=0.15 — already-established runs are nearly invisible
- short_trend_emergence=1.55 detects multiple short-term MAs crossing bullish

This profile catches the first winds of a directional move before it becomes obvious to the broader market.

### If you want durable quality leadership

Use `quality_value_compounder`.

You should expect the leaders to have:

- stronger profitability and returns on capital (ROIC=1.40 is the primary quality filter)
- better balance-sheet quality and operational efficiency
- acceptable valuation with FCF-based measures emphasized (P/FCF=1.25)
- evidence of diluted EPS growth and gross margin stability
- attention inverted at 0.70x/1.50x — the most aggressive anti-concentration lever

Names with missing fundamentals should rank significantly worse (quality fallback at years = −1.30).

### If you want best-in-class sector outperformers

Use `sector_relative_outperformer`.

You should expect the leaders to have:

- superior operational efficiency: revenue_per_employee=1.30, strong gross margins, disciplined capital allocation
- durable profitability: ROIC=1.40, operating_margin=1.30, growing FCF margins
- sustained institutional accumulation: volume_trend=1.50 (highest in system), not one-day spikes
- confirmed trend structure: above key moving averages, trading above monthly pivot point
- long-duration momentum: Perf.Y=1.30, Perf.6M=1.20 while short-term noise crushed (change=0.15)

### If you want forward-looking quality improvements

Use `forward_edge_active`.

You should expect the leaders to have:

- forward EPS improvement: eps_forward_growth=1.75 (highest single signal weight in the entire system)
- sequential QoQ acceleration: FCF QoQ=1.45, net income QoQ=1.40 (QoQ dominates YoY)
- coiled-energy before breakout: bb_squeeze=1.60, range_compression=1.55, short_trend_emergence=1.65 (highest trend signal)
- earnings surprise history: eps_surprise_percent_fq=1.60 (highest in system)
- most anti-crowd bias: attention at 0.50x/1.50x, Perf.Y=0.05

This is the most forward-looking profile — it looks at what is getting better, not what was.

### If you want overlooked quality with asymmetric upside

Use `asymmetric_value`.

You should expect the leaders to have:

- deep value discount: earnings_yield=1.40, range_position_52w=1.35, EV/FCF=1.35, book_value_discount=1.30, and distance_from_52w_high=1.25
- quality NOT rewarded by the market: momentum bias is mildly inverted (+0.82x/−0.62x), so weakness is forgiven without blindly rewarding stagnant names
- robust operating-company floor: ROIC=1.35, FCF margin=1.35, forward EPS growth=1.45, Piotroski F-score=1.35, EV/FCF=1.35, and book_value_discount=1.30
- strong safety to avoid value traps: safety negative at −1.35x

This profile is built for active decisions where you believe the market over-discounted the name.

### If you want rerating or recovery ideas

Use `value_recovery`.

You should expect the leaders to have:

- cheaper valuation with PEG=1.25, earnings_yield=1.25, price_book=1.25, target upside, EV/FCF, and book-value discount
- sequential improvement: QoQ growth dominates YoY (1.20–1.35 vs 0.85–1.10)
- forward recovery conviction: eps_forward_growth=1.50
- entry trigger confirmation: stoch_rsi_crossover=1.35 and short_trend_emergence=1.20
- recent weakness forgiven: momentum negative bias at 0.60x (most forgiving long profile)
- balance-sheet repair: Altman Z=1.25, net_debt=1.20, debt_to_equity=1.20

### If you want aggressive value with a catalyst

Use `deep_value_momentum`.

You should expect the leaders to have:

- valuation support from multiple sources: earnings yield, P/B, EV/EBITDA, EV/FCF, target upside, downside floor, and book value discount
- early technical reversal evidence: stoch_rsi_crossover=1.55 and short_trend_emergence=1.40
- improving operating quality: forward EPS growth, sequential FCF/net income/EBITDA improvement, Piotroski F-score, and sustainable growth
- risk controls: debt/EBITDA, beta-adjusted ATRP, target dispersion, and balance-sheet safety

This is the profile to use when you want value-driven but aggressive positioning: cheap enough to matter, but already starting to move.

### If you want short-fragility candidates

Use `fragility_short`.

You should expect the leaders to have:

- deteriorating safety: safety weight=0.57 at years (highest single-component weight in system)
- all directional biases inverted to amplify negatives: safety bias −1.45x (most aggressive)
- worsening trend and momentum with downside event pressure
- higher beta: beta NOT inverted at 1.15 — higher volatility = more fragile
- weak quality: FCF margin deterioration, declining EPS outlook

## Consensus Aggregator

The consensus aggregator (`run_consensus_aggregator`) runs all 10 profiles against the same scan data and produces a meta-ranking based on cross-profile agreement.

### How it works

1. Each profile scores every name independently using its own weights, biases, and signals
2. Profile scores are collected per ticker per horizon
3. The consensus score is a weighted average across all profiles for each horizon
4. `fragility_short` scores are sign-inverted before aggregation (a bearish fragility call becomes a negative contribution)
5. Names requiring fewer than 3 profiles with valid opinions are excluded
6. Score variance across profiles penalizes inconsistent cross-profile readings

### Profile weights in consensus

| Profile | Consensus weight | Rationale |
| --- | ---: | --- |
| `breakout_long` | 0.20 | Empirically strongest near-term predictor; tactical momentum confirmation |
| `early_momentum_inflection` | 0.12 | Nascent move and pre-breakout detection |
| `forward_edge_active` | 0.11 | Forward-looking quality improvement |
| `quality_value_compounder` | 0.10 | Durable quality and fundamental ballast |
| `durable_value_compounder` | 0.09 | Quality-led value investing for months/years positioning |
| `sector_relative_outperformer` | 0.09 | Efficiency, durability, and relative strength |
| `asymmetric_value` | 0.09 | Mispricing detection with operating-company filters |
| `value_recovery` | 0.08 | Turnaround and rerating with reversal confirmation |
| `fragility_short` | 0.07 | Short-side / hedge signal, sign-inverted in consensus |
| `deep_value_momentum` | 0.05 | Value plus catalyst intersection |

### Confidence calculation

Consensus confidence combines:
- Score magnitude (abs value × 15)
- Agreement ratio (fraction of directional profiles that agree × 30)
- Consistency bonus (lower variance across profiles → up to 10 points)
- Opinion coverage (fraction of profiles contributing × 15)
- Base of 25, clamped to [5, 99]

### Output

The aggregator produces:
- A ranked report per horizon showing consensus score, risk-adjusted score, risk tier, manager action signal, direction, confidence, agreement ratio, and opinions
- A "high conviction" section listing names where 5+ profiles agree on direction with ≥60% agreement
- An active-manager shortlist grouped by action signal (`add_long_breakout`, `accumulate_value_catalyst`, `watch_value_reversal`, `hold_quality_long`, `hedge_or_short`, `avoid_value_trap`)
- A CSV file with consensus scores plus all individual profile scores for further analysis

### When to use

Use `run_consensus_aggregator` when you want the highest-conviction names that are supported by multiple independent lenses. Use `run_full_analysis_suite` to run both the individual profile suite and the consensus aggregator together.

## Similarity Issue Addressed Explicitly

The earlier similarity problem came from two structural effects, not just from superficial weight choices.

### Effect 1: profile inheritance was too broad

`quality_value_compounder` behaved too much like `balanced` on shorter horizons because it inherited the tactical default map.

That is now fixed by giving it its own `days` and `weeks` horizon weights.

### Effect 2: missing important components did not hurt enough

A profile could say it cared about `quality`, `valuation`, or `safety`, but if those fields were absent the horizon score just renormalized around the remaining components.

That is now fixed by per-profile missing-component fallback scores.

Practical consequence:

- `quality_value_compounder` now has to actually see quality, valuation, and safety support
- `value_recovery` now has to actually see rerating and stabilization evidence
- `breakout_long` now has to actually see tactical confirmation
- `fragility_short` now has to actually see enough fragility evidence to deserve ranking highly

## Confidence Controls

Confidence does not change ranking directly, but it changes how much trust the report signals should receive.

Current profile confidence parameters:

| Profile | Confidence multiplier | Confidence offset |
| --- | ---: | ---: |
| `breakout_long` | 1.07 | 0.00 |
| `early_momentum_inflection` | 1.05 | 0.00 |
| `quality_value_compounder` | 1.02 | 0.00 |
| `durable_value_compounder` | 1.04 | 0.00 |
| `sector_relative_outperformer` | 1.02 | 0.00 |
| `forward_edge_active` | 1.03 | 0.00 |
| `asymmetric_value` | 1.02 | 0.00 |
| `value_recovery` | 1.00 | 0.00 |
| `deep_value_momentum` | 1.05 | 0.00 |
| `fragility_short` | 1.07 | 0.00 |

These are secondary to score construction. The main ranking differentiation comes from:

1. horizon weights
2. signal weights
3. directional bias
4. missing-component fallbacks

## Summary

The revised preset logic organizes 10 profiles into three investment ideas with sharply differentiated scoring.

### Trend Following
- `breakout_long` is purely tactical — quality=0.00 at days, momentum bias +1.40x/−0.65x (most aggressive upside), ADX directional spread=1.50, volume_trend=1.40 for confirmation.
- `early_momentum_inflection` catches nascent moves — aroon_spread=1.65 (highest momentum signal), bb_squeeze=1.55, range_compression=1.50 for coiled energy, Perf.Y=0.10 to eliminate established runs.

### Real Quality Identification
- `quality_value_compounder` is fundamentally strict with quality=42% at years, ROIC=1.40, FCF margin=1.35, Piotroski F-score=1.35, attention inverted 0.70x/1.50x, scale explicitly zeroed, and the heaviest missing-quality penalties (−1.30 at years).
- `durable_value_compounder` is quality-led value: quality+valuation=0.67-0.76 across horizons, Piotroski=1.50, ROIC=1.45, EV/FCF=1.55, FCF yield=1.55, Graham gap=1.40, safety negative=1.45x, and scale explicitly zeroed.
- `sector_relative_outperformer` rewards efficiency — revenue_per_employee=1.30, volume_trend=1.50 (highest in system for accumulation), Perf.Y=1.30, quality bias +1.30x/−1.35x.
- `forward_edge_active` is most forward-looking — eps_forward_growth=1.75 (highest single signal), QoQ dominates YoY, short_trend_emergence=1.65, attention most inverted at 0.50x/1.50x.

### Overlooked Fundamentals and Hedging
- `asymmetric_value` is now value-dominant: valuation=0.38-0.40 across horizons, earnings_yield=1.55, EV/FCF=1.50, book_value_discount=1.40, peer_revenue_value_gap=1.35, momentum damped to +0.70x/−0.45x, scale explicitly zeroed, and safety negative at −1.40x for value-trap guard.
- `value_recovery` forgives weakness most (momentum negative at 0.60x), QoQ dominates YoY (1.20–1.35 vs 0.85–1.10), eps_forward_growth=1.50, and stoch_rsi_crossover=1.35 is the recovery trigger.
- `deep_value_momentum` requires both value and reversal confirmation — stoch_rsi_crossover=1.55, short_trend_emergence=1.40, eps_forward_growth=1.55, earnings_yield=1.40, and EV/FCF=1.30.
- `fragility_short` inverts all directional biases to amplify negatives — safety=0.57 at years (highest single weight), safety bias −1.45x (most aggressive), beta NOT inverted.

### Removed profiles
- `balanced` and `backtest_period_ladder` were removed. Backward-compatible aliases map them to `quality_value_compounder` and `sector_relative_outperformer` respectively.

### Consensus aggregator
- `run_consensus_aggregator` runs all 9 profiles and produces a weighted meta-ranking per horizon.
- `fragility_short` scores are sign-inverted before aggregation.
- High-conviction names (5+ profiles agreeing with ≥60% agreement ratio) are highlighted.
- Risk-adjusted scores, risk tiers, and manager action signals are exported in logs and CSVs.
- `run_full_analysis_suite` runs individual profiles plus the consensus aggregator together.

## Extended Signal System (March 30, 2026 — expanded April 5, 2026 and May 2026)

Extended signals were added to the component signal map behind a backward-compatible gating system. The April 5 expansion added seven more signals and eight new raw scan fields. The May 2026 active-manager expansion added analyst target, book-value, EV/cash-flow, Piotroski, debt/EBITDA, volatility-adjusted risk, and Camarilla pivot signals.

### How it works

Signals in the `EXTENDED_SIGNALS` set default to weight `0.00` when a profile does not explicitly override them. This means:

- existing profiles (`balanced`, `breakout_long`, `fragility_short`, `backtest_period_ladder`) continue to behave identically for non-extended signals
- `quality_value_compounder` and `value_recovery` now also activate relevant extended signals (`free_cash_flow_margin_ttm`, `buyback_yield`, `earnings_yield`, `revenue_per_employee`, `earnings_per_share_diluted_yoy_growth_ttm`, `gross_profit_margin_fy`, `pivot_distance`)
- `asymmetric_value` and `early_momentum_inflection` activate the extended signals they need by setting nonzero weights
- `forward_edge_active` activates volume trend, Stoch RSI crossover, intraday momentum, and forward quality signals
- `sector_relative_outperformer` activates all efficiency, capital return, and accumulation signals
- `breakout_long` now activates volume trend and intraday momentum for tactical confirmation
- `deep_value_momentum` activates the value plus catalyst signal stack: target upside/downside, book value discount, EV/FCF, Piotroski F-score, sustainable growth, debt/EBITDA, beta-adjusted ATRP, and Camarilla distances
- any future or custom profile can opt into arbitrary subsets of the extended signals

### Extended signal list

| Signal | Component | Source | Description |
| --- | --- | --- | --- |
| `Perf.6M` | momentum | raw | 6-month price performance |
| `aroon_spread` | momentum | derived (`Aroon.Up - Aroon.Down`) | new trend detection |
| `adx_directional_spread` | momentum | derived (`ADX+DI - ADX-DI`) | directional strength emergence |
| `stoch_rsi_centered` | momentum | derived (`Stoch.RSI.K - 50`) | short-term momentum impulse |
| `CCI20` | momentum | raw | directional thrust |
| `stoch_rsi_crossover` | momentum | derived (`Stoch.RSI.K - Stoch.RSI.D`) | bullish Stoch RSI crossover signal |
| `bb_position` | trend | derived (`(close - BB.lower) / (BB.upper - BB.lower)`) | position within Bollinger Bands |
| `close_vs_sma10` | trend | derived (`+1/-1`) | above/below 10-day SMA |
| `close_vs_sma20` | trend | derived (`+1/-1`) | above/below 20-day SMA |
| `close_vs_sma30` | trend | derived (`+1/-1`) | above/below 30-day SMA |
| `close_vs_ema10` | trend | derived (`+1/-1`) | above/below 10-day EMA |
| `close_vs_ema20` | trend | derived (`+1/-1`) | above/below 20-day EMA |
| `close_vs_ema30` | trend | derived (`+1/-1`) | above/below 30-day EMA |
| `short_trend_emergence` | trend | derived (avg of 6 short-term MAs) | early trend forming |
| `pivot_distance` | trend | derived (`(close - Pivot.M.Classic.Middle) / close`) | stock above monthly pivot = support confirmation |
| `close_vs_camarilla_s1` | trend | derived (`(close - Pivot.M.Camarilla.S1) / close`) | distance above monthly Camarilla support S1 |
| `close_vs_camarilla_s2` | trend | derived (`(close - Pivot.M.Camarilla.S2) / close`) | distance above deeper monthly Camarilla support S2 |
| `close_vs_camarilla_r1` | trend | derived (`(close - Pivot.M.Camarilla.R1) / close`) | distance above monthly Camarilla resistance R1 |
| `close_vs_camarilla_r2` | trend | derived (`(close - Pivot.M.Camarilla.R2) / close`) | distance above stronger monthly Camarilla resistance R2 |
| `bb_squeeze` | attention | derived (`(BB.upper - BB.lower) / close`, inverted) | band compression = coiled energy |
| `range_compression` | attention | derived (`(High.3M - Low.3M) / close`, inverted) | coiled 3-month range |
| `volatility_contraction` | attention | derived (`Volatility.D / Volatility.M`, inverted) | daily-vs-monthly vol contraction |
| `volume_trend` | attention | derived (`average_volume_10d_calc / average_volume_30d_calc`) | rising short-term volume = accumulation |
| `intraday_momentum` | attention | raw `change_from_open` | open-to-current momentum confirmation |
| `earnings_yield` | valuation | raw | earnings-to-price yield (higher = cheaper) |
| `distance_from_52w_high` | valuation | derived (`(close - 52w_high) / abs(52w_high)`, inverted) | discount from peak |
| `range_position_52w` | valuation | derived (`(close - 52w_low) / (52w_high - 52w_low)`, inverted) | position in 52-week range |
| `enterprise_value_to_free_cash_flow_ttm` | valuation | raw, inverted positive-only | EV/FCF cash-flow value |
| `enterprise_value_to_gross_profit_ttm` | valuation | raw, inverted positive-only | EV/gross-profit value |
| `price_target_upside_average` | valuation | derived (`(price_target_average - close) / close`) | average analyst target upside |
| `price_target_upside_median` | valuation | derived (`(price_target_median - close) / close`) | median analyst target upside |
| `price_target_downside_floor` | valuation | derived (`(price_target_low - close) / close`) | lowest-target downside floor |
| `book_value_discount` | valuation | derived (`(book_value_per_share_fq - close) / close`) | discount to book value |
| `free_cash_flow_margin_ttm` | quality | raw | cash generation quality |
| `buyback_yield` | quality | raw | shareholder return via buybacks |
| `dividends_yield_current` | quality | raw | income return |
| `eps_forward_growth` | quality | derived (`(forecast_next_fq - actual_fq) / abs(actual_fq)`) | forward EPS improvement |
| `revenue_per_employee` | quality | derived (`total_revenue / number_of_employees`) | operational efficiency per head |
| `earnings_per_share_diluted_yoy_growth_ttm` | quality | raw | diluted EPS growth trajectory |
| `gross_profit_margin_fy` | quality | raw | annual gross margin stability |
| `sustainable_growth_rate_ttm` | quality | raw | internally sustainable growth |
| `piotroski_f_score_ttm` | quality | raw | accounting quality and financial strength |
| `dividend_yield_recent` | quality | raw | recent dividend yield |
| `dps_common_stock_prim_issue_yoy_growth_fy` | quality | raw | dividend per share growth |
| `total_debt_to_ebitda_fq` | safety | raw, inverted positive-only | leverage burden vs EBITDA |
| `beta_adjusted_atrp` | safety | derived (`ATRP * beta_1_year`) | volatility adjusted by beta |
| `price_target_dispersion` | safety | derived (`(price_target_high - price_target_low) / close`, inverted positive-only) | analyst disagreement / uncertainty |
| `eps_surprise_percent_fq` | event | raw | earnings surprise history |

### New raw fields added to scan payload (April 5, 2026 and May 2026)

The following columns were added to the TradingView scan payload (`GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`):

`average_volume_30d_calc`, `Stoch.RSI.D`, `change_from_open`, `earnings_per_share_diluted_yoy_growth_ttm`, `ebitda`, `net_income`, `Pivot.M.Classic.Middle`, `gross_profit_margin_fy`

May 2026 active-manager expansion:

`price_target_average`, `price_target_median`, `price_target_high`, `price_target_low`, `price_target_1y`, `book_value_per_share_fq`, `sustainable_growth_rate_ttm`, `piotroski_f_score_ttm`, `dividend_yield_recent`, `dividends_per_share_fq`, `dps_common_stock_prim_issue_yoy_growth_fy`, `enterprise_value_to_free_cash_flow_ttm`, `enterprise_value_to_gross_profit_ttm`, `total_debt_to_ebitda_fq`, `Pivot.M.Camarilla.S1`, `Pivot.M.Camarilla.S2`, `Pivot.M.Camarilla.R1`, `Pivot.M.Camarilla.R2`, `type`, `typespecs`

These were added for:

- **Volume accumulation detection**: `average_volume_30d_calc` enables the `volume_trend` derived signal (10d/30d ratio). Rising ratio = sustained accumulation vs one-day spikes.
- **Stoch RSI crossover**: `Stoch.RSI.D` enables the `stoch_rsi_crossover` derived signal (K minus D). Positive crossover = bullish momentum inflection.
- **Intraday momentum**: `change_from_open` directly measures open-to-current momentum for real-time confirmation.
- **Diluted EPS growth**: `earnings_per_share_diluted_yoy_growth_ttm` adds diluted (not basic) EPS growth for quality assessment. More conservative than basic EPS.
- **Raw financials**: `ebitda` and `net_income` added for future derived ratios and efficiency calculations.
- **Monthly pivot point**: `Pivot.M.Classic.Middle` enables `pivot_distance` for support/resistance confirmation.
- **Gross margin stability**: `gross_profit_margin_fy` adds annual gross profit margin as a quality floor signal.
- **Forward value anchors**: analyst target upside/downside and dispersion add a street-implied fair-value layer.
- **Operating-company filters**: Piotroski F-score, sustainable growth, debt/EBITDA, EV/FCF, and EV/gross-profit help distinguish real operating companies from passive vehicles with optically cheap multiples.
- **Risk-adjusted volatility**: `beta_adjusted_atrp` penalizes volatile names where high beta and high ATRP combine.
- **Camarilla pivot distances**: monthly support/resistance distances provide short-horizon entry context for reversal/value-catalyst profiles.

## Anti-Concentration Tuning (March 30, 2026)

The March 30 run revealed a concentration bias toward Investment Trusts / Mutual Fund industry names across multiple profiles. These entities score well on valuation (structurally low P/E, P/B, P/S), safety (low debt, low beta), and quality (stable margins) because their financial structure is fundamentally different from operating companies. When the fundamental-heavy profiles gave near-zero weight to attention, this structural advantage went unchecked.

### Root causes identified

1. **Valuation component uses `positive_only=True`**: Investment trusts always have positive multiples (low P/E, low P/B). Many growth/tech companies have negative P/E and are excluded from valuation scoring. This means trusts are always evaluated on valuation while many peers are skipped.

2. **Safety advantage is structural**: Trusts typically have low/zero debt, high cash ratios, and low beta. The inverted safety signals (lower = better for debt, beta) give them consistently strong safety scores.

3. **Near-zero attention weight**: Profiles like `quality_value_compounder` (0.03-0.00), `value_recovery` (0.05-0.00), and `asymmetric_value` (0.02-0.00) gave almost no weight to attention. Low-volume passive vehicles were not penalized for lack of market engagement.

4. **MAD normalization is correct but industry-blind**: The scan-relative robust normalization (MAD-based) is working as designed, but it normalizes across the entire scan universe including structurally different entity types. Trusts with stable financials cluster in favorable territory for fundamental components.

### Changes applied

1. **Raised attention weight** in `quality_value_compounder`, `value_recovery`, and `asymmetric_value` (from 0.00-0.03 to 0.02-0.07). Compensated by reducing quality or valuation weights proportionally. All horizon weight sums still equal 1.00.

2. **Added attention directional bias** (`positive_multiplier < 1.0`, `negative_multiplier > 1.0`): negative attention signals are now amplified by 30-40%, so low-volume names face a real drag. Positive attention is dampened because these profiles do not reward volume leadership — they only care that names are not completely inactive.

3. **Activated extended quality signals** in `quality_value_compounder` (`free_cash_flow_margin_ttm=1.15`, `buyback_yield=0.85`) and `value_recovery` (`free_cash_flow_margin_ttm=1.10`). FCF margin rewards operating businesses with real cash conversion. Buyback yield rewards companies that return capital through share repurchases — most investment trusts do not execute buyback programs.

4. **Activated extended valuation signal** in `value_recovery` (`earnings_yield=1.15`). Earnings yield directly measures how cheap a stock is on an earnings basis, adding another dimension beyond multiple-based inversion.

5. **Added missing-component fallbacks for attention** across `quality_value_compounder`, `value_recovery`, `asymmetric_value`, `balanced`, and `backtest_period_ladder`. Names with missing activity data are now penalized instead of silently skipped during renormalization.

6. **Tightened momentum forgiveness** in `quality_value_compounder` (negative_multiplier changed from 0.85 to 0.95). The previous value was too lenient and allowed low-momentum passive vehicles to score well.

### Expected effect

Investment trusts and closed-end funds will still score well on individual fundamental components (quality, valuation, safety) because the MAD normalization correctly identifies their relative strength. However, the combination of higher attention weight, amplified negative attention bias, and extended quality signals should reduce their overall ranking dominance by:

- introducing a structural drag from low attention scores (amplified by 1.30-1.40x)
- requiring evidence of operating cash generation via FCF margin
- penalizing missing activity data through fallback scores

This is a tuning adjustment, not a hard filter. Trusts with genuinely strong engagement and operating characteristics can still rank well.

## Forward-Edge Active Profile And New Signal Infrastructure (March 31, 2026)

### Problem observed

The CF Industries example (flagged on March 30 leaderboard, dropped significantly on March 31 and no longer anywhere near the list) exposed a systematic bias in `breakout_long`, `quality_value_compounder`, `asymmetric_value`, and `early_momentum_inflection`. The profiles catch names "in the making" or "too late" because:

1. **Trailing momentum dominance**: All profiles heavily weight Perf.W through Perf.Y. These are backward-looking — they measure what already happened, not what is about to happen.
2. **No forward-looking fundamental signals**: The scan already fetches `earnings_per_share_forecast_next_fq` and `earnings_per_share_fq` but these were not used in any component scoring.
3. **No anticipatory technical signals**: Short-term MAs (SMA10/20/30, EMA10/20/30), Stoch.RSI.K, and CCI20 were fetched but not wired into the signal map. No volatility contraction or range compression detection existed.
4. **No mean-reversion or exhaustion detection**: Current momentum signals are all trend-following with no mechanism to detect overextension.

### New signals implemented

Seven new derived metrics and three new raw-field signals were added to the infrastructure. All are registered as extended signals (default weight 0.00) so existing profiles are unaffected unless they explicitly activate them.

#### Trend component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `close_vs_sma10` | derived | `+1` if `close >= SMA10`, else `-1` | above shortest-term average |
| `close_vs_sma20` | derived | `+1` if `close >= SMA20`, else `-1` | above 20-day average |
| `close_vs_sma30` | derived | `+1` if `close >= SMA30`, else `-1` | above 30-day average |
| `close_vs_ema10` | derived | `+1` if `close >= EMA10`, else `-1` | above 10-day EMA |
| `close_vs_ema20` | derived | `+1` if `close >= EMA20`, else `-1` | above 20-day EMA |
| `close_vs_ema30` | derived | `+1` if `close >= EMA30`, else `-1` | above 30-day EMA |
| `short_trend_emergence` | derived | average of the six short-term MA comparisons above | more short-term MAs crossed above = early trend forming |

#### Attention component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `range_compression` | derived | `(High.3M - Low.3M) / close`, inverted | tighter 3-month range = more coiled energy |
| `volatility_contraction` | derived | `Volatility.D / Volatility.M`, inverted | daily vol lower than monthly = contraction / coiling |

#### Momentum component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `stoch_rsi_centered` | derived | `Stoch.RSI.K - 50` | stronger short-term momentum impulse |
| `CCI20` | raw field | robust normalized | stronger directional thrust |

#### Quality component addition

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `eps_forward_growth` | derived | `(forecast_next_fq - actual_fq) / abs(actual_fq)` | analysts expect EPS improvement next quarter |

#### Event component addition

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `eps_surprise_percent_fq` | raw field | robust normalized | last quarter beat consensus = positive surprise history |

### Scan payload changes

Added to `GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`:
- `eps_surprise_percent_fq` — most recent quarter earnings surprise percentage
- `earnings_per_share_forecast_next_fy` — next fiscal year EPS forecast

Changed `ignore_unknown_fields` to `True` to handle fields that may not be available for all global markets.

### `forward_edge_active` profile design

This profile targets risk-adjusted outperformance on the weeks-to-months timescale as an early catcher.

#### Core design principles

1. **Trend leads momentum**: The `trend` component (featuring short-term MA alignment) carries the most weight across days and weeks horizons. A stock crossing above its 10/20/30-day MAs is an earlier signal than waiting for Perf.W or Perf.1M to confirm.

2. **Trailing performance heavily damped**: `Perf.Y` weight is 0.10 (vs 1.00 default), `Perf.YTD` is 0.25, `Perf.6M` is 0.40. This prevents names already well into multi-month runs from dominating.

3. **Forward-looking quality emphasis**: `eps_forward_growth` (1.50 weight) and QoQ growth metrics (1.20-1.25 weight) are prioritized over trailing annual metrics. This shifts the quality lens toward what is changing rather than what was.

4. **Attention inversion**: Positive attention is damped (0.65x multiplier) while negative attention is amplified (1.30x). Low-attention names with early technical confirmation get the forward edge. High-attention names face a natural drag because crowded trades have less forward upside.

5. **Coiled-energy detection**: `bb_squeeze` (1.50), `range_compression` (1.40), and `volatility_contraction` (1.35) in the attention component detect names whose price action is compressing — a precursor to directional moves.

6. **Safety guardrails**: Safety weight increases from 0.12 (days) to 0.26 (years). Missing quality or safety data produces penalty fallback scores (-0.50 to -0.85 depending on horizon). This prevents catching falling knives.

7. **Earnings surprise as event signal**: `eps_surprise_percent_fq` (1.40 weight) in the event component rewards companies that consistently beat consensus. Persistent beaters tend to outperform in subsequent quarters.

#### Horizon weight table

| Component | Days | Weeks | Months | Years |
| --- | ---: | ---: | ---: | ---: |
| attention | 0.08 | 0.06 | 0.04 | 0.02 |
| event | 0.10 | 0.08 | 0.05 | 0.02 |
| momentum | 0.18 | 0.16 | 0.12 | 0.06 |
| trend | 0.26 | 0.24 | 0.20 | 0.14 |
| quality | 0.16 | 0.20 | 0.24 | 0.28 |
| valuation | 0.10 | 0.12 | 0.16 | 0.22 |
| safety | 0.12 | 0.14 | 0.19 | 0.26 |

### Suggestions for further improvement (not yet implemented)

These are data-layer and scan-level enhancements that could further improve forward-edge detection and outperformance identification. They require either new TradingView fields, external data sources, or architectural changes.

**Previously suggested items now implemented:**

- ~~Multi-timeframe relative volume~~: Implemented as `volume_trend` (10d/30d avg volume ratio) using `average_volume_30d_calc`. Detects sustained accumulation vs one-day spikes. Activated in `breakout_long`, `early_momentum_inflection`, `forward_edge_active`, and `sector_relative_outperformer`.
- ~~Revenue estimate revisions~~: Partially addressed. `earnings_per_share_diluted_yoy_growth_ttm` now added as a diluted EPS growth signal. `eps_forward_growth` (forecast vs actual EPS) was already implemented. True revenue estimate revision tracking would require historical estimate snapshots not available from TradingView scan.
- ~~Analyst price target delta~~: Implemented through derived `price_target_upside_average`, `price_target_upside_median`, `price_target_downside_floor`, and `price_target_dispersion` using fetched price target fields.

**Remaining suggested items:**

1. **Insider and institutional flow signals**: Fields like institutional ownership changes and insider buying/selling are not currently available from TradingView scan but could be sourced from SEC Form 4 filings (already available in the EDGAR database). High insider buying + low attention + emerging trend = strongest forward edge.

2. **Industry-relative scoring**: The current model normalizes against the entire scan universe. Computing signals relative to industry peers (e.g., "this stock's momentum rank within its industry") would separate genuine sector-relative leadership from broad market moves. The `sector_relative_outperformer` profile partially addresses this through efficiency metrics (revenue per employee) but does not yet normalize against peer groups.

3. **Earnings calendar proximity weighting**: Stocks approaching earnings within 2-4 weeks often exhibit pre-earnings drift (positive historical surprise -> upward drift). The model currently penalizes confidence near earnings but doesn't reward the drift pattern.

4. **Short interest ratio**: Not available in standard TradingView scan but could be sourced from FINRA/exchange data. High short interest + positive momentum inflection = short squeeze candidate.

5. **Options-implied volatility**: IV percentile or IV rank could replace or supplement Bollinger Band squeeze as a more market-informed volatility contraction measure. Not available from TradingView scan.

**New suggestions (April 5, 2026):**

7. ~~**Donchian Channel breakout detection**~~: **Implemented** as `donchian_position` in June 2026. Activated in `breakout_long`, `early_momentum_inflection`, and exhaustion/rotation profiles.

8. ~~**Parabolic SAR trend confirmation**~~: **Implemented** as `close_vs_psar` (`+1/-1`). Activated in breakout and inflection profiles.

9. ~~**Hull Moving Average crossover**~~: **Implemented** as `close_vs_hullma9` (`+1/-1`). Activated in inflection, forward-edge, pre-earnings, and sector-rotation profiles.

10. **Ichimoku Cloud positioning**: `Ichimoku.Lead1` and `Ichimoku.Lead2` are available. A composite signal based on whether price is above/below the cloud and whether the cloud is bullish (Lead1 > Lead2) would add a powerful multi-factor trend confirmation. Particularly useful for `sector_relative_outperformer` and `quality_value_compounder` on longer horizons.

11. ~~**Chaikin Money Flow as attention signal**~~: **Implemented** as `chaikin_money_flow_signal`. Activated in `breakout_long`, `sector_relative_outperformer`, and `sector_rotation_momentum`.

12. ~~**Bull/Bear Power divergence**~~: **Implemented** as `bbpower_divergence`. Activated in `early_momentum_inflection`, `value_recovery`, and `mean_reversion_exhaustion`.

13. **Revenue efficiency composite**: Combine `revenue_per_employee` with a new `ebitda_per_employee` (derived from `ebitda / number_of_employees`) to create a composite operational efficiency score. This would separate companies that generate high revenue per head AND retain most of it as operating profit from distribution/logistics companies that have high revenue per head but thin margins.

14. ~~**Cross-profile consensus layer**~~: **Implemented** as `run_consensus_aggregator`. Runs all 9 profiles against the same scan data, produces weighted consensus scores per horizon, identifies high-conviction names where 5+ profiles agree on direction with ≥60% agreement ratio. Output includes ranked report per horizon plus CSV with all individual profile scores, risk-adjusted scores, risk tiers, and manager action signals.

15. **Temporal stability scoring**: Track whether a name's component scores are stable across consecutive runs (requires storing historical scores). A name that consistently scores well across multiple days has more reliable support than a one-day spike. This would be a confidence multiplier rather than a ranking signal.

## May 2026 Practical Addendum — Profile Coverage And Relativity

This section is intentionally short and operational. It answers two recurring questions:

1. Which fields are actually active per profile (coverage)?
2. Are scores in `run_full_analysis_suite` relative to peers, broad market, or both?

### A. Profile coverage footprint (active non-zero signals)

The suite currently has **159 total candidate signals** across all components. A signal is counted as "active" for a profile when its effective weight is strictly greater than `0.0`.

| Profile | Attention | Event | Momentum | Trend | Quality | Valuation | Safety | Scale | Total active |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `breakout_long` | 10/10 | 6/6 | 21/21 | 16/20 | 14/39 | 9/33 | 9/24 | 6/6 | 91/159 |
| `early_momentum_inflection` | 10/10 | 6/6 | 21/21 | 16/20 | 14/39 | 9/33 | 9/24 | 6/6 | 91/159 |
| `quality_value_compounder` | 5/10 | 5/6 | 16/21 | 8/20 | 25/39 | 14/33 | 12/24 | 6/6 | 91/159 |
| `durable_value_compounder` | 6/10 | 5/6 | 16/21 | 12/20 | 39/39 | 31/33 | 24/24 | 6/6 | 139/159 |
| `sector_relative_outperformer` | 7/10 | 5/6 | 16/21 | 8/20 | 21/39 | 10/33 | 9/24 | 6/6 | 82/159 |
| `forward_edge_active` | 10/10 | 6/6 | 21/21 | 16/20 | 21/39 | 12/33 | 9/24 | 6/6 | 101/159 |
| `asymmetric_value` | 5/10 | 5/6 | 18/21 | 9/20 | 25/39 | 19/33 | 12/24 | 6/6 | 99/159 |
| `value_recovery` | 5/10 | 5/6 | 17/21 | 9/20 | 24/39 | 18/33 | 12/24 | 6/6 | 96/159 |
| `deep_value_momentum` | 7/10 | 5/6 | 21/21 | 18/20 | 24/39 | 18/33 | 12/24 | 6/6 | 111/159 |
| `fragility_short` | 7/10 | 6/6 | 20/21 | 13/20 | 19/39 | 13/33 | 11/24 | 6/6 | 95/159 |

How to read this quickly:

- `durable_value_compounder` is the broadest fundamental profile (very high quality/valuation/safety coverage).
- `sector_relative_outperformer` is intentionally selective (lowest total active footprint).
- `breakout_long` and `early_momentum_inflection` are tactical: they fully activate attention/event/momentum while keeping many deep-fundamental signals inactive.

For exact field weights per profile, use the per-profile sections above:

- "Signal weight overrides" gives every explicit override.
- Any field not listed there uses default logic (`1.0` for standard signals, `0.0` for extended signals).

### B. What `run_full_analysis_suite` is relative to

`run_full_analysis_suite` is **universe-relative** by design.

For each run, robust signals are normalized from the rows passed into that run:

1. Per-field median and MAD are computed on the provided `scan_data` only.
2. Raw/derived values become robust signals relative to that run's distribution.
3. Component and horizon scores are built from those run-relative signals.

Implication: if you pass all stocks, each score is relative to the all-stock universe in that run. If you pass one industry, each score is relative to that industry's peers in that run.

### C. Where true peer-relative behavior exists today

Current peer-relative signals already present:

- `_peer_market_cap_share` and `_peer_revenue_share` are computed from the **current run universe totals**.
- `peer_revenue_value_gap = _peer_revenue_share - _peer_market_cap_share` is therefore relative to the names in that run.

This means peer-relative valuation behavior is strongest when the run universe is already a coherent peer set (industry/sector scope).

### D. Should you run all stocks or per-industry?

Use this rule:

- **All-stocks scan**: best for global opportunity discovery and broad ranking.
- **Per-industry scan**: best when you want strict comparability among business-model peers.

Recommended workflow for production:

1. Run broad `run_full_analysis_suite` for discovery.
2. Re-run shortlisted sectors with `run_full_analysis_suite_by_industry` for peer-pure ranking.
3. Use `_cross_industry_aggregate` output to compare top names across sector winners.

If your objective is explicitly "best relative to peers", prefer the per-industry pathway as the primary ranking layer.