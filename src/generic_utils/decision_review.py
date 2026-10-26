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
from generic_utils.symbol_identity import SymbolCatalog, display_id, lookup_mapping, parse_symbol, qualify

DEFAULT_SPEC: dict[str, float] = {
    "top": 100,
    "head": 20,
    "tail": 20,
    "min_n": 12,
    "gap_pct": 1.0,
    "rank_good_pct": 3.0,
    "rank_bad_pct": -8.0,
    "performer_pct": 15.0,
    "pump_pct": 20.0,
    "break_pct": 12.0,
    "high_rank": 25.0,
    "tape_m1_pct": 25.0,
    "tape_w_pct": 15.0,
    "tape_d5_pct": 12.0,
    "tape_m3_pct": 40.0,
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


def rollup_names(
    sightings: Sequence[Mapping[str, Any]],
    spec: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """One row per symbol across every briefing tape day.

    Path return is the first close to the last close. Peak return is the
    first close to the highest close. A pump that gives back ``break_pct``
    from that peak is ``PUMP_BROKE`` even when the last close is still up.
    A published upside rank at or inside ``high_rank`` with a path at or
    below the bad band is ``HIGH_RANK_BAD``. Tape week and month fields
    mark a runner even when the name was never on the upside 100.
    """
    used = dict(DEFAULT_SPEC)
    if spec:
        used.update({key: float(spec[key]) for key in DEFAULT_SPEC if key in spec})
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in sightings:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(row)
    names: list[dict[str, Any]] = []
    for symbol, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: str(item.get("day") or ""))
        priced = [row for row in ordered if (_num(row.get("close")) or 0) > 0]
        if not priced:
            continue
        first = priced[0]
        last = priced[-1]
        peak = max(priced, key=lambda item: float(item["close"]))
        first_close = float(first["close"])
        last_close = float(last["close"])
        peak_close = float(peak["close"])
        ret = (last_close / first_close - 1.0) * 100.0
        peak_ret = (peak_close / first_close - 1.0) * 100.0
        giveback = (last_close / peak_close - 1.0) * 100.0
        ranks = [int(row["rank"]) for row in ordered if _num(row.get("rank")) is not None]
        best_rank = min(ranks) if ranks else None

        def _max_field(field: str) -> float | None:
            values = [_num(row.get(field)) for row in ordered]
            present = [value for value in values if value is not None]
            return max(present) if present else None

        max_day = _max_field("day_ret")
        max_d5 = _max_field("d5")
        max_w = _max_field("w")
        max_m1 = _max_field("m1")
        max_m3 = _max_field("m3")
        tape_runner = any(
            (
                max_m1 is not None and max_m1 >= float(used["tape_m1_pct"]),
                max_w is not None and max_w >= float(used["tape_w_pct"]),
                max_d5 is not None and max_d5 >= float(used["tape_d5_pct"]),
                max_m3 is not None and max_m3 >= float(used["tape_m3_pct"]),
            )
        )
        runner = ret >= float(used["performer_pct"]) or tape_runner
        broke = peak_ret >= float(used["pump_pct"]) and giveback <= -float(used["break_pct"])
        high_rank_bad = (
            best_rank is not None
            and best_rank <= int(used["high_rank"])
            and ret <= float(used["rank_bad_pct"])
        )
        ranked_bad = (
            best_rank is not None
            and best_rank <= int(used["top"])
            and ret <= float(used["rank_bad_pct"])
        )
        if broke:
            path_class = "PUMP_BROKE"
        elif ret >= float(used["performer_pct"]):
            path_class = "RUNNER"
        elif high_rank_bad:
            path_class = "HIGH_RANK_BAD"
        elif ret <= float(used["rank_bad_pct"]):
            path_class = "FADE"
        elif tape_runner:
            path_class = "TAPE_RUN"
        else:
            path_class = "FLAT"
        sample = last
        names.append(
            {
                "symbol": symbol,
                "ticker": sample.get("ticker") or parse_symbol(symbol).ticker,
                "company": str(sample.get("company") or ""),
                "ind": str(sample.get("ind") or ""),
                "n_days": len(priced),
                "first_day": str(first.get("day") or ""),
                "first_close": _round(first_close),
                "peak_day": str(peak.get("day") or ""),
                "peak_close": _round(peak_close),
                "last_day": str(last.get("day") or ""),
                "last_close": _round(last_close),
                "ret_pct": _round(ret),
                "peak_ret_pct": _round(peak_ret),
                "giveback_pct": _round(giveback),
                "best_rank": best_rank,
                "max_day": _round(max_day),
                "max_d5": _round(max_d5),
                "max_w": _round(max_w),
                "max_m1": _round(max_m1),
                "max_m3": _round(max_m3),
                "path_class": path_class,
                "runner": runner and not broke,
                "pump_broke": broke,
                "high_rank_bad": high_rank_bad,
                "ranked_bad": ranked_bad,
                "tape_runner": tape_runner,
            }
        )
    names.sort(
        key=lambda row: (
            0 if row["pump_broke"] else 1 if row["high_rank_bad"] else 2 if row["path_class"] == "RUNNER" else 3,
            -(abs(row["ret_pct"]) if row["ret_pct"] is not None else 0),
            -(row["peak_ret_pct"] or 0),
            row["ticker"],
        )
    )
    return names


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
    tape_sightings: Sequence[Mapping[str, Any]] | None = None,
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
            # EXCHANGE:TICKER from the prior, else qualified from that day's marks (a bare id that stays ambiguous stays bare).
            ticker = str(action.get("symbol") or "") or qualify(action.get("ticker"), catalogs.get(day))
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
            match = next((row for row in ranks if row.get("symbol") == ticker), None) or next(
                (row for row in ranks if ":" not in ticker and row.get("ticker") == ticker), None)
            decisions.append(
                {
                    "day": day,
                    "symbol": ticker,
                    "ticker": parse_symbol(ticker).ticker,
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
    name_path = rollup_names(tape_sightings, used) if tape_sightings is not None else []
    payload = {
        "spec": {
            **used,
            "n_sessions": len(list(sessions)),
            "n_ranks": len(rank_rows),
            "n_priced": sum(1 for row in rank_rows if row.get("ret_next_pct") is not None),
            "n_names": len(name_path),
            "n_runners": sum(1 for row in name_path if row.get("path_class") == "RUNNER"),
            "n_tape_run": sum(1 for row in name_path if row.get("path_class") == "TAPE_RUN"),
            "n_pump_broke": sum(1 for row in name_path if row.get("pump_broke")),
            "n_high_rank_bad": sum(1 for row in name_path if row.get("high_rank_bad")),
            "n_tape": len(list(tape_sightings or [])),
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
        "name_path": name_path,
        "tape": [dict(row) for row in (tape_sightings or [])],
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


def _preview_names(title: str, rows: Sequence[Mapping[str, Any]], note: str, *, limit: int = 25) -> list[str]:
    ordered = list(rows)
    lines = ["", f"## {title}", "", note, f"rows={len(ordered)} shown={min(limit, len(ordered))}", ""]
    if not ordered:
        lines.append("(none)")
        return lines
    lines.append("symbol  class  ret  peak  giveback  best_rank  max_m1  max_w  first  last")
    for row in ordered[:limit]:
        lines.append(
            "  {ticker:<16} {path:<14} {ret:>7} {peak:>7} {back:>8} {rank:>9} {m1:>7} {week:>7}  {first} {fc} -> {last} {lc}".format(
                ticker=display_id(row),
                path=row.get("path_class") or "",
                ret=row.get("ret_pct") if row.get("ret_pct") is not None else "—",
                peak=row.get("peak_ret_pct") if row.get("peak_ret_pct") is not None else "—",
                back=row.get("giveback_pct") if row.get("giveback_pct") is not None else "—",
                rank=row.get("best_rank") if row.get("best_rank") is not None else "—",
                m1=row.get("max_m1") if row.get("max_m1") is not None else "—",
                week=row.get("max_w") if row.get("max_w") is not None else "—",
                first=str(row.get("first_day") or "")[5:],
                fc=row.get("first_close"),
                last=str(row.get("last_day") or "")[5:],
                lc=row.get("last_close"),
            )
        )
    if len(ordered) > limit:
        lines.append(f"  ... {len(ordered) - limit} more in queries.txt")
    return lines


def _recommendation_lines(decisions: Sequence[Mapping[str, Any]]) -> list[str]:
    capital = {"NEW", "ADD", "TRIM", "EXIT", "SHORT", "SHORT_PRE", "DERISK", "DERISK_INTO_PRINT"}
    first: dict[str, Mapping[str, Any]] = {}
    for row in decisions:
        action = str(row.get("action") or "")
        if action not in capital or row.get("ret_pct") is None:
            continue
        ticker = display_id(row)
        current = first.get(ticker)
        if current is None or str(row.get("day") or "") < str(current.get("day") or ""):
            first[ticker] = row
    ordered = sorted(first.values(), key=lambda row: -(row.get("ret_pct") or 0))
    lines = [
        "",
        "## Recommendation stack",
        "",
        "First NEW, ADD, TRIM, EXIT, SHORT, or derisk that has a later close. Return is that call's close to the latest pack close.",
        "Label on the row is the next-day band. ret_pct is the path through the latest pack.",
        f"rows={len(ordered)}",
        "",
        "symbol  action  day  ret_to_latest  next_day_label  rank",
    ]
    if not ordered:
        lines.append("(none)")
        return lines
    for row in ordered:
        lines.append(
            "  {ticker:<16} {action:<8} {day}  {ret:>7}  {label:<6}  {rank}".format(
                ticker=display_id(row),
                action=row.get("action") or "",
                day=row.get("day") or "",
                ret=row.get("ret_pct"),
                label=row.get("label") or "",
                rank=row.get("rank") if row.get("rank") is not None else "—",
            )
        )
    return lines


def performance_queries(database: str) -> str:
    """Copy-paste SQL that judges the model. The field dump in queries.txt stays below this block."""
    db = database.replace('"', '""')
    return "\n".join(
        [
            "PERFORMANCE — read this block before the field dump",
            f'database="{db}"',
            "",
            "Path % is first tape close to last tape close. It is not a 0-100.",
            "Peak % is first close to the high. Giveback is last close versus that high.",
            "Best rank is the best place on a published upside 100. Null means the name never made that list.",
            "The matrix tables judge the upside-100 order. name_path judges the whole briefing tape.",
            "",
            "1. Path runners. First close to last close cleared the performer band.",
            "   SELECT ticker, ind, ret_pct, peak_ret_pct, giveback_pct, best_rank, max_m1, max_w, first_close, last_close",
            "   FROM name_path WHERE path_class = 'RUNNER' ORDER BY ret_pct DESC;",
            "",
            "2. Tape moves whose path stayed under that band.",
            "   SELECT ticker, ind, ret_pct, max_d5, max_w, max_m1, max_m3, best_rank, path_class",
            "   FROM name_path",
            "   WHERE path_class = 'TAPE_RUN'",
            "   ORDER BY max_m1 DESC NULLS LAST;",
            "",
            "3. Pumps that broke. Peak cleared the pump band, then the last close gave back the break band.",
            "   SELECT ticker, ind, ret_pct, peak_ret_pct, giveback_pct, best_rank, first_close, peak_close, last_close",
            "   FROM name_path WHERE path_class = 'PUMP_BROKE' ORDER BY peak_ret_pct DESC;",
            "",
            "4. High rank and a bad path. Best upside rank inside the high-rank band, path at or below the bad band.",
            "   SELECT ticker, ind, best_rank, ret_pct, peak_ret_pct, giveback_pct, first_close, last_close",
            "   FROM name_path WHERE path_class = 'HIGH_RANK_BAD' ORDER BY ret_pct;",
            "",
            "5. Any upside-100 name with a bad path, including ranks outside the high-rank band.",
            "   SELECT ticker, ind, best_rank, ret_pct, peak_ret_pct, giveback_pct, max_m1",
            "   FROM name_path WHERE ranked_bad ORDER BY ret_pct;",
            "",
            "6. One symbol across briefing days.",
            "   SELECT day, \"close\", rank, w, d5, m1, m3, \"left\", rr, bo",
            "   FROM tape WHERE ticker = 'MU' ORDER BY day;",
            "",
            "7. Recommendation stack. Every published capital call, earliest day first.",
            "   SELECT day, ticker, action, family, rank, in_top, ret_pct, ret_next_pct, label",
            "   FROM decisions",
            "   WHERE action IN ('NEW','ADD','TRIM','EXIT','SHORT','SHORT_PRE','DERISK','DERISK_INTO_PRINT')",
            "   ORDER BY ticker, day;",
            "",
            "8. Does the upside-100 order point at the next day's return.",
            "   SELECT lever, call, favored_median, other_median, favored_n, other_n, suggestion",
            "   FROM matrix ORDER BY lever;",
            "",
            "9. Same levers on the path through the latest pack. Holding length is not the same for every name.",
            "   SELECT lever, call, favored_median, other_median, favored_n, other_n, suggestion",
            "   FROM matrix_to_latest ORDER BY lever;",
            "",
            "10. Days the published head lagged the tail.",
            "   SELECT day, n_priced, spearman_rank_vs_forward, head_median, tail_median, head_minus_tail",
            "   FROM quality WHERE head_minus_tail < 0 ORDER BY head_minus_tail;",
            "",
        ]
    )


def _matrix_lines(title: str, rows: Sequence[Mapping[str, Any]], note: str) -> list[str]:
    lines = ["", f"## {title}", "", note, ""]
    if not rows:
        lines.append("(none)")
        return lines
    lines.append("call    lever                         favored   n    other   n")
    for row in rows:
        lines.append(
            "  {call:<6} {lever:<28} {fav:>8} {fn:>4}  {other:>8} {on:>4}".format(
                call=row.get("call") or "",
                lever=row.get("lever") or "",
                fav="—" if row.get("favored_median") is None else row.get("favored_median"),
                fn=row.get("favored_n"),
                other="—" if row.get("other_median") is None else row.get("other_median"),
                on=row.get("other_n"),
            )
        )
        lines.append(f"         {row.get('suggestion')}")
    return lines


def format_highlights(payload: Mapping[str, Any]) -> str:
    """Judgment page. Every row of each class stays in the DuckDB and in queries.txt."""
    spec = payload.get("spec") or {}
    names = list(payload.get("name_path") or [])
    counts: dict[str, int] = {}
    for row in names:
        key = str(row.get("path_class") or "NA")
        counts[key] = counts.get(key, 0) + 1
    ranked_bad = sum(1 for row in names if row.get("ranked_bad"))
    count_text = " ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "none"
    lines = [
        "Decision review — performance",
        (
            f"sessions={spec.get('n_sessions', 0)} "
            f"first={spec.get('first_day') or '—'} last={spec.get('last_day') or '—'} "
            f"rank_rows={spec.get('n_ranks', 0)} tape_rows={spec.get('n_tape', 0)} "
            f"names={spec.get('n_names', len(names))}"
        ),
        f"path_class {count_text} ranked_bad={ranked_bad}",
        "",
        "Read this file to judge the model. queries.txt item 1 through 10 pulls every row.",
        "Path % is the first tape close to the last tape close. It is not the operator-call clock.",
        "The recommendation stack below is the operator-call clock: first capital action to the latest close.",
        "Next-day matrix medians share one step. Through-latest medians mix short and long holds.",
        "KEEP means the favored slice beat the other side by the gap. CHANGE means the other side won. FLAT is inside the gap. THIN is a thin sample.",
        "A label does not drop a name. The full tape is table name_path.",
    ]
    lines.extend(
        _matrix_lines(
            "Model, next briefing day",
            list(payload.get("matrix") or []),
            "Comparable step. Favored is the published slice. Other is the rest of that cut.",
        )
    )
    lines.extend(
        _matrix_lines(
            "Model, through the latest pack",
            list(payload.get("matrix_to_latest") or []),
            "Same cuts on the full path. Do not treat a CHANGE here as the one-step call.",
        )
    )
    lines.extend(_recommendation_lines(list(payload.get("decisions") or [])))
    runners = sorted(
        [row for row in names if row.get("path_class") == "RUNNER"],
        key=lambda row: -(row.get("ret_pct") or 0),
    )
    tape_runs = sorted(
        [row for row in names if row.get("path_class") == "TAPE_RUN"],
        key=lambda row: -(row.get("max_m1") or row.get("max_w") or 0),
    )
    pumps = sorted(
        [row for row in names if row.get("path_class") == "PUMP_BROKE"],
        key=lambda row: -(row.get("peak_ret_pct") or 0),
    )
    high_bad = sorted(
        [row for row in names if row.get("path_class") == "HIGH_RANK_BAD"],
        key=lambda row: row.get("ret_pct") if row.get("ret_pct") is not None else 0,
    )
    ranked = sorted(
        [row for row in names if row.get("ranked_bad") and row.get("path_class") != "HIGH_RANK_BAD"],
        key=lambda row: row.get("ret_pct") if row.get("ret_pct") is not None else 0,
    )
    lines.extend(
        _preview_names(
            "Path runners",
            runners,
            "First close to last close cleared the performer band. Full list: queries.txt item 1.",
        )
    )
    lines.extend(
        _preview_names(
            "Tape moves under the path band",
            tape_runs,
            "A week, 5-day, month, or 3-month field cleared its band. The path itself did not. Full list: queries.txt item 2.",
            limit=15,
        )
    )
    lines.extend(
        _preview_names(
            "Pumps that broke",
            pumps,
            "Peak cleared the pump band, then the last close gave back the break band. Full list: queries.txt item 3.",
            limit=40,
        )
    )
    lines.extend(
        _preview_names(
            "High rank, bad path",
            high_bad,
            "Best upside rank was inside the high-rank band and the path is at or below the bad band. Full list: queries.txt item 4.",
            limit=40,
        )
    )
    lines.extend(
        _preview_names(
            "Other upside-100 names with a bad path",
            ranked,
            "Best rank was inside the top 100, outside the high-rank band, and the path is at or below the bad band. Full list: queries.txt item 5.",
        )
    )
    lines.extend(["", "## Days the published head lagged the tail", ""])
    lagged = [
        row
        for row in payload.get("quality") or []
        if row.get("head_minus_tail") is not None and float(row["head_minus_tail"]) < 0
    ]
    lagged.sort(key=lambda row: float(row["head_minus_tail"]))
    if not lagged:
        lines.append("(none)")
    else:
        lines.append("day         head    tail   spearman")
        for row in lagged[:8]:
            lines.append(
                f"  {row.get('day')}  {row.get('head_median'):>6}  {row.get('tail_median'):>6}  {row.get('spearman_rank_vs_forward')}"
            )
        lines.append("Spearman is rank number versus the next-day return. Negative means the top of the list had the higher return.")
    support = list(payload.get("signal_support") or [])
    lines.extend(["", "## Signal families", ""])
    if not support:
        lines.append("Not attached. This file scores the briefing tape. Signal-family accuracy is the backtests module.")
    else:
        lines.append("family  signal  horizon  spearman_ic  days")
        for row in support[:12]:
            lines.append(
                f"  {row.get('family')}  {row.get('signal_name')}  {row.get('horizon_days')}  {row.get('spearman_ic')}  {row.get('day_count')}"
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
        quoted = {
            "close": '"close"',
            "left": '"left" AS left',
            "day": '"day" AS day_ret',
            "w": "w",
            "d5": "d5",
            "m1": "m1",
            "m3": "m3",
            "street_left": "street_left",
            "rr": "rr",
            "bo": "bo",
            "rng": "rng",
            "peer": "peer",
            "mix": "mix",
            "company": "company",
            "ind": "ind",
        }
        for name, sql in quoted.items():
            if name in columns:
                pieces.append(sql)
        frame = connection.execute(f"SELECT {', '.join(pieces)} FROM us2b_tape").fetchdf()
    finally:
        connection.close()
    return frame.to_dict(orient="records")


def _book_rows_from_db(db_path: Path) -> list[dict[str, Any]]:
    import duckdb

    connection = duckdb.connect(db_path.as_posix(), read_only=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        if "book" not in tables:
            return []
        frame = connection.execute(
            'SELECT symbol, ticker, "close" AS close, ind FROM book'
        ).fetchdf()
    finally:
        connection.close()
    return frame.to_dict(orient="records")


def _sighting(day: str, row: Mapping[str, Any], rank: int | None) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").strip()
    return {
        "day": day,
        "symbol": symbol,
        "ticker": str(row.get("ticker") or parse_symbol(symbol).ticker),
        "company": str(row.get("company") or ""),
        "ind": str(row.get("ind") or ""),
        "close": _num(row.get("close")),
        "left": _num(row.get("left")),
        "street_left": _num(row.get("street_left")),
        "rr": _num(row.get("rr")),
        "bo": _num(row.get("bo")),
        "rng": _num(row.get("rng")),
        "peer": str(row.get("peer") or ""),
        "mix": str(row.get("mix") or ""),
        "day_ret": _num(row.get("day_ret") if "day_ret" in row else row.get("day")),
        "w": _num(row.get("w")),
        "d5": _num(row.get("d5")),
        "m1": _num(row.get("m1")),
        "m3": _num(row.get("m3")),
        "rank": rank,
    }


def load_briefing_rank_sessions(
    runs_root: str | Path,
    *,
    top: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], list[dict[str, Any]], list[str]]:
    """Upside-100 for the matrix, plus every tape and book name for the path rollup."""
    notes: list[str] = []
    sessions: list[dict[str, Any]] = []
    marks: dict[str, dict[str, float]] = {}
    sightings: list[dict[str, Any]] = []
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
        rank_of = {
            str(row.get("symbol") or ""): index
            for index, row in enumerate(sleeve, start=1)
            if row.get("symbol")
        }
        db_path = folder / "briefing_pack.duckdb"
        tape: list[dict[str, Any]] = []
        book_rows: list[dict[str, Any]] = []
        if db_path.exists():
            marks[day] = closes_from_pack_db(db_path)
            tape = _tape_rows_from_db(db_path)
            book_rows = _book_rows_from_db(db_path)
        else:
            tape = _tape_rows_from_json(payload)
            pairs = [(row.get("symbol"), row.get("close")) for row in tape]
            pairs.extend((row.get("symbol"), row.get("close")) for row in sleeve)
            from generic_utils.brief_history import index_closes

            marks[day] = index_closes([(key, value) for key, value in pairs if key])
        seen: set[str] = set()
        for row in tape:
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            sightings.append(_sighting(day, row, rank_of.get(symbol)))
        for row in book_rows:
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            sightings.append(_sighting(day, row, rank_of.get(symbol)))
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
    return sessions, marks, sightings, notes


def actions_from_priors(priors_dir: str | Path | None) -> dict[str, list[dict[str, Any]]]:
    if not priors_dir:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for session in load_sessions(priors_dir):
        day = str(session["day"])
        bucket = grouped.setdefault(day, [])
        seen = {row.get("symbol") or row["ticker"] for row in bucket}
        for action in session.get("actions") or []:
            ticker = str(action.get("symbol") or action.get("ticker") or "")
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
