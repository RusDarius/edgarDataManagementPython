"""Conviction mode for v1 move-prediction DuckDB runs.

Reads scored consensus/breakout/earnings tables after a suite run, ranks the full
universe, and writes a capped daily-focus log plus full conviction_rankings export.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from data_analysis_scripts.trading_view_move_prediction_conviction_config import (
    resolve_conviction_mode_config,
)
from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
    query_move_prediction_duckdb,
)
from generic_utils.log_to_files_util import log_to_file

SLEEVE_ORDER = ("short", "mid", "long")
SLEEVE_TITLES = {
    "short": "SHORT (days/weeks)",
    "mid": "MID (1–6 months)",
    "long": "LONG (6+ months)",
}

_CANDIDATE_FETCH_SQL = """
WITH multi_lens AS (
    SELECT
        row_number,
        COUNT(*) FILTER (
            WHERE score >= ?
              AND profile_name NOT IN ('fragility_short', 'mean_reversion_exhaustion_v1')
        ) AS bullish_profile_count
    FROM consensus_profile_horizon_scores
    WHERE run_id = ?
      AND horizon_name = 'weeks'
    GROUP BY row_number
),
breakout AS (
    SELECT *
    FROM (
        SELECT
            row_number,
            conviction_tier,
            entry_readiness,
            size_tier,
            tape_pass,
            risk_false_breakout,
            risk_fragility_conflict,
            thesis_1,
            ROW_NUMBER() OVER (
                PARTITION BY row_number
                ORDER BY prediction_row_number
            ) AS breakout_row_number
        FROM breakout_investment_stories
        WHERE run_id = ?
    ) ranked_breakout
    WHERE breakout_row_number = 1
)
SELECT
    w.row_number AS consensus_row_number,
    w.symbol,
    w.company,
    w.sector,
    w.industry,
    w.manager_action_signal,
    w.agreement_ratio AS weeks_agreement_ratio,
    w.opinions AS weeks_opinions,
    d.score AS days_score,
    d.risk_adjusted_score AS days_ras,
    w.score AS weeks_score,
    w.risk_adjusted_score AS weeks_ras,
    m.score AS months_score,
    m.risk_adjusted_score AS months_ras,
    y.score AS years_score,
    y.risk_adjusted_score AS years_ras,
    COALESCE(ml.bullish_profile_count, 0) AS bullish_profile_count,
    b.conviction_tier AS breakout_conviction_tier,
    b.entry_readiness,
    b.size_tier,
    b.tape_pass,
    b.risk_false_breakout,
    b.risk_fragility_conflict,
    b.thesis_1
FROM consensus_horizon_scores w
LEFT JOIN consensus_horizon_scores d
    ON d.run_id = w.run_id
   AND d.row_number = w.row_number
   AND d.horizon_name = 'days'
LEFT JOIN consensus_horizon_scores m
    ON m.run_id = w.run_id
   AND m.row_number = w.row_number
   AND m.horizon_name = 'months'
LEFT JOIN consensus_horizon_scores y
    ON y.run_id = w.run_id
   AND y.row_number = w.row_number
   AND y.horizon_name = 'years'
LEFT JOIN multi_lens ml ON ml.row_number = w.row_number
LEFT JOIN breakout b ON b.row_number = w.row_number
WHERE w.run_id = ?
  AND w.horizon_name = 'weeks'
  AND w.score IS NOT NULL
"""

_EARNINGS_SQL = """
SELECT
    row_number,
    ticker AS symbol,
    days_until_earnings,
    consensus_days_score
