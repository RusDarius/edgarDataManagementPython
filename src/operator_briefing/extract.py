"""Pull a bounded, join-ready name table from locked scan artifacts."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from generic_utils.aggregations import group_stats, numeric_summary
from generic_utils.ranking import to_float as _f

from .discovery import LockedSources
from .sleeves import leftover_pct, pct_vs, range_position_pct

PROFILE_WEEKS = (
    "breakout_long_v1",
    "quality_continuation_v1",
    "forward_edge_active_v2",
    "early_momentum_inflection_v1",
    "sustained_momentum_safety_v1",
    "fragility_short",
    "mean_reversion_exhaustion_v1",
)

AF_CANDIDATES = {
    "company": ("Company", "company_name", "name"),
    "exchange": ("exchange",),
    "country": ("country",),
    "sector": ("sector",),
    "industry": ("industry",),
    "close": ("close",),
    "mcap": ("market_cap_basic",),
    "day": ("change",),
    "d5": ("Perf.5D",),
    "m1": ("Perf.1M",),
    "rsi": ("RSI",),
    "sma20": ("SMA20",),
    "sma50": ("SMA50",),
    "sma200": ("SMA200",),
    "pt": ("price_target_average", "price_target_1y"),
    "hi52": ("price_52_week_high", "High.52Week", "price_52_week_high_current"),
    "lo52": ("price_52_week_low", "Low.52Week", "price_52_week_low_current"),
    "earn": (
        "earnings_release_next_date",
        "earnings_release_next_trading_date_fq",
        "earnings_release_next_calendar_date",
    ),
    "pe": ("price_earnings_ttm",),
    "opm": ("operating_margin",),
    "adx": ("ADX",),
}


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(database_path: Path, table: str) -> set[str]:
    rows = query_move_prediction_duckdb(database_path, f"PRAGMA table_info({_qident(table)})")
    return {str(r.get("name")) for r in rows}


_table_columns = table_columns


def _pick(available: set[str], candidates: Iterable[str]) -> str | None:
    for name in candidates:
        if name in available:
            return name
    return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        num = float(value)
        if num > 1e12:
            num /= 1000.0
        if 1e9 < num < 4e10:
            return datetime.fromtimestamp(num, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _scan_day(day_label: str) -> date | None:
    # DD_MM_YYYY
    parts = day_label.replace("-", "_").split("_")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def load_csv_by_symbol(path: Path | None, symbol_key: str = "symbol") -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = str(row.get(symbol_key) or "").strip()
            if symbol:
                out[symbol] = row
    return out


def extract_all_fields(
    sources: LockedSources,
    extra_symbols: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    available = _table_columns(sources.all_fields_db, "all_fields_rows")
    select_parts = ["symbol"]
    aliases: dict[str, str] = {}
    for alias, candidates in AF_CANDIDATES.items():
        col = _pick(available, candidates)
        if col:
            select_parts.append(f"{_qident(col)} AS {alias}")
            aliases[alias] = col
    extras = [s for s in (extra_symbols or []) if s]
    extra_sql = ""
    params: list[Any] = [sources.all_fields_run_id]
    if extras:
        extra_sql = f" OR symbol IN ({','.join(['?'] * len(extras))})"
        params.extend(extras)
    us_exchanges = ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "CBOE")
    exch_sql = ",".join("'" + x + "'" for x in us_exchanges)
    type_filter = ""
    if "type" in available:
        type_filter = "AND (type IS NULL OR type IN ('stock', 'dr'))"
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM all_fields_rows
        WHERE run_id = ?
          {type_filter}
          AND (
            country = 'United States'
            OR exchange IN ({exch_sql})
            {extra_sql}
          )
    """
    rows = query_move_prediction_duckdb(sources.all_fields_db, sql, params)
    scan = _scan_day(sources.all_fields_day_label)
    out: list[dict[str, Any]] = []
    for row in rows:
        close = _f(row.get("close"))
        rec = {
            "symbol": row.get("symbol"),
            "company": row.get("company"),
            "exchange": row.get("exchange"),
            "country": row.get("country"),
            "sector": row.get("sector"),
            "ind": row.get("industry"),
            "close": close,
            "mcap": _f(row.get("mcap")),
            "day": _f(row.get("day")),
            "d5": _f(row.get("d5")),
            "m1": _f(row.get("m1")),
            "rsi": _f(row.get("rsi")),
            "adx": _f(row.get("adx")),
            "pe": _f(row.get("pe")),
            "opm": _f(row.get("opm")),
            "pt": _f(row.get("pt")),
            "sma20": _f(row.get("sma20")),
            "sma50": _f(row.get("sma50")),
            "sma200": _f(row.get("sma200")),
        }
        rec["vs50"] = pct_vs(close, rec["sma50"])
        rec["vs200"] = pct_vs(close, rec["sma200"])
        rec["rng"] = range_position_pct(close, row.get("lo52"), row.get("hi52"))
        rec["street_left"] = leftover_pct(close, rec["pt"], None)
        earn = _parse_date(row.get("earn"))
        rec["earn"] = earn.isoformat() if earn else None
        rec["dte"] = (earn - scan).days if earn and scan else None
        rec["_af_aliases"] = aliases
        out.append(rec)
    return out


