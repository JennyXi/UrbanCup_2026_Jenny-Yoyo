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
    for key in ("base_capacity_representative_trips_per_30_min_direction", "maximum_extra_wait_min"):
        if float(config["bus_feedback"][key]) < 0:
            raise ValueError("bus feedback values must be non-negative")
    for value in config["ride_hailing_feedback"]["available_vehicles_per_30_min"].values():
        if float(value) <= 0:
            raise ValueError("ride-hailing supply must be positive")
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
    *, seed: int, transport: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    choices = {}
    for leg in sorted(legs, key=lambda row: row["leg_id"]):
        choices[leg["leg_id"]] = choose_mode(
            _agent(profiles[leg["agent_id"]]), _trip(leg), weather_week,
            seed=seed, config=transport,
        )
    return choices


def _build_system_state(
    legs: Iterable[Mapping[str, Any]], choices: Mapping[str, Mapping[str, Any]],
    emergence: Mapping[str, Any], *, bus_capacity_multiplier: float,
    ride_supply_multiplier: float, mode_field: str = "chosen_mode",
    ride_request_field: str | None = None, state_stage: str = "pre_feedback",
) -> tuple[Dict[tuple[str, str, str], Dict[str, float]], Dict[tuple[str, str, str], Dict[str, float]], Dict[tuple[str, str], Dict[str, float]], list[Dict[str, Any]]]:
    bus_counts: Counter = Counter()
    ride_counts: Counter = Counter()
    road_counts: Counter = Counter()
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
        day_multiplier = float(bus["rest_day_supply_multiplier"]) if day_type == "rest_day" else 1.0
        capacity = float(bus["base_capacity_representative_trips_per_30_min_direction"]) * day_multiplier * bus_capacity_multiplier
        load = demand / capacity if capacity else math.inf
        excess = max(0.0, load - float(bus["crowding_threshold_ratio"]))
        extra_wait = min(excess * float(bus["extra_wait_min_per_load_above_threshold"]), float(bus["maximum_extra_wait_min"]))
        success_factor = min(1.0, capacity / demand) if demand else 1.0
        bus_state[key] = {"demand": demand, "supply": capacity, "load_ratio": load, "extra_wait_min": extra_wait, "success_factor": success_factor}
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
    for key, demand in sorted(road_counts.items()):
        ratio = demand / float(road["reference_ride_hailing_vehicles_per_30_min"])
        speed = max(float(road["minimum_speed_multiplier"]), 1.0 / (1.0 + float(road["congestion_strength"]) * ratio))
        road_state[key] = {"demand": demand, "supply": float(road["reference_ride_hailing_vehicles_per_30_min"]), "load_ratio": ratio, "extra_wait_min": 0.0, "success_factor": speed}
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
    bus = bus_state.get((leg["day_type"], leg["time_bin"], leg["direction"]), {})
    if bus:
        wait_multiplier = float(config["weather"][weather_type]["wait_multiplier"]["bus"])
        config["zone_service_parameters"][leg["origin_zone"]]["bus_wait_min"] += float(bus["extra_wait_min"]) / wait_multiplier
        excess = max(0.0, float(bus["load_ratio"]) - float(emergence["bus_feedback"]["crowding_threshold_ratio"]))
        config["weather"][weather_type]["utility_penalty"]["bus"] -= excess * float(emergence["bus_feedback"]["crowding_utility_penalty_per_load_above_threshold"])
    road = road_state.get((leg["day_type"], leg["time_bin"]), {})
    speed_factor = float(road.get("success_factor", 1.0))
    config["weather"][weather_type]["speed_multiplier"]["bus"] *= speed_factor
    config["weather"][weather_type]["speed_multiplier"]["ride_hailing"] *= speed_factor
    ride = ride_state.get((leg["day_type"], leg["time_bin"], leg["origin_zone"]), {})
    return choose_mode(
        _agent(profile), _trip(leg), weather_week, seed=seed, config=config,
        ride_hailing_extra_wait_min=float(ride.get("extra_wait_min", 0.0)),
    )


