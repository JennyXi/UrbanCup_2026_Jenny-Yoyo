from __future__ import annotations

import json
import unittest
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "config" / "formal_nine_zone_200_supply_confirmation.json"


class FormalNineZone200SupplyConfirmationTest(unittest.TestCase):
    def test_ratio_and_formal_recalibration_are_explicit(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        scaling = config["experimental_supply_scaling"]
        self.assertAlmostEqual(
            scaling["provisional_baseline_vehicles"] / scaling["reference_agent_count"],
            scaling["vehicles_per_agent"],
        )
        self.assertEqual(scaling["vehicles_per_100_agents"], 18.0)
        self.assertTrue(scaling["formal_scale_recalibration_required"])
        self.assertIn("formal_agent_count", scaling["proportional_scale_starting_rule"])

    def test_confirmation_uses_a1_and_nested_36_24_pools(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        self.assertEqual(config["seed_count"], 10)
        pools = {int(key): value for key, value in config["vehicle_pools_by_total"].items()}
        self.assertEqual(set(pools), {24, 36})
        self.assertEqual(sum(pools[36].values()), 36)
        self.assertEqual(sum(pools[24].values()), 24)
        self.assertTrue(all(pools[24][zone] <= pools[36][zone] for zone in pools[36]))
        rates = config["mode_choice_override"]["weather_exposure_disutility"][
            "utility_penalty_per_outdoor_minute"
        ]
        self.assertEqual(rates, {"normal": 0.0, "extreme_heat": 0.015, "heavy_rain": 0.012})


if __name__ == "__main__":
    unittest.main()
