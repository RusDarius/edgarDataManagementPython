# Active Manager Implementation & Usage Guide

June 2026 implementation of [active_manager_profile_and_pattern_discovery_plan.md](improvements_plans/active_manager_profile_and_pattern_discovery_plan.md).

## What shipped

| Layer | Location | Scope |
| --- | --- | --- |
| Tier-1 signals + profile tuning | `src/data_analysis_scripts/trading_view_move_prediction_analysis.py` | 7 new derived signals, 6 new profiles, consensus rebalance, manager-action upgrades |
| API payload fields | `src/data_loaders/api_tradingview_client.py` | Donchian, PSAR, Hull MA, CMF, BBPower, weekly Recommend.All |
| Pattern discovery | `src/data_analysis_scripts/trading_view_move_prediction_pattern_discovery.py` | Join A+B+C databases, calibration exports |
| SQL prototypes | `sql_connections_space/move_prediction_pattern_discovery.session.sql` | DBCode-ready `@block` queries |
| Profile reference | [tradingview_move_prediction_profile_weighting_reference.md](tradingview_move_prediction_profile_weighting_reference.md) | Signal tables + new profile notes |
| Pattern reference | [tradingview_move_prediction_pattern_discovery_reference.md](tradingview_move_prediction_pattern_discovery_reference.md) | Discovery methods + weekly workflow |

## New derived signals

| Signal | Formula | Default weight | Activated in |
| --- | --- | ---: | --- |
| `donchian_position` | `(close - DonchCh20.Lower) / (Upper - Lower)` | 0.0 (extended) | `breakout_long`, `early_momentum_inflection`, `mean_reversion_exhaustion`, `sector_rotation_momentum` |
| `close_vs_psar` | `+1` if `close >= P.SAR`, else `-1` | 0.0 | `breakout_long`, `early_momentum_inflection`, `mean_reversion_exhaustion` |
| `close_vs_hullma9` | `+1` if `close >= HullMA9`, else `-1` | 0.0 | `early_momentum_inflection`, `forward_edge_active`, `pre_earnings_drift`, `sector_rotation_momentum` |
| `chaikin_money_flow_signal` | robust-normalized `ChaikinMoneyFlow` | 0.0 | `breakout_long`, `sector_relative_outperformer`, `sector_rotation_momentum` |
| `bbpower_divergence` | robust-normalized `BBPower` | 0.0 | `early_momentum_inflection`, `value_recovery`, `mean_reversion_exhaustion` |
| `recommend_tf_spread` | `Recommend.All\|1W - Recommend.All` | 0.0 | `forward_edge_active`, `mean_reversion_exhaustion`, `quality_growth_at_reasonable_price` |
| `ebitda_per_employee` | `ebitda / number_of_employees` | 0.0 | `durable_value_compounder`, `sector_relative_outperformer`, `sector_rotation_momentum` |

Extended signals default to **0.0** unless a profile sets an explicit override.

## New profiles (16 total)

| Profile | Sleeve | Typical `manager_action_signal` |
| --- | --- | --- |
| `income_compounder` | Core income | `hold_quality_long` |
| `pre_earnings_drift` | Tactical catalyst | `accumulate_value_catalyst` |
| `mean_reversion_exhaustion` | Risk overlay (consensus-inverted) | `trim_extended_long` |
| `defensive_fortress` | Defensive | `hold_quality_long` |
| `sector_rotation_momentum` | Tactical rotation | `add_long_breakout` |
| `quality_growth_at_reasonable_price` | Core GARP | `hold_quality_long` / `accumulate_value_catalyst` |

`mean_reversion_exhaustion` and `fragility_short` are **sign-inverted** in consensus aggregation.

## Weekly workflow

```text
Monday    run_full_analysis_suite_duckdb (+ earnings-priority if catalyst week)
Monday    export_all_tradingview_fields_duckdb
Tuesday   run_investment_shortlist_pipeline
Wednesday run_move_prediction_history_aggregation_duckdb (last 4–8 weeks)
Thursday  run_pattern_discovery_suite + SQL calibration blocks
Friday    Promote 0–2 rules; size per position_sizing_distribution_framework.md
```

### Run analysis (profiles + consensus)

```python
from data_loaders.api_tradingview_client import ApiTradingViewClient
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)

client = ApiTradingViewClient()
scan_data = client.scan_global_market_move_prediction(min_market_cap_usd=1_000_000_000)

result = run_full_analysis_suite_duckdb(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
)
```

`DEFAULT_MOVE_PREDICTION_PROFILE_SUITE` now includes all **16** profiles. Consensus weights sum to **1.00** — see weighting reference for the table.

### Run pattern discovery (Thursday research)

```python
from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    run_pattern_discovery_suite,
)

exports = run_pattern_discovery_suite(profile_name="breakout_long")
print(exports["overview_log"])
```

Auto-discovers the latest:
- `move_prediction_*.duckdb` under `prediction_analysis/duckdb_runs/`
- `tradingview_all_fields_*.duckdb` under `trading_view_all_fields_data/`
- `historical_prediction_analysis.duckdb` under `historical_prediction_analysis/`

Writes CSVs under `<weekly_db>/pattern_discovery/<timestamp>/`.

## Manager action signal changes

| Signal | When emitted | Notes |
| --- | --- | --- |
| `promote_to_breakout` | `early_momentum_inflection` only | Breakout weeks > inflection weeks, `volume_trend > 1.05`, bullish ADX spread |
| `trim_extended_long` | `mean_reversion_exhaustion` | Weeks score ≥ 0.55 with extended momentum |
| `hedge_or_short` | Consensus / `fragility_short` | Requires ≥2 bullish long-profile disagreements at consensus level |

## Capital sleeve mapping (quick)

| Sleeve | Profiles |
| --- | --- |
| Tactical 15–25% | `breakout_long`, `early_momentum_inflection`, `pre_earnings_drift`, `sector_rotation_momentum` |
| Core 40–55% | `quality_value_compounder`, `durable_value_compounder`, `sector_relative_outperformer`, `income_compounder`, `quality_growth_at_reasonable_price` |
| Opportunistic value 15–25% | `asymmetric_value`, `value_recovery`, `deep_value_momentum` |
| Hedge / defensive 5–15% | `fragility_short`, `defensive_fortress`, `mean_reversion_exhaustion` (overlay) |

## Tests

```powershell
$env:PYTHONPATH="D:\FinanceProjects\edgarDataManagementPython\src"
python -m pytest src/tests/test_trading_view_move_prediction_analysis.py `
  src/tests/test_trading_view_move_prediction_pattern_discovery.py -q
```

## Related docs

- [Profile weighting reference](tradingview_move_prediction_profile_weighting_reference.md)
- [Pattern discovery reference](tradingview_move_prediction_pattern_discovery_reference.md)
- [Implementation todos](improvements_plans/implementation_todos.md)
