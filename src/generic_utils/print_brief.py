"""Print brief: one card per print-night reporter, a short written read built only from the pack's numbers.

Reads ``PACK/print_night`` (the reporters, their pointer counts and lists, the measured cells), the earnings-week
calendar (consensus for the quarter, value to the industry and to its own history, street target, past prints),
the outlook ``names`` (the US $2B+ industry peers: forward P/E, month, quarter), the lab's print events (the
industry's prints this season), the alpha model's drivers and the deterioration state.

Table ``cards`` holds per name the recipe's ``card_fields`` and the texts: ``headline`` (one line), ``setup_text``
(going into the print), ``value_text`` (value to peers and to its own history, cash flow, street), ``quarter_text``
(what the quarter has to show: consensus against last quarter, revisions and where they sit among past prints,
last quarter's surprise, growth), ``peers_text`` (the industry's peers and their prints this season),
``pointers_text`` (the measured pointers, the alpha deciles and drivers), ``levels_text``, ``history_text``,
``print_text`` (once printed) and ``watch_items`` (what to read on the print, `` | `` separated: the quarter's
bar, the revision cell, the measured mix and after-hours cells, the recipe's set-up and slot notes, the size).
Table ``industries`` has one row per industry with a reporter: the peer medians and the season's prints.

Every number in a text is a pack value or a measured cell; the set-up and slot notes are recipe text. No ranking,
no composite, no course: which cards a page shows and what to do are the page's and the judgment's.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT
from generic_utils.ranking import to_float

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "print_brief.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _csv(path: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    return rows_from_csv(path) if path.exists() else []


def _by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol") or ""): dict(r) for r in rows if r.get("symbol")}


def _db_rows(path: Path, sql: str) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_duckdb

    return rows_from_duckdb(path, sql) if path.exists() else []


# --- wording ------------------------------------------------------------------------------------------------


def pct(value: Any, digits: int = 1) -> str | None:
    x = _f(value)
    return None if x is None else f"{x:+.{digits}f}%"


def pp(value: Any, digits: int = 1) -> str:
    """A lift in percentage points; '-' when missing."""
    x = _f(value)
    return "-" if x is None else f"{x:+.{digits}f} pp"


def num(value: Any, digits: int = 1) -> str | None:
    x = _f(value)
    return None if x is None else f"{x:.{digits}f}"


def price(value: Any) -> str | None:
    x = _f(value)
    if x is None:
        return None
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def money(value: Any) -> str | None:
    x = _f(value)
    if x is None:
        return None
    sign = "-" if x < 0 else ""
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"{sign}${abs(x) / div:.2f}{unit}"
    return f"{sign}${abs(x):,.0f}"


def rel(value: Any, what: str) -> str | None:
    """``-18`` -> '18% under {what}', ``+12`` -> '12% over {what}'."""
    x = _f(value)
    if x is None:
        return None
    return f"{abs(x):.0f}% {'under' if x < 0 else 'over'} {what}"


def sentence(*parts: str | None, sep: str = ", ") -> str | None:
    kept = [p for p in parts if p]
    return (sep.join(kept) + ".") if kept else None


def paragraph(*sentences: str | None) -> str | None:
    kept = [s for s in sentences if s]
    return " ".join(kept) or None


# --- peers ----------------------------------------------------------------------------------------------------


def peer_summary(sym: str, ind: str | None, peers: Sequence[Mapping[str, Any]], *, listed: int) -> dict[str, Any]:
    """Medians of the industry's other names and the name's month rank among them (1 = the best month)."""
    others = [p for p in peers if p.get("ind") == ind and p.get("symbol") != sym] if ind else []
    pes = [x for x in (_f(p.get("pe_nfy")) for p in others) if x is not None and x > 0]
    m1s = [x for x in (_f(p.get("m1")) for p in others) if x is not None]
    m3s = [x for x in (_f(p.get("m3")) for p in others) if x is not None]
    largest = sorted(others, key=lambda p: -(_f(p.get("mcap_b")) or 0))[:listed]
    return {"peer_count": len(others), "peer_pe_med": round(median(pes), 1) if pes else None,
            "peer_m1_med": round(median(m1s), 1) if m1s else None, "peer_m3_med": round(median(m3s), 1) if m3s else None,
            "largest": largest}


def month_rank(value: Any, others: Sequence[float]) -> int | None:
    x = _f(value)
    return None if x is None else 1 + sum(1 for o in others if o > x)


def season_prints(events: Sequence[Mapping[str, Any]], ind: str | None, before: str, *, days: int, exclude: str) -> list[dict[str, Any]]:
    """The industry's lab prints that reacted in [before - days, before), newest first."""
    if not ind or not before:
        return []
    start = (date.fromisoformat(before[:10]) - timedelta(days=days)).isoformat()
    rows = [e for e in events if e.get("ind") == ind and e.get("symbol") != exclude and _f(e.get("react_fx")) is not None
            and start <= str(e.get("react_on") or "")[:10] < before[:10]]
    return sorted(rows, key=lambda e: str(e.get("react_on")), reverse=True)


