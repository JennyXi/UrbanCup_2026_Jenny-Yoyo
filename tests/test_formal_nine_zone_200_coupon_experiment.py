from __future__ import annotations

import json
import unittest
from pathlib import Path

from custom.agents.coupon_experiment import COUPON_POLICIES


CONFIG = Path(__file__).resolve().parents[1] / "config" / "formal_nine_zone_200_coupon_experiment.json"


class FormalNineZone200CouponExperimentTest(unittest.TestCase):
    def test_confirmed_supply_a1_and_coupon_scale(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        self.assertEqual(sum(config["initial_vehicles"].values()), 36)
        self.assertEqual(config["expected_initial_vehicle_total"], 36)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 40)
        self.assertEqual(tuple(config["coupon_experiment"]["policies"]), COUPON_POLICIES)
        self.assertEqual(config["weather_scenarios"], ["W0", "W1", "W2"])
        rates = config["mode_choice_override"]["weather_exposure_disutility"][
            "utility_penalty_per_outdoor_minute"
        ]
        self.assertEqual(rates["extreme_heat"], 0.015)
        self.assertEqual(rates["heavy_rain"], 0.012)

    def test_coupon_rules_are_limited_and_once_daily(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))["coupon_experiment"]
        self.assertEqual(config["discount_multiplier"], 0.8)
        self.assertEqual(config["maximum_coupons_per_agent_day"], 1)
        self.assertEqual(config["maximum_redemptions_per_agent_day"], 1)
        self.assertLess(config["community_phone_coverage_rate"], 1.0)
        self.assertEqual(config["main_experiment_ride_hailing_noncapacity_success_probability"], 1.0)


if __name__ == "__main__":
    unittest.main()
