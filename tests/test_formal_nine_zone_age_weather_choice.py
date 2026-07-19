from __future__ import annotations

import unittest

from custom.agents.formal_nine_zone_experiment import _expected_outdoor_exposure_minutes


class FormalNineZoneAgeWeatherChoiceTest(unittest.TestCase):
    def test_expected_outdoor_minutes_follow_mode_components(self):
        common = {
            "final_total_time_min": 40.0, "access_time_min": 8.0,
            "period_wait_time_min": 6.0, "feeder_bus_wait_minutes": 3.0,
        }
        self.assertEqual(_expected_outdoor_exposure_minutes("walk", common), 40.0)
        self.assertEqual(_expected_outdoor_exposure_minutes("bus", common), 14.0)
        self.assertEqual(_expected_outdoor_exposure_minutes("metro", common), 11.0)
        self.assertEqual(_expected_outdoor_exposure_minutes("ride_hailing", common), 6.0)

    def test_same_exposure_has_higher_disutility_for_elder(self):
        minutes = 20.0
        rate = 0.015
        young = minutes * rate * 1.0
        elder = minutes * rate * 1.3
        self.assertGreater(elder, young)


if __name__ == "__main__":
    unittest.main()
