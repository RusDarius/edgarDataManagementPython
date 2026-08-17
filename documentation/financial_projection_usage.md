# Financial Projection — Usage & Field Guide

5-year forward projections from a TradingView **all-fields** DuckDB snapshot.

Three suites share the same entrypoint:

1. **Growth lanes (default / this iteration)** — revenue / EBIT / EBITDA / margins
   on three parallel paths: `universe`, `own`, `peer`.
2. **Price suite** — EV/Revenue → terminal price, plus street outlook and decision
   diagnostics (`--mode price`).
3. **Ticker overview** — single-name ~100-field all-fields pull, bucketed for
   projection scans (`--mode overview --ticker NASDAQ:PENG`).

**Entrypoint:** [`src/run_financial_projection.py`](../src/run_financial_projection.py)  
**Config:** [`config/financial_projection/scenarios_v1.json`](../config/financial_projection/scenarios_v1.json)  
**Package:** [`src/financial_projection/`](../src/financial_projection/)  
**Field catalog:** [`savedData/trading_view_stock_fields.csv`](../savedData/trading_view_stock_fields.csv)

Do **not** wire run examples into `src/main.py`. Use the entrypoint /
`example_*` helpers in `run_financial_projection.py`.

---

## Quick start

```powershell
cd src
python run_financial_projection.py --top-n 25
# EV/Rev price suite instead:
python run_financial_projection.py --mode price --top-n 25
# Single-ticker overview scan (~100 curated fields):
python run_financial_projection.py --mode overview --ticker NASDAQ:PENG
python run_financial_projection.py --mode overview --ticker NASDAQ:TTD --day-label 11_08_2026
```

```python
from run_financial_projection import (
    run_growth_projection_from_latest_all_fields,
    run_growth_projection_from_latest_prediction_analysis,
    run_growth_projection_from_all_fields_day,
    run_growth_projection_for_custom_peer_group,
    example_ticker_overview,
)
from financial_projection import run_ticker_projection_overview

run_growth_projection_from_latest_all_fields(min_market_cap_usd=500_000_000)
run_growth_projection_from_all_fields_day("01_07_2026", min_market_cap_usd=500_000_000)
run_growth_projection_from_latest_prediction_analysis(min_market_cap_usd=500_000_000)
# Optional: hand-picked comps (separate run, not main-suite params)
# run_growth_projection_for_custom_peer_group([...], group_label="saas_mature")

# Overview scan (like a TTD-style single-name metric dump, top ~100 only):
example_ticker_overview("NASDAQ:PENG")
run_ticker_projection_overview(ticker="NASDAQ:TTD", day_label="11_08_2026")
```

Symbols must be **`EXCHANGE:TICKER`** (e.g. `NASDAQ:ADBE`, `NYSE:ORCL`).

**Prediction DB = symbol shortlist only.** Fundamentals and forecasts always
come from the all-fields snapshot. Peers are built on the full eligible
universe even when you project a shortlist.

Growth outputs land under
`logs/tradingview_analysis/financial_projection/<dd_mm_yyyy>/fingrowth_*/`
(`growth_summary`, `growth_year_grid`, DuckDB, parquet, `run_metadata.json`).

Price outputs use `finproj_*` / `projection_summary` / `projection_year_grid`.

Overview outputs use `tickoverview_*` with `_ticker_overview.log`,
`metrics_by_bucket.csv`, `ticker_overview.json`, and `run_metadata.json`.
Buckets: `identity`, `price_performance`, `valuation`, `levels_margins`,
`growth_history`, `forward_street`, `quality_balance`, `cash_flow`,
`technical_risk`, plus a small `computed` helper set. Field list lives in
[`src/financial_projection/overview_fields.py`](../src/financial_projection/overview_fields.py).

---

## Three growth lanes (practical rule)

Every growth summary row is one of **three parallel lanes** × bear/base/bull.
They are not merged into one score.

