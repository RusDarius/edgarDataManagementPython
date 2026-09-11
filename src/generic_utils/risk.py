"""Book capital / loss-tolerance math. Formulas only — no trim/EXIT cutoff.

Answers, in dollars and in % of NAV (equity mark + cash):
what a further drop on a name, a drop vs cost, a SMA50/ATR structure
print, or a full wipe does to the position and to the whole book.

Eligibility (HOLD vs TRIM vs EXIT) stays in operator_briefing / the
RISK prompt. This module does not invent a 0–100 risk score.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import leftover_pct
from generic_utils.aggregations import weighted_group_stats
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "book_risk.json"
HOLDINGS_CONFIG = _PROJECT / "config" / "holdings_scoring" / "current_holdings.json"
HOLDINGS_RUNS = (
    _PROJECT / "logs" / "tradingview_analysis" / "holdings_scoring_analysis" / "runs"
)

_f = to_float


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return round(number, digits)


def pct_of(part: Any, whole: Any, *, digits: int = 2) -> float | None:
    """100 * part / whole. None when whole is missing or 0."""
    numer = _f(part)
    denom = _f(whole)
    if numer is None or denom is None or denom == 0:
        return None
    return round(100.0 * numer / denom, digits)


def price_at_return(ref: Any, pct: Any, *, digits: int = 4) -> float | None:
    """ref * (1 + pct/100). pct is negative for a drop."""
    base = _f(ref)
    move = _f(pct)
    if base is None or move is None:
        return None
    return round(base * (1.0 + move / 100.0), digits)


def sma50_price(close: Any, vs50: Any, *, digits: int = 4) -> float | None:
    """Close implied SMA50 from pack vs50 %."""
    close_f = _f(close)
    vs = _f(vs50)
    if close_f is None or vs is None:
        return None
    denom = 1.0 + vs / 100.0
    if denom == 0:
        return None
    return round(close_f / denom, digits)


def atr_stop_price(
    close: Any,
    *,
    atr: Any = None,
    atrp: Any = None,
    mult: float = 1.5,
    digits: int = 4,
) -> float | None:
    """Close minus `mult` * ATR. Prefers ATRP (% of price) when both exist."""
    close_f = _f(close)
    if close_f is None:
        return None
    atrp_f = _f(atrp)
    if atrp_f is not None:
        return round(close_f * (1.0 - float(mult) * atrp_f / 100.0), digits)
    atr_f = _f(atr)
    if atr_f is not None:
        return round(close_f - float(mult) * atr_f, digits)
    return None


def capital_snapshot(
    *,
    equity_usd: Any,
    cash_usd: Any,
    cost_usd: Any = None,
    n: int | None = None,
) -> dict[str, Any]:
    """NAV = equity mark + cash. Cash-inclusive weights live here, not cost `wt`."""
    equity = _f(equity_usd) or 0.0
    cash = _f(cash_usd) or 0.0
    cost = _f(cost_usd)
    nav = equity + cash
    return {
        "equity_usd": _r(equity, 0),
        "cash_usd": _r(cash, 0),
        "cost_usd": _r(cost, 0),
        "nav_usd": _r(nav, 0),
        "cash_pct": pct_of(cash, nav),
        "equity_pct": pct_of(equity, nav),
        "unrealized_usd": _r((equity - cost) if cost is not None else None, 0),
        "unrealized_pct": pct_of((equity - cost) if cost is not None else None, cost),
        "n": n,
    }


def impact_at_price(
    *,
    shares: Any,
    close: Any,
    px: Any,
    nav: Any,
    cost_px: Any = None,
    leftover_close: Any = None,
    pt: Any = None,
) -> dict[str, Any]:
    """Dollar and % impact if the name trades at `px` (from today's mark).

    `usd_from_mark` is additional P&L vs today's close (negative = further loss).
    `usd_vs_cost` is total P&L vs average cost at that price.
    """
    sh = _f(shares)
    close_f = _f(close)
    px_f = _f(px)
    nav_f = _f(nav)
    cost_f = _f(cost_px)
    if sh is None or px_f is None:
        return {"px": _r(px_f, 4)}
    usd_from_mark = None
    if close_f is not None:
        usd_from_mark = sh * (px_f - close_f)
    usd_vs_cost = sh * (px_f - cost_f) if cost_f is not None else None
    leftover_at = leftover_pct(px_f, pt, leftover_close)
    return {
        "px": _r(px_f, 4),
        "usd_from_mark": _r(usd_from_mark, 0),
        "nav_from_mark_pct": pct_of(usd_from_mark, nav_f),
        "pos_from_mark_pct": pct_of(px_f - close_f, close_f) if close_f else None,
        "usd_vs_cost": _r(usd_vs_cost, 0),
        "pos_vs_cost_pct": pct_of(px_f - cost_f, cost_f) if cost_f else None,
        "left_at_px": leftover_at,
    }


def cash_from_holdings_config(path: str | Path | None = None) -> float:
    cfg_path = Path(path) if path else HOLDINGS_CONFIG
    if not cfg_path.exists():
        return 0.0
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    cash = data.get("cash_position") or {}
    return _f(cash.get("value_in_portfolio_currency")) or _f(cash.get("value")) or 0.0


def cash_from_holdings_manifest(run_dir: str | Path) -> float | None:
    path = Path(run_dir) / "holdings_scoring__manifest.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cash = data.get("cash_position") or {}
    return _f(cash.get("value_in_portfolio_currency")) or _f(cash.get("value"))


def latest_holdings_run(root: str | Path | None = None) -> Path | None:
    base = Path(root) if root else HOLDINGS_RUNS
    if not base.exists():
        return None
    dirs = sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith("holdings_scoring_")],
        key=lambda p: p.name,
    )
    return dirs[-1] if dirs else None


def load_holdings_summaries(run_dir: str | Path) -> list[dict[str, Any]]:
    """Stock + ETF holdings__summary.csv from one holdings_scoring run."""
    root = Path(run_dir)
    rows: list[dict[str, Any]] = []
    for rel in (
        Path("scans") / "move_prediction_v1" / "holdings__summary.csv",
        Path("scans") / "etf_active_book_v1" / "holdings__summary.csv",
    ):
        path = root / rel
        if path.exists():
            rows.extend(rows_from_csv(path))
    return rows


def rows_from_holdings_json(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Cost-only rows from current_holdings.json (no close / vs_cost)."""
    cfg_path = Path(path) if path else HOLDINGS_CONFIG
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in data.get("holdings") or []:
        ticker = str(item.get("ticker") or "")
        symbol = str(item.get("symbol") or ticker)
        invested = _f(item.get("invested_sum"))
        avg = _f(item.get("average_price"))
        shares = None
        if invested is not None and avg and avg > 0:
            shares = invested / avg
        rows.append(
            {
                "ticker": ticker,
                "symbol": symbol,
                "sleeve": item.get("sleeve"),
                "cost_usd": invested,
                "average_price": avg,
                "shares": shares,
                "notes": item.get("notes"),
                "instrument_type": item.get("instrument_type") or "stock",
            }
        )
    return rows


def normalize_holdings_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map holdings__summary.csv (or already-normalized pack book) onto risk keys."""
    rec = dict(row)
    symbol = str(
        rec.get("symbol")
        or rec.get("matched_symbol")
        or rec.get("config_symbol")
        or rec.get("ticker")
        or ""
    )
    ticker = str(rec.get("ticker") or rec.get("config_ticker") or symbol.split(":")[-1])
    rec["symbol"] = symbol
    rec["ticker"] = ticker
    if rec.get("cost_usd") is None:
        rec["cost_usd"] = _f(rec.get("invested_sum_usd")) or _f(rec.get("invested_sum"))
    if rec.get("shares") is None:
        rec["shares"] = _f(rec.get("implied_shares"))
    if rec.get("value_usd") is None:
        rec["value_usd"] = _f(rec.get("current_value_usd")) or _f(rec.get("cost_usd"))
    if rec.get("vs_cost") is None:
        rec["vs_cost"] = _f(rec.get("unrealized_return_pct"))
    if rec.get("ind") is None:
        rec["ind"] = rec.get("industry")
    if rec.get("wt_cost") is None:
        rec["wt_cost"] = _f(rec.get("wt")) or _f(rec.get("portfolio_weight_pct"))
    close_usd = _f(rec.get("close_usd"))
    if close_usd is not None:
        rec["close"] = close_usd
    if rec.get("pnl_usd") is None:
        rec["pnl_usd"] = _f(rec.get("unrealized_pnl_usd"))
    rec["average_price"] = _f(rec.get("average_price"))
    cost_usd = _f(rec.get("cost_usd"))
    shares = _f(rec.get("shares"))
    if cost_usd is not None and shares and shares > 0:
        rec["average_price_usd"] = cost_usd / shares
    return rec


_PACK_COPY = (
    "left",
    "rsi",
    "vs50",
    "rng",
    "rr",
    "dte",
    "mix",
    "mtp",
    "bo",
    "ind",
    "atrp",
    "atr",
    "beta",
    "evrev",
    "street_px",
    "target_px",
)


def merge_pack_book(
    rows: Sequence[Mapping[str, Any]],
    pack_book: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy leftover / structure fields from pack book onto holdings rows."""
    from generic_utils.series import join_rows

    left = [normalize_holdings_row(r) for r in rows]
    right: list[dict[str, Any]] = []
    for row in pack_book:
        item = dict(row)
        if not str(item.get("ticker") or "").strip():
            item["ticker"] = str(item.get("symbol") or "").split(":")[-1]
        right.append(item)
    joined = join_rows(
        left,
        right,
        left_on="ticker",
        right_on="ticker",
        prefix="pack_",
    )
    out: list[dict[str, Any]] = []
    for rec in joined:
        item = dict(rec)
        for key in _PACK_COPY:
            local = item.get(key)
            pack_val = item.get(f"pack_{key}")
            if pack_val is not None and pack_val != "" and (local is None or local == ""):
                item[key] = pack_val
        if item.get("close") is None and item.get("pack_close") is not None:
            item["close"] = item.get("pack_close")
        out.append(item)
    return out


def load_book_risk_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    if not recipe_path.exists():
        return {
            "name": "book_risk",
            "ladder_from_mark_pct": [-5, -10, -15, -20],
            "ladder_from_cost_pct": [-10, -15, -20, -25],
            "atr_mult": 1.5,
            "trim_fracs": [0.25, 0.33, 0.5],
            "group_field": "ind",
            "weight_field": "value_usd",
        }
    return json.loads(recipe_path.read_text(encoding="utf-8"))


def attach_position_risk(
    row: Mapping[str, Any],
    *,
    nav: Any,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp loss-ladder scalars onto one holding. No eligibility class."""
    rec = normalize_holdings_row(row)
    spec = dict(recipe or {})
    nav_f = _f(nav)
    close = _f(rec.get("close"))
    shares = _f(rec.get("shares"))
    cost_px = _f(rec.get("average_price_usd")) or _f(rec.get("average_price"))
    cost_usd = _f(rec.get("cost_usd"))
    value = _f(rec.get("value_usd"))
    if value is None and shares is not None and close is not None:
        value = shares * close
        rec["value_usd"] = _r(value, 0)
    if shares is None and cost_usd is not None and cost_px and cost_px > 0:
        shares = cost_usd / cost_px
        rec["shares"] = shares
    if rec.get("pnl_usd") is None and value is not None and cost_usd is not None:
        rec["pnl_usd"] = _r(value - cost_usd, 0)
    rec["wt_nav"] = pct_of(value, nav_f)
    rec["nav_now_pct"] = pct_of(rec.get("pnl_usd"), nav_f)
    rec["wipe_nav_pct"] = rec.get("wt_nav")
    rec["sma50_px"] = sma50_price(close, rec.get("vs50"))
    atr_mult = float(spec.get("atr_mult") or 1.5)
    rec["atr_mult"] = atr_mult
    rec["atr_px"] = atr_stop_price(
        close, atr=rec.get("atr"), atrp=rec.get("atrp"), mult=atr_mult
    )

    levels: list[dict[str, Any]] = []

    def _add(name: str, px: Any, *, kind: str) -> dict[str, Any]:
        hit = impact_at_price(
            shares=shares,
            close=close,
            px=px,
            nav=nav_f,
            cost_px=cost_px,
            leftover_close=None,
            pt=rec.get("street_px") or rec.get("target_px") or rec.get("pt"),
        )
        hit["name"] = name
        hit["kind"] = kind
        hit["symbol"] = rec.get("symbol")
        hit["ticker"] = rec.get("ticker")
        levels.append(hit)
        return hit

    for pct in spec.get("ladder_from_mark_pct") or (-5, -10, -15, -20):
        px = price_at_return(close, pct)
        tag = f"m{abs(int(pct))}"
        hit = _add(f"mark_{pct}", px, kind="from_mark")
        rec[f"{tag}_px"] = hit.get("px")
        rec[f"{tag}_usd"] = hit.get("usd_from_mark")
        rec[f"{tag}_nav"] = hit.get("nav_from_mark_pct")

    for pct in spec.get("ladder_from_cost_pct") or (-10, -15, -20, -25):
        px = price_at_return(cost_px, pct)
        tag = f"c{abs(int(pct))}"
        hit = _add(f"cost_{pct}", px, kind="from_cost")
        rec[f"{tag}_px"] = hit.get("px")
        rec[f"{tag}_usd"] = hit.get("usd_from_mark")
        rec[f"{tag}_nav"] = hit.get("nav_from_mark_pct")
        rec[f"{tag}_vs_cost"] = hit.get("pos_vs_cost_pct")

    if rec.get("sma50_px") is not None:
        hit = _add("sma50", rec["sma50_px"], kind="structure")
        rec["sma50_usd"] = hit.get("usd_from_mark")
        rec["sma50_nav"] = hit.get("nav_from_mark_pct")
    if rec.get("atr_px") is not None:
        hit = _add("atr_stop", rec["atr_px"], kind="structure")
        rec["atr_usd"] = hit.get("usd_from_mark")
        rec["atr_nav"] = hit.get("nav_from_mark_pct")

    rec["levels"] = levels

    trims: list[dict[str, Any]] = []
    for frac in spec.get("trim_fracs") or (0.25, 0.33, 0.5):
        frac_f = float(frac)
        sold = None if value is None else value * frac_f
        remain = None if value is None else value * (1.0 - frac_f)
        trims.append(
            {
                "frac": frac_f,
                "cash_raised_usd": _r(sold, 0),
                "remain_usd": _r(remain, 0),
                "remain_nav_pct": pct_of(remain, nav_f),
                "cash_raised_nav_pct": pct_of(sold, nav_f),
            }
        )
        pct_tag = str(int(round(frac_f * 100)))
        rec[f"trim{pct_tag}_cash"] = _r(sold, 0)
        rec[f"trim{pct_tag}_remain_nav"] = pct_of(remain, nav_f)
    rec["trims"] = trims
    return rec


def rec_levels(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    levels = row.get("levels")
    if not isinstance(levels, list):
        return []
    return [dict(item) for item in levels if isinstance(item, Mapping)]


def slim_position_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop nested lists for CSV / DuckDB (levels live in the long table)."""
    skip = {"levels", "trims"}
    return {k: v for k, v in row.items() if k not in skip}


def attach_book_risk(
    rows: Sequence[Mapping[str, Any]],
    *,
    cash_usd: Any = 0,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Portfolio capital + per-name ladders + industry NAV weights."""
    spec = dict(recipe or load_book_risk_recipe())
    positions = [normalize_holdings_row(row) for row in rows]
    equity = sum((_f(r.get("value_usd")) or 0.0) for r in positions)
    cost = sum((_f(r.get("cost_usd")) or 0.0) for r in positions)
    cash = _f(cash_usd) or 0.0
    capital = capital_snapshot(
        equity_usd=equity, cash_usd=cash, cost_usd=cost, n=len(positions)
    )
    nav = _f(capital.get("nav_usd")) or 0.0
    out_rows = [
        attach_position_risk(row, nav=nav, recipe=spec) for row in positions
    ]
    out_rows.sort(key=lambda r: -(_f(r.get("wt_nav")) or _f(r.get("value_usd")) or 0))
    group_field = str(spec.get("group_field") or "ind")
    weight_field = str(spec.get("weight_field") or "value_usd")
    industry = weighted_group_stats(
        out_rows,
        group_field=group_field,
        weight_field=weight_field,
        metrics=["vs_cost", "left", "rsi"],
        min_n=1,
    )
    for rec in industry:
        rec["nav_pct"] = pct_of(rec.get("weight"), nav)
    levels = [level for row in out_rows for level in rec_levels(row)]
    spec_out = {
        "recipe": spec.get("name") or "book_risk",
        "ladder_from_mark_pct": list(spec.get("ladder_from_mark_pct") or []),
        "ladder_from_cost_pct": list(spec.get("ladder_from_cost_pct") or []),
        "atr_mult": spec.get("atr_mult"),
        "trim_fracs": list(spec.get("trim_fracs") or []),
        "n": len(out_rows),
        "nav_usd": capital.get("nav_usd"),
        "cash_usd": capital.get("cash_usd"),
    }
    m10 = sum((_f(r.get("m10_usd")) or 0.0) for r in out_rows)
    wipes = [_f(r.get("wt_nav")) for r in out_rows]
    wipes_clean = [w for w in wipes if w is not None]
    capital["stress_m10_usd"] = _r(m10, 0)
    capital["stress_m10_nav"] = pct_of(m10, nav)
    capital["max_wipe_nav"] = max(wipes_clean) if wipes_clean else None
    return {
        "spec": spec_out,
        "capital": capital,
        "positions": out_rows,
        "industry": industry,
        "levels": levels,
    }


def default_risk_out_dir(*, now: datetime | None = None) -> Path:
    """Dated scan folder under operator_briefing/runs when --out-dir is omitted."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M_utc")
    return (
        _PROJECT
        / "logs"
        / "tradingview_analysis"
        / "operator_briefing"
        / "runs"
        / f"book_risk_{stamp}"
    )


def _md_cell(value: Any, digits: int = 2) -> str:
    number = _f(value)
    if number is None:
        return "—"
    if digits == 0:
        return f"{int(round(number)):,}"
    return f"{number:.{digits}f}"


def format_book_risk_md(payload: Mapping[str, Any]) -> str:
    """Deterministic markdown snapshot. No TRIM/HOLD/EXIT — that is the canvas."""
    spec = payload.get("spec") or {}
    capital = payload.get("capital") or {}
    industry = payload.get("industry") or []
    positions = payload.get("positions") or []
    lines = [
        "# Book risk (loss vs NAV)",
        "",
        "Generic ladder dump. Course (TRIM / HOLD / EXIT) is the agent canvas, not this file.",
        "",
        f"- recipe `{spec.get('recipe') or 'book_risk'}`",
        f"- holdings `{spec.get('holdings') or ''}`",
        f"- holdings scan `{spec.get('run_dir') or ''}`",
        f"- pack `{spec.get('pack') or ''}`",
        f"- n `{spec.get('n')}`",
        "",
        "## Capital",
        "",
        f"- NAV **{_md_cell(capital.get('nav_usd'), 0)}** = equity {_md_cell(capital.get('equity_usd'), 0)} + cash {_md_cell(capital.get('cash_usd'), 0)} ({_md_cell(capital.get('cash_pct'))}% cash)",
        f"- cost {_md_cell(capital.get('cost_usd'), 0)} · unrealized {_md_cell(capital.get('unrealized_usd'), 0)} ({_md_cell(capital.get('unrealized_pct'))}% vs cost)",
        f"- stress_m10 {_md_cell(capital.get('stress_m10_usd'), 0)} USD / {_md_cell(capital.get('stress_m10_nav'))}% NAV",
        f"- max_wipe_nav {_md_cell(capital.get('max_wipe_nav'))}%",
        "",
        "## Industry NAV",
        "",
        "| industry | n | nav_pct | vs_cost | left | rsi |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for rec in industry:
        lines.append(
            f"| {rec.get('ind') or '—'} | {rec.get('n') or ''} | {_md_cell(rec.get('nav_pct'))} | {_md_cell(rec.get('vs_cost'))} | {_md_cell(rec.get('left'))} | {_md_cell(rec.get('rsi'))} |"
        )
    lines += [
        "",
        "## Positions (USD close / USD cost)",
        "",
        "| ticker | wt_nav | value | vs_cost | left | rsi | rng | vs50 | m10_usd | m10_nav | c15_usd | c15_nav | sma50_nav | atr_nav | trim33_cash | trim33_remain |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rec in positions:
        ticker = rec.get("ticker") or rec.get("symbol") or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    str(ticker),
                    _md_cell(rec.get("wt_nav")),
                    _md_cell(rec.get("value_usd"), 0),
                    _md_cell(rec.get("vs_cost")),
                    _md_cell(rec.get("left")),
                    _md_cell(rec.get("rsi")),
                    _md_cell(rec.get("rng")),
                    _md_cell(rec.get("vs50")),
                    _md_cell(rec.get("m10_usd"), 0),
                    _md_cell(rec.get("m10_nav")),
                    _md_cell(rec.get("c15_usd"), 0),
                    _md_cell(rec.get("c15_nav")),
                    _md_cell(rec.get("sma50_nav")),
                    _md_cell(rec.get("atr_nav")),
                    _md_cell(rec.get("trim33_cash"), 0),
                    _md_cell(rec.get("trim33_remain_nav")),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Three frames: `wt_nav` is position % of NAV (= wipe if price → 0); `_usd` is net dollars; `_nav` is that dollar hit as % of NAV.",
        "",
    ]
    return "\n".join(lines)