# --- texts ----------------------------------------------------------------------------------------------------


def setup_phrase(row: Mapping[str, Any]) -> str | None:
    runup, prev = _f(row.get("runup")), _f(row.get("react_last"))
    if runup is None:
        return None
    head = f"{'Sold into' if runup < 0 else 'Ran into'} the print ({pct(runup)} in 14 days)"
    return head + (f" after {'a down' if prev < 0 else 'an up'} print ({pct(prev)})" if prev is not None else "")


def headline(row: Mapping[str, Any]) -> str | None:
    if row.get("phase") == "post":
        printed = sentence(f"Printed {row.get('surprise_mix') or ''}".strip(), f"EPS {pct(row.get('last_surprise'))}" if row.get("last_surprise") not in (None, "") else None,
                           f"revenue {pct(row.get('rev_surprise'))}" if row.get("rev_surprise") not in (None, "") else None,
                           f"reaction {pct(row.get('react_fx'))} vs the market" if row.get("react_fx") not in (None, "") else None)
        return printed
    value = None
    if _f(row.get("pe_nfy")) is not None:
        value = "forward P/E " + ", ".join(x for x in (num(row.get("pe_nfy")), rel(row.get("pe_nfy_vs_ind"), "its industry"),
                                                       rel(row.get("pe_nfy_vs_own"), "its own median")) if x)
    pointers = None
    if row.get("h28_up") is not None:
        pointers = f"28-day hold {row.get('h28_up')} up / {row.get('h28_dn')} down, 7-day {row.get('h7_up')} / {row.get('h7_dn')}"
    move = f"its prints move {_f(row.get('react_abs_mean')):.0f}% on average" if _f(row.get("react_abs_mean")) is not None else None
    return sentence(setup_phrase(row), value, pointers, move, sep="; ")


def setup_text(row: Mapping[str, Any], det: Mapping[str, Any] | None) -> str | None:
    tape = sentence("Into the print: " + ", ".join(x for x in (
        f"{pct(row.get('runup'))} over 14 days" if row.get("runup") not in (None, "") else None,
        f"{pct(row.get('m1'))} over the month" if row.get("m1") not in (None, "") else None,
        f"{pct(row.get('m3'))} over three months" if row.get("m3") not in (None, "") else None,
        f"{pct(row.get('vs50'))} vs the 50-day" if row.get("vs50") not in (None, "") else None,
        f"RSI {num(row.get('rsi'), 0)}" if row.get("rsi") not in (None, "") else None,
        f"{num(row.get('rng'), 0)}% of its 52-week range" if row.get("rng") not in (None, "") else None) if x))
    hist = None
    if row.get("n_prints") not in (None, "", 0, "0"):
        hist = sentence(f"Previous print {pct(row.get('react_last'))}" if row.get("react_last") not in (None, "") else None,
                        f"{row.get('react_up')} past prints rose" if row.get("react_up") else None,
                        f"mean move {num(row.get('react_abs_mean'))}%" if row.get("react_abs_mean") not in (None, "") else None)
    cell = None
    if row.get("setup_cell"):
        cell = sentence(f"Set-up cell '{row['setup_cell']}': held 28 days {pct(row.get('setup_mean'), 2)} on past prints "
                        f"(t {num(row.get('setup_t'), 1)}, n {num(row.get('setup_n'), 0)})",
                        f"its reaction 10th / 90th percentile {pct(row.get('setup_rx_q10'))} / {pct(row.get('setup_rx_q90'))}"
                        if row.get("setup_rx_q10") not in (None, "") else None)
    state = None
    if det:
        state = sentence(f"Drawdown cell {det.get('dd')}", f"letters beyond price {det.get('det_seen')}" if det.get("det_seen") else None,
                         det.get("trend"), det.get("street"), det.get("run_12m"))
    return paragraph(tape, hist, cell, state)