| Lane | Role | Starting growth | Margins |
|------|------|-----------------|---------|
| **universe** | Absolute path from universe priors (median of peer medians, fallback 8%) | universe prior × scenario scale/fade | fade toward universe median margins |
| **own** | Company forecast / YoY / 5y CAGR persistence | `revenue_forecast_next_fy` → `total_revenue_yoy_growth_ttm` → `total_revenue_cagr_5y` | soft persist of own `ebitda_margin_ttm` / `operating_margin_ttm` / `net_margin_ttm` |
| **peer** | Peer-relative growth + margin convergence | 50/50 blend of own growth and peer median growth | fade toward peer median margins |

Key summary fields: `growth_lane`, `starting_growth_fraction`, `growth_lane_source`,
`y1_revenue_growth_pct`, `revenue_cagr_implied_pct`, `ebitda_cagr_implied_pct`,
`ebit_cagr_implied_pct`, `net_income_cagr_implied_pct`,
`terminal_total_revenue_ttm`, `terminal_ebitda`, `terminal_ebit_ttm`,
`terminal_net_income_ttm`, `terminal_*_margin_ttm`.

Year grid: one row per (`growth_lane`, `scenario`, `year`) with levels,
margins, and `*_growth_vs_y0_pct`.

TV input names stay exact (`total_revenue_ttm`, `ebit_ttm`, `ebitda_margin_ttm`, …).

---

## Four decision lanes (price suite)

Every **price** summary row carries **four parallel lanes**. They are not merged into one
score. Rank screens still use the **core** lane (`primary_upside_pct` /
`lane_core_upside_pct`); the others are explicit cross-checks.

| Lane | Role | Key fields | Trust / caveat |
|------|------|------------|----------------|
| **1. Core** | Comparable baseline from **global** bear/base/bull coeffs | `lane_core_upside_pct` (= `terminal_upside_pct`), `lane_core_y1_upside_pct` | Clean across names; does not silently rewrite coeffs from history |
| **2. Street** | Near-term forecast / target anchor | `lane_street_upside_pct`, `lane_street_fy_rev_growth_pct`, `lane_street_outlook` | Consensus can be wrong; still the best near-term tape |
| **3. History** | CAGR / YoY persistence overlay | `total_revenue_cagr_5y`, `total_revenue_yoy_growth_ttm`, `lane_hist_vs_street_gap_pp`, `lane_hist_*_adj`, `lane_hist_adjusted_upside_pct`, `lane_hist_trust` | Own-company history; peer-relative blend damped when peers look polluted |
| **4. Peer** | Multi-view industry / mcap / maturity / profitable | `lane_peer_*`, `lane_peer_industry_*`, `lane_peer_mcap_*`, `lane_peer_growth_*`, `lane_peer_view_suggested`, `lane_peer_trust` | Industry is default; other angles are optional — use trust + agreement |

**History does not replace core.** It suggests coeff tweaks
(`lane_hist_growth_scale_adj`, `lane_hist_fade_adj`, `lane_hist_terminal_mult_adj`,
clipped ~0.70–1.30) and runs a **parallel** path →
`lane_hist_adjusted_upside_pct` and `lane_hist_vs_core_gap_pp`.

**Peer trust** falls when: lens is `unsuitable`, `peer_n` is thin, relative
EV/S is pinned at the high clip, or peer-set EV/S dispersion is high. Low peer
trust limits how much history uses peer-relative CAGR in those tweaks.
Compare `lane_peer_industry_ev_rev_rel` vs `lane_peer_growth_ev_rev_rel` (ADBE
vs DDOG maturity) and `lane_peer_mcap_ev_rev_rel` when size matters.

---

## 1. INPUT data (exact all-fields names)

Anything taken from TradingView all-fields keeps the **exact** name from
`trading_view_stock_fields.csv`. There are **no** aliases
(`revenue_0`, `ev_rev_0`, `st_revenue_fq`, …).

Only **computed** metrics use non-TV names (`terminal_upside_pct`,
`st_next_fy_rev_growth_pct`, `valuation_lens`, …).

### 1.1 Identity / context

| Field | Role |
|-------|------|
| `symbol` | `EXCHANGE:TICKER` |
| `name`, `exchange`, `market` | Identity |
| `sector`, `industry` | Peer grouping + valuation lens |
| `close` | Spot price for upside math |
| `market_cap_basic` | Size filter + equity bridge |

### 1.2 Levels used by the 5y EV/Rev path

