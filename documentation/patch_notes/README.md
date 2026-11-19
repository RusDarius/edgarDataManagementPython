# Patch notes

One file per commit (or per batch of commits that ship together), written before the commit from a scan of the
change set and a review of the code. Commit messages say that something changed; these say what the platform can
do afterwards, how to use it, what was checked and what is still open. Together with the generated files they track
each feature from the patch that added it through every patch that changed it to the one that retires it.

## Files

| File | What it is | Written by |
|---|---|---|
| [0000_baseline_20261102.md](0000_baseline_20261102.md) | The platform through commit `4896018` (2 Nov 2026): layers, stores, manual flows, agent flows, conventions, known debt | hand |
| [0001_20261115_board_event_risk_forward_value_deep_dive.md](0001_20261115_board_event_risk_forward_value_deep_dive.md) | Opportunity board and three daily pages, event risk as a core warning, forward value, peer value, lane book, market sentiment, symbol resolver, deep-dive agent, patch notes | hand |
| [0002_20261118_session_marks_and_focused_field_coverage.md](0002_20261118_session_marks_and_focused_field_coverage.md) | Premarket / after-hours prices on the right base with session volume and VWAP; the ten fields the focused daily export stopped fetching (P/B, analyst rating, ex-dividend and IPO dates, pre/post close); live dividend field in three scoring profiles | hand |
| [lifecycle.json](lifecycle.json) | Declared status changes: on demand, renamed, deprecated, removal requested, retired, removed. Only what was requested or done | hand, in the patch that makes the change |
| [feature_register.md](feature_register.md) | Every feature (module with its CLI subcommands, refresh steps, page), page template and skill: since, last change, patches that changed it, status; first-seen and removed lists for refresh steps, default pages and CLI subcommands; files removed over the history | `patch_scan.py features` |
| [capability_index.md](capability_index.md) | Every runner, CLI subcommand, module, recipe, page template, refresh step, skill and prompt in the working tree | `patch_scan.py capabilities` |
| [commit_history.md](commit_history.md) | Every commit: date, subject, size, areas, modules / skills / docs it added | `patch_scan.py history` |

Add a row here for every new patch file. The three generated files are rewritten on every patch
(`patch_scan.py regen`); never edit them by hand.

## Reading order

- **New to the repo or back after a break:** the baseline, then the patches in order.
- **About to build something:** grep `capability_index.md` for the verb or noun first (`peer`, `drawdown`, `earnings`,
  `kill`). If a command or module already does it, the patch that added it says how and with what limits.
- **About to change or reuse a feature:** grep `feature_register.md` for it: its status (do not build on a
  `deprecated` one), its entry points, the patches that changed it. Then grep those patch files for the module name:
  the Feature changes, Review and Follow-ups sections say what moved and what is known to be weak.
- **Looking for when something arrived or went:** `feature_register.md` (per feature), `commit_history.md` (per commit).

## How a patch note is made

Skill: `.claude/skills/patch-notes/SKILL.md`. In short:

```
python src/generic_utils/patch_scan.py scan                 # the staged change set -> logs/patch_notes/<stamp>/inventory.md
python src/generic_utils/patch_scan.py scan --worktree      # staged + unstaged + untracked (when the review adds files)
python src/generic_utils/patch_scan.py scan --commit SHA    # a commit already made (back-filling a note)
python src/generic_utils/patch_scan.py scan --range A..B    # several commits at once
python src/generic_utils/patch_scan.py regen                # rewrite the three generated files
python src/generic_utils/patch_scan.py features             # ... or one of them: features | capabilities | history
```

1. Scan. Read `inventory.md` (about 300 lines for a 100-file patch), not the diff. Its Findings, "Features touched",
   "Removed in this change set" and "Lifecycle wording" sections come first.
2. Review the code the inventory points at: new modules, changed functions, anything in Findings. Measure where a
   number exists (refresh timings in `refresh.json`, row counts, a before / after on real data).
