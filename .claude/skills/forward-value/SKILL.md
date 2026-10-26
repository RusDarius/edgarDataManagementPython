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

## Do not

- Invent a composite “forward score”
- Substitute EV/Rev for missing `pe_fwd`
- Treat this dump as Build-50, movers, or Book actions
- Write `logs/_tmp_*.py` or `SELECT *` on `all_fields_rows`
- Require a canvas for FORWARD alone — DuckDB + `overview.log` is that deliverable
- Skip the Wisdom ledger canvas. Wisdom always gets `wisdom-YYYYMMDD` with one Ledger. The 1000-name tables stay in DuckDB unless the paste says `data lists`

Generic vs domain: `.claude/skills/generic-vs-domain/SKILL.md`.
Manual catalog of every suite: `src/run_operator_suites.py`.
