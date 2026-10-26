"""Ten-name capital band. Formulas only. No ADD/WAIT and no 0-100.

Two sleeves, caller knobs in config/generic_utils/payoff_focus.json:

- discount_momentum: quoted multiple is at least 5% cheaper than the
  industry median, and weeks breakout is at least momentum_min.
- premium_strength: quoted multiple is at least 5% richer, and weeks
  breakout is at least strength_min.

The order inside those sleeves is band_payoff: upside from the top of
the buy band to the value target, divided by the distance down to the
stop. The value target is the year-1 multiple brought inline with the
industry median. The quoted-multiple inline price is discount_px. Street
stays its own target and is the fallback when those two upsides are
missing. A wide upside over a tight stop sorts first. It is not a score
and it is not rr.

stake_per_1k_usd is the check that earns $1,000 if that target prints.
10% upside is a $10,000 check. 50% upside is a $2,000 check.
profit_on_10k_usd and profit_on_5k_usd are the dollar result of those
two fixed checks. A 10% name turns $10,000 into $1,000. A 50% name
turns $5,000 into $2,500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.entry_book import attach_entry_row, payoff_at_band
from generic_utils.ranking import group_capped_top_n, to_float
from generic_utils.run_export import load_json_recipe

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "payoff_focus.json"

_f = to_float


def load_payoff_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "payoff_focus")
    payload.setdefault("top", 10)
    payload.setdefault("group_field", "ind")
    payload.setdefault("cap", 2)
    payload.setdefault("unit_profit_usd", 1000)
    payload.setdefault("check_10k_usd", 10000)
    payload.setdefault("check_5k_usd", 5000)
    payload.setdefault("discount_max", -5)
    payload.setdefault("premium_min", 5)
    payload.setdefault("momentum_min", 0.15)
    payload.setdefault("strength_min", 0.5)
    payload.setdefault("atr_mult", 1.5)
    payload.setdefault("band_atr", 0.5)
    payload.setdefault("close_fields", ["close", "price"])
    payload.setdefault("vs50_fields", ["vs50"])
    payload.setdefault("atrp_fields", ["atrp", "atrp_last"])
    payload.setdefault("atr_fields", ["atr"])
    payload.setdefault("street_fields", ["street_px", "fwd_street_px", "pt"])
    payload.setdefault("pack_fields", ["target_px", "fwd_pack_px"])
    payload.setdefault("bo_fields", ["bo"])
    payload.setdefault("peer_fields", ["peer"])
    payload.setdefault("vs_ind_fields", ["val_vs_ind"])
    payload.setdefault("pe_y1_fields", ["fp_pe_y1", "pe_fwd"])
    payload.setdefault("pe_y5_fields", ["fp_pe_y5"])
    payload.setdefault("pe_peer_fields", ["pe_fwd_ind_med", "pe_ind_med"])
    payload.setdefault("pe_trail_fields", ["pe"])
    payload.setdefault("pe_trail_peer_fields", ["pe_ind_med"])
    payload.setdefault("evrev_fields", ["evrev"])
    payload.setdefault("evrev_peer_fields", ["evrev_ind_med"])
    payload.setdefault("evebit_fields", ["evebit", "ev_ebit"])
    payload.setdefault("evebit_peer_fields", ["evebit_ind_med", "ev_ebit_ind_med"])
    payload.setdefault("oldest_fields", ["oldest_close", "close_first", "close_oldest"])
    payload.setdefault("low_fields", ["close_min"])
    payload.setdefault("high_fields", ["close_max"])
    payload.setdefault("pe_sane_min", 2)
    payload.setdefault("pe_sane_max", 200)
    payload.setdefault("max_value_upside_pct", 100)
    return payload


def _first(row: Mapping[str, Any], fields: Sequence[str]) -> Any:
    for name in fields:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _peer_side(row: Mapping[str, Any], recipe: Mapping[str, Any]) -> str:
    """DISCOUNT, PREMIUM, or empty. val_vs_ind wins when it is present."""
    gap = _f(_first(row, recipe.get("vs_ind_fields") or ["val_vs_ind"]))
    discount_max = float(recipe.get("discount_max") if recipe.get("discount_max") is not None else -5)
    premium_min = float(recipe.get("premium_min") if recipe.get("premium_min") is not None else 5)
    if gap is not None:
        if gap <= discount_max:
            return "DISCOUNT"
        if gap >= premium_min:
            return "PREMIUM"
        return ""
    peer = str(_first(row, recipe.get("peer_fields") or ["peer"]) or "").strip().upper()
    if peer in {"DISCOUNT", "PREMIUM"}:
        return peer
    return ""


def allocation_sleeve(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> str:
    """discount_momentum, premium_strength, or empty. Empty is not a focus row."""
    used = recipe or load_payoff_recipe()
    side = _peer_side(row, used)
    bo = _f(_first(row, used.get("bo_fields") or ["bo"]))
    if side == "" or bo is None:
        return ""
    if side == "DISCOUNT" and bo >= float(used.get("momentum_min") if used.get("momentum_min") is not None else 0.15):
        return "discount_momentum"
    if side == "PREMIUM" and bo >= float(used.get("strength_min") if used.get("strength_min") is not None else 0.5):
        return "premium_strength"
    return ""


def price_at_peer_multiple(close: Any, multiple: Any, peer_multiple: Any) -> float | None:
    """Price if this multiple moved to the peer median and the denominator stayed put.

    Works for a price multiple (PE, forward PE). An EV multiple is only
    approximate. Missing or non-positive inputs are None.
    """
    mark = _f(close)
    current = _f(multiple)
    peer = _f(peer_multiple)
    if mark is None or current is None or peer is None:
        return None
    if mark <= 0 or current <= 0 or peer <= 0:
        return None
    return round(mark * peer / current, 2)


def _sane_pe(multiple: Any, recipe: Mapping[str, Any]) -> bool:
    number = _f(multiple)
    if number is None:
        return False
    floor = float(recipe.get("pe_sane_min") if recipe.get("pe_sane_min") is not None else 2)
    ceiling = float(recipe.get("pe_sane_max") if recipe.get("pe_sane_max") is not None else 200)
    return floor <= number <= ceiling


def year_value_prices(
    close: Any,
    peer_pe: Any,
    pe_y1: Any,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Year-1 PE at the industry median. No year-3 or year-5 price.

    A PE outside the sane band is skipped. This is not the street target.
    """
    used = recipe or load_payoff_recipe()
    peer = peer_pe if _sane_pe(peer_pe, used) else None
    y1 = price_at_peer_multiple(close, pe_y1, peer) if _sane_pe(pe_y1, used) else None
    return {"value_y1_px": y1}


