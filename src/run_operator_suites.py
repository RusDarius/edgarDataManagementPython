"""Run every operator + generic suite without an agent scan.

Sibling of ``main.py`` / ``run_financial_projection.py``. Cover is a **size**
(default 1000, max 1000) — not leftover/RSI eligibility and not an industry cap.

Two ways to use the same dumps:

  agent-assisted
      Skills (daily-operator-scan, price-movers, book-risk, forward-value,
      canvas-ledger) read the dumps / briefing pack and write canvases.
  wisdom / manual
      This file and the CLIs below. Data lands in ``.duckdb`` + ``overview.log``.
      No canvas, no ADD/WAIT/PASS, no 0–100 score.

Shell once (Git Bash)::

    export PYTHONPATH=src:.
    python src/run_operator_suites.py --gitbash
    python src/run_operator_suites.py --list
    python src/run_operator_suites.py wisdom --cover 1000
    python src/main.py   # wrappers: run_operator_wisdom_dumps()

Do not write ``logs/_tmp_*.py``. Do not ``SELECT *`` on ``all_fields_rows``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SRC_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_COVER = 1000
MAX_COVER = 1000
VALUE_TECH_RECIPE = str(
    PROJECT_ROOT / "config" / "generic_utils" / "value_tech.json"
)
FORWARD_RECIPE = str(
    PROJECT_ROOT / "config" / "generic_utils" / "forward_value.json"
)
MOVERS_DAY_RECIPE = str(
    PROJECT_ROOT / "config" / "generic_utils" / "movers_day.json"
)
MOVERS_3M_RECIPE = str(
    PROJECT_ROOT / "config" / "generic_utils" / "movers_3m.json"
)
BOOK_RISK_RECIPE = str(PROJECT_ROOT / "config" / "generic_utils" / "book_risk.json")
HOLDINGS_CONFIG = str(
    PROJECT_ROOT / "config" / "holdings_scoring" / "current_holdings.json"
)
SUITES_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "operator_briefing"
    / "runs"
)

# Ordered Git Bash data-flow for agent prompt runs. Print: --gitbash
# From repo root after: export PYTHONPATH=src:.
# Keep commands short (`$ python src/run_*.py`) like the scan producers.
GITBASH_FLOWS: tuple[dict[str, str], ...] = (
    {
        "stage": "0 shell",
        "prompt": "-",
        "command": "export PYTHONPATH=src:.",
        "skill": "-",
        "feeds": "required for every python src/... below",
    },
    {
        "stage": "1 tape",
        "prompt": "DAILY / FORWARD",
        "command": "python src/main.py",
        "skill": "daily-operator-scan (source lock)",
        "feeds": "whatever is uncommented in main() - today holdings_scoring; uncomment export_all_tradingview_fields_duckdb() and daily_prediction_move_analysis_suite() for a full tape day",
    },
    {
        "stage": "1 tape",
        "prompt": "DAILY / FORWARD",
        "command": 'python -c "from data_analysis_scripts.trading_view_export_all_tdfields import export_all_tradingview_fields_duckdb; export_all_tradingview_fields_duckdb()"',
        "skill": "tv-dataset-analysis",
        "feeds": "tradingview_all_fields_*.duckdb",
    },
    {
        "stage": "1 tape",
        "prompt": "DAILY",
        "command": 'python -c "from main import daily_prediction_move_analysis_suite; daily_prediction_move_analysis_suite()"',
        "skill": "daily-operator-scan",
        "feeds": "move_prediction_*.duckdb",
    },
    {
        "stage": "2 overlays",
        "prompt": "DAILY / FORWARD",
        "command": "python src/run_upside_opportunity_scan.py --full-flow",
        "skill": "daily-operator-scan (edge opp / FV)",
        "feeds": "edge parent + upside_opportunity_scan (runs edge-research first)",
    },
    {
        "stage": "2 overlays",
        "prompt": "DAILY",
        "command": "python src/run_market_timing_policy.py",
        "skill": "daily-operator-scan (MTP)",
        "feeds": "market_timing_policy/runs/",
    },
    {
        "stage": "2 overlays",
        "prompt": "FORWARD",
        "command": "python src/run_financial_projection.py --top-n 1000",
        "skill": "forward-value",
        "feeds": "fingrowth_*.duckdb (growth, default)",
    },
    {
        "stage": "2 overlays",
        "prompt": "FORWARD",
        "command": "python src/run_financial_projection.py --mode price --top-n 1000",
        "skill": "forward-value",
        "feeds": "finproj_*.duckdb (terminals)",
    },
    {
        "stage": "3 pack",
        "prompt": "DAILY (full)",
        "command": "python src/operator_briefing/example_entry.py inspect",
        "skill": "scan-evidence / daily-operator-scan",
        "feeds": "stdout: newest pred / all-fields / edge / holdings / MTP",
    },
    {
        "stage": "3 pack",
        "prompt": "DAILY (full)",
        "command": "python src/operator_briefing/example_entry.py compile",
        "skill": "daily-operator-scan",
        "feeds": "briefing_pack.json/.md/.duckdb",
    },
    {
        "stage": "4 agent",
        "prompt": "DAILY (full)",
        "command": "python src/operator_briefing/example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX",
        "skill": "scan-evidence",
        "feeds": "d_bo/d_cont/d_fwd vs prior pred run",
    },
    {
        "stage": "4 agent",
        "prompt": "LOOKUP / DELEGATE",
        "command": "python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD",
        "skill": "scan-evidence",
        "feeds": "name dossier",
    },
    {
        "stage": "4 agent",
        "prompt": "LOOKUP / stance",
        "command": "python src/operator_briefing/example_entry.py stance MU SNDK PGY JD",
        "skill": "scan-evidence",
        "feeds": "stance + suggested_conviction",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "MOVERS",
        "command": "python src/run_operator_suites.py movers",
        "skill": "price-movers",
        "feeds": "movers.duckdb (day/w/5D/1M). Canvas movers-YYYYMMDD is the agent step.",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "MOVERS (3M)",
        "command": "python src/run_operator_suites.py movers-3m",
        "skill": "price-movers",
        "feeds": "movers.duckdb (3M). Canvas movers-3m-YYYYMMDD is the agent step.",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "RISK",
        "command": "python src/run_operator_suites.py risk",
        "skill": "book-risk",
        "feeds": "book_risk.duckdb + book_risk.md. Canvas book-risk-YYYYMMDD is the agent step.",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "FORWARD / Value",
        "command": "python src/run_operator_suites.py value-tech --cover 1000 --forward",
        "skill": "forward-value / tv-dataset-analysis",
        "feeds": "setup.duckdb (val_field + street/TV fwd_* + finproj join)",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "FORWARD",
        "command": "python src/run_operator_suites.py forward --cover 1000",
        "skill": "forward-value",
        "feeds": "forward_value.duckdb (street/pack leftover + fp_*)",
    },
    {
        "stage": "5 skill dumps",
        "prompt": "DETERMINISTIC FOCUS",
        "command": "python src/run_operator_suites.py pack-focus --cover 1000",
        "skill": "tv-dataset-analysis / generic-ranking",
        "feeds": "pack_focus.duckdb (radar_upside_100 + us2b cover 1000, no industry cap)",
    },
    {
        "stage": "6 wisdom",
        "prompt": "WISDOM / ALL SUITES",
        "command": "python src/run_operator_suites.py wisdom --cover 1000",
        "skill": "none (dumps only)",
        "feeds": "suites_*/ child duckdbs + overview.log",
    },
    {
        "stage": "6 wisdom",
        "prompt": "WISDOM + model",
        "command": "python src/run_operator_suites.py wisdom --run-finproj --cover 1000",
        "skill": "forward-value",
        "feeds": "re-runs growth+price for 1000 names, then joins",
    },
    {
        "stage": "6 wisdom",
        "prompt": "DAILY + dumps",
        "command": "python src/run_operator_suites.py all --compile --cover 1000",
        "skill": "daily-operator-scan then wisdom",
        "feeds": "compile then wisdom dumps",
    },
)


# One row per agent_copy_paste.txt block. Print: --gitbash
# output = raw files the model/agent reads (not the canvas).
PROMPT_RUNS: tuple[dict[str, str], ...] = (
    {
        "prompt": "DAILY (full)",
        "command": "python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile",
        "usage": "Lock leftover, Build-50, Book, shorts, earnings, movers_tails. Compile only if inspect sources moved.",
        "output": "logs/.../operator_briefing/runs/briefing_pack_*/briefing_pack.json .md briefing_pack.duckdb overview.log  tables: names book capital radar_curated_50 radar_upside_100 short_book_15_curated earnings_* movers_tails us2b_tape suggested_courses",
    },
    {
        "prompt": "THEMED",
        "command": "python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile",
        "usage": "Same pack as DAILY. Agent overlay is daily_themed_chips_software.txt. Leadership from pack industries_5d.",
        "output": "same briefing_pack_* as DAILY (full)",
    },
    {
        "prompt": "DAILY (pack already good)",
        "command": "python src/operator_briefing/example_entry.py inspect",
        "usage": "Confirm pack sources match disk. Do not recompile. Agent reads the pack.",
        "output": "stdout inspect JSON; reuse newest briefing_pack.md/.json/.duckdb",
    },
    {
        "prompt": "STALE CHECK / RECOMPILE",
        "command": "python src/operator_briefing/example_entry.py inspect",
        "usage": "Diff inspect.sources vs pack sources (pred / all-fields / edge / holdings / MTP). Mismatch -> compile.",
        "output": "stdout JSON (locked run ids). Compile output same as DAILY (full).",
    },
    {
        "prompt": "MOVERS (day / 5D / 1M)",
        "command": "python src/run_operator_suites.py movers",
        "usage": "Both-tail 25 up / 25 punished per horizon. Val/Peer/Proj/Tech. Agent canvas is later.",
        "output": "briefing_pack_*/movers/  movers.duckdb table movers_tails  movers_tails.csv  us2b_tape.csv overview.log",
    },
    {
        "prompt": "MOVERS (3M)",
        "command": "python src/run_operator_suites.py movers-3m",
        "usage": "Same as movers, recipe movers_3m.json.",
        "output": "briefing_pack_*/movers_3m/  movers.duckdb movers_tails.csv overview.log",
    },
    {
        "prompt": "RISK (Book loss vs NAV)",
        "command": "python src/run_operator_suites.py risk",
        "usage": "NAV ladders from latest holdings scan + pack leftover. No TRIM/EXIT in the dump.",
        "output": "briefing_pack_*/book_risk/  book_risk.md book_risk.csv risk_levels.csv capital.csv industry_exposure.csv book_risk.duckdb overview.log",
    },
    {
        "prompt": "FORWARD / Value",
        "command": "python src/run_operator_suites.py value-tech --cover 1000 --forward",
        "usage": "Primary val_field / peer / tech + street/TV fwd_* + join latest finproj. Cover 1000, no industry cap.",
        "output": "briefing_pack_*/value_tech/  setup.duckdb setup.csv overview.log",
    },
    {
        "prompt": "FORWARD (street vs model)",
        "command": "python src/run_operator_suites.py forward --cover 1000",
        "usage": "Street PT leftover, pack leftover, pe_fwd, fp_terminal_px / fp_rev_cagr_own. Empty pe_fwd stays empty.",
        "output": "briefing_pack_*/forward_value/  forward_value.duckdb forward_value.csv overview.log",
    },
    {
        "prompt": "FORWARD (run model first)",
        "command": "python src/run_financial_projection.py --top-n 1000 && python src/run_financial_projection.py --mode price --top-n 1000",
        "usage": "Optional before forward join if dumps are stale. Growth default; price = terminals.",
        "output": "logs/.../financial_projection/<dd_mm_yyyy>/fingrowth_*.duckdb  finproj_*.duckdb  (growth_summary / projection_summary)",
    },
    {
        "prompt": "LOOKUP / PRUNE / DELEGATE",
        "command": "python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD",
        "usage": "Name dossier from the locked pack. Stance line: python src/operator_briefing/example_entry.py stance MU SNDK. Redirect to keep a file.",
        "output": "stdout JSON (stance suggested_conviction left rsi bo d_bo mix mtp dte conflicts course). Optional: > lookup.json",
    },
    {
        "prompt": "COMPARE (pred progression)",
        "command": "python src/operator_briefing/example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX",
        "usage": "Weeks bo/cont/fwd deltas vs prior calendar-day pred run. Not leftover.",
        "output": "stdout JSON. Optional: --ids MU,SNDK,...  Optional: > compare.json",
    },
    {
        "prompt": "BOOK ONLY",
        "command": "python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile",
        "usage": "Same pack as DAILY; agent uses book + capital only. Compile if holdings/tape ids moved.",
        "output": "briefing_pack.duckdb tables book capital risk_levels industry_exposure; JSON pack.book / capital",
    },
    {
        "prompt": "DETERMINISTIC FOCUS",
        "command": "python src/run_operator_suites.py pack-focus --cover 1000",
        "usage": "Raw Build-50 + uncapped Top 100 + leftover-sorted 1000-name tape. No agent judgment.",
        "output": "briefing_pack_*/pack_focus/  pack_focus.duckdb tables radar_curated_50 radar_upside_100 us2b_cover  + csv + overview.log",
    },
    {
        "prompt": "WISDOM / ALL SUITES",
        "command": "python src/run_operator_suites.py wisdom --cover 1000",
        "usage": "One folder: value-tech, forward, movers, movers-3m, pack-focus, risk. No canvas.",
        "output": "logs/.../operator_briefing/runs/suites_YYYYMMDD_HHMM_utc/  overview.log suites.json child duckdbs",
    },
    {
        "prompt": "WISDOM + refresh finproj",
        "command": "python src/run_operator_suites.py wisdom --run-finproj --cover 1000",
        "usage": "Re-run growth+price for 1000 names, then the wisdom dumps.",
        "output": "fingrowth_* + finproj_* under financial_projection/ plus suites_*/ as above",
    },
)


# id, how to run, skill (agent), output. Keep in sync with --list.
SUITES: tuple[dict[str, str], ...] = (
    {
        "id": "inspect",
        "cli": "python src/operator_briefing/example_entry.py inspect",
        "python": "run_inspect()",
        "skill": "scan-evidence / daily-operator-scan",
        "out": "stdout: locked pred / all-fields / edge / holdings / MTP / pack path",
    },
    {
        "id": "compile",
        "cli": "python src/operator_briefing/example_entry.py compile",
        "python": "run_compile()",
        "skill": "daily-operator-scan",
        "out": "briefing_pack.json/.md/.duckdb + overview.log (domain sleeves; industry cap on Build-50 only)",
    },
    {
        "id": "value-tech",
        "cli": "python src/generic_utils/tv_scan_cli.py setup --pack PACK.json --sleeve us2b --cover 1000 --out-dir DIR",
        "python": "run_value_tech_suite(cover=1000)",
        "skill": "tv-dataset-analysis / forward-value",
        "out": "setup.duckdb - val_field / peer / tech + street/TV fwd_* (no industry cap)",
    },
    {
        "id": "forward",
        "cli": "python src/generic_utils/tv_scan_cli.py forward --pack PACK.json --sleeve us2b --cover 1000 --out-dir DIR",
        "python": "run_forward_value_suite(cover=1000)",
        "skill": "forward-value",
        "out": "forward_value.duckdb - street PT + pack leftover + finproj terminals/CAGRs",
    },
    {
        "id": "movers",
        "cli": "python src/generic_utils/tv_scan_cli.py movers --recipe config/generic_utils/movers_day.json --csv us2b.csv --out-dir DIR",
        "python": "run_movers_suite()",
        "skill": "price-movers",
        "out": "movers.duckdb movers_tails (day/w/5D/1M). Canvas is agent-assisted only.",
    },
    {
        "id": "movers-3m",
        "cli": "python src/generic_utils/tv_scan_cli.py movers --recipe config/generic_utils/movers_3m.json --csv us2b.csv --out-dir DIR",
        "python": "run_movers_3m_suite()",
        "skill": "price-movers",
        "out": "movers.duckdb for 3M recipe",
    },
    {
        "id": "risk",
        "cli": "python src/generic_utils/tv_scan_cli.py risk --run latest --pack PACK.json --out-dir DIR",
        "python": "run_book_risk_suite()",
        "skill": "book-risk",
        "out": "book_risk.duckdb + book_risk.md (NAV ladders, no TRIM/EXIT cutoff)",
    },
    {
        "id": "pack-focus",
        "cli": "python src/generic_utils/tv_scan_cli.py pack-focus --pack PACK.json --sleeve sleeves.radar_upside_100 --out appendix.csv",
        "python": "run_pack_focus_suite(cover=1000)",
        "skill": "tv-dataset-analysis / generic-ranking",
        "out": "pack_focus.duckdb - radar_upside_100 (uncapped appendix) + covered us2b tape",
    },
    {
        "id": "finproj-growth",
        "cli": "python src/run_financial_projection.py --mode growth --top-n 1000 --symbols ...",
        "python": "run_finproj_growth_suite(cover=1000)",
        "skill": "forward-value (join after)",
        "out": "logs/.../financial_projection/*/fingrowth_*.duckdb (growth_summary)",
    },
    {
        "id": "finproj-price",
        "cli": "python src/run_financial_projection.py --mode price --top-n 1000 --symbols ...",
        "python": "run_finproj_price_suite(cover=1000)",
        "skill": "forward-value (join after)",
        "out": "logs/.../financial_projection/*/finproj_*.duckdb (projection_summary)",
    },
    {
        "id": "wisdom",
        "cli": "python src/run_operator_suites.py wisdom --cover 1000",
        "python": "run_wisdom(cover=1000)",
        "skill": "none — dumps only",
        "out": "suites_YYYYMMDD_HHMM_utc/ with child duckdbs + overview.log",
    },
    {
        "id": "all",
        "cli": "python src/run_operator_suites.py all --compile --cover 1000",
        "python": "run_all(compile_first=True, cover=1000)",
        "skill": "daily-operator-scan then wisdom dumps",
        "out": "optional compile, then wisdom dumps",
    },
)


def list_suites() -> list[dict[str, str]]:
    return [dict(item) for item in SUITES]


def list_prompt_runs() -> list[dict[str, str]]:
    return [dict(item) for item in PROMPT_RUNS]


def clamp_cover(n: int | None) -> int:
    from generic_utils.forward_value import clamp_cover as _clamp

    return _clamp(n, default=DEFAULT_COVER, ceiling=MAX_COVER)


def default_suites_out_dir(*, now: datetime | None = None) -> Path:
    stamp = now or datetime.now(tz=timezone.utc)
    label = stamp.strftime("suites_%Y%m%d_%H%M_utc")
    return SUITES_ROOT / label


def _default_pack_raw_dir(pack_path: str | Path | None, name: str) -> Path:
    if pack_path:
        return Path(pack_path).parent / name
    return default_suites_out_dir() / name


def load_pack(pack: str | Path | None = None) -> dict[str, Any]:
    from operator_briefing.discovery import load_briefing_pack

    return load_briefing_pack(pack)


def tape_from_pack(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    from generic_utils.focus import pack_us2b_rows

    return pack_us2b_rows(pack)


def covered_symbols(
    rows: Sequence[Mapping[str, Any]],
    cover: int | None = DEFAULT_COVER,
    *,
    field: str = "left",
) -> list[str]:
    from generic_utils.ranking import cover_rows

    size = clamp_cover(cover)
    taken = cover_rows(rows, size, field=field)
    out: list[str] = []
    for row in taken:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            out.append(symbol)
    return out


def _write_tape_csv(rows: Sequence[Mapping[str, Any]], path: str | Path) -> Path:
    from generic_utils.scan_sources import rows_to_csv

    return rows_to_csv(path, [dict(row) for row in rows])


def _export_of(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    exported = payload.get("export")
    return dict(exported) if isinstance(exported, Mapping) else {}


def _first_table_count(block: Any) -> int:
    """Row count of a section's first table (for overview.log)."""
    if not isinstance(block, Mapping):
        return 0
    tables = block.get("tables") or {}
    if not tables:
        return 0
    return int(next(iter(tables.values())) or 0)


