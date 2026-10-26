---
name: generic-vs-domain
description: Decide whether a scan need belongs in generic_utils (zero-cutoff fetch/aggregate/derive) or in operator_briefing / daily judgment. Use during every daily operator scan, when tempted to write SQL or logs/_tmp_*.py, when rankings omit names, or when adding valuation / history / breadth fields.
---

# Generic vs domain

Run this check on every DAILY (full), every MOVERS scan, and whenever a prompt would invent a one-off extract.

Formulas, group stats, named-column history, top-N with no eligibility cutoff, leftover/range/vs-ref, and all-fields field aliases are **generic**. ADD vs WAIT vs PASS, polar continuity, unpaid/short sleeves, and High/Med/Low are **domain**.

## After compile (required)

1. Name the need in one line (example: "current vs forward multiples on Book + radar").
2. Map it: grep [reference.md](reference.md) for the need (one row per need: generic home, and what not to do). Common homes: formulas `derive`; peer / value / tech `setup`; forward `forward_value`; ranking `rank_cli` / `focus`; history `history` + `span`; entry band `entry-book`; capital recycle `capital-plan`; Mesh `mesh`; movers `movers` (`up_class` / `down_class`); NAV ladders `risk`; pages `page --template`. Current-vs-future value aliases are in the same file.

3. If the same SQL or `_tmp_*.py` would be written a **third** time, promote it before briefing. Do not ship the third copy.
4. If a ranking omitted a live leftover + unused-range name, do **not** invent a cutoff. Read pack `radar_industry_overflow` and high-`rr` rows on `radar_upside_100`. Present those as opportunity the cap dropped.
5. Holdings marks vs later tape: leftover from pack `names.close` (all-fields). Refresh with `derive --refresh` if the Book close is stale. Do not mix Friday leftover with Tuesday close by hand.

## Output

One short note in the daily snapshot:

- Reused: which CLI / module
- Promoted this run: field or procedure added (or "none")
- Still domain: the judgment call that stayed in the canvas
