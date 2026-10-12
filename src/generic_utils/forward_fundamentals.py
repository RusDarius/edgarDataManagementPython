"""Forward PE, EV/EBITDA, debt, and free cash flow from financial_projection.

TradingView ``price_earnings_forward_fy`` is empty on recent all-fields
scans. These columns replace that gap for suites and analysis.

Next year and the far year (at most 5) prefer the projection year grid
(``path``). When that cell is missing, trailing levels are compounded with
a CAGR (``cagr``). A one-year growth rate is not compounded out to year 5.

Forward PE is today's market cap / that year's net income.
Forward EV/EBITDA is today's enterprise value / that year's EBITDA.
Neither multiple is filled with EV/Rev or with trailing PE.

``fp_*_y5`` uses ``fp_horizon_year`` (the last projected year, capped at 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.symbol_identity import parse_symbol

MAX_HORIZON_YEAR = 5
GROWTH_CLIP_LOW = -0.80
GROWTH_CLIP_HIGH = 1.50

PACK_FORWARD_KEYS: tuple[str, ...] = (
    "pe_fwd",
    "pe_fwd_tv",
    "pe_fwd_nongaap",
    "pe_fwd_source",
    "evebitda_fwd",
    "fwd_pe",
    "fwd_pe_source",
    "fwd_evebitda",
    "fp_horizon_year",
    "fp_pe_y1",
    "fp_pe_y5",
    "fp_pe_method",
    "fp_pe_y5_method",
    "fp_evebitda_y1",
    "fp_evebitda_y5",
    "fp_evebitda_method",
    "fp_evebitda_y5_method",
    "fp_ev",
    "fp_net_debt_y1",
    "fp_net_debt_y5",
    "fp_debt_method",
    "fp_fcf_y1",
    "fp_fcf_y5",
    "fp_fcf_method",
    "fp_ni_y1",
    "fp_ni_y5",
    "fp_ebitda_y1",
    "fp_ebitda_y5",
    "fp_rev_y1",
    "fp_rev_y5",
    "ni",
    "ebitda",
    "fcf",
    "net_debt",
    "total_debt",
    "cash",
    "ev",
)

_CACHE: dict[tuple[str, str, str], "ProjectionContext"] = {}


@dataclass
class ProjectionContext:
    """Indexed projection and trailing levels. Keys are full symbols and bare tickers."""

    growth_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    growth_years: dict[str, dict[str, Any]] = field(default_factory=dict)
    price_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    price_years: dict[str, dict[str, Any]] = field(default_factory=dict)
    trailing: dict[str, dict[str, Any]] = field(default_factory=dict)
    growth_db: str | None = None
    price_db: str | None = None
    all_fields_db: str | None = None
    warning: str = ""


def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_names(db: str | Path) -> set[str]:
    from generic_utils.scan_sources import rows_from_duckdb

    names: set[str] = set()
    for row in rows_from_duckdb(db, "SHOW TABLES"):
        name = row.get("name") or row.get("table_name")
        if name:
            names.add(str(name))
    return names


def _column_names(db: str | Path, table: str) -> set[str]:
    from generic_utils.scan_sources import rows_from_duckdb

    ident = _qident(table)
    return {
        str(row.get("name"))
        for row in rows_from_duckdb(db, f"PRAGMA table_info({ident})")
        if row.get("name")
    }


def _store(index: dict[str, dict[str, Any]], symbol: Any, payload: Mapping[str, Any], *, rank: float) -> None:
    full = parse_symbol(symbol).normalized
    body = dict(payload)
    body["_rank"] = rank
    if full:
        current = index.get(full)
        if current is None or rank >= float(current.get("_rank") or 0):
            index[full] = body


def _bound(index: Mapping[str, Mapping[str, Any]]) -> tuple[Mapping[str, Mapping[str, Any]], Any]:
    """Catalog once. A later row must not rescan ``index.keys()``."""
    from generic_utils.symbol_identity import SymbolCatalog

    return index, SymbolCatalog.build(index.keys())


def _lookup(
    index: Mapping[str, Mapping[str, Any]] | tuple[Mapping[str, Mapping[str, Any]], Any],
    symbol: Any,
) -> Mapping[str, Any] | None:
    from generic_utils.symbol_identity import lookup_mapping

    if isinstance(index, tuple):
        mapping, catalog = index
        found = lookup_mapping(mapping, symbol, catalog)
    else:
        found = lookup_mapping(index, symbol)
    return found if isinstance(found, Mapping) else None


def _num(value: Any) -> float | None:
    return to_float(value)


def _row_num(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _ctx_num(
    indexes: Sequence[Mapping[str, Mapping[str, Any]]],
    symbol: Any,
    *keys: str,
) -> float | None:
    for index in indexes:
        found = _lookup(index, symbol)
        if not found:
            continue
        for key in keys:
            value = _num(found.get(key))
            if value is not None:
                return value
    return None


def _first(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _fraction(pct: float | None) -> float | None:
    if pct is None:
        return None
    return max(GROWTH_CLIP_LOW, min(GROWTH_CLIP_HIGH, pct / 100.0))


def _first_fraction(year: int, cagr_pcts: Sequence[float | None], yoy_pcts: Sequence[float | None]) -> float | None:
    for pct in cagr_pcts:
        fraction = _fraction(pct)
        if fraction is not None:
            return fraction
    if year != 1:
        return None
    for pct in yoy_pcts:
        fraction = _fraction(pct)
        if fraction is not None:
            return fraction
    return None


def _compound(level: float | None, fraction: float | None, year: int) -> float | None:
    if level is None or fraction is None or year < 1:
        return None
    return level * ((1.0 + fraction) ** year)


def _resolve_level(
    path_value: float | None,
    trailing: float | None,
    fraction: float | None,
    year: int,
) -> tuple[float | None, str | None]:
    if path_value is not None:
        return path_value, "path"
    grown = _compound(trailing, fraction, year)
    if grown is None:
        return None, None
    return grown, "cagr"


def _multiple(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or numerator <= 0 or denominator <= 0:
        return None
    return numerator / denominator


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _year_of(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    year = int(round(number))
    if abs(number - year) > 1e-6:
        return None
    return year


def index_summary_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        rank = abs(
            _first(
                _row_num(row, "market_cap_basic", "mcap"),
                _row_num(row, "total_revenue_ttm", "rev_ttm"),
                _row_num(row, "net_income_ttm", "ni"),
            )
            or 0.0
        )
        _store(index, symbol, row, rank=rank)
    return index


def index_year_grid(
    rows: Sequence[Mapping[str, Any]],
    measures: Mapping[str, str],
    *,
    lane: str | None = None,
    scenario: str = "base",
) -> dict[str, dict[str, Any]]:
    """Collapse raw year-grid rows to y1 and the far year (<= 5) per symbol."""
    buckets: dict[str, dict[int, Mapping[str, Any]]] = {}
    symbols: dict[str, Any] = {}
    for row in rows:
        if lane is not None and str(row.get("growth_lane") or "") != lane:
            continue
        if str(row.get("scenario") or "").lower() != scenario:
            continue
        year = _year_of(row.get("year"))
        if year is None or year < 1 or year > MAX_HORIZON_YEAR:
            continue
        symbol = row.get("symbol")
        if not symbol:
            continue
        key = str(symbol).strip().upper()
        buckets.setdefault(key, {})[year] = row
        symbols[key] = symbol
    index: dict[str, dict[str, Any]] = {}
    for key, by_year in buckets.items():
        far = max(by_year)
        y1 = by_year.get(1) or {}
        yfar = by_year[far]
        payload: dict[str, Any] = {"horizon_year": far}
        for alias, column in measures.items():
            payload[f"{alias}_y1"] = _num(y1.get(column)) if y1 else None
            payload[f"{alias}_y5"] = _num(yfar.get(column))
        rank = abs(_num(payload.get("ni_y1")) or _num(payload.get("rev_y1")) or 0.0)
        _store(index, symbols[key], payload, rank=rank)
    return index


def index_pivot_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        rank = abs(_num(row.get("ni_y1")) or _num(row.get("rev_y1")) or 0.0)
        _store(index, symbol, row, rank=rank)
    return index


def context_from_parts(
    *,
    growth_summary: Sequence[Mapping[str, Any]] = (),
    growth_years: Sequence[Mapping[str, Any]] = (),
    price_summary: Sequence[Mapping[str, Any]] = (),
    price_years: Sequence[Mapping[str, Any]] = (),
    trailing: Sequence[Mapping[str, Any]] = (),
) -> ProjectionContext:
    """Build a context from in-memory rows. Used by tests and by the loader."""
    return ProjectionContext(
        growth_summary=index_summary_rows(growth_summary),
        growth_years=index_year_grid(
            growth_years,
            {"rev": "total_revenue_ttm", "ebitda": "ebitda", "ni": "net_income_ttm"},
            lane="own",
            scenario="base",
        ),
        price_summary=index_summary_rows(price_summary),
        price_years=index_year_grid(
            price_years,
            {"net_debt": "net_debt"},
            scenario="base",
        ),
        trailing=index_summary_rows(trailing),
    )


def _pivot_sql(table: str, measures: Mapping[str, str], where_sql: str) -> str:
    select_cols = ", ".join(_qident(column) for column in measures.values())
    bits: list[str] = ["MAX(far.year_far) AS horizon_year"]
    for alias, column in measures.items():
        ident = f"src.{_qident(column)}"
        bits.append(f"MAX(CASE WHEN src.year = 1 THEN {ident} END) AS {alias}_y1")
        bits.append(
            f"MAX(CASE WHEN src.year = far.year_far THEN {ident} END) AS {alias}_y5"
        )
    return f"""
        WITH src AS (
            SELECT symbol,
                   TRY_CAST(year AS INTEGER) AS year,
                   {select_cols}
            FROM {_qident(table)}
            WHERE {where_sql}
              AND TRY_CAST(year AS INTEGER) BETWEEN 1 AND {MAX_HORIZON_YEAR}
        ),
        far AS (
            SELECT symbol, MAX(year) AS year_far
            FROM src
            GROUP BY symbol
        )
        SELECT src.symbol, {", ".join(bits)}
        FROM src
        JOIN far USING (symbol)
        GROUP BY src.symbol
    """


def _load_summary(db: str | Path, table: str, columns: Sequence[str], where_sql: str) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_duckdb

    present = _column_names(db, table)
    chosen = [column for column in columns if column == "symbol" or column in present]
    if "symbol" not in chosen:
        return []
    sql = (
        f"SELECT {', '.join(_qident(column) for column in chosen)} "
        f"FROM {_qident(table)} WHERE {where_sql}"
    )
    return rows_from_duckdb(db, sql)


def _load_trailing(db: str | Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_duckdb

    tables = _table_names(db)
    if "all_fields_rows" not in tables:
        return []
    present = _column_names(db, "all_fields_rows")
    alias_of = {
        "fcf": "free_cash_flow_ttm",
        "fcf_cagr": "free_cash_flow_cagr_5y",
        "ni": "net_income_ttm",
        "ni_cagr": "net_income_cagr_5y",
        "ebitda": "ebitda_ttm",
        "ebitda_yoy": "ebitda_yoy_growth_ttm",
        "rev_ttm": "total_revenue_ttm",
        "rev_cagr": "total_revenue_cagr_5y",
        "rev_yoy": "total_revenue_yoy_growth_ttm",
        "net_debt": "net_debt",
        "total_debt": "total_debt",
        "cash": "cash_n_equivalents_fq",
        "ev": "enterprise_value_current",
        "mcap": "market_cap_basic",
        "pe_fwd_nongaap": "non_gaap_price_to_earnings_per_share_forecast_next_fy",
    }
    bits = ["symbol"]
    for alias, column in alias_of.items():
        if column in present:
            bits.append(f"{_qident(column)} AS {alias}")
    if len(bits) == 1:
        return []
    params: list[Any] = []
    where = ""
    if "run_id" in present and "run_metadata" in tables:
        meta = rows_from_duckdb(
            db,
            "SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC NULLS LAST LIMIT 1",
        )
        run_id = str(meta[0].get("run_id")) if meta and meta[0].get("run_id") else ""
        if run_id:
            where = " WHERE run_id = ?"
            params.append(run_id)
    return rows_from_duckdb(
        db,
        f"SELECT {', '.join(bits)} FROM all_fields_rows{where}",
        params,
    )


def load_projection_context(
    growth_db: str | Path | None = None,
    price_db: str | Path | None = None,
    all_fields_db: str | Path | None = None,
    *,
    discover: bool = True,
) -> ProjectionContext:
    """Latest fingrowth, finproj, and all-fields trailing levels. Missing files are skipped."""
    if discover:
        from generic_utils.forward_value import discover_latest_projection_db

        if not growth_db:
            found = discover_latest_projection_db(prefix="fingrowth_")
            growth_db = str(found) if found else None
        if not price_db:
            found = discover_latest_projection_db(prefix="finproj_")
            price_db = str(found) if found else None
        if not all_fields_db:
            from financial_projection.load_all_fields import discover_latest_all_fields_db

            found_af = discover_latest_all_fields_db()
            all_fields_db = str(found_af) if found_af else None
    key = (str(growth_db or ""), str(price_db or ""), str(all_fields_db or ""))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    warnings: list[str] = []
    growth_summary: dict[str, dict[str, Any]] = {}
    growth_years: dict[str, dict[str, Any]] = {}
    price_summary: dict[str, dict[str, Any]] = {}
    price_years: dict[str, dict[str, Any]] = {}
    trailing: dict[str, dict[str, Any]] = {}
    if growth_db and Path(growth_db).exists():
        try:
            tables = _table_names(growth_db)
            if "growth_summary" in tables:
                growth_summary = index_summary_rows(
                    _load_summary(
                        growth_db,
                        "growth_summary",
                        (
                            "symbol",
                            "close",
                            "market_cap_basic",
                            "total_revenue_ttm",
                            "ebitda",
                            "net_income_ttm",
                            "revenue_cagr_implied_pct",
                            "ebitda_cagr_implied_pct",
                            "net_income_cagr_implied_pct",
                            "y1_revenue_growth_pct",
                            "y1_ebitda_growth_pct",
                        ),
                        "growth_lane = 'own' AND lower(CAST(scenario AS VARCHAR)) = 'base'",
                    )
                )
            if "growth_year_grid" in tables:
                columns = _column_names(growth_db, "growth_year_grid")
                measures = {
                    alias: column
                    for alias, column in (
                        ("rev", "total_revenue_ttm"),
                        ("ebitda", "ebitda"),
                        ("ni", "net_income_ttm"),
                    )
                    if column in columns
                }
                if measures and "growth_lane" in columns:
                    from generic_utils.scan_sources import rows_from_duckdb

                    growth_years = index_pivot_rows(
                        rows_from_duckdb(
                            growth_db,
                            _pivot_sql(
                                "growth_year_grid",
                                measures,
                                "growth_lane = 'own' AND lower(CAST(scenario AS VARCHAR)) = 'base'",
                            ),
                        )
                    )
        except Exception as exc:
            warnings.append(f"growth: {exc}")
    if price_db and Path(price_db).exists():
        try:
            tables = _table_names(price_db)
            if "projection_summary" in tables:
                price_summary = index_summary_rows(
                    _load_summary(
                        price_db,
                        "projection_summary",
                        (
                            "symbol",
                            "close",
                            "market_cap_basic",
                            "net_debt",
                            "enterprise_value_current",
                            "free_cash_flow_cagr_5y",
                        ),
                        "lower(CAST(scenario AS VARCHAR)) = 'base'",
                    )
                )
            if "projection_year_grid" in tables and "net_debt" in _column_names(
                price_db, "projection_year_grid"
            ):
                from generic_utils.scan_sources import rows_from_duckdb

                price_years = index_pivot_rows(
                    rows_from_duckdb(
                        price_db,
                        _pivot_sql(
                            "projection_year_grid",
                            {"net_debt": "net_debt"},
                            "lower(CAST(scenario AS VARCHAR)) = 'base'",
                        ),
                    )
                )
        except Exception as exc:
            warnings.append(f"price: {exc}")
    if all_fields_db and Path(all_fields_db).exists():
        try:
            trailing = index_summary_rows(_load_trailing(all_fields_db))
        except Exception as exc:
            warnings.append(f"all-fields: {exc}")
    ctx = ProjectionContext(
        growth_summary=growth_summary,
        growth_years=growth_years,
        price_summary=price_summary,
        price_years=price_years,
        trailing=trailing,
        growth_db=str(growth_db) if growth_db else None,
        price_db=str(price_db) if price_db else None,
        all_fields_db=str(all_fields_db) if all_fields_db else None,
        warning="; ".join(warnings),
    )
    _CACHE[key] = ctx
    return ctx


def _pull(
    row: Mapping[str, Any],
    indexes: Sequence[Mapping[str, Mapping[str, Any]]],
    symbol: Any,
    row_keys: Sequence[str],
    ctx_keys: Sequence[str],
) -> float | None:
    value = _row_num(row, *row_keys)
    if value is not None:
        return value
    return _ctx_num(indexes, symbol, *ctx_keys)


def _debt_forward(
    *,
    current: float | None,
    path_y0: float | None,
    path_year: float | None,
) -> tuple[float | None, str | None]:
    """Scale today's net debt by the model path. Fall back to the path level."""
    if path_year is None:
        return None, None
    if current is not None and path_y0 not in (None, 0):
        return current * (path_year / float(path_y0)), "path"
    return path_year, "path"


