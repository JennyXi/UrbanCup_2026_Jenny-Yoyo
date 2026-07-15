import tempfile
import unittest
from pathlib import Path

from scripts.run_coupon_coverage_threshold_experiment import load_threshold_config, run_threshold_experiment


class CouponCoverageThresholdTests(unittest.TestCase):
    def test_configured_grid_is_zero_to_forty_percent(self):
        config = load_threshold_config()
        self.assertEqual(config["total_agents"], 200)
        self.assertEqual(config["coupon_pool_grid"], [0, 20, 40, 60, 80])
        self.assertEqual(config["discount_multiplier"], 0.8)

    def test_two_point_smoke_has_common_nested_awards_and_valid_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_threshold_experiment(seed_start=3001, seed_count=1,
                                              output=Path(temp), pools=[0, 20])
            self.assertEqual(len(result["coverage_per_seed"]), 2 * 3 * 2)
            self.assertTrue(all(row["passed"] for row in result["coverage_consistency_checks"]))
            self.assertTrue(all(row["public_awards_nested_across_pool_grid"]
                                for row in result["coverage_nested_award_checks"]))
            zero = [row for row in result["coverage_per_seed"] if row["coupon_pool"] == 0]
            twenty = [row for row in result["coverage_per_seed"] if row["coupon_pool"] == 20]
            self.assertTrue(all(row["coupon_awarded"] == 0 for row in zero))
            self.assertTrue(all(row["coupon_awarded"] <= 20 for row in twenty))
            self.assertEqual(len(result["coverage_candidate_thresholds"]), 7 * 4)


if __name__ == "__main__":
    unittest.main()
