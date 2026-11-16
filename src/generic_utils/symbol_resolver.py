"""Where a held or named id is found when the caller's own run does not have it.

TradingView marks one listing per company as primary, and that choice moves: on 2 Nov 2026 Alphabet's primary flipped
from NASDAQ:GOOG to NASDAQ:GOOGL, so every primary-only scan lost the held class overnight (the panel store has GOOG
since 29 Mar, GOOGL only since 2 Nov). Tickers also get renamed (NYSE:SQ -> NYSE:XYZ) or move exchange. This module is
the one place every area resolves such ids, in a fixed order:

1. ``config/generic_utils/symbol_aliases.json``: old id -> current id (renames, exchange moves). Written by hand from
   ``symbol-check`` candidates; never inferred.
2. The caller's own source run (prediction ``raw_scan_rows``, the pack, a suite table): the caller does this step.
3. The newest all-fields day DuckDB, newest run in it: the widest scan (world primaries plus US secondary share
   classes and ADRs since 3 Nov 2026).
4. The panel store's last row for the id (a stale mark carries ``as_of`` and ``age_days``).
5. Not found: reported with a reason. A held line then still counts at cost (``mark_source = cost``), so NAV never
   drops a held line silently.

It never substitutes a sibling share class or another exchange's listing (NASDAQ:GOOG never takes NASDAQ:GOOGL's
price): an id that is not found stays unresolved and is reported. No cutoffs, no scores.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.symbol_identity import parse_symbol

REPO_ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = REPO_ROOT / "config" / "generic_utils" / "symbol_aliases.json"
ALL_FIELDS_ROOT = REPO_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")


def _lit(values: Iterable[str]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values) or "''"


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


# --- aliases ----------------------------------------------------------------------------------------------


def load_aliases(path: str | Path | None = None) -> dict[str, str]:
    """{OLD EXCHANGE:TICKER: CURRENT EXCHANGE:TICKER} from the alias file (keys and values normalized)."""
    p = Path(path) if path else ALIASES_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8")).get("aliases") or {}
    return {parse_symbol(k).normalized: parse_symbol(v).normalized for k, v in raw.items() if k and v}


def canonical(symbol: Any, aliases: Mapping[str, str] | None = None) -> str:
    """The current id for ``symbol``: follows the alias chain (at most 8 hops, a loop stops where it started)."""
    sym = parse_symbol(symbol).normalized
    table = load_aliases() if aliases is None else aliases
    seen = {sym}
    for _ in range(8):
        nxt = table.get(sym)
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        sym = nxt
    return sym


# --- stores -----------------------------------------------------------------------------------------------


def newest_all_fields(root: str | Path | None = None) -> tuple[Path | None, str | None]:
    """(newest all-fields day DuckDB by mtime, its newest run_id by created_at_utc)."""
    import duckdb

    base = Path(root) if root else ALL_FIELDS_ROOT
    dbs = sorted(base.rglob("tradingview_all_fields_*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    for db in dbs:
        try:
            con = duckdb.connect(str(db), read_only=True)
            try:
                row = con.execute("SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC LIMIT 1").fetchone()
            finally:
                con.close()
        except Exception:  # a file being written
            continue
        if row:
            return db, str(row[0])
    return None, None


def _all_fields_rows(symbols: Sequence[str], db: Path, run_id: str) -> dict[str, dict[str, Any]]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'all_fields_rows'").fetchall()}
        want = [c for c in ("symbol", "name", "description", "exchange", "country", "close", "market_cap_basic", "is_primary", "type")
                if c in cols]
        cur = con.execute(f"SELECT {', '.join(want)} FROM all_fields_rows WHERE run_id = ? AND symbol IN ({_lit(symbols)})", [run_id])
        names = [d[0] for d in cur.description]
        return {str(r[0]).upper(): dict(zip(names, r)) for r in cur.fetchall()}
    finally:
        con.close()


def qualify_bare(tickers: Sequence[str], db: Path, run_id: str) -> dict[str, tuple[str | None, str]]:
    """{bare ticker: (EXCHANGE:TICKER or None, how)} from one all-fields run: the only listing with that name
    ('unique'), else the only one on a US exchange ('unique_us'), else None ('ambiguous: A, B' / 'not_found')."""
    import duckdb

    if not tickers:
        return {}
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(f"SELECT name, symbol, exchange FROM all_fields_rows WHERE run_id = ? AND name IN ({_lit(tickers)})",
                           [run_id]).fetchall()
    finally:
        con.close()
    by: dict[str, list[tuple[str, str]]] = {}
    for name, sym, exch in rows:
        by.setdefault(str(name).upper(), []).append((str(sym).upper(), str(exch).upper()))
    out: dict[str, tuple[str | None, str]] = {}
    for t in tickers:
        found = sorted(set(by.get(t, [])))
        us = [s for s, e in found if e in US_EXCHANGES]
        if len(found) == 1:
            out[t] = (found[0][0], "unique")
        elif len(us) == 1:
            out[t] = (us[0], "unique_us")
        else:
            out[t] = (None, f"ambiguous: {', '.join(s for s, _ in found)}" if found else "not_found")
    return out


def _store_rows(symbols: Sequence[str], store: Path) -> dict[str, dict[str, Any]]:
    import duckdb

    con = duckdb.connect(str(store), read_only=True)
    try:
        cur = con.execute(f"""
            SELECT symbol, as_of, exchange, close, mcap FROM panel
            WHERE symbol IN ({_lit(symbols)})
            QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY as_of DESC) = 1""")
        names = [d[0] for d in cur.description]
        return {str(r[0]).upper(): dict(zip(names, r)) for r in cur.fetchall()}
    finally:
        con.close()


def _default_store() -> Path | None:
    try:
        from generic_utils.panel_store import default_store_path, load_store_recipe

        path = Path(default_store_path(load_store_recipe()))
    except Exception:
        return None
    return path if path.exists() else None


def resolve_marks(symbols: Iterable[Any], *, aliases: Mapping[str, str] | None = None, all_fields_db: str | Path | None = None,
                  all_fields_run: str | None = None, store: str | Path | None = None, today: date | None = None) -> dict[str, dict[str, Any]]:
    """{requested id: {symbol (current id), alias_from, close, exchange, country, name, description, mcap, as_of,
    age_days, mark_source ('all_fields' | 'panel_store' | None), source_ref, reason}} for qualified ids.

    Steps 1, 3 and 4 of the module order; the caller has already tried its own run (step 2). A bare id is first
    qualified on the newest all-fields run (``qualify_bare``: its only listing, else its only US listing; ``qualified``
    says how); an ambiguous or unknown bare id stays unresolved and is reported."""
    table = load_aliases() if aliases is None else aliases
    asked = [parse_symbol(s).normalized for s in symbols if parse_symbol(s).normalized]
    out: dict[str, dict[str, Any]] = {}
    current: dict[str, str] = {}
    how: dict[str, str] = {}
    today = today or date.today()
    db, run = (Path(all_fields_db), all_fields_run) if all_fields_db and all_fields_run else newest_all_fields()
    bare = sorted({s for s in asked if not parse_symbol(s).qualified})
    quals = qualify_bare(bare, Path(db), run) if (bare and db is not None and run is not None) else {}
    for sym in asked:
        if not parse_symbol(sym).qualified:
            full, why = quals.get(sym, (None, "no_all_fields_run"))
            if full is None:
                out[sym] = {"symbol": sym, "alias_from": None, "qualified": why, "mark_source": None, "reason": "bare_id"}
                continue
            how[sym] = why
            current[sym] = canonical(full, table)
            continue
        current[sym] = canonical(sym, table)
    want = sorted(set(current.values()))
    af: dict[str, dict[str, Any]] = {}
    af_ref = None
    if want and db is not None and run is not None:
        af = _all_fields_rows(want, Path(db), run)
        af_ref = f"{Path(db).name}:{run}"
    missing = [s for s in want if _f((af.get(s) or {}).get("close")) is None]
    st_path = Path(store) if store else _default_store()
    st = _store_rows(missing, st_path) if (missing and st_path) else {}
    for req, sym in current.items():
        rec: dict[str, Any] = {"symbol": sym, "alias_from": req if (parse_symbol(req).qualified and sym != req) else None,
                               "qualified": how.get(req)}
        a, s = af.get(sym), st.get(sym)
        if a and _f(a.get("close")) is not None:
            rec.update({"close": _f(a["close"]), "exchange": a.get("exchange"), "country": a.get("country"), "name": a.get("name"),
                        "description": a.get("description"), "mcap": _f(a.get("market_cap_basic")), "as_of": None, "age_days": 0,
                        "mark_source": "all_fields", "source_ref": af_ref, "reason": None})
        elif s and _f(s.get("close")) is not None:
            as_of = s["as_of"] if isinstance(s["as_of"], date) else datetime.fromisoformat(str(s["as_of"])).date()
            rec.update({"close": _f(s["close"]), "exchange": s.get("exchange"), "country": None, "name": parse_symbol(sym).ticker,
                        "description": None, "mcap": _f(s.get("mcap")), "as_of": as_of.isoformat(), "age_days": (today - as_of).days,
                        "mark_source": "panel_store", "source_ref": st_path.as_posix() if st_path else None, "reason": None})
        else:
            rec.update({"close": None, "mark_source": None, "reason": "not_in_all_fields_or_store"})
        out[req] = rec
    return out


# --- check ------------------------------------------------------------------------------------------------


def symbol_check(symbols: Iterable[Any], *, aliases: Mapping[str, str] | None = None, store: str | Path | None = None,
                 today: date | None = None, stale_days: int = 5) -> list[dict[str, Any]]:
    """One row per id: its current id, the all-fields mark, the store's first / last day, and candidates when it looks
    old (missing from the newest all-fields run, or the store's last day older than ``stale_days`` before its newest
    file): same company description under another id, or the same ticker on another exchange. Candidates are reported,
    never applied: a confirmed one goes into symbol_aliases.json by hand."""
    import duckdb

    table = load_aliases() if aliases is None else aliases
    asked = [parse_symbol(s).normalized for s in symbols if parse_symbol(s).normalized]
    marks = resolve_marks(asked, aliases=table, store=store, today=today)
    db, run = newest_all_fields()
    st_path = Path(store) if store else _default_store()
    span: dict[str, tuple[Any, Any]] = {}
    newest_file = None
    if st_path:
        con = duckdb.connect(str(st_path), read_only=True)
        try:
            cur_ids = sorted({m["symbol"] for m in marks.values()})
            span = {str(r[0]).upper(): (r[1], r[2]) for r in con.execute(
                f"SELECT symbol, min(as_of), max(as_of) FROM panel WHERE symbol IN ({_lit(cur_ids)}) GROUP BY 1").fetchall()}
            newest_file = con.execute("SELECT max(as_of) FROM panel").fetchone()[0]
        finally:
            con.close()
    rows = []
    for req in asked:
        m = marks.get(req) or {}
        sym = m.get("symbol") or req
        first, last = span.get(sym, (None, None))
        stale = last is not None and newest_file is not None and (newest_file - last).days > stale_days
        old = m.get("mark_source") != "all_fields" or stale
        cands: list[str] = []
        if old and db is not None and run is not None:
            ticker = parse_symbol(sym).ticker
            con = duckdb.connect(str(db), read_only=True)
            try:
                desc = m.get("description")
                q = (f"SELECT symbol, description FROM all_fields_rows WHERE run_id = ? AND symbol <> ? AND exchange IN ({_lit(US_EXCHANGES)}) "
                     f"AND (name = ?" + (" OR description = ?" if desc else "") + ") ORDER BY symbol")
                params = [run, sym, ticker] + ([desc] if desc else [])
                cands = [f"{r[0]} ({r[1]})" for r in con.execute(q, params).fetchall()]
            finally:
                con.close()
        rows.append({"symbol": req, "current": sym, "alias_from": m.get("alias_from"), "qualified": m.get("qualified"),
                     "mark_source": m.get("mark_source"),
                     "close": m.get("close"), "mark_age_days": m.get("age_days"), "store_first": str(first) if first else None,
                     "store_last": str(last) if last else None, "stale": stale, "looks_old": old,
                     "candidates": "; ".join(cands) or None, "reason": m.get("reason")})
    return rows


def holdings_ids(path: str | Path | None = None, types: Sequence[str] = ("stock",)) -> list[str]:
    """Ids (``symbol``, else the bare ``ticker``) of the holdings lines whose ``instrument_type`` (default stock) is in
    ``types``. Funds are marked by the ETF scan, not by these stock stores."""
    p = Path(path) if path else REPO_ROOT / "config" / "holdings_scoring" / "current_holdings.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    lines = data.get("holdings") if isinstance(data, dict) else data
    return [str(h.get("symbol") or h.get("ticker")) for h in lines or []
            if isinstance(h, dict) and (h.get("symbol") or h.get("ticker")) and str(h.get("instrument_type") or "stock") in types]


def symbol_check_local_main() -> None:
    for row in symbol_check(holdings_ids()):
        print(row)


if __name__ == "__main__":
    symbol_check_local_main()
