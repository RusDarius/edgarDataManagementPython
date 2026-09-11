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
   Standalone: `tv_scan_cli.py risk`. Skill: `.cursor/skills/book-risk/SKILL.md`.

Daily agent task: `src/focus_pool_screening/prompts/agent_copy_paste.txt`
(`--- DAILY (full) ---`). Skill: `.cursor/skills/daily-operator-scan/SKILL.md`.

Longer module playbooks (do not duplicate here):

- `documentation/edge_research_tools_active_management_playbook.md`
- `documentation/edge_upside_rankings_and_tradeable_safety_guide.md`
- `config/move_prediction_profiles/profiles/*.json`
