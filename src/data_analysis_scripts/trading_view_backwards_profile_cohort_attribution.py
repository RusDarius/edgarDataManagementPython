from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
    DEFAULT_HORIZON_NAME,
    _ensure_datetime_utc,
    _normalize_text,
    _split_symbol_ticker,
    resolve_backwards_profile_targets,
)
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    resolve_backwards_analysis_database,
)
from db.trading_view_backwards_prediction_duckdb import query_backwards_prediction_duckdb

AttributionMethod = Literal[
    "period_boundary",
    "inclusion_entry",
    "hold_until_rank_exit",
]
RankScope = Literal["global", "min1bil"]

DEFAULT_TOP_N = 250
DEFAULT_MIN_MARKET_CAP_USD = 1_000_000_000.0


@dataclass(frozen=True)
class AnchorObservation:
    anchor_name: str
    is_current: bool
    point_time: datetime
    profile_name: str
    symbol: str
    bare_ticker: str
    company: str | None
    close: float | None
    score: float | None
    profile_rank: int | None
    market_cap_basic: float | None


@dataclass(frozen=True)
class SymbolWindowAttribution:
    profile_name: str
    bare_ticker: str
    symbol: str
    company: str | None
    best_rank: int | None
    first_anchor_name: str
    last_anchor_name: str
    entry_anchor_name: str
    first_close: float
    last_close: float
    entry_close: float
    period_return_pct: float
    inclusion_return_pct: float
    in_top_n: bool
    exit_anchor_name: str | None = None
    exit_close: float | None = None
    hold_until_rank_exit_return_pct: float | None = None
    exited_by_rank: bool = False


@dataclass(frozen=True)
class ProfileCohortSummary:
    profile_name: str
    method: AttributionMethod
    rank_scope: RankScope
    top_n: int
    cohort_size: int
    avg_return_pct: float | None
    median_return_pct: float | None
    win_rate: float | None


def _anchor_sort_key(obs: AnchorObservation) -> tuple[bool, datetime, str]:
    return (obs.is_current, obs.point_time, obs.anchor_name)


def _load_anchor_observations(
    database_path: str | Path,
    *,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_names: Sequence[str] | None = None,
    include_current: bool = True,
) -> list[AnchorObservation]:
    resolved_db = Path(database_path)
    context_rows = query_backwards_prediction_duckdb(
        resolved_db,
        "SELECT backwards_analysis_id FROM backwards_analysis_runs LIMIT 1",
    )
    if not context_rows:
        raise ValueError(f"No backwards_analysis_runs row found in {resolved_db}")

    analysis_id = str(context_rows[0]["backwards_analysis_id"])
    where_clauses = [
        "s.backwards_analysis_id = ?",
        "s.horizon_name = ?",
        "s.close IS NOT NULL",
    ]
    params: list[Any] = [analysis_id, horizon_name]
    if profile_names:
        placeholders = ", ".join("?" for _ in profile_names)
        where_clauses.append(f"LOWER(TRIM(s.profile_name)) IN ({placeholders})")
        params.extend(name.strip().lower() for name in profile_names)
    if not include_current:
        where_clauses.append("NOT s.is_current")

    rows = query_backwards_prediction_duckdb(
        resolved_db,
        f"""
        SELECT s.anchor_name,
            s.is_current,
            s.profile_name,
            s.symbol,
            s.company,
            s.close,
            s.score,
            s.profile_rank,
            s.market_cap_basic,
            COALESCE(a.anchor_created_at_utc, runs.current_created_at_utc) AS point_time
        FROM backwards_anchor_snapshots AS s
        INNER JOIN backwards_analysis_runs AS runs
            ON s.backwards_analysis_id = runs.backwards_analysis_id
        LEFT JOIN backwards_analysis_anchors AS a
            ON s.backwards_analysis_id = a.backwards_analysis_id
            AND s.anchor_name = a.anchor_name
        WHERE {" AND ".join(where_clauses)}
        ORDER BY s.profile_name, s.symbol, point_time, s.anchor_name
        """,
        parameters=params,
    )

    observations: list[AnchorObservation] = []
    for row in rows:
        symbol = _normalize_text(row.get("symbol"))
        profile_name = _normalize_text(row.get("profile_name"))
        if symbol is None or profile_name is None:
            continue
        point_time = _ensure_datetime_utc(row.get("point_time"))
        if point_time is None:
            continue
        close = row.get("close")
        observations.append(
            AnchorObservation(
                anchor_name=str(row.get("anchor_name") or ""),
                is_current=bool(row.get("is_current")),
                point_time=point_time,
                profile_name=profile_name,
                symbol=symbol,
                bare_ticker=_split_symbol_ticker(symbol),
                company=_normalize_text(row.get("company")),
                close=float(close) if close is not None else None,
                score=float(row["score"]) if row.get("score") is not None else None,
                profile_rank=int(row["profile_rank"])
                if row.get("profile_rank") is not None
                else None,
                market_cap_basic=float(row["market_cap_basic"])
                if row.get("market_cap_basic") is not None
                else None,
            )
        )
    return observations


