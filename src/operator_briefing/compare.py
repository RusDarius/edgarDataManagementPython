"""Compare two move-prediction runs: profile deltas, mix flips, top-N churn.

Generic replacement for scripts/_tmp_compare_runs.py.
Joins on symbol from profile_horizon_scores / conviction_rankings.
consensus_rows.ticker is a fallback only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

from .discovery import lock_sources

AVOID_MIX_PREFIX = ("avoid_",)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 3) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return round(number, digits)


def _query(database_path: Path, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(database_path, sql, params)


def _profile_map(database_path: Path, run_id: str) -> dict[str, dict[str, Any]]:
    rows = _query(
        database_path,
        """
        SELECT symbol,
               max(CASE WHEN profile_name = 'breakout_long_v1' THEN score END) AS bo,
               max(CASE WHEN profile_name = 'quality_continuation_v1' THEN score END) AS cont,
               max(CASE WHEN profile_name = 'forward_edge_active_v2' THEN score END) AS fwd
        FROM profile_horizon_scores
        WHERE run_id = ?
          AND horizon_name = 'weeks'
        GROUP BY 1
        """,
        [run_id],
    )
    return {str(r["symbol"]): r for r in rows if r.get("symbol")}


def _conviction_map(database_path: Path, run_id: str) -> dict[str, dict[str, Any]]:
    try:
        rows = _query(
            database_path,
            """
            SELECT symbol, manager_action_signal, conviction_score, rank_overall, weeks_ras
            FROM conviction_rankings
            WHERE run_id = ?
            """,
            [run_id],
        )
    except Exception:  # noqa: BLE001
        return {}
    return {str(r["symbol"]): r for r in rows if r.get("symbol")}


def _consensus_ticker_map(database_path: Path, run_id: str) -> dict[str, dict[str, Any]]:
    try:
        rows = _query(
            database_path,
            """
            SELECT ticker, manager_action_signal, consensus_weeks_score, consensus_weeks_risk_adjusted_score
            FROM consensus_rows
            WHERE run_id = ?
            """,
            [run_id],
        )
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            out[ticker] = row
    return out


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, 3)


def build_compare_rows(
    profiles_a: dict[str, dict[str, Any]],
    profiles_b: dict[str, dict[str, Any]],
    conv_a: dict[str, dict[str, Any]],
    conv_b: dict[str, dict[str, Any]],
    consensus_a: dict[str, dict[str, Any]] | None = None,
    consensus_b: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    symbols = set(profiles_a) | set(profiles_b) | set(conv_a) | set(conv_b)
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        pa = profiles_a.get(symbol) or {}
        pb = profiles_b.get(symbol) or {}
        ca = conv_a.get(symbol) or {}
        cb = conv_b.get(symbol) or {}
        ticker = symbol.split(":")[-1].upper()
        cons_a = (consensus_a or {}).get(ticker) or {}
        cons_b = (consensus_b or {}).get(ticker) or {}
        bo_a = _f(pa.get("bo"))
        bo_b = _f(pb.get("bo"))
        mix_a = ca.get("manager_action_signal") or cons_a.get("manager_action_signal")
        mix_b = cb.get("manager_action_signal") or cons_b.get("manager_action_signal")
        rows.append(
            {
                "symbol": symbol,
                "ticker": ticker,
                "bo_a": _round(bo_a),
                "bo_b": _round(bo_b),
                "d_bo": _delta(bo_a, bo_b),
                "cont_a": _round(pa.get("cont")),
                "cont_b": _round(pb.get("cont")),
                "d_cont": _delta(_f(pa.get("cont")), _f(pb.get("cont"))),
                "fwd_a": _round(pa.get("fwd")),
                "fwd_b": _round(pb.get("fwd")),
                "d_fwd": _delta(_f(pa.get("fwd")), _f(pb.get("fwd"))),
                "mix_a": mix_a,
                "mix_b": mix_b,
                "rank_a": ca.get("rank_overall"),
                "rank_b": cb.get("rank_overall"),
                "ras_a": _round(ca.get("weeks_ras") or cons_a.get("consensus_weeks_risk_adjusted_score")),
                "ras_b": _round(cb.get("weeks_ras") or cons_b.get("consensus_weeks_risk_adjusted_score")),
            }
        )
    return rows


def _compact(row: dict[str, Any], extra: tuple[str, ...] = ()) -> dict[str, Any]:
    keys = (
        "symbol",
        "ticker",
        "bo_a",
        "bo_b",
        "d_bo",
        "cont_a",
        "cont_b",
        "d_cont",
        "mix_a",
        "mix_b",
        "rank_a",
        "rank_b",
        *extra,
    )
    return {k: row.get(k) for k in keys if row.get(k) is not None}


def summarize_run_compare(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 40,
    list_n: int = 20,
) -> dict[str, Any]:
    scored_a = [r for r in rows if r.get("bo_a") is not None]
    scored_b = [r for r in rows if r.get("bo_b") is not None]
    top_a = {r["symbol"] for r in sorted(scored_a, key=lambda r: r["bo_a"], reverse=True)[:top_n]}
    top_b = {r["symbol"] for r in sorted(scored_b, key=lambda r: r["bo_b"], reverse=True)[:top_n]}
    by_symbol = {r["symbol"]: r for r in rows}
    with_delta = [r for r in rows if r.get("d_bo") is not None]
    gainers = sorted(with_delta, key=lambda r: r["d_bo"], reverse=True)
    losers = sorted(with_delta, key=lambda r: r["d_bo"])
    mix_flips = [
        r
        for r in rows
        if r.get("mix_a") and r.get("mix_b") and r["mix_a"] != r["mix_b"]
    ]
    mix_flips.sort(key=lambda r: abs(r.get("d_bo") or 0), reverse=True)
    rising = []
    for row in gainers:
        mix_b = str(row.get("mix_b") or "")
        if (row.get("d_bo") or 0) < 0.15:
            break
        if mix_b.startswith(AVOID_MIX_PREFIX):
            continue
        rising.append(row)

    def _top_payload(symbols: set[str]) -> list[dict[str, Any]]:
        out = []
        for symbol in sorted(symbols):
            row = by_symbol.get(symbol) or {}
            out.append(
                {
                    "symbol": symbol,
                    "bo_b": row.get("bo_b"),
                    "mix_b": row.get("mix_b"),
                    "d_bo": row.get("d_bo"),
                }
            )
        out.sort(key=lambda r: r.get("bo_b") or -999, reverse=True)
        return out

    return {
        "n": len(rows),
        "top_n": top_n,
        "gainers": [_compact(r, ("fwd_a", "fwd_b", "d_fwd", "ras_a", "ras_b")) for r in gainers[:list_n]],
        "losers": [_compact(r, ("fwd_a", "fwd_b", "d_fwd", "ras_a", "ras_b")) for r in losers[:list_n]],
        "new_in_top": _top_payload(top_b - top_a),
        "dropped_from_top": _top_payload(top_a - top_b),
        "stable_top": sorted(top_a & top_b),
        "mix_flips": [_compact(r) for r in mix_flips[:list_n]],
        "rising_actionable": [_compact(r) for r in rising[:list_n]],
        "read": (
            "Gainers/losers are weeks breakout_long_v1 score deltas, not leftover. "
            "rising_actionable = d_bo>=0.15 and mix is not avoid_*. "
            "Confirm leftover/unpaid with lookup or the briefing pack before NEW/ADD."
        ),
    }


def compare_prediction_runs(
    *,
    run_a: str | None = None,
    run_b: str | None = None,
    database_path: Path | str | None = None,
    top_n: int = 40,
    list_n: int = 20,
) -> dict[str, Any]:
    sources = lock_sources()
    db = Path(database_path) if database_path else sources.prediction_db
    current = run_b or sources.prediction_run_id
    prior = run_a or (sources.prediction_prior_run_ids[0] if sources.prediction_prior_run_ids else None)
    if not prior or not current:
        raise ValueError("Need two prediction run_ids (pass --run-a and --run-b).")
    profiles_a = _profile_map(db, prior)
    profiles_b = _profile_map(db, current)
    conv_a = _conviction_map(db, prior)
    conv_b = _conviction_map(db, current)
    notes = []
    if not conv_a and not conv_b:
        notes.append("conviction_rankings missing — mix taken from consensus_rows.ticker if present")
    consensus_a = _consensus_ticker_map(db, prior)
    consensus_b = _consensus_ticker_map(db, current)
    rows = build_compare_rows(profiles_a, profiles_b, conv_a, conv_b, consensus_a, consensus_b)
    summary = summarize_run_compare(rows, top_n=top_n, list_n=list_n)
    return {
        "run_a": prior,
        "run_b": current,
        "database": db.as_posix(),
        "notes": notes,
        **summary,
    }
