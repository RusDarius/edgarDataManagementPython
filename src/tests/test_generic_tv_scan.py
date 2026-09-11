import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestDerive(unittest.TestCase):
    def test_leftover_is_max_of_street_and_edge(self):
        from generic_utils.derive import leftover_pct

        self.assertAlmostEqual(leftover_pct(100, 160, 40), 60)
        self.assertAlmostEqual(leftover_pct(100, 110, 40), 40)
        self.assertIsNone(leftover_pct(None, 110, None))

    def test_leftover_to_range_has_no_default_dump_cutoff(self):
        from generic_utils.derive import leftover_to_range

        self.assertEqual(leftover_to_range(120, 20), 6.0)
        self.assertIsNone(leftover_to_range(120, 20, leftover_max=90))

    def test_attach_vs_group_is_pct_vs_industry_median(self):
        from generic_utils.derive import attach_vs_group, keep_with_vs_group

        rows = (
            [{"symbol": f"A{i}", "ind": "HW", "evrev": 8.0, "left": 40} for i in range(5)]
            + [{"symbol": "CHEAP", "ind": "HW", "evrev": 4.0, "left": 80}]
            + [{"symbol": f"B{i}", "ind": "SW", "evrev": 20.0, "left": 10} for i in range(3)]
        )
        out = attach_vs_group(
            rows, group_field="ind", fields=["evrev", "left"], min_n=6
        )
        by_id = {r["symbol"]: r for r in out}
        self.assertEqual(by_id["CHEAP"]["evrev_vs_ind"], -50.0)
        self.assertGreater(by_id["CHEAP"]["left_vs_ind"], 0)
        self.assertIsNone(by_id["B0"]["evrev_vs_ind"])
        keep = keep_with_vs_group(
            ["rsi"],
            {"vs_group": {"by": "ind", "fields": ["evrev"], "min_n": 6}},
        )
        self.assertIn("evrev_vs_ind", keep)
        self.assertIn("evrev_ind_med", keep)
        self.assertIn("close", keep)
        self.assertIn("street_px", keep)
        self.assertIn("target_px", keep)

    def test_implied_price_from_leftover_pct(self):
        from generic_utils.derive import attach_implied_targets, implied_price

        self.assertEqual(implied_price(100, 50), 150.0)
        self.assertIsNone(implied_price(None, 50))
        rows = attach_implied_targets(
            [{"symbol": "A", "close": 200, "street_left": 25, "left": 50}]
        )
        self.assertEqual(rows[0]["street_px"], 250.0)
        self.assertEqual(rows[0]["target_px"], 300.0)


class TestSetup(unittest.TestCase):
    def test_does_not_quote_evrev_when_earnings_exist(self):
        from generic_utils.setup import attach_setup, pick_value_field, resolve_family

        recipe = {
            "families": {
                "sales": ["evrev"],
                "earnings": ["pe_fwd", "pe", "peg", "evebitda"],
                "cash": ["evebitda", "pe"],
                "book": ["pb", "pe"],
            },
            "efficiency": {"sales": ["opm"], "earnings": ["opm"], "cash": ["opm"], "book": []},
            "industry_family": {
                "Packaged Software": "sales",
                "Managed Health Care": "earnings",
                "Major Banks": "book",
            },
            "default_family": "cash",
            "omit_industry_substrings": ["etf"],
            "vs_fields": ["pe", "evrev", "evebitda", "pb", "opm"],
            "min_n": 3,
        }
        adbe = {
            "symbol": "NASDAQ:ADBE",
            "ind": "Packaged Software",
            "evrev": 6.5,
            "pe": 28.0,
            "evebitda": 18.0,
            "opm": 35.0,
        }
        picked = pick_value_field(adbe, recipe)
        self.assertEqual(resolve_family(adbe, recipe), "sales")
        self.assertEqual(picked["val_field"], "pe")
        self.assertNotEqual(picked["val_field"], "evrev")

        unh = {
            "symbol": "NYSE:UNH",
            "ind": "Managed Health Care",
            "evrev": 1.4,
            "pe": 22.0,
            "evebitda": 14.0,
        }
        self.assertEqual(pick_value_field(unh, recipe)["val_field"], "pe")

        bank = {
            "symbol": "NYSE:JPM",
            "ind": "Major Banks",
            "evrev": 0.4,
            "pb": 1.8,
            "pe": 12.0,
        }
        self.assertEqual(pick_value_field(bank, recipe)["val_field"], "pb")

        etf = {"symbol": "XETR:VUAA", "ind": "S&P 500 ETF", "evrev": 99.0}
        self.assertEqual(pick_value_field(etf, recipe)["val_family"], "omit")
        self.assertIsNone(pick_value_field(etf, recipe)["val_field"])

        software = [
            {
                "symbol": f"S{i}",
                "ind": "Packaged Software",
                "mcap": 5_000_000_000,
                "evrev": 10.0,
                "pe": 30.0,
                "evebitda": 20.0,
                "opm": 20.0,
                "rsi": 50,
                "rng": 40,
                "vs50": 2.0,
            }
            for i in range(4)
        ]
        software[0]["symbol"] = "NASDAQ:CHEAP"
        software[0]["pe"] = 15.0
        out = attach_setup(software, recipe)
        cheap = next(r for r in out if r["symbol"] == "NASDAQ:CHEAP")
        self.assertEqual(cheap["val_field"], "pe")
        self.assertEqual(cheap["peer"], "DISCOUNT")
        self.assertEqual(cheap["tech_sma"], "ABOVE")

    def test_sales_uses_evrev_only_when_earnings_missing(self):
        from generic_utils.setup import pick_value_field

        recipe = {
            "families": {"sales": ["evrev"], "earnings": ["pe"]},
            "industry_family": {"Internet Software": "sales"},
            "omit_industry_substrings": [],
        }
        growth = {
            "symbol": "NASDAQ:GROW",
            "ind": "Internet Software",
            "evrev": 8.0,
            "pe": None,
            "evebitda": 400.0,
        }
        self.assertEqual(pick_value_field(growth, recipe)["val_field"], "evrev")

    def test_punished_cheap_ignores_out_of_place_evrev(self):
        from operator_briefing.sleeves import classify_punished_tape

        fake_discount = {
            "left": 15,
            "rsi": 55,
            "rng": 50,
            "bo": 0.1,
            "cont": 0.1,
            "val_field": "pe",
            "val_vs_ind": 12.0,
            "evrev_vs_ind": -45.0,
        }
        self.assertEqual(classify_punished_tape(fake_discount), "MIXED")
        real = dict(fake_discount)
        real["val_vs_ind"] = -25.0
        self.assertEqual(classify_punished_tape(real), "BOUNCE")


