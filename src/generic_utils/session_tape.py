"""Join two all-fields scans.

``build_post_earn`` keeps names that reported on a caller date.
``build_named_premarket`` keeps a caller symbol list and says whether
``premarket_change`` was refreshed. Neither applies a leftover or
market-cap cutoff.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from operator_briefing.extract import _parse_date

SESSION_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "industry",
    "close",
    "change",
    "premarket_change",
    "premarket_gap",
    "premarket_close",
    "postmarket_change",
    "postmarket_close",
    "earnings_release_date",
    "earnings_release_time",
    "price_target_average",
    "RSI",
)

_WHEN = {
    "1": "AMC",
    "1.0": "AMC",
    "-1": "BMO",
    "-1.0": "BMO",
    "0": "UNSPEC",
    "0.0": "UNSPEC",
}


def when_label(value: Any) -> str:
    """TradingView earnings_release_time. 1 after close, -1 before open."""
    if value is None or value == "":
        return "UNSPEC"
    text = str(value).strip()
    return _WHEN.get(text, "UNSPEC")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def reported_on(row: Mapping[str, Any], earn_on: date) -> bool:
    """True when the last release date is the caller's day."""
    return _parse_date(row.get("earnings_release_date")) == earn_on


def _index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            out[symbol] = row
    return out


