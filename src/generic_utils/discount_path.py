"""Discount path: own-history multiples, peer upside, balance-sheet strength.

Formulas only. No 0-100. No ADD/WAIT.

A multiple's peer bin uses the same -5 / +5 rule as setup peer.
DISCOUNT is that multiple's percent versus the industry median at or below -5.
PREMIUM is at or above 5. INLINE is between. THIN means the industry
had fewer than min_n quotes. NA means this name has no positive multiple.

``FIELD_up_pct`` is the percent the price would move if that multiple went
to the industry median. It is empty when the multiple or the median is not
positive. A negative number means the name is already richer than the median.

``FIELD_pct`` is the first-to-last change of that multiple on the caller's
window. Negative means the last print is below the first. ``FIELD_path`` is
CHEAPER, RICHER, FLAT, or THIN_HIST. CHEAPER requires both ends positive, so
a multiple that crossed through zero is THIN_HIST. DYING is not used here.

``cheaper_count`` is how many spanned multiples are CHEAPER (0-4 or more).
It is a count, like nmesh. It is not a score.

``widest_up_pct`` is the largest ``FIELD_up_pct`` among multiples that are
DISCOUNT now. The gaps are not added together.

``strength_read`` is a label on the balance sheet, not a rank:
- net_cash: net debt is at or below zero.
- covered: net debt / EBITDA is at or under the covered max, operating
  margin clears the floor, and free-cash-flow margin is not negative.
- levered: net debt / EBITDA is at or above the levered floor.
- open: the numbers exist and none of those three labels fired.
- thin: net debt and EBITDA are both missing.

Knobs live in config/generic_utils/discount_path.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import attach_vs_group
from generic_utils.ranking import to_float
from generic_utils.run_export import load_json_recipe

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "discount_path.json"

# Trailing fields that can be spanned from all-fields history.
SPAN_FIELDS: tuple[str, ...] = ("pe", "evrev", "evebitda", "pb", "evfcf", "close")
# Quarterly book multiple. Focused scans often omit price_book_ratio and still
# print this. It is a path of its own. It counts toward cheaper_count only when
# the price_book_ratio path is not already CHEAPER.
EXTRA_PATH_FIELDS: tuple[str, ...] = ("pb_fq",)
# Current multiples that get a peer bin and an upside-to-median.
QUOTE_FIELDS: tuple[str, ...] = (
    "pe",
    "evrev",
    "evebitda",
    "pb",
    "evfcf",
    "pe_fwd",
    "evebitda_fwd",
)

_f = to_float


def load_discount_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "discount_path")
    payload.setdefault("months", 3)
    payload.setdefault("min_market_cap_usd", 2_000_000_000)
    payload.setdefault("min_n", 6)
    payload.setdefault("hist_min_n", 4)
    payload.setdefault("cheap_pct", -5)
    payload.setdefault("rich_pct", 5)
    payload.setdefault("covered_nd_ebitda_max", 2)
    payload.setdefault("covered_opm_min", 8)
    payload.setdefault("levered_nd_ebitda_min", 4)
    return payload


def upside_to_median(value: Any, median: Any, *, digits: int = 1) -> float | None:
    """Percent move if this positive multiple went to a positive median.

    10 versus a median of 15 is +50. 20 versus 15 is -25. A negative
    multiple, a missing median, or a non-positive median stays empty.
    """
    current = _f(value)
    center = _f(median)
    if current is None or center is None or current <= 0 or center <= 0:
        return None
    return round((center / current - 1.0) * 100.0, digits)


def multiple_peer(vs_ind: Any, *, has_value: bool, thin: bool) -> str:
    """DISCOUNT / PREMIUM / INLINE / THIN / NA. Same -5 / +5 band as setup."""
    if not has_value:
        return "NA"
    gap = _f(vs_ind)
    if thin or gap is None:
        return "THIN"
    if gap <= -5:
        return "DISCOUNT"
    if gap >= 5:
        return "PREMIUM"
    return "INLINE"


def path_label(pct: Any, n: Any, *, cheap_pct: float = -5, rich_pct: float = 5, hist_min_n: int = 4) -> str:
    """CHEAPER, RICHER, FLAT, or THIN_HIST. The percent stays in its own column."""
    count = _f(n)
    change = _f(pct)
    if count is None or count < hist_min_n or change is None:
        return "THIN_HIST"
    if change <= cheap_pct:
        return "CHEAPER"
    if change >= rich_pct:
        return "RICHER"
    return "FLAT"


def ratio(numerator: Any, denominator: Any, *, digits: int = 2) -> float | None:
    num = _f(numerator)
    den = _f(denominator)
    if num is None or den is None or den == 0:
        return None
    return round(num / den, digits)


def strength_read(
    net_debt: Any,
    ebitda: Any,
    opm: Any,
    fcf_margin: Any,
    *,
    covered_nd_ebitda_max: float = 2,
    covered_opm_min: float = 8,
    levered_nd_ebitda_min: float = 4,
) -> str:
    """net_cash, covered, levered, open, or thin. A label, not a rank."""
    debt = _f(net_debt)
    profit = _f(ebitda)
    margin = _f(opm)
    cash_margin = _f(fcf_margin)
    if debt is None and profit is None:
        return "thin"
    if debt is not None and debt <= 0:
        return "net_cash"
    leverage = ratio(debt, profit) if profit is not None and profit > 0 else None
    if leverage is not None and leverage >= levered_nd_ebitda_min:
        return "levered"
    if (
        leverage is not None
        and leverage <= covered_nd_ebitda_max
        and margin is not None
        and margin >= covered_opm_min
        and (cash_margin is None or cash_margin >= 0)
    ):
        return "covered"
    return "open"


def street_gap(close: Any, pt: Any, *, digits: int = 1) -> float | None:
    """Street target versus close, in percent. Empty when either price is missing."""
    mark = _f(close)
    target = _f(pt)
    if mark is None or target is None or mark <= 0:
        return None
    return round((target / mark - 1.0) * 100.0, digits)


def _rename(row: Mapping[str, Any]) -> dict[str, Any]:
    alias = {
        "industry": "ind",
        "market_cap_basic": "mcap",
        "price_earnings_ttm": "pe",
        "enterprise_value_to_revenue_ttm": "evrev",
        "enterprise_value_ebitda_ttm": "evebitda",
        "price_book_ratio": "pb",
        "enterprise_value_to_free_cash_flow_ttm": "evfcf",
        "operating_margin": "opm",
        "net_debt": "net_debt",
        "total_debt": "total_debt",
        "cash_n_equivalents_fq": "cash",
        "ebitda_ttm": "ebitda",
        "free_cash_flow_ttm": "fcf",
        "total_revenue_ttm": "rev_ttm",
        "price_target_average": "pt",
        "RSI": "rsi",
    }
    out = dict(row)
    for source, name in alias.items():
        if source in row and name not in out:
            out[name] = row.get(source)
        elif source in row and out.get(name) in (None, ""):
            out[name] = row.get(source)
    return out


def prepare_discount_row(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One name: strength ratios, street gap. Peer columns are attached later."""
    cfg = recipe or load_discount_recipe()
    rec = _rename(row)
    ratio_pb = _f(rec.get("pb"))
    quarterly_pb = _f(rec.get("price_book_fq"))
    if ratio_pb is not None and ratio_pb > 0:
        rec["pb"] = ratio_pb
        rec["pb_source"] = "price_book_ratio"
    elif quarterly_pb is not None and quarterly_pb > 0:
        rec["pb"] = quarterly_pb
        rec["pb_source"] = "price_book_fq"
    else:
        rec["pb_source"] = None
    rec["nd_ebitda"] = ratio(rec.get("net_debt"), rec.get("ebitda")) if (_f(rec.get("ebitda")) or 0) > 0 else None
    rec["cash_to_debt"] = ratio(rec.get("cash"), rec.get("total_debt")) if (_f(rec.get("total_debt")) or 0) > 0 else None
    fcf_m = ratio(rec.get("fcf"), rec.get("rev_ttm"), digits=4)
    rec["fcf_margin"] = None if fcf_m is None else round(fcf_m * 100.0, 1)
    rec["street_left"] = street_gap(rec.get("close"), rec.get("pt"))
    rec["strength_read"] = strength_read(
        rec.get("net_debt"),
        rec.get("ebitda"),
        rec.get("opm"),
        rec.get("fcf_margin"),
        covered_nd_ebitda_max=float(cfg.get("covered_nd_ebitda_max") or 2),
        covered_opm_min=float(cfg.get("covered_opm_min") or 8),
        levered_nd_ebitda_min=float(cfg.get("levered_nd_ebitda_min") or 4),
    )
    return rec


