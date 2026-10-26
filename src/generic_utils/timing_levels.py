"""Entry band, timing kill, and thesis-change prices.

Prices only. No ENTER/WATCH, no leftover cutoff, no 0-100.

The buy band is the same band as ``entry_exit_band``. ``kill_px`` uses the
timing stop width (0.75 ATR on a two-day setup, 1.0 ATR otherwise), not the
1.5 ATR book kill. ``thesis_drawdown_px`` is the 10% drawdown line under the
recent high. ``thesis_chase_px`` is a 5% day, the print that can become
AVOID_CHASE on the next session when breadth is not expanding.

``street_up_pct`` and ``downside_atr_pct`` are separate. Their ratio is
``up_down_ratio``. A caller screen may filter on them. This module does not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.entry_book import trim_level
from generic_utils.ranking import to_float
from generic_utils.risk import entry_exit_band
from generic_utils.run_export import load_json_recipe, write_run_export

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "timing_levels.json"
DEFAULT_SCREEN = _PROJECT / "config" / "generic_utils" / "low_downside.json"

_f = to_float

LEVEL_FIELDS = (
    "entry_low",
    "entry_high",
    "sma_role",
    "trim_px",
    "trim_state",
    "kill_px",
    "kill_atr_units",
    "thesis_drawdown_px",
    "thesis_drawdown_state",
    "thesis_chase_px",
    "thesis_chase_state",
    "thesis_chase_from",
    "downside_atr_pct",
    "downside_atr_units",
    "street_up_pct",
    "up_down_ratio",
)


def load_timing_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "timing_levels")
    payload.setdefault("stop_atr_units", 1.0)
    payload.setdefault("band_atr", 0.5)
    payload.setdefault("drawdown_min_pct", 10.0)
    payload.setdefault("chase_day_pct", 5.0)
    payload.setdefault("downside_atr_units", 1.5)
    payload.setdefault("close_fields", ["close", "current_close", "price"])
    payload.setdefault("vs50_fields", ["vs50"])
    payload.setdefault("sma_fields", ["sma50", "SMA50"])
    payload.setdefault("atrp_fields", ["atrp", "atrp_last"])
    payload.setdefault("atr_fields", ["atr"])
    payload.setdefault("stop_fields", ["stop_atr_units", "kill_atr_units"])
    payload.setdefault(
        "street_pct_fields", ["street_up_pct", "street_left", "fwd_street_pct"]
    )
    payload.setdefault("street_px_fields", ["street_px", "fwd_street_px", "pt"])
    payload.setdefault(
        "drawdown_fields", ["drawdown_from_high_pct", "drawdown_pct"]
    )
    payload.setdefault("high_fields", ["high_px", "close_max"])
    payload.setdefault("day_fields", ["current_day_pct", "day", "change"])
    payload.setdefault("prior_close_fields", ["prior_close"])
    return payload


def load_screen_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_SCREEN
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "low_downside")
    payload.setdefault(
        "where",
        "street_up_pct>=8,downside_atr_pct<=18,up_down_ratio>=1.5,rng<=80,rsi<=70",
    )
    payload.setdefault("field", "up_down_ratio")
    payload.setdefault("top", 120)
    payload.setdefault("group_field", "ind")
    payload.setdefault("group_field_fallback", "industry")
    payload.setdefault("cap", 6)
    return payload


def _first_num(row: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for name in fields:
        number = _f(row.get(name))
        if number is not None:
            return number
    return None


def vs50_from_sma(close: Any, sma: Any) -> float | None:
    """Percent of close versus a 50-day price. Positive means the close is above."""
    mark = _f(close)
    average = _f(sma)
    if mark is None or average is None or average == 0:
        return None
    return (mark / average - 1.0) * 100.0


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _street_up_pct(row: Mapping[str, Any], close: float | None, recipe: Mapping[str, Any]) -> float | None:
    quoted = _first_num(row, recipe["street_pct_fields"])
    if quoted is not None:
        return quoted
    street_px = _first_num(row, recipe["street_px_fields"])
    if street_px is None or close is None or close == 0:
        return None
    return (street_px / close - 1.0) * 100.0


def _drawdown_line(
    close: float | None,
    row: Mapping[str, Any],
    recipe: Mapping[str, Any],
) -> tuple[float | None, str]:
    floor_pct = float(recipe["drawdown_min_pct"])
    high = _first_num(row, recipe["high_fields"])
    if high is None and close is not None:
        drawdown = _first_num(row, recipe["drawdown_fields"])
        if drawdown is not None and (1.0 + drawdown / 100.0) != 0:
            high = close / (1.0 + drawdown / 100.0)
    if high is None:
        return None, "unknown"
    line = high * (1.0 - floor_pct / 100.0)
    if close is None:
        return line, "unknown"
    state = "in_drawdown" if close <= line else "above_drawdown"
    return line, state


def _chase_line(
    close: float | None,
    row: Mapping[str, Any],
    recipe: Mapping[str, Any],
) -> tuple[float | None, str, str]:
    chase_pct = float(recipe["chase_day_pct"])
    prior = _first_num(row, recipe["prior_close_fields"])
    source = "prior_close" if prior is not None else ""
    if prior is None and close is not None:
        day = _first_num(row, recipe["day_fields"])
        if day is not None and (1.0 + day / 100.0) != 0:
            prior = close / (1.0 + day / 100.0)
            source = "session_open"
    if prior is None and close is not None:
        prior = close
        source = "this_close"
    if prior is None:
        return None, "unknown", "missing"
    line = prior * (1.0 + chase_pct / 100.0)
    if close is None:
        return line, "unknown", source or "missing"
    state = "through_chase" if close >= line else "under_chase"
    return line, state, source or "missing"


def attach_timing_levels(
    row: Mapping[str, Any],
    *,
    recipe: Mapping[str, Any] | None = None,
    stop_atr_units: float | None = None,
) -> dict[str, Any]:
    """Prices for one row. Missing inputs stay empty. Nothing is filtered out."""
    used = dict(recipe or load_timing_recipe())
    close = _first_num(row, used["close_fields"])
    vs50 = _first_num(row, used["vs50_fields"])
    if vs50 is None:
        vs50 = vs50_from_sma(close, _first_num(row, used["sma_fields"]))
    atrp = _first_num(row, used["atrp_fields"])
    atr = _first_num(row, used["atr_fields"])
    units = stop_atr_units
    if units is None:
        units = _first_num(row, used["stop_fields"])
    if units is None:
        units = float(used["stop_atr_units"])
    band = entry_exit_band(
        close,
        vs50,
        atr=atr,
        atrp=atrp,
        mult=float(units),
        band_atr=float(used["band_atr"]),
    )
    sma = band["entry_low"] if band["sma_role"] in {"reclaim", "stop_below", "at_average"} else None
    trim = trim_level(band["sma_role"], sma)
    drawdown_px, drawdown_state = _drawdown_line(close, row, used)
    chase_px, chase_state, chase_from = _chase_line(close, row, used)
    down_units = float(used["downside_atr_units"])
    downside = None if atrp is None else atrp * down_units
    street = _street_up_pct(row, close, used)
    ratio = None
    if street is not None and downside is not None and downside > 0:
        ratio = street / downside
    return {
        "entry_low": band["entry_low"],
        "entry_high": band["entry_high"],
        "sma_role": band["sma_role"],
        "trim_px": trim["trim_px"],
        "trim_state": trim["trim_state"],
        "kill_px": band["exit_stop"],
        "kill_atr_units": _round(float(units), 2),
        "thesis_drawdown_px": _round(drawdown_px),
        "thesis_drawdown_state": drawdown_state,
        "thesis_chase_px": _round(chase_px),
        "thesis_chase_state": chase_state,
        "thesis_chase_from": chase_from,
        "downside_atr_pct": _round(downside),
        "downside_atr_units": down_units,
        "street_up_pct": _round(street),
        "up_down_ratio": _round(ratio),
    }


def attach_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    recipe: Mapping[str, Any] | None = None,
    stop_atr_units: float | None = None,
) -> list[dict[str, Any]]:
    used = recipe or load_timing_recipe()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.update(
            attach_timing_levels(row, recipe=used, stop_atr_units=stop_atr_units)
        )
        out.append(item)
    return out


def apply_caller_screen(
    rows: Sequence[Mapping[str, Any]],
    screen: Mapping[str, Any],
) -> dict[str, Any]:
    """Rank with the caller's where-string. The where lives in the recipe, not here."""
    from generic_utils.focus import build_focus

    group = screen.get("group_field")
    fallback = screen.get("group_field_fallback")
    sample = rows[0] if rows else {}
    if group and sample and group not in sample and fallback in sample:
        group = fallback
    return build_focus(
        list(rows),
        where=screen.get("where"),
        field=str(screen.get("field") or "up_down_ratio"),
        n=int(screen.get("top") or 120),
        reverse=True,
        group_field=group,
        cap=screen.get("cap"),
        id_field=str(screen.get("id_field") or "symbol"),
    )


