"""Price-to-book cycle read for a caller-named industry list.

Formulas only. No 0-100. The industry list and the growth floors live in
config/generic_utils/book_cycle.json. They are not cutoffs inside ranking.py.

cycle_read:
- peak_margin: net margin is at or above the peak floor (default 40%).
- earn_leads: earnings growth beats revenue growth by the gap (default 25 points).
- rev_carries: revenue growth and net margin both clear their floors, and the
  two labels above are off.
- thin: a number is missing, or the floors are not met.

pb_read uses pb_vs_ind and the same -5 / +5 bins as setup peer.
seat_pct is 0 at the window low and 100 at the window high.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.run_export import load_json_recipe

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "book_cycle.json"

_f = to_float


def load_book_cycle_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "book_cycle")
    payload.setdefault("industries", [])
    payload.setdefault("min_rev_growth", 10)
    payload.setdefault("min_net_margin", 8)
    payload.setdefault("peak_net_margin", 40)
    payload.setdefault("earn_lead_gap", 25)
    payload.setdefault("growth_quote_max", 300)
    payload.setdefault("pb_discount_max", -5)
    payload.setdefault("seat_max", 85)
    payload.setdefault("symbol_fields", ["symbol"])
    payload.setdefault("close_fields", ["close", "close_last"])
    payload.setdefault("pb_fields", ["pb", "price_book_fq", "price_book_ratio"])
    payload.setdefault("pb_vs_fields", ["pb_vs_ind"])
    payload.setdefault("rev_fields", ["rev_yoy", "total_revenue_yoy_growth_ttm", "rev_growth"])
    payload.setdefault("earn_fields", ["earn_yoy", "net_income_yoy_growth_ttm", "earn_growth"])
    payload.setdefault("margin_fields", ["net_margin", "net_margin_ttm"])
    payload.setdefault("low_fields", ["close_min"])
    payload.setdefault("high_fields", ["close_max"])
    return payload


def _first_num(row: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for name in fields:
        if name not in row:
            continue
        number = _f(row.get(name))
        if number is not None:
            return number
    return None


def _symbol(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    for name in fields:
        text = str(row.get(name) or "").strip()
        if text:
            return text
    return ""


def range_seat(close: Any, low: Any, high: Any, *, digits: int = 2) -> dict[str, float | None]:
    """Where the close sits between the window low and the window high.

    seat_pct is 0 at the low and 100 at the high. dd_from_high_pct is negative
    when the close is under the high. up_from_low_pct is the rise off the low.
    """
    mark = _f(close)
    lo = _f(low)
    hi = _f(high)
    seat = None
    dd = None
    up = None
    if mark is not None and lo is not None and hi is not None and hi > lo:
        seat = round((mark - lo) / (hi - lo) * 100.0, digits)
    if mark is not None and hi not in (None, 0):
        dd = round((mark / hi - 1.0) * 100.0, digits)
    if mark is not None and lo not in (None, 0):
        up = round((mark / lo - 1.0) * 100.0, digits)
    return {"seat_pct": seat, "dd_from_high_pct": dd, "up_from_low_pct": up}


def pb_read(pb_vs_ind: Any, *, discount_max: float = -5, premium_min: float = 5) -> str:
    gap = _f(pb_vs_ind)
    if gap is None:
        return ""
    if gap <= discount_max:
        return "DISCOUNT"
    if gap >= premium_min:
        return "PREMIUM"
    return "INLINE"


def cycle_read(
    rev_growth: Any,
    earn_growth: Any,
    net_margin: Any,
    *,
    min_rev_growth: float = 10,
    min_net_margin: float = 8,
    peak_net_margin: float = 40,
    earn_lead_gap: float = 25,
) -> str:
    """peak_margin, earn_leads, rev_carries, or thin. A label, not a rank."""
    rev = _f(rev_growth)
    earn = _f(earn_growth)
    margin = _f(net_margin)
    if margin is not None and margin >= peak_net_margin:
        return "peak_margin"
    if rev is not None and earn is not None and earn >= rev + earn_lead_gap:
        return "earn_leads"
    if (
        rev is not None
        and rev >= min_rev_growth
        and margin is not None
        and margin >= min_net_margin
    ):
        return "rev_carries"
    return "thin"


def attach_book_cycle_row(
    row: Mapping[str, Any],
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    used = load_book_cycle_recipe()
    if recipe:
        used.update(recipe)
    out = dict(row)
    rev = _first_num(out, used["rev_fields"])
    earn = _first_num(out, used["earn_fields"])
    margin = _first_num(out, used["margin_fields"])
    close = _first_num(out, used["close_fields"])
    low = _first_num(out, used["low_fields"])
    high = _first_num(out, used["high_fields"])
    pb_vs = _first_num(out, used["pb_vs_fields"])
    out["rev_yoy"] = rev
    out["earn_yoy"] = earn
    out["net_margin"] = margin
    out["cycle_read"] = cycle_read(
        rev,
        earn,
        margin,
        min_rev_growth=float(used["min_rev_growth"]),
        min_net_margin=float(used["min_net_margin"]),
        peak_net_margin=float(used["peak_net_margin"]),
        earn_lead_gap=float(used["earn_lead_gap"]),
    )
    out["pb_read"] = pb_read(pb_vs, discount_max=float(used["pb_discount_max"]))
    out.update(range_seat(close, low, high))
    quote_max = float(used.get("growth_quote_max") or 300)
    out["base_effect"] = (rev is not None and rev >= quote_max) or (earn is not None and earn >= quote_max)
    seat = out.get("seat_pct")
    seat_max = float(used["seat_max"])
    out["resilient"] = (
        out["cycle_read"] == "rev_carries"
        and out["pb_read"] == "DISCOUNT"
        and not out["base_effect"]
        and seat is not None
        and seat <= seat_max
    )
    return out


def index_by_symbol(rows: Sequence[Mapping[str, Any]], field: str = "symbol") -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get(field) or "").strip()
        if key:
            out[key] = row
    return out


def build_book_cycle(
    tape_rows: Sequence[Mapping[str, Any]],
    fund_rows: Sequence[Mapping[str, Any]] | None = None,
    span_rows: Sequence[Mapping[str, Any]] | None = None,
    peer_rows: Sequence[Mapping[str, Any]] | None = None,
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Join tape, fundamentals, the high/low span, and pb versus industry.

    Rows are not dropped. ``resilient`` is true only for rev_carries, a P/B
    discount, and a seat that is not at the window high.
    """
    used = load_book_cycle_recipe()
    if recipe:
        used.update(recipe)
    funds = index_by_symbol(fund_rows or [])
    spans = index_by_symbol(span_rows or [])
    peers = index_by_symbol(peer_rows or [])
    industries = {str(name) for name in (used.get("industries") or [])}
    out: list[dict[str, Any]] = []
    for row in tape_rows:
        if industries and str(row.get("ind") or "") not in industries:
            continue
        key = _symbol(row, used["symbol_fields"])
        merged = dict(row)
        for extra in (funds.get(key), spans.get(key), peers.get(key)):
            if not extra:
                continue
            for field, value in extra.items():
                if field in {"symbol", "ind"}:
                    continue
                if field not in merged or merged.get(field) in (None, ""):
                    merged[field] = value
        out.append(attach_book_cycle_row(merged, used))
    return out


