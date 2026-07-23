import argparse
import asyncio
import copy
import csv
import tempfile
import unittest
from pathlib import Path

from custom.agents.formal_nine_zone_experiment import load_formal_nine_zone_config
from scripts.build_platform_2000_rest_day_plan import scenario_specs
from scripts.run_city_mobility_200_api import (
    DEFAULT_COUPLING_CONFIG,
    DEFAULT_ELDER_ACCESS_CONFIG,
    _apply_dispatch_priority_policy,
    _scenario_policy_label,
    run,
)


class Platform2000RestDayPlanTests(unittest.TestCase):
    def test_full_weekend_matrix_has_19_unique_scenarios(self):
        rows = scenario_specs()
        self.assertEqual(len(rows), 19)
        self.assertEqual(len({row["scenario"] for row in rows}), 19)
        expected = {
            (weather, policy)
            for weather in ("W0", "W1", "W2")
            for policy in ("C0", "C1", "C2", "C3", "D1", "D3")
        }
        expected.add(("W2", "P4"))
        self.assertEqual(
            {(row["weather"], row["policy"]) for row in rows},
            expected,
        )

    def test_p4_changes_only_existing_dispatch_policy_field(self):
        formal = load_formal_nine_zone_config()
        before = copy.deepcopy(formal)
        _apply_dispatch_priority_policy(formal, "P4_elder_priority")
        self.assertEqual(
            formal["ride_hailing_fleet"]["dispatch_priority_policy"],
            "P4_elder_priority",
        )
        before["ride_hailing_fleet"]["dispatch_priority_policy"] = (
            "P4_elder_priority"
        )
        self.assertEqual(formal, before)

    def test_unknown_dispatch_policy_is_rejected(self):
        formal = load_formal_nine_zone_config()
        with self.assertRaises(ValueError):
            _apply_dispatch_priority_policy(formal, "age_based_mode_choice")

    def test_policy_label_reflects_actual_intervention(self):
        self.assertEqual(
            _scenario_policy_label(
                {"policy": "C2_elder_limited"},
                {"policy": "D0_baseline"},
                "P0_first_come",
            ),
            "C2_elder_limited",
        )
        self.assertEqual(
            _scenario_policy_label(
                {},
                {"policy": "D3_universal_elder_digital_access"},
                "P0_first_come",
            ),
            "D3_universal_elder_digital_access",
        )
        self.assertEqual(
            _scenario_policy_label(
                {},
                {"policy": "D0_baseline"},
                "P4_elder_priority",
            ),
            "P4_elder_priority",
        )

    def test_api_rest_day_dry_run_applies_activity_state_machine_before_choice(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "rest-day"
            args = argparse.Namespace(
                formal_experiment_config=(
                    root / "config" / "formal_nine_zone_50_experiment.json"
                ),
                coupling_config=DEFAULT_COUPLING_CONFIG,
                coupon_result=None,
                elder_access_policy="D0",
                elder_access_config=DEFAULT_ELDER_ACCESS_CONFIG,
                output_dir=output,
                seed=47,
                weather_scenario="W2",
                day_type="rest_day",
                discount_multiplier=0.8,
                dispatch_priority_policy="P0_first_come",
                represented_trips_per_agent=30.0,
                max_decisions=None,
                progress_every=1000,
                concurrency=4,
                dry_run=True,
            )
            summary = asyncio.run(run(args))
            with (output / "activity_states.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                states = list(csv.DictReader(stream))
            with (output / "mode_choices.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                choices = list(csv.DictReader(stream))

        cancelled = {
            row["activity_id"]
            for row in states
            if row["weather_cancellation"] == "True"
        }
        choice_activities = {row["activity_id"] for row in choices}
        self.assertGreater(summary["weather_cancelled_activities"], 0)
        self.assertEqual(summary["policy"], "C0_no_coupon")
        self.assertTrue(cancelled)
        self.assertTrue(all(
            row["weather_decision_departure_time_source"]
            == "mode_informed_prechoice_departure"
            for row in states
        ))
        self.assertTrue(cancelled.isdisjoint(choice_activities))


if __name__ == "__main__":
    unittest.main()
