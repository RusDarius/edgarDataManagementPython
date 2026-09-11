---
name: generic-vs-domain
description: Decide whether a scan need belongs in generic_utils (zero-cutoff fetch/aggregate/derive) or in operator_briefing / daily judgment. Use during every daily operator scan, when tempted to write SQL or logs/_tmp_*.py, when rankings omit names, or when adding valuation / history / breadth fields.
---

# Generic vs domain

Run this check on every DAILY (full), every MOVERS scan, and whenever a prompt would invent a one-off extract.

Formulas, group stats, named-column history, top-N with no eligibility cutoff, leftover/range/vs-ref, and all-fields field aliases are **generic**. ADD vs WAIT vs PASS, polar continuity, unpaid/short sleeves, and High/Med/Low are **domain**.

## After compile (required)

1. Name the need in one line (example: "current vs forward multiples on Book + radar").
2. Map it:

| Need | Home | Do not |
|---|---|---|
| Leftover / 52w range / vs-ref / refresh from a later close | `generic_utils.derive` | Recode PT upside in chat |
| Industry median relative (EV/Rev, PE, leftover, 5D) | `derive.attach_vs_group` / `derive --vs-by` / movers recipe `vs_group` | Hand-discount vs remembered peers; a 0–100 value score |
| Primary value / efficiency / peer + technical structure | `generic_utils.setup` / `tv_scan_cli.py setup` (recipe `value_tech.json`; street/TV `fwd_*` on every row) | Quoting EV/Rev on a bank, insurer, health-care name, ETF, or profitable software just because it is “clear” or shows a discount |
| Street vs pack vs model forward price / growth | `generic_utils.forward_value` / `tv_scan_cli.py forward` / `run_operator_suites.py forward` (recipe `forward_value.json`; join `finproj_*` + `fingrowth_*`) | A 0–100 forward score; filling empty `pe_fwd` with EV/Rev |
| Manual dump of every suite (max 1000 names, no industry cap) | `src/run_operator_suites.py` (`wisdom`, `--list`) / `main.run_operator_wisdom_dumps` | `logs/_tmp_*.py`; treating the dump as ADD/WAIT |
| Industry breadth, histograms, weighted Book exposure | `aggregations` / `tv_scan_cli.py group` / `weight-group` | Hand-average 5D |
| Top-N / industry cap with overflow visible | `ranking` / `rank_cli.py` | Silent drop of a crowded industry |
| Filter then rank (caller `--where`) | `focus` / `tv_scan_cli.py focus` | Hardcode RSI/leftover in a new script |
| Named columns, field path, score history | `series` / `named` / `history` / `span` | `SELECT *` on `all_fields_rows`; EV/Rev history when `val_field` is not evrev |
| Extra all-fields aliases (PE, fwd PE, PEG, EV/EBITDA, P/B, EV/FCF, week/3M/relvol) | `extract.AF_CANDIDATES` | A `_tmp_` that picks those columns once |
| Unpaid / short / earnings eligibility | `operator_briefing.sleeves` | New cutoff inside `ranking.py` |
| ADD / TRIM / EXIT / High/Med/Low | daily-operator-scan canvas | A 0–100 mix composite |
| Day / week / 5D / 1M / 3M both-tail movers | `ranking.horizon_movers` / `tv_scan_cli.py movers --recipe` (recipes pin 25 up / 25 punished, `vs_group`, `street_px`/`target_px`; `drop_duplicate_suffixes` drops `TICKER23` leaks). One canvas `movers-YYYYMMDD` for day/week/5D/1M; 3M is `movers-3m-YYYYMMDD`. | SQL ORDER BY change; a supported-ness or value composite; a new skill per horizon; a canvas per period (`movers-day` / `movers-5d` / `movers-1m`) |
| Punished bounce vs continue-down | `sleeves.classify_punished_tape` / `down_class` on movers_tails | A 0–100 bounce score; leftover-first laggard dump; cutoffs inside `ranking.py` |
| Book NAV / loss ladders / trim-size | `generic_utils.risk` / `tv_scan_cli.py risk --run latest` (recipe `book_risk.json`; `--out-dir` writes `book_risk.md`) | A 0–100 risk score; hand % of NAV; `--config-only` as mark-to-market on FX names; announcing a canvas without writing `book-risk-YYYYMMDD.canvas.tsx` |
| Book price / multiple / score path | `history` / `span` on close, PT, ATRP, **and `val_field`** (not EV/Rev for every name) | A homemade path score; quoting EV/Rev path to manufacture a discount; mixing leftover from a later pred close with an older mark by hand |
| Pred-run Δbo/Δcont/Δfwd | `example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX` | Bare compare (intra-day OTC/LSE dump) as the DAILY progression |
| Street / pack price levels | `derive.implied_price` → `street_px` / `target_px` | Recode PT in chat from leftover % |
| DuckDB leftover column | `named --fields left` or SQL `"left"` (`series.qident`) | `SELECT left` (reserved word); `logs/_tmp_*.py` |
| Manual section DuckDB + overview.log | `run_export.write_run_export` / `tv_scan_cli.py export` | `_tmp_*.py`; SELECT * on all_fields_rows |
| Abbreviation / symbol explainers (rng, rr, leftover, mix, …) | one Ledger tab on the canvas (`.cursor/skills/canvas-ledger/SKILL.md`) | A glossary repeated on every section |
| Scoring layer order / field inventory | `src/generic_utils/specs/` | A homemade 0–100; leftover-first dump as the daily ranking |

3. If the same SQL or `_tmp_*.py` would be written a **third** time, promote it before briefing. Do not ship the third copy.
4. If a ranking omitted a live leftover + unused-range name, do **not** invent a cutoff. Read pack `radar_industry_overflow` and high-`rr` rows on `radar_upside_100`. Present those as opportunity the cap dropped.
5. Holdings marks vs later tape: leftover from pack `names.close` (all-fields). Refresh with `derive --refresh` if the Book close is stale. Do not mix Friday leftover with Tuesday close by hand.

## Current vs future value (standing alias list)

Use these all-fields names via `AF_CANDIDATES` / `named` — do not rediscover them:

- Current: `price_earnings_ttm`, `enterprise_value_to_revenue_ttm`, `enterprise_value_ebitda_ttm`, `price_book_ratio`
- Future / growth-adjusted: `price_earnings_forward_fy` (often null — then leftover + PEG), `price_earnings_growth_ttm`, `non_gaap_price_to_earnings_per_share_forecast_next_fy`, street leftover (`pt` vs close), edge `forward_valuation_upside_pct`

**Quote `val_field` / `val` / `val_vs_ind` from `generic_utils.setup`.** Empty `pe_fwd` is a data gap, not a reason to skip Value. PEG + leftover + EV/EBITDA still describe current vs future **when they are the primary field**. Do not fill a missing PE with EV/Rev to manufacture a discount.

Street PT leftover (`fwd_street_*`), pack leftover (`fwd_pack_*`), and `financial_projection` terminals (`fp_terminal_px`, `fp_primary_upside_pct`, own-lane `fp_rev_cagr_own`) are attached by `generic_utils.forward_value` — keep them as separate columns, not a blend.

## Output

One short note in the daily snapshot:

- Reused: which CLI / module
- Promoted this run: field or procedure added (or "none")
- Still domain: the judgment call that stayed in the canvas
