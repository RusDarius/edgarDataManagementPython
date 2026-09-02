---
name: daily-operator-scan
description: Compile and brief the daily active-manager scan from holdings scoring, move-prediction, all-fields, market-timing-policy, and edge leftover. Use when the user asks for a daily focus, operator briefing, leftover radar, Top 30/50 scan, shorts overlay, canvas daily-focus, or to analyse today's datasets for trades.
---

# Daily operator scan

Deterministic scores already exist. This skill is the **reuse contract**: compile a briefing pack, then reason. Do not re-fish DuckDB or invent a 0–100 composite.

## First command

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py
```

Pin runs if the user attached specific ids:

```powershell
python src/operator_briefing/example_entry.py --pred-run RUN_ID --af-run RUN_ID
```

Read the newest `logs/tradingview_analysis/operator_briefing/runs/briefing_pack_*/briefing_pack.md` **and** `.json`. If compile fails, say so and fall back to the attached run folders. Do not skip continuity.

Prompt text: `src/focus_pool_screening/prompts/daily_generic.txt` (or the themed file only if the user asked for that sleeve). Overrides: `overrides_template.txt`.

## Work order

1. Source lock from the pack `sources` object. Cite run_ids.
2. Continuity from pack `book[].continuity` plus `logs/AI_ANALYSIS_UTILS/priors/*_operator_actions.json`. Polar ADD/NEW → EXIT is blocked unless `thesis_kill` is true.
3. Regime from pack `regime.us_2b` and `industries_5d` **before** naming stocks. Leadership is whatever 5D/1M actually lead.
4. Book: one action per holding. Prefer pack continuity action over naive mix.
5. Earnings 0–21d from pack. Catalyst policy PASS-all is not a filter.
6. Radar from pack **sleeves** (unpaid / forming / continuation_paid / shorts_limited_upside), then curate. Do not paste auto leftover-first dumps.
7. Canvas: `daily-focus-YYYYMMDD.canvas.tsx` in the workspace canvases dir. Chat is a short snapshot + canvas link.

## Decision rules (do not renegotiate)

- Mix `avoid_value_trap` on a live leftover Book line → HOLD_NO_ADD, not EXIT.
- MTP `ENTER_SMALL` / `ENTER_PROBE` → timing climate, not a buy list.
- Leftover = `left` in the pack = max(street PT, edge forward upside). Do not average it with conviction.
- Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) → watch, do not chase.
- Shorts need limited leftover (pack shorts sleeve). Do not overlay-short a leftover-91 EXIT.
- Default budget: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short.
- After publishing, write `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

## Schema cheat sheet

See [reference.md](reference.md) for column names, table map, and known gotchas.

## Output

Primary: Cursor canvas. Chat: bias, 5–8 actions, earnings alerts, canvas path. No giant markdown tables when the canvas exists.