def value_text(row: Mapping[str, Any]) -> str | None:
    pe = None
    if _f(row.get("pe_nfy")) is not None:
        pe = sentence(f"Forward P/E {num(row.get('pe_nfy'))}",
                      f"{rel(row.get('pe_nfy_vs_ind'), 'the industry median')} ({num(row.get('pe_nfy_ind_med'))})" if row.get("pe_nfy_vs_ind") not in (None, "") else None,
                      f"{rel(row.get('pe_nfy_vs_own'), 'its own median')} ({num(row.get('pe_nfy_own_med'))})" if row.get("pe_nfy_vs_own") not in (None, "") else None,
                      f"EV/EBITDA {rel(row.get('evebitda_vs_ind'), 'the industry')}" if row.get("evebitda_vs_ind") not in (None, "") else None)
    cash = sentence(f"FCF margin {num(row.get('fcf_margin'))}%" if row.get("fcf_margin") not in (None, "") else None,
                    f"operating margin {num(row.get('opm'))}%" if row.get("opm") not in (None, "") else None,
                    f"net debt / EBITDA {num(row.get('nd_ebitda'))}" if row.get("nd_ebitda") not in (None, "") else None)
    street = None
    if row.get("pt") not in (None, ""):
        rng = f", range {pct(row.get('pt_low_up'), 0)} to {pct(row.get('pt_high_up'), 0)}" if row.get("pt_low_up") not in (None, "") else ""
        drift = f", moved {pct(row.get('pt_drift_1m'))} in a month" if row.get("pt_drift_1m") not in (None, "") else ""
        street = f"Street target {price(row.get('pt'))} ({pct(row.get('pt_up'))}{rng}){drift}."
    mos = sentence(f"Margin of safety to its own P/E median {pct(row.get('mos_own_med_pct'))}" if row.get("mos_own_med_pct") not in (None, "") else None,
                   f"to peers {pct(row.get('mos_peer_pct'))}" if row.get("mos_peer_pct") not in (None, "") else None)
    return paragraph(pe, cash, street, mos)


def quarter_text(row: Mapping[str, Any], bucket: str | None, cell: Mapping[str, Any] | None) -> str | None:
    cons = sentence(f"Consensus EPS {num(row.get('eps_next_q'), 2)} vs {num(row.get('eps_last_q'), 2)} last quarter ({pct(row.get('eps_q_step'))})"
                    if row.get("eps_next_q") not in (None, "") else None,
                    f"revenue {money(row.get('rev_next_q'))} vs {money(row.get('rev_last_q'))} ({pct(row.get('rev_q_step'))})"
                    if row.get("rev_next_q") not in (None, "") else None)
    rev = None
    if row.get("eps_rev_3m") not in (None, ""):
        where = (f"; {bucket} of past prints, which held 28 days {pp(cell.get('lift'), 1)} vs the rest"
                 + (" (a measured lean)" if cell.get("dir") in {"up", "down"} else " (no clear lean)")) if bucket and cell else ""
        rev = f"Next-FY EPS estimates {pct(row.get('eps_rev_1m'))} in a month, {pct(row.get('eps_rev_3m'))} in three{where}."
    last = sentence(f"Last quarter: EPS surprise {pct(row.get('last_surprise'))}" if row.get("last_surprise") not in (None, "") else None,
                    f"revenue {pct(row.get('rev_surprise'))}" if row.get("rev_surprise") not in (None, "") else None,
                    f"mix {row.get('surprise_mix')}" if row.get("surprise_mix") else None)
    growth = sentence(f"Revenue {pct(row.get('rev_yoy'))} y/y" if row.get("rev_yoy") not in (None, "") else None,
                      f"next-FY EPS growth {pct(row.get('eps_nfy_g'))}" if row.get("eps_nfy_g") not in (None, "") else None,
                      f"next-FY revenue growth {pct(row.get('rev_nfy_g'))}" if row.get("rev_nfy_g") not in (None, "") else None)
    if row.get("phase") == "post":
        last = None  # the calendar's surprise is this print's, on print_text
    return paragraph(cons, rev, last, growth)


