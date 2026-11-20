import json
import unittest
from pathlib import Path

from excavation_evidence_workbench import PIPELINES, process
from excavation_evidence_workbench.phase_dating_review import build_facts

ROOT = Path(__file__).resolve().parent.parent

TRENCHES = [{"id": "T1"}, {"id": "T2"}]
DEPOSITS = [
    {"id": "A", "trench": "T1"},
    {"id": "B", "trench": "T1"},
    {"id": "C", "trench": "T2"},
]
# 带深回折的本地参考曲线，按资料给定的历年次序排列。
CURVE = [
    {"id": "p1", "reference_bp": 3400, "calendar_bp": 3000},
    {"id": "p2", "reference_bp": 3500, "calendar_bp": 3100},
    {"id": "p3", "reference_bp": 2900, "calendar_bp": 3200},
    {"id": "p4", "reference_bp": 3450, "calendar_bp": 3300},
    {"id": "p5", "reference_bp": 3900, "calendar_bp": 3400},
]


def base_data():
    return {
        "trenches": [dict(row) for row in TRENCHES],
        "deposits": [dict(row) for row in DEPOSITS],
        "relations": [
            {"id": "r1", "earlier": "A", "later": "B", "state": "confirmed"},
        ],
        "specimens": [
            {"id": "s1", "deposit": "B", "field_attribution": "ambiguous",
             "origin": "A", "measured_bp": 3450, "tolerance_bp": 80},
        ],
        "reference_points": [dict(row) for row in CURVE],
        "reviews": [],
    }


def facts_by_id(data):
    return {row["fact_id"]: row for row in build_facts(data)}


