# Financial Projection — Usage & Field Guide

Readable guide for **what moves**, **which coefficients**, and **how to pull
rows**. Growth lanes are the default suite; price and overview are secondary.

| Suite | Entrypoint mode | Output prefix | Question it answers |
|-------|-----------------|---------------|---------------------|
| **Growth** (default) | `--mode growth` | `fingrowth_*` | How do **revenue / EBIT / EBITDA / margins** evolve over 5y under 3 assumption lanes? |
| **Price** | `--mode price` | `finproj_*` | What **terminal price / upside** does EV/Rev imply (with street + peer diagnostics)? |
| **Overview** | `--mode overview --ticker …` | `tickoverview_*` | What ~100 all-fields metrics does this name show **today** (no projection path)? |

**Entrypoint:** [`src/run_financial_projection.py`](../src/run_financial_projection.py)  
**Operator join / all-suite dumps:** [`src/run_operator_suites.py`](../src/run_operator_suites.py) (`forward`, `finproj-growth`, `finproj-price`, `wisdom --run-finproj`)  
**Config:** [`config/financial_projection/scenarios_v1.json`](../config/financial_projection/scenarios_v1.json)  
**Package:** [`src/financial_projection/`](../src/financial_projection/)  
**Field catalog:** [`savedData/trading_view_stock_fields.csv`](../savedData/trading_view_stock_fields.csv)

`main.py` exposes `run_forward_value_dump()` / `run_operator_wisdom_dumps()` which call the suites runner. The projection engine itself stays on `run_financial_projection.py`. Cover on those dumps is a size (max 1000), not a leftover/RSI screen and not an industry cap.

---

## Quick start

```powershell
cd src
python run_financial_projection.py --top-n 25
python run_financial_projection.py --mode price --top-n 25
python run_financial_projection.py --mode overview --ticker NASDAQ:MU
```

```python
from run_financial_projection import (
    run_growth_projection_from_latest_prediction_analysis,
    run_growth_projection_from_latest_all_fields,
    run_growth_projection_for_custom_peer_group,
)

run_growth_projection_from_latest_prediction_analysis(min_market_cap_usd=500_000_000)
# run_growth_projection_from_latest_all_fields(symbols=["NASDAQ:MU", "NASDAQ:SNDK"])
```

Growth artifacts:
`logs/tradingview_analysis/financial_projection/<dd_mm_yyyy>/fingrowth_*/`
→ `growth_summary`, `growth_year_grid`, DuckDB, parquet, `run_metadata.json`.

Units to remember:

| Kind | Storage | How to read |
|------|---------|-------------|
| Growth fractions in model math | `0.20` = 20% | `starting_growth_fraction`, `growth_fraction` |
| TV YoY / CAGR / margins in inputs | usually **percent points** (`20` = 20%) | `total_revenue_yoy_growth_ttm`, `ebitda_margin_ttm` |
| Implied CAGR / Y1 growth in outputs | **percent** (`35.4` = 35.4%) | `revenue_cagr_implied_pct`, `y1_revenue_growth_pct` |
| Levels | currency units from TV | `total_revenue_ttm`, `terminal_ebitda` |

---

## A. How to read a growth run (60 seconds)

1. Open the run DuckDB (or CSV). Filter **`scenario = 'base'`** first.
2. Pick a symbol. You get **3 rows** (lanes) for base, not one merged score:
   - **`own`** — company forecast / YoY / 5y CAGR (personal history + street).
   - **`peer`** — blend toward industry peer growth + peer margins.
   - **`universe`** — absolute prior from median of peer medians (fallback 8%).
3. Read **movement**, not jargon:
   - Today: `total_revenue_ttm`, margins (`*_margin_ttm`).
   - Year-1 growth used: `y1_revenue_growth_pct` (= effective start × 100).
   - 5y compounded outcome: `revenue_cagr_implied_pct`, `terminal_total_revenue_ttm`.
   - Profit path: `ebitda_cagr_implied_pct` / `ebit_cagr_implied_pct` / `net_income_cagr_implied_pct`
     and `terminal_*_margin_ttm`.
4. Compare lanes on the **same metric** (e.g. `revenue_cagr_implied_pct`):
   - own ≫ peer/universe → street/history is hot vs comps.
   - peer ≫ own → comps imply faster growth / better margins than the name embeds.
