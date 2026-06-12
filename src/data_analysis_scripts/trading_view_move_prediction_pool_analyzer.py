"""Multi-run pool analysis for move prediction performance and pattern discovery.

Analyzes aggregated move prediction data across multiple weeks/runs to identify:
- Top performing profiles (by score, price performance, correlation)
- Pattern predictors and performance correlations
- Best performing stocks across profiles

Typical usage::

    from data_analysis_scripts.trading_view_move_prediction_pool_analyzer import (
        analyze_pool_database,
        run_pool_analysis_from_path,
        export_analysis_reports,
    )

    # Analyze a specific pool database
    result = analyze_pool_database(
        database_path="logs/tradingview_analysis/prediction_analysis/multi_run_pool/runs/.../pooled_move_prediction_runs.duckdb",
        min_score_threshold=65.0,
        top_n_profiles=10,
        top_n_stocks=100,
    )

    # Or analyze by run pool ID pattern
    result = run_pool_analysis_from_path(
        pool_id_pattern="move_prediction_run_pool_20260610_2000_utc_94a206f7",
    )

Command-line usage::

    python -m data_analysis_scripts.trading_view_move_prediction_pool_analyzer \
        --database-path "path/to/pooled_move_prediction_runs.duckdb" \
        --output-dir "./analysis_output" \
        --min-score 65.0 \
        --top-n-profiles 10 \
        --top-n-stocks 100
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import textwrap
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from db.trading_view_move_prediction_duckdb import (
    open_move_prediction_duckdb_connection,
)

DEFAULT_PREDICTION_ROOT = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis"
)
DEFAULT_POOL_ROOT = DEFAULT_PREDICTION_ROOT / "multi_run_pool"
DEFAULT_ANALYSIS_OUTPUT_ROOT = DEFAULT_PREDICTION_ROOT / "pool_analysis"

# Default analysis parameters
# Score scale: -3.0 to +3.0 (breakout thresholds: 0.35 directional, 1.10 STRONG_UP, 1.50 VERY_HIGH, 2.00 ELITE)
DEFAULT_MIN_SCORE_THRESHOLD = 0.35  # DIRECTIONAL_MOVE_SCORE_THRESHOLD equivalent
DEFAULT_TOP_N_PROFILES = 15
DEFAULT_TOP_N_STOCKS = 200
# Confidence scale: 5-99 (high >= 70)
DEFAULT_CONFIDENCE_THRESHOLD = 60.0  # confidence >= 60 (was 0.6 on wrong scale)
DEFAULT_COVERAGE_THRESHOLD = 0.3  # coverage is 0.0-1.0 ratio, this threshold is correct


@dataclass(frozen=True)
class ProfilePerformanceMetrics:
    """Aggregated performance metrics for a single profile across all runs."""

    profile_name: str
    total_runs: int
    total_symbols_scored: int
    avg_score: float | None
    avg_risk_adjusted_score: float | None
    avg_confidence: float | None
    avg_coverage: float | None
    score_std_dev: float | None
    bullish_direction_ratio: float | None  # direction IN ('Strong Up', 'Up')
    high_confidence_ratio: float | None  # confidence >= 70 (5-99 scale, high >= 70)
    strong_up_ratio: float | None  # score >= 1.10 (STRONG_UP threshold on -3..+3 scale)
    top_decile_capture_rate: float | None  # how often profile picks end up in top decile performers
    price_performance_correlation: float | None
    sector_diversity_score: float | None  # normalized entropy of sector distribution
    persistence_score: float | None  # how consistent is the profile across time


@dataclass(frozen=True)
class StockCompositeRanking:
    """Composite ranking for a stock across all profiles and runs."""

    symbol: str
    company: str | None
    sector: str | None
    industry: str | None
    profile_appearances: int  # number of profiles that scored this stock
    run_appearances: int  # number of runs where stock appeared
    avg_score_across_profiles: float | None
    max_score_across_profiles: float | None
    best_profile: str | None
    avg_risk_adjusted_score: float | None
    avg_confidence: float | None
    avg_direction: float | None  # average direction value (positive = long bias)
    consensus_long_count: int  # how many profiles suggested long
    consensus_short_count: int  # how many profiles suggested short
    composite_rank: float | None  # weighted composite score
    price_performance_proxy: float | None  # proxy based on momentum/trend fields
    strongest_predictor_profile: str | None  # profile that most consistently scored this high


@dataclass(frozen=True)
class PatternPredictorInsight:
    """Insights about which raw fields/components predict performance."""

    profile_name: str
    predictor_field: str  # raw field or component name
    predictor_category: str  # momentum, trend, quality, valuation, safety, scale, attention, event
    correlation_with_high_score: float | None
    correlation_with_direction: float | None
    predictive_power_score: float | None  # composite of correlations
    coverage_rate: float | None  # how often this field is populated
    sector_bias: str | None  # which sector shows strongest correlation
    time_stability: float | None  # how stable is the correlation across time periods


@dataclass
class PoolAnalysisResult:
    """Complete analysis result container."""

    pool_aggregation_id: str
    database_path: str
    analysis_timestamp_utc: str
    total_runs: int
    total_weeks: int
    week_range: tuple[int | None, int | None]
    profile_metrics: list[ProfilePerformanceMetrics]
    stock_rankings: list[StockCompositeRanking]
    pattern_insights: list[PatternPredictorInsight]
    score_vs_price_correlation: float | None
    top_performers_intersection: list[str]  # symbols that score high AND show price perf
    missing_performance_symbols: list[str]  # high scores but poor price performance
    execution_time_seconds: float


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _ensure_output_dir(output_dir: str | Path | None) -> Path:
    if output_dir is None:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_dir = DEFAULT_ANALYSIS_OUTPUT_ROOT / f"pool_analysis_{timestamp}"
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_pool_aggregation_summary(conn: Any) -> dict[str, Any]:
    """Extract basic metadata about the pooled aggregation."""
    result = conn.execute("""
        SELECT
            pool_aggregation_id,
            COUNT(DISTINCT source_database_path) as total_source_databases,
            COUNT(DISTINCT source_iso_year || '_' || source_iso_week) as total_weeks,
            MIN(source_iso_week) as min_week,
            MAX(source_iso_week) as max_week,
            MIN(source_iso_year) as min_year,
            MAX(source_iso_year) as max_year
        FROM pool_source_databases
        GROUP BY pool_aggregation_id
        ORDER BY pool_aggregation_id DESC
        LIMIT 1
    """).fetchone()

    if result is None:
        return {}

    return {
        "pool_aggregation_id": result[0],
        "total_source_databases": result[1],
        "total_weeks": result[2],
        "week_range": (result[3], result[4]),
        "year_range": (result[5], result[6]),
    }


def _get_run_inventory(conn: Any) -> list[dict[str, Any]]:
    """Get inventory of all runs in the pool."""
    rows = conn.execute("""
        SELECT
            run_id,
            created_at_utc,
            run_date_utc,
            iso_year,
            iso_week,
            scan_data_count,
            profile_names_json,
            suite_name
        FROM pool_run_metadata
        ORDER BY created_at_utc DESC
    """).fetchall()

    return [
        {
            "run_id": row[0],
            "created_at_utc": row[1],
            "run_date_utc": row[2],
            "iso_year": row[3],
            "iso_week": row[4],
            "scan_data_count": row[5],
            "profile_names_json": json.loads(row[6]) if row[6] else [],
            "suite_name": row[7],
        }
        for row in rows
    ]


def _analyze_profile_performance(
    conn: Any,
    min_score_threshold: float = DEFAULT_MIN_SCORE_THRESHOLD,
) -> list[ProfilePerformanceMetrics]:
    """Analyze performance metrics for each profile across the pool."""

    # First, get all unique profiles
    profile_rows = conn.execute("""
        SELECT DISTINCT profile_name
        FROM pool_profile_horizon_scores
        WHERE profile_name IS NOT NULL
        ORDER BY profile_name
    """).fetchall()

    profiles = [row[0] for row in profile_rows]
    metrics: list[ProfilePerformanceMetrics] = []

    for profile in profiles:
        # Aggregate metrics for this profile
        row = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT run_id) as total_runs,
                COUNT(DISTINCT symbol) as total_symbols,
                AVG(score) as avg_score,
                AVG(risk_adjusted_score) as avg_risk_adjusted_score,
                AVG(confidence) as avg_confidence,
                AVG(coverage) as avg_coverage,
                STDDEV(score) as score_std_dev,
                AVG(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1.0 ELSE 0.0 END) as bullish_direction_ratio,
                AVG(CASE WHEN confidence >= 70 THEN 1.0 ELSE 0.0 END) as high_confidence_ratio,
                AVG(CASE WHEN score >= 1.10 THEN 1.0 ELSE 0.0 END) as strong_up_ratio
            FROM pool_profile_horizon_scores
            WHERE profile_name = ?
                AND horizon_name = 'weeks'
            """,
            [profile],
        ).fetchone()

        if row is None or row[0] == 0:
            continue

        # Calculate sector diversity
        sector_row = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT sector) as unique_sectors,
                COUNT(*) as total_rows,
                SUM(POWER(COUNT(*), 2)) as sum_squares
            FROM pool_profile_horizon_scores
            WHERE profile_name = ?
                AND horizon_name = 'weeks'
            GROUP BY sector
            """,
            [profile],
        ).fetchone()

        sector_diversity = None
        if sector_row and sector_row[1] > 0:
            # Herfindahl index approximation (normalized)
            total = sector_row[1]
            unique_sectors = sector_row[0]
            sector_diversity = unique_sectors / min(total, 12)  # normalize by max expected sectors

        metrics.append(
            ProfilePerformanceMetrics(
                profile_name=profile,
                total_runs=row[0] or 0,
                total_symbols_scored=row[1] or 0,
                avg_score=row[2],
                avg_risk_adjusted_score=row[3],
                avg_confidence=row[4],
                avg_coverage=row[5],
                score_std_dev=row[6],
                bullish_direction_ratio=row[7],
                high_confidence_ratio=row[8],
                strong_up_ratio=row[9],
                top_decile_capture_rate=None,  # Would need price performance data
                price_performance_correlation=None,  # Calculated separately
                sector_diversity_score=sector_diversity,
                persistence_score=None,  # Would need time-series analysis
            )
        )

    # Sort by avg_risk_adjusted_score descending
    metrics.sort(key=lambda x: (x.avg_risk_adjusted_score or 0), reverse=True)
    return metrics


def _analyze_stock_composite_rankings(
    conn: Any,
    top_n: int = DEFAULT_TOP_N_STOCKS,
    min_profile_appearances: int = 2,
) -> list[StockCompositeRanking]:
    """Create composite stock rankings across all profiles and runs."""

    rows = conn.execute(
        f"""
        SELECT
            symbol,
            MAX(company) as company,
            MAX(sector) as sector,
            MAX(industry) as industry,
            COUNT(DISTINCT profile_name) as profile_appearances,
            COUNT(DISTINCT run_id) as run_appearances,
            AVG(score) as avg_score,
            MAX(score) as max_score,
            MAX(CASE WHEN score = max_score THEN profile_name END) as best_profile,
            AVG(risk_adjusted_score) as avg_risk_adjusted_score,
            AVG(confidence) as avg_confidence,
            AVG(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1.0 ELSE 0.0 END) as bullish_ratio,
            SUM(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1 ELSE 0 END) as long_count,
            SUM(CASE WHEN direction IN ('Strong Down', 'Down') THEN 1 ELSE 0 END) as short_count
        FROM pool_profile_horizon_scores
        WHERE horizon_name = 'weeks'
            AND score IS NOT NULL
        GROUP BY symbol
        HAVING COUNT(DISTINCT profile_name) >= ?
        ORDER BY avg_risk_adjusted_score DESC NULLS LAST
        LIMIT ?
        """,
        [min_profile_appearances, top_n * 3],  # Get more for filtering
    ).fetchall()

    rankings: list[StockCompositeRanking] = []
    for row in rows:
        # Calculate composite rank score (weighted combination)
        avg_score = row[6] or 0
        avg_risk_adj = row[8] or 0
        avg_confidence = row[9] or 0
        profile_apps = row[4] or 0

        # Composite: risk-adjusted is primary, score and confidence secondary
        composite = (
            (avg_risk_adj * 0.5) +
            (avg_score * 0.3) +
            (avg_confidence * 10 * 0.2)  # scale confidence to similar range
        ) * (1 + min(profile_apps / 10, 0.5))  # boost for multi-profile appearances

        rankings.append(
            StockCompositeRanking(
                symbol=row[0],
                company=row[1],
                sector=row[2],
                industry=row[3],
                profile_appearances=row[4] or 0,
                run_appearances=row[5] or 0,
                avg_score_across_profiles=row[6],
                max_score_across_profiles=row[7],
                best_profile=row[8],
                avg_risk_adjusted_score=row[8],
                avg_confidence=row[9],
                avg_direction=row[10],
                consensus_long_count=row[11] or 0,
                consensus_short_count=row[12] or 0,
                composite_rank=composite,
                price_performance_proxy=None,
                strongest_predictor_profile=None,
            )
        )

    # Re-sort by composite rank and limit
    rankings.sort(key=lambda x: (x.composite_rank or 0), reverse=True)
    return rankings[:top_n]


def _discover_pattern_predictors(
    conn: Any,
    top_profiles: list[str] | None = None,
) -> list[PatternPredictorInsight]:
    """Discover which raw fields/components correlate with high scores/performance."""

    insights: list[PatternPredictorInsight] = []

    # Check if pool_raw_scan_rows has data
    has_raw_data = conn.execute("""
        SELECT COUNT(*) FROM pool_raw_scan_rows LIMIT 1
    """).fetchone()[0] > 0

    if not has_raw_data:
        return insights

    # Key predictive fields to analyze (from raw scan data)
    predictor_fields = [
        ("momentum", "momentum"),
        ("trend", "trend"),
        ("quality", "quality"),
        ("valuation", "valuation"),
        ("safety", "safety"),
        ("scale", "scale"),
        ("attention", "attention"),
        ("event", "event"),
        ("market_cap_basic", "scale"),
        ("volume", "attention"),
        ("Perf.W", "momentum"),
        ("Perf.1M", "momentum"),
        ("Perf.3M", "momentum"),
        ("RSI", "momentum"),
        ("MACD.macd", "trend"),
    ]

    # Get top performing profiles for analysis
    if top_profiles is None:
        profile_rows = conn.execute("""
            SELECT profile_name, AVG(risk_adjusted_score) as avg_score
            FROM pool_profile_horizon_scores
            WHERE horizon_name = 'weeks'
            GROUP BY profile_name
            ORDER BY avg_score DESC NULLS LAST
            LIMIT 5
        """).fetchall()
        top_profiles = [row[0] for row in profile_rows if row[0]]

    for profile in top_profiles:
        for field, category in predictor_fields:
            # Calculate correlation between this field and high scores
            try:
                corr_row = conn.execute(
                    f"""
                    SELECT
                        CORR(CAST(raw.{_quote_identifier(field)} AS DOUBLE), scores.score) as score_corr,
                        CORR(CAST(raw.{_quote_identifier(field)} AS DOUBLE), scores.direction) as direction_corr,
                        AVG(CASE WHEN raw.{_quote_identifier(field)} IS NOT NULL THEN 1.0 ELSE 0.0 END) as coverage
                    FROM pool_profile_horizon_scores scores
                    INNER JOIN pool_raw_scan_rows raw
                        ON scores.run_id = raw.run_id
                        AND scores.symbol = raw.symbol
                        OR raw.symbol LIKE '%:' || scores.symbol
                        OR scores.symbol LIKE '%:' || raw.symbol
                    WHERE scores.profile_name = ?
                        AND scores.horizon_name = 'weeks'
                    """,
                    [profile],
                ).fetchone()

                if corr_row and (corr_row[0] is not None or corr_row[1] is not None):
                    predictive_power = (
                        (abs(corr_row[0]) if corr_row[0] else 0) +
                        (abs(corr_row[1]) if corr_row[1] else 0)
                    ) / 2

                    insights.append(
                        PatternPredictorInsight(
                            profile_name=profile,
                            predictor_field=field,
                            predictor_category=category,
                            correlation_with_high_score=corr_row[0],
                            correlation_with_direction=corr_row[1],
                            predictive_power_score=predictive_power,
                            coverage_rate=corr_row[2],
                            sector_bias=None,
                            time_stability=None,
                        )
                    )
            except Exception:
                # Field might not exist in raw data
                continue

    # Sort by predictive power
    insights.sort(key=lambda x: (x.predictive_power_score or 0), reverse=True)
    return insights[:50]  # Return top 50 insights


def _find_score_price_intersections(
    conn: Any,
    score_threshold: float = 75.0,
    top_n: int = 50,
) -> tuple[list[str], list[str]]:
    """Find symbols that intersect between high scores and price performance."""

    # Get high-scoring symbols (winners in score)
    high_score_symbols = conn.execute(
        """
        SELECT DISTINCT symbol
        FROM pool_profile_horizon_scores
        WHERE risk_adjusted_score >= ?
            AND horizon_name = 'weeks'
        """,
        [score_threshold],
    ).fetchall()
    high_score_set = {row[0] for row in high_score_symbols if row[0]}

    # Try to find price performance indicators in raw data
    try:
        # Symbols with positive weekly/monthly performance
        price_performers = conn.execute("""
            SELECT DISTINCT symbol
            FROM pool_raw_scan_rows
            WHERE
                (CAST(Perf.W AS DOUBLE) > 0 OR CAST(Perf.1M AS DOUBLE) > 0)
                AND CAST(RSI AS DOUBLE) BETWEEN 50 AND 70
        """).fetchall()
        price_perf_set = {row[0] for row in price_performers if row[0]}

        # Find intersection (high score AND price performance)
        intersection = list(high_score_set & price_perf_set)[:top_n]

        # Find missing (high score but poor price performance - potential false positives)
        missing = list(high_score_set - price_perf_set)[:top_n]

        return intersection, missing
    except Exception:
        # Raw data might not have price perf fields
        return list(high_score_set)[:top_n], []


def analyze_pool_database(
    database_path: str | Path,
    output_dir: str | Path | None = None,
    min_score_threshold: float = DEFAULT_MIN_SCORE_THRESHOLD,
    top_n_profiles: int = DEFAULT_TOP_N_PROFILES,
    top_n_stocks: int = DEFAULT_TOP_N_STOCKS,
    verbose: bool = True,
) -> PoolAnalysisResult:
    """Run complete analysis on a pooled move prediction database.

    Args:
        database_path: Path to the pooled_move_prediction_runs.duckdb file
        output_dir: Directory for analysis output (default: auto-generated)
        min_score_threshold: Minimum score threshold for filtering
        top_n_profiles: Number of top profiles to analyze in detail
        top_n_stocks: Number of top stocks to include in rankings
        verbose: Print progress to stderr

    Returns:
        PoolAnalysisResult containing all analysis data
    """
    started = time.perf_counter()
    db_path = Path(database_path)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    output_dir = _ensure_output_dir(output_dir)

    if verbose:
        print(f"[pool-analyzer] Analyzing: {db_path.name}", file=sys.stderr)
        print(f"[pool-analyzer] Output: {output_dir}", file=sys.stderr)

    with open_move_prediction_duckdb_connection(db_path) as conn:
        # Get pool metadata
        summary = _get_pool_aggregation_summary(conn)
        pool_id = summary.get("pool_aggregation_id", "unknown")

        if verbose:
            print(
                f"[pool-analyzer] Pool ID: {pool_id}",
                file=sys.stderr,
            )
            print(
                f"[pool-analyzer] Sources: {summary.get('total_source_databases', 0)} databases, "
                f"{summary.get('total_weeks', 0)} weeks",
                file=sys.stderr,
            )

        # Get run inventory
        runs = _get_run_inventory(conn)
        total_runs = len(runs)

        if verbose:
            print(f"[pool-analyzer] Total runs: {total_runs}", file=sys.stderr)
            print("[pool-analyzer] Analyzing profile performance...", file=sys.stderr)

        # Analyze profile performance
        profile_metrics = _analyze_profile_performance(conn, min_score_threshold)

        if verbose:
            print(
                f"[pool-analyzer] Profile metrics: {len(profile_metrics)} profiles",
                file=sys.stderr,
            )
            print("[pool-analyzer] Ranking stocks...", file=sys.stderr)

        # Analyze stock rankings
        stock_rankings = _analyze_stock_composite_rankings(
            conn,
            top_n=top_n_stocks,
        )

        if verbose:
            print(
                f"[pool-analyzer] Stock rankings: {len(stock_rankings)} stocks",
                file=sys.stderr,
            )
            print("[pool-analyzer] Discovering patterns...", file=sys.stderr)

        # Discover pattern predictors
        top_profile_names = [p.profile_name for p in profile_metrics[:5]]
        pattern_insights = _discover_pattern_predictors(conn, top_profile_names)

        if verbose:
            print(
                f"[pool-analyzer] Pattern insights: {len(pattern_insights)} findings",
                file=sys.stderr,
            )
            print("[pool-analyzer] Finding intersections...", file=sys.stderr)

        # Find score/price intersections
        intersection, missing = _find_score_price_intersections(
            conn,
            score_threshold=min_score_threshold + 15,  # Higher threshold for intersection
        )

        elapsed = time.perf_counter() - started

        result = PoolAnalysisResult(
            pool_aggregation_id=pool_id,
            database_path=str(db_path),
            analysis_timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
            total_runs=total_runs,
            total_weeks=summary.get("total_weeks", 0),
            week_range=summary.get("week_range", (None, None)),
            profile_metrics=profile_metrics[:top_n_profiles],
            stock_rankings=stock_rankings,
            pattern_insights=pattern_insights,
            score_vs_price_correlation=None,
            top_performers_intersection=intersection,
            missing_performance_symbols=missing,
            execution_time_seconds=elapsed,
        )

        if verbose:
            print(
                f"[pool-analyzer] Analysis complete in {elapsed:.1f}s",
                file=sys.stderr,
            )

        return result


def export_analysis_reports(
    result: PoolAnalysisResult,
    output_dir: str | Path,
    export_json: bool = True,
    export_csv: bool = True,
    export_sql: bool = True,
) -> dict[str, Path]:
    """Export analysis results to multiple formats.

    Returns:
        Dictionary mapping report type to file path
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: dict[str, Path] = {}

    # JSON export (full data)
    if export_json:
        json_path = output_dir / "pool_analysis_full.json"

        def serialize(obj: Any) -> Any:
            if isinstance(obj, (ProfilePerformanceMetrics, StockCompositeRanking, PatternPredictorInsight)):
                return asdict(obj)
            if isinstance(obj, (datetime,)):
                return obj.isoformat()
            raise TypeError(f"Cannot serialize {type(obj)}")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "metadata": {
                        "pool_aggregation_id": result.pool_aggregation_id,
                        "database_path": result.database_path,
                        "analysis_timestamp_utc": result.analysis_timestamp_utc,
                        "total_runs": result.total_runs,
                        "total_weeks": result.total_weeks,
                        "week_range": result.week_range,
                        "execution_time_seconds": result.execution_time_seconds,
                    },
                    "profile_metrics": [asdict(m) for m in result.profile_metrics],
                    "stock_rankings": [asdict(r) for r in result.stock_rankings],
                    "pattern_insights": [asdict(i) for i in result.pattern_insights],
                    "top_performers_intersection": result.top_performers_intersection,
                    "missing_performance_symbols": result.missing_performance_symbols,
                },
                f,
                indent=2,
                default=serialize,
            )
        exported["json_full"] = json_path

    # CSV export - Profile Metrics
    if export_csv:
        profile_csv = output_dir / "profile_performance_metrics.csv"
        with open(profile_csv, "w", newline="", encoding="utf-8") as f:
            if result.profile_metrics:
                writer = csv.DictWriter(f, fieldnames=asdict(result.profile_metrics[0]).keys())
                writer.writeheader()
                for metric in result.profile_metrics:
                    writer.writerow(asdict(metric))
        exported["profile_csv"] = profile_csv

        # CSV export - Stock Rankings
        stock_csv = output_dir / "stock_composite_rankings.csv"
        with open(stock_csv, "w", newline="", encoding="utf-8") as f:
            if result.stock_rankings:
                writer = csv.DictWriter(f, fieldnames=asdict(result.stock_rankings[0]).keys())
                writer.writeheader()
                for ranking in result.stock_rankings:
                    writer.writerow(asdict(ranking))
        exported["stock_csv"] = stock_csv

        # CSV export - Pattern Insights
        pattern_csv = output_dir / "pattern_predictor_insights.csv"
        with open(pattern_csv, "w", newline="", encoding="utf-8") as f:
            if result.pattern_insights:
                writer = csv.DictWriter(f, fieldnames=asdict(result.pattern_insights[0]).keys())
                writer.writeheader()
                for insight in result.pattern_insights:
                    writer.writerow(asdict(insight))
        exported["pattern_csv"] = pattern_csv

    # SQL queries export
    if export_sql:
        sql_path = output_dir / "analysis_queries.sql"
        sql_content = _generate_analysis_queries_sql(result)
        sql_path.write_text(sql_content, encoding="utf-8")
        exported["sql_queries"] = sql_path

    return exported