def write_timing_levels(
    rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    recipe: Mapping[str, Any] | None = None,
    screen: Mapping[str, Any] | None = None,
    stop_atr_units: float | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    used = dict(recipe or load_timing_recipe())
    leveled = attach_rows(rows, recipe=used, stop_atr_units=stop_atr_units)
    tables: dict[str, list[dict[str, Any]]] = {"timing_levels": leveled}
    notes = [
        "entry_low to entry_high is the buy. reclaim means the 50-day is overhead. Do not buy that row at the close.",
        "kill_px is stop_atr_units times ATR under the band. Timing setups use 0.75 or 1.0. The book kill in entry-book stays 1.5. They are different prices.",
        "trim_px is the 50-day when trim_state is trim_at_sma. through_average leaves trim_px empty.",
        "thesis_drawdown_px is the recent high times (1 - drawdown_min_pct). in_drawdown means the close is already through it. A close back above it ends that drawdown leg.",
        "thesis_chase_px is a chase_day_pct move off the prior close. through_chase is the print that can become AVOID_CHASE next session if breadth is not expanding.",
        "street_up_pct is the street quote. downside_atr_pct is downside_atr_units times ATRP. up_down_ratio divides them. It is not a 0-100.",
        "ENTER_SMALL and ENTER_PROBE stay climate. These prices do not authorize a new line.",
    ]
    if source_note:
        notes.append(source_note)
    if screen:
        focused = apply_caller_screen(leveled, screen)
        tables["timing_screen"] = list(focused.get("rows") or [])
        notes.append(
            "timing_screen is the caller where on timing_levels: "
            + str(screen.get("where"))
            + ". Edit config/generic_utils/low_downside.json. It is not a rank score."
        )
    return write_run_export(
        out_dir,
        tool="tv_scan_cli.timing-levels",
        tables=tables,
        sources={
            "recipe": used.get("name"),
            "stop_atr_units": stop_atr_units if stop_atr_units is not None else used.get("stop_atr_units"),
            "screen": None if not screen else screen.get("name"),
            "n_in": len(rows),
        },
        notes=notes,
        duckdb_name="timing_levels.duckdb",
    )
