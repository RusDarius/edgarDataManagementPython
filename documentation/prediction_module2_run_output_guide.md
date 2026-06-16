# Prediction Module2 — Run Output Guide

Orthogonal outlook scoring via `analysis_prediction_module2.py`. Module2 is **separate from v1**
move-prediction (`logs/tradingview_analysis/prediction_analysis/`). It uses outlook **pillars**,
**regime gates**, and **family-orthogonal consensus** to reduce profile overlap (e.g. upside swing
vs breakout continuation).

**Code:** `src/data_analysis_scripts/analysis_prediction_module2.py`  
**Profiles:** `config/prediction_module2_profiles/`  
**Default suite:** `active_manager_module2_v1`

---

## Where output lives

Each run creates a folder under:

```
logs/tradingview_analysis/prediction_module2/duckdb_runs/
  iso_year=YYYY/week=WW/runs/module2_YYYYMMDD_HHMM_utc_<hash>/
```

Run id pattern example: `module2_20260615_1850_utc_d2447b99`.

Weekly DuckDB (when using `run_module2_suite_duckdb`):

```
logs/tradingview_analysis/prediction_module2/duckdb_runs/
  iso_year=YYYY/week=WW/prediction_module2_YYYY_WW.duckdb
```

Tables: `module2_run_metadata`, `module2_profile_scores`, `module2_consensus_scores`
(full universe in DuckDB; CSV/logs are top 30 only).

---

## Files in each run folder

| File | Purpose |
|------|---------|
| `_module2_run_overview.log` | Run metadata + pairwise profile overlap (Jaccard) summary |
| `module2__profile_<name>.csv` | Top 30 ranked symbols for one outlook profile |
| `module2__profile_<name>.log` | Same leaders in readable log form with pillar breakdown |
| `module2__consensus.csv` | Top 30 family-orthogonal consensus names |
| `module2__overlap_report.json` | Top-30 Jaccard overlap between every profile pair + gate purity |

**Canonical reference for columns and scoring:** this document (`documentation/prediction_module2_run_output_guide.md`).

---

## How a profile score is built

Each symbol goes through this pipeline **per profile**:

```
raw scan row + derived metrics
  → hard regime gates (pass/fail — fail = no score)
  → pillar z-scores (peer-relative, roughly -3 … +3)
  → base_score (weighted pillar blend)
  → directional bias multipliers (profile-specific)
  → anti-signal penalties (crowding, extension, etc.)
  → × soft_gate_multiplier (0–1, repair confirmation etc.)
  → final score (clamped -3 … +3)
```

### Z-scores (what pillar values mean)

Every metric is converted to a **robust z-score vs today's scan universe** (median/MAD).
Higher z generally means “more of that signal than peers” after direction correction
(e.g. lower RSI → higher z for oversold fields).

| Range | Rough read |
|-------|------------|
| **+2 to +3** | Extreme vs peers — strong fit for that pillar |
| **+0.5 to +2** | Above average fit |
| **-0.5 to +0.5** | Neutral / mixed |
| **-2 to -0.5** | Below average — weak or opposite setup |
| **-3 to -2** | Strong headwind for that pillar |

### Profile CSV / log columns

| Column | Meaning |
|--------|---------|
| `symbol` | Exchange ticker (e.g. `NASDAQ:IDCC`). |
| `company_name` | Full company name from TradingView (`ticker-view.description`). |
| `score` | Final ranked value after gates, bias, anti-signals, soft multiplier. **Use for sorting.** |
| `hard_gate_passed` | `True` only if all hard regime gates passed. Leaders always `True`. |
| `soft_gate_multiplier` | 0–1 scale factor from soft gates (e.g. repair confirmation). Lower = less conviction. |
| `base_score` | Pillar blend **before** directional bias, anti-signals, and soft multiplier. |
| `pillar_*` | Sub-scores for each outlook pillar (z-scale). See profile sections below. |

