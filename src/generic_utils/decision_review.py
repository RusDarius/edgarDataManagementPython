"""Review published briefing ranks against later pack closes.

The unit is one briefing day and its top ranks (default 100). A later close
is the mark. The suggestion matrix compares slices that were knowable on
that day. It is not a 0-100 score and it does not rewrite Build-50.

``src/backtests`` answers a different question: which signal families
discriminated forward returns. This module can attach that file as support.
It does not rerun the backtest.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from generic_utils.brief_history import (
    action_family,
    closes_from_pack_db,
    label_outcome,
    load_bands,
    load_sessions,
    pack_day,
)
from generic_utils.derive import leftover_carrier
from generic_utils.symbol_identity import SymbolCatalog, lookup_mapping, parse_symbol

DEFAULT_SPEC: dict[str, float] = {
    "top": 100,
    "head": 20,
    "tail": 20,
    "min_n": 12,
    "gap_pct": 1.0,
    "rank_good_pct": 3.0,
    "rank_bad_pct": -8.0,
}

_KEEP = "KEEP"
_CHANGE = "CHANGE"
_THIN = "THIN"
_FLAT = "FLAT"


def load_spec(recipe_path: str | Path | None) -> dict[str, float]:
    spec = dict(DEFAULT_SPEC)
    if not recipe_path:
        return spec
    path = Path(recipe_path)
    if not path.exists():
        return spec
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return spec
    for key in DEFAULT_SPEC:
        if key in payload and payload[key] is not None:
            spec[key] = float(payload[key])
    return spec


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Rank correlation. Negative means a smaller rank number had the higher return."""
    if len(pairs) < 8:
        return None
    xs = _ordinal([item[0] for item in pairs])
    ys = _ordinal([item[1] for item in pairs])
    count = len(xs)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    numer = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return numer / (den_x * den_y)


