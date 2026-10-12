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
| Primary value / efficiency / peer + technical structure | `generic_utils.setup` / `tv_scan_cli.py setup` (recipe `value_tech.json`; street/TV `fwd_*` on every row). Peer bin uses `val_vs_ind` (≤ −5 DISCOUNT, ≥ 5 PREMIUM, else INLINE). ABOVE/BELOW uses `vs50`. | Quoting EV/Rev on a bank, insurer, health-care name, ETF, or profitable software just because it is “clear” or shows a discount; treating the bin as the size of the gap |
| Street vs pack vs model forward price / growth, plus projection PE, EV/EBITDA, debt, FCF (year 1 and up to year 5) | `generic_utils.forward_fundamentals` then `forward_value` / `tv_scan_cli.py forward` / `run_operator_suites.py forward` (recipe `forward_value.json`; join `finproj_*` + `fingrowth_*`) | A 0–100 forward score; filling `pe_fwd` with EV/Rev |
| Growth radar (revenue lane and earnings lane, $1B floor, peer discount, own-PE span) | `src/run_growth_radar.py` / `generic_utils.growth_radar`. Thresholds live in `run_growth_radar_local_main`. Skill: `.cursor/skills/growth-radar/SKILL.md` | A 0–100 that adds revenue growth to earnings growth; baking 15% into `ranking.py`; history on the whole tape |
| Manual dump of every suite (max 1000 names, no industry cap) | `src/run_operator_suites.py` (`wisdom`, `--list`) | `logs/_tmp_*.py`; treating the dump as ADD/WAIT; a 1000-row canvas. Wisdom still gets a ledger canvas (`wisdom-YYYYMMDD`). Name lists only when the paste says `data lists` |
| Six-dataset lock (all-fields, prediction, edge, MTP, backtest family IC, value projection) | `run_operator_suites.py sources` / wisdom `datasets/` (`generic_utils.dataset_lock`) | A rescan of those producers; rerunning `run_backtests.py` unless asked; a 0–100 that blends IC with leftover |
| Best week/month trades and long holds, ranked by asymmetric payoff and by conviction | `.cursor/skills/analysis-style/SKILL.md` after the wisdom dump. `focus --field rr` then `history`/`span` on `trading_view_all_fields_data`. ASYM is the `rr` order among OPENING/CHEAPER names. CONVICTION is the `bo` order of those same names. | A 0–100 that adds `rr` and `bo`; baking the starter where into `ranking.py`; history on the whole tape; news as a sort key or a size |
| Industry breadth, histograms, weighted Book exposure | `aggregations` / `tv_scan_cli.py group` / `weight-group` | Hand-average 5D |
| Top-N / industry cap with overflow visible | `ranking` / `rank_cli.py` | Silent drop of a crowded industry |
| Filter then rank (caller `--where`) | `focus` / `tv_scan_cli.py focus` | Hardcode RSI/leftover in a new script |
| Named columns, field path, score history | `series` / `named` / `history` / `span` | `SELECT *` on `all_fields_rows`; EV/Rev history when `val_field` is not evrev |
| Extra all-fields aliases (PE, fwd PE, PEG, EV/EBITDA, P/B, EV/FCF, week/3M/relvol) | `extract.AF_CANDIDATES` | A `_tmp_` that picks those columns once |
| Unpaid / short / earnings eligibility | `operator_briefing.sleeves` | New cutoff inside `ranking.py` |
| ADD / TRIM / EXIT / High/Med/Low, and starter dollars | daily-operator-scan canvas | A 0–100 mix composite. A new line is sized with about $5,000 in mind; an add to a held line can be about $1,000. That is advice on the recommendation, not a cutoff in `ranking.py`. |
| Day / week / 5D / 1M / 3M both-tail movers | `ranking.horizon_movers` / `tv_scan_cli.py movers --recipe` (recipes pin 25 up / 25 punished, `vs_group`, `street_px`/`target_px`; `drop_duplicate_suffixes` drops `TICKER23` leaks). One canvas `movers-YYYYMMDD` for day/week/5D/1M; 3M is `movers-3m-YYYYMMDD`. | SQL ORDER BY change; a supported-ness or value composite; a new skill per horizon; a canvas per period (`movers-day` / `movers-5d` / `movers-1m`) |
| Punished bounce vs continue-down | `sleeves.classify_punished_tape` / `down_class` on movers_tails | A 0–100 bounce score; leftover-first laggard dump; cutoffs inside `ranking.py` |
| Book NAV / loss ladders / trim-size | `generic_utils.risk` / `tv_scan_cli.py risk --run latest` (recipe `book_risk.json`; `--out-dir` writes `book_risk.md`) | A 0–100 risk score; hand % of NAV; `--config-only` as mark-to-market on FX names; announcing a canvas without writing `book-risk-YYYYMMDD.canvas.tsx` |
| Book price / multiple / score path | `history` / `span` on close, PT, ATRP, **and `val_field`** (not EV/Rev for every name) | A homemade path score; quoting EV/Rev path to manufacture a discount; mixing leftover from a later pred close with an older mark by hand |
| Pred-run Δbo/Δcont/Δfwd | `example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX` | Bare compare (intra-day OTC/LSE dump) as the DAILY progression |
| How prior briefs held up across sessions | `generic_utils.brief_history` / `tv_scan_cli.py briefs` (recipe `brief_history.json`). Read `digest.md`. One name is `events` in that DuckDB | Re-reading every `*_operator_actions.json` note; a 0–100 lesson score; using the label to reorder Build-50, mover 25s, or Best 25 |
| Whether the published top 100 captured the later close, and which slice should change | `src/run_decision_review.py` / `generic_utils.decision_review`. Read `highlights.log`. Name tape is table `ranks`. Skill: `.cursor/skills/decision-review/SKILL.md` | A 0–100 capture score; rerunning `run_backtests.py` to judge a briefing; treating family IC as the briefing rank |
| Print-day afternoon mark vs later cash close and after-hours | `generic_utils.session_tape` / `tv_scan_cli.py session --earn-on YYYY-MM-DD` | Treating `premarket_change` on a pre-open file as that morning's gap when `pre_repeat` is true; a 0–100 earnings score |
| Last N ISO weeks of bo/cont/fwd/early/recov | `run_operator_suites.py weeks-progression --weeks 25` (`series.field_history` + `series_span` on `pred.profile_weeks_pivot`, newest N week files, one run each) | A homemade weekly score; `SELECT *` on profile_horizon_scores; treating span Δbo as a 0–100 |
| Playbook raw inputs (upside 100, shorts, forming, book, 25w path) | `run_operator_suites.py playbook --weeks 25` | Mesh letters or Best 25 inside the dump; a mesh score; a second Daily budget |
| Street / pack price levels | `derive.implied_price` → `street_px` / `target_px` | Recode PT in chat from leftover % |
| Repeated symbol join (projection, trailing, peer maps) | `symbol_identity.SymbolCatalog` once, then `lookup_mapping` | `resolve_symbol(index.keys())` inside a row loop. That rebuilds the catalog on every field. |
| DuckDB leftover column | `named --fields left` or SQL `"left"` (`series.qident`) | `SELECT left` (reserved word); `logs/_tmp_*.py` |
| Manual section DuckDB + overview.log | `run_export.write_run_export` / `tv_scan_cli.py export` | `_tmp_*.py`; SELECT * on all_fields_rows |
| Abbreviation / symbol explainers (rng, rr, leftover, mix, …) | one Ledger tab on the canvas (`.cursor/skills/canvas-ledger/SKILL.md`) | A glossary repeated on every section |
| Scoring layer order / field inventory | `src/generic_utils/specs/` | A homemade 0–100; leftover-first dump as the daily ranking |
| Backtest IC / overlay lift / coverage | `src/backtests` + `rank_cli.py --csv family_metrics.csv --field spearman_ic` (`.cursor/skills/backtest-horizon/SKILL.md`) | `SELECT *` on 15 GB `signal_rows`; a 0–100 horizon score; MTP as a leftover gate |
| Recovery / early-turn / bounce (buy-the-red, forming) | pack `recov` / `early` / `rev` + next backtest `profile_names` + `history`/`span` on close and street PT | A new 0–100 momentum score; ranking leftover by 5D; merging groovers into Build-50 |
| Whole-book path + capture job tags | `generic_utils.capture` / `tv_scan_cli.py capture` / `run_operator_suites.py capture` | A 0–100 capture score; `SELECT *` on all-fields; using only current_holdings.json as the universe |
| Capture job replay vs later closes | `tv_scan_cli.py capture-replay` / `run_operator_suites.py capture-replay` (PIT flags; hindsight jobs labeled; `ras` = mean/stdev) | Feeding future span into PIT flags; treating replay ras as a 0–100; mixing with HORIZON family IC |