def _positive_multiple(value: Any, ceiling: float = 80.0) -> bool:
    number = _f(value)
    return number is not None and 0 < number <= ceiling


def _metric_price(
    close: Any,
    row: Mapping[str, Any],
    multiple_fields: Sequence[str],
    median_fields: Sequence[str],
    *,
    sane_pe: bool = False,
    recipe: Mapping[str, Any] | None = None,
) -> float | None:
    multiple = _first(row, multiple_fields)
    median = _first(row, median_fields)
    if sane_pe:
        used = recipe or load_payoff_recipe()
        if not _sane_pe(multiple, used) or not _sane_pe(median, used):
            return None
    elif not _positive_multiple(multiple) or not _positive_multiple(median):
        return None
    return price_at_peer_multiple(close, multiple, median)


def attach_value_targets(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Year-1 PE value, plus a peer price on P/E, EV/EBIT, and EV/Rev.

    All three prices are returned. A missing multiple stays empty. EBITDA
    is not used. ``value_px`` is the year-1 PE price, or trailing PE when
    year-1 is missing.
    """
    used = recipe or load_payoff_recipe()
    close = _f(_first(row, used.get("close_fields") or ["close", "price"]))
    y1 = year_value_prices(
        close,
        _first(row, used.get("pe_peer_fields") or ["pe_fwd_ind_med"]),
        _first(row, used.get("pe_y1_fields") or ["fp_pe_y1", "pe_fwd"]),
        used,
    )["value_y1_px"]
    trail = _metric_price(
        close,
        row,
        used.get("pe_trail_fields") or ["pe"],
        used.get("pe_trail_peer_fields") or ["pe_ind_med"],
        sane_pe=True,
        recipe=used,
    )
    evebit = _metric_price(
        close,
        row,
        used.get("evebit_fields") or ["evebit", "ev_ebit"],
        used.get("evebit_peer_fields") or ["evebit_ind_med", "ev_ebit_ind_med"],
    )
    evrev = _metric_price(
        close,
        row,
        used.get("evrev_fields") or ["evrev"],
        used.get("evrev_peer_fields") or ["evrev_ind_med"],
    )
    if y1 is not None:
        value, source = y1, "pe_y1"
    elif trail is not None:
        value, source = trail, "pe_trail"
    else:
        value, source = None, "missing"
    return {
        "value_y1_px": y1,
        "value_px": value,
        "value_source": source,
        "pe_discount_px": y1 if y1 is not None else trail,
        "evrev_discount_px": evrev,
        "evebit_discount_px": evebit,
        "discount_px": evebit if evebit is not None else evrev,
        "discount_source": "evebit" if evebit is not None else ("evrev" if evrev is not None else "missing"),
    }


def attach_price_strength(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Where the close sits versus the oldest captured price on the row.

    Uses ``oldest_close``, ``close_first``, or ``close_oldest`` when one is
    present. Window low and high use ``close_min`` and ``close_max``.
    Missing history is ``unknown``. This is not a score.
    """
    used = recipe or load_payoff_recipe()
    close = _f(_first(row, used.get("close_fields") or ["close", "price"]))
    oldest = _f(_first(row, used.get("oldest_fields") or ["oldest_close", "close_first"]))
    low = _f(_first(row, used.get("low_fields") or ["close_min"]))
    high = _f(_first(row, used.get("high_fields") or ["close_max"]))

    def _gap(base: float | None) -> float | None:
        if close is None or base in (None, 0):
            return None
        return round((close / float(base) - 1.0) * 100.0, 2)

    versus = _gap(oldest)
    if versus is None:
        state = "unknown"
    elif versus > 0.5:
        state = "above_oldest"
    elif versus < -0.5:
        state = "below_oldest"
    else:
        state = "at_oldest"
    return {
        "oldest_close": None if oldest is None else round(oldest, 2),
        "px_vs_oldest_pct": versus,
        "strength_state": state,
        "px_vs_low_pct": _gap(low),
        "px_vs_high_pct": _gap(high),
    }


def load_evebit_peers(
    all_fields_db: str | Path,
    run_id: str,
    *,
    mcap_min: float = 2_000_000_000,
    min_n: int = 6,
    multiple_cap: float = 80.0,
) -> dict[str, dict[str, float | None]]:
    """EV/EBIT and its industry median from one all-fields run.

    Median uses US names at or above ``mcap_min``, with at least ``min_n``
    quotes in the industry. EBITDA is not read.
    """
    import duckdb

    con = duckdb.connect(str(all_fields_db), read_only=True)
    rows = con.execute(
        """
        WITH base AS (
            SELECT symbol, industry, TRY_CAST(enterprise_value_to_ebit_ttm AS DOUBLE) AS evebit
            FROM all_fields_rows
            WHERE run_id = ?
              AND TRY_CAST(market_cap_basic AS DOUBLE) >= ?
              AND TRY_CAST(enterprise_value_to_ebit_ttm AS DOUBLE) > 0
              AND TRY_CAST(enterprise_value_to_ebit_ttm AS DOUBLE) <= ?
        ),
        med AS (
            SELECT industry, median(evebit) AS evebit_ind_med, count(*) AS n
            FROM base
            GROUP BY industry
        )
        SELECT b.symbol, b.evebit, m.evebit_ind_med
        FROM base b
        LEFT JOIN med m ON b.industry = m.industry AND m.n >= ?
        """,
        [run_id, mcap_min, multiple_cap, min_n],
    ).fetchall()
    out: dict[str, dict[str, float | None]] = {}
    for symbol, evebit, median in rows:
        key = str(symbol or "").strip()
        if not key:
            continue
        out[key] = {
            "evebit": None if evebit is None else round(float(evebit), 4),
            "evebit_ind_med": None if median is None else round(float(median), 4),
        }
    return out


def capital_for_upside(
    up_pct: Any,
    *,
    unit_profit_usd: float = 1000,
    check_10k_usd: float = 10000,
    check_5k_usd: float = 5000,
) -> dict[str, float | None]:
    """Dollars behind an upside percent. Missing or non-positive upside is None."""
    up = _f(up_pct)
    stake = None
    profit_10k = None
    profit_5k = None
    if up is not None and up > 0:
        stake = round(float(unit_profit_usd) / (up / 100.0), 0)
        profit_10k = round(float(check_10k_usd) * up / 100.0, 0)
        profit_5k = round(float(check_5k_usd) * up / 100.0, 0)
    return {
        "stake_per_1k_usd": stake,
        "profit_on_10k_usd": profit_10k,
        "profit_on_5k_usd": profit_5k,
    }


def attach_payoff_row(
    row: Mapping[str, Any],
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy the row and add the band, the sleeve, and the dollar reading."""
    used = load_payoff_recipe()
    if recipe:
        used.update(recipe)
    band_recipe = {
        "close_fields": used["close_fields"],
        "vs50_fields": used["vs50_fields"],
        "atrp_fields": used["atrp_fields"],
        "atr_fields": used["atr_fields"],
        "street_fields": used["street_fields"],
        "pack_fields": used["pack_fields"],
        "cost_fields": ["cost", "average_price", "average_price_usd"],
        "atr_mult": used["atr_mult"],
        "band_atr": used["band_atr"],
        "starter_hint_usd": None,
        "add_hint_usd": None,
    }
    out = attach_entry_row(row, band_recipe)
    up = out.get("target_from_entry_pct")
    down = out.get("kill_from_entry_pct")
    out["up_from_entry_pct"] = up
    out["down_from_entry_pct"] = down
    out["band_payoff_street"] = out.get("rr_at_band")
    out.update(attach_value_targets(out, used))
    out.update(attach_price_strength(out, used))
    value_pay = payoff_at_band(out.get("entry_high"), out.get("kill_px"), out.get("value_px"))
    discount_pay = payoff_at_band(out.get("entry_high"), out.get("kill_px"), out.get("discount_px"))
    out["value_up_from_entry_pct"] = value_pay["target_from_entry_pct"]
    out["band_payoff_value"] = value_pay["rr_at_band"]
    out["discount_up_from_entry_pct"] = discount_pay["target_from_entry_pct"]
    out["band_payoff_discount"] = discount_pay["rr_at_band"]
    cap = float(used.get("max_value_upside_pct") if used.get("max_value_upside_pct") is not None else 100)
    source, chosen = _choose_band_payoff(out, cap)
    out["band_payoff_source"] = source
    out["band_payoff"] = chosen
    out["alloc_sleeve"] = allocation_sleeve(out, used)
    money = capital_for_upside(
        up,
        unit_profit_usd=float(used.get("unit_profit_usd") or 1000),
        check_10k_usd=float(used.get("check_10k_usd") or 10000),
        check_5k_usd=float(used.get("check_5k_usd") or 5000),
    )
    value_money = capital_for_upside(
        out.get("value_up_from_entry_pct"),
        unit_profit_usd=float(used.get("unit_profit_usd") or 1000),
        check_10k_usd=float(used.get("check_10k_usd") or 10000),
        check_5k_usd=float(used.get("check_5k_usd") or 5000),
    )
    out.update(money)
    out["stake_per_1k_value"] = value_money["stake_per_1k_usd"]
    out["profit_on_5k_value"] = value_money["profit_on_5k_usd"]
    return out


def _close_upside(price: Any, close: Any) -> float | None:
    mark = _f(close)
    aim = _f(price)
    if mark in (None, 0) or aim is None:
        return None
    return (aim / mark - 1.0) * 100.0


def _inside_cap(price: Any, close: Any, cap: float) -> bool:
    upside = _close_upside(price, close)
    return upside is not None and 0 < upside <= cap


def _choose_band_payoff(row: Mapping[str, Any], cap: float) -> tuple[str, float | None]:
    """Year-1 value when the inline price is above the close and within the cap.

    A value price already under the close does not fall back to street.
    A value price more than ``cap`` percent above the close is still stored
    and does not set the order. Discount, then street, can.
    """
    close = row.get("close")
    value_up = _close_upside(row.get("value_px"), close) if row.get("value_px") is not None else None
    if value_up is not None and value_up <= 0:
        return "value_below", None
    if value_up is not None and value_up <= cap:
        upside = _f(row.get("value_up_from_entry_pct"))
        ratio = _f(row.get("band_payoff_value"))
        if upside is not None and upside > 0 and ratio is not None:
            return "value", ratio
    wide = value_up is not None and value_up > cap
    if _inside_cap(row.get("discount_px"), close, cap):
        upside = _f(row.get("discount_up_from_entry_pct"))
        ratio = _f(row.get("band_payoff_discount"))
        if upside is not None and upside > 0 and ratio is not None:
            return "discount", ratio
    street_up = _f(row.get("up_from_entry_pct"))
    street_ratio = _f(row.get("band_payoff_street"))
    if street_up is not None and street_up > 0 and street_ratio is not None:
        return ("value_wide" if wide else "street"), street_ratio
    if wide:
        return "value_wide", None
    return "missing", None


def attach_payoff_rows(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    used = load_payoff_recipe()
    if recipe:
        used.update(recipe)
    return [attach_payoff_row(row, used) for row in rows]


def focus_payoff(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Top names in the two sleeves, ordered by band_payoff.

    Rows outside the sleeves stay in ``attached`` and off the focus.
    Overflow is the sleeve rows the industry cap or the top size left out.
    """
    used = load_payoff_recipe()
    if recipe:
        used.update(recipe)
    attached = attach_payoff_rows(rows, used)
    qualified = [
        row
        for row in attached
        if row.get("alloc_sleeve") and _f(row.get("band_payoff")) is not None
    ]
    top = int(used.get("top") or 10)
    cap = used.get("cap")
    group_field = str(used.get("group_field") or "ind")
    if cap is None:
        from generic_utils.ranking import top_n

        focus = list(top_n(qualified, field="band_payoff", n=top, reverse=True))
        overflow: list[dict[str, Any]] = []
    else:
        focus, overflow = group_capped_top_n(
            qualified,
            group_field=group_field,
            cap=int(cap),
            field="band_payoff",
            reverse=True,
            n=top,
            overflow_fields=("alloc_sleeve", "band_payoff", "up_from_entry_pct", "ticker"),
        )
        focus = list(focus)
    for index, row in enumerate(focus, start=1):
        row["focus_order"] = index
    return {
        "attached": attached,
        "qualified": qualified,
        "focus": focus,
        "overflow": overflow,
        "spec": {
            "top": top,
            "cap": cap,
            "group_field": group_field,
            "n_in": len(attached),
            "n_discount_momentum": sum(1 for row in attached if row.get("alloc_sleeve") == "discount_momentum"),
            "n_premium_strength": sum(1 for row in attached if row.get("alloc_sleeve") == "premium_strength"),
            "n_qualified": len(qualified),
            "n_focus": len(focus),
            "n_overflow": len(overflow),
            "sort": "band_payoff",
            "band_payoff_order": "value, discount, street",
            "momentum_min": used.get("momentum_min"),
            "strength_min": used.get("strength_min"),
        },
    }


def write_payoff_focus(
    rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    recipe: Mapping[str, Any] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """Write payoff_focus.csv, the qualified sleeve, overflow, and queries.txt."""
    from generic_utils.run_export import write_run_export

    built = focus_payoff(rows, recipe)
    notes = [
        "alloc_sleeve discount_momentum: val_vs_ind at or below -5, or peer DISCOUNT when the percent is missing, and bo at least momentum_min.",
        "alloc_sleeve premium_strength: val_vs_ind at or above 5, or peer PREMIUM when the percent is missing, and bo at least strength_min.",
        "An empty alloc_sleeve is not on the focus. Discount without breakout, and premium without strength, stay off.",
        "entry_low to entry_high is the buy. exit_stop is under that band. exit_target is the street price when the row has one.",
        "up_from_entry_pct is the percent from entry_high to exit_target. down_from_entry_pct is the percent from entry_high down to exit_stop.",
        "band_payoff_street is the street distance over the kill. band_payoff_value uses value_px. band_payoff_discount uses discount_px.",
        "value_px is year-1 PE at the industry median. If that PE is missing, trailing PE, then EV/EBIT, then EV/Rev. EBITDA is not used.",
        "discount_px is the other peer price: EV/EBIT when the row has it, otherwise EV/Rev. It is not the street price and not EV/EBITDA.",
        "band_payoff uses value, then discount, then street, and only when that upside from entry_high is positive. band_payoff_source names which one. It is not rr.",
        "strength_state is above_oldest, below_oldest, at_oldest, or unknown from the oldest close on the row. It does not change the sort.",
        "stake_per_1k_usd is the check that earns 1000 dollars if exit_target prints. 10 percent upside is 10000 dollars. 50 percent upside is 2000 dollars.",
        "profit_on_10k_usd is 10000 times the upside percent. profit_on_5k_usd is 5000 times the upside percent. A 50 percent name turns 5000 dollars into 2500 of profit.",
        "focus_order is reading order inside this cut. It is not a second Daily budget.",
        "Industry cap and top size live in the recipe. Overflow rows are the sleeve names those knobs left out.",
    ]
    if source_note:
        notes.insert(0, source_note)
    return write_run_export(
        out_dir,
        tool="generic_utils.payoff_focus",
        tables={
            "payoff_focus": built["focus"],
            "payoff_qualified": built["qualified"],
            "payoff_overflow": built["overflow"],
        },
        sources=built["spec"],
        notes=notes,
        duckdb_name="payoff_focus.duckdb",
    )
