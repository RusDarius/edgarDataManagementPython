# generic_utils specs

This folder is the **standing map** of how scores, ranks, leftover, and tape
become daily advice. It is documentation, not executable ranking.

| File | Use |
|---|---|
| [scoring_layer_order.md](scoring_layer_order.md) | Layer order: raw data → move prediction → edge → leftover → sleeves → Build-50 → operator. |
| [data_used_for_ranking.md](data_used_for_ranking.md) | Fundamentals, technicals, and other fields each layer actually uses. |

## Rules this folder exists to protect

1. **Do not flatten layers into one 0–100 score.** Mix, leftover, edge rank,
   weeks bo/cont/fwd, MTP, and operator High/Med/Low stay separate.
2. **Formulas live in generic_utils.** Eligibility (unpaid, shorts, industry
   skip) lives in `operator_briefing.sleeves`. Judgment lives on the canvas.
3. **Daily headline ranking is Build-50** (`sleeves.radar_curated_50`):
   50 names, industry-capped, Book exempt. That is “best trades / positions to
   build on.” It is **not** the movers tape (day/week/5D/1M groovers).
4. DuckDB leftover column is `"left"` (reserved word). Prefer `named` / `--csv`.
5. **Book NAV / loss ladders** live in `generic_utils.risk` (not a 0–100).
   Standalone: `tv_scan_cli.py risk`. Skill: `.claude/skills/book-risk/SKILL.md`.
6. **Backward evidence and the forecast are separate layers.** `generic_utils.panel_store`
   (point-in-time day-file panel) feeds `generic_utils.signal_lab` (feature IC, print event
   study, pack-list returns) and `generic_utils.alpha_model` (walk-forward `pred_<h>` with
   bands). `pred_<h>` is its own order: never summed with rr / bo / left, never a gate.
   `generic_utils.week_plays` joins both onto the earnings-week calendar and the Book (bands,
   kills, print cells with their measured mean / t / n) with zero cutoffs; the page owns the screens.
   `generic_utils.deterioration` measures drawdown x letters beyond price (estimate / revenue cuts,
   negative FCF, the last miss) on the same study frame and puts every Book line in its cell, with the
   line's cell dollars and recovery levels; bucket edges and letter rules are recipe knobs, hold / cut is judgment.
   `generic_utils.pathway` sets the Book's NAV-weighted factor percentiles beside the lab's factor ICs, splits the
   Book's return gap into industry and stock parts, and gives each trade's effect on the tilt; stages are judgment.
   `generic_utils.print_night` puts tonight's reporters on measured pointers (pre-print fields cut into quintiles on the lab's past
   prints, each cell's lift vs the rest with t, halves and hit rate); arrows and counts are measured cells, the entry is judgment.
   `generic_utils.print_brief` writes one short read per reporter from those numbers and the peers; OUTSIDE notes are judgment.

Daily agent task: `src/research_prompt/agent_copy_paste.txt`
(`--- DAILY (full) ---`). Skill: `.claude/skills/daily-operator-scan/SKILL.md`.

Longer module playbooks (do not duplicate here):

- `documentation/edge_research_tools_active_management_playbook.md`
- `documentation/edge_upside_rankings_and_tradeable_safety_guide.md`
- `config/move_prediction_profiles/profiles/*.json`
