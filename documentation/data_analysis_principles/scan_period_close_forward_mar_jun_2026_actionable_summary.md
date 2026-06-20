# Scan-Period Close-Forward Tracking — Mar–Jun 2026 Actionable Summary

> **Status:** Completed batch run + stock rankings (June 2026)  
> **Run ID:** `scan_period_close_forward_tracking_29mar_15jun2026_b108820e`  
> **Window:** 29 Mar 2026 → 15 Jun 2026 · 55 daily scans · ~5,716 symbols (filtered universe)  
> **Performance target:** `period_return_pct` (close at period start → close at period end)  
> **Predictor anchor:** period start (29 Mar)  
> **Related:** [active management discussion](active_management_discussion_and_improvements.md) §3.6 · [all-fields analyzer usage](../trading_view_all_fields_metric_pattern_analyzer_usage.md) · `scripts/run_close_forward_predictor_stock_rankings.py` · `scripts/resume_scan_period_rolling.py`

---

## 1. What this run is (and is not)

### What it measures

| Lens | Question answered |
|------|-------------------|
| **Period total** (`period_total/`) | Which fields at **29 Mar** predicted **full-period** close-to-close return? |
| **Rolling windows** (21 × 7-scan slices, 3-day step) | Which fields stayed directionally consistent across **sub-periods**? |
| **Stability aggregate** (`aggregates/`) | Which predictors pass `runs_seen ≥ 3`, `sign_consistency ≥ 0.6` across windows? |
| **Stock rankings** (`predictor_stock_rankings/`) | Which names **today** score high on **stable** bullish fields (Playbook A/B/C)? |

### What it is not

- Not a live trading signal by itself — rankings use a **15 Jun** daily snapshot for positioning.
- Not proof of out-of-sample alpha — Mar–Jun 2026 was a **strong rising tape** (median +5.9%, 67% win rate).
- Not a substitute for move-prediction profiles, Module2, or EDGAR quality — use as **regime confirmation** and **field pruning** (see §3.6 in active management doc).

---

## 2. Universe context — regime label for this window

**Label:** *Risk-on / high-beta rally — volatility and beta rewarded.*

| Stat | Value |
|------|-------|
| Symbols with period return | 5,716 |
| Median `period_return_pct` | **+5.9%** |
| Mean `period_return_pct` | **+12.2%** |
| 25th / 75th percentile | **-1.3% / +18.4%** |
| Win rate (return > 0) | **67%** |

**Implication for interpretation:** Relative predictors (quintile spreads) measure **who won more within a rising market**, not market direction. Value/size headwinds may reflect **lagging mega-caps in a beta rally**, not permanent “avoid quality.”

---

## 3. Period-total findings (full Mar–Jun)

Top `pattern_score` fields on the **whole period** (single pooled cross-section):

| Signal family | Examples | Read |
|---------------|----------|------|
| **Volatility / range** | `ADRP\|1W`, `ATRP`, `ATRP\|1W`, `Volatility.M`, `ADRP` | Dominant theme — higher range → higher period return |
| **Beta** | `beta_1_year`, `beta_5_year`, `beta_3_year` | High-beta cohort led |
| **Candlestick patterns** | Various `Candle.*` | High raw score, weak adjusted correlation — treat as **noise / sparse** |

**Pooled quintile spreads (advised fields, full period):**

| Predictor | Q1 avg return | Q5 avg return | Q5 − Q1 |
|-----------|---------------|---------------|---------|
| `ADRP\|15` | +4.4% | +13.4% | **+9.0 pp** |
| `ADRP\|1W` | +3.6% | +14.8% | **+11.2 pp** |
| `ATRP\|1W` | +3.6% | +14.5% | **+10.9 pp** |
| `ebitda_ttm` | +10.9% | +6.5% | **-4.4 pp** (large EBITDA lagged) |
| `Recommend.MA\|1M` | +11.2% | +6.9% | **-4.3 pp** (crowded consensus lagged) |

---

## 4. Rolling stability (21 windows) — what survived

Only **5 of 13 advised predictors** cleared aggregate stability gates with `runs_seen = 21`:

