"""Minimal agent mode-choice model for the Simple_Tests branch.

The module is intentionally independent from the full T7--T10 supply stack.  It
provides a small, auditable baseline that can later be replaced component by
component while keeping the same input/output contract.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "simple_agent_model.json"
MODES = ("walk", "bus", "ride_hailing")
WEATHER_BY_WEEK = {"W0": "normal", "W1": "extreme_heat", "W2": "heavy_rain"}


@dataclass(frozen=True)
class SimpleAgent:
    agent_id: str
    age_group: str
    home_zone: str
    digital_access: bool = True
    value_of_time_yuan_per_hour: float = 30.0
    family_assistance: bool = False


def load_simple_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    zone_ids = [zone["zone_id"] for zone in config["zones"]]
    if len(zone_ids) != len(set(zone_ids)):
        raise ValueError("zone_id must be unique")
    line = config["bus_line"]["zones"]
    if set(line) != set(zone_ids) or len(line) != len(zone_ids):
        raise ValueError("the simple model bus line must serve every zone exactly once")
    if set(config["modes"]) != set(MODES):
        raise ValueError(f"simple model must define exactly {MODES}")
    if set(config["weather"]) != set(WEATHER_BY_WEEK.values()):
        raise ValueError("weather configuration must define W0/W1/W2 weather types")
    service = config.get("zone_service_parameters", {})
    if set(service) != set(zone_ids):
        raise ValueError("zone_service_parameters must cover every zone")
    for zone_id, parameters in service.items():
        coverage = float(parameters["bus_coverage_rate"])
        if not 0 <= coverage <= 1:
            raise ValueError(f"bus coverage for {zone_id} must be in [0, 1]")
    feedback = config.get("ride_hailing_demand_feedback", {})
    if float(feedback.get("additional_wait_min_per_initial_request", -1)) < 0:
        raise ValueError("ride-hailing demand wait slope must be non-negative")
    if float(feedback.get("maximum_additional_wait_min", -1)) < 0:
        raise ValueError("ride-hailing maximum additional wait must be non-negative")
    if feedback.get("iterations") != 1:
        raise ValueError("the simple model must use exactly one demand-feedback iteration")
    cancellation = config.get("activity_cancellation", {})
    if cancellation.get("scenario_level") not in {"low", "base", "high"}:
        raise ValueError("activity cancellation scenario_level must be low, base, or high")
    if len(cancellation.get("w2_windows", [])) != 3:
        raise ValueError("activity cancellation must define exactly three W2 windows")
    return config


def calculate_ride_hailing_feedback_wait(
    initial_request_count: int, *, config: Mapping[str, Any] | None = None
) -> float:
    """Convert first-round requests into one bounded second-round wait increment."""
    if isinstance(initial_request_count, bool) or not isinstance(initial_request_count, int) or initial_request_count < 0:
        raise ValueError("initial_request_count must be a non-negative integer")
    config = config or load_simple_config()
    parameters = config["ride_hailing_demand_feedback"]
    added = initial_request_count * float(parameters["additional_wait_min_per_initial_request"])
    return round(min(added, float(parameters["maximum_additional_wait_min"])), 3)


def _zone_by_id(config: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {zone["zone_id"]: zone for zone in config["zones"]}


def _distance_km(origin: str, destination: str, config: Mapping[str, Any]) -> float:
    zones = _zone_by_id(config)
    try:
        left, right = zones[origin], zones[destination]
    except KeyError as exc:
        raise ValueError(f"unknown zone: {exc.args[0]}") from exc
    if origin == destination:
        return float(left["intrazonal_distance_km"])
    straight = math.hypot(float(left["x_km"]) - float(right["x_km"]), float(left["y_km"]) - float(right["y_km"]))
    return straight * max(float(left["road_factor"]), float(right["road_factor"]))


def build_mode_options(
    origin: str,
    destination: str,
    weather_week: str,
    *,
    config: Mapping[str, Any] | None = None,
    ride_hailing_extra_wait_min: float = 0.0,
) -> Dict[str, Dict[str, Any]]:
    """Build the three alternatives for one OD and one weather scenario."""
    config = config or load_simple_config()
    if ride_hailing_extra_wait_min < 0:
        raise ValueError("ride_hailing_extra_wait_min must be non-negative")
    if weather_week not in WEATHER_BY_WEEK:
        raise ValueError(f"weather_week must be one of {tuple(WEATHER_BY_WEEK)}")
    weather_type = WEATHER_BY_WEEK[weather_week]
    weather = config["weather"][weather_type]
    distance = _distance_km(origin, destination, config)
    service = config["zone_service_parameters"]
    result: Dict[str, Dict[str, Any]] = {}
    for mode in MODES:
        params = config["modes"][mode]
        available = not (mode == "walk" and distance > float(params["maximum_distance_km"]))
        speed = float(params["speed_kmh"]) * float(weather["speed_multiplier"][mode])
        in_vehicle_time = distance / speed * 60.0
        if mode == "bus":
            origin_service = service[origin]
            destination_service = service[destination]
            wait = float(origin_service["bus_wait_min"]) * float(weather["wait_multiplier"][mode])
            access = (
                float(origin_service["bus_access_min"])
                + float(destination_service["bus_access_min"])
            ) / 2.0
            coverage = min(
                float(origin_service["bus_coverage_rate"]),
                float(destination_service["bus_coverage_rate"]),
            )
        else:
            wait = float(params.get("wait_min", 0.0)) * float(weather["wait_multiplier"][mode])
            if mode == "ride_hailing":
                wait += float(ride_hailing_extra_wait_min)
            access = float(params.get("access_min", 0.0))
            coverage = 1.0
        if mode == "ride_hailing":
            metered_fare = (
                distance * float(params["distance_rate_per_km"])
                + in_vehicle_time * float(params["time_rate_per_min"])
                + max(0.0, distance - float(params["long_distance_threshold_km"]))
                * float(params["long_distance_rate_per_km"])
            )
            fare_before_dynamic = max(float(params["minimum_fare"]), metered_fare)
            dynamic_price_multiplier = float(weather["dynamic_price_multiplier"])
            fare = fare_before_dynamic * dynamic_price_multiplier
        else:
            fare = float(params["base_fare"]) + float(params.get("per_km", 0.0)) * distance
            fare_before_dynamic = fare
            dynamic_price_multiplier = 1.0
        result[mode] = {
            "mode": mode,
            "available": available,
            "intrazonal": origin == destination,
            "service_coverage_rate": coverage,
            "distance_km": round(distance, 3),
            "wait_time_min": round(wait, 3) if available else None,
            "in_vehicle_time_min": round(in_vehicle_time, 3) if available else None,
            "travel_time_min": round(in_vehicle_time + wait + access, 3) if available else None,
            "fare_before_dynamic_yuan": round(fare_before_dynamic, 2) if available else None,
            "dynamic_price_multiplier": dynamic_price_multiplier,
            "fare_yuan": round(fare, 2) if available else None,
            "weather_week": weather_week,
            "weather_type": weather_type,
        }
    return result


def _stable_gumbel(seed: int, agent_id: str, trip_id: str, mode: str) -> float:
    payload = f"{seed}|{agent_id}|{trip_id}|{mode}".encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    uniform = (integer + 0.5) / (2**64)
    return -math.log(-math.log(uniform))


def choose_mode(
    agent: SimpleAgent,
    trip: Mapping[str, Any],
    weather_week: str,
    *,
    seed: int = 2026,
    config: Mapping[str, Any] | None = None,
    ride_hailing_extra_wait_min: float = 0.0,
) -> Dict[str, Any]:
    """Choose the available alternative with the highest transparent utility."""
    config = config or load_simple_config()
    required = {"trip_id", "origin_zone", "destination_zone"}
    missing = sorted(required - set(trip))
    if missing:
        raise ValueError(f"trip missing required fields: {missing}")
    if agent.age_group not in {"18-39", "40-59", "60+"}:
        raise ValueError(f"unsupported age_group: {agent.age_group}")
    options = build_mode_options(
        str(trip["origin_zone"]), str(trip["destination_zone"]), weather_week,
        config=config, ride_hailing_extra_wait_min=ride_hailing_extra_wait_min,
    )
    weather_type = WEATHER_BY_WEEK[weather_week]
    weather_penalty = config["weather"][weather_type]["utility_penalty"]
    weights = config["choice_weights"]
    scored = []
    for mode, option in options.items():
        ride_hailing_access = agent.digital_access or agent.family_assistance
        if not option["available"] or (mode == "ride_hailing" and not ride_hailing_access):
            continue
        time_cost = option["travel_time_min"] / 60.0 * agent.value_of_time_yuan_per_hour
        utility = -float(weights["generalized_cost"]) * (time_cost + option["fare_yuan"])
        utility += float(weather_penalty[mode])
        utility += float(config["age_mode_constant"][agent.age_group][mode])
        utility += float(weights["random_scale"]) * _stable_gumbel(seed, agent.agent_id, str(trip["trip_id"]), mode)
        scored.append({**option, "utility": round(utility, 6)})
    if not scored:
        raise ValueError("agent has no available travel mode")
    selected = max(scored, key=lambda row: (row["utility"], row["mode"]))
    return {
        "agent_id": agent.agent_id,
        "trip_id": str(trip["trip_id"]),
        "origin_zone": str(trip["origin_zone"]),
        "destination_zone": str(trip["destination_zone"]),
        "weather_week": weather_week,
        "weather_type": weather_type,
        "chosen_mode": selected["mode"],
        "chosen_time_min": selected["travel_time_min"],
        "chosen_fare_yuan": selected["fare_yuan"],
        "ride_hailing_extra_wait_min": round(float(ride_hailing_extra_wait_min), 3),
        "alternatives": sorted(scored, key=lambda row: MODES.index(row["mode"])),
    }


def simulate_trips(
    agents: Iterable[SimpleAgent],
    trips: Iterable[Mapping[str, Any]],
    weather_week: str,
    *,
    seed: int = 2026,
) -> list[Dict[str, Any]]:
    by_id = {agent.agent_id: agent for agent in agents}
    results = []
    for trip in trips:
        agent_id = str(trip.get("agent_id"))
        if agent_id not in by_id:
            raise ValueError(f"trip refers to unknown agent: {agent_id}")
        results.append(choose_mode(by_id[agent_id], trip, weather_week, seed=seed))
    return results
