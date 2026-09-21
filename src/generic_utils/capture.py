"""Whole-book capture path: holdings membership + fundamentals + progression.

Deterministic dump only (no discretionary course labels):
- book_asof / book_names / exits from holdings run history
- fund_path / fund_span over all-fields history
- score_path / score_span over prediction profile progression
- jobs join + coverage_fill_rates + optional IC snapshot

This module is intended to support operator prompts and manual CLI usage with
existing on-disk data (default 90-day window).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import leftover_pct, pct_vs, range_position_pct
from generic_utils.ranking import to_float
from generic_utils.risk import cash_from_holdings_manifest, load_holdings_summaries, normalize_holdings_row
from generic_utils.run_export import load_json_recipe
from generic_utils.scan_sources import rows_from_csv
from generic_utils.series import fetch_named, field_history, list_dated_files, series_span

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "capture.json"
DEFAULT_HOLDINGS_ROOT = (
    _PROJECT / "logs" / "tradingview_analysis" / "holdings_scoring_analysis" / "runs"
)
DEFAULT_ALL_FIELDS_ROOT = (
    _PROJECT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
)
DEFAULT_PREDICTION_ROOT = (
    _PROJECT / "logs" / "tradingview_analysis" / "prediction_analysis" / "duckdb_runs"
)
DEFAULT_BACKTEST_ROOT = _PROJECT / "logs" / "tradingview_analysis" / "backtests" / "runs"

DEFAULT_ALL_FIELDS_GLOB = "**/tradingview_all_fields_*.duckdb"
DEFAULT_PREDICTION_GLOB = "**/move_prediction_*.duckdb"
DEFAULT_PROFILE_RECIPE = "pred.profile_weeks_pivot"
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_JOB_SPEC = {
    "rerate_left_min": 12.0,
    "rerate_pt_pct_min": -8.0,
    "rerate_close_lag_max": 8.0,
    "forming_left_min": 10.0,
    "forming_early_min": 0.45,
    "forming_bo_min": 0.4,
    "forming_rng_max": 80.0,
    "red_left_min": 8.0,
    "red_score_min": 0.45,
    "monitor_left_min": 10.0,
}
DEFAULT_REPLAY_HORIZONS = (5, 10, 21, 60)
CAPTURE_DUMP_TABLES = (
    "book_asof",
    "book_names",
    "exits",
    "fund_path",
    "fund_span",
    "score_path",
    "score_span",
    "jobs",
    "coverage_fill_rates",
    "ic_snapshot",
)

_HOLDINGS_RUN = re.compile(r"^holdings_scoring_(\d{8})_(\d{4})_utc_[0-9a-f]+$", re.I)


def _f(value: Any) -> float | None:
    return to_float(value)


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return round(number, digits)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text


def parse_day_label(value: str | None) -> date | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%d_%m_%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _iso_day(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _pick(row: Mapping[str, Any], candidates: Sequence[str]) -> Any:
    for field in candidates:
        if field not in row:
            continue
        value = row.get(field)
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return None


def _pick_text(row: Mapping[str, Any], candidates: Sequence[str]) -> str | None:
    return _text(_pick(row, candidates))


def _pick_num(row: Mapping[str, Any], candidates: Sequence[str]) -> float | None:
    return _f(_pick(row, candidates))


def _asof_key(rec: Mapping[str, Any]) -> str:
    return str(rec.get("as_of") or "")


def _latest_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _norm_symbol(row.get("symbol"))
        if not symbol:
            continue
        prev = out.get(symbol)
        if prev is None:
            out[symbol] = dict(row)
            continue
        key_prev = (str(prev.get("as_of") or ""), str(prev.get("run_id") or ""))
        key_new = (str(row.get("as_of") or ""), str(row.get("run_id") or ""))
        if key_new >= key_prev:
            out[symbol] = dict(row)
    return out


def _window_sources(
    *,
    root: str | Path,
    glob_pattern: str,
    lookback_days: int,
    start_label: str | None = None,
    end_label: str | None = None,
) -> tuple[list[dict[str, Any]], date, date]:
    sources = [dict(item) for item in list_dated_files(root, glob_pattern)]
    dated = [item for item in sources if item.get("as_of_date") is not None]
    if not dated:
        raise FileNotFoundError(f"No dated files found for {root} / {glob_pattern}")
    dated.sort(key=lambda item: (item["as_of_date"], item["mtime"], str(item["path"])))
    end_date = parse_day_label(end_label) or dated[-1]["as_of_date"]
    if end_date is None:
        raise ValueError("Could not resolve end date from all-fields sources")
    start_date = parse_day_label(start_label)
    if start_date is None:
        days = max(1, int(lookback_days))
        start_date = end_date - timedelta(days=days - 1)
    kept = [
        item
        for item in dated
        if item.get("as_of_date") and start_date <= item["as_of_date"] <= end_date
    ]
    if not kept:
        raise FileNotFoundError(
            f"No files in requested window {start_date.isoformat()}..{end_date.isoformat()}"
        )
    return kept, start_date, end_date


def _parse_holdings_run_date(name: str) -> date | None:
    match = _HOLDINGS_RUN.match(name)
    if not match:
        return None
    yyyymmdd = match.group(1)
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").date()
    except ValueError:
        return None


def _list_holdings_runs(
    root: str | Path,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in base.iterdir():
        if not path.is_dir():
            continue
        as_of_date = _parse_holdings_run_date(path.name)
        if as_of_date is None:
            continue
        if as_of_date < start_date or as_of_date > end_date:
            continue
        out.append(
            {
                "path": path,
                "as_of_date": as_of_date,
                "as_of": as_of_date.isoformat(),
                "run_id": path.name,
            }
        )
    out.sort(key=lambda item: (item["as_of_date"], item["run_id"]))
    return out


def _af_candidates() -> dict[str, tuple[str, ...]]:
    from operator_briefing.extract import AF_CANDIDATES

    wanted = (
        "company",
        "exchange",
        "country",
        "sector",
        "industry",
        "mcap",
        "close",
        "pt",
        "pe",
        "pe_fwd",
        "peg",
        "evebitda",
        "evrev",
        "opm",
        "rsi",
        "sma50",
        "atr",
        "atrp",
        "hi52",
        "lo52",
        "day",
        "d5",
        "w",
        "m1",
        "m3",
        "relvol",
    )
    out: dict[str, tuple[str, ...]] = {}
    for key in wanted:
        values = tuple(str(v) for v in (AF_CANDIDATES.get(key) or ()))
        if values:
            out[key] = values
    return out


def _resolve_root(path_like: str | Path | None, fallback: Path) -> Path:
    raw = Path(path_like) if path_like else Path(fallback)
    return raw if raw.is_absolute() else (_PROJECT / raw)


def _flatten_requested_columns(candidates: Mapping[str, Sequence[str]]) -> list[str]:
    out: list[str] = ["symbol"]
    seen = {"symbol"}
    for fields in candidates.values():
        for field in fields:
            if field in seen:
                continue
            seen.add(field)
            out.append(field)
    return out


def _book_from_runs(runs: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        run_dir = Path(run["path"])
        as_of = str(run["as_of"])
        holdings = load_holdings_summaries(run_dir)
        if not holdings:
            continue
        cash = cash_from_holdings_manifest(run_dir)
        for raw in holdings:
            rec = normalize_holdings_row(raw)
            symbol = _norm_symbol(
                rec.get("symbol")
                or rec.get("matched_symbol")
                or rec.get("config_symbol")
                or rec.get("ticker")
            )
            if not symbol:
                continue
            ticker = _text(rec.get("ticker") or rec.get("config_ticker")) or symbol.split(":")[-1]
            rows.append(
                {
                    "as_of": as_of,
                    "holdings_run_id": run["run_id"],
                    "source_run_id": _text(rec.get("source_run_id")),
                    "symbol": symbol,
                    "ticker": ticker,
                    "company": _text(rec.get("company") or rec.get("matched_company")),
                    "instrument_type": _text(rec.get("instrument_type") or rec.get("category") or "stock"),
                    "sleeve": _text(rec.get("sleeve")),
                    "cost_usd": _r(rec.get("cost_usd"), 2),
                    "value_usd": _r(rec.get("value_usd"), 2),
                    "shares": _r(rec.get("shares"), 6),
                    "average_price": _r(
                        rec.get("average_price_usd") or rec.get("average_price"), 4
                    ),
                    "pred_close": _r(rec.get("close"), 4),
                    "vs_cost": _r(rec.get("vs_cost"), 2),
                    "wt_cost": _r(rec.get("wt_cost"), 2),
                    "cash_usd": _r(cash, 2),
                }
            )
    rows.sort(key=lambda rec: (str(rec.get("as_of") or ""), str(rec.get("symbol") or "")))
    if not rows:
        return [], [], []

    latest_as_of = max(str(row["as_of"]) for row in rows)
    latest_symbols = {
        str(row.get("symbol"))
        for row in rows
        if str(row.get("as_of")) == latest_as_of and str(row.get("symbol") or "")
    }
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row["symbol"])].append(row)
    names: list[dict[str, Any]] = []
    for symbol, trail in by_symbol.items():
        trail.sort(key=_asof_key)
        first = trail[0]
        last = trail[-1]
        names.append(
            {
                "symbol": symbol,
                "ticker": last.get("ticker") or first.get("ticker"),
                "company": last.get("company") or first.get("company"),
                "instrument_type": last.get("instrument_type") or first.get("instrument_type"),
                "first_as_of": first.get("as_of"),
                "last_as_of": last.get("as_of"),
                "first_holdings_run_id": first.get("holdings_run_id"),
                "last_holdings_run_id": last.get("holdings_run_id"),
                "n_asofs": len({str(item.get("as_of")) for item in trail}),
                "n_runs": len({str(item.get("holdings_run_id")) for item in trail}),
                "still_held": symbol in latest_symbols,
                "first_cost_usd": _r(first.get("cost_usd"), 2),
                "last_cost_usd": _r(last.get("cost_usd"), 2),
                "last_value_usd": _r(last.get("value_usd"), 2),
                "last_vs_cost": _r(last.get("vs_cost"), 2),
            }
        )
    names.sort(key=lambda rec: (str(rec.get("last_as_of") or ""), str(rec.get("symbol") or "")))
    exits = [row for row in names if not row.get("still_held")]
    exits.sort(key=lambda rec: (str(rec.get("last_as_of") or ""), str(rec.get("symbol") or "")))
    return rows, names, exits


def _build_fund_path(
    *,
    symbols: Sequence[str],
    af_sources: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, Sequence[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    wanted = sorted({_norm_symbol(item) for item in symbols if _norm_symbol(item)})
    if not wanted:
        return [], [], []
    wanted_set = set(wanted)
    columns = _flatten_requested_columns(candidates)
    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for source in af_sources:
        path = Path(source["path"])
        as_of = str(source.get("as_of") or "")
        fetched = fetch_named(
            path,
            table="all_fields_rows",
            columns=columns,
            run_id=None,
            id_field="symbol",
            ids=wanted,
        )
        spec = fetched.get("spec") or {}
        snapshots.append(
            {
                "path": path.as_posix(),
                "as_of": as_of,
                "n": spec.get("n"),
                "missing_columns": ",".join(spec.get("missing_columns") or []),
            }
        )
        for row in fetched.get("rows") or []:
            symbol = _norm_symbol(row.get("symbol"))
            if not symbol or symbol not in wanted_set:
                continue
            close = _pick_num(row, candidates.get("close", ()))
            pt = _pick_num(row, candidates.get("pt", ()))
            lo52 = _pick_num(row, candidates.get("lo52", ()))
            hi52 = _pick_num(row, candidates.get("hi52", ()))
            sma50 = _pick_num(row, candidates.get("sma50", ()))
            rec = {
                "as_of": as_of,
                "symbol": symbol,
                "company": _pick_text(row, candidates.get("company", ())),
                "exchange": _pick_text(row, candidates.get("exchange", ())),
                "country": _pick_text(row, candidates.get("country", ())),
                "sector": _pick_text(row, candidates.get("sector", ())),
                "ind": _pick_text(row, candidates.get("industry", ())),
                "mcap": _r(_pick_num(row, candidates.get("mcap", ())), 2),
                "close": _r(close, 4),
                "pt": _r(pt, 4),
                "leftover_street": _r(leftover_pct(close, pt, None), 4),
                "pe": _r(_pick_num(row, candidates.get("pe", ())), 4),
                "pe_fwd": _r(_pick_num(row, candidates.get("pe_fwd", ())), 4),
                "peg": _r(_pick_num(row, candidates.get("peg", ())), 4),
                "evebitda": _r(_pick_num(row, candidates.get("evebitda", ())), 4),
                "evrev": _r(_pick_num(row, candidates.get("evrev", ())), 4),
                "opm": _r(_pick_num(row, candidates.get("opm", ())), 4),
                "rsi": _r(_pick_num(row, candidates.get("rsi", ())), 4),
                "sma50": _r(sma50, 4),
                "vs50": _r(pct_vs(close, sma50), 4),
                "atr": _r(_pick_num(row, candidates.get("atr", ())), 4),
                "atrp": _r(_pick_num(row, candidates.get("atrp", ())), 4),
                "hi52": _r(hi52, 4),
                "lo52": _r(lo52, 4),
                "rng": _r(range_position_pct(close, lo52, hi52), 4),
                "day": _r(_pick_num(row, candidates.get("day", ())), 4),
                "d5": _r(_pick_num(row, candidates.get("d5", ())), 4),
                "w": _r(_pick_num(row, candidates.get("w", ())), 4),
                "m1": _r(_pick_num(row, candidates.get("m1", ())), 4),
                "m3": _r(_pick_num(row, candidates.get("m3", ())), 4),
                "relvol": _r(_pick_num(row, candidates.get("relvol", ())), 4),
            }
            rows.append(rec)
    rows.sort(key=lambda rec: (str(rec.get("as_of") or ""), str(rec.get("symbol") or "")))
    span = series_span(
        rows,
        id_field="symbol",
        fields=[
            "close",
            "pt",
            "leftover_street",
            "pe",
            "pe_fwd",
            "rsi",
            "atrp",
            "day",
            "d5",
            "w",
            "m1",
            "m3",
        ],
        as_of_field="as_of",
    )
    span.sort(key=lambda rec: str(rec.get("symbol") or ""))
    return rows, span, snapshots


def _build_score_path(
    *,
    symbols: Sequence[str],
    pred_sources: Sequence[Mapping[str, Any]],
    start_date: date,
    end_date: date,
    profile_recipe: str = DEFAULT_PROFILE_RECIPE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    wanted = sorted({_norm_symbol(item) for item in symbols if _norm_symbol(item)})
    if not wanted:
        return [], [], []
    raw_rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for source in pred_sources:
        try:
            chunk = field_history(
                [source],
                table="profile_horizon_scores",
                columns=("symbol",),
                id_field="symbol",
                ids=wanted,
                run_prefix="move_prediction_",
                all_runs=True,
                recipe=profile_recipe,
            )
        except Exception as exc:
            preview = str(exc).replace("\n", " ").strip()
            snapshots.append(
                {
                    "path": Path(source["path"]).as_posix(),
                    "as_of": source.get("as_of"),
                    "run_id": None,
                    "n": 0,
                    "note": f"skipped: {preview[:180]}",
                }
            )
            continue
        raw_rows.extend(chunk.get("rows") or [])
        snapshots.extend([dict(item) for item in (chunk.get("snapshots") or [])])
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        symbol = _norm_symbol(row.get("symbol"))
        as_of_date = _iso_day(row.get("as_of"))
        if not symbol or as_of_date is None:
            continue
        if as_of_date < start_date or as_of_date > end_date:
            continue
        rows.append(
            {
                "as_of": as_of_date.isoformat(),
                "run_id": _text(row.get("run_id")),
                "symbol": symbol,
                "bo": _r(row.get("bo"), 6),
                "cont": _r(row.get("cont"), 6),
                "fwd": _r(row.get("fwd"), 6),
                "early": _r(row.get("early"), 6),
                "sms": _r(row.get("sms"), 6),
                "recov": _r(row.get("recov"), 6),
                "rev": _r(row.get("rev"), 6),
            }
        )
    rows.sort(
        key=lambda rec: (
            str(rec.get("as_of") or ""),
            str(rec.get("symbol") or ""),
            str(rec.get("run_id") or ""),
        )
    )
    span = series_span(
        rows,
        id_field="symbol",
        fields=["bo", "cont", "fwd", "early", "sms", "recov", "rev"],
        as_of_field="as_of",
    )
    span.sort(key=lambda rec: str(rec.get("symbol") or ""))
    return rows, span, snapshots


def _fill_rate_rows(
    *,
    dataset: str,
    rows: Sequence[Mapping[str, Any]],
    symbols: Sequence[str],
    days: Sequence[str],
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    wanted_symbols = sorted({_norm_symbol(item) for item in symbols if _norm_symbol(item)})
    wanted_days = sorted({str(day) for day in days if str(day)})
    expected = len(wanted_symbols) * len(wanted_days)
    matrix: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in rows:
        symbol = _norm_symbol(rec.get("symbol"))
        as_of = str(rec.get("as_of") or "")
        if not symbol or not as_of:
            continue
        matrix[(symbol, as_of)] = dict(rec)
    out: list[dict[str, Any]] = []
    for field in fields:
        non_null = 0
        for symbol in wanted_symbols:
            for as_of in wanted_days:
                rec = matrix.get((symbol, as_of))
                if rec is None:
                    continue
                value = rec.get(field)
                if value is None:
                    continue
                if isinstance(value, str) and value.strip() == "":
                    continue
                non_null += 1
        out.append(
            {
                "dataset": dataset,
                "field": field,
                "n_symbols": len(wanted_symbols),
                "n_days": len(wanted_days),
                "expected": expected,
                "non_null": non_null,
                "fill_rate": _r((non_null / expected) if expected else None, 4),
            }
        )
    return out


def _pack_maps(pack: Mapping[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    names_map: dict[str, dict[str, Any]] = {}
    book_map: dict[str, dict[str, Any]] = {}
    if not isinstance(pack, Mapping):
        return names_map, book_map
    names = pack.get("names")
    if isinstance(names, Mapping):
        for key, value in names.items():
            if not isinstance(value, Mapping):
                continue
            symbol = _norm_symbol(value.get("symbol") or key)
            if symbol:
                names_map[symbol] = dict(value)
    elif isinstance(names, Sequence):
        for value in names:
            if not isinstance(value, Mapping):
                continue
            symbol = _norm_symbol(value.get("symbol"))
            if symbol:
                names_map[symbol] = dict(value)
    for value in pack.get("book") or []:
        if not isinstance(value, Mapping):
            continue
        symbol = _norm_symbol(value.get("symbol") or value.get("matched_symbol"))
        if symbol:
            book_map[symbol] = dict(value)
    return names_map, book_map


def job_spec_from_recipe(recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = dict(DEFAULT_JOB_SPEC)
    raw = (recipe or {}).get("jobs") if isinstance(recipe, Mapping) else None
    if isinstance(raw, Mapping):
        for key, default in DEFAULT_JOB_SPEC.items():
            if raw.get(key) is not None:
                number = _f(raw.get(key))
                spec[key] = number if number is not None else default
    return spec


def tag_jobs(
    *,
    left_now: Any,
    pt_pct: Any,
    close_pct: Any,
    early: Any,
    bo: Any,
    rng: Any,
    still_held: bool,
    vs_cost: Any,
    recov: Any,
    rev: Any,
    spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Boolean job tags from joined fields. No 0-100 score."""
    rules = dict(spec or DEFAULT_JOB_SPEC)
    left_f = _f(left_now)
    pt_f = _f(pt_pct)
    close_f = _f(close_pct)
    early_f = _f(early)
    bo_f = _f(bo)
    rng_f = _f(rng)
    vs_f = _f(vs_cost)
    recov_f = _f(recov)
    rev_f = _f(rev)
    lag_max = float(rules.get("rerate_close_lag_max") or 8)
    job_rerate = (
        left_f is not None
        and left_f >= float(rules.get("rerate_left_min") or 12)
        and pt_f is not None
        and pt_f >= float(rules.get("rerate_pt_pct_min") or -8)
        and close_f is not None
        and close_f <= (pt_f + lag_max)
    )
    forming_bo_min = float(rules.get("forming_bo_min") or 0.4)
    forming_rng_max = float(rules.get("forming_rng_max") or 80)
    job_forming = (
        left_f is not None
        and left_f >= float(rules.get("forming_left_min") or 10)
        and early_f is not None
        and early_f >= float(rules.get("forming_early_min") or 0.45)
        and (bo_f is None or bo_f >= forming_bo_min)
        and (rng_f is None or rng_f <= forming_rng_max)
    )
    red_min = float(rules.get("red_score_min") or 0.45)
    job_red_buy = (
        bool(still_held)
        and vs_f is not None
        and vs_f < 0
        and left_f is not None
        and left_f >= float(rules.get("red_left_min") or 8)
        and ((recov_f is not None and recov_f >= red_min) or (rev_f is not None and rev_f >= red_min))
    )
    reasons: list[str] = []
    if job_rerate:
        reasons.append("rerate-monitor")
    if job_forming:
        reasons.append("forming-momentum")
    if job_red_buy:
        reasons.append("buy-red-recovery")
    if not reasons and left_f is not None and left_f >= float(rules.get("monitor_left_min") or 10):
        reasons.append("monitor-upside")
    return {
        "job_rerate_monitor": bool(job_rerate),
        "job_forming_momentum": bool(job_forming),
        "job_buy_red_recovery": bool(job_red_buy),
        "job_count": int(bool(job_rerate)) + int(bool(job_forming)) + int(bool(job_red_buy)),
        "reasons": reasons,
        "job_reasons": ",".join(reasons),
    }