def _outdoor(option: Mapping[str, Any], leg: Mapping[str, Any], transport: Mapping[str, Any]) -> float:
    if option["mode"] == "walk":
        return float(option["travel_time_min"])
    if option["mode"] == "ride_hailing":
        return float(option["wait_time_min"])
    service = transport["zone_service_parameters"]
    access = (float(service[leg["origin_zone"]]["bus_access_min"]) + float(service[leg["destination_zone"]]["bus_access_min"])) / 2
    return access + float(option["wait_time_min"])


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
    symmetric: Mapping[str, Any], transport: Mapping[str, Any],
    bus_state: Mapping[tuple[str, str, str], Mapping[str, float]],
    ride_state: Mapping[tuple[str, str, str], Mapping[str, float]],
) -> Dict[str, Any]:
    options = {row["mode"]: row for row in choice["alternatives"]}
    primary = choice["chosen_mode"]
    elapsed = spent = wait = exposure = ride_wait = 0.0
    attempts: list[Dict[str, Any]] = []
    final_mode = ""
    failure_reason = ""
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
        outdoor = _outdoor(option, leg, transport)
        wait += float(option["wait_time_min"])
        exposure += outdoor
        if mode == "ride_hailing":
            ride_wait += float(option["wait_time_min"])
        attempts.append({"mode": mode, "success_probability": probability, "success_draw": draw, "succeeded": succeeded})
        if succeeded:
            elapsed += float(option["travel_time_min"])
            spent += float(option["fare_yuan"])
            final_mode = mode
            break
        elapsed += outdoor
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
    }


