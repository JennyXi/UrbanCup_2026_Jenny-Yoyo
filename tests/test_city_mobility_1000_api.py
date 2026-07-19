from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from custom.agents.formal_nine_zone_50_experiment import load_formal_50_config
from scripts.run_city_mobility_1000_api import (
    _coupon_validator,
    _travel_validator,
)


ROOT = Path(__file__).resolve().parents[1]


class CityMobility1000APITest(unittest.TestCase):
    def test_config_is_explicitly_1000_and_uses_main_elder_v2(self) -> None:
        path = ROOT / "config" / "city_mobility_1000_api.json"
        config = load_formal_50_config(path)
        self.assertEqual(config["total_agents"], 1000)
        self.assertTrue(
            config["comparability"]["age_weather_exposure_multiplier_loaded"]
        )
        self.assertEqual(
            config["comparability"]["age_weather_exposure_version"],
            "A1_main_stable_elder_behavior_7d21a4f",
        )
        self.assertFalse(
            config["comparability"]["strictly_comparable_to_200_age_behavior"]
        )
        self.assertEqual(config["scale_definition"]["ride_hailing_vehicles"], 240)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 200)
        self.assertEqual(config["scale_definition"]["represented_trips_per_agent"], 6.0)

    def test_travel_validator_never_falls_back(self) -> None:
        validate = _travel_validator({"bus", "metro"})
        self.assertEqual(
            validate({"mode": "metro", "reason": "faster"})["mode"], "metro"
        )
        with self.assertRaises(ValueError):
            validate({"mode": "ride_hailing", "reason": "not available"})
        with self.assertRaises(ValueError):
            validate(None)

    def test_coupon_validator_enforces_integer_endowment(self) -> None:
        validate = _coupon_validator(20)
        self.assertEqual(
            validate({"contribution_tokens": 12, "reason": "cooperate"})[
                "contribution_tokens"
            ],
            12,
        )
        with self.assertRaises(ValueError):
            validate({"contribution_tokens": 21, "reason": "too much"})
        with self.assertRaises(ValueError):
            validate({"contribution_tokens": 10.5, "reason": "fractional"})


if __name__ == "__main__":
    unittest.main()