### Directional bias multipliers (from profile JSON)

Applied to `base_score` before anti-signals:

- **`positive_multiplier`** — amplifies positive base scores (e.g. 1.2 = 20% boost to bullish pillar read).
- **`negative_multiplier`** — scales negative base scores (e.g. 0.5 = dampen bearish reads; upside swing forgives oversold tape).

### Anti-signals

When a “bad context” metric z-score crosses a threshold, score is **reduced** by
`penalty × |z|` (e.g. near 52w high, crowded MA consensus). These prevent extended
momentum names from ranking on mean-reversion lenses.

---

## Module2 derived composites (shared building blocks)

| Derived field | What it signals |
|---------------|-----------------|
| `regime_oversold_daily` | Daily-bar oversold composite (RSI, Stoch, BB, 52w range floor) |
| `regime_extended_tape` | Extended momentum context (near highs, trend alignment, long Perf) — **swing hard gate rejects high values** |
| `repair_confirmation_score` | Fresh bounce (Perf.5D/W) + volume/ATRP; penalized if “monthly oversold only” |
| `upside_room_score` | Headroom to 52w high, analyst PT, resistance levels |
| `continuation_strength` | ATRP + volume trend + aroon/adx — **breakout pillar driver** |
| `fundamental_inflection` | QoQ growth + turnaround composite |
| `quality_stability` | Margins, FCF, ROIC, Piotroski |
| `distress_floor` | Altman, leverage, interest cover — safety / fragility |

---

## Outlook profiles in `active_manager_module2_v1`

### `upside_swing_v1` — `mean_reversion_repair`

Depressed names with confirmed short-term repair and upside room. Hard-filters extended momentum leaders.

| Pillar | Weight | Signals |
|--------|--------|---------|
| oversold_context | 30% | `regime_oversold_daily`, RSI/Stoch/BB, 52w range floor |
| repair_trigger | 35% | Perf.5D/W, relative volume, `repair_confirmation_score` |
| upside_room | 20% | `upside_room_score`, distance from 52w high |
| safety_floor | 15% | Altman, leverage, `distress_floor` |

Hard gates: depressed `range_position_52w`, non-extended `regime_extended_tape`.

### `breakout_continuation_v1` — `continuation_momentum`

Confirmed upside continuation with volume/vol expansion. Rejects depressed-range oversold setups.

| Pillar | Weight | Signals |
|--------|--------|---------|
| participation | 30% | Relative volume, `volume_trend`, intraday momentum |
| continuation | 40% | `continuation_strength`, aroon/adx, short trend emergence |
| extension | 20% | Near 52w high, trend alignment, Perf.3M/6M |
| risk_control | 10% | Beta-adjusted ATRP |

Hard gates: elevated range position + minimum `continuation_strength`.

### `forward_fundamental_edge_v1` — `fundamental_inflection`

Operating improvement and forward estimate revision led; tape repair is secondary.

| Pillar | Weight | Signals |
|--------|--------|---------|
| growth_inflection | 45% | QoQ revenue/FCF/income, `fundamental_inflection`, turnaround |
| coiled_tape | 25% | BB squeeze, range compression, muted trailing Perf |
| quality_check | 20% | `quality_stability`, ROIC, operating margin |
| valuation_anchor | 10% | Earnings yield, analyst PT upside |

Anti-signals penalize extended tape and high continuation strength.

### `quality_compounder_v1` — `quality_compounding`

High-quality compounders with stable margins, FCF, and balance-sheet resilience.

| Pillar | Weight | Signals |
|--------|--------|---------|
| quality | 45% | `quality_stability`, ROIC, margins |
| cash_generation | 30% | FCF yield, FCF margin |
| balance_sheet | 15% | `distress_floor`, net cash |
| durability | 10% | Perf.Y, Perf.5Y |

### `value_recovery_v1` — `value_recovery`

