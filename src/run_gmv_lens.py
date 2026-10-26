"""GMV lens — growth, estimates, momentum, participation, and value on the all-fields day history.

How to run (from the repo root; PYTHONPATH=src;. is set by .claude/settings.json):

    python src/run_gmv_lens.py                       # data only: newest pack -> PACK/gmv (~35 s)
    python src/run_gmv_lens.py --full-flow           # produce first: today's all-fields export + compile + pack dumps, then GMV
    python src/run_gmv_lens.py --full-flow --no-export   # compile on the newest all-fields (no export), then GMV
    python src/run_gmv_lens.py --page                # + data-only page into logs/canvases/<day>/<HHMM>_gmv/
    python src/run_gmv_lens.py --print-prompt        # + page + the agent prompt to paste (prompt mode in the IDE)
    python src/run_gmv_lens.py --agent               # + page + headless Claude fills pages.gmv and re-renders
    python src/run_gmv_lens.py --pack PACK --out-dir PACK/gmv   # what the daily refresh suite runs
    python src/run_gmv_lens.py --no-evidence         # skip the walk-forward check (~20 s faster)

Inside the day: `python src/run_daily_refresh.py` and `python src/run_operator_day.py [--agent]`
already run GMV (suite `gmv`) and render the `gmv` template beside daily / movers / playbook,
so nothing extra is needed there. Use this runner for a standalone GMV pass.

Data flow: all-fields day files (generic_utils.panel) -> features, lenses, profiles
(generic_utils.gmv_lens) -> pack us2b_tape overlay and Book flag -> PACK/gmv/gmv_lens.duckdb
(focus, profile_top, profile_overflow, book_lens, lens_tape, evidence, evidence_days),
queries.txt (task SQL first), overview.log, highlights.log, spec.json. Without a pack the
folder is logs/tradingview_analysis/gmv_lens/runs/gmv_lens_<stamp>/ and there is no page.

Prompt and canvas: the page is config/generic_utils/pages/gmv.json (tables from PACK/gmv,
judgment hooks pages.gmv.blocks.*, courses joined as Course / Reason). The prompt is
src/research_prompt/agent_runs/gmv.txt. Judgment: the day's live judgment when a Daily already
ran (the agent adds pages.gmv and new courses only), else <run>/judgment.json, so the next
run_operator_day still runs the full Daily. Knobs: config/generic_utils/gmv_lens.json;
no-arg defaults in run_gmv_lens_local_main. Skill: .claude/skills/gmv-lens/SKILL.md.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from generic_utils.gmv_lens import (
    PANEL_NUMERIC,
    PANEL_TEXT,
    build_live,
    highlights_text,
    join_pack,
    lens_evidence,
    load_gmv_recipe,
    panel_where,
    prepare_series,
    select_profiles,
    task_queries,
)
from generic_utils.panel import load_panel, series_by_id
from generic_utils.run_export import write_run_export
from generic_utils.series import list_dated_files

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "gmv_lens" / "runs"
DEFAULT_ALL_FIELDS_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
PACK_RUNS = PROJECT_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"


def _pack_dir(pack: str | Path | None) -> Path | None:
    if pack in (None, ""):
        return None
    if str(pack) == "latest":
        packs = sorted(PACK_RUNS.glob("briefing_pack_*"), key=lambda p: p.name)
        return packs[-1] if packs else None
    path = Path(pack)
    path = path if path.is_absolute() else PROJECT_ROOT / path
    return path.parent if path.suffix == ".json" else path


def _pack_overlay(pack_dir: Path | None) -> tuple[list[dict[str, Any]], list[str], str]:
    """US $2B tape rows and Book symbols from briefing_pack.duckdb; empty when there is no pack."""
    if pack_dir is None:
        return [], [], "no pack: overlays skipped"
    from generic_utils.scan_sources import rows_from_duckdb

    db = pack_dir / "briefing_pack.duckdb"
    tape = rows_from_duckdb(db, 'SELECT symbol, "left", street_left, rr, bo, cont, fwd, mix, mtp, dte, rng, rsi, vs50, pe_fwd FROM us2b_tape')
    held = [r["symbol"] for r in rows_from_duckdb(db, "SELECT symbol FROM book") if r.get("symbol")]
    return tape, held, f"pack={pack_dir.name} us2b_tape={len(tape)} book={len(held)}"


def run_gmv_lens_method(
    *,
    pack: str | Path | None = "latest",
    out_dir: str | Path | None = None,
    recipe_path: str | Path | None = None,
    history_newest: int | None = None,
    with_evidence: bool = True,
    all_fields_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load the day-file panel, score today's universe, pick profiles, and (optionally) walk forward."""
    from generic_utils.movement_opportunity import attach_dte

    started = time.time()
    recipe = load_gmv_recipe(recipe_path)
    newest = int(history_newest or recipe.get("history_newest") or 200)
    sources = list_dated_files(Path(all_fields_root) if all_fields_root else DEFAULT_ALL_FIELDS_ROOT,
                               "**/tradingview_all_fields_*.duckdb", newest=newest)
    if not sources:
        raise FileNotFoundError("No tradingview_all_fields_*.duckdb day files. Export all-fields first.")
    where, params = panel_where(recipe)
    panel = load_panel(sources, columns=PANEL_TEXT + PANEL_NUMERIC, numeric=PANEL_NUMERIC, where_sql=where, params=params)
    prepared = prepare_series(series_by_id(panel["rows"]), recipe)
    loaded_s = round(time.time() - started, 1)

    live = build_live(prepared, recipe)
    as_of = live[0]["as_of"] if live else None
    pack_dir = _pack_dir(pack)
    tape, held, overlay_note = _pack_overlay(pack_dir)
    live = join_pack(live, tape, held=held)
    live = attach_dte(live, date.fromisoformat(as_of) if as_of else None)
    picked = select_profiles(live, recipe)
    in_universe = {str(r["symbol"]) for r in live}
    book_lens = [r for r in live if r.get("book")]
    book_lens += [{"symbol": s, "book": True, "lenses": "not in the GMV universe (cap floor, venue, or no snapshot today)"}
                  for s in held if s not in in_universe]

    tables: dict[str, list[dict[str, Any]]] = {
        "focus": picked["focus"],
        "profile_top": picked["profile_top"],
        "profile_overflow": picked["profile_overflow"],
        "book_lens": book_lens,
        "lens_tape": live,
    }
    evidence_note = "evidence skipped"
    # Same columns as a real run, so the page's Evidence tables still render and say why they are empty.
    skipped = {"kind": "skipped", "name": "evidence skipped (--no-evidence)", "column": None, "horizon_days": None,
               "n_days": 0, "ic_mean": None, "ic_sd": None, "ic_t": None, "ic_pos_rate": None, "ic_recent3": None,
               "ic_last": None, "top_excess_mean_pct": None, "first_day": None, "last_day": None}
    tables["evidence"] = [skipped, dict(skipped, kind="part")]
    if with_evidence:
        ev = lens_evidence(prepared, recipe)
        tables["evidence"] = ev["evidence"]
        tables["evidence_days"] = ev["evidence_days"]
        days = sorted({r["as_of"] for r in ev["evidence_days"]})
        evidence_note = f"evidence days={len(days)} first={days[0] if days else None} last={days[-1] if days else None}"

    created = datetime.now(tz=timezone.utc)
    run_id = f"gmv_lens_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    target = Path(out_dir) if out_dir else (pack_dir / "gmv" if pack_dir else DEFAULT_OUTPUT_ROOT / run_id)
    first = sources[0].get("as_of")
    last = sources[-1].get("as_of")
    meta = {
        "run_id": run_id,
        "as_of": as_of,
        "history_files": len(sources),
        "history_first": first,
        "history_last": last,
        "history_days": (date.fromisoformat(str(last)) - date.fromisoformat(str(first))).days if first and last else None,
        "panel_rows": panel["spec"]["n"],
        "names_with_history": len(prepared),
        "split_events": sum(item["splits"] for item in prepared.values()),
        "overlays": overlay_note,
        "evidence": evidence_note,
        "seconds_load": loaded_s,
        "seconds_total": round(time.time() - started, 1),
    }
    notes = [
        "Lens percentiles G E M P V are 0-1 ranks in the day's universe. A lens is the median of its present parts (min_parts).",
        "Lenses are never added. A profile floor is the weakest lens of that profile; mean is the tie-break.",
        "FADE inverts M E P: low trend, falling estimates, and sellers together. Short candidates only.",
        "Volatility (vol_d, atrp) is a denominator in r_3m_per_vol and the size in exp_move_20d. It is not a lens.",
        "pe_nfy is price over the street next-FY EPS forecast. pe_fwd is the projection PE (pack overlay). They differ.",
        "pt_left is the all-fields price target vs close; street_left and left are the pack's columns (overlay, not a lens).",
        "Estimate revisions skip a single step beyond revision_step_max_pct as a fiscal-year roll (fy_roll_skips).",
        "Per-share prints are split-adjusted across split-like jumps (split_guard). splits counts the events per name.",
        "evidence is a walk-forward rank IC on this project's own day files. Horizons overlap; ic_t is a size check, not a test.",
        overlay_note,
        evidence_note,
    ]
    exported = write_run_export(
        target,
        tool="gmv_lens",
        tables=tables,
        sources=meta,
        schema_version="gmv_lens_v1",
        notes=notes,
        duckdb_name="gmv_lens.duckdb",
        log_name="overview.log",
        write_csv=True,
    )
    guide = Path(exported["query_guide"])
    guide.write_text(task_queries(str(exported["duckdb"])) + "\n" + guide.read_text(encoding="utf-8"), encoding="utf-8")
    highlights = highlights_text(recipe=recipe, tables=tables, sources=meta)
    (target / "highlights.log").write_text(highlights, encoding="utf-8")
    (target / "spec.json").write_text(json.dumps({**meta, "recipe": recipe}, indent=2, default=str), encoding="utf-8")
    return {
        "run_id": run_id,
        "out_dir": target.as_posix(),
        "duckdb": exported["duckdb"],
        "queries": guide.as_posix(),
        "highlights_log": (target / "highlights.log").as_posix(),
        "tables": exported["tables"],
        "meta": meta,
    }


