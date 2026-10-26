---
name: daily-operator-scan
description: Compile and brief the daily active-manager leftover scan (Book, Build-50 ranking, Top 100 unpaid appendix, shorts overlay, earnings lanes, daily-focus canvas). Use when the user asks for a daily focus, operator briefing, leftover radar, or Top 100 unpaid scan. Do not use for price movers, groovers, or tape-leader scans — that is price-movers. Do not use for week/month IC plans — that is backtest-horizon.
---

# Daily operator scan

**Canvas = operator HTML page** (JSON spec in `logs/canvases/` rendered by `tv_scan_cli.py page`, see `.claude/skills/canvas-ledger/SKILL.md`). Not a Cursor `.canvas.tsx`.

Deterministic scores already exist. This skill is the **reuse contract**: compile a briefing pack, then reason. Do not re-fish DuckDB, invent a 0–100 composite, or write `_tmp_*.py` under `logs/`.

If the pasted block says `duckdb output`, stop at the pack folder. Write `daily-focus-YYYYMMDD` only when it says `canvas output`. Name tables on that canvas only when it also says `data lists`. Selection columns on any name table: `opm`, `pe`, `pe_fwd`, `peg`, `dte`, `bo`/`cont`/`fwd`, `rsi`, `rng`, `vs50`, `vs200`, `tech_sma`, `peer`, `val_vs_ind`, `val_ind_med`, `val_vs_avg`, `val_ind_avg`, `eff_vs_ind`, `eff_vs_avg`, `val`, and street / pack / model prices kept separate. Median percent and average percent are different columns. The peer label uses `val_vs_ind` only. Each other relative metric on the row is `FIELD_vs_ind` plus `FIELD_vs_avg` (and the median and average levels). A bin label (`ABOVE`/`BELOW`, `DISCOUNT`/`PREMIUM`/`INLINE`, `UNUSED`/`MID`/`PAID`) is shown with its percent: `vs50` beside the 50-day label, both peer percents beside Peer, `rng` beside the range label. On a canvas, add those columns even when the page is not a full data list.

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

Delegated name/progression work: `.claude/skills/scan-evidence/SKILL.md`.
Generic TV interrogation (schema, group stats, FOCUS CSV, named SQL, field history): `.claude/skills/tv-dataset-analysis/SKILL.md`.
Manual dumps of every suite (value/tech, forward, movers, risk, finproj) without a canvas: `python src/run_operator_suites.py --list`.
Before inventing SQL, a `_tmp_*.py`, or a new cutoff: `.claude/skills/generic-vs-domain/SKILL.md`.
Canvas abbreviations: `.claude/skills/canvas-ledger/SKILL.md` — exactly one Ledger menu for every symbol. Do not repeat ledgers on other tabs.

Prompt text: `src/research_prompt/daily_generic.txt`. Earnings-lane / canvas-layout / manual DuckDB runs: `src/research_prompt/daily_generic_reference.md` (do not @ it on DAILY (full)). Overrides: `src/research_prompt/overrides_template.txt`. Copy-paste: `src/research_prompt/agent_copy_paste.txt`. DAILY (full) is week map, **Build-50** (`radar_curated_50`) ranking of best trades / positions to build on, Top 100 unpaid ∪ continuation appendix, short 15, earnings both ways, operator High/Med/Low, plus **one Tape/Support section** (day / week / 5D / 1M). Scoring layers: `src/generic_utils/specs/`. 3M tape is --- MOVERS (3M) ---. Do not attach `src/research_prompt/movers_generic.txt` on DAILY (full).

## Work order

