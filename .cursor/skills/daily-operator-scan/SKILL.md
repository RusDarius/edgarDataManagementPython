---
name: daily-operator-scan
description: Compile and brief the daily active-manager leftover scan (Book, Build-50 ranking, Top 100 unpaid appendix, shorts overlay, earnings lanes, daily-focus canvas). Use when the user asks for a daily focus, operator briefing, leftover radar, or Top 100 unpaid scan. Do not use for price movers, groovers, or tape-leader scans — that is price-movers.
---

# Daily operator scan

Deterministic scores already exist. This skill is the **reuse contract**: compile a briefing pack, then reason. Do not re-fish DuckDB, invent a 0–100 composite, or write `_tmp_*.py` under `logs/`.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_operator_suites.py --gitbash
  (Prompt | Command | Usage/output table; raw dumps, not canvases)
python src/operator_briefing/example_entry.py compile
python src/operator_briefing/example_entry.py inspect
python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD
python src/operator_briefing/example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX
python src/operator_briefing/example_entry.py stance MU SNDK PGY JD
```

Pin runs: `compile --pred-run RUN --af-run RUN`. Compare two pred ids: `compare --run-a RUN --run-b RUN`. DAILY compare uses `--session --exchanges NASDAQ,NYSE,AMEX` so gainers are not intra-day OTC noise. `street_px` / `target_px` on pack names are price levels (street leftover vs pack leftover).

Read the newest `logs/tradingview_analysis/operator_briefing/runs/briefing_pack_*/briefing_pack.md` **and** `.json`. Use pack `primary_course`, `suggested_courses`, `stances`, `names`, `progression`. If compile fails, say so and fall back to attached run folders. Do not skip continuity.

Delegated name/progression work: `.cursor/skills/scan-evidence/SKILL.md`.
Generic TV interrogation (schema, group stats, FOCUS CSV, named SQL, field history): `.cursor/skills/tv-dataset-analysis/SKILL.md`.
Manual dumps of every suite (value/tech, forward, movers, risk, finproj) without a canvas: `python src/run_operator_suites.py --list`.
Before inventing SQL, a `_tmp_*.py`, or a new cutoff: `.cursor/skills/generic-vs-domain/SKILL.md`.
Canvas abbreviations: `.cursor/skills/canvas-ledger/SKILL.md` — exactly one Ledger menu for every symbol. Do not repeat ledgers on other tabs.

Prompt text: `src/focus_pool_screening/prompts/daily_generic.txt`. Earnings-lane / canvas-layout / manual DuckDB runs: `daily_generic_reference.md` (do not @ it on DAILY (full)). Overrides: `overrides_template.txt`. Copy-paste: `src/focus_pool_screening/prompts/agent_copy_paste.txt`. DAILY (full) is week map, **Build-50** (`radar_curated_50`) ranking of best trades / positions to build on, Top 100 unpaid ∪ continuation appendix, short 15, earnings both ways, operator High/Med/Low, plus **one Tape/Support section** (day / week / 5D / 1M). Scoring layers: `src/generic_utils/specs/`. 3M tape is --- MOVERS (3M) ---. Do not attach `movers_generic.txt` on DAILY (full).

## Work order

1. Source lock from pack `sources`. Cite run_ids.
2. Continuity from pack `book[].continuity` plus `logs/AI_ANALYSIS_UTILS/priors/*_operator_actions.json`. Polar ADD/NEW → EXIT is blocked unless `thesis_kill` is true.
3. Regime from pack `regime.us_2b` and `industries_5d` **before** naming stocks. Name-level day/week/5D/1M groovers go in the canvas **Tape** section (pack `movers` / DuckDB `movers_tails`): **ranked Top 25 up** by that horizon’s return (best → least) and a **bottom 25 punished pool** split **BOUNCE** vs **CONTINUE_DOWN** (`down_class`). Tables include **Val / Peer / Proj / Tech** on the same tab. Not extra leftover rank and not four extra tabs.
4. Book: one action per holding. Prefer pack continuity, then pack `stances`. Cite pack `capital` (NAV = mark + cash) and `wt_nav`. Full loss ladders are --- RISK --- (`.cursor/skills/book-risk/SKILL.md`) → own canvas `book-risk-YYYYMMDD` plus `--out-dir/book_risk.md`. Do not steal Daily tabs.
5. Earnings 0–7 / 8–21 / 22–60 from pack `earnings_lanes`. Publish **upside** and **downside** lists (curate 8–12 each). Catalyst policy PASS-all is not a filter. Unix epoch dates in all-fields are parsed by the pack. Do not double-size a Book ADD as a rumour ticket.
6. Week map (today / rest of week / 8–21d / 22–60d) before naming extra stocks.
7. Radar from pack **sleeves** — headline **Build-50** from `radar_curated_50` (industry-capped, Book exempt, overflow on `radar_industry_overflow`). Cover all 50 on the canvas Radar tab (filters OK). `radar_curated_25` is the first 25 of that list. Uncapped `radar_upside_100` / `short_book_15` / `.upside` / `.downside` are appendix-only. Cite each row's `sleeve_tags`/`sleeve_count`. Then pack `suggested_courses` (ranked **inside** ADD / NEW / WAIT / PASS / SHORT_WAIT) seeds the Plan tab — do not re-derive the budget by hand. Do not paste leftover-first dumps. Do not flatten those buckets into one score. Auto NEW is not the 0–2 NEW budget. Layer map: `src/generic_utils/specs/scoring_layer_order.md`.
8. Canvas: `daily-focus-YYYYMMDD.canvas.tsx`. Chat is a short snapshot + canvas link. Breadth, **Value** (`val_field` / peer / efficiency — not EV/Rev for every name), **Tech** (`tech_sma` / rng / RSI / vs50), Book positioning, and **one Tape/Support section** (clusters + BOUNCE / CONTINUE_DOWN). Not four Daily mover tabs. If the user also asked --- MOVERS ---, full 25s go on **one** `movers-YYYYMMDD` (price-movers skill) — Daily Tape stays condensed and points there. 3M stays off Daily. Required: exactly one **Ledger** menu (`.cursor/skills/canvas-ledger/SKILL.md`).
9. Rankings omit: read `radar_industry_overflow` / high-`rr` unused-range rows in `radar_upside_100` before locking NEW. Do not add a new leftover/RSI cutoff to "clean" the list.
10. After the brief, log one generic-vs-domain note (what was reused, what should be promoted). See `.cursor/skills/generic-vs-domain/SKILL.md`.

## Decision rules (do not renegotiate)

- Mix `avoid_value_trap` on a live leftover Book line → HOLD_NO_ADD, not EXIT.
- Prior DERISK sticks while dte ≤ 7. Prior TRIM sticks until tape repairs. Prior HOLD_NO_ADD does not silently become HOLD.
- MTP `ENTER_SMALL` / `ENTER_PROBE` → timing climate, not a buy list.
- Leftover = `left` in the pack = max(street PT, edge forward upside). Do not average it with conviction.
- `rr` = leftover / max(52w range, 15). High = unused upside vs already-paid range. Not a 0–100 score.
- Book risk: `wt_nav` = mark / NAV. `wipe_nav_pct` is the name-goes-to-zero ceiling. `m15_nav` is a further −15% from mark as % of NAV. Not a 0–100 risk score. Full ladders: `tv_scan_cli.py risk`.
- `suggested_conviction` is **support for a named course** (0.15–0.90), not mix and not a probability. Always quote `evidence` and `conflicts`. Operator then assigns High / Med / Low.
- Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) → watch, do not chase.
- Shorts need limited leftover (pack shorts sleeve / SHORT_WAIT). Do not overlay-short a leftover-91 EXIT.
- A table that just relays a raw pack sleeve, or an Operator/Action column that is only differentiated for a hardcoded handful of tickers, is a pack dump even with correct numbers. Every row needs a course/conflict-derived reason.
- Default budget: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short.
- After publishing, write `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

## Schema cheat sheet

See [reference.md](reference.md).
