import copy
from collections import Counter
import math
import random
import unittest

from custom.agents.emergence_experiment import (
    build_emergence_activities, calculate_heat_hazard_dose,
    heat_vulnerability_weight, load_emergence_config,
    run_emergence_experiment, run_emergence_weather, summarize_macro,
)
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES
from custom.agents.simple_mode_choice import load_simple_config
from scripts.run_emergence_experiment import summarize_groups
from scripts.run_heat_threshold_sensitivity import (
    POLICY_CHANGE_METRICS, build_policy_changes,
)


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

    def test_utci_heat_dose_is_higher_in_afternoon_than_morning(self):
        morning = calculate_heat_hazard_dose("08:00", 10.0, "W1")
        afternoon = calculate_heat_hazard_dose("14:00", 10.0, "W1")
        self.assertEqual(morning, 40.0)
        self.assertEqual(afternoon, 160.0)
        self.assertGreater(afternoon, morning)

    def test_heat_dose_splits_exposure_at_30_minute_boundaries(self):
        dose = calculate_heat_hazard_dose("08:15", 30.0, "W1")
        self.assertEqual(dose, 15 * (30 - 26) + 15 * (31 - 26))

    def test_heat_dose_crosses_midnight_with_periodic_profile(self):
        dose = calculate_heat_hazard_dose("23:45", 30.0, "W1")
        self.assertEqual(dose, 15 * (28.0 - 26.0) + 15 * (27.5 - 26.0))

    def test_bus_failure_keeps_only_origin_walk_and_wait_before_fallback(self):
        rows = [
            row for row in self.result["leg_results"]
            if row["weather_week"] == "W1" and row["initial_mode"] == "bus"
            and row["fallback_used"]
        ]
        self.assertTrue(rows)
        for row in rows:
            self.assertGreater(row["bus_origin_walk_minutes"], 0)
            self.assertGreater(row["bus_wait_minutes"], 0)
            self.assertEqual(row["bus_in_vehicle_minutes"], 0)
            self.assertEqual(row["bus_destination_walk_minutes"], 0)
            departure = int(row["departure_time"][:2]) * 60 + int(row["departure_time"][3:])
            self.assertAlmostEqual(
                row["fallback_start_minute"],
                departure + row["bus_origin_walk_minutes"] + row["bus_wait_minutes"],
                places=3,
            )

    def test_fallback_heat_uses_delayed_start_time(self):
        row = next(
            row for row in self.result["leg_results"]
            if row["weather_week"] == "W1" and row["initial_mode"] == "bus"
            and row["fallback_mode"] == "ride_hailing" and row["fallback_success"]
        )
        fallback_dose = calculate_heat_hazard_dose(
            row["fallback_start_minute"], row["ride_hailing_wait_min"], "W1"
        )
        self.assertAlmostEqual(
            row["heat_hazard_dose_c_min"],
            row["failed_attempt_heat_hazard_dose_c_min"] + fallback_dose,
            places=3,
        )

    def test_heat_dose_is_separate_from_normal_and_rain_exposure(self):
        self.assertEqual(calculate_heat_hazard_dose("14:00", 20.0, "W0"), 0.0)
        self.assertEqual(calculate_heat_hazard_dose("14:00", 20.0, "W2"), 0.0)

    def test_age_changes_vulnerability_not_environmental_heat_dose(self):
        dose = calculate_heat_hazard_dose("14:00", 10.0, "W1")
        young = heat_vulnerability_weight("18-39")
        middle = heat_vulnerability_weight("40-59")
        elder = heat_vulnerability_weight("60+")
        self.assertEqual(dose, calculate_heat_hazard_dose("14:00", 10.0, "W1"))
        self.assertLess(young, middle)
        self.assertLess(middle, elder)
        self.assertGreater(dose * elder, dose * young)

    def test_digital_access_does_not_change_heat_vulnerability(self):
        elder_profiles = [row for row in self.result["profiles"] if row.age_group == "60+"]
        self.assertGreater(len({row.digital_access for row in elder_profiles}), 1)
        weights = {heat_vulnerability_weight(row.age_group) for row in elder_profiles}
        self.assertEqual(weights, {1.3})

    def test_cancelled_and_remote_activities_have_zero_transport_heat(self):
        rows = []
        for seed in range(self.seed, self.seed + 5):
            rows.extend(run_emergence_experiment(seed)["activity_results"])
        excluded = [row for row in rows if row["weather_cancellation"] or row["remote_work"]]
        self.assertTrue(excluded)
        self.assertTrue(all(row["heat_hazard_dose_c_min"] == 0 for row in excluded))
        self.assertTrue(all(row["heat_risk_burden"] == 0 for row in excluded))

    def test_failed_attempt_heat_is_retained(self):
        fallback = [
            row for row in self.result["leg_results"]
            if row["weather_week"] == "W1" and row["fallback_used"]
        ]
        self.assertTrue(fallback)
        self.assertTrue(all(row["failed_attempt_heat_hazard_dose_c_min"] > 0 for row in fallback))
        self.assertTrue(all(
            row["heat_hazard_dose_c_min"] >= row["failed_attempt_heat_hazard_dose_c_min"]
            for row in fallback
        ))

    def test_leg_and_activity_heat_risk_conservation(self):
        leg_by_activity = {}
        for leg in self.result["leg_results"]:
            leg_by_activity.setdefault((leg["activity_id"], leg["weather_week"]), []).append(leg)
        for activity in self.result["activity_results"]:
            legs = leg_by_activity.get((activity["activity_id"], activity["weather_week"]), [])
            self.assertAlmostEqual(
                activity["heat_hazard_dose_c_min"],
                sum(row["heat_hazard_dose_c_min"] for row in legs),
                places=3,
            )
            self.assertAlmostEqual(
                activity["heat_risk_burden"],
                sum(row["heat_risk_burden"] for row in legs),
                places=3,
            )

    def test_heat_threshold_does_not_change_mode_choice(self):
        low_config = load_emergence_config()
        high_config = copy.deepcopy(low_config)
        high_config["heat_exposure"]["heat_stress_threshold_c"] = 32.0
        low = run_emergence_experiment(self.seed, config=low_config)
        high = run_emergence_experiment(self.seed, config=high_config)
        fields = (
            "leg_id", "weather_week", "pre_feedback_mode", "initial_mode",
            "fallback_mode", "final_success_mode", "cumulative_wait_min",
            "cumulative_travel_time_min", "cumulative_fare_yuan",
        )
        self.assertEqual(
            [tuple(row[field] for field in fields) for row in low["leg_results"]],
            [tuple(row[field] for field in fields) for row in high["leg_results"]],
        )
        activity_fields = (
            "activity_id", "weather_week", "activity_final_status",
            "activity_completed", "weather_cancellation", "transport_related_unmet",
            "necessary_transport_related_unmet",
            "cumulative_wait_min", "cumulative_fare_yuan",
        )
        self.assertEqual(
            [tuple(row[field] for field in activity_fields) for row in low["activity_results"]],
            [tuple(row[field] for field in activity_fields) for row in high["activity_results"]],
        )
        self.assertEqual(low["system_state"], high["system_state"])

    def test_macro_activity_transport_and_heat_conservation(self):
        required = {
            "planned_activities", "completed_activities", "activity_completion_rate",
            "planned_necessary_activities", "completed_necessary_activities",
            "necessary_activity_completion_rate", "weather_cancelled_activities",
            "transport_related_unmet", "necessary_transport_related_unmet",
            "walking_legs", "bus_legs", "ride_hailing_legs",
            "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
            "fallback_attempts", "fallback_successes", "transport_success_rate",
            "total_bus_wait_minutes", "total_ride_hailing_wait_minutes",
            "total_system_wait_minutes", "mean_bus_wait_minutes_per_attempt",
            "mean_ride_hailing_wait_minutes_per_request", "mean_total_travel_time", "bus_demand",
            "ride_hailing_requests", "successful_ride_hailing_requests",
            "failed_ride_hailing_requests", "scheduled_bus_vehicle_trips",
            "successful_ride_hailing_vehicle_trips", "road_vehicle_volume", "mean_volume_capacity_ratio",
            "mean_dynamic_congestion_multiplier", "mean_road_speed_kmh",
            "total_outdoor_exposure_minutes", "total_heat_hazard_dose_c_min",
            "total_heat_risk_burden", "necessary_heat_risk_burden",
            "heat_risk_per_completed_travel_required_necessary_activity",
            "heat_risk_per_planned_travel_required_necessary_activity",
        }
        for row in summarize_macro(self.result):
            self.assertTrue(required.issubset(row))
            self.assertEqual(
                row["walking_legs"] + row["bus_legs"] + row["ride_hailing_legs"],
                row["successful_legs"],
            )
            self.assertLessEqual(row["fallback_successes"], row["fallback_attempts"])
            self.assertEqual(
                row["completed_activities"] + row["weather_cancelled_activities"]
                + row["transport_related_unmet"],
                row["planned_activities"],
            )
            self.assertEqual(
                row["road_vehicle_volume"],
                row["scheduled_bus_vehicle_trips"]
                + row["successful_ride_hailing_vehicle_trips"],
            )
            expected = 1.0 if row["successful_legs"] else 0.0
            self.assertAlmostEqual(
                row["walking_mode_share"] + row["bus_mode_share"]
                + row["ride_hailing_mode_share"], expected, places=5,
            )

    def test_policy_percent_change_is_blank_when_p0_is_zero(self):
        base = {
            "seed": 1, "weather_scenario": "W1", "day_type": "workday",
            "heat_threshold_c": 26.0,
            **{metric: 0.0 for metric in POLICY_CHANGE_METRICS},
        }
        changes = build_policy_changes([
            {**base, "policy": "P0_baseline"},
            {**base, "policy": "P1_bus_frequency_plus_50pct"},
        ])
        self.assertTrue(changes)
        self.assertTrue(all(row["percent_change"] == "" for row in changes))
        self.assertTrue(all(row["percent_change_defined"] is False for row in changes))
        self.assertTrue(all(row["undefined_reason"] == "baseline_zero" for row in changes))

    def test_policy_label_alone_does_not_change_heat(self):
        left_config = load_emergence_config()
        right_config = copy.deepcopy(left_config)
        right_config["heat_threshold_sensitivity"]["policy_scenarios"] = {
            "DIFFERENT_LABEL_ONLY": {
                "bus_frequency_multiplier": 1.0, "ride_supply_multiplier": 1.0,
            }
        }
        left = run_emergence_experiment(self.seed, config=left_config)
        right = run_emergence_experiment(self.seed, config=right_config)
        self.assertEqual(
            [row["heat_hazard_dose_c_min"] for row in left["leg_results"]],
            [row["heat_hazard_dose_c_min"] for row in right["leg_results"]],
        )

    def test_heat_exposure_index_is_only_legacy_outdoor_minutes_alias(self):
        for row in self.result["activity_results"]:
            self.assertTrue(row["heat_exposure_index_is_outdoor_minutes_alias"])
            expected = row["outdoor_exposure_minutes"] if row["weather_week"] == "W1" else 0.0
            self.assertEqual(row["heat_exposure_index"], expected)

    def test_group_heat_outputs_include_population_denominators(self):
        rows = summarize_groups(self.result)
        for week in WEATHER_TYPES:
            for day_type in ("workday", "rest_day"):
                subset = [
                    row for row in rows
                    if row["weather_week"] == week and row["day_type"] == day_type
                ]
                self.assertEqual(sum(row["agent_count"] for row in subset), 50)
        for row in rows:
            self.assertGreater(row["agent_count"], 0)
            self.assertAlmostEqual(
                row["heat_risk_burden_per_agent"],
                row["heat_risk_burden"] / row["agent_count"],
                places=5,
            )

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
                state_road[(row["weather_week"], row["day_type"], row["time_bin"])] = row["successful_ride_hailing_vehicle_trips"]
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

    def test_lower_bus_frequency_cannot_lower_peak_load(self):
        base = summarize_macro(self.result)
        low = summarize_macro(run_emergence_experiment(self.seed, bus_frequency_multiplier=0.5))
        base_lookup = {(row["weather_week"], row["day_type"]): row for row in base}
        low_lookup = {(row["weather_week"], row["day_type"]): row for row in low}
        for key in base_lookup:
            self.assertGreaterEqual(low_lookup[key]["peak_bus_load_ratio"], base_lookup[key]["peak_bus_load_ratio"])

    def test_bus_vehicle_schedule_is_not_passenger_legs_and_peaks_are_higher(self):
        macro = summarize_macro(self.result)
        self.assertTrue(all(row["scheduled_bus_vehicle_trips"] != row["bus_legs"] for row in macro))
        roads = [
            row for row in self.result["system_state"]
            if row["weather_week"] == "W1" and row["day_type"] == "workday"
            and row["state_type"] == "road"
        ]
        by_start = {row["time_bin"].split("-", 1)[0]: row for row in roads}
        self.assertGreater(
            by_start["07:30"]["scheduled_bus_vehicle_trips"],
            by_start["12:00"]["scheduled_bus_vehicle_trips"],
        )

    def test_bus_frequency_policy_changes_trips_but_per_vehicle_capacity_is_fixed(self):
        base_config = load_emergence_config()
        p0 = summarize_macro(run_emergence_experiment(self.seed, config=base_config))
        p1 = summarize_macro(run_emergence_experiment(
            self.seed, bus_frequency_multiplier=1.5, config=base_config,
        ))
        for left, right in zip(p0, p1):
            self.assertAlmostEqual(
                right["scheduled_bus_vehicle_trips"],
                left["scheduled_bus_vehicle_trips"] * 1.5,
            )
        self.assertEqual(
            base_config["bus_feedback"]["per_vehicle_capacity_representative_passengers"],
            6.0,
        )

    def test_per_vehicle_capacity_change_does_not_change_bus_schedule(self):
        changed = load_emergence_config()
        changed["bus_feedback"]["per_vehicle_capacity_representative_passengers"] *= 2
        base = summarize_macro(self.result)
        alternative = summarize_macro(run_emergence_experiment(self.seed, config=changed))
        self.assertEqual(
            [row["scheduled_bus_vehicle_trips"] for row in base],
            [row["scheduled_bus_vehicle_trips"] for row in alternative],
        )

    def test_bus_and_ride_hailing_share_congestion_multiplier(self):
        transport = load_simple_config()
        weather_multiplier = transport["weather"]["extreme_heat"]["speed_multiplier"]
        roads = [
            row for row in self.result["system_state"]
            if row["weather_week"] == "W1" and row["state_type"] == "road"
        ]
        for row in roads:
            bus_ratio = row["bus_road_speed_kmh"] / (
                transport["modes"]["bus"]["speed_kmh"] * weather_multiplier["bus"]
            )
            ride_ratio = row["ride_hailing_road_speed_kmh"] / (
                transport["modes"]["ride_hailing"]["speed_kmh"] * weather_multiplier["ride_hailing"]
            )
            self.assertAlmostEqual(bus_ratio, row["dynamic_congestion_multiplier"], places=6)
            self.assertAlmostEqual(ride_ratio, row["dynamic_congestion_multiplier"], places=6)

    def test_activity_final_status_has_three_exclusive_values(self):
        rows = self.result["activity_results"]
        self.assertTrue(all(
            row["activity_final_status"] in {"completed", "weather_cancelled", "transport_unmet"}
            for row in rows
        ))
        self.assertFalse(any("stranded_after_activity" in row for row in rows))

    def test_remote_work_is_excluded_from_completed_travel_necessary_denominator(self):
        results = [run_emergence_experiment(seed) for seed in range(self.seed, self.seed + 10)]
        macro = [row for result in results for row in summarize_macro(result)]
        with_remote = [row for row in macro if row["remote_work"] > 0]
        self.assertTrue(with_remote)
        for row in with_remote:
            self.assertEqual(
                row["completed_necessary_activities"],
                row["completed_travel_required_necessary_activities"] + row["remote_work"],
            )

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
