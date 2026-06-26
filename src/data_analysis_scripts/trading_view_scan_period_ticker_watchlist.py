"""Analyze a ticker watchlist against a scan-period close-forward tracking run."""

from __future__ import annotations

import csv
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    DEFAULT_ALL_FIELDS_ROOT,
    PERIOD_PERFORMANCE_FIELD,
    _extract_day_label_from_database_path,
    _import_duckdb,
    _quote_identifier,
    _quote_path_literal,
    _quote_sql_literal,
    _resolve_scan_period_tracking_run_root,
    discover_all_fields_daily_databases,
)
from data_analysis_scripts.trading_view_regime_context_overlay import (
    load_regime_context_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WATCHLIST_PREDICTOR_FIELDS: tuple[str, ...] = (
    "ATRP|1W",
    "ADRP|1W",
    "relative_volume",
    "ebitda_ttm",
    "Recommend.MA|1M",
    "oper_income_ttm",
    "RSI21[1]|1M",
    "Stoch.K_14_1_3|1M",
    "W.R|1M",
)

MEGA_CAP_WARNING_FIELDS: frozenset[str] = frozenset(
    {"ebitda_ttm", "oper_income_ttm", "Recommend.MA|1M"}
)
OVERSOLD_WARNING_FIELDS: frozenset[str] = frozenset(
    {"RSI21[1]|1M", "Stoch.K_14_1_3|1M", "W.R|1M"}
)

PLAYBOOK_A_LOG_PATTERN = re.compile(
    r"^\s{2}(?P<symbol>[A-Z0-9._:-]+)\s+"
    r"RegFit=(?P<regfit>[\d.]+)\s+"
    r"ATRP\|1W=(?P<atrp>[\d.]+)\s+"
    r"relvol=(?P<relvol>[\d.]+)\s*$"
)

DEFAULT_REGIME_SECTION_HEADERS: dict[str, str] = {
    "playbook_a": "PLAYBOOK A TACTICAL",
}

PREFERRED_EXCHANGE_PREFIXES: tuple[str, ...] = (
    "NASDAQ",
    "NYSE",
    "OMXSTO",
    "LSE",
    "XETR",
    "EURONEXT",
    "SIX",
    "OMXHEX",
    "TSX",
    "AMEX",
    "BATS",
    "OTC",
    "NEWCONNECT",
    "BME",
    "VIE",
    "GPW",
    "BVB",
)


def _normalize_symbol(symbol: str) -> str:
    return symbol.split(":", 1)[-1].strip().upper()


def parse_regime_context_log_section(
    log_path: str | Path,
    *,
    section: str = "playbook_a",
) -> list[dict[str, Any]]:
    """Parse ticker rows from a regime context focus log section."""
    resolved = Path(log_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Regime context log not found: {resolved.as_posix()}")

    header = DEFAULT_REGIME_SECTION_HEADERS.get(section)
    if header is None:
        raise ValueError(
            f"Unknown regime section {section!r}. "
            f"Supported: {sorted(DEFAULT_REGIME_SECTION_HEADERS)}"
        )

    rows: list[dict[str, Any]] = []
    in_section = False
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(header):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("-") and rows:
            break
        if stripped.startswith("(") or not stripped:
            continue
        if stripped.startswith("PLAYBOOK ") or stripped.startswith("WARNING "):
            break
        match = PLAYBOOK_A_LOG_PATTERN.match(line)
        if match is None:
            continue
        rows.append(
            {
                "symbol": _normalize_symbol(match.group("symbol")),
                "regime_fit_score": float(match.group("regfit")),
                "current_atrp_1w": float(match.group("atrp")),
                "current_relative_volume": float(match.group("relvol")),
            }
        )
    return rows


def _resolve_daily_db_for_day(
    all_fields_root: str | Path,
    day_label: str,
) -> Path | None:
    normalized = day_label.replace("-", "_")
    for candidate in discover_all_fields_daily_databases(all_fields_root=all_fields_root):
        if _extract_day_label_from_database_path(candidate) == normalized:
            return candidate
    return None


def _exchange_prefix(symbol: str) -> str:
    if ":" in symbol:
        return symbol.split(":", 1)[0].upper()
    return ""


def _dedupe_period_rows_by_bare_symbol(
    rows: Sequence[Mapping[str, Any]],
    *,
    preferred_exchanges: Sequence[str] = PREFERRED_EXCHANGE_PREFIXES,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        bare_symbol = _normalize_symbol(symbol)
        grouped.setdefault(bare_symbol, []).append(dict(row))

    deduped: list[dict[str, Any]] = []
    for bare_symbol in sorted(grouped):
        candidates = grouped[bare_symbol]
        if len(candidates) == 1:
            deduped.append(candidates[0])
            continue

        def rank(candidate: Mapping[str, Any]) -> tuple[int, float]:
            prefix = _exchange_prefix(str(candidate.get("symbol") or ""))
            try:
                exchange_rank = preferred_exchanges.index(prefix)
            except ValueError:
                exchange_rank = len(preferred_exchanges)
            period_return = _coerce_float(candidate.get("period_return_pct"))
            return (exchange_rank, -(period_return or -9999.0))

        deduped.append(min(candidates, key=rank))
    return deduped


def _fetch_dict_rows(conn: Any, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _attach_readonly(conn: Any, alias: str, path: Path) -> None:
    conn.execute(
        f"ATTACH {_quote_path_literal(path)} AS {_quote_identifier(alias)} (READ_ONLY)"
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def _build_risk_flags(
    row: Mapping[str, Any],
    *,
    universe_median_period_return: float | None,
) -> list[str]:
    flags: list[str] = []
    period_return = _coerce_float(row.get("period_return_pct"))
    atrp_start = _coerce_float(row.get("atrp_1w_at_start"))
    relvol_start = _coerce_float(row.get("relvol_at_start"))
    warning_count = int(row.get("warning_predictor_count") or 0)
    regfit = _coerce_float(row.get("regime_fit_score"))
    max_drawdown = _coerce_float(row.get("max_drawdown_from_start_pct"))

    if atrp_start is not None and atrp_start >= 50.0:
        flags.append("extreme_atrp_at_start")
    if relvol_start is not None and relvol_start >= 3.0:
        flags.append("high_relvol_at_start")
    if warning_count >= 2:
        flags.append("multiple_warning_predictors")
    if period_return is not None and period_return < 0:
        flags.append("negative_period_return")
        if regfit is not None and regfit >= 60.0:
            flags.append("negative_return_despite_high_regfit")
    if (
        period_return is not None
        and universe_median_period_return is not None
        and period_return < universe_median_period_return
    ):
        flags.append("below_universe_median_return")
    if max_drawdown is not None and max_drawdown <= -20.0:
        flags.append("deep_drawdown_during_period")
    return flags


def _resolve_watchlist_scan_period_run_root(
    *,
    run_root: str | Path | None = None,
    tracking_id: str | None = None,
    regime_context_config_path: str | Path | None = None,
) -> Path:
    """Resolve the scan-period tracking folder that supplies period returns and predictors.

    Resolution order:
    1. ``run_root`` — explicit folder from ``run_scan_period_close_forward_predictor_tracking``
    2. ``tracking_id`` — looked up under ``pattern_analysis/runs/{tracking_id}``
    3. ``regime_context_config_path`` — reads ``scan_period_run_root`` from regime JSON
       (same source used by move-prediction regime overlay)
    """
    if run_root is not None or tracking_id:
        return _resolve_scan_period_tracking_run_root(
            run_root=run_root,
            tracking_id=tracking_id,
        )
    if regime_context_config_path is not None:
        config = load_regime_context_config(regime_context_config_path)
        return _resolve_scan_period_tracking_run_root(
            run_root=config.scan_period_run_root,
        )
    raise ValueError(
        "Scan-period tracking run is required. Provide run_root, tracking_id, or "
        "regime_context_config_path (uses scan_period_run_root from the JSON)."
    )


def _write_csv_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def run_scan_period_ticker_watchlist_analysis(
    *,
    run_root: str | Path | None = None,
    tracking_id: str | None = None,
    regime_context_config_path: str | Path | None = None,
    tickers: Sequence[str] | None = None,
    from_regime_log: str | Path | None = None,
    regime_section: str = "playbook_a",
    snapshot_day_labels: Sequence[str] | None = None,
    all_fields_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    watchlist_id: str | None = None,
) -> dict[str, Any]:
    """Join a ticker watchlist to scan-period returns, predictors, and progression.

    Requires a completed scan-period tracking run as the performance source
    (``period_total/``, ``progression/``, ``_scan_period_close_forward_tracking.json``).
    The watchlist only selects *which symbols* to evaluate; all period metrics come
    from that run.
    """
    regime_config = (
        load_regime_context_config(regime_context_config_path)
        if regime_context_config_path is not None
        else None
    )
    resolved_run_root = _resolve_watchlist_scan_period_run_root(
        run_root=run_root,
        tracking_id=tracking_id,
        regime_context_config_path=regime_context_config_path,
    )
    resolved_all_fields_root = (
        Path(all_fields_root)
        if all_fields_root is not None
        else regime_config.all_fields_root
        if regime_config is not None
        else DEFAULT_ALL_FIELDS_ROOT
    )
    tracking_json_path = resolved_run_root / "_scan_period_close_forward_tracking.json"
    tracking_payload: dict[str, Any] = {}
    if tracking_json_path.exists():
        tracking_payload = json.loads(tracking_json_path.read_text(encoding="utf-8"))

    regime_rows: list[dict[str, Any]] = []
    if from_regime_log is not None:
        regime_rows = parse_regime_context_log_section(
            from_regime_log,
            section=regime_section,
        )

    normalized_tickers = [
        _normalize_symbol(symbol)
        for symbol in (tickers or [])
        if str(symbol).strip()
    ]
    if regime_rows:
        normalized_tickers.extend(row["symbol"] for row in regime_rows)
    normalized_tickers = sorted(set(normalized_tickers))
    if not normalized_tickers:
        raise ValueError("Provide tickers and/or from_regime_log with at least one symbol.")

    regime_by_symbol = {row["symbol"]: row for row in regime_rows}
    performance_target = str(
        tracking_payload.get("performance_target") or PERIOD_PERFORMANCE_FIELD
    )
    start_day_label = str(tracking_payload.get("start_day_label") or "")
    end_day_label = str(tracking_payload.get("end_day_label") or "")

    period_total_root = resolved_run_root / "period_total"
    period_input_path = Path(
        (tracking_payload.get("period_total_result") or {})
        .get("period_input_result", {})
        .get("database_path")
        or period_total_root / "period_analysis_input.duckdb"
    )
    period_returns_path = Path(
        (tracking_payload.get("period_total_result") or {})
        .get("period_returns_result", {})
        .get("database_path")
        or period_total_root / "period_returns" / "period_boundary_returns.duckdb"
    )
    progression_db_path = resolved_run_root / "progression" / "period_progression.duckdb"
    progression_parquet_path = (
        resolved_run_root / "progression" / "period_symbol_progression.parquet"
    )

    if not period_input_path.exists():
        raise FileNotFoundError(
            f"Missing period analysis input: {period_input_path.as_posix()}"
        )
    if not period_returns_path.exists():
        raise FileNotFoundError(
            f"Missing period boundary returns: {period_returns_path.as_posix()}"
        )

    resolved_watchlist_id = watchlist_id or (
        f"ticker_watchlist_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}_utc_"
        f"{uuid.uuid4().hex[:8]}"
    )
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root / "ticker_watchlist_analysis" / resolved_watchlist_id
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    ticker_literals = ", ".join(_quote_sql_literal(symbol) for symbol in normalized_tickers)
    symbol_filter_sql = (
        f"(e.symbol IN ({ticker_literals}) "
        f"OR split_part(e.symbol, ':', 2) IN ({ticker_literals}))"
    )
    predictor_select_parts = [
        f"TRY_CAST(e.{_quote_identifier(field)} AS DOUBLE) AS {_quote_identifier(field.replace('|', '_').replace('.', '_'))}"
        for field in WATCHLIST_PREDICTOR_FIELDS
    ]
    predictor_select_sql = ",\n        ".join(predictor_select_parts)

    resolved_snapshot_days = list(snapshot_day_labels or [])
    if not resolved_snapshot_days:
        if start_day_label:
            resolved_snapshot_days.append(start_day_label)
        if end_day_label and end_day_label not in resolved_snapshot_days:
            resolved_snapshot_days.append(end_day_label)

    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        _attach_readonly(conn, "enr", period_input_path)
        _attach_readonly(conn, "pr", period_returns_path)

        universe_median = conn.execute(
            f"""
            SELECT MEDIAN(TRY_CAST(period_return_pct AS DOUBLE))
            FROM pr.period_boundary_returns
            WHERE period_return_pct IS NOT NULL
            """
        ).fetchone()[0]

        period_rows = _fetch_dict_rows(
            conn,
            f"""
            SELECT e.symbol,
                TRY_CAST(p.period_return_pct AS DOUBLE) AS period_return_pct,
                TRY_CAST(p.start_close_price AS DOUBLE) AS start_close_price,
                TRY_CAST(p.end_close_price AS DOUBLE) AS end_close_price,
                {predictor_select_sql}
            FROM enr.all_fields_rows e
            INNER JOIN pr.period_boundary_returns p USING (symbol)
            WHERE {symbol_filter_sql}
            ORDER BY p.period_return_pct DESC NULLS LAST, e.symbol
            """,
        )
        period_rows = _dedupe_period_rows_by_bare_symbol(period_rows)

        progression_rows: list[dict[str, Any]] = []
        progression_stats: dict[str, dict[str, Any]] = {}
        if progression_db_path.exists():
            _attach_readonly(conn, "prog", progression_db_path)
            progression_rows = _fetch_dict_rows(
                conn,
                f"""
                SELECT source_day_label,
                    symbol,
                    close_price,
                    cumulative_return_from_start_pct
                FROM prog.period_symbol_progression
                WHERE symbol IN ({ticker_literals})
                   OR split_part(symbol, ':', 2) IN ({ticker_literals})
                ORDER BY source_day_label, symbol
                """,
            )
        elif progression_parquet_path.exists():
            progression_rows = _fetch_dict_rows(
                conn,
                f"""
                SELECT source_day_label,
                    symbol,
                    close_price,
                    cumulative_return_from_start_pct
                FROM read_parquet({_quote_path_literal(progression_parquet_path)})
                WHERE symbol IN ({ticker_literals})
                   OR split_part(symbol, ':', 2) IN ({ticker_literals})
                ORDER BY source_day_label, symbol
                """,
            )

        for symbol in normalized_tickers:
            symbol_rows = [
                row
                for row in progression_rows
                if _normalize_symbol(str(row.get("symbol") or "")) == symbol
                and row.get("cumulative_return_from_start_pct") is not None
            ]
            if not symbol_rows:
                continue
            cumulative_values = [
                float(row["cumulative_return_from_start_pct"]) for row in symbol_rows
            ]
            underwater_days = sum(1 for value in cumulative_values if value < 0)
            progression_stats[symbol] = {
                "peak_cumulative_return_pct": max(cumulative_values),
                "max_drawdown_from_start_pct": min(cumulative_values),
                "days_underwater": underwater_days,
                "progression_scan_days": len(symbol_rows),
            }

        warning_thresholds: dict[str, tuple[float, float]] = {}
        for field in WATCHLIST_PREDICTOR_FIELDS:
            col_alias = field.replace("|", "_").replace(".", "_")
            stats = conn.execute(
                f"""
                SELECT quantile_cont(TRY_CAST(e.{_quote_identifier(field)} AS DOUBLE), 0.25) AS q1,
                    quantile_cont(TRY_CAST(e.{_quote_identifier(field)} AS DOUBLE), 0.75) AS q3
                FROM enr.all_fields_rows e
                INNER JOIN pr.period_boundary_returns p USING (symbol)
                WHERE TRY_CAST(e.{_quote_identifier(field)} AS DOUBLE) IS NOT NULL
                """
            ).fetchone()
            if stats and stats[0] is not None and stats[1] is not None:
                warning_thresholds[field] = (float(stats[0]), float(stats[1]))

        snapshot_rows: list[dict[str, Any]] = []
        for day_label in resolved_snapshot_days:
            daily_db = _resolve_daily_db_for_day(resolved_all_fields_root, day_label)
            if daily_db is None:
                continue
            alias = f"snap_{day_label.replace('_', '')}"
            _attach_readonly(conn, alias, daily_db)
            select_parts = [
                f"TRY_CAST({_quote_identifier(field)} AS DOUBLE) AS {_quote_identifier(field.replace('|', '_').replace('.', '_'))}"
                for field in WATCHLIST_PREDICTOR_FIELDS
            ]
            snapshot_rows.extend(
                _fetch_dict_rows(
                    conn,
                    f"""
                SELECT symbol,
                    {_quote_sql_literal(day_label)} AS snapshot_day_label,
                    {", ".join(select_parts)}
                FROM {_quote_identifier(alias)}.all_fields_rows
                WHERE symbol IN ({ticker_literals})
                   OR split_part(symbol, ':', 2) IN ({ticker_literals})
                """,
                )
            )
    finally:
        conn.close()

    summary_rows: list[dict[str, Any]] = []
    for row in period_rows:
        symbol = str(row.get("symbol") or "")
        bare_symbol = _normalize_symbol(symbol)
        regime_meta = regime_by_symbol.get(bare_symbol, {})
        prog = progression_stats.get(symbol) or progression_stats.get(bare_symbol, {})
        warning_count = 0
        for field in WATCHLIST_PREDICTOR_FIELDS:
            col_alias = field.replace("|", "_").replace(".", "_")
            value = _coerce_float(row.get(col_alias))
            thresholds = warning_thresholds.get(field)
            if value is None or thresholds is None:
                continue
            q1, q3 = thresholds
            if field in MEGA_CAP_WARNING_FIELDS and value >= q3:
                warning_count += 1
            elif field in OVERSOLD_WARNING_FIELDS and value <= q1:
                warning_count += 1

        period_return = _coerce_float(row.get("period_return_pct"))
        summary_row: dict[str, Any] = {
            "symbol": symbol,
            "bare_symbol": bare_symbol,
            "period_return_pct": period_return,
            "start_close_price": _coerce_float(row.get("start_close_price")),
            "end_close_price": _coerce_float(row.get("end_close_price")),
            "vs_universe_median_period_return_pp": (
                period_return - float(universe_median)
                if period_return is not None and universe_median is not None
                else None
            ),
            "atrp_1w_at_start": _coerce_float(row.get("ATRP_1W")),
            "adrp_1w_at_start": _coerce_float(row.get("ADRP_1W")),
            "relvol_at_start": _coerce_float(row.get("relative_volume")),
            "ebitda_ttm_at_start": _coerce_float(row.get("ebitda_ttm")),
            "recommend_ma_1m_at_start": _coerce_float(row.get("Recommend_MA_1M")),
            "warning_predictor_count": warning_count,
            "regime_fit_score": regime_meta.get("regime_fit_score"),
            "current_atrp_1w": regime_meta.get("current_atrp_1w"),
            "current_relative_volume": regime_meta.get("current_relative_volume"),
            "peak_cumulative_return_pct": prog.get("peak_cumulative_return_pct"),
            "max_drawdown_from_start_pct": prog.get("max_drawdown_from_start_pct"),
            "days_underwater": prog.get("days_underwater"),
            "progression_scan_days": prog.get("progression_scan_days"),
        }
        summary_row["risk_flags"] = "|".join(
            _build_risk_flags(
                summary_row,
                universe_median_period_return=_coerce_float(universe_median),
            )
        )
        summary_rows.append(summary_row)

    period_returns_path_out = resolved_output_dir / "ticker_watchlist__period_returns.csv"
    predictor_snapshot_path_out = (
        resolved_output_dir / "ticker_watchlist__predictor_snapshot.csv"
    )
    progression_path_out = resolved_output_dir / "ticker_watchlist__daily_progression.csv"
    summary_path_out = resolved_output_dir / "ticker_watchlist__summary.csv"
    summary_log_path = resolved_output_dir / "ticker_watchlist__summary.log"

    summary_fieldnames = list(summary_rows[0].keys()) if summary_rows else ["symbol"]
    _write_csv_rows(summary_path_out, summary_rows, fieldnames=summary_fieldnames)
    _write_csv_rows(
        period_returns_path_out,
        summary_rows,
        fieldnames=[
            "symbol",
            "period_return_pct",
            "start_close_price",
            "end_close_price",
            "vs_universe_median_period_return_pp",
            "regime_fit_score",
            "risk_flags",
        ],
    )
    if snapshot_rows:
        snapshot_fieldnames = sorted(
            {key for row in snapshot_rows for key in row.keys()},
            key=str,
        )
        _write_csv_rows(
            predictor_snapshot_path_out,
            snapshot_rows,
            fieldnames=snapshot_fieldnames,
        )
    if progression_rows:
        progression_fieldnames = sorted(
            {key for row in progression_rows for key in row.keys()},
            key=str,
        )
        _write_csv_rows(
            progression_path_out,
            progression_rows,
            fieldnames=progression_fieldnames,
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log_lines = [
        "SCAN-PERIOD TICKER WATCHLIST ANALYSIS",
        "=" * 120,
        f"Generated: {generated_at}",
        f"Watchlist ID: {resolved_watchlist_id}",
        f"Run root: {resolved_run_root.as_posix()}",
        f"Performance target: {performance_target}",
        f"Window: {start_day_label.replace('_', ' ')} -> {end_day_label.replace('_', ' ')}",
        f"Universe median period return: {universe_median:.2f}%"
        if universe_median is not None
        else "Universe median period return: n/a",
        f"Symbols requested: {len(normalized_tickers)} | matched: {len(summary_rows)}",
        "",
        "SUMMARY (sorted by period return)",
        "-" * 120,
    ]
    for row in summary_rows:
        flags = row.get("risk_flags") or ""
        flag_suffix = f"  FLAGS: {flags}" if flags else ""
        log_lines.append(
            f"  {row['symbol']:<12} "
            f"ret={row.get('period_return_pct') if row.get('period_return_pct') is not None else 'n/a':>7}% "
            f"RegFit={row.get('regime_fit_score') if row.get('regime_fit_score') is not None else 'n/a':>5} "
            f"ATRP|1W={row.get('atrp_1w_at_start') if row.get('atrp_1w_at_start') is not None else 'n/a':>6} "
            f"relvol={row.get('relvol_at_start') if row.get('relvol_at_start') is not None else 'n/a':>5} "
            f"warn={row.get('warning_predictor_count', 0)}{flag_suffix}"
        )
    unmatched = sorted(
        set(normalized_tickers)
        - {_normalize_symbol(str(row["symbol"])) for row in summary_rows}
    )
    if unmatched:
        log_lines.extend(["", "UNMATCHED SYMBOLS (not in period universe)", "-" * 120])
        log_lines.extend(f"  {symbol}" for symbol in unmatched)

    summary_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    manifest = {
        "watchlist_id": resolved_watchlist_id,
        "generated_at_utc": generated_at,
        "run_root": resolved_run_root.as_posix(),
        "regime_context_config_path": str(regime_context_config_path)
        if regime_context_config_path
        else None,
        "all_fields_root": resolved_all_fields_root.as_posix(),
        "tracking_id": tracking_payload.get("tracking_id") or resolved_run_root.name,
        "performance_target": performance_target,
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "symbols_requested": normalized_tickers,
        "symbols_matched": [row["symbol"] for row in summary_rows],
        "symbols_unmatched": unmatched,
        "from_regime_log": str(from_regime_log) if from_regime_log else None,
        "regime_section": regime_section if from_regime_log else None,
        "snapshot_day_labels": resolved_snapshot_days,
        "outputs": {
            "summary_csv": summary_path_out.as_posix(),
            "summary_log": summary_log_path.as_posix(),
            "period_returns_csv": period_returns_path_out.as_posix(),
            "predictor_snapshot_csv": predictor_snapshot_path_out.as_posix()
            if snapshot_rows
            else None,
            "daily_progression_csv": progression_path_out.as_posix()
            if progression_rows
            else None,
        },
    }
    manifest_path = resolved_output_dir / "ticker_watchlist__manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "watchlist_id": resolved_watchlist_id,
        "output_dir": resolved_output_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "summary_log": summary_log_path.as_posix(),
        "summary_csv": summary_path_out.as_posix(),
        "symbols_requested": len(normalized_tickers),
        "symbols_matched": len(summary_rows),
        "symbols_unmatched": unmatched,
        "summary_rows": summary_rows,
    }


__all__ = [
    "_resolve_watchlist_scan_period_run_root",
    "parse_regime_context_log_section",
    "run_scan_period_ticker_watchlist_analysis",
]
