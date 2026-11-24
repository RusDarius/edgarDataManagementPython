"""UPDATE mode for a day's operator pages: same judgment, newer pack, and a delta report.

No course is changed here. The tool replays the day's capital plan and recorded
scans on the new pack, re-renders the day's templates with the existing judgment,
and writes `update.md` / `.json` into the run folder (`logs/canvases/<day>/<HHMM>_update/`,
see canvas_runs) listing only what moved:

- a judged name's later close against its *original* band (entry_book.attach_later_mark:
  the band and kill do not move; through_kill means that entry is finished)
- field moves past `diff_thresholds` (close %, leftover, rr, bo, RSI, range, vs50, peer %)
- label flips (up_class, down_class, peer, mix, MTP, mesh)
- names that entered or left a watched list without a course
- on a post-close pack, judged names that printed after the close and moved past `after_hours.min_abs_pct`
  after hours (held lines and capital courses always; other courses at twice the floor)
- judged names whose deterioration letters (`det_seen`, PACK/deterioration) changed between the packs
  (held lines and capital courses always reviewed; others listed as minor)
- judged names whose event-risk level (`warn_level`, PACK/event_risk) changed between the packs
  (held lines, capital courses and any new WARN_STRONG reviewed; others minor)

The agent reads that report, edits only the flagged names in the judgment file,
sets `reviewed_pack`, and re-renders. Unflagged names keep their reasons.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "generic_utils" / "daily_refresh.json"
RUNS_ROOT = Path("logs/tradingview_analysis/operator_briefing/runs")
TEXT_FIELDS = ("up_class", "down_class", "peer", "mix", "mtp", "mesh")


def load_refresh_config(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))


def pack_dir(pack_id: str) -> str:
    return (RUNS_ROOT / pack_id).as_posix() if "/" not in pack_id and "\\" not in pack_id else Path(pack_id).as_posix()


def _ticker(symbol: str) -> str:
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


def name_index(pack: str, sources: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """symbol -> first row found across `index_sources` (priority order). Missing files are skipped."""
    from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb
    from generic_utils.task_dag import substitute

    mapping = {"PACK": pack, "PLAYBOOK": f"{pack}/playbook"}
    index: dict[str, dict[str, Any]] = {}
    for source in sources:
        path = REPO_ROOT / substitute(source.get("db") or source.get("csv"), mapping)
        if not path.exists():
            continue
        try:
            rows = rows_from_csv(path) if source.get("csv") else rows_from_duckdb(path, f'SELECT * FROM "{source["table"]}"')
        except Exception:  # table missing on an older pack
            continue
        for row in rows:
            sym = str(row.get("symbol") or "")
            if sym and sym not in index:
                index[sym] = dict(row)
    return index


def watch_members(pack: str, lists: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    from generic_utils.scan_sources import rows_from_duckdb
    from generic_utils.task_dag import substitute

    mapping = {"PACK": pack, "PLAYBOOK": f"{pack}/playbook"}
    out: dict[str, set[str]] = {}
    for item in lists:
        db = REPO_ROOT / substitute(item["db"], mapping)
        try:
            out[item["name"]] = {str(r.get("symbol")) for r in rows_from_duckdb(db, item["sql"])} if db.exists() else set()
        except Exception:
            out[item["name"]] = set()
    return out


def diff_symbol(
    symbol: str,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    *,
    fields: Sequence[str],
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    """Flags for one judged name. Band state uses the old row's band against the new close."""
    from generic_utils.entry_book import attach_later_mark

    flags: list[str] = []
    rec: dict[str, Any] = {"symbol": symbol}
    if old is None and new is None:
        return {**rec, "flags": ["not_on_any_table"], "review": True}
    if new is None:
        return {**rec, "flags": ["dropped_from_tables"], "review": True}
    close_old, close_new = to_float((old or {}).get("close")), to_float(new.get("close"))
    rec.update(close_old=close_old, close_new=close_new)
    if close_old and close_new:
        pct = round(100 * (close_new / close_old - 1), 2)
        rec["close_pct"] = pct
        if abs(pct) >= thresholds.get("close_pct", 2.0):
            flags.append(f"close {pct:+.1f}%")
    if old and to_float(old.get("entry_high")) is not None and close_new is not None:
        # Same band and kill, judged at the old close and at the new close. Only a change of state is news:
        # a reclaim name whose kill already sat above the price is not "through the kill" again.
        state = {}
        for label, close in (("old", close_old), ("now", close_new)):
            if close is not None:
                state[label] = attach_later_mark([dict(old, symbol=symbol)], {symbol: close})[0].get("band_state_now")
        rec["band_old"], rec["band_now"] = state.get("old"), state.get("now")
        rec["entry_low"], rec["entry_high"], rec["kill_px"] = old.get("entry_low"), old.get("entry_high"), old.get("kill_px")
        if rec["band_now"] != rec["band_old"]:
            flags.append("THROUGH_KILL" if rec["band_now"] == "through_kill" else f"band {rec['band_old']}->{rec['band_now']}")
    for field in fields:
        if field == "close":
            continue
        a, b = (old or {}).get(field), new.get(field)
        if field in TEXT_FIELDS:
            # A label that is missing on one side means the name left (or joined) that table, not a flip.
            if a not in (None, "") and b not in (None, "") and str(a) != str(b):
                flags.append(f"{field} {a}->{b}")
                rec[field] = f"{a}->{b}"
            continue
        fa, fb = to_float(a), to_float(b)
        if fa is None or fb is None:
            continue
        limit = thresholds.get(field)
        if limit is not None and abs(fb - fa) >= limit:
            flags.append(f"{field} {fa:.2f}->{fb:.2f}")
            rec[field] = f"{fa:.2f}->{fb:.2f}"
    if old is None:
        flags.append("new_on_tables")
    rec["flags"] = flags
    rec["review"] = bool(flags)
    return rec