def peers_text(row: Mapping[str, Any], summary: Mapping[str, Any], season: Sequence[Mapping[str, Any]], same_night: Sequence[str],
               *, listed: int, m1_rank: int | None) -> str | None:
    if not summary.get("peer_count") and not season:
        return "No US $2B+ peer set for this industry in the outlook (non-US listing or a thin industry)."
    head = sentence(f"{row.get('ind')}: {summary['peer_count']} US $2B+ peers",
                    f"median forward P/E {summary.get('peer_pe_med')} (this name {num(row.get('pe_nfy'))})" if summary.get("peer_pe_med") is not None else None,
                    f"median month {pct(summary.get('peer_m1_med'))} (this name {pct(row.get('m1'))}, rank {m1_rank} of {summary['peer_count'] + 1})"
                    if summary.get("peer_m1_med") is not None and m1_rank else None,
                    f"median quarter {pct(summary.get('peer_m3_med'))}" if summary.get("peer_m3_med") is not None else None)
    big = None
    if summary.get("largest"):
        big = "Largest peers: " + "; ".join(f"{p['symbol']} (P/E {num(p.get('pe_nfy')) or '-'}, 1M {pct(p.get('m1')) or '-'})"
                                            for p in summary["largest"]) + "."
    seas = None
    if season:
        xs = [float(e["react_fx"]) for e in season]
        seas = (f"Industry prints in the last 35 days: {len(xs)}, mean reaction {pct(sum(xs) / len(xs))} vs the market, "
                f"{sum(1 for x in xs if x > 0)} up; latest " + "; ".join(f"{e['symbol']} {str(e.get('react_on'))[5:10]} {pct(e.get('react_fx'))}"
                                                                         for e in season[:listed]) + ".")
    night = f"Also printing this session: {', '.join(same_night)}." if same_night else None
    return paragraph(head, big, seas, night)


def pointers_text(row: Mapping[str, Any], why: str | None) -> str | None:
    if row.get("h28_up") is None:
        return None
    lines = [f"28-day hold: {row.get('h28_up')} up / {row.get('h28_dn')} down from {row.get('h28_fam_up')} / {row.get('h28_fam_dn')} families"
             + (f" (up: {row['h28_up_list']}" if row.get("h28_up_list") else "")
             + (f"; down: {row['h28_dn_list']})" if row.get("h28_dn_list") else (")" if row.get("h28_up_list") else "")) + ".",
             f"7-day hold: {row.get('h7_up')} / {row.get('h7_dn')}" + (f" (up: {row['h7_up_list']})" if row.get("h7_up_list") else "")
             + (f" (down: {row['h7_dn_list']})" if row.get("h7_dn_list") else "") + ".",
             f"Reaction session: {row.get('rx_up')} / {row.get('rx_dn')}."]
    alpha = sentence(f"Alpha 7d decile {num(row.get('dec_7d'), 0)} (that decile returned {pct(row.get('dec_7d_fx'), 2)} out of sample, "
                     f"state {row.get('state_7d')})" if row.get("dec_7d") not in (None, "") else None,
                     f"28d decile {num(row.get('dec_28d'), 0)} ({pct(row.get('pred_28d'))} forecast)" if row.get("dec_28d") not in (None, "") else None)
    drivers = f"Model drivers (28d): {why}." if why else None
    peers = f"Industry prints this season: {pct(row.get('peer_rx'))} mean reaction (n {row.get('peer_n')})." if row.get("peer_rx") not in (None, "") else None
    return paragraph(*lines, alpha, drivers, peers)


def levels_text(row: Mapping[str, Any], starter: float) -> str | None:
    band = sentence(f"Buy-in-red band {price(row.get('bounce_low'))}-{price(row.get('bounce_high'))}, kill {price(row.get('bounce_kill'))}"
                    if row.get("bounce_low") not in (None, "") else None,
                    f"50-day band {price(row.get('entry_low'))}-{price(row.get('entry_high'))}, kill {price(row.get('kill_px'))}"
                    if row.get("entry_low") not in (None, "") else None,
                    f"first target {price(row.get('first_target'))}" if row.get("first_target") not in (None, "") else None)
    size = sentence(f"A ${starter:,.0f} starter: overnight bad case {money(row.get('tail_starter_usd'))} (set-up cell q10)"
                    if row.get("tail_starter_usd") not in (None, "") else None,
                    f"its own typical move ±{money(row.get('own_move_starter_usd'))}" if row.get("own_move_starter_usd") not in (None, "") else None)
    book = (f"Held: {money(row.get('book_usd'))} ({pct(row.get('book_vs_cost'))} vs cost)." if _f(row.get("book_usd")) else None)
    return paragraph(band, size, book)