1. Source lock from pack `sources` (all-fields, prediction, edge, MTP). When `datasets/` exists, also cite backtest `family_metrics` and the value-projection dumps. MTP stays climate. Backtest IC does not change ADD vs WAIT — that plan is backtest-horizon.
2. Continuity from pack `book[].continuity` plus the brief-history digest (`python src/generic_utils/tv_scan_cli.py briefs`, then `digest.md`). Polar ADD/NEW → EXIT is blocked unless `thesis_kill` is true. Do not re-read every prior JSON. Open one prior only for a BAD or MISSED name that needs its original reason. Skill: `.claude/skills/brief-history/SKILL.md`.
3. Regime from pack `regime.us_2b` and `industries_5d` **before** naming stocks. Name-level day/week/5D/1M groovers go in the canvas **Tape** section (pack `movers` / DuckDB `movers_tails`): **ranked Top 25 up** by that horizon’s return (best → least) and a **bottom 25 punished pool** split **BOUNCE** vs **CONTINUE_DOWN** (`down_class`). Tables include **Val / Peer / Proj / Tech** on the same tab. Not extra leftover rank and not four extra tabs.
4. Book: one action per holding. Prefer pack continuity, then pack `stances`. Cite pack `capital` (NAV = mark + cash) and `wt_nav`. Full loss ladders are --- RISK --- (`.claude/skills/book-risk/SKILL.md`) → own canvas `book-risk-YYYYMMDD` plus `--out-dir/book_risk.md`. Do not steal Daily tabs.
5. Earnings 0–7 / 8–21 / 22–60 from pack `earnings_lanes`. Publish **upside** and **downside** lists (curate 8–12 each). Catalyst policy PASS-all is not a filter. Unix epoch dates in all-fields are parsed by the pack. Do not double-size a Book ADD as a rumour ticket.
6. Week map (today / rest of week / 8–21d / 22–60d) before naming extra stocks.
7. Radar from pack **sleeves** — headline **Build-50** from `radar_curated_50` (industry-capped, Book exempt, overflow on `radar_industry_overflow`). Cover all 50 on the canvas Radar tab (filters OK). `radar_curated_25` is the first 25 of that list. Uncapped `radar_upside_100` / `short_book_15` / `.upside` / `.downside` are appendix-only. Cite each row's `sleeve_tags`/`sleeve_count`. Then pack `suggested_courses` (ranked **inside** ADD / NEW / WAIT / PASS / SHORT_WAIT) seeds the Plan tab — do not re-derive the budget by hand. Do not paste leftover-first dumps. Do not flatten those buckets into one score. Auto NEW is not the 0–2 NEW budget. Layer map: `src/generic_utils/specs/scoring_layer_order.md`.
7b. Payoff focus, ten names, from that same Build-50 table. `python src/generic_utils/tv_scan_cli.py payoff-focus --db PACK.duckdb --table radar_curated_50 --out-dir PACK/payoff_focus`. Sleeves are `discount_momentum` and `premium_strength`. Order is `band_payoff` (upside from `entry_high` to `value_px`, over the stop), not `rr`. `value_px` is the year-1 PE at the peer median. `discount_px` is the quoted multiple brought inline. Street stays `exit_target`. `stake_per_1k_usd` is still the street check that earns $1,000. `stake_per_1k_value` is that check at `value_px`. The ten are not a second NEW budget. A row still needs a pack course.
7c. Focus band on every action row and on that ten. Quote invest `entry_low` to `entry_high`, trim `trim_px` with `trim_state`, and kill `kill_px`. When cost is on the row, also quote `cost_kill_px` (15% under cost). `trim_at_sma` means a print at the 50-day lightens a held line and does not end the entry. `through_average` means the trim already printed and `trim_px` is empty. Do not buy a reclaim name at the mark. Above the band is not a new fill. Starter about $5,000. An add to a held line can be about $1,000. Run `tv_scan_cli.py entry-book` on the book and on the focus CSV so those prices come from `generic_utils.entry_book`, not from a hand formula.
7d. Capital and recycle. Write the Plan courses (EXIT / TRIM / NEW / ADD, with `px` from the band and `frac` for a trim) to `PACK/capital_plan/plan.csv`, then `python src/generic_utils/tv_scan_cli.py capital-plan --plan PACK/capital_plan/plan.csv --pack PACK/briefing_pack.json --out-dir PACK/capital_plan`. Quote `cash_end_pct`, `starters_left_at_hint`, and industry `nav_pct_after` on the Plan tab. A NEW flagged `under_starter_hint` is resized or dropped, not shipped small. Do not sum cash by hand.
8. Canvas: write `logs/canvases/YYYYMMDD/judgment.json` (courses for every Book / Build-50 / earnings / shorts / tape name you rule on, page blocks, capital_plan rows, extra scans) and render `tv_scan_cli.py page --template daily_focus --day YYYYMMDD --judgment ... --out-dir RUN_DIR` (canvas-ledger). RUN_DIR comes from the runner prompt; from an IDE paste open it first with `tv_scan_cli.py page-run --day YYYYMMDD --mode full`. Do not hand-write spec JSON. Later refreshes the same day are `daily-update`. Name tables use `briefing_pack.duckdb` tables (`book`, `radar_curated_50`, `short_book_15_curated`, `earnings_*_curated`, `movers_tails`) as their `source`; the spec adds Course / Grade / Reason. Chat is a short snapshot + link to the HTML. Breadth, **Value** (`val_field` / peer / efficiency — not EV/Rev for every name), **Tech** (`tech_sma` / rng / RSI / vs50), every Book row, all of Build-50, shorts, earnings both ways, and **one Tape/Support section** with the ranked Top 25 up and BOUNCE versus CONTINUE_DOWN. Not four Daily mover tabs. If the user also asked --- MOVERS ---, the same 25s also go on **one** `movers-YYYYMMDD`. That movers page does not replace the Daily tape tables. A Daily tape that only says "see movers" is incomplete. 3M stays off Daily. Required: exactly one **Ledger** menu (`.claude/skills/canvas-ledger/SKILL.md`).
9. Rankings omit: read `radar_industry_overflow` / high-`rr` unused-range rows in `radar_upside_100` before locking NEW. Do not add a new leftover/RSI cutoff to "clean" the list.
10. After the brief, log one generic-vs-domain note (what was reused, what should be promoted). See `.claude/skills/generic-vs-domain/SKILL.md`.

