"""Independent two-day experiment for observing transport-system emergence."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.agents.simple_experiment import AGE_VALUE_OF_TIME, assign_two_zone_homes
from custom.agents.simple_mode_choice import MODES, SimpleAgent, build_mode_options, choose_mode, load_simple_config
from custom.agents.symmetric_weather_experiment import (
    EMPLOYED_STATUSES, WEATHER_TYPES, load_symmetric_experiment_config,
    remote_work_decision, weather_cancellation_decision,
)
from custom.agents.trip_planning import (
    DAILY_ACTIVITY_COUNT_OPTIONS, MEDICAL_WEEKLY_COUNT_OPTIONS,
    NON_WORK_DURATION_OPTIONS, OPTIONAL_PURPOSE_PROBABILITIES,
)


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "emergence_experiment.json"
DAY_TYPES = ("workday", "rest_day")


def load_emergence_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if tuple(config["day_types"]) != DAY_TYPES:
        raise ValueError(f"day_types must be {DAY_TYPES}")
    if tuple(config["weather_weeks"]) != tuple(WEATHER_TYPES):
        raise ValueError("emergence experiment must include W0/W1/W2")
    if int(config["time_bin_minutes"]) != 30:
        raise ValueError("the first emergence experiment uses 30-minute bins")
    if int(config["feedback_iterations"]) != 1:
        raise ValueError("the emergence experiment must use exactly one feedback iteration")
    for key in ("per_vehicle_capacity_representative_passengers", "maximum_extra_wait_min"):
        if float(config["bus_feedback"][key]) < 0:
            raise ValueError("bus feedback values must be non-negative")
    for value in config["ride_hailing_feedback"]["available_vehicles_per_30_min"].values():
        if float(value) <= 0:
            raise ValueError("ride-hailing supply must be positive")
    schedule = config["bus_vehicle_schedule"]
    if float(schedule["ordinary_vehicle_trips_per_30_min"]) <= 0:
        raise ValueError("ordinary bus vehicle trips must be positive")
    if float(schedule["peak_vehicle_trips_per_30_min"]) <= float(schedule["ordinary_vehicle_trips_per_30_min"]):
        raise ValueError("peak bus vehicle trips must exceed ordinary trips")
    if schedule["frequency_policy_changes_vehicle_trips"] is not True:
        raise ValueError("bus frequency policy must change scheduled vehicle trips")
    threshold_experiment = config["ride_supply_threshold_experiment"]
    grid = [float(value) for value in threshold_experiment["ride_supply_multipliers"]]
    if grid != sorted(set(grid)) or any(value <= 0 for value in grid) or 1.0 not in grid:
        raise ValueError("ride supply threshold grid must be sorted, unique, positive and include 1.0")
    if float(threshold_experiment["fixed_bus_frequency_multiplier"]) != 1.0:
        raise ValueError("ride supply threshold experiment must hold bus frequency at P0")
    if float(threshold_experiment["fixed_reference_road_vehicles_per_30_min"]) <= 0:
        raise ValueError("ride supply threshold road reference must be positive")
    digital_experiment = config["elder_digital_access_experiment"]
    if tuple(digital_experiment["policies"]) != (
        "D0_baseline", "D1_targeted_digital_training_75pct",
        "D2_family_assistance_90pct", "D3_universal_elder_digital_access",
    ):
        raise ValueError("unexpected elder digital-access policy order")
    for policy in digital_experiment["policies"].values():
        for key in ("elder_digital_access_target", "elder_family_assistance_target"):
            value = policy[key]
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{key} must be null or in [0, 1]")
    if float(digital_experiment["fixed_bus_frequency_multiplier"]) != 1.0:
        raise ValueError("digital-access experiment must hold bus frequency at P0")
    if float(digital_experiment["fixed_ride_supply_multiplier"]) != 1.0:
        raise ValueError("digital-access experiment must hold ride supply at P0")
    heat = config["heat_exposure"]
    if heat["method"] != "utci_degree_minutes_above_threshold":
        raise ValueError("unsupported heat exposure method")
    if int(heat["time_bin_minutes"]) != 30:
        raise ValueError("heat exposure must use 30-minute bins")
    if not set(heat["enabled_weather_weeks"]).issubset(WEATHER_TYPES):
        raise ValueError("heat exposure contains an unknown weather week")
    if float(heat["heat_stress_threshold_c"]) < 0:
        raise ValueError("heat stress threshold must be non-negative")
    thresholds = [float(value) for value in heat["heat_stress_threshold_sensitivity_c"]]
    if thresholds != [26.0, 32.0] or float(heat["heat_stress_threshold_c"]) != 26.0:
        raise ValueError("main/sensitivity heat thresholds must be 26/32 C")
    expected_profile = {_clock(minute) for minute in range(0, 24 * 60, 30)}
    if set(heat["utci_c_by_30_min"]) != expected_profile:
        raise ValueError("UTCI profile must cover all 48 half-hour bins")
    for clock, value in heat["utci_c_by_30_min"].items():
        if _minutes(clock) % 30:
            raise ValueError("UTCI profile keys must be on the 30-minute grid")
        if not math.isfinite(float(value)):
            raise ValueError("UTCI profile values must be finite")
    for mode in MODES:
        factor = float(heat["outdoor_segment_factor"][mode])
        if not 0.0 <= factor <= 1.0:
            raise ValueError("outdoor segment factors must be in [0, 1]")
    if set(heat["age_vulnerability_weight"]) != {"18-39", "40-59", "60+"}:
        raise ValueError("heat vulnerability weights must cover all age groups")
    if any(float(value) <= 0 or not math.isfinite(float(value)) for value in heat["age_vulnerability_weight"].values()):
        raise ValueError("heat vulnerability weights must be finite and positive")
    return config


def _stable_seed(seed: int, *parts: Any) -> int:
    payload = "|".join(map(str, (seed, *parts))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _uniform(seed: int, *parts: Any) -> float:
    return _stable_seed(seed, *parts) / 2**64


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock(value: int) -> str:
    value = max(0, min(int(value), 23 * 60 + 59))
    return f"{value // 60:02d}:{value % 60:02d}"


def heat_vulnerability_weight(
    age_group: str, *, config: Mapping[str, Any] | None = None,
) -> float:
    """Return a scenario weight, not a clinical probability or risk ratio."""
    emergence = config or load_emergence_config()
    try:
        return float(emergence["heat_exposure"]["age_vulnerability_weight"][age_group])
    except KeyError as exc:
        raise ValueError(f"unsupported age group for heat vulnerability: {age_group}") from exc


def calculate_heat_hazard_dose(
    start_time: str | float, outdoor_minutes: float, weather_week: str, *,
    segment_factor: float = 1.0, config: Mapping[str, Any] | None = None,
) -> float:
    """Integrate UTCI degree-minutes above the configured threshold."""
    emergence = config or load_emergence_config()
    heat = emergence["heat_exposure"]
    duration = float(outdoor_minutes)
    if duration < 0 or not math.isfinite(duration):
        raise ValueError("outdoor_minutes must be finite and non-negative")
    factor = float(segment_factor)
    if not 0.0 <= factor <= 1.0:
        raise ValueError("segment_factor must be in [0, 1]")
    if duration == 0 or weather_week not in heat["enabled_weather_weeks"]:
        return 0.0
    cursor = float(_minutes(start_time) if isinstance(start_time, str) else start_time)
    if not math.isfinite(cursor):
        raise ValueError("start_time must be finite")
    bin_minutes = int(heat["time_bin_minutes"])
    threshold = float(heat["heat_stress_threshold_c"])
    default_utci = float(heat["default_utci_c"])
    profile = heat["utci_c_by_30_min"]
    remaining = duration
    dose = 0.0
    while remaining > 1e-12:
        minute_of_day = cursor % (24 * 60)
        bin_start = int(minute_of_day // bin_minutes) * bin_minutes
        within_bin = minute_of_day - bin_start
        span = min(remaining, bin_minutes - within_bin)
        key = f"{bin_start // 60:02d}:{bin_start % 60:02d}"
        utci = float(profile.get(key, default_utci))
        dose += span * factor * max(utci - threshold, 0.0)
        cursor += span
        remaining -= span
    return round(dose, 6)


def _weighted_choice(rng: random.Random, options: Iterable[tuple[Any, float]]) -> Any:
    values, weights = zip(*tuple(options))
    return rng.choices(values, weights=weights, k=1)[0]


def _mode_config(symmetric: Mapping[str, Any]) -> Dict[str, Any]:
    config = copy.deepcopy(load_simple_config())
    p = symmetric["mode_preferences"]
    config["weather"]["extreme_heat"]["utility_penalty"] = {
        "walk": p["heat_walking_preference"], "bus": p["heat_bus_preference"],
        "ride_hailing": p["heat_ride_hailing_preference"],
    }
    config["weather"]["heavy_rain"]["utility_penalty"] = {
        "walk": p["rain_walking_preference"], "bus": p["rain_bus_preference"],
        "ride_hailing": p["rain_ride_hailing_preference"],
    }
    return config


def _w1_behavior_active(clock: str, emergence: Mapping[str, Any]) -> bool:
    start, end = map(_minutes, emergence["extreme_heat_behavior_window"])
    minute = _minutes(clock)
    return start <= minute < end


def _gate_w1_preference(
    transport: Dict[str, Any], leg: Mapping[str, Any], weather_week: str,
    emergence: Mapping[str, Any],
) -> None:
    """Remove only W1 behavioral preference shifts outside 11:00-18:00."""
    if weather_week == "W1" and not _w1_behavior_active(str(leg["departure_time"]), emergence):
        transport["weather"]["extreme_heat"]["utility_penalty"] = {
            mode: 0.0 for mode in MODES
        }


def _agent(profile: AgentProfile) -> SimpleAgent:
    return SimpleAgent(
        agent_id=str(profile.agent_id), age_group=profile.age_group,
        home_zone=str(profile.home_zone), digital_access=bool(profile.digital_access),
        value_of_time_yuan_per_hour=AGE_VALUE_OF_TIME[profile.age_group],
        family_assistance=bool(profile.family_assistance),
    )


def _sample_work_schedule(profile: AgentProfile, seed: int, symmetric: Mapping[str, Any], transport: Mapping[str, Any]) -> Dict[str, str]:
    schedule = symmetric["work_schedule"]
    prefix = "part_time" if profile.work_status == "part_time_worker" else "regular"
    rng = random.Random(_stable_seed(seed, profile.agent_id, "company-schedule"))
    start = rng.choice(schedule[f"{prefix}_worker_start_times"])
    duration = int(rng.choice(schedule[f"{prefix}_worker_duration_min"]))
    end = max(_minutes(start) + duration, _minutes(schedule["minimum_end_time"]))
    end = min(end, _minutes(schedule["maximum_end_time"]))
    options = build_mode_options(str(profile.home_zone), "S1", "W0", config=transport)
    reference = schedule["baseline_departure_reference_mode"]
    departure = _minutes(start) - math.ceil(float(options[reference]["travel_time_min"]))
    return {"departure": _clock(departure), "start": start, "end": _clock(end)}


def _elder_medical_probability(profile: AgentProfile) -> float:
    options = MEDICAL_WEEKLY_COUNT_OPTIONS[str(profile.medical_need_level)]
    return sum(options) / len(options) / 5.0


def build_emergence_activities(
    profiles: Iterable[AgentProfile], *, seed: int,
    config: Mapping[str, Any] | None = None,
    symmetric: Mapping[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Generate age-specific workday/rest-day demand once, before weather."""
    emergence = config or load_emergence_config()
    symmetric = symmetric or load_symmetric_experiment_config()
    transport = load_simple_config()
    activities: list[Dict[str, Any]] = []
    for profile in sorted(profiles, key=lambda row: row.agent_id):
        work_schedule = _sample_work_schedule(profile, seed, symmetric, transport) if profile.work_status in EMPLOYED_STATUSES else None
        for day_type in DAY_TYPES:
            rng = random.Random(_stable_seed(seed, profile.agent_id, day_type, "activity-plan"))
            daily_options = DAILY_ACTIVITY_COUNT_OPTIONS["weekday" if day_type == "workday" else "weekend"][profile.age_group]
            desired_total = int(_weighted_choice(rng, daily_options))
            planned: list[tuple[str, str, str, str]] = []
            if day_type == "workday" and work_schedule is not None:
                planned.append(("work", work_schedule["departure"], work_schedule["end"], work_schedule["start"]))
            elif day_type == "workday" and profile.is_elder and rng.random() < _elder_medical_probability(profile):
                departure = rng.choice(("09:00", "09:30", "10:00", "10:30", "11:00"))
                duration = int(_weighted_choice(rng, NON_WORK_DURATION_OPTIONS["medical"]))
                planned.append(("medical", departure, _clock(_minutes(departure) + duration), ""))
            desired_total = max(desired_total, len(planned))
            optional_count = desired_total - len(planned)
            optional_probabilities = OPTIONAL_PURPOSE_PROBABILITIES[profile.age_group]
            latest_end = max((_minutes(row[2]) for row in planned), default=None)
            for index in range(optional_count):
                purpose = str(_weighted_choice(rng, optional_probabilities))
                duration = int(_weighted_choice(rng, NON_WORK_DURATION_OPTIONS[purpose]))
                if day_type == "rest_day":
                    slots = emergence["optional_departure_slots"]["rest_day"]
                    feasible = [str(slot) for slot in slots if latest_end is None or _minutes(str(slot)) >= latest_end + 30]
                    departure_minutes = _minutes(rng.choice(feasible)) if feasible else (latest_end + 30 if latest_end is not None else _minutes(str(slots[0])))
                elif work_schedule is not None:
                    offsets = emergence["optional_departure_slots"]["workday_after_work_offsets_min"]
                    departure_minutes = max(
                        _minutes(work_schedule["end"]) + int(rng.choice(offsets)),
                        latest_end + 30 if latest_end is not None else 0,
                    )
                else:
                    slots = emergence["optional_departure_slots"]["workday_nonworker"]
                    feasible = [str(slot) for slot in slots if latest_end is None or _minutes(str(slot)) >= latest_end + 30]
                    departure_minutes = _minutes(rng.choice(feasible)) if feasible else (latest_end + 30 if latest_end is not None else _minutes(str(slots[0])))
                if departure_minutes > 21 * 60 + 30 or departure_minutes + duration > 23 * 60 + 30:
                    continue
                departure = _clock(departure_minutes)
                return_time = _clock(departure_minutes + duration)
                planned.append((purpose, departure, return_time, ""))
                latest_end = departure_minutes + duration

            for sequence, (purpose, departure, return_time, work_start) in enumerate(planned, start=1):
                necessary = purpose in {"work", "medical"}
                if purpose in {"work", "medical"}:
                    destination = "S1"
                else:
                    p_s1 = float(emergence["optional_destination_s1_probability"][purpose])
                    destination = "S1" if _uniform(seed, profile.agent_id, day_type, sequence, purpose, "destination") < p_s1 else "S2"
                origin = str(profile.home_zone)
                distance = build_mode_options(origin, destination, "W0", config=transport)["bus"]["distance_km"]
                constraints = emergence["activity_constraints"]
                prefix = "necessary" if necessary else "optional"
                activities.append({
                    "agent_id": profile.agent_id,
                    "activity_id": f"E{profile.agent_id:03d}-{day_type.upper()}-{sequence:02d}-{purpose.upper()}",
                    "day_type": day_type, "activity_purpose": purpose,
                    "departure_time": departure, "return_time": return_time,
                    "work_start_time": work_start,
                    "origin_zone": origin, "destination_zone": destination,
                    "distance_km": distance, "necessary_activity": necessary,
                    "max_leg_time_min": float(constraints[f"{prefix}_max_leg_time_min"]),
                    "max_leg_budget_yuan": float(constraints[f"{prefix}_max_leg_budget_yuan"]),
                })
    return sorted(activities, key=lambda row: row["activity_id"])