class TestAggregations(unittest.TestCase):
    def test_numeric_summary(self):
        from generic_utils.aggregations import numeric_summary

        rows = [{"d5": 10}, {"d5": -5}, {"d5": None}]
        result = numeric_summary(rows, ["d5"])
        self.assertEqual(result["d5"]["n"], 2)
        self.assertEqual(result["d5"]["missing"], 1)
        self.assertEqual(result["d5"]["pct_positive"], 50.0)

    def test_group_stats_min_n_and_pct_positive(self):
        from generic_utils.aggregations import group_stats

        rows = (
            [{"ind": "A", "d5": 2, "day": 1}] * 3
            + [{"ind": "B", "d5": -4, "day": -1}] * 2
            + [{"ind": "C", "d5": 9, "day": 1}]
        )
        result = group_stats(
            rows, group_field="ind", metrics=["d5"], min_n=2, pct_positive_field="day"
        )
        names = [r["ind"] for r in result]
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(result[0]["pct_positive"], 100.0)
        self.assertEqual(result[1]["n"], 2)

    def test_group_stats_pct_positive_denom_n(self):
        from generic_utils.aggregations import group_stats

        rows = [
            {"ind": "A", "day": 1},
            {"ind": "A", "day": None},
            {"ind": "A", "day": -1},
        ]
        clean = group_stats(
            rows, group_field="ind", metrics=["day"], pct_positive_field="day"
        )
        of_n = group_stats(
            rows,
            group_field="ind",
            metrics=["day"],
            pct_positive_field="day",
            pct_positive_denom="n",
        )
        self.assertEqual(clean[0]["pct_positive"], 50.0)
        self.assertEqual(of_n[0]["pct_positive"], 33.3)
        from generic_utils.aggregations import histogram

        rows = [{"rsi": v} for v in (10, 30, 50, 70, 90)]
        buckets = histogram(rows, field="rsi", bins=[0, 30, 70, 100])
        by_label = {b["label"]: b["n"] for b in buckets}
        self.assertEqual(by_label["0-30"], 1)
        self.assertEqual(by_label["30-70"], 2)
        self.assertEqual(by_label["70-100"], 2)

    def test_overlap(self):
        from generic_utils.aggregations import overlap

        result = overlap(["A", "B", "C"], ["B", "C", "D"])
        self.assertEqual(result["n_both"], 2)
        self.assertEqual(result["both"], ["B", "C"])

    def test_weighted_group_stats(self):
        from generic_utils.aggregations import weighted_group_stats

        rows = [
            {"ind": "SW", "wt": 10, "left": 20, "bo": 1},
            {"ind": "SW", "wt": 5, "left": 50, "bo": 0},
            {"ind": "HW", "wt": 15, "left": 10, "bo": 2},
        ]
        grouped = weighted_group_stats(
            rows, group_field="ind", weight_field="wt", metrics=["left", "bo"]
        )
        by_ind = {r["ind"]: r for r in grouped}
        self.assertEqual(by_ind["SW"]["weight"], 15)
        self.assertEqual(by_ind["SW"]["left"], 30)
        self.assertEqual(by_ind["HW"]["weight"], 15)
        self.assertEqual(grouped[0]["weight"], grouped[1]["weight"])

    def test_movers_cover_split(self):
        from generic_utils.ranking import horizon_movers, resolve_movers_tail_n, split_cover

        self.assertEqual(split_cover(50, 0.75), (38, 12))
        self.assertEqual(resolve_movers_tail_n(cover=50, up_share=0.75), (38, 12))
        self.assertEqual(resolve_movers_tail_n(n=2), (2, 2))
        rows = [
            {"symbol": f"S{i}", "ind": f"I{i % 10}", "day": 100 - i}
            for i in range(60)
        ]
        payload = horizon_movers(rows, fields=["day"], cover=50, up_share=0.75)
        day = payload["horizons"]["day"]
        self.assertEqual(payload["spec"]["cover"], 50)
        self.assertEqual(payload["spec"]["up_share"], 0.75)
        self.assertEqual(payload["spec"]["n_leaders"], 38)
        self.assertEqual(payload["spec"]["n_laggards"], 12)
        self.assertEqual(len(day["leaders"]), 38)
        self.assertEqual(len(day["laggards"]), 12)
        self.assertEqual(day["leaders"][0]["symbol"], "S0")
        self.assertEqual(day["laggards"][0]["symbol"], "S59")
        pinned = horizon_movers(rows, fields=["day"], n_leaders=25, n_laggards=25)
        self.assertEqual(pinned["spec"]["n_leaders"], 25)
        self.assertEqual(pinned["spec"]["n_laggards"], 25)
        self.assertEqual(len(pinned["horizons"]["day"]["leaders"]), 25)
        self.assertEqual(len(pinned["horizons"]["day"]["laggards"]), 25)
        self.assertIsNone(pinned["spec"]["group_field"])
        pinned = horizon_movers(rows, fields=["day"], n_leaders=25, n_laggards=25)
        self.assertEqual(pinned["spec"]["n_leaders"], 25)
        self.assertEqual(pinned["spec"]["n_laggards"], 25)
        self.assertEqual(len(pinned["horizons"]["day"]["leaders"]), 25)
        self.assertEqual(len(pinned["horizons"]["day"]["laggards"]), 25)
        self.assertIsNone(pinned["spec"]["group_field"])

    def test_both_tails_and_horizon_movers(self):
        from generic_utils.ranking import flatten_movers, horizon_movers
        from generic_utils.tv_scan_cli import main

        rows = [
            {"symbol": "A", "ind": "X", "day": 8, "d5": 1, "rsi": 40},
            {"symbol": "B", "ind": "X", "day": 3, "d5": 9, "rsi": 41},
            {"symbol": "C", "ind": "Y", "day": -4, "d5": -2, "rsi": 42},
            {"symbol": "D", "ind": "Y", "day": -9, "d5": 0, "rsi": 43},
            {"symbol": "E", "ind": "Z", "day": 1, "d5": 2, "rsi": 44},
        ]
        uncapped = horizon_movers(rows, fields=["day"], n=2, keep=["rsi"])
        day = uncapped["horizons"]["day"]
        self.assertEqual([r["symbol"] for r in day["leaders"]], ["A", "B"])
        self.assertEqual([r["symbol"] for r in day["laggards"]], ["D", "C"])
        self.assertEqual(day["leaders"][0]["rsi"], 40)

        capped = horizon_movers(
            rows, fields=["day"], n=3, group_field="ind", cap=1
        )
        capped_day = capped["horizons"]["day"]
        self.assertEqual([r["symbol"] for r in capped_day["leaders"]], ["A", "E", "C"])
        self.assertGreater(capped_day["overflow_leaders_n"], 0)
        flat = flatten_movers(uncapped)
        self.assertEqual(flat[0]["horizon"], "day")
        self.assertEqual(flat[0]["tail"], "leaders")

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tape.csv"
            csv_path.write_text(
                "symbol,ind,day,d5\nA,X,8,1\nB,X,3,9\nC,Y,-4,-2\n",
                encoding="utf-8",
            )
            out_path = Path(tmp) / "tails.csv"
            cli = main(
                [
                    "movers",
                    "--csv",
                    str(csv_path),
                    "--fields",
                    "day,d5",
                    "--top",
                    "1",
                    "--out",
                    str(out_path),
                ]
            )
            self.assertEqual(cli["spec"]["emitted_n"], 4)
            self.assertEqual(cli["horizons"], {})
            self.assertTrue(out_path.exists())
            filtered = main(
                [
                    "movers",
                    "--csv",
                    str(csv_path),
                    "--fields",
                    "day",
                    "--top",
                    "10",
                    "--where",
                    "day>=0",
                    "--include-rows",
                ]
            )
            self.assertEqual(filtered["spec"]["filtered_rows"], 2)
            self.assertEqual(len(filtered["horizons"]["day"]["laggards"]), 2)

    def test_movers_recipe_and_export_duckdb(self):
        from generic_utils.ranking import movers_size_kwargs_from_recipe
        from generic_utils.run_export import write_run_export
        from generic_utils.scan_sources import rows_from_duckdb
        from generic_utils.tv_scan_cli import main
        from operator_briefing.compile import MOVERS_DAY_RECIPE, _build_movers

        self.assertEqual(
            movers_size_kwargs_from_recipe(
                json.loads(MOVERS_DAY_RECIPE.read_text(encoding="utf-8"))
            ),
            {"n_leaders": 25, "n_laggards": 25},
        )

        tape = [
            {
                "symbol": f"S{i}",
                "ind": "X" if i < 4 else "Y",
                "mcap": 3_000_000_000,
                "day": 10 - i,
                "w": 1.0,
                "d5": i,
                "m1": -i,
                "rsi": 50,
                "evrev": 8.0 if i < 4 else 20.0,
                "pe": 15.0,
                "left": 40 - i,
            }
            for i in range(8)
        ]
        pack, flat = _build_movers(tape, MOVERS_DAY_RECIPE)
        self.assertEqual(pack["spec"]["recipe"], "movers_day")
        self.assertEqual(pack["spec"]["n_leaders"], 25)
        self.assertEqual(pack["spec"]["n_laggards"], 25)
        self.assertEqual((pack["spec"].get("vs_group") or {}).get("by"), "ind")
        self.assertIn("day", pack["horizons"])
        self.assertGreater(len(flat), 0)
        day_laggards = pack["horizons"]["day"]["laggards"]
        self.assertTrue(day_laggards)
        self.assertIn(day_laggards[0].get("down_class"), {"BOUNCE", "CONTINUE_DOWN", "MIXED"})
        self.assertTrue(
            all(r.get("down_class") in {"BOUNCE", "CONTINUE_DOWN", "MIXED"} for r in flat if r.get("tail") == "laggards")
        )

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tape.csv"
            csv_path.write_text(
                "symbol,ind,mcap,day,w,d5,m1\nA,X,3000000000,8,1,2,3\nB,X,3000000000,-4,0,-1,-2\n",
                encoding="utf-8",
            )
            out_dir = Path(tmp) / "run"
            payload = main(
                [
                    "movers",
                    "--csv",
                    str(csv_path),
                    "--recipe",
                    str(MOVERS_DAY_RECIPE),
                    "--out-dir",
                    str(out_dir),
                ]
            )
            self.assertEqual(payload["spec"]["recipe"], "movers_day")
            self.assertEqual(payload["spec"]["n_leaders"], 25)
            self.assertEqual(payload["spec"]["n_laggards"], 25)
            self.assertAlmostEqual(payload["spec"]["up_share"], 0.5)
            self.assertTrue((out_dir / "movers.duckdb").exists())
            log = (out_dir / "overview.log").read_text(encoding="utf-8")
            self.assertIn("tool=tv_scan_cli.movers", log)
            self.assertIn("created_at_utc=", log)
            exported = write_run_export(
                Path(tmp) / "export",
                tool="tv_scan_cli.export",
                tables={"radar": [{"symbol": "MU", "left": 55}]},
                sources={"prediction_run_id": "demo"},
                schema_version="tv_scan_export_v1",
            )
            rows = rows_from_duckdb(exported["duckdb"], "SELECT symbol FROM radar")
            self.assertEqual(rows[0]["symbol"], "MU")
            overview = Path(exported["overview_log"]).read_text(encoding="utf-8")
            self.assertIn("source.prediction_run_id=demo", overview)