5. Inspect year-by-year in `growth_year_grid` (`year` 0…5).

**There is no single “fair price” in growth mode.** Growth mode projects
**levels and margins**. Price upside is `--mode price`.

---

## B. What moves — coefficients and why

### B.1 Starting revenue growth (before scenario)

Priority order for **own** lane (`own_revenue_growth_source`):

| Priority | Source field | Coefficient type | Why |
|----------|--------------|------------------|-----|
| 1 | `revenue_forecast_next_fy / total_revenue_ttm − 1` | **Street / personal forward** | Best near-term consensus level if sane |
| 2 | `total_revenue_yoy_growth_ttm` | **Personal recent** | Trailing run-rate if forecast missing/rejected |
| 3 | `total_revenue_cagr_5y` | **Personal history** | Longer persistence if YoY missing |
| Reject | implied forecast > `forecast_growth_max_fraction` (1.5) | Safety clip | Absurd forecast → fall back; flag `forecast_rejected` |
| Clip | ±`max_starting_growth_fraction` (1.0) | Hard cap | Caps ±100% start growth |

Lane-specific **starting growth** (`starting_growth_fraction`):

| Lane | Formula | Coefficient origin |
|------|---------|-------------------|
| `own` | resolved own growth (above) | personal / street |
| `peer` | `0.5 × own + 0.5 × peer_median_growth` | personal + **industry peer median** |
| `universe` | median of peer YoY/CAGR medians across names, else **0.08** | **market-reasonable absolute prior** |

Peer median growth prefers `total_revenue_yoy_growth_ttm` peer median, else
`total_revenue_cagr_5y` peer median (`rev_growth_peer_median` /
`rev_cagr_peer_median` on the summary).

### B.2 Scenario coefficients (bear / base / bull)

From `scenarios_v1.json`. Applied **after** lane starting growth:

```
g0 = starting_growth × revenue_growth_scale   # (or revenue_growth_override if set)
year1 growth = g0
year2 growth = g0 × fade
year3 growth = g0 × fade²
…
```

| Scenario | `revenue_growth_scale` | `growth_fade_per_year` | Meaning |
|----------|------------------------|------------------------|---------|
| bear | **0.55** | **0.85** | Cut growth hard; fade faster |
| base | **1.00** | **0.90** | Use starting growth as-is; mild fade |
| bull | **1.25** | **0.95** | Uplift growth; slow fade |

`effective_starting_growth_fraction` = year-1 growth after scale (= `g0`).  
`y1_revenue_growth_pct` = that × 100.

**Growth suite does not use** (price-suite only):  
`terminal_multiple_scale_vs_peer`, `own_multiple_blend`, `net_debt_growth_per_year`,
`sgr_cap_multiplier`.

### B.3 Margin path coefficients

Levels each year:

```
revenue_t = revenue_{t-1} × (1 + growth_t)
margin_t  = fade(start_margin → target_margin, year, blend_speed)
ebitda_t  = revenue_t × ebitda_margin_t
ebit_t    = revenue_t × operating_margin_t
net_t     = revenue_t × net_margin_t
```

Margin fade weight ≈ `min(1, (year / 5) × blend_speed)`:

| Lane | Margin **target** | `blend_speed` | Why |
|------|-------------------|---------------|-----|
| `own` | own current margins | **0.25** | Soft persist — history stays sticky |
| `peer` | peer median margins | **1.00** | Full convergence toward comps by Y5 |
| `universe` | universe median margins | **0.85** | Strong fade toward market-reasonable margins |

Starting margins: TV `ebitda_margin_ttm` / `operating_margin_ttm` / `net_margin_ttm`
(percent → fraction), else level ÷ revenue.

### B.4 What is *not* a free coefficient

- Industry peer set size / trim / mcap bands come from config peer rules
  (same peer builder as price suite). Growth uses the **default peer view**
  medians for growth and margins (`peer_scope`, usually industry).
- Universe prior is **not** a hand-picked “8% GDP” by default: it is the
  median of peer growth medians in the run (fallback 8% only if empty).

---

## C. Input fields — used vs loaded but unused (growth)

Anything from TradingView keeps the **exact** all-fields name.

