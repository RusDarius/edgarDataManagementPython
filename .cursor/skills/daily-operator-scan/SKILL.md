---
name: daily-operator-scan
description: Compile and brief the daily active-manager scan from holdings scoring, move-prediction, all-fields, market-timing-policy, and edge leftover. Use when the user asks for a daily focus, operator briefing, leftover radar, Top 100 scan, shorts overlay, canvas daily-focus, or to analyse today's datasets for trades.
---

# Daily operator scan

Deterministic scores already exist. This skill is the **reuse contract**: compile a briefing pack, then reason. Do not re-fish DuckDB, invent a 0–100 composite, or write `_tmp_*.py` under `logs/`.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py compile
python src/operator_briefing/example_entry.py inspect
python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD
python src/operator_briefing/example_entry.py compare
python src/operator_briefing/example_entry.py stance MU SNDK PGY JD
```

Pin runs: `compile --pred-run RUN --af-run RUN`. Compare two pred ids: `compare --run-a RUN --run-b RUN`.

Read the newest `logs/tradingview_analysis/operator_briefing/runs/briefing_pack_*/briefing_pack.md` **and** `.json`. Use pack `primary_course`, `suggested_courses`, `stances`, `names`, `progression`. If compile fails, say so and fall back to attached run folders. Do not skip continuity.

Delegated name/progression work: `.cursor/skills/scan-evidence/SKILL.md`.
Generic TV interrogation (schema, group stats, FOCUS CSV, named SQL): `.cursor/skills/tv-dataset-analysis/SKILL.md`.

Prompt text: `src/focus_pool_screening/prompts/daily_generic.txt` (themed file only if the user asked for that sleeve). Overrides: `overrides_template.txt`. Copy-paste chat blocks: `src/focus_pool_screening/prompts/agent_copy_paste.txt`. DAILY (full) is the operator's spoken scan (week map, Top 100 unpaid ∪ continuation, short 15, earnings both ways, late-Sep rumour, operator High/Med/Low).

## Work order

1. Source lock from pack `sources`. Cite run_ids.
2. Continuity from pack `book[].continuity` plus `logs/AI_ANALYSIS_UTILS/priors/*_operator_actions.json`. Polar ADD/NEW → EXIT is blocked unless `thesis_kill` is true.
3. Regime from pack `regime.us_2b` and `industries_5d` **before** naming stocks.
4. Book: one action per holding. Prefer pack continuity, then pack `stances`.
5. Earnings 0–7 / 8–21 / 22–60 from pack `earnings_lanes`. Publish **upside** and **downside** lists (curate 8–12 each). Catalyst policy PASS-all is not a filter. Unix epoch dates in all-fields are parsed by the pack. Do not double-size a Book ADD as a rumour ticket.
6. Week map (today / rest of week / 8–21d / 22–60d) before naming extra stocks.
7. Radar from pack **sleeves** — headline from `radar_curated_25` / `short_book_15_curated` / `earnings_lanes.upside_curated` / `.downside_curated` (industry-capped, Book exempt, overflow visible on `*_overflow`); the uncapped `radar_upside_100` / `short_book_15` / `.upside` / `.downside` are appendix-only. Cite each row's `sleeve_tags`/`sleeve_count`. Then pack `suggested_courses` (ranked **inside** ADD / NEW / WAIT / PASS / SHORT_WAIT) seeds the Plan tab — do not re-derive the budget by hand. Do not paste leftover-first dumps. Do not flatten those buckets into one score. Auto NEW is not the 0–2 NEW budget.
8. Canvas: `daily-focus-YYYYMMDD.canvas.tsx`. Chat is a short snapshot + canvas link.

## Decision rules (do not renegotiate)

- Mix `avoid_value_trap` on a live leftover Book line → HOLD_NO_ADD, not EXIT.
- Prior DERISK sticks while dte ≤ 7. Prior TRIM sticks until tape repairs. Prior HOLD_NO_ADD does not silently become HOLD.
- MTP `ENTER_SMALL` / `ENTER_PROBE` → timing climate, not a buy list.
- Leftover = `left` in the pack = max(street PT, edge forward upside). Do not average it with conviction.
- `rr` = leftover / max(52w range, 15). High = unused upside vs already-paid range. Not a 0–100 score.
- `suggested_conviction` is **support for a named course** (0.15–0.90), not mix and not a probability. Always quote `evidence` and `conflicts`. Operator then assigns High / Med / Low.
- Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) → watch, do not chase.
- Shorts need limited leftover (pack shorts sleeve / SHORT_WAIT). Do not overlay-short a leftover-91 EXIT.
- A table that just relays a raw pack sleeve, or an Operator/Action column that is only differentiated for a hardcoded handful of tickers, is a pack dump even with correct numbers. Every row needs a course/conflict-derived reason.
- Default budget: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short.
- After publishing, write `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

## Schema cheat sheet

See [reference.md](reference.md).