def _build_jobs(
    *,
    book_names: Sequence[Mapping[str, Any]],
    fund_path: Sequence[Mapping[str, Any]],
    fund_span: Sequence[Mapping[str, Any]],
    score_path: Sequence[Mapping[str, Any]],
    score_span: Sequence[Mapping[str, Any]],
    pack: Mapping[str, Any] | None = None,
    job_spec: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    job_spec = job_spec_from_recipe({"jobs": dict(job_spec or DEFAULT_JOB_SPEC)})
    fund_latest = _latest_by_symbol(fund_path)
    score_latest = _latest_by_symbol(score_path)
    fund_span_map = {_norm_symbol(row.get("symbol")): dict(row) for row in fund_span}
    score_span_map = {_norm_symbol(row.get("symbol")): dict(row) for row in score_span}
    pack_names, pack_book = _pack_maps(pack)
    out: list[dict[str, Any]] = []
    for base in book_names:
        symbol = _norm_symbol(base.get("symbol"))
        if not symbol:
            continue
        fund = dict(fund_latest.get(symbol) or {})
        score = dict(score_latest.get(symbol) or {})
        fspan = dict(fund_span_map.get(symbol) or {})
        sspan = dict(score_span_map.get(symbol) or {})
        pack_row = dict(pack_names.get(symbol) or pack_book.get(symbol) or {})

        left_now = _f(pack_row.get("left"))
        if left_now is None:
            left_now = _f(fund.get("leftover_street"))
        recov = _f(score.get("recov"))
        rev = _f(score.get("rev"))
        early = _f(score.get("early"))
        bo = _f(score.get("bo"))
        cont = _f(score.get("cont"))
        fwd = _f(score.get("fwd"))
        vs_cost = _f(base.get("last_vs_cost"))
        rng = _f(fund.get("rng"))
        pt_pct = _f(fspan.get("pt_pct"))
        close_pct = _f(fspan.get("close_pct"))

        tagged = tag_jobs(
            left_now=left_now,
            pt_pct=pt_pct,
            close_pct=close_pct,
            early=early,
            bo=bo,
            rng=rng,
            still_held=bool(base.get("still_held")),
            vs_cost=vs_cost,
            recov=recov,
            rev=rev,
            spec=job_spec,
        )
        job_rerate = tagged["job_rerate_monitor"]
        job_forming = tagged["job_forming_momentum"]
        job_red_buy = tagged["job_buy_red_recovery"]
        reasons = list(tagged["reasons"])

        joined = {
            "symbol": symbol,
            "ticker": base.get("ticker"),
            "company": base.get("company"),
            "still_held": bool(base.get("still_held")),
            "first_as_of": base.get("first_as_of"),
            "last_as_of": base.get("last_as_of"),
            "n_runs": base.get("n_runs"),
            "last_vs_cost": _r(vs_cost, 2),
            "left_now": _r(left_now, 4),
            "close_now": _r(fund.get("close"), 4),
            "pt_now": _r(fund.get("pt"), 4),
            "rsi_now": _r(fund.get("rsi"), 4),
            "rng_now": _r(rng, 4),
            "bo_now": _r(bo, 6),
            "cont_now": _r(cont, 6),
            "fwd_now": _r(fwd, 6),
            "early_now": _r(early, 6),
            "sms_now": _r(score.get("sms"), 6),
            "recov_now": _r(recov, 6),
            "rev_now": _r(rev, 6),
            "close_pct_lookback": _r(close_pct, 2),
            "pt_pct_lookback": _r(pt_pct, 2),
            "leftover_pct_lookback": _r(fspan.get("leftover_street_pct"), 2),
            "bo_pct_lookback": _r(sspan.get("bo_pct"), 2),
            "cont_pct_lookback": _r(sspan.get("cont_pct"), 2),
            "fwd_pct_lookback": _r(sspan.get("fwd_pct"), 2),
            "early_pct_lookback": _r(sspan.get("early_pct"), 2),
            "recov_pct_lookback": _r(sspan.get("recov_pct"), 2),
            "rev_pct_lookback": _r(sspan.get("rev_pct"), 2),
            "pack_left": _r(pack_row.get("left"), 4),
            "pack_setup_upside": _r(pack_row.get("setup_upside"), 4),
            "pack_setup_tag": _text(pack_row.get("setup_tag")),
            "pack_opp": _r(pack_row.get("opp"), 4),
            "pack_mix": _r(pack_row.get("mix"), 4),
            "pack_val_field": _text(pack_row.get("val_field")),
            "pack_course": _text(pack_row.get("course")),
            "pack_suggested_conviction": _r(pack_row.get("suggested_conviction"), 4),
            "job_rerate_monitor": bool(job_rerate),
            "job_forming_momentum": bool(job_forming),
            "job_buy_red_recovery": bool(job_red_buy),
            "job_count": int(bool(job_rerate)) + int(bool(job_forming)) + int(bool(job_red_buy)),
            "job_reasons": ",".join(reasons),
        }
        out.append(joined)
    out.sort(
        key=lambda row: (
            -(int(row.get("job_count") or 0)),
            -(1 if row.get("still_held") else 0),
            -(_f(row.get("left_now")) or -9e9),
            str(row.get("symbol") or ""),
        )
    )
    return out


def _latest_ic_snapshot(
    root: str | Path = DEFAULT_BACKTEST_ROOT,
    *,
    top: int = 18,
    cap_per_family: int = 3,
) -> tuple[list[dict[str, Any]], str | None]:
    base = Path(root)
    if not base.exists():
        return [], None
    files = [p for p in base.glob("backtests_*/family_metrics.csv") if p.is_file()]
    if not files:
        return [], None
    latest = max(files, key=lambda p: p.stat().st_mtime)
    rows = rows_from_csv(latest)
    scored: list[dict[str, Any]] = []
    for row in rows:
        scored.append(
            {
                "signal_name": _text(row.get("signal_name")),
                "family": _text(row.get("family")),
                "horizon_label": _text(row.get("horizon_label") or row.get("horizon")),
                "spearman_ic": _r(row.get("spearman_ic"), 6),
                "day_count": _r(row.get("day_count"), 0),
                "pair_count": _r(row.get("pair_count"), 0),
            }
        )
    scored.sort(key=lambda rec: -(_f(rec.get("spearman_ic")) or -9e9))
    out: list[dict[str, Any]] = []
    family_counts: dict[str, int] = defaultdict(int)
    for row in scored:
        family = str(row.get("family") or "")
        if cap_per_family > 0 and family:
            if family_counts[family] >= cap_per_family:
                continue
            family_counts[family] += 1
        out.append(row)
        if top and len(out) >= top:
            break
    return out, latest.as_posix()


def load_capture_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    if recipe_path.exists():
        payload = load_json_recipe(recipe_path)
    else:
        payload = {}
    payload.setdefault("name", "capture_v1")
    payload.setdefault("lookback_days", DEFAULT_LOOKBACK_DAYS)
    payload.setdefault("holdings_runs_root", DEFAULT_HOLDINGS_ROOT.as_posix())
    payload.setdefault("all_fields_root", DEFAULT_ALL_FIELDS_ROOT.as_posix())
    payload.setdefault("all_fields_glob", DEFAULT_ALL_FIELDS_GLOB)
    payload.setdefault("prediction_root", DEFAULT_PREDICTION_ROOT.as_posix())
    payload.setdefault("prediction_glob", DEFAULT_PREDICTION_GLOB)
    payload.setdefault("profile_recipe", DEFAULT_PROFILE_RECIPE)
    payload.setdefault("ic_root", DEFAULT_BACKTEST_ROOT.as_posix())
    payload.setdefault("jobs", dict(DEFAULT_JOB_SPEC))
    payload.setdefault(
        "replay",
        {
            "horizons": list(DEFAULT_REPLAY_HORIZONS),
            "tune_left_floors": [8, 10, 12, 20],
            "tune_early_floors": [0.3, 0.45, 0.6],
        },
    )
    return payload


def default_capture_out_dir(*, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M_utc")
    return (
        _PROJECT
        / "logs"
        / "tradingview_analysis"
        / "operator_briefing"
        / "runs"
        / f"capture_{stamp}"
    )


def build_capture_dump(
    *,
    recipe: Mapping[str, Any] | None = None,
    lookback_days: int | None = None,
    af_start: str | None = None,
    af_end: str | None = None,
    pack_path: str | Path | None = None,
    holdings_runs_root: str | Path | None = None,
    all_fields_root: str | Path | None = None,
    prediction_root: str | Path | None = None,
) -> dict[str, Any]:
    cfg = dict(recipe or load_capture_recipe())
    days = int(lookback_days or cfg.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)
    holdings_root = _resolve_root(
        holdings_runs_root or cfg.get("holdings_runs_root"), DEFAULT_HOLDINGS_ROOT
    )
    af_root = _resolve_root(
        all_fields_root or cfg.get("all_fields_root"), DEFAULT_ALL_FIELDS_ROOT
    )
    pred_root = _resolve_root(
        prediction_root or cfg.get("prediction_root"), DEFAULT_PREDICTION_ROOT
    )
    af_glob = str(cfg.get("all_fields_glob") or DEFAULT_ALL_FIELDS_GLOB)
    pred_glob = str(cfg.get("prediction_glob") or DEFAULT_PREDICTION_GLOB)
    profile_recipe = str(cfg.get("profile_recipe") or DEFAULT_PROFILE_RECIPE)

    af_sources, start_date, end_date = _window_sources(
        root=af_root,
        glob_pattern=af_glob,
        lookback_days=days,
        start_label=af_start,
        end_label=af_end,
    )
    pred_sources_all = [dict(item) for item in list_dated_files(pred_root, pred_glob)]
    pred_sources = [
        item
        for item in pred_sources_all
        if item.get("as_of_date") is None
        or (start_date - timedelta(days=8)) <= item["as_of_date"] <= (end_date + timedelta(days=8))
    ]
    holdings_runs = _list_holdings_runs(
        holdings_root,
        start_date=start_date,
        end_date=end_date,
    )

    book_asof, book_names, exits = _book_from_runs(holdings_runs)
    symbols = [str(row.get("symbol") or "") for row in book_names if row.get("symbol")]

    candidates = _af_candidates()
    fund_path, fund_span, fund_snapshots = _build_fund_path(
        symbols=symbols,
        af_sources=af_sources,
        candidates=candidates,
    )
    score_path, score_span, score_snapshots = _build_score_path(
        symbols=symbols,
        pred_sources=pred_sources,
        start_date=start_date,
        end_date=end_date,
        profile_recipe=profile_recipe,
    )

    pack = None
    if pack_path:
        pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))

    jobs = _build_jobs(
        book_names=book_names,
        fund_path=fund_path,
        fund_span=fund_span,
        score_path=score_path,
        score_span=score_span,
        pack=pack,
        job_spec=job_spec_from_recipe(cfg),
    )

    af_days = [str(item.get("as_of") or "") for item in af_sources if item.get("as_of")]
    score_days = sorted({str(row.get("as_of") or "") for row in score_path if row.get("as_of")})
    coverage = []
    coverage.extend(
        _fill_rate_rows(
            dataset="fund_path",
            rows=fund_path,
            symbols=symbols,
            days=af_days,
            fields=["close", "pt", "pe", "pe_fwd", "rsi", "atrp", "leftover_street"],
        )
    )
    coverage.extend(
        _fill_rate_rows(
            dataset="score_path",
            rows=score_path,
            symbols=symbols,
            days=score_days,
            fields=["bo", "cont", "fwd", "early", "sms", "recov", "rev"],
        )
    )

    ic_rows, ic_source = _latest_ic_snapshot(
        _resolve_root(cfg.get("ic_root"), DEFAULT_BACKTEST_ROOT)
    )
    stale_pack_profiles = False
    if isinstance(pack, Mapping):
        name_map, _ = _pack_maps(pack)
        if name_map:
            stale_pack_profiles = all(
                _f(row.get("recov")) is None and _f(row.get("rev")) is None
                for row in name_map.values()
            )

    notes = [
        "capture is deterministic: no new data fetch, no 0-100 composite.",
        "book_asof uses holdings membership/cost snapshots from holdings_scoring runs.",
        "fund_path uses all-fields named columns with leftover_street derived from close/pt.",
        f"score_path recipe={profile_recipe} (bo/cont/fwd/early/sms/recov/rev).",
    ]
    if stale_pack_profiles:
        notes.append(
            "pack names appear to miss recov/rev fields; jobs still use score_path latest values."
        )

    summary = {
        "n_symbols": len(symbols),
        "n_holdings_runs": len(holdings_runs),
        "n_book_asof": len(book_asof),
        "n_exits": len(exits),
        "n_fund_rows": len(fund_path),
        "n_score_rows": len(score_path),
        "n_jobs": len(jobs),
        "n_job_hits": sum(int((row.get("job_count") or 0) > 0) for row in jobs),
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
    }

    spec = {
        "recipe": cfg.get("name") or "capture_v1",
        "lookback_days": days,
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "pack": str(pack_path) if pack_path else None,
        "holdings_runs_root": holdings_root.as_posix(),
        "all_fields_root": af_root.as_posix(),
        "prediction_root": pred_root.as_posix(),
        "all_fields_glob": af_glob,
        "prediction_glob": pred_glob,
        "profile_recipe": profile_recipe,
        "af_days": len(af_days),
        "score_days": len(score_days),
        "stale_pack_profiles": stale_pack_profiles,
    }
    if ic_source:
        spec["ic_snapshot_source"] = ic_source
    return {
        "spec": spec,
        "summary": summary,
        "notes": notes,
        "book_asof": book_asof,
        "book_names": book_names,
        "exits": exits,
        "fund_path": fund_path,
        "fund_span": fund_span,
        "score_path": score_path,
        "score_span": score_span,
        "jobs": jobs,
        "coverage_fill_rates": coverage,
        "fund_snapshots": fund_snapshots,
        "score_snapshots": score_snapshots,
        "ic_snapshot": ic_rows,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def _stdev(values: Sequence[float]) -> float | None:
    clean = [float(v) for v in values]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    var = sum((item - mean) ** 2 for item in clean) / (len(clean) - 1)
    return var ** 0.5


def _group_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = _norm_symbol(row.get("symbol"))
        if not symbol:
            continue
        grouped[symbol].append(dict(row))
    for items in grouped.values():
        items.sort(key=lambda rec: str(rec.get("as_of") or ""))
    return grouped


def _row_on_or_before(rows: Sequence[Mapping[str, Any]], as_of: str) -> dict[str, Any] | None:
    picked: dict[str, Any] | None = None
    for rec in rows:
        stamp = str(rec.get("as_of") or "")
        if stamp <= as_of:
            picked = dict(rec)
        elif stamp > as_of:
            break
    return picked


def _forward_close(
    rows: Sequence[Mapping[str, Any]],
    as_of: str,
    horizon: int,
) -> tuple[str | None, float | None]:
    index = None
    for i, rec in enumerate(rows):
        stamp = str(rec.get("as_of") or "")
        if stamp == as_of:
            index = i
            break
        if stamp <= as_of:
            index = i
    if index is None:
        return None, None
    target = index + int(horizon)
    if target >= len(rows):
        return None, None
    dest = rows[target]
    return str(dest.get("as_of") or "") or None, _f(dest.get("close"))


def _held_maps(
    book_asof: Sequence[Mapping[str, Any]],
) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in book_asof:
        as_of = str(row.get("as_of") or "")
        symbol = _norm_symbol(row.get("symbol"))
        if as_of and symbol:
            by_date[as_of][symbol] = dict(row)
    dates = sorted(by_date)
    return dates, by_date


def _held_at(
    dates: Sequence[str],
    by_date: Mapping[str, Mapping[str, Mapping[str, Any]]],
    as_of: str,
) -> dict[str, dict[str, Any]]:
    prev = [stamp for stamp in dates if stamp <= as_of]
    if not prev:
        return {}
    return dict(by_date[prev[-1]])


def _prefix_span(
    rows: Sequence[Mapping[str, Any]],
    as_of: str,
    fields: Sequence[str],
) -> dict[str, Any]:
    prefix = [rec for rec in rows if str(rec.get("as_of") or "") <= as_of]
    if not prefix:
        return {}
    spanned = series_span(prefix, id_field="symbol", fields=list(fields), as_of_field="as_of")
    return dict(spanned[0]) if spanned else {}


def _sleeve_stats(returns: Sequence[float | None], *, digits: int = 4) -> dict[str, Any]:
    clean = [float(v) for v in returns if v is not None]
    n = len(clean)
    mean = (sum(clean) / n) if n else None
    hits = [v for v in clean if v > 0]
    stdev = _stdev(clean)
    ras = None
    if mean is not None and stdev not in (None, 0):
        ras = mean / stdev
    median = None
    if n:
        ordered = sorted(clean)
        mid = n // 2
        median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "n": n,
        "mean": _r(mean, digits),
        "median": _r(median, digits),
        "stdev": _r(stdev, digits),
        "ras": _r(ras, 4),
        "hit_rate": _r((100.0 * len(hits) / n) if n else None, 1),
        "min": _r(min(clean), digits) if clean else None,
        "max": _r(max(clean), digits) if clean else None,
    }


