import unittest

from scripts.run_200_agent_coupon_experiment import (
    build_run_config,
    load_agent_200_coupon_config,
)


class Agent200CouponExperimentTests(unittest.TestCase):
    def test_profile_scales_population_coupon_coverage_and_fleet(self):
        profile = load_agent_200_coupon_config()
        config = build_run_config(profile)
        self.assertEqual(config["total_agents"], 200)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 40)
        fleets = config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"]
        self.assertEqual(sum(fleets["workday"].values()), 52)
        self.assertEqual(sum(fleets["rest_day"].values()), 44)

    def test_profile_keeps_noncapacity_failure_disabled(self):
        config = build_run_config(load_agent_200_coupon_config())
        self.assertEqual(
            config["coupon_experiment"][
                "main_experiment_ride_hailing_noncapacity_success_probability"
            ],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
