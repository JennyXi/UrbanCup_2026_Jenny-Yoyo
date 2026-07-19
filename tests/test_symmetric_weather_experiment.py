import copy
import math
import random
import unittest
from unittest.mock import patch

from custom.agents.agent_population import generate_population_agents
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import (
    build_symmetric_activities, load_symmetric_experiment_config,
    remote_work_decision, run_symmetric_experiment, run_symmetric_weather,
    weather_cancellation_decision,
)


class SymmetricWeatherExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = 2026
        cls.profiles = assign_two_zone_homes(generate_population_agents(50, seed=cls.seed), seed=cls.seed)
        cls.activities = build_symmetric_activities(cls.profiles, seed=cls.seed)
        cls.profile_by_id = {row.agent_id: row for row in cls.profiles}

    def test_paired_activity_fields_are_identical_except_weather_outputs(self):
        result = run_symmetric_experiment(self.seed)
        by_key = {(row["activity_id"], row["weather_week"]): row for row in result["results"]}
        fields = ("agent_id", "activity_id", "day_type", "activity_purpose", "departure_time", "return_time", "work_start_time", "work_end_time", "origin_zone", "destination_zone", "distance_km")
        for activity in result["activities"]:
            w0 = by_key[(activity["activity_id"], "W0")]
            w1 = by_key[(activity["activity_id"], "W1")]
            w2 = by_key[(activity["activity_id"], "W2")]
            self.assertTrue(all(w0[field] == w1[field] == w2[field] for field in fields))

    def test_work_exists_only_on_workday_for_both_employment_types(self):
        work = [row for row in self.activities if row["activity_purpose"] == "work"]
        statuses = {self.profile_by_id[row["agent_id"]].work_status for row in work}
        self.assertEqual(statuses, {"regular_worker", "part_time_worker"})
        self.assertTrue(all(row["day_type"] == "workday" for row in work))
        self.assertFalse(any(row["activity_purpose"] == "work" and row["day_type"] == "rest_day" for row in self.activities))

    def test_work_times_follow_main_regular_and_part_time_ranges(self):
        for row in (item for item in self.activities if item["activity_purpose"] == "work"):
            status = self.profile_by_id[row["agent_id"]].work_status
            if status == "regular_worker":
                self.assertIn(row["work_start_time"], {"08:00", "08:30", "09:00", "09:30", "10:00", "10:30"})
            else:
                self.assertIn(row["work_start_time"], {"10:00", "10:30"})
            self.assertGreaterEqual(row["work_end_time"], "17:00")
            self.assertLessEqual(row["work_end_time"], "21:30")

    def test_w0_has_no_weather_remote_work_cancellation_or_transport_failure(self):
        rows = run_symmetric_weather(self.profiles, self.activities, "W0", seed=self.seed)
        self.assertTrue(all(not row["remote_work"] and not row["weather_cancellation"] for row in rows))
        self.assertTrue(all(not row["outbound_transport_failure"] and not row["return_transport_failure"] for row in rows))
        self.assertTrue(all(row["heat_exposure_index"] == 0 and row["rain_exposure_index"] == 0 for row in rows))

    def test_fixed_seed_is_reproducible(self):
        self.assertEqual(run_symmetric_experiment(self.seed)["results"], run_symmetric_experiment(self.seed)["results"])

    def test_activity_order_does_not_change_results(self):
        shuffled = copy.deepcopy(self.activities)
        random.Random(99).shuffle(shuffled)
        self.assertEqual(
            run_symmetric_weather(self.profiles, self.activities, "W2", seed=self.seed),
            run_symmetric_weather(self.profiles, shuffled, "W2", seed=self.seed),
        )

    def test_work_only_exists_for_employed_and_never_weather_cancels(self):
        work = [row for row in self.activities if row["activity_purpose"] == "work"]
        self.assertTrue(work)
        self.assertTrue(all(self.profile_by_id[row["agent_id"]].work_status in {"regular_worker", "part_time_worker"} for row in work))
        rows = run_symmetric_experiment(self.seed)["results"]
        self.assertTrue(all(not row["weather_cancellation"] for row in rows if row["activity_purpose"] in {"work", "medical"}))

    def test_remote_draw_once_and_probability_source_depends_on_exposure(self):
        activity = next(row for row in self.activities if row["activity_purpose"] == "work")
        profile = self.profile_by_id[activity["agent_id"]]
        w1 = remote_work_decision(activity, profile, "W1", seed=self.seed)
        w2 = remote_work_decision(activity, profile, "W2", seed=self.seed)
        self.assertEqual(w1["remote_work_draw"], w2["remote_work_draw"])
        self.assertEqual(w1["p_remote_work"], 0.02)
        self.assertEqual(w2["p_remote_work"], 0.05)
        unexposed = {**activity, "departure_time": "11:00"}
        self.assertEqual(remote_work_decision(unexposed, profile, "W1", seed=self.seed)["p_remote_work"], 0.0)
        self.assertEqual(remote_work_decision(unexposed, profile, "W2", seed=self.seed)["p_remote_work"], 0.0)

    def test_remote_work_generates_no_commute_leg_and_is_completed(self):
        config = copy.deepcopy(load_symmetric_experiment_config())
        config["work_remote_probability"] = {"normal": 1.0, "extreme_heat": 1.0, "heavy_rain": 1.0}
        config["work_remote_probability_sensitivity"] = {key: [0.0, 1.0] for key in config["work_remote_probability"]}
        rows = run_symmetric_weather(self.profiles, self.activities, "W1", seed=self.seed, config=config)
        work = [row for row in rows if row["activity_purpose"] == "work"]
        self.assertTrue(all(row["remote_work"] and row["work_completed"] and not row["travel_required"] for row in work))
        self.assertTrue(all(not row["outbound_leg_generated"] and not row["return_leg_generated"] and not row["unmet_mandatory"] for row in work))

    def test_digital_access_does_not_change_weather_cancellation(self):
        activity = next(row for row in self.activities if row["activity_purpose"] == "shopping")
        profile = self.profile_by_id[activity["agent_id"]]
        altered = copy.deepcopy(profile)
        altered.digital_access = not profile.digital_access
        self.assertEqual(
            weather_cancellation_decision(activity, profile, "W2", seed=self.seed),
            weather_cancellation_decision(activity, altered, "W2", seed=self.seed),
        )

    def test_nondigital_unassisted_never_uses_ride_hailing(self):
        profile = next(row for row in self.profiles if row.is_elder and not row.digital_access and not row.family_assistance)
        activities = [row for row in self.activities if row["agent_id"] == profile.agent_id]
        rows = run_symmetric_weather([profile], activities, "W2", seed=self.seed)
        fields = ("outbound_initial_mode", "outbound_fallback_mode", "outbound_final_mode", "return_initial_mode", "return_fallback_mode", "return_final_mode")
        self.assertTrue(all(all(row[field] != "ride_hailing" for field in fields) for row in rows))

    def test_primary_failure_can_succeed_via_one_fallback(self):
        config = copy.deepcopy(load_symmetric_experiment_config())
        config["transport_success_probability"]["W1"] = {"walk": 1.0, "bus": 0.0, "ride_hailing": 0.0}
        rows = run_symmetric_weather(self.profiles, self.activities, "W1", seed=self.seed, config=config)
        recovered = [row for row in rows if row["outbound_fallback_success"]]
        self.assertTrue(recovered)
        self.assertTrue(all(row["outbound_attempt_count"] == 2 and not row["transport_related_unmet"] for row in recovered))
        self.assertTrue(all(row["outbound_fallback_mode"] != row["outbound_initial_mode"] for row in recovered))

    def test_fallback_is_never_attempted_more_than_once(self):
        rows = run_symmetric_experiment(self.seed)["results"]
        self.assertTrue(all(row["outbound_attempt_count"] <= 2 and row["return_attempt_count"] <= 2 for row in rows))

    def test_return_failure_preserves_completion_and_does_not_add_unmet(self):
        def controlled(seed, activity_id, week, leg_role, attempt, mode, experiment):
            return leg_role == "outbound"
        with patch("custom.agents.symmetric_weather_experiment._attempt_success", side_effect=controlled):
            rows = run_symmetric_weather(self.profiles, self.activities, "W1", seed=self.seed)
        stranded = [row for row in rows if row["stranded_after_activity"]]
        self.assertTrue(stranded)
        self.assertTrue(all(row["activity_completed"] and not row["unmet_mandatory"] for row in stranded))

    def test_state_is_mutually_exclusive_and_conserved(self):
        rows = run_symmetric_experiment(self.seed)["results"]
        for row in rows:
            self.assertEqual(int(row["remote_work"]) + int(row["weather_cancellation"]) + int(row["travel_required"]), 1)
            if row["transport_related_unmet"]:
                self.assertTrue(row["necessary_activity"] and row["travel_required"] and not row["activity_completed"])

    def test_cancelled_activity_has_no_transport_or_hazard_exposure(self):
        cancelled = [row for row in run_symmetric_experiment(self.seed)["results"] if row["weather_cancellation"]]
        self.assertTrue(cancelled)
        for row in cancelled:
            self.assertFalse(row["outbound_leg_generated"])
            self.assertEqual(row["outdoor_exposure_minutes"], 0.0)
            self.assertEqual(row["heat_exposure_index"], 0.0)
            self.assertEqual(row["rain_exposure_index"], 0.0)

    def test_failures_are_not_counted_as_successful_modes(self):
        config = copy.deepcopy(load_symmetric_experiment_config())
        config["transport_success_probability"]["W2"] = {mode: 0.0 for mode in ("walk", "bus", "ride_hailing")}
        rows = run_symmetric_weather(self.profiles, self.activities, "W2", seed=self.seed, config=config)
        failures = [row for row in rows if row["outbound_transport_failure"]]
        self.assertTrue(failures)
        self.assertTrue(all(row["outbound_final_mode"] == "" for row in failures))

    def test_numeric_outputs_are_finite_and_nonnegative(self):
        fields = ("ride_hailing_wait_min", "cumulative_wait_min", "cumulative_travel_time_min", "cumulative_fare_yuan", "outdoor_exposure_minutes", "heat_exposure_index", "rain_exposure_index")
        for row in run_symmetric_experiment(self.seed)["results"]:
            for field in fields:
                self.assertTrue(math.isfinite(float(row[field])))
                self.assertGreaterEqual(float(row[field]), 0.0)


if __name__ == "__main__":
    unittest.main()