def _print_result(result: dict[str, Any]) -> None:
    print(f"Run: {result['run_id']}")
    print(f"Folder: {result['out_dir']}")
    print(f"DuckDB: {result['duckdb']}")
    print(f"Queries: {result['queries']}")
    print(f"Highlights: {result['highlights_log']}")
    print(f"Tables: {result['tables']}")
    meta = result["meta"]
    print(f"As of {meta['as_of']}; history {meta['history_files']} files over {meta['history_days']} days; "
          f"{meta['seconds_total']} s")
    for key in ("run_dir", "page", "prompt_path", "judgment", "agent_rc"):
        if result.get(key) is not None:
            print(f"{key}: {result[key]}")
    if result.get("page_warnings"):
        print(f"page warnings: {len(result['page_warnings'])}")


def full_flow_refresh(*, export_all_fields: bool = True, serial: bool = False) -> str:
    """Produce GMV's inputs: today's all-fields export (the only scan GMV reads), a pack compile, and the
    pack's other suite dumps (movers, playbook, ...), so the new newest pack is complete for every page.

    The gmv suite is left out: the caller runs it once. Without the export, compile and dumps are
    skipped when the newest pack already locks the newest inputs. The other producers (prediction,
    edge, MTP) are reused as they are; the full refresh (run_daily_refresh.py) reruns them.
    """
    from run_daily_refresh import run_daily_refresh

    record = run_daily_refresh(producers=export_all_fields, producer_names=["allfields"], compile_pack=True,
                               suites=True, skip_suites=["gmv"], serial=serial)
    return str(record["pack"])


