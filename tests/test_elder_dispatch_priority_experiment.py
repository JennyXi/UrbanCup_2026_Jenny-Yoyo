import tempfile
import unittest
from pathlib import Path

from custom.agents.agent_population import AgentProfile
from custom.agents.emergence_experiment import _elder_dispatch_rank
from scripts.run_elder_dispatch_priority_experiment import load_priority_config, run_priority_experiment


class ElderDispatchPriorityTests(unittest.TestCase):
    def setUp(self):
        self.elder = AgentProfile(1, "60+", (60, 100), True, True, False, "elder")
        self.younger = AgentProfile(2, "18-39", (18, 39), False, True, None, "younger")

    def test_priority_rules_are_narrow_and_transparent(self):
        medical = {"activity_purpose": "medical"}
        shopping = {"activity_purpose": "shopping"}
        self.assertEqual(_elder_dispatch_rank("R0_first_come", medical, self.elder), 0)
        self.assertEqual(_elder_dispatch_rank("R0_first_come", medical, self.younger), 0)
        self.assertEqual(_elder_dispatch_rank("R1_elder_medical_priority", medical, self.elder), 0)
        self.assertEqual(_elder_dispatch_rank("R1_elder_medical_priority", shopping, self.elder), 1)
        self.assertEqual(_elder_dispatch_rank("R1_elder_medical_priority", medical, self.younger), 1)
        self.assertEqual(_elder_dispatch_rank("R2_all_elder_priority", shopping, self.elder), 0)
        self.assertEqual(_elder_dispatch_rank("R2_all_elder_priority", shopping, self.younger), 1)

    def test_one_seed_smoke_conserves_fleet_and_common_base_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_priority_experiment(seed_start=3001, seed_count=1, output=Path(temp))
            self.assertEqual(len(result["priority_system_per_seed"]), 3 * 3 * 2)
            self.assertTrue(all(row["passed"] for row in result["priority_consistency_checks"]))
            self.assertTrue(all(row["shared_base_dispatch_priorities_identical"]
                                for row in result["priority_common_random_checks"]))

    def test_config_is_no_coupon_and_fixed_fleet(self):
        config = load_priority_config()
        self.assertEqual(config["coupon_policy"], "C0_no_coupon")
        self.assertEqual(sum(config["initial_daily_vehicles_by_day_type"]["workday"].values()), 52)
        self.assertEqual(sum(config["initial_daily_vehicles_by_day_type"]["rest_day"].values()), 44)


if __name__ == "__main__":
    unittest.main()
