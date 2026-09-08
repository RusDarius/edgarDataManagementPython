"""Suggested course per name: leftover flags + stance + exposed support.

This is not mix, not leftover rank, and not a 0-100 play score.
`suggested_conviction` is support for the named course (0.15-0.90).
Conflicts stay visible so an agent cannot pretend the call is certain.
"""

from __future__ import annotations

from typing import Any, Mapping

from .sleeves import (
    CROWD_INDUSTRIES,
    MTP_BOUNCE_ACTIONS,
    continuation_paid,
    continuation_unpaid,
    forming_eligible,
    profile_live,
    short_limited_upside,
    unpaid_eligible,
)

CONVICTION_NOTE = (
    "suggested_conviction is support for this course, not mix, not leftover rank, "
    "and not a probability. Conflicts must stay in the dossier."
)

AVOID_MIX = frozenset({"avoid_value_trap", "avoid_reversal_trap"})


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float = 0.15, hi: float = 0.90) -> float:
    return round(min(hi, max(lo, value)), 2)


def leftover_flags(row: Mapping[str, Any]) -> dict[str, Any]:
    left = _f(row.get("left")) or 0.0
    rsi = _f(row.get("rsi")) or 50.0
    rng = _f(row.get("rng"))
    exh = _f(row.get("exh")) or 0.0
    day = _f(row.get("day")) or 0.0
    paid = left <= 8 and rsi >= 70
    unpaid = left >= 18 and rsi <= 68
    digesting = day <= -2.0 and left >= 12
    chase = paid or (exh >= 1.0 and rsi >= 75 and left <= 10)
    unused_range = rng is not None and rng <= 25 and left >= 15
    industry = str(row.get("ind") or row.get("industry") or "")
    return {
        "left": round(left, 1),
        "paid": paid,
        "unpaid": unpaid,
        "digesting": digesting,
        "chase": chase,
        "unused_range": unused_range,
        "crowd_industry": industry in CROWD_INDUSTRIES,
        "industry": industry or None,
    }


def classify_event_play(
    row: Mapping[str, Any],
    *,
    in_book: bool = False,
) -> dict[str, Any]:
    """Map dte + leftover/tape into one catalyst play. Not a probability."""
    dte = _f(row.get("dte"))
    left = _f(row.get("left"))
    flags = leftover_flags(row)
    paid = continuation_paid(row) or flags["chase"]
    shortish = short_limited_upside(row) and (left is None or left <= 25)
    leftover_live = unpaid_eligible(row) or continuation_unpaid(row) or (
        flags["unpaid"] and profile_live(row)
    )
    if dte is None:
        return {"play": None, "window": None, "dte": None}
    if dte <= 7:
        window = "0-7d print week"
        if in_book and paid:
            play = "DERISK_INTO_PRINT"
        elif in_book and leftover_live:
            play = "HOLD_THROUGH"
        elif shortish:
            play = "SHORT_PRE"
        elif paid:
            play = "SELL_THE_NEWS"
        else:
            play = "WATCH_CATALYST"
    elif dte <= 21:
        window = "8-21d this/next week"
        if paid and in_book:
            play = "DERISK_INTO_PRINT"
        elif paid:
            play = "PASS"
        elif shortish:
            play = "SHORT_PRE"
        elif leftover_live and in_book:
            play = "BUILD_TO_SELL"
        elif leftover_live:
            play = "BUY_PRE"
        else:
            play = "WATCH_CATALYST"
    else:
        window = "22-60d late Sep / next month"
        if paid:
            play = "WATCH_SELL_NEWS"
        elif shortish:
            play = "WATCH_SHORT"
        elif leftover_live:
            play = "BUY_THE_RUMOUR"
        else:
            play = "WATCH_CATALYST"
    return {"play": play, "window": window, "dte": int(dte)}


def _sleeve_tags(row: Mapping[str, Any], *, in_book: bool) -> list[str]:
    tags: list[str] = []
    book_symbols = {str(row.get("symbol") or "")} if in_book else None
    if unpaid_eligible(row, book_symbols=book_symbols):
        tags.append("unpaid")
    if continuation_unpaid(row):
        tags.append("continuation_unpaid")
    if forming_eligible(row):
        tags.append("forming")
    if continuation_paid(row):
        tags.append("continuation_paid")
    if short_limited_upside(row) and not in_book:
        tags.append("shorts_limited_upside")
    return tags


