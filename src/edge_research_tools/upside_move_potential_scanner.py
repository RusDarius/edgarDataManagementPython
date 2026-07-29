"""Upside *move-potential* ranking (Magnitude x Tilt x Catalyst).

Sibling to ``upside_opportunity_scanner``: writes into the same
``upside_opportunity_scan/`` folder but a **separate** DuckDB / CSV set so the
existing opportunity score/rank stay untouched.

This flow deliberately ignores hist-validation, forward-valuation, and safety
lenses. It ranks as many unified-highlight symbols as possible on:

- Magnitude: how far price can travel (realized-vol / ATRP / RelVol / ADRP)
- UpsideTilt: how skewed the path looks to the upside (Recommend, Perf, RSI, MAs)
- Catalyst: earnings proximity as a *move-size* amplifier (not a risk penalty)
- ExpectedMoveProxy: ATRP-based %-move estimate for sizing

Optional join of opportunity rank/score columns is for aggregate use only.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from edge_research_tools.upside_opportunity_scanner import (
    load_supplementary_technical_fields,
)

DEFAULT_TOP_COUNT = 30
DEFAULT_MOVE_HORIZON_DAYS = 5

MOVE_TIER_PRIORITY: dict[str, int] = {
    "HIGH_MOVE_UPSIDE": 0,
    "MODERATE_MOVE_UPSIDE": 1,
    "CATALYST_MOVE_WATCH": 2,
    "LOW_MOVE_EDGE": 3,
}


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _read_csv_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.is_dir():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _index_rows_by_symbol(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol and symbol not in indexed:
            indexed[symbol] = row
    return indexed


def _percentile_ranks(values: Sequence[float | None]) -> list[float]:
    """Average-rank percentiles in [0, 1]; missing values -> 0.5."""
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    out = [0.5] * len(values)
    if not indexed:
        return out
    if len(indexed) == 1:
        out[indexed[0][0]] = 0.5
        return out
    indexed.sort(key=lambda item: item[1])
    n = len(indexed)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1)
        for k in range(i, j + 1):
            out[indexed[k][0]] = float(pct)
        i = j + 1
    return out


def compute_magnitude_component(row: Mapping[str, Any]) -> float:
    """Realized move-size head (0-1). No hist/valuation/safety."""
    adrp_pct = _clamp(_safe_float(row.get("adrp_pct_today")))
    relvol_pct = _clamp(_safe_float(row.get("relvol_pct_today")))
    atrp = _safe_float_or_none(row.get("atrp"))
    atrp_norm = _clamp((atrp or 0.0) / 6.0) if atrp is not None else 0.45
    vol_w = _safe_float_or_none(row.get("volatility_w"))
    vol_w_norm = _clamp((vol_w or 0.0) / 10.0) if vol_w is not None else 0.45
    vol_d = _safe_float_or_none(row.get("volatility_d"))
    vol_d_norm = _clamp((vol_d or 0.0) / 5.0) if vol_d is not None else 0.45
    beta = _safe_float_or_none(row.get("beta_1_year"))
    beta_norm = (
        _clamp(abs(beta) / 2.0) if beta is not None else 0.45
    )
    return round(
        _clamp(
            (0.25 * adrp_pct)
            + (0.20 * atrp_norm)
            + (0.20 * vol_w_norm)
            + (0.10 * vol_d_norm)
            + (0.15 * relvol_pct)
            + (0.10 * beta_norm)
        ),
        4,
    )


def compute_upside_tilt_component(row: Mapping[str, Any]) -> float:
    """Directional upside tilt (0-1). No hist/valuation/safety."""
    recommend_all = _safe_float_or_none(row.get("recommend_all_raw"))
    recommend_norm = (
        _clamp((recommend_all + 1.0) / 2.0) if recommend_all is not None else 0.5
    )
    mom_core_pct = _clamp(_safe_float(row.get("mom_core_pct_today"), 0.5))

    def _perf_norm(key: str, scale: float) -> float:
        value = _safe_float_or_none(row.get(key))
        if value is None:
            return 0.5
        return _clamp((_clamp(value / scale, -1.0, 1.0) + 1.0) / 2.0)

    perf_5d_norm = _perf_norm("perf_5d", 12.0)
    perf_1m_norm = _perf_norm("perf_1m", 25.0)
    perf_3m_norm = _perf_norm("perf_3m", 40.0)

    rsi = _safe_float_or_none(row.get("rsi"))
    rsi_norm = _clamp(rsi / 100.0) if rsi is not None else 0.5

    sma20 = _safe_float_or_none(row.get("sma20"))
    sma50 = _safe_float_or_none(row.get("sma50"))
    close = _safe_float_or_none(row.get("close"))
    if sma20 is not None and sma50 is not None:
        ma_stack = 1.0 if sma20 >= sma50 else 0.0
    else:
        ma_stack = 0.5
    if close is not None and sma20 is not None and sma20 != 0:
        above_sma20 = 1.0 if close >= sma20 else 0.0
    else:
        above_sma20 = 0.5
    trend_norm = 0.5 * ma_stack + 0.5 * above_sma20

    adx = _safe_float_or_none(row.get("adx"))
    adx_norm = _clamp((adx or 0.0) / 40.0) if adx is not None else 0.5
    # ADX is strength, not direction — multiply mild tilt toward recommend side.
    adx_directional = _clamp(0.5 + (recommend_norm - 0.5) * adx_norm)

    return round(
        _clamp(
            (0.22 * recommend_norm)
            + (0.18 * mom_core_pct)
            + (0.14 * perf_5d_norm)
            + (0.16 * perf_1m_norm)
            + (0.08 * perf_3m_norm)
            + (0.10 * rsi_norm)
            + (0.07 * trend_norm)
            + (0.05 * adx_directional)
        ),
        4,
    )


def compute_catalyst_move_score(
    earnings_days_until: int | None,
) -> float:
    """Earnings proximity as move-fuel (0-1). Opposite polarity of opportunity binary-risk."""
    if earnings_days_until is None:
        return 0.15
    if earnings_days_until < 0:
        return 0.20
    if earnings_days_until <= 10:
        return 1.0
    if earnings_days_until <= 21:
        return 0.65
    if earnings_days_until <= 45:
        return 0.35
    return 0.15


def compute_expected_move_proxy_pct(
    row: Mapping[str, Any],
    *,
    horizon_days: int = DEFAULT_MOVE_HORIZON_DAYS,
) -> float | None:
    """Approximate %-move over horizon from ATRP / daily vol (not options IV)."""
    horizon = max(1, int(horizon_days))
    scale = horizon**0.5
    atrp = _safe_float_or_none(row.get("atrp"))
    if atrp is not None and atrp > 0:
        return round(float(atrp) * scale, 4)
    vol_d = _safe_float_or_none(row.get("volatility_d"))
    if vol_d is not None and vol_d > 0:
        return round(float(vol_d) * scale, 4)
    return None


def classify_move_tier(
    *,
    move_upside_score: float,
    catalyst_score: float,
    magnitude_score: float,
) -> str:
    if move_upside_score >= 0.70 and magnitude_score >= 0.45:
        return "HIGH_MOVE_UPSIDE"
    if move_upside_score >= 0.55:
        return "MODERATE_MOVE_UPSIDE"
    if catalyst_score >= 0.65 and magnitude_score >= 0.40:
        return "CATALYST_MOVE_WATCH"
    return "LOW_MOVE_EDGE"


def compute_move_potential_fields(
    row: Mapping[str, Any],
    *,
    earnings_days_until: int | None = None,
    horizon_days: int = DEFAULT_MOVE_HORIZON_DAYS,
) -> dict[str, Any]:
    magnitude = compute_magnitude_component(row)
    tilt = compute_upside_tilt_component(row)
    catalyst = compute_catalyst_move_score(earnings_days_until)
    expected_move = compute_expected_move_proxy_pct(row, horizon_days=horizon_days)
    # Mild catalyst boost — preserves ranking when earnings date missing.
    blended = _clamp((0.55 * tilt) + (0.45 * magnitude))
    move_score = round(_clamp(blended * (0.85 + 0.15 * catalyst)), 4)
    return {
        "move_magnitude_score": magnitude,
        "move_upside_tilt_score": tilt,
        "move_catalyst_score": catalyst,
        "move_upside_score_raw": move_score,
        "expected_move_proxy_pct": (
            expected_move if expected_move is not None else ""
        ),
        "expected_move_horizon_days": int(horizon_days),
        "earnings_days_until": (
            earnings_days_until if earnings_days_until is not None else ""
        ),
    }


def finalize_move_potential_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Cross-sectionally re-rank Magnitude/Tilt then emit final score + rank."""
    working = [dict(row) for row in rows]
    mag_pcts = _percentile_ranks(
        [_safe_float_or_none(row.get("move_magnitude_score")) for row in working]
    )
    tilt_pcts = _percentile_ranks(
        [_safe_float_or_none(row.get("move_upside_tilt_score")) for row in working]
    )
    for row, mag_pct, tilt_pct in zip(working, mag_pcts, tilt_pcts, strict=True):
        catalyst = _clamp(_safe_float(row.get("move_catalyst_score"), 0.15))
        blended = _clamp((0.55 * tilt_pct) + (0.45 * mag_pct))
        score = round(_clamp(blended * (0.85 + 0.15 * catalyst)), 4)
        row["move_magnitude_universe_pct"] = round(mag_pct, 4)
        row["move_upside_tilt_universe_pct"] = round(tilt_pct, 4)
        row["move_upside_score"] = score
        row["move_tier"] = classify_move_tier(
            move_upside_score=score,
            catalyst_score=catalyst,
            magnitude_score=_clamp(_safe_float(row.get("move_magnitude_score"))),
        )

    working.sort(
        key=lambda row: (
            -_safe_float(row.get("move_upside_score")),
            -_safe_float(row.get("move_upside_tilt_universe_pct")),
            -_safe_float(row.get("move_magnitude_universe_pct")),
            str(row.get("symbol") or ""),
        )
    )
    for position, row in enumerate(working, start=1):
        row["move_upside_rank"] = position
    return working


