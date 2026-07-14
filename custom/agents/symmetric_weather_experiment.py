"""Independent paired-weather experiment with a necessary-activity state machine.

This module deliberately does not alter T2, the formal W0/W1/W2 calendars, or
the production mode-choice parameters.  All additions are experiment-local.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.agents.simple_experiment import AGE_VALUE_OF_TIME, assign_two_zone_homes
from custom.agents.simple_mode_choice import (
    MODES, SimpleAgent, build_mode_options, calculate_ride_hailing_feedback_wait,
    choose_mode, load_simple_config,
)
from custom.envs.weather import compute_weather_cancel_probability


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "symmetric_weather_experiment.json"
WEATHER_TYPES = {"W0": "normal", "W1": "extreme_heat", "W2": "heavy_rain"}
PURPOSES = ("work", "medical", "shopping")
DAY_TYPES = ("workday", "rest_day")
EMPLOYED_STATUSES = {"regular_worker", "part_time_worker"}


def load_symmetric_experiment_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if tuple(config.get("purposes", {})) != PURPOSES:
        raise ValueError(f"symmetric experiment purposes must be {PURPOSES}")
    required = {
        "heat_walking_preference", "heat_bus_preference", "heat_ride_hailing_preference",
        "rain_walking_preference", "rain_bus_preference", "rain_ride_hailing_preference",
    }
    if set(config.get("mode_preferences", {})) != required:
        raise ValueError("symmetric experiment must define all six weather-mode preferences")
    for weather in ("normal", "extreme_heat", "heavy_rain"):
        p = float(config["work_remote_probability"][weather])
        low, high = map(float, config["work_remote_probability_sensitivity"][weather])
        if not (0 <= low <= p <= high <= 1):
            raise ValueError("remote-work base probability must be inside its sensitivity range")
    if tuple(config.get("day_types", ())) != DAY_TYPES:
        raise ValueError(f"day_types must be {DAY_TYPES}")
    for week in ("W1", "W2"):
        if len(config["work_weather_windows"][week]) != 2:
            raise ValueError("each adverse-weather work window needs start and end")
    for week in WEATHER_TYPES:
        for mode in MODES:
            p = float(config["transport_success_probability"][week][mode])
            if not 0 <= p <= 1:
                raise ValueError("transport success probabilities must be in [0, 1]")
    for value in config["failed_attempt_charge_fraction"].values():
        if not 0 <= float(value) <= 1:
            raise ValueError("failed-attempt charge fractions must be in [0, 1]")
    return config


def _stable_uniform(seed: int, *parts: Any) -> float:
    payload = "|".join(map(str, (seed, *parts))).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return integer / 2**64


def _clock(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def _minutes(value: str | time) -> int:
    current = _clock(value) if isinstance(value, str) else value
    return current.hour * 60 + current.minute


def _format_minutes(value: int) -> str:
    value = max(0, min(value, 23 * 60 + 59))
    return f"{value // 60:02d}:{value % 60:02d}"


def _stable_choice(values: Iterable[Any], seed: int, *parts: Any) -> Any:
    options = tuple(values)
    index = min(int(_stable_uniform(seed, *parts) * len(options)), len(options) - 1)
    return options[index]


def build_symmetric_activities(
    profiles: Iterable[AgentProfile], *, seed: int, config: Mapping[str, Any] | None = None
) -> list[Dict[str, Any]]:
    """Build one paired workday and rest day; work exists only on workday."""
    experiment = config or load_symmetric_experiment_config()
    transport = load_simple_config()
    activities: list[Dict[str, Any]] = []
    for profile in sorted(profiles, key=lambda row: row.agent_id):
        scheduled: list[tuple[str, str]] = []
        for day_type in DAY_TYPES:
            purposes = ("medical", "shopping")
            if day_type == "workday" and profile.work_status in EMPLOYED_STATUSES:
                purposes = ("work", *purposes)
            scheduled.extend((day_type, purpose) for purpose in purposes)
        for day_type, purpose in scheduled:
            template = experiment["purposes"][purpose]
            if template["destination"] == "stable_S1_or_S2":
                destination = "S1" if _stable_uniform(seed, "destination", profile.agent_id, purpose) < float(template["s1_destination_probability"]) else "S2"
            else:
                destination = template["destination"]
            origin = str(profile.home_zone)
            normal_options = build_mode_options(origin, destination, "W0", config=transport)
            distance = normal_options["bus"]["distance_km"]
            if purpose == "work":
                schedule = experiment["work_schedule"]
                prefix = "part_time" if profile.work_status == "part_time_worker" else "regular"
                start = str(_stable_choice(schedule[f"{prefix}_worker_start_times"], seed, "work-start", profile.agent_id))
                duration = int(_stable_choice(schedule[f"{prefix}_worker_duration_min"], seed, "work-duration", profile.agent_id))
                end_minutes = _minutes(start) + duration
                end_minutes = max(end_minutes, _minutes(schedule["minimum_end_time"]))
                end_minutes = min(end_minutes, _minutes(schedule["maximum_end_time"]))
                return_time = _format_minutes(end_minutes)
                reference_mode = str(schedule["baseline_departure_reference_mode"])
                departure_time = _format_minutes(_minutes(start) - math.ceil(float(normal_options[reference_mode]["travel_time_min"])))
                work_start_time = start
                work_end_time = return_time
            else:
                departure_time = str(template["departure_time"])
                return_time = str(template["return_time"])
                work_start_time = ""
                work_end_time = ""
            activities.append({
                "agent_id": profile.agent_id,
                "activity_id": f"A{profile.agent_id:03d}-{day_type.upper()}-{purpose.upper()}",
                "day_type": day_type,
                "activity_purpose": purpose,
                "departure_time": departure_time,
                "return_time": return_time,
                "work_start_time": work_start_time,
                "work_end_time": work_end_time,
                "origin_zone": origin,
                "destination_zone": destination,
                "distance_km": distance,
                "necessary_activity": bool(template["necessary"]),
                "max_leg_time_min": float(template["max_leg_time_min"]),
                "max_leg_budget_yuan": float(template["max_leg_budget_yuan"]),
            })
    return sorted(activities, key=lambda row: row["activity_id"])


def _mode_choice_config(experiment: Mapping[str, Any]) -> Dict[str, Any]:
    config = copy.deepcopy(load_simple_config())
    p = experiment["mode_preferences"]
    config["weather"]["extreme_heat"]["utility_penalty"] = {
        "walk": p["heat_walking_preference"], "bus": p["heat_bus_preference"],
        "ride_hailing": p["heat_ride_hailing_preference"],
    }
    config["weather"]["heavy_rain"]["utility_penalty"] = {
        "walk": p["rain_walking_preference"], "bus": p["rain_bus_preference"],
        "ride_hailing": p["rain_ride_hailing_preference"],
    }
    return config


def _profile_value(profile: AgentProfile, experiment: Mapping[str, Any]) -> tuple[str, str]:
    return (
        experiment["mobility_constraint_by_age"][profile.age_group],
        experiment["schedule_flexibility_by_work_status"][profile.work_status],
    )


def work_weather_exposed(activity: Mapping[str, Any], weather_week: str, *, config: Mapping[str, Any] | None = None) -> bool:
    experiment = config or load_symmetric_experiment_config()
    if activity["activity_purpose"] != "work" or weather_week == "W0":
        return False
    start, end = map(_clock, experiment["work_weather_windows"][weather_week])
    departure = _clock(str(activity["departure_time"]))
    return start <= departure < end


def remote_work_decision(
    activity: Mapping[str, Any], profile: AgentProfile, weather_week: str, *, seed: int,
    config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Draw exactly once per work activity, using a paired common random number."""
    experiment = config or load_symmetric_experiment_config()
    applicable = activity["activity_purpose"] == "work" and profile.work_status in EMPLOYED_STATUSES
    exposed = applicable and work_weather_exposed(activity, weather_week, config=experiment)
    weather_key = WEATHER_TYPES[weather_week] if exposed else "normal"
    probability = float(experiment["work_remote_probability"][weather_key]) if applicable else 0.0
    draw = _stable_uniform(seed, activity["agent_id"], activity["activity_id"], "remote-work")
    return {
        "remote_work_applicable": applicable, "work_weather_exposed": exposed,
        "remote_work_probability_source": weather_key if applicable else "not_applicable",
        "p_remote_work": probability, "remote_work_draw": draw,
        "remote_work": applicable and draw < probability,
    }


