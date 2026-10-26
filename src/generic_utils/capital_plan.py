"""Capital plan: recycle cash from sells into buys and show the book after.

Arithmetic only. The caller names each row's course (EXIT / TRIM / NEW /
ADD / HOLD ...) and, optionally, the dollars, fraction, and price. This
module does not decide a course, rank names, or block a size.

- EXIT sells the whole line; TRIM sells `frac` (default from the recipe).
- NEW is sized at `usd` or the recipe starter hint (about $5,000); ADD at
  `usd` or the add hint (about $1,000). Whole shares at `px`.
- `size_note` flags a NEW under the starter hint. It is advice, not a cutoff.
- Cash, NAV %, and industry NAV % are reported before and after the plan.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = REPO_ROOT / "config" / "generic_utils" / "capital_plan.json"

SELL_COURSES = frozenset({"EXIT", "TRIM", "DERISK", "DERISK_INTO_PRINT", "SELL_THE_NEWS"})
BUY_COURSES = frozenset({"NEW", "ADD", "BUY_PRE", "BUY_THE_RUMOUR"})


def load_capital_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE_PATH
    payload = json.loads(recipe_path.read_text(encoding="utf-8")) if recipe_path.exists() else {}
    payload.setdefault("starter_hint_usd", 5000)
    payload.setdefault("add_hint_usd", 1000)
    payload.setdefault("trim_frac", 0.33)
    payload.setdefault("derisk_frac", 0.4)
    payload.setdefault("group_field", "ind")
    return payload


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _key(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(":", 1)[1] if ":" in text else text


def plan_rows(
    plan: Sequence[Mapping[str, Any]],
    book: Sequence[Mapping[str, Any]],
    *,
    cash: float,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return {"plan": rows, "industry": rows, "summary": row}.

    `book` rows need symbol (or ticker), value_usd, and the group field.
    `plan` rows need symbol and course; usd, frac, px, and the group field are optional.
    """
    used = dict(recipe or load_capital_recipe())
    group_field = str(used["group_field"])
    held = {}
    for row in book:
        sym = _key(row.get("symbol") or row.get("ticker") or row.get("config_ticker"))
        if sym:
            held[sym] = {
                "value_usd": to_float(row.get("value_usd")) or 0.0,
                "group": row.get(group_field) or row.get("industry") or "",
            }
    equity = sum(h["value_usd"] for h in held.values())
    nav = equity + cash

    out: list[dict[str, Any]] = []
    cash_in = cash_out = 0.0
    group_delta: dict[str, float] = {}
    for row in plan:
        sym = _key(row.get("symbol") or row.get("ticker"))
        course = str(row.get("course") or "").strip().upper()
        holding = held.get(sym, {})
        value_now = holding.get("value_usd", 0.0)
        group = row.get(group_field) or row.get("industry") or holding.get("group") or ""
        px = to_float(row.get("px"))
        frac = to_float(row.get("frac"))
        usd = to_float(row.get("usd"))
        flow = 0.0
        shares = None
        size_note = ""
        if course in SELL_COURSES:
            if frac is None:
                frac = 1.0 if course == "EXIT" else float(
                    used["trim_frac"] if course == "TRIM" else used["derisk_frac"]
                )
            flow = value_now * frac
            cash_in += flow
            if sym not in held:
                size_note = "not_in_book"
        elif course in BUY_COURSES:
            hint = used["add_hint_usd"] if (course == "ADD" or sym in held) else used["starter_hint_usd"]
            planned = usd if usd is not None else float(hint)
            if px and px > 0:
                shares = math.floor(planned / px)
                flow = shares * px
            else:
                flow = planned
            # Whole-share rounding (one share short of the hint) is not an undersized line.
            one_share = px if (px and px > 0) else 0.0
            if course != "ADD" and sym not in held and flow + one_share < float(used["starter_hint_usd"]):
                size_note = "under_starter_hint"
            cash_out += flow
        value_after = value_now - flow if course in SELL_COURSES else value_now + (flow if course in BUY_COURSES else 0.0)
        if course in SELL_COURSES:
            group_delta[group] = group_delta.get(group, 0.0) - flow
        elif course in BUY_COURSES:
            group_delta[group] = group_delta.get(group, 0.0) + flow
        out.append({
            "symbol": row.get("symbol") or sym,
            "course": course,
            group_field: group,
            "in_book": sym in held,
            "px": px,
            "frac": _r(frac, 3),
            "shares": shares,
            "cash_in_usd": _r(flow) if course in SELL_COURSES else 0.0,
            "cash_out_usd": _r(flow) if course in BUY_COURSES else 0.0,
            "value_now_usd": _r(value_now),
            "value_after_usd": _r(value_after),
            "nav_pct_now": _r(100 * value_now / nav) if nav else None,
            "nav_pct_after": _r(100 * value_after / nav) if nav else None,
            "size_note": size_note,
            "note": row.get("note") or row.get("reason") or "",
        })

    groups_now: dict[str, float] = {}
    for h in held.values():
        groups_now[h["group"]] = groups_now.get(h["group"], 0.0) + h["value_usd"]
    industry = []
    for group in sorted(set(groups_now) | set(group_delta)):
        now = groups_now.get(group, 0.0)
        after = now + group_delta.get(group, 0.0)
        industry.append({
            group_field: group,
            "value_now_usd": _r(now),
            "value_after_usd": _r(after),
            "nav_pct_now": _r(100 * now / nav) if nav else None,
            "nav_pct_after": _r(100 * after / nav) if nav else None,
        })
    industry.sort(key=lambda r: -(r["value_after_usd"] or 0.0))

    cash_end = cash + cash_in - cash_out
    summary = {
        "nav_usd": _r(nav),
        "equity_usd": _r(equity),
        "cash_start_usd": _r(cash),
        "cash_start_pct": _r(100 * cash / nav) if nav else None,
        "cash_from_sells_usd": _r(cash_in),
        "cash_to_buys_usd": _r(cash_out),
        "cash_end_usd": _r(cash_end),
        "cash_end_pct": _r(100 * cash_end / nav) if nav else None,
        "starters_left_at_hint": int(cash_end // float(used["starter_hint_usd"])) if cash_end > 0 else 0,
        "starter_hint_usd": used["starter_hint_usd"],
        "add_hint_usd": used["add_hint_usd"],
        "n_sells": sum(1 for r in out if r["course"] in SELL_COURSES),
        "n_buys": sum(1 for r in out if r["course"] in BUY_COURSES),
    }
    return {"plan": out, "industry": industry, "summary": summary}


def write_capital_plan(
    plan: Sequence[Mapping[str, Any]],
    book: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    cash: float,
    recipe: Mapping[str, Any] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """Write capital_plan.duckdb (plan, industry, summary), CSVs, queries.txt, overview.log."""
    from generic_utils.run_export import write_run_export

    used = dict(recipe or load_capital_recipe())
    result = plan_rows(plan, book, cash=cash, recipe=used)
    notes = [
        "Courses come from the caller. This file does not choose EXIT, TRIM, NEW, or ADD.",
        "EXIT sells the whole line. TRIM sells frac (recipe trim_frac when blank). DERISK uses derisk_frac.",
        "NEW is sized at usd or starter_hint_usd. ADD at usd or add_hint_usd. Whole shares at px.",
        "size_note under_starter_hint is advice: a new line under about $5,000 is not a useful book slot. It is not a block.",
        "size_note not_in_book on a sell row: the symbol is not in the book, so it raises no cash.",
        "starters_left_at_hint is cash_end divided by starter_hint_usd. Not a budget.",
        "nav_pct_now / nav_pct_after use NAV before the plan (equity mark + cash).",
    ]
    if source_note:
        notes.insert(0, source_note)
    exported = write_run_export(
        out_dir,
        tool="generic_utils.capital_plan",
        tables={"plan": result["plan"], "industry": result["industry"], "summary": [result["summary"]]},
        sources={"recipe": str(used.get("name") or "capital_plan"), "n_plan": len(plan), "n_book": len(book)},
        notes=notes,
        duckdb_name="capital_plan.duckdb",
    )
    return {"summary": result["summary"], "export": exported}
