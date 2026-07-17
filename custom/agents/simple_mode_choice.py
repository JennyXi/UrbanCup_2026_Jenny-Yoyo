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


def _configured_modes(config: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(config.get("mode_order", tuple(config["modes"])))


def metro_service_at_time(
    departure_time: str | float, *, config: Mapping[str, Any],
) -> Dict[str, float | bool]:
    """Return schedule frequency and implied average wait for one departure."""
    if "metro" not in config["modes"]:
        raise ValueError("metro service requested for a configuration without metro")
    schedule = config["metro_schedule"]
    if isinstance(departure_time, str):
        hour, minute = map(int, departure_time.split(":"))
        minute_of_day = hour * 60 + minute
    else:
        minute_of_day = float(departure_time) % (24 * 60)

    def in_window(start: str, end: str) -> bool:
        start_h, start_m = map(int, start.split(":"))
        end_h, end_m = map(int, end.split(":"))
        left, right = start_h * 60 + start_m, end_h * 60 + end_m
        return left <= minute_of_day < right if left <= right else (
            minute_of_day >= left or minute_of_day < right
        )

    is_peak = any(in_window(start, end) for start, end in schedule["peak_windows"])
    key = "peak_train_trips_per_30_min" if is_peak else "ordinary_train_trips_per_30_min"
    trips = float(schedule[key])
    wait = float(schedule["average_wait_numerator_minutes"]) / trips
    return {
        "is_peak": is_peak,
        "train_trips_per_30_min": round(trips, 6),
        "average_wait_min": round(wait, 6),
    }


def load_simple_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    zone_ids = [zone["zone_id"] for zone in config["zones"]]
    if len(zone_ids) != len(set(zone_ids)):
        raise ValueError("zone_id must be unique")
    line = config["bus_line"]["zones"]
    if set(line) != set(zone_ids) or len(line) != len(zone_ids):
        raise ValueError("the simple model bus line must serve every zone exactly once")
    configured_modes = _configured_modes(config)
    if set(config["modes"]) != set(configured_modes):
        raise ValueError("mode_order must contain every configured mode exactly once")
    if not set(MODES).issubset(configured_modes):
        raise ValueError(f"simple model must include the base modes {MODES}")
    if "metro" in configured_modes:
        metro_line = config.get("metro_line", {}).get("zones", [])
        if set(metro_line) != set(zone_ids) or len(metro_line) != len(zone_ids):
            raise ValueError("the simple metro line must serve every zone exactly once")
        metro_service = config.get("metro_zone_service_parameters", {})
        if set(metro_service) != set(zone_ids):
            raise ValueError("metro_zone_service_parameters must cover every zone")
        schedule = config.get("metro_schedule", {})
        ordinary = float(schedule.get("ordinary_train_trips_per_30_min", 0.0))
        peak = float(schedule.get("peak_train_trips_per_30_min", 0.0))
        if ordinary <= 0 or peak <= ordinary:
            raise ValueError("metro peak train trips must exceed positive ordinary trips")
        if not schedule.get("peak_windows"):
            raise ValueError("metro_schedule must define peak windows")
        if float(schedule.get("average_wait_numerator_minutes", 0.0)) <= 0:
            raise ValueError("metro average-wait numerator must be positive")
    if set(config["weather"]) != set(WEATHER_BY_WEEK.values()):
        raise ValueError("weather configuration must define W0/W1/W2 weather types")
    for weather in config["weather"].values():
        for key in ("speed_multiplier", "wait_multiplier", "utility_penalty"):
            if set(weather[key]) != set(configured_modes):
                raise ValueError(f"weather {key} must cover every configured mode")
    for age_group in ("18-39", "40-59", "60+"):
        if set(config["age_mode_constant"][age_group]) != set(configured_modes):
            raise ValueError("age_mode_constant must cover every configured mode")
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
    """Build the configured alternatives for one OD and one weather scenario."""
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
    for mode in _configured_modes(config):
        params = config["modes"][mode]
        available = not (mode == "walk" and distance > float(params["maximum_distance_km"]))
        if mode == "metro":
            available = (
                origin != destination
                and origin in config["metro_line"]["zones"]
                and destination in config["metro_line"]["zones"]
            )
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
        elif mode == "metro":
            origin_service = config["metro_zone_service_parameters"][origin]
            destination_service = config["metro_zone_service_parameters"][destination]
            wait = float(params["wait_min"]) * float(weather["wait_multiplier"][mode])
            access = (
                float(origin_service["metro_access_min"])
                + float(destination_service["metro_access_min"])
            ) / 2.0
            coverage = min(
                float(origin_service["metro_coverage_rate"]),
                float(destination_service["metro_coverage_rate"]),
            )
            available = available and coverage > 0.0
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


def _stable_uniform(seed: int, agent_id: str, trip_id: str, mode: str, purpose: str) -> float:
    payload = f"{seed}|{agent_id}|{trip_id}|{mode}|{purpose}".encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return (integer + 0.5) / (2**64)


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
    mode_order = _configured_modes(config)
    scored = []
    availability_audit: Dict[str, Dict[str, Any]] = {}
    for mode, option in options.items():
        ride_hailing_access = agent.digital_access or agent.family_assistance
        enforce_coverage = bool(config["modes"][mode].get("enforce_service_coverage", False))
        coverage_key = "-".join(sorted((
            str(trip["origin_zone"]), str(trip["destination_zone"]),
        )))
        coverage_draw = _stable_uniform(
            seed, agent.agent_id, coverage_key, mode, "service-coverage"
        ) if enforce_coverage else None
        coverage_available = (
            not enforce_coverage
            or float(coverage_draw) < float(option["service_coverage_rate"])
        )
        available = bool(option["available"]) and coverage_available
        if mode == "ride_hailing" and not ride_hailing_access:
            available = False
        availability_audit[mode] = {
            "physical_available": bool(option["available"]),
            "service_coverage_rate": float(option["service_coverage_rate"]),
            "coverage_enforced": enforce_coverage,
            "coverage_draw": round(float(coverage_draw), 6) if coverage_draw is not None else None,
            "available_after_coverage": available,
        }
        if not available:
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
        "mode_availability": availability_audit,
        "alternatives": sorted(scored, key=lambda row: mode_order.index(row["mode"])),
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
