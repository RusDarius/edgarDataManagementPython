from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    profile_family,
)
from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
    DEFAULT_HORIZON_NAME,
    _ensure_datetime_utc,
    _normalize_text,
    _split_symbol_ticker,
)
from db.trading_view_backwards_prediction_duckdb import (
    query_backwards_prediction_duckdb,
)
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

RankScope = Literal["global", "min1bil"]
ExitReason = Literal[
    "rank_exit",
    "score_exit",
    "stale_age_exit",
    "window_end",
]

DEFAULT_MIN_MARKET_CAP_USD = 1_000_000_000.0
DEFAULT_FRESH_RANK_THRESHOLD = 250


@dataclass(frozen=True)
class ExecutionRule:
    rule_id: str
    entry_rank_threshold: int = 100
    exit_rank_threshold: int = 1000
    require_fresh_inclusion: bool = False
    fresh_rank_threshold: int = DEFAULT_FRESH_RANK_THRESHOLD
    min_entry_score: float | None = None
    min_entry_relative_volume: float | None = None
    excluded_entry_manager_actions: tuple[str, ...] = field(default_factory=tuple)
    exit_score_below: float | None = None
    exit_score_below_consecutive_anchors: int = 1
    max_holding_days_without_progress: int | None = None
    min_progress_return_pct: float | None = None


@dataclass(frozen=True)
class ExecutionObservation:
    anchor_name: str
    is_current: bool
    point_time: datetime
    run_id: str
    profile_name: str
    source_profile_name: str
    symbol: str
    bare_ticker: str
    company: str | None
    close: float | None
    score: float | None
    profile_rank: int | None
    market_cap_basic: float | None
    relative_volume_10d_calc: float | None = None
    manager_action_signal: str | None = None


@dataclass(frozen=True)
class ExecutionTrade:
    rule_id: str
    profile_name: str
    bare_ticker: str
    symbol: str
    company: str | None
    entry_anchor_name: str
    entry_time: str
    entry_close: float
    entry_rank: int | None
    entry_score: float | None
    entry_relative_volume_10d_calc: float | None
    entry_manager_action_signal: str | None
    entry_prior_anchor_name: str | None
    entry_prior_rank: int | None
    fresh_entry: bool
    exit_anchor_name: str
    exit_time: str
    exit_close: float
    exit_rank: int | None
    exit_score: float | None
    exit_reason: ExitReason
    holding_days: float
    return_pct: float


@dataclass(frozen=True)
class ExecutionBacktestSummary:
    rule_id: str
    rank_scope: RankScope
    trade_count: int
    avg_return_pct: float | None
    median_return_pct: float | None
    win_rate: float | None
    avg_holding_days: float | None
    exit_reason_counts: dict[str, int]


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


def _anchor_key(obs: ExecutionObservation) -> bool | str:
    return "current" if obs.is_current else obs.anchor_name


def _observation_sort_key(obs: ExecutionObservation) -> tuple[bool, datetime, str]:
    return (obs.is_current, obs.point_time, obs.anchor_name)


def _pick_preferred_symbol(symbols: Sequence[str]) -> str:
    exchange_qualified = [symbol for symbol in symbols if ":" in symbol]
    if exchange_qualified:
        return sorted(exchange_qualified)[0]
    return sorted(symbols)[0]


def _normalize_manager_action(value: str | None) -> str:
    return (value or "").strip().lower()


def _load_analysis_id(database_path: str | Path) -> str:
    rows = query_backwards_prediction_duckdb(
        database_path,
        "SELECT backwards_analysis_id FROM backwards_analysis_runs LIMIT 1",
    )
    if not rows:
        raise ValueError(f"No backwards_analysis_runs row found in {database_path}")
    return str(rows[0]["backwards_analysis_id"])


def _source_context_query() -> str:
    return """
        WITH scored AS (
            SELECT phs.profile_name,
                phs.symbol,
                phs.manager_action_signal,
                COALESCE(
                    NULLIF(split_part(CAST(phs.symbol AS VARCHAR), ':', 2), ''),
                    CAST(phs.symbol AS VARCHAR)
                ) AS bare_ticker
            FROM profile_horizon_scores AS phs
            WHERE phs.run_id = ?
              AND phs.horizon_name = ?
        ),
        raw_rows AS (
            SELECT COALESCE(
                    NULLIF(split_part(CAST(raw.symbol AS VARCHAR), ':', 2), ''),
                    CAST(raw.symbol AS VARCHAR)
                ) AS bare_ticker,
                TRY_CAST(raw.relative_volume_10d_calc AS DOUBLE) AS relative_volume_10d_calc
            FROM raw_scan_rows AS raw
            WHERE raw.run_id = ?
        )
        SELECT scored.profile_name,
            scored.symbol,
            scored.bare_ticker,
            scored.manager_action_signal,
            raw_rows.relative_volume_10d_calc
        FROM scored
        LEFT JOIN raw_rows
            ON scored.bare_ticker = raw_rows.bare_ticker
    """


