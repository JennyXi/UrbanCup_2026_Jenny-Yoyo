import tempfile
import unittest
from pathlib import Path

from custom.agents.agent_population import generate_population_agents
from custom.agents.emergence_experiment import load_emergence_config
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import load_symmetric_experiment_config
from scripts.run_elder_digital_access_experiment import (
    apply_digital_policy, run_digital_access_experiment,
)


class ElderDigitalAccessExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_emergence_config()
        cls.symmetric = load_symmetric_experiment_config()
        cls.base = assign_two_zone_homes(
            generate_population_agents(50, seed=3001), seed=3001,
            s2_share=float(cls.symmetric["s2_home_share"]),
        )
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = run_digital_access_experiment(
            seed_start=3001, seed_count=1, output=Path(cls.temp.name), config=cls.config,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def policy_profiles(self, policy):
        return apply_digital_policy(self.base, policy, seed=3001, config=self.config)

    def test_d0_preserves_every_profile(self):
        policy = self.policy_profiles("D0_baseline")
        self.assertEqual([row.to_dict() for row in policy], [row.to_dict() for row in self.base])

    def test_d1_reaches_target_without_changing_smartphones_or_assistance(self):
        policy = self.policy_profiles("D1_targeted_digital_training_75pct")
        elders = [row for row in policy if row.is_elder]
        self.assertEqual(sum(row.digital_access for row in elders), int(len(elders) * 0.75 + 0.5))
        base = {row.agent_id: row for row in self.base}
        self.assertTrue(all(row.smartphone_access == base[row.agent_id].smartphone_access for row in policy))
        self.assertTrue(all(row.family_assistance == base[row.agent_id].family_assistance for row in policy))

    def test_d2_only_increases_elder_family_assistance(self):
        policy = self.policy_profiles("D2_family_assistance_90pct")
        elders = [row for row in policy if row.is_elder]
        self.assertEqual(sum(bool(row.family_assistance) for row in elders), int(len(elders) * 0.90 + 0.5))
        base = {row.agent_id: row for row in self.base}
        self.assertTrue(all(row.digital_access == base[row.agent_id].digital_access for row in policy))
        self.assertTrue(all(row.smartphone_access == base[row.agent_id].smartphone_access for row in policy))

    def test_d3_provides_universal_elder_device_and_access(self):
        policy = self.policy_profiles("D3_universal_elder_digital_access")
        elders = [row for row in policy if row.is_elder]
        self.assertTrue(all(row.smartphone_access and row.digital_access for row in elders))

    def test_nonelder_profiles_never_change(self):
        base = {row.agent_id: row for row in self.base if not row.is_elder}
        for policy_name in self.config["elder_digital_access_experiment"]["policies"]:
            policy = {row.agent_id: row for row in self.policy_profiles(policy_name) if not row.is_elder}
            self.assertEqual(
                {key: row.to_dict() for key, row in policy.items()},
                {key: row.to_dict() for key, row in base.items()},
            )

    def test_policy_assignment_is_reproducible(self):
        first = self.policy_profiles("D1_targeted_digital_training_75pct")
        second = self.policy_profiles("D1_targeted_digital_training_75pct")
        self.assertEqual([row.to_dict() for row in first], [row.to_dict() for row in second])

    def test_digital_policy_does_not_change_weather_cancellation(self):
        self.assertTrue(all(
            row["weather_cancellation_changes_vs_d0"] == 0
            for row in self.result["consistency_checks"]
        ))

    def test_nondigital_unassisted_agents_never_use_ride_hailing(self):
        self.assertTrue(all(
            row["nondigital_unassisted_illegal_ride_legs"] == 0
            for row in self.result["consistency_checks"]
        ))

    def test_group_planned_activity_counts_conserve_system_total(self):
        groups = self.result["group_per_seed"]
        for system in self.result["system_per_seed"]:
            matching = [row for row in groups if
                        row["seed"] == system["seed"] and row["policy"] == system["policy"]
                        and row["weather_scenario"] == system["weather_week"]
                        and row["day_type"] == system["day_type"]]
            self.assertEqual(sum(row["planned_activities"] for row in matching), system["planned_activities"])

    def test_all_expected_tables_are_written(self):
        expected = {
            "system_per_seed.csv", "group_per_seed.csv", "system_distribution.csv",
            "group_distribution.csv", "system_policy_changes.csv",
            "group_policy_changes.csv", "intervention_roster.csv",
            "consistency_checks.csv", "experiment_metadata.json",
        }
        self.assertEqual(expected, {row.name for row in Path(self.temp.name).iterdir()})


if __name__ == "__main__":
    unittest.main()