def write_book_cycle(
    tape_rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    fund_rows: Sequence[Mapping[str, Any]] | None = None,
    span_rows: Sequence[Mapping[str, Any]] | None = None,
    peer_rows: Sequence[Mapping[str, Any]] | None = None,
    recipe: Mapping[str, Any] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    used = load_book_cycle_recipe()
    if recipe:
        used.update(recipe)
    rows = build_book_cycle(tape_rows, fund_rows, span_rows, peer_rows, used)
    resilient = [row for row in rows if row.get("resilient")]
    resilient.sort(key=lambda row: (_f(row.get("pb_vs_ind")) is None, _f(row.get("pb_vs_ind")) or 0))
    notes = [
        "Industries and floors are the recipe. They are not a cutoff in ranking.py.",
        "cycle_read peak_margin: net margin at or above the peak floor. That is the cycle-end label.",
        "cycle_read earn_leads: earnings growth beats revenue growth by the gap. Revenue is not the carrier.",
        "cycle_read rev_carries: revenue growth and net margin clear the floors, and the two labels above are off.",
        "pb_read DISCOUNT is pb_vs_ind at or below -5. The percent is the gap versus the industry median on the full tape, not versus this industry list alone.",
        "close_min and close_max are the window low and high from series_span. seat_pct is 0 at the low and 100 at the high.",
        "dd_from_high_pct is the close versus that high. up_from_low_pct is the close versus that low.",
        "resilient is rev_carries, pb_read DISCOUNT, seat_pct at or under seat_max, and base_effect false. It is a label. It is not a 0-100.",
        "base_effect is true when revenue or earnings growth is at or above growth_quote_max (default 300). That print is a tiny base, not a growth rate.",
        "The resilient table is those rows, cheapest pb_vs_ind first. That order is not a rank score.",
    ]
    if source_note:
        notes.insert(0, source_note)
    return write_run_export(
        out_dir,
        tool="generic_utils.book_cycle",
        tables={"book_cycle": rows, "book_cycle_resilient": resilient},
        sources={
            "n": len(rows),
            "n_resilient": len(resilient),
            "n_peak_margin": sum(1 for row in rows if row.get("cycle_read") == "peak_margin"),
            "n_earn_leads": sum(1 for row in rows if row.get("cycle_read") == "earn_leads"),
            "n_rev_carries": sum(1 for row in rows if row.get("cycle_read") == "rev_carries"),
            "industries": list(used.get("industries") or []),
        },
        notes=notes,
        duckdb_name="book_cycle.duckdb",
    )