class TestSeries(unittest.TestCase):
    def test_expand_ids_and_sql(self):
        from generic_utils.series import build_named_sql, expand_ids, parse_path_date

        self.assertEqual(expand_ids(["NASDAQ:MU", "ADBE"]), ["NASDAQ:MU", "ADBE"])
        self.assertEqual(
            expand_ids(["NASDAQ:MU"], also_bare=True), ["NASDAQ:MU", "MU"]
        )
        self.assertEqual(
            parse_path_date("logs/x/06_09_2026/tradingview_all_fields_06_09_2026.duckdb").isoformat(),
            "2026-09-06",
        )
        sql, missing = build_named_sql(
            table="all_fields_rows",
            columns=["close", "missing_col"],
            available=["symbol", "close", "run_id"],
            id_field="symbol",
            n_ids=2,
            has_run_id=True,
        )
        self.assertIn("run_id = ?", sql)
        self.assertEqual(missing, ["missing_col"])
        self.assertNotIn("*", sql)

    def test_series_span_oldest_to_latest(self):
        from generic_utils.series import series_span

        rows = [
            {"ticker": "NASDAQ:MU", "as_of": "2026-06-01", "close": 100, "evrev": 8},
            {"ticker": "NASDAQ:MU", "as_of": "2026-09-06", "close": 125, "evrev": 6},
        ]
        span = series_span(rows, id_field="ticker", fields=["close", "evrev"])
        self.assertEqual(span[0]["ticker"], "NASDAQ:MU")
        self.assertEqual(span[0]["n"], 2)
        self.assertEqual(span[0]["close_pct"], 25.0)
        self.assertEqual(span[0]["evrev_chg"], -2.0)

    def test_list_dated_files_oldest_first(self):
        from generic_utils.series import list_dated_files

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "01_08_2026"
            newer = root / "06_09_2026"
            older.mkdir()
            newer.mkdir()
            (older / "tradingview_all_fields_01_08_2026.duckdb").write_bytes(b"x")
            (newer / "tradingview_all_fields_06_09_2026.duckdb").write_bytes(b"y")
            listed = list_dated_files(root, "**/tradingview_all_fields_*.duckdb")
            self.assertEqual(
                [row["as_of"] for row in listed], ["2026-08-01", "2026-09-06"]
            )
            newest = list_dated_files(
                root, "**/tradingview_all_fields_*.duckdb", newest=1
            )
            self.assertEqual(newest[0]["as_of"], "2026-09-06")

    def test_span_and_weight_cli(self):
        from generic_utils.tv_scan_cli import main

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "hist.csv"
            csv_path.write_text(
                "ticker,as_of,close,wt,ind\nMU,2026-06-01,100,10,HW\nMU,2026-09-06,130,10,HW\n",
                encoding="utf-8",
            )
            span = main(
                [
                    "span",
                    "--csv",
                    str(csv_path),
                    "--id-field",
                    "ticker",
                    "--fields",
                    "close",
                ]
            )
            self.assertEqual(span["rows"][0]["close_pct"], 30.0)
            grouped = main(
                [
                    "weight-group",
                    "--csv",
                    str(csv_path),
                    "--by",
                    "ind",
                    "--weight",
                    "wt",
                    "--metrics",
                    "close",
                ]
            )
            self.assertEqual(grouped["rows"][0]["ind"], "HW")
            self.assertEqual(grouped["rows"][0]["weight"], 20.0)

    def test_join_and_derive_refresh(self):
        from generic_utils.derive import apply_derived
        from generic_utils.series import join_rows

        left = [{"ticker": "MU", "left": 55, "close": 900}]
        right = [{"symbol": "NASDAQ:MU", "close": 1038, "pt": 1400, "sma50": 1000}]
        joined = join_rows(
            left, right, left_on="ticker", right_on="symbol", prefix="tape_"
        )
        self.assertEqual(joined[0]["tape_close"], 1038)
        refreshed = apply_derived(
            [{"close": 1038, "pt": 1400, "left": 55}],
            refresh=True,
        )
        self.assertGreater(refreshed[0]["left"], 30)
        self.assertLess(refreshed[0]["left"], 40)


