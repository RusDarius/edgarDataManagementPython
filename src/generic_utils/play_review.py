"""Play review: a prior day's judgment and its levels against a later pack.

Table ``calls`` has one row per name the prior judgment ruled on (its ``courses``), tagged when the name
was also on the prior week-plays list (``pages.week_plays.play_ids``). Each row carries the prior close
(the prior pack's US $2B tape, else its week-plays / Book rows), the later close, ``ret_pct``, and
``ret_fx`` (less the median of the same return over every name on both tapes), the prior levels (bounce
band and kill from the prior ``PACK/week_plays``, the entry book's 50-day band and kill), the day-file
path between the two scans (each later day's open from ``change_from_open`` and its close at the newest
run), and where the later open / close sat against the prior band: ABOVE_BAND, IN_BAND, UNDER_BAND,
UNDER_KILL. The day files hold no session low or high, so a touch is measured on opens and closes only.
The later pack's week-plays row adds the print state (phase, react_fx, surprise mix) and the later
alpha deciles. Table ``by_course`` summarises ``ret_fx`` per prior course (n, mean, median, share > 0).

No labels on the outcome, no cutoffs, no grades: brief-history owns the GOOD / BAD bands and the
judgment decides what a miss means. Prior judgment courses are read as written.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

_f = to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
CANVAS_ROOT = REPO_ROOT / "logs" / "canvases"
RUNS_ROOT = REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
LEVEL_FIELDS: tuple[str, ...] = ("bounce_low", "bounce_high", "bounce_kill", "entry_low", "entry_high", "kill_px",
                                 "dec_7d", "dec_28d", "b7d_q20", "b7d_q80", "setup_cell", "setup_mean", "mix_cell",
                                 "mix_mean", "phase", "earn_date", "slot")
LATER_FIELDS: tuple[str, ...] = ("phase", "earn_date", "slot", "react_fx", "last_surprise", "surprise_mix",
                                 "mix_cell", "mix_mean", "big_cell", "big_mean", "dec_7d", "dec_28d")
PATH_FIELDS: tuple[str, ...] = ("symbol", "close", "change", "change_from_open")


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _csv(path: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    return rows_from_csv(path) if path.exists() else []


def _by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol") or ""): dict(r) for r in rows if r.get("symbol")}


def _ret(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1.0) * 100.0


def _r(value: float | None, nd: int = 2) -> float | None:
    return None if value is None else round(value, nd)


def prior_judgment_path(day: str, *, root: Path = CANVAS_ROOT) -> Path | None:
    """The newest ``<YYYYMMDD>/judgment.json`` strictly before ``day`` (YYYYMMDD)."""
    days = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8 and p.name < day
                  and (p / "judgment.json").exists()) if root.exists() else []
    return root / days[-1] / "judgment.json" if days else None


def prior_judgment_for(pack_dir: str | Path, *, before: str | None = None) -> Path | None:
    """The judgment to review against ``pack_dir``: the newest one before ``before`` or the pack's scan day."""
    from generic_utils.week_plays import scan_day_of

    return prior_judgment_path(before or scan_day_of(_abs(pack_dir)).replace("-", ""))


def band_state(px: Any, low: Any, high: Any, kill: Any) -> str | None:
    """Where ``px`` sits against a buy band and its kill: ABOVE_BAND, IN_BAND, UNDER_BAND or UNDER_KILL."""
    p, lo, hi, k = _f(px), _f(low), _f(high), _f(kill)
    if p is None or lo is None or hi is None:
        return None
    if k is not None and p < k:
        return "UNDER_KILL"
    if p > hi:
        return "ABOVE_BAND"
    return "IN_BAND" if p >= lo else "UNDER_BAND"


def market_ret(prior: Mapping[str, Mapping[str, Any]], later: Mapping[str, Mapping[str, Any]]) -> float | None:
    """Median close-to-close return over every symbol on both tapes (the ret_fx reference)."""
    xs = [x for x in (_ret(later[s].get("close"), prior[s].get("close")) for s in prior if s in later) if x is not None]
    return median(xs) if xs else None


