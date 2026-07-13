"""Dynamic road congestion layered after T7, T8 and T9 supply."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from custom.transport.network import MODES
from custom.transport.time_supply import load_time_supply_configuration
from custom.transport.weather_supply import (
    WEATHER_SUPPLY_OUTPUT_FIELDS,
    calculate_weather_adjusted_leg_mode_option,
    load_weather_supply_configuration,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DYNAMIC_CONGESTION_CONFIG_PATH = ROOT / "config" / "dynamic_road_congestion.json"
DYNAMIC_CONGESTION_EXTRA_FIELDS = (
    "road_state_id",
    "capacity_profile_id",
    "normal_road_capacity",
    "weather_capacity",
    "current_road_volume",
    "volume_capacity_ratio",
    "dynamic_congestion_multiplier",
    "weather_free_flow_speed",
    "final_speed",
    "final_in_vehicle_time",
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
        raise ValueError("Dynamic road congestion must apply only to bus and ride_hailing")
    if set(config.get("non_road_modes", ())) != {"walk", "metro"}:
        raise ValueError("Walk and metro must remain outside road capacity calculation")
    if set(config["road_modes"]) | set(config["non_road_modes"]) != set(MODES):
        raise ValueError("Road and non-road mode groups must cover all transport modes")
    profiles = config.get("capacity_profiles", {})
    if not profiles:
        raise ValueError("At least one normal road capacity profile is required")
    for profile_id, profile in profiles.items():
        capacity = float(profile.get("normal_road_capacity", 0))
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError(f"Invalid normal road capacity for {profile_id}")
        if "model_assumption" not in str(profile.get("provenance", "")):
            raise ValueError(f"Capacity profile {profile_id} must be marked as a model assumption")

    function = config.get("congestion_function", {})
    if function.get("type") != "bpr_speed_multiplier":
        raise ValueError("Only the configured BPR speed multiplier is supported")
    alpha = float(function.get("alpha", -1))
    beta = float(function.get("beta", 0))
    if not math.isfinite(alpha) or alpha < 0 or not math.isfinite(beta) or beta <= 0:
        raise ValueError("BPR alpha must be non-negative and beta must be positive")
    if "not_estimated_from_yoyo_database" not in str(function.get("provenance", "")):
        raise ValueError("BPR parameters must not be presented as database estimates")

    volume = config.get("traffic_volume_input", {})
    if (
        volume.get("generated_by_this_layer") is not False
        or volume.get("agent_mode_choice_required") is not False
    ):
        raise ValueError("Traffic volume must remain an external aggregate input")
    boundaries = config.get("boundaries", {})
    required_false = (
        "t7_fields_overwritten",
        "t8_fields_overwritten",
        "t9_fields_overwritten",
        "road_capacity_multiplier_used_directly_as_speed_multiplier",
        "agent_mode_choice_applied",
        "ride_hailing_vehicle_turnover_applied",
        "dispatch_applied",
        "dynamic_waiting_applied",
        "dynamic_pricing_applied",
        "dispatch_failure_applied",
    )
    if any(boundaries.get(name) is not False for name in required_false):
        raise ValueError("Dynamic congestion layer boundaries must remain explicitly false")


def bpr_dynamic_congestion_multiplier(
    volume_capacity_ratio: float,
    config: Mapping[str, Any],
) -> float:
    """Convert v/c to a speed multiplier; zero flow returns exactly one."""
    ratio = float(volume_capacity_ratio)
    if not math.isfinite(ratio) or ratio < 0:
        raise ValueError("volume_capacity_ratio must be a finite non-negative number")
    function = config["congestion_function"]
    alpha = float(function["alpha"])
    beta = float(function["beta"])
    return 1.0 / (1.0 + alpha * ratio ** beta)


def calculate_dynamic_congestion_leg_mode_option(
    network: Mapping[str, Any],
    leg: Mapping[str, Any],
    mode: str,
    events: Sequence[Mapping[str, Any]],
    current_road_volume: Optional[float],
    *,
    road_state_id: str = "aggregate_network",
    capacity_profile_id: str = "aggregate_network",
    congestion_config: Optional[Mapping[str, Any]] = None,
    weather_config: Optional[Mapping[str, Any]] = None,
    time_config: Optional[Mapping[str, Any]] = None,
    seed: Any = 47,
) -> Dict[str, Any]:
    """Apply one external road-state congestion factor to an immutable T9 option."""
    congestion_config = congestion_config or load_dynamic_congestion_configuration()
    weather_config = weather_config or load_weather_supply_configuration()
    time_config = time_config or load_time_supply_configuration()
    validate_dynamic_congestion_configuration(congestion_config)
    if not isinstance(road_state_id, str) or not road_state_id:
        raise ValueError("road_state_id must be a non-empty string")
    if capacity_profile_id not in congestion_config["capacity_profiles"]:
        raise ValueError(f"Unknown capacity profile: {capacity_profile_id}")

    weather = calculate_weather_adjusted_leg_mode_option(
        network,
        leg,
        mode,
        events,
        weather_config,
        time_config,
        seed,
    )
    if not weather["available"]:
        result = {
            **weather,
            **{field: None for field in DYNAMIC_CONGESTION_EXTRA_FIELDS},
        }
        if tuple(result) != DYNAMIC_CONGESTION_OUTPUT_FIELDS:
            raise AssertionError("Dynamic-congestion output fields changed")
        return result

    base_speed = float(network["config"]["modes"][mode]["base_speed_kmh"])
    weather_free_flow_speed = base_speed * float(weather["final_speed_multiplier"])
    weather_vehicle_time = float(weather["weather_adjusted_vehicle_time_min"])

    if mode in congestion_config["non_road_modes"]:
        normal_capacity = None
        weather_capacity = None
        volume = None
        ratio = None
        congestion_multiplier = 1.0
    else:
        if current_road_volume is None:
            raise ValueError("Road modes require current_road_volume")
        volume = float(current_road_volume)
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("current_road_volume must be a finite non-negative number")
        normal_capacity = float(
            congestion_config["capacity_profiles"][capacity_profile_id]["normal_road_capacity"]
        )
        # Capacity affects v/c only. It is deliberately absent from the speed product.
        weather_capacity = normal_capacity * float(weather["road_capacity_multiplier"])
        ratio = volume / weather_capacity
        congestion_multiplier = bpr_dynamic_congestion_multiplier(
            ratio, congestion_config
        )

    final_speed = weather_free_flow_speed * congestion_multiplier
    final_vehicle_time = weather_vehicle_time / congestion_multiplier
    result = {
        **weather,
        "road_state_id": road_state_id if mode in congestion_config["road_modes"] else None,
        "capacity_profile_id": capacity_profile_id if mode in congestion_config["road_modes"] else None,
        "normal_road_capacity": None if normal_capacity is None else round(normal_capacity, 3),
        "weather_capacity": None if weather_capacity is None else round(weather_capacity, 3),
        "current_road_volume": None if volume is None else round(volume, 3),
        "volume_capacity_ratio": None if ratio is None else round(ratio, 6),
        "dynamic_congestion_multiplier": round(congestion_multiplier, 6),
        "weather_free_flow_speed": round(weather_free_flow_speed, 6),
        "final_speed": round(final_speed, 6),
        "final_in_vehicle_time": round(final_vehicle_time, 6),
    }
    if tuple(result) != DYNAMIC_CONGESTION_OUTPUT_FIELDS:
        raise AssertionError("Dynamic-congestion output fields changed")
    return result
