from __future__ import annotations

import unittest
from pathlib import Path

from custom.agents.formal_nine_zone_50_experiment import load_formal_50_config
from scripts.run_formal_nine_zone_200_baseline import _add_scale_metrics


CONFIG = Path(__file__).resolve().parents[1] / "config" / "formal_nine_zone_200_baseline.json"


class FormalNineZone200BaselineTest(unittest.TestCase):
    def test_scale_and_run_scope_are_explicit(self):
        config = load_formal_50_config(CONFIG)
        self.assertEqual(config["total_agents"], 200)
        self.assertEqual(config["run_weather_scenarios"], ["W0", "W1", "W2"])
        self.assertEqual(config["run_day_types"], ["workday"])
        self.assertEqual(config["policy"], "P0_no_policy")
        fleet = config["formal_overrides"]["ride_hailing_fleet"]["initial_vehicles_by_day_type"]["workday"]
        self.assertEqual(sum(fleet.values()), 48)
        self.assertEqual(set(fleet), {f"Z{index}" for index in range(1, 10)})

    def test_public_transport_is_not_scaled_with_population(self):
        config = load_formal_50_config(CONFIG)
        scale = config["scale_definition"]
        self.assertFalse(scale["public_transport_service_scaled_with_population"])
        self.assertFalse(scale["public_transport_capacity_constraint_enabled"])

    def test_per_100_agent_metrics_are_normalized(self):
        row = {
            "agent_count": 200, "ride_hailing_requests": 20,
            "successful_ride_hailing_requests": 16,
            "failed_ride_hailing_requests": 4, "fallback_attempts": 4,
            "transport_related_unmet": 2, "mandatory_activity_incomplete": 1,
        }
        scaled = _add_scale_metrics(row)
        self.assertEqual(scaled["ride_hailing_requests_per_100_agents"], 10.0)
        self.assertEqual(scaled["mandatory_activity_incomplete_per_100_agents"], 0.5)


if __name__ == "__main__":
    unittest.main()