| Field | Role |
|-------|------|
| `total_revenue_ttm` | Starting revenue (preferred) |
| `total_revenue` | FY revenue; used only to backfill TTM if TTM missing |
| `enterprise_value_current` | EV; may be reconstructed as EV/Rev × revenue |
| `enterprise_value_to_revenue_ttm` | Starting multiple; may be reconstructed as EV / revenue |
| `net_debt` | Starting net debt; if missing → `total_debt − cash_n_equivalents_fq` (else 0) |
| `total_debt`, `cash_n_equivalents_fq` | Only used when `net_debt` is missing |
| `ebitda` | EV/EBITDA cross-check path |
| `enterprise_value_ebitda_ttm` | Own EV/EBITDA for cross-check blend |
| `sustainable_growth_rate_ttm` | Optional growth cap (SGR × scenario multiplier) |

### 1.3 Growth / history sources

| Field | Role |
|-------|------|
| `revenue_forecast_next_fy` | Preferred starting growth: forecast / TTM − 1 |
| `total_revenue_yoy_growth_ttm` | Fallback growth; also history-lane YoY |
| `total_revenue_cagr_5y` | Last-resort starting growth; history-lane CAGR |
| `total_revenue_5y_growth_fy` | Extra history context (not starting-growth default) |
| `total_revenue_yoy_growth_fy` | Extra history context |
| `free_cash_flow_cagr_5y` | History quality companion (surfaced on summary) |
| `ebitda_yoy_growth_ttm` | Available for quality checks / peers |

### 1.4 Short-term / street inputs

| Field | Role |
|-------|------|
| `total_revenue_fq` | Latest reported quarter revenue |
| `revenue_forecast_fq`, `revenue_forecast_next_fq` | Current / next quarter rev forecasts |
| `earnings_per_share_fq` (or diluted FQ) | Latest quarter EPS |
| `earnings_per_share_diluted_ttm` (or basic TTM) | TTM EPS |
| `earnings_per_share_forecast_fq`, `_next_fq`, `_next_fy` | EPS forecasts |
| `price_earnings_forward_fy` | Forward P/E (often empty in exports) |
| `non_gaap_price_to_earnings_per_share_forecast_next_fy` | Fallback forward P/E |
| `price_earnings_ttm` | Trailing P/E (informational only; **not** used for implied price) |
| `price_target_median`, `average`, `1y`, `low`, `high` | Street targets |
| `earnings_release_next_date` | Preferred next earnings timestamp |
| `earnings_release_next_trading_date_fq` | Fallback earnings date |
| `earnings_release_next_calendar_date` | Last-resort earnings date |

### 1.5 Who is eligible (filters on inputs)

| Filter | Default | Effect |
|--------|---------|--------|
| Markets | TV preferred list | Venue gate |
| `min_market_cap_usd` | `$500M` | Size gate |
| `min_revenue_usd` | `$25M` | Uses resolved `total_revenue_ttm` |
| `max_ev_to_revenue` | `80` | Drops extreme EV/Rev shells |
| Hard required | close, mcap, revenue, EV/Rev, growth | Else `valid=false` |

---

## 2. How INPUT is used (derived logic)

This section is the pipeline: input → peers / growth / scenarios → path →
diagnostics.

### 2.1 Prepare step (gap fills only)

Still exact field names — no new aliases:

1. Prefer `total_revenue_ttm`; if missing, copy `total_revenue` into
   `total_revenue_ttm` for modeling.
2. If `enterprise_value_to_revenue_ttm` missing → EV / revenue.
3. If `enterprise_value_current` missing → EV/Rev × revenue.
4. If `net_debt` missing → `total_debt − cash_n_equivalents_fq` (else 0).

### 2.2 Peer grouping (multi-view)

Peer medians drive terminal EV/Rev (and EV/EBITDA cross-check). The **core
path** defaults to the **industry** median when the industry set is large
enough (then sector → global). That keeps industry comparison as the primary
anchor.

In parallel, the peer lane builds **optional angles** so you can decide which
view is relevant:

| View | Meaning |
|------|---------|
| `industry` | Full industry (default core anchor) |
| `industry_mcap` | Expandable mcap band inside industry (starts `[0.25×, 4×]`) |
| `industry_rev` | Revenue band inside industry (`[0.4×, 2.5×]`, ISS-style) |
| `industry_growth` | Growth / maturity band (±15pp YoY/CAGR, or relative when extreme) |
| `industry_profitable` | Positive-EBITDA peers (when the name itself is profitable) |
| `sector` / `global` | Fallbacks when industry is thin |

Each view carries `peer_n`, EV/Rev median + relative scale, dispersion
(`iqr_over_median`), and a trust score. High dispersion / thin n / clip-pin
lowers trust. `lane_peer_view_suggested` is the highest-trust view (hint only
— does not overwrite core). `lane_peer_view_agreement` says whether other
angles agree with industry on rich/mid/cheap.

Medians are trimmed 5% each tail when n ≥ 20. Relative scales
(own ÷ peer) are clipped to `[0.25, 3.0]`.

Default-path fields: `peer_scope`, `peer_n`, `ev_rev_peer_median`,
`ev_rev_relative_scale`, `peer_dispersion_iqr_over_median`.

Per-angle lane fields: `lane_peer_industry_*`, `lane_peer_mcap_*`,
`lane_peer_rev_*`, `lane_peer_growth_*`, `lane_peer_profitable_*`,
plus `lane_peer_views_json`.

#### Custom peer-group run (separate method)

For hand-picked comps (e.g. mature SaaS vs hypergrowth), use a **separate
run method** — not extra params on the main suite:

```python
from run_financial_projection import (
    run_growth_projection_for_custom_peer_group,
    run_financial_projection_for_custom_peer_group,
)

run_growth_projection_for_custom_peer_group(
    ["NASDAQ:ADBE", "NASDAQ:CRM", "NYSE:ORCL", "NASDAQ:INTU", "NASDAQ:ADSK"],
    group_label="saas_mature",
)
# Price suite equivalent:
# run_financial_projection_for_custom_peer_group([...], group_label="saas_mature")
```

Peers (and size/growth/profitable angles) are built only from that list.
`project_symbols=` can project a subset.

### 2.3 Starting growth resolution

1. Implied = `revenue_forecast_next_fy / total_revenue_ttm − 1`
2. If implied > `forecast_growth_max_fraction` (1.5) → **reject**, keep street
   FY visible, fall back to YoY then 5y CAGR
3. Clip model growth to ±`max_starting_growth_fraction` (1.0)
4. Scenario then applies scale / fade / SGR along years 1…5

Derived: `starting_growth_fraction`, `starting_growth_source`,
`street_fy_rev_growth_fraction`, `forecast_implied_growth_fraction`,
`forecast_rejected`, `effective_starting_growth_fraction`.

### 2.4 Scenario path (bear / base / bull)

For each year `t=0..5`:

```
revenue_t = revenue_{t-1} × (1 + growth_t)
ev_rev_t  = blend(own, peer) → fades toward peer × terminal_scale
EV_t      = ev_rev_t × revenue_t
net_debt_t = net_debt_0 × (1 + net_debt_growth)^t
equity_t  = EV_t − net_debt_t
price_t   = close × equity_t / market_cap_basic
```

Scenario knobs (`scenarios_v1.json`): `revenue_growth_scale`,
`growth_fade_per_year`, `terminal_multiple_scale_vs_peer`,
`own_multiple_blend`, `net_debt_growth_per_year`, `sgr_cap_multiplier`,
optional `revenue_growth_override`.

Light EV/EBITDA cross-check grows `ebitda` on the same growth path and applies
a blended peer EV/EBITDA terminal multiple.

### 2.5 Short-term derived metrics (`st_*`)

Raw TV levels stay under exact names. Only **computed** values use `st_*`:

| Derived from | Produces |
|--------------|----------|
| FQ / next-FQ rev & EPS | `st_next_fq_*_growth_pct`, surprise `st_fq_*_vs_forecast_pct` |
| TTM vs next-FY rev & EPS | `st_next_fy_*_growth_pct` |
| Targets vs `close` | `st_street_price_upside_pct`, `st_street_1y_upside_pct` |
| Next-FY EPS × forward P/E | `st_fy_eps_implied_*` (forward PE only; no TTM PE fallback) |
| FY rev − next-FQ EPS growth | `st_rev_eps_divergence_pp` |
| Model Y1 vs street | `st_model_vs_street_upside_gap_pct` |
| Mix of ST signals | `st_outlook` |
| Earnings epoch fields | `st_earnings_release_next_calendar_date` (`YYYY-MM-DD`) |