### C.1 Directly drives growth math

| Field | Role in growth |
|-------|----------------|
| `symbol`, `name`, `sector`, `industry` | Identity + peer grouping |
| `close`, `market_cap_basic` | Surfaced on summary (size context; not in growth path math) |
| `total_revenue_ttm` (else `total_revenue`) | Revenue₀ |
| `ebitda_ttm` / `ebitda` | EBITDA₀ (also margin backfill) |
| `ebit_ttm` / `oper_income_ttm` | EBIT₀ |
| `net_income_ttm` | Net₀ |
| `ebitda_margin_ttm`, `operating_margin_ttm`, `net_margin_ttm` | Margin₀ (or derived from levels) |
| `revenue_forecast_next_fy` | Preferred own growth |
| `total_revenue_yoy_growth_ttm` | Own + peer growth |
| `total_revenue_cagr_5y` | Own + peer growth fallback |
| Peer medians of YoY/CAGR/margins | Peer + universe lanes |

Eligibility filters (before project): markets, `min_market_cap_usd` ($500M),
`min_revenue_usd` ($25M), `max_ev_to_revenue` (80).

### C.2 Loaded on the whitelist / peers but **not used in growth path math**

These appear in all-fields load or peer metric medians, or matter for
**price/overview**, but **growth lanes do not consume them** for the year path:

| Field | Why it is present | Growth use |
|-------|-------------------|------------|
| `enterprise_value_current`, `enterprise_value_to_revenue_ttm` | Eligibility / price suite | Not in growth path |
| `enterprise_value_ebitda_ttm`, `price_earnings_*`, `price_sales_current` | Peer / price | Not in growth path |
| `total_debt`, `net_debt`, `cash_n_equivalents_fq` | Price equity bridge | Not in growth path |
| `sustainable_growth_rate_ttm` | Price SGR cap | Not applied in growth |
| `free_cash_flow_cagr_5y`, `net_income_cagr_5y`, `net_income_yoy_growth_ttm` | Peer metrics / history context | Not starting growth (rev only) |
| `total_revenue_yoy_growth_fy`, `total_revenue_5y_growth_fy` | Extra history | Not starting growth default |
| FQ/EPS forecasts, price targets, earnings dates | Street / ST outlook (price) | Not in growth path |
| `ebitda_yoy_growth_ttm` | Surfaced on summary | Not driving growth fraction |

Use **overview mode** or all-fields SQL when you need price performance,
multiples, FCF, balance sheet, or technicals that growth does not project.

---

## D. Output fields — `growth_summary` (every column)

One row = one `(symbol, growth_lane, scenario)`.

### D.1 Identity / filter context

| Field | Meaning |
|-------|---------|
| `symbol` | `EXCHANGE:TICKER` |
| `name`, `sector`, `industry` | Identity |
| `growth_lane` | `universe` \| `own` \| `peer` |
| `scenario` | `bear` \| `base` \| `bull` |
| `close`, `market_cap_basic` | Spot size context |
| `valid` | Path computed |
| `invalid_reason` | e.g. `missing_revenue`, `missing_growth` |

### D.2 Today’s levels & margins (inputs copied)

| Field | Meaning |
|-------|---------|
| `total_revenue_ttm` | Revenue₀ |
| `ebitda`, `ebitda_ttm` | EBITDA₀ |
| `ebit_ttm`, `oper_income_ttm` | EBIT₀ |
| `net_income_ttm` | Net₀ |
| `ebitda_margin_ttm`, `operating_margin_ttm`, `net_margin_ttm` | Margins as **fractions** after normalize |
| `total_revenue_yoy_growth_ttm`, `total_revenue_cagr_5y` | Raw TV growth inputs (often % points) |
| `revenue_forecast_next_fy` | Street FY revenue level |
| `ebitda_yoy_growth_ttm` | Surfaced only |

### D.3 Coefficient / source audit (read these to understand “why”)

