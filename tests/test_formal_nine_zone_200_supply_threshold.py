from __future__ import annotations

import json
import unittest
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "config" / "formal_nine_zone_200_supply_threshold.json"


class FormalNineZone200SupplyThresholdTest(unittest.TestCase):
    def test_fleet_maps_are_nested_and_conserved(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        pools = {int(total): zones for total, zones in config["vehicle_pools_by_total"].items()}
        ordered = sorted(pools, reverse=True)
        for total, zones in pools.items():
            self.assertEqual(sum(zones.values()), total)
            self.assertEqual(set(zones), {f"Z{i}" for i in range(1, 10)})
            self.assertTrue(all(value >= 1 for value in zones.values()))
        for high, low in zip(ordered, ordered[1:]):
            self.assertTrue(all(pools[low][zone] <= pools[high][zone] for zone in pools[high]))

    def test_a1_is_fixed_without_changing_normal_weather(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        exposure = config["mode_choice_override"]["weather_exposure_disutility"]
        self.assertEqual(exposure["utility_penalty_per_outdoor_minute"]["normal"], 0.0)
        self.assertEqual(exposure["utility_penalty_per_outdoor_minute"]["extreme_heat"], 0.015)
        self.assertEqual(exposure["utility_penalty_per_outdoor_minute"]["heavy_rain"], 0.012)
        self.assertGreater(exposure["age_vulnerability_weight"]["60+"],
                           exposure["age_vulnerability_weight"]["18-39"])


if __name__ == "__main__":
    unittest.main()
