from __future__ import annotations

import unittest
import copy
from datetime import datetime, timedelta

from custom.agents.formal_nine_zone_experiment import (
    _dispatch_selected_ride_requests,
    _dispatch_group_rank,
    _dispatch_priority,
    load_formal_nine_zone_config,
)
from scripts.run_formal_nine_zone_50_elder_dispatch_priority import (
    DEFAULT_CONFIG_PATH,
    _load,
)


class FormalNineZoneElderDispatchPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.younger = {"agent_id": 1, "age_group": "18-39"}
        self.elder = {"agent_id": 2, "age_group": "60+"}
        self.leg = {"leg_id": "leg-1"}

    def test_configuration_uses_p0_p4_and_selected_twelve_vehicle_fleet(self):
        config = _load(DEFAULT_CONFIG_PATH)
        self.assertEqual(sum(config["initial_vehicles"].values()), 12)
        self.assertEqual(list(config["policies"]), ["P0_first_come", "P4_elder_priority"])

    def test_p4_changes_only_elder_group_rank(self):
        p0 = {"ride_hailing_fleet": {"dispatch_priority_policy": "P0_first_come"}}
        p4 = {"ride_hailing_fleet": {"dispatch_priority_policy": "P4_elder_priority"}}
        self.assertEqual(_dispatch_group_rank(p0, self.younger, self.leg), 0)
        self.assertEqual(_dispatch_group_rank(p0, self.elder, self.leg), 0)
        self.assertEqual(_dispatch_group_rank(p4, self.elder, self.leg), 0)
        self.assertEqual(_dispatch_group_rank(p4, self.younger, self.leg), 1)

    def test_p4_reorders_only_still_pending_requests_when_vehicle_releases(self):
        start = datetime(2026, 7, 6, 8, 0)
        agents = {
            1: {"agent_id": 1, "age_group": "18-39"},
            2: {"agent_id": 2, "age_group": "18-39"},
            3: {"agent_id": 3, "age_group": "60+"},
        }
        selected = []
        for agent_id, minute, duration in ((1, 0, 10), (2, 1, 1), (3, 2, 1)):
            selected.append({
                "chosen_mode": "ride_hailing",
                "chosen_option": {
                    "period_wait_time_min": 0.0,
                    "final_in_vehicle_time_min": float(duration),
                },
                "leg": {
                    "leg_id": f"leg-{agent_id}", "agent_id": agent_id,
                    "departure_time": start + timedelta(minutes=minute),
                    "origin_zone": "Z1", "destination_zone": "Z1",
                },
            })
        base = load_formal_nine_zone_config()
        base["ride_hailing_fleet"]["initial_vehicles_by_day_type"]["workday"] = {"Z1": 1}
        base["ride_hailing_fleet"]["maximum_vehicle_wait_min"] = 20.0

        p0 = copy.deepcopy(base)
        p0["ride_hailing_fleet"]["dispatch_priority_policy"] = "P0_first_come"
        p4 = copy.deepcopy(base)
        p4["ride_hailing_fleet"]["dispatch_priority_policy"] = "P4_elder_priority"
        p0_rows, _ = _dispatch_selected_ride_requests(selected, agents, p0, "workday", 47)
        p4_rows, _ = _dispatch_selected_ride_requests(selected, agents, p4, "workday", 47)

        self.assertEqual(p0_rows["leg-1"]["dispatch_time"], 8 * 60)
        self.assertEqual(p4_rows["leg-1"]["dispatch_time"], 8 * 60)
        self.assertLess(p0_rows["leg-2"]["dispatch_time"], p0_rows["leg-3"]["dispatch_time"])
        self.assertLess(p4_rows["leg-3"]["dispatch_time"], p4_rows["leg-2"]["dispatch_time"])

    def test_base_dispatch_priority_does_not_depend_on_policy_or_age(self):
        expected = _dispatch_priority(47, "leg-1")
        self.assertEqual(expected, _dispatch_priority(47, "leg-1"))


if __name__ == "__main__":
    unittest.main()
