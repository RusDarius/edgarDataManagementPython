"""Per-name value / efficiency / peer + technical structure + street/TV fwd_*.

Field selection only. No eligibility cutoff and no 0–100 composite.

EV/Rev is quoted only when it is the native sales field *and* earnings /
cash / book multiples are missing. A cheap EV/Rev on a bank, insurer,
health-care name, or profitable software line is not a discount.

Street leftover (`fwd_street_*`) and pack leftover (`fwd_pack_*`) are attached
on every row. Quoted `pe_fwd` is the projection next-year PE when that
column was attached. financial_projection terminals live on ``forward_value``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import RELATIVE_SUFFIXES, attach_vs_group, pct_vs
from generic_utils.ranking import to_float

DEFAULT_RECIPE_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "generic_utils" / "value_tech.json"
)

VAL_LABELS = {
    "evrev": "EV/Rev",
    "pe": "PE ttm",
    "pe_fwd": "PE fwd",
    "peg": "PEG",
    "evebitda": "EV/EBITDA",
    "evebitda_fwd": "EV/EBITDA fwd",
    "pb": "P/B",
    "evfcf": "EV/FCF",
    "opm": "opm",
}

SETUP_KEEP = (
    "val_field",
    "val_label",
    "val",
    "val_vs_ind",
    "val_ind_med",
    "val_vs_avg",
    "val_ind_avg",
    "val_n",
    "val_family",
    "eff_field",
    "eff_label",
    "eff",
    "eff_vs_ind",
    "eff_ind_med",
    "eff_vs_avg",
    "eff_ind_avg",
    "peer",
    "tech_sma",
    "tech_sma200",
    "tech_rng",
    "tech_rsi",
    "sector",
    "vs200",
    "fwd_street_pct",
    "fwd_street_px",
    "fwd_pack_pct",
    "fwd_pack_px",
    "fwd_pe",
    "fwd_pe_source",
    "fwd_evebitda",
    "fwd_peg",
    "pe_peer",
    "evebitda_peer",
    "pb_peer",
    "evfcf_peer",
    "pe_fwd_peer",
    "evebitda_fwd_peer",
)

# TradingView multiples copied as published. The bin is on that field's own
# industry median. It is not the projection.
TV_AS_IS_PEER_FIELDS = ("pe", "evebitda", "pb", "evfcf")
# Projection derivatives. Same bins, separate columns, because the path can be off.
FORWARD_PEER_FIELDS = ("pe_fwd", "evebitda_fwd")

_POSITIVE_MULTS = frozenset(
    {"pe", "pe_fwd", "peg", "evrev", "evebitda", "evebitda_fwd", "pb", "evfcf"}
)


def load_setup_recipe(path: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.run_export import load_json_recipe

    return load_json_recipe(path or DEFAULT_RECIPE_PATH)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


_UNUSABLE_HIGH = {
    "pe": 150.0,
    "pe_fwd": 150.0,
    "peg": 15.0,
    "evrev": 80.0,
    "evebitda": 80.0,
    "evebitda_fwd": 80.0,
    "evfcf": 80.0,
    "pb": 25.0,
}


def _usable(row: Mapping[str, Any], field: str) -> float | None:
    value = to_float(row.get(field))
    if value is None:
        return None
    if field in _POSITIVE_MULTS and value <= 0:
        return None
    cap = _UNUSABLE_HIGH.get(field)
    if cap is not None and value > cap:
        return None
    return value


def resolve_family(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> str:
    """Industry / sector / listing type -> family. Not a buy/sell gate."""
    cfg = recipe or {}
    type_field = str(cfg.get("type_field") or "type")
    type_val = _norm(row.get(type_field))
    omit_types = {_norm(item) for item in (cfg.get("omit_types") or ())}
    if type_val and type_val in omit_types:
        return "omit"

    industry = str(row.get(cfg.get("group_field") or "ind") or row.get("industry") or "")
    industry_l = _norm(industry)
    for needle in cfg.get("omit_industry_substrings") or ():
        if _norm(needle) and _norm(needle) in industry_l:
            return "omit"

    by_ind = cfg.get("industry_family") or {}
    if industry in by_ind:
        return str(by_ind[industry])
    for key, family in by_ind.items():
        if _norm(key) == industry_l:
            return str(family)

    sector = str(row.get(cfg.get("sector_field") or "sector") or "")
    by_sec = cfg.get("sector_family") or {}
    if sector in by_sec:
        return str(by_sec[sector])
    for key, family in by_sec.items():
        if _norm(key) == _norm(sector):
            return str(family)

    return str(cfg.get("default_family") or "cash")


def _first_usable(row: Mapping[str, Any], fields: Sequence[str]) -> str | None:
    for field in fields:
        if _usable(row, field) is not None:
            return field
    return None


def pick_value_field(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Choose the primary multiple to quote. Empty means omit — do not substitute EV/Rev."""
    cfg = recipe or {}
    families = cfg.get("families") or {}
    family = resolve_family(row, cfg)
    if family == "omit":
        return {
            "val_field": None,
            "val_label": None,
            "val": None,
            "val_family": "omit",
        }

    chain = list(families.get(family) or ())
    # Sales: EV/Rev only when earnings/cash are missing. Profitable software
    # / retail with PE or EV/EBITDA must not be quoted on EV/Rev just
    # because that multiple is "clear" or shows a discount.
    if family == "sales":
        earnings = _first_usable(row, ["pe_fwd", "pe", "peg"])
        if earnings:
            field = earnings
        elif _usable(row, "evebitda") is not None:
            field = "evebitda"
        else:
            field = _first_usable(row, chain + ["evfcf"])
    else:
        # Book / earnings / cash chains never include EV/Rev, so there is no
        # sales fall-through here; an empty chain hit means "do not quote".
        field = _first_usable(row, chain)

    return {
        "val_field": field,
        "val_label": VAL_LABELS.get(field) if field else None,
        "val": _usable(row, field) if field else None,
        "val_family": family,
    }