class TestPredicates(unittest.TestCase):
    def test_where_filters_without_baked_cutoffs(self):
        from generic_utils.predicates import filter_rows

        rows = [
            {"symbol": "A", "left": 80, "rsi": 40, "ind": "Software"},
            {"symbol": "B", "left": 10, "rsi": 40, "ind": "Software"},
            {"symbol": "C", "left": 80, "rsi": 80, "ind": "Software"},
            {"symbol": "D", "left": 80, "rsi": 40, "ind": "Biotechnology"},
        ]
        kept, clauses = filter_rows(rows, "left>=25,rsi<=68,ind!=Biotechnology")
        self.assertEqual([r["symbol"] for r in kept], ["A"])
        self.assertEqual(len(clauses), 3)

    def test_missing_numeric_fails_clause(self):
        from generic_utils.predicates import filter_rows

        kept, _ = filter_rows([{"left": None}], "left>=25")
        self.assertEqual(kept, [])


class TestFocus(unittest.TestCase):
    def test_build_focus_echoes_spec(self):
        from generic_utils.focus import build_focus

        rows = [{"symbol": f"S{i}", "ind": "X", "left": 100 - i} for i in range(8)]
        payload = build_focus(
            rows, where="left>=95", field="left", n=10, group_field="ind", cap=2
        )
        self.assertEqual(payload["spec"]["filtered_rows"], 6)
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["spec"]["overflow_n"], 4)

    def test_pack_path_rows(self):
        from generic_utils.focus import pack_path_rows

        pack = {"sleeves": {"radar_curated_25": [{"symbol": "NASDAQ:MU", "left": 55}]}}
        rows = pack_path_rows(pack, "sleeves.radar_curated_25")
        self.assertEqual(rows[0]["symbol"], "NASDAQ:MU")