Depressed valuation with operating turnaround evidence required.

| Pillar | Weight | Signals |
|--------|--------|---------|
| turnaround | 40% | `operating_turnaround_score`, `fundamental_inflection` |
| valuation | 35% | Book discount, earnings yield, PT downside floor |
| safety | 25% | `distress_floor` |

### `defensive_income_v1` — `defensive_income`

Defensive names with income, low beta, and balance-sheet resilience.

| Pillar | Weight | Signals |
|--------|--------|---------|
| income | 35% | Dividend yield, shareholder yield |
| defensive_quality | 35% | `quality_stability`, `distress_floor` |
| low_volatility | 30% | Beta, ATRP |

### `fragility_risk_v1` — `fragility_risk`

High-risk, extended, or deteriorating setups — **sign-inverted in consensus** (trim/avoid overlay).

| Pillar | Weight | Signals |
|--------|--------|---------|
| extension_risk | 40% | `regime_extended_tape`, near 52w high, exhaustion |
| distress | 35% | Leverage, weak Altman |
| crowding | 25% | Bullish MA consensus |

---

## Consensus (`module2__consensus.csv`)

**Mode:** `family_orthogonal` — one best score per **outlook family**, then weighted blend across
families (avoids triple-counting the same tape-repair name on swing + breakout + forward).

| Column | Meaning |
|--------|---------|
| `symbol` | Exchange ticker. |
| `company_name` | Full company name. |
| `consensus_score` | Weighted blend of family scores (-3 … +3). Higher = stronger multi-outlook agreement. |
| `coverage` | Fraction of family weights with a valid score (0–1). Low = sparse / gated out. |
| `family_scores_json` | Per-family best score for this symbol (e.g. mean_reversion_repair, continuation_momentum). |
| `horizon_name` | Placeholder horizon tag (`weeks`) for future multi-horizon consensus. |

**Inverted profile:** `fragility_risk_v1` — score sign-flipped before consensus.

Suite weights (default): upside_swing 18%, breakout 16%, forward_edge 14%, quality 14%,
value_recovery 12%, defensive_income 12%, fragility 14%.

---

## Overlap report (`module2__overlap_report.json`)

| Field | Meaning |
|-------|---------|
| `jaccard_top_n` | Overlap of top-30 symbol sets between two profiles (0 = disjoint, 1 = identical). Target swing vs breakout **< 0.25**. |
| `family_purity` | Share of top-30 leaders that passed hard gates for that profile (should be 1.0). |
| `shared_symbols` | Tickers appearing in both profiles' top 30. |

Example from run `module2_20260615_1850_utc_d2447b99` (3,928 symbols): upside_swing vs
breakout_continuation Jaccard **0.000** — module2 de-overlap working vs v1 cluster overlap.

---

## Running from `main.py`

```python
from main import PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1
from data_analysis_scripts.analysis_prediction_module2 import run_module2_suite_duckdb

module2_result = run_module2_suite_duckdb(
    scan_data=move_prediction_scan_response,
    min_market_cap_usd=1_000_000_000,
    profile_suite_path=PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1,
)
# module2_result["run_output_dir"]  → run folder path
```

---

## Practical reading tips

1. **Pick the lens first** — read `module2__profile_upside_swing_v1.*` for bounce candidates, `breakout_continuation_v1` for trend continuation, etc.
2. **Check gates** — if a name ranks on swing, it must have depressed 52w range and non-extended tape.
3. **Compare pillars, not just score** — high `pillar_repair_trigger` + `pillar_upside_room` on swing = actionable bounce setup.
4. **Use consensus last** — it is a portfolio-level blend, not a single trade signal.
5. **Compare overlap report to v1** — low swing/breakout Jaccard means module2 de-overlap is working.

---

## Changelog

| Date | Note |
|------|------|
| 2026-06-15 | Initial guide for module2 orthogonal scoring and `active_manager_module2_v1` suite |