def _quote_forward_pe(rec: dict[str, Any], fp_pe_y1: float | None, pe_method: str | None) -> None:
    source_now = rec.get("pe_fwd_source")
    if "pe_fwd_tv" not in rec:
        if source_now in {"fp_path", "fp_cagr", "tv_nongaap"}:
            rec["pe_fwd_tv"] = None
        else:
            rec["pe_fwd_tv"] = _num(rec.get("pe_fwd"))
    tv = _num(rec.get("pe_fwd_tv"))
    nongaap = _num(rec.get("pe_fwd_nongaap"))
    if fp_pe_y1 is not None and fp_pe_y1 > 0:
        rec["pe_fwd"] = _round(fp_pe_y1)
        rec["pe_fwd_source"] = "fp_path" if pe_method == "path" else "fp_cagr"
    elif tv is not None and tv > 0:
        rec["pe_fwd"] = _round(tv)
        rec["pe_fwd_source"] = "tv"
    elif nongaap is not None and nongaap > 0:
        rec["pe_fwd"] = _round(nongaap)
        rec["pe_fwd_source"] = "tv_nongaap"
    else:
        rec["pe_fwd"] = None
        rec["pe_fwd_source"] = None
    rec["fwd_pe"] = rec.get("pe_fwd")
    rec["fwd_pe_source"] = rec.get("pe_fwd_source")
    rec["fwd_evebitda"] = rec.get("evebitda_fwd")


