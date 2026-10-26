"""Day priors file from a judgment: `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

Every action key is EXCHANGE:TICKER (symbol_identity output contract). A legacy
bare key already in the file is qualified from the pack (book, names, US $2B
tape) or left bare when the ticker is missing or listed twice.

No course is chosen here: actions are copied from the judgment's `courses`.
Which courses to keep is the caller's `skip` list (default drops the
no-position labels PASS / WATCH / STAND_ASIDE unless the name is held).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.symbol_identity import SymbolCatalog, parse_symbol, qualify, qualify_text, ticker_map

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRIORS_DIR = REPO_ROOT / "logs" / "AI_ANALYSIS_UTILS" / "priors"
DEFAULT_SKIP = ("PASS", "WATCH", "STAND_ASIDE")


def pack_catalogs(pack_dir: str | Path) -> tuple[SymbolCatalog, SymbolCatalog]:
    """(book catalog, every pack symbol catalog) from briefing_pack.duckdb. Book first when a ticker is listed twice."""
    from generic_utils.scan_sources import rows_from_duckdb

    db = Path(pack_dir) / "briefing_pack.duckdb"
    db = db if db.is_absolute() else REPO_ROOT / db
    book, every = [], []
    for table, target in (("book", book), ("names", every), ("us2b_tape", every)):
        try:
            target += [r.get("symbol") for r in rows_from_duckdb(db, f'SELECT symbol FROM "{table}"')]
        except Exception:  # table missing on an older pack
            continue
    return SymbolCatalog.build(book), SymbolCatalog.build(book + every)


def priors_from_judgment(
    judgment: Mapping[str, Any],
    *,
    held: Iterable[str] = (),
    skip: Sequence[str] = DEFAULT_SKIP,
    note_chars: int = 260,
) -> dict[str, dict[str, Any]]:
    """{EXCHANGE:TICKER: {action, grade, horizon, note}} from `courses`. Held names are always kept."""
    held_set = {parse_symbol(s).normalized for s in held}
    skipped = {s.upper() for s in skip}
    out: dict[str, dict[str, Any]] = {}
    for symbol, course in (judgment.get("courses") or {}).items():
        key = parse_symbol(symbol).normalized
        action = str((course or {}).get("Course") or "").strip()
        if not key or not action:
            continue
        if action.split(" ")[0].upper() in skipped and key not in held_set:
            continue
        rec = {"action": action, "grade": (course or {}).get("Grade") or ""}
        if course.get("Horizon"):
            rec["horizon"] = course["Horizon"]
        rec["note"] = str(course.get("Reason") or "")[:note_chars]
        out[key] = rec
    return out


def qualify_actions(actions: Mapping[str, Any], *catalogs: SymbolCatalog) -> dict[str, Any]:
    """Re-key an actions map to EXCHANGE:TICKER. A later qualified key wins over the same name's bare key."""
    out: dict[str, Any] = {}
    for key, value in actions.items():
        out[qualify(key, *catalogs)] = value
    return out


def write_priors(
    day: str,
    judgment: Mapping[str, Any],
    *,
    pack_dir: str | Path,
    priors_dir: str | Path | None = None,
    source: str = "",
    notes: str = "",
    priors_used: str = "",
    merge: bool = True,
    skip: Sequence[str] = DEFAULT_SKIP,
) -> dict[str, Any]:
    """Write (or merge into) the day's priors file. Returns path, counts, and ids left bare."""
    folder = Path(priors_dir) if priors_dir else DEFAULT_PRIORS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}_operator_actions.json"
    pack_path = Path(pack_dir)
    pack_json = json.loads(((pack_path if pack_path.is_absolute() else REPO_ROOT / pack_path) / "briefing_pack.json").read_text(encoding="utf-8"))
    held = [r.get("symbol") for r in pack_json.get("book") or [] if r.get("symbol")]
    book_cat, every_cat = pack_catalogs(pack_dir)
    existing = json.loads(path.read_text(encoding="utf-8")) if (merge and path.exists()) else {}
    actions = qualify_actions(existing.get("actions") or {}, book_cat, every_cat)
    actions.update(qualify_actions(priors_from_judgment(judgment, held=held, skip=skip), book_cat, every_cat))
    # Notes and reasons name stocks too: bare mentions of judged or held names become EXCHANGE:TICKER.
    by_ticker = ticker_map([*actions.keys(), *held])
    for rec in actions.values():
        if isinstance(rec, dict) and rec.get("note"):
            rec["note"] = qualify_text(rec["note"], by_ticker)[0]
    notes = qualify_text(notes or existing.get("notes") or "", by_ticker)[0]
    src = pack_json.get("sources") or {}
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    payload = {
        "scan_day": iso,
        "tape_day": iso,
        "source": source or existing.get("source") or "",
        "pack": pack_path.name,
        "pred": src.get("prediction_run_id"),
        "all_fields": src.get("all_fields_run_id"),
        "holdings": Path(str(src.get("holdings_run_dir") or "")).name,
        "priors_used": priors_used or existing.get("priors_used") or Path(str(src.get("priors_path") or "")).name,
        "notes": notes,
        "id_format": "EXCHANGE:TICKER",
        "actions": actions,
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    bare = sorted(k for k in actions if ":" not in k)
    return {"path": path.as_posix(), "n_actions": len(actions), "bare_ids": bare}