| Predictor | `rank_stability_score` | Sign consistency | Median Q-spread | Playbook role |
|-----------|------------------------|------------------|-----------------|---------------|
| `ADRP\|15` | **+0.99** | 62% | +1.6 pp | Volatility bullish — **primary weight** |
| `ADRP\|1W` | **+0.92** | 67% | +1.4 pp | Volatility bullish |
| `ATRP\|1W` | **+0.78** | 62% | +1.3 pp | Playbook A anchor |
| `ebitda_ttm` | **-0.57** | 71% | -0.8 pp | Playbook C — mega-cap headwind |
| `Recommend.MA\|1M` | **-0.12** | 62% | -0.2 pp | Playbook C — crowded consensus |

**Did not pass stability (composite excluded or near-zero weight):**

`ATRP`, `ADX-DI|1M`, `ADX-DI_50|1M`, `relative_volume`, `RSI21[1]|1M`, `Stoch.K_14_1_3|1M`, `W.R|1M`, `oper_income_ttm`

→ Composite scoring this period is **heavily ADRP-weighted**. Do not assume the full advised playbook was equally validated.

**Top stability scores overall** also include calendar/dividend fields (`ex_dividend_date_upcoming`, `payment_date_upcoming`, etc.). These are **structurally stable but not tradable** — ignore for active management; use script filters that exclude non-price fields when building regime cards.

**Bearish stability (avoid bucket):** oversold oscillators (`Stoch.D_*`, `CCI20`) stable **negative** — consistent with “don’t buy falling knives in a momentum rally.”

---

## 5. Stock-level screens — what the exports say

**Ranking run:** `predictor_stock_rankings/` generated 2026-06-17  
**Daily positioning DB:** `15_06_2026` · **Realized validation anchor:** `29_03_2026` (period start)

### 5.1 Composite (`01_composite_bullish_fit_top100.csv`)

High ADRP/ATRP fit on current snapshot. Top names skew **small/mid-cap, high ATRP (18–42)**:

Examples: `YSS`, `SOC`, `PAYP`, `VOR`, `NNNN`, `RGC`, `LIFE`, `CBIO`, `DMRA`

**Caution:** Many have **relvol < 1.5** — they rank on volatility level, not full Playbook A participation.

### 5.2 Playbook A — volatility continuation (`02_playbook_a_volatility_continuation_top100.csv`)

Stricter: `ATRP|1W` elevated + `relative_volume > 1.5` + ADX-DI context.

Examples: `PAYP`, `IQE`, `WYFI`, `SGP`, `AKTS`, `MAAS`, `HQ`, `VRXA`, `MH`, `ISO`

**Use:** Primary tactical **new-entry** shortlist when regime = risk-on (§2).

### 5.3 Playbook A realized (`06_playbook_a_realized_29_03_2026_top100.csv`)

In-sample validation from period **start** labels — **not** for forward picking; for **calibration only**.

Top realized: `VPG` (+251%), `INOD` (+167%), `OUST` (+156%), `XMTR` (+130%), `CRWD` (+87%)

Confirms volatility tilt worked **in this window**; do not size from hindsight ranks.

### 5.4 Playbook C — warning overlay (`05_warning_overlay_avoid_top100.csv`)

Multi-flag avoid list (4 warning signals): mega-cap / crowded names e.g. `META`, `ADBE`, `CRM`, `SAP`, `FISV`, `BABA`, `HCA`, `RNO`

Aligns with `ebitda_ttm` / `Recommend.MA|1M` negative stability.

### 5.5 Active management tiers (`07_active_management_candidates_top100.csv`)

| Tier | Count (top 100) | Meaning |
|------|-----------------|---------|
| `playbook_b_watch` | 92 | Composite fit + mild historical drift — **watchlist** |
| `playbook_b_quality_drift` | 6 | Stronger drift stats |
| `playbook_a_vol_continuation` | 2 | Strictest — **actionable tactical** |

**Rule:** Only names in `playbook_a_vol_continuation` or top-decile composite **with** relvol > 1.5 get tactical probe size without extra confirmation.

