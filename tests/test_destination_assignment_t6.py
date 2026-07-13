import unittest
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import random

from custom.agents.agent_population import generate_population_agents
from custom.agents.trip_planning import generate_seven_day_activity_plans
from custom.policies.t3_policy import evaluate_t3_policy
from custom.spatial.destination_assignment import (
    ZONE_IDS,
    _choose_zone,
    assign_destination_zones,
    assign_destination_zones_with_audit,
    build_destination_audit,
    effective_choice_distance,
    load_destination_configuration,
    validate_destination_configuration,
)
from custom.spatial.home_zone_assignment import assign_home_zones
from custom.spatial.zone_configuration import (
    allocate_zone_age_quotas,
    derive_spatial_configuration,
    load_zone_configuration,
)


WEEK_START = datetime(2026, 7, 6)


class DestinationAssignmentT6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.derived = derive_spatial_configuration(load_zone_configuration())
        cls.config = load_destination_configuration()
        quotas = allocate_zone_age_quotas(cls.derived, total_agents=50)["quota_matrix"]
        population = generate_population_agents(total_agents=50, seed=47)
        cls.agents = assign_home_zones(population, quotas, seed=47)
        cls.activities = generate_seven_day_activity_plans(cls.agents, WEEK_START, 47)

    def assign(self, seed=47, agents=None, activities=None, derived=None, config=None):
        return assign_destination_zones(
            self.agents if agents is None else agents,
            self.activities if activities is None else activities,
            self.derived if derived is None else derived,
            self.config if config is None else config,
            seed,
        )

    def test_remote_z9_uses_road_network_distance_penalty(self):
        by_id = {zone["zone_id"]: zone for zone in self.derived["zones"]}
        euclidean = ((by_id["Z6"]["centroid_x"] - by_id["Z9"]["centroid_x"]) ** 2 +
                     (by_id["Z6"]["centroid_y"] - by_id["Z9"]["centroid_y"]) ** 2) ** 0.5
        self.assertAlmostEqual(
            effective_choice_distance("Z6", "Z9", by_id),
            euclidean * by_id["Z9"]["network_distance_multiplier"],
        )

    def test_work_zone_is_fixed_for_each_worker(self):
        assigned = self.assign()
        by_agent = defaultdict(set)
        for item in assigned:
            if item["activity_purpose"] == "work":
                by_agent[item["agent_id"]].add(item["destination_zone"])
        self.assertTrue(by_agent)
        self.assertTrue(all(len(destinations) == 1 for destinations in by_agent.values()))

    def test_medical_zone_is_fixed_for_each_agent(self):
        assigned = self.assign()
        by_agent = defaultdict(set)
        for item in assigned:
            if item["activity_purpose"] == "medical":
                by_agent[item["agent_id"]].add(item["destination_zone"])
        self.assertTrue(by_agent)
        self.assertTrue(all(len(destinations) == 1 for destinations in by_agent.values()))

    def test_family_destination_is_primary_eighty_percent_with_stable_alternatives(self):
        family = {"visit", "out_of_home_family_care", "out_of_home_family_activity"}
        agent = {"agent_id": "family-reuse", "home_zone": "Z3", "work_status": "regular_worker"}
        base = deepcopy(self.activities[0])
        rows = []
        purposes = sorted(family)
        for index in range(1200):
            row = deepcopy(base)
            row.update({
                "agent_id": agent["agent_id"], "home_zone": agent["home_zone"],
                "activity_id": f"family-{index:04d}", "activity_purpose": purposes[index % len(purposes)],
                "destination_zone": None,
            })
            rows.append(row)
        first = assign_destination_zones_with_audit([agent], rows, self.derived, self.config, 47)
        second = assign_destination_zones_with_audit([agent], rows, self.derived, self.config, 47)
        self.assertEqual(first, second)
        reuse = first["selection_audit"]["family_destination_reuse"]
        self.assertGreater(reuse["realized_primary_reuse_rate"], 0.77)
        self.assertLess(reuse["realized_primary_reuse_rate"], 0.83)
        self.assertEqual(reuse["primary_reuse_count"] + reuse["alternative_count"], len(rows))
        self.assertGreater(len({row["destination_zone"] for row in first["activities"]}), 1)

    def test_activity_level_purposes_allow_same_zone(self):
        seen_same = set()
        for seed in range(20):
            for item in self.assign(seed=seed):
                if item["activity_purpose"] in {"shopping", "social_leisure"} and item["home_zone"] == item["destination_zone"]:
                    seen_same.add(item["activity_purpose"])
        self.assertEqual(seen_same, {"shopping", "social_leisure"})

    def test_local_ordinary_activity_preference_is_explicit_and_stronger_than_work(self):
        multipliers = self.config["same_zone_preference_multiplier"]
        self.assertEqual(multipliers["work"], 1.0)
        for purpose in (
            "medical", "visit", "out_of_home_family_care",
            "out_of_home_family_activity", "shopping", "social_leisure",
        ):
            self.assertGreater(multipliers[purpose], multipliers["work"])

        spatial = {zone["zone_id"]: zone for zone in self.derived["zones"]}
        neutral = deepcopy(self.config)
        neutral["same_zone_preference_multiplier"] = {
            purpose: 1.0 for purpose in self.config["same_zone_preference_multiplier"]
        }
        for purpose in ("shopping", "social_leisure", "medical"):
            preferred_same = 0
            neutral_same = 0
            for home_zone in ZONE_IDS:
                for agent_id in range(150):
                    common = dict(
                        agent_id=f"{home_zone}-{agent_id}", random_key=f"{purpose}-local-test",
                        home_zone=home_zone, purpose=purpose, spatial_by_id=spatial, seed=47,
                    )
                    preferred_same += _choose_zone(config=self.config, **common)[0] == home_zone
                    neutral_same += _choose_zone(config=neutral, **common)[0] == home_zone
            self.assertGreater(preferred_same, neutral_same, purpose)

    def test_work_and_medical_are_not_all_z1(self):
        assigned = self.assign()
        for purpose in ("work", "medical"):
            destinations = {item["destination_zone"] for item in assigned if item["activity_purpose"] == purpose}
            self.assertTrue(destinations)
            self.assertNotEqual(destinations, {"Z1"})

    def test_peripheral_medical_choice_prefers_nearby_options_without_center_binary(self):
        spatial = {zone["zone_id"]: zone for zone in self.derived["zones"]}
        destinations = Counter()
        nearby_choices = 0
        total = 0
        for home in ("Z4", "Z5", "Z8", "Z9"):
            nearest = {
                zone_id for zone_id, _ in sorted(
                    ((zone_id, effective_choice_distance(home, zone_id, spatial)) for zone_id in ZONE_IDS),
                    key=lambda item: item[1],
                )[:3]
            }
            for agent_id in range(200):
                destination, _ = _choose_zone(
                    agent_id=f"{home}-{agent_id}", random_key="medical-test",
                    home_zone=home, purpose="medical", spatial_by_id=spatial,
                    config=self.config, seed=47,
                )
                destinations[destination] += 1
                nearby_choices += destination in nearest
                total += 1
        self.assertGreaterEqual(nearby_choices / total, 0.60)
        self.assertLess((destinations["Z1"] + destinations["Z7"]) / total, 0.50)
        self.assertGreater(len(destinations), 4)

    def test_z7_workers_may_work_in_z7_without_candidate_restriction(self):
        seen_destinations = set()
        for seed in range(20):
            z7_work = [
                item for item in self.assign(seed=seed)
                if item["activity_purpose"] == "work" and item["home_zone"] == "Z7"
            ]
            self.assertTrue(z7_work)
            seen_destinations.update(item["destination_zone"] for item in z7_work)
        self.assertIn("Z7", seen_destinations)
        self.assertTrue(seen_destinations - {"Z1", "Z2", "Z3"})

    def test_same_zone_uses_intrazonal_not_zero_distance(self):
        spatial = {zone["zone_id"]: zone for zone in self.derived["zones"]}
        for zone_id in ZONE_IDS:
            distance = effective_choice_distance(zone_id, zone_id, spatial)
            self.assertGreater(distance, 0)
            self.assertEqual(distance, spatial[zone_id]["mean_intrazonal_distance"])

    def test_fixed_seed_and_input_order_are_stable(self):
        first = self.assign()
        reversed_derived = deepcopy(self.derived)
        reversed_derived["zones"] = list(reversed(reversed_derived["zones"]))
        reversed_config = deepcopy(self.config)
        reversed_config["zone_attraction_weights"] = dict(reversed(list(reversed_config["zone_attraction_weights"].items())))
        second = self.assign(
            agents=list(reversed(self.agents)),
            activities=list(reversed(self.activities)),
            derived=reversed_derived,
            config=reversed_config,
        )
        mapping = lambda records: {item["activity_id"]: item["destination_zone"] for item in records}
        self.assertEqual(mapping(first), mapping(second))
        self.assertEqual(first, self.assign())

    def test_only_destination_zone_changes(self):
        assigned = self.assign()
        originals = {item["activity_id"]: item for item in self.activities}
        for item in assigned:
            original = originals[item["activity_id"]]
            self.assertIn(item["destination_zone"], ZONE_IDS)
            for field, value in original.items():
                if field != "destination_zone":
                    self.assertEqual(item[field], value)
            self.assertIsNone(original["destination_zone"])

    def test_existing_destination_and_invalid_inputs_raise(self):
        existing = deepcopy(self.activities)
        existing[0]["destination_zone"] = "Z1"
        with self.assertRaisesRegex(ValueError, "already has destination"):
            self.assign(activities=existing)

        bad_home = deepcopy(self.activities)
        bad_home[0]["home_zone"] = "Z10"
        with self.assertRaisesRegex(ValueError, "home_zone"):
            self.assign(activities=bad_home)

        bad_purpose = deepcopy(self.activities)
        bad_purpose[0]["activity_purpose"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.assign(activities=bad_purpose)

        bad_config = deepcopy(self.config)
        del bad_config["zone_attraction_weights"]["Z1"]["employment_weight"]
        with self.assertRaisesRegex(ValueError, "attraction row"):
            validate_destination_configuration(bad_config)

    def test_audit_reports_required_distance_and_flow_metrics(self):
        result = assign_destination_zones_with_audit(
            self.agents, self.activities, self.derived, self.config, 47
        )
        audit = build_destination_audit(
            result["activities"], self.derived, result["selection_audit"]
        )
        self.assertEqual(audit["total_activities"], len(self.activities))
        self.assertIn("work", audit["purpose_audit"])
        for metrics in audit["purpose_audit"].values():
            self.assertGreater(metrics["average_effective_distance"], 0)
            self.assertIn("same_zone_share", metrics)
            self.assertIn("over_20_km_share", metrics)
            self.assertIn("over_30_km_share", metrics)
        self.assertTrue(audit["home_to_destination_distribution"])
        self.assertTrue(audit["z7_worker_destination_distribution"])
        self.assertEqual(set(audit["peripheral_medical_destination_distribution"]), {"Z8", "Z9"})
        self.assertIn("activity_level_demand_flow_distribution", audit)
        selection = audit["selection_audit"]
        self.assertGreater(selection["selection_event_count"], 0)
        self.assertGreater(selection["candidate_exclusion_count"], 0)
        self.assertEqual(selection["fallback_count"], 0)
        self.assertEqual(selection["fallback_share"], 0.0)
        self.assertIn("agent_level_fixed_destination_distribution", selection)

    def test_fixed_destinations_are_sampled_once_per_agent(self):
        result = assign_destination_zones_with_audit(
            self.agents, self.activities, self.derived, self.config, 47
        )
        selection = result["selection_audit"]
        agents_with_work = {item["agent_id"] for item in self.activities if item["activity_purpose"] == "work"}
        agents_with_medical = {item["agent_id"] for item in self.activities if item["activity_purpose"] == "medical"}
        agents_with_family = {
            item["agent_id"] for item in self.activities
            if item["activity_purpose"] in {"visit", "out_of_home_family_care", "out_of_home_family_activity"}
        }
        self.assertEqual(selection["by_selection_group"]["work"]["selection_event_count"], len(agents_with_work))
        self.assertEqual(selection["by_selection_group"]["medical"]["selection_event_count"], len(agents_with_medical))
        self.assertEqual(selection["by_selection_group"]["family"]["selection_event_count"], len(agents_with_family))

    def test_family_uses_strictest_observed_weekly_constraint(self):
        family_agent = {
            "agent_id": "family-test", "home_zone": "Z9", "work_status": "regular_worker"
        }
        base = deepcopy(self.activities[0])
        base.update({"agent_id": "family-test", "home_zone": "Z9", "destination_zone": None})
        visit = deepcopy(base)
        visit.update({"activity_id": "family-visit", "activity_purpose": "visit"})
        care = deepcopy(base)
        care.update({"activity_id": "family-care", "activity_purpose": "out_of_home_family_care"})
        config = deepcopy(self.config)
        config["family_primary_destination_reuse_rate"] = 1.0
        result = assign_destination_zones_with_audit(
            [family_agent], [visit, care], self.derived, config, 47
        )
        self.assertEqual(
            result["activities"][0]["destination_zone"],
            result["activities"][1]["destination_zone"],
        )
        expected_exclusions = sum(
            effective_choice_distance(
                "Z9", zone_id, {zone["zone_id"]: zone for zone in self.derived["zones"]}
            ) > 25
            for zone_id in ZONE_IDS
        )
        family_audit = result["selection_audit"]["by_selection_group"]["family"]
        self.assertEqual(family_audit["candidate_exclusion_count"], expected_exclusions)

    def test_activity_level_destinations_use_stable_activity_keys(self):
        agent = {"agent_id": "activity-key", "home_zone": "Z5", "work_status": "regular_worker"}
        base = deepcopy(self.activities[0])
        base.update({
            "agent_id": "activity-key", "home_zone": "Z5", "destination_zone": None,
            "activity_purpose": "shopping",
        })
        first = deepcopy(base); first["activity_id"] = "shopping-a"
        second = deepcopy(base); second["activity_id"] = "shopping-b"
        repeated = assign_destination_zones([agent], [first, second], self.derived, self.config, 91)
        self.assertEqual(
            repeated,
            assign_destination_zones([agent], [first, second], self.derived, self.config, 91),
        )
        observed_difference = False
        for seed in range(30):
            rows = assign_destination_zones([agent], [first, second], self.derived, self.config, seed)
            observed_difference |= rows[0]["destination_zone"] != rows[1]["destination_zone"]
        self.assertTrue(observed_difference)

    def test_extreme_hard_limit_fallback_is_deterministic(self):
        config = deepcopy(self.config)
        config["zone_attraction_weights"] = {
            zone_id: {
                "employment_weight": 1 / 9,
                "medical_weight": config["zone_attraction_weights"][zone_id]["medical_weight"],
                "service_weight": config["zone_attraction_weights"][zone_id]["service_weight"],
            }
            for zone_id in ZONE_IDS
        }
        spatial = {}
        for index, zone_id in enumerate(ZONE_IDS):
            spatial[zone_id] = {
                "zone_id": zone_id,
                "centroid_x": 0.0 if zone_id == "Z1" else 1.0,
                "centroid_y": 0.0,
                "mean_intrazonal_distance": 100.0,
                "population_weight": 1 / 9,
            }
        destination, diagnostic = _choose_zone(
            agent_id="fallback", random_key="fixed:work_zone", home_zone="Z1",
            purpose="work", spatial_by_id=spatial, config=config, seed=47,
            constraint_override={"soft_limit_km": 0.1, "extra_decay": 0.1, "hard_limit_km": 0.5},
        )
        self.assertEqual(destination, "Z2")
        self.assertTrue(diagnostic["fallback"])
        self.assertEqual(diagnostic["candidate_exclusion_count"], 9)

    def test_destination_scenario_invariance_metrics_are_all_zero(self):
        baseline = self.assign(seed=47)
        baseline_map = {
            (item["agent_id"], item["activity_id"]): item["destination_zone"]
            for item in baseline
        }

        shuffled_input = list(self.activities)
        random.Random(2026).shuffle(shuffled_input)
        shuffled_map = {
            (item["agent_id"], item["activity_id"]): item["destination_zone"]
            for item in self.assign(seed=47, activities=shuffled_input)
        }
        changed_after_shuffle = sum(
            shuffled_map[key] != destination
            for key, destination in baseline_map.items()
        )

        removable = next(
            item for item in self.activities
            if item["activity_purpose"] in {"shopping", "social_leisure"}
        )
        reduced_input = [
            item for item in self.activities
            if item["activity_id"] != removable["activity_id"]
        ]
        reduced_map = {
            (item["agent_id"], item["activity_id"]): item["destination_zone"]
            for item in self.assign(seed=47, activities=reduced_input)
        }
        changed_after_removal = sum(
            reduced_map[key] != destination
            for key, destination in baseline_map.items()
            if key in reduced_map
        )

        # Weather branches cancel/delete from already assigned baseline records.
        weather_retained = {
            "W0": deepcopy(baseline),
            "W1": [deepcopy(item) for index, item in enumerate(baseline) if index % 4 != 0],
            "W2": [deepcopy(item) for index, item in enumerate(baseline) if index % 3 != 0],
        }
        changed_after_weather = sum(
            item["destination_zone"]
            != baseline_map[(item["agent_id"], item["activity_id"])]
            for retained in weather_retained.values()
            for item in retained
        )

        policy_agent = {
            "agent_id": "policy-agent", "is_elder": True,
            "digital_access": True, "independent_ride_hailing": True,
            "coupon_awareness_probability": 1.0,
            "coupon_claim_probability": 1.0,
        }
        changed_across_policy_scenarios = 0
        for policy in ("P0", "P1", "P2", "P3", "P4"):
            branch = deepcopy(baseline)
            for item in branch:
                leg = {
                    "leg_id": item["activity_id"], "trip_continues": True,
                    "destination_zone": item["destination_zone"],
                }
                evaluate_t3_policy(
                    policy_agent,
                    leg,
                    policy_scenario=policy,
                    discount_level=None if policy in {"P0", "P4"} else "low",
                    discount_amount_low=10,
                    discount_amount_high=20,
                    weekly_discount_use_count=0,
                    random_seed=47,
                )
                changed_across_policy_scenarios += (
                    leg["destination_zone"] != item["destination_zone"]
                )
            changed_across_policy_scenarios += sum(
                item["destination_zone"]
                != baseline_map[(item["agent_id"], item["activity_id"])]
                for item in branch
            )

        grouped = defaultdict(lambda: {"work": set(), "medical": set(), "family": set()})
        family_purposes = {
            "visit", "out_of_home_family_care", "out_of_home_family_activity"
        }
        for item in baseline:
            purpose = item["activity_purpose"]
            if purpose == "work":
                grouped[item["agent_id"]]["work"].add(item["destination_zone"])
            elif purpose == "medical":
                grouped[item["agent_id"]]["medical"].add(item["destination_zone"])
            elif purpose in family_purposes:
                grouped[item["agent_id"]]["family"].add(item["destination_zone"])

        metrics = {
            "changed_after_shuffle": changed_after_shuffle,
            "changed_after_removal": changed_after_removal,
            "changed_after_weather": changed_after_weather,
            "changed_across_policy_scenarios": changed_across_policy_scenarios,
            "work_zone_violations": sum(len(values["work"]) > 1 for values in grouped.values()),
            "medical_zone_violations": sum(len(values["medical"]) > 1 for values in grouped.values()),
            "family_multi_destination_agents": sum(len(values["family"]) > 1 for values in grouped.values()),
        }
        print("T6 scenario invariance:", metrics)
        for field in (
            "changed_after_shuffle", "changed_after_removal", "changed_after_weather",
            "changed_across_policy_scenarios", "work_zone_violations", "medical_zone_violations",
        ):
            self.assertEqual(metrics[field], 0)
        self.assertGreater(metrics["family_multi_destination_agents"], 0)


if __name__ == "__main__":
    unittest.main()
