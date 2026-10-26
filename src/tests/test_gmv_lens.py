import unittest


def _recipe(**over):
    from generic_utils.gmv_lens import load_gmv_recipe

    recipe = load_gmv_recipe()
    recipe.update(over)
    return recipe


def _series(symbol, *, days=120, drift=0.004, start="2026-03-02", eps_step=0.002, ind="Semis", mcap=5e9, change_sign=1):
    from datetime import date, timedelta

    first = date.fromisoformat(start)
    points, close, eps = [], 100.0, 2.0
    for k in range(days):
        close *= 1.0 + drift
        eps *= 1.0 + eps_step
        points.append({
            "symbol": symbol, "industry": ind, "description": symbol, "as_of": (first + timedelta(days=k)).isoformat(),
            "close": close, "change": change_sign * abs(drift) * 100.0 if k % 3 else -abs(drift) * 50.0,
            "relative_volume_10d_calc": 1.5 if k % 3 else 0.5, "average_volume_10d_calc": 1.2e6, "average_volume_30d_calc": 1e6,
            "market_cap_basic": mcap, "total_revenue_ttm": 1e9, "revenue_forecast_next_fy": 1.2e9,
            "earnings_per_share_diluted_ttm": 1.5, "earnings_per_share_forecast_next_fy": eps,
            "total_revenue_yoy_growth_ttm": 20.0, "earnings_per_share_diluted_yoy_growth_ttm": 25.0,
            "eps_surprise_percent_fq": 5.0, "price_target_average": close * 1.2, "enterprise_value_ebitda_ttm": 12.0, "ATRP": 3.0,
        })
    return points


class TestPanelLoad(unittest.TestCase):
    def test_newest_run_numeric_cast_missing_column_and_where(self):
        import duckdb
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.panel import load_panel, series_by_id

        with TemporaryDirectory() as tmp:
            sources = []
            for day, with_rv in (("2026-10-22", False), ("2026-10-23", True)):
                path = Path(tmp) / f"{day}.duckdb"
                con = duckdb.connect(str(path))
                con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
                con.execute("INSERT INTO run_metadata VALUES ('old', '2026-10-01'), ('new', '2026-10-23')")
                extra = ", relative_volume_10d_calc VARCHAR" if with_rv else ""
                con.execute(f"CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, close VARCHAR, market_cap_basic VARCHAR{extra})")
                rv = ", '1.5'" if with_rv else ""
                con.execute(f"INSERT INTO all_fields_rows VALUES ('new', 'NASDAQ:MU', '880.5', '1e12'{rv}), "
                            f"('old', 'NASDAQ:MU', '1.0', '1e12'{rv}), ('new', 'NYSE:TINY', '5', '1e6'{rv})")
                con.close()
                sources.append({"path": path, "as_of": day})
            panel = load_panel(sources, columns=["symbol", "close", "market_cap_basic", "relative_volume_10d_calc"],
                               numeric=["close", "market_cap_basic", "relative_volume_10d_calc"],
                               where_sql="market_cap_basic >= ?", params=[1e9])
        rows = series_by_id(panel["rows"])
        self.assertEqual(list(rows), ["NASDAQ:MU"])
        self.assertEqual([p["close"] for p in rows["NASDAQ:MU"]], [880.5, 880.5])  # newest run only, cast to DOUBLE
        self.assertEqual([p["relative_volume_10d_calc"] for p in rows["NASDAQ:MU"]], [None, 1.5])
        self.assertEqual(panel["snapshots"][0]["missing_columns"], ["relative_volume_10d_calc"])