FROM earnings_priority_consensus_rows
WHERE run_id = ?
"""


def _execute_query(
    database_path: str | Path,
    sql: str,
    parameters: list[Any] | None = None,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    if conn is not None:
        cursor = conn.execute(sql, list(parameters or []))
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    return query_move_prediction_duckdb(database_path, sql, parameters=parameters)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    return default


def _horizon_fields(row: dict[str, Any], horizon: str) -> tuple[float | None, float | None]:
    score = row.get(f"{horizon}_score")
    ras = row.get(f"{horizon}_ras")
    score_f = float(score) if score is not None else None
    ras_f = float(ras) if ras is not None else None
    return score_f, ras_f


def _sleeve_horizon_score(
    row: dict[str, Any],
    sleeve_name: str,
    sleeve_config: dict[str, Any],
    scoring_weights: dict[str, float],
    manager_action: str,
) -> float:
    horizons = sleeve_config.get("horizons") or []
    min_score = float(sleeve_config.get("min_consensus_score") or 0.0)
    min_ras = sleeve_config.get("min_risk_adjusted_score")
    preferred_actions = set(sleeve_config.get("preferred_manager_actions") or [])

    horizon_scores: list[float] = []
    for horizon in horizons:
        score, ras = _horizon_fields(row, horizon)
        if score is None:
            continue
        if score < min_score:
            continue
        if min_ras is not None and (ras is None or ras < float(min_ras)):
            continue
        component = (
            scoring_weights.get("consensus_score_weight", 0.2) * abs(score)
            + scoring_weights.get("consensus_ras_weight", 0.35)
            * abs(ras if ras is not None else score * 0.5)
        )
        horizon_scores.append(component)

    if not horizon_scores:
        return 0.0

    base = sum(horizon_scores) / len(horizon_scores)
    if manager_action in preferred_actions:
        base += scoring_weights.get("manager_action_bonus", 0.05)
    return base


def _breakout_boost(
    breakout_row: dict[str, Any] | None,
    breakout_config: dict[str, Any],
    weight: float,
) -> tuple[float, dict[str, Any]]:
    if not breakout_row:
        return 0.0, {}

    tier_map = breakout_config.get("conviction_tiers") or {}
    readiness_map = breakout_config.get("entry_readiness") or {}
    tier = str(breakout_row.get("conviction_tier") or "none")
    readiness = str(breakout_row.get("entry_readiness") or "none")
    tier_mult = float(tier_map.get(tier, 0.0))
    readiness_mult = float(readiness_map.get(readiness, 0.0))
    boost = weight * tier_mult * readiness_mult
    return boost, {
        "breakout_tier": tier,
        "entry_readiness": readiness,
        "breakout_boost": boost,
    }


def _earnings_boost(
    earnings_row: dict[str, Any] | None,
    earnings_config: dict[str, Any],
    weight: float,
) -> tuple[float, dict[str, Any]]:
    if not earnings_row:
        return 0.0, {}

    days_until = _coerce_float(earnings_row.get("days_until_earnings"), default=999.0)
    days_score = _coerce_float(earnings_row.get("consensus_days_score"))
    max_days = float(earnings_config.get("max_days_until", 30))
    min_score = float(earnings_config.get("min_days_score", 0.35))

    if days_until > max_days or abs(days_score) < min_score:
        return 0.0, {"earnings_days_until": days_until}

    recency = max(0.0, 1.0 - (days_until / max_days))
    boost = weight * abs(days_score) * recency
    return boost, {
        "earnings_days_until": days_until,
        "earnings_boost": boost,
    }


def _exclusion_reason(
    row: dict[str, Any],
    breakout_row: dict[str, Any] | None,
    exclusions: dict[str, Any],
) -> str | None:
    manager_action = str(row.get("manager_action_signal") or "")
    excluded_actions = set(exclusions.get("manager_actions") or [])
    if manager_action in excluded_actions:
        return f"manager_action:{manager_action}"

    opinions = int(row.get("weeks_opinions") or 0)
    min_opinions = int(exclusions.get("min_consensus_opinions") or 0)
    if opinions < min_opinions:
        return f"opinions<{min_opinions}"

    agreement = _coerce_float(row.get("weeks_agreement_ratio"))
    min_agreement = float(exclusions.get("min_agreement_ratio") or 0.0)
    if agreement < min_agreement:
        return f"agreement<{min_agreement:.2f}"

    if breakout_row:
        risk_flag_map = {
            "false_breakout_risk": _coerce_bool(
                breakout_row.get("risk_false_breakout")
            ),
            "fragility_conflict": _coerce_bool(
                breakout_row.get("risk_fragility_conflict")
            ),
        }
        for flag_name in exclusions.get("breakout_risk_flags") or []:
            if risk_flag_map.get(flag_name):
                return f"breakout_risk:{flag_name}"

    return None


def _build_candidate_records(
    database_path: str | Path,
    run_id: str,
    config: dict[str, Any],
    *,
    include_earnings_boost: bool,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    multi_lens_floor = float(config.get("multi_lens", {}).get("score_floor", 0.35))
    base_rows = _execute_query(
        database_path,
        _CANDIDATE_FETCH_SQL,
        parameters=[multi_lens_floor, run_id, run_id, run_id],
        conn=conn,
    )

    earnings_by_row_number: dict[int, dict[str, Any]] = {}
    if include_earnings_boost:
        try:
            earnings_rows = _execute_query(
                database_path, _EARNINGS_SQL, parameters=[run_id], conn=conn
            )
            for earnings_row in earnings_rows:
                row_number = earnings_row.get("row_number")
                if row_number is not None:
                    earnings_by_row_number[int(row_number)] = earnings_row
        except Exception:
            earnings_by_row_number = {}

    scoring_weights = config.get("scoring") or {}
    sleeves_config = config.get("sleeves") or {}
    exclusions = config.get("exclusions") or {}
    multi_lens_cfg = config.get("multi_lens") or {}
    min_bullish = int(multi_lens_cfg.get("min_bullish_profiles") or 3)
    breakout_config = config.get("breakout_boost") or {}
    earnings_config = config.get("earnings_catalyst") or {}

    candidates: list[dict[str, Any]] = []
    for row in base_rows:
        consensus_row_number = row.get("consensus_row_number")
        symbol = str(row.get("symbol") or "")
        if consensus_row_number is None or not symbol:
            continue

        manager_action = str(row.get("manager_action_signal") or "")
        breakout_row = {
            "conviction_tier": row.get("breakout_conviction_tier"),
            "entry_readiness": row.get("entry_readiness"),
            "size_tier": row.get("size_tier"),
            "tape_pass": row.get("tape_pass"),
            "risk_false_breakout": row.get("risk_false_breakout"),
            "risk_fragility_conflict": row.get("risk_fragility_conflict"),
            "thesis_1": row.get("thesis_1"),
        }
        if not any(
            breakout_row.get(key) is not None
            for key in (
                "conviction_tier",
                "entry_readiness",
                "size_tier",
                "tape_pass",
                "risk_false_breakout",
                "risk_fragility_conflict",
                "thesis_1",
            )
        ):
            breakout_row = None

        earnings_row = earnings_by_row_number.get(int(consensus_row_number))
        exclusion = _exclusion_reason(row, breakout_row, exclusions)

        sleeve_scores = {
            sleeve_name: _sleeve_horizon_score(
                row,
                sleeve_name,
                sleeves_config.get(sleeve_name, {}),
                scoring_weights,
                manager_action,
            )
            for sleeve_name in SLEEVE_ORDER
        }

        primary_sleeve = max(
            sleeve_scores,
            key=lambda name: (sleeve_scores[name], -SLEEVE_ORDER.index(name)),
        )

        if exclusion:
            conviction_score = 0.0
            score_breakdown = {"sleeve_scores": sleeve_scores}
        else:
            primary_sub_score = sleeve_scores[primary_sleeve]

            bullish_count = int(row.get("bullish_profile_count") or 0)
            multi_lens_bonus = 0.0
            if bullish_count >= min_bullish:
                multi_lens_bonus = scoring_weights.get("multi_lens_weight", 0.15) * (
                    bullish_count / max(min_bullish, 1)
                )

            agreement_bonus = scoring_weights.get("agreement_ratio_weight", 0.15) * (
                _coerce_float(row.get("weeks_agreement_ratio"))
            )

            breakout_bonus, breakout_breakdown = _breakout_boost(
                breakout_row,
                breakout_config,
                scoring_weights.get("breakout_story_weight", 0.10),
            )
            earnings_bonus, earnings_breakdown = (0.0, {})
            if include_earnings_boost:
                earnings_bonus, earnings_breakdown = _earnings_boost(
                    earnings_row,
                    earnings_config,
                    scoring_weights.get("earnings_catalyst_weight", 0.10),
                )

            conviction_score = (
                primary_sub_score
                + multi_lens_bonus
                + agreement_bonus
                + breakout_bonus
                + earnings_bonus
            )

            score_breakdown = {
                "sleeve_scores": sleeve_scores,
                "primary_sleeve": primary_sleeve,
                "primary_sub_score": primary_sub_score,
                "multi_lens_bonus": multi_lens_bonus,
                "agreement_bonus": agreement_bonus,
                **breakout_breakdown,
                **earnings_breakdown,
            }

        candidates.append(
            {
                "consensus_row_number": int(consensus_row_number),
                "symbol": symbol,
                "company": row.get("company") or "",
                "sector": row.get("sector") or "",
                "industry": row.get("industry") or "",
                "sleeve": primary_sleeve,
                "conviction_score": conviction_score,
                "manager_action_signal": manager_action,
                "days_score": row.get("days_score"),
                "days_ras": row.get("days_ras"),
                "weeks_score": row.get("weeks_score"),
                "weeks_ras": row.get("weeks_ras"),
                "months_score": row.get("months_score"),
                "months_ras": row.get("months_ras"),
                "years_score": row.get("years_score"),
                "years_ras": row.get("years_ras"),
                "agreement_ratio": row.get("weeks_agreement_ratio"),
                "opinions": row.get("weeks_opinions"),
                "bullish_profile_count": int(row.get("bullish_profile_count") or 0),
                "breakout_conviction_tier": (
                    breakout_row.get("conviction_tier") if breakout_row else None
                ),
                "entry_readiness": (
                    breakout_row.get("entry_readiness") if breakout_row else None
                ),
                "size_tier": breakout_row.get("size_tier") if breakout_row else None,
                "tape_pass": breakout_row.get("tape_pass") if breakout_row else None,
                "earnings_days_until": (
                    earnings_row.get("days_until_earnings") if earnings_row else None
                ),
                "exclusion_reason": exclusion,
                "score_breakdown": score_breakdown,
                "thesis_1": breakout_row.get("thesis_1") if breakout_row else "",
            }
        )

    return candidates


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [c for c in candidates if not c.get("exclusion_reason")]
    eligible.sort(key=lambda row: row.get("conviction_score") or 0.0, reverse=True)

    for rank, row in enumerate(eligible, start=1):
        row["rank_overall"] = rank

    for sleeve_name in SLEEVE_ORDER:
        sleeve_rows = [c for c in eligible if c.get("sleeve") == sleeve_name]
        sleeve_rows.sort(
            key=lambda row: row.get("conviction_score") or 0.0, reverse=True
        )
        for rank, row in enumerate(sleeve_rows, start=1):
            row["rank_in_sleeve"] = rank

    excluded = [c for c in candidates if c.get("exclusion_reason")]
    excluded.sort(key=lambda row: row.get("conviction_score") or 0.0, reverse=True)
    for row in excluded:
        row["rank_overall"] = None
        row["rank_in_sleeve"] = None

    return eligible + excluded


def _candidate_key(row: dict[str, Any]) -> int:
    return int(row["consensus_row_number"])


def _select_display_rows(
    ranked_eligible: list[dict[str, Any]],
    config: dict[str, Any],
) -> set[int]:
    allocation = config.get("sleeve_allocation") or {}
    max_total = int(config.get("max_display_total") or 10)
    selected_keys: list[int] = []

    for sleeve_name in SLEEVE_ORDER:
        cap = int(allocation.get(sleeve_name) or 0)
        sleeve_rows = [
            row for row in ranked_eligible if row.get("sleeve") == sleeve_name
        ]
        for row in sleeve_rows[:cap]:
            candidate_key = _candidate_key(row)
            if candidate_key not in selected_keys:
                selected_keys.append(candidate_key)

    if len(selected_keys) < max_total:
        for row in ranked_eligible:
            candidate_key = _candidate_key(row)
            if candidate_key in selected_keys:
                continue
            selected_keys.append(candidate_key)
            if len(selected_keys) >= max_total:
                break

    return set(selected_keys[:max_total])


def _write_conviction_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "consensus_row_number",
        "symbol",
        "company",
        "sector",
        "industry",
        "sleeve",
        "conviction_score",
        "rank_overall",
        "rank_in_sleeve",
        "display_flag",
        "manager_action_signal",
        "days_score",
        "days_ras",
        "weeks_score",
        "weeks_ras",
        "months_score",
        "months_ras",
        "years_score",
        "years_ras",
        "agreement_ratio",
        "opinions",
        "bullish_profile_count",
        "breakout_conviction_tier",
        "entry_readiness",
        "size_tier",
        "tape_pass",
        "earnings_days_until",
        "exclusion_reason",
        "score_breakdown_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key) for key in fieldnames if key != "score_breakdown_json"}
            row["score_breakdown_json"] = json.dumps(
                record.get("score_breakdown") or {}, sort_keys=True
            )
            writer.writerow(row)


def _format_notes(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if _coerce_bool(row.get("tape_pass")):
        parts.append("tape_pass")
    bullish = row.get("bullish_profile_count")
    if bullish:
        parts.append(f"{bullish} lenses")
    weeks_ras = row.get("weeks_ras")
    if weeks_ras is not None:
        parts.append(f"RAS {float(weeks_ras):.2f}")
    thesis = str(row.get("thesis_1") or "").strip()
    if thesis:
        parts.append(thesis[:60])
    return "; ".join(parts) if parts else "—"


def _format_ticker_label(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "")
    company = str(row.get("company") or "").strip()
    if not company:
        return symbol
    short_company = company[:18]
    return f"{symbol} ({short_company})"


def _write_daily_focus_log(
    path: Path,
    *,
    run_id: str,
    config: dict[str, Any],
    ranked_eligible: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
    display_keys: set[int],
    suite_id: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    log_to_file(path, f"CONVICTION DAILY FOCUS — {config.get('config_id')}")
    log_to_file(path, "=" * 120)
    suite_label = suite_id or "n/a"
    log_to_file(
        path,
        f"run_id: {run_id} | config_hash: {config.get('config_hash')} | "
        f"suite: {suite_label} | candidates: {len(all_records)} | "
        f"eligible: {len(ranked_eligible)} | displayed: {len(display_keys)}",
    )
    log_to_file(path, "")

    allocation = config.get("sleeve_allocation") or {}
    display_rows = [
        r for r in ranked_eligible if _candidate_key(r) in display_keys
    ]

    for sleeve_name in SLEEVE_ORDER:
        cap = int(allocation.get(sleeve_name) or 0)
        sleeve_display = [r for r in display_rows if r.get("sleeve") == sleeve_name]
        filled = len(sleeve_display)
        log_to_file(path, f"{SLEEVE_TITLES[sleeve_name]} — allocation {filled}/{cap}")
        log_to_file(path, "-" * 120)
        log_to_file(
            path,
            f"{'Rank':<6}{'Score':>8}  {'Ticker':<32}{'Action':<32}{'Size':<10}Notes",
        )
        if not sleeve_display:
            log_to_file(path, "  (none)")
        else:
            for row in sleeve_display:
                log_to_file(
                    path,
                    f"{row.get('rank_in_sleeve') or '-':<6}"
                    f"{row.get('conviction_score', 0.0):>8.2f}  "
                    f"{_format_ticker_label(row):<32}"
                    f"{str(row.get('manager_action_signal') or '')[:31]:<32}"
                    f"{str(row.get('size_tier') or '—')[:9]:<10}"
                    f"{_format_notes(row)}",
                )
        log_to_file(path, "")

    near_misses = [
        r for r in all_records if r.get("exclusion_reason")
    ][:5]
    log_to_file(path, "EXCLUDED NEAR-MISSES (top by raw sleeve score)")
    log_to_file(path, "-" * 120)
    if not near_misses:
        log_to_file(path, "  (none)")
    else:
        for row in near_misses:
            log_to_file(
                path,
                f"  {row.get('symbol', ''):<14} "
                f"reason={row.get('exclusion_reason')} "
                f"sleeve={row.get('sleeve')}",
            )


def _records_for_duckdb(
    run_id: str,
    records: list[dict[str, Any]],
    display_keys: set[int],
    config_id: str,
) -> list[dict[str, Any]]:
    duckdb_records: list[dict[str, Any]] = []
    for row_number, record in enumerate(records, start=1):
        duckdb_records.append(
            {
                "run_id": run_id,
                "row_number": row_number,
                "symbol": record.get("symbol"),
                "company": record.get("company"),
                "sector": record.get("sector"),
                "industry": record.get("industry"),
                "sleeve": record.get("sleeve"),
                "conviction_score": record.get("conviction_score"),
                "rank_overall": record.get("rank_overall"),
                "rank_in_sleeve": record.get("rank_in_sleeve"),
                "display_flag": _candidate_key(record) in display_keys,
                "manager_action_signal": record.get("manager_action_signal"),
                "days_score": record.get("days_score"),
                "days_ras": record.get("days_ras"),
                "weeks_score": record.get("weeks_score"),
                "weeks_ras": record.get("weeks_ras"),
                "months_score": record.get("months_score"),
                "months_ras": record.get("months_ras"),
                "years_score": record.get("years_score"),
                "years_ras": record.get("years_ras"),
                "agreement_ratio": record.get("agreement_ratio"),
                "opinions": record.get("opinions"),
                "bullish_profile_count": record.get("bullish_profile_count"),
                "breakout_conviction_tier": record.get("breakout_conviction_tier"),
                "entry_readiness": record.get("entry_readiness"),
                "size_tier": record.get("size_tier"),
                "tape_pass": record.get("tape_pass"),
                "earnings_days_until": record.get("earnings_days_until"),
                "exclusion_reason": record.get("exclusion_reason"),
                "score_breakdown_json": json.dumps(
                    record.get("score_breakdown") or {}, sort_keys=True
                ),
                "config_id": config_id,
            }
        )
    return duckdb_records


def run_conviction_mode_duckdb(
    database_path: str | Path,
    run_id: str,
    run_output_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    include_earnings_boost: bool = False,
    export_parquet: bool = True,
    parquet_dir: str | Path | None = None,
    suite_id: str | None = None,
    duckdb_store: MovePredictionDuckDBStore | None = None,
) -> dict[str, Any]:
    """Build conviction rankings from an existing move-prediction DuckDB run."""
    config = resolve_conviction_mode_config(config_path)
    resolved_output = Path(run_output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)

    active_conn = duckdb_store.conn if duckdb_store is not None else None
    candidates = _build_candidate_records(
        database_path,
        run_id,
        config,
        include_earnings_boost=include_earnings_boost,
        conn=active_conn,
    )
    ranked = _rank_candidates(candidates)
    ranked_eligible = [row for row in ranked if not row.get("exclusion_reason")]
    display_keys = _select_display_rows(ranked_eligible, config)

    for record in ranked:
        record["display_flag"] = _candidate_key(record) in display_keys

    csv_path = resolved_output / "move_prediction__conviction_rankings.csv"
    log_path = resolved_output / "move_prediction__conviction_daily_focus.log"
    _write_conviction_csv(csv_path, ranked)
    _write_daily_focus_log(
        log_path,
        run_id=run_id,
        config=config,
        ranked_eligible=ranked_eligible,
        all_records=ranked,
        display_keys=display_keys,
        suite_id=suite_id,
    )

    duckdb_records = _records_for_duckdb(
        run_id,
        ranked,
        display_keys,
        str(config.get("config_id") or ""),
    )

    parquet_exports: dict[str, Path] = {}
    if duckdb_store is not None:
        duckdb_store.delete_run_data(run_id, table_names=["conviction_rankings"])
        duckdb_store.append_conviction_rankings(duckdb_records)
        duckdb_store.register_report(
            run_id=run_id,
            report_key="_conviction_rankings",
            report_type="conviction_rankings_csv",
            file_path=csv_path,
        )
        duckdb_store.register_report(
            run_id=run_id,
            report_key="_conviction_daily_focus",
            report_type="conviction_daily_focus_log",
            file_path=log_path,
        )
        # Parquet is exported by the parent suite (full or incremental) to avoid
        # re-writing large weekly tables twice in the same run.
    else:
        with MovePredictionDuckDBStore(
            database_path=database_path,
            parquet_dir=parquet_dir if export_parquet else None,
        ) as store:
            store.delete_run_data(run_id, table_names=["conviction_rankings"])
            store.append_conviction_rankings(duckdb_records)
            store.register_report(
                run_id=run_id,
                report_key="_conviction_rankings",
                report_type="conviction_rankings_csv",
                file_path=csv_path,
            )
            store.register_report(
                run_id=run_id,
                report_key="_conviction_daily_focus",
                report_type="conviction_daily_focus_log",
                file_path=log_path,
            )
            if export_parquet and parquet_dir:
                parquet_exports = store.export_tables_to_parquet(
                    run_id=run_id,
                    parquet_dir=parquet_dir,
                    table_names=["conviction_rankings", "generated_reports"],
                )

    return {
        "csv_path": csv_path,
        "log_path": log_path,
        "candidate_count": len(candidates),
        "eligible_count": len(ranked_eligible),
        "display_count": len(display_keys),
        "config_id": config.get("config_id"),
        "config_hash": config.get("config_hash"),
        "parquet_exports": parquet_exports,
    }
