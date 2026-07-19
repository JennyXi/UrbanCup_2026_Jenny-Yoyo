from __future__ import annotations

import math
from functools import lru_cache

from custom.agents.formal_nine_zone_50_experiment import (
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)


@lru_cache(maxsize=2)
def _result(seed=47):
    return run_formal_nine_zone_50_experiment(seed=seed)


def test_formal_experiment_uses_50_main_agents_and_all_nine_home_zones():
    result = _result()
    assert len(result["inputs"]["agents"]) == 50
    assert {row["home_zone"] for row in result["inputs"]["agents"]} == {
        f"Z{index}" for index in range(1, 10)
    }
    assert result["formal_config"]["enabled_modes"] == [
        "walk", "bus", "metro", "ride_hailing",
    ]


def test_main_activity_origins_destinations_are_paired_across_weather():
    result = _result()
    grouped = {}
    for row in result["activity_states"]:
        key = (row["agent_id"], row["activity_id"])
        signature = (
            row["activity_purpose"], row["home_zone"], row["destination_zone"],
            row["planned_start_datetime"], row["planned_end_datetime"],
        )
        grouped.setdefault(key, set()).add(signature)
    assert grouped
    assert all(len(signatures) == 1 for signatures in grouped.values())


def test_work_and_medical_never_weather_cancel_and_remote_work_has_no_leg():
    result = _result()
    assert all(
        not row["weather_cancellation"]
        for row in result["activity_results"]
        if row["activity_purpose"] in {"work", "medical"}
    )
    remote_ids = {
        (row["weather_scenario"], row["day_type"], row["activity_id"])
        for row in result["activity_results"] if row["remote_work"]
    }
    assert remote_ids
    choice_ids = {
        (row["weather_scenario"], row["day_type"], row["activity_id"])
        for row in result["mode_choices"]
    }
    assert remote_ids.isdisjoint(choice_ids)
    assert all(row["activity_purpose"] == "work" for row in result["activity_results"] if row["remote_work"])


def test_activity_states_are_mutually_exclusive_and_conserved():
    result = _result()
    for row in result["activity_results"]:
        assert row["final_status"] in {
            "completed", "weather_cancelled", "transport_unmet",
            "reached_but_activity_incomplete",
        }
        reached_incomplete = row["final_status"] == "reached_but_activity_incomplete"
        assert sum((
            row["completed"], row["weather_cancellation"],
            row["transport_unmet"], reached_incomplete,
        )) == 1
    for summary in result["summary_rows"]:
        assert (
            summary["completed_activities"]
            + summary["weather_cancelled_activities"]
            + summary["transport_unmet_activities"]
            + summary["reached_but_activity_incomplete"]
            == summary["planned_activities"]
        )


def test_cancelled_and_remote_activities_generate_no_transport_exposure():
    result = _result()
    blocked = {
        (row["weather_scenario"], row["day_type"], row["activity_id"])
        for row in result["activity_results"]
        if row["weather_cancellation"] or row["remote_work"]
    }
    choices = {
        (row["weather_scenario"], row["day_type"], row["activity_id"])
        for row in result["mode_choices"]
    }
    assert blocked.isdisjoint(choices)


def test_all_time_cost_and_exposure_outputs_are_finite_nonnegative():
    result = _result()
    fields = (
        "failed_attempt_consumed_minutes", "cumulative_wait_minutes",
        "outdoor_exposure_minutes", "heat_hazard_dose_c_min",
        "heat_risk_burden", "rain_exposure_minutes",
    )
    for row in result["mode_choices"]:
        for field in fields:
            assert math.isfinite(float(row[field])) and float(row[field]) >= 0
        if row["transport_succeeded"]:
            assert math.isfinite(float(row["total_travel_time_min"]))
            assert math.isfinite(float(row["fare_yuan"]))
            assert float(row["total_travel_time_min"]) >= 0
            assert float(row["fare_yuan"]) >= 0


def test_fixed_seed_is_reproducible():
    left = _result(53)
    right = run_formal_nine_zone_50_experiment(seed=53)
    assert left["summary_rows"] == right["summary_rows"]
    assert left["activity_results"] == right["activity_results"]
    assert left["mode_choices"] == right["mode_choices"]


def test_config_preserves_single_draw_remote_work_and_p0_scope():
    config = load_formal_50_config()
    assert config["activity_state_machine"]["remote_work_is_activity_level_single_draw"]
    assert not config["activity_state_machine"]["schedule_shift_enabled"]
    assert config["policy"] == "P0_no_policy"