3. Run the tests for the touched modules, then the suite.
4. Copy `patch_note.skeleton.md` to `NNNN_YYYYMMDD_<slug>.md` here and fill it. Its Feature changes table is
   pre-filled from the scan.
5. Put every status change that is not added / updated into `lifecycle.json`.
6. `regen`, add the row above.
7. After the commit, put its hash in the patch file's Commit row and `regen` again (the next patch can do it): the
   register then shows the patch number and the commit instead of `NNNN*` and "not committed".

## Format of a patch file

| Section | Holds |
|---|---|
| Header table | Date, commit, base, size, the scan folder |
| Summary | What the operator can do now, in a few sentences |
| What changed for the operator | One block per feature: the question it answers, how to run it, what it reads and writes, where it sits in the daily flow, its limits |
| Feature changes | One row per feature: added, updated, renamed, on_demand, deprecated, removal_requested or removed; its parts; what a user of it must know. Ends with a line that says what was deprecated or removed, or that nothing was |
| Technical changes | Per area: modules, functions, behaviour changes in existing code, renamed columns, recipe keys |
| Review | Numbered findings with severity, location, evidence and status (fixed in this patch / open) |
| Tests and checks | Commands run and results; what the scan's lint and guards found |
| Follow-ups | Open items, detailed enough to pick up cold |

Rules:

- Stock ids are `EXCHANGE:TICKER`.
- A finding states what was measured or read, and where. No finding without a file and a line or a number.
- Severity: **high** = wrong numbers reach a decision; **medium** = wrong in a corner, or slow on the daily path;
  **low** = quality, naming, duplication.
- Say what was not reviewed.
- No Book sizes or cash figures: `config/holdings_scoring/current_holdings.json` is the record of those.
- The patch file describes the commit as made. If a later patch reverses something, the later file says so; the
  earlier one is not rewritten (fix typos and facts that were wrong when written).

## Lifecycle: tracking a feature, deprecating it, removing it

What counts as a feature: one module of `src/generic_utils`, `src/operator_briefing`, `src/financial_projection` or a
`src/run_*.py` runner, with the parts found by name (recipe, test file, the CLI subcommands it fronts, the refresh
steps that run it, its page template). Page templates no module owns and skills are tracked beside them.

| Change | When | What the patch does |
|---|---|---|
| added | A new module, page, skill or subcommand | Feature changes row. The register shows it as `new` until the commit |
| updated | Behaviour, a default, an output column or a flag changed | Feature changes row that says what a user must do differently. A changed default is named as one |
| renamed | A column, flag, subcommand or file got a new name | Row + `lifecycle.json` entry with `replaced_by`; the note says whether readers still accept the old name and where |
| on_demand | Still supported, no longer in the default flow | Row + `lifecycle.json` entry; say how to run it by name |
| deprecated | Still works, must not be built on | Row + entry with `replaced_by` and `patterns`. From then on the scan flags any added line that uses it (outside the entry's `allow` files) |
| removal_requested | The user asked for it to go; not done yet | Row + entry: who asked, when, and what must be true first. The register shows what still refers to it |
| removed | Deleted in this patch | The scan lists it under "Removed in this change set" with every file that still refers to it, and each leftover reference is a finding. Clear them or state why they stay; row + entry with status `removed` |

Rules:

- **Only on request or as done.** Nothing is deprecated or removed because a reviewer finds it old. A candidate goes
  into the patch's Follow-ups; it enters `lifecycle.json` when the user asks for it or the change set does it.
- **Removal in two patches when anything depends on it:** first `deprecated` or `removal_requested` with the
  replacement in place, then `removed` once the register shows no reference outside the allowed files. A leaf with no
  reference can go in one patch.
- **A removal is checked, not assumed.** Before deleting: `Grep` the name, then run `scan`; the commit carries no
  "removed but still referenced" finding without a sentence in the patch that explains it.
- **The record is kept.** A removed feature keeps its `lifecycle.json` entry and its rows in the old patch files; the
  register lists it under "Files removed over the history".
- A patch that deprecates or removes nothing says so in one line under its Feature changes table.