def run_inspect(*, pred_run: str | None = None, af_run: str | None = None) -> dict[str, Any]:
    from operator_briefing.inspect_sources import inspect_locked_sources

    return inspect_locked_sources(prediction_run_id=pred_run, all_fields_run_id=af_run)


def run_compile(
    *,
    pred_run: str | None = None,
    af_run: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    from operator_briefing.compile import compile_briefing_pack

    return compile_briefing_pack(
        output_root=Path(output_root) if output_root else None,
        prediction_run_id=pred_run,
        all_fields_run_id=af_run,
    )


def run_value_tech_suite(
    *,
    pack: str | Path | None = None,
    csv: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    out_dir: str | Path | None = None,
    forward: bool = False,
    quiet: bool = True,
) -> dict[str, Any]:
    """Primary value / tech + street/TV fwd_*. ``forward=True`` also joins finproj."""
    from argparse import Namespace

    from generic_utils.tv_scan_cli import _cmd_setup

    pack_path = str(pack) if pack else None
    if pack_path is None and csv is None:
        from operator_briefing.discovery import latest_briefing_pack_path

        found = latest_briefing_pack_path()
        pack_path = str(found) if found else None
    if out_dir is None:
        out_dir = _default_pack_raw_dir(pack_path, "value_tech")
    args = Namespace(
        csv=str(csv) if csv else None,
        db=None,
        sql=None,
        pack=pack_path,
        sleeve="us2b" if pack_path else "names",
        peer_csv=None,
        peer_mcap=2_000_000_000,
        recipe=VALUE_TECH_RECIPE,
        forward=forward,
        forward_recipe=FORWARD_RECIPE,
        finproj_db=None,
        fingrowth_db=None,
        no_finproj=not forward,
        cover=clamp_cover(cover) if cover is not None else None,
        rank_field="left",
        out=None,
        out_dir=str(out_dir) if out_dir else None,
        include_rows=False,
        quiet=quiet,
    )
    return _cmd_setup(args)


def run_forward_value_suite(
    *,
    pack: str | Path | None = None,
    csv: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    out_dir: str | Path | None = None,
    finproj_db: str | Path | None = None,
    fingrowth_db: str | Path | None = None,
    no_finproj: bool = False,
    quiet: bool = True,
) -> dict[str, Any]:
    """Street/TV leftover + financial_projection join. Cover max 1000, no industry cap."""
    from argparse import Namespace

    from generic_utils.tv_scan_cli import _cmd_forward
    from operator_briefing.discovery import latest_briefing_pack_path

    pack_path = str(pack) if pack else None
    if pack_path is None and csv is None:
        found = latest_briefing_pack_path()
        pack_path = str(found) if found else None
    if out_dir is None:
        out_dir = _default_pack_raw_dir(pack_path, "forward_value")
    args = Namespace(
        csv=str(csv) if csv else None,
        db=None,
        sql=None,
        pack=pack_path,
        sleeve="us2b" if pack_path else "names",
        peer_csv=None,
        peer_mcap=2_000_000_000,
        recipe=FORWARD_RECIPE,
        setup_recipe=VALUE_TECH_RECIPE,
        no_setup=False,
        finproj_db=str(finproj_db) if finproj_db else None,
        fingrowth_db=str(fingrowth_db) if fingrowth_db else None,
        no_finproj=no_finproj,
        cover=clamp_cover(cover),
        rank_field="left",
        out=None,
        out_dir=str(out_dir) if out_dir else None,
        include_rows=False,
        quiet=quiet,
    )
    return _cmd_forward(args)


def run_movers_suite(
    *,
    csv: str | Path | None = None,
    pack: str | Path | None = None,
    recipe: str = MOVERS_DAY_RECIPE,
    out_dir: str | Path | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    from argparse import Namespace

    from generic_utils.tv_scan_cli import _cmd_movers
    from operator_briefing.discovery import latest_briefing_pack_path

    csv_path = str(csv) if csv else None
    if csv_path is None:
        pack_path = Path(pack) if pack else latest_briefing_pack_path()
        if pack_path is None:
            raise FileNotFoundError("movers needs --csv or a briefing_pack.json")
        loaded = load_pack(pack_path)
        tape = tape_from_pack(loaded)
        if out_dir is None:
            folder = (
                "movers_3m"
                if Path(recipe).name.startswith("movers_3m")
                else "movers"
            )
            out_dir = Path(pack_path).parent / folder
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        csv_path = _write_tape_csv(tape, Path(out_dir) / "us2b_tape.csv").as_posix()
    elif out_dir is None:
        folder = (
            "movers_3m"
            if Path(recipe).name.startswith("movers_3m")
            else "movers"
        )
        pack_hint = Path(pack) if pack else latest_briefing_pack_path()
        out_dir = _default_pack_raw_dir(pack_hint, folder)
    args = Namespace(
        csv=csv_path,
        db=None,
        sql=None,
        recipe=recipe,
        fields=None,
        where=None,
        top=None,
        cover=None,
        up_share=None,
        leaders=None,
        laggards=None,
        group_field=None,
        cap=None,
        id_field=None,
        keep=None,
        keep_duplicate_suffixes=False,
        out=None,
        out_dir=str(out_dir) if out_dir else None,
        include_rows=False,
        quiet=quiet,
    )
    return _cmd_movers(args)


def run_movers_3m_suite(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("recipe", MOVERS_3M_RECIPE)
    return run_movers_suite(**kwargs)


def run_book_risk_suite(
    *,
    pack: str | Path | None = None,
    holdings: str | Path = HOLDINGS_CONFIG,
    run: str = "latest",
    out_dir: str | Path | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    from argparse import Namespace

    from generic_utils.tv_scan_cli import _cmd_risk
    from operator_briefing.discovery import latest_briefing_pack_path

    pack_path = str(pack) if pack else None
    if pack_path is None:
        found = latest_briefing_pack_path()
        pack_path = str(found) if found else None
    if out_dir is None:
        out_dir = _default_pack_raw_dir(pack_path, "book_risk")
    args = Namespace(
        holdings=str(holdings),
        run=run,
        summary=None,
        config_only=False,
        cash=None,
        pack=pack_path,
        recipe=BOOK_RISK_RECIPE,
        out=None,
        out_dir=str(out_dir) if out_dir else None,
        include_rows=False,
        quiet=quiet,
    )
    return _cmd_risk(args)


def run_pack_focus_suite(
    *,
    pack: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Uncapped radar appendix + size-limited US $2B tape. No industry cap."""
    from generic_utils.focus import pack_path_rows
    from generic_utils.ranking import cover_rows
    from generic_utils.run_export import write_run_export

    loaded = load_pack(pack)
    tape = tape_from_pack(loaded)
    size = clamp_cover(cover)
    covered = list(cover_rows(tape, size, field="left"))
    try:
        appendix = pack_path_rows(loaded, "sleeves.radar_upside_100")
    except (KeyError, TypeError):
        appendix = list(loaded.get("radar_upside_100") or [])
    try:
        radar50 = pack_path_rows(loaded, "sleeves.radar_curated_50")
    except (KeyError, TypeError):
        radar50 = []
    directory = Path(out_dir) if out_dir else _default_pack_raw_dir(
        loaded.get("_pack_path") or pack, "pack_focus"
    )
    exported = write_run_export(
        directory,
        tool="run_operator_suites.pack_focus",
        tables={
            "us2b_cover": covered,
            "radar_upside_100": appendix,
            "radar_curated_50": radar50,
        },
        sources={"pack": loaded.get("run_id"), "cover": size},
        notes=[
            "us2b_cover is a size (max 1000), leftover-sorted, no industry cap",
            "radar_upside_100 is the uncapped unpaid appendix; radar_curated_50 is domain-capped",
        ],
        extra={"cover": size, "tape_n": len(tape)},
        duckdb_name="pack_focus.duckdb",
    )
    return {"spec": {"cover": size, "tape_n": len(tape), "appendix_n": len(appendix)}, "export": exported}


def _finproj_symbols(pack: Mapping[str, Any], cover: int | None) -> list[str]:
    return covered_symbols(tape_from_pack(pack), cover)


def run_finproj_growth_suite(
    *,
    pack: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    symbols: Sequence[str] | None = None,
    print_console: bool = False,
) -> dict[str, Any]:
    from run_financial_projection import run_growth_projection_from_latest_all_fields

    names = list(symbols) if symbols else _finproj_symbols(load_pack(pack), cover)
    result = run_growth_projection_from_latest_all_fields(
        symbols=names or None,
        top_n=clamp_cover(cover),
        print_console=print_console,
    )
    result["cover"] = clamp_cover(cover)
    result["symbol_n"] = len(names)
    return result


def run_finproj_price_suite(
    *,
    pack: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    symbols: Sequence[str] | None = None,
    print_console: bool = False,
) -> dict[str, Any]:
    from run_financial_projection import run_financial_projection_from_latest_all_fields

    names = list(symbols) if symbols else _finproj_symbols(load_pack(pack), cover)
    result = run_financial_projection_from_latest_all_fields(
        symbols=names or None,
        top_n=clamp_cover(cover),
        print_console=print_console,
    )
    result["cover"] = clamp_cover(cover)
    result["symbol_n"] = len(names)
    return result


def run_wisdom(
    *,
    pack: str | Path | None = None,
    cover: int | None = DEFAULT_COVER,
    out_dir: str | Path | None = None,
    run_finproj: bool = False,
    include_risk: bool = True,
    include_movers: bool = True,
) -> dict[str, Any]:
    """Dump value/tech, forward, movers, pack-focus (and optional finproj + risk)."""
    from generic_utils.forward_value import discover_latest_projection_db
    from generic_utils.run_export import write_overview_log
    from operator_briefing.discovery import latest_briefing_pack_path

    size = clamp_cover(cover)
    pack_path = Path(pack) if pack else latest_briefing_pack_path()
    if pack_path is None:
        raise FileNotFoundError(
            "No briefing_pack.json. Run: python src/run_operator_suites.py compile"
        )
    loaded = load_pack(pack_path)
    root = Path(out_dir) if out_dir else default_suites_out_dir()
    root.mkdir(parents=True, exist_ok=True)
    tape_csv = _write_tape_csv(tape_from_pack(loaded), root / "us2b_tape.csv")
    sections: dict[str, Any] = {}
    notes = [
        "wisdom dumps — no canvas, no ADD/WAIT, no industry cap on these tables",
        f"cover={size} (max {MAX_COVER})",
    ]

    growth_result: dict[str, Any] | None = None
    price_result: dict[str, Any] | None = None
    if run_finproj:
        try:
            growth_result = run_finproj_growth_suite(pack=pack_path, cover=size)
            sections["finproj_growth"] = {
                "database": growth_result.get("database_path"),
                "run_dir": growth_result.get("run_dir"),
            }
        except FileNotFoundError as exc:
            notes.append(f"finproj-growth skipped: {exc}")
        try:
            price_result = run_finproj_price_suite(pack=pack_path, cover=size)
            sections["finproj_price"] = {
                "database": price_result.get("database_path"),
                "run_dir": price_result.get("run_dir"),
            }
        except FileNotFoundError as exc:
            notes.append(f"finproj-price skipped: {exc}")

    fingrowth = discover_latest_projection_db(prefix="fingrowth_")
    finproj = discover_latest_projection_db(prefix="finproj_")

    value = run_value_tech_suite(
        pack=pack_path, cover=size, out_dir=root / "value_tech", quiet=True
    )
    sections["value_tech"] = _export_of(value)
    forward = run_forward_value_suite(
        pack=pack_path,
        cover=size,
        out_dir=root / "forward_value",
        finproj_db=finproj,
        fingrowth_db=fingrowth,
        quiet=True,
    )
    sections["forward_value"] = _export_of(forward)
    focus = run_pack_focus_suite(pack=pack_path, cover=size, out_dir=root / "pack_focus")
    sections["pack_focus"] = _export_of(focus)
    if include_movers:
        movers = run_movers_suite(
            csv=tape_csv, out_dir=root / "movers", quiet=True
        )
        sections["movers"] = _export_of(movers)
        movers3m = run_movers_3m_suite(
            csv=tape_csv, out_dir=root / "movers_3m", quiet=True
        )
        sections["movers_3m"] = _export_of(movers3m)
    if include_risk:
        risk = run_book_risk_suite(
            pack=pack_path, out_dir=root / "risk", quiet=True
        )
        sections["risk"] = _export_of(risk)

    duckdbs = {
        key: (block.get("duckdb") if isinstance(block, Mapping) else None)
        for key, block in sections.items()
    }
    log_path = write_overview_log(
        root / "overview.log",
        tool="run_operator_suites.wisdom",
        sources={
            "pack": str(pack_path),
            "pack_run_id": loaded.get("run_id"),
            "finproj_db": str(finproj) if finproj else None,
            "fingrowth_db": str(fingrowth) if fingrowth else None,
        },
        tables={key: _first_table_count(block) for key, block in sections.items()},
        notes=notes,
        extra={"cover": size, "sections": ",".join(sections)},
    )
    payload = {
        "out_dir": root.as_posix(),
        "overview_log": log_path.as_posix(),
        "cover": size,
        "pack": str(pack_path),
        "tape_csv": tape_csv.as_posix(),
        "sections": sections,
        "duckdbs": duckdbs,
        "notes": notes,
    }
    (root / "suites.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return payload


def run_all(
    *,
    compile_first: bool = False,
    cover: int | None = DEFAULT_COVER,
    run_finproj: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    compiled = None
    if compile_first:
        compiled = run_compile()
        pack = (compiled.get("output") or {}).get("json")
        kwargs["pack"] = pack
    wisdom = run_wisdom(cover=cover, run_finproj=run_finproj, **kwargs)
    if compiled is not None:
        wisdom["compile"] = {
            "run_id": compiled.get("run_id"),
            "json": (compiled.get("output") or {}).get("json"),
        }
    return wisdom


def _print_list() -> None:
    print("Operator suites - agent-assisted (skills/canvas) vs wisdom (this file).\n")
    print(f"{'id':<16} {'python':<40} output")
    print("-" * 100)
    for item in SUITES:
        print(f"{item['id']:<16} {item['python']:<40} {item['out']}")
    print("\nCLI (same methods):")
    for item in SUITES:
        print(f"  {item['id']}: {item['cli']}")
    print(
        "\nCover default/max is 1000 names. No leftover/RSI cutoff and no industry cap "
        "on value-tech / forward / pack-focus us2b_cover. Build-50 remains domain-capped "
        "inside compile."
    )
    print("\nPrompt | command | usage/output: python src/run_operator_suites.py --gitbash")


def _cmd_prefix(command: str) -> str:
    if command in ("-", "") or command.startswith("export ") or command.startswith("PACK="):
        return ""
    return "$ "


def _print_gitbash() -> None:
    print("Agent prompt flows - raw data the model uses (not canvases)")
    print("Repo root. Git Bash: export PYTHONPATH=src:.")
    print("PowerShell: $env:PYTHONPATH='src;.'")
    print("Copy a command. Canvases are a later agent step.\n")
    print("Prompt | Command | Usage / output")
    print("=" * 100)
    for item in PROMPT_RUNS:
        print(item["prompt"])
        print(f"  command: {_cmd_prefix(item['command'])}{item['command']}")
        print(f"  usage:   {item['usage']}")
        print(f"  output:  {item['output']}")
        print()
    print("Scan-day producers (only if tape / edge / MTP / finproj is stale):")
    print("-" * 100)
    for item in GITBASH_FLOWS:
        if not str(item["stage"]).startswith(("0 ", "1 ", "2 ")):
            continue
        print(
            f"  {item['stage']:<10} {_cmd_prefix(item['command'])}{item['command']}"
        )
        print(f"             -> {item['feeds']}")
    print(
        "\nSame table: src/generic_utils/specs/gitbash_agent_data_flows.md"
    )


def _add_cover(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cover",
        type=int,
        default=DEFAULT_COVER,
        help=f"Names to dump (default {DEFAULT_COVER}, max {MAX_COVER}). Size, not a screen.",
    )


def main(argv: list[str] | None = None) -> dict[str, Any] | None:
    parser = argparse.ArgumentParser(
        description=(
            "Run operator + generic suites manually. DuckDB + overview.log. "
            "Cover max 1000, no industry cap on these dumps."
        )
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print suite catalog (same as the list subcommand).",
    )
    parser.add_argument(
        "--gitbash",
        action="store_true",
        help="Print Prompt | Command | Usage/output for each agent copy-paste flow.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="Print suite catalog.")
    sub.add_parser("gitbash", help="Print Prompt | Command | Usage/output table.")
    ins = sub.add_parser("inspect")
    ins.add_argument("--pred-run", default=None)
    ins.add_argument("--af-run", default=None)
    comp = sub.add_parser("compile")
    comp.add_argument("--pred-run", default=None)
    comp.add_argument("--af-run", default=None)
    vt = sub.add_parser("value-tech")
    vt.add_argument("--pack", default=None)
    vt.add_argument("--csv", default=None)
    vt.add_argument("--out-dir", default=None)
    vt.add_argument("--forward", action="store_true")
    _add_cover(vt)
    fwd = sub.add_parser("forward")
    fwd.add_argument("--pack", default=None)
    fwd.add_argument("--csv", default=None)
    fwd.add_argument("--out-dir", default=None)
    fwd.add_argument("--finproj-db", default=None)
    fwd.add_argument("--fingrowth-db", default=None)
    fwd.add_argument("--no-finproj", action="store_true")
    _add_cover(fwd)
    mv = sub.add_parser("movers")
    mv.add_argument("--csv", default=None)
    mv.add_argument("--pack", default=None)
    mv.add_argument("--out-dir", default=None)
    mv3 = sub.add_parser("movers-3m")
    mv3.add_argument("--csv", default=None)
    mv3.add_argument("--pack", default=None)
    mv3.add_argument("--out-dir", default=None)
    risk = sub.add_parser("risk")
    risk.add_argument("--pack", default=None)
    risk.add_argument("--out-dir", default=None)
    pf = sub.add_parser("pack-focus")
    pf.add_argument("--pack", default=None)
    pf.add_argument("--out-dir", default=None)
    _add_cover(pf)
    fg = sub.add_parser("finproj-growth")
    fg.add_argument("--pack", default=None)
    _add_cover(fg)
    fp = sub.add_parser("finproj-price")
    fp.add_argument("--pack", default=None)
    _add_cover(fp)
    wis = sub.add_parser("wisdom", help="Dump all generic suites from the latest pack.")
    wis.add_argument("--pack", default=None)
    wis.add_argument("--out-dir", default=None)
    wis.add_argument("--run-finproj", action="store_true")
    wis.add_argument("--skip-risk", action="store_true")
    wis.add_argument("--skip-movers", action="store_true")
    _add_cover(wis)
    allp = sub.add_parser("all", help="Optional compile, then wisdom dumps.")
    allp.add_argument("--compile", action="store_true")
    allp.add_argument("--pack", default=None)
    allp.add_argument("--out-dir", default=None)
    allp.add_argument("--run-finproj", action="store_true")
    _add_cover(allp)

    args = parser.parse_args(argv)
    command = args.command or "list"
    if args.list:
        command = "list"
    if getattr(args, "gitbash", False):
        command = "gitbash"
    payload: dict[str, Any] | None
    if command == "list":
        _print_list()
        return {"suites": list_suites()}
    if command == "gitbash":
        _print_gitbash()
        return {
            "prompt_runs": list_prompt_runs(),
            "flows": [dict(item) for item in GITBASH_FLOWS],
        }
    if command == "inspect":
        payload = run_inspect(pred_run=args.pred_run, af_run=args.af_run)
    elif command == "compile":
        payload = run_compile(pred_run=args.pred_run, af_run=args.af_run)
    elif command == "value-tech":
        payload = run_value_tech_suite(
            pack=args.pack,
            csv=args.csv,
            cover=args.cover,
            out_dir=args.out_dir,
            forward=args.forward,
            quiet=False,
        )
    elif command == "forward":
        payload = run_forward_value_suite(
            pack=args.pack,
            csv=args.csv,
            cover=args.cover,
            out_dir=args.out_dir,
            finproj_db=args.finproj_db,
            fingrowth_db=args.fingrowth_db,
            no_finproj=args.no_finproj,
            quiet=False,
        )
    elif command == "movers":
        payload = run_movers_suite(
            csv=args.csv, pack=args.pack, out_dir=args.out_dir, quiet=False
        )
    elif command == "movers-3m":
        payload = run_movers_3m_suite(
            csv=args.csv, pack=args.pack, out_dir=args.out_dir, quiet=False
        )
    elif command == "risk":
        payload = run_book_risk_suite(
            pack=args.pack, out_dir=args.out_dir, quiet=False
        )
    elif command == "pack-focus":
        payload = run_pack_focus_suite(
            pack=args.pack, cover=args.cover, out_dir=args.out_dir
        )
    elif command == "finproj-growth":
        payload = run_finproj_growth_suite(pack=args.pack, cover=args.cover, print_console=True)
    elif command == "finproj-price":
        payload = run_finproj_price_suite(pack=args.pack, cover=args.cover, print_console=True)
    elif command == "wisdom":
        payload = run_wisdom(
            pack=args.pack,
            cover=args.cover,
            out_dir=args.out_dir,
            run_finproj=args.run_finproj,
            include_risk=not args.skip_risk,
            include_movers=not args.skip_movers,
        )
    elif command == "all":
        payload = run_all(
            compile_first=args.compile,
            pack=args.pack,
            cover=args.cover,
            out_dir=args.out_dir,
            run_finproj=args.run_finproj,
        )
    else:
        raise SystemExit(f"Unknown command {command}")
    if command in {"wisdom", "all", "pack-focus", "inspect"}:
        print(json.dumps(
            {k: payload.get(k) for k in ("out_dir", "overview_log", "cover", "pack", "run_id", "sections") if k in payload}
            if isinstance(payload, dict)
            else payload,
            indent=2,
            default=str,
        ))
    return payload


if __name__ == "__main__":
    main()
