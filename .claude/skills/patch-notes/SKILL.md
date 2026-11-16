---
name: patch-notes
description: Review a change set before it is committed and write its patch note - a deterministic scan of the staged files (or the working tree, a commit, a range) into an inventory (areas, features added / updated / removed, functions added / changed, CLI subcommands and flags, recipe keys, suites, skills, tests, wiring of each new module, what still refers to anything removed, deprecation wording, lint and repo guards), a code review of what the inventory points at (performance measured on refresh timings, unit and sign errors, recipe knobs hard-coded in text, duplicated helpers, the repo invariants), then one numbered file in documentation/patch_notes/ with what the operator can do now, a feature-change table, the technical changes, numbered findings and follow-ups, plus the regenerated capability index, commit history and feature register. Use when the user asks to review staged / uncommitted changes, to document a commit, for patch notes, release notes or a changelog, what changed since a commit, what the project can do (capability map), when a feature arrived or last changed (feature register), or to deprecate, retire or remove a feature; also before any commit of more than a few files, and first when looking for an existing tool to reuse. Not a Daily, not a market scan.
---

# Patch notes

**What did this change set add, change or retire, is it sound, and how does the next person (or agent) use it?**

Commit messages in this repo are one line. The record of what the platform can do, how each feature got there and what
is known to be weak is `documentation/patch_notes/`: a baseline (`0000_*`), one numbered file per commit, a hand-written
`lifecycle.json`, and three generated files (`capability_index.md`, `feature_register.md`, `commit_history.md`). Format
and rules: `documentation/patch_notes/README.md`.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/patch_scan.py scan                    # staged vs HEAD (default)
python src/generic_utils/patch_scan.py scan --worktree         # staged + unstaged + untracked vs HEAD
python src/generic_utils/patch_scan.py scan --commit SHA       # a commit already made (back-fill)
python src/generic_utils/patch_scan.py scan --range A..B
python src/generic_utils/patch_scan.py regen                   # rewrite capability_index.md, commit_history.md, feature_register.md
python src/generic_utils/patch_scan.py features                # ... or one of: features | capabilities | history
```

`scan` writes `logs/patch_notes/<stamp>_<mode>/`: `inventory.md` (about 300 lines for a 100-file patch),
`inventory.json`, `patch_note.skeleton.md` (the next number and a pre-filled Feature changes table are in it). It reads
git objects; it never stages, commits or edits a tracked file. Recipe: `config/generic_utils/patch_scan.json` (areas,
guards, wiring files, lifecycle words and reference roots, feature globs).

## Looking for an existing capability or its history (no patch involved)

- **What exists:** Grep `documentation/patch_notes/capability_index.md` for the verb or noun
  (`Grep "peer|premium" capability_index.md`). It lists every runner with its flags, every CLI subcommand with its help
  line, every module with a one-line purpose, its recipe and its test file, the refresh graph, recipes, page templates
  and skills.
- **Its state and history:** Grep `feature_register.md` for the module, subcommand, page or skill: status (`active`,
  `new`, `changed`, or a declared `on_demand` / `renamed` / `deprecated` / `removal_requested` / `removed`), entry
  points, the commit it arrived in, the last change, the patch numbers that changed it. Do not build on a
  `deprecated` feature: its `lifecycle.json` entry names the replacement.
- **Why and how weak:** Grep the numbered patch files named in "changed in" for the module name: Feature changes,
  Review and Follow-ups.

Do this before writing a new module, a new SQL query or a new subcommand. Never read the generated files whole
(about 70 KB and 35 KB).

## Work order for a patch

1. **Scan.** `scan` (or `--worktree` when the review will add files). Read `inventory.md`. Do not read the whole diff:
   the inventory names the functions that changed.
2. **Guards first.** Anything under Findings is handled before the rest: a forbidden path
   (`.vscode/settings.json`), a `_tmp_` file, an `AI GENERATED CODE` marker, a `SELECT *` on `all_fields_rows` /
   `raw_scan_rows`, a file that does not parse, invalid JSON, lint, `removed but still referenced`, `deprecated item
   used`.
3. **Lifecycle.** Read "Features touched", "Removed in this change set" and "Lifecycle wording in added lines".
   - Each removed thing lists the files that still refer to it: clear them, or state in the patch why one stays.
   - A removed function, subcommand, flag, recipe key, refresh step, page or skill is a change for its users even
     when nothing in the repo refers to it (prompts outside the repo, the operator's habits): it gets a Feature
     changes row.
   - Check what the scan cannot see: a renamed output column, a changed default, a page or suite that left the default
     flow. Grep the readers of a renamed column and say whether they accept the old name.
4. **Review the code.** Read every new module and every changed function the inventory lists (`git diff --cached -U2
   -- <files>` for the modified ones, in groups; the new modules with Read). Use the checklist below. A finding needs
   a file and a line, or a number.
5. **Measure.** Performance from the newest `logs/tradingview_analysis/operator_briefing/runs/refresh_*/refresh.json`
   (`steps[].seconds`; compare a run with producers and one with `--no-producers`). Correctness on real data when a
   formula changed: a short read-only script in the scratchpad against the newest store or run, before / after.
   Do not rerun producers or write a new run folder to prove a point; say what was not rerun.
6. **Tests.** The test files of the touched modules, then `python -m pytest src/tests -q`. Report the count, and name
   failures that were there before (baseline B6) separately from new ones.
7. **Fix or record.** Fix what is small, certain and local (a unit conversion with a test, an unused variable, a
   falsy-zero bug). Leave the fix unstaged and say so: the user decides what the commit carries. Record everything
   else as an open finding with enough detail to pick up cold. A fix that changes numbers the operator acts on is
   stated first in the reply, with the measured effect.
8. **Write.** Copy the skeleton to `documentation/patch_notes/NNNN_YYYYMMDD_<slug>.md` and fill every section. Lead
   "What changed for the operator" with flows (what question, which command or paste, what it reads and writes, where
   it sits in the daily graph, limits), then the Feature changes table (one row per feature a user would notice; end
   with what was deprecated or removed, or that nothing was), the technical changes per area, the review table.
9. **Record status changes.** Every row that is not added / updated goes into `lifecycle.json` (fields are described
   in the file): `names` as the register prints them, `status`, `patch`, `replaced_by`, `note`, and `patterns` when a
   regex can find a use of it.
10. **Regenerate.** `regen`; add the row to `documentation/patch_notes/README.md`.
11. **Commit question.** Ask before committing (CLAUDE.md). After the commit, put the hash in the patch file's Commit
    row and `regen` (the next patch may do it): `NNNN*` and "not committed" in the register become the patch number
    and the commit.

## Deprecating or removing a feature

Only when the user asks for it, or when the change set already does it. A reviewer's view that something is old goes
into the patch's Follow-ups, not into `lifecycle.json`.

| Asked for | Do |
|---|---|
| "Stop using X, use Y" (X keeps working) | Entry `deprecated` with `replaced_by` and `patterns`; Feature changes row; point the skills, prompts and `CLAUDE.md` at Y. The scan then flags new lines that use X |
| "Remove X" and something still refers to it | Entry `removal_requested` (who, when, what must be true first). List the references from the register or `Grep`. Move them to the replacement in this patch or the next; remove X when none is left outside the `allow` files |
| "Remove X" and nothing refers to it | Delete the module with its recipe, test file, CLI subcommand, refresh step, page template, Ledger keys and the words (skill, `CLAUDE.md`, run catalog, paste blocks): the six parts of a tool. Run `scan`: "Removed in this change set" must show no reference left. Entry `removed` |
| "Take X out of the Daily" (keep it) | Remove it from the default list (`templates`, a refresh step's default set); entry `on_demand`; say how to run it by name |
| "Rename X to Y" | Rename, keep a reader for the old name where stored output carries it (old packs), say where; entry `renamed` |

Never delete output folders under `logs/`, stored runs or the panel store as part of a feature removal: those are data,
and git cannot bring them back. Deleting tracked files is a change like any other: it is staged by the user or with
their go-ahead.

## Review checklist

| Look for | Where it has bitten |
|---|---|
| Units: percent points vs fractions, pence vs pounds, a field that is empty on the focused scan | `valuation._pct` read a 1.7% buyback yield as 170%; `dividend_yield_recent` was empty on every row (patch 0001 R1, R2) |
| A falsy zero: `value or default` on a number that can be 0.0 | `lane_book` best-lane pick (0001 R9) |
| A universe that changed under a study: new listings, a new filter, a renamed column | US secondary listings entered the panel store floor on 3 Nov 2026 (0001 R4) |
| A join to a run that is not in the refresh graph (age, stale close) | `finvalue_*` joined by the pack (0001 R3) |
| A recipe knob or a recipe list's codes repeated as a literal in code, notes, highlights or a field name | `event_risk` texts (0001 R11); `beat_bar` measure codes (0001 R19) |
| A store-only study recomputed on every UPDATE; a suite on the critical path | `anatomy` 41-81 s before `board` (0001 R5) |
| Wide `SELECT f.*` copies, the same scan repeated per horizon | `lane_book.build_frame` / `stats_rows` (0001 R6) |
| Page script work per keystroke over every row | `canvas_page` screen previews (0001 R7) |
| Sample size overstated: overlapping windows, one name on many days | dossier analogs (0001 R8) |
| An input stored without a check that a later reader silently skips | `sentiment-add --date` (0001 R20) |
| A new `_f` / `_r` / `load_recipe` / `_lit` copy; an import of another module's underscore helper | baseline B2, B9 |
| The six parts of a tool: module, recipe, CLI, refresh entry, tests, words (CLAUDE.md, skill, run catalog, Ledger) | the Wiring table in the inventory shows the blanks |
| Something removed or renamed that a reader, a prompt, a skill or a refresh step still names | "Removed in this change set"; the `er_letters` rename kept two fallback readers (0001) |
| A statement in a note that was not checked against git | baseline B8 said a deleted script was still tracked (corrected in 0001's second pass) |
| The invariants: no 0-100, no cutoffs in generic code, `EXCHANGE:TICKER`, one budget, OUTSIDE tagged, prices kept separate | `CLAUDE.md` |
| Broad `except Exception` that turns a schema error into an empty join | the inventory counts them per file |

## What goes in the note, and what does not

- In: what a command answers and how to run it; renamed columns and changed defaults; what left the default flow, was
  deprecated or removed; measured timings and effects; findings with status; what was not reviewed.
- Out: Book sizes and cash (the holdings config is the record); market calls; anything from `.vscode/settings.json`;
  a retelling of the diff line by line.
- Stock ids are `EXCHANGE:TICKER`. Severity: high = wrong numbers reach a decision; medium = wrong in a corner or slow
  on the daily path; low = quality.

## Limits

- The scan compares ASTs, so a changed constant inside a function shows as "changed" with no detail: read the diff
  for that function.
- Wiring and references are text searches of the working tree (file name, dotted module, `<cli>.py <subcommand>`, a
  function name in files that also name its module): a blank cell or "no reference left" is a prompt to look, not
  proof. A reference built at run time (`getattr`, a name read from a recipe) is not found.
- A feature is a module of the recipe's `wiring.module_globs`; code in the other packages (`data_loaders`,
  `data_analysis_scripts`, `edge_research_tools`, ...) appears in the capability index and in the scan's module
  lists, not in the feature register.
- A CLI subcommand is tied to the module its `_cmd_<name>` function imports (the one named like it, else the one the
  fewest commands share): check the pairing when a helper module shows a subcommand.
- pyflakes is the only linter installed; it does not check types or complexity.
- The generated files describe the working tree at the time they were generated, staged or not.