class TestRecipesAndCli(unittest.TestCase):
    def test_recipe_requires_run_id(self):
        from generic_utils.recipes import render_recipe

        sql, params = render_recipe(
            "pred.profile_weeks_pivot", run_id="move_prediction_demo"
        )
        self.assertIn("breakout_long_v1", sql)
        self.assertEqual(params, ["move_prediction_demo"])

    def test_tape_returns_recipe_aliases_horizons(self):
        from generic_utils.recipes import render_recipe

        sql, params = render_recipe("pred.tape_returns", run_id="move_prediction_demo")
        self.assertIn("Perf.W", sql)
        self.assertIn("AS d5", sql)
        self.assertEqual(params, ["move_prediction_demo"])

    def test_pack_focus_cli_writes_csv(self):
        from generic_utils.tv_scan_cli import main

        pack = {
            "run_id": "briefing_pack_demo",
            "sleeves": {
                "radar_curated_25": [
                    {"symbol": "NASDAQ:MU", "left": 55, "ind": "Semiconductors"}
                ]
            },
        }
        with TemporaryDirectory() as tmp:
            pack_path = Path(tmp) / "pack.json"
            out_path = Path(tmp) / "focus.csv"
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            payload = main(
                [
                    "pack-focus",
                    "--pack",
                    str(pack_path),
                    "--sleeve",
                    "sleeves.radar_curated_25",
                    "--out",
                    str(out_path),
                ]
            )
            self.assertEqual(payload["spec"]["n"], 1)
            self.assertEqual(payload["rows"], [])
            self.assertTrue(out_path.exists())
            text = out_path.read_text(encoding="utf-8-sig")
            self.assertIn("NASDAQ:MU", text)


class TestExtractStats(unittest.TestCase):
    def test_industry_breadth_preserves_pack_keys(self):
        from operator_briefing.extract import industry_breadth, universe_stats

        rows = [
            {
                "ind": "Software",
                "mcap": 3_000_000_000,
                "day": 1,
                "d5": 2,
                "m1": 3,
                "rsi": 50,
                "country": "United States",
                "exchange": "NASDAQ",
            }
        ] * 6
        rows.append(
            {
                "ind": "Software",
                "mcap": 100,
                "day": 9,
                "d5": 9,
                "m1": 9,
                "rsi": 90,
                "country": "United States",
                "exchange": "NASDAQ",
            }
        )
        breadth = industry_breadth(rows, min_mcap=2_000_000_000, min_n=6)
        self.assertEqual(len(breadth), 1)
        self.assertEqual(breadth[0]["n"], 6)
        self.assertEqual(breadth[0]["pct_up"], 100.0)
        self.assertEqual(breadth[0]["d5"], 2.0)
        stats = universe_stats(rows, min_mcap=2_000_000_000)
        self.assertEqual(stats["n"], 6)
        self.assertEqual(stats["pct_up"], 100.0)
        self.assertEqual(stats["rsi"], 50.0)