## Decision rules (do not renegotiate)

- Mix `avoid_value_trap` on a live leftover Book line → HOLD_NO_ADD, not EXIT.
- Prior DERISK sticks while dte ≤ 7. Prior TRIM sticks until tape repairs. Prior HOLD_NO_ADD does not silently become HOLD.
- MTP `ENTER_SMALL` / `ENTER_PROBE` → timing climate, not a buy list.
- Leftover = `left` in the pack = max(street PT, edge forward upside). Do not average it with conviction. When `street_left` is negative and `left` is positive, `generic_utils.derive.leftover_carrier` is `edge_only`. Cite `street_px` beside `left`. Do not spend a NEW on `edge_only`.
- `rr` = leftover / max(52w range, 15). High = unused upside vs already-paid range. Not a 0–100 score.
- Payoff focus `band_payoff` is a different ratio: upside from `entry_high` to `value_px` (year-1 inline), divided by the distance down to `exit_stop`. A year-1 price already under the band does not fall back to street. It does not replace `rr` and it does not spend a NEW slot.
- Book risk: `wt_nav` = mark / NAV. `wipe_nav_pct` is the name-goes-to-zero ceiling. `m15_nav` is a further −15% from mark as % of NAV. Not a 0–100 risk score. Full ladders: `tv_scan_cli.py risk`.
- `suggested_conviction` is **support for a named course** (0.15–0.90), not mix and not a probability. Always quote `evidence` and `conflicts`. Operator then assigns High / Med / Low.
- Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) → watch, do not chase.
- Read `src/feedback_models` open lessons before a NEW into an earnings print. `SELL_THE_NEWS` stays the course when weeks `bo` is high. Do not buy the leftover that reopens the same day the price gaps down.
- Shorts need limited leftover (pack shorts sleeve / SHORT_WAIT). Do not overlay-short a leftover-91 EXIT.
- A table that just relays a raw pack sleeve, or an Operator/Action column that is only differentiated for a hardcoded handful of tickers, is a pack dump even with correct numbers. Every row needs a course/conflict-derived reason.
- Default budget: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short.
- Starter size to bear in mind: about $5,000 for a new line. A smaller new position is not a useful book slot. An add to a line already held can be about $1,000 and up. This is advice on the recommendation, not a hard cash target and not a ranking cutoff. Do not put it in `ranking.py`.
- After publishing, write priors with `python src/generic_utils/tv_scan_cli.py priors --day YYYYMMDD --notes "<plan line>"` (copies the judgment courses, every key `EXCHANGE:TICKER`). Do not hand-write the priors JSON.
- Every stock id on the page, in the judgment, the capital plan and chat is `EXCHANGE:TICKER`. A bare ticker is input only.

## Schema cheat sheet

See [reference.md](reference.md).