class PhaseDatingReviewTests(unittest.TestCase):
    def test_wiggly_curve_keeps_every_in_tolerance_segment(self):
        data = base_data()
        specimen = facts_by_id(data)["s1"]
        # 允许范围 3370–3530，回折点 p3 跌到范围之外，形成两个不相交历年段。
        self.assertEqual(specimen["allowed_reference_bp"], [3370.0, 3530.0])
        self.assertEqual(specimen["calibration_status"], "calibrated")
        segments = specimen["segments"]
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["reference_point_ids"], ["p1", "p2"])
        self.assertEqual(segments[1]["reference_point_ids"], ["p4"])
        self.assertEqual(segments[0]["calendar_bp_range"], [3000.0, 3100.0])
        self.assertEqual(segments[1]["calendar_bp_range"], [3300.0, 3300.0])

    def test_ambiguous_specimen_shows_both_attributions(self):
        data = base_data()
        specimen = facts_by_id(data)["s1"]
        self.assertEqual(specimen["effective_attribution"], "ambiguous")
        scenarios = {row["attribution"]: row for row in specimen["scenarios"]}
        self.assertEqual(set(scenarios), {"in-situ", "reworked"})
        self.assertEqual(scenarios["in-situ"]["deposit_id"], "B")
        self.assertEqual(scenarios["reworked"]["deposit_id"], "A")
        for scenario in scenarios.values():
            self.assertTrue(scenario["compatible"])
            self.assertEqual(scenario["blocked_by_relation_ids"], [])
            self.assertEqual(scenario["supported_relation_ids"], ["r1"])
            # 两种归属下同样保留回折产生的两个历年段。
            self.assertEqual(scenario["calendar_bp_envelope"], [3000.0, 3300.0])
        # 归属未定时，两个阶段都不应把该标本计入硬约束。
        phases = facts_by_id(data)
        self.assertEqual(phases["A"]["direct_constraints"], [])
        self.assertEqual(phases["B"]["direct_constraints"], [])
        self.assertEqual(phases["B"]["ambiguous_specimen_ids"], ["s1"])

    def test_in_situ_specimen_constrains_its_phase(self):
        data = base_data()
        data["specimens"] = [
            {"id": "s2", "deposit": "A", "field_attribution": "in-situ",
             "measured_bp": 3500, "tolerance_bp": 10},
        ]
        phases = facts_by_id(data)
        self.assertEqual(len(phases["A"]["direct_constraints"]), 1)
        self.assertEqual(phases["A"]["calendar_bp_envelope"], [3100.0, 3100.0])
        self.assertEqual(phases["B"]["calendar_bp_envelope"], None)

    def test_independent_trenches_stay_independent(self):
        data = base_data()
        data["specimens"] = [
            {"id": "s2", "deposit": "A", "field_attribution": "in-situ",
             "measured_bp": 3100, "tolerance_bp": 10},
            {"id": "s3", "deposit": "C", "field_attribution": "in-situ",
             "measured_bp": 3900, "tolerance_bp": 10},
        ]
        phases = facts_by_id(data)
        self.assertEqual(phases["A"]["component_id"], phases["B"]["component_id"])
        self.assertNotEqual(phases["A"]["component_id"], phases["C"]["component_id"])
        # A 老于 B，且 C 与它们没有任何相对关系：两者的层位都独立从 0 起算。
        self.assertEqual(phases["A"]["level"], 0)
        self.assertEqual(phases["B"]["level"], 1)
        self.assertEqual(phases["C"]["level"], 0)
        # 即使 C 的历年早于 A，也不产生倒置冲突。
        self.assertEqual([row for row in build_facts(data) if row["kind"] == "conflict"], [])
        self.assertEqual(phases["C"]["support"], "supported")

    def test_stratigraphic_cycle_marks_affected_phases_and_keeps_others(self):
        data = base_data()
        data["relations"].append(
            {"id": "r2", "earlier": "B", "later": "A", "state": "confirmed"})
        data["specimens"] = [
            {"id": "s2", "deposit": "C", "field_attribution": "in-situ",
             "measured_bp": 3900, "tolerance_bp": 10},
        ]
        facts = build_facts(data)
        conflicts = [row for row in facts if row["kind"] == "conflict"]
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["conflict_type"], "stratigraphic-cycle")
        self.assertEqual(conflict["deposit_ids"], ["A", "B"])
        self.assertEqual(conflict["evidence"]["relation_ids"], ["r1", "r2"])
        phases = {row["fact_id"]: row for row in facts if row["kind"] == "phase-review"}
        self.assertIsNone(phases["A"]["level"])
        self.assertIsNone(phases["B"]["level"])
        self.assertEqual(phases["A"]["support"], "not-supported")
        self.assertEqual(phases["B"]["support"], "not-supported")
        # 独立探方 T2 的结论仍然成立。
        self.assertEqual(phases["C"]["level"], 0)
        self.assertEqual(phases["C"]["support"], "supported")
        self.assertEqual(phases["C"]["conflict_ids"], [])

    def test_chronological_inversion_conflict_and_evidence(self):
        data = base_data()
        data["specimens"] = [
            {"id": "old", "deposit": "A", "field_attribution": "in-situ",
             "measured_bp": 3400, "tolerance_bp": 10},
            {"id": "young", "deposit": "B", "field_attribution": "in-situ",
             "measured_bp": 3900, "tolerance_bp": 10},
        ]
        facts = build_facts(data)
        conflicts = [row for row in facts if row["kind"] == "conflict"]
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["conflict_type"], "chronological-inversion")
        self.assertEqual(conflict["relation_id"], "r1")
        self.assertEqual(conflict["evidence"]["specimen_ids"], ["old", "young"])
        self.assertEqual(conflict["evidence"]["earlier_envelope_bp"], [3000.0, 3000.0])
        self.assertEqual(conflict["evidence"]["later_envelope_bp"], [3400.0, 3400.0])
        phases = {row["fact_id"]: row for row in facts if row["kind"] == "phase-review"}
        self.assertEqual(phases["A"]["conflict_ids"], [conflict["fact_id"]])
        self.assertEqual(phases["B"]["conflict_ids"], [conflict["fact_id"]])
        self.assertEqual(phases["A"]["support"], "not-supported")
        # 独立探方不受影响。
        self.assertEqual(phases["C"]["support"], "supported")

    def test_ambiguous_scenario_reports_blocking_relation(self):
        data = base_data()
        data["specimens"] = [
            {"id": "old", "deposit": "A", "field_attribution": "in-situ",
             "measured_bp": 3400, "tolerance_bp": 10},
            {"id": "s1", "deposit": "B", "field_attribution": "ambiguous",
             "origin": "A", "measured_bp": 3900, "tolerance_bp": 10},
        ]
        specimen = facts_by_id(data)["s1"]
        scenarios = {row["attribution"]: row for row in specimen["scenarios"]}
        # 视为原位则 B 反而老于 A，与 r1 无法同时成立；视为旧层卷入则相容。
        self.assertFalse(scenarios["in-situ"]["compatible"])
        self.assertEqual(scenarios["in-situ"]["blocked_by_relation_ids"], ["r1"])
        self.assertTrue(scenarios["reworked"]["compatible"])
        self.assertEqual(scenarios["reworked"]["blocked_by_relation_ids"], [])

    def test_withdrawn_relation_restores_order_and_documents_change(self):
        data = base_data()
        data["relations"].append(
            {"id": "r2", "earlier": "B", "later": "A", "state": "confirmed"})
        data["reviews"] = [
            {"id": "v1", "target_kind": "relation", "target": "r2",
             "action": "withdraw", "resolution": "withdrawn", "note": "误记撤回"},
        ]
        facts = build_facts(data)
        phases = {row["fact_id"]: row for row in facts if row["kind"] == "phase-review"}
        self.assertEqual([row for row in facts if row["kind"] == "conflict"], [])
        self.assertEqual(phases["A"]["level"], 0)
        self.assertEqual(phases["B"]["level"], 1)
        review = next(row for row in facts if row["kind"] == "review-change")
        self.assertEqual(review["evidence_basis"], ["v1", "r2"])
        changed = {row["deposit_id"]: row for row in review["changes"]}
        self.assertIsNone(changed["A"]["prior_level"])
        self.assertEqual(changed["A"]["current_level"], 0)
        self.assertEqual(changed["A"]["prior_conflict_ids"], ["conflict:cycle:component-1"])
        self.assertEqual(changed["A"]["current_conflict_ids"], [])
        self.assertIsNone(changed["B"]["prior_level"])
        self.assertEqual(changed["B"]["current_level"], 1)

    def test_attribution_correction_moves_date_range_with_evidence(self):
        data = base_data()
        data["reviews"] = [
            {"id": "v2", "target_kind": "specimen", "target": "s1",
             "action": "correct-attribution", "resolution": "reworked",
             "note": "判定为旧层卷入"},
        ]
        facts = build_facts(data)
        specimen = next(row for row in facts if row["fact_id"] == "s1")
        self.assertEqual(specimen["effective_attribution"], "reworked")
        self.assertEqual(specimen["resolved_by_review_id"], "v2")
        phases = {row["fact_id"]: row for row in facts if row["kind"] == "phase-review"}
        # 复核后年代约束落在旧层 A，B 仅保留残余（旧层来源）提示。
        self.assertEqual(phases["A"]["direct_constraints"][0]["attribution"], "reworked")
        self.assertEqual(phases["A"]["calendar_bp_envelope"], [3000.0, 3300.0])
        self.assertEqual(phases["B"]["direct_constraints"], [])
        self.assertEqual(phases["B"]["residual_old_layer_constraints"][0]["specimen_id"], "s1")
        review = next(row for row in facts if row["kind"] == "review-change")
        changed = {row["deposit_id"]: row for row in review["changes"]}
        self.assertIsNone(changed["A"]["prior_calendar_bp_envelope"])
        self.assertEqual(changed["A"]["current_calendar_bp_envelope"], [3000.0, 3300.0])
        self.assertEqual(review["evidence_basis"], ["v2", "s1"])

    def test_no_reference_point_in_tolerance_is_flagged(self):
        data = base_data()
        data["specimens"] = [
            {"id": "sx", "deposit": "A", "field_attribution": "in-situ",
             "measured_bp": 9000, "tolerance_bp": 5},
        ]
        specimen = facts_by_id(data)["sx"]
        self.assertEqual(specimen["calibration_status"], "no-reference-point-in-tolerance")
        self.assertEqual(specimen["segments"], [])
        self.assertIsNone(facts_by_id(data)["A"]["calendar_bp_envelope"])

    def test_invalid_inputs_are_rejected(self):
        def reject(payload, fragment):
            with self.assertRaises(ValueError) as context:
                build_facts(payload)
            self.assertIn(fragment, str(context.exception))

        unknown_deposit = base_data()
        unknown_deposit["relations"][0]["later"] = "ZZ"
        reject(unknown_deposit, "unknown deposit")

        bad_state = base_data()
        bad_state["relations"][0]["state"] = "withdrawn"
        reject(bad_state, "withdrawals are recorded as reviews")

        dangling_withdraw = base_data()
        dangling_withdraw["reviews"] = [
            {"id": "v1", "target_kind": "relation", "target": "r9",
             "action": "withdraw", "resolution": "withdrawn", "note": ""},
        ]
        reject(dangling_withdraw, "does not withdraw")

        review_without_ambiguity = base_data()
        review_without_ambiguity["specimens"][0]["field_attribution"] = "in-situ"
        review_without_ambiguity["reviews"] = [
            {"id": "v2", "target_kind": "specimen", "target": "s1",
             "action": "correct-attribution", "resolution": "reworked", "note": ""},
        ]
        reject(review_without_ambiguity, "does not resolve an ambiguous specimen")

        missing_origin = base_data()
        del missing_origin["specimens"][0]["origin"]
        reject(missing_origin, "origin")

        duplicate_review_target = base_data()
        duplicate_review_target["reviews"] = [
            {"id": "v1", "target_kind": "relation", "target": "r1",
             "action": "withdraw", "resolution": "withdrawn", "note": ""},
            {"id": "v2", "target_kind": "relation", "target": "r1",
             "action": "withdraw", "resolution": "withdrawn", "note": ""},
        ]
        reject(duplicate_review_target, "duplicate review target")

        empty_curve = base_data()
        empty_curve["reference_points"] = []
        reject(empty_curve, "reference curve")

        self_rel = base_data()
        self_rel["relations"][0]["later"] = "A"
        reject(self_rel, "same deposit twice")


class ExampleContractTests(unittest.TestCase):
    def test_example_matches_expected_facts(self):
        source = json.loads(
            (ROOT / "examples" / "phase-dating-review.json").read_text(encoding="utf-8"))
        expected = json.loads(
            (ROOT / "tests" / "expected" / "phase-dating-review.json").read_text(encoding="utf-8"))
        self.assertEqual(process("phase-dating-review", source), expected)
        self.assertIn("phase-dating-review", PIPELINES)
        self.assertEqual(len(PIPELINES), 6)


if __name__ == "__main__":
    unittest.main()