def _pick_preferred_symbol(symbols: Sequence[str]) -> str:
    exchange_qualified = [symbol for symbol in symbols if ":" in symbol]
    if exchange_qualified:
        return sorted(exchange_qualified)[0]
    return sorted(symbols)[0]


def _merge_observations_by_bare_ticker(
    observations: Sequence[AnchorObservation],
) -> dict[tuple[str, str], list[AnchorObservation]]:
    """One merged timeline row per (profile_name, bare_ticker, anchor_key)."""
    buckets: dict[tuple[str, str, bool | str], list[AnchorObservation]] = {}
    for obs in observations:
        anchor_key: bool | str = "current" if obs.is_current else obs.anchor_name
        key = (obs.profile_name.lower(), obs.bare_ticker.upper(), anchor_key)
        buckets.setdefault(key, []).append(obs)

    merged: dict[tuple[str, str], list[AnchorObservation]] = {}
    for (profile_name, bare_ticker, _anchor_key), group in buckets.items():
        preferred_symbol = _pick_preferred_symbol([row.symbol for row in group])
        representative = next(row for row in group if row.symbol == preferred_symbol)
        close_candidates = [row.close for row in group if row.close is not None]
        if not close_candidates:
            continue
        preferred_rows = [row for row in group if row.symbol == preferred_symbol]
        if preferred_rows and preferred_rows[0].close is not None:
            merged_close = preferred_rows[0].close
        else:
            merged_close = close_candidates[0]
        merged_row = AnchorObservation(
            anchor_name=representative.anchor_name,
            is_current=representative.is_current,
            point_time=representative.point_time,
            profile_name=representative.profile_name,
            symbol=preferred_symbol,
            bare_ticker=bare_ticker,
            company=next(
                (_normalize_text(row.company) for row in group if _normalize_text(row.company)),
                None,
            ),
            close=merged_close,
            score=max((row.score for row in group if row.score is not None), default=None),
            profile_rank=min(
                (row.profile_rank for row in group if row.profile_rank is not None),
                default=None,
            ),
            market_cap_basic=max(
                (row.market_cap_basic for row in group if row.market_cap_basic is not None),
                default=None,
            ),
        )
        merged.setdefault((profile_name, bare_ticker), []).append(merged_row)

    for timeline in merged.values():
        timeline.sort(key=_anchor_sort_key)
    return merged


def _effective_rank(
    obs: AnchorObservation,
    *,
    rank_scope: RankScope,
    min1bil_ranks: dict[tuple[str, str, bool | str], int] | None,
) -> int | None:
    anchor_key: bool | str = "current" if obs.is_current else obs.anchor_name
    if rank_scope == "min1bil" and min1bil_ranks is not None:
        return min1bil_ranks.get(
            (obs.profile_name.lower(), obs.bare_ticker.upper(), anchor_key)
        )
    return obs.profile_rank


def _build_min1bil_ranks(
    observations: Sequence[AnchorObservation],
    *,
    min_market_cap_usd: float,
) -> dict[tuple[str, str, bool | str], int]:
    by_anchor: dict[tuple[str, str, bool | str], list[AnchorObservation]] = {}
    for obs in observations:
        if obs.market_cap_basic is None or obs.market_cap_basic < min_market_cap_usd:
            continue
        anchor_key: bool | str = "current" if obs.is_current else obs.anchor_name
        by_anchor.setdefault((obs.profile_name.lower(), anchor_key), []).append(obs)

    ranks: dict[tuple[str, str, bool | str], int] = {}
    for (profile_name, anchor_key), rows in by_anchor.items():
        bare_best: dict[str, AnchorObservation] = {}
        for row in rows:
            bare = row.bare_ticker.upper()
            existing = bare_best.get(bare)
            if existing is None or (row.score or float("-inf")) > (existing.score or float("-inf")):
                bare_best[bare] = row
        ordered = sorted(
            bare_best.values(),
            key=lambda row: (
                -(row.score if row.score is not None else float("-inf")),
                row.symbol,
            ),
        )
        for index, row in enumerate(ordered, start=1):
            ranks[(profile_name, row.bare_ticker.upper(), anchor_key)] = index
    return ranks


