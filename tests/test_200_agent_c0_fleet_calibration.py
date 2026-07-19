import tempfile
import unittest
from pathlib import Path

from scripts.run_200_agent_c0_fleet_calibration import (
    load_calibration_config,
    run_calibration,
)


class Agent200C0FleetCalibrationTests(unittest.TestCase):
    def test_config_preserves_expected_day_type_fleet_totals(self):
        tiers = load_calibration_config()["fleet_tiers"]
        self.assertEqual([sum(tiers[name]["workday"].values()) for name in tiers], [26, 39, 52])
        self.assertEqual([sum(tiers[name]["rest_day"].values()) for name in tiers], [22, 33, 44])

    def test_one_tier_smoke_outputs_and_conserves_vehicles(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_calibration(
                seed_start=3001,
                seed_count=1,
                output=Path(temp),
                tier_names=["low"],
            )
            self.assertEqual(len(result["c0_fleet_per_seed"]), 3 * 2)
            self.assertEqual(len(result["c0_fleet_summary"]), 3 * 2)
            self.assertTrue(all(row["passed"] for row in result["c0_fleet_consistency_checks"]))
            self.assertTrue(all(row["noncapacity_failures"] == 0 for row in result["c0_fleet_per_seed"]))
            self.assertEqual(
                {
                    "c0_fleet_per_seed.csv", "c0_fleet_summary.csv",
                    "c0_fleet_end_zone_states.csv", "c0_fleet_consistency_checks.csv",
                    "experiment_metadata.json",
                },
                {path.name for path in Path(temp).iterdir()},
            )


if __name__ == "__main__":
    unittest.main()