def extract_profile_weeks(sources: LockedSources) -> dict[str, dict[str, float | None]]:
    placeholders = ",".join(["?"] * len(PROFILE_WEEKS))
    rows = query_move_prediction_duckdb(
        sources.prediction_db,
        f"""
        SELECT symbol, profile_name, score
        FROM profile_horizon_scores
        WHERE run_id = ?
          AND horizon_name = 'weeks'
          AND profile_name IN ({placeholders})
        """,
        [sources.prediction_run_id, *PROFILE_WEEKS],
    )
    by_symbol: dict[str, dict[str, float | None]] = defaultdict(dict)
    for row in rows:
        by_symbol[str(row["symbol"])][str(row["profile_name"])] = _f(row.get("score"))
    return dict(by_symbol)


def extract_conviction(sources: LockedSources) -> dict[str, dict[str, Any]]:
    rows = query_move_prediction_duckdb(
        sources.prediction_db,
        """
        SELECT symbol, conviction_score, rank_overall, manager_action_signal,
               weeks_score, weeks_ras, months_ras, entry_readiness,
               breakout_conviction_tier, exclusion_reason, sleeve
        FROM conviction_rankings
        WHERE run_id = ?
        """,
        [sources.prediction_run_id],
    )
    return {str(r["symbol"]): r for r in rows}


def extract_regime(sources: LockedSources) -> dict[str, dict[str, Any]]:
    rows = query_move_prediction_duckdb(
        sources.prediction_db,
        """
        SELECT symbol, regime_fit_score, active_mgmt_tier
        FROM regime_context_scores
        WHERE run_id = ?
        """,
        [sources.prediction_run_id],
    )
    return {str(r["symbol"]): r for r in rows}


