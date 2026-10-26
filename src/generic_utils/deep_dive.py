"""Deep-dive page for named groups: discount, buy-in-red band, kill, reclaim add, trim zone.

Builds a page template over a ``name_levels`` folder (safety_band.duckdb + gmv_names.csv):
a Plan tab (judgment callouts, rules, and the action levels of the first group), then
per group three tables (levels, margin of safety, 7-month path) and a Lenses tab. Group
labels and ids come from the caller (themes.json or --ids), so the same generator serves
any watch list. Courses join through `extra_from: courses`; text blocks come from
`pages.deepdive.blocks.*` (stats, bias, contrast, timing, rules, group_<n>, lens_text,
sources). Without a judgment it renders the data-only page.

No ranking and no choice: the sort orders are the levels' own columns.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

LEVELS = ("symbol, ind, close, bounce_low, bounce_high, bounce_anchor, bounce_kill, bounce_kill_pct, first_target, "
          "first_target_src, rr_bounce, add_trigger_low, add_trigger_high, trim_zone, trim_zone_state, street_px, street_left, dte")
ANCHORS = ("symbol, close, pe_nfy, pe_nfy_ind_med, pe_nfy_vs_ind, pe_nfy_min, pe_nfy_med, pe_nfy_max, pe_nfy_seat, "
           "px_own_low_pe, mos_own_low_pct, px_own_med_pe, mos_own_med_pct, px_peer_pe, mos_peer_pct, street_px, street_left")
PATH = ("symbol, window_high, window_high_as_of, giveback_pct, window_low, window_low_as_of, rebound_pct, close_window_pct, "
        "eps_fc_pct, pt_window_pct, derate_pct, splits, rsi, rng, vs50, dte")
LENS_COLUMNS = ["symbol", "lenses", "weak_lens", "tape_state", "r_1m", "r_3m", "trend_q", "updown_vol", "accum_3m",
                "eps_rev_3m", "eps_rev_breadth", "pt_drift_3m", "fwd_rev_g", "fwd_eps_g", "rev_yoy", "peg_fwd"]
COURSES = {"extra_columns": ["Course", "Grade", "Horizon", "Reason"], "tone_field": "Course", "extra_from": "courses"}
TONES = {"HOLD": "info", "HOLD_NO_ADD": "warning", "WAIT": "neutral", "WATCH": "neutral", "PASS": "bad", "NEW": "good", "ADD": "good"}


def qualify_groups(groups: Mapping[str, Sequence[str]], symbols: Sequence[str]) -> dict[str, list[str]]:
    """Map each group's input ids onto the EXCHANGE:TICKER ids the levels folder holds (unknown ids drop out)."""
    from generic_utils.symbol_identity import parse_symbol, ticker_map

    held = set(symbols)
    by_ticker = ticker_map(symbols)
    out: dict[str, list[str]] = {}
    for label, ids in groups.items():
        picked: list[str] = []
        for value in ids:
            ident = parse_symbol(value)
            full = ident.normalized if ident.qualified else by_ticker.get(ident.ticker)
            if full and full in held and full not in picked:
                picked.append(full)
        out[label] = picked
    return out


def _in(ids: Sequence[str]) -> str:
    return ", ".join("'" + i.replace("'", "''") + "'" for i in ids)


def build_template(levels_dir: str, groups: Mapping[str, Sequence[str]], *, name: str = "deep-dive-{DAY}",
                   title: str = "Deep dive {DAY}") -> dict[str, Any]:
    """Template dict over ``levels_dir`` (a name_levels folder) for ``groups`` (label -> EXCHANGE:TICKER ids)."""
    db = f"{levels_dir}/safety_band/safety_band.duckdb"

    def table(title_text: str, cols: str, ids: Sequence[str], sort: str, courses: bool) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "table", "title": title_text, "sort": sort,
                                 "source": {"duckdb": db, "sql": f"SELECT {cols} FROM safety_band WHERE symbol IN ({_in(ids)})"}}
        if courses:
            block.update(COURSES)
        return block

    labels = [label for label, ids in groups.items() if ids]
    first = groups[labels[0]] if labels else []
    tabs: list[dict[str, Any]] = [{"label": "Plan", "blocks": [
        {"type": "stats", "from": "pages.deepdive.blocks.stats"},
        {"type": "callout", "from": "pages.deepdive.blocks.bias"},
        {"type": "callout", "from": "pages.deepdive.blocks.contrast"},
        {"type": "callout", "from": "pages.deepdive.blocks.timing"},
        {"type": "h2", "text": "How to use the levels"},
        {"type": "list", "from": "pages.deepdive.blocks.rules"},
    ] + ([table(f"{labels[0]}: action levels", LEVELS, first, "-rr_bounce", True)] if first else [])}]
    for index, label in enumerate(labels, start=1):
        ids = groups[label]
        tabs.append({"label": label[:28], "blocks": [
            {"type": "text", "from": f"pages.deepdive.blocks.group_{index}"},
            table(f"{label}: buy-in-red band, kill, add on reclaim, trim zone", LEVELS, ids, "-rr_bounce", True),
            table(f"{label}: margin of safety on today's next-FY EPS", ANCHORS, ids, "-mos_peer_pct", False),
            table(f"{label}: 7-month path, split-adjusted (price vs estimates vs multiple)", PATH, ids, "derate_pct", False),
        ]})
    tabs.append({"label": "Lenses", "blocks": [
        {"type": "text", "from": "pages.deepdive.blocks.lens_text"},
        {"type": "table", "title": "GMV lenses and parts", "source": {"csv": f"{levels_dir}/gmv_names.csv"},
         "columns": LENS_COLUMNS, "extra_columns": ["Course"], "tone_field": "Course", "extra_from": "courses"},
    ]})
    tabs.append({"label": "Sources", "blocks": [{"type": "text", "from": "pages.deepdive.blocks.sources"}]})
    return {"name": name, "page_key": "deepdive", "title": title, "subtitle": "",
            "sources_extra": [f"name_levels folder {levels_dir} (tv_scan_cli.py name-levels)"],
            "tone_map": dict(TONES), "tabs": tabs, "ledger": {"auto": True}}
