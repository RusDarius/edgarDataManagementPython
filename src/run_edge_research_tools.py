from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from constants.trading_view_constants import (
    PREFERRED_MARKETS as TRADING_VIEW_PREFERRED_MARKETS,
)
from edge_research_tools.forward_upside_valuation import (
    FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS,
    FORWARD_UPSIDE_VALUATION_SAFETY_TRAILING_COLUMNS,
    MAX_ACTIVE_VALUATION_LENSES,
    MIN_USABLE_VALUATION_LENSES,
    build_forward_upside_valuation_rows,
    extract_forward_upside_valuation_overlay_fields,
    load_latest_source_rows_for_symbols,
    project_forward_upside_valuation_row,
    sort_rows_by_forward_upside_valuation,
)
from edge_research_tools.unified_edge_highlights import (
    build_unified_edge_highlights_store,
)
from edge_research_tools.upside_prediction import (
    UPSIDE_PREDICTION_SAFETY_TRAILING_COLUMNS,
    compute_upside_prediction_fields,
    project_upside_prediction_row,
    sort_rows_by_upside_prediction,
)
from edge_research_tools import (
    DEFAULT_FORWARD_LABEL_HORIZONS,
    DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    run_forward_label_generation,
    run_edge_research_source_inventory,
    run_edge_highlights,
    run_edge_safety_highlights,
    run_symbol_day_feature_snapshot,
    run_volatility_liquidity_edge_suite,
    run_volatility_liquidity_setup_pass,
    run_edge_screen,
    run_persistence_scan,
    run_edge_summary,
    resolve_edge_research_paths,
)
from edge_research_tools.config import PROJECT_ROOT

OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "edge_research_tools" / "runs"
)
DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD = 500_000_000.0
PREFERRED_MARKETS = tuple(
    str(value).lower() for value in TRADING_VIEW_PREFERRED_MARKETS
)


def _split_csv_values(csv_values: str | None) -> list[str] | None:
    if csv_values is None:
        return None
    values = [value.strip() for value in str(csv_values).split(",") if value.strip()]
    return values or None


def _build_parent_run_dir(
    *,
    output_root: str | Path | None,
    suite_name: str,
) -> Path:
    output_paths = resolve_edge_research_paths(output_root=output_root)
    created_at_utc = datetime.now(tz=timezone.utc)
    run_id = (
        f"{suite_name}_{created_at_utc.strftime('%Y%m%d_%H%M')}_utc_"
        f"{uuid.uuid4().hex[:8]}"
    )
    parent_run_dir = output_paths.output_root / run_id
    parent_run_dir.mkdir(parents=True, exist_ok=True)
    return parent_run_dir


