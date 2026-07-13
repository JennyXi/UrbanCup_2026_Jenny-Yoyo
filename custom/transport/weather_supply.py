"""Exogenous weather effects layered on T7 base and T8 time supply."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from custom.envs.weather import CONFIG as T2_WEATHER_CONFIG
from custom.transport.network import MODES
from custom.transport.time_supply import (
    TIME_SUPPLY_OUTPUT_FIELDS,
    _adjusted_components,
    calculate_time_adjusted_leg_mode_option,
    load_time_supply_configuration,
    next_supply_boundary,
    period_supply_parameters,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEATHER_SUPPLY_CONFIG_PATH = ROOT / "config" / "weather_transport_supply.json"
WEATHER_SUPPLY_EXTRA_FIELDS = (
    "weather_type",
    "weather_phase",
    "weather_speed_multiplier",
    "final_speed_multiplier",
    "road_capacity_multiplier",
    "weather_adjusted_vehicle_time_min",
    "weather_adjusted_total_time_min",
    "weather_supply_segments",
)
WEATHER_SUPPLY_OUTPUT_FIELDS = TIME_SUPPLY_OUTPUT_FIELDS + WEATHER_SUPPLY_EXTRA_FIELDS


def load_weather_supply_configuration(
    path: Path | str = DEFAULT_WEATHER_SUPPLY_CONFIG_PATH,
) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    validate_weather_supply_configuration(config)
    return config


def validate_weather_supply_configuration(config: Mapping[str, Any]) -> None:
    weather_types = config.get("weather_types", {})
    if set(weather_types) != {"normal", "extreme_heat", "heavy_rain"}:
        raise ValueError("Weather supply must define normal, extreme_heat and heavy_rain")
    for weather_type, params in weather_types.items():
        for field in ("active_speed_multipliers", "recovery_speed_multipliers"):
            values = params.get(field, {})
            if set(values) != set(MODES) or any(not 0 < float(value) <= 1 for value in values.values()):
                raise ValueError(f"{weather_type}.{field} must cover all modes with (0, 1] values")
        if not 0 < float(params["road_capacity_multiplier"]) <= 1:
            raise ValueError(f"Invalid road capacity for {weather_type}")
        if not 0 < float(params["recovery_road_capacity_multiplier"]) <= 1:
            raise ValueError(f"Invalid recovery road capacity for {weather_type}")
        if float(params["recovery_duration_min"]) < 0:
            raise ValueError(f"Invalid recovery duration for {weather_type}")

    neutral = weather_types["extreme_heat"]
    if any(float(value) != 1.0 for value in neutral["active_speed_multipliers"].values()):
        raise ValueError("Extreme heat speed multipliers must default to 1.00")
    rain = weather_types["heavy_rain"]
    if float(rain["active_speed_multipliers"]["metro"]) != 1.0:
        raise ValueError("Metro must retain normal speed during heavy rain")
    if any(float(rain["active_speed_multipliers"][mode]) >= 1.0 for mode in ("walk", "bus", "ride_hailing")):
        raise ValueError("Heavy rain must slow walk, bus and ride_hailing")
    if float(rain["recovery_duration_min"]) <= 0:
        raise ValueError("Heavy rain must have a non-zero recovery stage")

    boundaries = config.get("boundaries", {})
    forbidden_true = (
        "base_speed_overwritten",
        "period_peak_factors_stacked",
        "road_capacity_used_as_speed_multiplier",
        "vehicle_turnover_applied",
        "dispatch_success_applied",
        "agent_activity_cancellation_applied",
        "mode_choice_applied",
        "endogenous_congestion_applied",
    )
    if any(boundaries.get(name) is not False for name in forbidden_true):
        raise ValueError("Weather layer boundaries must remain explicitly false")
    if config.get("combination_rule") != (
        "final_speed_equals_base_speed_times_one_period_direction_multiplier_times_weather_speed_multiplier"
    ):
        raise ValueError("Unexpected speed combination rule")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _normalise_events(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for event in events:
        weather_type = str(event["weather_type"])
        if weather_type not in {"extreme_heat", "heavy_rain"}:
            raise ValueError(f"Unsupported event weather_type: {weather_type}")
        start = _as_datetime(event["start"])
        end = _as_datetime(event["end"])
        if end <= start:
            raise ValueError("Weather event end must be after start")
        result.append({"weather_type": weather_type, "start": start, "end": end})
    result.sort(key=lambda row: row["start"])
    for previous, current in zip(result, result[1:]):
        if current["start"] < previous["end"]:
            raise ValueError("Weather event active windows must not overlap")
    return result


def weather_events_from_t2_config(
    reference_monday: date | datetime,
    t2_config: Any = T2_WEATHER_CONFIG,
) -> List[Dict[str, Any]]:
    """Convert T2 scenario windows to dated supply events without copying parameters."""
    monday = reference_monday.date() if isinstance(reference_monday, datetime) else reference_monday
    weekday = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }
    if t2_config.current_week == "W0":
        return []
    weather_type = "extreme_heat" if t2_config.current_week == "W1" else "heavy_rain"
    windows = t2_config.w1_windows if t2_config.current_week == "W1" else t2_config.w2_windows
    if t2_config.current_week == "W2" and not windows:
        raise ValueError("T2 W2 windows are not configured")
    events = []
    for window in windows:
        day = monday + timedelta(days=weekday[window.day])
        events.append({
            "weather_type": weather_type,
            "start": datetime.combine(day, time.fromisoformat(window.start_time)),
            "end": datetime.combine(day, time.fromisoformat(window.end_time)),
        })
    return _normalise_events(events)


def weather_supply_parameters(
    moment: datetime,
    mode: str,
    events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the instantaneous weather speed and independent capacity signal."""
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    normalised = _normalise_events(events)
    active = [event for event in normalised if event["start"] <= moment < event["end"]]
    if active:
        weather_type = active[-1]["weather_type"]
        params = config["weather_types"][weather_type]
        return {
            "weather_type": weather_type,
            "weather_phase": "active",
            "weather_speed_multiplier": float(params["active_speed_multipliers"][mode]),
            "road_capacity_multiplier": float(params["road_capacity_multiplier"]),
        }
    recoveries = []
    for event in normalised:
        duration = float(config["weather_types"][event["weather_type"]]["recovery_duration_min"])
        if event["end"] <= moment < event["end"] + timedelta(minutes=duration):
            recoveries.append(event)
    if recoveries:
        event = max(recoveries, key=lambda row: row["end"])
        weather_type = event["weather_type"]
        params = config["weather_types"][weather_type]
        return {
            "weather_type": weather_type,
            "weather_phase": "recovery",
            "weather_speed_multiplier": float(params["recovery_speed_multipliers"][mode]),
            "road_capacity_multiplier": float(params["recovery_road_capacity_multiplier"]),
        }
    params = config["weather_types"]["normal"]
    return {
        "weather_type": "normal",
        "weather_phase": "normal",
        "weather_speed_multiplier": float(params["active_speed_multipliers"][mode]),
        "road_capacity_multiplier": float(params["road_capacity_multiplier"]),
    }


