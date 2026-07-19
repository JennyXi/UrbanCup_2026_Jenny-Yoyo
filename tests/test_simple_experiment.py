import tempfile
import unittest
from pathlib import Path

from custom.agents.simple_experiment import run_experiment, write_experiment_outputs


class SimpleExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_experiment(50, seed=2026)

    def test_main_population_identity_ratios_are_reused(self):
        population = self.result["population"]
        self.assertEqual(population["total_agents"], 50)
        self.assertEqual(population["age_group_counts"], {"18-39": 20, "40-59": 17, "60+": 13})
        self.assertEqual(sum(population["home_zone_counts"].values()), 50)
        self.assertEqual(population["home_zone_counts"], {"S1": 20, "S2": 30})

    def test_every_agent_has_two_trips_in_every_weather(self):
        self.assertEqual(len(self.result["activity_decisions"]), 50 * 3)
        self.assertEqual(len(self.result["system_summaries"]), 3)
        self.assertTrue(all(row["planned_trip_count"] == 100 for row in self.result["system_summaries"]))
        self.assertTrue(all(
            row["trip_count"] + row["cancelled_trip_count"] == 100
            for row in self.result["system_summaries"]
        ))

    def test_system_counts_and_shares_are_consistent(self):
        for row in self.result["system_summaries"]:
            self.assertEqual(sum(row["mode_trip_counts"].values()), row["trip_count"])
            self.assertAlmostEqual(sum(row["mode_shares"].values()), 1.0, places=3)
            self.assertEqual(row["additional_road_flow_pcu_per_representative_day"], row["ride_hailing_vehicle_trips"])

    def test_w0_never_cancels_and_adverse_weather_can_cancel(self):
        by_week = {row["weather_week"]: row for row in self.result["system_summaries"]}
        self.assertEqual(by_week["W0"]["cancelled_activity_count"], 0)
        self.assertEqual(by_week["W0"]["trip_count"], 100)
        self.assertGreater(
            by_week["W1"]["cancelled_activity_count"] + by_week["W2"]["cancelled_activity_count"], 0
        )

    def test_fixed_seed_is_reproducible(self):
        self.assertEqual(self.result, run_experiment(50, seed=2026))

    def test_adverse_weather_retains_non_deterministic_alternatives(self):
        by_week = {row["weather_week"]: row for row in self.result["system_summaries"]}
        self.assertGreater(by_week["W1"]["mode_trip_counts"]["walk"], 0)
        w2_digital = [
            row for row in self.result["decisions"]
            if row["weather_week"] == "W2" and row["digital_access"] is True
        ]
        self.assertTrue(w2_digital)
        self.assertTrue(any(row["chosen_mode"] != "ride_hailing" for row in w2_digital))

    def test_feedback_wait_matches_first_round_demand_and_never_decreases(self):
        summaries = sorted(
            self.result["system_summaries"], key=lambda row: row["first_round_ride_hailing_requests"]
        )
        waits = [row["ride_hailing_feedback_wait_min"] for row in summaries]
        self.assertEqual(waits, sorted(waits))

    def test_outputs_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_experiment_outputs(self.result, Path(directory))
            self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in paths.values()))


if __name__ == "__main__":
    unittest.main()
