from __future__ import annotations

import unittest
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import pstdev

from custom.agents.formal_nine_zone_50_experiment import (
    DEFAULT_CONFIG_PATH,
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)


STRESS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "formal_nine_zone_50_stress_test.json"


@lru_cache(maxsize=2)
def _paired_results():
    baseline = run_formal_nine_zone_50_experiment(
        config=load_formal_50_config(DEFAULT_CONFIG_PATH), seed=47,
        weather_scenarios=("W0", "W2"), day_types=("workday",),
    )
    stress = run_formal_nine_zone_50_experiment(
        config=load_formal_50_config(STRESS_CONFIG_PATH), seed=47,
        weather_scenarios=("W0", "W2"), day_types=("workday",),
    )
    return baseline, stress


class FormalNineZoneStressTest(unittest.TestCase):
    def test_stress_uses_same_inputs_and_half_workday_fleet(self):
        baseline, stress = _paired_results()
        self.assertEqual(baseline["inputs"]["agents"], stress["inputs"]["agents"])
        self.assertEqual(baseline["inputs"]["activities"], stress["inputs"]["activities"])
        self.assertEqual(
            sum(baseline["formal_config"]["ride_hailing_fleet"]["initial_vehicles_by_day_type"]["workday"].values()),
            16,
        )
        self.assertEqual(
            sum(stress["formal_config"]["ride_hailing_fleet"]["initial_vehicles_by_day_type"]["workday"].values()),
            8,
        )

    def test_work_and_medical_departures_are_more_concentrated(self):
        baseline, stress = _paired_results()
        def spread(result):
            rows = [
                row for row in result["mode_choices"]
                if row["weather_scenario"] == "W0" and row["leg_role"] != "return_home"
                and row["purpose"] in {"work", "medical"}
            ]
            minutes = [row["departure_time"].hour * 60 + row["departure_time"].minute for row in rows]
            return pstdev(minutes)
        self.assertLess(spread(stress), spread(baseline))

    def test_vehicle_conservation_and_no_double_service(self):
        _baseline, stress = _paired_results()
        for weather in ("W0", "W2"):
            states = [row for row in stress["vehicle_end_states"] if row["weather_scenario"] == weather]
            self.assertEqual(len(states), 8)
            by_vehicle = defaultdict(list)
            for row in stress["ride_hailing_dispatch"]:
                if row["weather_scenario"] == weather and row["succeeded"]:
                    by_vehicle[row["vehicle_id"]].append(row)
            for rows in by_vehicle.values():
                ordered = sorted(rows, key=lambda row: row["busy_start"])
                for earlier, later in zip(ordered, ordered[1:]):
                    self.assertGreaterEqual(later["busy_start"], earlier["busy_until"])

    def test_fallback_uses_updated_time_and_completion_thresholds(self):
        _baseline, stress = _paired_results()
        fallback = [row for row in stress["mode_choices"] if row["fallback_attempted"]]
        self.assertTrue(fallback)
        for row in fallback:
            elapsed = (row["final_attempt_departure_time"] - row["departure_time"]).total_seconds() / 60.0
            self.assertAlmostEqual(elapsed, row["failed_attempt_consumed_minutes"], places=6)
        reached = [
            row for row in stress["mode_choices"]
            if row["leg_role"] != "return_home" and row["transport_succeeded"]
        ]
        self.assertTrue(any(not row["activity_completed"] for row in reached))
        for row in reached:
            self.assertEqual(
                row["activity_completed"],
                not row["maximum_commute_time_exceeded"]
                and not row["maximum_lateness_exceeded"],
            )

    def test_required_stress_outputs_exist(self):
        _baseline, stress = _paired_results()
        required = {
            "ride_hailing_failed", "fallback_succeeded", "fallback_failed",
            "late_but_reached", "transport_unmet", "mandatory_activity_incomplete",
        }
        for row in stress["summary_rows"]:
            self.assertTrue(required <= row.keys())


if __name__ == "__main__":
    unittest.main()