class TestDuplicateIdSuffixes(unittest.TestCase):
    def test_drops_two_digit_suffix_when_stem_exists(self):
        from generic_utils.ranking import drop_duplicate_id_suffixes

        rows = [
            {"symbol": "NASDAQ:NVTS", "m3": -44},
            {"symbol": "NASDAQ:NVTS23", "m3": -49},
            {"symbol": "NASDAQ:NVTS03", "m3": -48},
            {"symbol": "NASDAQ:VSH", "m3": -44},
        ]
        kept = drop_duplicate_id_suffixes(rows)
        self.assertEqual(
            [r["symbol"] for r in kept],
            ["NASDAQ:NVTS", "NASDAQ:VSH"],
        )

    def test_keeps_suffix_when_stem_absent(self):
        from generic_utils.ranking import drop_duplicate_id_suffixes

        rows = [{"symbol": "NASDAQ:FOO23", "m3": -10}]
        kept = drop_duplicate_id_suffixes(rows)
        self.assertEqual([r["symbol"] for r in kept], ["NASDAQ:FOO23"])


class TestDuckdbSqlHint(unittest.TestCase):
    def test_hints_unquoted_left_column(self):
        from generic_utils.scan_sources import duckdb_sql_hint

        hint = duckdb_sql_hint(
            "SELECT symbol, left FROM us2b_tape",
            Exception("Parser Error: syntax error at or near JOIN"),
        )
        self.assertIsNotNone(hint)
        self.assertIn('"left"', hint)

    def test_no_hint_when_quoted(self):
        from generic_utils.scan_sources import duckdb_sql_hint

        self.assertIsNone(
            duckdb_sql_hint(
                'SELECT symbol, "left" FROM us2b_tape',
                Exception("Parser Error: syntax error"),
            )
        )


class TestBookRisk(unittest.TestCase):
    def test_nav_ladders_are_percent_of_equity_plus_cash(self):
        from generic_utils.risk import attach_book_risk, capital_snapshot, pct_of

        capital = capital_snapshot(equity_usd=10_000, cash_usd=5_000, cost_usd=9_000, n=1)
        self.assertEqual(capital["nav_usd"], 15_000)
        self.assertEqual(capital["cash_pct"], 33.33)
        self.assertEqual(pct_of(3_000, 15_000), 20.0)

        rows = [
            {
                "ticker": "MU",
                "symbol": "NASDAQ:MU",
                "shares": 10,
                "close": 100,
                "value_usd": 1_000,
                "cost_usd": 800,
                "average_price": 80,
                "vs_cost": 25,
                "vs50": 10,
                "atrp": 4.0,
                "ind": "Semiconductors",
                "left": 40,
            }
        ]
        payload = attach_book_risk(rows, cash_usd=4_000)
        self.assertEqual(payload["capital"]["nav_usd"], 5_000)
        pos = payload["positions"][0]
        self.assertEqual(pos["wt_nav"], 20.0)
        self.assertEqual(pos["wipe_nav_pct"], 20.0)
        self.assertEqual(pos["m10_usd"], -100)
        self.assertEqual(pos["m10_nav"], -2.0)
        self.assertEqual(payload["capital"]["stress_m10_usd"], -100)
        self.assertEqual(payload["capital"]["max_wipe_nav"], 20.0)
        self.assertIsNotNone(pos["sma50_px"])
        self.assertLess(pos["atr_px"], 100)
        self.assertEqual(pos["trim33_cash"], 330)
        self.assertTrue(payload["industry"])
        self.assertEqual(payload["industry"][0]["ind"], "Semiconductors")

    def test_fx_close_usd_and_cost_usd_drive_ladders(self):
        from generic_utils.risk import attach_book_risk, merge_pack_book

        rows = [
            {
                "config_ticker": "NOVO_B",
                "matched_symbol": "OMXCOP:NOVO_B",
                "implied_shares": 224.0,
                "close": 288.9,
                "close_usd": 45.0,
                "current_value_usd": 10_080.0,
                "invested_sum_usd": 11_500.0,
                "average_price": 51.34,
                "unrealized_return_pct": -12.3,
                "industry": "Pharmaceuticals: Major",
            }
        ]
        pack = [{"ticker": "NOVO_B", "left": 0.0, "vs50": -8.0, "atrp": 3.0}]
        merged = merge_pack_book(rows, pack)
        self.assertEqual(merged[0]["left"], 0.0)
        self.assertEqual(merged[0]["close"], 45.0)
        payload = attach_book_risk(merged, cash_usd=5_000)
        pos = payload["positions"][0]
        self.assertEqual(pos["m10_usd"], -1008)
        self.assertAlmostEqual(pos["average_price_usd"] or 0, 11_500 / 224.0, places=4)
        self.assertGreater(pos["c15_px"], 40)
        self.assertLess(pos["c15_px"], 50)

    def test_cli_config_only_writes_duckdb(self):
        from generic_utils.tv_scan_cli import main

        holdings = {
            "schema_version": "holdings_scoring_v1",
            "holdings_id": "t",
            "portfolio_currency": "USD",
            "cash_position": {"value": 1000, "currency": "USD"},
            "holdings": [
                {
                    "ticker": "MU",
                    "invested_sum": 2000,
                    "invested_currency": "USD",
                    "average_price": 100,
                    "price_currency": "USD",
                }
            ],
        }
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "holdings.json"
            cfg.write_text(json.dumps(holdings), encoding="utf-8")
            out_dir = Path(tmp) / "run"
            payload = main(
                [
                    "risk",
                    "--holdings",
                    str(cfg),
                    "--config-only",
                    "--out-dir",
                    str(out_dir),
                ]
            )
            self.assertEqual(payload["capital"]["cash_usd"], 1_000)
            self.assertEqual(payload["capital"]["equity_usd"], 2_000)
            self.assertEqual(payload["capital"]["nav_usd"], 3_000)
            self.assertEqual(payload["spec"]["n"], 1)
            self.assertTrue((out_dir / "book_risk.duckdb").exists())
            self.assertTrue((out_dir / "book_risk.md").exists())
            md = (out_dir / "book_risk.md").read_text(encoding="utf-8")
            self.assertIn("NAV", md)
            self.assertIn("MU", md)


