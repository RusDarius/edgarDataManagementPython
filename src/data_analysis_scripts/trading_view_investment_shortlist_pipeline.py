"""Investment opportunity shortlist pipeline for prediction_analysis DuckDB stores.

Runs named discovery, validation, and action-bucket queries against the weekly
move_prediction DuckDB database and exports watchlist CSVs grouped by
manager_action_signal.

SQL query definitions live in:
    sql_connections_space/investment_opportunity_queries.session.sql
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERY_SESSION_PATH = (
    PROJECT_ROOT
    / "sql_connections_space"
    / "investment_opportunity_queries.session.sql"
)

LATEST_RUN_CTE = """
WITH latest_run AS (
    SELECT run_id, created_at_utc
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
"""

# raw_scan_rows often stores exchange-prefixed symbols; profile tables use short tickers.
RAW_SYMBOL_JOIN = """
    (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
"""

RAW_SYMBOL_JOIN_LEADERS = """
    (
        r.symbol = l.symbol
        OR r.symbol LIKE '%:' || l.symbol
        OR l.symbol LIKE '%:' || r.symbol
    )
"""

DISCOVERY_SQL = (
    LATEST_RUN_CTE
    + """
SELECT c.run_id,
    c.symbol,
    c.company,
    c.sector,
    c.industry,
    c.horizon_name,
    c.score,
    c.risk_adjusted_score,
    c.agreement_ratio,
    c.opinions,
    c.confidence,
    c.risk_tier,
    c.manager_action_signal,
    c.direction
FROM consensus_horizon_scores c
    JOIN latest_run lr ON c.run_id = lr.run_id
WHERE c.horizon_name = 'weeks'
    AND c.score IS NOT NULL
ORDER BY c.risk_adjusted_score DESC NULLS LAST
LIMIT 100
"""
)

BREAKOUT_VALIDATED_SQL = (
    LATEST_RUN_CTE
    + """
SELECT h.run_id,
    h.symbol,
    h.company,
    h.sector,
    h.industry,
    h.score,
    h.confidence,
    h.coverage,
    h.risk_adjusted_score,
    h.risk_tier,
    h.manager_action_signal,
    r.relative_volume_10d_calc,
    r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw,
    r."ADX+DI" - r."ADX-DI" AS adx_directional_spread_raw,
    r."Aroon.Up" - r."Aroon.Down" AS aroon_spread_raw,
    c.attention,
    c.momentum,
    c.trend,
    c.safety
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
        AND """
    + RAW_SYMBOL_JOIN.strip()
    + """
    JOIN profile_components c
        ON c.run_id = h.run_id
        AND c.profile_name = h.profile_name
        AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND h.coverage >= 0.70
    AND r.relative_volume_10d_calc > 1.3
    AND (r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0)) > 1.05
    AND (r."ADX+DI" - r."ADX-DI") > 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50
"""
)

AVOID_TRAPS_SQL = (
    LATEST_RUN_CTE
    + """
SELECT DISTINCT h.symbol,
    h.company,
    h.profile_name,
    h.manager_action_signal,
    c.valuation,
    c.quality,
    c.safety
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c
        ON c.run_id = h.run_id
        AND c.profile_name = h.profile_name
        AND c.symbol = h.symbol
WHERE h.manager_action_signal = 'avoid_value_trap'
   OR (c.valuation >= 0.55 AND (c.quality <= -0.35 OR c.safety <= -0.55))
"""
)

MULTI_LENS_AGREE_SQL = (
    LATEST_RUN_CTE
    + """
, profile_opinions AS (
    SELECT symbol,
        company,
        COUNT(*) FILTER (WHERE score >= 0.35) AS bullish_profile_count,
        COUNT(*) AS total_profiles,
        AVG(score) AS avg_profile_score
    FROM consensus_profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE horizon_name = 'weeks'
        AND profile_name != 'fragility_short'
    GROUP BY symbol, company
)
SELECT p.symbol,
    p.company,
    p.bullish_profile_count,
    p.total_profiles,
    p.avg_profile_score,
    c.score AS consensus_score,
    c.agreement_ratio,
    c.risk_adjusted_score,
    c.manager_action_signal
FROM profile_opinions p
    JOIN consensus_horizon_scores c
        ON c.run_id = (SELECT run_id FROM latest_run)
        AND c.symbol = p.symbol
        AND c.horizon_name = 'weeks'
WHERE p.bullish_profile_count >= 3
ORDER BY p.bullish_profile_count DESC,
    c.risk_adjusted_score DESC NULLS LAST
LIMIT 50
"""
)

ACTION_BUCKET_HORIZONS = {
    "add_long_breakout": "weeks",
    "accumulate_reversal_long": "weeks",
    "watch_reversal_entry": "weeks",
    "accumulate_value_catalyst": "months",
    "watch_value_reversal": "months",
    "hold_quality_long": "years",
    "hedge_or_short": "weeks",
    "mean_reversion_watch": "weeks",
    "avoid_reversal_trap": "weeks",
    "neutral_watch": "weeks",
}

_BLOCK_PATTERN = re.compile(
    r"--\s*@block\s+(?P<name>[a-zA-Z0-9_]+)\s*\n(?P<body>.*?)(?=\n--\s*@block|\Z)",
    re.DOTALL,
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")


def load_named_query_sql(
    query_name: str,
    session_path: str | Path | None = None,
) -> str:
    """Return the SQL body for a named @block from the investment query session file."""
    resolved_path = Path(session_path or DEFAULT_QUERY_SESSION_PATH)
    session_text = resolved_path.read_text(encoding="utf-8")
    matches = {
        match.group("name"): match.group("body").strip()
        for match in _BLOCK_PATTERN.finditer(session_text)
    }
    if query_name not in matches:
        available = ", ".join(sorted(matches))
        raise KeyError(
            f"Query '{query_name}' not found in {resolved_path}. Available: {available}"
        )
    return matches[query_name]


def run_named_query(
    database_path: str | Path,
    query_name: str,
    session_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Execute a named query block from the investment opportunity SQL session."""
    sql = load_named_query_sql(query_name, session_path=session_path)
    return query_move_prediction_duckdb(database_path, sql)


def _resolve_latest_run_id(database_path: str | Path) -> str | None:
    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
        """,
    )
    if not rows:
        return None
    return str(rows[0]["run_id"])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return path


def _group_action_bucket_rows(
    database_path: str | Path,
    run_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    run_filter = (
        "run_id = ?"
        if run_id
        else "run_id = (SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC LIMIT 1)"
    )
    params: list[Any] = [run_id] if run_id else []

    rows = query_move_prediction_duckdb(
        database_path,
        f"""
        SELECT symbol,
            company,
            sector,
            industry,
            horizon_name,
            score,
            risk_adjusted_score,
            agreement_ratio,
            opinions,
            confidence,
            risk_tier,
            manager_action_signal,
            direction
        FROM consensus_horizon_scores
        WHERE {run_filter}
          AND manager_action_signal IS NOT NULL
          AND manager_action_signal != ''
        ORDER BY manager_action_signal,
            risk_adjusted_score DESC NULLS LAST
        """,
        parameters=params,
    )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    preferred_horizon_seen: set[tuple[str, str]] = set()
    for row in rows:
        action = str(row.get("manager_action_signal") or "neutral_watch")
        symbol = str(row.get("symbol") or "")
        horizon = str(row.get("horizon_name") or "")
        preferred_horizon = ACTION_BUCKET_HORIZONS.get(action)
        dedupe_key = (action, symbol)

        if preferred_horizon and horizon != preferred_horizon:
            continue
        if dedupe_key in preferred_horizon_seen:
            continue
        preferred_horizon_seen.add(dedupe_key)
        grouped[action].append(row)
    return dict(grouped)


def run_investment_shortlist_pipeline(
    database_path: str | Path,
    output_dir: str | Path | None = None,
    run_id: str | None = None,
    session_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run discovery -> validation -> action-bucket export for the latest DuckDB run.

    Writes:
      - investment_shortlist__discovery.csv
      - investment_shortlist__breakout_long_validated.csv
      - investment_shortlist__multi_lens_agree.csv
      - investment_shortlist__avoid_traps.csv
      - investment_shortlist__action_<signal>.csv (one per manager_action_signal)
      - investment_shortlist__overview.log
    """
    resolved_db = Path(database_path)
    if not resolved_db.exists():
        raise FileNotFoundError(f"DuckDB database not found: {resolved_db}")

    resolved_run_id = run_id or _resolve_latest_run_id(resolved_db)
    if not resolved_run_id:
        raise ValueError(f"No runs found in DuckDB database: {resolved_db}")

    timestamp = _utc_timestamp()
    resolved_output = Path(
        output_dir
        or (
            resolved_db.parent
            / "runs"
            / resolved_run_id
            / "investment_shortlists"
            / timestamp
        )
    )
    resolved_output.mkdir(parents=True, exist_ok=True)

    discovery_rows = query_move_prediction_duckdb(resolved_db, DISCOVERY_SQL)
    breakout_rows = query_move_prediction_duckdb(resolved_db, BREAKOUT_VALIDATED_SQL)
    multi_lens_rows = query_move_prediction_duckdb(resolved_db, MULTI_LENS_AGREE_SQL)
    avoid_trap_rows = query_move_prediction_duckdb(resolved_db, AVOID_TRAPS_SQL)
    action_buckets = _group_action_bucket_rows(resolved_db, run_id=resolved_run_id)

    exported_files: dict[str, str] = {
        "discovery": str(
            _write_csv(
                resolved_output / "investment_shortlist__discovery.csv", discovery_rows
            )
        ),
        "breakout_long_validated": str(
            _write_csv(
                resolved_output / "investment_shortlist__breakout_long_validated.csv",
                breakout_rows,
            )
        ),
        "multi_lens_agree": str(
            _write_csv(
                resolved_output / "investment_shortlist__multi_lens_agree.csv",
                multi_lens_rows,
            )
        ),
        "avoid_traps": str(
            _write_csv(
                resolved_output / "investment_shortlist__avoid_traps.csv",
                avoid_trap_rows,
            )
        ),
    }

    for action_signal, rows in sorted(action_buckets.items()):
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", action_signal).strip("_").lower()
        exported_path = resolved_output / f"investment_shortlist__action_{slug}.csv"
        exported_files[f"action_{slug}"] = str(_write_csv(exported_path, rows))

    overview_path = resolved_output / "investment_shortlist__overview.log"
    overview_lines = [
        "Investment Opportunity Shortlist Pipeline",
        f"database_path: {resolved_db}",
        f"run_id: {resolved_run_id}",
        f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"discovery_rows: {len(discovery_rows)}",
        f"breakout_long_validated_rows: {len(breakout_rows)}",
        f"multi_lens_agree_rows: {len(multi_lens_rows)}",
        f"avoid_trap_rows: {len(avoid_trap_rows)}",
        "",
        "action_bucket_counts:",
    ]
    for action_signal, rows in sorted(action_buckets.items()):
        overview_lines.append(f"  {action_signal}: {len(rows)}")
    overview_lines.extend(["", "exported_files:"])
    for key, path in sorted(exported_files.items()):
        overview_lines.append(f"  {key}: {path}")
    overview_path.write_text("\n".join(overview_lines) + "\n", encoding="utf-8")

    return {
        "database_path": str(resolved_db),
        "run_id": resolved_run_id,
        "output_dir": str(resolved_output),
        "overview_log": str(overview_path),
        "counts": {
            "discovery": len(discovery_rows),
            "breakout_long_validated": len(breakout_rows),
            "multi_lens_agree": len(multi_lens_rows),
            "avoid_traps": len(avoid_trap_rows),
            "action_buckets": {
                key: len(value) for key, value in action_buckets.items()
            },
        },
        "exported_files": exported_files,
        "query_session_path": str(session_path or DEFAULT_QUERY_SESSION_PATH),
    }


def run_profile_raw_validation_report(
    database_path: str | Path,
    profile_name: str = "breakout_long",
    horizon_name: str = "weeks",
    top_n: int = 10,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize top profile leaders with key raw fields used in scoring validation."""
    sql = (
        """
    WITH latest_run AS (
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
    ),
    leaders AS (
        SELECT h.symbol,
            h.company,
            h.score,
            h.risk_adjusted_score,
            ROW_NUMBER() OVER (ORDER BY h.risk_adjusted_score DESC NULLS LAST) AS leader_rank
        FROM profile_horizon_scores h
            JOIN latest_run lr ON h.run_id = lr.run_id
        WHERE h.profile_name = ?
          AND h.horizon_name = ?
    )
    SELECT l.leader_rank,
        l.symbol,
        l.company,
        l.score,
        l.risk_adjusted_score,
        r.relative_volume_10d_calc,
        r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw,
        r."ADX+DI" - r."ADX-DI" AS adx_directional_spread_raw,
        r."Aroon.Up" - r."Aroon.Down" AS aroon_spread_raw,
        r."Perf.5D",
        r."Perf.W",
        r.gap,
        r.ATRP,
        ABS(r.gap) / NULLIF(r.ATRP, 0) AS gap_severity_raw,
        c.attention,
        c.event,
        c.momentum,
        c.trend,
        c.quality,
        c.valuation,
        c.safety
    FROM leaders l
        JOIN latest_run lr ON TRUE
        JOIN raw_scan_rows r ON r.run_id = lr.run_id
            AND """
        + RAW_SYMBOL_JOIN_LEADERS.strip()
        + """
        JOIN profile_components c
            ON c.run_id = lr.run_id
            AND c.profile_name = ?
            AND c.symbol = l.symbol
    WHERE l.leader_rank <= ?
    ORDER BY l.leader_rank
    """
    )
    rows = query_move_prediction_duckdb(
        database_path,
        sql,
        parameters=[profile_name, horizon_name, profile_name, top_n],
    )

    if output_path:
        _write_csv(Path(output_path), rows)

    return {
        "profile_name": profile_name,
        "horizon_name": horizon_name,
        "top_n": top_n,
        "row_count": len(rows),
        "rows": rows,
        "output_path": str(output_path) if output_path else None,
    }
