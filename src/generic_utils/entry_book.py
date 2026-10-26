"""Buy band and thesis-kill prices from any catalog row.

Formulas only. No ADD/WAIT, no leftover cutoff, no 0-100.

The band is where this row says to buy. ``kill_px`` is where that entry
dies. A later close can open a new band. The old kill does not ban the
ticker. Street and pack targets stay separate columns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.risk import entry_exit_band, price_at_return, sma50_price
from generic_utils.run_export import load_json_recipe

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "entry_book.json"

_f = to_float


def load_entry_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "entry_book")
    payload.setdefault("atr_mult", 1.5)
    payload.setdefault("band_atr", 0.5)
    payload.setdefault("starter_hint_usd", 5000)
    payload.setdefault("add_hint_usd", 1000)
    payload.setdefault("close_fields", ["close", "price"])
    payload.setdefault("vs50_fields", ["vs50"])
    payload.setdefault("atrp_fields", ["atrp", "atrp_last"])
    payload.setdefault("atr_fields", ["atr"])
    payload.setdefault("street_fields", ["street_px", "fwd_street_px"])
    payload.setdefault("pack_fields", ["target_px", "fwd_pack_px"])
    payload.setdefault("cost_fields", ["cost", "average_price", "average_price_usd"])
    return payload


def _first_num(row: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for name in fields:
        if name not in row:
            continue
        number = _f(row.get(name))
        if number is not None:
            return number
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def trim_level(sma_role: Any, sma: Any) -> dict[str, Any]:
    """First trim versus the kill. A label, not a course and not a cutoff.

    Above the 50-day, ``trim_px`` is that average. A print there lightens a
    held line. ``kill_px`` is further down and ends the entry. Under the
    average the trim has already printed, so ``trim_px`` stays empty.
    """
    role = str(sma_role or "")
    level = _f(sma)
    if role == "stop_below" and level is not None:
        return {"trim_px": level, "trim_state": "trim_at_sma"}
    if role == "reclaim":
        return {"trim_px": None, "trim_state": "through_average"}
    if role == "at_average":
        return {"trim_px": level, "trim_state": "at_average"}
    return {"trim_px": None, "trim_state": "unknown"}


def band_position(close: Any, entry_low: Any, entry_high: Any) -> str:
    """Where a price sits versus the buy band. Not a course."""
    mark = _f(close)
    low = _f(entry_low)
    high = _f(entry_high)
    if mark is None or low is None or high is None:
        return "unknown"
    if mark > high:
        return "above_band"
    if mark < low:
        return "below_band"
    return "inside"


def payoff_at_band(
    entry_high: Any,
    kill_px: Any,
    target: Any,
    *,
    digits: int = 2,
) -> dict[str, float | None]:
    """Payoff if the buy is filled at the top of the band.

    ``rr_at_band`` is target distance divided by the distance down to the
    kill. It is a ratio, not a 0-100. Missing or non-positive risk is None.
    """
    high = _f(entry_high)
    kill = _f(kill_px)
    aim = _f(target)
    kill_pct = None
    target_pct = None
    ratio = None
    if high not in (None, 0) and kill is not None:
        kill_pct = round((kill / high - 1.0) * 100.0, digits)
    if high not in (None, 0) and aim is not None:
        target_pct = round((aim / high - 1.0) * 100.0, digits)
    if high is not None and kill is not None and aim is not None:
        risk = high - kill
        if risk > 0:
            ratio = round((aim - high) / risk, digits)
    return {
        "kill_from_entry_pct": kill_pct,
        "target_from_entry_pct": target_pct,
        "rr_at_band": ratio,
    }


def attach_entry_row(
    row: Mapping[str, Any],
    recipe: Mapping[str, Any] | None = None,
    *,
    digits: int = 2,
) -> dict[str, Any]:
    """Copy the row and add the band, the kill, and the size advice."""
    used = dict(recipe or load_entry_recipe())
    out = dict(row)
    close = _first_num(out, used["close_fields"])
    vs50 = _first_num(out, used["vs50_fields"])
    atrp = _first_num(out, used["atrp_fields"])
    atr = _first_num(out, used["atr_fields"])
    street = _first_num(out, used["street_fields"])
    pack_px = _first_num(out, used["pack_fields"])
    cost = _first_num(out, used["cost_fields"])
    if street is not None:
        target = street
        target_source = "street_px"
    elif pack_px is not None:
        target = pack_px
        target_source = "target_px"
    else:
        target = None
        target_source = "missing"
    band = entry_exit_band(
        close,
        vs50,
        atr=atr,
        atrp=atrp,
        target=target,
        mult=float(used.get("atr_mult") or 1.5),
        band_atr=float(used.get("band_atr") or 0.5),
        digits=digits,
    )
    kill = band["exit_stop"]
    trim = trim_level(band["sma_role"], sma50_price(close, vs50, digits=digits))
    payoff = payoff_at_band(band["entry_high"], kill, target, digits=digits)
    held = _truthy(out.get("in_book"))
    starter = used.get("starter_hint_usd")
    add = used.get("add_hint_usd")
    out.update(
        {
            "sma_role": band["sma_role"],
            "entry_low": band["entry_low"],
            "entry_high": band["entry_high"],
            "exit_stop": kill,
            "kill_px": kill,
            "kill_scope": "this_entry",
            "trim_px": trim["trim_px"],
            "trim_state": trim["trim_state"],
            "exit_target": band["exit_target"],
            "exit_target_street": None if street is None else round(street, digits),
            "exit_target_pack": None if pack_px is None else round(pack_px, digits),
            "target_source": target_source,
            "mark_vs_band": band_position(close, band["entry_low"], band["entry_high"]),
            "cost_kill_px": price_at_return(cost, -15.0, digits=digits) if cost is not None else None,
            "starter_hint_usd": starter,
            "add_hint_usd": add,
            "size_role": "add" if held else "starter",
            "advised_usd": add if held else starter,
            **payoff,
        }
    )
    return out


def attach_entry_book(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One band per input row. Rows are not dropped."""
    used = recipe or load_entry_recipe()
    return [attach_entry_row(row, used) for row in rows]