def _load_anchor_sources(
    database_path: str | Path, analysis_id: str
) -> list[dict[str, str]]:
    current_rows = query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT 'current' AS anchor_name,
            current_run_id AS run_id,
            current_database_path AS source_database_path
        FROM backwards_analysis_runs
        WHERE backwards_analysis_id = ?
        """,
        parameters=[analysis_id],
    )
    anchor_rows = query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT anchor_name,
            anchor_run_id AS run_id,
            source_database_path
        FROM backwards_analysis_anchors
        WHERE backwards_analysis_id = ?
        """,
        parameters=[analysis_id],
    )
    return [
        {
            "anchor_name": str(row.get("anchor_name") or ""),
            "run_id": str(row.get("run_id") or ""),
            "source_database_path": str(row.get("source_database_path") or ""),
        }
        for row in [*current_rows, *anchor_rows]
        if row.get("run_id") and row.get("source_database_path")
    ]


def _load_source_context(
    database_path: str | Path,
    *,
    analysis_id: str,
    horizon_name: str,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    context: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source in _load_anchor_sources(database_path, analysis_id):
        source_path = Path(source["source_database_path"])
        if not source_path.exists():
            continue
        try:
            rows = query_move_prediction_duckdb(
                source_path,
                _source_context_query(),
                parameters=[source["run_id"], horizon_name, source["run_id"]],
            )
        except Exception:
            continue
        for row in rows:
            profile_name = _normalize_text(row.get("profile_name"))
            bare_ticker = _normalize_text(row.get("bare_ticker"))
            if profile_name is None or bare_ticker is None:
                continue
            key = (
                source["anchor_name"],
                profile_name.lower(),
                bare_ticker.upper(),
            )
            context[key] = {
                "relative_volume_10d_calc": row.get("relative_volume_10d_calc"),
                "manager_action_signal": _normalize_text(
                    row.get("manager_action_signal")
                ),
            }
    return context


def load_execution_observations(
    database_path: str | Path,
    *,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_names: Sequence[str] | None = None,
    include_current: bool = True,
    include_source_context: bool = True,
    max_profile_rank: int | None = None,
) -> list[ExecutionObservation]:
    resolved_db = Path(database_path)
    analysis_id = _load_analysis_id(resolved_db)
    source_context = (
        _load_source_context(
            resolved_db,
            analysis_id=analysis_id,
            horizon_name=horizon_name,
        )
        if include_source_context
        else {}
    )

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
    if max_profile_rank is not None:
        where_clauses.append("s.profile_rank IS NOT NULL AND s.profile_rank <= ?")
        params.append(max_profile_rank)

    rows = query_backwards_prediction_duckdb(
        resolved_db,
        f"""
        SELECT s.anchor_name,
            s.is_current,
            s.run_id,
            s.profile_name,
            s.anchor_profile_name,
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
        """,
        parameters=params,
    )

    observations: list[ExecutionObservation] = []
    for row in rows:
        symbol = _normalize_text(row.get("symbol"))
        profile_name = _normalize_text(row.get("profile_name"))
        if symbol is None or profile_name is None:
            continue
        point_time = _ensure_datetime_utc(row.get("point_time"))
        if point_time is None:
            continue
        anchor_name = str(row.get("anchor_name") or "")
        source_profile_name = (
            _normalize_text(row.get("anchor_profile_name")) or profile_name
        )
        bare_ticker = _split_symbol_ticker(symbol)
        context = source_context.get(
            (anchor_name, source_profile_name.lower(), bare_ticker.upper())
        ) or source_context.get(
            (anchor_name, profile_name.lower(), bare_ticker.upper())
        )
        close = row.get("close")
        observations.append(
            ExecutionObservation(
                anchor_name=anchor_name,
                is_current=bool(row.get("is_current")),
                point_time=point_time,
                run_id=str(row.get("run_id") or ""),
                profile_name=profile_name,
                source_profile_name=source_profile_name,
                symbol=symbol,
                bare_ticker=bare_ticker,
                company=_normalize_text(row.get("company")),
                close=float(close) if close is not None else None,
                score=float(row["score"]) if row.get("score") is not None else None,
                profile_rank=(
                    int(row["profile_rank"])
                    if row.get("profile_rank") is not None
                    else None
                ),
                market_cap_basic=(
                    float(row["market_cap_basic"])
                    if row.get("market_cap_basic") is not None
                    else None
                ),
                relative_volume_10d_calc=(
                    float(context["relative_volume_10d_calc"])
                    if context and context.get("relative_volume_10d_calc") is not None
                    else None
                ),
                manager_action_signal=(
                    str(context["manager_action_signal"])
                    if context and context.get("manager_action_signal") is not None
                    else None
                ),
            )
        )
    return observations


def _merge_observations_by_bare_ticker(
    observations: Sequence[ExecutionObservation],
) -> dict[tuple[str, str], list[ExecutionObservation]]:
    buckets: dict[tuple[str, str, bool | str], list[ExecutionObservation]] = {}
    for obs in observations:
        key = (obs.profile_name.lower(), obs.bare_ticker.upper(), _anchor_key(obs))
        buckets.setdefault(key, []).append(obs)

    merged: dict[tuple[str, str], list[ExecutionObservation]] = {}
    for (profile_name, bare_ticker, _), group in buckets.items():
        preferred_symbol = _pick_preferred_symbol([row.symbol for row in group])
        representative = next(row for row in group if row.symbol == preferred_symbol)
        close_candidates = [row.close for row in group if row.close is not None]
        if not close_candidates:
            continue
        preferred_rows = [row for row in group if row.symbol == preferred_symbol]
        preferred_close = preferred_rows[0].close if preferred_rows else None
        merged_row = ExecutionObservation(
            anchor_name=representative.anchor_name,
            is_current=representative.is_current,
            point_time=representative.point_time,
            run_id=representative.run_id,
            profile_name=representative.profile_name,
            source_profile_name=representative.source_profile_name,
            symbol=preferred_symbol,
            bare_ticker=bare_ticker,
            company=next(
                (_normalize_text(row.company) for row in group if row.company), None
            ),
            close=(
                preferred_close if preferred_close is not None else close_candidates[0]
            ),
            score=max(
                (row.score for row in group if row.score is not None), default=None
            ),
            profile_rank=min(
                (row.profile_rank for row in group if row.profile_rank is not None),
                default=None,
            ),
            market_cap_basic=max(
                (
                    row.market_cap_basic
                    for row in group
                    if row.market_cap_basic is not None
                ),
                default=None,
            ),
            relative_volume_10d_calc=next(
                (
                    row.relative_volume_10d_calc
                    for row in group
                    if row.relative_volume_10d_calc is not None
                ),
                None,
            ),
            manager_action_signal=next(
                (
                    row.manager_action_signal
                    for row in group
                    if row.manager_action_signal
                ),
                None,
            ),
        )
        merged.setdefault((profile_name, bare_ticker), []).append(merged_row)

    for timeline in merged.values():
        timeline.sort(key=_observation_sort_key)
    return merged


def _build_min1bil_ranks(
    observations: Sequence[ExecutionObservation],
    *,
    min_market_cap_usd: float,
) -> dict[tuple[str, str, bool | str], int]:
    by_anchor: dict[tuple[str, bool | str], list[ExecutionObservation]] = {}
    for obs in observations:
        if obs.market_cap_basic is None or obs.market_cap_basic < min_market_cap_usd:
            continue
        by_anchor.setdefault((obs.profile_name.lower(), _anchor_key(obs)), []).append(
            obs
        )

    ranks: dict[tuple[str, str, bool | str], int] = {}
    for (profile_name, anchor_key), rows in by_anchor.items():
        bare_best: dict[str, ExecutionObservation] = {}
        for row in rows:
            bare = row.bare_ticker.upper()
            existing = bare_best.get(bare)
            if existing is None or (row.score or float("-inf")) > (
                existing.score or float("-inf")
            ):
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


def _effective_rank(
    obs: ExecutionObservation,
    *,
    rank_scope: RankScope,
    min1bil_ranks: dict[tuple[str, str, bool | str], int] | None,
) -> int | None:
    if rank_scope == "min1bil" and min1bil_ranks is not None:
        return min1bil_ranks.get(
            (obs.profile_name.lower(), obs.bare_ticker.upper(), _anchor_key(obs))
        )
    return obs.profile_rank


def _entry_allowed(
    obs: ExecutionObservation,
    *,
    rank: int | None,
    prior_rank: int | None,
    rule: ExecutionRule,
) -> bool:
    if obs.close is None or rank is None or rank > rule.entry_rank_threshold:
        return False
    if rule.require_fresh_inclusion:
        if prior_rank is None or prior_rank <= rule.fresh_rank_threshold:
            return False
    if rule.min_entry_score is not None:
        if obs.score is None or obs.score < rule.min_entry_score:
            return False
    if rule.min_entry_relative_volume is not None:
        if (
            obs.relative_volume_10d_calc is None
            or obs.relative_volume_10d_calc < rule.min_entry_relative_volume
        ):
            return False
    excluded_actions = {
        _normalize_manager_action(value)
        for value in rule.excluded_entry_manager_actions
    }
    if (
        excluded_actions
        and _normalize_manager_action(obs.manager_action_signal) in excluded_actions
    ):
        return False
    return True


def _build_trade(
    *,
    rule: ExecutionRule,
    profile_name: str,
    bare_ticker: str,
    entry_obs: ExecutionObservation,
    entry_rank: int | None,
    entry_prior_obs: ExecutionObservation | None,
    entry_prior_rank: int | None,
    exit_obs: ExecutionObservation,
    exit_rank: int | None,
    exit_reason: ExitReason,
) -> ExecutionTrade:
    if entry_obs.close is None or exit_obs.close is None:
        raise ValueError("Cannot build execution trade without entry and exit closes.")
    holding_days = (
        exit_obs.point_time - entry_obs.point_time
    ).total_seconds() / 86400.0
    return ExecutionTrade(
        rule_id=rule.rule_id,
        profile_name=profile_name,
        bare_ticker=bare_ticker,
        symbol=exit_obs.symbol,
        company=exit_obs.company or entry_obs.company,
        entry_anchor_name=entry_obs.anchor_name,
        entry_time=entry_obs.point_time.isoformat(),
        entry_close=entry_obs.close,
        entry_rank=entry_rank,
        entry_score=entry_obs.score,
        entry_relative_volume_10d_calc=entry_obs.relative_volume_10d_calc,
        entry_manager_action_signal=entry_obs.manager_action_signal,
        entry_prior_anchor_name=(
            entry_prior_obs.anchor_name if entry_prior_obs else None
        ),
        entry_prior_rank=entry_prior_rank,
        fresh_entry=(
            entry_prior_rank is not None
            and entry_prior_rank > rule.fresh_rank_threshold
        ),
        exit_anchor_name=exit_obs.anchor_name,
        exit_time=exit_obs.point_time.isoformat(),
        exit_close=exit_obs.close,
        exit_rank=exit_rank,
        exit_score=exit_obs.score,
        exit_reason=exit_reason,
        holding_days=round(holding_days, 4),
        return_pct=round(_pct_return(exit_obs.close, entry_obs.close), 4),
    )


def simulate_execution_trades(
    observations: Sequence[ExecutionObservation],
    *,
    rule: ExecutionRule,
    rank_scope: RankScope = "min1bil",
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> list[ExecutionTrade]:
    merged = _merge_observations_by_bare_ticker(observations)
    min1bil_ranks = (
        _build_min1bil_ranks(observations, min_market_cap_usd=min_market_cap_usd)
        if rank_scope == "min1bil"
        else None
    )
    return _simulate_execution_trades_from_prepared(
        merged,
        min1bil_ranks=min1bil_ranks,
        rule=rule,
        rank_scope=rank_scope,
    )


def _simulate_execution_trades_from_prepared(
    merged: dict[tuple[str, str], list[ExecutionObservation]],
    *,
    min1bil_ranks: dict[tuple[str, str, bool | str], int] | None,
    rule: ExecutionRule,
    rank_scope: RankScope,
) -> list[ExecutionTrade]:

    trades: list[ExecutionTrade] = []
    for (profile_name, bare_ticker), timeline in sorted(merged.items()):
        if len(timeline) < 2:
            continue
        entry_obs: ExecutionObservation | None = None
        entry_rank: int | None = None
        entry_prior_obs: ExecutionObservation | None = None
        entry_prior_rank: int | None = None
        consecutive_score_exit_count = 0
        prior_obs: ExecutionObservation | None = None
        prior_rank: int | None = None

        for obs in timeline:
            rank = _effective_rank(
                obs, rank_scope=rank_scope, min1bil_ranks=min1bil_ranks
            )
            if entry_obs is None:
                if _entry_allowed(obs, rank=rank, prior_rank=prior_rank, rule=rule):
                    entry_obs = obs
                    entry_rank = rank
                    entry_prior_obs = prior_obs
                    entry_prior_rank = prior_rank
                    consecutive_score_exit_count = 0
                prior_obs = obs
                prior_rank = rank
                continue

            if obs.close is None:
                prior_obs = obs
                prior_rank = rank
                continue

            exit_reason: ExitReason | None = None
            if rank is not None and rank > rule.exit_rank_threshold:
                exit_reason = "rank_exit"
            elif rule.exit_score_below is not None:
                if obs.score is not None and obs.score < rule.exit_score_below:
                    consecutive_score_exit_count += 1
                else:
                    consecutive_score_exit_count = 0
                if consecutive_score_exit_count >= max(
                    1, rule.exit_score_below_consecutive_anchors
                ):
                    exit_reason = "score_exit"

            if (
                exit_reason is None
                and rule.max_holding_days_without_progress is not None
            ):
                if rule.min_progress_return_pct is None:
                    min_progress = 0.0
                else:
                    min_progress = rule.min_progress_return_pct
                holding_days = (
                    obs.point_time - entry_obs.point_time
                ).total_seconds() / 86400.0
                current_return = _pct_return(obs.close, entry_obs.close)
                if (
                    holding_days >= rule.max_holding_days_without_progress
                    and current_return < min_progress
                ):
                    exit_reason = "stale_age_exit"

            if exit_reason is not None:
                trades.append(
                    _build_trade(
                        rule=rule,
                        profile_name=profile_name,
                        bare_ticker=bare_ticker,
                        entry_obs=entry_obs,
                        entry_rank=entry_rank,
                        entry_prior_obs=entry_prior_obs,
                        entry_prior_rank=entry_prior_rank,
                        exit_obs=obs,
                        exit_rank=rank,
                        exit_reason=exit_reason,
                    )
                )
                entry_obs = None
                entry_rank = None
                entry_prior_obs = None
                entry_prior_rank = None
                consecutive_score_exit_count = 0

            prior_obs = obs
            prior_rank = rank

        if entry_obs is not None:
            exit_obs = next(
                (row for row in reversed(timeline) if row.close is not None), None
            )
            if exit_obs is not None and exit_obs is not entry_obs:
                exit_rank = _effective_rank(
                    exit_obs,
                    rank_scope=rank_scope,
                    min1bil_ranks=min1bil_ranks,
                )
                trades.append(
                    _build_trade(
                        rule=rule,
                        profile_name=profile_name,
                        bare_ticker=bare_ticker,
                        entry_obs=entry_obs,
                        entry_rank=entry_rank,
                        entry_prior_obs=entry_prior_obs,
                        entry_prior_rank=entry_prior_rank,
                        exit_obs=exit_obs,
                        exit_rank=exit_rank,
                        exit_reason="window_end",
                    )
                )
    return trades


def summarize_execution_trades(
    trades: Sequence[ExecutionTrade],
    *,
    rule_id: str,
    rank_scope: RankScope,
) -> ExecutionBacktestSummary:
    returns = [trade.return_pct for trade in trades]
    holding_days = [trade.holding_days for trade in trades]
    exit_counts: dict[str, int] = {}
    for trade in trades:
        exit_counts[trade.exit_reason] = exit_counts.get(trade.exit_reason, 0) + 1
    return ExecutionBacktestSummary(
        rule_id=rule_id,
        rank_scope=rank_scope,
        trade_count=len(trades),
        avg_return_pct=(sum(returns) / len(returns)) if returns else None,
        median_return_pct=_median(returns),
        win_rate=(
            (sum(1 for value in returns if value > 0) / len(returns))
            if returns
            else None
        ),
        avg_holding_days=(
            (sum(holding_days) / len(holding_days)) if holding_days else None
        ),
        exit_reason_counts=exit_counts,
    )


def _write_trades_csv(path: Path, trades: Sequence[ExecutionTrade]) -> None:
    headers = (
        list(asdict(trades[0]).keys())
        if trades
        else [field.name for field in ExecutionTrade.__dataclass_fields__.values()]
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def _write_summary_csv(
    path: Path, summaries: Sequence[ExecutionBacktestSummary]
) -> None:
    headers = [
        "rule_id",
        "rank_scope",
        "trade_count",
        "avg_return_pct",
        "median_return_pct",
        "win_rate",
        "avg_holding_days",
        "exit_reason_counts_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(
                {
                    "rule_id": summary.rule_id,
                    "rank_scope": summary.rank_scope,
                    "trade_count": summary.trade_count,
                    "avg_return_pct": (
                        round(summary.avg_return_pct, 4)
                        if summary.avg_return_pct is not None
                        else None
                    ),
                    "median_return_pct": (
                        round(summary.median_return_pct, 4)
                        if summary.median_return_pct is not None
                        else None
                    ),
                    "win_rate": (
                        round(summary.win_rate, 4)
                        if summary.win_rate is not None
                        else None
                    ),
                    "avg_holding_days": (
                        round(summary.avg_holding_days, 4)
                        if summary.avg_holding_days is not None
                        else None
                    ),
                    "exit_reason_counts_json": json.dumps(
                        summary.exit_reason_counts, sort_keys=True
                    ),
                }
            )


def run_backwards_execution_backtest(
    *,
    database_path: str | Path,
    output_dir: str | Path | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_names: Sequence[str] | None = None,
    rules: Sequence[ExecutionRule] | None = None,
    rank_scope: RankScope = "min1bil",
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    include_current: bool = True,
    include_source_context: bool = True,
    max_profile_rank: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_db = Path(database_path)
    resolved_rules = (
        list(rules) if rules is not None else [ExecutionRule(rule_id="top100_exit1000")]
    )
    observations = load_execution_observations(
        resolved_db,
        horizon_name=horizon_name,
        profile_names=profile_names,
        include_current=include_current,
        include_source_context=include_source_context,
        max_profile_rank=max_profile_rank,
    )
    merged_observations = _merge_observations_by_bare_ticker(observations)
    min1bil_ranks = (
        _build_min1bil_ranks(observations, min_market_cap_usd=min_market_cap_usd)
        if rank_scope == "min1bil"
        else None
    )
    run_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_db.parent / "execution_backtests"
    )
    run_output_dir.mkdir(parents=True, exist_ok=True)

    all_trades: list[ExecutionTrade] = []
    summaries: list[ExecutionBacktestSummary] = []
    trade_csv_paths: dict[str, str] = {}
    for rule in resolved_rules:
        trades = _simulate_execution_trades_from_prepared(
            merged_observations,
            min1bil_ranks=min1bil_ranks,
            rule=rule,
            rank_scope=rank_scope,
        )
        all_trades.extend(trades)
        summaries.append(
            summarize_execution_trades(
                trades, rule_id=rule.rule_id, rank_scope=rank_scope
            )
        )
        trade_csv = run_output_dir / f"positions__{rule.rule_id}.csv"
        _write_trades_csv(trade_csv, trades)
        trade_csv_paths[rule.rule_id] = trade_csv.as_posix()

    summary_csv = run_output_dir / "execution_backtest_summary.csv"
    _write_summary_csv(summary_csv, summaries)
    manifest_path = run_output_dir / "execution_backtest_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "database_path": resolved_db.as_posix(),
                "output_dir": run_output_dir.as_posix(),
                "horizon_name": horizon_name,
                "profile_names": (
                    list(profile_names) if profile_names is not None else None
                ),
                "rank_scope": rank_scope,
                "min_market_cap_usd": min_market_cap_usd,
                "include_current": include_current,
                "include_source_context": include_source_context,
                "max_profile_rank": max_profile_rank,
                "observation_count": len(observations),
                "trade_count": len(all_trades),
                "rules": [asdict(rule) for rule in resolved_rules],
                "summaries": [asdict(summary) for summary in summaries],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "database_path": resolved_db.as_posix(),
        "output_dir": run_output_dir.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "trade_csv_paths": trade_csv_paths,
        "trades": all_trades,
        "summaries": summaries,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


__all__ = [
    "ExecutionBacktestSummary",
    "ExecutionObservation",
    "ExecutionRule",
    "ExecutionTrade",
    "load_execution_observations",
    "run_backwards_execution_backtest",
    "simulate_execution_trades",
    "summarize_execution_trades",
]
