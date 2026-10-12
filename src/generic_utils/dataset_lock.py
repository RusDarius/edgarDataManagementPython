"""Lock the six operator datasets into one queryable folder.

Rows only. No eligibility cutoff and no 0–100. The briefing pack already
carries prediction, all-fields, edge, and market-timing on each name.
This folder records where those files are, plus the latest backtest
family IC and the latest value-projection dumps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKTEST_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "backtests" / "runs"
)

# dataset id, what the model uses it for, pack sources key for the file
_PACK_DATASETS: tuple[tuple[str, str, str, str], ...] = (
    (
        "all_fields",
        "TradingView all-fields tape (close, multiples, RSI, ATR, street PT)",
        "all_fields_db",
        "all_fields_run_id",
    ),
    (
        "prediction",
        "Move-prediction weeks scores (bo, cont, fwd) already on the pack tape",
        "prediction_db",
        "prediction_run_id",
    ),
    (
        "edge",
        "Edge research upside. Pack leftover is already max(street PT, edge upside)",
        "edge_opportunity_csv",
        "edge_parent_dir",
    ),
    (
        "mtp",
        "Market-timing policy. Climate on the pack row. Not a leftover gate",
        "mtp_policy_csv",
        "mtp_dir",
    ),
)


def latest_family_metrics(root: str | Path | None = None) -> Path | None:
    """Newest family_metrics.csv under backtests/runs. Does not rerun a backtest."""
    base = Path(root) if root else DEFAULT_BACKTEST_ROOT
    if not base.exists():
        return None
    matches = [
        path
        for path in base.glob("backtests_*/family_metrics.csv")
        if path.is_file()
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def dataset_rows(
    pack: Mapping[str, Any] | None,
    *,
    finproj_db: str | Path | None = None,
    fingrowth_db: str | Path | None = None,
    family_metrics: str | Path | None = None,
) -> list[dict[str, str]]:
    """One row per dataset. Empty path means that dump is not on disk."""
    sources = {}
    if isinstance(pack, Mapping):
        raw = pack.get("sources")
        sources = dict(raw) if isinstance(raw, Mapping) else {}
    rows: list[dict[str, str]] = []
    for dataset, use, path_key, run_key in _PACK_DATASETS:
        path = sources.get(path_key) or ""
        run_id = sources.get(run_key) or ""
        rows.append(
            {
                "dataset": dataset,
                "use": use,
                "path": str(path or ""),
                "run_id": str(run_id or ""),
            }
        )
    metrics = str(family_metrics or "")
    rows.append(
        {
            "dataset": "backtest",
            "use": "Family IC on family_metrics. Rank with rank_cli --field spearman_ic. Not a 0-100 and not a buy list",
            "path": metrics,
            "run_id": Path(metrics).parent.name if metrics else "",
        }
    )
    projection_bits = [str(path) for path in (finproj_db, fingrowth_db) if path]
    rows.append(
        {
            "dataset": "value_projection",
            "use": "financial_projection terminals, growth CAGRs, and forward PE / EV/EBITDA / debt / FCF (fp_pe_y1, fp_pe_y5, fp_evebitda_y1, fp_net_debt_y1, fp_fcf_y1). pe_fwd_tv stays empty",
            "path": " | ".join(projection_bits),
            "run_id": "",
        }
    )
    return rows


def export_dataset_lock(
    out_dir: str | Path,
    pack: Mapping[str, Any] | None = None,
    *,
    finproj_db: str | Path | None = None,
    fingrowth_db: str | Path | None = None,
    backtest_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write datasets.duckdb. Includes family_metrics when a backtest run exists."""
    from generic_utils.run_export import write_run_export
    from generic_utils.scan_sources import rows_from_csv

    metrics_path = latest_family_metrics(backtest_root)
    rows = dataset_rows(
        pack,
        finproj_db=finproj_db,
        fingrowth_db=fingrowth_db,
        family_metrics=metrics_path,
    )
    tables: dict[str, list[dict[str, Any]]] = {"datasets": rows}
    notes = [
        "Six datasets: all_fields, prediction, edge, mtp, backtest, value_projection.",
        "Pack names already carry prediction, all-fields, edge leftover, and MTP.",
        "Do not SELECT * on all_fields_rows. Use history/span or named columns.",
        "Do not rerun the multi-hour backtest unless asked. This file is the latest family_metrics.csv.",
        "MTP is climate. Backtest IC is not a leftover gate. Quoted pe_fwd is fp_pe_y1. pe_fwd_tv stays empty.",
    ]
    if metrics_path is None:
        notes.append("family_metrics.csv was not on disk. backtest path is empty.")
    else:
        tables["family_metrics"] = rows_from_csv(metrics_path)
    return write_run_export(
        out_dir,
        tool="generic_utils.dataset_lock",
        tables=tables,
        sources={
            "finproj_db": str(finproj_db) if finproj_db else None,
            "fingrowth_db": str(fingrowth_db) if fingrowth_db else None,
            "family_metrics": str(metrics_path) if metrics_path else None,
        },
        schema_version="dataset_lock_v1",
        notes=notes,
        duckdb_name="datasets.duckdb",
    )