class TestCoverRows(unittest.TestCase):
    def test_size_is_not_an_industry_cap(self):
        from generic_utils.ranking import cover_rows

        rows = [
            {"symbol": f"S{i}", "ind": "SW" if i < 8 else "HW", "left": 100 - i}
            for i in range(12)
        ]
        taken = cover_rows(rows, 10, field="left")
        self.assertEqual(len(taken), 10)
        software = [row for row in taken if row["ind"] == "SW"]
        self.assertGreater(len(software), 5)

    def test_missing_field_keeps_input_order(self):
        from generic_utils.ranking import cover_rows

        rows = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
        taken = cover_rows(rows, 2)
        self.assertEqual([row["symbol"] for row in taken], ["A", "B"])


class TestForwardValue(unittest.TestCase):
    def test_street_columns_and_ticker_join(self):
        from generic_utils.forward_value import attach_forward_value, clamp_cover

        self.assertEqual(clamp_cover(5000), 1000)
        self.assertEqual(clamp_cover(None), 1000)
        rows = [
            {
                "symbol": "NASDAQ:MU",
                "close": 100,
                "pt": 150,
                "street_left": 50,
                "left": 60,
                "pe_fwd": 18.5,
                "peg": 1.2,
            }
        ]
        price = [
            {
                "symbol": "MU",
                "terminal_price": 200,
                "terminal_upside_pct": 100,
                "primary_upside_pct": 80,
                "primary_upside_source": "street",
                "scenario": "base",
            }
        ]
        growth = [
            {
                "symbol": "NASDAQ:MU",
                "revenue_cagr_implied_pct": 22.0,
                "ebitda_cagr_implied_pct": 18.0,
                "y1_revenue_growth_pct": 30.0,
                "growth_lane": "own",
            }
        ]
        rec = attach_forward_value(rows, price_rows=price, growth_rows=growth)[0]
        self.assertEqual(rec["fwd_street_px"], 150)
        self.assertEqual(rec["fwd_pack_px"], 160)
        self.assertEqual(rec["fp_terminal_px"], 200.0)
        self.assertEqual(rec["fp_rev_cagr_own"], 22.0)
        self.assertEqual(rec["fp_joined"], "street_tv+price+growth")

    def test_empty_pe_fwd_is_not_filled_with_evrev(self):
        from generic_utils.forward_value import attach_street_forward

        rec = attach_street_forward(
            [{"symbol": "A", "close": 10, "evrev": 8.0, "left": 40}]
        )[0]
        self.assertIsNone(rec["fwd_pe"])
        self.assertEqual(rec["fwd_pack_pct"], 40)

    def test_setup_attaches_street_forward(self):
        from generic_utils.setup import attach_setup

        out = attach_setup(
            [
                {
                    "symbol": "NASDAQ:X",
                    "ind": "Semiconductors",
                    "close": 50,
                    "pt": 75,
                    "street_left": 50,
                    "left": 50,
                    "pe": 20,
                    "pe_fwd": 16,
                    "rsi": 45,
                    "rng": 30,
                    "vs50": -1,
                }
            ],
            {
                "families": {"earnings": ["pe_fwd", "pe"], "sales": ["evrev"]},
                "industry_family": {"Semiconductors": "earnings"},
                "default_family": "cash",
                "efficiency": {"earnings": []},
                "vs_fields": [],
                "min_n": 1,
            },
        )
        self.assertEqual(out[0]["fwd_street_px"], 75)
        self.assertEqual(out[0]["fwd_pe"], 16)

    def test_forward_cli_writes_duckdb(self):
        from generic_utils.scan_sources import rows_to_csv
        from generic_utils.tv_scan_cli import main

        root = Path(__file__).resolve().parents[2]
        rows = [
            {
                "symbol": f"NASDAQ:T{i}",
                "ind": "Semiconductors",
                "mcap": 3_000_000_000,
                "close": 100,
                "pt": 120,
                "street_left": 20,
                "left": 40 - i,
                "pe": 20,
                "pe_fwd": 18,
                "rsi": 50,
                "rng": 40,
                "vs50": 1,
            }
            for i in range(6)
        ]
        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tape.csv"
            rows_to_csv(csv_path, rows)
            out_dir = Path(tmp) / "fwd"
            payload = main(
                [
                    "forward",
                    "--csv",
                    str(csv_path),
                    "--recipe",
                    str(root / "config" / "generic_utils" / "forward_value.json"),
                    "--setup-recipe",
                    str(root / "config" / "generic_utils" / "value_tech.json"),
                    "--no-finproj",
                    "--cover",
                    "3",
                    "--out-dir",
                    str(out_dir),
                    "--quiet",
                ]
            )
            self.assertEqual(payload["spec"]["n"], 3)
            self.assertTrue((out_dir / "forward_value.duckdb").exists())
            self.assertTrue((out_dir / "overview.log").exists())
            log = (out_dir / "overview.log").read_text(encoding="utf-8")
            self.assertIn("tv_scan_cli.forward", log)