def latest_capture_dir(root: str | Path | None = None) -> Path | None:
    base = Path(root) if root else (
        _PROJECT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
    )
    if not base.exists():
        return None
    found = [p for p in base.glob("briefing_pack_*/capture") if (p / "fund_path.csv").exists()]
    found.extend([p for p in base.glob("capture_*") if (p / "fund_path.csv").exists()])
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def load_capture_dump(path: str | Path) -> dict[str, Any]:
    directory = Path(path)
    if directory.is_file():
        directory = directory.parent
    tables: dict[str, list[dict[str, Any]]] = {}
    for name in CAPTURE_DUMP_TABLES:
        csv_path = directory / f"{name}.csv"
        tables[name] = rows_from_csv(csv_path) if csv_path.exists() else []
    overview = directory / "overview.log"
    as_ofs = sorted({str(row.get("as_of") or "") for row in tables.get("fund_path") or [] if row.get("as_of")})
    return {
        "spec": {
            "capture_dir": directory.as_posix(),
            "window_start": as_ofs[0] if as_ofs else None,
            "window_end": as_ofs[-1] if as_ofs else None,
        },
        "summary": {},
        "notes": [f"loaded dump {directory.as_posix()}"],
        **tables,
        "overview_log": overview.as_posix() if overview.exists() else None,
    }