def _pct_return(exit_close: float, entry_close: float) -> float:
    if entry_close == 0:
        return 0.0
    return (exit_close - entry_close) / abs(entry_close) * 100.0


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def compute_symbol_window_attributions(
    database_path: str | Path,
    *,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_names: Sequence[str] | None = None,
    profile_suite_path: str | Path | None = None,
    top_n: int = DEFAULT_TOP_N,
    rank_scope: RankScope = "min1bil",
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    min_entry_close: float = 1.0,
    max_abs_return_pct: float = 500.0,
    include_current: bool = True,
    start_anchor_name: str | None = None,
    end_anchor_name: str | None = None,
    exit_rank_threshold: int | None = None,
) -> list[SymbolWindowAttribution]:
    resolved_db = Path(database_path)
    if profile_names is None and profile_suite_path is not None:
        targets, _ = resolve_backwards_profile_targets(
            resolved_db,
            horizon_name=horizon_name,
            profile_suite_path=profile_suite_path,
        )
        profile_names = [target.profile_name for target in targets]

    observations = _load_anchor_observations(
        resolved_db,
        horizon_name=horizon_name,
        profile_names=profile_names,
        include_current=include_current,
    )
    merged = _merge_observations_by_bare_ticker(observations)
    min1bil_ranks = (
        _build_min1bil_ranks(observations, min_market_cap_usd=min_market_cap_usd)
        if rank_scope == "min1bil"
        else None
    )

    company_by_key: dict[tuple[str, str], str | None] = {}
    for row in observations:
        if row.company:
            company_by_key[(row.profile_name.lower(), row.bare_ticker.upper())] = row.company

    results: list[SymbolWindowAttribution] = []
    for (profile_name, bare_ticker), timeline in sorted(merged.items()):
        if len(timeline) < 2:
            continue

        if start_anchor_name is not None:
            start_index = next(
                (index for index, row in enumerate(timeline) if row.anchor_name == start_anchor_name),
                None,
            )
            if start_index is None:
                continue
            timeline = timeline[start_index:]
        if end_anchor_name is not None:
            end_index = next(
                (
                    index
                    for index, row in enumerate(timeline)
                    if row.anchor_name == end_anchor_name
                ),
                None,
            )
            if end_index is None:
                continue
            timeline = timeline[: end_index + 1]
        if len(timeline) < 2:
            continue

        first_row = timeline[0]
        last_row = timeline[-1]
        if first_row.close is None or last_row.close is None:
            continue

        ranks = [
            rank
            for row in timeline
            if (rank := _effective_rank(row, rank_scope=rank_scope, min1bil_ranks=min1bil_ranks))
            is not None
        ]
        best_rank = min(ranks) if ranks else None
        in_top_n = best_rank is not None and best_rank <= top_n

        entry_row = None
        for row in timeline:
            rank = _effective_rank(row, rank_scope=rank_scope, min1bil_ranks=min1bil_ranks)
            if rank is not None and rank <= top_n and row.close is not None:
                entry_row = row
                break
        if entry_row is None:
            entry_row = first_row

        period_return_pct = _pct_return(last_row.close, first_row.close)
        inclusion_return_pct = _pct_return(last_row.close, entry_row.close)

        exit_anchor_name: str | None = None
        exit_close: float | None = None
        hold_until_rank_exit_return_pct: float | None = None
        exited_by_rank = False
        if exit_rank_threshold is not None and entry_row.close is not None:
            exit_row = last_row
            entry_index = next(
                (index for index, row in enumerate(timeline) if row is entry_row),
                0,
            )
            for row in timeline[entry_index + 1 :]:
                rank = _effective_rank(
                    row,
                    rank_scope=rank_scope,
                    min1bil_ranks=min1bil_ranks,
                )
                if rank is not None and rank > exit_rank_threshold and row.close is not None:
                    exit_row = row
                    exited_by_rank = True
                    break
            if exit_row.close is not None:
                exit_anchor_name = exit_row.anchor_name
                exit_close = exit_row.close
                hold_until_rank_exit_return_pct = _pct_return(
                    exit_row.close,
                    entry_row.close,
                )

        results.append(
            SymbolWindowAttribution(
                profile_name=first_row.profile_name,
                bare_ticker=bare_ticker,
                symbol=last_row.symbol,
                company=company_by_key.get((profile_name, bare_ticker)),
                best_rank=best_rank,
                first_anchor_name=first_row.anchor_name,
                last_anchor_name=last_row.anchor_name,
                entry_anchor_name=entry_row.anchor_name,
                first_close=first_row.close,
                last_close=last_row.close,
                entry_close=entry_row.close,
                period_return_pct=period_return_pct,
                inclusion_return_pct=inclusion_return_pct,
                in_top_n=in_top_n,
                exit_anchor_name=exit_anchor_name,
                exit_close=exit_close,
                hold_until_rank_exit_return_pct=hold_until_rank_exit_return_pct,
                exited_by_rank=exited_by_rank,
            )
        )
    return results