def print_text(row: Mapping[str, Any], mix_cells: Mapping[str, Mapping[str, Any]]) -> str | None:
    if row.get("phase") != "post":
        return None
    mix = row.get("surprise_mix")
    rx = mix_cells.get((mix, "react_fx")) if mix else None
    dr = mix_cells.get((mix, "drift_fx_28d")) if mix else None
    first = sentence(f"Printed: EPS surprise {pct(row.get('last_surprise'))}", f"revenue {pct(row.get('rev_surprise'))}",
                     f"mix {mix} (revenue posted: {row.get('rev_posted')})" if mix else None,
                     f"after hours {pct(row.get('post_chg'))}" if row.get("post_chg") not in (None, "") else None,
                     f"reaction {pct(row.get('react_fx'))} vs the market" if row.get("react_fx") not in (None, "") else None)
    cells = sentence(f"{mix} prints reacted {pp(rx.get('lift'))} vs the rest (n {rx.get('n')})" if rx else None,
                     f"drifted {pp(dr.get('lift'))} over 28 days" if dr else None)
    ptr = sentence(f"Print pointers: reaction {row.get('rx_up')} up / {row.get('rx_dn')} down",
                   f"after hours to close {row.get('ac_up')} / {row.get('ac_dn')}", f"28-day drift {row.get('d28_up')} / {row.get('d28_dn')}")
    week = sentence(f"Set-up x reaction cell '{row.get('setrx_cell')}': drift {pct(row.get('setrx_mean'), 2)} (t {num(row.get('setrx_t'))})"
                    if row.get("setrx_cell") else None)
    return paragraph(first, cells, ptr, week)


def watch_items(row: Mapping[str, Any], recipe: Mapping[str, Any], mix_cells: Mapping[tuple[str, str], Mapping[str, Any]],
                ah_cell: Mapping[str, Any] | None, ah_edge: float | None, starter: float) -> list[str]:
    items: list[str] = []
    if row.get("eps_next_q") not in (None, "") or row.get("rev_next_q") not in (None, ""):
        items.append("The bar: " + ", ".join(x for x in (
            f"EPS {num(row.get('eps_next_q'), 2)} ({pct(row.get('eps_q_step'))} vs last quarter)" if row.get("eps_next_q") not in (None, "") else None,
            f"revenue {money(row.get('rev_next_q'))} ({pct(row.get('rev_q_step'))})" if row.get("rev_next_q") not in (None, "") else None) if x) + ".")
    bb, mm = mix_cells.get(("B/B", "react_fx")), mix_cells.get(("M/M", "react_fx"))
    bbd = mix_cells.get(("B/B", "drift_fx_28d"))
    if bb and mm:
        items.append(f"Read rev_posted, then the mix: B/B prints reacted {pp(bb.get('lift'))} vs the rest"
                     + (f" and drifted {pp(bbd.get('lift'))} over 28 days" if bbd else "")
                     + f"; M/M reacted {pp(mm.get('lift'))}.")
    slot = str(row.get("slot") or "").rstrip("~")
    if slot == "AMC" and ah_cell and ah_edge is not None:
        items.append(f"After hours below {ah_edge:+.1f}%: such reads reacted {pp(ah_cell.get('react_lift'))} vs the rest but "
                     f"closed {pp(ah_cell.get('lift'))} above the after-hours print (n {ah_cell.get('n')}).")
    if (recipe.get("slot_notes") or {}).get(slot):
        items.append(recipe["slot_notes"][slot])
    if row.get("setup_cell") and (recipe.get("setup_notes") or {}).get(row["setup_cell"]):
        items.append(recipe["setup_notes"][row["setup_cell"]])
    if row.get("tail_starter_usd") not in (None, ""):
        items.append(f"Size: a ${starter:,.0f} starter takes {money(row.get('tail_starter_usd'))} in the set-up cell's bad case overnight"
                     + (f"; its own prints move ±{money(row.get('own_move_starter_usd'))}" if row.get("own_move_starter_usd") not in (None, "") else "") + ".")
    if row.get("h28_dn_list"):
        items.append(f"Down pointers on the 28-day hold: {row['h28_dn_list']}.")
    return items


# --- build ------------------------------------------------------------------------------------------------------


