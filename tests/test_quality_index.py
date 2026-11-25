from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_index import Project
from quality_index.dashboard import write_dashboard

DATA = ROOT / "data"
MONTHS = ["2026-04", "2026-05"]
BASELINE = ["2025-04", "2025-05"]


class IndexCalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = Project.load(DATA)

    def test_unknown_is_excluded_and_pooled_candidate_index_matches(self):
        view = self.project.basket_view("candidate", MONTHS)
        self.assertTrue(view["complete"])
        self.assertAlmostEqual(view["index"], 79.7083414109, places=6)

        a1 = next(i for i in view["items"] if i["sku"] == "A1" and i["market"] == "domestic")
        # pooled H=143, M=57, L=24; unknown=6 is not in denominator.
        self.assertEqual((a1["high"], a1["middle"], a1["low"], a1["unknown"]), (143, 57, 24, 6))
        self.assertAlmostEqual(a1["score"], 17390 / 224, places=8)

    def test_incomplete_month_is_not_renormalized_into_official_index(self):
        view = self.project.basket_view("candidate", ["2026-05"])
        self.assertFalse(view["complete"])
        self.assertIsNone(view["index"])
        self.assertAlmostEqual(view["observed_weight"], 0.5, places=8)
        self.assertAlmostEqual(view["missing_weight"], 0.5, places=8)
        self.assertAlmostEqual(view["partial_normalized_index_not_official"], 82.1416994303, places=6)

        change = self.project.same_basket_change("candidate", ["2026-04"], ["2026-05"])
        self.assertFalse(change["is_full_basket"])
        self.assertAlmostEqual(change["performance_delta"], -2.3954906523, places=6)

    def test_actual_composition_reading_reconciles_to_basket_gap(self):
        reading = self.project.actual_reading("candidate", MONTHS)
        composition = self.project.composition_view("candidate", MONTHS)
        self.assertAlmostEqual(reading["index"], 81.5320910973, places=6)
        self.assertAlmostEqual(composition["basket_index"], 79.7083414109, places=6)
        self.assertAlmostEqual(composition["composition_gap_actual_ref_minus_basket"], 1.82375, places=4)
        self.assertEqual(composition["outside_reference_share"], 0.0)

    def test_yoy_operating_delta_reconciles_and_is_dominated_by_structure(self):
        bridge = self.project.operating_bridge(
            BASELINE,
            MONTHS,
            old_edition="published",
            new_edition="candidate",
            group_fields=("line", "market"),
        )
        self.assertTrue(bridge["exact"])
        self.assertAlmostEqual(bridge["old_index"], 73.7168141593, places=6)
        self.assertAlmostEqual(bridge["new_index"], 81.5320910973, places=6)
        self.assertAlmostEqual(bridge["total_delta"], 7.8152769380, places=6)
        self.assertAlmostEqual(bridge["new_scope_effect"], 43.7163561077, places=6)
        # 持续组 raw 得分效应为负；且因版本/边界不同不能称为质量改善。
        self.assertLess(bridge["continuing_raw_score_effect"], 0)
        self.assertTrue(bridge["caliber_warning"])

    def test_mapping_keeps_nonoverlapping_firm_shares_and_residual(self):
        plan = self.project.mapping_plan("A0", "domestic")
        self.assertAlmostEqual(plan["firm_total"], 0.65)
        self.assertAlmostEqual(plan["unallocated_firm_residual"], 0.35)
        by_id = {e["id"]: e for e in plan["edges"]}
        self.assertLessEqual(
            by_id["M01"]["firm_share"] + by_id["M02"]["firm_share"],
            1.0,
        )
        # 两项的上限不能同时按 max 强拆；联合可行上限仍受 1 约束。
        self.assertAlmostEqual(plan["feasible_max_nonoverlap_total"], 1.0)
        self.assertAlmostEqual(plan["unproven_upper_band"], 0.35)

    def test_pair_threshold_blocks_export_but_allows_domestic_edges(self):
        export_pairs = self.project.pair_summary("A", "export", "A1")
        domestic_pairs = self.project.pair_summary("A", "domestic", "A1")
        self.assertEqual(export_pairs["pair_count"], 9)
        self.assertEqual(domestic_pairs["pair_count"], 81)
        self.assertLess(export_pairs["pair_count"], 35)
        self.assertGreaterEqual(domestic_pairs["pair_count"], 35)

    def test_reference_bridge_closes_and_keeps_unbridgeable_weights(self):
        bridge = self.project.reference_bridge("published", MONTHS, BASELINE)
        self.assertTrue(bridge["weight_accounting_exact"])
        self.assertAlmostEqual(bridge["certified_weight"], 0.46, places=8)
        self.assertAlmostEqual(bridge["caliber_blocked_weight"], 0.34, places=8)
        self.assertAlmostEqual(bridge["mapping_residual_weight"], 0.20, places=8)
        self.assertAlmostEqual(bridge["unresolved_weight"], 0.54, places=8)
        self.assertFalse(bridge["fully_certified"])
        self.assertAlmostEqual(bridge["baseline_index"], 75.5608504399, places=6)
        self.assertAlmostEqual(bridge["proxy_if_unresolved_unchanged"], 75.7630263525, places=6)

    def test_regional_targets_preserve_commitment_and_trace_exclusions(self):
        t01 = self.project.evaluate_target("T01", quarter="2026-Q2")
        self.assertEqual(t01["status"], "committed_definition_not_certifiable")
        self.assertIsNone(t01["index"])
        self.assertAlmostEqual(t01["proxy_index"], 72.0424185988, places=6)
        self.assertAlmostEqual(t01["proxy_conservative_zero_earned"], 71.6726027618, places=6)
        excluded_markets = {x["market"] for x in t01["excluded_market_shares"]}
        self.assertEqual(excluded_markets, {"export", "new"})
        self.assertTrue(all(c["fixed"] for c in t01["original_commitments"]))

        t02 = self.project.evaluate_target("T02", quarter="2026-Q2")
        self.assertEqual(t02["status"], "committed_definition_not_certifiable")
        self.assertAlmostEqual(t02["proxy_index"], 84.4444444444, places=6)

        t03 = self.project.evaluate_target("T03", quarter="2026-Q2")
        self.assertEqual(t03["status"], "future_or_inactive_target_baseline")
        self.assertAlmostEqual(t03["gap"], -6.2916585891, places=6)

        t04 = self.project.evaluate_target("T04", quarter="2026-Q2")
        self.assertAlmostEqual(t04["index"], 87.2520661157, places=6)
        self.assertAlmostEqual(t04["gap"], 8.2520661157, places=6)

        region = self.project.trace_region("new", "2026-Q2")
        self.assertEqual(region["target_ids"], ["T04"])
        self.assertTrue(region["original_commitments"])

    def test_target_decomposition_gaps_are_separated(self):
        alloc = self.project.target_allocation("T03", quarter="2026-Q2")
        hard = alloc["hard_contract_arithmetic"]
        envelope = alloc["floor_cap_envelope"]
        self.assertFalse(hard["total_feasible"])
        self.assertAlmostEqual(hard["achievable_min"], 70.85, places=8)
        self.assertAlmostEqual(hard["achievable_max"], 73.25, places=8)
        self.assertAlmostEqual(hard["gap_above_max"], 12.75, places=8)
        self.assertAlmostEqual(alloc["proposed_weighted_target"], 72.65, places=8)
        self.assertAlmostEqual(alloc["proposed_gap_above_total"], 13.35, places=8)
        self.assertFalse(envelope["total_feasible"])
        self.assertAlmostEqual(envelope["weighted_max"], 80.05, places=8)
        self.assertAlmostEqual(envelope["gap_above_max"], 5.95, places=8)
        self.assertAlmostEqual(alloc["baseline_gap_to_total"], -6.2916585891, places=6)

    def test_reference_overlap_quantifies_non_decomposable_weights(self):
        overlap = self.project.reference_overlap("published", "candidate")
        self.assertAlmostEqual(overlap["mapping_supported_common_weight"], 0.56, places=8)
        self.assertAlmostEqual(overlap["double_review_supported_common_weight"], 0.31, places=8)
        self.assertAlmostEqual(overlap["old_reference_nonoverlap_weight"], 0.44, places=8)
        self.assertAlmostEqual(overlap["new_reference_nonoverlap_weight"], 0.44, places=8)
        self.assertTrue(overlap["no_unique_cross_edition_decomposition"])

    def test_sku_level_operating_bridge_still_reconciles(self):
        bridge = self.project.operating_bridge(
            BASELINE,
            MONTHS,
            group_fields=("line", "market", "sku"),
        )
        self.assertTrue(bridge["exact"])
        self.assertLess(abs(bridge["reconciliation_residual"]), 1e-9)

    def test_q3_overlap_keeps_both_editions(self):
        overlaps = self.project.effective_overlaps("2026-Q3")
        self.assertTrue(any(set(o["target_ids"]) == {"T01", "T02", "T03", "T04"} for o in overlaps))