Forward P/E order: `price_earnings_forward_fy` →
`non_gaap_price_to_earnings_per_share_forecast_next_fy`. Resolved value is
written to `price_earnings_forward_fy`; source in
`st_price_earnings_forward_source`.

### 2.6 Valuation lens / regime / rank gate

EV/Rev always runs when valid. Diagnostics decide **how to read** it:

| `valuation_lens` | When | Read as |
|------------------|------|---------|
| `ev_revenue` | Default | Trust EV/Rev terminal (unless conflict/regime overrides) |
| `earnings` | e.g. Managed Health Care | Prefer street / FY EPS×PE; EV/Rev secondary |
| `unsuitable` | Finance sector (config) | Do not rank on EV/Rev; street is primary |

| `valuation_regime` | Rule |
|--------------------|------|
| `rich_growth` | Relative EV/S at high clip **and** street FY rev ≥ 15% |
| `rich_vs_peers` / `cheap_vs_peers` | Relative scale ≥ 2.0 / ≤ 0.6 |
| `normal` | Else |

`rank_eligible=false` when invalid, outlier (Y5 upside ≥ 400% and EV/Rev < 1.5),
lens `unsuitable`, or regime `rich_growth`. Console top-N uses eligible rows
and ranks by `primary_upside_pct`.

Other gates: `|EV/Rev − EV/EBITDA|` conflict (≥ 40pp prefer EBITDA),
rev−EPS divergence (≥ 25pp = margin risk), bull−bear width (≥ 100pp = wide).

### 2.7 Four lanes (how they are built)

1. **Core** — unchanged global scenario path → `lane_core_*`.
2. **Street** — copies street target / FY rev / outlook into `lane_street_*`.
3. **History** — reads `total_revenue_cagr_5y` / YoY; compares to street and peer
   CAGR; emits trust + suggested coeff multipliers; optionally re-runs path with
   adjusted coeffs → `lane_hist_adjusted_upside_pct` (does **not** overwrite core).
4. **Peer** — multi-view industry default + mcap / rev / growth / profitable
   angles with per-view trust, dispersion, suggested view, and agreement.

---

## 3. What RESULT fields mean

Summary = one row per `symbol × scenario`. Year grid = one row per
`symbol × scenario × year`.

### 3.1 Pass-through inputs on the summary

Same names as §1 — for convenience when reading CSV/DuckDB without rejoining
all-fields. Examples: `close`, `market_cap_basic`, `total_revenue_ttm`,
`enterprise_value_to_revenue_ttm`, `net_debt`, `ebitda`,
`revenue_forecast_next_fy`, `price_target_median`, `total_revenue_fq`,
EPS forecast fields, etc.

### 3.2 5y model results

| Field | Meaning |
|-------|---------|
| `scenario` | `bear` / `base` / `bull` |
| `valid` | Usable terminal equity produced |
| `invalid_reason` | e.g. `missing_growth`, `non_positive_equity`, `ev_rev_above_max` |
| `terminal_price` | Model price at year 5 |
| `terminal_upside_pct` | `(terminal_price / close − 1) × 100` |
| `implied_price_cagr_pct` | CAGR close → terminal over horizon |
| `terminal_ev_rev` | EV/Rev at year 5 |
| `model_y1_price`, `model_y1_upside_pct` | Year-1 point on the same path |
| `ev_ebitda_crosscheck_price_t5`, `…_upside_pct` | Alternate Y5 via EBITDA path |

### 3.3 Growth / peer derived fields

| Field | Meaning |
|-------|---------|
| `starting_growth_fraction` | Pre-scenario Year-1 growth (fraction) |
| `starting_growth_source` | What was used / rejected / clipped |
| `street_fy_rev_growth_fraction` | Street FY implied growth (kept even if rejected) |
| `forecast_rejected` | Street FY growth exceeded cap |
| `effective_starting_growth_fraction` | After scenario scale + SGR |
| `peer_scope`, `peer_n` | Which peer set + size |
| `ev_rev_peer_median`, `rev_growth_peer_median` | Peer medians |
| `ev_rev_relative_scale`, `rev_growth_relative_scale` | Own ÷ peer (clipped) |

