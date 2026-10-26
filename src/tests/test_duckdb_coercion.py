import math
import unittest


def reference_coerce(value, sql_type):
    """The pre-optimisation _coerce_value, kept verbatim as the oracle."""
    from db.trading_view_move_prediction_duckdb import _is_blank, _json_dump, _parse_float

    if _is_blank(value):
        return None
    normalized_type = sql_type.upper()
    if normalized_type in {"DOUBLE", "FLOAT", "REAL"}:
        return _parse_float(value)
    if normalized_type in {"BIGINT", "INTEGER", "INT"}:
        parsed = _parse_float(value)
        return int(parsed) if parsed is not None else None
    if normalized_type == "BOOLEAN":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None
    if normalized_type == "TIMESTAMP":
        return value
    if isinstance(value, (dict, list)):
        return _json_dump(value)
    return str(value)


VALUES = [None, "", "  ", "x", " x ", "1.5", " 2 ", "nan", "inf", "-0", 0, 1, -7, 10**20, 1.25, -0.0,
          float("nan"), float("inf"), True, False, {"a": 1}, [1, 2], "true", "No", "YES", "0", "abc1"]
TYPES = ["DOUBLE", "double", "FLOAT", "REAL", "BIGINT", "INTEGER", "INT", "BOOLEAN", "TIMESTAMP", "VARCHAR", "TEXT", "JSON", "DATE"]


class TestCoercionEquivalence(unittest.TestCase):
    def test_new_coercers_match_the_reference(self):
        from db.trading_view_move_prediction_duckdb import _coerce_value

        for sql_type in TYPES:
            for value in VALUES:
                expected = reference_coerce(value, sql_type)
                got = _coerce_value(value, sql_type)
                same = (expected == got and type(expected) is type(got)) or (
                    isinstance(expected, float) and isinstance(got, float) and math.isnan(expected) and math.isnan(got))
                self.assertTrue(same, f"{sql_type} {value!r}: {expected!r} != {got!r}")

    def test_append_tabular_output_rows_unchanged(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from db.trading_view_move_prediction_duckdb import MovePredictionDuckDBStore as Store

        with TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "t.duckdb") as store:
                store.append_tabular_output("t", ["sym", "score", "flag"], [["A", "1.5", True], ["B", "", None], ["C", 2, "x"]],
                                            context={"run_id": "r1"})
                rows = store.conn.execute("SELECT run_id, row_number, sym, score, flag FROM t ORDER BY row_number").fetchall()
        self.assertEqual(rows[0][:4], ("r1", 1, "A", 1.5))
        self.assertEqual(rows[1][2:4], ("B", None))
        self.assertEqual(rows[2][1], 3)


if __name__ == "__main__":
    unittest.main()
