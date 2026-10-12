import json
from pathlib import Path

from generic_utils.brief_history import (
    action_family,
    build_brief_history,
    closes_from_pack_db,
    format_digest_md,
    label_outcome,
    load_bands,
    load_sessions,
    DEFAULT_BANDS,
)


def test_bands_label_the_return_and_leave_unknown_actions_open() -> None:
    bands = DEFAULT_BANDS
    assert label_outcome("ADD", 3, bands) == "GOOD"
    assert label_outcome("ADD", 2.9, bands) == "FLAT"
    assert label_outcome("ADD", -8, bands) == "BAD"
    assert label_outcome("HOLD", 8, bands) == "GOOD"
    assert label_outcome("HOLD", -12, bands) == "BAD"
    assert label_outcome("REDUCE", -3, bands) == "GOOD"
    assert label_outcome("REDUCE", 8, bands) == "BAD"
    assert label_outcome("PASS", 15, bands) == "MISSED"
    assert label_outcome("PASS", 0, bands) == "GOOD"
    assert label_outcome("SHORT", -5, bands) == "GOOD"
    assert label_outcome("SHORT", 5, bands) == "BAD"
    assert label_outcome("OTHER", 40, bands) == "OPEN"
    assert label_outcome("ADD", None, bands) == "OPEN"
    assert action_family("HOLD_NO_ADD") == "HOLD"
    assert action_family("SHORT_WAIT") == "SHORT"


def test_digest_scores_the_first_priced_call_and_omits_notes(tmp_path: Path) -> None:
    priors = tmp_path / "priors"
    priors.mkdir()
    (priors / "20260921_operator_actions.json").write_text(
        json.dumps(
            {
                "scan_day": "2026-09-21",
                "pack": "briefing_pack_20260921_x",
                "actions": {
                    "MU": {"action": "ADD", "grade": "High", "note": "SECRET_NOTE_MU do not print"},
                    "CRWD": {"action": "PASS", "note": "SECRET_NOTE_CRWD"},
                    "NOVO_B": {"action": "EXIT", "note": "SECRET_NOTE_NOVO"},
                    "FLATZ": {"action": "HOLD", "note": "SECRET_NOTE_FLAT"},
                },
            }
        ),
        encoding="utf-8",
    )
    (priors / "20260928_operator_actions.json").write_text(
        json.dumps(
            {
                "scan_day": "2026-09-28",
                "actions": {
                    "MU": {"action": "EXIT", "grade": "Med"},
                    "ARM": {"action": "SHORT_WAIT"},
                },
            }
        ),
        encoding="utf-8",
    )
    sessions = load_sessions(priors)
    marks = {
        "2026-09-21": {
            "NASDAQ:MU": 100.0,
            "NASDAQ:CRWD": 50.0,
            "OMXCOP:NOVO_B": 40.0,
            "NASDAQ:FLATZ": 10.0,
        },
        "2026-09-28": {"NASDAQ:MU": 90.0, "NASDAQ:ARM": 80.0},
        "2026-10-09": {
            "NASDAQ:MU": 70.0,
            "NASDAQ:CRWD": 70.0,
            "OMXCOP:NOVO_B": 30.0,
            "NASDAQ:FLATZ": 10.2,
            "NASDAQ:ARM": 90.0,
        },
    }
    payload = build_brief_history(
        sessions,
        marks,
        latest_day="2026-10-09",
        max_names=2,
        note_chars=0,
    )
    by_ticker = {row["ticker"]: row for row in payload["names"]}
    assert by_ticker["MU"]["ret_pct"] == -30.0
    assert by_ticker["MU"]["label"] == "BAD"
    assert by_ticker["MU"]["flipped"] is True
    assert by_ticker["MU"]["scored_action"] == "ADD"
    assert by_ticker["MU"]["last_action"] == "EXIT"
    assert by_ticker["CRWD"]["label"] == "MISSED"
    assert by_ticker["CRWD"]["ret_pct"] == 40.0
    assert by_ticker["NOVO_B"]["label"] == "GOOD"
    assert by_ticker["NOVO_B"]["family"] == "REDUCE"
    assert by_ticker["ARM"]["label"] == "BAD"
    assert by_ticker["ARM"]["entry_day"] == "2026-09-28"
    assert by_ticker["FLATZ"]["label"] == "FLAT"
    assert payload["spec"]["omitted"] == 3
    assert len(payload["shown"]) == 2
    assert all(row["note_head"] == "" for row in payload["events"])
    page = format_digest_md(payload)
    assert "SECRET_NOTE" not in page
    assert "FLATZ" not in page
    assert "MU" in page
    assert "Omitted from this page: 3" in page
    assert any("faded" in line for line in payload["adapt"])
    assert any("missed a runner" in line for line in payload["adapt"])


def test_same_day_mark_is_not_a_lesson() -> None:
    sessions = [
        {
            "day": "2026-10-09",
            "pack": "",
            "source_file": "20261009_operator_actions.json",
            "actions": [{"ticker": "MU", "action": "ADD", "grade": "", "support": None, "note": ""}],
        }
    ]
    payload = build_brief_history(
        sessions,
        {"2026-10-09": {"NASDAQ:MU": 100.0}},
        latest_day="2026-10-09",
    )
    assert payload["names"][0]["label"] == "OPEN"
    assert payload["names"][0]["ret_pct"] is None


def test_pack_db_joins_a_bare_ticker_to_the_qualified_close(tmp_path: Path) -> None:
    import duckdb

    db_path = tmp_path / "briefing_pack.duckdb"
    connection = duckdb.connect(db_path.as_posix())
    connection.execute("CREATE TABLE names (symbol VARCHAR, close DOUBLE)")
    connection.execute("INSERT INTO names VALUES ('NASDAQ:MU', 1034.34)")
    connection.execute("CREATE TABLE book (ticker VARCHAR, symbol VARCHAR, close DOUBLE)")
    connection.execute("INSERT INTO book VALUES ('NOVO_B', 'OMXCOP:NOVO_B', 38.47), ('MU', 'NASDAQ:MU', 1)")
    connection.close()
    closes = closes_from_pack_db(db_path)
    assert closes["NASDAQ:MU"] == 1034.34
    assert closes["OMXCOP:NOVO_B"] == 38.47
    from generic_utils.symbol_identity import SymbolCatalog, lookup_mapping

    catalog = SymbolCatalog.build(closes.keys())
    assert lookup_mapping(closes, "MU", catalog) == 1034.34
    assert lookup_mapping(closes, "NOVO_B", catalog) == 38.47


def test_recipe_overrides_a_band(tmp_path: Path) -> None:
    path = tmp_path / "brief_history.json"
    path.write_text(json.dumps({"add_bad_pct": -20}), encoding="utf-8")
    bands = load_bands(path)
    assert bands["add_bad_pct"] == -20
    assert bands["add_good_pct"] == DEFAULT_BANDS["add_good_pct"]
    assert label_outcome("ADD", -10, bands) == "FLAT"
