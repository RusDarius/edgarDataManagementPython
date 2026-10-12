"""Compact scorecard of prior operator briefs versus later session closes.

Reads ``*_operator_actions.json`` files and joins a close from each cited
briefing pack plus the latest pack. The printed digest drops the note text.
Full notes stay in the prior files. One name is a query on ``events``.

Labels are bands on a return. They are not a 0-100 score and not an
eligibility cutoff. ADD versus WAIT stays in the daily canvas.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.symbol_identity import SymbolCatalog, lookup_mapping, parse_symbol

DEFAULT_BANDS: dict[str, float] = {
    "add_good_pct": 3.0,
    "add_bad_pct": -8.0,
    "hold_good_pct": 8.0,
    "hold_bad_pct": -12.0,
    "reduce_good_pct": -3.0,
    "reduce_bad_pct": 8.0,
    "pass_miss_pct": 15.0,
    "pass_good_pct": 0.0,
    "short_good_pct": -5.0,
    "short_bad_pct": 5.0,
}

ADD_ACTIONS = frozenset({"ADD", "NEW", "HOLD_WITH_ADD", "BUY"})
HOLD_ACTIONS = frozenset(
    {
        "HOLD",
        "HOLD_NO_ADD",
        "HOLD_THROUGH",
        "WATCH",
        "WAIT",
        "WATCH_CATALYST",
    }
)
REDUCE_ACTIONS = frozenset({"TRIM", "EXIT", "DERISK", "DERISK_INTO_PRINT", "SELL"})
PASS_ACTIONS = frozenset({"PASS"})
SHORT_ACTIONS = frozenset({"SHORT", "SHORT_WAIT", "SHORT_PRE", "HEDGE", "HEDGE_OR_SHORT"})

_LABEL_RANK = {"BAD": 0, "MISSED": 1, "GOOD": 2, "FLAT": 3, "OPEN": 4}
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_PACK_DAY = re.compile(r"briefing_pack_(\d{4})(\d{2})(\d{2})")
_FILE_DAY = re.compile(r"(\d{4})(\d{2})(\d{2})")


def load_bands(recipe_path: str | Path | None) -> dict[str, float]:
    bands = dict(DEFAULT_BANDS)
    if not recipe_path:
        return bands
    path = Path(recipe_path)
    if not path.exists():
        return bands
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return bands
    for key in DEFAULT_BANDS:
        if key in payload and payload[key] is not None:
            bands[key] = float(payload[key])
    return bands


def action_family(action: str | None) -> str:
    text = str(action or "").strip().upper()
    if text in ADD_ACTIONS:
        return "ADD"
    if text in HOLD_ACTIONS:
        return "HOLD"
    if text in REDUCE_ACTIONS:
        return "REDUCE"
    if text in PASS_ACTIONS:
        return "PASS"
    if text in SHORT_ACTIONS:
        return "SHORT"
    return "OTHER"


def label_outcome(family: str, ret_pct: float | None, bands: Mapping[str, float]) -> str:
    """Band label for one action family. Missing return or an unknown family is OPEN."""
    if ret_pct is None or family == "OTHER":
        return "OPEN"
    if family == "ADD":
        if ret_pct >= float(bands["add_good_pct"]):
            return "GOOD"
        if ret_pct <= float(bands["add_bad_pct"]):
            return "BAD"
        return "FLAT"
    if family == "HOLD":
        if ret_pct >= float(bands["hold_good_pct"]):
            return "GOOD"
        if ret_pct <= float(bands["hold_bad_pct"]):
            return "BAD"
        return "FLAT"
    if family == "REDUCE":
        if ret_pct <= float(bands["reduce_good_pct"]):
            return "GOOD"
        if ret_pct >= float(bands["reduce_bad_pct"]):
            return "BAD"
        return "FLAT"
    if family == "PASS":
        if ret_pct >= float(bands["pass_miss_pct"]):
            return "MISSED"
        if ret_pct <= float(bands["pass_good_pct"]):
            return "GOOD"
        return "FLAT"
    if family == "SHORT":
        if ret_pct <= float(bands["short_good_pct"]):
            return "GOOD"
        if ret_pct >= float(bands["short_bad_pct"]):
            return "BAD"
        return "FLAT"
    return "OPEN"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if _DAY.fullmatch(text):
        return text
    match = _FILE_DAY.fullmatch(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def pack_day(name: str) -> str | None:
    match = _PACK_DAY.search(str(name))
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _mmdd(day: str) -> str:
    match = _DAY.fullmatch(day or "")
    if not match:
        return day
    return f"{match.group(2)}-{match.group(3)}"


def compress_path(steps: Sequence[Mapping[str, str]], *, limit: int = 8) -> str:
    parts = [f"{_mmdd(str(step['day']))}:{step['action']}" for step in steps if step.get("action")]
    if len(parts) <= limit:
        return ">".join(parts)
    return ">".join(parts[:2] + ["..."] + parts[-3:])


def _action_row(key: str, value: Any) -> dict[str, Any] | None:
    ticker = parse_symbol(key).ticker
    if not ticker:
        return None
    if isinstance(value, str):
        value = {"action": value}
    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip().upper()
    if not action:
        return None
    note = str(value.get("note") or "").replace("\n", " ").strip()
    return {
        "ticker": ticker,
        "action": action,
        "grade": str(value.get("grade") or "").strip(),
        "support": _num(value.get("support")),
        "note": note,
    }


def load_sessions(priors_dir: str | Path) -> list[dict[str, Any]]:
    """One dict per prior file, sorted by session day then filename."""
    root = Path(priors_dir)
    if not root.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for path in sorted(root.glob("*_operator_actions.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        day = _iso_day(payload.get("scan_day")) or _iso_day(path.name[:8])
        if day is None:
            continue
        actions = []
        raw = payload.get("actions") or {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                row = _action_row(str(key), value)
                if row:
                    actions.append(row)
        sessions.append(
            {
                "day": day,
                "pack": str(payload.get("pack") or ""),
                "source_file": path.name,
                "actions": actions,
            }
        )
    sessions.sort(key=lambda row: (row["day"], row["source_file"]))
    return sessions


def index_closes(pairs: Sequence[tuple[Any, Any]]) -> dict[str, float]:
    """Qualified ids always. A bare ticker only when no qualified id shares it."""
    qualified: dict[str, float] = {}
    bare_only: dict[str, float] = {}
    for key, close in pairs:
        price = _num(close)
        if price is None or price <= 0:
            continue
        identity = parse_symbol(key)
        if not identity.normalized:
            continue
        if identity.qualified:
            qualified[identity.normalized] = price
        else:
            bare_only[identity.ticker] = price
    taken = {parse_symbol(key).ticker for key in qualified}
    out = dict(qualified)
    for ticker, price in bare_only.items():
        if ticker not in taken:
            out[ticker] = price
    return out


def _table_exists(connection: Any, name: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()
    return bool(row and row[0])


def closes_from_pack_db(db_path: str | Path) -> dict[str, float]:
    """Tape close from ``names``. Book close fills symbols the tape does not have."""
    import duckdb

    path = Path(db_path)
    connection = duckdb.connect(path.as_posix(), read_only=True)
    try:
        name_pairs: list[tuple[Any, Any]] = []
        book_pairs: list[tuple[Any, Any]] = []
        if _table_exists(connection, "names"):
            name_pairs = connection.execute(
                "SELECT symbol, close FROM names WHERE close IS NOT NULL"
            ).fetchall()
        if _table_exists(connection, "book"):
            book_pairs = connection.execute(
                "SELECT COALESCE(symbol, ticker), close FROM book WHERE close IS NOT NULL"
            ).fetchall()
    finally:
        connection.close()
    merged = index_closes(book_pairs)
    merged.update(index_closes(name_pairs))
    return merged


def resolve_pack_dir(runs_root: Path, pack_name: str, scan_day: str) -> Path | None:
    if pack_name:
        direct = runs_root / pack_name
        if (direct / "briefing_pack.duckdb").exists():
            return direct
    token = scan_day.replace("-", "")
    matches = sorted(path for path in runs_root.glob(f"briefing_pack_{token}*") if path.is_dir())
    for folder in matches:
        if (folder / "briefing_pack.duckdb").exists():
            return folder
    return None


def latest_pack_dir(runs_root: Path) -> Path | None:
    matches = [
        path
        for path in runs_root.glob("briefing_pack_*")
        if path.is_dir() and (path / "briefing_pack.duckdb").exists()
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.name)


def _close_for(
    day_map: Mapping[str, float],
    ticker: str,
    catalog: SymbolCatalog | None,
) -> float | None:
    if not day_map:
        return None
    found = lookup_mapping(day_map, ticker, catalog)
    price = _num(found)
    if price is None or price <= 0:
        return None
    return price


def _later_close(
    marks_by_day: Mapping[str, Mapping[str, float]],
    catalogs: Mapping[str, SymbolCatalog],
    ticker: str,
    after_day: str,
) -> tuple[str | None, float | None]:
    days = sorted(day for day in marks_by_day if day > after_day)
    for day in reversed(days):
        price = _close_for(marks_by_day[day], ticker, catalogs.get(day))
        if price is not None:
            return day, price
    return None, None


def _collapse(sessions: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    order: list[str] = []
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for session in sessions:
        day = str(session["day"])
        if day not in by_day:
            order.append(day)
            by_day[day] = {}
        for action in session.get("actions") or []:
            ticker = str(action.get("ticker") or "")
            if ticker:
                by_day[day][ticker] = dict(action)
    return order, by_day


def _note_head(note: str, note_chars: int) -> str:
    if note_chars <= 0 or not note:
        return ""
    if len(note) <= note_chars:
        return note
    return note[:note_chars].rstrip() + "..."


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def build_brief_history(
    sessions: Sequence[Mapping[str, Any]],
    marks_by_day: Mapping[str, Mapping[str, float]],
    *,
    latest_day: str | None,
    bands: Mapping[str, float] | None = None,
    max_names: int = 40,
    note_chars: int = 0,
) -> dict[str, Any]:
    """Roll every prior action into one row per ticker plus one event per session."""
    used = dict(DEFAULT_BANDS)
    if bands:
        used.update({key: float(bands[key]) for key in DEFAULT_BANDS if key in bands})
    order, by_day = _collapse(sessions)
    catalogs = {day: SymbolCatalog.build(marks.keys()) for day, marks in marks_by_day.items()}
    tickers: list[str] = []
    seen: set[str] = set()
    for day in order:
        for ticker in by_day[day]:
            if ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)

    names: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for ticker in tickers:
        steps = []
        for day in order:
            action = by_day[day].get(ticker)
            if action:
                steps.append({"day": day, **action})
        if not steps:
            continue
        priced: list[dict[str, Any]] = []
        for step in steps:
            price = _close_for(marks_by_day.get(step["day"]) or {}, ticker, catalogs.get(step["day"]))
            if price is not None:
                priced.append({**step, "entry_close": price})
        entry = priced[0] if priced else None
        entry_day = entry["day"] if entry else None
        entry_close = entry["entry_close"] if entry else None
        latest_mark_day, latest_close = (None, None)
        if entry_day:
            latest_mark_day, latest_close = _later_close(marks_by_day, catalogs, ticker, entry_day)
        ret = None
        if entry_close and latest_close:
            ret = (latest_close / entry_close - 1.0) * 100.0
        scored = entry or steps[0]
        family = action_family(scored["action"])
        label = label_outcome(family, ret, used)
        families = [action_family(step["action"]) for step in steps]
        flipped = False
        for index, fam in enumerate(families):
            if fam == "ADD" and any(later in {"REDUCE", "SHORT"} for later in families[index + 1 :]):
                flipped = True
                break
        first = steps[0]
        last = steps[-1]
        names.append(
            {
                "ticker": ticker,
                "path": compress_path(steps),
                "first_action": first["action"],
                "last_action": last["action"],
                "scored_action": scored["action"],
                "first_grade": first.get("grade") or "",
                "last_grade": last.get("grade") or "",
                "family": family,
                "entry_day": entry_day or "",
                "entry_close": _round(entry_close),
                "latest_day": latest_mark_day or "",
                "latest_close": _round(latest_close),
                "ret_pct": _round(ret),
                "label": label,
                "flipped": flipped,
                "n_sessions": len(steps),
            }
        )
        for step in steps:
            step_close = _close_for(marks_by_day.get(step["day"]) or {}, ticker, catalogs.get(step["day"]))
            step_latest_day, step_latest = (None, None)
            step_ret = None
            if step_close is not None:
                step_latest_day, step_latest = _later_close(marks_by_day, catalogs, ticker, step["day"])
                if step_latest:
                    step_ret = (step_latest / step_close - 1.0) * 100.0
            step_family = action_family(step["action"])
            events.append(
                {
                    "day": step["day"],
                    "ticker": ticker,
                    "action": step["action"],
                    "family": step_family,
                    "grade": step.get("grade") or "",
                    "support": step.get("support"),
                    "entry_close": _round(step_close),
                    "latest_day": step_latest_day or "",
                    "latest_close": _round(step_latest),
                    "ret_pct": _round(step_ret),
                    "label": label_outcome(step_family, step_ret, used),
                    "note_head": _note_head(str(step.get("note") or ""), note_chars),
                }
            )

    names.sort(
        key=lambda row: (
            _LABEL_RANK.get(str(row["label"]), 9),
            -(abs(row["ret_pct"]) if row["ret_pct"] is not None else -1),
            row["ticker"],
        )
    )
    buckets = dict(Counter(str(row["label"]) for row in names))
    for key in ("GOOD", "BAD", "MISSED", "FLAT", "OPEN"):
        buckets.setdefault(key, 0)
    buckets["flipped"] = sum(1 for row in names if row["flipped"])
    buckets["n"] = len(names)
    shown = names if max_names <= 0 else names[:max_names]
    omitted = max(0, len(names) - len(shown))
    session_rows = [
        {
            "day": session["day"],
            "pack": session.get("pack") or "",
            "n_actions": len(session.get("actions") or []),
            "source_file": session.get("source_file") or "",
            "pack_found": bool(session.get("pack_found")),
        }
        for session in sessions
    ]
    payload = {
        "spec": {
            "n_sessions": len(sessions),
            "n_names": len(names),
            "n_events": len(events),
            "max_names": max_names,
            "omitted": omitted,
            "note_chars": note_chars,
            "latest_day": latest_day or "",
            "first_day": order[0] if order else "",
            "last_prior_day": order[-1] if order else "",
            "bands": used,
        },
        "sessions": session_rows,
        "buckets": buckets,
        "adapt": adapt_lines(names, used),
        "names": names,
        "events": events,
        "shown": shown,
    }
    return payload


def _examples(names: Sequence[Mapping[str, Any]], family: str, label: str, limit: int = 5) -> str:
    found = [str(row["ticker"]) for row in names if row.get("family") == family and row.get("label") == label]
    if not found:
        return ""
    return ", ".join(found[:limit])


def adapt_lines(names: Sequence[Mapping[str, Any]], bands: Mapping[str, float]) -> list[str]:
    """Short lessons from counts. Example tickers only. No note text."""
    lines: list[str] = []

    def _line(family: str, label: str, text: str) -> None:
        who = _examples(names, family, label)
        count = sum(1 for row in names if row.get("family") == family and row.get("label") == label)
        if count <= 0:
            return
        suffix = f" Examples: {who}." if who else ""
        lines.append(f"{text}{suffix}")

    _line(
        "ADD",
        "BAD",
        f"ADD/NEW faded at or below {bands['add_bad_pct']:.0f}%. Re-read those before another starter of the same shape.",
    )
    _line(
        "ADD",
        "GOOD",
        f"ADD/NEW held at or above {bands['add_good_pct']:.0f}%.",
    )
    _line(
        "REDUCE",
        "BAD",
        f"TRIM/EXIT then rose at or above {bands['reduce_bad_pct']:.0f}%. The cut was early.",
    )
    _line(
        "REDUCE",
        "GOOD",
        f"TRIM/EXIT then fell at or below {bands['reduce_good_pct']:.0f}%. The cut held.",
    )
    _line(
        "PASS",
        "MISSED",
        f"PASS then rose at or above {bands['pass_miss_pct']:.0f}%. Those passes missed a runner.",
    )
    _line(
        "SHORT",
        "BAD",
        f"Short-side calls rose at or above {bands['short_bad_pct']:.0f}%.",
    )
    _line(
        "HOLD",
        "BAD",
        f"Holds fell at or below {bands['hold_bad_pct']:.0f}%. The hold did not survive the band.",
    )
    flipped = [str(row["ticker"]) for row in names if row.get("flipped")]
    if flipped:
        lines.append(
            "Add-like then later trim, exit, or short: " + ", ".join(flipped[:5]) + "."
        )
    open_n = sum(1 for row in names if row.get("label") == "OPEN")
    if open_n:
        lines.append(f"{open_n} names have no later close, so they are not a lesson yet.")
    return lines


def format_digest_md(payload: Mapping[str, Any]) -> str:
    """One short page. Notes are omitted on purpose."""
    spec = payload.get("spec") or {}
    buckets = payload.get("buckets") or {}
    bands = spec.get("bands") or {}
    lines = [
        "# Brief history",
        "",
        (
            f"Sessions {spec.get('n_sessions', 0)} "
            f"({spec.get('first_day') or '—'} to {spec.get('last_prior_day') or '—'}). "
            f"Latest mark {spec.get('latest_day') or '—'}. "
            f"Names {spec.get('n_names', 0)}. Events {spec.get('n_events', 0)}."
        ),
        "The return is the close on the first session that has a price to the latest later close. The label uses the action in the From column.",
        "Notes are not in this file. One name: SELECT * FROM events WHERE ticker = 'MU'.",
        "",
        "## Bands",
        "",
        (
            f"ADD good >= {bands.get('add_good_pct')} / bad <= {bands.get('add_bad_pct')}. "
            f"HOLD good >= {bands.get('hold_good_pct')} / bad <= {bands.get('hold_bad_pct')}. "
            f"REDUCE good <= {bands.get('reduce_good_pct')} / bad >= {bands.get('reduce_bad_pct')}. "
            f"PASS missed >= {bands.get('pass_miss_pct')} / good <= {bands.get('pass_good_pct')}. "
            f"SHORT good <= {bands.get('short_good_pct')} / bad >= {bands.get('short_bad_pct')}."
        ),
        "FLAT is inside the band. OPEN is no later close, or an action family this scorecard does not label.",
        "",
        "## Counts",
        "",
        (
            f"GOOD {buckets.get('GOOD', 0)}. BAD {buckets.get('BAD', 0)}. "
            f"MISSED {buckets.get('MISSED', 0)}. FLAT {buckets.get('FLAT', 0)}. "
            f"OPEN {buckets.get('OPEN', 0)}. Flipped {buckets.get('flipped', 0)}."
        ),
        "",
        "## Adapt",
        "",
    ]
    adapt = list(payload.get("adapt") or [])
    if not adapt:
        lines.append("No labeled lesson. Either there are no actions or no later close.")
    else:
        lines.extend(f"- {line}" for line in adapt)
    lines.extend(
        [
            "",
            "## Names",
            "",
            "| Ticker | Path | From | To | Ret % | Label | Flip |",
            "|---|---|---|---|---:|---|---|",
        ]
    )
    shown = list(payload.get("shown") or [])
    if not shown:
        lines.append("| — | — | — | — | — | — | — |")
    for row in shown:
        entry = "—"
        if row.get("entry_day") and row.get("entry_close") is not None:
            entry = f"{_mmdd(str(row['entry_day']))} {row.get('scored_action') or ''} {row['entry_close']}"
        latest = "—"
        if row.get("latest_day") and row.get("latest_close") is not None:
            latest = f"{_mmdd(str(row['latest_day']))} {row['latest_close']}"
        ret = "—" if row.get("ret_pct") is None else f"{row['ret_pct']:.1f}"
        lines.append(
            "| {ticker} | {path} | {entry} | {latest} | {ret} | {label} | {flip} |".format(
                ticker=row.get("ticker"),
                path=row.get("path") or "",
                entry=entry,
                latest=latest,
                ret=ret,
                label=row.get("label"),
                flip="yes" if row.get("flipped") else "",
            )
        )
    omitted = int(spec.get("omitted") or 0)
    lines.extend(
        [
            "",
            f"Omitted from this page: {omitted}. Full table is `names` in the DuckDB.",
            "Session index is `sessions`. Per-call returns are `events`.",
            "",
        ]
    )
    return "\n".join(lines)


def collect_marks(
    sessions: Sequence[dict[str, Any]],
    runs_root: str | Path,
    latest_pack: str | Path | None = None,
) -> tuple[dict[str, dict[str, float]], str | None, list[str]]:
    """Join each prior to its pack close, then add the latest pack as the later mark."""
    root = Path(runs_root)
    notes: list[str] = []
    by_day: dict[str, dict[str, float]] = {}
    for session in sessions:
        folder = resolve_pack_dir(root, str(session.get("pack") or ""), str(session["day"]))
        session["pack_found"] = folder is not None
        if folder is None:
            notes.append(f"no pack duckdb for {session['day']}")
            continue
        by_day[str(session["day"])] = closes_from_pack_db(folder / "briefing_pack.duckdb")
    latest_dir: Path | None
    if latest_pack:
        candidate = Path(latest_pack)
        latest_dir = candidate.parent if candidate.suffix == ".duckdb" else candidate
    else:
        latest_dir = latest_pack_dir(root) if root.exists() else None
    latest_day = None
    if latest_dir is not None:
        db_path = latest_dir if latest_dir.suffix == ".duckdb" else latest_dir / "briefing_pack.duckdb"
        if db_path.exists():
            latest_day = pack_day(db_path.parent.name) or "latest"
            by_day[latest_day] = closes_from_pack_db(db_path)
        else:
            notes.append(f"latest pack duckdb missing: {db_path}")
    else:
        notes.append("no briefing pack duckdb")
    return by_day, latest_day, notes
