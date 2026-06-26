from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
    DEFAULT_HORIZON_NAME,
    DEFAULT_WATCHLIST_PATH,
    HighlightMarker,
    ProgressionPoint,
    ProgressionSymbolSpecLine,
    ResolvedBackwardsProfileTarget,
    _build_watchlist_highlights,
    _normalize_symbol_candidates,
    _normalize_text,
    _slugify,
    _split_symbol_ticker,
    _write_plot_spec_json,
    _write_points_csv,
    _write_quality_json,
    build_progression_plot_title_and_spec,
    build_symbol_set_slug,
    extract_progression_series,
    render_score_progression_plotly_html,
    resolve_backwards_profile_targets,
    validate_progression_integrity,
)
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    DEFAULT_BACKWARDS_RUNS_ROOT,
    resolve_backwards_analysis_database,
)
from data_analysis_scripts.trading_view_holdings_scoring_analysis import (
    holding_matches_db_symbol_strict,
    normalize_holding_ticker,
)
from db.trading_view_backwards_prediction_duckdb import query_backwards_prediction_duckdb

RESOLUTION_EXACT = "exact_match"
RESOLUTION_UNIQUE_TICKER = "unique_ticker"
RESOLUTION_EXCHANGE_REQUEST = "exchange_from_request"
RESOLUTION_HOLDINGS_SYMBOL = "holdings_symbol"
RESOLUTION_CATALOG_EXCHANGE = "catalog_exchange"
RESOLUTION_CATALOG_MARKET_CAP = "catalog_market_cap"
RESOLUTION_SNAPSHOT_COVERAGE = "snapshot_coverage"
RESOLUTION_NOT_FOUND = "not_found"
RESOLUTION_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class BackwardsSymbolCatalogEntry:
    symbol: str
    bare_ticker: str
    exchange: str | None
    company: str | None
    anchor_count: int = 0


@dataclass(frozen=True)
class ResolvedSymbolMapping:
    requested: str
    db_symbol: str
    bare_ticker: str
    exchange: str | None
    company: str | None
    resolution: str
    candidates: tuple[str, ...] = ()


def _split_exchange_symbol(value: str) -> tuple[str | None, str]:
    normalized = normalize_holding_ticker(value)
    if not normalized:
        return None, ""
    if ":" in normalized:
        exchange, ticker = normalized.split(":", 1)
        return exchange or None, ticker
    return None, normalized


def _catalog_entry_from_row(
    symbol: str,
    company: str | None,
    *,
    anchor_count: int = 0,
) -> BackwardsSymbolCatalogEntry:
    exchange, bare_ticker = _split_exchange_symbol(symbol)
    return BackwardsSymbolCatalogEntry(
        symbol=symbol,
        bare_ticker=bare_ticker or _split_symbol_ticker(symbol),
        exchange=exchange,
        company=company,
        anchor_count=anchor_count,
    )


def resolve_backwards_progression_database(
    *,
    database_path: str | Path | None = None,
    run_folder_pattern: str | None = None,
    backwards_runs_root: str | Path | None = None,
) -> Path:
    """Resolve an existing backwards analysis DuckDB from a path or run-folder substring."""
    return resolve_backwards_analysis_database(
        database_path=database_path,
        run_folder_pattern=run_folder_pattern,
        backwards_runs_root=backwards_runs_root,
    )


