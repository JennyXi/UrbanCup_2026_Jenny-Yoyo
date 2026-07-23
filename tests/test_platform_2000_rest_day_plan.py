import argparse
import asyncio
import copy
import csv
import json
import tempfile
import unittest
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from custom.agents.agent_population import AgentProfile
from custom.agents.formal_nine_zone_50_experiment import (
    apply_weekend_activity_participation,
    load_formal_50_config,
)
from custom.agents.formal_nine_zone_experiment import load_formal_nine_zone_config
from custom.agents.formal_nine_zone_experiment import build_formal_nine_zone_inputs
from custom.agents.leg_generation import build_time_feasible_legs
from scripts.build_platform_2000_rest_day_plan import scenario_specs
from scripts.run_city_mobility_200_api import (
    DEFAULT_COUPLING_CONFIG,
    DEFAULT_ELDER_ACCESS_CONFIG,
    _apply_dispatch_priority_policy,
    _scenario_policy_label,
    run,
)


class Platform2000RestDayPlanTests(unittest.TestCase):
    @staticmethod
    def _activity(
        agent_id: int, sequence: int, *, mandatory: bool
    ) -> dict:
        start = datetime(2026, 7, 11, 9 + sequence, 0)
        return {
            "agent_id": agent_id,
            "age_group": "18-39",
            "activity_id": f"a-{agent_id}-{sequence}",
            "activity_sequence": sequence,
            "sequence_order": sequence,
            "planned_start_datetime": start,
            "planned_end_datetime": start + timedelta(hours=1),
            "activity_purpose": "medical" if mandatory else "shopping",
            "is_mandatory": mandatory,
        }

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

    def test_weekend_gate_preserves_mandatory_and_is_nested_and_reproducible(self):
        activities = []
        for agent_id in range(1, 101):
            activities.append(self._activity(agent_id, 1, mandatory=False))
        activities.append(self._activity(1, 2, mandatory=True))
        base = {
            "weekend_activity_participation": {
                "enabled": True,
                "optional_agent_day_probability": 0.9,
                "preserve_mandatory_activities": True,
                "ordinary_baseline_cancellation_enabled": False,
            }
        }
        retained_90, audit_90, summary_90 = apply_weekend_activity_participation(
            activities, day_type="rest_day", experiment=base, seed=47
        )
        repeat, repeat_audit, _ = apply_weekend_activity_participation(
            activities, day_type="rest_day", experiment=base, seed=47
        )
        lower = json.loads(json.dumps(base))
        lower["weekend_activity_participation"][
            "optional_agent_day_probability"
        ] = 0.8
        retained_80, _, _ = apply_weekend_activity_participation(
            activities, day_type="rest_day", experiment=lower, seed=47
        )

        optional_90 = {
            row["activity_id"] for row in retained_90 if not row["is_mandatory"]
        }
        optional_80 = {
            row["activity_id"] for row in retained_80 if not row["is_mandatory"]
        }
        self.assertEqual(retained_90, repeat)
        self.assertEqual(audit_90, repeat_audit)
        self.assertTrue(optional_80.issubset(optional_90))
        self.assertIn("a-1-2", {row["activity_id"] for row in retained_90})
        self.assertEqual(summary_90["mandatory_activities_before"], 1)
        self.assertEqual(summary_90["mandatory_activities_after"], 1)
        self.assertFalse(summary_90["ordinary_baseline_cancellation_enabled"])

    def test_weekend_gate_rebuilds_a_continuous_home_activity_chain(self):
        root = Path(__file__).resolve().parents[1]
        experiment = load_formal_50_config(
            root / "config" / "formal_nine_zone_2000_api_360_vehicle_rest_day.json"
        )
        formal = load_formal_nine_zone_config()
        formal["total_agents"] = 50
        inputs = build_formal_nine_zone_inputs(config=formal, seed=47)
        rest_day = date.fromisoformat(formal["selected_days"]["rest_day"])
        selected = [
            row for row in inputs["activities"]
            if row["planned_start_datetime"].date() == rest_day
        ]
        retained, _, summary = apply_weekend_activity_participation(
            selected,
            day_type="rest_day",
            experiment=experiment,
            seed=47,
        )
        profiles = [AgentProfile(**dict(row)) for row in inputs["agents"]]
        rebuilt = build_time_feasible_legs(
            profiles, retained, inputs["spatial_by_id"], seed=47
        )
        profile_by_id = {int(row.agent_id): row for row in profiles}
        legs_by_agent = defaultdict(list)
        for leg in rebuilt["legs"]:
            legs_by_agent[int(leg["agent_id"])].append(leg)

        self.assertTrue(summary["enabled"])
        self.assertEqual(
            summary["mandatory_activities_before"],
            summary["mandatory_activities_after"],
        )
        self.assertEqual(
            len(rebuilt["legs"]),
            len(rebuilt["activities"]) + len(legs_by_agent),
        )
        activity_ids = {row["activity_id"] for row in rebuilt["activities"]}
        nonreturn_ids = {
            row["activity_id"]
            for row in rebuilt["legs"]
            if row["leg_role"] != "return_home"
        }
        self.assertEqual(activity_ids, nonreturn_ids)
        for agent_id, legs in legs_by_agent.items():
            ordered = sorted(legs, key=lambda row: row["departure_time"])
            home = profile_by_id[agent_id].home_zone
            self.assertEqual(ordered[0]["origin_zone"], home)
            self.assertEqual(ordered[-1]["destination_zone"], home)
            for previous, current in zip(ordered, ordered[1:]):
                self.assertEqual(
                    previous["destination_zone"], current["origin_zone"]
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
