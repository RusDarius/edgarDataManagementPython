"""Print pointers: the last print's surprise, the coming quarter's step, and days from / to a print, as tape columns.

Snapshot columns come from one all-fields row (no history):

- ``last_surprise``: TradingView's EPS surprise of the last reported quarter (``eps_surprise_percent_fq``), percent.
- ``rev_surprise``: revenue of the last quarter against its forecast (``total_revenue_fq`` / ``revenue_forecast_fq``),
  percent; TradingView's ``revenue_surprise_percent_fq`` when the forecast is missing (the export dropped it on 2026-10-14).
- ``surprise_mix``: EPS / revenue as B (beat, surprise >= 0) or M (miss), ``-`` when one side is missing.
- ``eps_q_step`` / ``rev_q_step``: the coming quarter's consensus against the last reported quarter, percent.
- ``earn_last`` / ``dsp``: the last print date and days since it; ``earn_slot``: BMO / AMC of the next print.

History columns (``n_prints``, ``react_last``, ``react_abs_mean``, ``react_up``, ``since_print``) come from the panel
store's ``prints`` table when the store holds the scan day, else from the day files through earnings_week.

Zero cutoffs: a beat is a sign, nothing else is a threshold. No ranking and no composite.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.ranking import to_float

_f = to_float

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "last_surprise", "rev_surprise", "surprise_mix", "eps_q_step", "rev_q_step", "earn_last", "dsp", "earn_slot",
)
HISTORY_COLUMNS: tuple[str, ...] = ("n_prints", "react_last", "react_abs_mean", "react_up", "since_print")
POINTER_COLUMNS: tuple[str, ...] = SNAPSHOT_COLUMNS + HISTORY_COLUMNS


def _pct(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1.0) * 100.0, 2)


def rev_surprise_pct(actual: Any, estimate: Any, tv_value: Any = None) -> float | None:
    """Revenue surprise in percent from actual and forecast; TradingView's own figure when either is missing."""
    derived = _pct(actual, estimate) if (_f(actual) or 0) > 0 else None
    if derived is not None:
        return derived
    tv = _f(tv_value)
    return round(tv, 2) if tv is not None else None


def surprise_mix(eps_surprise: Any, rev_surprise: Any) -> str | None:
    """'B/B', 'B/M', 'M/B', 'M/M' (EPS / revenue, beat when >= 0); '-' for a missing side; None when both are missing."""
    def side(value: Any) -> str:
        number = _f(value)
        return "-" if number is None else ("B" if number >= 0 else "M")

    eps, rev = side(eps_surprise), side(rev_surprise)
    return None if eps == "-" and rev == "-" else f"{eps}/{rev}"


def _epoch_date(value: Any) -> date | None:
    number = _f(value)
    if number is not None and number > 0:
        return datetime.fromtimestamp(number, tz=timezone.utc).date()
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def snapshot_pointers(row: Mapping[str, Any], scan_day: date | None) -> dict[str, Any]:
    """Pointer columns from one row keyed by the pack aliases (extract.AF_CANDIDATES / earnings_week.RAW_FIELDS names)."""
    from generic_utils.earnings_week import epoch_dt, slot_label

    last_surprise = _f(row.get("last_surprise"))
    rev = rev_surprise_pct(row.get("rev_last_q"), row.get("rev_est_last_q"), row.get("rev_surprise_tv"))
    earn_last = _epoch_date(row.get("earn_last"))
    nxt = epoch_dt(row.get("earn_next"))
    return {
        "last_surprise": round(last_surprise, 2) if last_surprise is not None else None,
        "rev_surprise": rev,
        "surprise_mix": surprise_mix(last_surprise, rev),
        "eps_q_step": _pct(row.get("eps_next_q"), row.get("eps_last_q")),
        "rev_q_step": _pct(row.get("rev_next_q"), row.get("rev_last_q")),
        "earn_last": earn_last.isoformat() if earn_last else None,
        "dsp": (scan_day - earn_last).days if scan_day and earn_last else None,
        "earn_slot": slot_label(row.get("earn_next_time"), nxt) if nxt else None,
    }


def summarize_prints(prints: Sequence[Mapping[str, Any]], close_now: Any, split_seq_now: Any = None) -> dict[str, Any]:
    """History pointers from one name's past prints (oldest-first, with ``react`` and ``react_close``)."""
    done = [p for p in prints if _f(p.get("react")) is not None]
    out: dict[str, Any] = {"n_prints": len(done)}
    if not done:
        return out
    last = done[-1]
    out["react_last"] = round(_f(last["react"]), 2)
    out["react_abs_mean"] = round(sum(abs(_f(p["react"])) for p in done) / len(done), 2)
    out["react_up"] = f"{sum(1 for p in done if _f(p['react']) > 0)}/{len(done)}"
    same_scale = split_seq_now is None or last.get("split_seq") is None or _f(last.get("split_seq")) == _f(split_seq_now)
    out["since_print"] = _pct(close_now, last.get("react_close")) if same_scale else None
    return out


