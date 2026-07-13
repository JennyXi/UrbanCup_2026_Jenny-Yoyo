import math
import unittest

from custom.transport.network import (
    MODES,
    _shortest_service_path,
    _shortest_road_distance,
    build_all_od_options,
    build_transport_network,
    calculate_leg_mode_option,
    calculate_od_option,
    intrazonal_metro_is_covered,
)


class TransportNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = build_transport_network()
        cls.rows = build_all_od_options(cls.network)
        cls.by_key = {
            (row["origin_zone"], row["destination_zone"], row["mode"]): row
            for row in cls.rows
        }

    def test_complete_od_mode_table(self):
        self.assertEqual(len(self.rows), 9 * 9 * 4)
        self.assertEqual(len(self.by_key), len(self.rows))
        self.assertTrue(all("effective_distance_km" not in row for row in self.rows))
        for origin in self.network["zone_ids"]:
            for destination in self.network["zone_ids"]:
                self.assertEqual(
                    {mode for o, d, mode in self.by_key if o == origin and d == destination},
                    set(MODES),
                )

    def test_road_and_bus_cover_all_zones(self):
        for origin in self.network["zone_ids"]:
            for destination in self.network["zone_ids"]:
                self.assertTrue(self.by_key[(origin, destination, "bus")]["available"])
                self.assertTrue(self.by_key[(origin, destination, "ride_hailing")]["available"])

    def test_intrazonal_public_transport_service_is_explicit(self):
        services = self.network["config"]["intrazonal_services"]
        self.assertEqual(set(services), set(self.network["zone_ids"]))
        self.assertTrue(all(row["road"] and row["bus"] and row["ride_hailing"] for row in services.values()))
        self.assertEqual(
            {zone for zone, row in services.items() if row["metro"]},
            {"Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8"},
        )
        for zone in self.network["zone_ids"]:
            self.assertTrue(self.by_key[(zone, zone, "bus")]["available"])
            self.assertEqual(self.by_key[(zone, zone, "metro")]["available"], services[zone]["metro"])

    def test_unavailable_modes_are_explicit(self):
        self.assertTrue(self.by_key[("Z1", "Z1", "walk")]["available"])
        self.assertFalse(self.by_key[("Z1", "Z2", "walk")]["available"])
        row = self.by_key[("Z9", "Z9", "metro")]
        self.assertFalse(row["available"])
        self.assertTrue(all(row[field] is None for field in (
            "access_mode", "main_network_distance_km", "access_distance_km", "network_distance_km",
            "in_vehicle_time_min", "access_time_min",
            "wait_time_min", "transfer_time_min", "total_time_min", "main_fare",
            "access_fare", "fare", "line_transfer_count", "mode_transfer_count",
        )))

    def test_available_times_and_fares_are_non_negative_and_add_up(self):
        for row in self.rows:
            if not row["available"]:
                continue
            for field in (
                "euclidean_distance_km", "road_network_distance_km", "main_network_distance_km",
                "access_distance_km", "network_distance_km",
                "in_vehicle_time_min", "access_time_min",
                "wait_time_min", "transfer_time_min", "total_time_min", "main_fare",
                "access_fare", "fare", "line_transfer_count", "mode_transfer_count",
            ):
                self.assertGreaterEqual(row[field], 0, (row, field))
            expected = sum(row[field] for field in (
                "in_vehicle_time_min", "access_time_min", "wait_time_min", "transfer_time_min"
            ))
            self.assertTrue(math.isclose(row["total_time_min"], expected, abs_tol=1e-9), row)
            self.assertTrue(math.isclose(row["fare"], row["main_fare"] + row["access_fare"], abs_tol=1e-9), row)
            self.assertTrue(math.isclose(
                row["network_distance_km"],
                row["main_network_distance_km"] + row["access_distance_km"],
                abs_tol=1e-9,
            ), row)

    def test_metro_transfer_and_ride_hailing_distance_fare(self):
        direct = self.by_key[("Z1", "Z7", "metro")]
        transfer = self.by_key[("Z6", "Z4", "metro")]
        self.assertTrue(direct["available"] and transfer["available"])
        self.assertEqual(direct["line_transfer_count"], 0)
        self.assertGreaterEqual(transfer["line_transfer_count"], 1)
        short = self.by_key[("Z1", "Z2", "ride_hailing")]
        long = self.by_key[("Z9", "Z1", "ride_hailing")]
        self.assertGreater(long["network_distance_km"], short["network_distance_km"])
        self.assertGreater(long["fare"], short["fare"])

    def test_distance_field_semantics_and_mode_formulas(self):
        ride = self.by_key[("Z1", "Z2", "ride_hailing")]
        self.assertAlmostEqual(
            ride["road_network_distance_km"],
            _shortest_road_distance(self.network["road"], "Z1", "Z2"),
            places=3,
        )
        self.assertEqual(ride["main_network_distance_km"], ride["road_network_distance_km"])
        expected_time = ride["road_network_distance_km"] / self.network["config"]["modes"]["ride_hailing"]["speed_kmh"] * 60
        self.assertAlmostEqual(ride["in_vehicle_time_min"], expected_time, places=3)
        expected_fare = 14 + max(0, ride["road_network_distance_km"] - 3) * 2.7
        self.assertAlmostEqual(ride["fare"], expected_fare, places=2)

        bus = self.by_key[("Z2", "Z7", "bus")]
        bus_path = _shortest_service_path(
            self.network["bus"], "Z2", "Z7", speed_kmh=18.0,
            transfer_penalty_min=8.0, origin_access_min=6.0,
            destination_access_min=6.0,
        )
        self.assertAlmostEqual(bus["main_network_distance_km"], bus_path[0], places=3)
        self.assertGreater(bus["main_network_distance_km"], bus["road_network_distance_km"])
        self.assertGreater(bus["access_distance_km"], 0)

        metro = self.by_key[("Z2", "Z7", "metro")]
        metro_path = _shortest_service_path(
            self.network["metro"], "Z2", "Z7", speed_kmh=35.0,
            transfer_penalty_min=7.0, origin_access_min=5.0,
            destination_access_min=5.0,
        )
        self.assertAlmostEqual(metro["main_network_distance_km"], metro_path[0], places=3)
        self.assertGreater(metro["access_distance_km"], 0)

    def test_z9_road_detour_factor_is_one_point_three_five(self):
        row = self.by_key[("Z9", "Z6", "ride_hailing")]
        self.assertAlmostEqual(
            row["road_network_distance_km"], row["euclidean_distance_km"] * 1.35, places=3
        )

    def test_road_distance_follows_configured_graph(self):
        row = self.by_key[("Z9", "Z1", "ride_hailing")]
        configured_path = _shortest_road_distance(self.network["road"], "Z9", "Z1")
        self.assertAlmostEqual(row["road_network_distance_km"], configured_path, places=3)
        self.assertGreater(row["road_network_distance_km"], row["euclidean_distance_km"])

    def test_z9_public_transport_is_weaker_than_central_zones(self):
        z9_bus = self.by_key[("Z9", "Z6", "bus")]
        central_bus = self.by_key[("Z1", "Z2", "bus")]
        self.assertGreater(z9_bus["wait_time_min"], central_bus["wait_time_min"])
        self.assertGreater(z9_bus["access_time_min"], central_bus["access_time_min"])
        z9_metro = self.by_key[("Z9", "Z1", "metro")]
        self.assertTrue(z9_metro["available"])
        self.assertEqual(z9_metro["access_mode"], "bus")
        self.assertEqual(z9_metro["mode_transfer_count"], 1)
        self.assertEqual(z9_metro["access_fare"], 2.0)
        self.assertGreater(z9_metro["access_distance_km"], 0)
        self.assertAlmostEqual(
            z9_metro["network_distance_km"],
            z9_metro["main_network_distance_km"] + z9_metro["access_distance_km"],
            places=3,
        )
        self.assertGreater(z9_metro["access_time_min"], central_bus["access_time_min"])
        self.assertTrue(self.by_key[("Z1", "Z7", "metro")]["available"])
        self.assertTrue(all("Z9" not in line["zones"] for line in self.network["config"]["graphs"]["metro"]["lines"]))

    def test_z9_metro_feeder_time_is_not_counted_twice(self):
        feeder = self.by_key[("Z9", "Z6", "bus")]
        metro = self.by_key[("Z9", "Z1", "metro")]
        z1_exit = self.network["config"]["zone_service_parameters"]["Z1"]["metro_access_min"]
        self.assertAlmostEqual(metro["access_time_min"], feeder["total_time_min"] + z1_exit, places=3)
        self.assertEqual(metro["transfer_time_min"], 6.0)
        self.assertNotAlmostEqual(
            metro["access_time_min"], feeder["total_time_min"] + z1_exit + 14.0, places=3
        )

    def test_intrazonal_metro_coverage_and_long_trip_rule(self):
        expected = {"Z1": .75, "Z2": .60, "Z3": .60, "Z4": .35, "Z5": .30, "Z6": .35, "Z7": .50, "Z8": .25, "Z9": 0.0}
        self.assertEqual(self.network["config"]["intrazonal_metro"]["metro_coverage_rate"], expected)
        mean = self.network["zone_by_id"]["Z1"]["mean_intrazonal_distance"]
        short_leg = {
            "leg_id": "short-z1", "origin_zone": "Z1", "destination_zone": "Z1",
            "road_network_distance_km": mean * 0.5,
        }
        self.assertFalse(calculate_leg_mode_option(self.network, short_leg, "metro")["available"])
        long_distance = mean * 1.2
        covered_key = next(
            f"covered-{index}" for index in range(10000)
            if intrazonal_metro_is_covered(self.network, "Z1", long_distance, f"covered-{index}", 47)
        )
        uncovered_key = next(
            f"uncovered-{index}" for index in range(10000)
            if not intrazonal_metro_is_covered(self.network, "Z1", long_distance, f"uncovered-{index}", 47)
        )
        base = {"origin_zone": "Z1", "destination_zone": "Z1", "road_network_distance_km": long_distance}
        self.assertTrue(calculate_leg_mode_option(self.network, {**base, "leg_id": covered_key}, "metro")["available"])
        self.assertFalse(calculate_leg_mode_option(self.network, {**base, "leg_id": uncovered_key}, "metro")["available"])
        self.assertFalse(intrazonal_metro_is_covered(self.network, "Z9", 20.0, "z9", 47))

    def test_intrazonal_distance_reuses_zone_specific_spatial_value(self):
        z1 = self.by_key[("Z1", "Z1", "ride_hailing")]["network_distance_km"]
        z7 = self.by_key[("Z7", "Z7", "ride_hailing")]["network_distance_km"]
        z9 = self.by_key[("Z9", "Z9", "ride_hailing")]["network_distance_km"]
        self.assertAlmostEqual(z1, self.network["zone_by_id"]["Z1"]["mean_intrazonal_distance"], places=3)
        self.assertGreater(z7, z1)
        self.assertGreater(z9, z1)

    def test_service_routing_minimizes_time_not_distance(self):
        adjacency = {
            "A": [("B", "direct", 12.0), ("C", "first", 5.0)],
            "C": [("A", "first", 5.0), ("B", "second", 5.0)],
            "B": [("A", "direct", 12.0), ("C", "second", 5.0)],
        }
        distance, transfers, generalized_time = _shortest_service_path(
            adjacency, "A", "B", speed_kmh=35.0, transfer_penalty_min=7.0,
            origin_access_min=3.0, destination_access_min=4.0,
        )
        self.assertEqual(distance, 12.0)  # longer than 10 km, but faster after transfer penalty
        self.assertEqual(transfers, 0)
        self.assertAlmostEqual(generalized_time, 3.0 + 12.0 / 35.0 * 60.0 + 4.0)

    def test_no_agent_weather_or_dispatch_inputs(self):
        row = calculate_od_option(self.network, "Z3", "Z7", "bus")
        forbidden = {
            "agent_id", "age_group", "digital_access", "weather_type", "coupon",
            "dispatch_success", "congestion_multiplier",
        }
        self.assertTrue(forbidden.isdisjoint(row))
        evidence = self.network["config"]["evidence_references"]["yoyo_database_review"]
        self.assertFalse(evidence["applied_in_this_baseline_network"])


if __name__ == "__main__":
    unittest.main()