def summarize_profile_cohorts(
    symbol_rows: Sequence[SymbolWindowAttribution],
    *,
    top_n: int = DEFAULT_TOP_N,
    rank_scope: RankScope = "min1bil",
    methods: Sequence[AttributionMethod] | None = None,
    exit_rank_threshold: int | None = None,
    min_entry_close: float = 1.0,
    max_abs_return_pct: float = 500.0,
) -> list[ProfileCohortSummary]:
    resolved_methods: tuple[AttributionMethod, ...]
    if methods is None:
        resolved_methods = ("period_boundary", "inclusion_entry")
        if exit_rank_threshold is not None:
            resolved_methods = (*resolved_methods, "hold_until_rank_exit")
    else:
        resolved_methods = tuple(methods)

    summaries: list[ProfileCohortSummary] = []
    profiles = sorted({row.profile_name for row in symbol_rows})
    for profile_name in profiles:
        cohort = [row for row in symbol_rows if row.profile_name == profile_name and row.in_top_n]
        for method in resolved_methods:
            returns: list[float] = []
            for row in cohort:
                if method == "period_boundary":
                    entry_close = row.first_close
                    value = row.period_return_pct
                elif method == "hold_until_rank_exit":
                    entry_close = row.entry_close
                    value = row.hold_until_rank_exit_return_pct
                    if value is None:
                        continue
                else:
                    entry_close = row.entry_close
                    value = row.inclusion_return_pct
                if entry_close < min_entry_close:
                    continue
                if abs(value) > max_abs_return_pct:
                    continue
                returns.append(value)
            win_rate = (
                sum(1 for value in returns if value > 0) / len(returns) if returns else None
            )
            summaries.append(
                ProfileCohortSummary(
                    profile_name=profile_name,
                    method=method,
                    rank_scope=rank_scope,
                    top_n=top_n,
                    cohort_size=len(returns),
                    avg_return_pct=(sum(returns) / len(returns)) if returns else None,
                    median_return_pct=_median(returns),
                    win_rate=win_rate,
                )
            )
    return summaries


