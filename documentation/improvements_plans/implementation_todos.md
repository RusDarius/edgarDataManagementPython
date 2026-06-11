# Implementation Todos — Active Manager Plan

Prioritized backlog from [active_manager_profile_and_pattern_discovery_plan.md](active_manager_profile_and_pattern_discovery_plan.md).  
Ordered for a retail active manager running a **$100k+** book: process first, low-risk signal additions second, new profiles last.

**June 2026 code delivery:** P2 (Tier-1 signals), P3 (profile tuning), P4 (six new profiles), and P5 (pattern discovery module + SQL) are implemented. See [active_manager_implementation_usage.md](../active_manager_implementation_usage.md).

---

## Phase 0 — Weekly process (no code, start immediately)

| ID | Task | Owner | Done when |
|----|------|-------|-----------|
| P0-1 | Adopt capital sleeve budget: tactical 15–25%, core 40–55%, value 15–25%, hedge 5–15%, cash remainder | PM | Written in personal playbook |
| P0-2 | Run `run_full_analysis_suite_duckdb` every Monday | PM | Weekly DuckDB under `prediction_analysis/duckdb_runs/` |
| P0-3 | Run `export_all_tradingview_fields_duckdb` once per week for research | PM | Daily `tradingview_all_fields_*.duckdb` exists |
| P0-4 | Run `run_investment_shortlist_pipeline` after each analysis run | PM | Shortlist CSVs in `runs/<run_id>/investment_shortlists/` |
| P0-5 | Aggregate last 4–8 weeks via `run_move_prediction_history_aggregation_duckdb` | PM | `historical_prediction_analysis.duckdb` updated |
| P0-6 | Review `q_hist_profile_calibration`, `q_hist_persistent_winners`, `q_hist_false_positives` | PM | Friday review log entry |
| P0-7 | Size positions per [position_sizing_distribution_framework.md](../position_sizing_distribution_framework.md) | PM | No position > 8–12% without liquidity pass |

---

## Phase 1 — Shortlist & SQL gates (low code risk)

| ID | Task | File(s) | Priority |
|----|------|---------|----------|
| P1-1 | Add `type = 'stock'` hard filter to all shortlist exports | `trading_view_investment_shortlist_pipeline.py` | High |
| P1-2 | Add liquidity gate: reject if intended position > 5% of `AvgValue.Traded_10d` | shortlist pipeline + SQL | High |
| P1-3 | Add `q_breakout_long_false_breakout_screen` to automated shortlist reject pass | `investment_opportunity_queries.session.sql` | High |
| P1-4 | Add `q_consensus_avoid_traps` output to `avoid_traps` CSV enrichment | shortlist pipeline | High |
| P1-5 | Codify inflection→breakout handoff query (`q_early_inflection_vs_breakout_handoff`) as weekly promotion list | SQL session + shortlist | Medium |
| P1-6 | Add DVC vs QVC correlation check: flag when months scores correlate > 0.8 | new SQL `@block` | Medium |
| P1-7 | Add post-breakout exhaustion screen: `Perf.1M` top decile + RSI > 70 + `volume_trend` falling | new SQL `@block` | Medium |
| P1-8 | Wire `q_hist_snapshot_mismatch` leaders into Friday trim list | manual SQL → watchlist | Medium |

---

## Phase 2 — New derived signals (catalog fields already available)

| ID | Signal | Formula / logic | Target profiles | File |
|----|--------|-----------------|-----------------|------|
| P2-1 | `donchian_position` | `(close - DonchCh20.Lower) / (DonchCh20.Upper - DonchCh20.Lower)` | `breakout_long`, `early_momentum_inflection` | `trading_view_move_prediction_analysis.py` |
| P2-2 | `close_vs_psar` | `+1` if `close >= P.SAR`, else `-1` | `breakout_long`, `early_momentum_inflection` | same |
| P2-3 | `close_vs_hullma9` | `+1` if `close >= HullMA9`, else `-1` | `early_momentum_inflection`, `forward_edge_active` | same |
| P2-4 | `chaikin_money_flow_signal` | robust normalize `ChaikinMoneyFlow` | `breakout_long`, `sector_relative_outperformer` | same |
| P2-5 | `bbpower_divergence` | robust normalize `BBPower` (inverted for exhaustion) | `early_momentum_inflection`, `value_recovery` | same |
| P2-6 | `recommend_tf_spread` | `Recommend.All|1W` minus `Recommend.All` | `forward_edge_active`, `mean_reversion_exhaustion` (new) | same |
| P2-7 | `ebitda_per_employee` | `ebitda / number_of_employees` | `sector_relative_outperformer`, `durable_value_compounder` | same |
| P2-8 | Add all new signals to `EXTENDED_SIGNALS` with default weight `0.0` | — | — | same |
| P2-9 | Add fields to `GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD` if not already fetched | Donchian, Hull, CMF, BBPower, P.SAR | — | same |
| P2-10 | Update [profile weighting reference](../tradingview_move_prediction_profile_weighting_reference.md) | — | — | documentation |

---

## Phase 3 — Existing profile tuning

