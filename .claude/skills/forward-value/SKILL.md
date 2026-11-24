---
name: forward-value
description: Street / TradingView leftover targets plus financial_projection terminals and growth CAGRs. Use when the user wants forward value projections, street vs model price, PE fwd / PEG vs leftover, or the FORWARD / value-tech dump. Not leftover radar, movers, Book risk, or ADD vs WAIT.
---

# Forward value (street + TV + financial_projection)

Field attach only. Not a 0–100 and not a buy list.

- Street leftover = analyst PT vs close (`fwd_street_pct` / `fwd_street_px`)
- Pack leftover = max(street PT, edge FV) (`fwd_pack_pct` / `fwd_pack_px`)
- `fwd_pe` / `pe_fwd` = projection next-year PE (`fp_pe_y1`) when the year-grid path or a CAGR exists. `pe_fwd_tv` is the TradingView forward PE and stays empty when that column is empty. Do not fill either with EV/Rev.
- `fp_pe_y5`, `fp_evebitda_y1`, `fp_evebitda_y5` are the far-year PE (at most 5, see `fp_horizon_year`) and the EV/EBITDA forwards. `fp_net_debt_y1` / `fp_net_debt_y5` and `fp_fcf_y1` / `fp_fcf_y5` are debt and free cash flow. `path` is the year grid. `cagr` compounds trailing levels. A one-year growth rate is not compounded to year 5.
- **Forward value run** (`finvalue_*`, `run_financial_projection.py --mode value`, also written by the default script run): when present, `pe_fwd` / `fp_pe_y1` / `fp_pe_y5` come from its street-anchored base path (`pe_fwd_source=fp_street`, `fp_eps_y1` = the street's next-FY EPS) and rows carry `fv_bear_px` / `fv_base_px` / `fv_bull_px` (fair value today), `fv_exp_px` / `fv_upside_pct` (scenario-weighted, 25/50/25), `irr_exp_pct` vs `hurdle_pct` (cost of equity) = `excess_irr_pp`, `priced_in_cagr_pct` and `growth_gap_pp`, `fv_lens`, `fv_flags`. Mispricing = `excess_irr_pp` and `growth_gap_pp`; `fv_bear_px` is the margin-of-safety floor. The level is conservative (median `fv_upside_pct` about -11%): compare names, not to zero. Read `fv_flags` (`pe_under_half_industry` = often cyclical peak earnings; `net_debt_over_mcap`). Model and knobs: `documentation/financial_projection_usage.md` section V.
- `fp_*` price and growth joins stay `scenario=base`, growth `lane=own`

Quote `val_field` / `val` / `val_vs_ind` from setup on the same row. Keep street, pack, and model terminals as **separate** columns.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_operator_suites.py --list
python src/run_operator_suites.py forward --cover 1000
python src/generic_utils/tv_scan_cli.py setup --pack PACK.json --sleeve us2b --forward --cover 1000 --out-dir RUN/value_tech
python src/generic_utils/tv_scan_cli.py forward --pack PACK.json --sleeve us2b --cover 1000 --out-dir RUN/forward
python src/run_financial_projection.py --mode growth --top-n 1000
python src/run_financial_projection.py --mode price --top-n 1000
```

`--cover` is a **size** (max 1000). No leftover/RSI filter and no industry cap.

Python (from `main.py` / REPL): `run_forward_value_dump()`, `run_value_tech_dump()`, `run_operator_wisdom_dumps()`.

Join uses the latest `finproj_*.duckdb` and `fingrowth_*.duckdb` unless `--finproj-db` / `--fingrowth-db` / `--no-finproj`.

Before treating a model terminal as support, read `src/feedback_models` open lessons. An empty `fp_terminal_px` is not confirmation. A terminal many times the price, or one that sits far under the price, is a data gap or a reject. A weeks `bo` into an earnings print does not override a pack `SELL_THE_NEWS` course. Re-run the model first with `run_operator_suites.py finproj-growth` / `finproj-price` or `wisdom --run-finproj`.

Wisdom `datasets/` (or `run_operator_suites.py sources`) records that value-projection path next to all-fields, prediction, edge, MTP, and backtest family IC. Edge upside is already inside pack leftover. Do not replace `fp_*` with it.

**Cycle scenarios** (what a price assumes about a boom ending): `python src/generic_utils/tv_scan_cli.py cycle --pack PACK --ids NVDA,SNDK --line`
(optional `--shocks CSV`: symbol, role, one column per scenario of percent price moves; `generic_utils/cycle_scenarios.py`, knobs
`config/generic_utils/cycle_scenarios.json`). From the newest `finvalue_*` `value_names` plus PACK/deterioration names: year 1 = the street EPS,
k more boom years at the year-1 pace halved each year (cap 30%), an EPS drop d from the peak, a two-year climb to peak x (1 - d/2), owner cash
0.8 x EPS, a through-cycle exit P/E (10, 15, industry) at year 5, discounted at the value run's hurdle. Tables `names` (`drop_in_k<k>` = the
bust the close already pays for at 15x; `pe_need_k<k>_d<d>` = the exit P/E the close needs; `k_needed_d<d>`; `up_k<k>_d<d>`; margin vs the
industry), `grid` (every k x d x exit with the EPS path, `cum_eps_vs_close_pct`, `pe_on_norm`, IRR) and `stress` (Book dollars per shock
scenario, a `BOOK total` row). Read: a low `pe_need` at k0 = the price already pays for an early bust (memory at peak margins); a high
`pe_need` even at k2 / d25 = the price needs the boom to continue and the danger is the multiple. A 50% drop fits memory; read d25 for the
rest. Scenarios, not forecasts; shocks and odds are judgment. Page pattern: `logs/canvases/20261103/2317_ide/ai-cycle-20261103.json`.

**Peer value** (premium to industry peers, earned or not): `python src/generic_utils/tv_scan_cli.py peer-value --pack PACK --ids A,B --line`
(`generic_utils/peer_value.py`, knobs `config/generic_utils/peer_value.json`, page template `peer_value`, refresh suite `peer_value`).
Peers are the TradingView industry on the panel store's newest day (the sector when fewer than `min_peers`). Per lens (forward P/E, EV/EBITDA,
EV/sales) the log multiple is fitted inside the group on z-scored growth and quality drivers (next-FY revenue / EPS growth, operating margin,
ROE, FCF margin, 3-month EPS revisions):

- `prem_pct` = premium to the peer median; `just_pct` = the premium the name's profile earns in that fit; `gap_pct` = fitted vs actual multiple
  (positive = cheaper than peers with the same growth / quality); `px_fit` = the price at the fitted multiple (a peer level, kept apart from
  street, pack and model prices).
- Read the pair: a discount with `gap_pct` near 0 is earned (the profile explains it: a peer value trap); a premium with `gap_pct` > 0 is still
  under what the profile earns; `gap_pct` < 0 is paying for more than the profile delivers.
- `groups.pay_<driver>` says what an industry pays for (multiple % per 1 sd). A growth premium is only rewarded where `pay_rev_nfy_g` is positive.
- `priced_in_cagr_pct_vs_ind` / `irr_exp_pct_vs_ind`: the forward value run's priced-in growth and IRR against the group's median.
- Fit quality (patch 0003): an industry with fewer than `min_fit_n` names with a positive yield borrows the sector's slopes at its own level
  (`fit_on` = sector, `fit_n` = the sector's count); shrinkage grows with the group (`ridge_per_name`); growth / quality drivers
  (`expect_positive`) never lower a multiple; `fit_loo_r2` is the leave-one-out R2 against the median of the other peers, and a fit at or under
  `min_loo_r2` is not used (`fit_used` = 0: `just_pct` 0, `gap_pct` = the plain discount). On 23 Nov the median fit's LOO R2 was 0.04: read
  `gap_pct` beside `fit_loo_r2`, and a sector fit (`fit_on` = sector) as weaker than a group fit. Board / brief: `peer_fit_on`,
  `peer_fit_loo_r2`, `peer_fit_used`.
- Caveats: cyclicals at peak margins (memory) fit as very cheap (read `cycle` beside it); misclassified names (a solar maker in Semiconductors)
  fit against the wrong peers.
- Evidence (29 store days, 9 Jun - 3 Nov 2026, US $2B+): the adjusted gap beat a plain peer discount at every horizon (28d IC 0.024 vs 0.012,
  56d 0.043 with 74% positive days vs 61%; top-fifth 28d excess +1.9 vs +0.4 for the middle fifth, a plain discount was flat). Direction,
  not proof (t under 2); re-read `evidence` before leaning on it.

## Do not

- Invent a composite “forward score”
- Substitute EV/Rev for missing `pe_fwd`
- Treat this dump as Build-50, movers, or Book actions
- Write `logs/_tmp_*.py` or `SELECT *` on `all_fields_rows`
- Require a canvas for FORWARD alone — DuckDB + `overview.log` is that deliverable
- Skip the Wisdom ledger canvas. Wisdom always gets `wisdom-YYYYMMDD` with one Ledger. The 1000-name tables stay in DuckDB unless the paste says `data lists`

Generic vs domain: `.claude/skills/generic-vs-domain/SKILL.md`.
Manual catalog of every suite: `src/run_operator_suites.py`.