def fetch_backwards_symbol_catalog(
    database_path: str | Path,
    *,
    profile_name: str | None = None,
    profile_family: str | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
) -> list[BackwardsSymbolCatalogEntry]:
    """Distinct symbols present in a backwards analysis database."""
    resolved_db = Path(database_path)
    where_clauses = ["horizon_name = ?"]
    params: list[Any] = [horizon_name]

    if profile_name is not None:
        where_clauses.append("LOWER(TRIM(profile_name)) = ?")
        params.append(profile_name.strip().lower())
    if profile_family is not None:
        where_clauses.append("LOWER(TRIM(profile_family)) = ?")
        params.append(profile_family.strip().lower())

    rows = query_backwards_prediction_duckdb(
        resolved_db,
        f"""
        SELECT symbol,
            MAX(company) AS company,
            COUNT(DISTINCT anchor_name) AS anchor_count
        FROM backwards_anchor_snapshots
        WHERE {" AND ".join(where_clauses)}
        GROUP BY symbol
        ORDER BY symbol
        """,
        parameters=params,
    )

    catalog: list[BackwardsSymbolCatalogEntry] = []
    for row in rows:
        symbol = _normalize_text(row.get("symbol"))
        if symbol is None:
            continue
        anchor_count = row.get("anchor_count")
        catalog.append(
            _catalog_entry_from_row(
                symbol,
                _normalize_text(row.get("company")),
                anchor_count=int(anchor_count) if anchor_count is not None else 0,
            )
        )
    return catalog


def load_holdings_exchange_hints(
    watchlist_path: str | Path | None = DEFAULT_WATCHLIST_PATH,
) -> dict[str, str]:
    """
    Map bare ticker -> preferred EXCHANGE:TICKER from holdings config.

    Holdings entries with an explicit ``symbol`` field (e.g. ``NYSE:CF`` for ticker ``CF``)
    are used to disambiguate duplicate bare tickers in backwards snapshots.
    """
    path = Path(watchlist_path) if watchlist_path is not None else DEFAULT_WATCHLIST_PATH
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    hints: dict[str, str] = {}
    for row in payload.get("holdings", []):
        if not isinstance(row, dict):
            continue
        symbol = _normalize_text(row.get("symbol"))
        ticker = _normalize_text(row.get("ticker"))
        if symbol is None or ticker is None:
            continue
        hints[normalize_holding_ticker(ticker)] = normalize_holding_ticker(symbol)
    return hints