def run_emergence_weather(
    profiles: Iterable[AgentProfile], activities: Iterable[Mapping[str, Any]], weather_week: str,
    *, seed: int, bus_capacity_multiplier: float = 1.0, ride_supply_multiplier: float = 1.0,
    config: Mapping[str, Any] | None = None, symmetric: Mapping[str, Any] | None = None,
) -> Dict[str, list[Dict[str, Any]]]:
    if bus_capacity_multiplier <= 0 or ride_supply_multiplier <= 0:
        raise ValueError("supply multipliers must be positive")
    emergence = config or load_emergence_config()
    symmetric = symmetric or load_symmetric_experiment_config()
    transport = _mode_config(symmetric)
    profile_by_id = {row.agent_id: row for row in profiles}
    ordered = sorted((dict(row) for row in activities), key=lambda row: row["activity_id"])
    states: Dict[str, Dict[str, Any]] = {}
    prospective_legs: list[Dict[str, Any]] = []
    for activity in ordered:
        profile = profile_by_id[activity["agent_id"]]
        remote = remote_work_decision(activity, profile, weather_week, seed=seed, config=symmetric)
        cancel = weather_cancellation_decision(activity, profile, weather_week, seed=seed, config=symmetric)
        cancelled = False if activity["activity_purpose"] in {"work", "medical"} else bool(cancel["weather_cancellation"])
        travel_required = not remote["remote_work"] and not cancelled
        states[activity["activity_id"]] = {**remote, **cancel, "weather_cancellation": cancelled, "travel_required": travel_required}
        if travel_required:
            prospective_legs.extend((_leg(activity, "outbound", int(emergence["time_bin_minutes"])), _leg(activity, "return", int(emergence["time_bin_minutes"]))))
    first = _initial_choices(prospective_legs, profile_by_id, weather_week, seed=seed, transport=transport)
    bus_state, ride_state, road_state, pre_feedback_rows = _build_system_state(
        prospective_legs, first, emergence, bus_capacity_multiplier=bus_capacity_multiplier,
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
                symmetric=symmetric, transport=transport, bus_state=bus_state, ride_state=ride_state,
            )
            outbound_result["pre_feedback_mode"] = first[outbound_leg["leg_id"]]["chosen_mode"]
            outbound_result["mode_changed_after_feedback"] = outbound_result["initial_mode"] != outbound_result["pre_feedback_mode"]
            leg_results.append(outbound_result)
            if outbound_result["final_success_mode"]:
                return_leg = leg_by_activity_role[(activity["activity_id"], "return")]
                return_result = _simulate_leg(
                    return_leg, second[return_leg["leg_id"]], weather_week, seed=seed,
                    symmetric=symmetric, transport=transport, bus_state=bus_state, ride_state=ride_state,
                )
                return_result["pre_feedback_mode"] = first[return_leg["leg_id"]]["chosen_mode"]
                return_result["mode_changed_after_feedback"] = return_result["initial_mode"] != return_result["pre_feedback_mode"]
                leg_results.append(return_result)
        remote = bool(state["remote_work"])
        outbound_success = bool(outbound_result and outbound_result["final_success_mode"])
        completed = remote or outbound_success
        necessary = bool(activity["necessary_activity"])
        unmet = necessary and state["travel_required"] and not outbound_success
        stranded = bool(return_result and return_result["transport_failure"])
        used_legs = [row for row in (outbound_result, return_result) if row]
        outdoor = sum(float(row["outdoor_exposure_minutes"]) for row in used_legs)
        activity_results.append({
            **activity, "weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week],
            "age_group": profile.age_group, "work_status": profile.work_status,
            "digital_access": bool(profile.digital_access), "family_assistance": bool(profile.family_assistance),
            **state, "activity_completed": completed, "transport_related_unmet": unmet,
            "stranded_after_activity": stranded,
            "outbound_final_mode": outbound_result["final_success_mode"] if outbound_result else "",
            "return_final_mode": return_result["final_success_mode"] if return_result else "",
            "fallback_uses": sum(row["fallback_used"] for row in used_legs),
            "cumulative_wait_min": round(sum(float(row["cumulative_wait_min"]) for row in used_legs), 3),
            "cumulative_fare_yuan": round(sum(float(row["cumulative_fare_yuan"]) for row in used_legs), 3),
            "outdoor_exposure_minutes": round(outdoor, 3),
            "heat_exposure_index": round(outdoor if weather_week == "W1" else 0.0, 3),
            "rain_exposure_index": round(outdoor if weather_week == "W2" else 0.0, 3),
        })
    final_by_leg = {row["leg_id"]: row for row in leg_results}
    _, _, _, final_system_rows = _build_system_state(
        prospective_legs, final_by_leg, emergence,
        bus_capacity_multiplier=bus_capacity_multiplier,
        ride_supply_multiplier=ride_supply_multiplier,
        mode_field="final_success_mode", ride_request_field="ride_hailing_request_count",
        state_stage="final",
    )
    for row in pre_feedback_rows + final_system_rows:
        row.update({"weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week]})
    return {
        "activity_results": activity_results,
        "leg_results": leg_results,
        "pre_feedback_system_state": pre_feedback_rows,
        "system_state": final_system_rows,
    }


def run_emergence_experiment(
    seed: int, *, bus_capacity_multiplier: float = 1.0, ride_supply_multiplier: float = 1.0,
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
            bus_capacity_multiplier=bus_capacity_multiplier,
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
            summaries.append({
                "seed": result["seed"], "weather_week": week, "weather_type": WEATHER_TYPES[week], "day_type": day_type,
                "planned_activities": len(activities), "travel_required": sum(row["travel_required"] for row in activities),
                "weather_cancellations": sum(row["weather_cancellation"] for row in activities),
                "remote_work": sum(row["remote_work"] for row in activities),
                "successful_legs": successful, "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                "ride_hailing_legs": modes["ride_hailing"],
                "walking_share": round(modes["walk"] / successful, 6) if successful else 0.0,
                "bus_share": round(modes["bus"] / successful, 6) if successful else 0.0,
                "ride_hailing_share": round(modes["ride_hailing"] / successful, 6) if successful else 0.0,
                "fallback_uses": sum(row["fallback_used"] for row in legs),
                "mode_changes_after_feedback": sum(row["mode_changed_after_feedback"] for row in legs),
                "supply_constrained_primary_attempts": sum(row["supply_constrained_primary"] for row in legs),
                "transport_failures": sum(row["transport_failure"] for row in legs),
                "transport_related_unmet": sum(row["transport_related_unmet"] for row in activities),
                "stranded_after_activity": sum(row["stranded_after_activity"] for row in activities),
                "necessary_activity_completion_rate": round(sum(row["activity_completed"] for row in necessary) / len(necessary), 6) if necessary else 1.0,
                "peak_bus_load_ratio": round(max((float(row["load_ratio"]) for row in bus_states), default=0.0), 6),
                "bus_over_capacity_bins": sum(float(row["load_ratio"]) > 1.0 for row in bus_states),
                "peak_ride_demand_supply_ratio": round(max((float(row["load_ratio"]) for row in ride_states), default=0.0), 6),
                "average_ride_system_extra_wait_min": round(sum(float(row["extra_wait_min"]) for row in ride_states) / len(ride_states), 6) if ride_states else 0.0,
                "minimum_road_speed_multiplier": round(min((float(row["success_factor"]) for row in road_states), default=1.0), 6),
                "total_wait_min": round(sum(float(row["cumulative_wait_min"]) for row in legs), 6),
                "total_fare_yuan": round(sum(float(row["cumulative_fare_yuan"]) for row in legs), 6),
                "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in activities), 6),
            })
    return summaries
