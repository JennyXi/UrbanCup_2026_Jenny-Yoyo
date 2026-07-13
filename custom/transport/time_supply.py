"""Normal-weather exogenous time-of-day transport supply for concrete legs."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from custom.transport.network import (
    MODES,
    OUTPUT_FIELDS,
    calculate_leg_mode_option,
    calculate_od_option,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIME_SUPPLY_CONFIG_PATH = ROOT / "config" / "time_dependent_transport_supply.json"
TIME_SUPPLY_EXTRA_FIELDS = (
    "time_period",
    "operating",
    "base_total_time_min",
    "period_speed_multiplier",
    "period_wait_time_min",
    "period_transfer_penalty_min",
    "time_adjusted_total_time_min",
    "latest_feasible_departure",
    "supply_level",
)
TIME_SUPPLY_OUTPUT_FIELDS = OUTPUT_FIELDS + TIME_SUPPLY_EXTRA_FIELDS


def load_time_supply_configuration(
    path: Path | str = DEFAULT_TIME_SUPPLY_CONFIG_PATH,
) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    validate_time_supply_configuration(config)
    return config


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid clock time: {value}")
    return hour * 60 + minute


def _clock(value: str) -> time:
    minutes = _clock_minutes(value)
    return time(minutes // 60, minutes % 60)


def validate_time_supply_configuration(config: Mapping[str, Any]) -> None:
    periods = config.get("time_periods", [])
    if len(periods) != 8 or len({row["period_id"] for row in periods}) != 8:
        raise ValueError("Exactly eight unique time periods are required")
    coverage = [0] * (24 * 60)
    for row in periods:
        start = _clock_minutes(row["start"])
        end = _clock_minutes(row["end"])
        cursor = start
        while cursor != end:
            coverage[cursor] += 1
            cursor = (cursor + 1) % (24 * 60)
    if any(count != 1 for count in coverage):
        raise ValueError("Time periods must cover each minute of the day exactly once")

    zone_groups = config.get("zone_groups", {})
    supply_groups = ("strong_supply", "ordinary_outer", "remote_weak")
    if set().union(*(set(zone_groups[name]) for name in supply_groups)) != {
        f"Z{index}" for index in range(1, 10)
    }:
        raise ValueError("Supply zone groups must cover Z1-Z9")
    if sum(len(zone_groups[name]) for name in supply_groups) != 9:
        raise ValueError("Supply zone groups must not overlap")

    period_ids = {row["period_id"] for row in periods}
    for mode in ("bus", "metro", "ride_hailing"):
        if set(config["modes"][mode]["profiles"]) != period_ids:
            raise ValueError(f"{mode} profiles must cover all eight periods")
        if any(
            any(key in profile for key in (
                "speed_multiplier", "road_speed_multiplier", "bus_speed", "ride_speed"
            ))
            for profile in config["modes"][mode]["profiles"].values()
        ):
            raise ValueError("Mode profiles must not duplicate the central speed policy")
    for period_id, profile in config["modes"]["bus"]["profiles"].items():
        if profile["headway_min"] <= 0 or profile["expected_wait_min"] < 0:
            raise ValueError(f"Invalid bus service values for {period_id}")
        if profile["service_frequency_multiplier"] <= 0:
            raise ValueError(f"Invalid bus multipliers for {period_id}")
    for period_id, profile in config["modes"]["metro"]["profiles"].items():
        if any(profile[key] < 0 for key in (
            "headway_min", "expected_wait_min", "crowding_index", "transfer_penalty_min"
        )):
            raise ValueError(f"Invalid metro service values for {period_id}")
    for period_id, profile in config["modes"]["ride_hailing"]["profiles"].items():
        if profile["baseline_wait_min"] < 0:
            raise ValueError(f"Invalid ride-hailing service values for {period_id}")
        if not 0 <= profile["baseline_availability"] <= 1 or profile["pickup_access_time"] < 0:
            raise ValueError(f"Invalid ride-hailing availability for {period_id}")
    fleet_policy = config["modes"]["ride_hailing"].get("fleet_policy", {})
    if (
        fleet_policy.get("normal_day_fleet_size_multiplier") != 1.0
        or fleet_policy.get("time_varying_fleet_size") is not False
        or fleet_policy.get("dispatch_success_generated_here") is not False
        or fleet_policy.get("baseline_availability_role")
        != "descriptive_only_not_used_in_calculation"
    ):
        raise ValueError("Ride-hailing fleet must remain constant and dispatch must stay out of T8")

    boundaries = config.get("boundaries", {})
    expected_false = (
        "agent_preferences_applied", "endogenous_congestion_applied",
        "dynamic_pricing_applied", "dispatch_applied",
        "ride_hailing_dynamic_supply_applied",
    )
    if not boundaries.get("normal_weather_only") or any(boundaries.get(key) for key in expected_false):
        raise ValueError("This layer must remain normal-weather exogenous supply only")
    phase_shifts = config.get("directional_peak", {}).get("phase_shift_min", {})
    if set(phase_shifts) != {"morning_flow", "evening_flow"}:
        raise ValueError("Morning and evening directional phase shifts are required")
    if any(abs(float(value)) > 30 for row in phase_shifts.values() for value in row.values()):
        raise ValueError("Directional phase shifts must remain within 30 minutes")
    if any(
        any(key in impact for key in ("bus_speed", "ride_speed", "speed_multiplier"))
        for impact in config["directional_peak"]["period_impacts"].values()
    ):
        raise ValueError("Directional impacts must not stack extra road speed multipliers")
    speed_policy = config.get("speed_policy", {})
    if (
        speed_policy.get("normal_off_peak_multiplier") != 1.0
        or speed_policy.get("ordinary_peak_multiplier") != 0.85
        or speed_policy.get("strongest_directional_peak_multiplier") != 0.75
        or set(speed_policy.get("road_congestion_modes", ())) != {"bus", "ride_hailing"}
        or set(speed_policy.get("unaffected_modes", ())) != {"walk", "metro"}
        or speed_policy.get("combination_rule")
        != "select_one_final_multiplier_never_multiply_peak_factors"
    ):
        raise ValueError("Speed policy must use final 1.00/0.85/0.75 multipliers without stacking")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def period_for_datetime(moment: datetime, config: Mapping[str, Any]) -> Mapping[str, Any]:
    minute = moment.hour * 60 + moment.minute
    for row in config["time_periods"]:
        start = _clock_minutes(row["start"])
        end = _clock_minutes(row["end"])
        if start < end and start <= minute < end:
            return row
        if start > end and (minute >= start or minute < end):
            return row
    raise AssertionError(f"No time period for {moment}")


def _period_end(moment: datetime, period: Mapping[str, Any]) -> datetime:
    boundary = datetime.combine(moment.date(), _clock(period["end"]))
    if boundary <= moment:
        boundary += timedelta(days=1)
    return boundary


def split_interval_by_period(
    start: datetime, end: datetime, config: Mapping[str, Any]
) -> List[Tuple[str, float]]:
    """Return exact overlap minutes for a non-empty interval."""
    if end <= start:
        raise ValueError("Interval end must be later than start")
    result: List[Tuple[str, float]] = []
    cursor = start
    while cursor < end:
        period = period_for_datetime(cursor, config)
        boundary = min(end, _period_end(cursor, period))
        minutes = (boundary - cursor).total_seconds() / 60.0
        if result and result[-1][0] == period["period_id"]:
            result[-1] = (result[-1][0], result[-1][1] + minutes)
        else:
            result.append((period["period_id"], minutes))
        cursor = boundary
    return result


def _supply_zone_group(zone_id: str, config: Mapping[str, Any]) -> str:
    for name in ("strong_supply", "ordinary_outer", "remote_weak"):
        if zone_id in config["zone_groups"][name]:
            return name
    raise ValueError(f"Zone {zone_id} is missing from supply groups")


def _direction_context(
    origin: str, destination: str, moment: datetime, config: Mapping[str, Any]
) -> Tuple[bool, str, int, Mapping[str, Any]]:
    """Apply a small group-based phase shift only to directional peak load."""
    groups = config["zone_groups"]
    directional = config["directional_peak"]
    for flow_name in ("morning_flow", "evening_flow"):
        flow = directional[flow_name]
        if not (
            origin in groups[flow["origin_group"]]
            and destination in groups[flow["destination_group"]]
        ):
            continue
        anchor_zone = origin if flow_name == "morning_flow" else destination
        anchor_group = _supply_zone_group(anchor_zone, config)
        shift = int(directional["phase_shift_min"][flow_name].get(anchor_group, 0))
        shifted_period = period_for_datetime(moment + timedelta(minutes=shift), config)["period_id"]
        impact = directional["period_impacts"].get(shifted_period, {})
        if impact.get("flow") == flow_name:
            return True, shifted_period, shift, impact
        return False, shifted_period, shift, {}
    period_id = period_for_datetime(moment, config)["period_id"]
    return False, period_id, 0, {}


def period_supply_parameters(
    config: Mapping[str, Any], mode: str, origin: str, destination: str,
    moment: datetime,
) -> Dict[str, Any]:
    """Return deterministic supply parameters at one instant."""
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    period = period_for_datetime(moment, config)
    period_id = period["period_id"]
    group = _supply_zone_group(origin, config)
    directional, directional_period_id, phase_shift_min, impact = _direction_context(
        origin, destination, moment, config
    )
    speed_policy = config["speed_policy"]

    if mode in speed_policy["unaffected_modes"]:
        final_speed_multiplier = float(speed_policy["normal_off_peak_multiplier"])
    elif directional and directional_period_id in {
        "morning_core_peak", "evening_core_peak"
    }:
        final_speed_multiplier = float(
            speed_policy["strongest_directional_peak_multiplier"]
        )
    elif period["intensity"] in {"light_peak", "core_peak"}:
        final_speed_multiplier = float(speed_policy["ordinary_peak_multiplier"])
    else:
        final_speed_multiplier = float(speed_policy["normal_off_peak_multiplier"])

    if mode == "walk":
        return {
            "period_id": period_id, "operating": True,
            "speed_multiplier": final_speed_multiplier,
            "expected_wait_min": 0.0, "transfer_penalty_min": 0.0,
            "directional_peak_applied": False,
            "directional_peak_period_id": period_id,
            "directional_phase_shift_min": 0,
        }

    profile = dict(config["modes"][mode]["profiles"][period_id])
    service_period_id = directional_period_id if directional and mode in {"bus", "metro"} else period_id
    service_profile = config["modes"][mode]["profiles"][service_period_id]
    zone = config["modes"][mode]["zone_multipliers"][group]
    if mode == "bus":
        profile.update({
            "headway_min": service_profile["headway_min"] * zone["headway"],
            "expected_wait_min": service_profile["expected_wait_min"] * zone["wait"] * impact.get("bus_wait", 1.0),
            "speed_multiplier": final_speed_multiplier,
            "service_frequency_multiplier": service_profile["service_frequency_multiplier"] * zone["frequency"],
            "transfer_penalty_min": None,
            "directional_peak_applied": directional,
            "directional_peak_period_id": directional_period_id,
            "directional_phase_shift_min": phase_shift_min,
            "service_profile_period_id": service_period_id,
        })
    elif mode == "metro":
        profile.update({
            "operating": True,
            "speed_multiplier": final_speed_multiplier,
            "headway_min": service_profile["headway_min"] * zone["headway"],
            "expected_wait_min": service_profile["expected_wait_min"] * zone["wait"] * impact.get("metro_wait", 1.0),
            "crowding_index": service_profile["crowding_index"],
            "transfer_penalty_min": service_profile["transfer_penalty_min"] * impact.get("metro_transfer", 1.0),
            "directional_peak_applied": directional,
            "directional_peak_period_id": directional_period_id,
            "directional_phase_shift_min": phase_shift_min,
            "service_profile_period_id": service_period_id,
        })
    else:
        profile.update({
            # T8 has no fleet state or dispatch process. This mode stays available
            # whenever its static T7 option is available; baseline_availability is
            # retained only as descriptive metadata for future calibration.
            "operating": True,
            "expected_wait_min": profile["baseline_wait_min"] * zone["wait"] * impact.get("ride_wait", 1.0),
            "speed_multiplier": final_speed_multiplier,
            "transfer_penalty_min": 0.0,
            "directional_peak_applied": directional,
            "directional_peak_period_id": directional_period_id,
            "directional_phase_shift_min": phase_shift_min,
            "service_profile_period_id": service_period_id,
        })
    return {"period_id": period_id, **profile}


def _advance_vehicle_work(
    start: datetime,
    base_vehicle_minutes: float,
    config: Mapping[str, Any],
    mode: str,
    origin: str,
    destination: str,
) -> Tuple[float, List[Tuple[str, float, datetime]], float]:
    """Consume base vehicle-minutes at period-specific relative speeds."""
    if base_vehicle_minutes <= 0:
        return 0.0, [], 1.0
    remaining = base_vehicle_minutes
    cursor = start
    segments: List[Tuple[str, float, datetime]] = []
    while remaining > 1e-10:
        params = period_supply_parameters(config, mode, origin, destination, cursor)
        speed = float(params["speed_multiplier"])
        boundary = _next_supply_boundary(
            cursor, config, origin, destination
        )
        available_real_minutes = (boundary - cursor).total_seconds() / 60.0
        completed_work = available_real_minutes * speed
        if completed_work + 1e-10 >= remaining:
            real_minutes = remaining / speed
            segments.append((params["period_id"], real_minutes, cursor))
            cursor += timedelta(minutes=real_minutes)
            remaining = 0.0
        else:
            segments.append((params["period_id"], available_real_minutes, cursor))
            remaining -= completed_work
            cursor = boundary
    adjusted = sum(minutes for _, minutes, _ in segments)
    return adjusted, segments, base_vehicle_minutes / adjusted


def _next_supply_boundary(
    moment: datetime,
    config: Mapping[str, Any],
    origin: str,
    destination: str,
) -> datetime:
    """Return the next actual or directionally shifted supply boundary."""
    actual = _period_end(moment, period_for_datetime(moment, config))
    _, _, phase_shift_min, _ = _direction_context(
        origin, destination, moment, config
    )
    if not phase_shift_min:
        return actual
    shifted_moment = moment + timedelta(minutes=phase_shift_min)
    shifted_end = _period_end(
        shifted_moment, period_for_datetime(shifted_moment, config)
    ) - timedelta(minutes=phase_shift_min)
    return min(actual, shifted_end)


def _advance_wait(
    start: datetime,
    config: Mapping[str, Any],
    mode: str,
    origin: str,
    destination: str,
) -> Tuple[float, List[Tuple[str, float, datetime]]]:
    """Complete one unit of expected-wait work across time-period boundaries."""
    remaining_work = 1.0
    cursor = start
    segments: List[Tuple[str, float, datetime]] = []
    while remaining_work > 1e-10:
        params = period_supply_parameters(config, mode, origin, destination, cursor)
        expected_wait = float(params["expected_wait_min"])
        if expected_wait <= 0:
            return (cursor - start).total_seconds() / 60.0, segments
        boundary = _next_supply_boundary(
            cursor, config, origin, destination
        )
        available_minutes = (boundary - cursor).total_seconds() / 60.0
        work_rate = 1.0 / expected_wait
        available_work = available_minutes * work_rate
        if available_work + 1e-10 >= remaining_work:
            elapsed = remaining_work / work_rate
            segments.append((params["period_id"], elapsed, cursor))
            cursor += timedelta(minutes=elapsed)
            remaining_work = 0.0
        else:
            segments.append((params["period_id"], available_minutes, cursor))
            remaining_work -= available_work
            cursor = boundary
    return (cursor - start).total_seconds() / 60.0, segments


def _weighted_metro_transfer_penalty(
    config: Mapping[str, Any], origin: str, destination: str,
    segments: List[Tuple[str, float, datetime]], fallback_moment: datetime,
) -> float:
    if not segments:
        return float(period_supply_parameters(
            config, "metro", origin, destination, fallback_moment
        )["transfer_penalty_min"])
    total = sum(minutes for _, minutes, _ in segments)
    weighted = 0.0
    for _, minutes, sample in segments:
        penalty = period_supply_parameters(
            config, "metro", origin, destination, sample
        )["transfer_penalty_min"]
        weighted += float(penalty) * minutes
    return weighted / total


def _adjusted_components(
    network: Mapping[str, Any], base: Mapping[str, Any], mode: str,
    origin: str, destination: str, departure: datetime,
    config: Mapping[str, Any],
) -> Dict[str, float]:
    if mode == "metro" and (origin == "Z9" or destination == "Z9"):
        return _adjusted_metro_feeder_components(
            network, base, origin, destination, departure, config
        )
    if mode == "walk":
        access = 0.0
        wait = 0.0
        transfer = 0.0
    elif mode == "ride_hailing":
        params = period_supply_parameters(config, mode, origin, destination, departure)
        access = float(params["pickup_access_time"])
        transfer = 0.0
    else:
        access = float(base["access_time_min"])
        transfer = float(base["transfer_time_min"])

    wait_start = departure + timedelta(minutes=access)
    if mode != "walk":
        wait, _ = _advance_wait(
            wait_start, config, mode, origin, destination
        )
    vehicle_start = wait_start + timedelta(minutes=wait)
    adjusted_vehicle, segments, effective_speed = _advance_vehicle_work(
        vehicle_start, float(base["in_vehicle_time_min"]), config,
        mode, origin, destination,
    )
    if mode == "metro":
        line_transfers = int(base["line_transfer_count"])
        mode_transfers = int(base["mode_transfer_count"])
        line_penalty = _weighted_metro_transfer_penalty(
            config, origin, destination, segments, vehicle_start
        )
        mode_penalty = float(network["config"]["modes"]["metro"]["mode_transfer_penalty_min"])
        transfer = line_transfers * line_penalty + mode_transfers * mode_penalty
    total = access + wait + adjusted_vehicle + transfer
    return {
        "access": access,
        "wait": wait,
        "transfer": transfer,
        "vehicle": adjusted_vehicle,
        "speed_multiplier": effective_speed,
        "total": total,
    }


def _adjusted_metro_feeder_components(
    network: Mapping[str, Any], base: Mapping[str, Any], origin: str,
    destination: str, departure: datetime, config: Mapping[str, Any],
) -> Dict[str, float]:
    """Time the Z9 bus feeder explicitly before or after the metro main leg."""
    feeder = network["config"]["graphs"]["metro"]["feeder_access"]["Z9"]
    gateway = feeder["gateway_zone"]
    if origin == "Z9":
        feeder_origin, feeder_destination = "Z9", gateway
    else:
        feeder_origin, feeder_destination = gateway, "Z9"
    feeder_base = calculate_od_option(
        network, feeder_origin, feeder_destination, feeder["access_mode"]
    )
    remaining_metro_access = max(
        0.0, float(base["access_time_min"]) - float(feeder_base["total_time_min"])
    )
    mode_penalty = float(network["config"]["modes"]["metro"]["mode_transfer_penalty_min"])
    line_transfers = int(base["line_transfer_count"])

    if origin == "Z9":
        feeder_adjusted = _adjusted_components(
            network, feeder_base, "bus", feeder_origin, feeder_destination,
            departure, config,
        )
        metro_access_start = departure + timedelta(minutes=feeder_adjusted["total"])
        metro_wait_start = metro_access_start + timedelta(minutes=remaining_metro_access)
        metro_wait, _ = _advance_wait(
            metro_wait_start, config, "metro", origin, destination
        )
        vehicle_start = metro_wait_start + timedelta(minutes=metro_wait + mode_penalty)
        metro_vehicle, metro_segments, _ = _advance_vehicle_work(
            vehicle_start, float(base["in_vehicle_time_min"]), config,
            "metro", origin, destination,
        )
        line_penalty = line_transfers * _weighted_metro_transfer_penalty(
            config, origin, destination, metro_segments, vehicle_start
        )
        total = (
            feeder_adjusted["total"] + remaining_metro_access + metro_wait
            + mode_penalty + metro_vehicle + line_penalty
        )
    else:
        metro_wait_start = departure + timedelta(minutes=remaining_metro_access)
        metro_wait, _ = _advance_wait(
            metro_wait_start, config, "metro", origin, destination
        )
        vehicle_start = metro_wait_start + timedelta(minutes=metro_wait)
        metro_vehicle, metro_segments, _ = _advance_vehicle_work(
            vehicle_start, float(base["in_vehicle_time_min"]), config,
            "metro", origin, destination,
        )
        line_penalty = line_transfers * _weighted_metro_transfer_penalty(
            config, origin, destination, metro_segments, vehicle_start
        )
        feeder_departure = vehicle_start + timedelta(
            minutes=metro_vehicle + line_penalty + mode_penalty
        )
        feeder_adjusted = _adjusted_components(
            network, feeder_base, "bus", feeder_origin, feeder_destination,
            feeder_departure, config,
        )
        total = (
            remaining_metro_access + metro_wait + metro_vehicle + line_penalty
            + mode_penalty + feeder_adjusted["total"]
        )

    base_vehicle = float(base["in_vehicle_time_min"]) + float(feeder_base["in_vehicle_time_min"])
    adjusted_vehicle = metro_vehicle + feeder_adjusted["vehicle"]
    return {
        "access": remaining_metro_access + feeder_adjusted["access"],
        "wait": metro_wait + feeder_adjusted["wait"],
        "transfer": line_penalty + mode_penalty,
        "vehicle": adjusted_vehicle,
        "speed_multiplier": base_vehicle / adjusted_vehicle,
        "total": total,
    }


def _metro_service_window(departure: datetime, config: Mapping[str, Any]) -> Tuple[datetime, datetime]:
    hours = config["modes"]["metro"]["operating_hours"]
    service_day: date = departure.date()
    if departure.time() < _clock(hours["service_day_cutoff"]):
        service_day -= timedelta(days=1)
    start = datetime.combine(service_day, _clock(hours["start"]))
    end = datetime.combine(service_day, _clock(hours["end"]))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _latest_feasible_metro_departure(
    network: Mapping[str, Any], base: Mapping[str, Any], origin: str,
    destination: str, reference: datetime, config: Mapping[str, Any],
) -> Optional[datetime]:
    start, end = _metro_service_window(reference, config)
    first = _adjusted_components(network, base, "metro", origin, destination, start, config)
    if start + timedelta(minutes=first["total"]) > end:
        return None
    low = start
    high = end
    for _ in range(50):
        middle = low + (high - low) / 2
        adjusted = _adjusted_components(
            network, base, "metro", origin, destination, middle, config
        )
        if middle + timedelta(minutes=adjusted["total"]) <= end:
            low = middle
        else:
            high = middle
    return low.replace(microsecond=0)


def _supply_level(
    operating: bool, mode: str, origin: str, period_ids: List[str],
    config: Mapping[str, Any],
) -> str:
    if not operating:
        return "unavailable"
    if mode == "walk":
        return "standard"
    group = _supply_zone_group(origin, config)
    if group == "remote_weak":
        return "weak"
    if "night" in period_ids:
        return "reduced"
    return "strong" if group == "strong_supply" else "standard"


def calculate_time_adjusted_leg_mode_option(
    network: Mapping[str, Any], leg: Mapping[str, Any], mode: str,
    config: Optional[Mapping[str, Any]] = None, seed: Any = 47,
) -> Dict[str, Any]:
    """Append time-of-day supply fields without changing the static base option."""
    config = config or load_time_supply_configuration()
    base = calculate_leg_mode_option(network, leg, mode, seed=seed)
    departure = _as_datetime(leg["departure_time"])
    start_period = period_for_datetime(departure, config)["period_id"]
    if not base["available"]:
        return {
            **base,
            "time_period": start_period,
            "operating": False,
            "base_total_time_min": None,
            "period_speed_multiplier": None,
            "period_wait_time_min": None,
            "period_transfer_penalty_min": None,
            "time_adjusted_total_time_min": None,
            "latest_feasible_departure": None,
            "supply_level": "unavailable",
        }

    origin = str(leg["origin_zone"])
    destination = str(leg["destination_zone"])
    adjusted = _adjusted_components(
        network, base, mode, origin, destination, departure, config
    )
    latest = None
    operating = True
    params = period_supply_parameters(config, mode, origin, destination, departure)
    if mode == "bus":
        operating = bool(params["operating"])
    elif mode == "ride_hailing":
        operating = bool(params["operating"])
    elif mode == "metro":
        latest = _latest_feasible_metro_departure(
            network, base, origin, destination, departure, config
        )
        service_start, _ = _metro_service_window(departure, config)
        operating = latest is not None and service_start <= departure <= latest

    arrival = departure + timedelta(minutes=adjusted["total"])
    overlaps = split_interval_by_period(departure, arrival, config)
    period_ids = [period_id for period_id, _ in overlaps]
    result = {
        **base,
        "time_period": "+".join(period_ids),
        "operating": operating,
        "base_total_time_min": round(float(base["total_time_min"]), 3),
        "period_speed_multiplier": round(adjusted["speed_multiplier"], 3),
        "period_wait_time_min": round(adjusted["wait"], 3),
        "period_transfer_penalty_min": round(adjusted["transfer"], 3),
        "time_adjusted_total_time_min": round(adjusted["total"], 3),
        "latest_feasible_departure": latest,
        "supply_level": _supply_level(operating, mode, origin, period_ids, config),
    }
    if tuple(result) != TIME_SUPPLY_OUTPUT_FIELDS:
        raise AssertionError("Time-supply output fields changed")
    return result