def _lookup_tradingview_companies_by_ticker(ticker: str) -> list[dict[str, Any]]:
    try:
        from db.connection_credentials import BASE_DB_CONFIG
        from db.connection_provider import get_mysql_connection
    except ImportError:
        return []

    bare = normalize_holding_ticker(ticker)
    if not bare:
        return []

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT symbol, name, exchange, market_cap_basic
                FROM trading_view_company_data_map
                WHERE UPPER(symbol) = %s
                   OR UPPER(SUBSTRING_INDEX(symbol, ':', -1)) = %s
                ORDER BY market_cap_basic DESC, symbol ASC
                """,
                (bare, bare),
            )
            return list(cursor.fetchall())
    except Exception:
        return []
    finally:
        conn.close()


def _pick_candidate_by_exchange_hint(
    candidates: Sequence[BackwardsSymbolCatalogEntry],
    exchange_symbol: str,
) -> BackwardsSymbolCatalogEntry | None:
    matches = [
        entry
        for entry in candidates
        if holding_matches_db_symbol_strict(exchange_symbol, entry.symbol)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _pick_candidate_by_snapshot_coverage(
    candidates: Sequence[BackwardsSymbolCatalogEntry],
) -> tuple[BackwardsSymbolCatalogEntry | None, str | None]:
    """Prefer the backwards snapshot symbol with the most weekly anchor coverage."""
    if not candidates:
        return None, None

    max_count = max(entry.anchor_count for entry in candidates)
    if max_count <= 0:
        return None, None

    top = [entry for entry in candidates if entry.anchor_count == max_count]
    if len(top) == 1:
        return top[0], RESOLUTION_SNAPSHOT_COVERAGE

    with_exchange = [entry for entry in top if entry.exchange]
    if len(with_exchange) == 1:
        return with_exchange[0], RESOLUTION_SNAPSHOT_COVERAGE
    pool = with_exchange or top
    return sorted(pool, key=lambda entry: entry.symbol)[0], RESOLUTION_SNAPSHOT_COVERAGE


def _pick_candidate_by_catalog(
    candidates: Sequence[BackwardsSymbolCatalogEntry],
    catalog_rows: Sequence[dict[str, Any]],
) -> tuple[BackwardsSymbolCatalogEntry | None, str | None]:
    if not candidates or not catalog_rows:
        return None, None

    candidate_by_symbol = {entry.symbol.upper(): entry for entry in candidates}
    for row in catalog_rows:
        catalog_symbol = _normalize_text(row.get("symbol"))
        if catalog_symbol is None:
            continue
        matched = candidate_by_symbol.get(catalog_symbol.upper())
        if matched is not None:
            exchange = _normalize_text(row.get("exchange"))
            if exchange and matched.exchange and exchange.upper() == matched.exchange:
                return matched, RESOLUTION_CATALOG_EXCHANGE
            return matched, RESOLUTION_CATALOG_MARKET_CAP
    return None, None


def resolve_symbols_for_backwards_plot(
    requested_symbols: Sequence[str],
    catalog: Sequence[BackwardsSymbolCatalogEntry],
    *,
    holdings_hints: dict[str, str] | None = None,
    use_tradingview_catalog: bool = True,
    on_ambiguous: str = "error",
) -> tuple[list[ResolvedSymbolMapping], list[str]]:
    """
    Map user tickers (bare or EXCHANGE:TICKER) to exact backwards snapshot symbols.

    When a bare ticker matches multiple snapshot symbols (e.g. ``CF``), resolution order is:
    1. explicit ``EXCHANGE:TICKER`` in the request
    2. holdings ``symbol`` hint (``NYSE:CF``)
    3. backwards snapshot coverage (most distinct weekly anchors)
    4. TradingView company catalog (exchange / market cap)
    5. error or skip when still ambiguous
    """
    if on_ambiguous not in {"error", "skip"}:
        raise ValueError("on_ambiguous must be 'error' or 'skip'")

    hints = holdings_hints or {}
    catalog_by_symbol = {entry.symbol.upper(): entry for entry in catalog}
    catalog_by_ticker: dict[str, list[BackwardsSymbolCatalogEntry]] = {}
    for entry in catalog:
        catalog_by_ticker.setdefault(entry.bare_ticker.upper(), []).append(entry)

    resolved: list[ResolvedSymbolMapping] = []
    messages: list[str] = []

    for requested in _normalize_symbol_candidates(requested_symbols):
        request_exchange, request_ticker = _split_exchange_symbol(requested)
        strict_request = request_exchange is not None

        if strict_request:
            exact = catalog_by_symbol.get(requested.upper())
            if exact is None:
                picked = _pick_candidate_by_exchange_hint(catalog, requested)
                if picked is None:
                    messages.append(
                        f"{requested!r}: no backwards snapshot symbol matched exchange-qualified request."
                    )
                    continue
                exact = picked
            resolved.append(
                ResolvedSymbolMapping(
                    requested=requested,
                    db_symbol=exact.symbol,
                    bare_ticker=exact.bare_ticker,
                    exchange=exact.exchange,
                    company=exact.company,
                    resolution=RESOLUTION_EXACT,
                )
            )
            continue

        bare = request_ticker or requested
        candidates = catalog_by_ticker.get(bare.upper(), [])
        if not candidates:
            messages.append(f"{requested!r}: symbol not found in backwards analysis snapshots.")
            continue
        if len(candidates) == 1:
            entry = candidates[0]
            resolved.append(
                ResolvedSymbolMapping(
                    requested=requested,
                    db_symbol=entry.symbol,
                    bare_ticker=entry.bare_ticker,
                    exchange=entry.exchange,
                    company=entry.company,
                    resolution=RESOLUTION_UNIQUE_TICKER,
                )
            )
            continue

        candidate_symbols = tuple(sorted(entry.symbol for entry in candidates))
        hint_symbol = hints.get(bare.upper())
        if hint_symbol:
            picked = _pick_candidate_by_exchange_hint(candidates, hint_symbol)
            if picked is not None:
                resolved.append(
                    ResolvedSymbolMapping(
                        requested=requested,
                        db_symbol=picked.symbol,
                        bare_ticker=picked.bare_ticker,
                        exchange=picked.exchange,
                        company=picked.company,
                        resolution=RESOLUTION_HOLDINGS_SYMBOL,
                        candidates=candidate_symbols,
                    )
                )
                continue
            messages.append(
                f"{requested!r}: holdings hint {hint_symbol!r} did not match backwards candidates "
                f"{list(candidate_symbols)}."
            )

        picked, coverage_resolution = _pick_candidate_by_snapshot_coverage(candidates)
        if picked is not None and coverage_resolution is not None:
            resolved.append(
                ResolvedSymbolMapping(
                    requested=requested,
                    db_symbol=picked.symbol,
                    bare_ticker=picked.bare_ticker,
                    exchange=picked.exchange,
                    company=picked.company,
                    resolution=coverage_resolution,
                    candidates=candidate_symbols,
                )
            )
            continue

        catalog_rows: list[dict[str, Any]] = []
        if use_tradingview_catalog:
            catalog_rows = _lookup_tradingview_companies_by_ticker(bare)
        picked, catalog_resolution = _pick_candidate_by_catalog(candidates, catalog_rows)
        if picked is not None and catalog_resolution is not None:
            resolved.append(
                ResolvedSymbolMapping(
                    requested=requested,
                    db_symbol=picked.symbol,
                    bare_ticker=picked.bare_ticker,
                    exchange=picked.exchange,
                    company=picked.company,
                    resolution=catalog_resolution,
                    candidates=candidate_symbols,
                )
            )
            continue

        ambiguity = (
            f"{requested!r}: ambiguous bare ticker; backwards candidates={list(candidate_symbols)}. "
            "Pass EXCHANGE:TICKER, set holdings symbol, or ensure TradingView catalog coverage."
        )
        if on_ambiguous == "skip":
            messages.append(ambiguity)
            continue
        raise ValueError(ambiguity)

    return resolved, messages


def _catalog_symbol_aliases_for_bare_ticker(
    bare_ticker: str,
    catalog: Sequence[BackwardsSymbolCatalogEntry],
) -> tuple[str, ...]:
    key = bare_ticker.strip().upper()
    return tuple(
        sorted(
            {
                entry.symbol
                for entry in catalog
                if entry.bare_ticker.upper() == key
            }
        )
    )


def _extraction_symbols_for_mappings(
    mappings: Sequence[ResolvedSymbolMapping],
    catalog: Sequence[BackwardsSymbolCatalogEntry],
) -> list[str]:
    """All backwards snapshot symbol keys to query for the resolved bare tickers."""
    ordered: list[str] = []
    seen: set[str] = set()
    for mapping in mappings:
        candidates = (
            mapping.requested,
            mapping.db_symbol,
            mapping.bare_ticker,
            *_catalog_symbol_aliases_for_bare_ticker(mapping.bare_ticker, catalog),
        )
        for symbol in candidates:
            key = symbol.strip().upper()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(symbol)
    return ordered


def _allowed_symbols_for_mappings(
    mappings: Sequence[ResolvedSymbolMapping],
    catalog: Sequence[BackwardsSymbolCatalogEntry],
) -> set[str]:
    allowed: set[str] = set()
    for mapping in mappings:
        for symbol in (
            mapping.db_symbol,
            mapping.bare_ticker,
            *_catalog_symbol_aliases_for_bare_ticker(mapping.bare_ticker, catalog),
        ):
            allowed.add(symbol.upper())
    return allowed


def _filter_points_to_db_symbols(
    points: Sequence[ProgressionPoint],
    db_symbols: Sequence[str],
) -> list[ProgressionPoint]:
    allowed = {symbol.upper() for symbol in db_symbols}
    return [point for point in points if point.symbol.upper() in allowed]


def _filter_points_for_mappings(
    points: Sequence[ProgressionPoint],
    mappings: Sequence[ResolvedSymbolMapping],
    catalog: Sequence[BackwardsSymbolCatalogEntry],
) -> list[ProgressionPoint]:
    allowed = _allowed_symbols_for_mappings(mappings, catalog)
    return [point for point in points if point.symbol.upper() in allowed]


def _merge_progression_points_by_anchor(
    points: Sequence[ProgressionPoint],
    mappings: Sequence[ResolvedSymbolMapping],
) -> list[ProgressionPoint]:
    """
    Collapse exchange-qualified and bare snapshot rows into one point per anchor.

    Weekly pools can switch symbol keys mid-run (e.g. ``NYSE:VEEV`` early,
    ``VEEV`` later). Prefer the resolved ``db_symbol`` when both exist.
    """
    mapping_by_bare = {mapping.bare_ticker.upper(): mapping for mapping in mappings}
    groups: dict[tuple[str, str, bool], list[ProgressionPoint]] = {}
    for point in points:
        bare = _split_symbol_ticker(point.symbol).upper()
        if bare not in mapping_by_bare:
            continue
        key = (bare, point.anchor_name, point.is_current)
        groups.setdefault(key, []).append(point)

    merged: list[ProgressionPoint] = []
    for (bare, _anchor_name, _is_current), group in groups.items():
        mapping = mapping_by_bare[bare]
        preferred = {mapping.db_symbol.upper(), mapping.bare_ticker.upper()}
        picked: ProgressionPoint | None = None
        for candidate in group:
            if candidate.symbol.upper() not in preferred:
                continue
            picked = candidate
            if candidate.symbol.upper() == mapping.db_symbol.upper():
                break
        merged.append(picked or group[0])

    merged.sort(key=lambda point: (point.point_time, point.anchor_name, point.symbol))
    return merged


def _series_chart_label(mapping: ResolvedSymbolMapping) -> str:
    if mapping.exchange:
        label = mapping.db_symbol
    else:
        label = mapping.bare_ticker
    if mapping.company:
        return f"{label} ({mapping.company})"
    return label


def _remap_points_for_chart(
    points: Sequence[ProgressionPoint],
    mappings: Sequence[ResolvedSymbolMapping],
) -> list[ProgressionPoint]:
    """Replace symbol with a readable chart label while preserving db_symbol in company suffix."""
    label_by_symbol: dict[str, str] = {}
    for mapping in mappings:
        label = _series_chart_label(mapping)
        label_by_symbol[mapping.db_symbol.upper()] = label
        label_by_symbol[mapping.bare_ticker.upper()] = label
    remapped: list[ProgressionPoint] = []
    for point in points:
        bare = _split_symbol_ticker(point.symbol).upper()
        chart_label = (
            label_by_symbol.get(point.symbol.upper())
            or label_by_symbol.get(bare)
            or point.symbol
        )
        if chart_label == point.symbol:
            remapped.append(point)
            continue
        remapped.append(
            ProgressionPoint(
                backwards_analysis_id=point.backwards_analysis_id,
                point_time=point.point_time,
                anchor_name=point.anchor_name,
                is_current=point.is_current,
                run_id=point.run_id,
                snapshot_label=point.snapshot_label,
                profile_name=point.profile_name,
                profile_family=point.profile_family,
                anchor_profile_name=point.anchor_profile_name,
                horizon_name=point.horizon_name,
                symbol=chart_label,
                company=point.company,
                sector=point.sector,
                industry=point.industry,
                score=point.score,
                close=point.close,
                profile_rank=point.profile_rank,
                direction=point.direction,
                confidence=point.confidence,
            )
        )
    return remapped


def _symbol_spec_lines_from_mappings(
    mappings: Sequence[ResolvedSymbolMapping],
) -> list[ProgressionSymbolSpecLine]:
    return [
        ProgressionSymbolSpecLine(
            requested=mapping.requested,
            plotted_label=_series_chart_label(mapping),
            db_symbol=mapping.db_symbol,
            company=mapping.company,
            note=mapping.resolution if mapping.resolution not in {"exact_match", "unique_ticker"} else None,
        )
        for mapping in mappings
    ]


def _plot_symbols_for_profile_target(
    *,
    resolved_db: Path,
    run_slug: str,
    profile_target: ResolvedBackwardsProfileTarget,
    mappings: Sequence[ResolvedSymbolMapping],
    catalog: Sequence[BackwardsSymbolCatalogEntry],
    resolution_messages: Sequence[str],
    symbols: Sequence[str],
    horizon_name: str,
    run_output_dir: Path,
    title: str | None,
    include_watchlist_highlights: bool,
    include_close_secondary_axis: bool,
    close_price_visible_by_default: bool,
    include_chart_subtitle: bool,
    plots_root_dir: Path | None = None,
    allow_empty: bool = False,
) -> dict[str, Any] | None:
    extraction_symbols = _extraction_symbols_for_mappings(mappings, catalog)
    points = extract_progression_series(
        resolved_db,
        profile_name=profile_target.profile_name,
        horizon_name=horizon_name,
        symbols=extraction_symbols,
        include_current=True,
    )
    points = _merge_progression_points_by_anchor(
        _filter_points_for_mappings(points, mappings, catalog),
        mappings,
    )
    if not points:
        if allow_empty:
            return None
        resolved_labels = [mapping.db_symbol for mapping in mappings]
        raise ValueError(
            "No progression points extracted for resolved symbols "
            f"{resolved_labels} on profile {profile_target.profile_name!r}"
        )

    chart_points = _remap_points_for_chart(points, mappings)
    highlights: list[HighlightMarker] = []
    if include_watchlist_highlights:
        highlights = _build_watchlist_highlights(
            points,
            [mapping.bare_ticker for mapping in mappings],
        )

    quality = validate_progression_integrity(chart_points)
    symbol_labels = [mapping.bare_ticker for mapping in mappings]
    symbol_slug = build_symbol_set_slug(symbol_labels)
    profile_slug = _slugify(profile_target.profile_name)

    chart_title, plot_spec_text, plot_spec_dict = build_progression_plot_title_and_spec(
        profile_name=profile_target.profile_name,
        horizon_name=horizon_name,
        symbol_lines=_symbol_spec_lines_from_mappings(mappings),
        title_override=title,
        include_chart_subtitle=include_chart_subtitle,
    )
    plot_spec_dict["requested_symbols"] = list(symbols)
    plot_spec_dict["resolution_messages"] = list(resolution_messages)
    from data_analysis_scripts.trading_view_plot_browser_view import (
        BackwardsPlotRef,
        register_backwards_plot,
        resolve_backwards_plot_html_path,
        write_plot_locations_sidecar,
    )

    html_path = resolve_backwards_plot_html_path(run_slug, symbol_slug, profile_slug)
    render_score_progression_plotly_html(
        chart_points,
        highlights,
        title=chart_title,
        plot_spec=plot_spec_text or None,
        output_path=html_path,
        include_close_secondary_axis=include_close_secondary_axis,
        close_price_visible_by_default=close_price_visible_by_default,
    )
    plot_entry = register_backwards_plot(
        html_path,
        plot_ref=BackwardsPlotRef(
            run_slug=run_slug,
            symbol_slug=symbol_slug,
            profile_slug=profile_slug,
            horizon_name=horizon_name,
            database_path=resolved_db.as_posix(),
            run_data_dir=run_output_dir.as_posix(),
            title=chart_title,
        ),
        plots_root_dir=plots_root_dir,
    )
    write_plot_locations_sidecar(
        run_output_dir,
        plot_entry=plot_entry,
        profile_slug=profile_slug,
    )

    points_csv = (
        run_output_dir
        / f"score_progression_points__{profile_slug}__{symbol_slug}__{horizon_name}.csv"
    )
    _write_points_csv(points, points_csv)
    mappings_path = run_output_dir / f"symbol_resolution__{profile_slug}__{symbol_slug}.json"
    mappings_path.write_text(
        json.dumps(
            {
                "requested_symbols": list(symbols),
                "resolved_profile_name": profile_target.profile_name,
                "resolved_profile_family": profile_target.profile_family,
                "resolved": [asdict(mapping) for mapping in mappings],
                "messages": list(resolution_messages),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    plot_spec_path = run_output_dir / f"plot_spec__{profile_slug}__{symbol_slug}.json"
    _write_plot_spec_json(plot_spec_dict, plot_spec_path)
    quality_path = (
        run_output_dir
        / f"progression_data_quality__{profile_slug}__{symbol_slug}__{horizon_name}.json"
    )
    _write_quality_json(quality, quality_path)

    resolved_symbols = [mapping.db_symbol for mapping in mappings]
    return {
        "profile_family": profile_target.profile_family,
        "resolved_profile_name": profile_target.profile_name,
        "html_path": plot_entry["html_path"],
        "browser_html_path": plot_entry["browser_html_path"],
        "browser_file_uri": plot_entry["browser_file_uri"],
        "plots_index_file_uri": plot_entry.get("plots_index_file_uri"),
        "latest_index_file_uri": plot_entry.get("latest_index_file_uri"),
        "symbol_index_file_uri": plot_entry.get("symbol_index_file_uri"),
        "run_index_file_uri": plot_entry.get("run_index_file_uri"),
        "points_csv": points_csv.as_posix(),
        "symbol_resolution_path": mappings_path.as_posix(),
        "plot_spec_path": plot_spec_path.as_posix(),
        "quality_report_path": quality_path.as_posix(),
        "point_count": len(points),
        "highlight_count": len(highlights),
        "resolved_symbols": resolved_symbols,
        "symbol_mappings": mappings,
        "resolution_messages": list(resolution_messages),
        "quality": quality,
    }


def plot_backwards_progression_for_symbols(
    *,
    symbols: Sequence[str],
    database_path: str | Path | None = None,
    run_folder_pattern: str | None = None,
    backwards_runs_root: str | Path | None = None,
    profile_name: str | None = None,
    profile_family: str | None = None,
    profile_families: Sequence[str] | None = None,
    profile_names: Sequence[str] | None = None,
    profile_suite_path: str | Path | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    watchlist_path: str | Path | None = DEFAULT_WATCHLIST_PATH,
    output_dir: str | Path | None = None,
    title: str | None = None,
    on_ambiguous: str = "error",
    use_tradingview_catalog: bool = True,
    include_watchlist_highlights: bool = True,
    include_close_secondary_axis: bool = True,
    close_price_visible_by_default: bool = False,
    include_chart_subtitle: bool = False,
    plots_root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Plot score progression for explicit tickers from an existing backwards analysis run.

    Provide ``database_path`` or ``run_folder_pattern`` (substring of the run folder name).
    Symbols may be bare tickers (``CF``) or exchange-qualified (``NYSE:CF``).

    Pass ``profile_suite_path`` to plot one chart per suite family, using the latest
    profile version present in the backwards database for each family.
    """
    started = time.perf_counter()
    if not symbols:
        raise ValueError("At least one symbol is required.")

    resolved_db = resolve_backwards_progression_database(
        database_path=database_path,
        run_folder_pattern=run_folder_pattern,
        backwards_runs_root=backwards_runs_root,
    )
    profile_targets, profile_messages = resolve_backwards_profile_targets(
        resolved_db,
        horizon_name=horizon_name,
        profile_name=profile_name,
        profile_family=profile_family,
        profile_families=profile_families,
        profile_names=profile_names,
        profile_suite_path=profile_suite_path,
    )

    catalog = fetch_backwards_symbol_catalog(
        resolved_db,
        horizon_name=horizon_name,
    )
    holdings_hints = load_holdings_exchange_hints(watchlist_path)
    mappings, resolution_messages = resolve_symbols_for_backwards_plot(
        symbols,
        catalog,
        holdings_hints=holdings_hints,
        use_tradingview_catalog=use_tradingview_catalog,
        on_ambiguous=on_ambiguous,
    )
    if not mappings:
        detail = "; ".join(resolution_messages) if resolution_messages else "no symbols resolved"
        raise ValueError(f"No symbols could be resolved for plotting: {detail}")

    symbol_slug = build_symbol_set_slug([mapping.bare_ticker for mapping in mappings])
    from data_analysis_scripts.trading_view_plot_browser_view import extract_backwards_run_slug

    run_slug = extract_backwards_run_slug(resolved_db.parent)
    run_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_db.parent / "progression_plots" / f"symbols__{symbol_slug}"
    )
    run_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_plots_root = Path(plots_root_dir).resolve() if plots_root_dir is not None else None

    profile_plots: list[dict[str, Any]] = []
    allow_empty_profiles = len(profile_targets) > 1
    for profile_target in profile_targets:
        plot_result = _plot_symbols_for_profile_target(
            resolved_db=resolved_db,
            run_slug=run_slug,
            profile_target=profile_target,
            mappings=mappings,
            catalog=catalog,
            resolution_messages=resolution_messages,
            symbols=symbols,
            horizon_name=horizon_name,
            run_output_dir=run_output_dir,
            title=title,
            include_watchlist_highlights=include_watchlist_highlights,
            include_close_secondary_axis=include_close_secondary_axis,
            close_price_visible_by_default=close_price_visible_by_default,
            include_chart_subtitle=include_chart_subtitle,
            plots_root_dir=resolved_plots_root,
            allow_empty=allow_empty_profiles,
        )
        if plot_result is None:
            profile_messages.append(
                f"Profile {profile_target.profile_name!r}: none of the resolved symbols "
                "appear in backwards snapshots for this profile; skipped."
            )
            continue
        profile_plots.append(plot_result)

    if not profile_plots:
        db_symbols = [mapping.db_symbol for mapping in mappings]
        raise ValueError(
            "No progression points extracted for resolved symbols "
            f"{db_symbols} across {len(profile_targets)} requested profile(s)"
        )

    primary_plot = profile_plots[0]
    result = {
        "database_path": resolved_db.as_posix(),
        "run_slug": run_slug,
        "html_path": primary_plot["html_path"],
        "browser_html_path": primary_plot["browser_html_path"],
        "browser_file_uri": primary_plot["browser_file_uri"],
        "plots_index_file_uri": primary_plot.get("plots_index_file_uri"),
        "latest_index_file_uri": primary_plot.get("latest_index_file_uri"),
        "run_index_file_uri": primary_plot.get("run_index_file_uri"),
        "points_csv": primary_plot["points_csv"],
        "symbol_resolution_path": primary_plot["symbol_resolution_path"],
        "quality_report_path": primary_plot["quality_report_path"],
        "point_count": sum(plot["point_count"] for plot in profile_plots),
        "highlight_count": sum(plot["highlight_count"] for plot in profile_plots),
        "resolved_symbols": primary_plot["resolved_symbols"],
        "symbol_mappings": mappings,
        "resolution_messages": resolution_messages,
        "resolved_profile_name": primary_plot["resolved_profile_name"],
        "resolved_profile_family": primary_plot["profile_family"],
        "resolved_profile_targets": profile_targets,
        "profile_plots": profile_plots,
        "profile_resolution_messages": profile_messages,
        "quality": primary_plot["quality"],
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }
    if len(profile_plots) > 1:
        manifest_path = run_output_dir / f"symbol_plot_manifest__{symbol_slug}__{horizon_name}.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "profile_plots": profile_plots,
                    "profile_resolution_messages": profile_messages,
                    "resolved_profile_targets": [
                        asdict(target) for target in profile_targets
                    ],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        result["plot_manifest_path"] = manifest_path.as_posix()
    return result


__all__ = [
    "BackwardsSymbolCatalogEntry",
    "ResolvedSymbolMapping",
    "fetch_backwards_symbol_catalog",
    "load_holdings_exchange_hints",
    "plot_backwards_progression_for_symbols",
    "resolve_backwards_progression_database",
    "resolve_symbols_for_backwards_plot",
]