class TestSeriesFeatures(unittest.TestCase):
    def test_split_guard_rescales_a_split_but_not_a_crash(self):
        from generic_utils.gmv_lens import split_adjust

        points = [{"as_of": "2026-10-01", "close": 100.0, "change": 0.5, "price_target_average": 120.0},
                  {"as_of": "2026-10-02", "close": 50.5, "change": 1.0, "price_target_average": 60.0},
                  {"as_of": "2026-10-03", "close": 25.0, "change": -50.5, "price_target_average": 60.0}]
        out, events = split_adjust(points, {"max_gap_days": 5, "jump_ratio": 1.8, "day_change_max_pct": 25})
        self.assertEqual(events, 1)  # 100 -> 50.5 with a +1% day is a split; 50.5 -> 25 with -50.5% is a crash
        self.assertAlmostEqual(out[0]["close"], 50.5)
        self.assertAlmostEqual(out[0]["price_target_average"], 60.6)
        self.assertEqual(out[2]["close"], 25.0)

    def test_revision_skips_a_fiscal_year_roll(self):
        from generic_utils.gmv_lens import _revision

        growth, breadth, skipped = _revision([2.0, 2.02, 2.04, 3.0, 3.03, 2.99], 25.0)
        self.assertEqual(skipped, 1)
        self.assertAlmostEqual(breadth, (3 - 1) / 4)
        self.assertAlmostEqual(growth, (1.01 * (2.04 / 2.02) * 1.01 * (2.99 / 3.03) - 1) * 100, places=6)

    def test_point_features_on_a_steady_riser(self):
        from generic_utils.gmv_lens import point_features

        points = _series("NASDAQ:UP")
        rec = point_features(points, len(points) - 1, _recipe())
        self.assertGreater(rec["trend_q"], 0.99)
        self.assertAlmostEqual(rec["r_3m"], (1.004 ** 91 - 1) * 100, delta=0.2)
        self.assertEqual(rec["hi_prox"], 0.0)
        self.assertGreater(rec["updown_vol"], 1.0)
        self.assertGreater(rec["accum_3m"], 0.5)
        self.assertAlmostEqual(rec["eps_rev_3m"], (1.002 ** 91 - 1) * 100, delta=0.1)
        self.assertEqual(rec["eps_rev_breadth"], 1.0)
        self.assertAlmostEqual(rec["fwd_rev_g"], 20.0)
        self.assertAlmostEqual(rec["pt_left"], 20.0)
        self.assertAlmostEqual(rec["exp_move_20d"], round(3.0 * 20 ** 0.5, 1))


    def test_base_effect_growth_is_left_unranked_and_named(self):
        from generic_utils.gmv_lens import point_features

        points = _series("NYSE:BASE")
        for point in points:
            point["earnings_per_share_diluted_ttm"] = 0.01  # forecast ~2.5 on a 0.01 base
        rec = point_features(points, len(points) - 1, _recipe())
        self.assertIsNone(rec["fwd_eps_g"])
        self.assertIsNone(rec["peg_fwd"])
        self.assertEqual(rec["base_effect"], "fwd_eps_g")
        self.assertAlmostEqual(rec["fwd_rev_g"], 20.0)


class TestLensesAndProfiles(unittest.TestCase):
    def test_percentiles_lens_median_and_weakest_lens_profiles(self):
        from generic_utils.gmv_lens import attach_lenses, attach_profiles, percentile_ranks

        self.assertEqual(percentile_ranks([3, None, 1, 3]), [2 / 3, None, 1 / 6, 2 / 3])  # ties share rank 2.5
        recipe = _recipe(min_parts=2, lenses={
            "G": {"parts": {"g1": 1, "g2": 1}},
            "V": {"parts": {"cheap": -1, "room": 1}},
        }, profiles={"BOTH": {"lenses": ["G", "V"]}, "SHORT": {"lenses": ["G"], "invert": True}})
        rows = [{"symbol": "A", "g1": 10, "g2": 10, "cheap": 5, "room": 30},
                {"symbol": "B", "g1": 1, "g2": 1, "cheap": 50, "room": 1},
                {"symbol": "C", "g1": 5, "g2": None, "cheap": 20, "room": 10}]
        out = attach_profiles(attach_lenses(rows, recipe), recipe)
        a, b, c = out
        self.assertEqual(a["cheap_pct"], round(5 / 6, 3))  # lower multiple ranks higher
        self.assertEqual(a["G_pct"], round((a["g1_pct"] + a["g2_pct"]) / 2, 3))  # median of two parts
        self.assertIsNone(c["G_pct"])  # one part present, min_parts 2
        self.assertEqual(c["lenses"], "G-- V50")
        self.assertEqual(a["both_floor"], min(a["G_pct"], a["V_pct"]))
        self.assertIsNone(c["both_floor"])
        self.assertEqual(b["short_floor"], round(1 - b["G_pct"], 3))
        self.assertEqual(b["weak_lens"], "V")  # V 0.167 under G 0.208

    def test_focus_round_robin_industry_cap_and_fade_kept_out(self):
        from generic_utils.gmv_lens import select_profiles

        recipe = _recipe(industry_cap=1, focus_max=3, profiles={
            "LONG1": {"lenses": ["G"], "top": 2}, "LONG2": {"lenses": ["V"], "top": 2},
            "FADE": {"lenses": ["G"], "invert": True, "top": 2}})
        rows = [
            {"symbol": "NYSE:A", "ind": "x", "long1_floor": 0.9, "long2_floor": 0.1, "fade_floor": 0.1},
            {"symbol": "NYSE:B", "ind": "x", "long1_floor": 0.8, "long2_floor": 0.2, "fade_floor": 0.2},
            {"symbol": "NYSE:C", "ind": "y", "long1_floor": 0.7, "long2_floor": 0.9, "fade_floor": 0.3},
            {"symbol": "NYSE:D", "ind": "z", "long1_floor": 0.1, "long2_floor": 0.8, "fade_floor": 0.9},
        ]
        picked = select_profiles(rows, recipe)
        long1 = [r["symbol"] for r in picked["profile_top"] if r["profile"] == "LONG1"]
        self.assertEqual(long1, ["NYSE:A", "NYSE:C"])  # B held back by the industry cap
        self.assertIn({"symbol": "NYSE:B", "ind": "x", "long1_floor": 0.8, "lenses": None, "mcap": None, "profile": "LONG1"},
                      picked["profile_overflow"])
        focus = [(r["symbol"], r["best_profile"], r["profiles"]) for r in picked["focus"]]
        self.assertEqual(focus, [("NYSE:A", "LONG1", "LONG1"), ("NYSE:C", "LONG2", "LONG1,LONG2"), ("NYSE:D", "LONG2", "LONG2")])

    def test_walk_forward_evidence_finds_a_part_that_leads(self):
        from generic_utils.gmv_lens import lens_evidence, prepare_series, rank_ic

        self.assertAlmostEqual(rank_ic(list(range(40)), list(range(40))), 1.0)
        self.assertIsNone(rank_ic([1, 2], [1, 2]))
        # Estimate revisions lead the drift one-for-one; everything else is the same for every name.
        series = {f"NYSE:S{k:02d}": _series(f"NYSE:S{k:02d}", days=150, drift=0.0002 * k, eps_step=0.0003 * k, ind=f"i{k % 7}")
                  for k in range(-20, 21)}
        recipe = _recipe(evidence={"step_days": 14, "horizons_days": [14], "forward_tolerance_days": 3, "min_names": 20, "top_frac": 0.2})
        out = lens_evidence(prepare_series(series, recipe), recipe)
        by = {(r["name"], r["horizon_days"]): r for r in out["evidence"]}
        self.assertGreater(by[("E.eps_rev_3m", 14)]["n_days"], 0)
        self.assertGreater(by[("E.eps_rev_3m", 14)]["ic_mean"], 0.9)
        self.assertGreater(by[("M", 14)]["ic_mean"], 0.9)
        self.assertIsNotNone(by[("E.eps_rev_3m", 14)]["ic_recent3"])


