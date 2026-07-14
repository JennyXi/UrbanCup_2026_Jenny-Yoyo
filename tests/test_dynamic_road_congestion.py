import copy
import unittest
from datetime import datetime

from custom.transport.dynamic_congestion import (
    bpr_dynamic_congestion_multiplier,
    calculate_dynamic_congestion_leg_mode_option,
    load_dynamic_congestion_configuration,
    marginal_extra_multiplier,
    validate_dynamic_congestion_configuration,
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

    def leg(self, origin, destination, departure, leg_id="t10-marginal-leg"):
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

    def option(
        self, mode, excess_flow, events=(), departure=None,
        origin="Z1", destination="Z2", direction="eastbound",
    ):
        departure = departure or datetime(2026, 7, 6, 12, 0)
        return calculate_dynamic_congestion_leg_mode_option(
            self.network,
            self.leg(origin, destination, departure),
            mode,
            events,
            excess_flow,
            corridor_id="C_TEST",
            direction=direction,
            shared_state_flow_is_aggregated=True,
            excess_flow_source="agent_mode_choice_scenario_delta",
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )

    def test_zero_excess_flow_multiplier_is_exactly_one(self):
        result = self.option(
            "bus", 0.0, (), datetime(2026, 7, 6, 8, 20), "Z4", "Z1", "inbound"
        )
        self.assertLess(result["period_speed_multiplier"], 1.0)
        self.assertEqual(result["extra_multiplier"], 1.0)
        self.assertFalse(result["motor_vehicle_speed_floor_applied"])
        self.assertFalse(result["motor_vehicle_oversaturated"])
        self.assertEqual(result["unclipped_final_speed_kmh"], result["final_speed_kmh"])
        self.assertEqual(
            result["final_in_vehicle_time_min"],
            result["weather_adjusted_vehicle_time_min"],
        )
        self.assertTrue(all(
            row["extra_multiplier"] == 1.0
            for row in result["dynamic_congestion_segments"]
        ))

    def test_same_excess_flow_has_more_effect_in_core_peak_than_off_peak(self):
        core = self.option(
            "ride_hailing", 900.0, (), datetime(2026, 7, 6, 8, 20)
        )
        off_peak = self.option(
            "ride_hailing", 900.0, (), datetime(2026, 7, 6, 12, 0)
        )
        self.assertGreater(core["baseline_vc"], off_peak["baseline_vc"])
        self.assertLess(
            core["dynamic_congestion_segments"][0]["extra_multiplier"],
            off_peak["dynamic_congestion_segments"][0]["extra_multiplier"],
        )

    def test_rain_capacity_reduction_increases_marginal_effect(self):
        normal = self.option("bus", 900.0)
        rain = self.option("bus", 900.0, self.rain_event())
        self.assertLess(
            rain["weather_capacity_at_vehicle_start"],
            normal["weather_capacity_at_vehicle_start"],
        )
        self.assertGreater(rain["baseline_vc_weather"], normal["baseline_vc_weather"])
        self.assertGreater(rain["scenario_vc"], normal["scenario_vc"])
        self.assertLess(rain["extra_multiplier"], normal["extra_multiplier"])

    def test_main_commute_direction_has_more_effect_than_reverse(self):
        main = self.option(
            "bus", 600.0, (), datetime(2026, 7, 6, 8, 20),
            "Z4", "Z1", "inbound",
        )
        reverse = self.option(
            "bus", 600.0, (), datetime(2026, 7, 6, 8, 20),
            "Z1", "Z4", "outbound",
        )
        self.assertGreater(main["baseline_vc"], reverse["baseline_vc"])
        self.assertLess(
            main["dynamic_congestion_segments"][0]["extra_multiplier"],
            reverse["dynamic_congestion_segments"][0]["extra_multiplier"],
        )

    def test_final_total_time_closes(self):
        result = self.option("ride_hailing", 1200.0, self.rain_event())
        expected = (
            result["weather_adjusted_total_time_min"]
            - result["weather_adjusted_vehicle_time_min"]
            + result["final_in_vehicle_time_min"]
        )
        self.assertAlmostEqual(result["final_total_time_min"], expected, places=6)

    def test_extreme_input_limits_and_overflow_protection(self):
        result = self.option(
            "ride_hailing", 1e300, (), datetime(2026, 7, 6, 12),
            "Z9", "Z1", "inbound",
        )
        limits = self.congestion_config["safety_limits"]
        self.assertEqual(result["scenario_vc"], limits["maximum_vc"])
        self.assertGreaterEqual(result["final_speed_kmh"], limits["minimum_final_speed_kmh"])
        self.assertLess(result["unclipped_final_speed_kmh"], 10.0)
        self.assertTrue(result["motor_vehicle_speed_floor_applied"])
        self.assertTrue(result["motor_vehicle_oversaturated"])
        self.assertTrue(all(
            row["duration_min"] <= limits["maximum_segment_time_min"] + 1e-9
            for row in result["dynamic_congestion_segments"]
        ))
        self.assertGreaterEqual(
            bpr_dynamic_congestion_multiplier(1e300, self.congestion_config), 0.0
        )

    def test_dynamic_delay_resegments_at_time_and_weather_boundaries(self):
        events = [{
            "weather_type": "heavy_rain",
            "start": datetime(2026, 7, 6, 9, 40),
            "end": datetime(2026, 7, 6, 9, 50),
        }]
        result = self.option(
            "ride_hailing", 3600.0, events, datetime(2026, 7, 6, 9, 20),
            "Z9", "Z1", "inbound",
        )
        phases = {row["weather_phase"] for row in result["dynamic_congestion_segments"]}
        bins = {row["time_bin"] for row in result["dynamic_congestion_segments"]}
        self.assertIn("active", phases)
        self.assertIn("recovery", phases)
        self.assertIn("morning_recovery", bins)
        self.assertIn("day_off_peak", bins)

    def test_marginal_formula_matches_bpr_ratio_and_never_exceeds_one(self):
        background = 0.88
        scenario = 1.38
        expected = (
            bpr_dynamic_congestion_multiplier(scenario, self.congestion_config)
            / bpr_dynamic_congestion_multiplier(background, self.congestion_config)
        )
        actual = marginal_extra_multiplier(background, scenario, self.congestion_config)
        self.assertAlmostEqual(actual, expected)
        self.assertLessEqual(actual, 1.0)
        self.assertEqual(
            marginal_extra_multiplier(background, background, self.congestion_config), 1.0
        )

    def test_shared_state_key_is_corridor_direction_time_bin(self):
        bus = self.option("bus", 750.0, direction="eastbound")
        ride = self.option("ride_hailing", 750.0, direction="eastbound")
        self.assertEqual(
            bus["shared_state_key_at_vehicle_start"],
            "C_TEST|eastbound|day_off_peak",
        )
        self.assertEqual(
            bus["shared_state_key_at_vehicle_start"],
            ride["shared_state_key_at_vehicle_start"],
        )
        for field in (
            "weather_capacity_at_vehicle_start", "excess_road_flow_pcu_per_hour",
            "baseline_vc", "scenario_vc",
        ):
            self.assertEqual(bus[field], ride[field])

    def test_walk_and_metro_remain_outside_road_state(self):
        walk = self.option("walk", None, self.rain_event(), origin="Z1", destination="Z1")
        metro = self.option("metro", None, self.rain_event(), origin="Z1", destination="Z4")
        for result in (walk, metro):
            self.assertIsNone(result["corridor_id"])
            self.assertIsNone(result["excess_road_flow_pcu_per_hour"])
            self.assertIsNone(result["baseline_vc"])
            self.assertEqual(result["extra_multiplier"], 1.0)
            self.assertIsNone(result["motor_vehicle_speed_floor_applied"])
            self.assertIsNone(result["motor_vehicle_oversaturated"])
            self.assertIsNone(result["unclipped_final_speed_kmh"])
            self.assertEqual(
                result["final_in_vehicle_time_min"],
                result["weather_adjusted_vehicle_time_min"],
            )

    def test_configuration_forbids_manual_weather_demand_addition(self):
        flow = self.congestion_config["traffic_flow_input"]
        self.assertEqual(flow["field"], "excess_road_flow_pcu_per_hour")
        self.assertEqual(flow["unit"], "PCU/hour/direction")
        self.assertTrue(flow["includes_weather_response_from_agent_mode_choice"])
        self.assertFalse(flow["manual_weather_demand_addition_allowed"])
        self.assertEqual(
            self.congestion_config["shared_state_key"]["fields"],
            ["corridor_id", "direction", "time_bin"],
        )

    def test_all_eight_t8_periods_require_explicit_baseline_vc(self):
        expected = {
            "morning_shoulder", "morning_core_peak", "morning_recovery",
            "day_off_peak", "evening_shoulder", "evening_core_peak",
            "evening_recovery", "night",
        }
        baseline = self.congestion_config["baseline_vc"]
        self.assertEqual(set(baseline["period_base"]), expected)
        self.assertEqual(set(baseline["main_commute_direction_addition"]), expected)
        changed = copy.deepcopy(self.congestion_config)
        del changed["baseline_vc"]["period_base"]["morning_recovery"]
        with self.assertRaises(ValueError):
            validate_dynamic_congestion_configuration(changed)

    def test_long_bus_leg_is_split_but_not_capped_at_120_minutes(self):
        result = self.option(
            "bus", 1e300, (), datetime(2026, 7, 6, 12),
            "Z9", "Z1", "inbound",
        )
        limit = self.congestion_config["safety_limits"]["maximum_segment_time_min"]
        self.assertGreater(result["final_in_vehicle_time_min"], limit)
        self.assertTrue(all(
            row["duration_min"] <= limit + 1e-9
            for row in result["dynamic_congestion_segments"]
        ))
        self.assertAlmostEqual(
            sum(row["duration_min"] for row in result["dynamic_congestion_segments"]),
            result["final_in_vehicle_time_min"],
            places=5,
        )

    def test_negative_excess_flow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            self.option("bus", -1.0)

    def test_unavailable_road_option_does_not_bypass_flow_validation(self):
        departure = datetime(2026, 7, 6, 3, 0)
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            self.option("bus", -1.0, (), departure)
        with self.assertRaisesRegex(ValueError, "preaggregated excess PCU"):
            calculate_dynamic_congestion_leg_mode_option(
                self.network,
                self.leg("Z1", "Z2", departure),
                "bus",
                (),
                750.0,
                corridor_id="C_TEST",
                direction="eastbound",
                shared_state_flow_is_aggregated=False,
                congestion_config=self.congestion_config,
                weather_config=self.weather_config,
                time_config=self.time_config,
            )

    def test_shared_state_requires_preaggregated_all_mode_flow(self):
        shared = self.congestion_config["shared_state_key"]
        self.assertTrue(shared["requires_preaggregated_all_motorized_excess_pcu"])
        self.assertFalse(shared["separate_mode_flow_inputs_allowed"])
        with self.assertRaisesRegex(ValueError, "preaggregated excess PCU"):
            calculate_dynamic_congestion_leg_mode_option(
                self.network,
                self.leg("Z1", "Z2", datetime(2026, 7, 6, 12)),
                "bus",
                (),
                750.0,
                corridor_id="C_TEST",
                direction="eastbound",
                shared_state_flow_is_aggregated=False,
                congestion_config=self.congestion_config,
                weather_config=self.weather_config,
                time_config=self.time_config,
            )

    def test_invalid_flow_unit_is_rejected(self):
        changed = copy.deepcopy(self.congestion_config)
        changed["flow_unit"] = "vehicles"
        with self.assertRaises(ValueError):
            validate_dynamic_congestion_configuration(changed)

    def test_repeated_run_is_idempotent(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        kwargs = dict(
            network=self.network,
            leg=leg,
            mode="ride_hailing",
            events=self.rain_event(),
            excess_road_flow_pcu_per_hour=1200.0,
            corridor_id="C_TEST",
            direction="inbound",
            shared_state_flow_is_aggregated=True,
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )
        self.assertEqual(
            calculate_dynamic_congestion_leg_mode_option(**kwargs),
            calculate_dynamic_congestion_leg_mode_option(**kwargs),
        )

    def test_t7_t8_t9_fields_are_not_overwritten(self):
        leg = self.leg("Z4", "Z1", datetime(2026, 7, 6, 12))
        original = copy.deepcopy(leg)
        weather = calculate_weather_adjusted_leg_mode_option(
            self.network, leg, "bus", self.rain_event(),
            self.weather_config, self.time_config,
        )
        dynamic = calculate_dynamic_congestion_leg_mode_option(
            self.network, leg, "bus", self.rain_event(), 900.0,
            corridor_id="C_TEST", direction="inbound",
            shared_state_flow_is_aggregated=True,
            congestion_config=self.congestion_config,
            weather_config=self.weather_config,
            time_config=self.time_config,
        )
        self.assertEqual(leg, original)
        for field in WEATHER_SUPPLY_OUTPUT_FIELDS:
            self.assertEqual(dynamic[field], weather[field], field)


if __name__ == "__main__":
    unittest.main()
