import copy
import unittest
from datetime import datetime

from custom.agents.emergence_experiment import calculate_heat_hazard_dose, load_emergence_config
from custom.agents.formal_nine_zone_experiment import load_formal_nine_zone_config
from scripts.postprocess_platform_weather_exposure import (
    _rain_overlap_minutes,
    calculate_leg_exposure_rows,
)


class PlatformWeatherExposurePostprocessTests(unittest.TestCase):
    def test_afternoon_heat_dose_exceeds_morning_for_equal_minutes(self):
        config = load_emergence_config()
        morning = calculate_heat_hazard_dose("08:00", 30, "W1", config=config)
        afternoon = calculate_heat_hazard_dose("14:00", 30, "W1", config=config)
        self.assertGreater(afternoon, morning)

    def test_32_degree_threshold_cannot_increase_dose(self):
        low = load_emergence_config()
        high = copy.deepcopy(low)
        high["heat_exposure"]["heat_stress_threshold_c"] = 32.0
        self.assertGreaterEqual(
            calculate_heat_hazard_dose("13:45", 75, "W1", config=low),
            calculate_heat_hazard_dose("13:45", 75, "W1", config=high),
        )

    def test_rain_overlap_counts_only_active_window(self):
        events = [{
            "start": datetime.fromisoformat("2026-07-01 07:00:00"),
            "end": datetime.fromisoformat("2026-07-01 10:00:00"),
        }]
        value = _rain_overlap_minutes(
            datetime.fromisoformat("2026-07-01 06:50:00"), 30, events
        )
        self.assertEqual(value, 20.0)

    def test_heat_integral_wraps_across_midnight(self):
        config = load_emergence_config()
        split = (
            calculate_heat_hazard_dose("23:50", 10, "W1", config=config)
            + calculate_heat_hazard_dose("00:00", 20, "W1", config=config)
        )
        self.assertEqual(
            calculate_heat_hazard_dose("23:50", 30, "W1", config=config), split
        )

    def _row(self, **overrides):
        row = {
            "leg_id": "L1", "agent_id": "1", "activity_id": "A1",
            "purpose": "work", "leg_role": "outbound", "origin_zone": "Z4",
            "destination_zone": "Z2", "departure_time": "2026-07-14 07:00:00",
            "final_attempt_departure_time": "2026-07-14 07:00:00",
            "weather_scenario": "W2", "day_type": "workday", "policy": "P0",
            "primary_mode": "metro", "final_mode": "metro",
            "transport_succeeded": "True", "fallback_attempted": "False",
            "fallback_succeeded": "False", "failed_attempt_consumed_minutes": "0",
            "wait_minutes": "15", "access_time_min": "20", "transfer_time_min": "5",
            "in_vehicle_time_min": "60", "total_travel_time_min": "100",
            "bus_metro_transfer_count": "0",
        }
        row.update(overrides)
        return row

    def _audit(self):
        return [{
            "agent_id": "1", "age_group": "60+", "digital_access": "False",
            "family_assistance": "False", "elder_access_policy": "D0_baseline",
        }]

    def test_metro_platform_wait_is_not_outdoor_exposure(self):
        formal = load_formal_nine_zone_config()
        result = calculate_leg_exposure_rows(
            [self._row()], self._audit(), formal, load_emergence_config()
        )[0]
        self.assertAlmostEqual(result["outdoor_exposure_minutes"], 20.0)

    def test_failed_ride_wait_is_counted_once_before_fallback(self):
        formal = load_formal_nine_zone_config()
        result = calculate_leg_exposure_rows([
            self._row(
                primary_mode="ride_hailing", final_mode="bus",
                fallback_attempted="True", fallback_succeeded="True",
                failed_attempt_consumed_minutes="10",
                final_attempt_departure_time="2026-07-14 07:10:00",
                wait_minutes="5", access_time_min="6", transfer_time_min="0",
                in_vehicle_time_min="40", total_travel_time_min="61",
            )
        ], self._audit(), formal, load_emergence_config())[0]
        self.assertEqual(result["failed_attempt_outdoor_exposure_minutes"], 10.0)
        self.assertAlmostEqual(result["outdoor_exposure_minutes"], 21.0)


if __name__ == "__main__":
    unittest.main()