def _mark_key(row: Mapping[str, Any], field: str) -> str:
    return str(row.get(field) or "").strip().upper()


def attach_later_mark(
    rows: Sequence[Mapping[str, Any]],
    marks: Mapping[str, Any],
    *,
    digits: int = 2,
) -> list[dict[str, Any]]:
    """Attach a later close without moving the band.

    ``band_state_now`` is ``through_kill`` when the later close is at or
    under ``kill_px``. That entry is finished. A new catalog row is a new
    band. The later close does not rewrite ``entry_low`` or ``kill_px``.
    """
    keyed = {str(key).strip().upper(): _f(value) for key, value in marks.items()}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        close_now = None
        for field in ("ticker", "symbol"):
            key = _mark_key(item, field)
            if key and key in keyed and keyed[key] is not None:
                close_now = keyed[key]
                break
        item["close_now"] = None if close_now is None else round(close_now, digits)
        item["now_vs_band"] = (
            band_position(close_now, item.get("entry_low"), item.get("entry_high"))
            if close_now is not None
            else None
        )
        kill = _f(item.get("kill_px"))
        if close_now is None:
            item["band_state_now"] = None
        elif kill is not None and close_now <= kill:
            item["band_state_now"] = "through_kill"
        else:
            item["band_state_now"] = item["now_vs_band"]
        out.append(item)
    return out


def write_entry_book(
    rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    recipe: Mapping[str, Any] | None = None,
    marks: Mapping[str, Any] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """Write entry_book.csv, entry_book.duckdb, queries.txt, overview.log."""
    from generic_utils.run_export import write_run_export

    used = recipe or load_entry_recipe()
    attached = attach_entry_book(rows, used)
    if marks:
        attached = attach_later_mark(attached, marks)
    notes = [
        "entry_low to entry_high is the buy for this row.",
        "kill_px is exit_stop. A print there ends this entry. kill_scope is this_entry.",
        "trim_px is the 50-day when the close is above it (trim_state trim_at_sma). It is the first trim, not the kill.",
        "trim_state through_average means the close is already under the 50-day, so trim_px is empty. at_average means the close is on the average.",
        "A later close can open a new band. It does not rewrite this kill.",
        "exit_target_street and exit_target_pack stay separate. exit_target uses street when it is present.",
        "rr_at_band is target distance from entry_high divided by the distance down to kill_px. Not a 0-100.",
        "starter_hint_usd and add_hint_usd are advice copied from the recipe. They are not a filter.",
        "advised_usd is the starter amount when in_book is false and the add amount when in_book is true.",
        "cost_kill_px is 15 percent under cost when a cost column is on the row. It is separate from kill_px.",
        "band_state_now is through_kill when a later close is at or under kill_px. The band prices do not move.",
    ]
    if source_note:
        notes.insert(0, source_note)
    return write_run_export(
        out_dir,
        tool="generic_utils.entry_book",
        tables={"entry_book": attached},
        sources={"recipe": str(used.get("name") or "entry_book"), "n": len(attached)},
        notes=notes,
        duckdb_name="entry_book.duckdb",
    )


def marks_from_rows(rows: Sequence[Mapping[str, Any]], *, close_field: str = "close") -> dict[str, float]:
    """Ticker and symbol keys to a later close. Last row wins."""
    marks: dict[str, float] = {}
    for row in rows:
        price = _f(row.get(close_field))
        if price is None:
            continue
        for field in ("ticker", "symbol"):
            key = _mark_key(row, field)
            if key:
                marks[key] = price
    return marks