class TestOperatorSuites(unittest.TestCase):
    def test_catalog_ids(self):
        from run_operator_suites import list_suites

        ids = [item["id"] for item in list_suites()]
        for needed in (
            "inspect",
            "compile",
            "value-tech",
            "forward",
            "movers",
            "movers-3m",
            "risk",
            "pack-focus",
            "finproj-growth",
            "finproj-price",
            "wisdom",
            "all",
        ):
            self.assertIn(needed, ids)

    def test_gitbash_flows_include_scan_producers(self):
        from run_operator_suites import GITBASH_FLOWS

        joined = "\n".join(item["command"] for item in GITBASH_FLOWS)
        self.assertIn("python src/run_financial_projection.py", joined)
        self.assertIn("python src/run_upside_opportunity_scan.py --full-flow", joined)
        self.assertIn("python src/run_market_timing_policy.py", joined)
        self.assertIn("python src/operator_briefing/example_entry.py compile", joined)
        self.assertIn("python src/run_operator_suites.py wisdom --cover 1000", joined)

    def test_prompt_runs_cover_agent_flows(self):
        from run_operator_suites import list_prompt_runs

        rows = list_prompt_runs()
        prompts = "\n".join(item["prompt"] for item in rows)
        for needed in (
            "DAILY (full)",
            "THEMED",
            "DAILY (pack already good)",
            "STALE CHECK",
            "MOVERS (day / 5D / 1M)",
            "MOVERS (3M)",
            "RISK",
            "FORWARD",
            "LOOKUP",
            "COMPARE",
            "BOOK ONLY",
            "DETERMINISTIC FOCUS",
            "WISDOM",
        ):
            self.assertIn(needed, prompts)
        cmds = "\n".join(item["command"] for item in rows)
        self.assertIn("example_entry.py compile", cmds)
        self.assertIn("python src/run_operator_suites.py movers", cmds)
        self.assertIn("python src/run_operator_suites.py movers-3m", cmds)
        self.assertIn("python src/run_operator_suites.py risk", cmds)
        self.assertIn("python src/run_operator_suites.py forward --cover 1000", cmds)
        self.assertIn("python src/run_operator_suites.py wisdom --cover 1000", cmds)
        for item in rows:
            self.assertTrue(item["usage"])
            self.assertTrue(item["output"])

    def test_default_pack_raw_dir(self):
        from run_operator_suites import _default_pack_raw_dir

        pack = Path("logs/tradingview_analysis/operator_briefing/runs/briefing_pack_x/briefing_pack.json")
        self.assertEqual(
            _default_pack_raw_dir(pack, "movers"),
            pack.parent / "movers",
        )

    def test_pack_focus_cover_writes_duckdb(self):
        from run_operator_suites import run_pack_focus_suite

        pack = {
            "run_id": "demo",
            "us2b_tape": [
                {"symbol": f"NASDAQ:N{i}", "left": 90 - i, "ind": "SW" if i < 20 else "HW"}
                for i in range(40)
            ],
            "sleeves": {
                "radar_upside_100": [{"symbol": "NASDAQ:N0", "left": 90}],
                "radar_curated_50": [{"symbol": "NASDAQ:N0", "left": 90}],
            },
        }
        with TemporaryDirectory() as tmp:
            pack_path = Path(tmp) / "briefing_pack.json"
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            out_dir = Path(tmp) / "focus"
            payload = run_pack_focus_suite(pack=pack_path, cover=25, out_dir=out_dir)
            self.assertEqual(payload["spec"]["cover"], 25)
            self.assertTrue((out_dir / "pack_focus.duckdb").exists())
            self.assertTrue((out_dir / "overview.log").exists())

    def test_us2b_tape_loads_from_pack_duckdb(self):
        from generic_utils.focus import pack_us2b_rows
        from generic_utils.run_export import write_run_export

        tape = [{"symbol": f"NASDAQ:N{i}", "left": 50 + i} for i in range(8)]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run_export(
                root,
                tool="test",
                tables={"us2b_tape": tape},
                duckdb_name="briefing_pack.duckdb",
            )
            pack_path = root / "briefing_pack.json"
            pack_path.write_text(
                json.dumps({"run_id": "demo", "names": {"NASDAQ:N0": tape[0]}}),
                encoding="utf-8",
            )
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            rows = pack_us2b_rows(pack, pack_path=pack_path)
            self.assertEqual(len(rows), 8)


if __name__ == "__main__":
    unittest.main()
