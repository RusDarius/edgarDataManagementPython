from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .region_filters import resolve_snapshot_market_filter


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _qi(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(connection: Any, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchone()[0]
    )


def _normalize_string_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip()
        if stripped:
            normalized.append(stripped.upper())
    return tuple(normalized)


def _normalize_market_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip().lower()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _expand_us_aliases(countries: Sequence[str], us_only: bool) -> tuple[str, ...]:
    resolved = list(countries)
    if us_only:
        for alias in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
            if alias not in resolved:
                resolved.append(alias)
    return tuple(resolved)


def _build_region_filter_sql(
    *,
    country_expr: str,
    exchange_expr: str,
    countries: Sequence[str],
    exchanges: Sequence[str],
) -> str:
    filters: list[str] = []
    if countries:
        literals = ", ".join(_q(value.upper()) for value in countries)
        filters.append(f"UPPER(COALESCE({country_expr}, '')) IN ({literals})")
    if exchanges:
        literals = ", ".join(_q(value.upper()) for value in exchanges)
        filters.append(f"UPPER(COALESCE({exchange_expr}, '')) IN ({literals})")
    return " AND ".join(filters) if filters else "1 = 1"


def _build_market_filter_sql(*, market_expr: str, markets: Sequence[str]) -> str:
    if not markets:
        return "1 = 1"
    literals = ", ".join(_q(value.lower()) for value in markets)
    return f"LOWER(COALESCE({market_expr}, '')) IN ({literals})"


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
        candidates = sorted(
            paths.output_root.glob(
                "edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        raise FileNotFoundError(
            "No edge_feature_snapshot_* database was found under the edge research foundations or legacy runs directories."
        )
    return candidates[0]


def _format_scope_args(
    *,
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
) -> str:
    args: list[str] = []
    if markets:
        args.append(f"--markets {','.join(markets)}")
    if countries:
        args.append(f"--countries {','.join(countries)}")
    if exchanges:
        args.append(f"--exchanges {','.join(exchanges)}")
    if us_only:
        args.append("--us-only")
    return f" {' '.join(args)}" if args else ""


def _fetch_dict_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _optional_double_select(
    *,
    field_name: str,
    alias: str,
    available_columns: set[str],
) -> str:
    if field_name not in available_columns:
        return f"CAST(NULL AS DOUBLE) AS {_qi(alias)}"
    return f"TRY_CAST(r.{_qi(field_name)} AS DOUBLE) AS {_qi(alias)}"


def _avg_available_sql(columns: Sequence[str]) -> str:
    if not columns:
        return "NULL"
    value_sum = " + ".join(f"COALESCE({col}, 0.0)" for col in columns)
    count_sum = " + ".join(
        f"CASE WHEN {col} IS NULL THEN 0 ELSE 1 END" for col in columns
    )
    return f"(({value_sum}) / NULLIF(({count_sum}), 0))"


def _write_safety_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    source_database_path: Path,
    target_date: str,
    group_by: str,
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
    min_balance_sheet_score: float,
    min_cash_generation_score: float,
    min_combined_score: float,
    shortlist_top_n: int,
    top10_count: int,
    group_min_count: int,
    scored_path: Path,
    group_summary_path: Path,
    shortlist_path: Path,
    top30_path: Path,
    top10_path: Path,
    scored_rows: int,
    group_rows: int,
    shortlist_rows: int,
    top30_rows: int,
    top10_rows: int,
    threshold_fallback_used: bool,
    filter_args: str,
) -> None:
    lines = [
        "# Safety Highlights",
        "",
        "## What This Run Does",
        "",
        "This companion command ranks names by balance-sheet resilience and cash-generation/value support.",
        "It is intentionally separate from the volatility-liquidity timing composite and meant to be intersected with edge outputs.",
        "",
        "## Score Definitions",
        "",
        "- balance_sheet_safety_score: liquidity + solvency mix (cash/current coverage, leverage, Altman, Zmijewski, net-cash context).",
        "- cash_generation_value_score: cash production and valuation support (FCF/cashflow economics and yields).",
        "- safety_companion_score: blended score from balance-sheet and cash-generation components.",
        "",
        "## Indicator Columns",
        "",
        "This output includes explicit boolean indicator columns so selection is interpretable:",
        "- indicator_net_cash",
        "- indicator_altman_safe / indicator_altman_distress",
        "- indicator_cash_ratio_strong / indicator_current_ratio_strong",
        "- indicator_fcf_margin_positive / indicator_fcf_growth_positive",
        "- indicator_earnings_yield_positive / indicator_shareholder_yield_positive",
        "- indicator_debt_to_equity_low / indicator_zmijewski_safe",
        "- indicator_short_term_cash_coverage_ok",
        "- indicator_pass_count",
        "",
        "## Replay Commands",
        "",
        "PowerShell:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools safety-highlights --snapshot-db "{snapshot_database_path.as_posix()}" --date {target_date} --group-by {group_by}{filter_args}',
        "```",
        "",
        "Git bash:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools safety-highlights --snapshot-db \"{snapshot_database_path.as_posix()}\" --date {target_date} --group-by {group_by}{filter_args}",
        "```",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{snapshot_database_path.as_posix()}`",
        f"- source_database_path: `{source_database_path.as_posix()}`",
        f"- target_date: {target_date}",
        f"- group_by: {group_by}",
        f"- markets: {', '.join(markets) if markets else 'all'}",
        f"- countries: {', '.join(countries) if countries else 'all'}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'all'}",
        f"- us_only: {us_only}",
        f"- min_balance_sheet_score: {min_balance_sheet_score}",
        f"- min_cash_generation_score: {min_cash_generation_score}",
        f"- min_combined_score: {min_combined_score}",
        f"- shortlist_top_n: {shortlist_top_n}",
        f"- top10_count: {top10_count}",
        f"- group_min_count: {group_min_count}",
        f"- threshold_fallback_used: {threshold_fallback_used}",
        "",
        "## Output Files",
        "",
        f"- safety scored universe: `{scored_path.as_posix()}` ({scored_rows} rows)",
        f"- safety group summary: `{group_summary_path.as_posix()}` ({group_rows} rows)",
        f"- safety shortlist: `{shortlist_path.as_posix()}` ({shortlist_rows} rows)",
        f"- safety top30: `{top30_path.as_posix()}` ({top30_rows} rows)",
        f"- safety top10 focus: `{top10_path.as_posix()}` ({top10_rows} rows)",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_edge_safety_highlights(
    *,
    snapshot_database_path: str | Path | None = None,
    target_date: str | None = None,
    group_by: str = "industry",
    markets: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
    exchanges: Iterable[str] | None = None,
    us_only: bool = False,
    min_balance_sheet_score: float = 0.45,
    min_cash_generation_score: float = 0.40,
    min_combined_score: float = 0.50,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    group_min_count: int = 3,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    allowed_groupings = {"industry", "sector", "exchange", "country"}
    normalized_group_by = str(group_by).strip().lower() or "industry"
    if normalized_group_by not in allowed_groupings:
        raise ValueError(
            f"Unsupported group_by '{group_by}'. Expected one of: {', '.join(sorted(allowed_groupings))}."
        )

    resolved_snapshot_database_path = (
        Path(snapshot_database_path)
        if snapshot_database_path is not None
        else _resolve_latest_snapshot_database(output_root)
    )
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="edge_safety_highlights",
        output_root=output_paths.output_root,
    )
    scored_path = context.output_dir / "edge_safety_scored.csv"
    group_summary_path = context.output_dir / "edge_safety_group_summary.csv"
    shortlist_path = context.output_dir / "edge_safety_name_shortlist.csv"
    top30_path = context.output_dir / "edge_safety_name_top30.csv"
    top10_path = (
        context.output_dir / f"edge_safety_name_top{int(max(1, top10_count))}_focus.csv"
    )
    report_md = context.output_dir / "edge_safety_highlights_report.md"
    manifest_path = context.output_dir / "edge_safety_highlights_manifest.json"

    resolved_countries = _expand_us_aliases(
        _normalize_string_list(countries),
        bool(us_only),
    )
    resolved_markets = _normalize_market_list(markets)
    resolved_exchanges = _normalize_string_list(exchanges)
    filter_args = _format_scope_args(
        markets=resolved_markets,
        countries=resolved_countries,
        exchanges=resolved_exchanges,
        us_only=bool(us_only),
    )

    duckdb_mod = _import_duckdb()
    conn = duckdb_mod.connect(
        resolved_snapshot_database_path.as_posix(), read_only=False
    )
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        if not _table_exists(conn, "symbol_day_feature_snapshot"):
            raise ValueError(
                "Required table 'symbol_day_feature_snapshot' not found. Run the full suite first."
            )

        region_filter_sql = _build_region_filter_sql(
            country_expr="snap.country",
            exchange_expr="snap.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter_sql, _market_filter_applied = resolve_snapshot_market_filter(
            conn,
            alias="snap",
            markets=resolved_markets,
        )

        resolved_target_date = (
            target_date
            if target_date is not None
            else str(
                conn.execute(
                    "SELECT MAX(source_date)::VARCHAR FROM symbol_day_feature_snapshot AS snap "
                    f"WHERE {region_filter_sql} AND {market_filter_sql}"
                ).fetchone()[0]
            )
        )
        if not resolved_target_date or resolved_target_date == "None":
            raise ValueError(
                "No source_date matched the selected region filters in symbol_day_feature_snapshot."
            )

        source_db_row = conn.execute(f"""
            SELECT source_database_path, COUNT(*) AS row_count
            FROM symbol_day_feature_snapshot AS snap
            WHERE snap.source_date = {_q(resolved_target_date)}::DATE
              AND {region_filter_sql}
                            AND {market_filter_sql}
            GROUP BY source_database_path
            ORDER BY row_count DESC, source_database_path
            LIMIT 1
            """).fetchone()
        if source_db_row is None:
            raise ValueError(
                "No source database path was found for the selected date and region filters."
            )
        resolved_source_database_path = Path(str(source_db_row[0]))
        if not resolved_source_database_path.exists():
            raise FileNotFoundError(
                f"Source daily database was not found: {resolved_source_database_path}"
            )

        conn.execute(
            f"ATTACH {_q(resolved_source_database_path.as_posix())} AS src_daily (READ_ONLY)"
        )
        available_columns = {
            str(row[0])
            for row in conn.execute(
                "SELECT column_name FROM duckdb_columns() "
                "WHERE database_name = 'src_daily' AND table_name = 'all_fields_rows'"
            ).fetchall()
        }
        if not available_columns:
            raise ValueError(
                "Table src_daily.all_fields_rows was not found or has no columns."
            )

        raw_metric_selects = [
            _optional_double_select(
                field_name="current_ratio",
                alias="current_ratio",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="current_ratio_current",
                alias="current_ratio_current",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="current_ratio_fq",
                alias="current_ratio_fq",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="quick_ratio",
                alias="quick_ratio",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="cash_ratio",
                alias="cash_ratio",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="short_term_cash_coverage",
                alias="short_term_cash_coverage",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="total_debt",
                alias="total_debt",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="net_debt",
                alias="net_debt",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="debt_to_equity",
                alias="debt_to_equity",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="debt_to_assets",
                alias="debt_to_assets",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="debt_to_asset_fq",
                alias="debt_to_asset_fq",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="total_debt_to_ebitda_fq",
                alias="total_debt_to_ebitda_fq",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="altman_z_score_ttm",
                alias="altman_z_score_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="altman_z_score_fy",
                alias="altman_z_score_fy",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="zmijewski_score_ttm",
                alias="zmijewski_score_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="cash_f_operating_activities_ttm",
                alias="cash_f_operating_activities_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="free_cash_flow_margin_ttm",
                alias="free_cash_flow_margin_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="free_cash_flow_yoy_growth_ttm",
                alias="free_cash_flow_yoy_growth_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="earnings_yield",
                alias="earnings_yield",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="shareholder_yield",
                alias="shareholder_yield",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="enterprise_value_to_free_cash_flow_ttm",
                alias="enterprise_value_to_free_cash_flow_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="price_free_cash_flow_ttm",
                alias="price_free_cash_flow_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="price_to_cash_f_operating_activities_ttm",
                alias="price_to_cash_f_operating_activities_ttm",
                available_columns=available_columns,
            ),
            _optional_double_select(
                field_name="return_on_invested_capital",
                alias="return_on_invested_capital",
                available_columns=available_columns,
            ),
        ]
        raw_metric_sql = ",\n                    ".join(raw_metric_selects)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE source_day_stage AS
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT
                    CAST(r.symbol AS VARCHAR) AS symbol,
                    {raw_metric_sql},
                    ROW_NUMBER() OVER (
                        PARTITION BY r.symbol
                        ORDER BY m.created_at_utc DESC NULLS LAST, r.run_id DESC, r.row_number DESC
                    ) AS row_num
                FROM src_daily.all_fields_rows AS r
                LEFT JOIN src_daily.run_metadata AS m USING (run_id)
            )
            WHERE row_num = 1
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE safety_base AS
            SELECT
                snap.symbol,
                snap.bare_ticker,
                snap.company_name,
                snap.exchange,
                snap.country,
                snap.sector,
                snap.industry,
                snap.source_date,
                snap.close_price,
                snap.market_cap_basic,
                COALESCE(src.current_ratio, src.current_ratio_current, src.current_ratio_fq) AS current_ratio_val,
                src.quick_ratio AS quick_ratio_val,
                src.cash_ratio AS cash_ratio_val,
                src.short_term_cash_coverage AS short_term_cash_coverage_val,
                src.total_debt AS total_debt_val,
                src.net_debt AS net_debt_val,
                src.debt_to_equity AS debt_to_equity_val,
                COALESCE(src.debt_to_assets, src.debt_to_asset_fq) AS debt_to_assets_val,
                src.total_debt_to_ebitda_fq AS total_debt_to_ebitda_val,
                COALESCE(src.altman_z_score_ttm, src.altman_z_score_fy) AS altman_z_val,
                src.zmijewski_score_ttm AS zmijewski_val,
                src.cash_f_operating_activities_ttm AS cfo_ttm_val,
                src.free_cash_flow_margin_ttm AS free_cash_flow_margin_val,
                src.free_cash_flow_yoy_growth_ttm AS free_cash_flow_yoy_growth_val,
                src.earnings_yield AS earnings_yield_val,
                src.shareholder_yield AS shareholder_yield_val,
                src.enterprise_value_to_free_cash_flow_ttm AS ev_to_fcf_val,
                src.price_free_cash_flow_ttm AS price_to_fcf_val,
                src.price_to_cash_f_operating_activities_ttm AS price_to_cfo_val,
                src.return_on_invested_capital AS roic_val,
                CASE
                    WHEN snap.market_cap_basic IS NULL OR snap.market_cap_basic = 0 THEN NULL
                    ELSE src.cash_f_operating_activities_ttm / snap.market_cap_basic
                END AS cfo_to_mcap,
                CASE
                    WHEN snap.market_cap_basic IS NULL OR snap.market_cap_basic = 0 THEN NULL
                    ELSE (-src.net_debt) / snap.market_cap_basic
                END AS net_cash_to_mcap,
                CASE
                    WHEN src.enterprise_value_to_free_cash_flow_ttm > 0 THEN src.enterprise_value_to_free_cash_flow_ttm
                    ELSE NULL
                END AS ev_to_fcf_positive,
                CASE
                    WHEN src.price_free_cash_flow_ttm > 0 THEN src.price_free_cash_flow_ttm
                    ELSE NULL
                END AS price_to_fcf_positive,
                CASE
                    WHEN src.price_to_cash_f_operating_activities_ttm > 0 THEN src.price_to_cash_f_operating_activities_ttm
                    ELSE NULL
                END AS price_to_cfo_positive
            FROM symbol_day_feature_snapshot AS snap
            LEFT JOIN source_day_stage AS src
                ON src.symbol = snap.symbol
            WHERE snap.source_date = {_q(resolved_target_date)}::DATE
              AND {region_filter_sql}
                            AND {market_filter_sql}
            """)

        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE safety_scored AS
            WITH pct AS (
                SELECT
                    base.*,
                    CASE WHEN current_ratio_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY current_ratio_val) END AS pct_current_ratio,
                    CASE WHEN quick_ratio_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY quick_ratio_val) END AS pct_quick_ratio,
                    CASE WHEN cash_ratio_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY cash_ratio_val) END AS pct_cash_ratio,
                    CASE WHEN short_term_cash_coverage_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY short_term_cash_coverage_val) END AS pct_short_term_cash_coverage,
                    CASE WHEN net_cash_to_mcap IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY net_cash_to_mcap) END AS pct_net_cash_to_mcap,
                    CASE WHEN debt_to_equity_val IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY debt_to_equity_val) END AS pct_debt_to_equity,
                    CASE WHEN debt_to_assets_val IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY debt_to_assets_val) END AS pct_debt_to_assets,
                    CASE WHEN total_debt_to_ebitda_val IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY total_debt_to_ebitda_val) END AS pct_total_debt_to_ebitda,
                    CASE WHEN altman_z_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY altman_z_val) END AS pct_altman_z,
                    CASE WHEN zmijewski_val IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY zmijewski_val) END AS pct_zmijewski,
                    CASE WHEN free_cash_flow_margin_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY free_cash_flow_margin_val) END AS pct_fcf_margin,
                    CASE WHEN free_cash_flow_yoy_growth_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY free_cash_flow_yoy_growth_val) END AS pct_fcf_growth,
                    CASE WHEN cfo_to_mcap IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY cfo_to_mcap) END AS pct_cfo_to_mcap,
                    CASE WHEN earnings_yield_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY earnings_yield_val) END AS pct_earnings_yield,
                    CASE WHEN shareholder_yield_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY shareholder_yield_val) END AS pct_shareholder_yield,
                    CASE WHEN ev_to_fcf_positive IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY ev_to_fcf_positive) END AS pct_ev_to_fcf,
                    CASE WHEN price_to_fcf_positive IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY price_to_fcf_positive) END AS pct_price_to_fcf,
                    CASE WHEN price_to_cfo_positive IS NULL THEN NULL ELSE 1.0 - CUME_DIST() OVER (ORDER BY price_to_cfo_positive) END AS pct_price_to_cfo,
                    CASE WHEN roic_val IS NULL THEN NULL ELSE CUME_DIST() OVER (ORDER BY roic_val) END AS pct_roic
                FROM safety_base AS base
            ),
            scores AS (
                SELECT
                    pct.*,
                    ROUND(
                        """
            + _avg_available_sql(
                [
                    "pct_current_ratio",
                    "pct_quick_ratio",
                    "pct_cash_ratio",
                    "pct_short_term_cash_coverage",
                    "pct_net_cash_to_mcap",
                    "pct_debt_to_equity",
                    "pct_debt_to_assets",
                    "pct_total_debt_to_ebitda",
                    "pct_altman_z",
                    "pct_zmijewski",
                ]
            )
            + """
                    ,
                        4
                    ) AS balance_sheet_safety_score,
                    ROUND(
                        """
            + _avg_available_sql(
                [
                    "pct_fcf_margin",
                    "pct_fcf_growth",
                    "pct_cfo_to_mcap",
                    "pct_earnings_yield",
                    "pct_shareholder_yield",
                    "pct_ev_to_fcf",
                    "pct_price_to_fcf",
                    "pct_price_to_cfo",
                    "pct_roic",
                ]
            )
            + """
                    ,
                        4
                    ) AS cash_generation_value_score
                FROM pct
            )
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY
                        ROUND(
                            (0.55 * COALESCE(balance_sheet_safety_score, 0.0)) +
                            (0.45 * COALESCE(cash_generation_value_score, 0.0)),
                            4
                        ) DESC,
                        COALESCE(balance_sheet_safety_score, 0.0) DESC,
                        COALESCE(cash_generation_value_score, 0.0) DESC,
                        symbol
                ) AS safety_rank,
                scores.*,
                ROUND(
                    (0.55 * COALESCE(balance_sheet_safety_score, 0.0)) +
                    (0.45 * COALESCE(cash_generation_value_score, 0.0)),
                    4
                ) AS safety_companion_score,
                CASE WHEN net_debt_val IS NOT NULL AND net_debt_val < 0 THEN 1 ELSE 0 END AS indicator_net_cash,
                CASE WHEN altman_z_val IS NOT NULL AND altman_z_val >= 3.0 THEN 1 ELSE 0 END AS indicator_altman_safe,
                CASE WHEN altman_z_val IS NOT NULL AND altman_z_val < 1.8 THEN 1 ELSE 0 END AS indicator_altman_distress,
                CASE WHEN cash_ratio_val IS NOT NULL AND cash_ratio_val >= 1.0 THEN 1 ELSE 0 END AS indicator_cash_ratio_strong,
                CASE WHEN current_ratio_val IS NOT NULL AND current_ratio_val >= 1.5 THEN 1 ELSE 0 END AS indicator_current_ratio_strong,
                CASE WHEN free_cash_flow_margin_val IS NOT NULL AND free_cash_flow_margin_val > 0 THEN 1 ELSE 0 END AS indicator_fcf_margin_positive,
                CASE WHEN free_cash_flow_yoy_growth_val IS NOT NULL AND free_cash_flow_yoy_growth_val > 0 THEN 1 ELSE 0 END AS indicator_fcf_growth_positive,
                CASE WHEN earnings_yield_val IS NOT NULL AND earnings_yield_val > 0 THEN 1 ELSE 0 END AS indicator_earnings_yield_positive,
                CASE WHEN shareholder_yield_val IS NOT NULL AND shareholder_yield_val > 0 THEN 1 ELSE 0 END AS indicator_shareholder_yield_positive,
                CASE WHEN debt_to_equity_val IS NOT NULL AND debt_to_equity_val <= 1.0 THEN 1 ELSE 0 END AS indicator_debt_to_equity_low,
                CASE WHEN zmijewski_val IS NOT NULL AND zmijewski_val <= 0 THEN 1 ELSE 0 END AS indicator_zmijewski_safe,
                CASE WHEN short_term_cash_coverage_val IS NOT NULL AND short_term_cash_coverage_val >= 1.0 THEN 1 ELSE 0 END AS indicator_short_term_cash_coverage_ok,
                (
                    CASE WHEN net_debt_val IS NOT NULL AND net_debt_val < 0 THEN 1 ELSE 0 END +
                    CASE WHEN altman_z_val IS NOT NULL AND altman_z_val >= 3.0 THEN 1 ELSE 0 END +
                    CASE WHEN cash_ratio_val IS NOT NULL AND cash_ratio_val >= 1.0 THEN 1 ELSE 0 END +
                    CASE WHEN current_ratio_val IS NOT NULL AND current_ratio_val >= 1.5 THEN 1 ELSE 0 END +
                    CASE WHEN free_cash_flow_margin_val IS NOT NULL AND free_cash_flow_margin_val > 0 THEN 1 ELSE 0 END +
                    CASE WHEN free_cash_flow_yoy_growth_val IS NOT NULL AND free_cash_flow_yoy_growth_val > 0 THEN 1 ELSE 0 END +
                    CASE WHEN earnings_yield_val IS NOT NULL AND earnings_yield_val > 0 THEN 1 ELSE 0 END +
                    CASE WHEN shareholder_yield_val IS NOT NULL AND shareholder_yield_val > 0 THEN 1 ELSE 0 END +
                    CASE WHEN debt_to_equity_val IS NOT NULL AND debt_to_equity_val <= 1.0 THEN 1 ELSE 0 END +
                    CASE WHEN zmijewski_val IS NOT NULL AND zmijewski_val <= 0 THEN 1 ELSE 0 END +
                    CASE WHEN short_term_cash_coverage_val IS NOT NULL AND short_term_cash_coverage_val >= 1.0 THEN 1 ELSE 0 END
                ) AS indicator_pass_count
            FROM scores
            """
        )

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE safety_shortlist AS
            SELECT *
            FROM safety_scored
            WHERE safety_companion_score >= {float(min_combined_score)}
               OR (
                    COALESCE(balance_sheet_safety_score, 0.0) >= {float(min_balance_sheet_score)}
                AND COALESCE(cash_generation_value_score, 0.0) >= {float(min_cash_generation_score)}
               )
            ORDER BY
                safety_companion_score DESC,
                indicator_pass_count DESC,
                balance_sheet_safety_score DESC,
                symbol
            """)

        scored_row_count = int(
            conn.execute("SELECT COUNT(*) FROM safety_scored").fetchone()[0]
        )
        conn.execute(
            f"COPY (SELECT * FROM safety_scored ORDER BY safety_companion_score DESC, indicator_pass_count DESC, balance_sheet_safety_score DESC, symbol) TO {_q(scored_path.as_posix())} (HEADER, DELIMITER ',')"
        )
        shortlist_rows = _fetch_dict_rows(
            conn,
            """
            SELECT *
            FROM safety_shortlist
            ORDER BY
                safety_companion_score DESC,
                indicator_pass_count DESC,
                balance_sheet_safety_score DESC,
                symbol
            """,
        )
        threshold_fallback_used = False
        if not shortlist_rows:
            threshold_fallback_used = True
            shortlist_rows = _fetch_dict_rows(
                conn,
                """
                SELECT *
                FROM safety_scored
                ORDER BY
                    safety_companion_score DESC,
                    indicator_pass_count DESC,
                    balance_sheet_safety_score DESC,
                    symbol
                LIMIT 120
                """,
            )

        for idx, row in enumerate(shortlist_rows, start=1):
            row["safety_rank"] = idx
            row["focus_rank"] = idx

        top30_rows = shortlist_rows[: int(max(1, shortlist_top_n))]
        focus_sorted = sorted(
            shortlist_rows,
            key=lambda row: (
                _safe_float(row.get("safety_companion_score")),
                _safe_int(row.get("indicator_pass_count")),
                _safe_float(row.get("balance_sheet_safety_score")),
                _safe_float(row.get("cash_generation_value_score")),
            ),
            reverse=True,
        )
        top10_rows = focus_sorted[: int(max(1, top10_count))]
        for idx, row in enumerate(top10_rows, start=1):
            row["focus_rank"] = idx

        group_rows = _fetch_dict_rows(
            conn,
            f"""
            SELECT
                COALESCE({normalized_group_by}, 'Unknown') AS group_value,
                COUNT(*) AS shortlist_count,
                COUNT(DISTINCT symbol) AS distinct_symbol_count,
                MEDIAN(safety_companion_score) AS median_safety_companion_score,
                AVG(safety_companion_score) AS avg_safety_companion_score,
                MEDIAN(balance_sheet_safety_score) AS median_balance_sheet_safety_score,
                AVG(balance_sheet_safety_score) AS avg_balance_sheet_safety_score,
                MEDIAN(cash_generation_value_score) AS median_cash_generation_value_score,
                AVG(cash_generation_value_score) AS avg_cash_generation_value_score,
                AVG(indicator_pass_count) AS avg_indicator_pass_count
            FROM safety_shortlist
            GROUP BY COALESCE({normalized_group_by}, 'Unknown')
            HAVING COUNT(*) >= {int(max(1, group_min_count))}
            ORDER BY median_safety_companion_score DESC NULLS LAST, shortlist_count DESC
            """,
        )

        _write_csv(group_summary_path, group_rows)
        _write_csv(shortlist_path, shortlist_rows)
        _write_csv(top30_path, top30_rows)
        _write_csv(top10_path, top10_rows)

        _write_safety_report(
            report_md,
            snapshot_database_path=resolved_snapshot_database_path,
            source_database_path=resolved_source_database_path,
            target_date=resolved_target_date,
            group_by=normalized_group_by,
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
            min_balance_sheet_score=min_balance_sheet_score,
            min_cash_generation_score=min_cash_generation_score,
            min_combined_score=min_combined_score,
            shortlist_top_n=shortlist_top_n,
            top10_count=top10_count,
            group_min_count=group_min_count,
            scored_path=scored_path,
            group_summary_path=group_summary_path,
            shortlist_path=shortlist_path,
            top30_path=top30_path,
            top10_path=top10_path,
            scored_rows=scored_row_count,
            group_rows=len(group_rows),
            shortlist_rows=len(shortlist_rows),
            top30_rows=len(top30_rows),
            top10_rows=len(top10_rows),
            threshold_fallback_used=threshold_fallback_used,
            filter_args=filter_args,
        )

        manifest = {
            "run_id": context.run_id,
            "run_created_at_utc": context.created_at_utc.isoformat(),
            "command": "safety-highlights",
            "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
            "source_database_path": resolved_source_database_path.as_posix(),
            "target_date": resolved_target_date,
            "group_by": normalized_group_by,
            "markets": list(resolved_markets),
            "countries": list(resolved_countries),
            "exchanges": list(resolved_exchanges),
            "us_only": bool(us_only),
            "min_balance_sheet_score": min_balance_sheet_score,
            "min_cash_generation_score": min_cash_generation_score,
            "min_combined_score": min_combined_score,
            "shortlist_top_n": shortlist_top_n,
            "top10_count": top10_count,
            "group_min_count": group_min_count,
            "threshold_fallback_used": threshold_fallback_used,
            "scored_row_count": scored_row_count,
            "group_row_count": len(group_rows),
            "shortlist_row_count": len(shortlist_rows),
            "top30_row_count": len(top30_rows),
            "top10_row_count": len(top10_rows),
            "output_dir": context.output_dir.as_posix(),
            "scored_csv": scored_path.as_posix(),
            "group_summary_csv": group_summary_path.as_posix(),
            "shortlist_csv": shortlist_path.as_posix(),
            "top30_csv": top30_path.as_posix(),
            "top10_csv": top10_path.as_posix(),
            "report_md": report_md.as_posix(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output_dir": context.output_dir,
            "scored_csv": scored_path,
            "group_summary_csv": group_summary_path,
            "shortlist_csv": shortlist_path,
            "top30_csv": top30_path,
            "top10_csv": top10_path,
            "report_md": report_md,
            "manifest_path": manifest_path,
            "target_date": resolved_target_date,
            "scored_row_count": scored_row_count,
            "group_row_count": len(group_rows),
            "shortlist_row_count": len(shortlist_rows),
            "top30_row_count": len(top30_rows),
            "top10_row_count": len(top10_rows),
            "snapshot_database_path": resolved_snapshot_database_path,
            "source_database_path": resolved_source_database_path,
        }
    finally:
        conn.close()