| ID | Profile | Change | Rationale |
|----|---------|--------|-----------|
| P3-1 | `breakout_long` | Activate P2-1, P2-2, P2-4; dampen when RSI/Stoch extended + high `price_target_dispersion` | Reduce false breakouts |
| P3-2 | `early_momentum_inflection` | Activate P2-3, P2-5; add handoff note in `manager_action_signal` when breakout overtakes | Coiled → confirmed promotion |
| P3-3 | `forward_edge_active` | Boost pre-earnings drift weighting; pair with earnings-priority suite | Catalyst week edge |
| P3-4 | `durable_value_compounder` | Require min FCF yield / EV/FCF evidence via missing-component fallbacks | Separate from QVC |
| P3-5 | `quality_value_compounder` | Keep premium tolerance; add explicit scale=0 confirmation in docs/tests | Anti size-proxy |
| P3-6 | `asymmetric_value` / `value_recovery` / `deep_value_momentum` | Rebalance consensus weights after 4-week alignment review | De-cluster value sleeve |
| P3-7 | `fragility_short` | Require ≥2 long-profile disagreement before `hedge_or_short` label | Hedge overlay only |
| P3-8 | All profiles | Re-run overlap audit (top-10 Jaccard per horizon) after tuning | Validate differentiation |

---

## Phase 4 — New profiles (after Phase 2–3 evidence)

| ID | Profile | Sleeve | Depends on |
|----|---------|--------|------------|
| P4-1 | `income_compounder` | Core 10–20% | Dividend coverage signals in catalog |
| P4-2 | `pre_earnings_drift` | Tactical 5–10% | Earnings-priority suite + P2 signals |
| P4-3 | `mean_reversion_exhaustion` | Risk overlay | P2-5, P2-6, RSI/Stoch extended logic |
| P4-4 | `defensive_fortress` | Defensive 10–20% | `net_cash_to_market_cap`, low beta stack |
| P4-5 | `sector_rotation_momentum` | Tactical 10–15% | Per-industry scan pathway |
| P4-6 | `quality_growth_at_reasonable_price` | Core 15–25% | GARP signal stack (PEG, forward EPS, ROIC) |

Each new profile requires:
1. `ScoringProfile` entry in `PRESET_SCORING_PROFILES`
2. Consensus weight in `CONSENSUS_PROFILE_WEIGHTS`
3. SQL `@block` queries in investment session
4. Shortlist export bucket (if actionable)
5. Tests in `test_trading_view_move_prediction_analysis.py` or profile-specific tests
6. Documentation update in weighting reference

---

## Phase 5 — Pattern discovery infrastructure

| ID | Task | File(s) | Priority |
|----|------|---------|----------|
| P5-1 | SQL template: join `move_prediction_*.duckdb` + `tradingview_all_fields_*.duckdb` on symbol | new session SQL file | High |
| P5-2 | SQL template: top-decile vs bottom-decile field comparison by profile cohort | same | High |
| P5-3 | SQL template: false-positive field autopsy (score up, price down) | same | High |
| P5-4 | Script or notebook: weekly calibration report from history DB | optional new script | Medium |
| P5-5 | Temporal stability score: names with `presence_ratio >= 0.5` over ≥4 snapshots | history aggregator extension | Medium |
| P5-6 | Industry-relative normalization layer (peer z-score within sector) | major analysis change | Low (architectural) |

---

## Phase 6 — External data (deferred)

| ID | Feature | Source | Notes |
|----|---------|--------|-------|
| P6-1 | Insider buying/selling signal | EDGAR Form 4 | Repo already has EDGAR pipeline |
| P6-2 | Short interest / squeeze candidate profile | FINRA or exchange data | Not in TradingView catalog |
| P6-3 | Options IV rank / percentile | External vendor | Replace/supplement BB squeeze |
| P6-4 | Revenue estimate revision trend | Historical estimate snapshots | Not in single-point scan |

---

## Promotion ladder (how any todo graduates)

1. **Observation** — pattern noted in weekly review (Phase 0)
2. **SQL prototype** — new `@block` tested in DBCode (Phase 1 or 5)
3. **Shortlist gate** — automated filter in pipeline (Phase 1)
4. **Signal / weight change** — `PRESET_SCORING_PROFILES` edit (Phase 2–3)
5. **New profile** — only if orthogonal to existing 10 lenses (Phase 4)

**Minimum evidence before Phase 3–4 code changes:**
- `aligned_positive_ratio` > 0.55 on target profile×horizon
- `score_price_corr` > 0.15 (weeks or months)
- False-positive rate < 35% on score-improved cohort
- Tested on both all-universe and at least one per-industry rerun

---

## Suggested 90-day sequence

| Weeks | Focus |
|-------|-------|
| 1–4 | Phase 0 only — establish weekly rhythm and history DB |
| 5–8 | Phase 1 — shortlist gates and trap screens |
| 9–12 | Phase 2 — Tier 1 derived signals + Phase 3 breakout/inflection tuning |
| 13+ | Phase 4 — first new profile (`pre_earnings_drift` or `mean_reversion_exhaustion`) based on calibration winners |

---

## Quick reference: highest-impact first moves

1. **P0-2 + P0-4** — weekly analysis + shortlist (immediate edge)
2. **P1-1 + P1-2** — stock-only + liquidity gates (protect $100k book)
3. **P2-1 + P2-4 + P3-1** — Donchian + CMF + breakout tuning (reduce false breakouts)
4. **P5-1 + P5-3** — join prediction + all-fields for false-positive autopsy (learn what fails)
5. **P4-2** — `pre_earnings_drift` profile (if earnings season aligns with your calendar)