def _generate_analysis_queries_sql(result: PoolAnalysisResult) -> str:
    """Generate SQL query templates for further analysis."""

    return f"""-- Auto-generated SQL queries for pool analysis
-- Pool: {result.pool_aggregation_id}
-- Generated: {result.analysis_timestamp_utc}

-- ============================================
-- 1. TOP PERFORMING PROFILES BY METRICS
-- ============================================

-- Top profiles by average risk-adjusted score
SELECT
    profile_name,
    AVG(risk_adjusted_score) as avg_risk_adj_score,
    AVG(score) as avg_score,
    AVG(confidence) as avg_confidence,
    COUNT(DISTINCT run_id) as runs_present,
    COUNT(DISTINCT symbol) as symbols_scored
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
GROUP BY profile_name
ORDER BY avg_risk_adj_score DESC NULLS LAST;

-- Profiles with highest bullish direction ratio
SELECT
    profile_name,
    AVG(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1.0 ELSE 0.0 END) as bullish_ratio,
    AVG(score) as avg_score
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
GROUP BY profile_name
ORDER BY bullish_ratio DESC NULLS LAST;

-- ============================================
-- 2. STOCK RANKINGS AND COMPOSITE SCORES
-- ============================================

-- Top stocks by composite ranking (multi-profile consensus)
WITH stock_stats AS (
    SELECT
        symbol,
        MAX(company) as company,
        MAX(sector) as sector,
        COUNT(DISTINCT profile_name) as profile_count,
        COUNT(DISTINCT run_id) as run_count,
        AVG(score) as avg_score,
        AVG(risk_adjusted_score) as avg_risk_adj,
        AVG(confidence) as avg_confidence,
        MAX(risk_adjusted_score) as max_risk_adj,
        SUM(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1 ELSE 0 END) as long_votes,
        SUM(CASE WHEN direction IN ('Strong Down', 'Down') THEN 1 ELSE 0 END) as short_votes
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND score IS NOT NULL
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 3
)
SELECT
    symbol,
    company,
    sector,
    profile_count,
    run_count,
    avg_score,
    avg_risk_adj,
    avg_confidence,
    long_votes,
    short_votes,
    (avg_risk_adj * 0.5 + avg_score * 0.3 + avg_confidence * 10 * 0.2)
        * (1 + LEAST(profile_count / 10, 0.5)) as composite_rank
FROM stock_stats
ORDER BY composite_rank DESC NULLS LAST
LIMIT 100;

-- Stocks with strongest multi-profile consensus (long)
SELECT
    symbol,
    MAX(company) as company,
    COUNT(DISTINCT profile_name) as bullish_profiles,
    COUNT(DISTINCT run_id) as runs_present,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    -- Which profiles like this stock
    STRING_AGG(DISTINCT profile_name, ', ' ORDER BY profile_name) as profiles
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND direction IN ('Strong Up', 'Up')
    AND score >= 0.35
    AND confidence >= 60
GROUP BY symbol
HAVING COUNT(DISTINCT profile_name) >= 3
ORDER BY bullish_profiles DESC, avg_risk_adj DESC NULLS LAST
LIMIT 50;

-- ============================================
-- 3. PATTERN PREDICTOR ANALYSIS
-- ============================================

-- Correlation between raw momentum fields and scores
WITH score_momentum AS (
    SELECT
        s.profile_name,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        s.direction,
        CAST(r.RSI AS DOUBLE) as rsi,
        CAST(r.Perf.W AS DOUBLE) as perf_w,
        CAST(r.Perf.1M AS DOUBLE) as perf_1m,
        CAST(r.momentum AS DOUBLE) as momentum_component
    FROM pool_profile_horizon_scores s
    INNER JOIN pool_raw_scan_rows r
        ON s.run_id = r.run_id
        AND (s.symbol = r.symbol
            OR r.symbol LIKE '%:' || s.symbol
            OR s.symbol LIKE '%:' || r.symbol)
    WHERE s.horizon_name = 'weeks'
)
SELECT
    profile_name,
    CORR(score, rsi) as score_rsi_corr,
    CORR(score, perf_w) as score_perf_w_corr,
    CORR(score, perf_1m) as score_perf_1m_corr,
    CORR(score, momentum_component) as score_momentum_corr,
    CORR(direction, rsi) as direction_rsi_corr,
    CORR(risk_adjusted_score, momentum_component) as riskadj_momentum_corr
FROM score_momentum
GROUP BY profile_name
HAVING CORR(score, momentum_component) IS NOT NULL
ORDER BY ABS(CORR(score, momentum_component)) DESC NULLS LAST;

-- Best performing sectors by profile
SELECT
    profile_name,
    sector,
    COUNT(*) as stock_count,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    STDDEV(score) as score_stddev
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND sector IS NOT NULL
GROUP BY profile_name, sector
HAVING COUNT(*) >= 10
ORDER BY profile_name, avg_risk_adj DESC NULLS LAST;

-- ============================================
-- 4. INTERSECTION AND MISSING ANALYSIS
-- ============================================

-- High scores but missing price performance (potential false positives)
WITH high_scorers AS (
    SELECT DISTINCT symbol, MAX(risk_adjusted_score) as max_score
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 1.10
    GROUP BY symbol
),
price_performers AS (
    SELECT DISTINCT symbol
    FROM pool_raw_scan_rows
    WHERE CAST(Perf.W AS DOUBLE) > 0
        AND CAST(RSI AS DOUBLE) BETWEEN 40 AND 70
)
SELECT
    h.symbol,
    h.max_score,
    'High score, check price action' as status
FROM high_scorers h
LEFT JOIN price_performers p ON h.symbol = p.symbol
WHERE p.symbol IS NULL
ORDER BY h.max_score DESC
LIMIT 50;

-- Intersection winners (high scores AND positive momentum)
WITH high_scorers AS (
    SELECT DISTINCT symbol, MAX(risk_adjusted_score) as max_score
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 75
    GROUP BY symbol
),
price_performers AS (
    SELECT DISTINCT symbol,
        CAST(Perf.W AS DOUBLE) as perf_w,
        CAST(RSI AS DOUBLE) as rsi
    FROM pool_raw_scan_rows
    WHERE CAST(Perf.W AS DOUBLE) > 2.0
        AND CAST(RSI AS DOUBLE) BETWEEN 50 AND 70
)
SELECT
    h.symbol,
    h.max_score,
    p.perf_w,
    p.rsi,
    'High score + Price momentum' as status
FROM high_scorers h
INNER JOIN price_performers p ON h.symbol = p.symbol
ORDER BY h.max_score DESC, p.perf_w DESC
LIMIT 50;

-- ============================================
-- 5. TEMPORAL ANALYSIS
-- ============================================

-- Profile performance consistency across weeks
SELECT
    profile_name,
    source_iso_week as week,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    COUNT(DISTINCT symbol) as symbols_scored,
    AVG(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1.0 ELSE 0.0 END) as bullish_pct
FROM pool_profile_horizon_scores
    JOIN pool_run_metadata USING (run_id, pool_aggregation_id, source_database_path)
WHERE horizon_name = 'weeks'
GROUP BY profile_name, source_iso_week
ORDER BY profile_name, source_iso_week;

-- Weekly consensus leaders
SELECT
    source_iso_week as week,
    symbol,
    MAX(company) as company,
    COUNT(DISTINCT profile_name) as profile_count,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    SUM(CASE WHEN direction IN ('Strong Up', 'Up') THEN 1 ELSE 0 END) as long_votes
FROM pool_profile_horizon_scores
    JOIN pool_run_metadata USING (run_id, pool_aggregation_id, source_database_path)
WHERE horizon_name = 'weeks'
GROUP BY source_iso_week, symbol
HAVING COUNT(DISTINCT profile_name) >= 4
ORDER BY week DESC, avg_risk_adj DESC NULLS LAST;

-- ============================================
-- 6. RAW FIELD PREDICTOR SCAN
-- ============================================

-- Scan which raw fields have predictive power for each profile
-- (Requires manual inspection of available fields)
SELECT
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'pool_raw_scan_rows'
    AND column_name NOT IN ('pool_aggregation_id', 'source_database_path',
        'source_iso_year', 'source_iso_week', 'run_id', 'row_number')
ORDER BY column_name;

-- Component analysis for a specific profile
SELECT
    c.profile_name,
    c.symbol,
    c.momentum,
    c.trend,
    c.quality,
    c.valuation,
    c.safety,
    c.scale,
    c.attention,
    c.event,
    s.score,
    s.risk_adjusted_score,
    s.direction
FROM pool_profile_components c
INNER JOIN pool_profile_horizon_scores s
    ON c.run_id = s.run_id
    AND c.profile_name = s.profile_name
    AND c.symbol = s.symbol
WHERE c.profile_name = 'breakout_long'  -- Change to desired profile
ORDER BY s.risk_adjusted_score DESC NULLS LAST
LIMIT 100;
"""