def _next_weather_boundary(
    moment: datetime,
    events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Optional[datetime]:
    candidates: List[datetime] = []
    for event in _normalise_events(events):
        duration = float(config["weather_types"][event["weather_type"]]["recovery_duration_min"])
        candidates.extend((event["start"], event["end"], event["end"] + timedelta(minutes=duration)))
    future = [boundary for boundary in candidates if boundary > moment]
    return min(future) if future else None


def _advance_combined_vehicle_work(
    start: datetime,
    base_vehicle_minutes: float,
    mode: str,
    origin: str,
    destination: str,
    events: Sequence[Mapping[str, Any]],
    weather_config: Mapping[str, Any],
    time_config: Mapping[str, Any],
) -> Tuple[float, List[Dict[str, Any]]]:
    remaining = base_vehicle_minutes
    cursor = start
    segments: List[Dict[str, Any]] = []
    while remaining > 1e-10:
        period = period_supply_parameters(time_config, mode, origin, destination, cursor)
        weather = weather_supply_parameters(cursor, mode, events, weather_config)
        period_multiplier = float(period["speed_multiplier"])
        weather_multiplier = float(weather["weather_speed_multiplier"])
        final_multiplier = period_multiplier * weather_multiplier
        boundary = next_supply_boundary(cursor, time_config, origin, destination)
        weather_boundary = _next_weather_boundary(cursor, events, weather_config)
        if weather_boundary is not None:
            boundary = min(boundary, weather_boundary)
        available = (boundary - cursor).total_seconds() / 60.0
        completed = available * final_multiplier
        elapsed = remaining / final_multiplier if completed + 1e-10 >= remaining else available
        end = cursor + timedelta(minutes=elapsed)
        segments.append({
            "start": cursor.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"),
            "duration_min": round(elapsed, 9),
            "time_period": period["period_id"],
            "weather_type": weather["weather_type"],
            "weather_phase": weather["weather_phase"],
            "period_direction_multiplier": round(period_multiplier, 6),
            "weather_speed_multiplier": round(weather_multiplier, 6),
            "final_speed_multiplier": round(final_multiplier, 6),
            "road_capacity_multiplier": round(float(weather["road_capacity_multiplier"]), 6),
        })
        remaining -= elapsed * final_multiplier
        cursor = end
    return sum(float(row["duration_min"]) for row in segments), segments


def _ordered_unique(values: Iterable[str]) -> str:
    return "+".join(dict.fromkeys(values))


def calculate_weather_adjusted_leg_mode_option(
    network: Mapping[str, Any],
    leg: Mapping[str, Any],
    mode: str,
    events: Sequence[Mapping[str, Any]],
    weather_config: Optional[Mapping[str, Any]] = None,
    time_config: Optional[Mapping[str, Any]] = None,
    seed: Any = 47,
) -> Dict[str, Any]:
    """Recompute from immutable T7/T8 inputs, so repeated calls cannot compound weather."""
    weather_config = weather_config or load_weather_supply_configuration()
    time_config = time_config or load_time_supply_configuration()
    validate_weather_supply_configuration(weather_config)
    normalised_events = _normalise_events(events)
    timed = calculate_time_adjusted_leg_mode_option(network, leg, mode, time_config, seed)
    if not timed["available"]:
        result = {**timed, **{field: None for field in WEATHER_SUPPLY_EXTRA_FIELDS}}
        result["weather_supply_segments"] = []
        return result

    departure = _as_datetime(leg["departure_time"])
    origin = str(leg["origin_zone"])
    destination = str(leg["destination_zone"])
    components = _adjusted_components(
        network, timed, mode, origin, destination, departure, time_config
    )

    if mode == "metro":
        weather_vehicle = float(components["vehicle"])
        segment_weather = weather_supply_parameters(departure, mode, normalised_events, weather_config)
        segments: List[Dict[str, Any]] = []
        weather_multiplier = 1.0
        final_multiplier = float(timed["period_speed_multiplier"])
        weather_type = segment_weather["weather_type"]
        weather_phase = segment_weather["weather_phase"]
        capacity = float(segment_weather["road_capacity_multiplier"])
    else:
        vehicle_start = departure + timedelta(minutes=components["access"] + components["wait"])
        weather_vehicle, segments = _advance_combined_vehicle_work(
            vehicle_start,
            float(timed["in_vehicle_time_min"]),
            mode,
            origin,
            destination,
            normalised_events,
            weather_config,
            time_config,
        )
        weather_multiplier = float(components["vehicle"]) / weather_vehicle
        final_multiplier = float(timed["in_vehicle_time_min"]) / weather_vehicle
        weather_type = _ordered_unique(row["weather_type"] for row in segments)
        weather_phase = _ordered_unique(row["weather_phase"] for row in segments)
        total_segment_minutes = sum(float(row["duration_min"]) for row in segments)
        capacity = sum(
            row["road_capacity_multiplier"] * float(row["duration_min"])
            for row in segments
        ) / total_segment_minutes

    weather_total = float(components["total"]) - float(components["vehicle"]) + weather_vehicle
    result = {
        **timed,
        "weather_type": weather_type,
        "weather_phase": weather_phase,
        "weather_speed_multiplier": round(weather_multiplier, 3),
        "final_speed_multiplier": round(final_multiplier, 3),
        "road_capacity_multiplier": round(capacity, 3),
        "weather_adjusted_vehicle_time_min": round(weather_vehicle, 3),
        "weather_adjusted_total_time_min": round(weather_total, 3),
        "weather_supply_segments": segments,
    }
    if tuple(result) != WEATHER_SUPPLY_OUTPUT_FIELDS:
        raise AssertionError("Weather-supply output fields changed")
    return result
