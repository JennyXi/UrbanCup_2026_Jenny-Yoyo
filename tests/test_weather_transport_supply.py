import copy
import unittest
from datetime import datetime

from custom.transport.network import (
    build_transport_network,
    calculate_od_option,
    load_transport_configuration,
)
from custom.transport.time_supply import (
    calculate_time_adjusted_leg_mode_option,
    load_time_supply_configuration,
)
from custom.transport.weather_supply import (
    calculate_weather_adjusted_leg_mode_option,
    load_weather_supply_configuration,
    weather_supply_parameters,
)


class WeatherTransportSupplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = build_transport_network()
        cls.time_config = load_time_supply_configuration()
        cls.weather_config = load_weather_supply_configuration()

    def leg(self, origin, destination, departure, leg_id="weather-leg"):
        road = calculate_od_option(
            self.network, origin, destination, "ride_hailing"
        )["road_network_distance_km"]
        return {
            "leg_id": leg_id,
            "origin_zone": origin,
            "destination_zone": destination,
            "road_network_distance_km": road,
            "departure_time": departure,
        }

    @staticmethod
    def event(start, end, weather_type="heavy_rain"):
        return [{"weather_type": weather_type, "start": start, "end": end}]

    def option(self, origin, destination, mode, departure, events=()):
        return calculate_weather_adjusted_leg_mode_option(
            self.network,
            self.leg(origin, destination, departure),
            mode,
            events,
            self.weather_config,
            self.time_config,
        )

    def test_period_direction_and_weather_multipliers_combine_once(self):
        events = self.event(datetime(2026, 7, 6, 7), datetime(2026, 7, 6, 10))
        result = self.option("Z4", "Z1", "bus", datetime(2026, 7, 6, 8, 20), events)
        active = next(row for row in result["weather_supply_segments"] if row["weather_phase"] == "active")
        self.assertEqual(active["period_direction_multiplier"], 0.75)
        self.assertEqual(active["weather_speed_multiplier"], 0.80)
        self.assertEqual(active["final_speed_multiplier"], 0.60)
        self.assertEqual(active["road_capacity_multiplier"], 0.85)

    def test_ordinary_and_strongest_peak_are_mutually_exclusive(self):
        ordinary = self.option("Z1", "Z4", "ride_hailing", datetime(2026, 7, 6, 8, 20))
        strongest = self.option("Z4", "Z1", "ride_hailing", datetime(2026, 7, 6, 8, 20))
        ordinary_values = {row["period_direction_multiplier"] for row in ordinary["weather_supply_segments"]}
        strongest_values = {row["period_direction_multiplier"] for row in strongest["weather_supply_segments"]}
        self.assertIn(0.85, ordinary_values)
        self.assertIn(0.75, strongest_values)
        self.assertNotIn(round(0.85 * 0.75, 6), strongest_values)

    def test_heavy_rain_slows_walk_bus_and_ride_hailing(self):
        events = self.event(datetime(2026, 7, 6), datetime(2026, 7, 7))
        cases = (("walk", "Z1", "Z1"), ("bus", "Z4", "Z1"),
                 ("ride_hailing", "Z4", "Z1"))
        rain = self.weather_config["weather_types"]["heavy_rain"]
        for mode, origin, destination in cases:
            result = self.option(origin, destination, mode, datetime(2026, 7, 6, 12), events)
            self.assertTrue(result["available"])
            self.assertEqual(
                result["weather_supply_segments"][0]["weather_speed_multiplier"],
                rain["active_speed_multipliers"][mode],
            )
            self.assertGreater(result["weather_adjusted_total_time_min"], result["time_adjusted_total_time_min"])

    def test_heavy_rain_does_not_change_metro_speed(self):
        events = self.event(datetime(2026, 7, 6), datetime(2026, 7, 7))
        result = self.option("Z1", "Z4", "metro", datetime(2026, 7, 6, 12), events)
        self.assertEqual(result["weather_speed_multiplier"], 1.0)
        self.assertEqual(result["weather_adjusted_total_time_min"], result["time_adjusted_total_time_min"])

    def test_heavy_rain_end_enters_recovery_before_normal(self):
        events = self.event(datetime(2026, 7, 6, 7), datetime(2026, 7, 6, 8))
        recovery = weather_supply_parameters(
            datetime(2026, 7, 6, 8, 30), "walk", events, self.weather_config
        )
        normal = weather_supply_parameters(
            datetime(2026, 7, 6, 10), "walk", events, self.weather_config
        )
        self.assertEqual(recovery["weather_phase"], "recovery")
        rain = self.weather_config["weather_types"]["heavy_rain"]
        self.assertEqual(recovery["weather_speed_multiplier"], rain["recovery_speed_multipliers"]["walk"])
        self.assertEqual(recovery["road_capacity_multiplier"], rain["recovery_road_capacity_multiplier"])
        self.assertEqual(normal["weather_phase"], "normal")

    def test_extreme_heat_default_speed_is_one(self):
        events = self.event(
            datetime(2026, 7, 6, 11), datetime(2026, 7, 6, 18), "extreme_heat"
        )
        for mode in ("walk", "bus", "metro", "ride_hailing"):
            params = weather_supply_parameters(
                datetime(2026, 7, 6, 12), mode, events, self.weather_config
            )
            self.assertEqual(params["weather_speed_multiplier"], 1.0)

    def test_original_od_base_speed_and_base_times_are_not_overwritten(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        leg_before = copy.deepcopy(leg)
        transport_config = load_transport_configuration()
        speeds_before = {
            mode: params["base_speed_kmh"] for mode, params in transport_config["modes"].items()
        }
        base = calculate_od_option(self.network, "Z4", "Z1", "bus")
        timed = calculate_time_adjusted_leg_mode_option(
            self.network, leg, "bus", self.time_config
        )
        result = calculate_weather_adjusted_leg_mode_option(
            self.network, leg, "bus",
            self.event(datetime(2026, 7, 6), datetime(2026, 7, 7)),
            self.weather_config, self.time_config,
        )
        self.assertEqual(leg, leg_before)
        self.assertEqual(result["origin_zone"], base["origin_zone"])
        self.assertEqual(result["destination_zone"], base["destination_zone"])
        self.assertEqual(result["in_vehicle_time_min"], base["in_vehicle_time_min"])
        self.assertEqual(result["base_total_time_min"], timed["base_total_time_min"])
        self.assertEqual(speeds_before, {
            mode: params["base_speed_kmh"] for mode, params in self.network["config"]["modes"].items()
        })

    def test_repeated_run_is_idempotent(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        events = self.event(datetime(2026, 7, 6), datetime(2026, 7, 7))
        first = calculate_weather_adjusted_leg_mode_option(
            self.network, leg, "ride_hailing", events, self.weather_config, self.time_config
        )
        second = calculate_weather_adjusted_leg_mode_option(
            self.network, leg, "ride_hailing", events, self.weather_config, self.time_config
        )
        self.assertEqual(first, second)

    def test_outbound_return_use_their_own_datetimes(self):
        events = self.event(datetime(2026, 7, 6, 8), datetime(2026, 7, 6, 9))
        outbound = self.option("Z4", "Z1", "ride_hailing", datetime(2026, 7, 6, 8, 10), events)
        returning = self.option("Z1", "Z4", "ride_hailing", datetime(2026, 7, 6, 12), events)
        self.assertIn("active", outbound["weather_phase"])
        self.assertEqual(returning["weather_phase"], "normal")

    def test_trip_is_segmented_at_weather_boundary(self):
        departure = datetime(2026, 7, 6, 10, 20)
        event_start = datetime(2026, 7, 6, 10, 40)
        events = self.event(event_start, datetime(2026, 7, 6, 12))
        result = self.option("Z9", "Z1", "bus", departure, events)
        phases = [row["weather_phase"] for row in result["weather_supply_segments"]]
        self.assertIn("normal", phases)
        self.assertIn("active", phases)

    def test_forbidden_dynamic_modules_remain_disabled(self):
        boundaries = self.weather_config["boundaries"]
        self.assertTrue(all(value is False for value in boundaries.values()))

    def test_road_capacity_is_independent_from_speed_and_time(self):
        departure = datetime(2026, 7, 6, 12)
        events = self.event(datetime(2026, 7, 6), datetime(2026, 7, 7))
        baseline = self.option("Z4", "Z1", "bus", departure, events)
        changed = copy.deepcopy(self.weather_config)
        changed["weather_types"]["heavy_rain"]["road_capacity_multiplier"] = 0.70
        result = calculate_weather_adjusted_leg_mode_option(
            self.network, self.leg("Z4", "Z1", departure), "bus", events,
            changed, self.time_config,
        )
        self.assertNotEqual(baseline["road_capacity_multiplier"], result["road_capacity_multiplier"])
        self.assertEqual(baseline["weather_speed_multiplier"], result["weather_speed_multiplier"])
        self.assertEqual(
            baseline["weather_adjusted_total_time_min"],
            result["weather_adjusted_total_time_min"],
        )


if __name__ == "__main__":
    unittest.main()
