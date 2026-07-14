import math
import unittest

from custom.envs import weather


W2_WINDOWS = [
    ("Tuesday", "07:00", "10:00"),
    ("Thursday", "16:00", "19:00"),
    ("Saturday", "11:00", "15:00"),
]


def activity(activity_id="a-1", purpose="shopping", departure="07:30", arrival="08:00"):
    return {
        "agent_id": "agent-1",
        "activity_id": activity_id,
        "day_of_week": "Tuesday",
        "activity_purpose": purpose,
        "planned_outbound_departure": departure,
        "planned_activity_arrival": arrival,
    }


class WeatherActivityDisruptionTests(unittest.TestCase):
    def setUp(self):
        weather.set_week("W2")
        weather.set_w2_windows(W2_WINDOWS)
        weather.set_scenario_level("base")
        weather.init_rng(47)
        self.young = {
            "age_group": "18-39",
            "mobility_constraint": "none",
            "schedule_flexibility": "medium",
        }

    def test_all_current_activity_types_are_explicit_and_unknown_raises(self):
        expected = {
            "medical": "medical",
            "work": "work",
            "out_of_home_family_care": "family_care",
            "out_of_home_family_activity": "family_activity",
            "visit": "visit",
            "shopping": "shopping",
            "social_leisure": "social_leisure",
        }
        self.assertEqual(
            {key: weather.map_activity_to_weather_purpose(key) for key in expected},
            expected,
        )
        with self.assertRaises(ValueError):
            weather.map_activity_to_weather_purpose("daily")

    def test_probability_ordering_weather_and_age(self):
        probability = weather.compute_weather_cancel_probability
        medical = probability("heavy_rain", "medical", "18-39")
        work = probability("heavy_rain", "work", "18-39")
        shopping = probability("heavy_rain", "shopping", "18-39")
        leisure = probability("heavy_rain", "social_leisure", "18-39")
        self.assertLess(medical, work)
        self.assertLess(work, shopping)
        self.assertLess(shopping, leisure)
        self.assertGreater(
            probability("heavy_rain", "shopping", "18-39"),
            probability("extreme_heat", "shopping", "18-39"),
        )
        self.assertGreater(
            probability("heavy_rain", "shopping", "60+"),
            probability("heavy_rain", "shopping", "18-39"),
        )

    def test_mobility_and_schedule_multipliers_and_probability_clamp(self):
        base = weather.compute_weather_cancel_probability(
            "heavy_rain", "shopping", "40-59", "none", "medium", "high"
        )
        constrained = weather.compute_weather_cancel_probability(
            "heavy_rain", "shopping", "40-59", "high", "high", "high"
        )
        self.assertGreater(constrained, base)
        self.assertLessEqual(constrained, 1.0)

    def test_only_outbound_interval_can_cancel(self):
        outbound_exposed = weather.evaluate_planned_activity(activity(), self.young)
        self.assertTrue(outbound_exposed["outbound_weather_exposed"])
        self.assertGreater(outbound_exposed["p_weather_cancel"], 0)

        outbound_clear = activity(departure="06:00", arrival="06:30")
        decision = weather.evaluate_planned_activity(outbound_clear, self.young)
        self.assertFalse(decision["outbound_weather_exposed"])
        self.assertFalse(decision["weather_cancelled"])
        self.assertEqual(decision["p_weather_cancel"], 0.0)

        outbound_leg = {
            "agent_id": "agent-1", "activity_id": "return-only", "leg_role": "outbound",
            "day": "Tuesday", "purpose": "shopping",
            "departure_time": "06:00", "arrival_time": "06:30",
        }
        return_leg = {
            "agent_id": "agent-1", "activity_id": "return-only", "leg_role": "return_home",
            "day": "Tuesday", "departure_time": "07:30", "arrival_time": "08:00",
        }
        outbound_continues, return_continues = weather.process_outbound_return(
            outbound_leg, return_leg, self.young, outbound_trip_completed=True
        )
        self.assertTrue(outbound_continues)
        self.assertTrue(return_continues)
        self.assertEqual(outbound_leg["ride_hailing_odds_multiplier"], 1.0)
        self.assertEqual(outbound_leg["ride_hailing_utility_shift"], 0.0)
        self.assertTrue(return_leg["weather_event_active"])
        self.assertEqual(return_leg["ride_hailing_odds_multiplier"], 1.30)
        self.assertAlmostEqual(return_leg["ride_hailing_utility_shift"], math.log(1.30))
        self.assertFalse(decision["weather_cancelled"])

    def test_outbound_and_return_preference_signals_use_their_own_times(self):
        outbound = None
        for index in range(100):
            candidate = {
                "agent_id": "agent-1", "activity_id": f"leg-signal-{index}",
                "leg_role": "outbound", "day": "Tuesday", "purpose": "shopping",
                "departure_time": "07:30", "arrival_time": "08:00",
            }
            probe = weather.evaluate_planned_activity(
                activity(f"leg-signal-{index}"), self.young
            )
            if not probe["weather_cancelled"]:
                outbound = candidate
                break
        self.assertIsNotNone(outbound)
        ret = {
            "agent_id": "agent-1", "activity_id": outbound["activity_id"],
            "leg_role": "return_home", "day": "Tuesday",
            "departure_time": "12:00", "arrival_time": "12:30",
        }
        weather.process_outbound_return(outbound, ret, self.young, outbound_trip_completed=True)
        self.assertEqual(outbound["ride_hailing_odds_multiplier"], 1.30)
        self.assertAlmostEqual(outbound["ride_hailing_utility_shift"], math.log(1.30))
        self.assertEqual(ret["ride_hailing_odds_multiplier"], 1.0)
        self.assertEqual(ret["ride_hailing_utility_shift"], 0.0)

    def test_agent_behavior_fields_are_required_even_in_w0(self):
        weather.set_week("W0")
        required = ("age_group", "mobility_constraint", "schedule_flexibility")
        for missing in required:
            profile = dict(self.young)
            profile.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                weather.evaluate_planned_activity(activity(f"missing-{missing}"), profile)
        invalid_profiles = (
            {**self.young, "age_group": "unknown"},
            {**self.young, "mobility_constraint": "unknown"},
            {**self.young, "schedule_flexibility": "unknown"},
        )
        for profile in invalid_profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                weather.evaluate_planned_activity(activity("invalid-profile"), profile)

    def test_w0_is_strictly_neutral_and_preserves_inputs(self):
        weather.set_week("W0")
        planned = activity("w0", "medical", "07:30", "08:00")
        planned.update({"is_mandatory": True, "baseline_cancel_probability": 0.01})
        original_activity = dict(planned)
        decision = weather.evaluate_planned_activity(planned, self.young)
        self.assertEqual(planned, original_activity)
        self.assertEqual(decision["p_weather_cancel"], 0.0)
        self.assertEqual(decision["ride_hailing_odds_multiplier"], 1.0)
        self.assertEqual(decision["ride_hailing_utility_shift"], 0.0)
        self.assertFalse(decision["weather_cancelled"])
        self.assertFalse(decision["unmet_mandatory_trip"])
        self.assertTrue(decision["is_mandatory"])
        self.assertEqual(decision["baseline_cancel_probability"], 0.01)

        leg = {
            "leg_id": "w0-leg", "day": "Tuesday", "departure_time": "07:30",
            "arrival_time": "08:00", "is_mandatory": True, "base_time_min": 30.0,
        }
        original_leg = dict(leg)
        weather.annotate_leg_with_weather(leg)
        for key, value in original_leg.items():
            self.assertEqual(leg[key], value)
        self.assertEqual(leg["ride_hailing_odds_multiplier"], 1.0)
        self.assertEqual(leg["ride_hailing_utility_shift"], 0.0)
        self.assertNotIn("weather_cancelled", leg)
        self.assertNotIn("unmet_mandatory_trip", leg)

    def test_cancelled_activity_marks_mandatory_and_leaves_no_retained_activity(self):
        profile = {
            "age_group": "60+", "mobility_constraint": "high",
            "schedule_flexibility": "high",
        }
        cancelled = None
        for index in range(1000):
            candidate = activity(f"mandatory-{index}", "work")
            decision = weather.evaluate_planned_activity(candidate, profile, scenario_level="high")
            if decision["weather_cancelled"]:
                cancelled = candidate
                break
        self.assertIsNotNone(cancelled)
        result = weather.apply_weather_disruption_before_mode_choice(
            [cancelled], {"agent-1": profile}, scenario_level="high"
        )
        decision = result["activity_decisions"][0]
        self.assertTrue(decision["weather_cancelled"])
        self.assertTrue(decision["unmet_mandatory_trip"])
        self.assertFalse(decision["outbound_leg_executes"])
        self.assertFalse(decision["return_leg_executes"])
        self.assertEqual(result["retained_activities"], [])

    def test_outbound_cancel_invalidates_both_linked_legs(self):
        profile = {
            "age_group": "60+", "mobility_constraint": "high",
            "schedule_flexibility": "high",
        }
        outbound = None
        for index in range(1000):
            candidate = {
                "agent_id": "agent-1", "activity_id": f"pair-{index}",
                "leg_role": "outbound", "day": "Tuesday", "purpose": "social_leisure",
                "departure_time": "07:30", "arrival_time": "08:00",
            }
            probe = weather.evaluate_planned_activity(
                activity(f"pair-{index}", "social_leisure"), profile, scenario_level="high"
            )
            if probe["weather_cancelled"]:
                outbound = candidate
                break
        self.assertIsNotNone(outbound)
        ret = {
            "agent_id": "agent-1", "activity_id": outbound["activity_id"],
            "leg_role": "return_home", "day": "Tuesday",
            "departure_time": "12:00", "arrival_time": "12:30",
        }
        weather.set_scenario_level("high")
        continues, return_continues = weather.process_outbound_return(outbound, ret, profile)
        self.assertFalse(continues)
        self.assertFalse(return_continues)
        self.assertFalse(outbound["trip_continues"])
        self.assertFalse(ret["trip_continues"])
        self.assertTrue(ret["invalidated_by_outbound"])

    def test_low_base_high_cancellation_sets_are_nested(self):
        profile = {
            "age_group": "60+", "mobility_constraint": "mild",
            "schedule_flexibility": "high",
        }
        sets = {}
        for level in weather.SCENARIO_LEVELS:
            sets[level] = {
                index
                for index in range(500)
                if weather.evaluate_planned_activity(
                    activity(f"nested-{index}", "work"), profile,
                    scenario_level=level, seed=91,
                )["weather_cancelled"]
            }
        self.assertTrue(sets["low"] <= sets["base"] <= sets["high"])
        self.assertLess(len(sets["low"]), len(sets["high"]))

    def test_stable_draw_is_independent_of_level_and_call_order(self):
        draws = [
            weather.evaluate_planned_activity(activity("stable"), self.young, scenario_level=level)["weather_random_draw"]
            for level in ("high", "low", "base")
        ]
        self.assertEqual(draws[0], draws[1])
        self.assertEqual(draws[1], draws[2])

    def test_ride_hailing_output_is_signal_only(self):
        decision = None
        for index in range(100):
            candidate = weather.evaluate_planned_activity(activity(f"signal-{index}"), self.young)
            if not candidate["weather_cancelled"]:
                decision = candidate
                break
        self.assertIsNotNone(decision)
        self.assertEqual(decision["ride_hailing_odds_multiplier"], 1.30)
        self.assertAlmostEqual(decision["ride_hailing_utility_shift"], math.log(1.30))
        self.assertFalse(decision["mode_choice_applied"])
        for forbidden in ("selected_mode", "ride_hailing_demand", "excess_road_flow_pcu_per_hour"):
            self.assertNotIn(forbidden, decision)

    def test_baseline_probability_combines_multiplicatively(self):
        self.assertAlmostEqual(
            weather.combine_baseline_and_weather_cancel_probability(0.20, 0.30),
            0.44,
        )

    def test_parameter_provenance_is_explicit(self):
        metadata = weather.PARAMETERS["metadata"]
        self.assertEqual(metadata["source_type"], "model_assumption")
        self.assertEqual(metadata["calibration_status"], "sensitivity_analysis")
        self.assertIs(metadata["not_database_estimate"], True)


if __name__ == "__main__":
    unittest.main()
