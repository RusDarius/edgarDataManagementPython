---
name: backtest-horizon
description: Translate generic backtest IC/coverage into week/month position choices on the current briefing pack. Use when the user asks for a horizon plan, backtest canvas, best moves for the weeks/month ahead, or the HORIZON copy-paste suite. Not leftover radar (daily-operator-scan), not tape movers (price-movers), not NAV ladders (book-risk).
---

# Backtest horizon plan

**Which locked signals actually discriminated forward returns, and which current unpaid names that implies for the next 1–4 weeks?** Daily leftover answers **can this still pay today**. This suite answers **which layers to trust over a hold**.

Do not invent a 0–100 "horizon score". Do not write `logs/_tmp_*.py`. Do not steal Daily / Movers / Risk tabs.

## Reuse (do not add a parallel engine)

| Need | Home |
|---|---|
| Run / recover backtests | `python src/run_backtests.py` / `--finalize-run` |
| Rank `family_metrics.csv` by IC | `rank_cli.py --field spearman_ic --group-field family --cap 3` (generic-ranking) |
| Current leftover / Book / Build-50 | newest `briefing_pack` via daily-operator-scan `inspect` / `compile` |
| Named raw + Δbo | scan-evidence `lookup` / `compare --session --exchanges NASDAQ,NYSE,AMEX` |
| Street vs pack vs model $ | forward-value (optional; not a 0–100) |

Prefer IC and `day_count` over Top20Lift. Extreme ATRP/ADRP lifts are outliers.

## Standing evidence (default_v1, Jun–Aug 2026 run `295c09eb`)

Trust, do not flatten:

- **Edge artifact** (`upside_opportunity_score`, `move_magnitude_score`, opp rank) at **10–20d**. Thin calendar (edge only late-window).
- **Weeks consensus RAS** modest IC, fuller sample. Keep live `bo/cont/fwd` in the Build-50 sort.
- **MTP artifact score as a ranking signal** at 10–20d. **MTP as a hard filter / weight on leftover picks hurts IC** — climate, not a buy list (already layer 4).
- **Reconstructed** momentum / timing is worse than missing. Wait for the artifact.

Do not:

- Rank leftover names by ATRP / ADRP / `Recommend.All`
- Treat `timing_filter` / `timing_weight` as a gate on Build-50
- Overweight `financial_projection` filters (coverage 4/62 growth, 2/62 price)
- Treat 60d IC as a month plan (few labeled days)

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
python src/generic_utils/rank_cli.py --csv logs/tradingview_analysis/backtests/runs/<newest>/family_metrics.csv --field spearman_ic --top 16 --id-field signal_name --group-field family --cap 3
python src/operator_briefing/example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX --ids MU,SNDK,FSLR,EPAM
python src/operator_briefing/example_entry.py lookup MU SNDK FSLR EPAM
```

Compile the pack only if inspect sources moved. Do not re-run the multi-hour backtest unless the user asked.

## Deliverable

Canvas `horizon-plan-YYYYMMDD.canvas.tsx` (project canvases/). Chat is a short snapshot + markdown link.

Tabs: **Plan / Evidence / Overlays / Scan join / Positions / Follow-up / Ledger**.

Exactly one Ledger (`.cursor/skills/canvas-ledger/SKILL.md`). Cover:

1. Source lock (backtest run_id + pack pred / all-fields / edge / MTP).
2. IC table by family × horizon with pair_count and day_count.
3. Overlay lift vs `picks_only` (timing filter is a drag).
4. Best moves: this week / 8–21d / 22–60d. Budget still 0–2 NEW / ADD. Polar still on.
5. Filter Build-50 with backtest rules (artifact edge + live weeks + leftover; no MTP gate; no paid ATRP chase). Every row needs a reason.
6. Follow-up changes (coverage, persist, next window) — not a second Daily.

Standing spec: `src/focus_pool_screening/prompts/backtest_horizon.txt`. Copy-paste: --- HORIZON --- in `agent_copy_paste.txt`.
