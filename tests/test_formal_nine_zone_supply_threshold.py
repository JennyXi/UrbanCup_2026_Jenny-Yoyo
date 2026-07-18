from __future__ import annotations

import unittest

from scripts.run_formal_nine_zone_50_supply_threshold import (
    DEFAULT_CONFIG_PATH,
    _load,
    _monotonicity_audit,
)


class FormalNineZoneSupplyThresholdTests(unittest.TestCase):
    def test_configured_vehicle_pools_are_spatially_nested(self):
        config = _load(DEFAULT_CONFIG_PATH)
        pools = {
            int(total): zones for total, zones in config["vehicle_pools_by_total"].items()
        }
        totals = sorted(pools, reverse=True)
        self.assertEqual(totals, [16, 12, 10, 8, 6])
        for total, zones in pools.items():
            self.assertEqual(sum(zones.values()), total)
        for higher, lower in zip(totals, totals[1:]):
            self.assertTrue(all(
                pools[lower][zone] <= pools[higher][zone] for zone in pools[higher]
            ))

    def test_monotonicity_audit_detects_valid_sequence(self):
        config = {"weather_scenarios": ["W0"]}
        rows = [
            {
                "vehicle_total": total, "weather_scenario": "W0",
                "ride_hailing_failed": failed,
                "successful_ride_hailing_requests": success,
                "mean_ride_hailing_wait_minutes_per_request": wait,
            }
            for total, failed, success, wait in (
                (16, 0, 6, 8), (12, 1, 5, 9), (8, 3, 3, 12)
            )
        ]
        self.assertTrue(_monotonicity_audit(rows, config)[0]["all_monotonicity_checks_pass"])


if __name__ == "__main__":
    unittest.main()
