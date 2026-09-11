---
name: forward-value
description: Street / TradingView leftover targets plus financial_projection terminals and growth CAGRs. Use when the user wants forward value projections, street vs model price, PE fwd / PEG vs leftover, or the FORWARD / value-tech dump. Not leftover radar, movers, Book risk, or ADD vs WAIT.
---

# Forward value (street + TV + financial_projection)

Field attach only. Not a 0–100 and not a buy list.

- Street leftover = analyst PT vs close (`fwd_street_pct` / `fwd_street_px`)
- Pack leftover = max(street PT, edge FV) (`fwd_pack_pct` / `fwd_pack_px`)
- `fwd_pe` = `pe_fwd`. Empty is a **data gap** — do not fill with EV/Rev.
- `fp_*` = `financial_projection` join (`scenario=base`, growth `lane=own`)

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

Join uses the latest `finproj_*.duckdb` and `fingrowth_*.duckdb` unless `--finproj-db` / `--fingrowth-db` / `--no-finproj`. Re-run the model first with `run_operator_suites.py finproj-growth` / `finproj-price` or `wisdom --run-finproj`.

## Do not

- Invent a composite “forward score”
- Substitute EV/Rev for missing `pe_fwd`
- Treat this dump as Build-50, movers, or Book actions
- Write `logs/_tmp_*.py` or `SELECT *` on `all_fields_rows`
- Require a canvas — DuckDB + `overview.log` is the deliverable unless the operator asks for one

Generic vs domain: `.cursor/skills/generic-vs-domain/SKILL.md`.
Manual catalog of every suite: `src/run_operator_suites.py`.
