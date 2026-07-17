import unittest

from custom.agents.metro_experiment import (
    load_metro_transport_config,
    run_metro_scenario,
    summarize_metro_scenario,
)
from custom.agents.simple_mode_choice import (
    SimpleAgent, build_mode_options, choose_mode, metro_service_at_time,
)


class Metro50AgentExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transport = load_metro_transport_config()
        cls.m0 = run_metro_scenario(3001, "M0_no_metro")
        cls.m1 = run_metro_scenario(3001, "M1_optimistic_metro")
        cls.m2_transport = load_metro_transport_config(scenario="M2_realistic_access")
        cls.m2 = run_metro_scenario(3001, "M2_realistic_access")

    def test_metro_config_has_four_modes_and_one_line(self):
        self.assertEqual(
            tuple(self.transport["mode_order"]),
            ("walk", "bus", "ride_hailing", "metro"),
        )
        self.assertEqual(self.transport["metro_line"]["zones"], ["S1", "S2"])

    def test_metro_is_only_available_between_the_two_zones(self):
        interzonal = build_mode_options("S1", "S2", "W0", config=self.transport)
        intrazonal = build_mode_options("S1", "S1", "W0", config=self.transport)
        self.assertTrue(interzonal["metro"]["available"])
        self.assertFalse(intrazonal["metro"]["available"])

    def test_metro_speed_wait_and_time_are_weather_stable(self):
        options = [
            build_mode_options("S1", "S2", week, config=self.transport)["metro"]
            for week in ("W0", "W1", "W2")
        ]
        for field in ("wait_time_min", "in_vehicle_time_min", "travel_time_min", "fare_yuan"):
            self.assertEqual(options[0][field], options[1][field])
            self.assertEqual(options[1][field], options[2][field])

    def test_peak_frequency_is_higher_and_wait_is_lower(self):
        morning = metro_service_at_time("08:00", config=self.transport)
        evening = metro_service_at_time("18:00", config=self.transport)
        ordinary = metro_service_at_time("12:00", config=self.transport)
        self.assertEqual(morning, evening)
        self.assertTrue(morning["is_peak"])
        self.assertFalse(ordinary["is_peak"])
        self.assertGreater(
            morning["train_trips_per_30_min"], ordinary["train_trips_per_30_min"]
        )
        self.assertLess(morning["average_wait_min"], ordinary["average_wait_min"])
        self.assertEqual(morning["average_wait_min"], 3.0)
        self.assertEqual(ordinary["average_wait_min"], 6.0)

    def test_peak_boundaries_are_half_open(self):
        self.assertTrue(metro_service_at_time("09:59", config=self.transport)["is_peak"])
        self.assertFalse(metro_service_at_time("10:00", config=self.transport)["is_peak"])
        self.assertTrue(metro_service_at_time("19:59", config=self.transport)["is_peak"])
        self.assertFalse(metro_service_at_time("20:00", config=self.transport)["is_peak"])

    def test_m0_and_m1_use_identical_agents_and_activities(self):
        self.assertEqual(self.m0["profiles"], self.m1["profiles"])
        self.assertEqual(self.m0["activities"], self.m1["activities"])
        self.assertEqual(self.m0["profiles"], self.m2["profiles"])
        self.assertEqual(self.m0["activities"], self.m2["activities"])

    def test_fixed_seed_is_reproducible(self):
        rerun = run_metro_scenario(3001, "M1_optimistic_metro")
        self.assertEqual(self.m1["activity_results"], rerun["activity_results"])
        self.assertEqual(self.m1["leg_results"], rerun["leg_results"])

    def test_metro_does_not_enter_road_vehicle_volume(self):
        self.assertTrue(any(row["final_success_mode"] == "metro" for row in self.m1["leg_results"]))
        for row in self.m1["system_state"]:
            if row["state_type"] == "road":
                self.assertAlmostEqual(
                    float(row["road_vehicle_volume"]),
                    float(row["scheduled_bus_vehicle_trips"])
                    + float(row["successful_ride_hailing_vehicle_trips"]),
                )

    def test_four_mode_shares_sum_to_one(self):
        for row in summarize_metro_scenario(self.m1):
            self.assertAlmostEqual(
                float(row["walking_mode_share"])
                + float(row["bus_mode_share"])
                + float(row["ride_hailing_mode_share"])
                + float(row["metro_mode_share"]),
                1.0,
                places=5,
            )

    def test_metro_runtime_is_not_multiplied_by_road_congestion(self):
        metro_legs = [
            row for row in self.m1["leg_results"]
            if row["weather_week"] == "W2" and row["final_success_mode"] == "metro"
            and row["origin_zone"] != row["destination_zone"]
            and row["attempt_count"] == 1
        ]
        self.assertTrue(metro_legs)
        for row in metro_legs:
            expected = build_mode_options(
                row["origin_zone"], row["destination_zone"], "W2", config=self.transport
            )["metro"]
            service = metro_service_at_time(row["departure_time"], config=self.transport)
            self.assertAlmostEqual(
                float(row["cumulative_travel_time_min"]),
                float(expected["travel_time_min"])
                + float(service["average_wait_min"])
                - float(expected["wait_time_min"]),
                places=3,
            )

    def test_metro_fallback_uses_actual_shifted_start_time(self):
        rows = [
            row for row in self.m1["leg_results"]
            if row["fallback_mode"] == "metro"
        ]
        self.assertTrue(rows)
        for row in rows:
            service = metro_service_at_time(
                float(row["fallback_start_minute"]), config=self.transport
            )
            self.assertEqual(
                bool(row["metro_peak_service_used"]), bool(service["is_peak"])
            )
            self.assertEqual(
                float(row["metro_train_trips_per_30_min"]),
                float(service["train_trips_per_30_min"]),
            )

    def test_realistic_access_is_longer_and_coverage_is_lower(self):
        optimistic = self.transport["metro_zone_service_parameters"]
        realistic = self.m2_transport["metro_zone_service_parameters"]
        for zone in ("S1", "S2"):
            self.assertGreater(
                realistic[zone]["metro_access_min"],
                optimistic[zone]["metro_access_min"],
            )
            self.assertLess(
                realistic[zone]["metro_coverage_rate"],
                optimistic[zone]["metro_coverage_rate"],
            )

    def test_agent_level_metro_coverage_is_stable_across_weather_and_direction(self):
        agent = SimpleAgent("coverage-agent", "18-39", "S2")
        decisions = [
            choose_mode(
                agent,
                {"trip_id": trip_id, "origin_zone": origin, "destination_zone": destination},
                weather,
                seed=3001,
                config=self.m2_transport,
            )
            for trip_id, origin, destination, weather in (
                ("out-W0", "S2", "S1", "W0"),
                ("out-W2", "S2", "S1", "W2"),
                ("return-W1", "S1", "S2", "W1"),
            )
        ]
        audits = [row["mode_availability"]["metro"] for row in decisions]
        self.assertEqual(len({row["coverage_draw"] for row in audits}), 1)
        self.assertEqual(len({row["available_after_coverage"] for row in audits}), 1)

    def test_realistic_coverage_is_enforced_for_a_population(self):
        available = 0
        for index in range(500):
            agent = SimpleAgent(f"coverage-{index:03d}", "18-39", "S2")
            decision = choose_mode(
                agent,
                {"trip_id": "commute", "origin_zone": "S2", "destination_zone": "S1"},
                "W0", seed=3001, config=self.m2_transport,
            )
            available += int(
                decision["mode_availability"]["metro"]["available_after_coverage"]
            )
        realized = available / 500
        self.assertGreater(realized, 0.32)
        self.assertLess(realized, 0.48)

    def test_realistic_access_reduces_metro_use_relative_to_optimistic(self):
        optimistic = sum(row["metro_legs"] for row in summarize_metro_scenario(self.m1))
        realistic = sum(row["metro_legs"] for row in summarize_metro_scenario(self.m2))
        self.assertLess(realistic, optimistic)


if __name__ == "__main__":
    unittest.main()