def stamp_peers(rows: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Peer bins and upside-to-median on the full loaded set. Nothing is dropped.

    The industry median uses positive multiples only. A negative P/E stays on
    the row and its peer bin is NA. It does not pull the median.
    """
    cfg = recipe or load_discount_recipe()
    min_n = int(cfg.get("min_n") or 6)
    present = [field for field in QUOTE_FIELDS if any(field in row for row in rows)]
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        for field in present:
            value = _f(rec.get(field))
            if value is None or value <= 0:
                rec[field] = None
        cleaned.append(rec)
    stamped = attach_vs_group(
        cleaned,
        group_field="ind",
        fields=present,
        min_n=min_n,
    )
    out: list[dict[str, Any]] = []
    for original, row in zip(rows, stamped):
        rec = dict(row)
        upsides: list[float] = []
        for field in QUOTE_FIELDS:
            value = _f(original.get(field))
            rec[field] = original.get(field)
            has_value = value is not None and value > 0
            if not has_value:
                rec[f"{field}_vs_ind"] = None
            gap = rec.get(f"{field}_vs_ind")
            thin = gap is None and has_value
            rec[f"{field}_peer"] = multiple_peer(gap, has_value=has_value, thin=thin)
            up = upside_to_median(value, rec.get(f"{field}_ind_med"))
            rec[f"{field}_up_pct"] = up
            if rec[f"{field}_peer"] == "DISCOUNT" and up is not None:
                upsides.append(up)
        rec["widest_up_pct"] = round(max(upsides), 1) if upsides else None
        out.append(rec)
    return out


def apply_window_span(
    rows: Sequence[Mapping[str, Any]],
    span_rows: Sequence[Mapping[str, Any]],
    *,
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Join first-to-last percents. A missing span stays on the row as THIN_HIST."""
    cfg = recipe or load_discount_recipe()
    cheap = float(cfg.get("cheap_pct") or -5)
    rich = float(cfg.get("rich_pct") or 5)
    hist_min = int(cfg.get("hist_min_n") or 4)
    by_symbol = {str(row.get("symbol") or ""): row for row in span_rows}
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        span = by_symbol.get(str(rec.get("symbol") or ""))
        n = int(_f(span.get("n")) or 0) if span else 0
        rec["hist_n"] = n or None
        rec["hist_first"] = span.get("first_as_of") if span else None
        rec["hist_last"] = span.get("last_as_of") if span else None
        cheaper = 0
        for field in SPAN_FIELDS + EXTRA_PATH_FIELDS:
            pct = _f(span.get(f"{field}_pct")) if span else None
            first = _f(span.get(f"{field}_first")) if span else None
            last = _f(span.get(f"{field}_last")) if span else None
            prints = _f(span.get(f"{field}_n")) if span else None
            rec[f"{field}_pct"] = None if pct is None else round(pct, 1)
            rec[f"{field}_first"] = first
            rec[f"{field}_last"] = last
            rec[f"{field}_n"] = None if prints is None else int(prints)
            if field == "close":
                continue
            both_positive = first is not None and last is not None and first > 0 and last > 0
            count = prints if prints is not None else n
            label = (
                path_label(pct, count, cheap_pct=cheap, rich_pct=rich, hist_min_n=hist_min)
                if both_positive
                else "THIN_HIST"
            )
            rec[f"{field}_path"] = label
            if field in SPAN_FIELDS and label == "CHEAPER":
                cheaper += 1
        if rec.get("pb_path") != "CHEAPER" and rec.get("pb_fq_path") == "CHEAPER":
            cheaper += 1
        rec["cheaper_count"] = cheaper
        out.append(rec)
    return out


def reading_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Progressed toward cheaper and the balance sheet is net cash or covered.

    This is a where, not a deletion of discount_tape. Order is cheaper_count,
    then widest_up_pct. Those are a count and one gap. They are not added.
    """
    kept = [
        dict(row)
        for row in rows
        if int(row.get("cheaper_count") or 0) >= 1
        and row.get("strength_read") in {"net_cash", "covered"}
        and row.get("widest_up_pct") is not None
    ]
    kept.sort(
        key=lambda row: (
            -int(row.get("cheaper_count") or 0),
            -(float(row.get("widest_up_pct") or 0)),
            str(row.get("symbol") or ""),
        )
    )
    return kept


def lane_rows(rows: Sequence[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    """Names with a window percent on this multiple, cheapest path first."""
    key = f"{field}_pct"
    kept = [dict(row) for row in rows if row.get(key) is not None]
    kept.sort(key=lambda row: (float(row[key]), str(row.get("symbol") or "")))
    return kept


def build_discount_tables(
    rows: Sequence[Mapping[str, Any]],
    span_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Full tape plus one lane per spanned multiple. Nobody is dropped from the tape."""
    cfg = recipe or load_discount_recipe()
    prepared = [prepare_discount_row(row, cfg) for row in rows if str(row.get("symbol") or "")]
    tape = stamp_peers(prepared, cfg)
    tape = apply_window_span(tape, span_rows or [], recipe=cfg)
    tape.sort(key=lambda row: str(row.get("symbol") or ""))
    return {
        "discount_tape": tape,
        "reading": reading_rows(tape),
        "pe_path": lane_rows(tape, "pe"),
        "evrev_path": lane_rows(tape, "evrev"),
        "evebitda_path": lane_rows(tape, "evebitda"),
        "pb_path": lane_rows(tape, "pb"),
        "pb_fq_path": lane_rows(tape, "pb_fq"),
        "evfcf_path": lane_rows(tape, "evfcf"),
    }


def task_queries(database: str) -> str:
    """Copy-paste SQL for DBeaver. The auto field list follows this block."""
    db = database.replace("'", "''")
    return "\n".join(
        [
            "DISCOUNT PATH — task queries",
            f"database={database}",
            "",
            "Open discount_path.duckdb. These blocks are the question.",
            "discount_tape keeps every loaded name. reading is the where below.",
            "A peer label without its percent is incomplete. Quote FIELD_vs_ind beside FIELD_peer.",
            "FIELD_up_pct is the gap to the industry median, in percent. Do not add those percents.",
            "FIELD_pct is the change from the first print of that field to the last print of that field.",
            "A focused scan that omitted the column does not become the last print.",
            "Negative means the last print is below the first. Both ends must be positive for CHEAPER.",
            "pb_pct is price_book_ratio. pb_fq_pct is price_book_fq. Current pb uses the ratio when it is positive, otherwise the quarterly print. pb_source names which one.",
            "cheaper_count counts CHEAPER paths. It is not a 0-100. Quarterly book counts only when the ratio path is not already CHEAPER.",
            "widest_up_pct is the largest discount gap, not the sum.",
            "strength_read is net_cash, covered, levered, open, or thin.",
            "street_left is the street target versus close. It is not FIELD_up_pct.",
            "pe_fwd and evebitda_fwd are the projection. They can be off. They are not spanned.",
            "A forward PE near zero can print an upside of several thousand percent. Query 1 keeps that row. Query 1b keeps the headline gap between 10 and 250.",
            "",
            "-- 1. Progressed cheaper, balance sheet net cash or covered, a live discount gap.",
            "SELECT symbol, ind, strength_read, cheaper_count, widest_up_pct,",
            "       pe, pe_peer, pe_vs_ind, pe_up_pct, pe_pct, pe_path,",
            "       evrev, evrev_peer, evrev_vs_ind, evrev_up_pct, evrev_pct, evrev_path,",
            "       evebitda, evebitda_peer, evebitda_vs_ind, evebitda_up_pct,",
            "       pe_fwd, pe_fwd_peer, pe_fwd_vs_ind, pe_fwd_up_pct,",
            "       opm, nd_ebitda, fcf_margin, cash_to_debt, street_left, close",
            "FROM reading",
            "ORDER BY cheaper_count DESC, widest_up_pct DESC;",
            "",
            "-- 1b. Same where, headline gap between 10 and 250.",
            "-- Edit the bounds. A gap above 250 is still on discount_tape and in query 1.",
            "SELECT symbol, ind, strength_read, cheaper_count, widest_up_pct,",
            "       pe, pe_peer, pe_vs_ind, pe_up_pct, pe_pct, pe_path,",
            "       evrev, evrev_peer, evrev_vs_ind, evrev_up_pct, evrev_pct, evrev_path,",
            "       evebitda, evebitda_peer, evebitda_vs_ind, evebitda_up_pct,",
            "       pe_fwd, pe_fwd_peer, pe_fwd_vs_ind, pe_fwd_up_pct,",
            "       opm, nd_ebitda, fcf_margin, cash_to_debt, street_left, close",
            "FROM reading",
            "WHERE widest_up_pct BETWEEN 10 AND 250",
            "ORDER BY cheaper_count DESC, widest_up_pct DESC;",
            "",
            "-- 2. Trailing PE got cheaper on the window. Current PE bin is beside the percent.",
            "SELECT symbol, ind, pe, pe_peer, pe_vs_ind, pe_up_pct, pe_pct, pe_path,",
            "       pe_first, pe_last, hist_n, strength_read, opm, nd_ebitda, street_left",
            "FROM pe_path",
            "WHERE pe_path = 'CHEAPER'",
            "ORDER BY pe_pct ASC;",
            "",
            "-- 3. EV/Revenue got cheaper. Same shape as query 2.",
            "SELECT symbol, ind, evrev, evrev_peer, evrev_vs_ind, evrev_up_pct, evrev_pct, evrev_path,",
            "       strength_read, opm, nd_ebitda, fcf_margin, street_left",
            "FROM evrev_path",
            "WHERE evrev_path = 'CHEAPER'",
            "ORDER BY evrev_pct ASC;",
            "",
            "-- 3b. Quarterly price to book, for scans that omitted price_book_ratio.",
            "SELECT symbol, ind, pb, pb_source, pb_fq_pct, pb_fq_path, pb_fq_n,",
            "       pb_pct, pb_path, strength_read, opm, nd_ebitda",
            "FROM pb_fq_path",
            "WHERE pb_fq_path = 'CHEAPER'",
            "ORDER BY pb_fq_pct ASC;",
            "",
            "-- 4. Forward PE is a current discount. This multiple is not on the history span.",
            "SELECT symbol, ind, pe_fwd, pe_fwd_peer, pe_fwd_vs_ind, pe_fwd_up_pct,",
            "       evebitda_fwd, evebitda_fwd_peer, evebitda_fwd_vs_ind, evebitda_fwd_up_pct,",
            "       strength_read, opm, nd_ebitda, street_left",
            "FROM discount_tape",
            "WHERE pe_fwd_peer = 'DISCOUNT'",
            "ORDER BY pe_fwd_up_pct DESC NULLS LAST;",
            "",
            "-- 5. One name. Every multiple, its bin, its percent, its window path.",
            "SELECT *",
            "FROM discount_tape",
            "WHERE symbol = 'NASDAQ:MU';",
            "",
            "-- 6. How many names sit in each strength label. Not a rank.",
            "SELECT strength_read, COUNT(*) AS n",
            "FROM discount_tape",
            "GROUP BY 1",
            "ORDER BY n DESC;",
            "",
            "-- 7. Edit this where. Discount now on PE or EV/Revenue, and not levered.",
            "SELECT symbol, ind, pe_peer, pe_vs_ind, pe_up_pct, evrev_peer, evrev_vs_ind, evrev_up_pct,",
            "       strength_read, nd_ebitda, opm, fcf_margin, cheaper_count, street_left",
            "FROM discount_tape",
            "WHERE (pe_peer = 'DISCOUNT' OR evrev_peer = 'DISCOUNT')",
            "  AND strength_read <> 'levered'",
            "ORDER BY widest_up_pct DESC NULLS LAST;",
            "",
            f"-- Attach with: ATTACH '{db}' AS discount (READ_ONLY);",
            "",
        ]
    )