def weather_cancellation_decision(
    activity: Mapping[str, Any], profile: AgentProfile, weather_week: str, *, seed: int,
    config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Only shopping enters ordinary T2 cancellation; access is intentionally absent."""
    experiment = config or load_symmetric_experiment_config()
    purpose = activity["activity_purpose"]
    if weather_week == "W0" or purpose in {"work", "medical"}:
        probability = 0.0
    else:
        mobility, flexibility = _profile_value(profile, experiment)
        probability = compute_weather_cancel_probability(
            WEATHER_TYPES[weather_week], purpose, profile.age_group, mobility,
            flexibility, experiment["scenario_level"],
        )
    draw = _stable_uniform(seed, activity["agent_id"], activity["activity_id"], "weather-cancel")
    return {
        "weather_exposed": weather_week != "W0", "p_weather_cancel": probability,
        "weather_cancel_draw": draw, "weather_cancellation": draw < probability,
    }


def _agent(profile: AgentProfile) -> SimpleAgent:
    return SimpleAgent(
        agent_id=str(profile.agent_id), age_group=profile.age_group,
        home_zone=str(profile.home_zone), digital_access=bool(profile.digital_access),
        value_of_time_yuan_per_hour=AGE_VALUE_OF_TIME[profile.age_group],
        family_assistance=bool(profile.family_assistance),
    )


def _trip(activity: Mapping[str, Any], leg_role: str) -> Dict[str, Any]:
    reverse = leg_role == "return"
    return {
        "trip_id": f'{activity["activity_id"]}-{leg_role}', "agent_id": str(activity["agent_id"]),
        "origin_zone": activity["destination_zone"] if reverse else activity["origin_zone"],
        "destination_zone": activity["origin_zone"] if reverse else activity["destination_zone"],
    }


def _outdoor_exposure(option: Mapping[str, Any], origin: str, destination: str, transport: Mapping[str, Any]) -> float:
    if option["mode"] == "walk":
        return float(option["travel_time_min"])
    if option["mode"] == "ride_hailing":
        return float(option["wait_time_min"])
    service = transport["zone_service_parameters"]
    access = (float(service[origin]["bus_access_min"]) + float(service[destination]["bus_access_min"])) / 2
    return access + float(option["wait_time_min"])


def _prepare_choices(
    activities: list[Mapping[str, Any]], profiles: Mapping[int, AgentProfile], weather_week: str,
    leg_role: str, *, seed: int, transport: Mapping[str, Any], excluded: Mapping[str, set[str]] | None = None,
) -> tuple[Dict[str, Dict[str, Any] | None], int, float]:
    """Apply the existing one-round ride-hailing demand feedback to a leg cohort."""
    excluded = excluded or {}
    first: Dict[str, Dict[str, Any] | None] = {}
    for activity in activities:
        try:
            first[activity["activity_id"]] = choose_mode(
                _agent(profiles[activity["agent_id"]]), _trip(activity, leg_role), weather_week,
                seed=seed, config=transport,
            )
        except ValueError as exc:
            if "no available travel mode" not in str(exc):
                raise
            first[activity["activity_id"]] = None
    demand = sum(row is not None and row["chosen_mode"] == "ride_hailing" for row in first.values())
    extra_wait = calculate_ride_hailing_feedback_wait(demand, config=transport)
    final: Dict[str, Dict[str, Any] | None] = {}
    for activity in activities:
        try:
            choice = choose_mode(
                _agent(profiles[activity["agent_id"]]), _trip(activity, leg_role), weather_week,
                seed=seed, config=transport, ride_hailing_extra_wait_min=extra_wait,
            )
            blocked = excluded.get(activity["activity_id"], set())
            alternatives = [row for row in choice["alternatives"] if row["mode"] not in blocked]
            if not alternatives:
                final[activity["activity_id"]] = None
            else:
                selected = max(alternatives, key=lambda row: (row["utility"], row["mode"]))
                final[activity["activity_id"]] = {**choice, "chosen_mode": selected["mode"], "alternatives": alternatives}
        except ValueError as exc:
            if "no available travel mode" not in str(exc):
                raise
            final[activity["activity_id"]] = None
    return final, demand, extra_wait


def _attempt_success(seed: int, activity_id: str, week: str, leg_role: str, attempt: int, mode: str, experiment: Mapping[str, Any]) -> bool:
    p = float(experiment["transport_success_probability"][week][mode])
    return _stable_uniform(seed, activity_id, leg_role, attempt, mode, "transport-success") < p


def _simulate_leg(
    activity: Mapping[str, Any], profile: AgentProfile, choice: Mapping[str, Any] | None,
    weather_week: str, leg_role: str, *, seed: int, experiment: Mapping[str, Any],
    transport: Mapping[str, Any], feedback_demand: int, feedback_wait: float,
) -> Dict[str, Any]:
    """Run a primary attempt and at most one fallback, preserving failed costs/exposure."""
    base = {
        "initial_mode": "", "fallback_used": False, "fallback_mode": "",
        "fallback_success": False, "final_success_mode": "", "transport_failure": False,
        "transport_failure_reason": "", "attempt_count": 0,
        "ride_hailing_demand": feedback_demand, "ride_hailing_feedback_wait_min": feedback_wait,
        "ride_hailing_request_count": 0, "cumulative_wait_min": 0.0,
        "ride_hailing_wait_min": 0.0,
        "cumulative_travel_time_min": 0.0, "cumulative_fare_yuan": 0.0,
        "cumulative_outdoor_exposure_minutes": 0.0,
    }
    if choice is None:
        return {**base, "transport_failure": True, "transport_failure_reason": "no_available_primary_mode"}
    options = {row["mode"]: row for row in choice["alternatives"]}
    primary = choice["chosen_mode"]
    base["initial_mode"] = primary
    origin = str(_trip(activity, leg_role)["origin_zone"])
    destination = str(_trip(activity, leg_role)["destination_zone"])
    elapsed = 0.0
    spent = 0.0
    for attempt_number, mode in enumerate((primary, None), start=1):
        if attempt_number == 2:
            remaining_time = float(activity["max_leg_time_min"]) - elapsed
            remaining_budget = float(activity["max_leg_budget_yuan"]) - spent
            candidates = [
                row for name, row in options.items() if name != primary
                and float(row["travel_time_min"]) <= remaining_time
                and float(row["fare_yuan"]) <= remaining_budget
            ]
            if not candidates:
                base["transport_failure"] = True
                base["transport_failure_reason"] = "no_feasible_fallback"
                break
            option = max(candidates, key=lambda row: (row["utility"], row["mode"]))
            mode = option["mode"]
            base["fallback_used"] = True
            base["fallback_mode"] = mode
        else:
            option = options[mode]
        base["attempt_count"] += 1
        wait = float(option["wait_time_min"])
        exposure = _outdoor_exposure(option, origin, destination, transport)
        request = mode == "ride_hailing"
        base["ride_hailing_request_count"] += int(request)
        if request:
            base["ride_hailing_wait_min"] += wait
        success = _attempt_success(seed, str(activity["activity_id"]), weather_week, leg_role, attempt_number, str(mode), experiment)
        if success:
            elapsed += float(option["travel_time_min"])
            spent += float(option["fare_yuan"])
            base["cumulative_wait_min"] += wait
            base["cumulative_outdoor_exposure_minutes"] += exposure
            base["final_success_mode"] = mode
            base["fallback_success"] = attempt_number == 2
            break
        failure_elapsed = exposure
        failure_charge = float(option["fare_yuan"]) * float(experiment["failed_attempt_charge_fraction"][mode])
        elapsed += failure_elapsed
        spent += failure_charge
        base["cumulative_wait_min"] += wait
        base["cumulative_outdoor_exposure_minutes"] += exposure
        if attempt_number == 2:
            base["transport_failure"] = True
            base["transport_failure_reason"] = "fallback_failed"
    base["cumulative_travel_time_min"] = round(elapsed, 3)
    base["cumulative_fare_yuan"] = round(spent, 3)
    base["cumulative_wait_min"] = round(float(base["cumulative_wait_min"]), 3)
    base["ride_hailing_wait_min"] = round(float(base["ride_hailing_wait_min"]), 3)
    base["cumulative_outdoor_exposure_minutes"] = round(float(base["cumulative_outdoor_exposure_minutes"]), 3)
    return base


def run_symmetric_weather(
    profiles: Iterable[AgentProfile], activities: Iterable[Mapping[str, Any]], weather_week: str,
    *, seed: int, config: Mapping[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    experiment = config or load_symmetric_experiment_config()
    transport = _mode_choice_config(experiment)
    profile_by_id = {profile.agent_id: profile for profile in profiles}
    ordered = sorted((dict(row) for row in activities), key=lambda row: row["activity_id"])
    state: Dict[str, Dict[str, Any]] = {}
    outbound_activities = []
    for activity in ordered:
        profile = profile_by_id[activity["agent_id"]]
        remote = remote_work_decision(activity, profile, weather_week, seed=seed, config=experiment)
        cancel = weather_cancellation_decision(activity, profile, weather_week, seed=seed, config=experiment)
        cancelled = False if activity["activity_purpose"] in {"work", "medical"} else cancel["weather_cancellation"]
        travel_required = not remote["remote_work"] and not cancelled
        state[activity["activity_id"]] = {**remote, **cancel, "weather_cancellation": cancelled, "travel_required": travel_required}
        if travel_required:
            outbound_activities.append(activity)
    outbound_choices, outbound_demand, outbound_wait = _prepare_choices(
        outbound_activities, profile_by_id, weather_week, "outbound", seed=seed, transport=transport,
    )
    outbound: Dict[str, Dict[str, Any]] = {}
    return_candidates = []
    for activity in outbound_activities:
        leg = _simulate_leg(
            activity, profile_by_id[activity["agent_id"]], outbound_choices[activity["activity_id"]],
            weather_week, "outbound", seed=seed, experiment=experiment, transport=transport,
            feedback_demand=outbound_demand, feedback_wait=outbound_wait,
        )
        outbound[activity["activity_id"]] = leg
        if leg["final_success_mode"]:
            return_candidates.append(activity)
    return_choices, return_demand, return_wait = _prepare_choices(
        return_candidates, profile_by_id, weather_week, "return", seed=seed, transport=transport,
    )
    returns: Dict[str, Dict[str, Any]] = {}
    for activity in return_candidates:
        returns[activity["activity_id"]] = _simulate_leg(
            activity, profile_by_id[activity["agent_id"]], return_choices[activity["activity_id"]],
            weather_week, "return", seed=seed, experiment=experiment, transport=transport,
            feedback_demand=return_demand, feedback_wait=return_wait,
        )

    results: list[Dict[str, Any]] = []
    for activity in ordered:
        profile = profile_by_id[activity["agent_id"]]
        flags = state[activity["activity_id"]]
        out = outbound.get(activity["activity_id"], {})
        ret = returns.get(activity["activity_id"], {})
        necessary = bool(activity["necessary_activity"])
        remote = bool(flags["remote_work"])
        outbound_success = bool(out.get("final_success_mode"))
        activity_completed = remote or outbound_success
        transport_unmet = necessary and flags["travel_required"] and not outbound_success
        return_generated = outbound_success
        return_failure = return_generated and not bool(ret.get("final_success_mode"))
        cumulative_wait = float(out.get("cumulative_wait_min", 0)) + float(ret.get("cumulative_wait_min", 0))
        ride_wait = float(out.get("ride_hailing_wait_min", 0)) + float(ret.get("ride_hailing_wait_min", 0))
        cumulative_time = float(out.get("cumulative_travel_time_min", 0)) + float(ret.get("cumulative_travel_time_min", 0))
        cumulative_fare = float(out.get("cumulative_fare_yuan", 0)) + float(ret.get("cumulative_fare_yuan", 0))
        outdoor = float(out.get("cumulative_outdoor_exposure_minutes", 0)) + float(ret.get("cumulative_outdoor_exposure_minutes", 0))
        heat_index = outdoor * float(experiment["hazard_exposure_weight"]["W1_heat"]) if weather_week == "W1" else 0.0
        rain_index = outdoor * float(experiment["hazard_exposure_weight"]["W2_rain"]) if weather_week == "W2" else 0.0
        row = {
            **activity, "weather_week": weather_week, "weather_type": WEATHER_TYPES[weather_week],
            "age_group": profile.age_group, "work_status": profile.work_status,
            "digital_access": bool(profile.digital_access), "family_assistance": bool(profile.family_assistance),
            **flags,
            "work_completed": activity["activity_purpose"] == "work" and activity_completed,
            "outbound_leg_generated": flags["travel_required"], "return_leg_generated": return_generated,
            "outbound_initial_mode": out.get("initial_mode", ""),
            "outbound_fallback_used": out.get("fallback_used", False),
            "outbound_fallback_mode": out.get("fallback_mode", ""),
            "outbound_fallback_success": out.get("fallback_success", False),
            "outbound_final_mode": out.get("final_success_mode", ""),
            "outbound_attempt_count": out.get("attempt_count", 0),
            "outbound_transport_failure": out.get("transport_failure", False),
            "outbound_failure_reason": out.get("transport_failure_reason", ""),
            "return_initial_mode": ret.get("initial_mode", ""),
            "return_fallback_used": ret.get("fallback_used", False),
            "return_fallback_mode": ret.get("fallback_mode", ""),
            "return_fallback_success": ret.get("fallback_success", False),
            "return_final_mode": ret.get("final_success_mode", ""),
            "return_attempt_count": ret.get("attempt_count", 0),
            "return_transport_failure": return_failure,
            "return_failure_reason": ret.get("transport_failure_reason", ""),
            "activity_completed": activity_completed,
            "unmet_mandatory": transport_unmet,
            "transport_related_unmet": transport_unmet,
            "stranded_after_activity": return_failure,
            "selected_mode": out.get("initial_mode", ""),
            "successful_mode": out.get("final_success_mode", ""),
            "transport_failure": bool(out.get("transport_failure", False)),
            "ride_hailing_request_count": int(out.get("ride_hailing_request_count", 0)) + int(ret.get("ride_hailing_request_count", 0)),
            "ride_hailing_wait_min": round(ride_wait, 3),
            "cumulative_wait_min": round(cumulative_wait, 3),
            "travel_time_min": round(cumulative_time, 3),
            "fare_yuan": round(cumulative_fare, 3),
            "cumulative_travel_time_min": round(cumulative_time, 3),
            "cumulative_fare_yuan": round(cumulative_fare, 3),
            "outdoor_exposure_minutes": round(outdoor, 3),
            "heat_exposure_index": round(heat_index, 3),
            "rain_exposure_index": round(rain_index, 3),
        }
        mutually_exclusive = int(remote) + int(bool(flags["weather_cancellation"])) + int(bool(flags["travel_required"]))
        if mutually_exclusive != 1:
            raise AssertionError(f"activity state is not conserved: {activity['activity_id']}")
        if remote and (row["outbound_leg_generated"] or row["return_leg_generated"]):
            raise AssertionError("remote work cannot generate a commute leg")
        for field in ("ride_hailing_wait_min", "cumulative_wait_min", "cumulative_travel_time_min", "cumulative_fare_yuan", "outdoor_exposure_minutes", "heat_exposure_index", "rain_exposure_index"):
            if not math.isfinite(float(row[field])) or float(row[field]) < 0:
                raise AssertionError(f"invalid {field}: {row[field]}")
        results.append(row)
    return results


def run_symmetric_experiment(seed: int, *, config: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    experiment = config or load_symmetric_experiment_config()
    profiles = assign_two_zone_homes(
        generate_population_agents(int(experiment["total_agents"]), seed=seed), seed=seed,
        s2_share=float(experiment["s2_home_share"]),
    )
    activities = build_symmetric_activities(profiles, seed=seed, config=experiment)
    rows = []
    for week in WEATHER_TYPES:
        rows.extend(run_symmetric_weather(profiles, activities, week, seed=seed, config=experiment))
    return {"seed": seed, "profiles": profiles, "activities": activities, "results": rows}


def summarize_seed(result: Mapping[str, Any]) -> list[Dict[str, Any]]:
    summaries = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            for purpose in PURPOSES:
                rows = [
                    row for row in result["results"]
                    if row["weather_week"] == week and row["day_type"] == day_type
                    and row["activity_purpose"] == purpose
                ]
                if not rows:
                    continue
                travel = [row for row in rows if row["travel_required"]]
                completed = [row for row in rows if row["activity_completed"]]
                modes = Counter(
                    mode for row in rows
                    for mode in (row["outbound_final_mode"], row["return_final_mode"]) if mode
                )
                fallback_uses = sum(bool(row["outbound_fallback_used"]) + bool(row["return_fallback_used"]) for row in rows)
                fallback_success = sum(bool(row["outbound_fallback_success"]) + bool(row["return_fallback_success"]) for row in rows)
                generated_legs = sum(row["outbound_leg_generated"] + row["return_leg_generated"] for row in rows)
                requests = sum(row["ride_hailing_request_count"] for row in rows)
                summaries.append({
                    "seed": result["seed"], "weather_week": week, "weather_type": WEATHER_TYPES[week],
                    "day_type": day_type, "activity_purpose": purpose,
                    "necessary_activity": rows[0]["necessary_activity"],
                    "planned_activities": len(rows),
                    "weather_exposed_activities": sum(row["weather_exposed"] for row in rows),
                    "weather_cancellations": sum(row["weather_cancellation"] for row in rows),
                    "conditional_weather_cancel_rate": round(sum(row["weather_cancellation"] for row in rows) / len(rows), 6),
                    "remote_work_count": sum(row["remote_work"] for row in rows),
                    "remote_work_rate_among_work": round(sum(row["remote_work"] for row in rows) / len(rows), 6) if purpose == "work" else 0.0,
                    "work_weather_exposed_count": sum(row["work_weather_exposed"] for row in rows) if purpose == "work" else 0,
                    "remote_work_rate_among_exposed_work": round(
                        sum(row["remote_work"] for row in rows) / sum(row["work_weather_exposed"] for row in rows), 6
                    ) if purpose == "work" and any(row["work_weather_exposed"] for row in rows) else 0.0,
                    "travel_required_count": len(travel),
                    "initial_walk_count": sum(row["outbound_initial_mode"] == "walk" for row in rows),
                    "initial_bus_count": sum(row["outbound_initial_mode"] == "bus" for row in rows),
                    "initial_ride_hailing_count": sum(row["outbound_initial_mode"] == "ride_hailing" for row in rows),
                    "fallback_uses": fallback_uses,
                    "fallback_use_rate_per_generated_leg": round(fallback_uses / generated_legs, 6) if generated_legs else 0.0,
                    "fallback_successes": fallback_success,
                    "fallback_success_rate": round(fallback_success / fallback_uses, 6) if fallback_uses else 0.0,
                    "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                    "ride_hailing_legs": modes["ride_hailing"],
                    "necessary_activity_completion_rate": round(len(completed) / len(rows), 6) if rows[0]["necessary_activity"] else "",
                    "completed_activities": len(completed),
                    "transport_related_unmet": sum(row["transport_related_unmet"] for row in rows),
                    "return_failures": sum(row["return_transport_failure"] for row in rows),
                    "stranded_after_activity": sum(row["stranded_after_activity"] for row in rows),
                    "ride_hailing_demand": requests,
                    "average_ride_hailing_wait_min": round(sum(row["ride_hailing_wait_min"] for row in rows) / requests, 6) if requests else 0.0,
                    "average_travel_time_min": round(statistics.mean(row["cumulative_travel_time_min"] for row in travel), 6) if travel else 0.0,
                    "average_fare_yuan": round(statistics.mean(row["cumulative_fare_yuan"] for row in travel), 6) if travel else 0.0,
                    "total_wait_min": round(sum(row["cumulative_wait_min"] for row in rows), 6),
                    "total_fare_yuan": round(sum(row["cumulative_fare_yuan"] for row in rows), 6),
                    "total_outdoor_exposure_minutes": round(sum(row["outdoor_exposure_minutes"] for row in rows), 6),
                    "total_heat_exposure_index": round(sum(row["heat_exposure_index"] for row in rows), 6),
                    "total_rain_exposure_index": round(sum(row["rain_exposure_index"] for row in rows), 6),
                })
    return summaries