def join_post_earn(
    prior_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
    earn_on: date,
    *,
    exchanges: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """One row per symbol that reported on earn_on in either scan.

    Sorted by absolute session-day return, then absolute after-hours return.
    Missing returns sort last. This order is not a score.
    """
    allowed = {item.strip().upper() for item in exchanges} if exchanges else None
    prior = _index(prior_rows)
    current = _index(current_rows)
    symbols = set(prior) | set(current)
    out: list[dict[str, Any]] = []
    for symbol in symbols:
        now = current.get(symbol) or {}
        then = prior.get(symbol) or {}
        if not (reported_on(now, earn_on) or reported_on(then, earn_on)):
            continue
        exchange = str(now.get("exchange") or then.get("exchange") or "")
        if allowed is not None and exchange.upper() not in allowed:
            continue
        afternoon = _num(then.get("close"))
        session = _num(now.get("close"))
        vs_afternoon = None
        if afternoon and session:
            vs_afternoon = (session / afternoon - 1.0) * 100.0
        pre_now = _num(now.get("premarket_change"))
        pre_then = _num(then.get("premarket_change"))
        pre_repeat = (
            pre_now is not None
            and pre_then is not None
            and abs(pre_now - pre_then) < 0.05
        )
        source = now or then
        out.append(
            {
                "symbol": symbol,
                "company": source.get("name") or source.get("company"),
                "ind": source.get("industry"),
                "exchange": exchange or None,
                "when_label": when_label(
                    source.get("earnings_release_time")
                    if now
                    else then.get("earnings_release_time")
                ),
                "earn_on": earn_on.isoformat(),
                "afternoon_close": _round(afternoon, 2),
                "afternoon_day": _round(_num(then.get("change")), 2),
                "session_close": _round(session, 2),
                "session_day": _round(_num(now.get("change")), 2),
                "vs_afternoon": _round(vs_afternoon, 2),
                "pre_chg": _round(pre_now, 2),
                "pre_gap": _round(_num(now.get("premarket_gap")), 2),
                "pre_close": _round(_num(now.get("premarket_close")), 2),
                "pre_repeat": pre_repeat,
                "post_chg": _round(_num(now.get("postmarket_change")), 2),
                "post_close": _round(_num(now.get("postmarket_close")), 2),
                "pt": _round(_num(source.get("price_target_average")), 2),
                "rsi": _round(_num(source.get("RSI")), 1),
            }
        )

    def _sort_key(row: Mapping[str, Any]) -> tuple[float, float]:
        day = row.get("session_day")
        post = row.get("post_chg")
        day_key = -(abs(day)) if isinstance(day, (int, float)) else 0.0
        post_key = -(abs(post)) if isinstance(post, (int, float)) else 0.0
        if day is None:
            day_key = 1.0
        return (day_key, post_key)

    out.sort(key=_sort_key)
    return out


def newest_run_id(database_path: str | Path) -> str:
    from generic_utils.scan_sources import rows_from_duckdb

    rows = rows_from_duckdb(
        database_path,
        "SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC NULLS LAST LIMIT 1",
        [],
    )
    if not rows or not rows[0].get("run_id"):
        raise FileNotFoundError(f"no run_metadata in {database_path}")
    return str(rows[0]["run_id"])


def load_session_rows(database_path: str | Path, run_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    from generic_utils.series import fetch_named

    chosen = run_id or newest_run_id(database_path)
    fetched = fetch_named(
        database_path,
        table="all_fields_rows",
        columns=list(SESSION_COLUMNS),
        run_id=chosen,
    )
    return chosen, list(fetched["rows"])


def parse_earn_on(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"earn-on date {value!r} is not YYYY-MM-DD")
    return parsed


PREMARKET_REPEAT_TOL = 0.05


def premarket_repeated(prior_change: Any, current_change: Any, tol: float = PREMARKET_REPEAT_TOL) -> bool:
    """True when the later premarket_change matches the prior file.

    A match means the later scan did not refresh the morning quote.
    """
    prior = _num(prior_change)
    current = _num(current_change)
    return prior is not None and current is not None and abs(current - prior) < tol


def implied_premarket_price(close: Any, premarket_change: Any, *, repeated: bool) -> float | None:
    """Cash close times the premarket percent. Empty when the percent was not refreshed."""
    if repeated:
        return None
    price = _num(close)
    change = _num(premarket_change)
    if price is None or change is None:
        return None
    return price * (1.0 + change / 100.0)


def join_named_premarket(
    prior_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    """One row per requested symbol. No earnings date and no market-cap cutoff.

    ``pre_px`` is the cash close adjusted by ``premarket_change`` only when
    that percent changed versus the prior scan. ``pre_repeat`` true means
    leave the cash close as the mark.
    """
    prior = _index(prior_rows)
    current = _index(current_rows)
    out: list[dict[str, Any]] = []
    for raw in symbols:
        symbol = str(raw).strip()
        if not symbol:
            continue
        now = current.get(symbol) or {}
        then = prior.get(symbol) or {}
        source = now or then
        pre_now = _num(now.get("premarket_change"))
        pre_then = _num(then.get("premarket_change"))
        repeated = premarket_repeated(pre_then, pre_now)
        close = _num(now.get("close"))
        out.append(
            {
                "symbol": symbol,
                "ind": source.get("industry"),
                "close": _round(close, 2),
                "prior_close": _round(_num(then.get("close")), 2),
                "day": _round(_num(now.get("change")), 2),
                "w": _round(_num(now.get("Perf.W")), 2),
                "d5": _round(_num(now.get("Perf.5D")), 2),
                "m1": _round(_num(now.get("Perf.1M")), 2),
                "m3": _round(_num(now.get("Perf.3M")), 2),
                "pre_chg": _round(pre_now, 2),
                "prior_pre_chg": _round(pre_then, 2),
                "pre_gap": _round(_num(now.get("premarket_gap")), 2),
                "pre_repeat": repeated,
                "pre_px": _round(implied_premarket_price(close, pre_now, repeated=repeated), 2),
                "rsi": _round(_num(now.get("RSI")), 1),
                "sma50": _round(_num(now.get("SMA50")), 2),
                "sma200": _round(_num(now.get("SMA200")), 2),
                "pt": _round(_num(now.get("price_target_average")), 2),
                "atrp": _round(_num(now.get("ATRP")), 2),
            }
        )
    return out


def build_named_premarket(
    *,
    prior_db: str | Path,
    current_db: str | Path,
    symbols: Sequence[str],
    prior_run: str | None = None,
    current_run: str | None = None,
) -> dict[str, Any]:
    """Load two scans for a caller symbol list and join the premarket quote."""
    from generic_utils.series import fetch_named

    columns = list(SESSION_COLUMNS) + ["Perf.W", "Perf.5D", "Perf.1M", "Perf.3M", "SMA50", "SMA200", "ATRP"]
    prior_id = prior_run or newest_run_id(prior_db)
    current_id = current_run or newest_run_id(current_db)
    prior_fetched = fetch_named(
        prior_db,
        table="all_fields_rows",
        columns=columns,
        run_id=prior_id,
        id_field="symbol",
        ids=list(symbols),
    )
    current_fetched = fetch_named(
        current_db,
        table="all_fields_rows",
        columns=columns,
        run_id=current_id,
        id_field="symbol",
        ids=list(symbols),
    )
    rows = join_named_premarket(prior_fetched["rows"], current_fetched["rows"], symbols)
    return {
        "rows": rows,
        "spec": {
            "prior_db": str(prior_db),
            "prior_run": prior_id,
            "current_db": str(current_db),
            "current_run": current_id,
            "symbols": [str(item).strip() for item in symbols if str(item).strip()],
            "n": len(rows),
            "notes": [
                "close is the cash close on the later file",
                "pre_px applies pre_chg to that close only when pre_repeat is false",
                "pre_repeat means premarket_change was not refreshed",
                "no earnings date, leftover, or market-cap cutoff",
            ],
        },
    }


def build_post_earn(
    *,
    prior_db: str | Path,
    current_db: str | Path,
    earn_on: str | date,
    prior_run: str | None = None,
    current_run: str | None = None,
    exchanges: Sequence[str] | None = ("NASDAQ", "NYSE", "AMEX"),
) -> dict[str, Any]:
    """Load two scans and return the post-earn join. No cutoff beyond the date."""
    day = parse_earn_on(earn_on)
    prior_id, prior_rows = load_session_rows(prior_db, prior_run)
    current_id, current_rows = load_session_rows(current_db, current_run)
    rows = join_post_earn(prior_rows, current_rows, day, exchanges=exchanges)
    return {
        "rows": rows,
        "spec": {
            "earn_on": day.isoformat(),
            "prior_db": str(prior_db),
            "prior_run": prior_id,
            "current_db": str(current_db),
            "current_run": current_id,
            "exchanges": list(exchanges) if exchanges else None,
            "n": len(rows),
            "notes": [
                "afternoon_* is the prior scan, which can be an intraday mark",
                "session_* is the later scan",
                "pre_repeat means premarket_change was not refreshed",
                "no leftover, RSI, or market-cap cutoff",
            ],
        },
    }


_PROJECT = Path(__file__).resolve().parents[2]
_AF_ROOT = _PROJECT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
_PACK_RUNS = _PROJECT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
_HOLDINGS = _PROJECT / "config" / "holdings_scoring" / "current_holdings.json"
DEFAULT_PREMARKET_RECIPE = _PROJECT / "config" / "generic_utils" / "premarket_book.json"


def _day_sort_key(path: Path) -> tuple[int, int, int]:
    day, month, year = path.name.split("_")
    return int(year), int(month), int(day)


def latest_all_fields_pair() -> tuple[Path, Path]:
    """The two newest day folders that contain an all-fields DuckDB.

    The later file is the one to read for premarket. The earlier file is the
    prior cash scan. This is not a compile.
    """
    found: list[Path] = []
    if _AF_ROOT.exists():
        for folder in _AF_ROOT.iterdir():
            if not folder.is_dir() or folder.name.count("_") != 2:
                continue
            db = folder / f"tradingview_all_fields_{folder.name}.duckdb"
            if db.exists():
                found.append(db)
    found.sort(key=lambda path: _day_sort_key(path.parent))
    if len(found) < 2:
        raise FileNotFoundError("need two all-fields day databases for a premarket join")
    return found[-2], found[-1]


def load_premarket_recipe(path: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.run_export import load_json_recipe

    recipe_path = Path(path) if path else DEFAULT_PREMARKET_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "premarket_book")
    payload.setdefault("context_symbols", [])
    payload.setdefault("mcap_min", 2_000_000_000)
    return payload


def book_symbols_from_latest_pack() -> list[str]:
    """Exchange symbols on the newest briefing pack book. Empty if no pack."""
    import duckdb

    packs = [
        path / "briefing_pack.duckdb"
        for path in _PACK_RUNS.iterdir()
        if path.is_dir() and path.name.startswith("briefing_pack_") and (path / "briefing_pack.duckdb").exists()
    ] if _PACK_RUNS.exists() else []
    if not packs:
        return []
    packs.sort(key=lambda path: path.parent.name)
    con = duckdb.connect(str(packs[-1]), read_only=True)
    rows = con.execute("SELECT symbol FROM book WHERE symbol IS NOT NULL").fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def holdings_symbols(path: str | Path | None = None) -> list[str]:
    """Symbol when the holdings row has one, otherwise the ticker."""
    import json

    payload = json.loads(Path(path or _HOLDINGS).read_text(encoding="utf-8"))
    out: list[str] = []
    for row in payload.get("holdings") or []:
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip()
        if symbol:
            out.append(symbol)
    return out


def premarket_universe(recipe: Mapping[str, Any] | None = None) -> tuple[list[str], set[str]]:
    """Book symbols plus the caller context list. Context is not a ranking."""
    used = recipe or load_premarket_recipe()
    book = book_symbols_from_latest_pack() or holdings_symbols()
    context = [str(item).strip() for item in (used.get("context_symbols") or []) if str(item).strip()]
    book_set = {item.upper() for item in book}
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in list(book) + context:
        key = symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(symbol)
    return ordered, book_set


def tape_climate(
    current_db: str | Path,
    run_id: str,
    *,
    mcap_min: float = 2_000_000_000,
) -> list[dict[str, Any]]:
    """Industry median session change and premarket change on the morning file.

    The market-cap floor is the same US $2B line the pack uses. It is not a
    new eligibility screen. ``change`` is the cash-session field on that file.
    ``premarket_change`` is separate and may be stale.
    """
    import duckdb

    con = duckdb.connect(str(current_db), read_only=True)
    rows = con.execute(
        """
        SELECT industry AS ind,
               count(*) AS n,
               round(median(TRY_CAST(change AS DOUBLE)), 2) AS day_med,
               round(median(TRY_CAST(premarket_change AS DOUBLE)), 2) AS pre_med,
               round(100.0 * avg(CASE WHEN TRY_CAST(change AS DOUBLE) < 0 THEN 1 ELSE 0 END), 1) AS pct_down
        FROM all_fields_rows
        WHERE run_id = ?
          AND TRY_CAST(market_cap_basic AS DOUBLE) >= ?
          AND industry IS NOT NULL
        GROUP BY industry
        HAVING count(*) >= 6
        ORDER BY day_med
        """,
        [run_id, mcap_min],
    ).fetchall()
    return [
        {"ind": ind, "n": n, "day_med": day_med, "pre_med": pre_med, "pct_down": pct_down}
        for ind, n, day_med, pre_med, pct_down in rows
    ]


def build_premarket_book(
    *,
    prior_db: str | Path | None = None,
    current_db: str | Path | None = None,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Book plus context names, with a premarket mark. Not a briefing compile."""
    used = recipe or load_premarket_recipe()
    if prior_db is None or current_db is None:
        discovered_prior, discovered_current = latest_all_fields_pair()
        prior_db = prior_db or discovered_prior
        current_db = current_db or discovered_current
    symbols, book_set = premarket_universe(used)
    built = build_named_premarket(prior_db=prior_db, current_db=current_db, symbols=symbols)
    for row in built["rows"]:
        symbol = str(row.get("symbol") or "").strip().upper()
        row["in_book"] = symbol in book_set
    current_run = str((built.get("spec") or {}).get("current_run") or "")
    climate = tape_climate(current_db, current_run, mcap_min=float(used.get("mcap_min") or 2_000_000_000))
    built["climate"] = climate
    built["spec"]["mode"] = "premarket_book"
    built["spec"]["in_book_n"] = sum(1 for row in built["rows"] if row.get("in_book"))
    built["spec"]["notes"] = list((built.get("spec") or {}).get("notes") or []) + [
        "premarket_book is not a compile and does not replace the cash-session briefing",
        "in_book is membership on the newest pack book, else the holdings file",
        "context_symbols are a caller list, not a ranking and not a buy list",
    ]
    return built