def suggest_stance(
    row: Mapping[str, Any],
    *,
    in_book: bool = False,
    continuity: Mapping[str, Any] | None = None,
    delta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    flags = leftover_flags(row)
    sleeves = _sleeve_tags(row, in_book=in_book)
    evidence: list[str] = []
    conflicts: list[str] = []
    symbol = str(row.get("symbol") or "")
    left = _f(row.get("left"))
    rsi = _f(row.get("rsi"))
    rng = _f(row.get("rng"))
    bo = _f(row.get("bo")) or 0.0
    cont = _f(row.get("cont"))
    fwd = _f(row.get("fwd"))
    opp = _f(row.get("opp"))
    mix = str(row.get("mix") or "")
    mtp = str(row.get("mtp") or "").upper()
    dte = _f(row.get("dte"))
    d_bo = _f((delta or {}).get("bo"))
    d_cont = _f((delta or {}).get("cont"))
    d_fwd = _f((delta or {}).get("fwd"))

    evidence.append(
        f"left={left} rsi={rsi} rng={rng} bo={bo} cont={cont} fwd={fwd} "
        f"opp={opp} mix={mix or '-'} mtp={mtp or '-'} dte={dte}"
    )
    if delta:
        evidence.append(f"progression Δbo={d_bo} Δcont={d_cont} Δfwd={d_fwd}")
    if flags["unpaid"]:
        evidence.append("leftover unpaid (left>=18, RSI<=68)")
    if flags["paid"] or flags["chase"]:
        evidence.append("paid/chase tape (leftover gone or RSI-stretch + exhaustion)")
    if flags["digesting"]:
        evidence.append("digesting down-day with leftover still present")
    if flags["unused_range"]:
        evidence.append("unused 52w range with leftover still present")
    if flags["crowd_industry"]:
        conflicts.append(
            f"{flags['industry']} is a paid/crowd cluster - leftover-first dump risk"
        )

    stance = "WAIT"
    support = 0.40
    course = "Stand aside until leftover, weeks profile, or print timing improves."
    invalidation = "Lost SMA50, or leftover gone (rng>=85 and RSI>=70)."
    next_check = "Revisit if Δbo turns up with leftover still unpaid."

    if in_book:
        published = str((continuity or {}).get("action") or "HOLD").upper()
        prior = (continuity or {}).get("prior_action")
        stance = published if published else "HOLD"
        course = f"Book {stance}." + (f" Continuity from prior {prior}." if prior else "")
        support = 0.50
        if (continuity or {}).get("polar_blocked"):
            conflicts.append(
                "Polar ADD/NEW -> EXIT blocked; mix trap / naive EXIT is not a thesis-kill"
            )
            support = 0.58
        reason = (continuity or {}).get("reason")
        if (continuity or {}).get("thesis_kill"):
            evidence.append(f"thesis-kill: {reason}")
            support = 0.72
        if stance == "ADD":
            support = 0.58
            if flags["unpaid"] and (d_bo is None or d_bo >= 0):
                support += 0.12
                evidence.append("Book ADD with unpaid leftover and non-falling breakout")
            if flags["chase"] or continuation_paid(row):
                stance = "HOLD_NO_ADD"
                conflicts.append("Chase/paid tape; do not ADD into RSI-70 leftover-gone")
                support = 0.52
                course = "Book HOLD_NO_ADD. Paid extension; do not add."
            elif mtp in MTP_BOUNCE_ACTIONS and not flags["unpaid"]:
                conflicts.append("MTP ENTER_SMALL is bounce climate, not ADD permission")
                support -= 0.10
                stance = "HOLD_NO_ADD"
                course = "Book HOLD_NO_ADD until leftover is unpaid and profile is live."
            else:
                course = "Book ADD leftover. Starter size, not a chase."
                invalidation = str((continuity or {}).get("invalidation") or invalidation)
        elif stance == "EXIT":
            support = 0.62 if (continuity or {}).get("thesis_kill") else 0.48
            if left is not None and left > 25:
                conflicts.append(
                    f"leftover {left}% still large - EXIT is a capital action, "
                    "not permission to overlay a short"
                )
                support = min(support, 0.55)
            course = (
                "Exit / do not re-enter until tape repairs "
                "(weeks RAS >= 0.25 and SMA50 held)."
            )
        elif stance in {"TRIM", "DERISK_INTO_PRINT", "DERISK"}:
            support = 0.58
            if dte is not None and dte <= 5:
                evidence.append(f"print in {int(dte)}d - derisk path")
            course = f"Book {stance}. Reduce into strength or into the print; do not add."
        elif stance in {"HOLD_NO_ADD", "HOLD", "WAIT", "WATCH"}:
            support = 0.52
            if flags["unpaid"]:
                next_check = "ADD only if weeks bo rises and RSI stays ≤68."
            if flags["chase"]:
                next_check = "Trim if leftover stays gone and RSI >=70 into the print."
        if dte is not None and dte <= 2 and (flags["paid"] or (rsi or 0) >= 68):
            if stance in {"ADD", "HOLD", "NEW"}:
                stance = "DERISK_INTO_PRINT"
                conflicts.append("Print in ≤2d on paid/extended tape")
                support = 0.60
                course = "Derisk into the print. Do not add."
    else:
        bounce_mtp = mtp in MTP_BOUNCE_ACTIONS
        rising = d_bo is not None and d_bo >= 0.10
        live = profile_live(row)
        mix_avoid = mix in AVOID_MIX
        if continuation_paid(row) or flags["chase"]:
            stance = "PASS"
            support = 0.64
            course = "Paid continuation. Watch, do not chase."
            next_check = "Only revisit on a digest that rebuilds leftover (RSI back ≤68)."
        elif short_limited_upside(row) and (left is None or left <= 25):
            if bounce_mtp:
                stance = "STAND_ASIDE"
                conflicts.append(
                    "MTP bounce climate vs short sleeve - do not overlay short into ENTER_SMALL"
                )
                support = 0.46
                course = "Failed structure, but bounce timing. Short wait, not short now."
            elif left is not None and left > 18:
                stance = "STAND_ASIDE"
                conflicts.append(
                    f"leftover {left}% is too large to short; squeeze risk"
                )
                support = 0.50
                course = "Deterioration without limited leftover. Do not short."
            else:
                stance = "SHORT_WAIT"
                support = 0.54
                frag = _f(row.get("frag")) or 0.0
                if frag >= 0.8:
                    support += 0.08
                    evidence.append(f"fragility {frag}")
                course = (
                    "Limited leftover + deterioration. Wait for bounce failure, then short. "
                    "Not a leftover dump."
                )
                next_check = "Abort the short if leftover rebuilds above 25% or MTP stays ENTER_SMALL."
        elif unpaid_eligible(row) and not flags["chase"]:
            if mix_avoid and not live:
                stance = "WAIT"
                support = 0.42
                conflicts.append("mix avoid_* with no live weeks profile - not a NEW")
                course = "Unpaid leftover on a trap mix. Needs profile confirmation."
            elif bounce_mtp and not rising and bo < 0.7:
                stance = "WAIT"
                support = 0.48
                conflicts.append("MTP ENTER_SMALL is bounce climate, not a buy list")
                course = (
                    "Unpaid leftover exists; wait for weeks-profile confirmation "
                    "rather than buying the bounce list."
                )
            elif live and (rising or bo >= 0.7):
                stance = "NEW"
                support = 0.55
                if rising:
                    support += 0.10
                    evidence.append("breakout score rising vs prior run")
                if opp is not None and opp <= 15:
                    support += 0.08
                    evidence.append(f"edge opportunity rank {int(opp)}")
                if flags["crowd_industry"]:
                    support -= 0.16
                if mix_avoid:
                    conflicts.append("mix avoid_* on a leftover NEW - size as a probe, not a core")
                    support -= 0.08
                if support >= 0.52:
                    course = (
                        "Starter NEW leftover sleeve. Small size. "
                        "Do not treat mix or leftover rank as conviction."
                    )
                    invalidation = "Leftover gone (rng>=85 and RSI>=70) or lost SMA50."
                    next_check = "Promote to ADD only after a second up-print in weeks bo."
                else:
                    stance = "WAIT"
                    course = "Unpaid leftover with crowding/timing conflict. Watch, do not force NEW."
            elif forming_eligible(row) or flags["unused_range"]:
                stance = "WAIT"
                support = 0.47
                course = (
                    "Forming / unused range. Do not pay up. "
                    "Revisit if bo rises with leftover still unpaid."
                )
            else:
                stance = "WAIT"
                support = 0.43
                course = "Unpaid leftover without a live weeks profile. Confirmation required."
        elif forming_eligible(row) or flags["unused_range"]:
            stance = "WAIT"
            support = 0.44
            course = "Forming setup. Confirmation required before NEW."
        else:
            stance = "PASS"
            support = 0.40
            course = "No unpaid leftover, no forming, no limited-upside short. Skip."

    if mtp in MTP_BOUNCE_ACTIONS and stance in {"NEW", "ADD", "SHORT_WAIT"}:
        conflicts.append("MTP ENTER_SMALL/PROBE is timing climate, not a buy/short list")
        if stance == "NEW" and bo < 1.0:
            stance = "WAIT"
            support = min(support, 0.48)
            course = (
                "Park auto NEW: MTP bounce climate. Stays on unpaid radar; "
                "not the 0–2 NEW budget."
            )

    return {
        "symbol": symbol,
        "in_book": in_book,
        "stance": stance,
        "suggested_conviction": _clamp(support),
        "conviction_note": CONVICTION_NOTE,
        "course": course,
        "evidence": evidence,
        "conflicts": conflicts,
        "flags": flags,
        "sleeves": sleeves,
        "invalidation": invalidation,
        "next_check": next_check,
        "raw": {
            "left": left,
            "rsi": rsi,
            "rng": rng,
            "bo": bo,
            "cont": cont,
            "fwd": fwd,
            "opp": opp,
            "mix": mix or None,
            "mtp": mtp or None,
            "dte": dte,
            "delta_bo": d_bo,
        },
    }


_BUCKET_MAP = {
    "ADD": "add",
    "NEW": "new",
    "HOLD_NO_ADD": "hold_no_add",
    "HOLD": "hold",
    "TRIM": "trim_exit",
    "EXIT": "trim_exit",
    "DERISK_INTO_PRINT": "trim_exit",
    "DERISK": "trim_exit",
    "WAIT": "wait",
    "WATCH": "wait",
    "PASS": "pass",
    "SHORT_WAIT": "short_wait",
    "STAND_ASIDE": "stand_aside",
}


def rank_suggested_courses(
    stances: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 12,
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "add": [],
        "new": [],
        "hold_no_add": [],
        "hold": [],
        "trim_exit": [],
        "wait": [],
        "pass": [],
        "short_wait": [],
        "stand_aside": [],
    }
    for row in stances.values():
        if not isinstance(row, Mapping):
            continue
        key = _BUCKET_MAP.get(str(row.get("stance") or ""), "wait")
        if key == "hold_no_add" and not row.get("in_book"):
            key = "wait"
        buckets[key].append(dict(row))
    ranked: dict[str, list[dict[str, Any]]] = {}
    for key, rows in buckets.items():
        rows.sort(key=lambda r: float(r.get("suggested_conviction") or 0), reverse=True)
        clipped = []
        for index, item in enumerate(rows[:limit], start=1):
            item["rank_in_bucket"] = index
            clipped.append(item)
        ranked[key] = clipped
    return ranked


def summarize_operator_course(
    stances: Mapping[str, Mapping[str, Any]],
    *,
    regime: Mapping[str, Any] | None = None,
    mtp: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ranked = rank_suggested_courses(stances)
    us = (regime or {}).get("us_2b") or {}
    bounce_n = int((mtp or {}).get("enter_bounce_n") or 0)
    pct_up = _f(us.get("pct_up")) or 0.0
    bounce_day = bounce_n >= 8 and pct_up >= 55
    do: list[str] = []
    do_not: list[str] = []
    for row in ranked["add"][:2]:
        do.append(
            f"ADD {row.get('symbol')} (support {row.get('suggested_conviction')}: {row.get('course')})"
        )
    for row in ranked["new"][:2]:
        do.append(
            f"NEW {row.get('symbol')} (support {row.get('suggested_conviction')}: {row.get('course')})"
        )
    for row in ranked["trim_exit"][:2]:
        do.append(
            f"{row.get('stance')} {row.get('symbol')} (support {row.get('suggested_conviction')})"
        )
    if not bounce_day:
        for row in ranked["short_wait"][:1]:
            do.append(
                f"SHORT_WAIT {row.get('symbol')} (support {row.get('suggested_conviction')}: {row.get('course')})"
            )
    else:
        for row in ranked["short_wait"][:2]:
            do_not.append(
                f"Do not overlay short {row.get('symbol')} on bounce day ({row.get('course')})"
            )
    for row in ranked["pass"][:3]:
        do_not.append(f"Do not chase {row.get('symbol')} (paid/continuation)")
    for row in ranked["stand_aside"][:2]:
        do_not.append(f"Do not short {row.get('symbol')} ({row.get('course')})")
    for row in ranked["new"][:8]:
        conflicts = row.get("conflicts") or []
        if any("crowd" in str(c).lower() for c in conflicts):
            do_not.append(f"Do not leftover-dump {row.get('symbol')} ({row.get('flags', {}).get('industry')})")

    if bounce_day:
        bias = "bounce day - leftover ADD/NEW only; no new short overlay"
        do_not.append("Do not overlay new shorts into ENTER_SMALL bounce climate")
    elif pct_up < 40:
        bias = "soft tape - HOLD_NO_ADD default; shorts only if leftover is limited"
    else:
        bias = "mixed - unpaid leftover over chase; shorts are a high-bar overlay"

    if not do:
        do.append("No ADD/NEW clears the leftover + profile + continuity bar. Hold cash / Book.")

    return {
        "bias": bias,
        "do": do[:8],
        "do_not": do_not[:8],
        "note": CONVICTION_NOTE,
        "regime_us_2b": {
            "n": us.get("n"),
            "day": us.get("day"),
            "pct_up": us.get("pct_up"),
            "d5": us.get("d5"),
            "rsi": us.get("rsi"),
        },
        "mtp_enter_bounce_n": bounce_n,
    }
