from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .region_filters import (
    build_region_filter_sql,
    expand_us_aliases,
    normalize_market_list,
    normalize_string_list,
    resolve_snapshot_market_filter,
    snapshot_market_select_sql,
)

DEFAULT_LOOKBACK_TRADING_DAYS = 20
DEFAULT_PERSISTENCE_WINDOW_DAYS = 10
DEFAULT_PERSISTENCE_THRESHOLD = 0.65
DEFAULT_HOT_LIQUIDITY_PCT_CEILING = 0.55
DEFAULT_HOT_VOLATILITY_PCT_CEILING = 0.55
DEFAULT_INDUSTRY_MIN_SYMBOL_COUNT = 5
DEFAULT_MIN_HISTORICAL_OCCURRENCE_COUNT = 3

BLINDSPOT_SYMBOL_COLUMNS: tuple[str, ...] = (
    "symbol",
    "bare_ticker",
    "company_name",
    "market",
    "exchange",
    "country",
    "sector",
    "industry",
    "source_day_label",
    "source_date",
    "close_price",
    "market_cap_basic",
    "perf_5d",
    "perf_1m",
    "perf_3m",
    "trend_persistence_score",
    "trend_persistence_score_prior",
    "perf_1m_trailing_avg",
    "perf_1m_trailing_avg_prior",
    "perf_1m_trend_delta",
    "avg_volume_recent",
    "avg_volume_recent_prior",
    "volume_base_trend_pct",
    "avg_value_traded_recent",
    "avg_value_traded_recent_prior",
    "value_traded_trend_pct",
    "volatility_core_pct_today",
    "liquidity_core_pct_today",
    "currently_quant_hot_flag",
    "in_shortlist_flag",
    "blindspot_hist_occurrence_count",
    "blindspot_hist_median_fwd_pct",
    "blindspot_hist_win_rate",
    "quiet_mover_score",
    "blindspot_flag",
    "quiet_mover_rank",
)

BLINDSPOT_INDUSTRY_COLUMNS: tuple[str, ...] = (
    "industry",
    "sector",
    "source_date",
    "industry_symbol_count",
    "industry_median_perf_1m",
    "industry_breadth_positive_1m",
    "industry_breadth_prior",
    "industry_breadth_delta",
    "industry_relative_strength_percentile",
    "industry_relative_strength_prior",
    "industry_rotation_delta",
    "industry_avg_volume",
    "industry_avg_volume_prior",
    "industry_volume_trend_pct",
    "blindspot_hist_occurrence_count",
    "blindspot_hist_median_fwd_pct",
    "blindspot_hist_win_rate",
    "industry_rotation_score",
    "industry_rotation_rank",
    "top_quiet_movers",
)


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_exists(connection: Any, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchone()[0]
    )


def _fetch_dict_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN check without importing math
        return default
    return parsed


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float | None) -> float:
    if value is None:
        return 0.5
    return max(0.0, min(1.0, value))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _discover_forward_label_horizons(connection: Any) -> tuple[int, ...]:
    pattern = re.compile(r"^forward_return_(\d+)d_pct$")
    columns = [
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info('symbol_day_forward_labels')"
        ).fetchall()
    ]
    return tuple(
        sorted(
            {int(match.group(1)) for col in columns if (match := pattern.match(col))}
        )
    )


