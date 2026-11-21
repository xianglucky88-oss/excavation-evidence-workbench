import copy
import unittest

from excavation_evidence_workbench import process

from recovery_fixtures import (
    build_observation,
    build_sample,
    build_source,
)


def facts_by_id(source):
    return {fact["fact_id"]: fact for fact in process("recovery-density", source)}


class RecoveryDensityValidationTests(unittest.TestCase):
    def test_reject_unknown_sample_or_fraction(self):
        sample = build_sample("S1", "H1", 10, [("light", ["u1"])])
        observation = build_observation(
            "o1", "S1", "light", "complete", scope={"u1": 1}
        )
        source = build_source([sample], [observation])
        bad = copy.deepcopy(source)
        bad["observations"][0]["sample"] = "nope"
        with self.assertRaises(ValueError):
            process("recovery-density", bad)
        bad = copy.deepcopy(source)
        bad["observations"][0]["fraction"] = "heavy"
        with self.assertRaises(ValueError):
            process("recovery-density", bad)

    def test_reject_unknown_material_unit_in_scope(self):
        sample = build_sample("S1", "H1", 10, [("light", ["u1"])])
        observation = build_observation(
            "o1", "S1", "light", "complete", scope={"u1": 1}
        )
        source = build_source([sample], [observation])
        bad = copy.deepcopy(source)
        bad["observations"][0]["scope"] = {"u9": 1}
        with self.assertRaises(ValueError):
            process("recovery-density", bad)

    def test_complete_observation_must_cover_every_unit(self):
        sample = build_sample("S1", "H1", 10, [("light", ["u1", "u2"])])
        observation = build_observation(
            "o1", "S1", "light", "complete", scope={"u1": 1}
        )
        source = build_source([sample], [observation])
        with self.assertRaises(ValueError):
            process("recovery-density", source)
        fixed = copy.deepcopy(source)
        fixed["observations"][0]["scope"] = {"u1": 1, "u2": 2}
        facts = facts_by_id(fixed)
        self.assertEqual(facts["sample:S1"]["whole_sample_inference"]["inferred_count"], 3.0)

    def test_random_observation_needs_method_group_and_matching_proportion(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observation = build_observation(
            "o1", "S1", "light", "random",
            scope={"u1": 1, "u2": 1},
            proportion=0.5, sampling_method="random_split", group="g1",
        )
        source = build_source([sample], [observation])
        facts = facts_by_id(source)
        self.assertTrue(facts["sample:S1"]["whole_sample_inference"]["supported"])

        for field in ("sampling_method", "subsample_group", "proportion"):
            bad = copy.deepcopy(source)
            obs = bad["observations"][0]
            obs.pop(field)
            with self.assertRaises((ValueError, KeyError)):
                process("recovery-density", bad)

        bad = copy.deepcopy(source)
        bad["observations"][0]["proportion"] = 0.25
        with self.assertRaises(ValueError):
            process("recovery-density", bad)

        bad = copy.deepcopy(source)
        bad["observations"][0]["scope"] = {"u1": 1}
        with self.assertRaises(ValueError):
            process("recovery-density", bad)

    def test_selective_observation_cannot_claim_sampling_coverage(self):
        sample = build_sample("S1", "H1", 10, [("light", ["u1", "u2"])])
        observation = build_observation(
            "o1", "S1", "light", "selective", scope={"u1": 1}
        )
        facts = facts_by_id(build_source([sample], [observation]))
        inference = facts["sample:S1"]["whole_sample_inference"]
        self.assertFalse(inference["supported"])
        self.assertIn("only_selective_picking_available", inference["limitations"][0].values())

        bad_observation = build_observation(
            "o1", "S1", "light", "selective", scope={"u1": 1},
            proportion=0.5, sampling_method="random_split",
        )
        with self.assertRaises(ValueError):
            process("recovery-density", build_source([sample], [bad_observation]))

    def test_recheck_must_target_its_group_primary(self):
        sample = build_sample("S1", "H1", 10, [("light", ["u1", "u2"])])
        primary = build_observation(
            "o1", "S1", "light", "random", scope={"u1": 1},
            proportion=0.5, group="g1",
        )
        recheck = build_observation(
            "o2", "S1", "light", "random", scope={"u1": 1},
            proportion=0.5, group="g1", recheck_of="o1",
        )
        bad = copy.deepcopy(build_source([sample], [primary, recheck]))
        bad["observations"][1]["recheck_of"] = "missing"
        with self.assertRaises(ValueError):
            process("recovery-density", bad)

        bad = copy.deepcopy(build_source([sample], [primary, recheck]))
        bad["observations"][1]["scope"] = {"u2": 1}
        with self.assertRaises(ValueError):
            process("recovery-density", bad)


class RecoveryDensitySemanticsTests(unittest.TestCase):
    def test_fractions_share_counts_but_volume_counts_once(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1"]), ("heavy", ["v1"])]
        )
        observations = [
            build_observation("o1", "S1", "light", "complete", scope={"u1": 3}),
            build_observation("o2", "S1", "heavy", "complete", scope={"v1": 2}),
        ]
        facts = facts_by_id(build_source([sample], observations))
        inference = facts["sample:S1"]["whole_sample_inference"]
        self.assertEqual(inference["inferred_count"], 5.0)
        self.assertEqual(inference["recovered_per_liter"], 0.5)
        self.assertEqual(facts["sample:S1"]["processed_liters"], 10.0)

    def test_random_subsample_extrapolates_with_interval_and_zero_rule(self):
        sample = build_sample(
            "S1", "H1", 8, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random",
                scope={"u1": 4, "u2": 4},
                proportion=0.5, group="g1",
            )
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction = facts["sample:S1"]["fractions"][0]
        self.assertEqual(fraction["inferred_count"], 16.0)
        low, high = fraction["count_95_interval"]
        self.assertAlmostEqual(low, 16 - 1.96 * 8 ** 0.5 * 2, places=6)
        self.assertAlmostEqual(high, 16 + 1.96 * 8 ** 0.5 * 2, places=6)

        zero_sample = build_sample(
            "S2", "H2", 4, [("light", ["u1", "u2", "u3", "u4"])]
        )
        zero_observation = build_observation(
            "z1", "S2", "light", "random", scope={"u1": 0, "u2": 0},
            proportion=0.5, group="gz",
        )
        zero_facts = facts_by_id(build_source([zero_sample], [zero_observation]))
        low, high = zero_facts["sample:S2"]["fractions"][0]["count_95_interval"]
        self.assertEqual(low, 0.0)
        self.assertEqual(high, 6.0)

    def test_recheck_is_counted_once_and_disagreement_is_flagged(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random",
                scope={"u1": 4, "u2": 1},
                proportion=0.5, group="g1",
            ),
            build_observation(
                "o2", "S1", "light", "random",
                scope={"u1": 5, "u2": 1},
                proportion=0.5, group="g1", recheck_of="o1",
            ),
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction = facts["sample:S1"]["fractions"][0]
        self.assertEqual(fraction["observed_count"], 5)
        self.assertFalse(fraction["rechecks"][0]["agrees_with_primary"])
        self.assertEqual(fraction["count_discrepancies"][0]["observations"], ["o1", "o2"])

    def test_overlapping_scopes_split_repeated_and_new_units(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random", scope={"u1": 2},
                proportion=0.25, group="g1",
            ),
            build_observation(
                "o2", "S1", "light", "random",
                scope={"u1": 2, "u2": 1, "u3": 1},
                proportion=0.75, group="g2",
            ),
            build_observation(
                "o3", "S1", "light", "random", scope={"u4": 1},
                proportion=0.25, group="g3",
            ),
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction = facts["sample:S1"]["fractions"][0]
        # u1 的重复观察只计一次，u2/u3/u4 为新增观察。
        self.assertEqual(fraction["observed_count"], 5)
        self.assertEqual(fraction["count_discrepancies"], [])

    def test_recheck_extending_scope_contributes_new_unit_only(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random", scope={"u1": 2},
                proportion=0.25, group="g1",
            ),
            build_observation(
                "o2", "S1", "light", "random", scope={"u1": 3, "u2": 1},
                proportion=0.5, group="g1", recheck_of="o1",
            ),
            build_observation(
                "o3", "S1", "light", "random", scope={"u3": 1},
                proportion=0.25, group="g2",
            ),
            build_observation(
                "o4", "S1", "light", "random", scope={"u4": 1},
                proportion=0.25, group="g3",
            ),
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction = facts["sample:S1"]["fractions"][0]
        recheck = fraction["rechecks"][0]
        self.assertEqual(recheck["repeated_units"], ["u1"])
        self.assertEqual(recheck["new_units"], ["u2"])
        self.assertFalse(recheck["agrees_with_primary"])
        self.assertEqual(fraction["observed_count"], 5)

    def test_true_absence_is_distinct_from_pending_identification(self):
        sample = build_sample(
            "S1", "H1", 10, [("light", ["u1", "u2"]), ("heavy", ["v1"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random", scope={"u1": 0, "u2": 0},
                proportion=1.0, group="g1",
            ),
            build_observation("o2", "S1", "heavy", "pending"),
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction_map = {
            fraction["fraction"]: fraction
            for fraction in facts["sample:S1"]["fractions"]
        }
        light, heavy = fraction_map["light"], fraction_map["heavy"]
        self.assertEqual(light["true_absence_units"], ["u1", "u2"])
        self.assertEqual(light["inferred_count"], 0.0)
        self.assertTrue(heavy["pending_identification"])
        self.assertIn("material_not_identified", heavy["unsupported_reasons"])
        self.assertTrue(facts["sample:S1"]["has_pending_identification"])

    def test_partial_random_coverage_extrapolates_without_full_cover(self):
        sample = build_sample(
            "S1", "H1", 8, [("light", ["u1", "u2", "u3", "u4"])]
        )
        observations = [
            build_observation(
                "o1", "S1", "light", "random", scope={"u1": 4, "u2": 4},
                proportion=0.5, group="g1",
            )
        ]
        facts = facts_by_id(build_source([sample], observations))
        fraction = facts["sample:S1"]["fractions"][0]
        self.assertEqual(fraction["identification_basis"], "random_subsample")
        self.assertEqual(fraction["inferred_count"], 16.0)
        low, high = fraction["count_95_interval"]
        self.assertAlmostEqual(low, 16 - 1.96 * 8 ** 0.5 * 2, places=6)
        self.assertAlmostEqual(high, 16 + 1.96 * 8 ** 0.5 * 2, places=6)
        self.assertTrue(facts["sample:S1"]["whole_sample_inference"]["supported"])

    def test_pit_comparison_flags_interpretability_and_scope(self):
        samples = [
            build_sample("S1", "H1", 20, [("light", ["u1", "u2"])]),
            build_sample("S2", "H2", 6, [("light", ["w1"])]),
            build_sample("S3", "H2", 10, [("heavy", ["v1"])]),
        ]
        observations = [
            build_observation(
                "o1", "S1", "light", "random", scope={"u1": 7, "u2": 7},
                proportion=1.0, group="g1",
            ),
            build_observation("o2", "S2", "light", "complete", scope={"w1": 16}),
            build_observation("o3", "S3", "heavy", "pending"),
        ]
        facts = facts_by_id(build_source(samples, observations))
        self.assertTrue(facts["pit:H1"]["whole_pit_inference"]["supported"])
        self.assertFalse(facts["pit:H2"]["whole_pit_inference"]["supported"])
        comparison = facts["comparison:H1:H2"]
        self.assertFalse(
            comparison["interpretable_as_whole_soil_recovery_difference"]
        )
        self.assertIsNone(comparison["density_difference_per_liter"])
        rows = {row["sample"]: row for row in comparison["samples"]}
        self.assertEqual(rows["S1"]["identification_basis"], "random_subsample")
        self.assertIsNone(rows["S3"]["identification_basis"])
        self.assertIn("material_not_identified", rows["S3"]["unsupported_reasons"])

    def test_interpretable_comparison_reports_difference_and_overlap(self):
        samples = [
            build_sample("S1", "H1", 10, [("light", ["u1"])]),
            build_sample("S2", "H2", 10, [("light", ["w1"])]),
        ]
        observations = [
            build_observation("o1", "S1", "light", "complete", scope={"u1": 5}),
            build_observation("o2", "S2", "light", "complete", scope={"w1": 20}),
        ]
        facts = facts_by_id(build_source(samples, observations))
        comparison = facts["comparison:H1:H2"]
        self.assertTrue(
            comparison["interpretable_as_whole_soil_recovery_difference"]
        )
        self.assertEqual(comparison["density_difference_per_liter"], -1.5)
        self.assertFalse(comparison["density_intervals_overlap"])


if __name__ == "__main__":
    unittest.main()
