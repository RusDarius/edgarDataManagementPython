---
name: capture-book
description: Compile whole-book holdings history with fundamentals and profile progression into a deterministic capture dump and canvas. Use when the user asks for whole-book opportunity capture, buy-in-red refinement, momentum/recovery trend evidence, or the CAPTURE copy-paste flow. Not for Daily leftover briefing, Movers tape-only, NAV risk ladders, or backtest horizon planning.
---

# Capture whole-book path

**Which names across the whole held history still carry repeatable upside or recovery evidence over the last 90 days, and where are the data gaps?**

Do not invent a 0-100 composite. Do not write `logs/_tmp_*.py`.

## Reuse (do not fork logic)

| Need | Home |
|---|---|
| Build capture tables | `python src/run_operator_suites.py capture` |
| Replay job tags vs later closes | `python src/run_operator_suites.py capture-replay` (default: latest dump). Any window: `--af-start DD_MM_YYYY --af-end DD_MM_YYYY --rebuild` |
| Direct CLI run | `python src/generic_utils/tv_scan_cli.py capture --lookback-days 90 --pack PACK.json --out-dir RUN/capture` |
| Named progression recipe | `pred.profile_weeks_pivot` in `generic_utils.recipes` |
| Pack lock / source checks | `python src/operator_briefing/example_entry.py inspect` |
| Canvas ledger contract | `.cursor/skills/canvas-ledger/SKILL.md` |

## Required outputs

1. Deterministic dump folder (`capture.duckdb`, `capture.md`, `overview.log`).
2. Canvas `capture-YYYYMMDD.canvas.tsx` with one Ledger tab.
3. Chat snapshot focused on best 1-4 week / 1-2 month moves and follow-up changes.

## Minimum checks

- `exits` includes expected names when present in run history (`PATH`, `TEAM`, `VEEV`, `CSCO`, `CHKP`).
- `fund_path` continues after holding exit dates when tape still has rows.
- `score_path` includes `bo/cont/fwd/early/sms/recov/rev` over the selected window.
- `coverage_fill_rates` reports `n_days` and fill for `close`, `pt`, `pe`, `rsi`.

## Replay (walk-forward on existing data)

`capture-replay` scores the **same** job tags against later `fund_path` closes already on disk.

- **PIT:** leftover / span / scores with `as_of <=` the decision date. No future leak into flags.
- **Hindsight:** end-of-window `jobs` table (full-span knowledge). Labeled separately.
- **ras:** mean / sample stdev of those event returns. Not a 0-100.
- Horizons are **N steps along that symbol's all-fields calendar**, not N calendar days.

Smoke: latest 90-day dump + canvas Replay tab. Any other window that has holdings + all-fields + prediction folders in the needed format can use `--af-start` / `--af-end --rebuild`.

## Optional open backtest section

Use only as an extension after the 90-day run passes:

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/tv_scan_cli.py capture --lookback-days 180 --af-start 01_04_2026 --pack PACK.json --out-dir RUN/capture_01_04
```
