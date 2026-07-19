from __future__ import annotations

import unittest

from custom.agents.formal_nine_zone_experiment import (
    _age_transfer_burden_minutes,
    _conditional_fare_sensitivity_multiplier,
    _medical_need_exposure_weight,
    load_formal_nine_zone_config,
)


class FormalNineZoneElderBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.choice = load_formal_nine_zone_config()["mode_choice"]
        self.elder = {"age_group": "60+", "medical_need_level": "high"}

    def test_selected_candidate_parameters_are_in_formal_config(self) -> None:
        self.assertEqual(
            self.choice["age_mode_constant"]["60+"]["ride_hailing"], 0.3
        )
        self.assertEqual(
            self.choice["weather_exposure_disutility"]["age_vulnerability_weight"]["60+"],
            1.6,
        )
        self.assertEqual(
            self.choice["conditional_fare_sensitivity"]["elder_exposed_necessary_multiplier"],
            0.9,
        )
        self.assertEqual(
            self.choice["age_transfer_burden"]["minutes_per_transfer_by_age"]["60+"],
            3.0,
        )

    def test_conditional_fare_sensitivity_has_strict_scope(self) -> None:
        work = {"purpose": "work"}
        shopping = {"purpose": "shopping"}
        adult = {"age_group": "40-59"}
        self.assertEqual(
            _conditional_fare_sensitivity_multiplier(
                self.elder, work, "extreme_heat", self.choice
            ),
            0.9,
        )
        self.assertEqual(
            _conditional_fare_sensitivity_multiplier(
                self.elder, work, "normal", self.choice
            ),
            1.0,
        )
        self.assertEqual(
            _conditional_fare_sensitivity_multiplier(
                self.elder, shopping, "heavy_rain", self.choice
            ),
            1.0,
        )
        self.assertEqual(
            _conditional_fare_sensitivity_multiplier(
                adult, work, "heavy_rain", self.choice
            ),
            1.0,
        )

    def test_transfer_burden_is_perceived_and_age_specific(self) -> None:
        option = {"bus_metro_transfer_count": 2, "final_total_time_min": 40.0}
        self.assertEqual(
            _age_transfer_burden_minutes(self.elder, option, self.choice), 6.0
        )
        self.assertEqual(
            _age_transfer_burden_minutes(
                {"age_group": "18-39"}, option, self.choice
            ),
            0.0,
        )
        self.assertEqual(option["final_total_time_min"], 40.0)

    def test_medical_need_weight_only_amplifies_elder_choice_exposure(self) -> None:
        exposure = self.choice["weather_exposure_disutility"]
        self.assertEqual(_medical_need_exposure_weight(self.elder, exposure), 1.5)
        self.assertEqual(
            _medical_need_exposure_weight(
                {"age_group": "40-59", "medical_need_level": "high"}, exposure
            ),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