class RecomputabilityTests(unittest.TestCase):
    def test_future_month_and_second_candidate_edition_recompute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            shutil.copytree(DATA, root)

            # 补一个 2026-06 行，键不重复且等级和等于 inspected。
            with (root / "counts.csv").open("a", encoding="utf-8", newline="") as f:
                f.write("2026-06,A,domestic,A1,new,200,100,70,20,8,2,policy_shift,selected\n")

            # 第二套候选参照：同样权重，分值卡更严。
            basket_path = root / "baskets.csv"
            with basket_path.open("a", encoding="utf-8", newline="") as f:
                f.write("candidate2,A,domestic,A1,0.2,new,90,45,9,35\n")
                f.write("candidate2,A,domestic,A2,0.2,new,90,45,9,35\n")
                f.write("candidate2,A,export,A1,0.15,new,90,45,9,35\n")
                f.write("candidate2,B,domestic,B0,0.15,new,90,45,9,35\n")
                f.write("candidate2,B,new,B9,0.3,new,90,45,9,35\n")

            project = Project.load(root)
            period = project.resolve_period(quarter="2026-Q2")
            self.assertTrue(period["quarter_calendar_complete"])
            self.assertIn("2026-06", period["months"])

            v1 = project.basket_view("candidate", period["months"])
            v2 = project.basket_view("candidate2", period["months"])
            self.assertTrue(v1["complete"])
            self.assertTrue(v2["complete"])
            self.assertNotAlmostEqual(v1["index"], v2["index"])
            # 更严分值卡应给出更低指数。
            self.assertLess(v2["index"], v1["index"])

            # 新月份进入后，目标对象不需要任何硬编码月份即可重算。
            trace = project.evaluate_target("T03", quarter="2026-Q2")
            self.assertEqual(trace["period"]["months"][-1], "2026-06")

            sampling = project.sampling_register(period["months"])
            self.assertGreaterEqual(sampling["policy_change_count"], 1)
            a1 = next(
                e for e in sampling["entries"]
                if e["key"] == ("A", "domestic", "A1", "new")
            )
            self.assertTrue(a1["policy_changed"])
            self.assertEqual(a1["sampling_modes"], ["routine", "selected"])

            dashboard = Path(tmp) / "dashboard.html"
            write_dashboard(project, dashboard, "2026-Q2")
            html = dashboard.read_text(encoding="utf-8")
            self.assertIn("当前经营读数", html)
            self.assertIn("report-data", html)


if __name__ == "__main__":
    unittest.main()