class TestRunnerIntegration(unittest.TestCase):
    def test_prompt_fills_every_placeholder_for_live_and_run_local_judgments(self):
        import re
        from pathlib import Path

        from run_gmv_lens import build_gmv_prompt

        run = Path("logs/canvases/20261023/1830_gmv")
        live = build_gmv_prompt("20261023", pack="PACKDIR", run_dir=run, judgment=Path("logs/canvases/20261023/judgment.json"), live=True)
        local = build_gmv_prompt("20261023", pack="PACKDIR", run_dir=run, judgment=run / "judgment.json", live=False)
        for text in (live, local):
            self.assertEqual(re.findall(r"\{[A-Z_]+\}", text), ["{PACK}"])  # only the literal argv placeholder
            self.assertIn("--template gmv --day 20261023 --judgment", text)
        self.assertIn("priors --day 20261023", live)
        self.assertIn("Do not create logs/canvases/20261023/judgment.json", local)
        self.assertIn('"pages": {"gmv": {"blocks": {}}}}', local)
        self.assertNotIn("priors --day", local)

    def test_refresh_runs_only_named_producers_and_skips_named_suites(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest import mock

        import run_daily_refresh as refresh

        staged = {}

        def fake_dag(steps, **kwargs):
            staged[Path(kwargs["log_dir"]).name] = steps
            return [{"name": s["name"], "rc": 0} for s in steps]

        with TemporaryDirectory() as tmp, mock.patch("generic_utils.task_dag.run_dag", fake_dag), \
                mock.patch.object(refresh, "RUNS", Path(tmp)), mock.patch.object(refresh, "_newest_pack", return_value="PACK"):
            refresh.run_daily_refresh(producers=True, producer_names=["allfields", "edge"], compile_pack=False,
                                      suites=True, skip_suites=["gmv", "playbook"])
        self.assertEqual([s["name"] for s in staged["producers"]], ["allfields", "edge"])
        self.assertEqual(staged["producers"][1]["after"], ["allfields"])
        suites = {s["name"]: s for s in staged["suites"]}
        self.assertNotIn("gmv", suites)
        self.assertNotIn("playbook", suites)
        self.assertEqual(suites["mesh_band"]["after"], ["entry_book_upside"])  # dependency on a skipped suite dropped


class TestTemplateRequires(unittest.TestCase):
    def test_page_is_skipped_when_the_suite_folder_is_missing(self):
        from tempfile import TemporaryDirectory

        from generic_utils.canvas_page import write_template_page

        with TemporaryDirectory() as tmp:
            payload = write_template_page("gmv", pack=tmp, day="20261023", out_dir=tmp)
        self.assertIsNone(payload["html"])
        self.assertIn("gmv_lens.duckdb", payload["skipped"])


if __name__ == "__main__":
    unittest.main()