---

## 6. Actionable rules (use in weekly process)

Integrate with the three-step funnel in [active_management_discussion_and_improvements.md](active_management_discussion_and_improvements.md) §3.1.

### 6.1 Regime card for Mar–Jun-style tape (until next batch run)

**Promote (stable positive):**

- `ADRP|15`, `ADRP|1W`, `ATRP|1W` — volatility expansion
- Optional confirmation only (unstable this window): `relative_volume`, `ADX-DI|1M`

**Demote / trap filter (stable negative):**

- `ebitda_ttm`, `oper_income_ttm` — size headwind in beta rally
- `Recommend.MA|1M` — crowded bullish consensus headwind
- Oversold cluster: `RSI21[1]|1M`, `Stoch.K`, `W.R` — **avoid long** in momentum regime

**Ignore for trading:**

- Calendar/dividend upcoming-date fields (stable but not actionable)

### 6.2 Sleeve-specific application

| Sleeve | How to use this scan |
|--------|----------------------|
| **Tactical / theme** | Entry: Playbook A shortlist ∩ breakout_long / early_momentum_inflection top-30. **Do not** require low P/E. |
| **Core** | Warning overlay on **existing** mega-cap holdings — trim if on warning list + `mean_reversion_exhaustion`. |
| **All** | If name fails stability on **zero** promoted fields and hits 2+ warning fields → max watchlist, no add. |

### 6.3 Position sizing hooks

- **Playbook A confirmed** (tier + relvol + 2 profile lanes): tactical max per existing framework (e.g. 2–4% probe).
- **Composite only, relvol < 1.5:** watchlist — no automatic entry.
- **Warning overlay ≥ 2 flags:** no new long; consider trim if tactical sleeve.

### 6.4 What to delete from attention (discipline per §3.6)

Until the next scan-period batch contradicts this:

1. Stop manually overweighting `ADX-DI` and `relative_volume` in composite logic for this regime — they **did not stabilize** over 21 windows.
2. Do not add new strategies from raw period-total candlestick leaders.
3. Do not use calendar-field stability ranks as alpha.

### 6.5 Re-run cadence and runtime expectations

| Scope | Rolling windows | Est. runtime |
|-------|-----------------|--------------|
| Full ~3 mo / 55 scans | 21 (7d window, 3d step) | ~15–18 h |
| Resume after interrupt | Skip completed parquets | ~44 min × remaining windows |
| Faster pilot | `rolling_window_step_days=7` | ~9 windows, ~45% less |

Use `scripts/resume_scan_period_rolling.py` after clean stop — do **not** rerun `run_scan_period_close_forward_predictor_tracking()` (recomputes period_total unnecessarily).

---

## 7. Follow-ups — next analysis prompts

Use these as copy-paste briefs for future sessions or plans.

### 7.1 Playbook A deep-dive

> Cross `02_playbook_a_volatility_continuation_top100.csv` with `active_manager_v3` conviction rankings, `current_holdings.json`, and sector weights. Flag: already held, new candidate, concentration risk. Output: ≤5 names with entry/avoid and max size.

### 7.2 Pilot comparison (`dd898100` vs `b108820e`)

> Compare stability aggregates for `scan_period_close_forward_tracking_25may_12jun2026_dd898100` vs full Mar–Jun run. Did ADRP/ATRP stability persist or is it window-specific? List fields stable in both vs only one.

### 7.3 Regime split (no recompute)

> Using saved `rolling_windows/*/period_field_performance_patterns.parquet`, bucket windows into Apr / May / Jun by start date. For advised predictors, plot sign flip rate and median Q-spread per month. Answer: did volatility signal weaken into June?

### 7.4 Holdings overlay

> For each symbol in `config/holdings_scoring/current_holdings.json`, look up period return, composite rank, warning flags, and move-prediction `manager_action_signal`. Classify: aligned / watch / trim candidate.

### 7.5 Profile ↔ field reconciliation

> For top-weighted signals in `active_manager_v3` profiles, check presence in `cross_run_field_stability` top 50. List profile weights **not** supported by period-driven stability — candidates for weight reduction.