def pick_efficiency_field(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = recipe or {}
    family = resolve_family(row, cfg)
    chain = list((cfg.get("efficiency") or {}).get(family) or ())
    val_field = row.get("val_field")
    fields = [item for item in chain if item != val_field]
    field = _first_usable(row, fields)
    return {
        "eff_field": field,
        "eff_label": VAL_LABELS.get(field) if field else None,
        "eff": _usable(row, field) if field else None,
    }


def pick_tech(row: Mapping[str, Any]) -> dict[str, Any]:
    """Structure labels. Bins match standing leftover/range language; not a score."""
    vs50 = to_float(row.get("vs50"))
    vs200 = to_float(row.get("vs200"))
    rng = to_float(row.get("rng"))
    rsi = to_float(row.get("rsi"))
    if vs50 is None:
        sma = None
    elif vs50 >= 0:
        sma = "ABOVE"
    else:
        sma = "BELOW"
    if vs200 is None:
        sma200 = None
    elif vs200 >= 0:
        sma200 = "ABOVE"
    else:
        sma200 = "BELOW"
    if rng is None:
        rng_label = None
    elif rng < 40:
        rng_label = "UNUSED"
    elif rng >= 85:
        rng_label = "PAID"
    else:
        rng_label = "MID"
    return {
        "tech_sma": sma,
        "tech_sma200": sma200,
        "tech_rng": rng_label,
        "tech_rsi": round(rsi, 1) if rsi is not None else None,
    }


# val_vs_ind is a percent versus the industry median, not a fraction.
# Inside this band the gap is too small for DISCOUNT or PREMIUM.
PEER_INLINE_BAND_PCT = 5.0


def _peer_label(val_vs_ind: float | None, *, has_field: bool, thin: bool) -> str:
    if not has_field:
        return "NA"
    if thin or val_vs_ind is None:
        return "THIN"
    if val_vs_ind <= -PEER_INLINE_BAND_PCT:
        return "DISCOUNT"
    if val_vs_ind >= PEER_INLINE_BAND_PCT:
        return "PREMIUM"
    return "INLINE"


def attach_setup(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
    *,
    peer_rows: Sequence[Mapping[str, Any]] | None = None,
    recipe_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Attach val_* / eff_* / peer / tech_* on every row.

    Peer medians come from `peer_rows` (US $2B tape) when given, else `rows`.
    Relatives already on the row are reused; missing vs-fields are filled.
    """
    cfg = dict(recipe or load_setup_recipe(recipe_path))
    group_field = str(cfg.get("group_field") or "ind")
    vs_fields = [str(item) for item in (cfg.get("vs_fields") or ()) if item]
    min_n = int(cfg.get("min_n") or 6)
    universe = [dict(r) for r in (peer_rows if peer_rows is not None else rows)]
    if vs_fields:
        universe = attach_vs_group(
            universe, group_field=group_field, fields=vs_fields, min_n=min_n
        )

    medians: dict[tuple[str, str], float | None] = {}
    means: dict[tuple[str, str], float | None] = {}
    group_n: dict[str, int] = {}
    for rec in universe:
        key = str(rec.get(group_field) or "")
        if key:
            group_n[key] = group_n.get(key, 0) + 1
        for field in vs_fields:
            medians[(key, field)] = rec.get(f"{field}_ind_med")
            means[(key, field)] = rec.get(f"{field}_ind_avg")

    by_symbol = {str(r.get("symbol") or ""): r for r in universe}
    out: list[dict[str, Any]] = []
    for raw in rows:
        rec = dict(raw)
        symbol = str(rec.get("symbol") or "")
        peer = by_symbol.get(symbol)
        if peer:
            for field in vs_fields:
                for suffix in RELATIVE_SUFFIXES:
                    col = f"{field}{suffix}"
                    if rec.get(col) is None:
                        rec[col] = peer.get(col)
        else:
            key = str(rec.get(group_field) or rec.get("industry") or "")
            for field in vs_fields:
                med = medians.get((key, field))
                avg = means.get((key, field))
                rec[f"{field}_ind_med"] = med
                rel_med = pct_vs(rec.get(field), med) if med is not None else None
                rec[f"{field}_vs_ind"] = round(rel_med, 1) if rel_med is not None else None
                rec[f"{field}_ind_avg"] = avg
                rel_avg = pct_vs(rec.get(field), avg) if avg is not None else None
                rec[f"{field}_vs_avg"] = round(rel_avg, 1) if rel_avg is not None else None

        picked = pick_value_field(rec, cfg)
        rec.update(picked)
        rec.update(pick_efficiency_field(rec, cfg))
        rec.update(pick_tech(rec))

        field = rec.get("val_field")
        key = str(rec.get(group_field) or rec.get("industry") or "")
        val_vs = rec.get(f"{field}_vs_ind") if field else None
        rec["val_vs_ind"] = val_vs
        rec["val_ind_med"] = rec.get(f"{field}_ind_med") if field else None
        rec["val_vs_avg"] = rec.get(f"{field}_vs_avg") if field else None
        rec["val_ind_avg"] = rec.get(f"{field}_ind_avg") if field else None
        rec["val_n"] = group_n.get(key)
        eff_field = rec.get("eff_field")
        rec["eff_vs_ind"] = rec.get(f"{eff_field}_vs_ind") if eff_field else None
        rec["eff_ind_med"] = rec.get(f"{eff_field}_ind_med") if eff_field else None
        rec["eff_vs_avg"] = rec.get(f"{eff_field}_vs_avg") if eff_field else None
        rec["eff_ind_avg"] = rec.get(f"{eff_field}_ind_avg") if eff_field else None
        thin = bool(field) and (
            rec.get("val_ind_med") is None
            or (rec.get("val_n") is not None and int(rec["val_n"] or 0) < min_n)
        )
        rec["peer"] = _peer_label(
            to_float(val_vs), has_field=bool(field), thin=thin
        )
        _stamp_valuation_peers(rec, min_n=min_n, group_n=rec.get("val_n"))
        if rec.get("val") is not None:
            rec["val"] = round(float(rec["val"]), 2)
        if rec.get("eff") is not None:
            rec["eff"] = round(float(rec["eff"]), 2)
        out.append(rec)
    from generic_utils.forward_value import attach_street_forward

    return attach_street_forward(out)


def _stamp_valuation_peers(
    rec: dict[str, Any],
    *,
    min_n: int,
    group_n: Any,
) -> None:
    """One DISCOUNT / PREMIUM / INLINE per trailing TV field and per forward field."""
    n = to_float(group_n)
    thin_n = n is not None and n < min_n
    for field in (*TV_AS_IS_PEER_FIELDS, *FORWARD_PEER_FIELDS):
        has = _usable(rec, field) is not None
        thin = thin_n or rec.get(f"{field}_ind_med") is None
        rec[f"{field}_peer"] = _peer_label(
            to_float(rec.get(f"{field}_vs_ind")),
            has_field=has,
            thin=thin,
        )


def recipe_relative_columns(recipe: Mapping[str, Any] | None = None) -> list[str]:
    """Every vs-field as percent and level, once for the median and once for the average."""
    cfg = recipe or load_setup_recipe()
    fields = [str(item) for item in (cfg.get("vs_fields") or ()) if item]
    return [f"{field}{suffix}" for field in fields for suffix in RELATIVE_SUFFIXES]


def keep_with_setup(keep: Sequence[str]) -> list[str]:
    out = list(keep)
    for extra in SETUP_KEEP:
        if extra not in out:
            out.append(extra)
    return out


def peer_discount_pct(row: Mapping[str, Any]) -> float | None:
    """Industry-relative on the *primary* multiple. Not a fallback to EV/Rev."""
    field = str(row.get("val_field") or "")
    if field:
        value = to_float(row.get("val_vs_ind"))
        if value is not None:
            return value
        value = to_float(row.get(f"{field}_vs_ind"))
        if value is not None:
            return value
    return None