### 3.4 Short-term computed (`st_*`)

| Field | Meaning |
|-------|---------|
| `st_next_fq_rev_growth_pct` / `st_next_fq_eps_growth_pct` | Next quarter vs latest FQ |
| `st_fq_eps_vs_forecast_pct` / `st_fq_rev_vs_forecast_pct` | Actual vs that quarter’s consensus |
| `st_next_fy_rev_growth_pct` / `st_next_fy_eps_growth_pct` | Next FY vs TTM |
| `st_street_price_upside_pct` | Median (else avg/1y) target vs close |
| `st_street_1y_upside_pct` | 1y target vs close |
| `st_fy_eps_implied_price` / `…_upside_pct` / `…_usable` | EPS×forward PE bridge |
| `st_price_earnings_forward_source` | Which PE field was used, or `missing` |
| `st_rev_eps_divergence_pp` | FY rev growth − next-FQ EPS growth |
| `st_model_y1_*` / `st_model_vs_street_upside_gap_pct` | Model Y1 vs street |
| `st_outlook` | `constructive` / `neutral` / `cautious` / `unavailable` |
| `st_earnings_release_next_calendar_date` | ISO earnings date |

### 3.5 Decision diagnostics

| Field | Meaning |
|-------|---------|
| `valuation_lens` | `ev_revenue` / `earnings` / `unsuitable` |
| `valuation_regime` | `normal` / `rich_growth` / `rich_vs_peers` / `cheap_vs_peers` |
| `valuation_lens_conflict_pp` / `…_conflict` | \|EV/Rev − EV/EBITDA\| gap |
| `valuation_preferred_on_conflict` | `ev_ebitda` when conflict fires |
| `primary_upside_pct` / `primary_upside_source` | Recommended upside to read first |
| `rank_eligible` | Safe for ranked screens |
| `outlier_flag`, `thin_peer_set_flag`, `margin_risk_flag` | Quality flags |
| `scenario_width_y5_pp` / `scenario_width_wide_flag` | Bull − bear uncertainty |
| `decision_flags` | Pipe-joined tags |

### 3.6 Four-lane result fields

| Field | Lane | Meaning |
|-------|------|---------|
| `lane_core_upside_pct` | Core | Same as `terminal_upside_pct` |
| `lane_core_y1_upside_pct` | Core | Same as `model_y1_upside_pct` |
| `lane_core_source` | Core | `global_scenario_coeffs` |
| `lane_street_upside_pct` | Street | Street target upside |
| `lane_street_fy_rev_growth_pct` | Street | Next-FY rev growth % |
| `lane_street_outlook` | Street | constructive / neutral / cautious / unavailable |
| `lane_street_source` | Street | `price_target_and_forecasts` |
| `total_revenue_cagr_5y` / `total_revenue_yoy_growth_ttm` | History | Exact all-fields history inputs |
| `lane_hist_vs_street_gap_pp` | History | History growth − street FY (pp) |
| `lane_hist_yoy_vs_cagr_gap_pp` | History | YoY − CAGR (pp); large ⇒ noisy regime |
| `lane_hist_vs_peer_cagr_rel` | History | Own CAGR ÷ peer CAGR (clipped) |
| `lane_hist_trust` | History | 0..1 own-history credibility |
| `lane_hist_growth_scale_adj` / `fade_adj` / `terminal_mult_adj` | History | Suggested multipliers on global coeffs |
| `lane_hist_coeff_notes` | History | e.g. `street_above_history` |
| `lane_hist_adjusted_upside_pct` | History | Parallel path with adjusted coeffs |
| `lane_hist_vs_core_gap_pp` | History | Hist-adjusted − core upside (pp) |
| `lane_peer_ev_rev_median` / `lane_peer_rev_cagr_median` | Peer | Default (industry) medians |
| `lane_peer_ev_rev_rel` | Peer | Own EV/Rev ÷ default peer (clipped) |
| `lane_peer_dispersion` | Peer | Default peer EV/S IQR ÷ median |
| `lane_peer_scope` / `lane_peer_n` | Peer | Default view name + size |
| `lane_peer_trust` | Peer | 0..1; low if polluted / thin / unsuitable / high dispersion |
| `lane_peer_industry_*` / `lane_peer_mcap_*` / `lane_peer_rev_*` / `lane_peer_growth_*` / `lane_peer_profitable_*` | Peer | Parallel angles (n, median, rel, dispersion, trust) |
| `lane_peer_view_suggested` | Peer | Highest-trust view (hint only) |
| `lane_peer_view_agreement` | Peer | Share of other views agreeing rich/mid/cheap |
| `lane_peer_views_json` | Peer | Compact JSON of all views |
| `lane_peer_source` | Peer | `multi_view_industry_default` |