def _rewrite_result_paths(value: Any, old_root: Path, new_root: Path) -> Any:
    old_prefix = old_root.as_posix()
    new_prefix = new_root.as_posix()

    def _rewrite_path_text(path_text: str) -> str:
        path_posix = Path(path_text).as_posix()
        if path_posix == old_prefix:
            return new_prefix
        if path_posix.startswith(old_prefix + "/"):
            suffix = path_posix[len(old_prefix) + 1 :]
            return (new_root / suffix).as_posix()
        return path_text

    if isinstance(value, dict):
        return {
            key: _rewrite_result_paths(item, old_root=old_root, new_root=new_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_result_paths(item, old_root=old_root, new_root=new_root)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _rewrite_result_paths(item, old_root=old_root, new_root=new_root)
            for item in value
        )
    if isinstance(value, Path):
        return Path(_rewrite_path_text(value.as_posix()))
    if isinstance(value, str):
        return _rewrite_path_text(value)
    return value


def _rewrite_moved_artifact_files(
    *,
    run_dir: Path,
    old_root: Path,
    new_root: Path,
) -> None:
    old_prefix_posix = old_root.as_posix()
    new_prefix_posix = new_root.as_posix()
    old_prefix_native = str(old_root)
    new_prefix_native = str(new_root)

    for artifact_path in run_dir.rglob("*"):
        if not artifact_path.is_file() or artifact_path.suffix.lower() not in {
            ".json",
            ".md",
            ".txt",
        }:
            continue
        try:
            original_text = artifact_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        updated_text = original_text.replace(old_prefix_posix, new_prefix_posix)
        if old_prefix_native != old_prefix_posix:
            updated_text = updated_text.replace(old_prefix_native, new_prefix_native)

        if updated_text != original_text:
            artifact_path.write_text(updated_text, encoding="utf-8")


def _move_child_run_to_parent(
    *,
    result: dict[str, Any],
    parent_run_dir: Path,
    child_name: str,
) -> dict[str, Any]:
    src_dir_raw = result.get("output_dir")
    if src_dir_raw is None:
        return result
    src_dir = Path(src_dir_raw)
    if not src_dir.exists():
        return result
    dest_dir = parent_run_dir / child_name
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        raise FileExistsError(f"Destination already exists: {dest_dir.as_posix()}")
    src_dir.rename(dest_dir)
    moved = _rewrite_result_paths(result, old_root=src_dir, new_root=dest_dir)
    _rewrite_moved_artifact_files(
        run_dir=dest_dir,
        old_root=src_dir,
        new_root=dest_dir,
    )
    if isinstance(moved, dict):
        moved["output_dir"] = dest_dir
    return moved


def _cleanup_staging_dir(staging_dir: Path) -> None:
    if not staging_dir.exists():
        return

    for child_dir in sorted(
        (path for path in staging_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(child_dir.iterdir()):
            child_dir.rmdir()

    if staging_dir.exists() and not any(staging_dir.iterdir()):
        staging_dir.rmdir()


def _write_parent_manifest(
    *,
    parent_run_dir: Path,
    payload: dict[str, Any],
) -> Path:
    manifest_path = parent_run_dir / "parent_run_manifest.json"
    manifest_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    return manifest_path


def _detect_snapshot_min_market_cap(snapshot_db: str | Path) -> float | None:
    snapshot_path = Path(snapshot_db)
    manifest_path = snapshot_path.parent / "symbol_day_feature_snapshot_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    min_cap = payload.get("min_market_cap_usd")
    if min_cap is None:
        return None
    try:
        return float(min_cap)
    except (TypeError, ValueError):
        return None


def _infer_parent_scoped_output_root_for_snapshot(
    snapshot_db: str | Path | None,
    *,
    child_root_name: str,
) -> Path | None:
    if snapshot_db is None:
        return None

    snapshot_path = Path(snapshot_db)
    for candidate in snapshot_path.parents:
        if (candidate / "parent_run_manifest.json").exists():
            return candidate / child_root_name
    return None


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_symbol_token(value: str | None) -> str:
    return str(value or "").strip().upper()


def _split_exchange_symbol(value: str | None) -> tuple[str | None, str]:
    normalized = _normalize_symbol_token(value)
    if not normalized:
        return None, ""
    if ":" not in normalized:
        return None, normalized
    exchange, bare_ticker = normalized.split(":", 1)
    return exchange or None, bare_ticker


def _build_historical_validation_fields(
    row: Mapping[str, Any],
    *,
    ranking_horizon: int,
    safety_companion_score: float | None = None,
) -> dict[str, Any]:
    horizon = int(ranking_horizon)
    hist_occurrence_count = _safe_int(row.get("hist_occurrence_count_in_setup"))
    hist_win_rate = _clamp(
        _safe_float(row.get(f"win_rate_{horizon}d_in_setup"), default=0.0)
    )
    hist_median_fwd_pct = _safe_float(
        row.get(f"median_fwd_{horizon}d_in_setup"),
        default=0.0,
    )
    any_setup_rate = _clamp(_safe_float(row.get("any_setup_rate"), default=0.0))
    stability_score = _clamp(_safe_float(row.get("stability_score"), default=0.0))

    occurrence_score = _clamp(hist_occurrence_count / 8.0)
    recurrence_score = _clamp(any_setup_rate / 0.15)
    return_score = _clamp(hist_median_fwd_pct / 12.0)

    base_score = _clamp(
        (0.28 * occurrence_score)
        + (0.22 * recurrence_score)
        + (0.20 * hist_win_rate)
        + (0.15 * return_score)
        + (0.15 * stability_score)
    )
    if safety_companion_score is not None:
        base_score = _clamp(
            (0.85 * base_score) + (0.15 * _clamp(float(safety_companion_score)))
        )

    historical_validation_pass = int(
        hist_occurrence_count >= 3
        and hist_win_rate >= 0.55
        and hist_median_fwd_pct >= 1.0
        and any_setup_rate >= 0.03
        and stability_score >= 0.45
        and (
            safety_companion_score is None
            or _clamp(float(safety_companion_score)) >= 0.50
        )
    )
    if historical_validation_pass:
        historical_validation_bucket = "strong"
    elif base_score >= 0.62 and hist_occurrence_count >= 2:
        historical_validation_bucket = "supported"
    elif base_score >= 0.45:
        historical_validation_bucket = "mixed"
    else:
        historical_validation_bucket = "thin"

    return {
        "historical_validation_score": round(base_score, 4),
        "historical_validation_bucket": historical_validation_bucket,
        "historical_validation_pass": historical_validation_pass,
        "historical_validation_occurrence_count": hist_occurrence_count,
        "historical_validation_win_rate": round(hist_win_rate, 4),
        "historical_validation_median_fwd_pct": round(hist_median_fwd_pct, 4),
        "historical_validation_any_setup_rate": round(any_setup_rate, 4),
        "historical_validation_stability_score": round(stability_score, 4),
    }


def _discover_latest_edge_parent_run_dir(
    *,
    output_root: str | Path | None,
) -> Path:
    output_paths = resolve_edge_research_paths(output_root=output_root)
    candidates = sorted(
        (
            path
            for path in output_paths.output_root.iterdir()
            if path.is_dir() and (path / "parent_run_manifest.json").exists()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No parent edge-research run directories were found under "
            f"{output_paths.output_root.as_posix()}."
        )
    return candidates[0]


def _resolve_edge_parent_run_dir(
    *,
    run_ref: str | Path | None,
    output_root: str | Path | None,
    auto_discover_latest: bool,
) -> Path:
    if run_ref is None:
        if not auto_discover_latest:
            raise ValueError("run_ref is required when auto_discover_latest is False.")
        return _discover_latest_edge_parent_run_dir(output_root=output_root)

    path = _resolve_project_path(run_ref)
    if path.is_file():
        current = path.parent
    else:
        current = path
    for candidate in (current, *current.parents):
        if (candidate / "parent_run_manifest.json").exists():
            return candidate

    context = _resolve_edge_research_run_reference(path)
    parent_run_dir = context.get("parent_run_dir")
    if parent_run_dir is None:
        raise ValueError(
            "Run reference does not resolve to an aggregate parent run directory: "
            f"{path.as_posix()}"
        )
    return Path(parent_run_dir)


def _resolve_requested_symbol(
    available_symbols: Sequence[str],
    requested_symbol: str,
) -> tuple[str, str]:
    normalized_requested = _normalize_symbol_token(requested_symbol)
    if not normalized_requested:
        raise ValueError("requested_symbol must be non-empty.")

    exact_map = {
        _normalize_symbol_token(symbol): str(symbol)
        for symbol in available_symbols
        if _normalize_symbol_token(symbol)
    }
    if normalized_requested in exact_map:
        return exact_map[normalized_requested], "exact"

    requested_exchange, requested_bare_ticker = _split_exchange_symbol(
        normalized_requested
    )
    bare_matches = [
        str(symbol)
        for symbol in available_symbols
        if _split_exchange_symbol(symbol)[1] == requested_bare_ticker
    ]
    if requested_exchange is not None:
        exchange_matches = [
            symbol
            for symbol in bare_matches
            if _split_exchange_symbol(symbol)[0] == requested_exchange
        ]
        if len(exchange_matches) == 1:
            return exchange_matches[0], "exchange-qualified"
    if len(bare_matches) == 1:
        return bare_matches[0], "unique-bare-ticker"
    if not bare_matches:
        raise KeyError(
            f"Requested symbol was not found in aggregate outputs: {requested_symbol}"
        )
    raise ValueError(
        "Requested symbol is ambiguous across aggregate outputs. "
        f"Pass EXCHANGE:TICKER instead: {requested_symbol}"
    )


def run_edge_symbol_inspection_method(
    *,
    symbol: str,
    run_ref: str | Path | None = None,
    output_root: str | Path | None = None,
    auto_discover_latest: bool = True,
) -> dict[str, Any]:
    parent_run_dir = _resolve_edge_parent_run_dir(
        run_ref=run_ref,
        output_root=output_root,
        auto_discover_latest=auto_discover_latest,
    )

    dataset_specs = (
        {
            "name": "unified_highlight",
            "path": parent_run_dir
            / "edge_unified_highlights"
            / "edge_unified_highlights.csv",
            "rank_key": "unified_edge_highlight_rank",
            "score_key": "unified_edge_highlight_score",
        },
        {
            "name": "big_mover",
            "path": parent_run_dir / "highlights" / "edge_name_shortlist.csv",
            "rank_key": "big_mover_rank",
            "score_key": "big_mover_score",
        },
        {
            "name": "confidence",
            "path": parent_run_dir / "highlights" / "edge_name_shortlist.csv",
            "rank_key": "confidence_rank",
            "score_key": "confidence_score",
        },
        {
            "name": "upside_prediction",
            "path": parent_run_dir
            / "upside_prediction_lens"
            / "edge_upside_prediction_ranked.csv",
            "rank_key": "upside_prediction_rank",
            "score_key": "upside_prediction_score",
        },
        {
            "name": "forward_valuation",
            "path": parent_run_dir
            / "forward_upside_valuation_lens"
            / "edge_forward_upside_valuation_ranked.csv",
            "rank_key": "forward_upside_rank",
            "score_key": "forward_upside_score",
        },
        {
            "name": "tradeable_safety",
            "path": parent_run_dir
            / "tradeable_safety_lens"
            / "edge_tradeable_safety_overlay.csv",
            "rank_key": "tradeable_safety_rank",
            "score_key": "tradeable_safety_blend_score",
        },
        {
            "name": "safety_companion",
            "path": parent_run_dir / "safety_highlights" / "edge_safety_scored.csv",
            "rank_key": "safety_rank",
            "score_key": "safety_companion_score",
        },
    )

    loaded_rows: dict[str, list[dict[str, str]]] = {}
    available_symbols: set[str] = set()
    for spec in dataset_specs:
        path = Path(spec["path"])
        rows = _read_csv_rows(path) if path.exists() else []
        loaded_rows[str(spec["name"])] = rows
        available_symbols.update(
            str(row.get("symbol") or "").strip()
            for row in rows
            if str(row.get("symbol") or "").strip()
        )

    matched_symbol, match_mode = _resolve_requested_symbol(
        sorted(available_symbols),
        symbol,
    )
    normalized_match = _normalize_symbol_token(matched_symbol)

    sections: list[dict[str, Any]] = []
    for spec in dataset_specs:
        rows = loaded_rows[str(spec["name"])]
        row = next(
            (
                candidate
                for candidate in rows
                if _normalize_symbol_token(candidate.get("symbol")) == normalized_match
            ),
            None,
        )
        if row is None:
            continue
        rank_key = str(spec["rank_key"])
        score_key = str(spec["score_key"])
        raw_rank = row.get(rank_key)
        rank_value = _safe_int(raw_rank) if raw_rank not in (None, "") else None
        raw_score = row.get(score_key)
        score_value: float | str = ""
        if raw_score not in (None, ""):
            score_value = round(_safe_float(raw_score), 4)
        section = {
            "dataset": str(spec["name"]),
            "rank": rank_value,
            "population": len(rows),
            "rank_display": (
                f"{rank_value}/{len(rows)}" if rank_value is not None else ""
            ),
            "score": score_value,
            "path": Path(spec["path"]).as_posix(),
        }
        if spec["name"] == "forward_valuation":
            section["forward_valuation_upside_pct"] = row.get(
                "forward_valuation_upside_pct",
                "",
            )
            section["historical_validation_score"] = row.get(
                "historical_validation_score",
                "",
            )
            section["historical_validation_bucket"] = row.get(
                "historical_validation_bucket",
                "",
            )
        if spec["name"] in {"tradeable_safety", "unified_highlight"}:
            section["historical_validation_score"] = row.get(
                "historical_validation_score",
                "",
            )
            section["historical_validation_bucket"] = row.get(
                "historical_validation_bucket",
                "",
            )
        sections.append(section)

    return {
        "parent_run_dir": parent_run_dir,
        "requested_symbol": symbol,
        "matched_symbol": matched_symbol,
        "match_mode": match_mode,
        "sections": sections,
    }


def _print_edge_symbol_inspection(result: Mapping[str, Any]) -> None:
    print(f"Parent run output: {result['parent_run_dir']}")
    print(f"Requested symbol: {result['requested_symbol']}")
    print("Matched symbol: " f"{result['matched_symbol']} ({result['match_mode']})")
    sections = list(result.get("sections") or [])
    if not sections:
        print("No aggregate rows found for the requested symbol.")
        return
    for index, section in enumerate(sections, start=1):
        rank_text = "" if section.get("rank") in (None, "") else str(section["rank"])
        rank_display = str(section.get("rank_display") or "")
        score = section.get("score", "")
        print(f"{index}\t{section['dataset']}\t{rank_text}\t{rank_display}\t{score}")


def _classify_tradeability_safety_bucket(
    *,
    safety_companion_score: float,
    indicator_pass_count: int,
    safety_data_available: bool,
) -> str:
    if not safety_data_available:
        return "unscored"
    if safety_companion_score >= 0.80 and indicator_pass_count >= 6:
        return "safer"
    if safety_companion_score >= 0.68 and indicator_pass_count >= 5:
        return "balanced"
    if safety_companion_score >= 0.55 and indicator_pass_count >= 3:
        return "aggressive"
    return "speculative"


def _write_tradeability_safety_lens_report(
    path: Path,
    *,
    highlights_shortlist_csv: Path,
    safety_scored_csv: Path,
    overlay_csv: Path,
    top_csv: Path,
    overlay_row_count: int,
    top_row_count: int,
    top_count: int,
) -> None:
    lines = [
        "# Tradeable Safety Lens",
        "",
        "## What This Output Does",
        "",
        "This overlay keeps the highlights shortlist intact and adds safety / quality / value context without excluding names.",
        "Use it when you want upside-oriented tradeable candidates ranked by both current setup quality and safety support.",
        "",
        "## Score Construction",
        "",
        "- `tradeable_core_score` = 55% confidence score + 45% big mover score.",
        "- `tradeable_safety_blend_score` = 65% tradeable core + 35% safety companion score.",
        "- `historical_validation_score` is a separate backward-context check using recurrence, historical win rate, historical median forward return, stability, and optional safety support.",
        "- `historical_validation_bucket` summarizes that check as `strong`, `supported`, `mixed`, or `thin` without changing the main blend rank.",
        "- `safety_bucket` is a non-excluding risk lens: `safer`, `balanced`, `aggressive`, `speculative`, or `unscored`.",
        "- `safety_shortlist_flag` shows whether the name also passed the explicit safety shortlist thresholds.",
        "- `safety_focus_flag` shows whether the name also landed in the safety focus list.",
        "- Forward valuation return columns (`forward_valuation_upside_pct`, horizon upside %, mode, lenses) are appended when the integrated suite runs the forward-upside lens first.",
        "",
        "## How To Use It",
        "",
        "- Start with the blend rank when you want upside names with some quality control.",
        "- Sort by `safety_rank_within_tradeables` when you want the safest names inside the highlights shortlist.",
        "- Sort by `risk_rank_within_tradeables` when you want the riskiest/high-beta candidates inside the same shortlist.",
        "- Keep `tradeable_core_score` visible so safety does not hide weak current-state names.",
        "",
        "## Inputs",
        "",
        f"- highlights shortlist: `{highlights_shortlist_csv.as_posix()}`",
        f"- safety scored universe: `{safety_scored_csv.as_posix()}`",
        "",
        "## Output Files",
        "",
        f"- overlay CSV: `{overlay_csv.as_posix()}` ({overlay_row_count} rows)",
        f"- top focus CSV: `{top_csv.as_posix()}` ({top_row_count} rows, requested count = {top_count})",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_tradeability_safety_lens(
    *,
    parent_run_dir: Path,
    highlights_result: dict[str, Any],
    safety_result: dict[str, Any],
    top_count: int,
    forward_upside_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    highlights_shortlist_csv = Path(highlights_result["shortlist_csv"])
    safety_scored_csv = Path(safety_result["scored_csv"])
    safety_shortlist_csv = Path(safety_result["shortlist_csv"])
    safety_focus_csv = Path(safety_result["top10_csv"])

    highlight_rows = _read_csv_rows(highlights_shortlist_csv)
    safety_scored_rows = _read_csv_rows(safety_scored_csv)
    safety_shortlist_rows = _read_csv_rows(safety_shortlist_csv)
    safety_focus_rows = _read_csv_rows(safety_focus_csv)

    safety_by_symbol = {
        str(row.get("symbol")): row for row in safety_scored_rows if row.get("symbol")
    }
    safety_shortlist_symbols = {
        str(row.get("symbol")) for row in safety_shortlist_rows if row.get("symbol")
    }
    safety_focus_symbols = {
        str(row.get("symbol")) for row in safety_focus_rows if row.get("symbol")
    }

    output_dir = parent_run_dir / "tradeable_safety_lens"
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay_csv = output_dir / "edge_tradeable_safety_overlay.csv"
    top_csv = output_dir / f"edge_tradeable_safety_top{int(max(1, top_count))}.csv"
    report_md = output_dir / "edge_tradeable_safety_lens_report.md"
    manifest_path = output_dir / "edge_tradeable_safety_lens_manifest.json"

    overlay_rows: list[dict[str, Any]] = []
    ranking_horizon = int(highlights_result.get("ranking_horizon") or 5)
    for highlight_row in highlight_rows:
        symbol = str(highlight_row.get("symbol") or "")
        safety_row = safety_by_symbol.get(symbol)
        safety_data_available = safety_row is not None

        big_mover_score = _safe_float(highlight_row.get("big_mover_score"))
        confidence_score = _safe_float(highlight_row.get("confidence_score"))
        stability_score = _safe_float(highlight_row.get("stability_score"))
        tradeable_core_score = round(
            (0.55 * confidence_score) + (0.45 * big_mover_score),
            4,
        )

        balance_sheet_safety_score = _safe_float(
            safety_row.get("balance_sheet_safety_score") if safety_row else None
        )
        cash_generation_value_score = _safe_float(
            safety_row.get("cash_generation_value_score") if safety_row else None
        )
        safety_companion_score = _safe_float(
            safety_row.get("safety_companion_score") if safety_row else None
        )
        indicator_pass_count = _safe_int(
            safety_row.get("indicator_pass_count") if safety_row else None
        )
        safety_rank = _safe_int(safety_row.get("safety_rank") if safety_row else None)

        tradeable_safety_blend_score = round(
            (0.65 * tradeable_core_score) + (0.35 * safety_companion_score),
            4,
        )
        safety_bucket = _classify_tradeability_safety_bucket(
            safety_companion_score=safety_companion_score,
            indicator_pass_count=indicator_pass_count,
            safety_data_available=safety_data_available,
        )
        historical_fields = _build_historical_validation_fields(
            highlight_row,
            ranking_horizon=ranking_horizon,
            safety_companion_score=(
                safety_companion_score if safety_data_available else None
            ),
        )

        overlay_rows.append(
            {
                **highlight_row,
                "tradeable_core_score": round(tradeable_core_score, 4),
                "tradeable_safety_blend_score": round(
                    tradeable_safety_blend_score,
                    4,
                ),
                "safety_rank_global": safety_rank if safety_data_available else "",
                "balance_sheet_safety_score": (
                    round(balance_sheet_safety_score, 4)
                    if safety_data_available
                    else ""
                ),
                "cash_generation_value_score": (
                    round(cash_generation_value_score, 4)
                    if safety_data_available
                    else ""
                ),
                "safety_companion_score": (
                    round(safety_companion_score, 4) if safety_data_available else ""
                ),
                "indicator_pass_count": (
                    indicator_pass_count if safety_data_available else ""
                ),
                "safety_shortlist_flag": int(symbol in safety_shortlist_symbols),
                "safety_focus_flag": int(symbol in safety_focus_symbols),
                "safety_data_available": int(safety_data_available),
                "safety_bucket": safety_bucket,
                **historical_fields,
                **extract_forward_upside_valuation_overlay_fields(
                    (forward_upside_by_symbol or {}).get(symbol)
                ),
            }
        )

    overlay_rows.sort(
        key=lambda row: (
            _safe_float(row.get("tradeable_safety_blend_score")),
            _safe_float(row.get("confidence_score")),
            _safe_float(row.get("safety_companion_score")),
        ),
        reverse=True,
    )
    for index, row in enumerate(overlay_rows, start=1):
        row["tradeable_safety_rank"] = index

    safest_rows = sorted(
        overlay_rows,
        key=lambda row: (
            _safe_float(row.get("safety_companion_score")),
            _safe_int(row.get("indicator_pass_count")),
            _safe_float(row.get("tradeable_core_score")),
        ),
        reverse=True,
    )
    safety_rank_within_tradeables = {
        str(row.get("symbol")): index for index, row in enumerate(safest_rows, start=1)
    }
    riskiest_rows = list(reversed(safest_rows))
    risk_rank_within_tradeables = {
        str(row.get("symbol")): index
        for index, row in enumerate(riskiest_rows, start=1)
    }
    for row in overlay_rows:
        symbol = str(row.get("symbol") or "")
        row["safety_rank_within_tradeables"] = safety_rank_within_tradeables.get(
            symbol,
            "",
        )
        row["risk_rank_within_tradeables"] = risk_rank_within_tradeables.get(
            symbol,
            "",
        )

    top_rows = overlay_rows[: int(max(1, top_count))]
    _write_csv_rows(overlay_csv, overlay_rows)
    _write_csv_rows(top_csv, top_rows)
    _write_tradeability_safety_lens_report(
        report_md,
        highlights_shortlist_csv=highlights_shortlist_csv,
        safety_scored_csv=safety_scored_csv,
        overlay_csv=overlay_csv,
        top_csv=top_csv,
        overlay_row_count=len(overlay_rows),
        top_row_count=len(top_rows),
        top_count=int(max(1, top_count)),
    )

    manifest = {
        "command": "tradeable-safety-lens",
        "highlights_shortlist_csv": highlights_shortlist_csv.as_posix(),
        "safety_scored_csv": safety_scored_csv.as_posix(),
        "safety_shortlist_csv": safety_shortlist_csv.as_posix(),
        "safety_focus_csv": safety_focus_csv.as_posix(),
        "output_dir": output_dir.as_posix(),
        "overlay_csv": overlay_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "overlay_row_count": len(overlay_rows),
        "top_row_count": len(top_rows),
        "top_count": int(max(1, top_count)),
        "tradeable_core_weight": 0.65,
        "safety_companion_weight": 0.35,
        "forward_upside_overlay_columns": list(
            FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS
        ),
        "forward_upside_overlay_enabled": int(forward_upside_by_symbol is not None),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_dir,
        "overlay_csv": overlay_csv,
        "top_csv": top_csv,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "overlay_row_count": len(overlay_rows),
        "top_row_count": len(top_rows),
        "top_count": int(max(1, top_count)),
    }


def _write_upside_prediction_lens_report(
    path: Path,
    *,
    highlights_shortlist_csv: Path,
    safety_scored_csv: Path,
    ranked_csv: Path,
    top_csv: Path,
    ranked_row_count: int,
    top_row_count: int,
    top_count: int,
    ranking_horizon: int,
) -> None:
    lines = [
        "# Upside Prediction Lens",
        "",
        "## What This Output Does",
        "",
        "This lens ranks highlights shortlist names for upward move prediction.",
        "Upside metrics lead the file; balance-sheet and cash-generation safety scores are appended at the end without excluding names.",
        "",
        "## Score Construction",
        "",
        "- `upside_prediction_score` = 30% big_mover + 25% blended historical upside signal + 20% composite + 15% lane context + 10% target-before-stop rate, with a small MAE drawdown penalty.",
        "- `upside_hist_signal_blend` blends name-level historical win/median/target/MFE with lane base rates; name weight scales with `hist_occurrence_count_in_setup`.",
        "- `upside_target_before_stop_rate` is the historical share of setup days where +10% was reached before -7% within the ranking horizon.",
        "- `balance_sheet_safety_score` and `cash_generation_value_score` are companion safety components appended for review only.",
        "",
        "## How To Use It",
        "",
        "- Sort by `upside_prediction_rank` for the best upward-move candidates.",
        "- Read `upside_hist_win_rate`, `upside_hist_median_fwd_pct`, and `upside_target_before_stop_rate` for empirical chance and magnitude.",
        "- Use trailing safety columns for balance-sheet and cash-generation context after upside review.",
        "",
        f"- ranking_horizon: {ranking_horizon}d",
        f"- target rule: +10% before -7% within horizon (from forward labels)",
        "",
        "## Inputs",
        "",
        f"- highlights shortlist: `{highlights_shortlist_csv.as_posix()}`",
        f"- safety scored universe: `{safety_scored_csv.as_posix()}`",
        "",
        "## Output Files",
        "",
        f"- ranked CSV: `{ranked_csv.as_posix()}` ({ranked_row_count} rows)",
        f"- top focus CSV: `{top_csv.as_posix()}` ({top_row_count} rows, requested count = {top_count})",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_upside_prediction_lens(
    *,
    parent_run_dir: Path,
    highlights_result: dict[str, Any],
    safety_result: dict[str, Any],
    top_count: int,
) -> dict[str, Any]:
    highlights_shortlist_csv = Path(highlights_result["shortlist_csv"])
    safety_scored_csv = Path(safety_result["scored_csv"])
    safety_shortlist_csv = Path(safety_result["shortlist_csv"])
    safety_focus_csv = Path(safety_result["top10_csv"])
    ranking_horizon = int(highlights_result.get("ranking_horizon") or 5)

    highlight_rows = _read_csv_rows(highlights_shortlist_csv)
    safety_scored_rows = _read_csv_rows(safety_scored_csv)
    safety_shortlist_rows = _read_csv_rows(safety_shortlist_csv)
    safety_focus_rows = _read_csv_rows(safety_focus_csv)

    safety_by_symbol = {
        str(row.get("symbol")): row for row in safety_scored_rows if row.get("symbol")
    }
    safety_shortlist_symbols = {
        str(row.get("symbol")) for row in safety_shortlist_rows if row.get("symbol")
    }
    safety_focus_symbols = {
        str(row.get("symbol")) for row in safety_focus_rows if row.get("symbol")
    }

    output_dir = parent_run_dir / "upside_prediction_lens"
    output_dir.mkdir(parents=True, exist_ok=True)
    ranked_csv = output_dir / "edge_upside_prediction_ranked.csv"
    top_csv = output_dir / f"edge_upside_prediction_top{int(max(1, top_count))}.csv"
    report_md = output_dir / "edge_upside_prediction_lens_report.md"
    manifest_path = output_dir / "edge_upside_prediction_lens_manifest.json"

    enriched_rows: list[dict[str, Any]] = []
    for highlight_row in highlight_rows:
        symbol = str(highlight_row.get("symbol") or "")
        safety_row = safety_by_symbol.get(symbol)
        safety_data_available = safety_row is not None

        row = dict(highlight_row)
        row.update(
            compute_upside_prediction_fields(
                row,
                ranking_horizon=ranking_horizon,
            )
        )

        balance_sheet_safety_score = _safe_float(
            safety_row.get("balance_sheet_safety_score") if safety_row else None
        )
        cash_generation_value_score = _safe_float(
            safety_row.get("cash_generation_value_score") if safety_row else None
        )
        safety_companion_score = _safe_float(
            safety_row.get("safety_companion_score") if safety_row else None
        )
        indicator_pass_count = _safe_int(
            safety_row.get("indicator_pass_count") if safety_row else None
        )
        safety_rank = _safe_int(safety_row.get("safety_rank") if safety_row else None)
        safety_bucket = _classify_tradeability_safety_bucket(
            safety_companion_score=safety_companion_score,
            indicator_pass_count=indicator_pass_count,
            safety_data_available=safety_data_available,
        )

        row.update(
            {
                "balance_sheet_safety_score": (
                    round(balance_sheet_safety_score, 4)
                    if safety_data_available
                    else ""
                ),
                "cash_generation_value_score": (
                    round(cash_generation_value_score, 4)
                    if safety_data_available
                    else ""
                ),
                "safety_companion_score": (
                    round(safety_companion_score, 4) if safety_data_available else ""
                ),
                "safety_bucket": safety_bucket,
                "indicator_pass_count": (
                    indicator_pass_count if safety_data_available else ""
                ),
                "safety_rank_global": safety_rank if safety_data_available else "",
                "safety_shortlist_flag": int(symbol in safety_shortlist_symbols),
                "safety_focus_flag": int(symbol in safety_focus_symbols),
                "safety_data_available": int(safety_data_available),
            }
        )
        enriched_rows.append(row)

    ranked_rows = sort_rows_by_upside_prediction(enriched_rows)
    projected_ranked_rows = [
        project_upside_prediction_row(
            row,
            ranking_horizon=ranking_horizon,
            include_safety=True,
        )
        for row in ranked_rows
    ]
    projected_top_rows = projected_ranked_rows[: int(max(1, top_count))]

    _write_csv_rows(ranked_csv, projected_ranked_rows)
    _write_csv_rows(top_csv, projected_top_rows)
    _write_upside_prediction_lens_report(
        report_md,
        highlights_shortlist_csv=highlights_shortlist_csv,
        safety_scored_csv=safety_scored_csv,
        ranked_csv=ranked_csv,
        top_csv=top_csv,
        ranked_row_count=len(projected_ranked_rows),
        top_row_count=len(projected_top_rows),
        top_count=int(max(1, top_count)),
        ranking_horizon=ranking_horizon,
    )

    manifest = {
        "command": "upside-prediction-lens",
        "ranking_horizon": ranking_horizon,
        "highlights_shortlist_csv": highlights_shortlist_csv.as_posix(),
        "safety_scored_csv": safety_scored_csv.as_posix(),
        "safety_shortlist_csv": safety_shortlist_csv.as_posix(),
        "safety_focus_csv": safety_focus_csv.as_posix(),
        "output_dir": output_dir.as_posix(),
        "ranked_csv": ranked_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "ranked_row_count": len(projected_ranked_rows),
        "top_row_count": len(projected_top_rows),
        "top_count": int(max(1, top_count)),
        "safety_trailing_columns": list(UPSIDE_PREDICTION_SAFETY_TRAILING_COLUMNS),
        "upside_score_weights": {
            "big_mover_score": 0.30,
            "blended_hist_signal": 0.25,
            "composite_score": 0.20,
            "lane_context_score": 0.15,
            "target_before_stop_rate": 0.10,
            "mae_penalty_max": 0.08,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_dir,
        "ranked_csv": ranked_csv,
        "top_csv": top_csv,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "ranked_row_count": len(projected_ranked_rows),
        "top_row_count": len(projected_top_rows),
        "top_count": int(max(1, top_count)),
        "ranking_horizon": ranking_horizon,
    }


def _write_forward_upside_valuation_lens_report(
    path: Path,
    *,
    highlights_shortlist_csv: Path,
    safety_scored_csv: Path,
    source_database_path: Path,
    peer_universe_row_count: int,
    ranked_csv: Path,
    top_csv: Path,
    ranked_row_count: int,
    top_row_count: int,
    top_count: int,
    ranking_horizon: int,
    max_active_lenses: int,
    min_valuation_lenses: int,
    valuation_row_count: int,
    fallback_row_count: int,
) -> None:
    lines = [
        "# Forward Upside Valuation Lens",
        "",
        "## What This Output Does",
        "",
        "This lens estimates forward-looking upside by adapting valuation method mix per company type.",
        "Each name uses at most four valuation mechanisms and falls back to edge-native upside ranking when valuation coverage is sparse.",
        "",
        "## Method Selection Rules",
        "",
        f"- Hard cap: max `{int(max_active_lenses)}` active valuation lenses per company.",
        f"- Minimum valuation coverage for valuation mode: `{int(min_valuation_lenses)}` usable lenses.",
        "- Peer multiples and peer-relative normalization use the full safety-scored universe for the run date, resolved per symbol as industry -> sector -> global fallback.",
        "- Growth tilt: trajectory + multiple + analyst + technical.",
        "- Value tilt: multiple + book_value + analyst + range.",
        "- Income tilt: yield_dcf + multiple + analyst + range.",
        "- Sparse coverage fallback: robust anchors then edge upside signal.",
        "",
        "## Score Construction",
        "",
        "- `forward_upside_score` combines valuation signal, selected-lens coverage, supporting upside signal, and safety context.",
        "- `forward_upside_mode` indicates whether valuation mode was used or fallback mode was triggered.",
        "- `forward_valuation_upside_pct` is the headline valuation-model upside %: a 55/25/20 blend of long/medium/near base fair-value upside vs current price.",
        "- `forward_rank_horizon_valuation_upside_pct` maps the suite ranking horizon to the nearest valuation horizon upside % (<=20d near, <=126d medium, else long).",
        "- `forward_valuation_bear_upside_pct` / `forward_valuation_bull_upside_pct` are the long-term scenario band from blended bear/bull price targets.",
        "- `forward_selected_lenses` shows the exact subset used for each symbol.",
        "- Targets inherit the targets-analysis upside blend outlier control (raw anchors preserved; blend capped at 4.0x close).",
        "",
        "## How To Use It",
        "",
        "- Sort by `forward_upside_rank` to prioritize names with strongest forward valuation upside.",
        "- Check `forward_upside_mode` and `forward_upside_fallback_reason` before acting on sparse-data names.",
        "- Use `forward_valuation_upside_pct` as the primary fair-value upside %; use bear/bull columns for scenario sizing.",
        "",
        f"- ranking_horizon: {ranking_horizon}d",
        "",
        "## Inputs",
        "",
        f"- highlights shortlist: `{highlights_shortlist_csv.as_posix()}`",
        f"- safety scored universe: `{safety_scored_csv.as_posix()}`",
        f"- peer valuation universe rows: {peer_universe_row_count}",
        f"- source daily database: `{source_database_path.as_posix()}`",
        "",
        "## Output Files",
        "",
        f"- ranked CSV: `{ranked_csv.as_posix()}` ({ranked_row_count} rows)",
        f"- top focus CSV: `{top_csv.as_posix()}` ({top_row_count} rows, requested count = {top_count})",
        "",
        "## Mode Coverage",
        "",
        f"- valuation mode rows: {valuation_row_count}",
        f"- fallback mode rows: {fallback_row_count}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_forward_upside_valuation_lens(
    *,
    parent_run_dir: Path,
    highlights_result: dict[str, Any],
    safety_result: dict[str, Any],
    top_count: int,
    max_active_lenses: int = MAX_ACTIVE_VALUATION_LENSES,
    min_valuation_lenses: int = MIN_USABLE_VALUATION_LENSES,
) -> dict[str, Any]:
    highlights_shortlist_csv = Path(highlights_result["shortlist_csv"])
    safety_scored_csv = Path(safety_result["scored_csv"])
    safety_shortlist_csv = Path(safety_result["shortlist_csv"])
    safety_focus_csv = Path(safety_result["top10_csv"])
    source_database_path = Path(safety_result["source_database_path"])
    ranking_horizon = int(highlights_result.get("ranking_horizon") or 5)

    highlight_rows = _read_csv_rows(highlights_shortlist_csv)
    safety_scored_rows = _read_csv_rows(safety_scored_csv)
    safety_shortlist_rows = _read_csv_rows(safety_shortlist_csv)
    safety_focus_rows = _read_csv_rows(safety_focus_csv)

    safety_by_symbol = {
        str(row.get("symbol")): row for row in safety_scored_rows if row.get("symbol")
    }
    safety_shortlist_symbols = {
        str(row.get("symbol")) for row in safety_shortlist_rows if row.get("symbol")
    }
    safety_focus_symbols = {
        str(row.get("symbol")) for row in safety_focus_rows if row.get("symbol")
    }
    highlight_symbols = [str(row.get("symbol") or "") for row in highlight_rows]
    highlight_symbol_set = {symbol for symbol in highlight_symbols if symbol}
    peer_universe_symbols = sorted(
        highlight_symbol_set
        | {
            str(row.get("symbol") or "")
            for row in safety_scored_rows
            if row.get("symbol")
        }
    )
    peer_source_rows_by_symbol = load_latest_source_rows_for_symbols(
        source_database_path=source_database_path,
        symbols=peer_universe_symbols,
    )
    source_rows_by_symbol = {
        symbol: row
        for symbol, row in peer_source_rows_by_symbol.items()
        if symbol in highlight_symbol_set
    }
    peer_universe_row_count = len(peer_source_rows_by_symbol)

    output_dir = parent_run_dir / "forward_upside_valuation_lens"
    output_dir.mkdir(parents=True, exist_ok=True)
    ranked_csv = output_dir / "edge_forward_upside_valuation_ranked.csv"
    top_csv = (
        output_dir / f"edge_forward_upside_valuation_top{int(max(1, top_count))}.csv"
    )
    report_md = output_dir / "edge_forward_upside_valuation_lens_report.md"
    manifest_path = output_dir / "edge_forward_upside_valuation_lens_manifest.json"

    enriched_rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows_by_symbol,
        peer_source_rows=list(peer_source_rows_by_symbol.values()),
        ranking_horizon=ranking_horizon,
        max_active_lenses=int(max(1, max_active_lenses)),
        min_valuation_lenses=int(max(1, min_valuation_lenses)),
    )

    for row in enriched_rows:
        symbol = str(row.get("symbol") or "")
        safety_row = safety_by_symbol.get(symbol)
        safety_data_available = safety_row is not None
        balance_sheet_safety_score = _safe_float(
            safety_row.get("balance_sheet_safety_score") if safety_row else None
        )
        cash_generation_value_score = _safe_float(
            safety_row.get("cash_generation_value_score") if safety_row else None
        )
        safety_companion_score = _safe_float(
            safety_row.get("safety_companion_score") if safety_row else None
        )
        indicator_pass_count = _safe_int(
            safety_row.get("indicator_pass_count") if safety_row else None
        )
        safety_rank = _safe_int(safety_row.get("safety_rank") if safety_row else None)
        safety_bucket = _classify_tradeability_safety_bucket(
            safety_companion_score=safety_companion_score,
            indicator_pass_count=indicator_pass_count,
            safety_data_available=safety_data_available,
        )
        historical_fields = _build_historical_validation_fields(
            row,
            ranking_horizon=ranking_horizon,
            safety_companion_score=(
                safety_companion_score if safety_data_available else None
            ),
        )
        row.update(
            {
                "balance_sheet_safety_score": (
                    round(balance_sheet_safety_score, 4)
                    if safety_data_available
                    else ""
                ),
                "cash_generation_value_score": (
                    round(cash_generation_value_score, 4)
                    if safety_data_available
                    else ""
                ),
                "safety_companion_score": (
                    round(safety_companion_score, 4) if safety_data_available else ""
                ),
                "safety_bucket": safety_bucket,
                "indicator_pass_count": (
                    indicator_pass_count if safety_data_available else ""
                ),
                "safety_rank_global": safety_rank if safety_data_available else "",
                "safety_shortlist_flag": int(symbol in safety_shortlist_symbols),
                "safety_focus_flag": int(symbol in safety_focus_symbols),
                "safety_data_available": int(safety_data_available),
                **historical_fields,
            }
        )

    ranked_rows = sort_rows_by_forward_upside_valuation(enriched_rows)
    rows_by_symbol = {
        str(row.get("symbol")): row for row in ranked_rows if row.get("symbol")
    }
    projected_ranked_rows = [
        project_forward_upside_valuation_row(
            row,
            ranking_horizon=ranking_horizon,
            include_safety=True,
        )
        for row in ranked_rows
    ]
    projected_top_rows = projected_ranked_rows[: int(max(1, top_count))]

    _write_csv_rows(ranked_csv, projected_ranked_rows)
    _write_csv_rows(top_csv, projected_top_rows)

    valuation_row_count = sum(
        1 for row in ranked_rows if str(row.get("forward_upside_mode")) == "valuation"
    )
    fallback_row_count = len(ranked_rows) - valuation_row_count
    _write_forward_upside_valuation_lens_report(
        report_md,
        highlights_shortlist_csv=highlights_shortlist_csv,
        safety_scored_csv=safety_scored_csv,
        source_database_path=source_database_path,
        peer_universe_row_count=peer_universe_row_count,
        ranked_csv=ranked_csv,
        top_csv=top_csv,
        ranked_row_count=len(projected_ranked_rows),
        top_row_count=len(projected_top_rows),
        top_count=int(max(1, top_count)),
        ranking_horizon=ranking_horizon,
        max_active_lenses=int(max(1, max_active_lenses)),
        min_valuation_lenses=int(max(1, min_valuation_lenses)),
        valuation_row_count=valuation_row_count,
        fallback_row_count=fallback_row_count,
    )

    manifest = {
        "command": "forward-upside-valuation-lens",
        "ranking_horizon": ranking_horizon,
        "highlights_shortlist_csv": highlights_shortlist_csv.as_posix(),
        "safety_scored_csv": safety_scored_csv.as_posix(),
        "safety_shortlist_csv": safety_shortlist_csv.as_posix(),
        "safety_focus_csv": safety_focus_csv.as_posix(),
        "source_database_path": source_database_path.as_posix(),
        "peer_universe_row_count": peer_universe_row_count,
        "output_dir": output_dir.as_posix(),
        "ranked_csv": ranked_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "ranked_row_count": len(projected_ranked_rows),
        "top_row_count": len(projected_top_rows),
        "top_count": int(max(1, top_count)),
        "max_active_lenses": int(max(1, max_active_lenses)),
        "min_valuation_lenses": int(max(1, min_valuation_lenses)),
        "valuation_row_count": valuation_row_count,
        "fallback_row_count": fallback_row_count,
        "safety_trailing_columns": list(
            FORWARD_UPSIDE_VALUATION_SAFETY_TRAILING_COLUMNS
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_dir,
        "ranked_csv": ranked_csv,
        "top_csv": top_csv,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "ranked_row_count": len(projected_ranked_rows),
        "top_row_count": len(projected_top_rows),
        "top_count": int(max(1, top_count)),
        "ranking_horizon": ranking_horizon,
        "valuation_row_count": valuation_row_count,
        "fallback_row_count": fallback_row_count,
        "peer_universe_row_count": peer_universe_row_count,
        "rows_by_symbol": rows_by_symbol,
    }


def _build_unified_edge_highlights_lens(
    *,
    parent_run_dir: Path,
    highlights_result: dict[str, Any],
    safety_result: dict[str, Any],
    scan_edge_result: dict[str, Any] | None,
    tradeable_safety_result: dict[str, Any] | None,
    upside_prediction_lens_result: dict[str, Any],
    forward_upside_valuation_lens_result: dict[str, Any],
) -> dict[str, Any]:
    output_dir = parent_run_dir / "edge_unified_highlights"
    return build_unified_edge_highlights_store(
        output_dir=output_dir,
        highlights_result=highlights_result,
        safety_result=safety_result,
        scan_edge_result=scan_edge_result,
        tradeable_safety_result=tradeable_safety_result,
        upside_prediction_lens_result=upside_prediction_lens_result,
        forward_upside_valuation_lens_result=forward_upside_valuation_lens_result,
    )


def run_inventory_method(
    *,
    limit: int = 10,
    project_root: str | Path | None = None,
    all_fields_root: str | Path | None = None,
    scan_period_runs_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    return run_edge_research_source_inventory(
        limit=max(1, int(limit)),
        project_root=project_root,
        all_fields_root=all_fields_root,
        scan_period_runs_root=scan_period_runs_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )


def run_snapshot_method(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path | None = None,
    output_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    include_non_primary: bool = False,
    min_market_cap_usd: float | None = None,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    return run_symbol_day_feature_snapshot(
        start_day_label=str(start_day_label),
        end_day_label=str(end_day_label),
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
        primary_only=not bool(include_non_primary),
        min_market_cap_usd=min_market_cap_usd,
        duckdb_threads=int(duckdb_threads),
        memory_limit_gb=float(memory_limit_gb),
    )


def run_labels_method(
    *,
    snapshot_db: str | Path,
    horizons: Sequence[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    return run_forward_label_generation(
        snapshot_database_path=snapshot_db,
        horizons=horizons,
        target_pct=float(target_pct),
        stop_pct=float(stop_pct),
        duckdb_threads=int(duckdb_threads),
        memory_limit_gb=float(memory_limit_gb),
    )


def run_setup_vol_liq_method(
    *,
    snapshot_db: str | Path,
    group_by: str = "universe",
    group_values: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    custom_group_csv: str | Path | None = None,
    ranking_horizon: int | None = None,
    min_occurrence_count: int = 10,
    min_symbol_occurrence_count: int = 3,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    return run_volatility_liquidity_setup_pass(
        snapshot_database_path=snapshot_db,
        group_by=str(group_by),
        group_values=group_values,
        symbols=symbols,
        custom_group_csv=custom_group_csv,
        ranking_horizon=ranking_horizon,
        min_occurrence_count=int(min_occurrence_count),
        min_symbol_occurrence_count=int(min_symbol_occurrence_count),
        output_root=output_root,
    )


def run_suite_method(
    *,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    use_full_range: bool = False,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
    include_non_primary: bool = False,
    min_market_cap_usd: float | None = None,
    horizons: Sequence[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    ranking_horizon: int | None = None,
    groupings: Sequence[str] = ("universe", "industry"),
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    return run_volatility_liquidity_edge_suite(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        use_full_range=bool(use_full_range),
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
        primary_only=not bool(include_non_primary),
        min_market_cap_usd=min_market_cap_usd,
        horizons=horizons,
        target_pct=float(target_pct),
        stop_pct=float(stop_pct),
        ranking_horizon=ranking_horizon,
        groupings=groupings,
        duckdb_threads=int(duckdb_threads),
        memory_limit_gb=float(memory_limit_gb),
    )


def run_screen_method(
    *,
    snapshot_db: str | Path,
    date: str | None = None,
    top_n: int = 50,
    min_composite: float = 0.55,
    ranking_horizon: int | None = None,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    return run_edge_screen(
        snapshot_database_path=snapshot_db,
        target_date=date,
        top_n=int(top_n),
        min_composite=float(min_composite),
        ranking_horizon=ranking_horizon,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=bool(us_only),
        output_root=output_root,
        duckdb_threads=int(duckdb_threads),
    )


def run_persistence_method(
    *,
    snapshot_db: str | Path,
    min_setup_days: int = 5,
    top_n: int = 100,
    ranking_horizon: int | None = None,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    return run_persistence_scan(
        snapshot_database_path=snapshot_db,
        min_setup_days=int(min_setup_days),
        top_n=int(top_n),
        ranking_horizon=ranking_horizon,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=bool(us_only),
        start_date=start_date,
        end_date=end_date,
        output_root=output_root,
        duckdb_threads=int(duckdb_threads),
    )


def run_edge_summary_method(
    *,
    snapshot_db: str | Path,
    group_by: str = "industry",
    ranking_horizon: int | None = None,
    min_occurrence_count: int = 5,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    bootstrap_iterations: int = 400,
    bootstrap_confidence_level: float = 0.9,
    bootstrap_seed: int = 17,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    return run_edge_summary(
        snapshot_database_path=snapshot_db,
        group_by=str(group_by),
        ranking_horizon=ranking_horizon,
        min_occurrence_count=int(min_occurrence_count),
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=bool(us_only),
        start_date=start_date,
        end_date=end_date,
        bootstrap_iterations=int(bootstrap_iterations),
        bootstrap_confidence_level=float(bootstrap_confidence_level),
        bootstrap_seed=int(bootstrap_seed),
        output_root=output_root,
        duckdb_threads=int(duckdb_threads),
    )


def run_highlights_method(
    *,
    snapshot_db: str | Path | None = None,
    date: str | None = None,
    ranking_horizon: int = 5,
    lane_setup_name: str = "adrp_relvol_core",
    group_by: str = "industry",
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    screen_min_composite: float = 0.45,
    lane_min_sample_count: int = 20,
    lane_min_win_rate: float = 0.55,
    lane_min_median_fwd: float = 1.0,
    min_any_setup_rate: float = 0.03,
    min_hist_occurrences: int = 2,
    min_hist_win_rate: float = 0.45,
    min_hist_median_fwd: float = 0.0,
    min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    stability_short_lookback: int = 10,
    stability_long_lookback: int = 20,
    bootstrap_iterations: int = 400,
    bootstrap_confidence_level: float = 0.9,
    bootstrap_seed: int = 31,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    resolved_snapshot_db = snapshot_db
    resolved_output_root = output_root
    if resolved_snapshot_db is None and resolved_output_root is None:
        try:
            latest_run_ref = _coalesce_existing_run_ref(
                None,
                output_root=output_root,
                auto_discover_latest=True,
            )
        except FileNotFoundError:
            latest_run_ref = None
        if latest_run_ref is not None:
            try:
                latest_context = _resolve_edge_research_run_reference(latest_run_ref)
            except (FileNotFoundError, ValueError):
                latest_context = None
            if latest_context is not None:
                resolved_snapshot_db = latest_context.get("snapshot_db")

    if resolved_output_root is None:
        inferred_output_root = _infer_parent_scoped_output_root_for_snapshot(
            resolved_snapshot_db,
            child_root_name="manual_highlights",
        )
        if inferred_output_root is not None:
            resolved_output_root = inferred_output_root

    return run_edge_highlights(
        snapshot_database_path=resolved_snapshot_db,
        target_date=date,
        ranking_horizon=int(ranking_horizon),
        lane_setup_name=str(lane_setup_name),
        group_by=str(group_by),
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=bool(us_only),
        screen_min_composite=float(screen_min_composite),
        lane_min_sample_count=int(lane_min_sample_count),
        lane_min_win_rate=float(lane_min_win_rate),
        lane_min_median_fwd=float(lane_min_median_fwd),
        min_any_setup_rate=float(min_any_setup_rate),
        min_hist_occurrences=int(min_hist_occurrences),
        min_hist_win_rate=float(min_hist_win_rate),
        min_hist_median_fwd=float(min_hist_median_fwd),
        min_shortlist_count=int(max(1, min_shortlist_count)),
        shortlist_top_n=int(shortlist_top_n),
        top10_count=int(top10_count),
        upside_top_count=int(upside_top_count),
        stability_short_lookback=int(stability_short_lookback),
        stability_long_lookback=int(stability_long_lookback),
        bootstrap_iterations=int(bootstrap_iterations),
        bootstrap_confidence_level=float(bootstrap_confidence_level),
        bootstrap_seed=int(bootstrap_seed),
        output_root=resolved_output_root,
        duckdb_threads=int(duckdb_threads),
    )


def run_safety_highlights_method(
    *,
    snapshot_db: str | Path | None = None,
    date: str | None = None,
    group_by: str = "industry",
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    min_balance_sheet_score: float = 0.45,
    min_cash_generation_score: float = 0.40,
    min_combined_score: float = 0.50,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    group_min_count: int = 3,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    return run_edge_safety_highlights(
        snapshot_database_path=snapshot_db,
        target_date=date,
        group_by=str(group_by),
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=bool(us_only),
        min_balance_sheet_score=float(min_balance_sheet_score),
        min_cash_generation_score=float(min_cash_generation_score),
        min_combined_score=float(min_combined_score),
        shortlist_top_n=int(shortlist_top_n),
        top10_count=int(top10_count),
        group_min_count=int(group_min_count),
        output_root=output_root,
        duckdb_threads=int(duckdb_threads),
    )


def run_daily_scan_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
    ranking_horizon: int = 5,
    screen_top_n: int = 300,
    screen_min_composite: float = 0.45,
    persistence_min_setup_days: int = 1,
    persistence_top_n: int = 20000,
    edge_group_by: str = "industry",
    edge_min_occurrence_count: int = 5,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    edge_bootstrap_iterations: int = 400,
    edge_bootstrap_confidence_level: float = 0.9,
    edge_bootstrap_seed: int = 17,
    parent_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Standalone Method 1: daily screen + persistence + grouped edge summary."""
    if parent_run_dir is None:
        parent_dir = _build_parent_run_dir(
            output_root=output_root,
            suite_name="edge_daily_aggregate_parent",
        )
        staging_output_root: str | Path | None = output_root
    else:
        parent_dir = Path(parent_run_dir)
        parent_dir.mkdir(parents=True, exist_ok=True)
        staging_output_root = parent_dir / "_staging"

    screen_result = run_screen_method(
        snapshot_db=snapshot_db,
        date=scan_day,
        top_n=screen_top_n,
        min_composite=screen_min_composite,
        ranking_horizon=ranking_horizon,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        output_root=staging_output_root,
        duckdb_threads=duckdb_threads,
    )
    screen_result = _move_child_run_to_parent(
        result=screen_result,
        parent_run_dir=parent_dir,
        child_name="screen",
    )

    persistence_result = run_persistence_method(
        snapshot_db=snapshot_db,
        min_setup_days=persistence_min_setup_days,
        top_n=persistence_top_n,
        ranking_horizon=ranking_horizon,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        start_date=window_start_date,
        end_date=scan_day,
        output_root=staging_output_root,
        duckdb_threads=duckdb_threads,
    )
    persistence_result = _move_child_run_to_parent(
        result=persistence_result,
        parent_run_dir=parent_dir,
        child_name="scan_persistence",
    )

    edge_summary_result = run_edge_summary_method(
        snapshot_db=snapshot_db,
        group_by=edge_group_by,
        ranking_horizon=ranking_horizon,
        min_occurrence_count=edge_min_occurrence_count,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        start_date=window_start_date,
        end_date=scan_day,
        bootstrap_iterations=edge_bootstrap_iterations,
        bootstrap_confidence_level=edge_bootstrap_confidence_level,
        bootstrap_seed=edge_bootstrap_seed,
        output_root=staging_output_root,
        duckdb_threads=duckdb_threads,
    )
    edge_summary_result = _move_child_run_to_parent(
        result=edge_summary_result,
        parent_run_dir=parent_dir,
        child_name="scan_edge",
    )

    if staging_output_root is not None:
        _cleanup_staging_dir(Path(staging_output_root))

    final_result = {
        "parent_run_dir": parent_dir,
        "screen": screen_result,
        "scan_persistence": persistence_result,
        "scan_edge": edge_summary_result,
    }
    _write_parent_manifest(
        parent_run_dir=parent_dir,
        payload={
            "suite": "daily_scan",
            "scan_day": scan_day,
            "window_start_date": window_start_date,
            "snapshot_db": Path(snapshot_db).as_posix(),
            "ranking_horizon": ranking_horizon,
            "markets": list(markets or []),
            "countries": list(countries or []),
            "exchanges": list(exchanges or []),
            "us_only": bool(us_only),
            "children": {
                "screen": str(screen_result.get("output_dir")),
                "scan_persistence": str(persistence_result.get("output_dir")),
                "scan_edge": str(edge_summary_result.get("output_dir")),
            },
        },
    )
    return final_result


def run_full_quantitative_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
    ranking_horizon: int = 5,
    screen_top_n: int = 500,
    screen_min_composite: float = 0.45,
    persistence_min_setup_days: int = 1,
    persistence_top_n: int = 20000,
    edge_group_by: str = "industry",
    edge_min_occurrence_count: int = 5,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    edge_bootstrap_iterations: int = 400,
    edge_bootstrap_confidence_level: float = 0.9,
    edge_bootstrap_seed: int = 17,
    lane_setup_name: str = "adrp_relvol_core",
    lane_min_sample_count: int = 20,
    lane_min_win_rate: float = 0.55,
    lane_min_median_fwd: float = 1.0,
    min_any_setup_rate: float = 0.03,
    min_hist_occurrences: int = 2,
    min_hist_win_rate: float = 0.45,
    min_hist_median_fwd: float = 0.0,
    highlights_min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    highlights_bootstrap_iterations: int = 400,
    highlights_bootstrap_confidence_level: float = 0.9,
    highlights_bootstrap_seed: int = 31,
    parent_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Standalone Method 2: full quantitative pass with top30/top10 highlights."""
    if parent_run_dir is None:
        parent_dir = _build_parent_run_dir(
            output_root=output_root,
            suite_name="edge_full_quant_parent",
        )
    else:
        parent_dir = Path(parent_run_dir)
        parent_dir.mkdir(parents=True, exist_ok=True)

    base_result = run_daily_scan_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=ranking_horizon,
        screen_top_n=screen_top_n,
        screen_min_composite=screen_min_composite,
        persistence_min_setup_days=persistence_min_setup_days,
        persistence_top_n=persistence_top_n,
        edge_group_by=edge_group_by,
        edge_min_occurrence_count=edge_min_occurrence_count,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        output_root=output_root,
        duckdb_threads=duckdb_threads,
        edge_bootstrap_iterations=edge_bootstrap_iterations,
        edge_bootstrap_confidence_level=edge_bootstrap_confidence_level,
        edge_bootstrap_seed=edge_bootstrap_seed,
        parent_run_dir=parent_dir,
    )

    highlights_result = run_highlights_method(
        snapshot_db=snapshot_db,
        date=scan_day,
        ranking_horizon=ranking_horizon,
        lane_setup_name=lane_setup_name,
        group_by=edge_group_by,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        screen_min_composite=screen_min_composite,
        lane_min_sample_count=lane_min_sample_count,
        lane_min_win_rate=lane_min_win_rate,
        lane_min_median_fwd=lane_min_median_fwd,
        min_any_setup_rate=min_any_setup_rate,
        min_hist_occurrences=min_hist_occurrences,
        min_hist_win_rate=min_hist_win_rate,
        min_hist_median_fwd=min_hist_median_fwd,
        min_shortlist_count=highlights_min_shortlist_count,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        upside_top_count=upside_top_count,
        bootstrap_iterations=highlights_bootstrap_iterations,
        bootstrap_confidence_level=highlights_bootstrap_confidence_level,
        bootstrap_seed=highlights_bootstrap_seed,
        output_root=parent_dir / "_staging",
        duckdb_threads=duckdb_threads,
    )
    highlights_result = _move_child_run_to_parent(
        result=highlights_result,
        parent_run_dir=parent_dir,
        child_name="highlights",
    )
    _cleanup_staging_dir(parent_dir / "_staging")

    final_result = {
        **base_result,
        "parent_run_dir": parent_dir,
        "highlights": highlights_result,
    }
    _write_parent_manifest(
        parent_run_dir=parent_dir,
        payload={
            "suite": "full_quantitative",
            "scan_day": scan_day,
            "window_start_date": window_start_date,
            "snapshot_db": Path(snapshot_db).as_posix(),
            "ranking_horizon": ranking_horizon,
            "markets": list(markets or []),
            "countries": list(countries or []),
            "exchanges": list(exchanges or []),
            "us_only": bool(us_only),
            "children": {
                "screen": str(base_result.get("screen", {}).get("output_dir")),
                "scan_persistence": str(
                    base_result.get("scan_persistence", {}).get("output_dir")
                ),
                "scan_edge": str(base_result.get("scan_edge", {}).get("output_dir")),
                "highlights": str(highlights_result.get("output_dir")),
            },
        },
    )
    return final_result


def run_integrated_extension_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
    ranking_horizon: int = 5,
    screen_top_n: int = 500,
    persistence_top_n: int = 20000,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    lane_setup_name: str = "adrp_relvol_core",
    highlights_min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    forward_upside_top_count: int = 20,
    edge_bootstrap_iterations: int = 1000,
    edge_bootstrap_confidence_level: float = 0.95,
    edge_bootstrap_seed: int = 17,
    highlights_bootstrap_iterations: int = 1000,
    highlights_bootstrap_confidence_level: float = 0.95,
    highlights_bootstrap_seed: int = 31,
    min_balance_sheet_score: float = 0.45,
    min_cash_generation_score: float = 0.40,
    min_combined_score: float = 0.50,
    safety_group_min_count: int = 3,
    parent_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Standalone Method 3: region filters + bootstrap CI + safety companion integration."""
    if parent_run_dir is None:
        parent_dir = _build_parent_run_dir(
            output_root=output_root,
            suite_name="edge_integrated_parent",
        )
    else:
        parent_dir = Path(parent_run_dir)
        parent_dir.mkdir(parents=True, exist_ok=True)

    quantitative_result = run_full_quantitative_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=ranking_horizon,
        screen_top_n=screen_top_n,
        persistence_top_n=persistence_top_n,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        output_root=output_root,
        duckdb_threads=duckdb_threads,
        lane_setup_name=lane_setup_name,
        highlights_min_shortlist_count=highlights_min_shortlist_count,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        upside_top_count=upside_top_count,
        edge_bootstrap_iterations=edge_bootstrap_iterations,
        edge_bootstrap_confidence_level=edge_bootstrap_confidence_level,
        edge_bootstrap_seed=edge_bootstrap_seed,
        highlights_bootstrap_iterations=highlights_bootstrap_iterations,
        highlights_bootstrap_confidence_level=highlights_bootstrap_confidence_level,
        highlights_bootstrap_seed=highlights_bootstrap_seed,
        parent_run_dir=parent_dir,
    )

    safety_result = run_safety_highlights_method(
        snapshot_db=snapshot_db,
        date=scan_day,
        group_by="industry",
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        min_balance_sheet_score=min_balance_sheet_score,
        min_cash_generation_score=min_cash_generation_score,
        min_combined_score=min_combined_score,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        group_min_count=safety_group_min_count,
        output_root=parent_dir / "_staging",
        duckdb_threads=duckdb_threads,
    )
    safety_result = _move_child_run_to_parent(
        result=safety_result,
        parent_run_dir=parent_dir,
        child_name="safety_highlights",
    )
    forward_upside_valuation_lens_result = _build_forward_upside_valuation_lens(
        parent_run_dir=parent_dir,
        highlights_result=quantitative_result["highlights"],
        safety_result=safety_result,
        top_count=forward_upside_top_count,
        max_active_lenses=MAX_ACTIVE_VALUATION_LENSES,
        min_valuation_lenses=MIN_USABLE_VALUATION_LENSES,
    )
    tradeable_safety_lens_result = _build_tradeability_safety_lens(
        parent_run_dir=parent_dir,
        highlights_result=quantitative_result["highlights"],
        safety_result=safety_result,
        top_count=top10_count,
        forward_upside_by_symbol=forward_upside_valuation_lens_result.get(
            "rows_by_symbol"
        ),
    )
    upside_prediction_lens_result = _build_upside_prediction_lens(
        parent_run_dir=parent_dir,
        highlights_result=quantitative_result["highlights"],
        safety_result=safety_result,
        top_count=upside_top_count,
    )
    unified_edge_highlights_result = _build_unified_edge_highlights_lens(
        parent_run_dir=parent_dir,
        highlights_result=quantitative_result["highlights"],
        safety_result=safety_result,
        scan_edge_result=quantitative_result.get("scan_edge"),
        tradeable_safety_result=tradeable_safety_lens_result,
        upside_prediction_lens_result=upside_prediction_lens_result,
        forward_upside_valuation_lens_result=forward_upside_valuation_lens_result,
    )
    _cleanup_staging_dir(parent_dir / "_staging")

    final_result = {
        **quantitative_result,
        "parent_run_dir": parent_dir,
        "safety_highlights": safety_result,
        "tradeable_safety_lens": tradeable_safety_lens_result,
        "upside_prediction_lens": upside_prediction_lens_result,
        "forward_upside_valuation_lens": forward_upside_valuation_lens_result,
        "edge_unified_highlights": unified_edge_highlights_result,
    }
    _write_parent_manifest(
        parent_run_dir=parent_dir,
        payload={
            "suite": "integrated_extension",
            "scan_day": scan_day,
            "window_start_date": window_start_date,
            "snapshot_db": Path(snapshot_db).as_posix(),
            "ranking_horizon": ranking_horizon,
            "markets": list(markets or []),
            "countries": list(countries or []),
            "exchanges": list(exchanges or []),
            "us_only": bool(us_only),
            "children": {
                "screen": str(quantitative_result.get("screen", {}).get("output_dir")),
                "scan_persistence": str(
                    quantitative_result.get("scan_persistence", {}).get("output_dir")
                ),
                "scan_edge": str(
                    quantitative_result.get("scan_edge", {}).get("output_dir")
                ),
                "highlights": str(
                    quantitative_result.get("highlights", {}).get("output_dir")
                ),
                "safety_highlights": str(safety_result.get("output_dir")),
                "tradeable_safety_lens": str(
                    tradeable_safety_lens_result.get("output_dir")
                ),
                "upside_prediction_lens": str(
                    upside_prediction_lens_result.get("output_dir")
                ),
                "forward_upside_valuation_lens": str(
                    forward_upside_valuation_lens_result.get("output_dir")
                ),
                "edge_unified_highlights": str(
                    unified_edge_highlights_result.get("output_dir")
                ),
            },
        },
    )
    return final_result


def run_default_production_suite_method() -> dict[str, Any]:
    """Default parameter-first full-range suite run used when script is launched without CLI args."""
    return run_suite_method(
        use_full_range=True,
        min_market_cap_usd=1_000_000_000,
        horizons=(3, 5, 10, 20),
        target_pct=10.0,
        stop_pct=7.0,
        groupings=("universe", "industry"),
        duckdb_threads=16,
        memory_limit_gb=24.0,
    )


def run_historic_current_aggregate_suite_preferred_markets(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
    output_root: str | Path | None = None,
    requested_min_market_cap_usd: float = DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
    ranking_horizon: int = 5,
    screen_top_n: int = 500,
    persistence_top_n: int = 20000,
    highlights_min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    forward_upside_top_count: int = 20,
    duckdb_threads: int = 16,
    horizons: Sequence[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    """
    Preferred-markets full analysis suite:
    - grouped parent run output
    - preferred markets filter constants
    - full scan aggregate method
    - detects whether current snapshot appears to be >= 1B universe only
    """
    snapshot_prep_result = _ensure_snapshot_ready_for_aggregate(
        snapshot_db,
        horizons=horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    snapshot_db = Path(snapshot_prep_result["snapshot_db"])
    parent_dir = _build_parent_run_dir(
        output_root=output_root,
        suite_name="edge_latest_500m_full_parent",
    )
    detected_snapshot_min_cap = _detect_snapshot_min_market_cap(snapshot_db)
    if detected_snapshot_min_cap is None:
        needs_new_500m_scan = True
        scan_requirement_reason = "snapshot_min_cap_unknown"
    elif detected_snapshot_min_cap > float(requested_min_market_cap_usd):
        needs_new_500m_scan = True
        scan_requirement_reason = "snapshot_min_cap_above_requested_threshold"
    else:
        needs_new_500m_scan = False
        scan_requirement_reason = "snapshot_min_cap_compatible"

    integrated_result = run_integrated_extension_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=ranking_horizon,
        screen_top_n=screen_top_n,
        persistence_top_n=persistence_top_n,
        markets=PREFERRED_MARKETS,
        countries=None,
        exchanges=None,
        us_only=False,
        output_root=output_root,
        duckdb_threads=duckdb_threads,
        highlights_min_shortlist_count=highlights_min_shortlist_count,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        upside_top_count=upside_top_count,
        forward_upside_top_count=forward_upside_top_count,
        edge_bootstrap_iterations=1000,
        edge_bootstrap_confidence_level=0.95,
        highlights_bootstrap_iterations=1000,
        highlights_bootstrap_confidence_level=0.95,
        parent_run_dir=parent_dir,
    )

    manifest_path = _write_parent_manifest(
        parent_run_dir=parent_dir,
        payload={
            "suite": "historic_current_aggregate_preferred_markets",
            "scan_day": scan_day,
            "window_start_date": window_start_date,
            "snapshot_db": Path(snapshot_db).as_posix(),
            "preferred_markets": list(PREFERRED_MARKETS),
            "requested_min_market_cap_usd": float(requested_min_market_cap_usd),
            "detected_snapshot_min_market_cap_usd": detected_snapshot_min_cap,
            "needs_new_500m_scan": bool(needs_new_500m_scan),
            "scan_requirement_reason": scan_requirement_reason,
            "screen_top_n": int(screen_top_n),
            "persistence_top_n": int(persistence_top_n),
            "highlights_min_shortlist_count": int(highlights_min_shortlist_count),
            "shortlist_top_n": int(shortlist_top_n),
            "top10_count": int(top10_count),
            "upside_top_count": int(upside_top_count),
            "forward_upside_top_count": int(forward_upside_top_count),
            "children": {
                "screen": str(integrated_result.get("screen", {}).get("output_dir")),
                "scan_persistence": str(
                    integrated_result.get("scan_persistence", {}).get("output_dir")
                ),
                "scan_edge": str(
                    integrated_result.get("scan_edge", {}).get("output_dir")
                ),
                "highlights": str(
                    integrated_result.get("highlights", {}).get("output_dir")
                ),
                "safety_highlights": str(
                    integrated_result.get("safety_highlights", {}).get("output_dir")
                ),
                "tradeable_safety_lens": str(
                    integrated_result.get("tradeable_safety_lens", {}).get("output_dir")
                ),
                "upside_prediction_lens": str(
                    integrated_result.get("upside_prediction_lens", {}).get(
                        "output_dir"
                    )
                ),
                "forward_upside_valuation_lens": str(
                    integrated_result.get("forward_upside_valuation_lens", {}).get(
                        "output_dir"
                    )
                ),
                "edge_unified_highlights": str(
                    integrated_result.get("edge_unified_highlights", {}).get(
                        "output_dir"
                    )
                ),
            },
        },
    )

    return {
        **integrated_result,
        "suite": "historic_current_aggregate_preferred_markets",
        "parent_run_dir": parent_dir,
        "manifest_path": manifest_path,
        "preferred_markets": list(PREFERRED_MARKETS),
        "requested_min_market_cap_usd": float(requested_min_market_cap_usd),
        "detected_snapshot_min_market_cap_usd": detected_snapshot_min_cap,
        "needs_new_500m_scan": bool(needs_new_500m_scan),
        "scan_requirement_reason": scan_requirement_reason,
        "snapshot_prep_result": snapshot_prep_result,
    }


def _resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def _discover_edge_snapshot_run_refs(
    output_root: str | Path | None = None,
) -> list[Path]:
    paths = resolve_edge_research_paths(output_root=output_root)
    discovered: list[tuple[float, Path]] = []
    seen: set[str] = set()
    for root in (paths.output_root, paths.foundation_root):
        if not root.exists():
            continue
        for db_path in root.glob("**/symbol_day_feature_snapshot.duckdb"):
            parent = db_path.parent.resolve()
            key = parent.as_posix()
            if key in seen:
                continue
            seen.add(key)
            discovered.append((db_path.stat().st_mtime, parent))
    discovered.sort(key=lambda item: item[0], reverse=True)
    return [parent for _, parent in discovered]


def _coalesce_existing_run_ref(
    existing_run_ref: str | Path | None,
    *,
    output_root: str | Path | None = None,
    auto_discover_latest: bool = False,
) -> Path | None:
    if existing_run_ref is None:
        if not auto_discover_latest:
            return None
        discovered = _discover_edge_snapshot_run_refs(output_root=output_root)
        if not discovered:
            raise FileNotFoundError(
                "No snapshot runs found under edge_research_tools. "
                "Set rebuild_foundation_snapshot=True in run_edge_research_local_main() "
                "to build a new foundation snapshot."
            )
        return discovered[0]

    path = _resolve_project_path(existing_run_ref)
    if path.exists():
        return path

    if path.name.startswith(
        ("edge_feature_snapshot_", "edge_latest_500m_full_parent_")
    ):
        alt_roots = []
        paths = resolve_edge_research_paths(output_root=output_root)
        alt_roots.extend([paths.foundation_root, paths.output_root])
        for alt_root in alt_roots:
            alt_path = alt_root / path.name
            if alt_path.exists():
                return alt_path.resolve()

    discovered = _discover_edge_snapshot_run_refs(output_root=output_root)
    message_lines = [f"Edge research run reference not found: {path}"]
    if discovered:
        message_lines.append("Available snapshot runs:")
        for candidate in discovered[:10]:
            try:
                rel = candidate.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                rel = candidate.as_posix()
            message_lines.append(f"  - {rel}")
    else:
        message_lines.append(
            "No snapshot runs found under edge_research_tools. "
            "Set existing_run_ref = None to build a fresh foundation snapshot."
        )
    raise FileNotFoundError("\n".join(message_lines))


AGGREGATE_REQUIRED_TABLES: tuple[str, ...] = (
    "symbol_day_feature_snapshot",
    "symbol_day_feature_values",
    "symbol_day_sleeve_scores",
    "symbol_day_forward_labels",
)


def _snapshot_table_names(snapshot_db: str | Path) -> set[str]:
    import duckdb

    connection = duckdb.connect(Path(snapshot_db).as_posix(), read_only=True)
    try:
        return {str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()}
    finally:
        connection.close()


def _ensure_snapshot_ready_for_aggregate(
    snapshot_db: str | Path,
    *,
    horizons: Sequence[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    """Ensure an existing snapshot DB has the tables required by aggregate scans."""
    db_path = Path(snapshot_db)
    if not db_path.exists():
        raise FileNotFoundError(f"Snapshot database not found: {db_path}")

    tables = _snapshot_table_names(db_path)
    missing_tables = [name for name in AGGREGATE_REQUIRED_TABLES if name not in tables]
    missing_tables_before = list(missing_tables)
    if not missing_tables:
        return {
            "snapshot_db": db_path.as_posix(),
            "labels_generated": False,
            "missing_tables_before": [],
        }

    if "symbol_day_feature_snapshot" not in tables:
        raise ValueError(
            "Snapshot database is missing core feature tables. "
            f"Missing: {', '.join(missing_tables)}. "
            "Set rebuild_foundation_snapshot=True to build a new foundation snapshot."
        )

    label_result: dict[str, Any] | None = None
    if "symbol_day_forward_labels" in missing_tables:
        label_result = run_labels_method(
            snapshot_db=db_path,
            horizons=horizons,
            target_pct=target_pct,
            stop_pct=stop_pct,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )
        tables = _snapshot_table_names(db_path)
        if "symbol_day_forward_labels" not in tables:
            raise ValueError(
                "Forward label generation completed but symbol_day_forward_labels "
                f"is still missing in {db_path.as_posix()}."
            )
        missing_tables = [
            name for name in AGGREGATE_REQUIRED_TABLES if name not in tables
        ]

    if missing_tables:
        raise ValueError(
            "Snapshot database is still incomplete for aggregate scans. "
            f"Missing: {', '.join(missing_tables)}."
        )

    return {
        "snapshot_db": db_path.as_posix(),
        "labels_generated": label_result is not None,
        "label_result": label_result,
        "missing_tables_before": missing_tables_before,
    }


def _day_label_to_iso_date(day_label: str) -> str:
    return datetime.strptime(day_label, "%d_%m_%Y").date().isoformat()


def _read_snapshot_day_range(
    *,
    snapshot_db: str | Path,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
) -> tuple[str, str]:
    if start_day_label and end_day_label:
        return start_day_label, end_day_label

    manifest_path = (
        Path(snapshot_db).parent / "symbol_day_feature_snapshot_manifest.json"
    )
    if not manifest_path.exists():
        raise ValueError(
            "Could not resolve snapshot day range. Provide start_day_label and "
            f"end_day_label, or use a snapshot with manifest: {manifest_path}"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved_start = payload.get("start_day_label")
    resolved_end = payload.get("end_day_label")
    if not resolved_start or not resolved_end:
        raise ValueError(
            f"Snapshot manifest is missing start_day_label/end_day_label: {manifest_path}"
        )
    return str(resolved_start), str(resolved_end)


def _resolve_edge_research_run_reference(
    run_ref: str | Path,
) -> dict[str, Any]:
    """Resolve a prior run folder or snapshot DB into reusable scan context."""
    path = _resolve_project_path(run_ref)
    if not path.exists():
        raise FileNotFoundError(f"Edge research run reference not found: {path}")

    def _context_from_snapshot_db(
        snapshot_db: Path,
        *,
        source_run_ref: Path | None = None,
        parent_run_dir: Path | None = None,
    ) -> dict[str, Any]:
        start_day_label, end_day_label = _read_snapshot_day_range(
            snapshot_db=snapshot_db
        )
        return {
            "snapshot_db": snapshot_db.resolve(),
            "start_day_label": start_day_label,
            "end_day_label": end_day_label,
            "scan_day": _day_label_to_iso_date(end_day_label),
            "window_start_date": _day_label_to_iso_date(start_day_label),
            "source_run_ref": (source_run_ref or path).resolve(),
            "parent_run_dir": parent_run_dir.resolve() if parent_run_dir else None,
        }

    def _apply_parent_manifest_dates(
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("start_day_label"):
            context["start_day_label"] = str(payload["start_day_label"])
        if payload.get("end_day_label"):
            context["end_day_label"] = str(payload["end_day_label"])
        if payload.get("scan_day"):
            context["scan_day"] = str(payload["scan_day"])
        if payload.get("window_start_date"):
            context["window_start_date"] = str(payload["window_start_date"])
        elif context.get("start_day_label") and context.get("end_day_label"):
            context["window_start_date"] = _day_label_to_iso_date(
                str(context["start_day_label"])
            )
            context["scan_day"] = _day_label_to_iso_date(str(context["end_day_label"]))
        return context

    def _context_from_suite_manifest(
        manifest_path: Path,
        *,
        source_run_ref: Path,
        parent_run_dir: Path | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot_result = payload.get("snapshot_result")
        if not isinstance(snapshot_result, dict):
            raise ValueError(
                f"Suite manifest is missing snapshot_result: {manifest_path}"
            )
        snapshot_db_raw = snapshot_result.get("database_path")
        if not snapshot_db_raw:
            raise ValueError(
                f"Suite manifest is missing snapshot_result.database_path: {manifest_path}"
            )
        snapshot_db = Path(str(snapshot_db_raw))
        if not snapshot_db.exists():
            raise FileNotFoundError(
                f"Snapshot database from suite manifest does not exist: {snapshot_db}"
            )
        context = _context_from_snapshot_db(
            snapshot_db,
            source_run_ref=source_run_ref,
            parent_run_dir=parent_run_dir,
        )
        if payload.get("start_day_label"):
            context["start_day_label"] = str(payload["start_day_label"])
        if payload.get("end_day_label"):
            context["end_day_label"] = str(payload["end_day_label"])
        if context.get("start_day_label") and context.get("end_day_label"):
            context["window_start_date"] = _day_label_to_iso_date(
                str(context["start_day_label"])
            )
            context["scan_day"] = _day_label_to_iso_date(str(context["end_day_label"]))
        return context

    if path.is_file():
        if path.suffix.lower() != ".duckdb":
            raise ValueError(f"Expected a .duckdb file, got: {path}")
        return _context_from_snapshot_db(path)

    direct_db = path / "symbol_day_feature_snapshot.duckdb"
    if direct_db.exists():
        return _context_from_snapshot_db(direct_db, source_run_ref=path)

    parent_manifest_path = path / "parent_run_manifest.json"
    if parent_manifest_path.exists():
        parent_payload = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
        snapshot_db_raw = parent_payload.get("snapshot_db")
        if snapshot_db_raw:
            snapshot_db = Path(str(snapshot_db_raw))
            if snapshot_db.exists():
                context = _context_from_snapshot_db(
                    snapshot_db,
                    source_run_ref=path,
                    parent_run_dir=path,
                )
                return _apply_parent_manifest_dates(context, parent_payload)

        foundation_suite_manifest = (
            path / "foundation" / "volatility_liquidity_edge_suite_manifest.json"
        )
        if foundation_suite_manifest.exists():
            return _context_from_suite_manifest(
                foundation_suite_manifest,
                source_run_ref=path,
                parent_run_dir=path,
            )

    suite_manifest_path = path / "volatility_liquidity_edge_suite_manifest.json"
    if suite_manifest_path.exists():
        return _context_from_suite_manifest(
            suite_manifest_path,
            source_run_ref=path,
        )

    foundation_suite_manifest = (
        path / "foundation" / "volatility_liquidity_edge_suite_manifest.json"
    )
    if foundation_suite_manifest.exists():
        return _context_from_suite_manifest(
            foundation_suite_manifest,
            source_run_ref=path,
            parent_run_dir=path,
        )

    matches = sorted(path.glob("**/symbol_day_feature_snapshot.duckdb"))
    if len(matches) == 1:
        return _context_from_snapshot_db(
            matches[0],
            source_run_ref=path,
            parent_run_dir=path if matches[0].parent != path else None,
        )
    if len(matches) > 1:
        preview = ", ".join(match.as_posix() for match in matches[:5])
        raise ValueError(
            "Ambiguous edge research run reference; multiple snapshot databases "
            f"found under {path.as_posix()}: {preview}"
        )

    raise ValueError(
        "Could not resolve an edge research snapshot database from run reference: "
        f"{path.as_posix()}"
    )


def run_latest_500m_full_edge_research_suite(
    *,
    use_full_range: bool = True,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    min_market_cap_usd: float = DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
    output_root: str | Path = OUTPUT_ROOT,
    rebuild_foundation_snapshot: bool = False,
    existing_run_ref: str | Path | None = None,
    auto_discover_latest_snapshot: bool = True,
    primary_only: bool = True,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    horizons: Sequence[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    foundation_groupings: Sequence[str] = ("universe", "industry"),
    ranking_horizon: int = 5,
    screen_top_n: int = 500,
    persistence_top_n: int = 20_000,
    highlights_min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    forward_upside_top_count: int = 20,
) -> dict[str, Any]:
    """
    Full edge-research parent chain: foundation suite + preferred-markets aggregate.

    By default this reuses the latest existing snapshot and only runs aggregate scans.
    Set ``rebuild_foundation_snapshot=True`` to build a fresh foundation snapshot first.
    """
    resolved_run_context: dict[str, Any] | None = None
    foundation_result: dict[str, Any] | None = None
    snapshot_prep_result: dict[str, Any] | None = None

    parent_dir = _build_parent_run_dir(
        output_root=output_root,
        suite_name="edge_latest_500m_full_parent",
    )
    staging_root = parent_dir / "_staging"

    if rebuild_foundation_snapshot:
        foundation_result = run_suite_method(
            start_day_label=start_day_label,
            end_day_label=end_day_label,
            use_full_range=bool(use_full_range),
            output_root=staging_root,
            include_non_primary=not bool(primary_only),
            min_market_cap_usd=min_market_cap_usd,
            horizons=horizons,
            target_pct=target_pct,
            stop_pct=stop_pct,
            groupings=foundation_groupings,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )
        foundation_result = _move_child_run_to_parent(
            result=foundation_result,
            parent_run_dir=parent_dir,
            child_name="foundation",
        )
        snapshot_db = Path(foundation_result["snapshot_result"]["database_path"])
        suite_manifest_path = Path(foundation_result["suite_manifest"])
        suite_payload = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
        resolved_start_day_label = str(suite_payload["start_day_label"])
        resolved_end_day_label = str(suite_payload["end_day_label"])
        scan_day = _day_label_to_iso_date(resolved_end_day_label)
        window_start_date = _day_label_to_iso_date(resolved_start_day_label)
    else:
        resolved_existing_run_ref = _coalesce_existing_run_ref(
            existing_run_ref,
            output_root=output_root,
            auto_discover_latest=(
                auto_discover_latest_snapshot and existing_run_ref is None
            ),
        )
        if resolved_existing_run_ref is None:
            raise FileNotFoundError(
                "No existing snapshot run was provided. Set existing_run_ref, enable "
                "auto_discover_latest_snapshot, or set rebuild_foundation_snapshot=True."
            )
        resolved_run_context = _resolve_edge_research_run_reference(
            resolved_existing_run_ref
        )
        snapshot_db = Path(resolved_run_context["snapshot_db"])
        resolved_start_day_label = str(resolved_run_context["start_day_label"])
        resolved_end_day_label = str(resolved_run_context["end_day_label"])
        scan_day = str(resolved_run_context["scan_day"])
        window_start_date = str(resolved_run_context["window_start_date"])
        snapshot_prep_result = _ensure_snapshot_ready_for_aggregate(
            snapshot_db,
            horizons=horizons,
            target_pct=target_pct,
            stop_pct=stop_pct,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )
        snapshot_db = Path(snapshot_prep_result["snapshot_db"])

    aggregate_result = run_historic_current_aggregate_suite_preferred_markets(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        output_root=staging_root,
        requested_min_market_cap_usd=min_market_cap_usd,
        ranking_horizon=ranking_horizon,
        screen_top_n=screen_top_n,
        persistence_top_n=persistence_top_n,
        highlights_min_shortlist_count=highlights_min_shortlist_count,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        upside_top_count=upside_top_count,
        forward_upside_top_count=forward_upside_top_count,
        duckdb_threads=duckdb_threads,
        horizons=horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        memory_limit_gb=memory_limit_gb,
    )
    aggregate_parent = Path(aggregate_result["parent_run_dir"])
    aggregate_dest = parent_dir / "aggregate"
    if aggregate_parent != aggregate_dest:
        if aggregate_dest.exists():
            raise FileExistsError(
                f"Destination already exists: {aggregate_dest.as_posix()}"
            )
        aggregate_parent.rename(aggregate_dest)
        _rewrite_moved_artifact_files(
            run_dir=aggregate_dest,
            old_root=aggregate_parent,
            new_root=aggregate_dest,
        )
        aggregate_result = _rewrite_result_paths(
            aggregate_result,
            old_root=aggregate_parent,
            new_root=aggregate_dest,
        )
        aggregate_result["parent_run_dir"] = aggregate_dest

    _cleanup_staging_dir(staging_root)

    manifest_path = _write_parent_manifest(
        parent_run_dir=parent_dir,
        payload={
            "suite": "latest_500m_full_edge_research",
            "use_full_range": bool(use_full_range),
            "start_day_label": resolved_start_day_label,
            "end_day_label": resolved_end_day_label,
            "scan_day": scan_day,
            "window_start_date": window_start_date,
            "min_market_cap_usd": float(min_market_cap_usd),
            "rebuild_foundation_snapshot": bool(rebuild_foundation_snapshot),
            "auto_discover_latest_snapshot": bool(auto_discover_latest_snapshot),
            "existing_run_ref": (
                str(resolved_run_context["source_run_ref"])
                if resolved_run_context is not None
                else None
            ),
            "snapshot_db": snapshot_db.as_posix(),
            "snapshot_prep_result": snapshot_prep_result,
            "preferred_markets": list(PREFERRED_MARKETS),
            "highlights_min_shortlist_count": int(highlights_min_shortlist_count),
            "children": {
                "foundation": (
                    str(foundation_result.get("output_dir"))
                    if foundation_result is not None
                    else None
                ),
                "aggregate": str(aggregate_result.get("parent_run_dir")),
                "screen": str(aggregate_result.get("screen", {}).get("output_dir")),
                "scan_persistence": str(
                    aggregate_result.get("scan_persistence", {}).get("output_dir")
                ),
                "scan_edge": str(
                    aggregate_result.get("scan_edge", {}).get("output_dir")
                ),
                "highlights": str(
                    aggregate_result.get("highlights", {}).get("output_dir")
                ),
                "safety_highlights": str(
                    aggregate_result.get("safety_highlights", {}).get("output_dir")
                ),
                "tradeable_safety_lens": str(
                    aggregate_result.get("tradeable_safety_lens", {}).get("output_dir")
                ),
                "upside_prediction_lens": str(
                    aggregate_result.get("upside_prediction_lens", {}).get("output_dir")
                ),
                "forward_upside_valuation_lens": str(
                    aggregate_result.get("forward_upside_valuation_lens", {}).get(
                        "output_dir"
                    )
                ),
                "edge_unified_highlights": str(
                    aggregate_result.get("edge_unified_highlights", {}).get(
                        "output_dir"
                    )
                ),
            },
        },
    )

    return {
        "parent_run_dir": parent_dir,
        "manifest_path": manifest_path,
        "snapshot_db": snapshot_db.as_posix(),
        "start_day_label": resolved_start_day_label,
        "end_day_label": resolved_end_day_label,
        "scan_day": scan_day,
        "window_start_date": window_start_date,
        "min_market_cap_usd": float(min_market_cap_usd),
        "existing_run_ref": (
            str(resolved_run_context["source_run_ref"])
            if resolved_run_context is not None
            else None
        ),
        "snapshot_prep_result": snapshot_prep_result,
        "foundation": foundation_result,
        "aggregate": aggregate_result,
    }


# ---------------------------------------------------------------------------
# Standalone parameter-first usage examples (no argparse required)
# ---------------------------------------------------------------------------


def example_run_daily_scan_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
) -> dict[str, Any]:
    """Example Method 1: daily screen + persistence + edge summary."""
    return run_daily_scan_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=5,
        screen_top_n=300,
        screen_min_composite=0.45,
        persistence_min_setup_days=1,
        persistence_top_n=20000,
        edge_group_by="industry",
        edge_min_occurrence_count=5,
    )


def example_run_full_quantitative_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
) -> dict[str, Any]:
    """Example Method 2: full quantitative pass with top30/top10 highlights."""
    return run_full_quantitative_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=5,
        edge_group_by="industry",
        shortlist_top_n=30,
        top10_count=10,
        edge_bootstrap_iterations=400,
        edge_bootstrap_confidence_level=0.9,
        highlights_bootstrap_iterations=400,
        highlights_bootstrap_confidence_level=0.9,
    )


def example_run_integrated_extension_method(
    *,
    snapshot_db: str | Path,
    scan_day: str,
    window_start_date: str,
) -> dict[str, Any]:
    """Example Method 3: integrated region filters + bootstrap CI + safety companion."""
    return run_integrated_extension_method(
        snapshot_db=snapshot_db,
        scan_day=scan_day,
        window_start_date=window_start_date,
        ranking_horizon=5,
        countries=("UNITED STATES",),
        exchanges=("NASDAQ", "NYSE"),
        shortlist_top_n=30,
        top10_count=10,
        edge_bootstrap_iterations=1000,
        edge_bootstrap_confidence_level=0.95,
        highlights_bootstrap_iterations=1000,
        highlights_bootstrap_confidence_level=0.95,
        min_balance_sheet_score=0.45,
        min_cash_generation_score=0.40,
        min_combined_score=0.50,
    )


def run_local_workflow_method(
    *,
    workflow_name: str = "latest_500m_full_edge_research",
    existing_run_ref: str | Path | None = None,
    auto_discover_latest_snapshot: bool = False,
    rebuild_foundation_snapshot: bool | None = None,
    use_full_range: bool = True,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    output_root: str | Path = OUTPUT_ROOT,
    requested_min_market_cap_usd: float = DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
    ranking_horizon: int = 5,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    screen_top_n: int = 500,
    persistence_top_n: int = 20_000,
    highlights_min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    forward_upside_top_count: int = 20,
) -> dict[str, Any]:
    """
      Deliberate parameter-driven local entrypoint.

      Edit params via ``run_edge_research_local_main()`` then run:
        PYTHONPATH='src:.' python src/run_edge_research_tools.py

      Workflows:
        - latest_500m_full_edge_research: full parent suite (foundation + aggregate)
        - build_snapshot_500m: snapshot only
        - build_full_foundation_suite_500m: snapshot + labels + vol/liq setup passes
        - daily_scan_existing_snapshot: screen + persistence + edge summary
        - full_quant_existing_snapshot: daily scan + highlights
        - historic_current_aggregate_existing_snapshot: preferred-markets integrated pass

      ``existing_run_ref`` accepts a snapshot folder, parent run folder, foundation
    folder, suite folder, or direct ``symbol_day_feature_snapshot.duckdb`` path.
    """
    resolved_run_context: dict[str, Any] | None = None
    resolved_existing_run_ref = _coalesce_existing_run_ref(
        existing_run_ref,
        output_root=output_root,
        auto_discover_latest=(
            auto_discover_latest_snapshot
            and existing_run_ref is None
            and rebuild_foundation_snapshot is not True
        ),
    )
    if resolved_existing_run_ref is not None:
        resolved_run_context = _resolve_edge_research_run_reference(
            resolved_existing_run_ref
        )

    snapshot_db = (
        Path(resolved_run_context["snapshot_db"])
        if resolved_run_context is not None
        else None
    )
    scan_day = (
        str(resolved_run_context["scan_day"])
        if resolved_run_context is not None
        else None
    )
    window_start_date = (
        str(resolved_run_context["window_start_date"])
        if resolved_run_context is not None
        else None
    )
    if resolved_run_context is not None:
        start_day_label = str(resolved_run_context["start_day_label"])
        end_day_label = str(resolved_run_context["end_day_label"])

    if workflow_name == "latest_500m_full_edge_research":
        return run_latest_500m_full_edge_research_suite(
            use_full_range=use_full_range,
            start_day_label=start_day_label,
            end_day_label=end_day_label,
            min_market_cap_usd=requested_min_market_cap_usd,
            output_root=output_root,
            rebuild_foundation_snapshot=(
                False
                if rebuild_foundation_snapshot is None
                else rebuild_foundation_snapshot
            ),
            existing_run_ref=existing_run_ref,
            auto_discover_latest_snapshot=auto_discover_latest_snapshot,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
            ranking_horizon=ranking_horizon,
            screen_top_n=screen_top_n,
            persistence_top_n=persistence_top_n,
            highlights_min_shortlist_count=highlights_min_shortlist_count,
            shortlist_top_n=shortlist_top_n,
            top10_count=top10_count,
            upside_top_count=upside_top_count,
            forward_upside_top_count=forward_upside_top_count,
        )

    if snapshot_db is None:
        raise ValueError(
            f"Workflow {workflow_name!r} requires existing_run_ref when not building "
            "a new snapshot."
        )
    if scan_day is None or window_start_date is None:
        raise ValueError(
            f"Workflow {workflow_name!r} could not resolve scan_day/window_start_date."
        )

    _ensure_snapshot_ready_for_aggregate(
        snapshot_db,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )

    if workflow_name == "build_snapshot_500m":
        if start_day_label is None or end_day_label is None:
            raise ValueError(
                "build_snapshot_500m requires start_day_label and end_day_label "
                "when existing_run_ref is not provided."
            )
        return run_snapshot_method(
            start_day_label=start_day_label,
            end_day_label=end_day_label,
            output_root=output_root,
            min_market_cap_usd=requested_min_market_cap_usd,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )

    if workflow_name == "build_full_foundation_suite_500m":
        if start_day_label is None or end_day_label is None:
            raise ValueError(
                "build_full_foundation_suite_500m requires start_day_label and "
                "end_day_label when existing_run_ref is not provided."
            )
        return run_suite_method(
            start_day_label=start_day_label,
            end_day_label=end_day_label,
            output_root=output_root,
            min_market_cap_usd=requested_min_market_cap_usd,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )

    if workflow_name == "daily_scan_existing_snapshot":
        return run_daily_scan_method(
            snapshot_db=snapshot_db,
            scan_day=scan_day,
            window_start_date=window_start_date,
            ranking_horizon=ranking_horizon,
            screen_top_n=screen_top_n,
            persistence_top_n=persistence_top_n,
            markets=PREFERRED_MARKETS,
            output_root=output_root,
            duckdb_threads=duckdb_threads,
        )

    if workflow_name == "full_quant_existing_snapshot":
        return run_full_quantitative_method(
            snapshot_db=snapshot_db,
            scan_day=scan_day,
            window_start_date=window_start_date,
            ranking_horizon=ranking_horizon,
            screen_top_n=screen_top_n,
            persistence_top_n=persistence_top_n,
            highlights_min_shortlist_count=highlights_min_shortlist_count,
            markets=PREFERRED_MARKETS,
            shortlist_top_n=shortlist_top_n,
            top10_count=top10_count,
            output_root=output_root,
            duckdb_threads=duckdb_threads,
        )

    if workflow_name == "historic_current_aggregate_existing_snapshot":
        return run_historic_current_aggregate_suite_preferred_markets(
            snapshot_db=snapshot_db,
            scan_day=scan_day,
            window_start_date=window_start_date,
            output_root=output_root,
            requested_min_market_cap_usd=requested_min_market_cap_usd,
            ranking_horizon=ranking_horizon,
            screen_top_n=screen_top_n,
            persistence_top_n=persistence_top_n,
            highlights_min_shortlist_count=highlights_min_shortlist_count,
            shortlist_top_n=shortlist_top_n,
            top10_count=top10_count,
            upside_top_count=upside_top_count,
            forward_upside_top_count=forward_upside_top_count,
            duckdb_threads=duckdb_threads,
        )

    raise ValueError(f"Unsupported local workflow_name: {workflow_name}")


def run_latest_500m_full_edge_research_suite_example() -> dict[str, Any]:
    """Run the full 500M parent suite and print a summary."""
    result = run_latest_500m_full_edge_research_suite()
    print_edge_research_parent_result(result)
    return result


def run_edge_research_local_main() -> dict[str, Any]:
    """
    Code-driven entrypoint for edge research runs.

    Edit the params below, then run this file with no CLI args.
    """
    # ===== edit params here =====
    workflow_name = "latest_500m_full_edge_research"

    # Reuse a prior run: snapshot folder, parent run folder, or .duckdb path.
    # Leave None to auto-pick the latest snapshot from foundations/ or runs/.
    # Set rebuild_foundation_snapshot=True only when you want a fresh foundation build.
    existing_run_ref: str | Path | None = None
    auto_discover_latest_snapshot = True
    rebuild_foundation_snapshot: bool | None = True

    use_full_range = True
    start_day_label: str | None = None
    end_day_label: str | None = None
    output_root = OUTPUT_ROOT
    requested_min_market_cap_usd = DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD
    ranking_horizon = 5
    duckdb_threads = 16
    memory_limit_gb = 24.0
    screen_top_n = 500
    persistence_top_n = 20_000
    highlights_min_shortlist_count = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT
    shortlist_top_n = 30
    top10_count = 20
    upside_top_count = 20
    forward_upside_top_count = 20
    # ===== end params =====

    result = run_local_workflow_method(
        workflow_name=workflow_name,
        existing_run_ref=existing_run_ref,
        auto_discover_latest_snapshot=auto_discover_latest_snapshot,
        rebuild_foundation_snapshot=rebuild_foundation_snapshot,
        use_full_range=use_full_range,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        output_root=output_root,
        requested_min_market_cap_usd=requested_min_market_cap_usd,
        ranking_horizon=ranking_horizon,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
        screen_top_n=screen_top_n,
        persistence_top_n=persistence_top_n,
        highlights_min_shortlist_count=highlights_min_shortlist_count,
        shortlist_top_n=shortlist_top_n,
        top10_count=top10_count,
        upside_top_count=upside_top_count,
        forward_upside_top_count=forward_upside_top_count,
    )
    print_edge_research_parent_result(result)
    return result


def _print_local_workflow_result(result: dict[str, Any]) -> None:
    parent_run_dir = result.get("parent_run_dir")
    output_dir = result.get("output_dir")
    manifest_path = result.get("manifest_path") or result.get("suite_manifest")
    report_path = result.get("report_md") or result.get("suite_report")

    if parent_run_dir is not None:
        print(f"Parent run output: {parent_run_dir}")
    if output_dir is not None:
        print(f"Output: {output_dir}")
    if manifest_path is not None:
        print(f"Manifest: {manifest_path}")
    if report_path is not None:
        print(f"Report: {report_path}")

    if "snapshot_db" in result:
        print(f"Snapshot database: {result['snapshot_db']}")
    if "start_day_label" in result and "end_day_label" in result:
        print(
            "Day range: "
            f"{result['start_day_label']} -> {result['end_day_label']} "
            f"(scan_day={result.get('scan_day')})"
        )
    if "min_market_cap_usd" in result:
        print(f"Min market cap USD: {result['min_market_cap_usd']}")
    if result.get("existing_run_ref"):
        print(f"Existing run reference: {result['existing_run_ref']}")
    if result.get("snapshot_prep_result"):
        prep = result["snapshot_prep_result"]
        if prep.get("labels_generated"):
            print("Snapshot prep: generated missing forward labels in place.")

    foundation = result.get("foundation")
    if isinstance(foundation, dict):
        print(f"Foundation suite output: {foundation.get('output_dir')}")
        snapshot_result = foundation.get("snapshot_result")
        if isinstance(snapshot_result, dict):
            print(
                f"Foundation snapshot database: {snapshot_result.get('database_path')}"
            )
        print(f"Foundation suite report: {foundation.get('suite_report')}")

    aggregate = result.get("aggregate")
    if isinstance(aggregate, dict):
        print(f"Aggregate parent output: {aggregate.get('parent_run_dir')}")
        lens = aggregate.get("tradeable_safety_lens")
        if isinstance(lens, dict):
            print(f"Tradeable safety lens output: {lens.get('output_dir')}")
        upside_lens = aggregate.get("upside_prediction_lens")
        if isinstance(upside_lens, dict):
            print(f"Upside prediction lens output: {upside_lens.get('output_dir')}")
        forward_upside_lens = aggregate.get("forward_upside_valuation_lens")
        if isinstance(forward_upside_lens, dict):
            print(
                "Forward upside valuation lens output: "
                f"{forward_upside_lens.get('output_dir')}"
            )
        unified_highlights = aggregate.get("edge_unified_highlights")
        if isinstance(unified_highlights, dict):
            print(
                "Unified edge highlights DuckDB: "
                f"{unified_highlights.get('database_path')}"
            )
        if "needs_new_500m_scan" in aggregate:
            print(
                "Needs new requested-threshold snapshot scan: "
                f"{aggregate['needs_new_500m_scan']}"
            )
            print(
                "Requested-threshold scan requirement reason: "
                f"{aggregate.get('scan_requirement_reason')}"
            )

    if "snapshot_result" in result:
        print(f"Snapshot database: {result['snapshot_result']['database_path']}")
    elif "database_path" in result:
        print(f"Database path: {result['database_path']}")

    if "screen" in result:
        print(f"Screen child output: {result['screen']['output_dir']}")
    if "scan_persistence" in result:
        print(f"Persistence child output: {result['scan_persistence']['output_dir']}")
    if "scan_edge" in result:
        print(f"Edge summary child output: {result['scan_edge']['output_dir']}")
    if "highlights" in result:
        print(f"Highlights child output: {result['highlights']['output_dir']}")
    if "safety_highlights" in result:
        print(
            f"Safety highlights child output: {result['safety_highlights']['output_dir']}"
        )
    if "tradeable_safety_lens" in result:
        print(
            "Tradeable safety lens child output: "
            f"{result['tradeable_safety_lens']['output_dir']}"
        )
    if "upside_prediction_lens" in result:
        print(
            "Upside prediction lens child output: "
            f"{result['upside_prediction_lens']['output_dir']}"
        )
    if "forward_upside_valuation_lens" in result:
        print(
            "Forward upside valuation lens child output: "
            f"{result['forward_upside_valuation_lens']['output_dir']}"
        )
    if "edge_unified_highlights" in result:
        print(
            "Unified edge highlights DuckDB: "
            f"{result['edge_unified_highlights']['database_path']}"
        )
    if "needs_new_500m_scan" in result:
        print(
            f"Needs new requested-threshold snapshot scan: {result['needs_new_500m_scan']}"
        )
        print(
            f"Requested-threshold scan requirement reason: {result['scan_requirement_reason']}"
        )


def print_edge_research_parent_result(result: dict[str, Any]) -> None:
    """Print a human-readable summary for parent-suite workflow results."""
    _print_local_workflow_result(result)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Thin entrypoint for the new all-fields edge research pipeline. "
            "It supports source inventory, snapshot building, forward labels, "
            "the first volatility/liquidity setup pass, and a thin end-to-end suite."
        )
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_screen_parser(subparsers)
    _add_persistence_parser(subparsers)
    _add_edge_summary_parser(subparsers)
    _add_highlights_parser(subparsers)
    _add_safety_highlights_parser(subparsers)
    _add_inspect_symbol_parser(subparsers)
    _add_historic_current_aggregate_parser(subparsers)

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="Scan reusable all-fields and scan-period research sources.",
    )
    inventory_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many latest daily databases and scan-period runs to include in samples.",
    )
    inventory_parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Optional project root override.",
    )
    inventory_parser.add_argument(
        "--all-fields-root",
        type=Path,
        default=None,
        help="Optional daily all-fields DuckDB root override.",
    )
    inventory_parser.add_argument(
        "--scan-period-runs-root",
        type=Path,
        default=None,
        help="Optional scan-period tracking runs root override.",
    )
    inventory_parser.add_argument(
        "--taxonomy-root",
        type=Path,
        default=None,
        help="Optional taxonomy root override.",
    )
    inventory_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional edge research output root override.",
    )

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Build a core symbol-day feature snapshot from daily all-fields DuckDB files.",
    )
    snapshot_parser.add_argument("--start-day-label", required=True)
    snapshot_parser.add_argument("--end-day-label", required=True)
    snapshot_parser.add_argument(
        "--all-fields-root",
        type=Path,
        default=None,
        help="Optional daily all-fields DuckDB root override.",
    )
    snapshot_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional edge research output root override.",
    )
    snapshot_parser.add_argument(
        "--taxonomy-root",
        type=Path,
        default=None,
        help="Optional taxonomy root override.",
    )
    snapshot_parser.add_argument(
        "--include-non-primary",
        action="store_true",
        help="Include non-primary symbols instead of filtering to primary rows.",
    )
    snapshot_parser.add_argument(
        "--min-market-cap-usd",
        type=float,
        default=None,
        help="Optional minimum market cap filter applied at source-row level when available.",
    )
    snapshot_parser.add_argument(
        "--duckdb-threads",
        type=int,
        default=16,
        help="DuckDB thread count for the snapshot build (default: 16).",
    )
    snapshot_parser.add_argument(
        "--memory-limit-gb",
        type=float,
        default=24.0,
        help="DuckDB memory limit in GB for the snapshot build (default: 24.0).",
    )

    labels_parser = subparsers.add_parser(
        "labels",
        help="Generate forward labels inside an existing symbol-day snapshot database.",
    )
    labels_parser.add_argument("--snapshot-db", type=Path, required=True)
    labels_parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=list(DEFAULT_FORWARD_LABEL_HORIZONS),
        help="Forward label horizons in trading days.",
    )
    labels_parser.add_argument(
        "--target-pct",
        type=float,
        default=10.0,
        help="Target threshold used for target-before-stop diagnostics.",
    )
    labels_parser.add_argument(
        "--stop-pct",
        type=float,
        default=7.0,
        help="Stop threshold used for target-before-stop diagnostics.",
    )
    labels_parser.add_argument(
        "--duckdb-threads",
        type=int,
        default=16,
        help="DuckDB thread count for the label build (default: 16).",
    )
    labels_parser.add_argument(
        "--memory-limit-gb",
        type=float,
        default=24.0,
        help="DuckDB memory limit in GB for the label build (default: 24.0).",
    )

    setup_parser = subparsers.add_parser(
        "setup-vol-liq",
        help="Run the first interpretable volatility/liquidity setup pass on a labeled snapshot database.",
    )
    setup_parser.add_argument("--snapshot-db", type=Path, required=True)
    setup_parser.add_argument(
        "--group-by",
        default="universe",
        help="Grouping scope: universe, sector, industry, exchange, country, custom_group, or symbol.",
    )
    setup_parser.add_argument(
        "--group-values",
        default=None,
        help="Optional comma-separated group filter values.",
    )
    setup_parser.add_argument(
        "--symbols",
        default=None,
        help="Optional comma-separated symbol list for peer or single-name runs.",
    )
    setup_parser.add_argument(
        "--custom-group-csv",
        type=Path,
        default=None,
        help="Optional CSV with columns symbol and group_name for custom lanes.",
    )
    setup_parser.add_argument(
        "--ranking-horizon",
        type=int,
        default=None,
        help="Optional horizon used to sort grouped summaries and best-name rankings.",
    )
    setup_parser.add_argument(
        "--min-occurrence-count",
        type=int,
        default=10,
        help="Minimum grouped occurrence count kept in the summary output.",
    )
    setup_parser.add_argument(
        "--min-symbol-occurrence-count",
        type=int,
        default=3,
        help="Minimum symbol occurrence count kept in the best-names output.",
    )
    setup_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional edge research output root override.",
    )

    suite_parser = subparsers.add_parser(
        "suite",
        help="Run snapshot, labels, and the first setup passes in sequence.",
    )
    suite_parser.add_argument("--start-day-label", default=None)
    suite_parser.add_argument("--end-day-label", default=None)
    suite_parser.add_argument(
        "--use-full-range",
        action="store_true",
        help="Use the oldest-to-latest available daily all-fields range automatically.",
    )
    suite_parser.add_argument(
        "--all-fields-root",
        type=Path,
        default=None,
        help="Optional daily all-fields DuckDB root override.",
    )
    suite_parser.add_argument(
        "--taxonomy-root",
        type=Path,
        default=None,
        help="Optional taxonomy root override.",
    )
    suite_parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional edge research output root override.",
    )
    suite_parser.add_argument(
        "--include-non-primary",
        action="store_true",
        help="Include non-primary symbols instead of filtering to primary rows.",
    )
    suite_parser.add_argument(
        "--min-market-cap-usd",
        type=float,
        default=None,
        help="Optional minimum market cap filter applied during snapshot building.",
    )
    suite_parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=list(DEFAULT_FORWARD_LABEL_HORIZONS),
        help="Forward label horizons in trading days.",
    )
    suite_parser.add_argument(
        "--target-pct",
        type=float,
        default=10.0,
        help="Target threshold used for target-before-stop diagnostics.",
    )
    suite_parser.add_argument(
        "--stop-pct",
        type=float,
        default=7.0,
        help="Stop threshold used for target-before-stop diagnostics.",
    )
    suite_parser.add_argument(
        "--ranking-horizon",
        type=int,
        default=None,
        help="Optional horizon used to sort grouped summaries and best-name rankings.",
    )
    suite_parser.add_argument(
        "--groupings",
        default="universe,industry",
        help="Comma-separated setup grouping passes to run after labels.",
    )
    suite_parser.add_argument(
        "--duckdb-threads",
        type=int,
        default=16,
        help="DuckDB thread count passed to snapshot and label builds (default: 16).",
    )
    suite_parser.add_argument(
        "--memory-limit-gb",
        type=float,
        default=24.0,
        help="DuckDB memory limit in GB passed to snapshot and label builds (default: 24.0).",
    )
    return parser


def _add_screen_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "screen",
        help="Point-in-time setup screener: rank all symbols for a given date (default = latest).",
    )
    p.add_argument("--snapshot-db", type=Path, required=True)
    p.add_argument(
        "--date",
        default=None,
        help="ISO date YYYY-MM-DD. Defaults to the latest date in the snapshot database.",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=50,
        help="Maximum number of candidates to return (default: 50).",
    )
    p.add_argument(
        "--min-composite",
        type=float,
        default=0.55,
        help="Minimum composite score threshold. Also shows any symbol in a variant regardless. (default: 0.55)",
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=None,
        help="Horizon used to rank historical in-setup performance. Defaults to longest available.",
    )
    p.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated country filters.",
    )
    p.add_argument(
        "--exchanges",
        default=None,
        help="Optional comma-separated exchange filters.",
    )
    p.add_argument(
        "--us-only",
        action="store_true",
        help="Convenience filter for US-only names.",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def _add_persistence_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "scan-persistence",
        help="Cross-day persistence scan: which symbols appeared in setup conditions across the most days?",
    )
    p.add_argument("--snapshot-db", type=Path, required=True)
    p.add_argument(
        "--min-setup-days",
        type=int,
        default=5,
        help="Minimum number of setup days required to appear in output (default: 5).",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=100,
        help="Maximum number of symbols to return (default: 100).",
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=None,
        help="Horizon used for in-setup performance display. Defaults to longest available.",
    )
    p.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated country filters.",
    )
    p.add_argument(
        "--exchanges",
        default=None,
        help="Optional comma-separated exchange filters.",
    )
    p.add_argument(
        "--us-only",
        action="store_true",
        help="Convenience filter for US-only names.",
    )
    p.add_argument(
        "--start-date",
        default=None,
        help="Optional ISO start date YYYY-MM-DD for persistence window.",
    )
    p.add_argument(
        "--end-date",
        default=None,
        help="Optional ISO end date YYYY-MM-DD for persistence window.",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def _add_edge_summary_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "scan-edge",
        help="Edge summary: forward-return distribution for each setup variant grouped by sector or industry.",
    )
    p.add_argument("--snapshot-db", type=Path, required=True)
    p.add_argument(
        "--group-by",
        default="industry",
        help="Group dimension: industry (default), sector, exchange, or country.",
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=None,
        help="Horizon used for sorting within each group. Defaults to longest available.",
    )
    p.add_argument(
        "--min-occurrence-count",
        type=int,
        default=5,
        help="Minimum setup occurrences for a group to appear in output (default: 5).",
    )
    p.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated country filters.",
    )
    p.add_argument(
        "--exchanges",
        default=None,
        help="Optional comma-separated exchange filters.",
    )
    p.add_argument(
        "--us-only",
        action="store_true",
        help="Convenience filter for US-only names.",
    )
    p.add_argument(
        "--start-date",
        default=None,
        help="Optional ISO start date YYYY-MM-DD for the summary window.",
    )
    p.add_argument(
        "--end-date",
        default=None,
        help="Optional ISO end date YYYY-MM-DD for the summary window.",
    )
    p.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=400,
        help="Bootstrap resamples for lane median confidence intervals (default: 400).",
    )
    p.add_argument(
        "--bootstrap-confidence-level",
        type=float,
        default=0.9,
        help="Bootstrap CI confidence level in (0,1) for lane medians (default: 0.9).",
    )
    p.add_argument(
        "--bootstrap-seed",
        type=int,
        default=17,
        help="Bootstrap RNG seed for reproducible lane confidence intervals (default: 17).",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def _add_highlights_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "highlights",
        help="Daily highlights: strongest lanes, full shortlist, top 30 big movers, top confidence, and upside prediction focus.",
    )
    p.add_argument(
        "--snapshot-db",
        type=Path,
        default=None,
        help="Optional snapshot database path. Defaults to the latest edge_feature_snapshot database.",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Optional ISO date YYYY-MM-DD. Defaults to the latest date within the selected region scope.",
    )
    p.add_argument(
        "--requested-min-market-cap-usd",
        type=float,
        default=DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
        help=(
            "Requested minimum market-cap threshold for compatibility checks against the existing snapshot "
            f"(default: {int(DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD)})."
        ),
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=5,
        help="Horizon used for lane and history scoring (default: 5).",
    )
    p.add_argument(
        "--lane-setup-name",
        default="adrp_relvol_core",
        help="Setup variant used for lane selection (default: adrp_relvol_core).",
    )
    p.add_argument(
        "--group-by",
        default="industry",
        help="Lane grouping dimension: industry (default), sector, exchange, or country.",
    )
    p.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated country filters applied before lane and name selection.",
    )
    p.add_argument(
        "--exchanges",
        default=None,
        help="Optional comma-separated exchange filters applied before lane and name selection.",
    )
    p.add_argument(
        "--us-only",
        action="store_true",
        help="Convenience filter for US-only names.",
    )
    p.add_argument(
        "--screen-min-composite",
        type=float,
        default=0.45,
        help="Minimum current-state composite considered before setup-flag override (default: 0.45).",
    )
    p.add_argument(
        "--lane-min-sample-count",
        type=int,
        default=20,
        help="Minimum sample count for the selected horizon when keeping lane leaders (default: 20).",
    )
    p.add_argument(
        "--lane-min-win-rate",
        type=float,
        default=0.55,
        help="Minimum lane win rate at the ranking horizon (default: 0.55).",
    )
    p.add_argument(
        "--lane-min-median-fwd",
        type=float,
        default=1.0,
        help="Minimum lane median forward return at the ranking horizon (default: 1.0).",
    )
    p.add_argument(
        "--min-any-setup-rate",
        type=float,
        default=0.03,
        help="Minimum recurrence rate across the full dataset window (default: 0.03).",
    )
    p.add_argument(
        "--min-hist-occurrences",
        type=int,
        default=2,
        help="Minimum historical in-setup occurrences for shortlist inclusion (default: 2).",
    )
    p.add_argument(
        "--min-hist-win-rate",
        type=float,
        default=0.45,
        help="Minimum historical in-setup win rate for shortlist inclusion (default: 0.45).",
    )
    p.add_argument(
        "--min-hist-median-fwd",
        type=float,
        default=0.0,
        help="Minimum historical in-setup median forward return for shortlist inclusion (default: 0.0).",
    )
    p.add_argument(
        "--min-shortlist-count",
        type=int,
        default=DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
        help=(
            "Minimum highlights shortlist row count target. If strict durability-pass names "
            "are fewer, expanded momentum candidates are added to reach this breadth "
            f"(default: {DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT})."
        ),
    )
    p.add_argument(
        "--shortlist-top-n",
        type=int,
        default=30,
        help="Number of daily big-mover focus names to keep in the top30 output (default: 30).",
    )
    p.add_argument(
        "--top10-count",
        type=int,
        default=10,
        help="Number of confidence-ranked names to keep in the quick screen output (default: 10).",
    )
    p.add_argument(
        "--upside-top-count",
        type=int,
        default=20,
        help="Number of upside-prediction-ranked names to keep in the focused upside output (default: 20).",
    )
    p.add_argument(
        "--stability-short-lookback",
        type=int,
        default=10,
        help="Short rolling window used for stability scoring (default: 10).",
    )
    p.add_argument(
        "--stability-long-lookback",
        type=int,
        default=20,
        help="Long rolling window used for stability scoring (default: 20).",
    )
    p.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=400,
        help="Bootstrap resamples for lane median confidence intervals (default: 400).",
    )
    p.add_argument(
        "--bootstrap-confidence-level",
        type=float,
        default=0.9,
        help="Bootstrap CI confidence level in (0,1) for lane medians (default: 0.9).",
    )
    p.add_argument(
        "--bootstrap-seed",
        type=int,
        default=31,
        help="Bootstrap RNG seed for reproducible lane confidence intervals (default: 31).",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def _add_safety_highlights_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "safety-highlights",
        help="Companion safety highlights: balance-sheet + cash-generation/value scoring with indicator columns.",
    )
    p.add_argument(
        "--snapshot-db",
        type=Path,
        default=None,
        help="Optional snapshot database path. Defaults to latest edge_feature_snapshot database.",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Optional ISO date YYYY-MM-DD. Defaults to latest date within selected region scope.",
    )
    p.add_argument(
        "--group-by",
        default="industry",
        help="Grouping dimension: industry (default), sector, exchange, or country.",
    )
    p.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated country filters.",
    )
    p.add_argument(
        "--exchanges",
        default=None,
        help="Optional comma-separated exchange filters.",
    )
    p.add_argument(
        "--us-only",
        action="store_true",
        help="Convenience filter for US-only names.",
    )
    p.add_argument(
        "--min-balance-sheet-score",
        type=float,
        default=0.45,
        help="Minimum balance_sheet_safety_score threshold (default: 0.45).",
    )
    p.add_argument(
        "--min-cash-generation-score",
        type=float,
        default=0.40,
        help="Minimum cash_generation_value_score threshold (default: 0.40).",
    )
    p.add_argument(
        "--min-combined-score",
        type=float,
        default=0.50,
        help="Minimum blended safety_companion_score threshold (default: 0.50).",
    )
    p.add_argument(
        "--shortlist-top-n",
        type=int,
        default=30,
        help="Number of names to keep in top30 output (default: 30).",
    )
    p.add_argument(
        "--top10-count",
        type=int,
        default=10,
        help="Number of names to keep in focus top10 output (default: 10).",
    )
    p.add_argument(
        "--group-min-count",
        type=int,
        default=3,
        help="Minimum shortlist count required to show a group in group summary (default: 3).",
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=5,
        help="Compatibility parameter for run-method parity. Included for command symmetry (default: 5).",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def _add_inspect_symbol_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "inspect-symbol",
        help="Inspect one symbol across aggregate edge-research outputs using EXCHANGE:TICKER-first matching.",
    )
    p.add_argument(
        "--symbol",
        required=True,
        help="Symbol to inspect. EXCHANGE:TICKER is preferred when available (example: NASDAQ:WDC).",
    )
    p.add_argument(
        "--run-ref",
        type=Path,
        default=None,
        help="Optional parent run directory, child output directory, or artifact path. Defaults to latest parent run when auto-discovery is enabled.",
    )
    p.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional edge research output root override for latest parent-run discovery.",
    )
    p.add_argument(
        "--no-auto-discover-latest",
        action="store_true",
        help="Disable automatic discovery of the latest parent run when --run-ref is omitted.",
    )


def _add_historic_current_aggregate_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    p = subparsers.add_parser(
        "suite-historic-current-aggregate",
        help=(
            "Run grouped parent-run suite for preferred markets with full historical current aggregate analysis."
        ),
    )
    p.add_argument("--snapshot-db", type=Path, required=True)
    p.add_argument(
        "--scan-day",
        required=True,
        help="ISO date YYYY-MM-DD used for point-in-time runs.",
    )
    p.add_argument(
        "--window-start-date",
        required=True,
        help="ISO date YYYY-MM-DD used as start for persistence/edge windows.",
    )
    p.add_argument(
        "--requested-min-market-cap-usd",
        type=float,
        default=DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
        help=(
            "Requested minimum market-cap threshold used to validate whether the existing snapshot covers the intended universe "
            f"(default: {int(DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD)})."
        ),
    )
    p.add_argument(
        "--ranking-horizon",
        type=int,
        default=5,
    )
    p.add_argument(
        "--screen-top-n",
        type=int,
        default=500,
    )
    p.add_argument(
        "--persistence-top-n",
        type=int,
        default=20000,
    )
    p.add_argument(
        "--min-shortlist-count",
        type=int,
        default=DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
        help=(
            "Minimum highlights shortlist row count target. If strict durability-pass names "
            "are fewer, expanded momentum candidates are added to reach this breadth "
            f"(default: {DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT})."
        ),
    )
    p.add_argument(
        "--shortlist-top-n",
        type=int,
        default=30,
    )
    p.add_argument(
        "--top10-count",
        type=int,
        default=10,
    )
    p.add_argument(
        "--upside-top-count",
        type=int,
        default=20,
        help="Number of upside-prediction-ranked names to keep in focused output (default: 20).",
    )
    p.add_argument(
        "--forward-upside-top-count",
        type=int,
        default=20,
        help="Number of forward-upside-valuation-ranked names to keep in focused output (default: 20).",
    )
    p.add_argument("--output-root", type=Path, default=None)
    p.add_argument("--duckdb-threads", type=int, default=16)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "inventory"

    if command == "inventory":
        result = run_inventory_method(
            limit=max(1, int(getattr(args, "limit", 10))),
            project_root=getattr(args, "project_root", None),
            all_fields_root=getattr(args, "all_fields_root", None),
            scan_period_runs_root=getattr(args, "scan_period_runs_root", None),
            taxonomy_root=getattr(args, "taxonomy_root", None),
            output_root=getattr(args, "output_root", None),
        )
        print(f"Edge research inventory output: {result['output_dir']}")
        print(f"Inventory report: {result['report_md']}")
        print(f"Inventory manifest: {result['manifest_path']}")
        print(f"Daily database count: {result['daily_database_count']}")
        print(f"Latest day label: {result['latest_day_label']}")
        print(f"Scan-period run count: {result['scan_period_run_count']}")
        return 0

    if command == "snapshot":
        result = run_snapshot_method(
            start_day_label=str(args.start_day_label),
            end_day_label=str(args.end_day_label),
            all_fields_root=getattr(args, "all_fields_root", None),
            taxonomy_root=getattr(args, "taxonomy_root", None),
            output_root=getattr(args, "output_root", None),
            include_non_primary=bool(getattr(args, "include_non_primary", False)),
            min_market_cap_usd=getattr(args, "min_market_cap_usd", None),
            duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
            memory_limit_gb=float(getattr(args, "memory_limit_gb", 24.0)),
        )
        print(f"Feature snapshot output: {result['output_dir']}")
        print(f"Feature snapshot database: {result['database_path']}")
        print(f"Feature snapshot report: {result['report_md']}")
        print(f"Feature snapshot manifest: {result['manifest_path']}")
        print(f"Feature snapshot rows: {result['row_count']}")
        print(f"Feature value rows: {result['feature_value_row_count']}")
        print(f"Sleeve score rows: {result['sleeve_score_row_count']}")
        return 0

    if command == "labels":
        result = run_labels_method(
            snapshot_db=getattr(args, "snapshot_db"),
            horizons=getattr(args, "horizons", DEFAULT_FORWARD_LABEL_HORIZONS),
            target_pct=float(getattr(args, "target_pct", 10.0)),
            stop_pct=float(getattr(args, "stop_pct", 7.0)),
            duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
            memory_limit_gb=float(getattr(args, "memory_limit_gb", 24.0)),
        )
        print(f"Forward label report: {result['report_md']}")
        print(f"Forward label manifest: {result['manifest_path']}")
        print(f"Forward label rows: {result['label_row_count']}")
        print(f"Forward label summary csv: {result['summary_csv_path']}")
        return 0

    if command == "setup-vol-liq":
        result = run_setup_vol_liq_method(
            snapshot_db=getattr(args, "snapshot_db"),
            group_by=str(getattr(args, "group_by", "universe")),
            group_values=_split_csv_values(getattr(args, "group_values", None)),
            symbols=_split_csv_values(getattr(args, "symbols", None)),
            custom_group_csv=getattr(args, "custom_group_csv", None),
            ranking_horizon=getattr(args, "ranking_horizon", None),
            min_occurrence_count=int(getattr(args, "min_occurrence_count", 10)),
            min_symbol_occurrence_count=int(
                getattr(args, "min_symbol_occurrence_count", 3)
            ),
            output_root=getattr(args, "output_root", None),
        )
        print(f"Setup pass output: {result['output_dir']}")
        print(f"Setup pass report: {result['report_md']}")
        print(f"Setup summary csv: {result['summary_csv_path']}")
        print(f"Best names csv: {result['best_names_csv_path']}")
        print(f"Occurrence rows: {result['occurrence_row_count']}")
        print(f"Summary rows: {result['summary_row_count']}")
        print(f"Best name rows: {result['best_name_row_count']}")
        return 0

    if command == "suite":
        result = run_suite_method(
            start_day_label=getattr(args, "start_day_label", None),
            end_day_label=getattr(args, "end_day_label", None),
            use_full_range=bool(getattr(args, "use_full_range", False)),
            all_fields_root=getattr(args, "all_fields_root", None),
            taxonomy_root=getattr(args, "taxonomy_root", None),
            output_root=getattr(args, "output_root", None),
            include_non_primary=bool(getattr(args, "include_non_primary", False)),
            min_market_cap_usd=getattr(args, "min_market_cap_usd", None),
            horizons=getattr(args, "horizons", DEFAULT_FORWARD_LABEL_HORIZONS),
            target_pct=float(getattr(args, "target_pct", 10.0)),
            stop_pct=float(getattr(args, "stop_pct", 7.0)),
            ranking_horizon=getattr(args, "ranking_horizon", None),
            groupings=(
                _split_csv_values(getattr(args, "groupings", "universe,industry"))
                or ["universe", "industry"]
            ),
            duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
            memory_limit_gb=float(getattr(args, "memory_limit_gb", 24.0)),
        )
        print(f"Suite output: {result['output_dir']}")
        print(f"Suite report: {result['suite_report']}")
        print(f"Suite snapshot database: {result['snapshot_result']['database_path']}")
        return 0

    if command == "screen":
        return _handle_screen(args)

    if command == "scan-persistence":
        return _handle_persistence(args)

    if command == "scan-edge":
        return _handle_edge_summary(args)

    if command == "highlights":
        return _handle_highlights(args)

    if command == "safety-highlights":
        return _handle_safety_highlights(args)

    if command == "inspect-symbol":
        return _handle_inspect_symbol(args)

    if command == "suite-historic-current-aggregate":
        return _handle_historic_current_aggregate_suite(args)

    parser.error(f"Unsupported command: {command}")
    return 2


def _handle_screen(args: argparse.Namespace) -> int:
    result = run_screen_method(
        snapshot_db=getattr(args, "snapshot_db"),
        date=getattr(args, "date", None),
        top_n=int(getattr(args, "top_n", 50)),
        min_composite=float(getattr(args, "min_composite", 0.55)),
        ranking_horizon=getattr(args, "ranking_horizon", None),
        countries=_split_csv_values(getattr(args, "countries", None)),
        exchanges=_split_csv_values(getattr(args, "exchanges", None)),
        us_only=bool(getattr(args, "us_only", False)),
        output_root=getattr(args, "output_root", None),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Screen output: {result['output_dir']}")
    print(f"Screen report: {result['report_md']}")
    print(f"Ranked candidates CSV: {result['ranked_csv']}")
    print(f"Screen date: {result['target_date']}")
    print(f"Candidates shown: {result['candidate_count']}")
    return 0


def _handle_persistence(args: argparse.Namespace) -> int:
    result = run_persistence_method(
        snapshot_db=getattr(args, "snapshot_db"),
        min_setup_days=int(getattr(args, "min_setup_days", 5)),
        top_n=int(getattr(args, "top_n", 100)),
        ranking_horizon=getattr(args, "ranking_horizon", None),
        countries=_split_csv_values(getattr(args, "countries", None)),
        exchanges=_split_csv_values(getattr(args, "exchanges", None)),
        us_only=bool(getattr(args, "us_only", False)),
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        output_root=getattr(args, "output_root", None),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Persistence scan output: {result['output_dir']}")
    print(f"Persistence scan report: {result['report_md']}")
    print(f"Persistence CSV: {result['persistence_csv']}")
    print(f"Total dataset days: {result['total_days']}")
    print(f"Symbols shown: {result['row_count']}")
    return 0


def _handle_edge_summary(args: argparse.Namespace) -> int:
    result = run_edge_summary_method(
        snapshot_db=getattr(args, "snapshot_db"),
        group_by=str(getattr(args, "group_by", "industry")),
        ranking_horizon=getattr(args, "ranking_horizon", None),
        min_occurrence_count=int(getattr(args, "min_occurrence_count", 5)),
        countries=_split_csv_values(getattr(args, "countries", None)),
        exchanges=_split_csv_values(getattr(args, "exchanges", None)),
        us_only=bool(getattr(args, "us_only", False)),
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        bootstrap_iterations=int(getattr(args, "bootstrap_iterations", 400)),
        bootstrap_confidence_level=float(
            getattr(args, "bootstrap_confidence_level", 0.9)
        ),
        bootstrap_seed=int(getattr(args, "bootstrap_seed", 17)),
        output_root=getattr(args, "output_root", None),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Edge summary output: {result['output_dir']}")
    print(f"Edge summary report: {result['report_md']}")
    print(f"Edge summary CSV: {result['summary_csv']}")
    print(f"Group by: {result['group_by']}")
    print(f"Group rows: {result['row_count']}")
    return 0


def _handle_highlights(args: argparse.Namespace) -> int:
    result = run_highlights_method(
        snapshot_db=getattr(args, "snapshot_db", None),
        date=getattr(args, "date", None),
        ranking_horizon=int(getattr(args, "ranking_horizon", 5)),
        lane_setup_name=str(getattr(args, "lane_setup_name", "adrp_relvol_core")),
        group_by=str(getattr(args, "group_by", "industry")),
        countries=_split_csv_values(getattr(args, "countries", None)),
        exchanges=_split_csv_values(getattr(args, "exchanges", None)),
        us_only=bool(getattr(args, "us_only", False)),
        screen_min_composite=float(getattr(args, "screen_min_composite", 0.45)),
        lane_min_sample_count=int(getattr(args, "lane_min_sample_count", 20)),
        lane_min_win_rate=float(getattr(args, "lane_min_win_rate", 0.55)),
        lane_min_median_fwd=float(getattr(args, "lane_min_median_fwd", 1.0)),
        min_any_setup_rate=float(getattr(args, "min_any_setup_rate", 0.03)),
        min_hist_occurrences=int(getattr(args, "min_hist_occurrences", 2)),
        min_hist_win_rate=float(getattr(args, "min_hist_win_rate", 0.45)),
        min_hist_median_fwd=float(getattr(args, "min_hist_median_fwd", 0.0)),
        min_shortlist_count=int(
            getattr(
                args,
                "min_shortlist_count",
                DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
            )
        ),
        shortlist_top_n=int(getattr(args, "shortlist_top_n", 30)),
        top10_count=int(getattr(args, "top10_count", 10)),
        upside_top_count=int(getattr(args, "upside_top_count", 20)),
        stability_short_lookback=int(getattr(args, "stability_short_lookback", 10)),
        stability_long_lookback=int(getattr(args, "stability_long_lookback", 20)),
        bootstrap_iterations=int(getattr(args, "bootstrap_iterations", 400)),
        bootstrap_confidence_level=float(
            getattr(args, "bootstrap_confidence_level", 0.9)
        ),
        bootstrap_seed=int(getattr(args, "bootstrap_seed", 31)),
        output_root=getattr(args, "output_root", None),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Highlights output: {result['output_dir']}")
    print(f"Highlights report: {result['report_md']}")
    print(f"Lane leaders CSV: {result['lane_leaders_csv']}")
    print(f"Shortlist CSV: {result['shortlist_csv']}")
    print(f"Top 30 CSV: {result['top30_csv']}")
    print(f"Top 10 confidence CSV: {result['top10_csv']}")
    print(f"Upside prediction ranked CSV: {result['upside_ranked_csv']}")
    print(f"Upside prediction top focus CSV: {result['upside_top_csv']}")
    print(f"Highlights date: {result['target_date']}")
    print(f"Lane rows: {result['lane_row_count']}")
    print(f"Shortlist rows: {result['shortlist_row_count']}")
    print(f"Top 30 rows: {result['top30_row_count']}")
    print(f"Top 10 rows: {result['top10_row_count']}")
    if "strict_shortlist_row_count" in result:
        print(f"Strict shortlist rows: {result['strict_shortlist_row_count']}")
    if "expanded_added_row_count" in result:
        print(f"Expanded shortlist rows added: {result['expanded_added_row_count']}")
    return 0


def _handle_safety_highlights(args: argparse.Namespace) -> int:
    result = run_safety_highlights_method(
        snapshot_db=getattr(args, "snapshot_db", None),
        date=getattr(args, "date", None),
        group_by=str(getattr(args, "group_by", "industry")),
        countries=_split_csv_values(getattr(args, "countries", None)),
        exchanges=_split_csv_values(getattr(args, "exchanges", None)),
        us_only=bool(getattr(args, "us_only", False)),
        min_balance_sheet_score=float(getattr(args, "min_balance_sheet_score", 0.45)),
        min_cash_generation_score=float(
            getattr(args, "min_cash_generation_score", 0.40)
        ),
        min_combined_score=float(getattr(args, "min_combined_score", 0.50)),
        shortlist_top_n=int(getattr(args, "shortlist_top_n", 30)),
        top10_count=int(getattr(args, "top10_count", 10)),
        group_min_count=int(getattr(args, "group_min_count", 3)),
        output_root=getattr(args, "output_root", None),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Safety highlights output: {result['output_dir']}")
    print(f"Safety highlights report: {result['report_md']}")
    print(f"Safety group summary CSV: {result['group_summary_csv']}")
    print(f"Safety shortlist CSV: {result['shortlist_csv']}")
    print(f"Safety top30 CSV: {result['top30_csv']}")
    print(f"Safety top10 focus CSV: {result['top10_csv']}")
    print(f"Safety date: {result['target_date']}")
    print(f"Group rows: {result['group_row_count']}")
    print(f"Shortlist rows: {result['shortlist_row_count']}")
    print(f"Top 30 rows: {result['top30_row_count']}")
    print(f"Top 10 rows: {result['top10_row_count']}")
    return 0


def _handle_inspect_symbol(args: argparse.Namespace) -> int:
    result = run_edge_symbol_inspection_method(
        symbol=str(getattr(args, "symbol")),
        run_ref=getattr(args, "run_ref", None),
        output_root=getattr(args, "output_root", None),
        auto_discover_latest=not bool(getattr(args, "no_auto_discover_latest", False)),
    )
    _print_edge_symbol_inspection(result)
    return 0


def _handle_historic_current_aggregate_suite(args: argparse.Namespace) -> int:
    result = run_historic_current_aggregate_suite_preferred_markets(
        snapshot_db=getattr(args, "snapshot_db"),
        scan_day=str(getattr(args, "scan_day")),
        window_start_date=str(getattr(args, "window_start_date")),
        output_root=getattr(args, "output_root", None),
        requested_min_market_cap_usd=float(
            getattr(
                args,
                "requested_min_market_cap_usd",
                DEFAULT_FULL_SCAN_MIN_MARKET_CAP_USD,
            )
        ),
        ranking_horizon=int(getattr(args, "ranking_horizon", 5)),
        screen_top_n=int(getattr(args, "screen_top_n", 500)),
        persistence_top_n=int(getattr(args, "persistence_top_n", 20000)),
        highlights_min_shortlist_count=int(
            getattr(
                args,
                "min_shortlist_count",
                DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
            )
        ),
        shortlist_top_n=int(getattr(args, "shortlist_top_n", 30)),
        top10_count=int(getattr(args, "top10_count", 10)),
        upside_top_count=int(getattr(args, "upside_top_count", 20)),
        forward_upside_top_count=int(getattr(args, "forward_upside_top_count", 20)),
        duckdb_threads=int(getattr(args, "duckdb_threads", 16)),
    )
    print(f"Parent suite run output: {result['parent_run_dir']}")
    print(f"Parent suite manifest: {result['manifest_path']}")
    print(f"Screen child output: {result['screen']['output_dir']}")
    print(f"Persistence child output: {result['scan_persistence']['output_dir']}")
    print(f"Edge summary child output: {result['scan_edge']['output_dir']}")
    print(f"Highlights child output: {result['highlights']['output_dir']}")
    print(
        f"Safety highlights child output: {result['safety_highlights']['output_dir']}"
    )
    if "upside_prediction_lens" in result:
        print(
            "Upside prediction lens child output: "
            f"{result['upside_prediction_lens']['output_dir']}"
        )
    if "forward_upside_valuation_lens" in result:
        print(
            "Forward upside valuation lens child output: "
            f"{result['forward_upside_valuation_lens']['output_dir']}"
        )
    if "edge_unified_highlights" in result:
        print(
            "Unified edge highlights DuckDB: "
            f"{result['edge_unified_highlights']['database_path']}"
        )
    print(f"Requested min market-cap: {result['requested_min_market_cap_usd']}")
    if "highlights_min_shortlist_count" in result:
        print(
            "Highlights minimum shortlist count: "
            f"{result['highlights_min_shortlist_count']}"
        )
    print(
        f"Detected snapshot min market-cap: {result['detected_snapshot_min_market_cap_usd']}"
    )
    print(
        f"Needs new requested-threshold snapshot scan: {result['needs_new_500m_scan']}"
    )
    print(
        f"Requested-threshold scan requirement reason: {result['scan_requirement_reason']}"
    )
    return 0


if __name__ == "__main__":
    import sys as _sys

    # Code-driven runs: edit workflow_name / params above, then:
    #   PYTHONPATH='src:.' python src/run_edge_research_tools.py
    #   $env:PYTHONPATH='src;.' ; python src/run_edge_research_tools.py
    if len(_sys.argv) == 1:
        run_edge_research_local_main()
        raise SystemExit(0)

    raise SystemExit(main())
