---
name: daily-operator-scan
description: Compile and brief the daily active-manager scan from holdings scoring, move-prediction, all-fields, market-timing-policy, and edge leftover. Use when the user asks for a daily focus, operator briefing, leftover radar, Top 30/50 scan, shorts overlay, canvas daily-focus, or to analyse today's datasets for trades.
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

Prompt text: `src/focus_pool_screening/prompts/daily_generic.txt` (themed file only if the user asked for that sleeve). Overrides: `overrides_template.txt`.

## Work order

1. Source lock from pack `sources`. Cite run_ids.
2. Continuity from pack `book[].continuity` plus `logs/AI_ANALYSIS_UTILS/priors/*_operator_actions.json`. Polar ADD/NEW → EXIT is blocked unless `thesis_kill` is true.
3. Regime from pack `regime.us_2b` and `industries_5d` **before** naming stocks.
4. Book: one action per holding. Prefer pack continuity, then pack `stances`.
5. Earnings 0–21d from pack. Catalyst policy PASS-all is not a filter.
6. Radar from pack **sleeves**, then pack `suggested_courses` (ranked **inside** ADD / NEW / WAIT / PASS / SHORT_WAIT). Do not paste leftover-first dumps. Do not flatten those buckets into one score.
7. Canvas: `daily-focus-YYYYMMDD.canvas.tsx`. Chat is a short snapshot + canvas link.

## Decision rules (do not renegotiate)

- Mix `avoid_value_trap` on a live leftover Book line → HOLD_NO_ADD, not EXIT.
- MTP `ENTER_SMALL` / `ENTER_PROBE` → timing climate, not a buy list.
- Leftover = `left` in the pack = max(street PT, edge forward upside). Do not average it with conviction.
- `suggested_conviction` is **support for a named course** (0.15–0.90), not mix and not a probability. Always quote `evidence` and `conflicts`.
- Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) → watch, do not chase.
- Shorts need limited leftover (pack shorts sleeve / SHORT_WAIT). Do not overlay-short a leftover-91 EXIT.
- Default budget: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short.
- After publishing, write `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

## Schema cheat sheet

See [reference.md](reference.md).