def _time_bin(clock: str, bin_minutes: int) -> str:
    start = (_minutes(clock) // bin_minutes) * bin_minutes
    return f"{_clock(start)}-{_clock(start + bin_minutes)}"


def _leg(activity: Mapping[str, Any], role: str, bin_minutes: int) -> Dict[str, Any]:
    reverse = role == "return"
    departure = str(activity["return_time"] if reverse else activity["departure_time"])
    origin = str(activity["destination_zone"] if reverse else activity["origin_zone"])
    destination = str(activity["origin_zone"] if reverse else activity["destination_zone"])
    return {
        "leg_id": f'{activity["activity_id"]}-{role}', "activity_id": activity["activity_id"],
        "agent_id": activity["agent_id"], "day_type": activity["day_type"],
        "activity_purpose": activity["activity_purpose"], "leg_role": role,
        "departure_time": departure, "time_bin": _time_bin(departure, bin_minutes),
        "origin_zone": origin, "destination_zone": destination,
        "direction": f"{origin}->{destination}",
        "max_leg_time_min": activity["max_leg_time_min"],
        "max_leg_budget_yuan": activity["max_leg_budget_yuan"],
    }


def _trip(leg: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "trip_id": leg["leg_id"], "agent_id": str(leg["agent_id"]),
        "origin_zone": leg["origin_zone"], "destination_zone": leg["destination_zone"],
    }


def _initial_choices(
    legs: Iterable[Mapping[str, Any]], profiles: Mapping[int, AgentProfile], weather_week: str,
    *, seed: int, transport: Mapping[str, Any], emergence: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    choices = {}
    for leg in sorted(legs, key=lambda row: row["leg_id"]):
        local_transport = copy.deepcopy(transport)
        _gate_w1_preference(local_transport, leg, weather_week, emergence)
        choices[leg["leg_id"]] = choose_mode(
            _agent(profiles[leg["agent_id"]]), _trip(leg), weather_week,
            seed=seed, config=local_transport,
        )
    return choices


def _scheduled_bus_vehicle_trips(
    time_bin: str, emergence: Mapping[str, Any], *, bus_frequency_multiplier: float = 1.0,
) -> float:
    """Return timetable vehicle trips; P1 changes frequency, not per-vehicle capacity."""
    schedule = emergence["bus_vehicle_schedule"]
    minute = _minutes(time_bin.split("-", 1)[0])
    peak = any(
        _minutes(start) <= minute < _minutes(end)
        for start, end in schedule["peak_windows"]
    )
    key = "peak_vehicle_trips_per_30_min" if peak else "ordinary_vehicle_trips_per_30_min"
    return float(schedule[key]) * float(bus_frequency_multiplier)


def _build_system_state(
    legs: Iterable[Mapping[str, Any]], choices: Mapping[str, Mapping[str, Any]],
    emergence: Mapping[str, Any], *, bus_frequency_multiplier: float,
    ride_supply_multiplier: float, mode_field: str = "chosen_mode",
    ride_request_field: str | None = None, state_stage: str = "pre_feedback",
) -> tuple[Dict[tuple[str, str, str], Dict[str, float]], Dict[tuple[str, str, str], Dict[str, float]], Dict[tuple[str, str], Dict[str, float]], list[Dict[str, Any]]]:
    bus_counts: Counter = Counter()
    ride_counts: Counter = Counter()
    road_counts: Counter = Counter()
    active_road_bins = {
        (day_type, _time_bin(_clock(minute), int(emergence["time_bin_minutes"])))
        for day_type in DAY_TYPES for minute in range(0, 24 * 60, int(emergence["time_bin_minutes"]))
    }
    for leg in legs:
        choice = choices.get(leg["leg_id"])
        if choice is None:
            continue
        mode = choice.get(mode_field, "")
        if mode == "bus":
            bus_counts[(leg["day_type"], leg["time_bin"], leg["direction"])] += 1
        ride_requests = (
            int(choice.get(ride_request_field, 0))
            if ride_request_field is not None else int(mode == "ride_hailing")
        )
        if ride_requests:
            ride_counts[(leg["day_type"], leg["time_bin"], leg["origin_zone"])] += ride_requests
        if mode == "ride_hailing":
            road_counts[(leg["day_type"], leg["time_bin"])] += 1
    bus_state: Dict[tuple[str, str, str], Dict[str, float]] = {}
    ride_state: Dict[tuple[str, str, str], Dict[str, float]] = {}
    road_state: Dict[tuple[str, str], Dict[str, float]] = {}
    rows: list[Dict[str, Any]] = []
    bus = emergence["bus_feedback"]
    ride = emergence["ride_hailing_feedback"]
    road = emergence["road_feedback"]
    for key, demand in sorted(bus_counts.items()):
        day_type, time_bin, direction = key
        scheduled_total = _scheduled_bus_vehicle_trips(
            time_bin, emergence, bus_frequency_multiplier=bus_frequency_multiplier
        )
        scheduled_direction = scheduled_total / 2.0
        capacity = float(bus["per_vehicle_capacity_representative_passengers"]) * scheduled_direction
        load = demand / capacity if capacity else math.inf
        excess = max(0.0, load - float(bus["crowding_threshold_ratio"]))
        extra_wait = min(excess * float(bus["extra_wait_min_per_load_above_threshold"]), float(bus["maximum_extra_wait_min"]))
        success_factor = min(1.0, capacity / demand) if demand else 1.0
        bus_state[key] = {
            "demand": demand, "supply": capacity,
            "scheduled_bus_vehicle_trips_direction": scheduled_direction,
            "load_ratio": load, "extra_wait_min": extra_wait, "success_factor": success_factor,
        }
        rows.append({"state_stage": state_stage, "state_type": "bus", "day_type": day_type, "time_bin": time_bin, "spatial_key": direction, **bus_state[key]})
    for key, demand in sorted(ride_counts.items()):
        day_type, time_bin, origin = key
        day_multiplier = float(ride["rest_day_supply_multiplier"]) if day_type == "rest_day" else 1.0
        supply = float(ride["available_vehicles_per_30_min"][origin]) * day_multiplier * ride_supply_multiplier
        ratio = demand / supply
        extra_wait = min(ratio * float(ride["extra_wait_min_per_demand_supply_ratio"]), float(ride["maximum_system_extra_wait_min"]))
        success_factor = min(1.0, supply / demand) if demand else 1.0
        ride_state[key] = {"demand": demand, "supply": supply, "load_ratio": ratio, "extra_wait_min": extra_wait, "success_factor": success_factor}
        rows.append({"state_stage": state_stage, "state_type": "ride_hailing", "day_type": day_type, "time_bin": time_bin, "spatial_key": origin, **ride_state[key]})
    for key in sorted(active_road_bins):
        ride_vehicle_trips = float(road_counts[key])
        scheduled_bus_trips = _scheduled_bus_vehicle_trips(
            key[1], emergence, bus_frequency_multiplier=bus_frequency_multiplier
        )
        demand = scheduled_bus_trips + ride_vehicle_trips
        ratio = demand / float(road["reference_road_vehicles_per_30_min"])
        speed = max(float(road["minimum_speed_multiplier"]), 1.0 / (1.0 + float(road["congestion_strength"]) * ratio))
        road_state[key] = {
            "demand": demand,
            "scheduled_bus_vehicle_trips": scheduled_bus_trips,
            "successful_ride_hailing_vehicle_trips": ride_vehicle_trips,
            "road_vehicle_volume": demand,
            "supply": float(road["reference_road_vehicles_per_30_min"]),
            "load_ratio": ratio, "extra_wait_min": 0.0, "success_factor": speed,
        }
        rows.append({"state_stage": state_stage, "state_type": "road", "day_type": key[0], "time_bin": key[1], "spatial_key": "all", **road_state[key]})
    return bus_state, ride_state, road_state, rows


def _local_choice(
    leg: Mapping[str, Any], profile: AgentProfile, weather_week: str, *, seed: int,
    base_transport: Mapping[str, Any], emergence: Mapping[str, Any],
    bus_state: Mapping[tuple[str, str, str], Mapping[str, float]],
    ride_state: Mapping[tuple[str, str, str], Mapping[str, float]],
    road_state: Mapping[tuple[str, str], Mapping[str, float]],
) -> Dict[str, Any]:
    config = copy.deepcopy(base_transport)
    weather_type = WEATHER_TYPES[weather_week]
    _gate_w1_preference(config, leg, weather_week, emergence)
    road = road_state.get((leg["day_type"], leg["time_bin"]), {})
    scheduled = float(road.get(
        "scheduled_bus_vehicle_trips",
        _scheduled_bus_vehicle_trips(leg["time_bin"], emergence),
    ))
    ordinary = float(emergence["bus_vehicle_schedule"]["ordinary_vehicle_trips_per_30_min"])
    frequency_ratio = scheduled / ordinary
    config["zone_service_parameters"][leg["origin_zone"]]["bus_wait_min"] /= frequency_ratio
    bus = bus_state.get((leg["day_type"], leg["time_bin"], leg["direction"]), {})
    if bus:
        wait_multiplier = float(config["weather"][weather_type]["wait_multiplier"]["bus"])
        config["zone_service_parameters"][leg["origin_zone"]]["bus_wait_min"] += float(bus["extra_wait_min"]) / wait_multiplier
        excess = max(0.0, float(bus["load_ratio"]) - float(emergence["bus_feedback"]["crowding_threshold_ratio"]))
        config["weather"][weather_type]["utility_penalty"]["bus"] -= excess * float(emergence["bus_feedback"]["crowding_utility_penalty_per_load_above_threshold"])
    speed_factor = float(road.get("success_factor", 1.0))
    config["weather"][weather_type]["speed_multiplier"]["bus"] *= speed_factor
    config["weather"][weather_type]["speed_multiplier"]["ride_hailing"] *= speed_factor
    ride = ride_state.get((leg["day_type"], leg["time_bin"], leg["origin_zone"]), {})
    return choose_mode(
        _agent(profile), _trip(leg), weather_week, seed=seed, config=config,
        ride_hailing_extra_wait_min=float(ride.get("extra_wait_min", 0.0)),
    )


def _attempt_segments(
    option: Mapping[str, Any], leg: Mapping[str, Any], transport: Mapping[str, Any],
    *, succeeded: bool,
) -> list[tuple[str, float, bool]]:
    """Return ordered, actually experienced segments for one mode attempt."""
    mode = str(option["mode"])
    if mode == "walk":
        return [("walking", float(option["travel_time_min"]), True)]
    wait = float(option["wait_time_min"])
    if mode == "ride_hailing":
        if not succeeded:
            return [("ride_hailing_wait", wait, True)]
        in_vehicle = float(option["in_vehicle_time_min"])
        residual = max(0.0, float(option["travel_time_min"]) - wait - in_vehicle)
        return [
            ("ride_hailing_wait", wait, True),
            ("ride_hailing_in_vehicle", in_vehicle, False),
            ("ride_hailing_access", residual, False),
        ]
    service = transport["zone_service_parameters"]
    # The simple model historically used half of each zone's access value; this
    # split preserves its total bus travel time and therefore its mode choices.
    origin_walk = float(service[leg["origin_zone"]]["bus_access_min"]) / 2.0
    destination_walk = float(service[leg["destination_zone"]]["bus_access_min"]) / 2.0
    segments = [("bus_origin_walk", origin_walk, True), ("bus_wait", wait, True)]
    if succeeded:
        segments.extend([
            ("bus_in_vehicle", float(option["in_vehicle_time_min"]), False),
            ("bus_destination_walk", destination_walk, True),
        ])
    return segments


def _success_probability(
    mode: str, leg: Mapping[str, Any], weather_week: str, symmetric: Mapping[str, Any],
    bus_state: Mapping[tuple[str, str, str], Mapping[str, float]],
    ride_state: Mapping[tuple[str, str, str], Mapping[str, float]],
) -> float:
    probability = float(symmetric["transport_success_probability"][weather_week][mode])
    if mode == "bus":
        probability *= float(bus_state.get((leg["day_type"], leg["time_bin"], leg["direction"]), {}).get("success_factor", 1.0))
    elif mode == "ride_hailing":
        probability *= float(ride_state.get((leg["day_type"], leg["time_bin"], leg["origin_zone"]), {}).get("success_factor", 1.0))
    return min(max(probability, 0.0), 1.0)


def _simulate_leg(
    leg: Mapping[str, Any], choice: Mapping[str, Any], weather_week: str, *, seed: int,
    profile: AgentProfile, emergence: Mapping[str, Any],
    symmetric: Mapping[str, Any], transport: Mapping[str, Any],
    bus_state: Mapping[tuple[str, str, str], Mapping[str, float]],
    ride_state: Mapping[tuple[str, str, str], Mapping[str, float]],
) -> Dict[str, Any]:
    options = {row["mode"]: row for row in choice["alternatives"]}
    primary = choice["chosen_mode"]
    elapsed = spent = wait = exposure = ride_wait = heat_dose = failed_heat_dose = 0.0
    segment_minutes: Counter = Counter()
    attempts: list[Dict[str, Any]] = []
    final_mode = ""
    failure_reason = ""
    vulnerability = heat_vulnerability_weight(profile.age_group, config=emergence)
    for attempt_number in (1, 2):
        if attempt_number == 1:
            mode = primary
            option = options[mode]
        else:
            candidates = [
                row for name, row in options.items() if name != primary
                and float(row["travel_time_min"]) <= float(leg["max_leg_time_min"]) - elapsed
                and float(row["fare_yuan"]) <= float(leg["max_leg_budget_yuan"]) - spent
            ]
            if not candidates:
                failure_reason = "no_feasible_fallback"
                break
            option = max(candidates, key=lambda row: (row["utility"], row["mode"]))
            mode = option["mode"]
        probability = _success_probability(mode, leg, weather_week, symmetric, bus_state, ride_state)
        draw = _uniform(seed, leg["leg_id"], attempt_number, mode, "shared-state-success")
        succeeded = draw < probability
        attempt_start = _minutes(str(leg["departure_time"])) + elapsed
        segments = _attempt_segments(option, leg, transport, succeeded=succeeded)
        attempt_elapsed = attempt_outdoor = attempt_heat_dose = 0.0
        for name, duration, is_outdoor in segments:
            segment_minutes[name] += duration
            if is_outdoor:
                attempt_outdoor += duration
                attempt_heat_dose += calculate_heat_hazard_dose(
                    attempt_start + attempt_elapsed, duration, weather_week,
                    segment_factor=float(emergence["heat_exposure"]["outdoor_segment_factor"][mode]),
                    config=emergence,
                )
            attempt_elapsed += duration
        actual_wait = sum(duration for name, duration, _ in segments if name in {"bus_wait", "ride_hailing_wait"})
        wait += actual_wait
        exposure += attempt_outdoor
        heat_dose += attempt_heat_dose
        if mode == "ride_hailing":
            ride_wait += actual_wait
        attempts.append({
            "mode": mode, "success_probability": probability,
            "success_draw": draw, "succeeded": succeeded,
            "attempt_start_minute": round(attempt_start, 3),
            "actual_elapsed_minutes": round(attempt_elapsed, 3),
            "outdoor_exposure_minutes": round(attempt_outdoor, 3),
            "heat_hazard_dose_c_min": round(attempt_heat_dose, 3),
        })
        if succeeded:
            elapsed += attempt_elapsed
            spent += float(option["fare_yuan"])
            final_mode = mode
            break
        failed_heat_dose += attempt_heat_dose
        elapsed += attempt_elapsed
        spent += float(option["fare_yuan"]) * float(symmetric["failed_attempt_charge_fraction"][mode])
        failure_reason = "primary_failed" if attempt_number == 1 else "fallback_failed"
    return {
        **leg, "weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week],
        "initial_mode": primary, "attempt_count": len(attempts),
        "fallback_used": len(attempts) == 2, "fallback_mode": attempts[1]["mode"] if len(attempts) == 2 else "",
        "fallback_success": len(attempts) == 2 and attempts[1]["succeeded"],
        "final_success_mode": final_mode, "transport_failure": not bool(final_mode),
        "failure_reason": "" if final_mode else failure_reason,
        "ride_hailing_request_count": sum(row["mode"] == "ride_hailing" for row in attempts),
        "primary_success_probability": round(attempts[0]["success_probability"], 6),
        "supply_constrained_primary": attempts[0]["success_probability"] < float(symmetric["transport_success_probability"][weather_week][primary]) - 1e-12,
        "cumulative_wait_min": round(wait, 3), "ride_hailing_wait_min": round(ride_wait, 3),
        "cumulative_travel_time_min": round(elapsed, 3), "cumulative_fare_yuan": round(spent, 3),
        "outdoor_exposure_minutes": round(exposure, 3),
        "failed_attempt_outdoor_exposure_minutes": round(sum(
            float(row["outdoor_exposure_minutes"]) for row in attempts if not row["succeeded"]
        ), 3),
        "bus_origin_walk_minutes": round(segment_minutes["bus_origin_walk"], 3),
        "bus_wait_minutes": round(segment_minutes["bus_wait"], 3),
        "bus_in_vehicle_minutes": round(segment_minutes["bus_in_vehicle"], 3),
        "bus_destination_walk_minutes": round(segment_minutes["bus_destination_walk"], 3),
        "walking_minutes": round(segment_minutes["walking"], 3),
        "ride_hailing_wait_segment_minutes": round(segment_minutes["ride_hailing_wait"], 3),
        "ride_hailing_in_vehicle_minutes": round(segment_minutes["ride_hailing_in_vehicle"], 3),
        "ride_hailing_access_minutes": round(segment_minutes["ride_hailing_access"], 3),
        "fallback_start_minute": round(attempts[1]["attempt_start_minute"], 3) if len(attempts) == 2 else None,
        "heat_hazard_dose_c_min": round(heat_dose, 3),
        "failed_attempt_heat_hazard_dose_c_min": round(failed_heat_dose, 3),
        "heat_vulnerability_weight": round(vulnerability, 3),
        "heat_risk_burden": round(heat_dose * vulnerability, 3),
    }


def run_emergence_weather(
    profiles: Iterable[AgentProfile], activities: Iterable[Mapping[str, Any]], weather_week: str,
    *, seed: int, bus_frequency_multiplier: float = 1.0, ride_supply_multiplier: float = 1.0,
    config: Mapping[str, Any] | None = None, symmetric: Mapping[str, Any] | None = None,
) -> Dict[str, list[Dict[str, Any]]]:
    if bus_frequency_multiplier <= 0 or ride_supply_multiplier <= 0:
        raise ValueError("supply multipliers must be positive")
    emergence = config or load_emergence_config()
    symmetric = symmetric or load_symmetric_experiment_config()
    transport = _mode_config(symmetric)
    behavior_symmetric = copy.deepcopy(symmetric)
    behavior_symmetric["work_weather_windows"]["W1"] = list(
        emergence["extreme_heat_behavior_window"]
    )
    profile_by_id = {row.agent_id: row for row in profiles}
    ordered = sorted((dict(row) for row in activities), key=lambda row: row["activity_id"])
    states: Dict[str, Dict[str, Any]] = {}
    prospective_legs: list[Dict[str, Any]] = []
    for activity in ordered:
        profile = profile_by_id[activity["agent_id"]]
        remote = remote_work_decision(
            activity, profile, weather_week, seed=seed, config=behavior_symmetric
        )
        cancel = weather_cancellation_decision(activity, profile, weather_week, seed=seed, config=symmetric)
        behavior_active = weather_week != "W1" or _w1_behavior_active(
            str(activity["departure_time"]), emergence
        )
        if weather_week == "W1" and not behavior_active:
            cancel = {**cancel, "p_weather_cancel": 0.0, "weather_cancellation": False}
        cancelled = False if activity["activity_purpose"] in {"work", "medical"} else bool(cancel["weather_cancellation"])
        travel_required = not remote["remote_work"] and not cancelled
        states[activity["activity_id"]] = {
            **remote, **cancel, "weather_behavior_window_active": behavior_active,
            "weather_cancellation": cancelled, "travel_required": travel_required,
        }
        if travel_required:
            prospective_legs.extend((_leg(activity, "outbound", int(emergence["time_bin_minutes"])), _leg(activity, "return", int(emergence["time_bin_minutes"]))))
    first = _initial_choices(
        prospective_legs, profile_by_id, weather_week, seed=seed,
        transport=transport, emergence=emergence,
    )
    bus_state, ride_state, road_state, pre_feedback_rows = _build_system_state(
        prospective_legs, first, emergence, bus_frequency_multiplier=bus_frequency_multiplier,
        ride_supply_multiplier=ride_supply_multiplier, state_stage="pre_feedback",
    )
    second = {
        leg["leg_id"]: _local_choice(
            leg, profile_by_id[leg["agent_id"]], weather_week, seed=seed,
            base_transport=transport, emergence=emergence, bus_state=bus_state,
            ride_state=ride_state, road_state=road_state,
        ) for leg in prospective_legs
    }
    leg_by_activity_role = {(leg["activity_id"], leg["leg_role"]): leg for leg in prospective_legs}
    leg_results: list[Dict[str, Any]] = []
    activity_results: list[Dict[str, Any]] = []
    for activity in ordered:
        profile = profile_by_id[activity["agent_id"]]
        state = states[activity["activity_id"]]
        outbound_result = None
        return_result = None
        if state["travel_required"]:
            outbound_leg = leg_by_activity_role[(activity["activity_id"], "outbound")]
            outbound_result = _simulate_leg(
                outbound_leg, second[outbound_leg["leg_id"]], weather_week, seed=seed,
                profile=profile, emergence=emergence,
                symmetric=symmetric, transport=transport, bus_state=bus_state, ride_state=ride_state,
            )
            outbound_result["pre_feedback_mode"] = first[outbound_leg["leg_id"]]["chosen_mode"]
            outbound_result["mode_changed_after_feedback"] = outbound_result["initial_mode"] != outbound_result["pre_feedback_mode"]
            leg_results.append(outbound_result)
            if outbound_result["final_success_mode"]:
                return_leg = leg_by_activity_role[(activity["activity_id"], "return")]
                return_result = _simulate_leg(
                    return_leg, second[return_leg["leg_id"]], weather_week, seed=seed,
                    profile=profile, emergence=emergence,
                    symmetric=symmetric, transport=transport, bus_state=bus_state, ride_state=ride_state,
                )
                return_result["pre_feedback_mode"] = first[return_leg["leg_id"]]["chosen_mode"]
                return_result["mode_changed_after_feedback"] = return_result["initial_mode"] != return_result["pre_feedback_mode"]
                leg_results.append(return_result)
        remote = bool(state["remote_work"])
        outbound_success = bool(outbound_result and outbound_result["final_success_mode"])
        completed = remote or outbound_success
        necessary = bool(activity["necessary_activity"])
        transport_unmet = bool(state["travel_required"] and not outbound_success)
        necessary_unmet = necessary and transport_unmet
        if state["weather_cancellation"]:
            final_status = "weather_cancelled"
        elif transport_unmet:
            final_status = "transport_unmet"
        else:
            final_status = "completed"
        used_legs = [row for row in (outbound_result, return_result) if row]
        outdoor = sum(float(row["outdoor_exposure_minutes"]) for row in used_legs)
        heat_dose = sum(float(row["heat_hazard_dose_c_min"]) for row in used_legs)
        heat_risk = sum(float(row["heat_risk_burden"]) for row in used_legs)
        activity_results.append({
            **activity, "weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week],
            "age_group": profile.age_group, "work_status": profile.work_status,
            "digital_access": bool(profile.digital_access), "family_assistance": bool(profile.family_assistance),
            **state, "activity_completed": completed,
            "transport_related_unmet": transport_unmet,
            "necessary_transport_related_unmet": necessary_unmet,
            "activity_final_status": final_status,
            "outbound_final_mode": outbound_result["final_success_mode"] if outbound_result else "",
            "return_final_mode": return_result["final_success_mode"] if return_result else "",
            "fallback_attempts": sum(row["fallback_used"] for row in used_legs),
            "fallback_successes": sum(row["fallback_success"] for row in used_legs),
            "cumulative_wait_min": round(sum(float(row["cumulative_wait_min"]) for row in used_legs), 3),
            "cumulative_fare_yuan": round(sum(float(row["cumulative_fare_yuan"]) for row in used_legs), 3),
            "outdoor_exposure_minutes": round(outdoor, 3),
            "heat_exposure_index": round(outdoor if weather_week == "W1" else 0.0, 3),
            "heat_exposure_index_is_outdoor_minutes_alias": True,
            "heat_hazard_dose_c_min": round(heat_dose, 3),
            "heat_vulnerability_weight": round(
                heat_vulnerability_weight(profile.age_group, config=emergence), 3
            ),
            "heat_risk_burden": round(heat_risk, 3),
            "rain_exposure_index": round(outdoor if weather_week == "W2" else 0.0, 3),
        })
    final_by_leg = {row["leg_id"]: row for row in leg_results}
    _, _, _, final_system_rows = _build_system_state(
        prospective_legs, final_by_leg, emergence,
        bus_frequency_multiplier=bus_frequency_multiplier,
        ride_supply_multiplier=ride_supply_multiplier,
        mode_field="final_success_mode", ride_request_field="ride_hailing_request_count",
        state_stage="final",
    )
    for row in pre_feedback_rows + final_system_rows:
        row.update({"weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week]})
        if row["state_type"] == "road":
            row["dynamic_congestion_multiplier"] = float(row["success_factor"])
            bus_speed = (
                float(transport["modes"]["bus"]["speed_kmh"])
                * float(transport["weather"][WEATHER_TYPES[weather_week]]["speed_multiplier"]["bus"])
                * float(row["dynamic_congestion_multiplier"])
            )
            ride_speed = (
                float(transport["modes"]["ride_hailing"]["speed_kmh"])
                * float(transport["weather"][WEATHER_TYPES[weather_week]]["speed_multiplier"]["ride_hailing"])
                * float(row["dynamic_congestion_multiplier"])
            )
            row["bus_road_speed_kmh"] = round(bus_speed, 6)
            row["ride_hailing_road_speed_kmh"] = round(ride_speed, 6)
            volume = float(row["road_vehicle_volume"])
            row["mean_road_speed_kmh"] = round((
                bus_speed * float(row["scheduled_bus_vehicle_trips"])
                + ride_speed * float(row["successful_ride_hailing_vehicle_trips"])
            ) / volume, 6) if volume else 0.0
    return {
        "activity_results": activity_results,
        "leg_results": leg_results,
        "pre_feedback_system_state": pre_feedback_rows,
        "system_state": final_system_rows,
    }


def run_emergence_experiment(
    seed: int, *, bus_frequency_multiplier: float = 1.0, ride_supply_multiplier: float = 1.0,
    config: Mapping[str, Any] | None = None, symmetric: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    emergence = config or load_emergence_config()
    symmetric = symmetric or load_symmetric_experiment_config()
    profiles = assign_two_zone_homes(
        generate_population_agents(int(emergence["total_agents"]), seed=seed), seed=seed,
        s2_share=float(symmetric["s2_home_share"]),
    )
    activities = build_emergence_activities(profiles, seed=seed, config=emergence, symmetric=symmetric)
    activity_results: list[Dict[str, Any]] = []
    leg_results: list[Dict[str, Any]] = []
    pre_feedback_system_state: list[Dict[str, Any]] = []
    system_state: list[Dict[str, Any]] = []
    for week in WEATHER_TYPES:
        result = run_emergence_weather(
            profiles, activities, week, seed=seed,
            bus_frequency_multiplier=bus_frequency_multiplier,
            ride_supply_multiplier=ride_supply_multiplier,
            config=emergence, symmetric=symmetric,
        )
        activity_results.extend(result["activity_results"])
        leg_results.extend(result["leg_results"])
        pre_feedback_system_state.extend(result["pre_feedback_system_state"])
        system_state.extend(result["system_state"])
    return {
        "seed": seed, "profiles": profiles, "activities": activities,
        "activity_results": activity_results, "leg_results": leg_results,
        "pre_feedback_system_state": pre_feedback_system_state,
        "system_state": system_state,
    }


def summarize_macro(result: Mapping[str, Any]) -> list[Dict[str, Any]]:
    summaries = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            activities = [row for row in result["activity_results"] if row["weather_week"] == week and row["day_type"] == day_type]
            legs = [row for row in result["leg_results"] if row["weather_week"] == week and row["day_type"] == day_type]
            states = [row for row in result["system_state"] if row["weather_week"] == week and row["day_type"] == day_type]
            modes = Counter(row["final_success_mode"] for row in legs if row["final_success_mode"])
            successful = sum(modes.values())
            bus_states = [row for row in states if row["state_type"] == "bus"]
            ride_states = [row for row in states if row["state_type"] == "ride_hailing"]
            road_states = [row for row in states if row["state_type"] == "road"]
            necessary = [row for row in activities if row["necessary_activity"]]
            completed_count = sum(row["activity_completed"] for row in activities)
            travel_required_count = sum(row["travel_required"] for row in activities)
            planned_travel_required_necessary = [
                row for row in necessary if row["travel_required"]
            ]
            completed_travel_required_necessary = [
                row for row in planned_travel_required_necessary if row["activity_completed"]
            ]
            completed_necessary_count = sum(row["activity_completed"] for row in necessary)
            total_heat_dose = sum(float(row["heat_hazard_dose_c_min"]) for row in activities)
            total_heat_risk = sum(float(row["heat_risk_burden"]) for row in activities)
            necessary_heat_risk = sum(float(row["heat_risk_burden"]) for row in necessary)
            bus_demand = sum(
                int(row["initial_mode"] == "bus") + int(row["fallback_mode"] == "bus")
                for row in legs
            )
            ride_requests = sum(int(row["ride_hailing_request_count"]) for row in legs)
            fallback_attempts = sum(row["fallback_used"] for row in legs)
            fallback_successes = sum(row["fallback_success"] for row in legs)
            scheduled_bus_vehicle_trips = sum(float(row["scheduled_bus_vehicle_trips"]) for row in road_states)
            successful_ride_vehicle_trips = sum(float(row["successful_ride_hailing_vehicle_trips"]) for row in road_states)
            road_volume = sum(float(row["road_vehicle_volume"]) for row in road_states)
            mean_road_speed = (
                sum(float(row["mean_road_speed_kmh"]) * float(row["road_vehicle_volume"]) for row in road_states)
                / road_volume if road_volume else 0.0
            )
            total_bus_wait = sum(float(row["bus_wait_minutes"]) for row in legs)
            total_ride_wait = sum(float(row["ride_hailing_wait_min"]) for row in legs)
            total_travel_time = sum(float(row["cumulative_travel_time_min"]) for row in legs)
            total_bus_in_vehicle = sum(float(row["bus_in_vehicle_minutes"]) for row in legs)
            total_ride_in_vehicle = sum(float(row["ride_hailing_in_vehicle_minutes"]) for row in legs)
            summaries.append({
                "seed": result["seed"], "weather_week": week, "weather_type": WEATHER_TYPES[week], "day_type": day_type,
                "planned_activities": len(activities),
                "completed_activities": completed_count,
                "activity_completion_rate": round(completed_count / len(activities), 6) if activities else 1.0,
                "planned_necessary_activities": len(necessary),
                "completed_necessary_activities": completed_necessary_count,
                "travel_required": sum(row["travel_required"] for row in activities),
                "weather_cancellations": sum(row["weather_cancellation"] for row in activities),
                "weather_cancelled_activities": sum(row["weather_cancellation"] for row in activities),
                "remote_work": sum(row["remote_work"] for row in activities),
                "successful_legs": successful, "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                "ride_hailing_legs": modes["ride_hailing"],
                "walking_share": round(modes["walk"] / successful, 6) if successful else 0.0,
                "bus_share": round(modes["bus"] / successful, 6) if successful else 0.0,
                "ride_hailing_share": round(modes["ride_hailing"] / successful, 6) if successful else 0.0,
                "walking_mode_share": round(modes["walk"] / successful, 6) if successful else 0.0,
                "bus_mode_share": round(modes["bus"] / successful, 6) if successful else 0.0,
                "ride_hailing_mode_share": round(modes["ride_hailing"] / successful, 6) if successful else 0.0,
                "fallback_attempts": fallback_attempts,
                "fallback_successes": fallback_successes,
                "transport_success_rate": round(successful / len(legs), 6) if legs else 1.0,
                "mode_changes_after_feedback": sum(row["mode_changed_after_feedback"] for row in legs),
                "supply_constrained_primary_attempts": sum(row["supply_constrained_primary"] for row in legs),
                "transport_failures": sum(row["transport_failure"] for row in legs),
                "transport_related_unmet": sum(row["transport_related_unmet"] for row in activities),
                "necessary_transport_related_unmet": sum(row["necessary_transport_related_unmet"] for row in activities),
                "necessary_activity_completion_rate": round(sum(row["activity_completed"] for row in necessary) / len(necessary), 6) if necessary else 1.0,
                "planned_travel_required_necessary_activities": len(planned_travel_required_necessary),
                "completed_travel_required_necessary_activities": len(completed_travel_required_necessary),
                "peak_bus_load_ratio": round(max((float(row["load_ratio"]) for row in bus_states), default=0.0), 6),
                "bus_over_capacity_bins": sum(float(row["load_ratio"]) > 1.0 for row in bus_states),
                "peak_ride_demand_supply_ratio": round(max((float(row["load_ratio"]) for row in ride_states), default=0.0), 6),
                "average_ride_system_extra_wait_min": round(sum(float(row["extra_wait_min"]) for row in ride_states) / len(ride_states), 6) if ride_states else 0.0,
                "minimum_road_speed_multiplier": round(min((float(row["success_factor"]) for row in road_states), default=1.0), 6),
                "total_bus_wait_minutes": round(total_bus_wait, 6),
                "total_ride_hailing_wait_minutes": round(total_ride_wait, 6),
                "total_system_wait_minutes": round(total_bus_wait + total_ride_wait, 6),
                "mean_bus_wait_minutes_per_attempt": round(
                    total_bus_wait / bus_demand, 6
                ) if bus_demand else 0.0,
                "mean_ride_hailing_wait_minutes_per_request": round(
                    total_ride_wait / ride_requests, 6
                ) if ride_requests else 0.0,
                "mean_total_travel_time": round(
                    total_travel_time / len(legs), 6
                ) if legs else 0.0,
                "total_travel_time_minutes": round(total_travel_time, 6),
                "total_non_wait_travel_time_minutes": round(
                    total_travel_time - total_bus_wait - total_ride_wait, 6
                ),
                "total_in_vehicle_time_minutes": round(
                    total_bus_in_vehicle + total_ride_in_vehicle, 6
                ),
                "total_bus_in_vehicle_time_minutes": round(total_bus_in_vehicle, 6),
                "total_ride_hailing_in_vehicle_time_minutes": round(total_ride_in_vehicle, 6),
                "bus_demand": bus_demand,
                "ride_hailing_requests": ride_requests,
                "successful_ride_hailing_requests": modes["ride_hailing"],
                "failed_ride_hailing_requests": ride_requests - modes["ride_hailing"],
                "scheduled_bus_vehicle_trips": round(scheduled_bus_vehicle_trips, 6),
                "successful_ride_hailing_vehicle_trips": round(successful_ride_vehicle_trips, 6),
                "road_vehicle_volume": round(road_volume, 6),
                "mean_volume_capacity_ratio": round(
                    sum(float(row["load_ratio"]) for row in road_states) / len(road_states), 6
                ) if road_states else 0.0,
                "peak_road_volume_capacity_ratio": round(
                    max((float(row["load_ratio"]) for row in road_states), default=0.0), 6
                ),
                "mean_dynamic_congestion_multiplier": round(
                    sum(float(row["dynamic_congestion_multiplier"]) for row in road_states) / len(road_states), 6
                ) if road_states else 1.0,
                "mean_road_speed_kmh": round(mean_road_speed, 6),
                "total_wait_min": round(sum(float(row["cumulative_wait_min"]) for row in legs), 6),
                "total_fare_yuan": round(sum(float(row["cumulative_fare_yuan"]) for row in legs), 6),
                "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in activities), 6),
                "total_heat_exposure_minutes": round(sum(float(row["heat_exposure_index"]) for row in activities), 6),
                "total_heat_exposure_minutes_definition": "legacy_W1_outdoor_minutes_alias",
                "total_heat_hazard_dose_c_min": round(total_heat_dose, 6),
                "total_heat_risk_burden": round(total_heat_risk, 6),
                "necessary_heat_risk_burden": round(necessary_heat_risk, 6),
                "mean_heat_risk_per_travel_required_activity": round(
                    total_heat_risk / travel_required_count, 6
                ) if travel_required_count else 0.0,
                "heat_risk_per_completed_travel_required_necessary_activity": round(
                    necessary_heat_risk / len(completed_travel_required_necessary), 6
                ) if completed_travel_required_necessary else 0.0,
                "heat_risk_per_planned_travel_required_necessary_activity": round(
                    necessary_heat_risk / len(planned_travel_required_necessary), 6
                ) if planned_travel_required_necessary else 0.0,
            })
    return summaries