def _opportunity_aggregate_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """Carry opportunity rank fields for aggregate joins without feeding the score."""
    keys = (
        "upside_opportunity_rank",
        "upside_opportunity_score",
        "opportunity_tier",
        "directional_lean",
        "opp_momentum_component",
        "opp_binary_risk_score",
        "opp_downside_risk_score",
    )
    return {key: row.get(key, "") for key in keys}


def _write_database(
    database_path: Path,
    *,
    candidates_csv_path: Path,
    manifest_rows: Sequence[tuple[str, str]],
) -> None:
    duckdb = _import_duckdb()
    database_path.unlink(missing_ok=True)
    conn = duckdb.connect(database_path.as_posix())
    try:
        if candidates_csv_path.exists() and candidates_csv_path.stat().st_size > 0:
            conn.execute(
                f"""
                CREATE TABLE upside_move_potential_candidates AS
                SELECT * FROM read_csv_auto({_q(candidates_csv_path.as_posix())}, HEADER=TRUE)
                """
            )
        else:
            conn.execute(
                "CREATE TABLE upside_move_potential_candidates(symbol VARCHAR)"
            )
        conn.execute(
            "CREATE TABLE upside_move_potential_run_manifest(key VARCHAR, value VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO upside_move_potential_run_manifest VALUES (?, ?)",
            list(manifest_rows),
        )
    finally:
        conn.close()


