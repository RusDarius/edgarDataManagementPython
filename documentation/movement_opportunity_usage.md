# Movement opportunity — usage and interpretation

First-iteration **trading** scan. Sibling of `src/run_financial_projection.py`.
It scores **how a stock is moving** and how much unused upside versus fragility
that move still has. It is **not** leftover radar (Daily), not tape movers,
not Book NAV ladders, and not a 5-year growth path.

`upside_10` and `downside_10` are 1–10 heuristics from **named components**.
Every component stays a column. They are not leftover, not mix, and not a
probability. Do not average them into a 0–100.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_movement_opportunity.py
python src/run_movement_opportunity.py --min-mcap 500000000 --history-days 30
python src/run_financial_projection.py --mode movement
```

No-arg run edits the floor and window in `run_movement_opportunity_local_main`.

Folder: `logs/tradingview_analysis/movement_opportunity/runs/movement_opportunity_<stamp>/`

| File | What it is |
|---|---|
| `movement_opportunity.duckdb` | `movement_tape`, `upside_50`, `downside_30`, `bounce`, overflow, `evidence` |
| `queries.txt` | Task SQL at the top, then every field. Open this before writing SQL. |
| `overview.log` | Source ids, row counts, overlay coverage |
| `highlights.log` | Shortlist in reading order plus the cited IC lines |
| `spec.json` | Floor, window, weights |

Quote leftover as `"left"`. `pe_fwd` is projection `fp_pe_y1`. MTP `ENTER_SMALL`
is climate, not a buy list.

## What it uses

Joins the six project datasets. Missing overlay does not drop the tape;
coverage flags go thin instead.

| Dataset | Role here |
|---|---|
Default universe is the operator US tape (`us_listings_only`: NASDAQ / NYSE /
AMEX / ARCA / BATS / CBOE, or country United States). Dual listings stay off
the ranking. The market-cap floor is a load filter, not a leftover cutoff.

| All-fields | Close, PT, range, RSI, ATRP, multiples, earnings date. History span of close / PT / PE. |
| Prediction weeks | `bo` / `cont` / `fwd` / `recov` / `frag` / `exh` / mix |
| Edge | Pack leftover already `max(street PT, edge FV)`. Lean and magnitude when present. |
| MTP | Climate only (`ENTER_SMALL` / `WATCH` / `AVOID_CHASE`) |
| Value projection | `fp_rev_cagr_own`, `fp_primary_upside_pct`, `fp_pe_y1`. Sparse. Does not gate. |
| Backtest `family_metrics` | Cited vs live IC on the `evidence` table. Not a leftover gate. |

Day-to-day variation is `series_span(..., include_steps=True)` on close:
step mean / abs mean / stdev / max up / max down / up-rate / streaks /
drawdown / giveback / rebound / seat. First/last/min/max stay as they were.

Expected-move envelope is the existing edge formula: `ATRP × √horizon`
(else daily vol). Columns `exp_move_1d` … `exp_move_60d`. Primary is 20d.

## How to read the 1–10

Each 1–10 is `1 + 9 ×` a coverage-renormalized mean of 0–1 parts.

**Upside (focus)**

| Component | Weight | Reads |
|---|---|---|
| `up_asymmetric_payoff` | 0.25 | leftover, `rr`, unused range, OPENING / CHEAPER |
| `up_structure_conviction` | 0.25 | live `bo`/`cont`/`fwd`, `recov`, mix (avoid cuts this) |
| `up_valuation_growth` | 0.20 | peer DISCOUNT, `val_vs_ind`, `fp_rev_cagr_own`, cheaper PE span |
| `up_timing_catalyst` | 0.15 | MTP climate, `dte` 8–21, punished `BOUNCE` |
| `up_move_fuel` | 0.15 | 20d expected-move **size**, coiled leftover, relvol |

**Downside**

| Component | Weight | Reads |
|---|---|---|
| `dn_fragility_trend` | 0.25 | `frag`, vs50 down, DYING, CONTINUE_DOWN, drawdown |
| `dn_valuation_ceiling` | 0.25 | PREMIUM, paid `rng`, leftover gone |
| `dn_exhaustion_reversal` | 0.20 | RSI stretch, `exh`, giveback from the window high |
| `dn_edge_risk` | 0.15 | lean AVOID / downside, mix avoid |
| `dn_vol_amplification` | 0.15 | high velocity **and** negative tilt. ATRP is size, not quality. |

`thin_up` / `thin_dn` means `*_cover_frac` < 0.4. Those ratings cannot print
above 6. A missing dataset cannot manufacture a 9.

`path_class` is OPENING / DYING / CHEAPER / FLAT / THIN_HIST. OPENING / DYING
/ CHEAPER match analysis-style. DYING wins over CHEAPER. Enough history that
is none of those three is FLAT. First-iteration CHEAPER uses the trailing PE
span (`pe_pct`), not each name's setup `val_field`.

`vol_class` is coiled / active_swing / high_velocity / extreme / thin.

`down_class` is BOUNCE / CONTINUE_DOWN / MIXED on **punished** names only
(same classifier as movers). The bounce table is that sleeve, sorted by
`upside_10`.

## How well this can be done (this iteration)

Cited from `backtests_20260912_1603_utc_64c52b03` and the latest decision
review. Live `family_metrics` overwrites `ic_live` on table `evidence`.

| Family | Cited | Read |
|---|---|---|
| `expected_move_proxy_pct` 20d | IC 0.233, **6 days** | Best magnitude proxy. Thin calendar. Size, not leftover. |
| `move_magnitude_score` 20d | IC 0.228, 6 days | Same thin window. |
| `value_recovery_v3` 20d | IC 0.112 | Support for bounce / repair. |
| `consensus_weeks` 60d | IC 0.138 | Weeks climate. Mix avoid still cuts conviction. |
| timing overlay vs picks_only | negative | MTP stays climate. Not a leftover gate. |
| ATRP / ADRP | negative as a **rank** | Envelope size and downside amp only. |
| projection join | 4/103 growth, 2/103 price on a prior lock | Do not gate. |
| decision review | FLAT on most slices | This 1–10 is **not** a validated predictor of later close. |

Honest ceiling this iteration: the dump can **rank unused leftover that has
not been consumed**, **size an expected-move envelope**, and **flag dying /
paid / fragile** names. It cannot claim a 20-day hit rate. The IC that looks
best is thin. Decision review of leftover ranks was flat.

## FOLLOWUPS

1. Re-run `src/backtests` so `expected_move_proxy_pct` / magnitude have more
   than six scoring days, then refresh `evidence`.
2. Span each shortlist name's own `val_field` (analysis-style), not only PE.
3. Walk prediction weeks history (`bo`/`cont`/`fwd` span), not only all-fields.
4. Optional `--full-history` already walks the loaded tape; a dedicated
   prediction-week path is still missing.
5. Include this dump in `run_operator_suites.py wisdom` once the 1–10 has a
   decision-review slice of its own.
6. Options IV when a vendor column exists. ATRP is a cash-tape proxy.
7. Do not bake the 1–10 into `ranking.py` or Daily Build-50.

## Not this scan

| Need | Home |
|---|---|
| Daily leftover / Build-50 | `daily-operator-scan` |
| Day / week / 5D / 1M tape leaders | `price-movers` |
| Book loss vs NAV | `book-risk` |
| 5-year growth / terminals | `run_financial_projection.py` (`growth` / `price`) |
| Street vs model forward price | `run_operator_suites.py forward` |
| ASYM / CONVICTION ranks | `analysis-style` after wisdom |
