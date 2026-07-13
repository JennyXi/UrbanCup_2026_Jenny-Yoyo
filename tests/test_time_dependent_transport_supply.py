import math
import copy
import unittest
from datetime import datetime

from custom.transport.network import build_transport_network, calculate_od_option
from custom.transport.time_supply import (
    TIME_SUPPLY_EXTRA_FIELDS,
    _advance_wait,
    calculate_time_adjusted_leg_mode_option,
    load_time_supply_configuration,
    period_for_datetime,
    period_supply_parameters,
    split_interval_by_period,
)


class TimeDependentTransportSupplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = build_transport_network()
        cls.config = load_time_supply_configuration()

    def leg(self, origin, destination, departure, leg_id="test-leg"):
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

    def option(self, origin, destination, mode, departure, leg_id="test-leg"):
        return calculate_time_adjusted_leg_mode_option(
            self.network,
            self.leg(origin, destination, departure, leg_id),
            mode,
            self.config,
            seed=47,
        )

    def test_eight_period_boundaries(self):
        expected = {
            "06:59": "night",
            "07:00": "morning_shoulder",
            "08:00": "morning_core_peak",
            "09:30": "morning_recovery",
            "10:30": "day_off_peak",
            "17:00": "evening_shoulder",
            "17:30": "evening_core_peak",
            "19:00": "evening_recovery",
            "20:00": "night",
        }
        self.assertEqual(len(self.config["time_periods"]), 8)
        for clock, period_id in expected.items():
            moment = datetime.fromisoformat(f"2026-07-06 {clock}:00")
            self.assertEqual(period_for_datetime(moment, self.config)["period_id"], period_id)

    def test_cross_period_overlap_minutes_are_exact(self):
        start = datetime(2026, 7, 6, 7, 50)
        end = datetime(2026, 7, 6, 8, 10)
        self.assertEqual(
            split_interval_by_period(start, end, self.config),
            [("morning_shoulder", 10.0), ("morning_core_peak", 10.0)],
        )
        crossing = self.option("Z4", "Z1", "bus", start)
        self.assertIn("morning_shoulder", crossing["time_period"])
        self.assertIn("morning_core_peak", crossing["time_period"])

    def test_waiting_crosses_period_boundary_instead_of_locking_to_start_period(self):
        start = datetime(2026, 7, 6, 7, 58)
        wait, segments = _advance_wait(
            start, self.config, "bus", "Z1", "Z2"
        )
        expected = 2.0 + (1.0 - 2.0 / 4.5) * 4.0
        self.assertAlmostEqual(wait, expected)
        self.assertEqual(
            [period_id for period_id, _, _ in segments],
            ["morning_shoulder", "morning_core_peak"],
        )

    def test_day_off_peak_reproduces_static_baseline(self):
        departure = datetime(2026, 7, 6, 12, 0)
        for origin, destination in (
            ("Z1", "Z2"), ("Z4", "Z1"), ("Z9", "Z1"), ("Z1", "Z9")
        ):
            for mode in ("bus", "metro", "ride_hailing"):
                static = calculate_od_option(self.network, origin, destination, mode)
                adjusted = self.option(origin, destination, mode, departure)
                self.assertTrue(adjusted["operating"])
                self.assertAlmostEqual(
                    adjusted["time_adjusted_total_time_min"],
                    static["total_time_min"],
                    places=2,
                )

    def test_all_peak_periods_use_the_single_ordinary_road_multiplier(self):
        shoulder = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 7, 5))
        core = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 8, 5))
        recovery = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 9, 35))
        off_peak = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 12, 0))
        self.assertEqual(shoulder["period_speed_multiplier"], 0.85)
        self.assertEqual(core["period_speed_multiplier"], 0.85)
        self.assertEqual(recovery["period_speed_multiplier"], 0.85)
        self.assertEqual(off_peak["period_speed_multiplier"], 1.0)

    def test_base_speeds_and_final_non_stacked_peak_multipliers(self):
        modes = self.network["config"]["modes"]
        self.assertEqual(modes["walk"]["base_speed_kmh"], 4.8)
        self.assertEqual(modes["bus"]["base_speed_kmh"], 18.0)
        self.assertEqual(modes["metro"]["base_speed_kmh"], 35.0)
        self.assertEqual(modes["ride_hailing"]["base_speed_kmh"], 33.0)

        ordinary_bus = period_supply_parameters(
            self.config, "bus", "Z1", "Z2", datetime(2026, 7, 6, 8, 15)
        )
        directional_bus = period_supply_parameters(
            self.config, "bus", "Z4", "Z1", datetime(2026, 7, 6, 8, 15)
        )
        ordinary_ride = period_supply_parameters(
            self.config, "ride_hailing", "Z1", "Z2", datetime(2026, 7, 6, 8, 15)
        )
        directional_ride = period_supply_parameters(
            self.config, "ride_hailing", "Z4", "Z1", datetime(2026, 7, 6, 8, 15)
        )
        walk = period_supply_parameters(
            self.config, "walk", "Z4", "Z1", datetime(2026, 7, 6, 8, 15)
        )
        metro = period_supply_parameters(
            self.config, "metro", "Z4", "Z1", datetime(2026, 7, 6, 8, 15)
        )

        self.assertEqual(ordinary_bus["speed_multiplier"], 0.85)
        self.assertEqual(directional_bus["speed_multiplier"], 0.75)
        self.assertEqual(ordinary_ride["speed_multiplier"], 0.85)
        self.assertEqual(directional_ride["speed_multiplier"], 0.75)
        self.assertEqual(walk["speed_multiplier"], 1.0)
        self.assertEqual(metro["speed_multiplier"], 1.0)
        self.assertAlmostEqual(18.0 * ordinary_bus["speed_multiplier"], 15.3)
        self.assertAlmostEqual(18.0 * directional_bus["speed_multiplier"], 13.5)
        self.assertAlmostEqual(33.0 * ordinary_ride["speed_multiplier"], 28.05)
        self.assertAlmostEqual(33.0 * directional_ride["speed_multiplier"], 24.75)
        self.assertNotEqual(directional_bus["speed_multiplier"], 0.85 * 0.75)

    def test_bus_peak_frequency_increases_while_vehicle_time_increases(self):
        peak = period_supply_parameters(
            self.config, "bus", "Z1", "Z2", datetime(2026, 7, 6, 8, 15)
        )
        off_peak = period_supply_parameters(
            self.config, "bus", "Z1", "Z2", datetime(2026, 7, 6, 12, 0)
        )
        self.assertLess(peak["headway_min"], off_peak["headway_min"])
        self.assertGreater(peak["service_frequency_multiplier"], off_peak["service_frequency_multiplier"])
        self.assertLess(peak["speed_multiplier"], off_peak["speed_multiplier"])

    def test_metro_speed_is_stable_but_peak_burden_is_higher(self):
        peak_params = period_supply_parameters(
            self.config, "metro", "Z5", "Z1", datetime(2026, 7, 6, 8, 15)
        )
        off_params = period_supply_parameters(
            self.config, "metro", "Z5", "Z1", datetime(2026, 7, 6, 12, 0)
        )
        self.assertEqual(peak_params["speed_multiplier"], 1.0)
        self.assertEqual(off_params["speed_multiplier"], 1.0)
        self.assertGreater(peak_params["crowding_index"], off_params["crowding_index"])
        peak = self.option("Z5", "Z1", "metro", datetime(2026, 7, 6, 8, 15))
        off_peak = self.option("Z5", "Z1", "metro", datetime(2026, 7, 6, 12, 0))
        self.assertEqual(peak["period_speed_multiplier"], 1.0)
        self.assertGreater(peak["period_transfer_penalty_min"], off_peak["period_transfer_penalty_min"])
        self.assertGreater(peak["period_wait_time_min"], off_peak["period_wait_time_min"])

    def test_night_supply_declines_and_last_train_is_enforced(self):
        day_bus = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 12, 0))
        night_bus = self.option("Z1", "Z2", "bus", datetime(2026, 7, 6, 21, 0))
        self.assertGreater(night_bus["period_wait_time_min"], day_bus["period_wait_time_min"])
        self.assertEqual(night_bus["supply_level"], "reduced")

        night_metro = self.option("Z1", "Z2", "metro", datetime(2026, 7, 6, 22, 0))
        after_service = self.option("Z1", "Z2", "metro", datetime(2026, 7, 6, 23, 31))
        self.assertTrue(night_metro["operating"])
        self.assertFalse(after_service["operating"])
        self.assertIsNotNone(night_metro["latest_feasible_departure"])

    def test_z9_waits_exceed_central_zone_in_every_period(self):
        for period in self.config["time_periods"]:
            sample = datetime.combine(datetime(2026, 7, 6).date(), datetime.strptime(period["start"], "%H:%M").time())
            central_bus = period_supply_parameters(self.config, "bus", "Z1", "Z2", sample)
            remote_bus = period_supply_parameters(self.config, "bus", "Z9", "Z6", sample)
            central_ride = period_supply_parameters(self.config, "ride_hailing", "Z1", "Z2", sample)
            remote_ride = period_supply_parameters(self.config, "ride_hailing", "Z9", "Z6", sample)
            central_metro = period_supply_parameters(self.config, "metro", "Z1", "Z2", sample)
            remote_metro = period_supply_parameters(self.config, "metro", "Z9", "Z1", sample)
            self.assertGreater(remote_bus["expected_wait_min"], central_bus["expected_wait_min"])
            self.assertGreater(remote_ride["expected_wait_min"], central_ride["expected_wait_min"])
            self.assertGreater(remote_metro["expected_wait_min"], central_metro["expected_wait_min"])

        central_trip = self.option("Z1", "Z2", "metro", datetime(2026, 7, 6, 8, 15))
        remote_trip = self.option("Z9", "Z1", "metro", datetime(2026, 7, 6, 8, 15))
        self.assertGreater(remote_trip["period_wait_time_min"], central_trip["period_wait_time_min"])
        self.assertLess(remote_trip["period_speed_multiplier"], 1.0)

    def test_ride_hailing_fleet_is_constant_and_availability_is_descriptive_only(self):
        ride_config = self.config["modes"]["ride_hailing"]
        self.assertEqual(ride_config["fleet_policy"]["normal_day_fleet_size_multiplier"], 1.0)
        self.assertFalse(ride_config["fleet_policy"]["time_varying_fleet_size"])
        self.assertFalse(ride_config["fleet_policy"]["dispatch_success_generated_here"])
        self.assertTrue(all(
            profile["baseline_availability"] == 1.0
            for profile in ride_config["profiles"].values()
        ))

        departure = datetime(2026, 7, 6, 8, 15)
        baseline = self.option("Z4", "Z1", "ride_hailing", departure)
        descriptive_change = copy.deepcopy(self.config)
        descriptive_change["modes"]["ride_hailing"]["profiles"]["morning_core_peak"][
            "baseline_availability"
        ] = 0.01
        changed = calculate_time_adjusted_leg_mode_option(
            self.network,
            self.leg("Z4", "Z1", departure),
            "ride_hailing",
            descriptive_change,
            seed=47,
        )
        self.assertTrue(baseline["operating"])
        self.assertTrue(changed["operating"])
        self.assertEqual(baseline["time_adjusted_total_time_min"], changed["time_adjusted_total_time_min"])
        self.assertEqual(baseline["period_wait_time_min"], changed["period_wait_time_min"])

    def test_morning_and_evening_directionality_are_opposite(self):
        morning_out = period_supply_parameters(
            self.config, "bus", "Z4", "Z1", datetime(2026, 7, 6, 8, 15)
        )
        morning_reverse = period_supply_parameters(
            self.config, "bus", "Z1", "Z4", datetime(2026, 7, 6, 8, 15)
        )
        evening_out = period_supply_parameters(
            self.config, "bus", "Z1", "Z4", datetime(2026, 7, 6, 18, 0)
        )
        evening_reverse = period_supply_parameters(
            self.config, "bus", "Z4", "Z1", datetime(2026, 7, 6, 18, 0)
        )
        self.assertTrue(morning_out["directional_peak_applied"])
        self.assertFalse(morning_reverse["directional_peak_applied"])
        self.assertTrue(evening_out["directional_peak_applied"])
        self.assertFalse(evening_reverse["directional_peak_applied"])
        self.assertEqual(morning_out["speed_multiplier"], 0.75)
        self.assertEqual(morning_reverse["speed_multiplier"], 0.85)
        self.assertEqual(evening_out["speed_multiplier"], 0.75)
        self.assertEqual(evening_reverse["speed_multiplier"], 0.85)

        late_return = period_supply_parameters(
            self.config, "bus", "Z1", "Z4", datetime(2026, 7, 6, 20, 30)
        )
        self.assertEqual(late_return["speed_multiplier"], 1.0)

    def test_each_leg_recomputes_speed_from_its_own_time_and_direction(self):
        morning_outbound = self.option(
            "Z4", "Z1", "bus", datetime(2026, 7, 6, 8, 15), "same-trip"
        )
        evening_return = self.option(
            "Z1", "Z4", "bus", datetime(2026, 7, 6, 18, 0), "same-trip"
        )
        off_peak_return = self.option(
            "Z1", "Z4", "bus", datetime(2026, 7, 6, 20, 30), "same-trip"
        )
        self.assertLess(morning_outbound["period_speed_multiplier"], 1.0)
        self.assertLess(evening_return["period_speed_multiplier"], 1.0)
        self.assertEqual(off_peak_return["period_speed_multiplier"], 1.0)
        self.assertNotEqual(
            morning_outbound["time_adjusted_total_time_min"],
            off_peak_return["time_adjusted_total_time_min"],
        )

    def test_speed_layers_are_not_duplicated_in_mode_profiles(self):
        forbidden = {"speed_multiplier", "road_speed_multiplier", "bus_speed", "ride_speed"}
        for mode in ("bus", "metro", "ride_hailing"):
            for profile in self.config["modes"][mode]["profiles"].values():
                self.assertTrue(forbidden.isdisjoint(profile))
        for impact in self.config["directional_peak"]["period_impacts"].values():
            self.assertTrue(forbidden.isdisjoint(impact))

    def test_suburban_directional_peak_uses_small_phase_shift_without_changing_base_period(self):
        outer_morning = period_supply_parameters(
            self.config, "bus", "Z4", "Z1", datetime(2026, 7, 6, 7, 45)
        )
        remote_morning = period_supply_parameters(
            self.config, "bus", "Z9", "Z1", datetime(2026, 7, 6, 7, 30)
        )
        outer_evening = period_supply_parameters(
            self.config, "bus", "Z1", "Z4", datetime(2026, 7, 6, 19, 5)
        )
        remote_evening = period_supply_parameters(
            self.config, "bus", "Z1", "Z9", datetime(2026, 7, 6, 19, 20)
        )
        self.assertEqual(outer_morning["period_id"], "morning_shoulder")
        self.assertEqual(outer_morning["directional_peak_period_id"], "morning_core_peak")
        self.assertEqual(outer_morning["directional_phase_shift_min"], 15)
        self.assertEqual(outer_morning["service_profile_period_id"], "morning_core_peak")
        self.assertAlmostEqual(outer_morning["headway_min"], 6.0 * 1.25)
        self.assertEqual(remote_morning["period_id"], "morning_shoulder")
        self.assertEqual(remote_morning["directional_peak_period_id"], "morning_core_peak")
        self.assertEqual(remote_morning["directional_phase_shift_min"], 30)
        self.assertAlmostEqual(remote_morning["headway_min"], 6.0 * 1.80)
        self.assertEqual(outer_evening["period_id"], "evening_recovery")
        self.assertEqual(outer_evening["directional_peak_period_id"], "evening_core_peak")
        self.assertEqual(outer_evening["directional_phase_shift_min"], -15)
        self.assertEqual(remote_evening["period_id"], "evening_recovery")
        self.assertEqual(remote_evening["directional_peak_period_id"], "evening_core_peak")
        self.assertEqual(remote_evening["directional_phase_shift_min"], -30)
        shifted_metro = period_supply_parameters(
            self.config, "metro", "Z9", "Z1", datetime(2026, 7, 6, 7, 30)
        )
        self.assertEqual(shifted_metro["service_profile_period_id"], "morning_core_peak")
        self.assertAlmostEqual(shifted_metro["headway_min"], 3.5 * 1.20)
        shifts = self.config["directional_peak"]["phase_shift_min"]
        self.assertLessEqual(max(abs(value) for row in shifts.values() for value in row.values()), 30)

    def test_latest_departure_is_od_specific(self):
        central = self.option("Z1", "Z2", "metro", datetime(2026, 7, 6, 20, 30))
        remote = self.option("Z9", "Z1", "metro", datetime(2026, 7, 6, 20, 30))
        self.assertLess(remote["latest_feasible_departure"], central["latest_feasible_departure"])

    def test_outputs_are_non_negative_and_reproducible(self):
        cases = [
            ("Z1", "Z2", "walk", datetime(2026, 7, 6, 7, 55)),
            ("Z4", "Z1", "bus", datetime(2026, 7, 6, 8, 20)),
            ("Z5", "Z1", "metro", datetime(2026, 7, 6, 18, 10)),
            ("Z9", "Z1", "ride_hailing", datetime(2026, 7, 6, 21, 10)),
        ]
        for index, (origin, destination, mode, departure) in enumerate(cases):
            first = self.option(origin, destination, mode, departure, f"case-{index}")
            second = self.option(origin, destination, mode, departure, f"case-{index}")
            self.assertEqual(first, second)
            self.assertTrue(all(field in first for field in TIME_SUPPLY_EXTRA_FIELDS))
            if first["available"]:
                for field in (
                    "base_total_time_min", "period_speed_multiplier", "period_wait_time_min",
                    "period_transfer_penalty_min", "time_adjusted_total_time_min",
                ):
                    self.assertGreaterEqual(first[field], 0)
                    self.assertTrue(math.isfinite(first[field]))

    def test_boundary_flags_exclude_weather_choice_and_endogenous_feedback(self):
        boundaries = self.config["boundaries"]
        self.assertTrue(boundaries["normal_weather_only"])
        for key in (
            "agent_preferences_applied", "endogenous_congestion_applied",
            "dynamic_pricing_applied", "dispatch_applied",
            "ride_hailing_dynamic_supply_applied",
        ):
            self.assertFalse(boundaries[key])


if __name__ == "__main__":
    unittest.main()