3. If the same SQL or `_tmp_*.py` would be written a **third** time, promote it before briefing. Do not ship the third copy.
4. If a ranking omitted a live leftover + unused-range name, do **not** invent a cutoff. Read pack `radar_industry_overflow` and high-`rr` rows on `radar_upside_100`. Present those as opportunity the cap dropped.
5. Holdings marks vs later tape: leftover from pack `names.close` (all-fields). Refresh with `derive --refresh` if the Book close is stale. Do not mix Friday leftover with Tuesday close by hand.

## Current vs future value (standing alias list)

Use these all-fields names via `AF_CANDIDATES` / `named` — do not rediscover them:

- Current: `price_earnings_ttm`, `enterprise_value_to_revenue_ttm`, `enterprise_value_ebitda_ttm`, `price_book_ratio`
- Future / growth-adjusted: projection `fp_pe_y1` / `fp_pe_y5` and `fp_evebitda_y1` / `fp_evebitda_y5` (path, else CAGR of trailing). TradingView `price_earnings_forward_fy` is `pe_fwd_tv` and is empty on recent scans. `price_earnings_growth_ttm` is PEG. Street leftover (`pt` vs close) and edge `forward_valuation_upside_pct` stay separate.

**Quote `val_field` / `val` / `val_vs_ind` / `val_vs_avg` from `generic_utils.setup`.** Median percent and average percent are different columns. The peer label uses the median. Quoted `pe_fwd` is the projection next-year PE. `pe_fwd_tv` stays empty. PEG + leftover + trailing EV/EBITDA still describe the row **when they are the primary field**. Do not fill a missing PE with EV/Rev to manufacture a discount.

Street PT leftover (`fwd_street_*`), pack leftover (`fwd_pack_*`), and `financial_projection` terminals (`fp_terminal_px`, `fp_primary_upside_pct`, own-lane `fp_rev_cagr_own`) are attached by `generic_utils.forward_value` — keep them as separate columns, not a blend.

`leftover_carrier(street_left, left)` in `generic_utils.derive` names who carries the pack number. `edge_only` means street leftover is negative and pack leftover is positive. `both` means both are positive and the edge number is higher. `street` means the street quote is the live one. `none` means pack leftover is missing or not positive, or the street quote is missing. It is a label. It is not a cutoff and not a 0–100. Spending a NEW on `edge_only` when the row has no quoteable multiple is a judgment miss.

## Output

One short note in the daily snapshot:

- Reused: which CLI / module
- Promoted this run: field or procedure added (or "none")
- Still domain: the judgment call that stayed in the canvas