### 3.7 Year grid (`projection_year_grid`)

Path columns reuse all-fields **concept names**. For `year > 0` they are
**projections**, not live snapshot values.

| Field | Meaning |
|-------|---------|
| `year` | 0 = today; 1…5 = forward |
| `total_revenue_ttm` | Revenue path from starting TTM |
| `growth_fraction` | Growth into that year (`null` at 0) |
| `enterprise_value_to_revenue_ttm` | Multiple path |
| `enterprise_value_current` | EV at that year |
| `net_debt` | Net debt path |
| `equity_value` | EV − net debt |
| `price`, `upside_pct` | Model price vs close |

---

## 4. How to USE the resulting fields

### 4.1 Default read order (one name)

1. `valid` — if false, stop; read `invalid_reason`.
2. `valuation_lens` + `valuation_regime` + `rank_eligible`.
3. **Lane 1 core:** `lane_core_upside_pct` / `primary_upside_pct`.
4. **Lane 2 street:** `lane_street_upside_pct` + gap vs model.
5. **Lane 3 history:** `lane_hist_vs_street_gap_pp`, `lane_hist_adjusted_upside_pct`,
   `lane_hist_trust` — persistence check, not a silent override.
6. **Lane 4 peer:** `lane_peer_trust` + relative EV/S — if trust is low, discount
   peer-driven conclusions (including hist peer-relative tweaks).
7. Uncertainty: `scenario_width_y5_pp`, `decision_flags`, conflicts, margin risk.

### 4.2 What to do by case

| Situation | Use these fields | Action |
|-----------|------------------|--------|
| Normal name | Core + street gap | Rank / size off core primary |
| Street ≫ history | `lane_hist_vs_street_gap_pp`, hist adj | Treat street optimism as fragile |
| History ≫ street | hist adj vs core | Persistence support; still keep core as baseline |
| Low `lane_peer_trust` | peer scope/n, clip flags | Don’t trust industry-relative multiple/CAGR |
| `rich_growth` | street + hist, not core sell | Multiple-sustainability debate |
| `earnings` / `unsuitable` lens | street lane | Re-lens; ignore EV/Rev rank |
| Wide scenario band | bear/base/bull + hist gap | Smaller size |

### 4.3 Screens

- **Ranked list:** `scenario=base` AND `rank_eligible` ORDER BY `primary_upside_pct`
  (core lane).
- **Core vs street disagreement:** sort by `st_model_vs_street_upside_gap_pct`.
- **Street vs history:** filter large `|lane_hist_vs_street_gap_pp|` with
  `lane_hist_trust` high.
- **Hist-adjusted overlay:** compare `lane_hist_adjusted_upside_pct` to
  `lane_core_upside_pct` where `lane_hist_trust` ≥ ~0.5 — do not replace core rank.
- **Low peer trust names:** `lane_peer_trust` low — review manually / use street+hist only.

### 4.4 Artifacts

| File | Use |
|------|-----|
| `projection_summary.csv` / `.parquet` | Decision table (inputs + 4 lanes + diagnostics) |
| `projection_year_grid.csv` / `.parquet` | Core path inspection by year |
| `*.duckdb` | SQL over both tables + `run_metadata` |
| `run_metadata.json` | Provenance (all-fields DB, config, counts) |

Console top-N ranks **core** rank-eligible rows by `primary_upside_pct`.