| Field | Meaning |
|-------|---------|
| `growth_lane_source` | Lane start label (`revenue_forecast_next_fy`, `blend_own_and_peer_median`, `universe_peer_median_or_default`, …) |
| `own_revenue_growth_source` | Own resolver label |
| `own_revenue_growth_fraction` | Own growth as fraction (pre-scenario) |
| `forecast_implied_growth_fraction` | Forecast/TTM−1 even if rejected |
| `forecast_rejected` | Forecast exceeded sanity cap |
| `starting_growth_fraction` | Lane start **before** scenario scale |
| `effective_starting_growth_fraction` | After `revenue_growth_scale` (= Y1 growth) |
| `universe_growth_fraction` | Absolute prior used by universe lane |
| `peer_scope`, `peer_n` | Which peer set + size |
| `rev_growth_peer_median`, `rev_cagr_peer_median` | Peer growth medians (TV % points typically) |
| `peer_ebitda_margin_median`, `peer_operating_margin_median`, `peer_net_margin_median` | Peer margin targets as fractions |

### D.4 Movement results (what the projection “says”)

| Field | Meaning |
|-------|---------|
| `y1_revenue_growth_pct` | Year-1 revenue growth **%** |
| `y1_ebitda_growth_pct` | Year-1 EBITDA growth **%** vs EBITDA₀ |
| `revenue_cagr_implied_pct` | Implied 5y revenue CAGR **%** from Rev₀ → Rev₅ |
| `ebitda_cagr_implied_pct` | Implied 5y EBITDA CAGR **%** |
| `ebit_cagr_implied_pct` | Implied 5y EBIT CAGR **%** |
| `net_income_cagr_implied_pct` | Implied 5y net income CAGR **%** |
| `terminal_total_revenue_ttm` | Revenue at year 5 |
| `terminal_ebitda`, `terminal_ebit_ttm`, `terminal_net_income_ttm` | Levels at year 5 |
| `terminal_ebitda_margin_ttm`, `terminal_operating_margin_ttm`, `terminal_net_margin_ttm` | Margins at year 5 (fractions) |

**How to interpret:** compare `own` vs `peer` vs `universe` on
`revenue_cagr_implied_pct` and terminal margins. That is the “story” of the scan.

---

## E. Output fields — `growth_year_grid`

One row = `(symbol, growth_lane, scenario, year)` with `year` ∈ {0,1,2,3,4,5}.

| Field | Meaning |
|-------|---------|
| `year` | 0 = today; 1…5 = forward |
| `total_revenue_ttm` | Revenue at that year |
| `growth_fraction` | Growth **into** that year (`null` at 0) |
| `ebitda`, `ebit_ttm`, `net_income_ttm` | Levels |
| `ebitda_margin_ttm`, `operating_margin_ttm`, `net_margin_ttm` | Margins (fractions) |
| `revenue_growth_vs_y0_pct` | Cumulative revenue change vs year 0 (%) |
| `ebitda_growth_vs_y0_pct` | Cumulative EBITDA vs year 0 (%) |
| `ebit_growth_vs_y0_pct` | Cumulative EBIT vs year 0 (%) |
| `net_income_growth_vs_y0_pct` | Cumulative net vs year 0 (%) |

---

## F. Queries to pull and read data

Replace the DuckDB path with your `fingrowth_*.duckdb`.

### F.1 Latest growth run folder (PowerShell)

```powershell
Get-ChildItem logs\tradingview_analysis\financial_projection -Recurse -Directory -Filter "fingrowth_*" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 FullName
```

### F.2 One name — three lanes (base) — the core read

```sql
SELECT
  symbol,
  growth_lane,
  growth_lane_source,
  own_revenue_growth_source,
  starting_growth_fraction,
  effective_starting_growth_fraction,
  y1_revenue_growth_pct,
  revenue_cagr_implied_pct,
  ebitda_cagr_implied_pct,
  ebit_cagr_implied_pct,
  net_income_cagr_implied_pct,
  total_revenue_ttm,
  terminal_total_revenue_ttm,
  ebitda_margin_ttm,
  terminal_ebitda_margin_ttm,
  operating_margin_ttm,
  terminal_operating_margin_ttm,
  peer_n,
  rev_growth_peer_median,
  universe_growth_fraction,
  forecast_rejected,
  valid
FROM growth_summary
WHERE scenario = 'base'
  AND symbol = 'NASDAQ:MU'
ORDER BY growth_lane;
```

### F.3 Year path for one lane