def annotate_projection_forward(
    rows: Sequence[Mapping[str, Any]],
    context: ProjectionContext | None = None,
) -> list[dict[str, Any]]:
    """Attach projection forwards, discovering the latest dumps when needed."""
    ctx = context
    if ctx is None:
        try:
            ctx = load_projection_context()
        except Exception:
            ctx = ProjectionContext()
    return attach_forward_fundamentals(rows, context=ctx)


def attach_forward_fundamentals(
    rows: Sequence[Mapping[str, Any]],
    context: ProjectionContext | None = None,
) -> list[dict[str, Any]]:
    """Attach projection forward multiples. Does not fill PE from EV/Rev."""
    ctx = context or ProjectionContext()
    growth_maps = (_bound(ctx.growth_summary),)
    trailing_maps = (_bound(ctx.trailing),)
    price_maps = (_bound(ctx.price_summary),)
    growth_years_index = _bound(ctx.growth_years)
    price_years_index = _bound(ctx.price_years)
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        symbol = rec.get("symbol") or rec.get("ticker")
        if _num(rec.get("pe_fwd_nongaap")) is None:
            nongaap = _ctx_num(trailing_maps, symbol, "pe_fwd_nongaap")
            if nongaap is not None:
                rec["pe_fwd_nongaap"] = nongaap

        mcap = _first(
            _pull(rec, growth_maps, symbol, ("mcap", "market_cap_basic"), ("market_cap_basic", "mcap")),
            _ctx_num(trailing_maps, symbol, "mcap", "market_cap_basic"),
            _ctx_num(price_maps, symbol, "market_cap_basic", "mcap"),
        )
        ni_trail = _first(
            _pull(rec, growth_maps, symbol, ("ni", "net_income_ttm"), ("net_income_ttm", "ni")),
            _ctx_num(trailing_maps, symbol, "ni", "net_income_ttm"),
        )
        ebitda_trail = _first(
            _pull(rec, growth_maps, symbol, ("ebitda", "ebitda_ttm"), ("ebitda", "ebitda_ttm")),
            _ctx_num(trailing_maps, symbol, "ebitda", "ebitda_ttm"),
        )
        rev_trail = _first(
            _pull(rec, growth_maps, symbol, ("rev_ttm", "total_revenue_ttm"), ("total_revenue_ttm", "rev_ttm")),
            _ctx_num(trailing_maps, symbol, "rev_ttm", "total_revenue_ttm"),
        )
        fcf_trail = _first(
            _row_num(rec, "fcf", "free_cash_flow_ttm", "free_cash_flow"),
            _ctx_num(trailing_maps, symbol, "fcf", "free_cash_flow_ttm"),
        )
        net_debt_now = _first(
            _row_num(rec, "net_debt"),
            _ctx_num(trailing_maps, symbol, "net_debt"),
            _ctx_num(price_maps, symbol, "net_debt"),
        )
        total_debt = _first(
            _row_num(rec, "total_debt"),
            _ctx_num(trailing_maps, symbol, "total_debt"),
        )
        cash = _first(_row_num(rec, "cash"), _ctx_num(trailing_maps, symbol, "cash"))
        if net_debt_now is None and total_debt is not None and cash is not None:
            net_debt_now = total_debt - cash
        ev_now = _first(
            _row_num(rec, "ev", "enterprise_value_current"),
            _ctx_num(trailing_maps, symbol, "ev", "enterprise_value_current"),
        )
        if ev_now is None or ev_now <= 0:
            if mcap is not None and mcap > 0 and net_debt_now is not None:
                ev_now = mcap + net_debt_now
            else:
                ev_now = _ctx_num(price_maps, symbol, "enterprise_value_current")

        ni_cagr = _first(
            _ctx_num(growth_maps, symbol, "net_income_cagr_implied_pct"),
            _row_num(rec, "ni_cagr", "net_income_cagr_5y"),
            _ctx_num(trailing_maps, symbol, "ni_cagr"),
        )
        ebitda_cagr = _ctx_num(growth_maps, symbol, "ebitda_cagr_implied_pct")
        ebitda_yoy = _first(
            _ctx_num(growth_maps, symbol, "y1_ebitda_growth_pct"),
            _row_num(rec, "ebitda_yoy", "ebitda_yoy_growth_ttm"),
            _ctx_num(trailing_maps, symbol, "ebitda_yoy"),
        )
        rev_cagr = _first(
            _ctx_num(growth_maps, symbol, "revenue_cagr_implied_pct"),
            _row_num(rec, "rev_cagr", "total_revenue_cagr_5y"),
            _ctx_num(trailing_maps, symbol, "rev_cagr"),
        )
        rev_yoy = _first(
            _ctx_num(growth_maps, symbol, "y1_revenue_growth_pct"),
            _row_num(rec, "rev_yoy", "total_revenue_yoy_growth_ttm"),
            _ctx_num(trailing_maps, symbol, "rev_yoy"),
        )
        fcf_cagr = _first(
            _row_num(rec, "fcf_cagr", "free_cash_flow_cagr_5y"),
            _ctx_num(trailing_maps, symbol, "fcf_cagr"),
            _ctx_num(price_maps, symbol, "free_cash_flow_cagr_5y"),
        )

        growth_year = _lookup(growth_years_index, symbol) or {}
        horizon = _year_of(growth_year.get("horizon_year"))
        far_year = horizon or MAX_HORIZON_YEAR

        def _at(
            path_key: str,
            trailing: float | None,
            cagr: float | None,
            yoy: float | None,
            year: int,
        ) -> tuple[float | None, str | None]:
            fraction = _first_fraction(year, [cagr], [yoy])
            return _resolve_level(_num(growth_year.get(path_key)), trailing, fraction, year)

        ni_y1, ni_m1 = _at("ni_y1", ni_trail, ni_cagr, None, 1)
        ni_y5, ni_m5 = _at("ni_y5", ni_trail, ni_cagr, None, far_year)
        ebitda_y1, eb_m1 = _at("ebitda_y1", ebitda_trail, ebitda_cagr, ebitda_yoy, 1)
        ebitda_y5, eb_m5 = _at("ebitda_y5", ebitda_trail, ebitda_cagr, ebitda_yoy, far_year)
        rev_y1, _rev_m1 = _at("rev_y1", rev_trail, rev_cagr, rev_yoy, 1)
        rev_y5, _rev_m5 = _at("rev_y5", rev_trail, rev_cagr, rev_yoy, far_year)

        pe_y1 = _multiple(mcap, ni_y1)
        pe_y5 = _multiple(mcap, ni_y5)
        evebitda_y1 = _multiple(ev_now, ebitda_y1)
        evebitda_y5 = _multiple(ev_now, ebitda_y5)

        fcf_fraction = _first_fraction(1, [fcf_cagr], [])
        fcf_y1, fcf_method = _resolve_level(None, fcf_trail, fcf_fraction, 1)
        fcf_y5_fraction = _first_fraction(far_year, [fcf_cagr], [])
        fcf_y5, _fcf_m5 = _resolve_level(None, fcf_trail, fcf_y5_fraction, far_year)
        if fcf_y1 is None:
            fcf_method = None

        path_debt_y0 = _ctx_num(price_maps, symbol, "net_debt")
        price_year = _lookup(price_years_index, symbol) or {}
        debt_y1, debt_method = _debt_forward(
            current=net_debt_now,
            path_y0=path_debt_y0,
            path_year=_num(price_year.get("net_debt_y1")),
        )
        debt_y5, debt_m5 = _debt_forward(
            current=net_debt_now,
            path_y0=path_debt_y0,
            path_year=_num(price_year.get("net_debt_y5")),
        )
        if debt_y1 is None:
            debt_method = debt_m5

        if ni_y1 is not None or ni_y5 is not None or ebitda_y1 is not None:
            rec["fp_horizon_year"] = far_year
        else:
            rec["fp_horizon_year"] = far_year if fcf_y5 is not None or debt_y5 is not None else None

        rec["fp_pe_y1"] = _round(pe_y1)
        rec["fp_pe_y5"] = _round(pe_y5)
        rec["fp_pe_method"] = ni_m1 if pe_y1 is not None else None
        rec["fp_pe_y5_method"] = ni_m5 if pe_y5 is not None else None
        rec["fp_evebitda_y1"] = _round(evebitda_y1)
        rec["fp_evebitda_y5"] = _round(evebitda_y5)
        rec["fp_evebitda_method"] = eb_m1 if evebitda_y1 is not None else None
        rec["fp_evebitda_y5_method"] = eb_m5 if evebitda_y5 is not None else None
        rec["evebitda_fwd"] = rec.get("fp_evebitda_y1")
        rec["fp_ev"] = _round(ev_now)
        rec["fp_net_debt_y1"] = _round(debt_y1)
        rec["fp_net_debt_y5"] = _round(debt_y5)
        rec["fp_debt_method"] = debt_method if debt_y1 is not None or debt_y5 is not None else None
        rec["fp_fcf_y1"] = _round(fcf_y1)
        rec["fp_fcf_y5"] = _round(fcf_y5)
        rec["fp_fcf_method"] = fcf_method if fcf_y1 is not None else None
        rec["fp_ni_y1"] = _round(ni_y1)
        rec["fp_ni_y5"] = _round(ni_y5)
        rec["fp_ebitda_y1"] = _round(ebitda_y1)
        rec["fp_ebitda_y5"] = _round(ebitda_y5)
        rec["fp_rev_y1"] = _round(rev_y1)
        rec["fp_rev_y5"] = _round(rev_y5)
        if rec.get("ni") is None and ni_trail is not None:
            rec["ni"] = _round(ni_trail)
        if rec.get("ebitda") is None and ebitda_trail is not None:
            rec["ebitda"] = _round(ebitda_trail)
        if rec.get("fcf") is None and fcf_trail is not None:
            rec["fcf"] = _round(fcf_trail)
        if rec.get("net_debt") is None and net_debt_now is not None:
            rec["net_debt"] = _round(net_debt_now)
        if rec.get("total_debt") is None and total_debt is not None:
            rec["total_debt"] = _round(total_debt)
        if rec.get("cash") is None and cash is not None:
            rec["cash"] = _round(cash)
        if rec.get("ev") is None and ev_now is not None:
            rec["ev"] = _round(ev_now)
        _quote_forward_pe(rec, pe_y1, ni_m1 if pe_y1 is not None else None)
        out.append(rec)
    return out
