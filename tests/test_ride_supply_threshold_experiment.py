import tempfile
import unittest
from pathlib import Path

from custom.agents.emergence_experiment import load_emergence_config
from scripts.run_ride_supply_threshold_experiment import (
    build_marginal_changes, run_supply_threshold_experiment,
)


class RideSupplyThresholdExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_emergence_config()
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = run_supply_threshold_experiment(
            seed_start=3001, seed_count=1,
            output=Path(cls.temp.name), config=cls.config,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_grid_is_fixed_and_includes_p0(self):
        grid = self.config["ride_supply_threshold_experiment"]["ride_supply_multipliers"]
        self.assertEqual(grid, [0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0])

    def test_output_has_every_supply_weather_and_day_combination(self):
        self.assertEqual(len(self.result["per_seed"]), 8 * 3 * 2)
        self.assertEqual(len(self.result["aggregate"]), 8 * 3 * 2)
        self.assertEqual(len(self.result["marginal"]), 7 * 3 * 2)

    def test_bus_schedule_is_fixed_across_ride_supply_grid(self):
        self.assertTrue(all(
            row["scheduled_bus_vehicle_trips_constant"]
            for row in self.result["schedule_checks"]
        ))

    def test_constrained_road_reference_is_local_to_threshold_experiment(self):
        configured = self.config["ride_supply_threshold_experiment"][
            "fixed_reference_road_vehicles_per_30_min"
        ]
        self.assertEqual(configured, 10.0)
        self.assertEqual(self.config["road_feedback"]["reference_road_vehicles_per_30_min"], 14.0)
        self.assertTrue(all(
            row["reference_road_vehicles_per_30_min"] == configured
            for row in self.result["per_seed"]
        ))

    def test_shared_road_volume_conserves_bus_plus_ride(self):
        for row in self.result["per_seed"]:
            self.assertEqual(
                row["road_vehicle_volume"],
                row["scheduled_bus_vehicle_trips"]
                + row["successful_ride_hailing_vehicle_trips"],
            )

    def test_total_travel_time_includes_in_vehicle_and_non_wait_components(self):
        for row in self.result["per_seed"]:
            self.assertGreaterEqual(row["total_travel_time_minutes"], 0.0)
            self.assertGreaterEqual(
                row["total_travel_time_minutes"], row["total_in_vehicle_time_minutes"]
            )
            self.assertGreaterEqual(
                row["total_travel_time_minutes"], row["total_non_wait_travel_time_minutes"]
            )

    def test_threshold_summary_covers_each_weather_and_day(self):
        self.assertEqual(len(self.result["thresholds"]), 3 * 2)
        self.assertTrue(all(
            row["candidate_is_mechanism_rule_not_optimum"]
            for row in self.result["thresholds"]
        ))

    def test_zero_baseline_marginal_percent_is_blank(self):
        grid = [1.0, 1.2]
        aggregate = []
        for week in ("W0", "W1", "W2"):
            for day_type in ("workday", "rest_day"):
                for supply in grid:
                    aggregate.append({
                        "weather_scenario": week, "day_type": day_type,
                        "ride_supply_multiplier": supply,
                        "necessary_activity_completion_rate": 1.0,
                        "activity_completion_rate": 1.0,
                        "mean_ride_hailing_wait_minutes_per_request": 0.0,
                        "transport_related_unmet": 0.0,
                        "road_vehicle_volume": 0.0,
                        "mean_road_speed_kmh": 0.0,
                        "minimum_road_speed_multiplier": 0.0,
                        "mean_total_travel_time": 0.0,
                        "total_travel_time_minutes": 0.0,
                        "total_in_vehicle_time_minutes": 0.0,
                        "peak_road_volume_capacity_ratio": 0.0,
                    })
        rows = build_marginal_changes(aggregate, grid)
        self.assertTrue(all(row["ride_wait_reduction_percent"] == "" for row in rows))
        self.assertTrue(all(row["road_vehicle_volume_change_percent"] == "" for row in rows))


if __name__ == "__main__":
    unittest.main()