```sql
SELECT
  year,
  total_revenue_ttm,
  growth_fraction,
  ebitda,
  ebit_ttm,
  net_income_ttm,
  ebitda_margin_ttm,
  operating_margin_ttm,
  net_margin_ttm,
  revenue_growth_vs_y0_pct,
  ebitda_growth_vs_y0_pct
FROM growth_year_grid
WHERE symbol = 'NASDAQ:MU'
  AND growth_lane = 'own'
  AND scenario = 'base'
ORDER BY year;
```

### F.4 Own vs peer vs universe gap (screen)

```sql
WITH b AS (
  SELECT * FROM growth_summary
  WHERE scenario = 'base' AND valid
)
SELECT
  o.symbol,
  o.name,
  o.industry,
  o.revenue_cagr_implied_pct AS own_rev_cagr,
  p.revenue_cagr_implied_pct AS peer_rev_cagr,
  u.revenue_cagr_implied_pct AS univ_rev_cagr,
  TRY_CAST(o.revenue_cagr_implied_pct AS DOUBLE)
    - TRY_CAST(p.revenue_cagr_implied_pct AS DOUBLE) AS own_minus_peer_pp,
  o.y1_revenue_growth_pct AS own_y1,
  o.growth_lane_source,
  o.peer_n,
  o.forecast_rejected
FROM b o
JOIN b p ON o.symbol = p.symbol AND p.growth_lane = 'peer'
JOIN b u ON o.symbol = u.symbol AND u.growth_lane = 'universe'
WHERE o.growth_lane = 'own'
ORDER BY own_minus_peer_pp DESC
LIMIT 50;
```

### F.5 Hot street forecasts (clipped / rejected)

```sql
SELECT
  symbol, name, industry,
  forecast_implied_growth_fraction,
  own_revenue_growth_fraction,
  forecast_rejected,
  growth_lane_source,
  y1_revenue_growth_pct,
  revenue_cagr_implied_pct
FROM growth_summary
WHERE scenario = 'base'
  AND growth_lane = 'own'
  AND valid
  AND (
    forecast_rejected
    OR starting_growth_fraction >= 0.8
  )
ORDER BY starting_growth_fraction DESC
LIMIT 40;
```

### F.6 Margin convergence (peer lane)

```sql
SELECT
  symbol,
  ebitda_margin_ttm AS m0,
  terminal_ebitda_margin_ttm AS m5,
  peer_ebitda_margin_median,
  terminal_ebitda_margin_ttm - ebitda_margin_ttm AS ebitda_m_delta,
  operating_margin_ttm,
  terminal_operating_margin_ttm,
  peer_operating_margin_median
FROM growth_summary
WHERE scenario = 'base'
  AND growth_lane = 'peer'
  AND valid
ORDER BY ABS(terminal_ebitda_margin_ttm - ebitda_margin_ttm) DESC
LIMIT 40;
```

### F.7 Bear / base / bull band for one lane

```sql
SELECT
  scenario,
  y1_revenue_growth_pct,
  revenue_cagr_implied_pct,
  terminal_total_revenue_ttm,
  terminal_ebitda_margin_ttm
FROM growth_summary
WHERE symbol = 'NASDAQ:MU'
  AND growth_lane = 'own'
ORDER BY CASE scenario
  WHEN 'bear' THEN 1 WHEN 'base' THEN 2 WHEN 'bull' THEN 3 END;
```

### F.8 Run provenance

```sql
SELECT run_id, day_label, created_at_utc, payload_json
FROM run_metadata;
```

In `run_metadata.json` / payload: `universe_growth_priors`,
`all_fields_database`, `requested_symbols`, `peer_mode`, counts.

### F.9 Python one-liner

```python
import duckdb
con = duckdb.connect(r"logs/.../fingrowth_....duckdb", read_only=True)
print(con.execute("""
  SELECT growth_lane, y1_revenue_growth_pct, revenue_cagr_implied_pct,
         terminal_total_revenue_ttm
  FROM growth_summary
  WHERE symbol='NASDAQ:MU' AND scenario='base'
""").fetchdf())
```

---

## G. What the projection does **not** cover

Be explicit — these gaps are why overview / price / all-fields still matter:

| Missing | Consequence |
|---------|-------------|
| No price / multiple path in growth mode | No `terminal_upside_pct`; use `--mode price` |
| No balance-sheet path (cash, debt, dilution) | Terminal equity/price not modeled here |
| No FCF / EPS path | Only rev → margin → EBIT/EBITDA/net |
| No daily/weekly price performance | Use overview `Perf.*` / `change|*` |
| No earnings surprise / ST street outlook tables | Price suite `st_*` only |
| No sector rotation / move-prediction scores | Separate prediction / edge runs |
| EBIT/EBITDA YoY not used as growth driver | Revenue growth drives levels; margins fade separately |
| Peer multi-views (mcap/growth/profitable) not separate growth lanes | Growth uses default peer medians; multi-view is price-lane rich |
| Absurd street FY still possible up to 100% after reject→fallback clip | Always check `forecast_rejected` + `growth_lane_source` |
| Margins can stay unrealistically high on `own` | Soft blend 0.25 — peer/universe exist to cross-check |

---

## H. Worked example (how to speak the output)

For `NASDAQ:MU`, `growth_lane=own`, `scenario=base` (illustrative from a real run):

- Source: `revenue_forecast_next_fy` → start growth ~43% (fraction 0.43).
- Base scale = 1.0 → `y1_revenue_growth_pct` ≈ 43%.
- Fade 0.9 each year → implied ~5y revenue CAGR ≈ 35%.
- Own margins almost unchanged (blend 0.25) → EBITDA/EBIT/net CAGRs ≈ same as rev CAGR.
- Compare to `peer` row: if peer CAGR is much lower, street is optimistic vs industry.
- Compare to `universe` row (~prior ~7%): absolute “market reasonable” path is far cooler.

If `own` looks insane and `forecast_rejected` is true, trust `peer`/`universe`
or dig the raw forecast level — do not treat clipped 100% starts as destiny.

---

## I. Price suite (secondary)

`--mode price` projects **Rev → EV/Rev → EV → equity → price** with four
decision lanes (core / street / history / peer). Outputs:
`finproj_*` / `projection_summary` / `projection_year_grid`.

Scenario knobs that matter here (not in growth):  
`terminal_multiple_scale_vs_peer`, `own_multiple_blend`,
`net_debt_growth_per_year`, `sgr_cap_multiplier`.

Default read order for one name:

1. `valid` / `invalid_reason`
2. `primary_upside_pct` (core) + `rank_eligible`
3. Street: `lane_street_upside_pct`, `st_outlook`
4. History overlay: `lane_hist_adjusted_upside_pct`, `lane_hist_trust`
5. Peer trust: `lane_peer_trust`, multi-view `lane_peer_*`

```sql
SELECT symbol, primary_upside_pct, lane_core_upside_pct,
       lane_street_upside_pct, lane_hist_adjusted_upside_pct,
       lane_peer_trust, valuation_lens, rank_eligible
FROM projection_summary
WHERE scenario='base' AND valid AND rank_eligible
ORDER BY primary_upside_pct DESC
LIMIT 25;
```

Custom comps (growth or price):

```python
run_growth_projection_for_custom_peer_group(
    ["NASDAQ:ADBE", "NASDAQ:CRM", "NYSE:ORCL", "NASDAQ:INTU", "NASDAQ:ADSK"],
    group_label="saas_mature",
)
```

---

## J. Overview suite (raw metrics, no path)

`--mode overview --ticker NASDAQ:MU` dumps ~100 curated all-fields into
`tickoverview_*` buckets: identity, price_performance, valuation,
levels_margins, growth_history, forward_street, quality_balance, cash_flow,
technical_risk (+ small `computed`). Field list:
[`src/financial_projection/overview_fields.py`](../src/financial_projection/overview_fields.py).

Use overview when you need context **growth does not project** (Perf, multiples,
FCF, RSI/ATR, targets).

---

## K. Naming rule

- **TV inputs:** exact all-fields names (`total_revenue_ttm`, …).
- **Computed outputs:** explicit names (`revenue_cagr_implied_pct`,
  `terminal_ebitda`, `growth_lane`, `st_*`, `lane_*`).

If a column looks “esoteric”, find it in **§D / §E** (growth) or **§I** (price)
and re-read it as either: (1) today’s raw metric, (2) a coefficient/source audit
field, or (3) a movement result (Y1 / CAGR / terminal).
