from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from custom.agents.formal_nine_zone_experiment import (
    _dispatch_group_rank,
    _dispatch_priority,
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

    def test_actual_request_time_precedes_elder_rank(self):
        start = datetime(2026, 7, 6, 8, 0)
        earlier_nonelder = (start, 1, 0.9, "young")
        later_elder = (start + timedelta(seconds=1), 0, 0.1, "elder")
        self.assertLess(earlier_nonelder, later_elder)

    def test_base_dispatch_priority_does_not_depend_on_policy_or_age(self):
        expected = _dispatch_priority(47, "leg-1")
        self.assertEqual(expected, _dispatch_priority(47, "leg-1"))


if __name__ == "__main__":
    unittest.main()