def _store_prints(store: Path, symbols: Sequence[str], scan_day: date) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]] | None:
    """{symbol: prints} from the panel store when it holds ``scan_day``; None when it does not."""
    import duckdb

    if not store.exists():
        return None
    con = duckdb.connect(str(store), read_only=True)
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        if not {"prints", "ingest_log", "series"} <= tables:
            return None
        newest = con.execute("SELECT max(as_of) FROM ingest_log").fetchone()[0]
        if newest is None or newest < scan_day:
            return None
        con.execute("CREATE TEMP TABLE want (symbol VARCHAR)")
        con.executemany("INSERT INTO want VALUES (?)", [[s] for s in symbols])
        cur = con.execute(
            "SELECT p.symbol, p.react_file, p.react, p.react_close, p.split_seq FROM prints p JOIN want USING (symbol) "
            "WHERE p.react_file <= ? ORDER BY p.symbol, p.print_date", [scan_day])
        names = [d[0] for d in cur.description]
        out: dict[str, list[dict[str, Any]]] = {}
        for values in cur.fetchall():
            rec = dict(zip(names, values))
            out.setdefault(str(rec["symbol"]), []).append(rec)
        seq = dict(con.execute(
            "SELECT s.symbol, s.split_seq FROM series s JOIN want USING (symbol) WHERE s.as_of = ?", [scan_day]).fetchall())
    finally:
        con.close()
    return out, seq


def history_pointers(symbols: Sequence[str], closes: Mapping[str, Any], *, scan_day: date,
                     store: str | Path | None = None, root: str | Path | None = None) -> tuple[dict[str, dict[str, Any]], str]:
    """{symbol: history pointers} and the source used ('panel_store' or 'day_files')."""
    from generic_utils.panel_store import default_store_path

    ids = [s for s in symbols if s]
    found = _store_prints(Path(store) if store else default_store_path(), ids, scan_day)
    if found is not None:
        prints, seq = found
        out = {s: summarize_prints(prints.get(s, []), closes.get(s), seq.get(s)) for s in ids if s in seq}
        # names outside the store's floor (a non-US holding) still get their history from the day files
        rest = [s for s in ids if s not in seq]
        if rest:
            out.update(_day_file_pointers(rest, closes, scan_day, root))
        return out, "panel_store" if not rest else f"panel_store + day_files ({len(rest)})"
    return _day_file_pointers(ids, closes, scan_day, root), "day_files"


def _day_file_pointers(ids: Sequence[str], closes: Mapping[str, Any], scan_day: date,
                       root: str | Path | None) -> dict[str, dict[str, Any]]:
    from generic_utils.earnings_week import load_history, load_recipe, past_prints, print_reactions

    recipe = load_recipe()
    history = load_history(ids, recipe, root=root)
    gap = int((recipe.get("reaction") or {}).get("min_days_between_prints", 20))
    out: dict[str, dict[str, Any]] = {}
    for symbol in ids:
        points = [p for p in history.get(symbol) or [] if str(p.get("as_of"))[:10] <= scan_day.isoformat()]
        reactions = print_reactions(points, past_prints(points, scan_day + timedelta(days=1), min_days_apart=gap), recipe)
        # day-file points are split-adjusted to the newest scale, so the newest close compares directly
        out[symbol] = summarize_prints(reactions, points[-1].get("close") if points else closes.get(symbol))
    return out


def attach_print_pointers(rows: list[dict[str, Any]], *, scan_day: date | None, history: bool = True,
                          store: str | Path | None = None, root: str | Path | None = None,
                          symbols: Iterable[str] | None = None) -> dict[str, Any]:
    """Fill the history pointer columns on ``rows`` in place (snapshot columns are the caller's). Returns a note."""
    if not history or scan_day is None:
        return {"history": "skipped"}
    wanted = list(symbols) if symbols is not None else [str(r.get("symbol") or "") for r in rows]
    closes = {str(r.get("symbol")): r.get("close") for r in rows}
    try:
        found, source = history_pointers(wanted, closes, scan_day=scan_day, store=store, root=root)
    except Exception as exc:  # noqa: BLE001 - history is a pointer, never a reason to fail the caller
        return {"history": "error", "error": f"{type(exc).__name__}: {exc}"}
    for row in rows:
        for key, value in (found.get(str(row.get("symbol"))) or {}).items():
            row[key] = value
    return {"history": source, "n": len(found)}