def run_pool_analysis_from_path(
    pool_id_pattern: str | None = None,
    database_path: str | Path | None = None,
    **analysis_kwargs: Any,
) -> PoolAnalysisResult:
    """Run analysis by finding the database automatically or by ID pattern.

    Args:
        pool_id_pattern: Partial or full pool aggregation ID to match
        database_path: Direct path to database (alternative to pool_id_pattern)
        **analysis_kwargs: Passed to analyze_pool_database()

    Returns:
        PoolAnalysisResult
    """
    if database_path is not None:
        return analyze_pool_database(database_path, **analysis_kwargs)

    if pool_id_pattern is None:
        raise ValueError("Provide either pool_id_pattern or database_path")

    # Search for matching database
    search_root = DEFAULT_POOL_ROOT
    matching_dbs = list(search_root.rglob("*/pooled_move_prediction_runs.duckdb"))

    for db_path in matching_dbs:
        # Check if the parent folder contains the pattern
        if pool_id_pattern in str(db_path):
            return analyze_pool_database(db_path, **analysis_kwargs)

    raise FileNotFoundError(
        f"No pool database found matching pattern: {pool_id_pattern}\n"
        f"Searched in: {search_root}"
    )


def _print_analysis_summary(result: PoolAnalysisResult, file: TextIO = sys.stdout) -> None:
    """Print a human-readable summary of the analysis."""
    print("=" * 70, file=file)
    print("MOVE PREDICTION POOL ANALYSIS SUMMARY", file=file)
    print("=" * 70, file=file)
    print(f"Pool ID: {result.pool_aggregation_id}", file=file)
    print(f"Database: {result.database_path}", file=file)
    print(f"Analysis Time: {result.analysis_timestamp_utc}", file=file)
    print(f"Total Runs: {result.total_runs}", file=file)
    print(f"Total Weeks: {result.total_weeks}", file=file)
    print(f"Execution Time: {result.execution_time_seconds:.1f}s", file=file)
    print(file=file)

    print("-" * 70, file=file)
    print("TOP PERFORMING PROFILES (by Risk-Adjusted Score)", file=file)
    print("-" * 70, file=file)
    print(
        f"{'Rank':<6}{'Profile':<30}{'Avg RiskAdj':<12}{'Avg Score':<12}{'High Conf %':<12}",
        file=file,
    )
    print("-" * 70, file=file)
    for i, profile in enumerate(result.profile_metrics[:10], 1):
        print(
            f"{i:<6}"
            f"{profile.profile_name:<30}"
            f"{profile.avg_risk_adjusted_score or 0:>11.2f}"
            f"{profile.avg_score or 0:>11.2f}"
            f"{(profile.high_confidence_ratio or 0) * 100:>11.1f}%",
            file=file,
        )
    print(file=file)

    print("-" * 70, file=file)
    print("TOP COMPOSITE STOCK RANKINGS", file=file)
    print("-" * 70, file=file)
    print(
        f"{'Rank':<6}{'Symbol':<10}{'Company':<25}{'Composite':<12}{'Profiles':<10}",
        file=file,
    )
    print("-" * 70, file=file)
    for i, stock in enumerate(result.stock_rankings[:15], 1):
        company = (stock.company or "")[:24]
        print(
            f"{i:<6}"
            f"{stock.symbol:<10}"
            f"{company:<25}"
            f"{stock.composite_rank or 0:>11.2f}"
            f"{stock.profile_appearances:>9}",
            file=file,
        )
    print(file=file)

    print("-" * 70, file=file)
    print("TOP PATTERN PREDICTORS", file=file)
    print("-" * 70, file=file)
    print(
        f"{'Profile':<25}{'Field':<20}{'Category':<12}{'Power':<10}{'Score Corr':<12}",
        file=file,
    )
    print("-" * 70, file=file)
    for i, insight in enumerate(result.pattern_insights[:10], 1):
        print(
            f"{insight.profile_name:<25}"
            f"{insight.predictor_field:<20}"
            f"{insight.predictor_category:<12}"
            f"{insight.predictive_power_score or 0:>9.3f}"
            f"{insight.correlation_with_high_score or 0:>11.3f}",
            file=file,
        )
    print(file=file)

    print("-" * 70, file=file)
    print("SCORE/PRICE INTERSECTION ANALYSIS", file=file)
    print("-" * 70, file=file)
    print(f"High Score + Price Performance: {len(result.top_performers_intersection)} symbols", file=file)
    if result.top_performers_intersection:
        print(f"  Top 10: {', '.join(result.top_performers_intersection[:10])}", file=file)
    print(f"High Score Only (check price): {len(result.missing_performance_symbols)} symbols", file=file)
    if result.missing_performance_symbols:
        print(f"  Sample: {', '.join(result.missing_performance_symbols[:10])}", file=file)
    print(file=file)

    print("=" * 70, file=file)