def day_path(symbols: Sequence[str], after_day: str, *, root: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Per symbol, the day files dated after ``after_day`` (YYYY-MM-DD): as_of, open (from change_from_open), close."""
    from generic_utils.name_levels import history_rows

    hist = history_rows(list(symbols), fields=PATH_FIELDS, root=root)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in hist["rows"]:
        day = str(row.get("as_of") or "")[:10]
        if day <= after_day:
            continue
        close, cfo = _f(row.get("close")), _f(row.get("change_from_open"))
        open_px = close / (1.0 + cfo / 100.0) if close is not None and cfo is not None and cfo > -100 else None
        out.setdefault(str(row["symbol"]), []).append({"as_of": day, "open": open_px, "close": close})
    return out


def _levels(pack: Path) -> dict[str, dict[str, Any]]:
    rows = _csv(pack / "week_plays" / "plays.csv") + _csv(pack / "week_plays" / "book.csv")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "")
        if sym and sym not in out:
            out[sym] = row
    return out


def _close_rows(pack: Path) -> dict[str, dict[str, Any]]:
    tape = _by_symbol(_csv(pack / "movers" / "us2b_tape.csv"))
    for path in (pack / "week_plays" / "book.csv", pack / "week_plays" / "plays.csv", pack / "book_risk" / "book_risk.csv"):
        for sym, row in _by_symbol(_csv(path)).items():
            if sym not in tape and _f(row.get("close")) is not None:
                tape[sym] = {"symbol": sym, "close": row.get("close")}
    return tape


def build_play_review(judgment_path: str | Path, *, pack_dir: str | Path, prior_pack_dir: str | Path | None = None,
                      ids: Sequence[str] = (), history_root: str | Path | None = None) -> dict[str, Any]:
    """Tables ``calls`` (one row per prior course) and ``by_course`` (ret_fx per prior course)."""
    from generic_utils.week_plays import scan_day_of

    jpath = _abs(judgment_path)
    judgment = json.loads(jpath.read_text(encoding="utf-8"))
    pack = _abs(pack_dir)
    named = str(prior_pack_dir or judgment.get("reviewed_pack") or judgment.get("pack") or "")
    prior = _abs(named) if named else RUNS_ROOT / "-"
    if named and not prior.is_dir() and (RUNS_ROOT / named).is_dir():
        prior = RUNS_ROOT / named
    if not prior.is_dir():
        raise FileNotFoundError(f"prior pack {prior} missing: pass --prior-pack")
    courses: Mapping[str, Mapping[str, Any]] = judgment.get("courses") or {}
    play_ids = list(((judgment.get("pages") or {}).get("week_plays") or {}).get("play_ids") or [])
    symbols = [s for s in courses if not ids or s in ids or s.split(":")[-1] in ids]
    prior_close, later_close = _close_rows(prior), _close_rows(pack)
    mkt = market_ret(_by_symbol(_csv(prior / "movers" / "us2b_tape.csv")), _by_symbol(_csv(pack / "movers" / "us2b_tape.csv")))
    prior_day, later_day = scan_day_of(prior), scan_day_of(pack)
    levels, later = _levels(prior), _levels(pack)
    paths = day_path(symbols, prior_day, root=history_root) if symbols else {}

    calls: list[dict[str, Any]] = []
    for sym in symbols:
        c = courses.get(sym) or {}
        lv = levels.get(sym) or {}
        p0, p1 = _f((prior_close.get(sym) or {}).get("close")), _f((later_close.get(sym) or {}).get("close"))
        ret = _ret(p1, p0)
        rec: dict[str, Any] = {"symbol": sym, "course": c.get("Course"), "grade": c.get("Grade"), "horizon": c.get("Horizon"),
                               "play_rank": play_ids.index(sym) + 1 if sym in play_ids else None,
                               "prior_close": p0, "close": p1, "ret_pct": _r(ret),
                               "ret_fx": _r(None if ret is None or mkt is None else ret - mkt)}
        for f in LEVEL_FIELDS:
            rec[f"prior_{f}" if f in ("phase", "dec_7d", "dec_28d", "setup_cell", "setup_mean", "mix_cell", "mix_mean") else f] = lv.get(f)
        steps = paths.get(sym) or []
        pts = [x for s in steps for x in (s["open"], s["close"]) if x is not None]
        rec["path"] = " ".join(f"{s['as_of'][5:]} o{_r(s['open'])} c{_r(s['close'])}" for s in steps) or None
        rec["path_min"], rec["path_max"] = (_r(min(pts)), _r(max(pts))) if pts else (None, None)
        rec["open_state"] = band_state(steps[0]["open"], lv.get("bounce_low"), lv.get("bounce_high"), lv.get("bounce_kill")) if steps else None
        rec["min_state"] = band_state(rec["path_min"], lv.get("bounce_low"), lv.get("bounce_high"), lv.get("bounce_kill"))
        rec["close_state"] = band_state(p1, lv.get("bounce_low"), lv.get("bounce_high"), lv.get("bounce_kill"))
        rec["entry_state"] = band_state(p1, lv.get("entry_low"), lv.get("entry_high"), lv.get("kill_px"))
        bh = _f(lv.get("bounce_high"))
        rec["close_vs_bhigh_pct"] = _r(_ret(p1, bh))
        lt = later.get(sym) or {}
        for f in LATER_FIELDS:
            rec[f"now_{f}"] = lt.get(f)
        calls.append(rec)

    by_course: list[dict[str, Any]] = []
    groups: dict[str, list[float]] = {}
    for rec in calls:
        if rec["ret_fx"] is not None:
            groups.setdefault(str(rec["course"] or "-"), []).append(rec["ret_fx"])
            if rec["play_rank"] is not None:
                groups.setdefault("(week plays list)", []).append(rec["ret_fx"])
    for course, xs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_course.append({"course": course, "n": len(xs), "ret_fx_mean": _r(mean(xs)), "ret_fx_median": _r(median(xs)),
                          "up_share": _r(sum(1 for x in xs if x > 0) / len(xs), 3)})
    return {
        "calls": calls, "by_course": by_course,
        "spec": {"judgment": jpath.as_posix(), "prior_pack": prior.as_posix(), "pack": pack.as_posix(),
                 "prior_day": prior_day, "later_day": later_day, "mkt_ret": _r(mkt, 3), "n_calls": len(calls),
                 "n_priced": sum(1 for r in calls if r["ret_pct"] is not None), "n_play_ids": len(play_ids)},
    }


def write_play_review(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"prior judgment {spec['judgment']} (pack {spec['prior_pack']}, scan {spec['prior_day']}) against pack {spec['pack']} (scan {spec['later_day']}).",
        f"ret_pct: later close / prior close - 1 (scan prices, so intraday when a scan ran in session). ret_fx: less the median ret over every symbol on both US $2B tapes ({spec['mkt_ret']}).",
        "bounce_* / entry_* / kill_px and prior_* columns come from the prior pack's week_plays plays / book tables: the levels as published.",
        "path: each later day file's open (close / (1 + change_from_open)) and close at its newest run. The day files hold no session low or high: a touch is on opens and closes only.",
        "open_state / min_state / close_state: the first later open, the lowest path point and the later close against the bounce band and bounce_kill; entry_state is the later close against the 50-day band and kill_px.",
        "now_* columns are the later pack's week_plays row (print phase, react_fx, surprise, cells, alpha deciles).",
        "by_course: ret_fx per prior course, plus '(week plays list)' for the play_ids. No outcome labels and no cutoffs.",
    ]
    return write_run_export(out_dir, tool="generic_utils.play_review", tables={k: payload[k] for k in ("calls", "by_course")},
                            sources={"judgment": spec["judgment"], "prior_pack": spec["prior_pack"], "pack": spec["pack"]},
                            notes=notes, extra={"spec": spec}, duckdb_name="play_review.duckdb")


def play_review_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(RUNS_ROOT)
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    judgment = prior_judgment_for(pack)
    if judgment is None:
        raise SystemExit("No prior judgment.json under logs/canvases")
    print(write_play_review(build_play_review(judgment, pack_dir=pack), pack / "play_review"))


if __name__ == "__main__":
    play_review_local_main()
