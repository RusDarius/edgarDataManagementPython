"""Hindsight entry and exit search on daily_closes.

One best long per symbol: buy a session close, sell a later session close.
The book keeps at most max_names of those swings at once, taking the
highest-return swings first. This is a ceiling, not a point-in-time selector.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from generic_utils.scan_sources import rows_to_csv

from .daily import load_close_series
from .reporting import resolve_run_dir

PREDICTION_FIELDS = ("bo", "cont", "fwd", "sms", "early", "recov")
RAW_FIELDS = (
    "left",
    "RSI",
    "ATRP",
    "operating_margin_ttm",
    "gross_margin_ttm",
    "market_cap_basic",
    "Perf.5D",
    "Perf.1M",
)
EDGE_FIELDS = ("edge_forward_valuation_upside_pct",)
MIN_ENTRY_CLOSE = 5.0


def best_long_per_symbol(
    series_by_symbol: dict[str, list[tuple[date, float]]],
    *,
    min_sessions: int = 5,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    hold = max(1, int(min_sessions))
    for symbol, series in series_by_symbol.items():
        if len(series) <= hold:
            continue
        min_i: int | None = None
        best: tuple[float, int, int] | None = None
        for j in range(len(series)):
            price = series[j][1]
            if (
                min_i is not None
                and price >= MIN_ENTRY_CLOSE
                and j - min_i >= hold
            ):
                entry_px = series[min_i][1]
                ret = price / entry_px - 1.0
                if best is None or ret > best[0]:
                    best = (ret, min_i, j)
            if price >= MIN_ENTRY_CLOSE and (min_i is None or price < series[min_i][1]):
                min_i = j
        if best is None or best[0] <= 0:
            continue
        ret, i, j = best
        trades.append(
            {
                "symbol": symbol,
                "entry_date": series[i][0].isoformat(),
                "exit_date": series[j][0].isoformat(),
                "entry_close": round(series[i][1], 6),
                "exit_close": round(series[j][1], 6),
                "return_pct": round(ret * 100.0, 4),
                "sessions_held": j - i,
            }
        )
    trades.sort(key=lambda row: (-float(row["return_pct"]), row["symbol"]))
    return trades


def pack_swings(
    trades: list[dict[str, Any]],
    *,
    max_names: int = 20,
) -> list[dict[str, Any]]:
    """Keep the highest-return swings that never put more than max_names in the book."""
    cap = max(1, int(max_names))
    occupancy: dict[str, int] = defaultdict(int)
    chosen: list[dict[str, Any]] = []
    for trade in trades:
        held_days = _held_labels(trade["entry_date"], trade["exit_date"])
        if any(occupancy[day] >= cap for day in held_days):
            continue
        for day in held_days:
            occupancy[day] += 1
        chosen.append(dict(trade))
    return chosen


def simulate_equal_weight_path(
    chosen: list[dict[str, Any]],
    series_by_symbol: dict[str, list[tuple[date, float]]],
) -> list[dict[str, Any]]:
    """Fully invested in the names whose entry close is behind us and exit close is today or later."""
    price: dict[tuple[str, str], float] = {}
    sessions: list[str] = []
    seen: set[str] = set()
    for symbol, series in series_by_symbol.items():
        for session, close in series:
            label = session.isoformat()
            price[(symbol, label)] = close
            if label not in seen:
                seen.add(label)
                sessions.append(label)
    sessions.sort()
    nav = 100.0
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(sessions):
        day_return = 0.0
        names = 0
        if index > 0:
            previous = sessions[index - 1]
            rets: list[float] = []
            for trade in chosen:
                if not (trade["entry_date"] < day <= trade["exit_date"]):
                    continue
                before = price.get((trade["symbol"], previous))
                after = price.get((trade["symbol"], day))
                if before is None or after is None or before <= 0:
                    continue
                rets.append(after / before - 1.0)
            if rets:
                day_return = sum(rets) / len(rets)
                names = len(rets)
            nav *= 1.0 + day_return
        rows.append(
            {
                "session_date": day,
                "nav": round(nav, 6),
                "day_return_pct": round(day_return * 100.0, 4),
                "names": names,
            }
        )
    return rows


def _held_labels(entry_date: str, exit_date: str) -> list[str]:
    """Holding days are the sessions after the entry close, through the exit close."""
    start = date.fromisoformat(entry_date)
    end = date.fromisoformat(exit_date)
    labels: list[str] = []
    # Occupancy is keyed by the exit-side sessions we will mark. Use ISO dates
    # that fall strictly after entry and on or before exit, generated from the
    # trade endpoints only when the caller passes session labels. Here we mark
    # the open interval by the two endpoints plus a dense day walk so overlapping
    # calendar days count even when a name skips a session.
    cursor = start.toordinal() + 1
    last = end.toordinal()
    while cursor <= last:
        labels.append(date.fromordinal(cursor).isoformat())
        cursor += 1
    return labels


def attach_entry_panel(
    run_dir: str | Path,
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Latest panel row on or before the entry date. Null when the trough is before any score."""
    if not trades:
        return []
    db_path = resolve_run_dir(run_dir) / "backtracking.duckdb"
    columns = {
        str(row.get("column_name") or "")
        for row in query_move_prediction_duckdb(
            db_path,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = 'panel_rows'
            """,
        )
    }
    wanted = [name for name in (*PREDICTION_FIELDS, *RAW_FIELDS, *EDGE_FIELDS, "industry") if name in columns]
    if not wanted:
        return [dict(trade) for trade in trades]
    select_list = ", ".join(f'"{name}"' for name in wanted)
    symbols = sorted({trade["symbol"] for trade in trades})
    panel = query_move_prediction_duckdb(
        db_path,
        f"""
        SELECT CAST(decision_date AS VARCHAR) AS decision_date, symbol, {select_list}
        FROM panel_rows
        WHERE symbol IN ({", ".join("?" for _ in symbols)})
        """,
        symbols,
    )
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        by_symbol[str(row.get("symbol") or "")].append(row)
    for rows in by_symbol.values():
        rows.sort(key=lambda row: str(row.get("decision_date") or ""))

    attached: list[dict[str, Any]] = []
    for trade in trades:
        payload = dict(trade)
        history = by_symbol.get(trade["symbol"], [])
        prior = [row for row in history if str(row.get("decision_date") or "") <= trade["entry_date"]]
        chosen = prior[-1] if prior else None
        payload["panel_date"] = chosen.get("decision_date") if chosen else None
        payload["panel_timing"] = "on_or_before_entry" if chosen else "no_panel_yet"
        for name in wanted:
            payload[name] = chosen.get(name) if chosen else None
        attached.append(payload)
    return attached


def compare_entry_metrics(
    chosen: list[dict[str, Any]],
    others: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Median of each field on the packed entries versus the other symbols. Not a blend."""
    fields = [
        ("prediction", name) for name in PREDICTION_FIELDS
    ] + [("raw", name) for name in RAW_FIELDS] + [("edge", name) for name in EDGE_FIELDS]
    rows: list[dict[str, Any]] = []
    for layer, name in fields:
        chosen_values = [_num(row.get(name)) for row in chosen]
        other_values = [_num(row.get(name)) for row in others]
        chosen_clean = [value for value in chosen_values if value is not None]
        other_clean = [value for value in other_values if value is not None]
        chosen_median = _median(chosen_clean)
        other_median = _median(other_clean)
        rows.append(
            {
                "layer": layer,
                "field": name,
                "packed_n": len(chosen_clean),
                "packed_median": None if chosen_median is None else round(chosen_median, 4),
                "other_n": len(other_clean),
                "other_median": None if other_median is None else round(other_median, 4),
                "packed_minus_other": (
                    None
                    if chosen_median is None or other_median is None
                    else round(chosen_median - other_median, 4)
                ),
            }
        )
    return rows


def build_best_book(
    run_dir: str | Path,
    *,
    max_names: int = 20,
    min_sessions: int = 5,
) -> dict[str, Any]:
    run_path = resolve_run_dir(run_dir)
    series = load_close_series(run_path)
    if not series:
        raise ValueError("daily_closes is empty. Run the days step before best.")
    all_trades = best_long_per_symbol(series, min_sessions=min_sessions)
    chosen = pack_swings(all_trades, max_names=max_names)
    path_rows = simulate_equal_weight_path(chosen, series)
    chosen_symbols = {trade["symbol"] for trade in chosen}
    weak = sorted(
        (trade for trade in all_trades if trade["symbol"] not in chosen_symbols),
        key=lambda trade: float(trade["return_pct"]),
    )
    quartile = max(1, len(weak) // 4)
    control = weak[:quartile][:500]
    chosen_m = attach_entry_panel(run_path, chosen)
    control_m = attach_entry_panel(run_path, control)
    metrics = compare_entry_metrics(chosen_m, control_m)

    trades_path = rows_to_csv(run_path / "best_trades.csv", chosen_m)
    rows_to_csv(run_path / "best_book_days.csv", path_rows)
    rows_to_csv(run_path / "best_entry_metrics.csv", metrics)
    end_nav = path_rows[-1]["nav"] if path_rows else 100.0
    return {
        "run_dir": run_path.as_posix(),
        "max_names": max_names,
        "min_sessions": min_sessions,
        "candidate_symbols": len(all_trades),
        "packed_names": len(chosen),
        "end_nav": end_nav,
        "return_pct": round(float(end_nav) - 100.0, 4),
        "trades_path": trades_path.as_posix(),
        "best_trade": chosen_m[0] if chosen_m else None,
        "metrics": metrics,
    }


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


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
