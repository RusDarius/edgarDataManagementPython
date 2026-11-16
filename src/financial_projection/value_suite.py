"""Forward value suite: run financial_projection.valuation over the all-fields universe and persist a ``finvalue_*`` run.

Writes ``<output_root>/<dd_mm_yyyy>/finvalue_<stamp>/`` with ``finvalue_<stamp>.duckdb`` (tables ``value_names``,
``value_summary``, ``value_year_grid``), CSVs, ``overview.log``, ``queries.txt`` and ``highlights.log``. Industry medians use
the whole loaded universe; ``symbols`` only limits which names are valued. No ranking, no cutoff.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import DEFAULT_OUTPUT_ROOT, PROJECT_ROOT
from .fields import PROJECTION_FIELD_WHITELIST
from .load_all_fields import load_latest_all_fields_universe, resolve_all_fields_database
from .valuation import SCENARIOS, value_universe

DEFAULT_VALUE_CONFIG_PATH = PROJECT_ROOT / "config" / "financial_projection" / "value_v1.json"
VALUE_EXTRA_FIELDS: tuple[str, ...] = (
    "beta_1_year",
    "dividends_yield_current",
    "dividend_yield_recent",
    "buyback_yield",
    "free_cash_flow_ttm",
    "total_shares_outstanding",
)


def load_value_config(path: str | Path | None = None) -> dict[str, Any]:
    recipe = json.loads(Path(path or DEFAULT_VALUE_CONFIG_PATH).read_text(encoding="utf-8"))
    missing = [s for s in SCENARIOS if s not in (recipe.get("scenarios") or {})]
    if missing:
        raise ValueError(f"value config misses scenarios: {missing}")
    return recipe


def _highlights(names: Sequence[Mapping[str, Any]], *, top: int = 20) -> list[str]:
    def num(row: Mapping[str, Any], key: str) -> float | None:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    lines = [f"names={len(names)} with a base value={sum(1 for r in names if num(r, 'fv_base_px') is not None)}"]
    for title, key, reverse in (("Highest scenario-weighted IRR over the hurdle (excess_irr_pp)", "excess_irr_pp", True),
                                ("Lowest (the price asks more than the paths pay)", "excess_irr_pp", False),
                                ("Largest gap between the base revenue CAGR and the CAGR priced in (growth_gap_pp)", "growth_gap_pp", True)):
        rows = sorted((r for r in names if num(r, key) is not None), key=lambda r: num(r, key), reverse=reverse)[:top]
        lines.append("")
        lines.append(title)
        for r in rows:
            lines.append(f"  {r['symbol']:<16} irr_exp {r.get('irr_exp_pct')}% hurdle {r.get('hurdle_pct')}% "
                         f"fv bear/base/bull {r.get('fv_bear_px')}/{r.get('fv_base_px')}/{r.get('fv_bull_px')} close {r.get('close')} "
                         f"priced_in {r.get('priced_in_cagr_pct')}% base_cagr {r.get('rev_cagr_base_pct')}% lens {r.get('lens')}")
    return lines


def run_value_projection_suite(
    *,
    all_fields_db: str | Path | None = None,
    config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    min_market_cap_usd: float | None = None,
    markets: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    print_console: bool = True,
) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    recipe = load_value_config(config_path)
    database_path = resolve_all_fields_database(all_fields_db=all_fields_db)
    min_mcap = float(min_market_cap_usd if min_market_cap_usd is not None else recipe.get("min_market_cap_usd", 0))
    fields = tuple(dict.fromkeys(PROJECTION_FIELD_WHITELIST + VALUE_EXTRA_FIELDS))
    raw_rows, load_metadata = load_latest_all_fields_universe(
        database_path=database_path, fields=fields, markets=markets, min_market_cap_usd=min_mcap)
    min_rev = float(recipe.get("min_revenue_usd", 0))
    universe = [r for r in raw_rows if (r.get("total_revenue_ttm") or r.get("total_revenue") or 0) >= min_rev]
    wanted = {str(s).strip().upper() for s in symbols or [] if str(s).strip()}
    project = [r for r in universe if str(r.get("symbol") or "").upper() in wanted] if wanted else None
    names, summaries, years = value_universe(universe, recipe=recipe, project=project)

    stamp = datetime.now(tz=timezone.utc)
    run_id = f"finvalue_{stamp.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = Path(output_root or DEFAULT_OUTPUT_ROOT) / stamp.strftime("%d_%m_%Y") / run_id
    notes = [
        f"Recipe {recipe.get('config_id')} ({Path(config_path or DEFAULT_VALUE_CONFIG_PATH).as_posix()}): horizon {recipe.get('horizon_years')} years, "
        f"scenario weights {recipe.get('scenario_weights')}, discount {recipe.get('discount')}.",
        "value_names: one row per name. fv_<s>_px = fair value today under scenario s (dividends + year-H price discounted at hurdle_pct); "
        "fv_exp_px / fv_upside_pct = scenario-weighted fair value and its gap to the close; irr_<s>_pct / irr_exp_pct = the annual return each path "
        "pays from today's price; excess_irr_pp = irr_exp - hurdle; priced_in_cagr_pct = the constant revenue CAGR the price needs to earn the hurdle "
        "on the base margins and exit multiple; growth_gap_pp = rev_cagr_base_pct - priced_in_cagr_pct; dcf_base_px = FCF-to-equity DCF cross-check.",
        "Year 1 is the street's (revenue_forecast_next_fy, earnings_per_share_forecast_next_fy); g1_source / eps1_source say when it is not. "
        "lens: earnings = exit P/E on year-H EPS; sales = exit EV/Sales on year-H revenue less net debt (loss makers).",
        "value_summary: per name x scenario detail; value_year_grid: revenue, growth, net margin, EPS, dividend, share factor per year.",
        "Industry medians use the whole loaded universe (industry, else sector, else universe with at least min_industry_n names). No ranking, no cutoff.",
    ]
    export = write_run_export(
        run_dir, tool="financial_projection.value_suite",
        tables={"value_names": names, "value_summary": summaries, "value_year_grid": years},
        sources={"all_fields": database_path.as_posix(), "config": Path(config_path or DEFAULT_VALUE_CONFIG_PATH).as_posix()},
        notes=notes,
        extra={"run_id": run_id, "load_metadata": {k: v for k, v in load_metadata.items() if k not in ("fields_requested", "fields_available")},
               "universe_rows": len(universe), "valued_names": len(names)},
        duckdb_name=f"{run_id}.duckdb",
    )
    highlights = run_dir / "highlights.log"
    highlights.write_text("\n".join(_highlights(names)) + "\n", encoding="utf-8")
    if print_console:
        print(f"Loaded {len(raw_rows)} all-fields rows -> {len(universe)} with revenue >= {min_rev:,.0f}; valued {len(names)} names")
        print(f"Value run -> {export['duckdb']}")
        print(f"Highlights -> {highlights.as_posix()}")
    return {"suite_kind": "value", "run_id": run_id, "run_dir": run_dir.as_posix(), "database_path": export["duckdb"],
            "summary_csv": (export.get("csv") or {}).get("value_names"), "highlights_log": highlights.as_posix(),
            "all_fields_database": database_path.as_posix(), "valued_names": len(names), "universe_rows": len(universe)}