def _resolve_latest_snapshot_database(output_root: str | Path | None) -> Path:
    paths = resolve_edge_research_paths(output_root=output_root)
    candidates = sorted(
        paths.foundation_root.glob(
            "edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No edge_feature_snapshot_* database was found under the edge research "
            "foundations directory."
        )
    return candidates[0]


def _write_blindspot_lane_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    as_of_date: str,
    lookback_trading_days: int,
    persistence_window_days: int,
    persistence_threshold: float,
    hot_liquidity_pct_ceiling: float,
    hot_volatility_pct_ceiling: float,
    industry_min_symbol_count: int,
    ranking_horizon: int,
    resolved_forward_horizon: int | None,
    universe_row_count: int,
    industry_row_count: int,
    symbol_candidate_count: int,
    blindspot_flag_count: int,
    industry_csv: Path,
    symbol_csv: Path,
    symbol_top_csv: Path,
    database_path: Path,
) -> None:
    lines = [
        "# Edge Blindspot Lane",
        "",
        "## Why This Section Exists",
        "",
        "The rest of the edge research suite (highlights / upside prediction / forward",
        "valuation / tradeable safety) is gated by *today's* volatility-liquidity state:",
        "a name only shows up once ADRP, relative volume, and value traded are already",
        "elevated. That is deliberate for the core lenses, but it structurally cannot see",
        "a rotation while it is still building -- for example a lane like Airlines or",
        "Medical Care / Nursing Services grinding higher for weeks on rising baseline",
        "volume before any single day looks statistically 'hot'.",
        "",
        "The blindspot lane removes that gate. It runs a DuckDB scan across the *entire*",
        "loaded snapshot history (not just the scan day) for the full region-filtered",
        "universe, and flags industries and individual tickers with persistent,",
        "multi-week trend/volume build-up and improving relative strength -- whether or",
        "not they are currently on the quantitative radar.",
        "",
        "## Method",
        "",
        "1. **Trend persistence (symbol level)**: rolling fraction of the trailing "
        f"{persistence_window_days} snapshot days where `perf_1m > 0`, computed with a "
        "DuckDB window function over the full symbol history.",
        "2. **Volume/value base build (symbol level)**: trailing-window average of "
        "`average_volume_30d_calc` and `value_traded`, compared against the same "
        f"trailing average from {lookback_trading_days} snapshot rows earlier "
        "(roughly that many trading days back, subject to data-capture gaps).",
        "3. **Industry rotation (lane level)**: each day, every industry's median "
        "`perf_1m` is percentile-ranked against all other industries that day. "
        f"`industry_rotation_delta` compares that percentile now versus "
        f"{lookback_trading_days} snapshot rows earlier -- a large positive delta is a "
        "lane moving from laggard to leader, which is exactly the Airlines / Medical "
        "Care rotation pattern this lane is designed to catch.",
        "4. **Currently-hot cross-check**: each symbol's *today* volatility_core / "
        "liquidity_core directional percentile (from `symbol_day_sleeve_scores`) is "
        f"compared against ceilings of {hot_liquidity_pct_ceiling:.2f} / "
        f"{hot_volatility_pct_ceiling:.2f}. Names below both ceilings are 'not "
        "currently quant-hot' -- the actual blind spot.",
        "5. **Historical validation**: across *all* historical snapshot days (not just "
        "today), the same 'persistent trend, not quant-hot' pattern is located per "
        "symbol and per industry, then joined to `symbol_day_forward_labels` to report "
        "the historical occurrence count, median forward return, and win rate that "
        "followed. This is the backward check that the pattern is a quantifiable, "
        "repeatable opportunity rather than a narrative.",
        "",
        "`quiet_mover_score` blends trend persistence, volume/value trend, medium-term "
        "performance acceleration, and the historical win rate (when enough history "
        "exists) into a single 0-1 ranking score. `blindspot_flag = 1` marks rows that "
        "clear the persistence threshold while remaining below the hot ceilings today.",
        "",
        "## How To Use This For Sector Research",
        "",
        "- Open `edge_blindspot_industry_rotation.csv` and sort by "
        "`industry_rotation_score` or `industry_rotation_delta` to see which lanes are "
        "rotating into favor right now.",
        "- Cross-reference `top_quiet_movers` on a lane of interest, then look up those "
        "tickers in `edge_blindspot_symbol_candidates.csv` for the underlying trend, "
        "volume, and historical-validation detail.",
        "- Filter `edge_blindspot_symbol_candidates.csv` on `blindspot_flag = 1` and "
        "`in_shortlist_flag = 0` for the purest 'moving but not on the radar yet' list.",
        "- `blindspot_hist_occurrence_count` below "
        f"{DEFAULT_MIN_HISTORICAL_OCCURRENCE_COUNT} means the historical validation "
        "sample is thin -- treat `blindspot_hist_median_fwd_pct` / "
        "`blindspot_hist_win_rate` as informative context only, not a standalone signal.",
        "",
        "## Scope",
        "",
        f"- snapshot database: `{snapshot_database_path.as_posix()}`",
        f"- as_of_date: {as_of_date}",
        f"- lookback_trading_days (snapshot rows): {lookback_trading_days}",
        f"- persistence_window_days (snapshot rows): {persistence_window_days}",
        f"- persistence_threshold: {persistence_threshold:.2f}",
        f"- hot_liquidity_pct_ceiling: {hot_liquidity_pct_ceiling:.2f}",
        f"- hot_volatility_pct_ceiling: {hot_volatility_pct_ceiling:.2f}",
        f"- industry_min_symbol_count: {industry_min_symbol_count}",
        f"- ranking_horizon requested: {ranking_horizon}d "
        f"(historical validation horizon used: "
        f"{resolved_forward_horizon if resolved_forward_horizon is not None else 'n/a - no forward labels available'})",
        "",
        "## Output Files",
        "",
        f"- universe rows scanned: {universe_row_count}",
        f"- industry rows: {industry_row_count} -> `{industry_csv.as_posix()}`",
        f"- symbol candidate rows: {symbol_candidate_count} -> `{symbol_csv.as_posix()}`",
        f"- symbol blindspot top rows: {blindspot_flag_count} -> `{symbol_top_csv.as_posix()}`",
        f"- duckdb bundle: `{database_path.as_posix()}`",
        "",
        "## Known Limitations",
        "",
        "- `lookback_trading_days` counts snapshot *rows* per symbol/industry, not "
        "calendar trading days -- data-capture gaps will shift the comparison window "
        "slightly.",
        "- Industry relative-strength percentiles depend on which industries have "
        "sufficient coverage on a given day; thin-coverage days add noise.",
        "- This lane is a screening aid, not a valuation or safety check -- always "
        "cross-reference candidates against `edge_unified_highlights` and the safety "
        "universe before acting.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_edge_blindspot_lane(
    *,
    snapshot_database_path: str | Path | None = None,
    output_root: str | Path | None = None,
    as_of_date: str | None = None,
    ranking_horizon: int = 5,
    shortlist_symbols: Iterable[str] | None = None,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    min_market_cap_usd: float | None = None,
    lookback_trading_days: int = DEFAULT_LOOKBACK_TRADING_DAYS,
    persistence_window_days: int = DEFAULT_PERSISTENCE_WINDOW_DAYS,
    persistence_threshold: float = DEFAULT_PERSISTENCE_THRESHOLD,
    hot_liquidity_pct_ceiling: float = DEFAULT_HOT_LIQUIDITY_PCT_CEILING,
    hot_volatility_pct_ceiling: float = DEFAULT_HOT_VOLATILITY_PCT_CEILING,
    industry_min_symbol_count: int = DEFAULT_INDUSTRY_MIN_SYMBOL_COUNT,
    top_count: int = 40,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    """Backward-scan DuckDB pass that finds persistent/rotating movers outside the

    current volatility-liquidity 'hot' gate used by the rest of the edge research
    suite (highlights / upside prediction / forward valuation / tradeable safety).
    """
    resolved_snapshot_database_path = (
        Path(snapshot_database_path)
        if snapshot_database_path is not None
        else _resolve_latest_snapshot_database(output_root)
    )
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )

    resolved_lookback = max(1, int(lookback_trading_days))
    resolved_persistence_window = max(1, int(persistence_window_days))
    resolved_industry_min_count = max(1, int(industry_min_symbol_count))
    resolved_top_count = max(1, int(top_count))

    resolved_countries = expand_us_aliases(
        normalize_string_list(countries), bool(us_only)
    )
    resolved_exchanges = normalize_string_list(exchanges)
    resolved_markets = normalize_market_list(markets)

    shortlist_symbol_set = {
        str(symbol).strip() for symbol in (shortlist_symbols or []) if str(symbol).strip()
    }

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="edge_blindspot_lane",
        output_root=output_paths.output_root,
    )
    industry_csv = context.output_dir / "edge_blindspot_industry_rotation.csv"
    symbol_csv = context.output_dir / "edge_blindspot_symbol_candidates.csv"
    symbol_top_csv = (
        context.output_dir / f"edge_blindspot_symbol_top{resolved_top_count}.csv"
    )
    database_path = context.output_dir / "edge_blindspot_lane.duckdb"
    report_md = context.output_dir / "edge_blindspot_lane_report.md"
    manifest_path = context.output_dir / "edge_blindspot_lane_manifest.json"

    duckdb = _import_duckdb()
    conn = duckdb.connect(resolved_snapshot_database_path.as_posix(), read_only=False)
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        for table_name in (
            "symbol_day_feature_snapshot",
            "symbol_day_sleeve_scores",
        ):
            if not _table_exists(conn, table_name):
                raise ValueError(
                    f"Required table '{table_name}' not found. Run the feature "
                    "snapshot build first."
                )
        has_forward_labels = _table_exists(conn, "symbol_day_forward_labels")

        market_select_sql = snapshot_market_select_sql("snap", conn)
        region_filter = build_region_filter_sql(
            country_expr="snap.country",
            exchange_expr="snap.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter, _market_filter_applied = resolve_snapshot_market_filter(
            conn, alias="snap", markets=resolved_markets
        )

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_region_filtered AS
            SELECT
                snap.symbol, snap.bare_ticker, snap.company_name,
                {market_select_sql}, snap.exchange, snap.country,
                snap.sector, snap.industry,
                snap.source_day_label, snap.source_date,
                snap.close_price, snap.perf_5d, snap.perf_1m, snap.perf_3m,
                snap.volume, snap.average_volume_30d_calc,
                snap.relative_volume_10d_calc, snap.value_traded,
                snap.market_cap_basic
            FROM symbol_day_feature_snapshot AS snap
            WHERE {region_filter} AND {market_filter}
            """)

        resolved_as_of_date = (
            _q(as_of_date)
            if as_of_date
            else "(SELECT MAX(source_date) FROM bl_region_filtered)"
        )
        min_cap_having = (
            f"AND TRY_CAST(market_cap_basic AS DOUBLE) >= {float(min_market_cap_usd)}"
            if min_market_cap_usd is not None
            else ""
        )
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_eligible_symbols AS
            SELECT DISTINCT symbol
            FROM bl_region_filtered
            WHERE source_date = {resolved_as_of_date}::DATE
            {min_cap_having}
            """)
        eligible_count = int(
            conn.execute("SELECT COUNT(*) FROM bl_eligible_symbols").fetchone()[0]
        )
        if eligible_count <= 0:
            raise ValueError(
                "No symbols matched the requested region/market-cap filters as of "
                f"{as_of_date or '(latest available date)'}."
            )

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE bl_daily AS
            SELECT rf.*
            FROM bl_region_filtered AS rf
            JOIN bl_eligible_symbols AS es USING (symbol)
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_windowed AS
            SELECT
                d.*,
                AVG(CASE WHEN perf_1m > 0 THEN 1.0 ELSE 0.0 END) OVER (
                    PARTITION BY symbol ORDER BY source_date
                    ROWS BETWEEN {resolved_persistence_window - 1} PRECEDING AND CURRENT ROW
                ) AS trend_persistence_score,
                AVG(perf_1m) OVER (
                    PARTITION BY symbol ORDER BY source_date
                    ROWS BETWEEN {resolved_persistence_window - 1} PRECEDING AND CURRENT ROW
                ) AS perf_1m_trailing_avg,
                AVG(average_volume_30d_calc) OVER (
                    PARTITION BY symbol ORDER BY source_date
                    ROWS BETWEEN {resolved_persistence_window - 1} PRECEDING AND CURRENT ROW
                ) AS avg_volume_recent,
                AVG(value_traded) OVER (
                    PARTITION BY symbol ORDER BY source_date
                    ROWS BETWEEN {resolved_persistence_window - 1} PRECEDING AND CURRENT ROW
                ) AS avg_value_traded_recent,
                COUNT(*) OVER (
                    PARTITION BY symbol ORDER BY source_date
                    ROWS BETWEEN {resolved_persistence_window - 1} PRECEDING AND CURRENT ROW
                ) AS trailing_obs_count
            FROM bl_daily AS d
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_symbol_with_prior AS
            SELECT
                w.*,
                LAG(trend_persistence_score, {resolved_lookback}) OVER (
                    PARTITION BY symbol ORDER BY source_date
                ) AS trend_persistence_score_prior,
                LAG(perf_1m_trailing_avg, {resolved_lookback}) OVER (
                    PARTITION BY symbol ORDER BY source_date
                ) AS perf_1m_trailing_avg_prior,
                LAG(avg_volume_recent, {resolved_lookback}) OVER (
                    PARTITION BY symbol ORDER BY source_date
                ) AS avg_volume_recent_prior,
                LAG(avg_value_traded_recent, {resolved_lookback}) OVER (
                    PARTITION BY symbol ORDER BY source_date
                ) AS avg_value_traded_recent_prior,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol ORDER BY source_date DESC
                ) AS recency_rank
            FROM bl_windowed AS w
            """)

        symbol_latest_rows = _fetch_dict_rows(
            conn,
            f"""
            SELECT *
            FROM bl_symbol_with_prior
            WHERE recency_rank = 1 AND trailing_obs_count >= {resolved_persistence_window}
            """,
        )

        hot_today_rows = _fetch_dict_rows(
            conn,
            """
            SELECT symbol,
                MAX(CASE WHEN sleeve_name = 'volatility_core'
                    THEN avg_directional_universe_percentile END) AS volatility_core_pct_today,
                MAX(CASE WHEN sleeve_name = 'liquidity_core'
                    THEN avg_directional_universe_percentile END) AS liquidity_core_pct_today
            FROM symbol_day_sleeve_scores
            WHERE source_date = (SELECT MAX(source_date) FROM bl_daily)
            GROUP BY symbol
            """,
        )
        hot_today_by_symbol = {
            str(row.get("symbol")): row for row in hot_today_rows if row.get("symbol")
        }

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_industry_daily AS
            SELECT
                industry,
                MAX(sector) AS sector,
                source_date,
                MEDIAN(perf_1m) AS industry_median_perf_1m,
                AVG(CASE WHEN perf_1m > 0 THEN 1.0 ELSE 0.0 END) AS industry_breadth_positive_1m,
                COUNT(*) AS industry_symbol_count,
                AVG(average_volume_30d_calc) AS industry_avg_volume
            FROM bl_daily
            WHERE industry IS NOT NULL AND industry <> ''
            GROUP BY industry, source_date
            """)
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE bl_industry_ranked AS
            SELECT *,
                PERCENT_RANK() OVER (
                    PARTITION BY source_date ORDER BY industry_median_perf_1m
                ) AS industry_relative_strength_percentile
            FROM bl_industry_daily
            """)
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE bl_industry_with_prior AS
            SELECT
                *,
                LAG(industry_relative_strength_percentile, {resolved_lookback}) OVER (
                    PARTITION BY industry ORDER BY source_date
                ) AS industry_relative_strength_prior,
                LAG(industry_breadth_positive_1m, {resolved_lookback}) OVER (
                    PARTITION BY industry ORDER BY source_date
                ) AS industry_breadth_prior,
                LAG(industry_avg_volume, {resolved_lookback}) OVER (
                    PARTITION BY industry ORDER BY source_date
                ) AS industry_avg_volume_prior,
                ROW_NUMBER() OVER (
                    PARTITION BY industry ORDER BY source_date DESC
                ) AS recency_rank
            FROM bl_industry_ranked
            """)
        industry_latest_rows = _fetch_dict_rows(
            conn,
            f"""
            SELECT *
            FROM bl_industry_with_prior
            WHERE recency_rank = 1 AND industry_symbol_count >= {resolved_industry_min_count}
            """,
        )

        symbol_hist_by_symbol: dict[str, dict[str, Any]] = {}
        industry_hist_by_industry: dict[str, dict[str, Any]] = {}
        resolved_forward_horizon: int | None = None
        if has_forward_labels:
            available_horizons = _discover_forward_label_horizons(conn)
            if available_horizons:
                resolved_forward_horizon = (
                    int(ranking_horizon)
                    if int(ranking_horizon) in available_horizons
                    else max(available_horizons)
                )
                fwd_col = f"forward_return_{resolved_forward_horizon}d_pct"
                conn.execute(f"""
                    CREATE OR REPLACE TEMP TABLE bl_sleeve_daily AS
                    SELECT source_day_label, symbol,
                        MAX(CASE WHEN sleeve_name = 'volatility_core'
                            THEN avg_directional_universe_percentile END) AS volatility_core_pct,
                        MAX(CASE WHEN sleeve_name = 'liquidity_core'
                            THEN avg_directional_universe_percentile END) AS liquidity_core_pct
                    FROM symbol_day_sleeve_scores
                    GROUP BY source_day_label, symbol
                    """)
                conn.execute(f"""
                    CREATE OR REPLACE TEMP TABLE bl_quiet_days AS
                    SELECT w.symbol, w.industry, w.source_day_label
                    FROM bl_windowed AS w
                    LEFT JOIN bl_sleeve_daily AS sd
                        ON sd.source_day_label = w.source_day_label
                        AND sd.symbol = w.symbol
                    WHERE w.trend_persistence_score >= {float(persistence_threshold)}
                      AND w.trailing_obs_count >= {resolved_persistence_window}
                      AND COALESCE(sd.liquidity_core_pct, 0) < {float(hot_liquidity_pct_ceiling)}
                      AND COALESCE(sd.volatility_core_pct, 0) < {float(hot_volatility_pct_ceiling)}
                    """)
                conn.execute(f"""
                    CREATE OR REPLACE TEMP TABLE bl_quiet_days_labeled AS
                    SELECT qd.symbol, qd.industry, qd.source_day_label,
                        lbl.{fwd_col} AS fwd_return
                    FROM bl_quiet_days AS qd
                    JOIN symbol_day_forward_labels AS lbl
                        ON lbl.source_day_label = qd.source_day_label
                        AND lbl.symbol = qd.symbol
                    WHERE lbl.{fwd_col} IS NOT NULL
                    """)
                symbol_hist_rows = _fetch_dict_rows(
                    conn,
                    """
                    SELECT symbol,
                        COUNT(*) AS blindspot_hist_occurrence_count,
                        MEDIAN(fwd_return) AS blindspot_hist_median_fwd_pct,
                        AVG(CASE WHEN fwd_return > 0 THEN 1.0 ELSE 0.0 END)
                            AS blindspot_hist_win_rate
                    FROM bl_quiet_days_labeled
                    GROUP BY symbol
                    """,
                )
                symbol_hist_by_symbol = {
                    str(row.get("symbol")): row
                    for row in symbol_hist_rows
                    if row.get("symbol")
                }
                industry_hist_rows = _fetch_dict_rows(
                    conn,
                    """
                    SELECT industry,
                        COUNT(*) AS blindspot_hist_occurrence_count,
                        MEDIAN(fwd_return) AS blindspot_hist_median_fwd_pct,
                        AVG(CASE WHEN fwd_return > 0 THEN 1.0 ELSE 0.0 END)
                            AS blindspot_hist_win_rate
                    FROM bl_quiet_days_labeled
                    WHERE industry IS NOT NULL AND industry <> ''
                    GROUP BY industry
                    """,
                )
                industry_hist_by_industry = {
                    str(row.get("industry")): row
                    for row in industry_hist_rows
                    if row.get("industry")
                }

        universe_row_count = int(
            conn.execute("SELECT COUNT(*) FROM bl_daily").fetchone()[0]
        )
    finally:
        conn.close()

    symbol_rows: list[dict[str, Any]] = []
    for row in symbol_latest_rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        hot_row = hot_today_by_symbol.get(symbol) or {}
        hist_row = symbol_hist_by_symbol.get(symbol) or {}

        trend_persistence_score = _clamp01(_safe_float(row.get("trend_persistence_score")))
        perf_1m_trailing_avg = _safe_float(row.get("perf_1m_trailing_avg"), None)
        perf_1m_trailing_avg_prior = _safe_float(
            row.get("perf_1m_trailing_avg_prior"), None
        )
        perf_1m_trend_delta = (
            perf_1m_trailing_avg - perf_1m_trailing_avg_prior
            if perf_1m_trailing_avg is not None and perf_1m_trailing_avg_prior is not None
            else None
        )
        avg_volume_recent = _safe_float(row.get("avg_volume_recent"), None)
        avg_volume_recent_prior = _safe_float(row.get("avg_volume_recent_prior"), None)
        volume_base_trend_pct = (
            (avg_volume_recent - avg_volume_recent_prior)
            / avg_volume_recent_prior
            * 100.0
            if avg_volume_recent is not None
            and avg_volume_recent_prior
            and avg_volume_recent_prior > 0
            else None
        )
        avg_value_traded_recent = _safe_float(row.get("avg_value_traded_recent"), None)
        avg_value_traded_recent_prior = _safe_float(
            row.get("avg_value_traded_recent_prior"), None
        )
        value_traded_trend_pct = (
            (avg_value_traded_recent - avg_value_traded_recent_prior)
            / avg_value_traded_recent_prior
            * 100.0
            if avg_value_traded_recent is not None
            and avg_value_traded_recent_prior
            and avg_value_traded_recent_prior > 0
            else None
        )

        volatility_core_pct_today = _safe_float(
            hot_row.get("volatility_core_pct_today"), None
        )
        liquidity_core_pct_today = _safe_float(
            hot_row.get("liquidity_core_pct_today"), None
        )
        currently_quant_hot_flag = int(
            (liquidity_core_pct_today or 0.0) >= hot_liquidity_pct_ceiling
            or (volatility_core_pct_today or 0.0) >= hot_volatility_pct_ceiling
        )
        in_shortlist_flag = int(symbol in shortlist_symbol_set)

        hist_occurrence_count = _safe_int(hist_row.get("blindspot_hist_occurrence_count"))
        hist_median_fwd_pct = _safe_float(
            hist_row.get("blindspot_hist_median_fwd_pct"), None
        )
        hist_win_rate = _safe_float(hist_row.get("blindspot_hist_win_rate"), None)

        volume_component = _clamp01(
            0.5 + (volume_base_trend_pct / 100.0)
            if volume_base_trend_pct is not None
            else None
        )
        perf_trend_component = _clamp01(
            0.5 + (perf_1m_trend_delta / 20.0)
            if perf_1m_trend_delta is not None
            else None
        )
        hist_component = (
            _clamp01(hist_win_rate)
            if hist_occurrence_count >= DEFAULT_MIN_HISTORICAL_OCCURRENCE_COUNT
            else 0.5
        )
        quiet_mover_score = round(
            0.30 * trend_persistence_score
            + 0.25 * volume_component
            + 0.25 * perf_trend_component
            + 0.20 * hist_component,
            4,
        )
        blindspot_flag = int(
            trend_persistence_score >= persistence_threshold
            and currently_quant_hot_flag == 0
        )

        symbol_rows.append(
            {
                "symbol": symbol,
                "bare_ticker": row.get("bare_ticker"),
                "company_name": row.get("company_name"),
                "market": row.get("market"),
                "exchange": row.get("exchange"),
                "country": row.get("country"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "source_day_label": row.get("source_day_label"),
                "source_date": row.get("source_date"),
                "close_price": _safe_float(row.get("close_price"), None),
                "market_cap_basic": _safe_float(row.get("market_cap_basic"), None),
                "perf_5d": _safe_float(row.get("perf_5d"), None),
                "perf_1m": _safe_float(row.get("perf_1m"), None),
                "perf_3m": _safe_float(row.get("perf_3m"), None),
                "trend_persistence_score": round(trend_persistence_score, 4),
                "trend_persistence_score_prior": (
                    round(_safe_float(row.get("trend_persistence_score_prior"), 0.0), 4)
                    if row.get("trend_persistence_score_prior") is not None
                    else ""
                ),
                "perf_1m_trailing_avg": (
                    round(perf_1m_trailing_avg, 4) if perf_1m_trailing_avg is not None else ""
                ),
                "perf_1m_trailing_avg_prior": (
                    round(perf_1m_trailing_avg_prior, 4)
                    if perf_1m_trailing_avg_prior is not None
                    else ""
                ),
                "perf_1m_trend_delta": (
                    round(perf_1m_trend_delta, 4) if perf_1m_trend_delta is not None else ""
                ),
                "avg_volume_recent": (
                    round(avg_volume_recent, 2) if avg_volume_recent is not None else ""
                ),
                "avg_volume_recent_prior": (
                    round(avg_volume_recent_prior, 2)
                    if avg_volume_recent_prior is not None
                    else ""
                ),
                "volume_base_trend_pct": (
                    round(volume_base_trend_pct, 2)
                    if volume_base_trend_pct is not None
                    else ""
                ),
                "avg_value_traded_recent": (
                    round(avg_value_traded_recent, 2)
                    if avg_value_traded_recent is not None
                    else ""
                ),
                "avg_value_traded_recent_prior": (
                    round(avg_value_traded_recent_prior, 2)
                    if avg_value_traded_recent_prior is not None
                    else ""
                ),
                "value_traded_trend_pct": (
                    round(value_traded_trend_pct, 2)
                    if value_traded_trend_pct is not None
                    else ""
                ),
                "volatility_core_pct_today": (
                    round(volatility_core_pct_today, 4)
                    if volatility_core_pct_today is not None
                    else ""
                ),
                "liquidity_core_pct_today": (
                    round(liquidity_core_pct_today, 4)
                    if liquidity_core_pct_today is not None
                    else ""
                ),
                "currently_quant_hot_flag": currently_quant_hot_flag,
                "in_shortlist_flag": in_shortlist_flag,
                "blindspot_hist_occurrence_count": hist_occurrence_count,
                "blindspot_hist_median_fwd_pct": (
                    round(hist_median_fwd_pct, 4) if hist_median_fwd_pct is not None else ""
                ),
                "blindspot_hist_win_rate": (
                    round(hist_win_rate, 4) if hist_win_rate is not None else ""
                ),
                "quiet_mover_score": quiet_mover_score,
                "blindspot_flag": blindspot_flag,
            }
        )

    symbol_rows.sort(key=lambda item: item["quiet_mover_score"], reverse=True)
    for index, row in enumerate(symbol_rows, start=1):
        row["quiet_mover_rank"] = index

    top_quiet_movers_by_industry: dict[str, list[str]] = {}
    for row in symbol_rows:
        industry = str(row.get("industry") or "").strip()
        if not industry:
            continue
        bucket = top_quiet_movers_by_industry.setdefault(industry, [])
        if len(bucket) < 5:
            bucket.append(str(row.get("symbol")))

    industry_rows: list[dict[str, Any]] = []
    for row in industry_latest_rows:
        industry = str(row.get("industry") or "")
        if not industry:
            continue
        hist_row = industry_hist_by_industry.get(industry) or {}

        relative_strength_now = _safe_float(
            row.get("industry_relative_strength_percentile"), None
        )
        relative_strength_prior = _safe_float(
            row.get("industry_relative_strength_prior"), None
        )
        rotation_delta = (
            relative_strength_now - relative_strength_prior
            if relative_strength_now is not None and relative_strength_prior is not None
            else None
        )
        breadth_now = _safe_float(row.get("industry_breadth_positive_1m"), None)
        breadth_prior = _safe_float(row.get("industry_breadth_prior"), None)
        breadth_delta = (
            breadth_now - breadth_prior
            if breadth_now is not None and breadth_prior is not None
            else None
        )
        avg_volume_now = _safe_float(row.get("industry_avg_volume"), None)
        avg_volume_prior = _safe_float(row.get("industry_avg_volume_prior"), None)
        volume_trend_pct = (
            (avg_volume_now - avg_volume_prior) / avg_volume_prior * 100.0
            if avg_volume_now is not None and avg_volume_prior and avg_volume_prior > 0
            else None
        )
        hist_occurrence_count = _safe_int(hist_row.get("blindspot_hist_occurrence_count"))
        hist_median_fwd_pct = _safe_float(
            hist_row.get("blindspot_hist_median_fwd_pct"), None
        )
        hist_win_rate = _safe_float(hist_row.get("blindspot_hist_win_rate"), None)

        rotation_component = _clamp01(0.5 + rotation_delta if rotation_delta is not None else None)
        breadth_component = _clamp01(breadth_now)
        breadth_delta_component = _clamp01(
            0.5 + breadth_delta if breadth_delta is not None else None
        )
        hist_component = (
            _clamp01(hist_win_rate)
            if hist_occurrence_count >= DEFAULT_MIN_HISTORICAL_OCCURRENCE_COUNT
            else 0.5
        )
        industry_rotation_score = round(
            0.35 * rotation_component
            + 0.25 * breadth_component
            + 0.20 * breadth_delta_component
            + 0.20 * hist_component,
            4,
        )

        industry_rows.append(
            {
                "industry": industry,
                "sector": row.get("sector"),
                "source_date": row.get("source_date"),
                "industry_symbol_count": _safe_int(row.get("industry_symbol_count")),
                "industry_median_perf_1m": _safe_float(
                    row.get("industry_median_perf_1m"), None
                ),
                "industry_breadth_positive_1m": (
                    round(breadth_now, 4) if breadth_now is not None else ""
                ),
                "industry_breadth_prior": (
                    round(breadth_prior, 4) if breadth_prior is not None else ""
                ),
                "industry_breadth_delta": (
                    round(breadth_delta, 4) if breadth_delta is not None else ""
                ),
                "industry_relative_strength_percentile": (
                    round(relative_strength_now, 4)
                    if relative_strength_now is not None
                    else ""
                ),
                "industry_relative_strength_prior": (
                    round(relative_strength_prior, 4)
                    if relative_strength_prior is not None
                    else ""
                ),
                "industry_rotation_delta": (
                    round(rotation_delta, 4) if rotation_delta is not None else ""
                ),
                "industry_avg_volume": (
                    round(avg_volume_now, 2) if avg_volume_now is not None else ""
                ),
                "industry_avg_volume_prior": (
                    round(avg_volume_prior, 2) if avg_volume_prior is not None else ""
                ),
                "industry_volume_trend_pct": (
                    round(volume_trend_pct, 2) if volume_trend_pct is not None else ""
                ),
                "blindspot_hist_occurrence_count": hist_occurrence_count,
                "blindspot_hist_median_fwd_pct": (
                    round(hist_median_fwd_pct, 4) if hist_median_fwd_pct is not None else ""
                ),
                "blindspot_hist_win_rate": (
                    round(hist_win_rate, 4) if hist_win_rate is not None else ""
                ),
                "industry_rotation_score": industry_rotation_score,
                "top_quiet_movers": ", ".join(
                    top_quiet_movers_by_industry.get(industry, [])
                ),
            }
        )

    industry_rows.sort(key=lambda item: item["industry_rotation_score"], reverse=True)
    for index, row in enumerate(industry_rows, start=1):
        row["industry_rotation_rank"] = index

    blindspot_candidate_rows = [row for row in symbol_rows if row["blindspot_flag"] == 1]
    top_symbol_rows = blindspot_candidate_rows[:resolved_top_count]

    context.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(industry_csv, industry_rows)
    _write_csv(symbol_csv, symbol_rows)
    _write_csv(symbol_top_csv, top_symbol_rows)

    resolved_as_of_date_value = (
        as_of_date
        or (symbol_rows[0].get("source_date") if symbol_rows else "unknown")
    )
    _write_blindspot_lane_report(
        report_md,
        snapshot_database_path=resolved_snapshot_database_path,
        as_of_date=str(resolved_as_of_date_value),
        lookback_trading_days=resolved_lookback,
        persistence_window_days=resolved_persistence_window,
        persistence_threshold=float(persistence_threshold),
        hot_liquidity_pct_ceiling=float(hot_liquidity_pct_ceiling),
        hot_volatility_pct_ceiling=float(hot_volatility_pct_ceiling),
        industry_min_symbol_count=resolved_industry_min_count,
        ranking_horizon=int(ranking_horizon),
        resolved_forward_horizon=resolved_forward_horizon,
        universe_row_count=universe_row_count,
        industry_row_count=len(industry_rows),
        symbol_candidate_count=len(symbol_rows),
        blindspot_flag_count=len(blindspot_candidate_rows),
        industry_csv=industry_csv,
        symbol_csv=symbol_csv,
        symbol_top_csv=symbol_top_csv,
        database_path=database_path,
    )

    duckdb = _import_duckdb()
    out_conn = duckdb.connect(database_path.as_posix())
    try:
        if industry_rows:
            out_conn.execute(f"""
                CREATE TABLE blindspot_industry_rotation AS
                SELECT * FROM read_csv({_q(industry_csv.as_posix())}, header = true, auto_detect = true)
                """)
        else:
            out_conn.execute("CREATE TABLE blindspot_industry_rotation(industry VARCHAR)")
        if symbol_rows:
            out_conn.execute(f"""
                CREATE TABLE blindspot_symbol_candidates AS
                SELECT * FROM read_csv({_q(symbol_csv.as_posix())}, header = true, auto_detect = true)
                """)
        else:
            out_conn.execute("CREATE TABLE blindspot_symbol_candidates(symbol VARCHAR)")
        out_conn.execute("""
            CREATE TABLE blindspot_lane_manifest (
                key VARCHAR,
                value VARCHAR
            )
            """)
    finally:
        out_conn.close()

    manifest = {
        "command": "edge-blindspot-lane",
        "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
        "as_of_date": str(resolved_as_of_date_value),
        "ranking_horizon": int(ranking_horizon),
        "resolved_forward_horizon": resolved_forward_horizon,
        "lookback_trading_days": resolved_lookback,
        "persistence_window_days": resolved_persistence_window,
        "persistence_threshold": float(persistence_threshold),
        "hot_liquidity_pct_ceiling": float(hot_liquidity_pct_ceiling),
        "hot_volatility_pct_ceiling": float(hot_volatility_pct_ceiling),
        "industry_min_symbol_count": resolved_industry_min_count,
        "min_market_cap_usd": min_market_cap_usd,
        "markets": list(resolved_markets),
        "countries": list(resolved_countries),
        "exchanges": list(resolved_exchanges),
        "us_only": bool(us_only),
        "universe_row_count": universe_row_count,
        "industry_row_count": len(industry_rows),
        "symbol_candidate_count": len(symbol_rows),
        "blindspot_flag_count": len(blindspot_candidate_rows),
        "top_count": resolved_top_count,
        "output_dir": context.output_dir.as_posix(),
        "industry_csv": industry_csv.as_posix(),
        "symbol_csv": symbol_csv.as_posix(),
        "symbol_top_csv": symbol_top_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": context.output_dir,
        "industry_csv": industry_csv,
        "symbol_csv": symbol_csv,
        "symbol_top_csv": symbol_top_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "as_of_date": str(resolved_as_of_date_value),
        "ranking_horizon": int(ranking_horizon),
        "resolved_forward_horizon": resolved_forward_horizon,
        "universe_row_count": universe_row_count,
        "industry_row_count": len(industry_rows),
        "symbol_candidate_count": len(symbol_rows),
        "blindspot_flag_count": len(blindspot_candidate_rows),
        "top_count": resolved_top_count,
    }
