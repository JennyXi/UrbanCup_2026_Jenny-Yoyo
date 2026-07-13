import copy
import unittest
from datetime import datetime

from custom.transport.dynamic_congestion import (
    bpr_dynamic_congestion_multiplier,
    calculate_dynamic_congestion_leg_mode_option,
    load_dynamic_congestion_configuration,
)
from custom.transport.network import build_transport_network, calculate_od_option
from custom.transport.time_supply import load_time_supply_configuration
from custom.transport.weather_supply import (
    WEATHER_SUPPLY_OUTPUT_FIELDS,
    calculate_weather_adjusted_leg_mode_option,
    load_weather_supply_configuration,
)


class DynamicRoadCongestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = build_transport_network()
        cls.time_config = load_time_supply_configuration()
        cls.weather_config = load_weather_supply_configuration()
        cls.congestion_config = load_dynamic_congestion_configuration()

    def leg(self, origin, destination, departure, leg_id="dynamic-road-leg"):
        road_distance = calculate_od_option(
            self.network, origin, destination, "ride_hailing"
        )["road_network_distance_km"]
        return {
            "leg_id": leg_id,
            "origin_zone": origin,
            "destination_zone": destination,
            "road_network_distance_km": road_distance,
            "departure_time": departure,
        }

    @staticmethod
    def rain_event():
        return [{
            "weather_type": "heavy_rain",
            "start": datetime(2026, 7, 6, 0, 0),
            "end": datetime(2026, 7, 7, 0, 0),
        }]

    def option(self, mode, volume, events=(), departure=None, origin="Z4", destination="Z1"):
        departure = departure or datetime(2026, 7, 6, 12, 0)
        return calculate_dynamic_congestion_leg_mode_option(
            self.network,
            self.leg(origin, destination, departure),
            mode,
            events,
            volume,
            road_state_id="shared-test-road",
            capacity_profile_id="aggregate_network",
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )

    def test_capacity_multiplier_does_not_directly_enter_speed_formula(self):
        result = self.option("bus", 0.0, self.rain_event())
        base_speed = self.network["config"]["modes"]["bus"]["base_speed_kmh"]
        self.assertEqual(result["road_capacity_multiplier"], 0.85)
        self.assertEqual(result["dynamic_congestion_multiplier"], 1.0)
        self.assertAlmostEqual(
            result["weather_free_flow_speed"],
            base_speed * result["final_speed_multiplier"],
            places=6,
        )
        self.assertEqual(result["final_speed"], result["weather_free_flow_speed"])
        self.assertNotAlmostEqual(
            result["final_speed"],
            result["weather_free_flow_speed"] * result["road_capacity_multiplier"],
        )

    def test_zero_volume_has_no_extra_dynamic_congestion(self):
        result = self.option("ride_hailing", 0.0)
        self.assertEqual(result["volume_capacity_ratio"], 0.0)
        self.assertEqual(result["dynamic_congestion_multiplier"], 1.0)
        self.assertEqual(result["final_speed"], result["weather_free_flow_speed"])
        self.assertEqual(
            result["final_in_vehicle_time"],
            result["weather_adjusted_vehicle_time_min"],
        )

    def test_rain_capacity_reduction_raises_vc_at_same_volume(self):
        normal = self.option("bus", 1200.0)
        rain = self.option("bus", 1200.0, self.rain_event())
        self.assertEqual(normal["normal_road_capacity"], rain["normal_road_capacity"])
        self.assertLess(rain["weather_capacity"], normal["weather_capacity"])
        self.assertGreater(rain["volume_capacity_ratio"], normal["volume_capacity_ratio"])

    def test_higher_vc_reduces_final_speed(self):
        low = self.option("ride_hailing", 300.0)
        high = self.option("ride_hailing", 1800.0)
        self.assertGreater(high["volume_capacity_ratio"], low["volume_capacity_ratio"])
        self.assertLess(high["dynamic_congestion_multiplier"], low["dynamic_congestion_multiplier"])
        self.assertLess(high["final_speed"], low["final_speed"])
        self.assertGreater(high["final_in_vehicle_time"], low["final_in_vehicle_time"])

    def test_bus_and_ride_hailing_share_the_same_road_state(self):
        bus = self.option("bus", 1350.0, self.rain_event())
        ride = self.option("ride_hailing", 1350.0, self.rain_event())
        for field in (
            "road_state_id", "capacity_profile_id", "normal_road_capacity",
            "weather_capacity", "current_road_volume", "volume_capacity_ratio",
            "dynamic_congestion_multiplier",
        ):
            self.assertEqual(bus[field], ride[field])
        self.assertNotEqual(bus["weather_free_flow_speed"], ride["weather_free_flow_speed"])

    def test_walk_and_metro_do_not_use_road_capacity(self):
        walk = self.option("walk", 1800.0, self.rain_event(), origin="Z1", destination="Z1")
        metro = self.option("metro", 1800.0, self.rain_event(), origin="Z1", destination="Z4")
        for result in (walk, metro):
            self.assertIsNone(result["normal_road_capacity"])
            self.assertIsNone(result["weather_capacity"])
            self.assertIsNone(result["current_road_volume"])
            self.assertIsNone(result["volume_capacity_ratio"])
            self.assertEqual(result["dynamic_congestion_multiplier"], 1.0)
            self.assertEqual(result["final_speed"], result["weather_free_flow_speed"])
            self.assertEqual(result["final_in_vehicle_time"], result["weather_adjusted_vehicle_time_min"])

    def test_period_weather_and_dynamic_factors_each_apply_once(self):
        result = self.option(
            "bus",
            900.0,
            self.rain_event(),
            datetime(2026, 7, 6, 8, 20),
            "Z4",
            "Z1",
        )
        first = result["weather_supply_segments"][0]
        self.assertEqual(first["period_direction_multiplier"], 0.75)
        self.assertEqual(first["weather_speed_multiplier"], 0.80)
        expected_dynamic = bpr_dynamic_congestion_multiplier(
            result["current_road_volume"] / result["weather_capacity"],
            self.congestion_config,
        )
        base_speed = self.network["config"]["modes"]["bus"]["base_speed_kmh"]
        self.assertAlmostEqual(result["dynamic_congestion_multiplier"], expected_dynamic, places=6)
        self.assertAlmostEqual(
            result["final_speed"],
            base_speed * result["final_speed_multiplier"] * expected_dynamic,
            places=5,
        )

    def test_repeated_run_does_not_accumulate_reduction(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        kwargs = dict(
            network=self.network,
            leg=leg,
            mode="ride_hailing",
            events=self.rain_event(),
            current_road_volume=1200.0,
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )
        self.assertEqual(
            calculate_dynamic_congestion_leg_mode_option(**kwargs),
            calculate_dynamic_congestion_leg_mode_option(**kwargs),
        )

    def test_t7_t8_t9_inputs_and_outputs_are_not_overwritten(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        original_leg = copy.deepcopy(leg)
        weather = calculate_weather_adjusted_leg_mode_option(
            self.network,
            leg,
            "bus",
            self.rain_event(),
            self.weather_config,
            self.time_config,
        )
        dynamic = calculate_dynamic_congestion_leg_mode_option(
            self.network,
            leg,
            "bus",
            self.rain_event(),
            1200.0,
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )
        self.assertEqual(leg, original_leg)
        for field in WEATHER_SUPPLY_OUTPUT_FIELDS:
            self.assertEqual(dynamic[field], weather[field], field)

    def test_configuration_excludes_future_supply_modules(self):
        self.assertTrue(all(value is False for value in self.congestion_config["boundaries"].values()))
        volume = self.congestion_config["traffic_volume_input"]
        self.assertFalse(volume["generated_by_this_layer"])
        self.assertFalse(volume["agent_mode_choice_required"])


if __name__ == "__main__":
    unittest.main()