def build_capture_replay(
    dump: Mapping[str, Any],
    *,
    recipe: Mapping[str, Any] | None = None,
    horizons: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Walk-forward replay of capture job tags vs later closes already on disk.

    PIT flags use only fund/score/book rows with as_of <= decision date.
    Hindsight flags reuse the end-of-window jobs table (full-span knowledge).
    Forward returns are N steps along that symbol's fund_path (not calendar days).
    """
    cfg = dict(recipe or load_capture_recipe())
    job_spec = job_spec_from_recipe(cfg)
    replay_cfg = dict(cfg.get("replay") or {})
    horizon_list = [
        int(h)
        for h in (horizons or replay_cfg.get("horizons") or DEFAULT_REPLAY_HORIZONS)
        if int(h) > 0
    ]
    if 1 not in horizon_list:
        path_horizons = [1, *horizon_list]
    else:
        path_horizons = list(horizon_list)

    fund_path = [dict(r) for r in (dump.get("fund_path") or [])]
    score_path = [dict(r) for r in (dump.get("score_path") or [])]
    book_asof = [dict(r) for r in (dump.get("book_asof") or [])]
    jobs = [dict(r) for r in (dump.get("jobs") or [])]
    if not fund_path:
        raise ValueError("capture replay needs fund_path rows (run capture first)")

    fund_by = _group_by_symbol(fund_path)
    score_by = _group_by_symbol(score_path)
    hold_dates, hold_map = _held_maps(book_asof)
    hind_jobs = {_norm_symbol(row.get("symbol")): dict(row) for row in jobs}

    fund_fields = ("close", "pt", "leftover_street", "rsi")
    score_fields = ("bo", "cont", "fwd", "early", "sms", "recov", "rev")
    events: list[dict[str, Any]] = []
    daily_members: dict[tuple[str, str], list[float]] = defaultdict(list)

    for symbol, frows in fund_by.items():
        srows = score_by.get(symbol) or []
        hind = hind_jobs.get(symbol) or {}
        for rec in frows:
            as_of = str(rec.get("as_of") or "")
            close = _f(rec.get("close"))
            if not as_of or close is None:
                continue
            held_rows = _held_at(hold_dates, hold_map, as_of)
            held_row = held_rows.get(symbol) or {}
            still_held = bool(held_row)
            vs_cost = _f(held_row.get("vs_cost") or held_row.get("last_vs_cost"))
            score = _row_on_or_before(srows, as_of) or {}
            fspan = _prefix_span(frows, as_of, fund_fields)
            pit = tag_jobs(
                left_now=_f(rec.get("leftover_street")),
                pt_pct=_f(fspan.get("pt_pct")),
                close_pct=_f(fspan.get("close_pct")),
                early=_f(score.get("early")),
                bo=_f(score.get("bo")),
                rng=_f(rec.get("rng")),
                still_held=still_held,
                vs_cost=vs_cost,
                recov=_f(score.get("recov")),
                rev=_f(score.get("rev")),
                spec=job_spec,
            )
            event = {
                "as_of": as_of,
                "symbol": symbol,
                "ticker": rec.get("ticker") or (held_row.get("ticker") or hind.get("ticker")),
                "close": _r(close, 4),
                "leftover_street": _r(rec.get("leftover_street"), 4),
                "held_then": still_held,
                "vs_cost_then": _r(vs_cost, 2),
                "pit_rerate": pit["job_rerate_monitor"],
                "pit_forming": pit["job_forming_momentum"],
                "pit_red": pit["job_buy_red_recovery"],
                "pit_any": pit["job_count"] > 0,
                "pit_reasons": pit["job_reasons"],
                "hind_rerate": _truthy(hind.get("job_rerate_monitor")),
                "hind_forming": _truthy(hind.get("job_forming_momentum")),
                "hind_red": _truthy(hind.get("job_buy_red_recovery")),
                "hind_any": (
                    (_f(hind.get("job_count")) or 0) > 0
                    or _truthy(hind.get("job_rerate_monitor"))
                    or _truthy(hind.get("job_forming_momentum"))
                    or _truthy(hind.get("job_buy_red_recovery"))
                ),
                "early_then": _r(score.get("early"), 6),
                "bo_then": _r(score.get("bo"), 6),
                "recov_then": _r(score.get("recov"), 6),
                "rev_then": _r(score.get("rev"), 6),
                "pt_pct_pit": _r(fspan.get("pt_pct"), 2),
                "close_pct_pit": _r(fspan.get("close_pct"), 2),
            }
            for horizon in path_horizons:
                fwd_as_of, fwd_close = _forward_close(frows, as_of, horizon)
                ret = pct_vs(fwd_close, close) if fwd_close is not None else None
                event[f"ret_{horizon}"] = _r(ret, 4)
                event[f"fwd_as_of_{horizon}"] = fwd_as_of
            events.append(event)
            day_ret = event.get("ret_1")
            if day_ret is None:
                continue
            daily_members[(as_of, "all")].append(float(day_ret))
            if still_held:
                daily_members[(as_of, "held")].append(float(day_ret))
            if event["pit_any"]:
                daily_members[(as_of, "pit_any")].append(float(day_ret))
            if event["pit_rerate"]:
                daily_members[(as_of, "pit_rerate")].append(float(day_ret))
            if event["pit_forming"]:
                daily_members[(as_of, "pit_forming")].append(float(day_ret))
            if event["pit_red"]:
                daily_members[(as_of, "pit_red")].append(float(day_ret))
            if event["hind_any"]:
                daily_members[(as_of, "hind_any")].append(float(day_ret))

    sleeve_defs = (
        ("all", lambda e: True),
        ("held", lambda e: e.get("held_then")),
        ("pit_any", lambda e: e.get("pit_any")),
        ("pit_rerate", lambda e: e.get("pit_rerate")),
        ("pit_forming", lambda e: e.get("pit_forming")),
        ("pit_red", lambda e: e.get("pit_red")),
        ("pit_none", lambda e: e.get("held_then") and not e.get("pit_any")),
        ("hind_any", lambda e: e.get("hind_any")),
        ("hind_rerate", lambda e: e.get("hind_rerate")),
        ("hind_forming", lambda e: e.get("hind_forming")),
        ("hind_red", lambda e: e.get("hind_red")),
    )
    sleeves: list[dict[str, Any]] = []
    for sleeve, predicate in sleeve_defs:
        subset = [e for e in events if predicate(e)]
        for horizon in horizon_list:
            stats = _sleeve_stats([e.get(f"ret_{horizon}") for e in subset])
            sleeves.append(
                {
                    "sleeve": sleeve,
                    "horizon": horizon,
                    **stats,
                    "n_names": len({e.get("symbol") for e in subset}),
                }
            )

    left_floors = [float(x) for x in (replay_cfg.get("tune_left_floors") or (8, 10, 12, 20))]
    early_floors = [float(x) for x in (replay_cfg.get("tune_early_floors") or (0.3, 0.45, 0.6))]
    tune: list[dict[str, Any]] = []
    held_21 = _sleeve_stats(
        [e.get("ret_21") for e in events if e.get("held_then")],
        digits=4,
    )
    for floor in left_floors:
        picked = [
            e
            for e in events
            if (_f(e.get("leftover_street")) or -9e9) >= floor
            and (_f(e.get("pt_pct_pit")) is not None)
            and (_f(e.get("pt_pct_pit")) or -9e9) >= float(job_spec.get("rerate_pt_pct_min") or -8)
            and (_f(e.get("close_pct_pit")) is not None)
            and (_f(e.get("close_pct_pit")) or 9e9)
            <= ((_f(e.get("pt_pct_pit")) or 0) + float(job_spec.get("rerate_close_lag_max") or 8))
        ]
        stats = _sleeve_stats([e.get("ret_21") for e in picked])
        stats.update(
            {
                "sleeve": f"tune_rerate_left_{int(floor)}",
                "horizon": 21,
                "param": "leftover_street",
                "floor": floor,
                "lift_vs_held": _r(
                    (stats.get("mean") - held_21["mean"])
                    if stats.get("mean") is not None and held_21.get("mean") is not None
                    else None,
                    4,
                ),
            }
        )
        tune.append(stats)
    for floor in early_floors:
        picked = [
            e
            for e in events
            if (_f(e.get("leftover_street")) or -9e9) >= float(job_spec.get("forming_left_min") or 10)
            and (_f(e.get("early_then")) or -9e9) >= floor
        ]
        stats = _sleeve_stats([e.get("ret_21") for e in picked])
        stats.update(
            {
                "sleeve": f"tune_forming_early_{floor}",
                "horizon": 21,
                "param": "early",
                "floor": floor,
                "lift_vs_held": _r(
                    (stats.get("mean") - held_21["mean"])
                    if stats.get("mean") is not None and held_21.get("mean") is not None
                    else None,
                    4,
                ),
            }
        )
        tune.append(stats)

    first_hit: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in sorted(events, key=lambda rec: (str(rec.get("as_of") or ""), str(rec.get("symbol") or ""))):
        symbol = str(event.get("symbol") or "")
        if not event.get("pit_any") or symbol in seen:
            continue
        seen.add(symbol)
        first_hit.append(
            {
                "symbol": symbol,
                "ticker": event.get("ticker"),
                "first_as_of": event.get("as_of"),
                "pit_reasons": event.get("pit_reasons"),
                "held_then": event.get("held_then"),
                "ret_5": event.get("ret_5"),
                "ret_10": event.get("ret_10"),
                "ret_21": event.get("ret_21"),
                "ret_60": event.get("ret_60"),
                "leftover_street": event.get("leftover_street"),
            }
        )

    path_dates = sorted({stamp for stamp, _sleeve in daily_members})
    paths: list[dict[str, Any]] = []
    equity: dict[str, float] = defaultdict(lambda: 100.0)
    peak: dict[str, float] = defaultdict(lambda: 100.0)
    max_dd: dict[str, float] = defaultdict(float)
    path_sleeves = ("all", "held", "pit_any", "pit_rerate", "pit_forming", "pit_red", "hind_any")
    for as_of in path_dates:
        for sleeve in path_sleeves:
            rets = daily_members.get((as_of, sleeve)) or []
            day_ret = (sum(rets) / len(rets)) if rets else None
            if day_ret is not None:
                equity[sleeve] *= 1.0 + (day_ret / 100.0)
                peak[sleeve] = max(peak[sleeve], equity[sleeve])
                draw = (equity[sleeve] / peak[sleeve] - 1.0) * 100.0
                max_dd[sleeve] = min(max_dd[sleeve], draw)
            paths.append(
                {
                    "as_of": as_of,
                    "sleeve": sleeve,
                    "n": len(rets),
                    "day_ret": _r(day_ret, 4),
                    "equity": _r(equity[sleeve], 4),
                }
            )

    notes = [
        "replay scores capture job tags on later fund_path closes already on disk.",
        "PIT tags use leftover/span/scores with as_of <= decision date (no future leak into flags).",
        "Hindsight tags are the end-of-window jobs table (full-span knowledge, labeled).",
        "ret_N is N steps along that symbol's all-fields calendar, not N calendar days.",
        "ras = mean / sample stdev of those event returns. Not a 0-100 score.",
    ]
    dump_spec = dict(dump.get("spec") or {})
    summary = {
        "n_events": len(events),
        "n_symbols": len(fund_by),
        "n_first_hits": len(first_hit),
        "horizons": ",".join(str(h) for h in horizon_list),
        "window_start": dump_spec.get("window_start"),
        "window_end": dump_spec.get("window_end"),
        "capture_dir": dump_spec.get("capture_dir"),
    }
    for sleeve in ("pit_any", "held", "all", "hind_any"):
        row = next((r for r in sleeves if r.get("sleeve") == sleeve and r.get("horizon") == 21), None)
        if row:
            summary[f"{sleeve}_21_mean"] = row.get("mean")
            summary[f"{sleeve}_21_ras"] = row.get("ras")
            summary[f"{sleeve}_21_n"] = row.get("n")
    spec = {
        "recipe": cfg.get("name") or "capture_v1",
        "job_spec": job_spec,
        "horizons": horizon_list,
        "capture_dir": dump_spec.get("capture_dir"),
        "window_start": dump_spec.get("window_start"),
        "window_end": dump_spec.get("window_end"),
        "max_dd": {k: _r(v, 2) for k, v in max_dd.items()},
    }
    return {
        "spec": spec,
        "summary": summary,
        "notes": notes,
        "replay_events": events,
        "replay_sleeves": sleeves,
        "replay_tune": tune,
        "replay_first_hit": first_hit,
        "replay_paths": paths,
    }


def format_capture_replay_md(payload: Mapping[str, Any]) -> str:
    spec = payload.get("spec") or {}
    summary = payload.get("summary") or {}
    sleeves = payload.get("replay_sleeves") or []
    tune = payload.get("replay_tune") or []
    first_hit = payload.get("replay_first_hit") or []
    max_dd = spec.get("max_dd") or {}
    lines = [
        "# Capture replay (PIT vs hindsight vs later closes)",
        "",
        "Walk-forward on an existing capture dump. No new fetch. No 0-100 composite.",
        "",
        f"- recipe `{spec.get('recipe')}`",
        f"- dump `{spec.get('capture_dir') or ''}`",
        f"- window `{summary.get('window_start')}` to `{summary.get('window_end')}`",
        f"- events `{summary.get('n_events')}` · symbols `{summary.get('n_symbols')}` · horizons `{summary.get('horizons')}`",
        "",
        "## Sleeve forward returns",
        "",
        "| sleeve | h | n | names | mean | median | stdev | ras | hit% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sleeves:
        if row.get("horizon") not in {5, 10, 21, 60}:
            continue
        lines.append(
            f"| {row.get('sleeve')} | {row.get('horizon')} | {row.get('n')} | {row.get('n_names')} | {_md_cell(row.get('mean'), 4)} | {_md_cell(row.get('median'), 4)} | {_md_cell(row.get('stdev'), 4)} | {_md_cell(row.get('ras'), 4)} | {_md_cell(row.get('hit_rate'), 1)} |"
        )
    lines += [
        "",
        "## Equal-weight 1-step max drawdown (peak-to-trough %)",
        "",
    ]
    for sleeve, value in max_dd.items():
        lines.append(f"- `{sleeve}` {_md_cell(value, 2)}%")
    lines += [
        "",
        "## Tune grid (21-step mean vs held)",
        "",
        "| sleeve | n | mean | ras | hit% | lift vs held |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in tune:
        lines.append(
            f"| {row.get('sleeve')} | {row.get('n')} | {_md_cell(row.get('mean'), 4)} | {_md_cell(row.get('ras'), 4)} | {_md_cell(row.get('hit_rate'), 1)} | {_md_cell(row.get('lift_vs_held'), 4)} |"
        )
    lines += [
        "",
        "## First PIT job hit (entry proxy)",
        "",
        "| ticker | first_as_of | ret_21 | ret_60 | reasons |",
        "|---|---|---:|---:|---|",
    ]
    for row in first_hit[:40]:
        lines.append(
            f"| {row.get('ticker') or row.get('symbol')} | {row.get('first_as_of')} | {_md_cell(row.get('ret_21'), 4)} | {_md_cell(row.get('ret_60'), 4)} | {row.get('pit_reasons') or ''} |"
        )
    lines += [
        "",
        "Any matching window: `python src/run_operator_suites.py capture-replay --af-start DD_MM_YYYY --af-end DD_MM_YYYY`",
        "Reuse a dump: `python src/generic_utils/tv_scan_cli.py capture-replay --capture-dir DIR`",
        "",
    ]
    return "\n".join(lines) + "\n"


def _md_cell(value: Any, digits: int = 2) -> str:
    number = _f(value)
    if number is None:
        text = _text(value)
        return text or "—"
    if digits == 0:
        return f"{int(round(number)):,}"
    return f"{number:.{digits}f}"


def format_capture_md(payload: Mapping[str, Any]) -> str:
    spec = payload.get("spec") or {}
    summary = payload.get("summary") or {}
    exits = payload.get("exits") or []
    coverage = payload.get("coverage_fill_rates") or []
    jobs = payload.get("jobs") or []
    top_jobs = [row for row in jobs if (row.get("job_count") or 0) > 0][:20]
    lines = [
        "# Capture (whole book + fundamentals + progression)",
        "",
        "Deterministic dump from existing files. No new fetch, no discretionary score.",
        "",
        f"- recipe `{spec.get('recipe')}`",
        f"- pack `{spec.get('pack') or ''}`",
        f"- window `{summary.get('window_start')}` to `{summary.get('window_end')}`",
        f"- symbols `{summary.get('n_symbols')}` · exits `{summary.get('n_exits')}` · holdings runs `{summary.get('n_holdings_runs')}`",
        f"- fund rows `{summary.get('n_fund_rows')}` · score rows `{summary.get('n_score_rows')}` · jobs `{summary.get('n_jobs')}` ({summary.get('n_job_hits')} with >=1 deterministic flag)",
        "",
        "## Exit set",
        "",
        "| symbol | last_as_of | n_runs | last_vs_cost |",
        "|---|---:|---:|---:|",
    ]
    for row in exits[:40]:
        lines.append(
            f"| {row.get('symbol') or ''} | {row.get('last_as_of') or ''} | {row.get('n_runs') or 0} | {_md_cell(row.get('last_vs_cost'))} |"
        )
    lines += [
        "",
        "## Coverage fill rates",
        "",
        "| dataset | field | n_symbols | n_days | fill_rate |",
        "|---|---|---:|---:|---:|",
    ]
    for row in coverage:
        lines.append(
            f"| {row.get('dataset') or ''} | {row.get('field') or ''} | {_md_cell(row.get('n_symbols'), 0)} | {_md_cell(row.get('n_days'), 0)} | {_md_cell(row.get('fill_rate'), 4)} |"
        )
    lines += [
        "",
        "## Top deterministic job hits",
        "",
        "| symbol | held | left_now | bo | early | recov | rev | reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in top_jobs:
        lines.append(
            f"| {row.get('symbol') or ''} | {1 if row.get('still_held') else 0} | {_md_cell(row.get('left_now'))} | {_md_cell(row.get('bo_now'), 4)} | {_md_cell(row.get('early_now'), 4)} | {_md_cell(row.get('recov_now'), 4)} | {_md_cell(row.get('rev_now'), 4)} | {row.get('job_reasons') or ''} |"
        )
    lines += [
        "",
        "## Optional open backtest extension",
        "",
        "Not required for the 90-day smoke test:",
        "",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        "python src/generic_utils/tv_scan_cli.py capture --lookback-days 180 --af-start 01_04_2026 --pack logs/tradingview_analysis/operator_briefing/runs/briefing_pack_*/briefing_pack.json",
        "```",
    ]
    return "\n".join(lines) + "\n"