def _repo_rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def build_gmv_prompt(day: str, *, pack: str, run_dir: Path, judgment: Path, live: bool) -> str:
    """agent_runs/gmv.txt with {DAY} {PACK} {RUN_DIR} {JUDGMENT} {JUDGMENT_MODE} {PRIORS_STEP} filled."""
    text = (PROJECT_ROOT / "src" / "research_prompt" / "agent_runs" / "gmv.txt").read_text(encoding="utf-8")
    text = text.replace("{{PACK}}", "\0")  # literal placeholder the agent writes into judgment scans
    if live:
        mode = ("It is the day's live judgment (a Daily already ran): add pages.gmv and courses for names it has "
                "not ruled on; do not change its existing courses (polar rule).")
        priors = (f'Priors: python src/generic_utils/tv_scan_cli.py priors --day {day} --notes "<one GMV line>" '
                  "(merges into the day's priors).")
    else:
        mode = ('No Daily judgment exists today: create this run-local file as {"pack": "<pack folder name>", '
                f'"courses": {{}}, "pages": {{"gmv": {{"blocks": {{}}}}}}}}. Do not create logs/canvases/{day}/judgment.json; '
                "the next run_operator_day must still run the full Daily.")
        priors = "No priors from a run-local judgment: the Daily writes the day's priors."
    mapping = {"DAY": day, "PACK": pack, "RUN_DIR": _repo_rel(run_dir), "JUDGMENT": _repo_rel(judgment),
               "JUDGMENT_MODE": mode, "PRIORS_STEP": priors}
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text.replace("\0", "{PACK}")