def extract_progression(
    sources: LockedSources,
    symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    wanted = [s for s in symbols if s]
    if not wanted:
        return {}
    run_ids = (sources.prediction_run_id, *sources.prediction_prior_run_ids[:5])
    placeholders_s = ",".join(["?"] * len(wanted))
    placeholders_r = ",".join(["?"] * len(run_ids))
    raw = query_move_prediction_duckdb(
        sources.prediction_db,
        f"""
        SELECT r.run_id, r.symbol, r.close, r.change AS day, r."Perf.1M" AS m1,
               CAST(m.created_at_utc AS VARCHAR) AS created_at_utc
        FROM raw_scan_rows r
        JOIN run_metadata m ON m.run_id = r.run_id
        WHERE r.symbol IN ({placeholders_s})
          AND r.run_id IN ({placeholders_r})
        """,
        [*wanted, *run_ids],
    )
    profiles = query_move_prediction_duckdb(
        sources.prediction_db,
        f"""
        SELECT run_id, symbol, profile_name, score
        FROM profile_horizon_scores
        WHERE horizon_name = 'weeks'
          AND profile_name IN ('breakout_long_v1', 'quality_continuation_v1', 'forward_edge_active_v2')
          AND symbol IN ({placeholders_s})
          AND run_id IN ({placeholders_r})
        """,
        [*wanted, *run_ids],
    )
    by_run: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in raw:
        by_run[str(row["symbol"])][str(row["run_id"])] = {
            "close": _f(row.get("close")),
            "day": _f(row.get("day")),
            "m1": _f(row.get("m1")),
            "created_at_utc": row.get("created_at_utc"),
            "bo": None,
            "cont": None,
            "fwd": None,
        }
    key_map = {
        "breakout_long_v1": "bo",
        "quality_continuation_v1": "cont",
        "forward_edge_active_v2": "fwd",
    }
    for row in profiles:
        slot = by_run.get(str(row["symbol"]), {}).get(str(row["run_id"]))
        if slot is None:
            continue
        dest = key_map.get(str(row["profile_name"]))
        if dest:
            slot[dest] = _f(row.get("score"))
    out: dict[str, dict[str, Any]] = {}
    ordered_runs = list(run_ids)
    for symbol, runs in by_run.items():
        path = []
        for run_id in reversed(ordered_runs):
            if run_id in runs:
                item = dict(runs[run_id])
                item["run_id"] = run_id
                path.append(item)
        latest = runs.get(sources.prediction_run_id) or {}
        prev = None
        for run_id in ordered_runs:
            if run_id != sources.prediction_run_id and run_id in runs:
                prev = runs[run_id]
                break
        delta = {}
        if latest and prev:
            for key in ("bo", "cont", "fwd", "close"):
                a = _f(latest.get(key))
                b = _f(prev.get(key))
                delta[key] = round(a - b, 3) if a is not None and b is not None else None
        out[symbol] = {"path": path, "delta_vs_prior_run": delta}
    return out


def _mean_or_zero(value: float | None, digits: int) -> float:
    return round(value, digits) if value is not None else 0.0


def industry_breadth(rows: list[dict[str, Any]], *, min_mcap: float, min_n: int = 6) -> list[dict[str, Any]]:
    """US-mcap filter, then generic group_stats. pct_up uses group size (missing day = not-up)."""
    eligible = [
        row
        for row in rows
        if (_f(row.get("mcap")) or 0.0) >= min_mcap and row.get("ind")
    ]
    grouped = group_stats(
        eligible,
        group_field="ind",
        metrics=["d5", "day", "m1", "rsi"],
        min_n=min_n,
        pct_positive_field="day",
        pct_positive_denom="n",
        digits=None,
    )
    out: list[dict[str, Any]] = []
    for rec in grouped:
        rsi = rec.get("rsi")
        out.append(
            {
                "ind": rec["ind"],
                "n": rec["n"],
                "pct_up": rec.get("pct_positive"),
                "day": _mean_or_zero(rec.get("day"), 2),
                "d5": _mean_or_zero(rec.get("d5"), 2),
                "m1": _mean_or_zero(rec.get("m1"), 2),
                "rsi": round(rsi, 1) if rsi is not None else None,
            }
        )
    return out


def universe_stats(rows: list[dict[str, Any]], *, min_mcap: float) -> dict[str, Any]:
    """US $2B listing filter, then generic numeric_summary. pct_up over non-missing day."""
    members = []
    for r in rows:
        if (_f(r.get("mcap")) or 0) < min_mcap:
            continue
        country = str(r.get("country") or "").lower()
        exchange = str(r.get("exchange") or "").upper()
        if country not in {"united states", "usa", "us"} and exchange not in {
            "NASDAQ",
            "NYSE",
            "AMEX",
            "ARCA",
            "BATS",
            "CBOE",
        }:
            continue
        members.append(r)
    summary = numeric_summary(members, ["day", "d5", "m1", "rsi"], digits=None)
    rsi = summary["rsi"]["mean"]
    return {
        "n": len(members),
        "day": _mean_or_zero(summary["day"]["mean"], 2),
        "pct_up": summary["day"]["pct_positive"],
        "d5": _mean_or_zero(summary["d5"]["mean"], 2),
        "m1": _mean_or_zero(summary["m1"]["mean"], 2),
        "rsi": round(rsi, 1) if rsi is not None else 0.0,
    }
