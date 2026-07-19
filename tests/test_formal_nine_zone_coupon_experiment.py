from __future__ import annotations

import copy
import unittest

from custom.agents.formal_nine_zone_experiment import (
    _available_to_agent,
    _score_options,
    load_formal_nine_zone_config,
)
from scripts.run_formal_nine_zone_50_coupon_experiment import DEFAULT_CONFIG_PATH, _load


class FormalNineZoneCouponTests(unittest.TestCase):
    def test_formal_coupon_configuration_uses_selected_baseline(self):
        config = _load(DEFAULT_CONFIG_PATH)
        self.assertEqual(sum(config["initial_vehicles"].values()), 12)
        self.assertEqual(config["coupon_experiment"]["discount_multiplier"], 0.8)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 10)

    def test_coupon_changes_only_ride_hailing_fare_in_utility(self):
        config = load_formal_nine_zone_config()
        config = copy.deepcopy(config)
        config["_coupon_discount_multiplier"] = 0.8
        leg = {
            "agent_id": 1, "leg_id": "coupon-test", "departure_time": __import__("datetime").datetime(2026, 7, 7, 8),
            "target_arrival_time": None,
        }
        agent = {"agent_id": 1, "age_group": "18-39", "digital_access": True, "family_assistance": False}
        options = {
            mode: {"mode": mode, "available": True, "final_total_time_min": 20.0, "fare": fare}
            for mode, fare in (("walk", 0.0), ("bus", 2.0), ("metro", 4.0), ("ride_hailing", 20.0))
        }
        full = {row["mode"]: row for row in _score_options(leg, agent, options, [], config, 47)}
        discounted = {
            row["mode"]: row for row in _score_options(
                leg, agent, options, [], config, 47, coupon_available=True,
            )
        }
        for mode in ("walk", "bus", "metro"):
            self.assertEqual(full[mode]["fare"], discounted[mode]["fare"])
        self.assertEqual(full["ride_hailing"]["fare"], 20.0)
        self.assertEqual(discounted["ride_hailing"]["fare"], 16.0)

    def test_community_proxy_does_not_make_other_modes_or_permanent_access(self):
        agent = {"digital_access": False, "family_assistance": False}
        option = {"available": True}
        self.assertFalse(_available_to_agent("ride_hailing", option, agent))
        self.assertTrue(_available_to_agent(
            "ride_hailing", option, agent, coupon_proxy_access=True,
        ))
        self.assertFalse(agent["digital_access"])
        self.assertFalse(agent["family_assistance"])


if __name__ == "__main__":
    unittest.main()
