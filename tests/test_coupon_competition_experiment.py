import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from custom.agents.agent_population import generate_population_agents
from custom.agents.agent_population import AgentProfile
from custom.agents.coupon_experiment import (
    COUPON_POLICIES, allocate_daily_coupons, community_assisted_booking,
)
from custom.agents.emergence_experiment import (
    _agent, _coupon_discounted_choice, build_emergence_activities,
    load_emergence_config,
)
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import load_symmetric_experiment_config
from scripts.run_coupon_competition_experiment import (
    GROUPS, _main_symmetric_config, run_coupon_experiment, run_coupon_policy,
    summarize_coupon_groups,
)


class CouponAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = 3001
        cls.config = load_emergence_config()
        symmetric = load_symmetric_experiment_config()
        cls.profiles = assign_two_zone_homes(
            generate_population_agents(50, seed=cls.seed), seed=cls.seed,
            s2_share=float(symmetric["s2_home_share"]),
        )

    def allocations(self, policy, day_type="workday"):
        return allocate_daily_coupons(
            self.profiles, policy, day_type, seed=self.seed, config=self.config,
        )

    def test_policy_pool_limits_and_mixed_split(self):
        pool = self.config["coupon_experiment"]["daily_total_coupon_pool"]
        for policy in COUPON_POLICIES:
            rows = self.allocations(policy)
            self.assertLessEqual(sum(row["coupon_awarded"] for row in rows), pool)
            self.assertEqual(len({row["agent_id"] for row in rows}), 50)
        mixed = self.allocations("C3_mixed")
        self.assertLessEqual(sum(row["coupon_pool_type"] == "public" for row in mixed), 7)
        self.assertLessEqual(sum(row["coupon_pool_type"] == "elder_reserved" for row in mixed), 3)

    def test_at_most_one_coupon_per_agent_day(self):
        for policy in COUPON_POLICIES:
            rows = self.allocations(policy)
            awarded = [row["agent_id"] for row in rows if row["coupon_awarded"]]
            self.assertEqual(len(awarded), len(set(awarded)))

    def test_common_public_random_numbers_across_policies(self):
        c1 = {row["agent_id"]: row for row in self.allocations("C1_public_limited")}
        c3 = {row["agent_id"]: row for row in self.allocations("C3_mixed")}
        for agent_id in c1:
            self.assertEqual(c1[agent_id]["public_participation_draw"], c3[agent_id]["public_participation_draw"])
            self.assertEqual(c1[agent_id]["public_dispatch_rank"], c3[agent_id]["public_dispatch_rank"])

    def test_nondigital_unassisted_elder_never_joins_public_pool(self):
        for policy in ("C1_public_limited", "C3_mixed"):
            blocked = [row for row in self.allocations(policy) if row["nondigital_unassisted"]]
            self.assertTrue(blocked)
            self.assertTrue(all(not row["public_coupon_participated"] for row in blocked))
            self.assertTrue(all(row["coupon_pool_type"] != "public" for row in blocked))

    def test_community_phone_coverage_is_limited(self):
        rate = self.config["coupon_experiment"]["community_phone_coverage_rate"]
        self.assertGreater(rate, 0.0)
        self.assertLess(rate, 1.0)

    def test_community_proxy_booking_requires_awarded_c3_reserve_coupon(self):
        allocation = {
            "coupon_policy": "C3_mixed", "coupon_awarded": True,
            "coupon_pool_type": "elder_reserved",
            "coupon_access_channel": "community_phone",
            "nondigital_unassisted": True,
        }
        self.assertTrue(community_assisted_booking(allocation))
        for changed in (
            {**allocation, "coupon_policy": "C2_elder_limited"},
            {**allocation, "coupon_awarded": False},
            {**allocation, "coupon_pool_type": "public"},
        ):
            self.assertFalse(community_assisted_booking(changed))

    def test_proxy_booking_does_not_change_digital_access(self):
        profile = AgentProfile(
            agent_id=999, age_group="60+", age_range=(60, 100), is_elder=True,
            digital_access=False, family_assistance=False, segment="test",
        )
        ordinary = _agent(profile)
        assisted = _agent(profile, community_booking_assistance=True)
        self.assertFalse(ordinary.digital_access)
        self.assertFalse(ordinary.family_assistance)
        self.assertFalse(assisted.digital_access)
        self.assertTrue(assisted.family_assistance)

    def test_coupon_changes_only_ride_fare_component_of_utility(self):
        choice = {
            "chosen_mode": "bus", "chosen_time_min": 20.0, "chosen_fare_yuan": 2.0,
            "alternatives": [
                {"mode": "walk", "fare_yuan": 0.0, "travel_time_min": 30.0, "utility": -3.0},
                {"mode": "bus", "fare_yuan": 2.0, "travel_time_min": 20.0, "utility": -2.0},
                {"mode": "ride_hailing", "fare_yuan": 20.0, "travel_time_min": 10.0, "utility": -4.0},
            ],
        }
        transport = {"choice_weights": {"generalized_cost": 1.0}}
        discounted = _coupon_discounted_choice(
            choice, discount_multiplier=0.8, transport=transport,
        )
        full = {row["mode"]: row for row in choice["alternatives"]}
        changed = {row["mode"]: row for row in discounted["alternatives"]}
        self.assertEqual(changed["walk"]["utility"], full["walk"]["utility"])
        self.assertEqual(changed["bus"]["utility"], full["bus"]["utility"])
        self.assertEqual(changed["ride_hailing"]["utility"], 0.0)
        self.assertEqual(changed["ride_hailing"]["fare_yuan"], 16.0)


class CouponIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = 3001
        cls.config = load_emergence_config()
        cls.symmetric = _main_symmetric_config(cls.config)
        cls.profiles = assign_two_zone_homes(
            generate_population_agents(50, seed=cls.seed), seed=cls.seed,
            s2_share=float(cls.symmetric["s2_home_share"]),
        )
        cls.activities = build_emergence_activities(
            cls.profiles, seed=cls.seed, config=cls.config, symmetric=cls.symmetric,
        )
        cls.results = {}
        cls.allocations = {}
        for policy in COUPON_POLICIES:
            result, allocation = run_coupon_policy(
                cls.profiles, cls.activities, policy, seed=cls.seed,
                config=cls.config, symmetric=cls.symmetric,
            )
            cls.results[policy] = result
            cls.allocations[policy] = allocation

    def test_agents_activities_and_weather_inputs_are_shared(self):
        activity_signature = [tuple(sorted(row.items())) for row in self.activities]
        for result in self.results.values():
            self.assertEqual([tuple(sorted(row.items())) for row in result["activities"]], activity_signature)
            self.assertIs(result["profiles"], self.profiles)

    def test_one_binding_and_redemption_per_agent_day_weather(self):
        for result in self.results.values():
            bound = Counter(
                (row["weather_week"], row["day_type"], row["agent_id"])
                for row in result["ride_hailing_requests"] if row["coupon_bound"]
            )
            redeemed = Counter(
                (row["weather_week"], row["day_type"], row["agent_id"])
                for row in result["ride_hailing_requests"] if row["coupon_redeemed"]
            )
            self.assertTrue(all(value <= 1 for value in bound.values()))
            self.assertTrue(all(value <= 1 for value in redeemed.values()))
            for request in result["ride_hailing_requests"]:
                if request["coupon_bound"]:
                    self.assertAlmostEqual(
                        request["fare_after_coupon_yuan"],
                        request["fare_before_coupon_yuan"] * 0.8,
                        places=2,
                    )

    def test_failed_bound_coupon_expires_and_cannot_reappear(self):
        for result in self.results.values():
            outcomes = {
                (row["weather_week"], row["day_type"], row["agent_id"]): row
                for row in result["coupon_outcomes"]
            }
            for request in result["ride_hailing_requests"]:
                if request["coupon_bound"] and not request["succeeded"]:
                    outcome = outcomes[(request["weather_week"], request["day_type"], request["agent_id"])]
                    self.assertEqual(outcome["coupon_status"], "expired_after_failed_request")
                    self.assertFalse(outcome["coupon_redeemed"])

    def test_noncapacity_ride_hailing_failure_is_disabled(self):
        for result in self.results.values():
            self.assertFalse(any(
                row["failure_reason"] == "non_capacity_transport_failure"
                for row in result["ride_hailing_requests"]
            ))

    def test_dispatch_priority_common_for_shared_requests(self):
        baseline = {
            (row["weather_week"], row["leg_id"]): row["dispatch_priority"]
            for row in self.results["C0_no_coupon"]["ride_hailing_requests"]
        }
        for policy in COUPON_POLICIES[1:]:
            current = {
                (row["weather_week"], row["leg_id"]): row["dispatch_priority"]
                for row in self.results[policy]["ride_hailing_requests"]
            }
            common = set(baseline) & set(current)
            self.assertTrue(common)
            self.assertTrue(all(baseline[key] == current[key] for key in common))

    def test_vehicle_conservation_survives_coupon_policies(self):
        for result in self.results.values():
            for week in ("W0", "W1", "W2"):
                for day_type in ("workday", "rest_day"):
                    expected = sum(self.config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"][day_type].values())
                    states = [row for row in result["ride_hailing_vehicle_states"] if row["weather_week"] == week and row["day_type"] == day_type]
                    self.assertEqual(len(states), expected)
                    by_vehicle = defaultdict(list)
                    for request in result["ride_hailing_requests"]:
                        if request["weather_week"] == week and request["day_type"] == day_type and request["succeeded"]:
                            by_vehicle[request["vehicle_id"]].append(request)
                    for requests in by_vehicle.values():
                        requests.sort(key=lambda row: row["busy_start"])
                        self.assertTrue(all(
                            right["busy_start"] >= left["busy_until"]
                            for left, right in zip(requests, requests[1:])
                        ))

    def test_special_elder_group_is_output_separately(self):
        self.assertIn("60+_nondigital_unassisted", GROUPS)
        rows = summarize_coupon_groups(
            self.results["C3_mixed"], self.allocations["C3_mixed"], "C3_mixed",
        )
        self.assertTrue(any(row["group"] == "60+_nondigital_unassisted" for row in rows))

    def test_community_assisted_booking_is_available_but_does_not_force_a_request(self):
        seed = 3002
        profiles = assign_two_zone_homes(
            generate_population_agents(50, seed=seed), seed=seed,
            s2_share=float(self.symmetric["s2_home_share"]),
        )
        activities = build_emergence_activities(
            profiles, seed=seed, config=self.config, symmetric=self.symmetric,
        )
        result, allocations = run_coupon_policy(
            profiles, activities, "C3_mixed", seed=seed,
            config=self.config, symmetric=self.symmetric,
        )
        community_agents = {
            (row["agent_id"], row["day_type"])
            for row in allocations if community_assisted_booking(row)
        }
        self.assertTrue(community_agents)
        assisted_outcomes = [
            row for row in result["coupon_outcomes"]
            if row["community_assisted_booking"]
        ]
        self.assertTrue(assisted_outcomes)
        self.assertTrue(all(
            (row["agent_id"], row["day_type"]) in community_agents
            for row in assisted_outcomes
        ))
        assisted_requests = [
            row for row in result["ride_hailing_requests"]
            if row["community_assisted_booking"]
        ]
        self.assertTrue(all(row["coupon_bound"] for row in assisted_requests))
        self.assertTrue(all(
            (row["agent_id"], row["day_type"]) in community_agents
            for row in assisted_requests
        ))
        self.assertTrue(all(
            count <= 1 for count in Counter(
                (row["weather_week"], row["day_type"], row["agent_id"])
                for row in assisted_requests
            ).values()
        ))

    def test_cli_tables_are_reproducible_for_fixed_seed(self):
        with tempfile.TemporaryDirectory() as left_dir, tempfile.TemporaryDirectory() as right_dir:
            left = run_coupon_experiment(
                seed_start=self.seed, seed_count=1, output=Path(left_dir), config=self.config,
            )
            right = run_coupon_experiment(
                seed_start=self.seed, seed_count=1, output=Path(right_dir), config=self.config,
            )
            self.assertEqual(left["system_per_seed"], right["system_per_seed"])
            self.assertEqual(left["coupon_outcomes"], right["coupon_outcomes"])


if __name__ == "__main__":
    unittest.main()