def build_print_brief(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = ()) -> dict[str, Any]:
    """Tables ``cards`` and ``industries`` for the reporters in ``PACK/print_night``."""
    from generic_utils.symbol_identity import ticker_map

    pack = Path(pack_dir) if Path(pack_dir).is_absolute() else REPO_ROOT / pack_dir
    recipe = dict(recipe or load_recipe())
    night_db = pack / "print_night" / "print_night.duckdb"
    if not night_db.exists():
        raise FileNotFoundError(f"{night_db} missing: run tv_scan_cli.py print-night --pack {pack} first")
    names = _db_rows(night_db, "SELECT * FROM names")
    if ids:
        by_ticker = ticker_map(r["symbol"] for r in names)
        wanted = {i if ":" in i else by_ticker.get(i.upper(), i) for i in ids}
        names = [r for r in names if r["symbol"] in wanted]
    cells = _db_rows(night_db, "SELECT pointer, bucket, measure, n, lift, lift_t, dir FROM cells")
    bucket_of = {(r["symbol"], r["pointer"]): r["bucket"]
                 for r in _db_rows(night_db, "SELECT symbol, pointer, bucket FROM pointers WHERE measure = 'hold_fx_28d' AND pointer = 'eps_rev_3m'")}
    cell_of = {(c["pointer"], c["bucket"], c["measure"]): c for c in cells}
    mix_cells = {(c["bucket"], c["measure"]): c for c in cells if c["pointer"] == "surprise_mix"}
    ah_rows = sorted((c for c in cells if c["pointer"] == "ah_read" and str(c["bucket"]).startswith("Q1")), key=lambda c: c["measure"])
    ah = next((c for c in ah_rows if c["measure"] == "ah_to_close"), None)
    ah_rx = next((c for c in ah_rows if c["measure"] == "react_fx"), None)
    ah_cell = {**ah, "react_lift": (ah_rx or {}).get("lift")} if ah else None
    ah_edge = None
    if ah:
        try:
            ah_edge = float(str(ah["bucket"]).split(",")[1].strip(" )"))
        except (IndexError, ValueError):
            ah_edge = None

    calendar = _by_symbol(_csv(pack / "earnings_week" / "calendar.csv"))
    peers = _db_rows(pack / "outlook" / "outlook.duckdb", "SELECT symbol, ind, mcap_b, pe_nfy, m1, m3 FROM names")
    events = _csv(pack / "signal_lab" / "print_events.csv")
    alpha = _by_symbol(_csv(pack / "alpha_model" / "live.csv"))
    det = _by_symbol(_csv(pack / "deterioration" / "names.csv"))
    plays = _by_symbol(_csv(pack / "week_plays" / "plays.csv"))
    starter = float(recipe.get("starter_usd") or 5000)
    days, listed, plisted = int(recipe.get("peer_window_days", 35)), int(recipe.get("peers_listed", 3)), int(recipe.get("peer_prints_listed", 4))
    by_ind_night: dict[str, list[str]] = {}
    for r in names:
        by_ind_night.setdefault(str(r.get("ind") or ""), []).append(r["symbol"])

    cards: list[dict[str, Any]] = []
    for base in names:
        sym = base["symbol"]
        row = {**(calendar.get(sym) or {}), **{k: v for k, v in base.items() if v not in (None, "")}}
        for k in ("vs50", "opm", "pt", "pt_low_up", "pt_high_up", "pt_drift_1m", "pe_nfy_ind_med", "pe_nfy_own_med", "evebitda_vs_ind",
                  "eps_next_q", "eps_last_q", "rev_next_q", "rev_last_q", "eps_rev_1m", "rev_yoy", "eps_nfy_g", "rev_nfy_g", "m3", "n_prints"):
            if row.get(k) in (None, ""):
                row[k] = (calendar.get(sym) or {}).get(k)
        for k in ("setup_t", "setup_n", "setrx_cell", "setrx_mean", "setrx_t"):
            if row.get(k) in (None, ""):
                row[k] = (plays.get(sym) or {}).get(k)
        summary = peer_summary(sym, row.get("ind"), peers, listed=listed)
        others_m1 = [x for x in (_f(p.get("m1")) for p in peers if p.get("ind") == row.get("ind") and p.get("symbol") != sym) if x is not None]
        season = season_prints(events, row.get("ind"), str(row.get("react_on") or ""), days=days, exclude=sym)
        same = [s for s in by_ind_night.get(str(row.get("ind") or ""), []) if s != sym]
        bucket = bucket_of.get((sym, "eps_rev_3m"))
        rev_cell = cell_of.get(("eps_rev_3m", bucket, "hold_fx_28d")) if bucket else None
        why = (alpha.get(sym) or {}).get("why_28d") or None
        rec: dict[str, Any] = {f: row.get(f) for f in recipe.get("card_fields") or []}
        rec["det_seen"] = (det.get(sym) or {}).get("det_seen")
        rec["peer_count"], rec["peer_pe_med"], rec["peer_m1_med"] = summary["peer_count"], summary["peer_pe_med"], summary["peer_m1_med"]
        rec["m1_rank"] = month_rank(row.get("m1"), others_m1) if others_m1 else None
        rec["headline"] = headline(row)
        rec["setup_text"] = setup_text(row, det.get(sym))
        rec["value_text"] = value_text(row)
        rec["quarter_text"] = quarter_text(row, bucket, rev_cell)
        rec["peers_text"] = peers_text(row, summary, season, same, listed=plisted, m1_rank=rec["m1_rank"])
        rec["pointers_text"] = pointers_text(row, why)
        rec["levels_text"] = levels_text(row, starter)
        rec["history_text"] = (f"Past prints, newest first: {row['react_hist']}." if row.get("react_hist") else None)
        rec["print_text"] = print_text(row, mix_cells)
        rec["watch_items"] = " | ".join(watch_items(row, recipe, mix_cells, ah_cell, ah_edge, starter)) or None
        cards.append(rec)

    industries: list[dict[str, Any]] = []
    for ind, syms in sorted(by_ind_night.items()):
        summary = peer_summary("", ind, peers, listed=listed)
        session = str(next((r.get("react_on") for r in names if str(r.get("ind") or "") == ind), "") or "")
        season = season_prints(events, ind, session, days=days, exclude="")
        xs = [float(e["react_fx"]) for e in season]
        industries.append({"ind": ind, "n_tonight": len(syms), "tonight": ", ".join(syms), "peer_count": summary["peer_count"],
                           "peer_pe_med": summary["peer_pe_med"], "peer_m1_med": summary["peer_m1_med"], "peer_m3_med": summary["peer_m3_med"],
                           "season_n": len(xs), "season_rx": round(sum(xs) / len(xs), 2) if xs else None,
                           "season_up": round(sum(1 for x in xs if x > 0) / len(xs), 2) if xs else None,
                           "season_latest": "; ".join(f"{e['symbol']} {str(e.get('react_on'))[5:10]} {pct(e.get('react_fx'))}" for e in season[:plisted]) or None})
    sessions = sorted({str(r.get("react_on") or "")[:10] for r in names if r.get("react_on")})
    return {"cards": cards, "industries": industries,
            "spec": {"pack": pack.as_posix(), "sessions": sessions, "n_cards": len(cards), "n_industries": len(industries),
                     "n_post": sum(1 for c in cards if c.get("phase") == "post"), "peers": bool(peers), "events": len(events)}}


def write_print_brief(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"reaction sessions {', '.join(spec['sessions']) or '-'}; {spec['n_cards']} cards ({spec['n_post']} printed), {spec['n_industries']} industries.",
        "cards: card_fields from PACK/print_night names and the earnings-week calendar; texts built only from pack values and the print-night "
        "cells (headline, setup_text, value_text, quarter_text, peers_text, pointers_text, levels_text, history_text, print_text); watch_items "
        "' | ' separated (the bar, the mix and after-hours cells, the recipe's set-up and slot notes, the size, the down pointers).",
        "Peers: the outlook names of the same industry (US $2B+), excluding the name; m1_rank 1 = the best month among them. Season prints: "
        "the lab's prints of the industry that reacted in the prior peer_window_days.",
        "industries: one row per industry with a reporter: peer medians, the season's prints (count, mean reaction vs the market, share up, latest).",
        "No ranking, no composite, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.print_brief", tables={k: payload.get(k) or [] for k in ("cards", "industries")},
                            sources={"pack": spec["pack"]}, notes=notes, extra={"spec": spec}, duckdb_name="print_brief.duckdb")


def print_brief_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_print_brief(build_print_brief(pack), pack / "print_brief"))


if __name__ == "__main__":
    print_brief_local_main()
