---
name: scan-evidence
description: Inspect locked scan schemas, look up named tickers with raw scores and progression, compare two move-prediction runs, and suggest a course with exposed support and conflicts. Use when the user asks to prune a radar, look up names, compare runs, explain ADD vs WAIT vs PASS vs SHORT, delegate analysis, or when tempted to write a logs/_tmp_ inspect/extract script.
---

# Scan evidence

Do not write `_tmp_*.py` or one-off extracts under `logs/`. Use the briefing-pack CLI.

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD PATH
python src/operator_briefing/example_entry.py compare
python src/operator_briefing/example_entry.py stance MU SNDK PGY JD
```

Compile first if there is no current `briefing_pack.json`: `python src/operator_briefing/example_entry.py compile`.

## What each command is for

| Command | Returns | Use |
|---|---|---|
| `inspect` | Locked paths, run_ids, tables, key columns, all-fields whitelist | Schema / "what is in today's DuckDB" |
| `lookup` | Per name: `raw`, `book`, `progression`, `stance` | Dossiers, prune a list, quote numbers |
| `compare` | Δbo/Δcont/Δfwd, gainers/losers, new/dropped top-40, mix flips, rising_actionable | Progression between two pred runs |
| `stance` | `stance`, `suggested_conviction`, `course`, `evidence`, `conflicts` | Suggest ADD/NEW/WAIT/PASS/SHORT_WAIT |

`compare` is weeks **profile** deltas, not leftover. Confirm leftover with `lookup` before NEW/ADD.

## How to decide (never 100%)

1. Quote **raw** (left, rsi, rng, bo, cont, fwd, opp, mix, mtp, dte) and **Δbo/Δcont/Δfwd**.
2. Rank **inside** a sleeve: unpaid NEW vs paid PASS vs limited-leftover SHORT_WAIT. Do not merge them into one 0–100.
3. Publish the pack `stance` plus `suggested_conviction` (support 0.15–0.90) and every `conflicts` line.
4. Polar ADD/NEW → EXIT is blocked unless `thesis_kill`. Leftover-91 EXIT is not a short overlay.
5. MTP ENTER_SMALL is bounce climate, not a buy/short list.
6. Strongly prefer one course per name, then say what would flip it (`invalidation`, `next_check`).

If a ticker is `missing` from the pack, recompile (Book/sleeves/earnings watch) rather than fishing DuckDB. Do not invent ranks.