### 7.6 Rankings script hardening

> Auto-read `start_day_label` from `_scan_period_close_forward_tracking.json` for realized Playbook A. Add optional `--relaxed-stability` export for exploratory predictors below 0.6 sign consistency.

### 7.7 Next batch run design

> Plan Q3 scan with `rolling_window_step_days=7`, explicit `predictor_fields` subset (50–80), and monthly chunks for ops — but one full-period `period_total` pass for rankings anchor.

---

## 8. Artifact map

```
logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/
  scan_period_close_forward_tracking_29mar_15jun2026_b108820e/
    _scan_period_close_forward_tracking.json
    period_total/
      period_field_performance_patterns.csv      ← full-period field ranks
      period_analysis_input.duckdb                 ← anchored predictors
      period_returns/period_boundary_returns.duckdb
    progression/
      period_universe_progression.csv
      period_field_quintile_progression.csv
    rolling_windows/                               ← 21 sub-period runs
      {start}_to_{end}/period_field_performance_patterns.parquet
    aggregates/
      all_fields_pattern_aggregate.duckdb          ← cross_run_field_stability
      cross_run_field_stability.parquet
      top_patterns_report.csv
    predictor_stock_rankings/
      01_composite_bullish_fit_top100.csv
      02_playbook_a_volatility_continuation_top100.csv
      05_warning_overlay_avoid_top100.csv
      07_active_management_candidates_top100.csv
      indicator_perf/00_advised_active_manager_predictor_profile.csv
      _manifest.json
```

---

## 9. Commands reference

```powershell
# Resume interrupted rolling (skip completed windows)
.venv\Scripts\python.exe scripts\resume_scan_period_rolling.py

# Field-level data_set_conclusions (writes under {run_root}/data_set_conclusions/)
.venv\Scripts\python.exe -c "from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import export_scan_period_data_set_conclusions; export_scan_period_data_set_conclusions(run_root=r'logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_29mar_15jun2026_b108820e')"

# Stock rankings (after aggregate exists)
.venv\Scripts\python.exe scripts\run_close_forward_predictor_stock_rankings.py `
  --run-root logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_29mar_15jun2026_b108820e `
  --daily-db logs/tradingview_analysis/trading_view_all_fields_data/15_06_2026/tradingview_all_fields_15_06_2026.duckdb `
  --daily-run-id tradingview_all_fields_20260615_2006_utc_f4ac11ac `
  --latest-scored-day 29_03_2026 `
  --top-n 100

# PLAYBOOK A tactical watchlist vs Mar–Jun period performance
.venv\Scripts\python.exe scripts\analyze_scan_period_ticker_watchlist.py `
  --run-root logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_29mar_15jun2026_b108820e `
  --from-regime-log logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=25/runs/move_prediction_20260618_1442_utc_31b685b4/move_prediction__regime_context_focus.log `
  --snapshot-days 29_03_2026,15_06_2026

# Regenerate PLAYBOOK A TACTICAL log: uncomment run_full_analysis_suite_duckdb block in src/main.py
# with regime_context_config_path=REGIME_CONTEXT_CONFIG, then: .venv\Scripts\python.exe src\main.py
```

**SQL entry point:** `sql_connections_space/tradingview_all_fields_pattern_analysis.session.sql` — blocks `[PERIOD B108820E W1–W4]` for watchlist queries on this run.

---

## 10. One-paragraph executive summary

Mar–Jun 2026 was a **broad risk-on rally** where **volatility expansion** (`ADRP`, `ATRP|1W`) was the only advised predictor family **stable across all 21 rolling windows**, with ~10 pp quintile separation. **Large EBITDA and bullish analyst MA consensus** reliably lagged; mega-cap warning lists overlap `META`, `ADBE`, `CRM`, etc. **Playbook A** is the validated tactical lens for this regime; composite ranks are useful but **over-inclusive** (92/100 are watch-tier). Use this batch to **narrow** field attention and **confirm** tactical entries — not to replace profile consensus or value vetoes on core holdings.
