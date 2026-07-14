import copy
from collections import Counter
import math
import random
import unittest

from custom.agents.emergence_experiment import (
    build_emergence_activities, load_emergence_config,
    run_emergence_experiment, run_emergence_weather, summarize_macro,
)
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES


class EmergenceExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = 3001
        cls.result = run_emergence_experiment(cls.seed)
        cls.profile_by_id = {row.agent_id: row for row in cls.result["profiles"]}

    def test_fixed_seed_is_reproducible(self):
        rerun = run_emergence_experiment(self.seed)
        self.assertEqual(self.result["activity_results"], rerun["activity_results"])
        self.assertEqual(self.result["system_state"], rerun["system_state"])

    def test_weather_scenarios_share_identical_base_schedule(self):
        paired = {(row["activity_id"], row["weather_week"]): row for row in self.result["activity_results"]}
        fields = ("agent_id", "activity_id", "day_type", "activity_purpose", "departure_time", "return_time", "origin_zone", "destination_zone", "distance_km")
        for activity in self.result["activities"]:
            rows = [paired[(activity["activity_id"], week)] for week in WEATHER_TYPES]
            self.assertTrue(all(rows[0][field] == rows[1][field] == rows[2][field] for field in fields))

    def test_rest_day_has_no_work(self):
        self.assertFalse(any(row["day_type"] == "rest_day" and row["activity_purpose"] == "work" for row in self.result["activities"]))

    def test_same_day_activities_do_not_overlap(self):
        grouped = {}
        for row in self.result["activities"]:
            grouped.setdefault((row["agent_id"], row["day_type"]), []).append(row)
        to_minutes = lambda value: int(value[:2]) * 60 + int(value[3:])
        for rows in grouped.values():
            ordered = sorted(rows, key=lambda row: row["departure_time"])
            for left, right in zip(ordered, ordered[1:]):
                self.assertLessEqual(to_minutes(left["return_time"]), to_minutes(right["departure_time"]))

    def test_work_schedule_includes_regular_and_part_time_rules(self):
        work = [row for row in self.result["activities"] if row["activity_purpose"] == "work"]
        statuses = {self.profile_by_id[row["agent_id"]].work_status for row in work}
        self.assertEqual(statuses, {"regular_worker", "part_time_worker"})
        for row in work:
            status = self.profile_by_id[row["agent_id"]].work_status
            if status == "part_time_worker":
                self.assertIn(row["work_start_time"], {"10:00", "10:30"})
            else:
                self.assertIn(row["work_start_time"], {"08:00", "08:30", "09:00", "09:30", "10:00", "10:30"})

    def test_main_like_activity_generation_makes_medical_a_minority(self):
        medical = ordinary = 0
        rest_by_age = {"18-39": [], "40-59": [], "60+": []}
        for seed in range(3100, 3120):
            profiles = run_emergence_experiment(seed)["profiles"]
            activities = build_emergence_activities(profiles, seed=seed)
            medical += sum(row["activity_purpose"] == "medical" for row in activities)
            ordinary += sum(row["activity_purpose"] not in {"work", "medical"} for row in activities)
            counts = {age: 0 for age in rest_by_age}
            agents = {age: 0 for age in rest_by_age}
            by_profile = {row.agent_id: row for row in profiles}
            for profile in profiles:
                agents[profile.age_group] += 1
            for row in activities:
                if row["day_type"] == "rest_day":
                    counts[by_profile[row["agent_id"]].age_group] += 1
            for age in rest_by_age:
                rest_by_age[age].append(counts[age] / agents[age])
        self.assertLess(medical, ordinary)
        self.assertGreater(sum(rest_by_age["18-39"]) / 20, sum(rest_by_age["60+"]) / 20)

    def test_activity_order_does_not_change_weather_result(self):
        shuffled = copy.deepcopy(self.result["activities"])
        random.Random(99).shuffle(shuffled)
        original = run_emergence_weather(self.result["profiles"], self.result["activities"], "W2", seed=self.seed)
        rerun = run_emergence_weather(self.result["profiles"], shuffled, "W2", seed=self.seed)
        self.assertEqual(original, rerun)

    def test_shared_feedback_changes_at_least_one_mode(self):
        self.assertTrue(any(row["mode_changed_after_feedback"] for row in self.result["leg_results"]))

    def test_final_system_state_matches_final_successful_modes_and_requests(self):
        bus = Counter()
        ride_requests = Counter()
        road = Counter()
        for leg in self.result["leg_results"]:
            if leg["final_success_mode"] == "bus":
                bus[(leg["weather_week"], leg["day_type"], leg["time_bin"], leg["direction"])] += 1
            if leg["ride_hailing_request_count"]:
                ride_requests[(leg["weather_week"], leg["day_type"], leg["time_bin"], leg["origin_zone"])] += leg["ride_hailing_request_count"]
            if leg["final_success_mode"] == "ride_hailing":
                road[(leg["weather_week"], leg["day_type"], leg["time_bin"])] += 1
        state_bus = Counter()
        state_ride = Counter()
        state_road = Counter()
        for row in self.result["system_state"]:
            self.assertEqual(row["state_stage"], "final")
            if row["state_type"] == "bus":
                state_bus[(row["weather_week"], row["day_type"], row["time_bin"], row["spatial_key"])] = row["demand"]
            elif row["state_type"] == "ride_hailing":
                state_ride[(row["weather_week"], row["day_type"], row["time_bin"], row["spatial_key"])] = row["demand"]
            elif row["state_type"] == "road":
                state_road[(row["weather_week"], row["day_type"], row["time_bin"])] = row["demand"]
        self.assertEqual(bus, state_bus)
        self.assertEqual(ride_requests, state_ride)
        self.assertEqual(road, state_road)

    def test_fallback_ride_requests_are_in_final_demand(self):
        fallback_requests = sum(
            row["fallback_mode"] == "ride_hailing" for row in self.result["leg_results"]
        )
        self.assertGreater(fallback_requests, 0)
        final_requests = sum(
            row["demand"] for row in self.result["system_state"]
            if row["state_type"] == "ride_hailing"
        )
        recorded_requests = sum(row["ride_hailing_request_count"] for row in self.result["leg_results"])
        self.assertEqual(final_requests, recorded_requests)

    def test_ride_supply_can_change_final_road_state(self):
        signatures = set()
        for multiplier in (0.6, 1.0, 1.4):
            result = run_emergence_experiment(self.seed, ride_supply_multiplier=multiplier)
            signatures.add(tuple(
                sorted(
                    (row["weather_week"], row["day_type"], row["time_bin"], row["demand"])
                    for row in result["system_state"] if row["state_type"] == "road"
                )
            ))
        self.assertGreater(len(signatures), 1)

    def test_lower_bus_capacity_cannot_lower_peak_load(self):
        base = summarize_macro(self.result)
        low = summarize_macro(run_emergence_experiment(self.seed, bus_capacity_multiplier=0.5))
        base_lookup = {(row["weather_week"], row["day_type"]): row for row in base}
        low_lookup = {(row["weather_week"], row["day_type"]): row for row in low}
        for key in base_lookup:
            self.assertGreaterEqual(low_lookup[key]["peak_bus_load_ratio"], base_lookup[key]["peak_bus_load_ratio"])

    def test_lower_ride_supply_cannot_lower_initial_demand_supply_ratio(self):
        base = summarize_macro(self.result)
        low = summarize_macro(run_emergence_experiment(self.seed, ride_supply_multiplier=0.5))
        base_lookup = {(row["weather_week"], row["day_type"]): row for row in base}
        low_lookup = {(row["weather_week"], row["day_type"]): row for row in low}
        for key in base_lookup:
            self.assertGreaterEqual(low_lookup[key]["peak_ride_demand_supply_ratio"], base_lookup[key]["peak_ride_demand_supply_ratio"])

    def test_nondigital_unassisted_agent_never_uses_ride_hailing(self):
        blocked = {
            row.agent_id for row in self.result["profiles"]
            if not row.digital_access and not row.family_assistance
        }
        legs = [row for row in self.result["leg_results"] if row["agent_id"] in blocked]
        self.assertTrue(legs)
        fields = ("pre_feedback_mode", "initial_mode", "fallback_mode", "final_success_mode")
        self.assertTrue(all(all(row[field] != "ride_hailing" for field in fields) for row in legs))

    def test_fallback_is_at_most_once_and_failure_is_not_a_success_mode(self):
        for row in self.result["leg_results"]:
            self.assertLessEqual(row["attempt_count"], 2)
            if row["transport_failure"]:
                self.assertEqual(row["final_success_mode"], "")

    def test_return_failure_does_not_create_mandatory_unmet(self):
        by_activity = {(row["activity_id"], row["weather_week"]): row for row in self.result["activity_results"]}
        for leg in self.result["leg_results"]:
            if leg["leg_role"] == "return" and leg["transport_failure"]:
                activity = by_activity[(leg["activity_id"], leg["weather_week"])]
                self.assertTrue(activity["activity_completed"])
                self.assertFalse(activity["transport_related_unmet"])

    def test_numeric_outputs_are_finite_nonnegative(self):
        macro = summarize_macro(self.result)
        numeric = [key for key, value in macro[0].items() if isinstance(value, (int, float))]
        for row in macro:
            for field in numeric:
                self.assertTrue(math.isfinite(float(row[field])))
                self.assertGreaterEqual(float(row[field]), 0.0)

    def test_config_requires_one_feedback_iteration(self):
        self.assertEqual(load_emergence_config()["feedback_iterations"], 1)


if __name__ == "__main__":
    unittest.main()