def export_profile_threshold_calibration(
    database_path: str | Path,
    profile_name: str = "breakout_long",
    horizon_name: str = "weeks",
    output_path: str | Path | None = None,
) -> Path:
    """Export threshold ladder calibration for a profile (SQL q_pool_profile_strongest_score_price_corr port).

    Analyzes score-price correlation at different threshold slices:
    - ALL (baseline)
    - BULLISH_UP (>= 0.35)
    - STRONG_UP (>= 1.10)
    - VERY_HIGH (>= 1.50)
    - ELITE (>= 2.00)

    Returns path to exported JSON with recommended thresholds.
    """
    from pathlib import Path
    import json

    db_path = Path(database_path)
    if output_path is None:
        output_path = db_path.parent / f"{profile_name}_{horizon_name}_threshold_calibration.json"
    else:
        output_path = Path(output_path)

    conn = open_move_prediction_duckdb_connection(db_path, read_only=True)
    try:
        # Threshold slices matching SQL logic
        slices = [
            ("ALL", None, 0),
            ("BULLISH_UP", 0.35, 1),
            ("STRONG_UP", 1.10, 2),
            ("VERY_HIGH", 1.50, 3),
            ("ELITE", 2.00, 4),
        ]

        results = []
        for slice_name, min_score, slice_order in slices:
            # Build score filter
            score_filter = "AND score IS NOT NULL" if min_score is None else f"AND score >= {min_score}"
            min_pairs = 50 if slice_name == "ALL" else (10 if slice_name == "ELITE" else 20)

            query = f"""
                SELECT
                    COUNT(*) as pair_count,
                    AVG(score) as avg_score,
                    AVG(risk_adjusted_score) as avg_ras,
                    AVG(TRY_CAST(r."Perf.W" AS DOUBLE)) as avg_perf_w,
                    CORR(score, TRY_CAST(r."Perf.W" AS DOUBLE)) as score_vs_perf_w_corr,
                    CORR(risk_adjusted_score, TRY_CAST(r."Perf.W" AS DOUBLE)) as ras_vs_perf_w_corr,
                    AVG(CASE WHEN direction IN ('Strong Up', 'Up') AND TRY_CAST(r."Perf.W" AS DOUBLE) > 0 THEN 1.0 ELSE 0.0 END) as bullish_hit_rate
                FROM pool_profile_horizon_scores s
                JOIN pool_raw_scan_rows r
                    ON s.pool_aggregation_id = r.pool_aggregation_id
                    AND s.source_database_path = r.source_database_path
                    AND s.run_id = r.run_id
                    AND (s.symbol = r.symbol OR r.symbol LIKE '%:' || s.symbol OR s.symbol LIKE '%:' || r.symbol)
                WHERE s.horizon_name = '{horizon_name}'
                    AND s.profile_name = '{profile_name}'
                    {score_filter}
            """

            row = conn.execute(query).fetchone()
            if row is None or row[0] < min_pairs:
                continue

            pair_count, avg_score, avg_ras, avg_perf_w, score_corr, ras_corr, hit_rate = row

            results.append(
                {
                    "slice": slice_name,
                    "slice_order": slice_order,
                    "min_score": min_score,
                    "pair_count": pair_count,
                    "avg_score": avg_score,
                    "avg_ras": avg_ras,
                    "avg_perf_w_pct": avg_perf_w,
                    "score_vs_perf_w_corr": score_corr,
                    "ras_vs_perf_w_corr": ras_corr,
                    "bullish_hit_rate": hit_rate,
                }
            )

        # Calculate lift vs baseline (ALL slice)
        baseline = next((r for r in results if r["slice"] == "ALL"), None)
        if baseline:
            for r in results:
                if r["slice"] != "ALL":
                    r["score_perf_w_lift_vs_all"] = (r["score_vs_perf_w_corr"] or 0) - (
                        baseline["score_vs_perf_w_corr"] or 0
                    )
                    r["bullish_hit_lift_vs_all"] = (r["bullish_hit_rate"] or 0) - (
                        baseline["bullish_hit_rate"] or 0
                    )

        # Recommended threshold based on hit rate improvement
        recommended = None
        for r in sorted(results, key=lambda x: x.get("bullish_hit_lift_vs_all", 0), reverse=True):
            if r["slice"] != "ALL" and r["pair_count"] >= 20:
                recommended = r["slice"]
                break

        output = {
            "profile_name": profile_name,
            "horizon_name": horizon_name,
            "database_path": str(db_path),
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "recommended_threshold_slice": recommended,
            "threshold_slices": results,
            "methodology": (
                "Threshold ladder analysis based on q_pool_profile_strongest_score_price_corr. "
                "Compares score-to-price correlation and bullish hit rate at different score cutoffs. "
                "Recommended threshold = slice with highest hit-rate lift vs baseline (ALL), "
                "requiring minimum 20 pairs for statistical relevance."
            ),
        }

        with open(output_path, "w") as f:
            json.dump(output, f, indent=2, default=str)

        return output_path

    finally:
        conn.close()