def run_gmv_flow(
    *,
    day: str | None = None,
    pack: str | Path | None = "latest",
    full_flow: bool = False,
    export_all_fields: bool = True,
    page: bool = False,
    agent: bool = False,
    print_prompt: bool = False,
    model: str | None = None,
    with_evidence: bool = True,
    recipe_path: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Optional upstream refresh, the GMV scan, then (page / prompt / agent) a canvas run folder.

    page: data-only page gmv-<day>-manual.html. print_prompt: page + prompt.txt, printed for the IDE.
    agent: page + headless Claude (run_operator_day.run_agent) on the same prompt, then the index.
    """
    from generic_utils.canvas_page import write_template_page
    from generic_utils.canvas_runs import judgment_path, new_run_dir, snapshot, write_index, write_run_record

    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    pack_ref = full_flow_refresh(export_all_fields=export_all_fields) if full_flow else pack
    result = run_gmv_lens_method(pack=pack_ref, out_dir=out_dir, recipe_path=recipe_path, with_evidence=with_evidence)
    if not (page or agent or print_prompt):
        return result
    pack_dir = _pack_dir(pack_ref)
    if pack_dir is None or Path(result["out_dir"]).resolve() != (pack_dir / "gmv").resolve():
        raise SystemExit("A GMV page needs a pack and the scan in PACK/gmv (drop --out-dir, pass --pack).")
    pack_rel = _repo_rel(pack_dir)
    run_dir = new_run_dir(day, "gmv")
    manual = write_template_page("gmv", pack=pack_rel, day=day, out_dir=run_dir)
    write_run_record(run_dir, pack=pack_rel, gmv_dir=_repo_rel(result["out_dir"]), origin="run_gmv_lens.py")
    result.update(run_dir=run_dir.as_posix(), page=manual["html"], page_warnings=manual["warnings"])
    if agent or print_prompt:
        live = judgment_path(day)
        judgment = live if live.exists() else run_dir / "judgment.json"
        prompt = build_gmv_prompt(day, pack=pack_rel, run_dir=run_dir, judgment=judgment, live=live.exists())
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        result.update(prompt_path=(run_dir / "prompt.txt").as_posix(), judgment=judgment.as_posix())
        if print_prompt:
            write_run_record(run_dir, origin="run_gmv_lens.py: prompt printed for the IDE")
            result["prompt"] = prompt
        else:
            from run_operator_day import run_agent

            result["agent_rc"] = run_agent(prompt, run_dir=run_dir, model=model)
            if live.exists():
                snapshot(live, run_dir, "judgment.json")  # the live judgment as the agent left it
            write_run_record(run_dir, agent_rc=result["agent_rc"],
                             closed_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    write_index()
    return result


def run_gmv_lens_local_main() -> dict[str, Any]:
    """Edit the knobs here (the rest are in config/generic_utils/gmv_lens.json), then run with no arguments."""
    # ===== edit params here =====
    pack = "latest"  # briefing pack for the leftover / weeks / MTP overlay and the Book flag; None skips it
    full_flow = False  # True: today's all-fields export + compile first (like --full-flow)
    export_all_fields = True  # with full_flow: False reuses the newest all-fields file
    page = True  # data-only page into logs/canvases/<day>/<HHMM>_gmv/
    print_prompt = False  # page + the agent prompt to paste
    agent = False  # page + headless Claude run
    with_evidence = True
    recipe_path = None
    # ===== end params =====
    result = run_gmv_flow(pack=pack, full_flow=full_flow, export_all_fields=export_all_fields, page=page,
                          agent=agent, print_prompt=print_prompt, with_evidence=with_evidence, recipe_path=recipe_path)
    _print_result(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GMV lens: growth, estimates, momentum, participation, value.")
    parser.add_argument("--pack", default="latest", help="briefing pack folder or json, 'latest', or '' for none")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--recipe", default=None)
    parser.add_argument("--history-newest", type=int, default=None)
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--full-flow", action="store_true",
                        help="Produce first: today's all-fields export and a pack compile, then GMV into the new pack.")
    parser.add_argument("--no-export", action="store_true", help="With --full-flow: reuse the newest all-fields file.")
    parser.add_argument("--page", action="store_true", help="Also render the data-only page into a <HHMM>_gmv canvas run.")
    parser.add_argument("--print-prompt", action="store_true", help="Page + print the agent prompt (paste it in the IDE).")
    parser.add_argument("--agent", action="store_true", help="Page + headless Claude run that writes pages.gmv and re-renders.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--day", default=None, help="YYYYMMDD for the canvas run (default: today, UTC).")
    return parser


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(argv)
    if args.full_flow or args.page or args.print_prompt or args.agent:
        if args.history_newest:
            raise SystemExit("--history-newest is for a data-only run; set history_newest in the recipe for a flow run.")
        result = run_gmv_flow(day=args.day, pack=args.pack or None, full_flow=args.full_flow,
                              export_all_fields=not args.no_export, page=args.page, agent=args.agent,
                              print_prompt=args.print_prompt, model=args.model, with_evidence=not args.no_evidence,
                              recipe_path=args.recipe, out_dir=args.out_dir)
    else:
        result = run_gmv_lens_method(pack=args.pack or None, out_dir=args.out_dir, recipe_path=args.recipe,
                                     history_newest=args.history_newest, with_evidence=not args.no_evidence)
    _print_result(result)
    if result.get("prompt"):
        print("\n----- PROMPT -----\n" + result["prompt"])
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_gmv_lens_local_main()
