"""Marginal road congestion relative to the fixed T8 background load."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from custom.transport.network import MODES
from custom.transport.time_supply import (
    load_time_supply_configuration,
    next_supply_boundary,
    period_supply_parameters,
)
from custom.transport.weather_supply import (
    WEATHER_SUPPLY_OUTPUT_FIELDS,
    calculate_weather_adjusted_leg_mode_option,
    load_weather_supply_configuration,
    next_weather_boundary,
    weather_supply_parameters,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DYNAMIC_CONGESTION_CONFIG_PATH = ROOT / "config" / "dynamic_road_congestion.json"
EXCESS_FLOW_SOURCES = {
    "agent_mode_choice_scenario_delta",
    "policy_scenario_delta",
    "combined_scenario_delta",
}
DYNAMIC_CONGESTION_EXTRA_FIELDS = (
    "corridor_id",
    "direction",
    "time_bin_at_vehicle_start",
    "shared_state_key_at_vehicle_start",
    "capacity_profile_id",
    "excess_flow_source",
    "corridor_capacity_pcu_per_hour_per_direction",
    "weather_capacity_at_vehicle_start",
    "excess_road_flow_pcu_per_hour",
    "baseline_vc",
    "baseline_vc_weather",
    "scenario_vc",
    "extra_multiplier",
    "unclipped_final_speed_kmh",
    "motor_vehicle_speed_floor_applied",
    "motor_vehicle_oversaturated",
    "final_speed_kmh",
    "final_in_vehicle_time_min",
    "final_total_time_min",
    "dynamic_congestion_segments",
)
DYNAMIC_CONGESTION_OUTPUT_FIELDS = (
    WEATHER_SUPPLY_OUTPUT_FIELDS + DYNAMIC_CONGESTION_EXTRA_FIELDS
)


def load_dynamic_congestion_configuration(
    path: Path | str = DEFAULT_DYNAMIC_CONGESTION_CONFIG_PATH,
) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    validate_dynamic_congestion_configuration(config)
    return config


def validate_dynamic_congestion_configuration(config: Mapping[str, Any]) -> None:
    if set(config.get("road_modes", ())) != {"bus", "ride_hailing"}:
        raise ValueError("T10 must apply only to bus and ride_hailing")
    if set(config.get("non_road_modes", ())) != {"walk", "metro"}:
        raise ValueError("Walk and metro must remain outside road congestion")
    if set(config["road_modes"]) | set(config["non_road_modes"]) != set(MODES):
        raise ValueError("Road and non-road mode groups must cover all modes")
    if config.get("flow_unit") != "PCU/hour/direction":
        raise ValueError("T10 flow unit must be PCU/hour/direction")

    profiles = config.get("capacity_profiles", {})
    if not profiles:
        raise ValueError("At least one directional corridor capacity profile is required")
    for profile_id, profile in profiles.items():
        capacity = float(profile.get("corridor_capacity_pcu_per_hour_per_direction", 0))
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError(f"Invalid corridor capacity for {profile_id}")
        if profile.get("spatial_semantics") != "representative_directional_corridor":
            raise ValueError(f"Capacity profile {profile_id} must use corridor semantics")
        if "model_assumption" not in str(profile.get("provenance", "")):
            raise ValueError(f"Capacity profile {profile_id} must be a model assumption")

    baseline = config.get("baseline_vc", {})
    expected_periods = {
        "morning_shoulder", "morning_core_peak", "morning_recovery",
        "day_off_peak", "evening_shoulder", "evening_core_peak",
        "evening_recovery", "night",
    }
    period_base = baseline.get("period_base", {})
    additions = baseline.get("main_commute_direction_addition", {})
    if set(period_base) != expected_periods or set(additions) != expected_periods:
        raise ValueError("baseline_vc must cover all T8 periods")
    if "model_assumption" not in str(baseline.get("provenance", "")):
        raise ValueError("baseline_vc must be marked as a model assumption")
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in period_base.values()):
        raise ValueError("baseline_vc period values must be finite and non-negative")
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in additions.values()):
        raise ValueError("direction additions must be finite and non-negative")
    if not (
        float(period_base["morning_core_peak"]) > float(period_base["day_off_peak"])
        and float(period_base["evening_core_peak"]) > float(period_base["day_off_peak"])
        and float(additions["morning_core_peak"]) > 0
        and float(additions["evening_core_peak"]) > 0
    ):
        raise ValueError("Core peaks and main commute directions must carry higher background load")

    function = config.get("congestion_function", {})
    if function.get("type") != "marginal_bpr_speed_ratio":
        raise ValueError("Only the marginal BPR speed ratio is supported")
    alpha = float(function.get("alpha", -1))
    beta = float(function.get("beta", 0))
    if not math.isfinite(alpha) or alpha < 0 or not math.isfinite(beta) or beta <= 0:
        raise ValueError("BPR alpha must be non-negative and beta must be positive")
    if "not_estimated_from_yoyo_database" not in str(function.get("provenance", "")):
        raise ValueError("BPR parameters must not be presented as database estimates")

    limits = config.get("safety_limits", {})
    maximum_vc = float(limits.get("maximum_vc", 0))
    minimum_speed = float(limits.get("minimum_final_speed_kmh", 0))
    maximum_segment = float(limits.get("maximum_segment_time_min", 0))
    if any(not math.isfinite(value) or value <= 0 for value in (
        maximum_vc, minimum_speed, maximum_segment
    )):
        raise ValueError("T10 safety limits must be finite and positive")
    if set(limits.get("speed_floor_scope", ())) != {"bus", "ride_hailing"}:
        raise ValueError("The speed floor must apply only to road motor modes")
    if limits.get("speed_floor_semantics") != (
        "severe_oversaturation_model_floor_not_normal_operating_speed"
    ):
        raise ValueError("The motor speed floor must be identified as an oversaturation safeguard")
    if limits.get("maximum_segment_time_semantics") != (
        "single_dynamic_segment_or_iteration_guard_not_whole_trip_cap"
    ):
        raise ValueError("The segment-time limit must not cap a whole trip")
    if max(float(value) + float(additions[key]) for key, value in period_base.items()) >= maximum_vc:
        raise ValueError("maximum_vc must exceed every configured baseline_vc")

    flow = config.get("traffic_flow_input", {})
    if (
        flow.get("field") != "excess_road_flow_pcu_per_hour"
        or flow.get("unit") != "PCU/hour/direction"
        or flow.get("semantics") != "scenario_flow_minus_t8_background_flow"
        or flow.get("must_exclude_baseline_flow") is not True
        or flow.get("negative_scenario_delta_policy")
        != "reject_negative_input_use_max_zero_upstream"
        or flow.get("includes_weather_response_from_agent_mode_choice") is not True
        or flow.get("manual_weather_demand_addition_allowed") is not False
        or flow.get("generated_by_this_layer") is not False
        or set(flow.get("allowed_sources", ())) != EXCESS_FLOW_SOURCES
    ):
        raise ValueError("T10 must consume one PCU/hour/direction scenario delta only")

    shared = config.get("shared_state_key", {})
    if (
        tuple(shared.get("fields", ())) != ("corridor_id", "direction", "time_bin")
        or shared.get("bus_and_ride_hailing_must_share_state") is not True
        or shared.get("requires_preaggregated_all_motorized_excess_pcu") is not True
        or shared.get("separate_mode_flow_inputs_allowed") is not False
        or shared.get("global_state_registry_implemented") is not False
        or shared.get("global_state_registry_owner") != "future_model_runner"
    ):
        raise ValueError("Shared road state key must be corridor_id + direction + time_bin")
    if any(config.get("boundaries", {}).values()):
        raise ValueError("All T10 out-of-scope boundary flags must remain false")


def bpr_dynamic_congestion_multiplier(
    volume_capacity_ratio: float,
    config: Mapping[str, Any],
) -> float:
    """Return overflow-safe BPR speed multiplier g(v/c)."""
    ratio = float(volume_capacity_ratio)
    if not math.isfinite(ratio) or ratio < 0:
        raise ValueError("volume_capacity_ratio must be finite and non-negative")
    alpha = float(config["congestion_function"]["alpha"])
    beta = float(config["congestion_function"]["beta"])
    if ratio == 0 or alpha == 0:
        return 1.0
    log_penalty = math.log(alpha) + beta * math.log(ratio)
    return 0.0 if log_penalty > 709.0 else 1.0 / (1.0 + math.exp(log_penalty))


def marginal_extra_multiplier(
    baseline_vc_weather: float,
    scenario_vc: float,
    config: Mapping[str, Any],
) -> float:
    """Return g(scenario)/g(background), capped to [0, 1]."""
    background = float(baseline_vc_weather)
    scenario = float(scenario_vc)
    if not all(math.isfinite(value) and value >= 0 for value in (background, scenario)):
        raise ValueError("baseline and scenario v/c must be finite and non-negative")
    if scenario < background:
        raise ValueError("scenario_vc cannot be below baseline_vc_weather")
    if scenario == background:
        return 1.0
    background_speed = bpr_dynamic_congestion_multiplier(background, config)
    scenario_speed = bpr_dynamic_congestion_multiplier(scenario, config)
    if background_speed <= 0:
        return 1.0
    return min(1.0, max(0.0, scenario_speed / background_speed))


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _background_state(
    period: Mapping[str, Any],
    weather: Mapping[str, Any],
    excess_flow: float,
    corridor_capacity: float,
    config: Mapping[str, Any],
) -> Dict[str, float | str | bool]:
    time_bin = str(
        period["directional_peak_period_id"]
        if period["directional_peak_applied"]
        else period["period_id"]
    )
    baseline_config = config["baseline_vc"]
    baseline_vc = float(baseline_config["period_base"][time_bin])
    if period["directional_peak_applied"]:
        baseline_vc += float(
            baseline_config["main_commute_direction_addition"][time_bin]
        )
    capacity_multiplier = float(weather["road_capacity_multiplier"])
    weather_capacity = corridor_capacity * capacity_multiplier
    baseline_weather = baseline_vc / capacity_multiplier
    scenario_uncapped = baseline_weather + excess_flow / weather_capacity
    maximum_vc = float(config["safety_limits"]["maximum_vc"])
    scenario_vc = min(maximum_vc, scenario_uncapped)
    extra = marginal_extra_multiplier(baseline_weather, scenario_vc, config)
    return {
        "time_bin": time_bin,
        "baseline_vc": baseline_vc,
        "baseline_vc_weather": baseline_weather,
        "weather_capacity": weather_capacity,
        "scenario_vc": scenario_vc,
        "scenario_vc_capped": scenario_uncapped > maximum_vc,
        "extra_multiplier": extra,
    }


def _advance_marginal_vehicle_work(
    start: datetime,
    base_vehicle_minutes: float,
    base_speed_kmh: float,
    mode: str,
    origin: str,
    destination: str,
    corridor_id: str,
    direction: str,
    events: Sequence[Mapping[str, Any]],
    excess_flow: float,
    corridor_capacity: float,
    congestion_config: Mapping[str, Any],
    weather_config: Mapping[str, Any],
    time_config: Mapping[str, Any],
    *,
    apply_speed_floor: bool,
) -> Tuple[float, List[Dict[str, Any]]]:
    remaining = base_vehicle_minutes
    cursor = start
    segments: List[Dict[str, Any]] = []
    limits = congestion_config["safety_limits"]
    minimum_speed = float(limits["minimum_final_speed_kmh"])
    maximum_segment = float(limits["maximum_segment_time_min"])
    while remaining > 1e-10:
        period = period_supply_parameters(time_config, mode, origin, destination, cursor)
        weather = weather_supply_parameters(cursor, mode, events, weather_config)
        state = _background_state(
            period, weather, excess_flow, corridor_capacity, congestion_config
        )
        t9_multiplier = (
            float(period["speed_multiplier"])
            * float(weather["weather_speed_multiplier"])
        )
        t9_speed = base_speed_kmh * t9_multiplier
        raw_final_speed = t9_speed * float(state["extra_multiplier"])
        speed_floor_applied = bool(apply_speed_floor and raw_final_speed < minimum_speed)
        final_speed = max(minimum_speed, raw_final_speed) if speed_floor_applied else raw_final_speed
        applied_extra = min(1.0, final_speed / t9_speed)
        combined_multiplier = final_speed / base_speed_kmh

        boundary = next_supply_boundary(cursor, time_config, origin, destination)
        weather_boundary = next_weather_boundary(cursor, events, weather_config)
        if weather_boundary is not None:
            boundary = min(boundary, weather_boundary)
        boundary = min(boundary, cursor + timedelta(minutes=maximum_segment))
        available = (boundary - cursor).total_seconds() / 60.0
        completed = available * combined_multiplier
        elapsed = remaining / combined_multiplier if completed + 1e-10 >= remaining else available
        end = cursor + timedelta(minutes=elapsed)
        state_key = f"{corridor_id}|{direction}|{state['time_bin']}"
        segments.append({
            "state_key": state_key,
            "corridor_id": corridor_id,
            "direction": direction,
            "time_bin": state["time_bin"],
            "start": cursor.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"),
            "duration_min": round(elapsed, 9),
            "weather_type": weather["weather_type"],
            "weather_phase": weather["weather_phase"],
            "weather_capacity_multiplier": round(float(weather["road_capacity_multiplier"]), 6),
            "weather_capacity": round(float(state["weather_capacity"]), 6),
            "excess_road_flow_pcu_per_hour": round(excess_flow, 6),
            "baseline_vc": round(float(state["baseline_vc"]), 6),
            "baseline_vc_weather": round(float(state["baseline_vc_weather"]), 6),
            "scenario_vc": round(float(state["scenario_vc"]), 6),
            "scenario_vc_capped": bool(state["scenario_vc_capped"]),
            "extra_multiplier": round(applied_extra, 6),
            "t9_speed_kmh": round(t9_speed, 6),
            "unclipped_final_speed_kmh": round(raw_final_speed, 6),
            "motor_vehicle_speed_floor_applied": speed_floor_applied,
            "motor_vehicle_oversaturated": speed_floor_applied,
            "final_speed_kmh": round(final_speed, 6),
        })
        remaining -= elapsed * combined_multiplier
        cursor = end
    return (cursor - start).total_seconds() / 60.0, segments


def calculate_dynamic_congestion_leg_mode_option(
    network: Mapping[str, Any],
    leg: Mapping[str, Any],
    mode: str,
    events: Sequence[Mapping[str, Any]],
    excess_road_flow_pcu_per_hour: Optional[float],
    *,
    corridor_id: str,
    direction: str,
    shared_state_flow_is_aggregated: bool,
    excess_flow_source: str = "agent_mode_choice_scenario_delta",
    capacity_profile_id: str = "representative_directional_corridor",
    congestion_config: Optional[Mapping[str, Any]] = None,
    weather_config: Optional[Mapping[str, Any]] = None,
    time_config: Optional[Mapping[str, Any]] = None,
    seed: Any = 47,
) -> Dict[str, Any]:
    """Apply marginal T10 congestion without reapplying the T8 background flow."""
    congestion_config = congestion_config or load_dynamic_congestion_configuration()
    weather_config = weather_config or load_weather_supply_configuration()
    time_config = time_config or load_time_supply_configuration()
    validate_dynamic_congestion_configuration(congestion_config)
    if not isinstance(corridor_id, str) or not corridor_id:
        raise ValueError("corridor_id must be a non-empty string")
    if not isinstance(direction, str) or not direction:
        raise ValueError("direction must be a non-empty string")
    if "|" in corridor_id or "|" in direction:
        raise ValueError("corridor_id and direction must not contain '|'")
    if capacity_profile_id not in congestion_config["capacity_profiles"]:
        raise ValueError(f"Unknown capacity profile: {capacity_profile_id}")
    if excess_flow_source not in EXCESS_FLOW_SOURCES:
        raise ValueError(f"Unknown excess_flow_source: {excess_flow_source}")
    if mode in congestion_config["road_modes"]:
        if shared_state_flow_is_aggregated is not True:
            raise ValueError(
                "Road modes require one preaggregated excess PCU flow for all motor modes "
                "sharing corridor_id + direction + time_bin"
            )
        if excess_road_flow_pcu_per_hour is None:
            raise ValueError("Road modes require excess_road_flow_pcu_per_hour")
        validated_excess_flow = float(excess_road_flow_pcu_per_hour)
        if not math.isfinite(validated_excess_flow) or validated_excess_flow < 0:
            raise ValueError("excess_road_flow_pcu_per_hour must be finite and non-negative")

    weather_option = calculate_weather_adjusted_leg_mode_option(
        network, leg, mode, events, weather_config, time_config, seed
    )
    if not weather_option["available"]:
        result = {
            **weather_option,
            **{field: None for field in DYNAMIC_CONGESTION_EXTRA_FIELDS},
        }
        result["dynamic_congestion_segments"] = []
        if tuple(result) != DYNAMIC_CONGESTION_OUTPUT_FIELDS:
            raise AssertionError("Dynamic-congestion output fields changed")
        return result

    base_speed = float(network["config"]["modes"][mode]["base_speed_kmh"])
    base_vehicle_minutes = float(weather_option["in_vehicle_time_min"])
    weather_vehicle_time = float(weather_option["weather_adjusted_vehicle_time_min"])
    weather_total_time = float(weather_option["weather_adjusted_total_time_min"])

    if mode in congestion_config["non_road_modes"]:
        corridor_capacity = None
        weather_capacity = None
        excess_flow = None
        start_state = None
        effective_extra = 1.0
        final_vehicle_time = weather_vehicle_time
        final_speed = base_speed * base_vehicle_minutes / final_vehicle_time
        unclipped_final_speed = None
        speed_floor_applied = None
        oversaturated = None
        segments: List[Dict[str, Any]] = []
    else:
        excess_flow = validated_excess_flow
        corridor_capacity = float(
            congestion_config["capacity_profiles"][capacity_profile_id]
            ["corridor_capacity_pcu_per_hour_per_direction"]
        )
        departure = _as_datetime(leg["departure_time"])
        origin = str(leg["origin_zone"])
        destination = str(leg["destination_zone"])
        vehicle_start = departure + timedelta(
            minutes=float(weather_option["access_time_min"])
            + float(weather_option["period_wait_time_min"])
        )
        start_period = period_supply_parameters(
            time_config, mode, origin, destination, vehicle_start
        )
        start_weather = weather_supply_parameters(
            vehicle_start, mode, events, weather_config
        )
        start_state = _background_state(
            start_period, start_weather, excess_flow,
            corridor_capacity, congestion_config,
        )
        weather_capacity = float(start_state["weather_capacity"])
        calculated_vehicle_time, segments = _advance_marginal_vehicle_work(
            vehicle_start,
            base_vehicle_minutes,
            base_speed,
            mode,
            origin,
            destination,
            corridor_id,
            direction,
            events,
            excess_flow,
            corridor_capacity,
            congestion_config,
            weather_config,
            time_config,
            apply_speed_floor=True,
        )
        if excess_flow == 0:
            final_vehicle_time = weather_vehicle_time
            effective_extra = 1.0
            final_speed = base_speed * base_vehicle_minutes / final_vehicle_time
            unclipped_final_speed = final_speed
            speed_floor_applied = False
            oversaturated = False
        else:
            final_vehicle_time = calculated_vehicle_time
            effective_extra = min(1.0, weather_vehicle_time / final_vehicle_time)
            final_speed = base_speed * base_vehicle_minutes / final_vehicle_time
            unclipped_vehicle_time, _ = _advance_marginal_vehicle_work(
                vehicle_start,
                base_vehicle_minutes,
                base_speed,
                mode,
                origin,
                destination,
                corridor_id,
                direction,
                events,
                excess_flow,
                corridor_capacity,
                congestion_config,
                weather_config,
                time_config,
                apply_speed_floor=False,
            )
            unclipped_final_speed = (
                base_speed * base_vehicle_minutes / unclipped_vehicle_time
            )
            speed_floor_applied = any(
                row["motor_vehicle_speed_floor_applied"] for row in segments
            )
            oversaturated = speed_floor_applied

    final_total_time = (
        weather_total_time - weather_vehicle_time + final_vehicle_time
    )
    time_bin = None if start_state is None else str(start_state["time_bin"])
    result = {
        **weather_option,
        "corridor_id": corridor_id if mode in congestion_config["road_modes"] else None,
        "direction": direction if mode in congestion_config["road_modes"] else None,
        "time_bin_at_vehicle_start": time_bin,
        "shared_state_key_at_vehicle_start": (
            f"{corridor_id}|{direction}|{time_bin}" if time_bin is not None else None
        ),
        "capacity_profile_id": capacity_profile_id if mode in congestion_config["road_modes"] else None,
        "excess_flow_source": excess_flow_source if mode in congestion_config["road_modes"] else None,
        "corridor_capacity_pcu_per_hour_per_direction": (
            None if corridor_capacity is None else round(corridor_capacity, 3)
        ),
        "weather_capacity_at_vehicle_start": (
            None if weather_capacity is None else round(weather_capacity, 3)
        ),
        "excess_road_flow_pcu_per_hour": (
            None if excess_flow is None else round(excess_flow, 3)
        ),
        "baseline_vc": None if start_state is None else round(float(start_state["baseline_vc"]), 6),
        "baseline_vc_weather": None if start_state is None else round(float(start_state["baseline_vc_weather"]), 6),
        "scenario_vc": None if start_state is None else round(float(start_state["scenario_vc"]), 6),
        "extra_multiplier": round(effective_extra, 6),
        "unclipped_final_speed_kmh": (
            None if unclipped_final_speed is None else round(unclipped_final_speed, 6)
        ),
        "motor_vehicle_speed_floor_applied": speed_floor_applied,
        "motor_vehicle_oversaturated": oversaturated,
        "final_speed_kmh": round(final_speed, 6),
        "final_in_vehicle_time_min": round(final_vehicle_time, 6),
        "final_total_time_min": round(final_total_time, 6),
        "dynamic_congestion_segments": segments,
    }
    if tuple(result) != DYNAMIC_CONGESTION_OUTPUT_FIELDS:
        raise AssertionError("Dynamic-congestion output fields changed")
    return result