def after_hours_moves(pack: str, spec: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """symbol -> {post_chg, held} for names that printed after the close on the pack's scan day (reaction session
    after it) and moved at least ``min_abs_pct`` after hours. A session-time pack has none: its after-hours
    field is the evening before, and the cash close already holds that reaction."""
    from generic_utils.scan_sources import rows_from_csv
    from generic_utils.task_dag import substitute

    if not spec or not spec.get("csv"):
        return {}
    path = REPO_ROOT / substitute(spec["csv"], {"PACK": pack, "PLAYBOOK": f"{pack}/playbook"})
    meta = REPO_ROOT / pack / "briefing_pack.json"
    if not path.exists() or not meta.exists():
        return {}
    scan_day = str(json.loads(meta.read_text(encoding="utf-8")).get("created_at_utc") or "")[:10]
    floor = float(spec.get("min_abs_pct", 3.0))
    out: dict[str, dict[str, Any]] = {}
    for row in rows_from_csv(path):
        post, on = to_float(row.get("post_chg")), str(row.get("react_on") or "")[:10]
        if str(row.get("printed")).lower() not in ("true", "1") or not on or on <= scan_day or post is None or abs(post) < floor:
            continue
        out[str(row.get("symbol"))] = {"post_chg": round(post, 2), "held": (to_float(row.get("book_usd")) or 0) > 0}
    return out


def letter_changes(old_pack: str, new_pack: str, spec: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """symbol -> {old, new, held} where the deterioration letters (``det_seen``, generic_utils.deterioration)
    differ between the two packs. A name missing on either side is not a change (it left or joined the table)."""
    from generic_utils.scan_sources import rows_from_duckdb
    from generic_utils.task_dag import substitute

    if not spec or not spec.get("db"):
        return {}

    def letters(pack: str) -> dict[str, dict[str, Any]]:
        path = REPO_ROOT / substitute(spec["db"], {"PACK": pack, "PLAYBOOK": f"{pack}/playbook"})
        if not path.exists():
            return {}
        try:
            rows = rows_from_duckdb(path, 'SELECT symbol, det_seen, in_book FROM "names" WHERE det_seen IS NOT NULL')
        except Exception:  # an older pack without the column
            return {}
        return {str(r["symbol"]): r for r in rows}

    old, new = letters(old_pack), letters(new_pack)
    return {sym: {"old": old[sym]["det_seen"], "new": row["det_seen"], "held": str(row.get("in_book")).lower() in ("true", "1")}
            for sym, row in new.items() if sym in old and str(old[sym]["det_seen"]) != str(row["det_seen"])}


def warn_changes(old_pack: str, new_pack: str) -> dict[str, dict[str, Any]]:
    """symbol -> {old, new, letters, held} where the event-risk level (PACK/event_risk, generic_utils.event_risk) differs
    between the packs. An old pack without the suite counts as no level, so a held line's first WARN is a change."""
    from generic_utils.scan_sources import rows_from_duckdb

    def levels(pack: str) -> dict[str, dict[str, Any]] | None:
        path = REPO_ROOT / pack / "event_risk" / "event_risk.duckdb"
        if not path.exists():
            return None
        rows = None
        for col in ("er_letters", "letters"):  # er_letters since 4 Nov 2026; earlier packs wrote letters
            try:
                rows = rows_from_duckdb(path, f'SELECT symbol, warn_level, {col} AS er_letters, in_book FROM "names"')
                break
            except Exception:  # an older run without the column
                continue
        if rows is None:
            return None
        return {str(r["symbol"]): r for r in rows}

    old, new = levels(old_pack), levels(new_pack)
    if new is None:
        return {}
    out = {}
    for sym, row in new.items():
        before = (old or {}).get(sym, {}).get("warn_level")
        if str(before or "") != str(row.get("warn_level") or ""):
            out[sym] = {"old": before or "-", "new": row.get("warn_level") or "-", "letters": row.get("er_letters"),
                        "held": str(row.get("in_book")).lower() in ("true", "1")}
    return out


def judged_symbols(judgment: Mapping[str, Any], priors: Mapping[str, Any] | None, index: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """symbol -> {course, source}. Priors use bare tickers; map them onto index symbols."""
    out = {sym: {"course": (c or {}).get("Course"), "source": "courses"} for sym, c in (judgment.get("courses") or {}).items()}
    by_ticker: dict[str, str] = {}
    for sym in index:
        by_ticker.setdefault(_ticker(sym).upper(), sym)
    for ticker, action in ((priors or {}).get("actions") or {}).items():
        sym = by_ticker.get(str(ticker).upper(), str(ticker))
        out.setdefault(sym, {"course": (action or {}).get("action"), "source": "priors"})
    return out


def build_update(
    judgment: Mapping[str, Any],
    *,
    old_pack: str,
    new_pack: str,
    priors: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_refresh_config())
    old_index = name_index(old_pack, cfg["index_sources"])
    new_index = name_index(new_pack, cfg["index_sources"])
    judged = judged_symbols(judgment, priors, {**old_index, **new_index})
    rows = []
    capital = {str(c).upper() for c in cfg.get("review_courses") or []}
    big = {k: v * float(cfg.get("minor_flag_multiplier") or 2.0) for k, v in cfg["diff_thresholds"].items()}
    after = after_hours_moves(new_pack, cfg.get("after_hours"))
    ah_floor = float((cfg.get("after_hours") or {}).get("min_abs_pct", 3.0))
    letters = letter_changes(old_pack, new_pack, cfg.get("deterioration"))
    warns = warn_changes(old_pack, new_pack)
    for sym, info in judged.items():
        rec = diff_symbol(sym, old_index.get(sym), new_index.get(sym), fields=cfg["diff_fields"], thresholds=cfg["diff_thresholds"])
        rec = {**rec, "course": info["course"], "judged_in": info["source"]}
        course = str(info["course"] or "").split(" ")[0].upper()
        if course.startswith("SHORT"):
            # Long entry bands do not apply to a short; its trigger is the failed bounce in the Reason.
            rec["flags"] = [f for f in rec["flags"] if f != "THROUGH_KILL" and not f.startswith("band ")]
            rec["review"] = bool(rec["flags"])
        if rec["review"] and course not in capital:
            # WAIT / WATCH / PASS names escalate only on state changes, label flips, or large moves.
            loud = diff_symbol(sym, old_index.get(sym), new_index.get(sym), fields=cfg["diff_fields"], thresholds=big)
            escalate = [f for f in loud["flags"]
                        if f not in ("dropped_from_tables", "not_on_any_table", "new_on_tables")
                        and not (f.startswith("band ") and not f.endswith("->inside"))
                        and not f.startswith("mesh ")]
            if not escalate:
                rec["review"], rec["minor"] = False, True
        ah = after.get(sym)
        if ah:
            # The cash close does not hold an after-close print yet: the after-hours move is the news.
            rec["flags"] = rec["flags"] + [f"after-hours {ah['post_chg']:+.1f}% (printed after the close)"]
            rec["post_chg"] = ah["post_chg"]
            if course in capital or ah["held"] or abs(ah["post_chg"]) >= 2 * ah_floor:
                rec["review"] = True
                rec.pop("minor", None)
            else:
                rec["minor"] = not rec["review"]
        lt = letters.get(sym)
        if lt:
            # A letter beyond price turning on or off is the review trigger for a held line, not the price.
            rec["flags"] = rec["flags"] + [f"letters {lt['old']}->{lt['new']}"]
            if course in capital or lt["held"]:
                rec["review"] = True
                rec.pop("minor", None)
            elif not rec["review"]:
                rec["minor"] = True
        wc = warns.get(sym)
        if wc:
            # An event-risk level change (a dated event, a crowd repriced, an industry shock) is reviewed on a held
            # line, a capital course or any new WARN_STRONG; elsewhere it is minor.
            rec["flags"] = rec["flags"] + [f"warn {wc['old']}->{wc['new']} ({wc['letters']})"]
            if course in capital or wc["held"] or wc["new"] == "WARN_STRONG":
                rec["review"] = True
                rec.pop("minor", None)
            elif not rec["review"]:
                rec["minor"] = True
        rows.append(rec)

    def rank(flag: str) -> int:
        return 0 if flag == "THROUGH_KILL" or flag.startswith(("after-hours", "letters ", "warn ")) else 1

    rows.sort(key=lambda r: (not r["review"], min([rank(f) for f in r["flags"]] or [9]), r["symbol"]))
    old_lists = watch_members(old_pack, cfg["watch_lists"])
    new_lists = watch_members(new_pack, cfg["watch_lists"])
    moves = []
    for name, members in new_lists.items():
        before = old_lists.get(name, set())
        entered = sorted(s for s in members - before)
        moves.append({
            "list": name,
            "entered": entered,
            "entered_without_course": [s for s in entered if s not in judged],
            "left": sorted(before - members),
        })
    return {
        "old_pack": old_pack,
        "new_pack": new_pack,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n_judged": len(rows),
        "n_review": sum(1 for r in rows if r["review"]),
        "n_minor": sum(1 for r in rows if r.get("minor")),
        "rows": rows,
        "list_moves": moves,
    }


def render_update_md(update: Mapping[str, Any], *, day: str) -> str:
    lines = [
        f"# Update {day} · {update['created_utc']}",
        "",
        f"Judgment reviewed on `{Path(update['old_pack']).name}`. Data now `{Path(update['new_pack']).name}`.",
        f"{update['n_review']} of {update['n_judged']} judged names moved past a threshold. Edit only these in the judgment file; the rest keep their reasons.",
        "",
        "## Names to review",
        "",
        "| symbol | course | flags | close | band | entry | kill |",
        "|---|---|---|---:|---|---|---:|",
    ]
    for r in update["rows"]:
        if not r["review"]:
            continue
        close = f"{r.get('close_old')} → {r.get('close_new')}" if r.get("close_old") is not None else str(r.get("close_new") or "")
        band = f"{r.get('band_old') or '-'} → {r.get('band_now') or '-'}" if r.get("band_now") else ""
        entry = f"{r['entry_low']}-{r['entry_high']}" if r.get("entry_high") is not None else ""
        lines.append(f"| {r['symbol']} | {r.get('course') or ''} | {'; '.join(r['flags'])} | {close} | {band} | {entry} | {r.get('kill_px') or ''} |")
    lines += ["", "## List moves", ""]
    for m in update["list_moves"]:
        if m["entered"] or m["left"]:
            lines.append(f"- **{m['list']}**: entered {', '.join(m['entered']) or 'none'}; left {', '.join(m['left']) or 'none'}."
                         + (f" No course yet: {', '.join(m['entered_without_course'])}." if m["entered_without_course"] else ""))
    left_tables = [r["symbol"] for r in update["rows"] if r.get("minor") and r["flags"] == ["dropped_from_tables"]]
    minor = [f"{r['symbol']} ({r['flags'][0]})" for r in update["rows"]
             if r.get("minor") and r["flags"] != ["dropped_from_tables"]]
    unchanged = [r["symbol"] for r in update["rows"] if not r["review"] and not r.get("minor")]
    lines += ["", f"## Minor moves on WAIT / WATCH / PASS names ({len(minor)}, no review needed)", "", "; ".join(minor) or "none",
              "", f"Left the page tables ({len(left_tables)}): {', '.join(left_tables) or 'none'}",
              "", f"Unchanged ({len(unchanged)}): {', '.join(unchanged)}", ""]
    return "\n".join(lines)


def replay_steps(scans: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Recorded scans as DAG steps. The refresh suites (board, print_night ...) ran before the replay, so an `after`
    naming a suite is already met; only an `after` naming another recorded scan orders the replay."""
    names = {s["name"] for s in scans}
    return [dict(s, after=[a for a in (s.get("after") or []) if a in names]) for s in scans]


def replay_inputs(judgment: Mapping[str, Any], *, new_pack: str, log_dir: Path) -> list[dict[str, Any]]:
    """Re-run the day's capital plans and recorded scans on the new pack."""
    from generic_utils.capital_plan import write_capital_plan
    from generic_utils.scan_sources import rows_to_csv
    from generic_utils.task_dag import run_dag, substitute

    done: list[dict[str, Any]] = []
    pack_json = json.loads((REPO_ROOT / new_pack / "briefing_pack.json").read_text(encoding="utf-8"))
    for folder, plan in (judgment.get("capital_plan") or {}).items():
        out_dir = REPO_ROOT / new_pack / folder
        rows_to_csv(out_dir / "plan.csv", plan)
        result = write_capital_plan(plan, pack_json.get("book") or [], out_dir,
                                    cash=float((pack_json.get("capital") or {}).get("cash_usd") or 0.0),
                                    source_note=f"replayed from judgment on {Path(new_pack).name}")
        done.append({"name": folder, "rc": 0, "summary": result["summary"]})
    scans = substitute(list(judgment.get("scans") or []), {"PACK": new_pack, "PLAYBOOK": f"{new_pack}/playbook"})
    if scans:
        for scan in scans:
            for arg in scan["argv"]:
                if arg.startswith(new_pack) and arg.endswith(".csv"):
                    (REPO_ROOT / arg).parent.mkdir(parents=True, exist_ok=True)
        done += run_dag(replay_steps(scans), log_dir=log_dir, cwd=REPO_ROOT, echo=False)
    return done


def run_page_update(
    day: str,
    *,
    new_pack: str,
    judgment_path: str | Path | None = None,
    priors_path: str | Path | None = None,
    templates: Sequence[str] | None = None,
    config_path: str | Path | None = None,
    log_dir: str | Path | None = None,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Report, starting judgment and pages go to `run_dir` (new <day>/<HHMM>_update when omitted).
    The live judgment (`<day>/judgment.json`) is edited in place."""
    from generic_utils.canvas_page import write_template_page
    from generic_utils.canvas_runs import judgment_path as day_judgment, new_run_dir, snapshot

    cfg = load_refresh_config(config_path)
    jpath = Path(judgment_path or day_judgment(day))
    run = Path(run_dir) if run_dir else new_run_dir(day, "update")
    judgment = json.loads(jpath.read_text(encoding="utf-8"))
    ppath = Path(priors_path or REPO_ROOT / "logs" / "AI_ANALYSIS_UTILS" / "priors" / f"{day}_operator_actions.json")
    priors = json.loads(ppath.read_text(encoding="utf-8")) if ppath.exists() else None
    old_pack = pack_dir(judgment.get("reviewed_pack") or judgment["pack"])
    new_pack = pack_dir(new_pack)
    logs = Path(log_dir or REPO_ROOT / new_pack / "update_replay")
    if Path(old_pack).name == Path(new_pack).name:
        # No newer data than the reviewed judgment: keep the report, banner and judgment.
        # Still replay the capital plans and scans (an edited plan must be recomputed), then re-render.
        replayed = replay_inputs(judgment, new_pack=new_pack, log_dir=logs)
        pages = [p for p in (write_template_page(t, pack=new_pack, day=day, judgment_path=jpath, out_dir=run)["html"]
                             for t in (templates or cfg.get("templates") or [])) if p]
        return {"update_md": None, "n_review": 0, "n_judged": len(judgment.get("courses") or {}), "replayed": replayed,
                "pages": pages, "old_pack": old_pack, "new_pack": new_pack, "run_dir": run.as_posix(),
                "note": "same pack as the reviewed judgment"}
    replayed = replay_inputs(judgment, new_pack=new_pack, log_dir=logs)
    update = build_update(judgment, old_pack=old_pack, new_pack=new_pack, priors=priors, config=cfg)
    md_path = run / "update.md"
    md_path.write_text(render_update_md(update, day=day), encoding="utf-8")
    (run / "update.json").write_text(json.dumps(update, indent=1, default=str), encoding="utf-8")
    snapshot(jpath, run, "judgment.prev.json")
    try:
        md_shown = md_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        md_shown = md_path.as_posix()
    judgment = dict(judgment)
    judgment.setdefault("reviewed_pack", judgment["pack"])
    judgment["pack"] = Path(new_pack).name
    judgment["update"] = {"banner": {
        "tone": "warning" if update["n_review"] else "info",
        "title": f"UPDATE {update['created_utc']}: data refreshed, judgment from {Path(old_pack).name}",
        "text": (f"{update['n_review']} of {update['n_judged']} judged names moved past a threshold and need review "
                 f"(`{md_shown}`). Reasons below were written on the earlier pack; prices, bands and tables are current."),
    }}
    jpath.write_text(json.dumps(judgment, indent=1, ensure_ascii=False), encoding="utf-8")
    pages = [p for p in (write_template_page(t, pack=new_pack, day=day, judgment_path=jpath, out_dir=run)["html"]
                         for t in (templates or cfg.get("templates") or [])) if p]
    return {"update_md": md_path.as_posix(), "n_review": update["n_review"], "n_judged": update["n_judged"],
            "replayed": replayed, "pages": pages, "old_pack": old_pack, "new_pack": new_pack, "run_dir": run.as_posix()}
