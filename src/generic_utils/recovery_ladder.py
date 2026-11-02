"""Recovery ladder: what adding to a held line does to its average cost and to the price that repairs it.

For every Book line (PACK/book_risk) joined to its levels and measured state (PACK/deterioration ``names``):

- ``lines``: the line now (value, cost, P&L, average price, the rise from the close back to the average and to the
  average plus each ``gain_pct``), its recovery levels (50-day, half-way to the high, the high, the street price),
  its bounce band and kills, and the state it sits in (drawdown, letters, the cell's 28 / 56-day excess and odds).
  ``add_be_at_<target>_usd`` solves the add, bought at the first ``solve_at`` level present, after which a return to
  ``<target>`` breaks the whole line even (0 when the target is already above the average; empty when the target
  is not above the buy price).
- ``ladder``: one row per line x add level x add size: the new average, the rise from the buy price to break even
  and to each gain, the levels it sits against, and the add's own loss at each kill.

Prices are in the line's quote currency (the levels are), dollars in USD through the book's price-to-USD rate.
No ranking, no cutoff, no course: the scenarios are recipe knobs (config/generic_utils/recovery_ladder.json) and
whether to add is the judgment's (deterioration skill: a double-down needs a no-letter state, a band and a kill).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv

_f = to_float
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "recovery_ladder.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _pct(to: float | None, frm: float | None) -> float | None:
    return None if to is None or not frm else round((to / frm - 1.0) * 100.0, 2)


def average_after_add(shares: float, avg_px: float, add_cost: float, add_px: float) -> tuple[float, float]:
    """(new shares, new average price) after buying ``add_cost`` (quote currency) at ``add_px``."""
    new_shares = shares + add_cost / add_px
    return new_shares, (shares * avg_px + add_cost) / new_shares


def add_to_break_even_at(shares: float, avg_px: float, add_px: float, target_px: float) -> float | None:
    """Quote-currency add at ``add_px`` after which the average equals ``target_px``.

    0 when the target is at or above the current average; None when the target is not above the buy price
    (no add at that price can bring the average down to it).
    """
    if target_px >= avg_px:
        return 0.0
    if target_px <= add_px:
        return None
    return (shares * avg_px - shares * target_px) / (target_px / add_px - 1.0)


def _levels(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, float | None]:
    return {k: _f(row.get(k)) for k in fields}


def build_recovery_ladder(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = ()) -> dict[str, Any]:
    from generic_utils.series import match_wanted_id

    recipe = dict(recipe or load_recipe())
    pack = Path(pack_dir)
    book = rows_from_csv(pack / "book_risk" / "book_risk.csv")
    names_path = pack / "deterioration" / "names.csv"
    state = {str(r.get("symbol")): r for r in (rows_from_csv(names_path) if names_path.exists() else [])}
    wanted = [str(i) for i in ids if str(i)]
    gains = [float(g) for g in recipe.get("gain_pct") or []]
    lines: list[dict[str, Any]] = []
    ladder: list[dict[str, Any]] = []
    for b in book:
        sym = str(b.get("symbol") or "")
        if wanted and match_wanted_id(sym, wanted) is None:
            continue
        shares, avg, close = _f(b.get("shares")), _f(b.get("average_price")), _f(b.get("close_quote") or b.get("close"))
        fx = _f(b.get("fx_rate_price_to_usd")) or 1.0
        if not shares or not avg or not close:
            continue
        s = state.get(sym) or {}
        lv = _levels(s, recipe.get("level_fields") or [])
        if str(b.get("price_currency") or "USD").upper() != "USD":
            lv["street_px"] = None  # the street target on a non-US line is the USD listing's; the other levels are in the quote currency
        lv["close"] = close
        kill = next((lv[k] for k in recipe.get("kill_fields") or [] if lv.get(k)), None)
        value, cost = _f(b.get("value_usd")), _f(b.get("cost_usd"))
        line: dict[str, Any] = {
            "symbol": sym, "ind": b.get("industry"), "ccy": b.get("price_currency"), "value_usd": _r(value, 0), "cost_usd": _r(cost, 0),
            "pnl_usd": _r((value or 0) - (cost or 0), 0) if value is not None and cost is not None else None,
            "pnl_pct": _pct(value, cost), "close": close, "avg_px": _r(avg), "shares": _r(shares, 3),
            "be_from_close_pct": _pct(avg, close),
        }
        for g in gains:
            line[f"gain{g:g}_px"] = _r(avg * (1 + g / 100.0))
            line[f"gain{g:g}_from_close_pct"] = _pct(avg * (1 + g / 100.0), close)
        for k, v in lv.items():
            if k != "close":
                line[k] = v
        for k in recipe.get("state_fields") or []:
            line[k] = s.get(k)
        at_name = next((k for k in recipe.get("solve_at") or [] if lv.get(k)), None)
        at_px = lv.get(at_name) if at_name else None
        line["solve_at"], line["solve_at_px"] = at_name, at_px
        for t in recipe.get("solve_targets") or []:
            target = lv.get(t)
            add_q = add_to_break_even_at(shares, avg, at_px, target) if at_px and target else None
            line[f"add_be_at_{t}_usd"] = _r(add_q * fx, 0) if add_q is not None else None
        lines.append(line)
        for level in recipe.get("add_levels") or []:
            px = lv.get(level)
            if not px:
                continue
            for add_usd in recipe.get("add_usd") or []:
                new_shares, new_avg = average_after_add(shares, avg, float(add_usd) / fx, px)
                row = {"symbol": sym, "add_at": level, "add_px": _r(px), "add_usd": add_usd, "new_avg": _r(new_avg),
                       "avg_cut_pct": _pct(new_avg, avg), "be_from_add_pct": _pct(new_avg, px)}
                for g in gains:
                    row[f"gain{g:g}_px"] = _r(new_avg * (1 + g / 100.0))
                    row[f"gain{g:g}_from_add_pct"] = _pct(new_avg * (1 + g / 100.0), px)
                for t in ("sma50", "half_px", "street_px"):
                    row[f"be_vs_{t}_pct"] = _pct(new_avg, lv.get(t))
                row["kill"] = kill
                row["add_kill_usd"] = _r(float(add_usd) * (kill / px - 1.0), 0) if kill else None
                row["line_kill_usd"] = _r(new_shares * (kill - px) * fx, 0) if kill else None
                ladder.append(row)
    spec = {"pack": str(pack), "n_lines": len(lines), "n_ladder": len(ladder), "add_usd": recipe.get("add_usd"),
            "add_levels": recipe.get("add_levels"), "gain_pct": gains, "solve_targets": recipe.get("solve_targets"),
            "missing_ids": [w for w in wanted if not any(match_wanted_id(l["symbol"], [w]) for l in lines)]}
    return {"spec": spec, "lines": lines, "ladder": ladder}


def write_recovery_ladder(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        "lines: the Book line now from PACK/book_risk (shares, average price, close in the quote currency; dollars via the price-to-USD rate), "
        "levels and state from PACK/deterioration names (street_px left empty on non-USD lines: it is the USD listing's). be_from_close_pct = the rise from the close back to the average price; "
        "gain<g>_px = the average plus g%.",
        "add_be_at_<t>_usd: the USD add bought at solve_at_px after which a return to level <t> breaks the whole line even "
        "(0 = <t> already above the average; empty = <t> not above the buy price).",
        f"ladder: line x add level {spec['add_levels']} x add size {spec['add_usd']} USD: new_avg, avg_cut_pct, be_from_add_pct (rise from the buy "
        "price to break even), gain<g>_from_add_pct, be_vs_<level>_pct (the new average against the level), add_kill_usd (the add's own loss "
        "at the kill), line_kill_usd (the whole line's loss from the buy price to the kill).",
        "Scenarios, not advice: no ranking, no cutoff, no course. A double-down still needs a no-letter state, a band and a kill (deterioration skill).",
    ]
    return write_run_export(out_dir, tool="generic_utils.recovery_ladder",
                            tables={"lines": payload.get("lines") or [], "ladder": payload.get("ladder") or []},
                            sources={"pack": spec["pack"]}, notes=notes, extra={"spec": spec}, duckdb_name="recovery_ladder.duckdb")


def recovery_ladder_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_recovery_ladder(build_recovery_ladder(pack), pack / "recovery_ladder"))


if __name__ == "__main__":
    recovery_ladder_local_main()
