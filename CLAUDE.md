# edgarDataManagementPython — Claude Code guide

Personal active-management research platform: TradingView scans → move-prediction / edge / MTP / projection DuckDBs → a compiled **briefing pack** → operator judgment (Book actions, Build-50, movers, risk). The operator (repo owner) runs a real book. Deterministic tools do the data work; the agent does judgment on top. README.md has the architecture.

## Environment

- Windows. Python 3.11 at the system path (no venv in use). `PYTHONPATH=src;.` is set by `.claude/settings.json`, so commands run from the repo root without a prefix. Skill text still shows `$env:PYTHONPATH='src;.'` for Cursor; it is harmless.
- Tests: `python -m pytest src/tests -q` (unittest-style classes, imports inside test methods). Run the relevant test file after any generic_utils / operator_briefing change.
- Outputs live under `logs/` (gitignored): `logs/tradingview_analysis/...` run folders and `logs/canvases/` operator pages.
- `.vscode/settings.json` holds DB connection settings. Do not read it or copy it anywhere.

## Skills (`.claude/skills/`) — pick by the question

| Ask | Skill |
|---|---|
| Daily briefing, radar, leftover, Build-50, Top 100 unpaid, DAILY paste | `daily-operator-scan` |
| Update / refresh today's plan or pages with the latest data, DAILY (update) paste | `daily-update` |
| Movers / groovers / is the tape supported (day, 5D, 1M; 3M recipe) | `price-movers` |
| Loss tolerance, trim vs add, NAV risk, RISK paste | `book-risk` |
| Asymmetric payoff / conviction rank, analysis style | `analysis-style` |
| Week/month plan from backtest IC, HORIZON paste | `backtest-horizon` |
| Weekly point-in-time pick → score loops, BACKTRACK | `backtracking` |
| Whole-book capture, buy-in-red, capture replay | `capture-book` |
| How prior briefs held up (digest) | `brief-history` |
| Did the published top 100 capture later closes (KEEP/CHANGE) | `decision-review` |
| Street vs pack vs model price, PE fwd, FORWARD / value-tech / WISDOM dumps | `forward-value` |
| Buy band, trim, kill prices for a catalog; bounce band / margin of safety for named ids (`name-levels`) | `entry-book` |
| Custom canvas on named themes or tickers (discount, bounce, kill, add, trim) | `watchlist-deep-dive` |
| Growth companies, revenue vs earnings lanes | `growth-radar` |
| Growth / momentum / volume / value lenses on the day history, which lens is working, GMV paste | `gmv-lens` |
| Trading movement, expected move, 1–10 up/down | `movement-opportunity` |
| Post-earn path, premarket after a print | `post-earn` |
| Lookup / compare / stance on named tickers | `scan-evidence` |
| Generic TV dataset interrogation (schema, group, history, span, focus) | `tv-dataset-analysis` |
| Top-N / industry cap with no cutoffs | `generic-ranking` |
| Any operator page ("canvas output") and its one Ledger | `canvas-ledger` |
| About to write SQL / a script / a new cutoff | `generic-vs-domain` (also run after every DAILY and MOVERS) |

Paste blocks live in `src/research_prompt/agent_copy_paste.txt` (index `00_index.txt`, commands `run_catalog.txt`). Their `@.claude/skills/...` lines are file mentions. Layer map: `src/generic_utils/specs/`. Coding-only tasks (refactors, tests, loaders) skip the operator skills.

## Output modes (the paste's Output line)

- `duckdb output` (default): stop at the suite folder. Before any SQL, Grep its `queries.txt` for the table or field (the pack one is ~130 KB; never read it whole).
- `canvas output`: also write the operator page: spec in the run folder (or `logs/canvases/YYYYMMDD/` outside a run) as `<name>-YYYYMMDD.json` → `python src/generic_utils/tv_scan_cli.py page --spec ...` → HTML beside it + `logs/canvases/index.html`. Tables point at the suite CSV/DuckDB as `source`; never retype rows. Fix every Ledger warning. Chat = short snapshot + link to the HTML.
- `data lists`: put name tables on that page. Wisdom always gets a page (`wisdom-YYYYMMDD`) with the Ledger.
- Never announce a page and stop. If rendering fails, finish in the suite's `.md` fallback.