def _write_report(
    path: Path,
    *,
    candidate_count: int,
    tier_counts: Mapping[str, int],
    candidates_csv: Path,
    top_csv: Path,
    database_path: Path,
    top_count: int,
    horizon_days: int,
) -> None:
    lines = [
        "# Upside Move-Potential Scanner",
        "",
        "Parallel ranking beside `upside_opportunity_scan` (same folder, separate "
        "DuckDB). Scores Magnitude x UpsideTilt x Catalyst move-fuel only — does "
        "**not** use hist-validation, forward-valuation, or safety lenses, and does "
        "**not** rewrite opportunity ranks.",
        "",
        "## Fields",
        "",
        "- `move_magnitude_score` / `move_magnitude_universe_pct`",
        "- `move_upside_tilt_score` / `move_upside_tilt_universe_pct`",
        "- `move_catalyst_score` (earnings proximity as move amplifier)",
        "- `expected_move_proxy_pct` (ATRP or Volatility.D × √horizon)",
        f"- `expected_move_horizon_days` (default {horizon_days})",
        "- `move_upside_score` / `move_upside_rank` / `move_tier`",
        "- Optional aggregate carry: `upside_opportunity_rank`, "
        "`upside_opportunity_score`, `opportunity_tier`, `directional_lean`",
        "",
        f"- candidate rows: {candidate_count}",
        "",
        "## Move Tier Counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(tier_counts.items()))
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- candidates CSV: `{candidates_csv.as_posix()}`",
            f"- top CSV: `{top_csv.as_posix()}` (top {top_count})",
            f"- query database: `{database_path.as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_upside_move_potential_scan(
    *,
    output_dir: str | Path,
    unified_csv_path: str | Path,
    source_database_path: str | Path,
    earnings_priority_csv_path: str | Path | None = None,
    opportunity_candidates_csv_path: str | Path | None = None,
    top_count: int = DEFAULT_TOP_COUNT,
    horizon_days: int = DEFAULT_MOVE_HORIZON_DAYS,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    unified_rows = _read_csv_rows(unified_csv_path)
    symbols = [
        str(row.get("symbol") or "") for row in unified_rows if row.get("symbol")
    ]
    supplementary_by_symbol = load_supplementary_technical_fields(
        source_database_path=source_database_path,
        symbols=symbols,
        duckdb_threads=duckdb_threads,
    )
    earnings_by_symbol = _index_rows_by_symbol(
        _read_csv_rows(earnings_priority_csv_path)
    )
    opportunity_by_symbol = _index_rows_by_symbol(
        _read_csv_rows(opportunity_candidates_csv_path)
    )

    scored_rows: list[dict[str, Any]] = []
    for unified_row in unified_rows:
        symbol = str(unified_row.get("symbol") or "")
        if not symbol:
            continue
        merged: dict[str, Any] = {"symbol": symbol}
        # Keep a thin identity / context set for aggregate joins.
        for key in (
            "sector",
            "industry",
            "adrp_pct_today",
            "relvol_pct_today",
            "mom_core_pct_today",
            "vol_core_pct_today",
        ):
            if key in unified_row:
                merged[key] = unified_row.get(key)

        supplementary_row = supplementary_by_symbol.get(symbol)
        if supplementary_row:
            for key, value in supplementary_row.items():
                if key != "symbol":
                    merged[key] = value

        earnings_row = earnings_by_symbol.get(symbol)
        earnings_days_until = (
            _safe_int_or_none(earnings_row.get("earnings_days_until"))
            if earnings_row
            else None
        )
        # Fallback: earnings days already on unified/opportunity row.
        if earnings_days_until is None:
            earnings_days_until = _safe_int_or_none(
                unified_row.get("earnings_days_until")
            )

        merged.update(
            compute_move_potential_fields(
                merged,
                earnings_days_until=earnings_days_until,
                horizon_days=horizon_days,
            )
        )

        opportunity_row = opportunity_by_symbol.get(symbol) or unified_row
        merged.update(_opportunity_aggregate_fields(opportunity_row))
        scored_rows.append(merged)

    ordered_rows = finalize_move_potential_rows(scored_rows)

    candidates_csv = output_path / "upside_move_potential_candidates.csv"
    top_csv = output_path / f"upside_move_potential_top{int(max(1, top_count))}.csv"
    database_path = output_path / "upside_move_potential_scan.duckdb"
    report_md = output_path / "upside_move_potential_scan_report.md"
    manifest_path = output_path / "upside_move_potential_scan_manifest.json"

    _write_csv_rows(candidates_csv, ordered_rows)
    top_rows = [
        row
        for row in ordered_rows
        if row.get("move_tier") in ("HIGH_MOVE_UPSIDE", "MODERATE_MOVE_UPSIDE")
    ][: int(max(1, top_count))]
    if len(top_rows) < int(max(1, top_count)):
        # Fill from overall rank so thin universes still get a top file.
        seen = {str(row.get("symbol")) for row in top_rows}
        for row in ordered_rows:
            if str(row.get("symbol")) in seen:
                continue
            top_rows.append(row)
            if len(top_rows) >= int(max(1, top_count)):
                break
    _write_csv_rows(top_csv, top_rows)

    tier_counts: dict[str, int] = {}
    for row in ordered_rows:
        tier = str(row.get("move_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    _write_database(
        database_path,
        candidates_csv_path=candidates_csv,
        manifest_rows=[
            ("candidate_count", str(len(ordered_rows))),
            ("horizon_days", str(int(horizon_days))),
            ("unified_csv_path", Path(unified_csv_path).as_posix()),
            ("source_database_path", Path(source_database_path).as_posix()),
            (
                "earnings_priority_csv_path",
                (
                    Path(earnings_priority_csv_path).as_posix()
                    if earnings_priority_csv_path
                    else ""
                ),
            ),
            (
                "opportunity_candidates_csv_path",
                (
                    Path(opportunity_candidates_csv_path).as_posix()
                    if opportunity_candidates_csv_path
                    else ""
                ),
            ),
            *[(f"tier_count:{key}", str(value)) for key, value in tier_counts.items()],
        ],
    )
    _write_report(
        report_md,
        candidate_count=len(ordered_rows),
        tier_counts=tier_counts,
        candidates_csv=candidates_csv,
        top_csv=top_csv,
        database_path=database_path,
        top_count=int(max(1, top_count)),
        horizon_days=int(horizon_days),
    )

    manifest = {
        "command": "upside-move-potential-scan",
        "output_dir": output_path.as_posix(),
        "candidates_csv": candidates_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
        "row_count": len(ordered_rows),
        "top_count": int(max(1, top_count)),
        "horizon_days": int(horizon_days),
        "tier_counts": tier_counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_path,
        "candidates_csv": candidates_csv,
        "top_csv": top_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(ordered_rows),
        "tier_counts": tier_counts,
    }