def _ordinal(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    for place, index in enumerate(order, start=1):
        ranks[index] = float(place)
    return ranks


def rank_label(ret_pct: float | None, spec: Mapping[str, float]) -> str:
    if ret_pct is None:
        return "OPEN"
    if ret_pct >= float(spec["rank_good_pct"]):
        return "GOOD"
    if ret_pct <= float(spec["rank_bad_pct"]):
        return "BAD"
    return "FLAT"


def position_band(rank: int, n_ranked: int, spec: Mapping[str, float]) -> str:
    head = int(spec["head"])
    tail = int(spec["tail"])
    if rank <= head:
        return "head"
    if n_ranked > tail and rank > n_ranked - tail:
        return "tail"
    return "mid"


def complete_top(
    sleeve: Sequence[Mapping[str, Any]],
    tape: Sequence[Mapping[str, Any]] | None,
    top: int,
) -> tuple[list[dict[str, Any]], str]:
    """Keep the published order. Append tape names by rr only while the list is short of ``top``."""
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sleeve:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        item = dict(row)
        item["rank_source"] = "sleeve"
        chosen.append(item)
        if len(chosen) >= top:
            return chosen[:top], "sleeve"
    short = len(chosen)
    fillers = sorted(
        (dict(row) for row in (tape or []) if str(row.get("symbol") or "").strip()),
        key=lambda row: -(_num(row.get("rr")) if _num(row.get("rr")) is not None else -1e18),
    )
    for row in fillers:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        row["rank_source"] = "tape_fill"
        chosen.append(row)
        if len(chosen) >= top:
            break
    if len(chosen) > short and short:
        source = "sleeve+tape_fill"
    elif short == 0 and chosen:
        source = "tape_fill"
    elif len(chosen) < top:
        source = "short"
    else:
        source = "sleeve"
    return chosen[:top], source


def _close_for(day_map: Mapping[str, float], symbol: str, catalog: SymbolCatalog | None) -> float | None:
    if not day_map or not symbol:
        return None
    found = lookup_mapping(day_map, symbol, catalog)
    price = _num(found)
    if price is None or price <= 0:
        return None
    return price


def _next_and_latest(
    marks: Mapping[str, Mapping[str, float]],
    catalogs: Mapping[str, SymbolCatalog],
    symbol: str,
    after_day: str,
) -> tuple[str | None, float | None, str | None, float | None]:
    later_days = sorted(day for day in marks if day > after_day)
    next_day = None
    next_close = None
    latest_day = None
    latest_close = None
    for day in later_days:
        price = _close_for(marks[day], symbol, catalogs.get(day))
        if price is None:
            continue
        if next_close is None:
            next_day, next_close = day, price
        latest_day, latest_close = day, price
    return next_day, next_close, latest_day, latest_close


def _slim(row: Mapping[str, Any], rank: int) -> dict[str, Any]:
    left = _num(row.get("left"))
    street = _num(row.get("street_left"))
    return {
        "symbol": str(row.get("symbol") or ""),
        "ticker": parse_symbol(row.get("symbol")).ticker,
        "rank": rank,
        "rank_source": str(row.get("rank_source") or "sleeve"),
        "close": _num(row.get("close")),
        "left": left,
        "street_left": street,
        "carrier": leftover_carrier(street, left),
        "rr": _num(row.get("rr")),
        "bo": _num(row.get("bo")),
        "rng": _num(row.get("rng")),
        "peer": str(row.get("peer") or ""),
        "mix": str(row.get("mix") or ""),
    }


def build_decision_review(
    sessions: Sequence[Mapping[str, Any]],
    marks_by_day: Mapping[str, Mapping[str, float]],
    *,
    spec: Mapping[str, float] | None = None,
    actions_by_day: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    latest_day: str | None = None,
    signal_support: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score each session's top ranks and build the suggestion matrix.

    ``sessions`` items need ``day`` and ``ranks`` (already ordered, already capped).
    ``marks_by_day`` is the close map used for the later mark.
    """
    used = dict(DEFAULT_SPEC)
    if spec:
        used.update({key: float(spec[key]) for key in DEFAULT_SPEC if key in spec})
    catalogs = {day: SymbolCatalog.build(marks.keys()) for day, marks in marks_by_day.items()}
    rank_rows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    bands = load_bands(None)

    for session in sessions:
        day = str(session["day"])
        ranks = list(session.get("ranks") or [])
        n_ranked = len(ranks)
        priced = 0
        pairs: list[tuple[float, float]] = []
        head_rets: list[float] = []
        tail_rets: list[float] = []
        for row in ranks:
            symbol = str(row.get("symbol") or "")
            entry = _num(row.get("close"))
            if entry is None:
                entry = _close_for(marks_by_day.get(day) or {}, symbol, catalogs.get(day))
            later_day, later_close = (None, None)
            next_day, next_close = (None, None)
            ret = None
            ret_next = None
            if entry is not None and entry > 0:
                next_day, next_close, later_day, later_close = _next_and_latest(
                    marks_by_day, catalogs, symbol, day
                )
                if later_close is not None:
                    ret = (later_close / entry - 1.0) * 100.0
                if next_close is not None:
                    ret_next = (next_close / entry - 1.0) * 100.0
                    priced += 1
                    pairs.append((float(row["rank"]), ret_next))
            band = position_band(int(row["rank"]), n_ranked, used)
            if ret_next is not None and band == "head":
                head_rets.append(ret_next)
            if ret_next is not None and band == "tail":
                tail_rets.append(ret_next)
            rank_rows.append(
                {
                    "day": day,
                    "pack": str(session.get("pack") or ""),
                    "symbol": symbol,
                    "ticker": row.get("ticker") or parse_symbol(symbol).ticker,
                    "rank": int(row["rank"]),
                    "position": band,
                    "rank_source": row.get("rank_source") or "sleeve",
                    "close": _round(entry),
                    "left": _round(row.get("left")),
                    "street_left": _round(row.get("street_left")),
                    "carrier": row.get("carrier") or "",
                    "rr": _round(row.get("rr")),
                    "bo": _round(row.get("bo"), 3),
                    "rng": _round(row.get("rng")),
                    "peer": row.get("peer") or "",
                    "mix": row.get("mix") or "",
                    "later_day": later_day or "",
                    "later_close": _round(later_close),
                    "ret_pct": _round(ret),
                    "next_day": next_day or "",
                    "next_close": _round(next_close),
                    "ret_next_pct": _round(ret_next),
                    "label": rank_label(ret_next, used),
                }
            )
        coverage.append(
            {
                "day": day,
                "pack": str(session.get("pack") or ""),
                "n_ranked": n_ranked,
                "n_priced": priced,
                "rank_source": str(session.get("rank_source") or ""),
                "shortfall": max(0, int(used["top"]) - n_ranked),
            }
        )
        rho = spearman(pairs)
        quality.append(
            {
                "day": day,
                "n_priced": priced,
                "spearman_rank_vs_forward": _round(rho, 4),
                "head_median": _round(median(head_rets)),
                "tail_median": _round(median(tail_rets)),
                "head_minus_tail": _round(
                    None
                    if median(head_rets) is None or median(tail_rets) is None
                    else median(head_rets) - median(tail_rets)
                ),
            }
        )
        for action in (actions_by_day or {}).get(day) or []:
            ticker = str(action.get("ticker") or "")
            family = action_family(action.get("action"))
            entry = _close_for(marks_by_day.get(day) or {}, ticker, catalogs.get(day))
            later_day, later_close = (None, None)
            next_day, next_close = (None, None)
            ret = None
            ret_next = None
            if entry is not None:
                next_day, next_close, later_day, later_close = _next_and_latest(
                    marks_by_day, catalogs, ticker, day
                )
                if later_close is not None:
                    ret = (later_close / entry - 1.0) * 100.0
                if next_close is not None:
                    ret_next = (next_close / entry - 1.0) * 100.0
            match = next((row for row in ranks if row.get("ticker") == ticker), None)
            decisions.append(
                {
                    "day": day,
                    "ticker": ticker,
                    "action": str(action.get("action") or ""),
                    "family": family,
                    "rank": match.get("rank") if match else None,
                    "in_top": bool(match),
                    "ret_pct": _round(ret),
                    "ret_next_pct": _round(ret_next),
                    "label": label_outcome(family, ret_next, bands),
                    "later_day": later_day or "",
                    "next_day": next_day or "",
                }
            )

    matrix = suggestion_matrix(rank_rows, used)
    matrix_to_latest = suggestion_matrix(rank_rows, used, field="ret_pct")
    payload = {
        "spec": {
            **used,
            "n_sessions": len(list(sessions)),
            "n_ranks": len(rank_rows),
            "n_priced": sum(1 for row in rank_rows if row.get("ret_next_pct") is not None),
            "days_at_top": sum(1 for row in coverage if row["shortfall"] == 0),
            "latest_day": latest_day or "",
            "first_day": sessions[0]["day"] if sessions else "",
            "last_day": sessions[-1]["day"] if sessions else "",
        },
        "coverage": coverage,
        "quality": quality,
        "ranks": rank_rows,
        "decisions": decisions,
        "matrix": matrix,
        "matrix_to_latest": matrix_to_latest,
        "signal_support": [dict(row) for row in (signal_support or [])],
    }
    return payload


def _slice_call(
    lever: str,
    favored_name: str,
    other_name: str,
    favored: Sequence[float],
    other: Sequence[float],
    *,
    min_n: int,
    gap: float,
    keep_text: str,
    change_text: str,
) -> dict[str, Any]:
    med_f = median(favored)
    med_o = median(other)
    if len(favored) < min_n or len(other) < min_n or med_f is None or med_o is None:
        call = _THIN
        text = f"Need at least {min_n} priced names on each side."
    elif med_f >= med_o + gap:
        call = _KEEP
        text = keep_text
    elif med_o >= med_f + gap:
        call = _CHANGE
        text = change_text
    else:
        call = _FLAT
        text = "The two medians sit inside the gap. No change from this slice."
    return {
        "lever": lever,
        "call": call,
        "favored": favored_name,
        "other": other_name,
        "favored_n": len(favored),
        "other_n": len(other),
        "favored_median": _round(med_f),
        "other_median": _round(med_o),
        "gap_pct": gap,
        "suggestion": text,
    }


def suggestion_matrix(
    ranks: Sequence[Mapping[str, Any]],
    spec: Mapping[str, float],
    *,
    field: str = "ret_next_pct",
) -> list[dict[str, Any]]:
    """One row per lever. Names without the chosen return are left out of the medians."""
    priced = [row for row in ranks if row.get(field) is not None]
    min_n = int(spec["min_n"])
    gap = float(spec["gap_pct"])

    def rets(pred: Callable[[Mapping[str, Any]], bool]) -> list[float]:
        return [float(row[field]) for row in priced if pred(row)]

    rows = [
        _slice_call(
            "rank_head",
            "ranks 1-20",
            "bottom 20 of the same list",
            rets(lambda row: row.get("position") == "head"),
            rets(lambda row: row.get("position") == "tail"),
            min_n=min_n,
            gap=gap,
            keep_text="The published head beat the tail of the same list. Keep list order as the first pass.",
            change_text="The tail beat the published head. Do not treat list order as the capture order.",
        ),
        _slice_call(
            "street_vs_edge_only",
            "street quote still positive",
            "edge_only",
            rets(lambda row: row.get("carrier") in {"street", "both"}),
            rets(lambda row: row.get("carrier") == "edge_only"),
            min_n=min_n,
            gap=gap,
            keep_text="Names with a live street quote beat edge_only. Keep passing on a new line when the street target is below the price.",
            change_text="edge_only beat names whose street target was still above the price. Revisit the edge_only pass before the next new line.",
        ),
        _slice_call(
            "unused_vs_paid",
            "rng under 40",
            "rng at least 85",
            rets(lambda row: _num(row.get("rng")) is not None and float(row["rng"]) < 40),
            rets(lambda row: _num(row.get("rng")) is not None and float(row["rng"]) >= 85),
            min_n=min_n,
            gap=gap,
            keep_text="Unused range beat paid range. Keep paid range off a new line.",
            change_text="Paid range beat unused range inside this top list. Do not drop a paid name only because rng is high.",
        ),
        _slice_call(
            "discount_vs_premium",
            "setup DISCOUNT",
            "setup PREMIUM",
            rets(lambda row: row.get("peer") == "DISCOUNT"),
            rets(lambda row: row.get("peer") == "PREMIUM"),
            min_n=min_n,
            gap=gap,
            keep_text="Setup DISCOUNT beat PREMIUM. Keep the discount as a quality flag on a new line.",
            change_text="Setup PREMIUM beat DISCOUNT. Do not require a discount before a name can lead.",
        ),
        _slice_call(
            "breakout_live_vs_flat",
            "bo at least 0.5",
            "bo under 0.15",
            rets(lambda row: _num(row.get("bo")) is not None and float(row["bo"]) >= 0.5),
            rets(lambda row: _num(row.get("bo")) is not None and float(row["bo"]) < 0.15),
            min_n=min_n,
            gap=gap,
            keep_text="A live weeks breakout beat a flat breakout. Keep bo as a tie-break, not as a blend with rr.",
            change_text="A flat breakout beat a live one. Do not promote bo ahead of the published rank order.",
        ),
    ]
    return rows


def format_highlights(payload: Mapping[str, Any]) -> str:
    spec = payload.get("spec") or {}
    lines = [
        "# Decision review",
        "",
        (
            f"Sessions {spec.get('n_sessions', 0)} "
            f"({spec.get('first_day') or '—'} to {spec.get('last_day') or '—'}). "
            f"Rank rows {spec.get('n_ranks', 0)}. "
            f"Priced forward {spec.get('n_priced', 0)}. "
            f"Days with a full top {int(spec.get('top') or 0)}: {spec.get('days_at_top', 0)}."
        ),
        "The matrix uses the close on the next briefing day. ret_pct on table ranks is the path through the latest pack.",
        "GOOD is a later close at or above the rank-good band. BAD is at or below the rank-bad band.",
        "This file is the matrix. The name tape is table ranks. One day: SELECT * FROM ranks WHERE day = 'YYYY-MM-DD'.",
        "",
        "## Suggestion matrix, next briefing day",
        "",
        "| Lever | Call | Favored median | Other median | Favored n | Other n | What to change |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("matrix") or []:
        lines.append(
            "| {lever} | {call} | {fav} | {other} | {fn} | {on} | {text} |".format(
                lever=row.get("lever"),
                call=row.get("call"),
                fav="—" if row.get("favored_median") is None else row.get("favored_median"),
                other="—" if row.get("other_median") is None else row.get("other_median"),
                fn=row.get("favored_n"),
                on=row.get("other_n"),
                text=row.get("suggestion"),
            )
            )
    lines.extend(["", "## Suggestion matrix, through the latest pack", ""])
    lines.append("Same levers on ret_pct. A September name and an October name do not share a holding length.")
    lines.append("")
    lines.append("| Lever | Call | Favored median | Other median | Favored n | Other n | What to change |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for row in payload.get("matrix_to_latest") or []:
        lines.append(
            "| {lever} | {call} | {fav} | {other} | {fn} | {on} | {text} |".format(
                lever=row.get("lever"),
                call=row.get("call"),
                fav="—" if row.get("favored_median") is None else row.get("favored_median"),
                other="—" if row.get("other_median") is None else row.get("other_median"),
                fn=row.get("favored_n"),
                on=row.get("other_n"),
                text=row.get("suggestion"),
            )
        )
    lines.extend(["", "## Coverage shortfalls", ""])
    short = [row for row in payload.get("coverage") or [] if int(row.get("shortfall") or 0) > 0]
    if not short:
        lines.append("Every session reached the top size.")
    else:
        lines.append("| Day | Ranked | Shortfall | Source |")
        lines.append("|---|---:|---:|---|")
        for row in short:
            lines.append(
                f"| {row.get('day')} | {row.get('n_ranked')} | {row.get('shortfall')} | {row.get('rank_source')} |"
            )
    lines.extend(["", "## Days where the head lagged the tail", ""])
    lagged = [
        row
        for row in payload.get("quality") or []
        if row.get("head_minus_tail") is not None and float(row["head_minus_tail"]) < 0
    ]
    lagged.sort(key=lambda row: float(row["head_minus_tail"]))
    if not lagged:
        lines.append("No priced day had a negative head-minus-tail.")
    else:
        lines.append("| Day | Head median | Tail median | Spearman |")
        lines.append("|---|---:|---:|---:|")
        for row in lagged[:8]:
            lines.append(
                f"| {row.get('day')} | {row.get('head_median')} | {row.get('tail_median')} | {row.get('spearman_rank_vs_forward')} |"
            )
        lines.append("")
        lines.append("Spearman is rank number versus later return. Negative means the top of the list had the higher return.")
    support = list(payload.get("signal_support") or [])
    lines.extend(["", "## Signal family support", ""])
    if not support:
        lines.append(
            "No family_metrics.csv was attached. src/backtests scores signal families, not these briefing ranks. "
            "Do not rerun python src/run_backtests.py for this review."
        )
    else:
        lines.append(
            "Attached from an existing backtest run. spearman_ic only. Top-20 lift is omitted because extreme ATRP lifts are outliers."
        )
        lines.append("")
        lines.append("| Family | Signal | Horizon | IC | Days |")
        lines.append("|---|---|---:|---:|---:|")
        for row in support[:12]:
            lines.append(
                f"| {row.get('family')} | {row.get('signal_name')} | {row.get('horizon_days')} | {row.get('spearman_ic')} | {row.get('day_count')} |"
            )
    lines.append("")
    return "\n".join(lines)


def latest_pack_dirs(runs_root: Path) -> list[Path]:
    """One briefing folder per calendar day, the latest stamp that day."""
    by_day: dict[str, Path] = {}
    if not runs_root.exists():
        return []
    for folder in runs_root.glob("briefing_pack_*"):
        if not folder.is_dir():
            continue
        day = pack_day(folder.name)
        if day is None:
            continue
        current = by_day.get(day)
        if current is None or folder.name > current.name:
            by_day[day] = folder
    return [by_day[day] for day in sorted(by_day)]


def _sleeve_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    sleeves = payload.get("sleeves") or {}
    rows = sleeves.get("radar_upside_100") if isinstance(sleeves, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _tape_rows_from_json(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    names = payload.get("names") or {}
    if isinstance(names, dict):
        return [row for row in names.values() if isinstance(row, dict) and row.get("symbol")]
    if isinstance(names, list):
        return [row for row in names if isinstance(row, dict) and row.get("symbol")]
    return []


def _tape_rows_from_db(db_path: Path) -> list[dict[str, Any]]:
    import duckdb

    connection = duckdb.connect(db_path.as_posix(), read_only=True)
    try:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'us2b_tape'"
            ).fetchall()
        }
        if "symbol" not in columns:
            return []
        pieces = ["symbol"]
        if "close" in columns:
            pieces.append('"close"')
        if "left" in columns:
            pieces.append('"left" AS left')
        for name in ("street_left", "rr", "bo", "rng", "peer", "mix"):
            if name in columns:
                pieces.append(name)
        frame = connection.execute(f"SELECT {', '.join(pieces)} FROM us2b_tape").fetchdf()
    finally:
        connection.close()
    return frame.to_dict(orient="records")


def load_briefing_rank_sessions(
    runs_root: str | Path,
    *,
    top: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], list[str]]:
    """Published upside-100 per day, plus a close map for the later mark."""
    notes: list[str] = []
    sessions: list[dict[str, Any]] = []
    marks: dict[str, dict[str, float]] = {}
    for folder in latest_pack_dirs(Path(runs_root)):
        day = pack_day(folder.name) or ""
        json_path = folder / "briefing_pack.json"
        payload: dict[str, Any] = {}
        if json_path.exists():
            try:
                loaded = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                notes.append(f"unreadable json {folder.name}")
        sleeve = _sleeve_rows(payload)
        db_path = folder / "briefing_pack.duckdb"
        tape: list[dict[str, Any]] = []
        if db_path.exists():
            marks[day] = closes_from_pack_db(db_path)
            if len(sleeve) < top:
                tape = _tape_rows_from_db(db_path)
        else:
            tape = _tape_rows_from_json(payload)
            pairs = [(row.get("symbol"), row.get("close")) for row in tape]
            pairs.extend((row.get("symbol"), row.get("close")) for row in sleeve)
            from generic_utils.brief_history import index_closes

            marks[day] = index_closes([(key, value) for key, value in pairs if key])
        ranks, source = complete_top(sleeve, tape, top)
        sessions.append(
            {
                "day": day,
                "pack": folder.name,
                "rank_source": source,
                "ranks": [_slim(row, index) for index, row in enumerate(ranks, start=1)],
            }
        )
        if len(ranks) < top:
            notes.append(f"{day} ranked {len(ranks)} short of {top}")
    return sessions, marks, notes


def actions_from_priors(priors_dir: str | Path | None) -> dict[str, list[dict[str, Any]]]:
    if not priors_dir:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for session in load_sessions(priors_dir):
        day = str(session["day"])
        bucket = grouped.setdefault(day, [])
        seen = {row["ticker"] for row in bucket}
        for action in session.get("actions") or []:
            ticker = str(action.get("ticker") or "")
            if ticker and ticker not in seen:
                seen.add(ticker)
                bucket.append(action)
    return grouped


def latest_family_metrics(runs_root: str | Path) -> Path | None:
    root = Path(runs_root)
    if not root.exists():
        return None
    files = [path for path in root.glob("backtests_*/family_metrics.csv") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.parent.name)


def load_signal_support(path: str | Path | None, *, per_horizon: int = 6, family_cap: int = 3) -> list[dict[str, Any]]:
    """Top spearman_ic rows at 10 and 20 days. Lift columns are not copied."""
    if path is None:
        return []
    file_path = Path(path)
    if not file_path.exists():
        return []
    grouped: dict[int, list[dict[str, Any]]] = {10: [], 20: []}
    with file_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            horizon = _num(row.get("horizon_days"))
            ic = _num(row.get("spearman_ic"))
            if horizon not in grouped or ic is None:
                continue
            grouped[int(horizon)].append(
                {
                    "family": row.get("family") or "",
                    "signal_name": row.get("signal_name") or "",
                    "horizon_days": int(horizon),
                    "spearman_ic": round(ic, 4),
                    "pair_count": None if _num(row.get("pair_count")) is None else int(float(row["pair_count"])),
                    "day_count": None if _num(row.get("day_count")) is None else int(float(row["day_count"])),
                }
            )
    kept: list[dict[str, Any]] = []
    for horizon in (10, 20):
        rows = sorted(grouped[horizon], key=lambda item: -float(item["spearman_ic"]))
        family_counts: dict[str, int] = {}
        taken = 0
        for row in rows:
            family = str(row["family"])
            if family_counts.get(family, 0) >= family_cap:
                continue
            family_counts[family] = family_counts.get(family, 0) + 1
            kept.append(row)
            taken += 1
            if taken >= per_horizon:
                break
    return kept