## Refresh and update

- **One command:** `python src/run_operator_day.py` (manual brief + data-only pages) or `python src/run_operator_day.py --agent` (refresh, then a headless Claude run: full Daily if today has no judgment file, else UPDATE; `--print-prompt` to paste instead; UPDATE skips the agent when nothing moved). Prompts: `src/research_prompt/agent_runs/`.

- GMV lens on its own: `python src/run_gmv_lens.py` (data into PACK/gmv; `--full-flow` exports all-fields and compiles first; `--page`, `--print-prompt`, `--agent` add the canvas run `<HHMM>_gmv` and prompt mode). The refresh and operator day already include it.
- Manual brief (no agent): `python src/run_daily_refresh.py` (producers → compile → all dumps, parallel graph from `config/generic_utils/daily_refresh.json`; `--pages` adds data-only pages, `--no-producers` reuses scans, `--serial` for the old order).
- Agent UPDATE: `python src/run_daily_refresh.py --update-day YYYYMMDD`, then follow `daily-update` (review only the `update.md` flags in the run folder it prints; edit the judgment file; re-render with `--out-dir` that folder).
- Pages = template (`config/generic_utils/pages/`) + day judgment (`logs/canvases/YYYYMMDD/judgment.json`). Never hand-write spec JSON for daily / movers / playbook / gmv / wisdom. A template with `requires` (gmv, wisdom) is skipped on a pack without that folder. Start the judgment from `tv_scan_cli.py judgment-skeleton` (full-mode runs write `RUN_DIR/judgment.skeleton.json`), preview cash with `capital-plan --judgment J --pack PACK/briefing_pack.json`, render everything with `tv_scan_cli.py page-all --day D --judgment J --out-dir RUN_DIR --strict`.
- Layout: `logs/canvases/YYYYMMDD/judgment.json` (the day's live judgment) and one folder per run, `logs/canvases/YYYYMMDD/HHMM_<full|update|manual|ide>/` (UTC start: pages, `run.json`, `prompt.txt`, `agent.log`, `judgment.json` as that run left it, `judgment.prev.json`, `update.md`). `logs/canvases/index.html` lists every day → run → page. A runner prompt gives `{RUN_DIR}`; an IDE paste opens one with `tv_scan_cli.py page-run --day YYYYMMDD --mode full` and passes `--out-dir` to every render (`generic_utils/canvas_runs.py`).

## Token budget

- Any table rows: `tv_scan_cli.py named --csv|--db ... --fields ... --ids ... --line` (one line per row) or `--quiet` with `--out`; `history --quiet`. Levels for named ids or a theme in one call: `name-levels --pack PACK --theme ... --ids ... --quiet` (themes: `config/generic_utils/themes.json`); theme candidates: `theme-slice --theme ai_buildout --line`.
- Name details: `example_entry.py stance|lookup SYM ... --line` (one line, about 300 bytes per name; `--brief` is the same fields as JSON, about 1 KB; full dossier about 8 KB); runs: `compare ... --brief`; sources: `inspect --sources`.
- A name that is not in the pack (outside nomination, peer): `tv_scan_cli.py named --csv PACK/movers/us2b_tape.csv --fields ... --ids A,B` (bare tickers match), then `entry-book --csv` on that CSV for band / kill / street target. Record both in the judgment `scans`.
- `queries.txt` (pack ~130 KB, playbook ~60 KB): Grep for the table or field; never read whole. `briefing_pack.md` (~22 KB): read once per Daily; in UPDATE read only `update.md`.
- `generic-vs-domain`: read the short SKILL.md, then grep `reference.md` for the one row you need.
- Never print data rows to stdout to "look"; write a CSV with the CLI and point a page table at it, or `--quiet`.
- Record any extra deterministic scan in the judgment `scans` list so UPDATE replays it instead of re-deriving it.

## Invariants (do not renegotiate)

- **No invented 0–100 composites.** `rr`, `bo`, `band_payoff`, Support, ras, IC are separate columns and separate orders. Never add `rr` and `bo`.
- **No `logs/_tmp_*.py`, no one-off extract scripts, no `SELECT *` on `all_fields_rows` (~3.5k cols) or `raw_scan_rows`.** Use the CLIs (`tv_scan_cli.py`, `rank_cli.py`, `example_entry.py`, `run_operator_suites.py`). Third time the same SQL is needed → promote it into `generic_utils` first.
- **Generic vs domain:** formulas, fetch, aggregate, top-N with zero cutoffs live in `generic_utils`. Eligibility, ADD/WAIT/PASS, polar continuity, High/Med/Low stay in `operator_briefing` or on the page. Never put thresholds in `ranking.py`.
- Leftover `left` = max(street PT upside, edge forward upside). Quote the DuckDB column as `"left"` (reserved word). Street, pack, and model (`fp_*`) prices stay separate. `edge_only` does not get a NEW.
- Quote `val_field` (the setup's native multiple), not EV/Rev for every name. Never fill `pe_fwd` with EV/Rev. A bin label (ABOVE/DISCOUNT/PAID…) always sits beside its percent.
- **Polar rule:** yesterday's ADD/NEW cannot become EXIT unless `thesis_kill`. `avoid_value_trap` on a live-leftover Book line → HOLD_NO_ADD. DERISK sticks while dte ≤ 7; TRIM sticks until tape repairs.
- MTP ENTER_SMALL/ENTER_PROBE is climate, not a buy list. Backtest IC is support, never a leftover gate. Catalyst PASS-all is not a filter.
- Capital recycle (EXIT/TRIM cash into starters, industry NAV % after) is `tv_scan_cli.py capital-plan`, never a hand sum. Mesh letters are `tv_scan_cli.py mesh`; mover leader classes are `up_class` from `sleeves.classify_up_tape`.
- Budget per Daily: 0–2 NEW, 0–2 ADD, 0–2 TRIM/EXIT, 0–2 event, 0–1 short. One budget only; other suites never publish a second NEW/ADD budget. Starter ≈ $5,000, add to a held line ≈ $1,000 (advice, not a cutoff).
- **Stock ids are always `EXCHANGE:TICKER`** (`NASDAQ:MU`, `XETR:VUAA`, `OMXCOP:NOVO_B`) in every output: scan tables, CSV / DuckDB, md, page tables and text, judgment `courses`, priors keys, capital plans, and chat. A bare `ticker` column may sit beside `symbol`; it is never the only id. Bare tickers are an input convenience only (CLIs resolve them). Code: `generic_utils.symbol_identity` (`qualify`, `display_id`, `qualify_rows`); an id that is missing or listed on two exchanges stays bare and is reported, never guessed.
- DAILY compare uses `compare --session --exchanges NASDAQ,NYSE,AMEX`. `history`/`span` take full `EXCHANGE:TICKER` ids.
- Outside news only after the deterministic orders exist, tagged `OUTSIDE`; it can change timing or Grade, never a sort key or size.
- After publishing a Daily, write priors with `python src/generic_utils/tv_scan_cli.py priors --day YYYYMMDD --notes "..."` (from the judgment, keys `EXCHANGE:TICKER`, merges on UPDATE). Do not hand-write the priors JSON.
- Do not rerun multi-hour producers (`run_backtests.py`, scan producers) unless asked; prefer `run_operator_suites.py sources` / existing folders.

## Code conventions

- Match the surrounding module: `from __future__ import annotations`, type hints, short module docstring stating what it does *not* do (no cutoffs, no 0–100), recipes/knobs in `config/generic_utils/*.json`, no-arg defaults in a `*_local_main` function.
- Do **not** add `=== AI GENERATED CODE ===` markers; `AI_CODE_DOCUMENTATION_RULES.md` is retired for Claude work.
- New CLI procedures go in `tv_scan_cli.py` as `_cmd_<name>` + subparser, writing a run folder with DuckDB + `overview.log` + `queries.txt` via `run_export`.
- Git: ask before commit, push, reset, clean, rebase. Read-only git and `git mv` are fine.

## Cursor coexistence

`.claude/skills/` is the single source of truth. `.cursor/rules/*.mdc` stay as thin Cursor triggers pointing at those skills; edit the skill, not the rule. Old Cursor `.canvas.tsx` pages (≈250) remain in `~/.cursor/projects/d-FinanceProjects-edgarDataManagementPython/canvases/` for history.