def main() -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze move prediction pool database for profile and stock performance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              # Analyze by database path
              python -m data_analysis_scripts.trading_view_move_prediction_pool_analyzer \\
                --database-path "path/to/pooled_move_prediction_runs.duckdb"

              # Analyze by pool ID pattern
              python -m data_analysis_scripts.trading_view_move_prediction_pool_analyzer \\
                --pool-id "move_prediction_run_pool_20260610"

              # With custom thresholds
              python -m data_analysis_scripts.trading_view_move_prediction_pool_analyzer \\
                --database-path "path/to/db.duckdb" \\
                --min-score 65 \\
                --top-n-profiles 10 \\
                --top-n-stocks 200
        """),
    )

    parser.add_argument(
        "--database-path",
        type=str,
        help="Path to pooled_move_prediction_runs.duckdb file",
    )
    parser.add_argument(
        "--pool-id",
        type=str,
        help="Pool aggregation ID pattern to search for (e.g., move_prediction_run_pool_20260610)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output directory for analysis reports (default: auto-generated)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE_THRESHOLD,
        help=f"Minimum score threshold (default: {DEFAULT_MIN_SCORE_THRESHOLD})",
    )
    parser.add_argument(
        "--top-n-profiles",
        type=int,
        default=DEFAULT_TOP_N_PROFILES,
        help=f"Number of top profiles to analyze (default: {DEFAULT_TOP_N_PROFILES})",
    )
    parser.add_argument(
        "--top-n-stocks",
        type=int,
        default=DEFAULT_TOP_N_STOCKS,
        help=f"Number of top stocks to rank (default: {DEFAULT_TOP_N_STOCKS})",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip JSON export",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip CSV export",
    )
    parser.add_argument(
        "--no-sql",
        action="store_true",
        help="Skip SQL queries export",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    try:
        # Run analysis
        if args.database_path:
            result = analyze_pool_database(
                database_path=args.database_path,
                output_dir=args.output_dir,
                min_score_threshold=args.min_score,
                top_n_profiles=args.top_n_profiles,
                top_n_stocks=args.top_n_stocks,
                verbose=not args.quiet,
            )
        elif args.pool_id:
            result = run_pool_analysis_from_path(
                pool_id_pattern=args.pool_id,
                output_dir=args.output_dir,
                min_score_threshold=args.min_score,
                top_n_profiles=args.top_n_profiles,
                top_n_stocks=args.top_n_stocks,
                verbose=not args.quiet,
            )
        else:
            parser.error("Provide either --database-path or --pool-id")
            return 1

        # Export results
        output_dir = _ensure_output_dir(args.output_dir)
        exported = export_analysis_reports(
            result,
            output_dir,
            export_json=not args.no_json,
            export_csv=not args.no_csv,
            export_sql=not args.no_sql,
        )

        # Print summary
        if not args.quiet:
            _print_analysis_summary(result)
            print(f"\nExported reports to: {output_dir}")
            for report_type, path in exported.items():
                print(f"  - {report_type}: {path.name}")

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error during analysis: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
