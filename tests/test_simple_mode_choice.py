import unittest

from custom.agents.simple_mode_choice import (
    SimpleAgent,
    build_mode_options,
    calculate_ride_hailing_feedback_wait,
    choose_mode,
    load_simple_config,
)


class SimpleModeChoiceTests(unittest.TestCase):
    def test_space_and_single_bus_line_contract(self):
        config = load_simple_config()
        self.assertEqual(len(config["zones"]), 2)
        self.assertEqual(config["bus_line"]["line_id"], "B1")
        self.assertEqual(set(config["bus_line"]["zones"]), {z["zone_id"] for z in config["zones"]})

    def test_exactly_three_modes_and_long_walk_unavailable(self):
        config = load_simple_config()
        config["modes"]["walk"]["maximum_distance_km"] = 1.0
        options = build_mode_options("S2", "S1", "W0", config=config)
        self.assertEqual(set(options), {"walk", "bus", "ride_hailing"})
        self.assertFalse(options["walk"]["available"])
        self.assertTrue(options["bus"]["available"])
        self.assertTrue(options["ride_hailing"]["available"])

    def test_intrazonal_travel_is_defined_for_every_zone_and_mode(self):
        for zone in ("S1", "S2"):
            options = build_mode_options(zone, zone, "W0")
            self.assertTrue(all(row["available"] for row in options.values()))
            self.assertTrue(all(row["intrazonal"] for row in options.values()))
            self.assertTrue(all(row["distance_km"] > 0 for row in options.values()))

    def test_s2_bus_coverage_is_lower_and_has_higher_access_burden(self):
        s1 = build_mode_options("S1", "S1", "W0")["bus"]
        s2 = build_mode_options("S2", "S2", "W0")["bus"]
        self.assertLess(s2["service_coverage_rate"], s1["service_coverage_rate"])
        self.assertGreater(s2["travel_time_min"], s1["travel_time_min"])

    def test_weather_changes_level_of_service(self):
        normal = build_mode_options("S1", "S1", "W0")
        rain = build_mode_options("S1", "S1", "W2")
        for mode in ("walk", "bus", "ride_hailing"):
            self.assertGreater(rain[mode]["travel_time_min"], normal[mode]["travel_time_min"])

    def test_choice_is_reproducible_and_auditable(self):
        agent = SimpleAgent("A1", "18-39", "S1")
        trip = {"trip_id": "T1", "origin_zone": "S1", "destination_zone": "S1"}
        first = choose_mode(agent, trip, "W1", seed=9)
        second = choose_mode(agent, trip, "W1", seed=9)
        self.assertEqual(first, second)
        self.assertIn(first["chosen_mode"], {"walk", "bus", "ride_hailing"})
        self.assertEqual(len(first["alternatives"]), 3)

    def test_non_digital_agent_cannot_choose_ride_hailing(self):
        agent = SimpleAgent("E1", "60+", "S2", digital_access=False, family_assistance=False)
        trip = {"trip_id": "T2", "origin_zone": "S2", "destination_zone": "S1"}
        result = choose_mode(agent, trip, "W2")
        self.assertNotEqual(result["chosen_mode"], "ride_hailing")
        self.assertNotIn("ride_hailing", {row["mode"] for row in result["alternatives"]})

    def test_ride_hailing_wait_feedback_is_monotonic(self):
        waits = [calculate_ride_hailing_feedback_wait(demand) for demand in (0, 10, 30, 100)]
        self.assertEqual(waits, sorted(waits))
        self.assertGreater(waits[-1], waits[0])

    def test_ride_hailing_fare_uses_distance_time_minimum_and_weather_multiplier(self):
        s1 = build_mode_options("S1", "S1", "W0")["ride_hailing"]
        s2 = build_mode_options("S2", "S2", "W0")["ride_hailing"]
        cross_w0 = build_mode_options("S1", "S2", "W0")["ride_hailing"]
        cross_w1 = build_mode_options("S1", "S2", "W1")["ride_hailing"]
        cross_w2 = build_mode_options("S1", "S2", "W2")["ride_hailing"]
        self.assertEqual(s1["fare_yuan"], 14.0)
        self.assertEqual(s2["fare_yuan"], 14.0)
        self.assertEqual(cross_w0["fare_yuan"], 15.73)
        self.assertEqual(cross_w1["fare_yuan"], 16.52)
        self.assertEqual(cross_w2["fare_yuan"], 19.40)
        self.assertEqual(cross_w2["dynamic_price_multiplier"], 1.15)


if __name__ == "__main__":
    unittest.main()