def run_backwards_profile_cohort_attribution(
    *,
    database_path: str | Path | None = None,
    run_folder_pattern: str | None = None,
    output_dir: str | Path | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_suite_path: str | Path | None = None,
    profile_names: Sequence[str] | None = None,
    top_n: int = DEFAULT_TOP_N,
    rank_scope: RankScope = "min1bil",
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    min_entry_close: float = 1.0,
    max_abs_return_pct: float = 500.0,
    include_current: bool = True,
    start_anchor_name: str | None = None,
    end_anchor_name: str | None = None,
    exit_rank_threshold: int | None = None,
) -> dict[str, Any]:
    """
    Rebuild memo-style top-N profile cohort returns from a backwards analysis DuckDB.

    Methods:
    - ``period_boundary``: cohort = ever top-N during window; return from first→last anchor close.
    - ``inclusion_entry``: same cohort; return from first top-N inclusion anchor→last anchor close.
    - ``hold_until_rank_exit``: enter at first top-N inclusion; exit at first anchor where
      rank exceeds ``exit_rank_threshold``, otherwise last anchor close.
    """
    started = time.perf_counter()
    resolved_db = resolve_backwards_analysis_database(
        database_path=database_path,
        run_folder_pattern=run_folder_pattern,
    )
    symbol_rows = compute_symbol_window_attributions(
        resolved_db,
        horizon_name=horizon_name,
        profile_names=profile_names,
        profile_suite_path=profile_suite_path,
        top_n=top_n,
        rank_scope=rank_scope,
        min_market_cap_usd=min_market_cap_usd,
        include_current=include_current,
        start_anchor_name=start_anchor_name,
        end_anchor_name=end_anchor_name,
        exit_rank_threshold=exit_rank_threshold,
    )
    summaries = summarize_profile_cohorts(
        symbol_rows,
        top_n=top_n,
        rank_scope=rank_scope,
        min_entry_close=min_entry_close,
        max_abs_return_pct=max_abs_return_pct,
        exit_rank_threshold=exit_rank_threshold,
    )

    run_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_db.parent / "profile_cohort_attribution"
    )
    run_output_dir.mkdir(parents=True, exist_ok=True)

    symbol_csv = run_output_dir / f"symbol_window_attribution__{horizon_name}.csv"
    summary_csv = run_output_dir / f"profile_cohort_summary__{horizon_name}.csv"
    summary_json = run_output_dir / f"profile_cohort_summary__{horizon_name}.json"

    _write_symbol_csv(symbol_csv, symbol_rows)
    _write_summary_csv(summary_csv, summaries)
    summary_json.write_text(
        json.dumps(
            {
                "database_path": resolved_db.as_posix(),
                "horizon_name": horizon_name,
                "top_n": top_n,
                "rank_scope": rank_scope,
                "min_market_cap_usd": min_market_cap_usd,
                "start_anchor_name": start_anchor_name,
                "end_anchor_name": end_anchor_name,
                "exit_rank_threshold": exit_rank_threshold,
                "symbol_count": len(symbol_rows),
                "summaries": [asdict(row) for row in summaries],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "database_path": resolved_db.as_posix(),
        "output_dir": run_output_dir.as_posix(),
        "symbol_csv": symbol_csv.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "summary_json": summary_json.as_posix(),
        "symbol_rows": symbol_rows,
        "summaries": summaries,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def _write_symbol_csv(path: Path, rows: Sequence[SymbolWindowAttribution]) -> None:
    import csv

    headers = [
        "profile_name",
        "bare_ticker",
        "symbol",
        "company",
        "best_rank",
        "in_top_n",
        "first_anchor_name",
        "last_anchor_name",
        "entry_anchor_name",
        "first_close",
        "last_close",
        "entry_close",
        "period_return_pct",
        "inclusion_return_pct",
        "exit_anchor_name",
        "exit_close",
        "hold_until_rank_exit_return_pct",
        "exited_by_rank",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_summary_csv(path: Path, rows: Sequence[ProfileCohortSummary]) -> None:
    import csv

    headers = [
        "profile_name",
        "method",
        "rank_scope",
        "top_n",
        "cohort_size",
        "avg_return_pct",
        "median_return_pct",
        "win_rate",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            payload = asdict(row)
            if payload["avg_return_pct"] is not None:
                payload["avg_return_pct"] = round(payload["avg_return_pct"], 2)
            if payload["median_return_pct"] is not None:
                payload["median_return_pct"] = round(payload["median_return_pct"], 2)
            if payload["win_rate"] is not None:
                payload["win_rate"] = round(payload["win_rate"], 4)
            writer.writerow(payload)


def format_profile_cohort_markdown_table(
    summaries: Sequence[ProfileCohortSummary],
    *,
    method: AttributionMethod = "period_boundary",
) -> str:
    rows = [row for row in summaries if row.method == method]
    if not rows:
        return "_No cohort summaries available._"

    lines = [
        f"Top {rows[0].top_n} cohort ({method}, rank_scope={rows[0].rank_scope}):",
        "",
        "| Profile | Cohort n | Avg return | Median return | Win rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda item: item.avg_return_pct or float("-inf"), reverse=True):
        avg = f"{row.avg_return_pct:.1f}%" if row.avg_return_pct is not None else "—"
        med = f"{row.median_return_pct:.1f}%" if row.median_return_pct is not None else "—"
        win = f"{row.win_rate * 100:.1f}%" if row.win_rate is not None else "—"
        lines.append(
            f"| {row.profile_name} | {row.cohort_size} | {avg} | {med} | {win} |"
        )
    return "\n".join(lines)


__all__ = [
    "AnchorObservation",
    "AttributionMethod",
    "ProfileCohortSummary",
    "RankScope",
    "SymbolWindowAttribution",
    "compute_symbol_window_attributions",
    "format_profile_cohort_markdown_table",
    "run_backwards_profile_cohort_attribution",
    "summarize_profile_cohorts",
]
