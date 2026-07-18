from __future__ import annotations

import copy

from custom.agents.formal_nine_zone_experiment import (
    ENABLED_MODES,
    build_formal_nine_zone_inputs,
    load_formal_nine_zone_config,
    run_formal_nine_zone_baseline,
)
from custom.transport.network import build_transport_network


def _small_config():
    config = load_formal_nine_zone_config()
    config["total_agents"] = 12
    config["ride_hailing_fleet"]["initial_vehicles_by_day_type"] = {
        day_type: {f"Z{index}": 1 for index in range(1, 10)}
        for day_type in ("workday", "rest_day")
    }
    return config


def test_formal_baseline_enables_four_modes_and_uses_all_nine_zones():
    config = load_formal_nine_zone_config()
    assert tuple(config["enabled_modes"]) == ENABLED_MODES
    assert "metro" in config["enabled_modes"]
    inputs = build_formal_nine_zone_inputs(config=config)
    assert {row["home_zone"] for row in inputs["agents"]} == {
        f"Z{index}" for index in range(1, 10)
    }
    assert any(row["origin_zone"] == row["destination_zone"] for row in inputs["legs"])
    assert any(row["origin_zone"] != row["destination_zone"] for row in inputs["legs"])


def test_z9_road_gateway_remains_z6():
    network = build_transport_network()
    assert {neighbor for neighbor, _distance in network["road"]["Z9"]} == {"Z6"}


def test_weather_runs_are_paired_and_non_heat_outcomes_do_not_change_plans():
    result = run_formal_nine_zone_baseline(config=_small_config())
    by_day = {}
    for row in result["summary_rows"]:
        by_day.setdefault(row["day_type"], set()).add(
            (row["planned_activities"], row["planned_legs"], row["agent_count"])
        )
    assert all(len(values) == 1 for values in by_day.values())
    assert {row["weather_scenario"] for row in result["summary_rows"]} == {"W0", "W1", "W2"}


def test_fixed_seed_is_reproducible_and_metro_enters_choice_set():
    config = _small_config()
    left = run_formal_nine_zone_baseline(config=copy.deepcopy(config), seed=91)
    right = run_formal_nine_zone_baseline(config=copy.deepcopy(config), seed=91)
    assert left["summary_rows"] == right["summary_rows"]
    assert left["mode_choices"] == right["mode_choices"]
    assert all(row["primary_mode"] in ENABLED_MODES for row in left["mode_choices"])
    assert all(row["final_mode"] in (*ENABLED_MODES, "") for row in left["mode_choices"])
    assert all(row["metro_enabled"] for row in left["summary_rows"])
    assert any(row["final_mode"] == "metro" for row in left["mode_choices"])


def test_vehicle_conservation_and_mode_share_identity():
    result = run_formal_nine_zone_baseline(config=_small_config(), seed=73)
    for row in result["summary_rows"]:
        assert row["end_idle_vehicles"] + row["end_busy_vehicles"] == row["initial_ride_hailing_vehicles"]
        assert row["walking_legs"] + row["bus_legs"] + row["metro_legs"] + row["ride_hailing_legs"] == row["successful_legs"]
        shares = row["walking_mode_share"] + row["bus_mode_share"] + row["metro_mode_share"] + row["ride_hailing_mode_share"]
        assert abs(shares - 1.0) < 2e-6
        assert row["fallback_successes"] <= row["fallback_attempts"]
        assert row["completed_activities"] + row["transport_unmet_activities"] == row["planned_activities"]
        assert row["weather_cancelled_activities"] == 0


def test_bus_peak_schedule_exceeds_off_peak_without_capacity_change():
    from datetime import datetime
    from custom.agents.formal_nine_zone_experiment import _scheduled_bus_trips_per_bin

    config = load_formal_nine_zone_config()
    network = build_transport_network()
    peak = _scheduled_bus_trips_per_bin(datetime(2026, 7, 7, 8, 0), network, config)
    off_peak = _scheduled_bus_trips_per_bin(datetime(2026, 7, 7, 12, 0), network, config)
    assert peak > off_peak
    assert config["bus_system"]["vehicle_capacity_passengers"] == 50.0


def test_metro_is_cross_zone_continuous_but_intrazonal_only_in_dense_zones():
    from custom.transport.network import calculate_leg_mode_option

    network = build_transport_network()
    assert set(network["config"]["graphs"]["metro"]["intrazonal_service_zones"]) == {
        "Z1", "Z2", "Z3", "Z7",
    }
    from custom.transport.network import calculate_od_option

    cross_zone = calculate_od_option(network, "Z4", "Z1", "metro")
    outer_intrazonal = {
        "leg_id": "z4-z4", "agent_id": 1, "purpose": "shopping",
        "origin_zone": "Z4", "destination_zone": "Z4",
        "road_network_distance_km": 10.0,
    }
    assert cross_zone["available"]
    assert not calculate_leg_mode_option(network, outer_intrazonal, "metro")["available"]
