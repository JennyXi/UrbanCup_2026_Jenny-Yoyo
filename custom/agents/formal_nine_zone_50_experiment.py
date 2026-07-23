"""Formal nine-zone 50-Agent weather experiment using main T1/T6 OD inputs."""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Mapping, Sequence

from custom.agents.agent_population import AgentProfile
from custom.agents.emergence_experiment import (
    calculate_heat_hazard_dose,
    heat_vulnerability_weight,
    load_emergence_config,
)
from custom.agents.formal_nine_zone_experiment import (
    ROOT,
    WEATHER_SCENARIOS,
    _events_for,
    build_formal_nine_zone_inputs,
    load_formal_nine_zone_config,
    run_formal_transport_scenario,
    validate_formal_nine_zone_config,
)
from custom.agents.leg_generation import build_time_feasible_legs
from custom.agents.symmetric_weather_experiment import (
    load_symmetric_experiment_config,
    remote_work_decision,
    weather_cancellation_decision,
)
from custom.transport.network import build_transport_network


DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_experiment.json"
EMPLOYED_STATUSES = {"regular_worker", "part_time_worker"}


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a recursive copy so a stress config cannot mutate the P0 config."""
    merged = copy.deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_formal_50_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    validate_formal_50_config(config)
    return config


def validate_formal_50_config(config: Mapping[str, Any]) -> None:
    total_agents = int(config["total_agents"])
    if total_agents <= 0:
        raise ValueError("formal staged experiment must use a positive Agent count")
    if tuple(config["weather_scenarios"]) != WEATHER_SCENARIOS:
        raise ValueError("formal experiment weather scenarios must be W0/W1/W2")
    if tuple(config["day_types"]) != ("workday", "rest_day"):
        raise ValueError("formal experiment must contain workday and rest_day")
    if config["policy"] != "P0_no_policy":
        raise ValueError("the formal baseline experiment is P0 only")
    state = config["activity_state_machine"]
    if state["work_weather_cancellation_allowed"] or state["medical_weather_cancellation_allowed"]:
        raise ValueError("work and medical cannot enter ordinary weather cancellation")
    expected = {"normal": 0.0, "extreme_heat": 0.02, "heavy_rain": 0.05}
    if {key: float(value) for key, value in state["remote_work_probability"].items()} != expected:
        raise ValueError("formal remote-work probabilities must be 0/2/5 percent")
    if not state["remote_work_is_activity_level_single_draw"] or state["schedule_shift_enabled"]:
        raise ValueError("remote work must use one activity-level draw and schedule shift stays off")


def _profile(row: Mapping[str, Any]) -> AgentProfile:
    return AgentProfile(**dict(row))


def _activity_day(activity: Mapping[str, Any]) -> date:
    return activity["planned_start_datetime"].date()


def _inbound_by_activity(legs: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {
        row["activity_id"]: row for row in legs if row["leg_role"] != "return_home"
    }


def _departure_exposed(moment: datetime, events: Sequence[Mapping[str, Any]]) -> bool:
    return any(event["start"] <= moment < event["end"] for event in events)


def _activity_states(
    activities: Sequence[Mapping[str, Any]], legs: Sequence[Mapping[str, Any]],
    profiles: Mapping[int, AgentProfile], weather_scenario: str, day_type: str,
    *, formal_config: Mapping[str, Any], experiment: Mapping[str, Any],
    symmetric: Mapping[str, Any], seed: int,
    departure_time_source: str = "preliminary_leg_departure",
) -> list[Dict[str, Any]]:
    events = _events_for(formal_config, weather_scenario, day_type)
    inbound = _inbound_by_activity(legs)
    weather_type = formal_config["weather_scenarios"][weather_scenario]["weather_type"]
    state_config = experiment["activity_state_machine"]
    rows = []
    for activity in sorted(activities, key=lambda row: row["activity_id"]):
        profile = profiles[int(activity["agent_id"])]
        leg = inbound.get(activity["activity_id"])
        departure = leg["departure_time"] if leg else activity["planned_start_datetime"]
        exposed = _departure_exposed(departure, events)
        proxy = {
            **dict(activity),
            "departure_time": departure.strftime("%H:%M"),
        }
        remote_base = remote_work_decision(
            proxy, profile, "W0", seed=seed, config=symmetric,
        )
        remote_applicable = (
            activity["activity_purpose"] == "work"
            and profile.work_status in EMPLOYED_STATUSES
        )
        remote_source = weather_type if exposed else "normal"
        p_remote = (
            float(state_config["remote_work_probability"][remote_source])
            if remote_applicable else 0.0
        )
        remote = remote_applicable and float(remote_base["remote_work_draw"]) < p_remote

        cancel_base = weather_cancellation_decision(
            proxy, profile, weather_scenario, seed=seed, config=symmetric,
        )
        cancellable = activity["activity_purpose"] not in {"work", "medical"}
        p_cancel = float(cancel_base["p_weather_cancel"]) if cancellable and exposed else 0.0
        cancelled = cancellable and exposed and float(cancel_base["weather_cancel_draw"]) < p_cancel
        travel_required = not remote and not cancelled
        rows.append({
            **dict(activity),
            "weather_scenario": weather_scenario, "day_type": day_type,
            "weather_type": weather_type, "departure_time": departure,
            "weather_decision_departure_time": departure,
            "weather_decision_departure_time_source": departure_time_source,
            "weather_exposed": exposed,
            "remote_work_applicable": remote_applicable,
            "remote_work_probability_source": remote_source if remote_applicable else "not_applicable",
            "p_remote_work": p_remote,
            "remote_work_draw": remote_base["remote_work_draw"],
            "remote_work": remote,
            "p_weather_cancel": p_cancel,
            "weather_cancel_draw": cancel_base["weather_cancel_draw"],
            "weather_cancellation": cancelled,
            "travel_required": travel_required,
        })
    return rows


def _rebuild_travel_legs(
    activity_states: Sequence[Mapping[str, Any]], profiles: Sequence[AgentProfile],
    spatial_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    retained = []
    for row in activity_states:
        if row["travel_required"]:
            retained.append({
                key: value for key, value in row.items()
                if key in {
                    "agent_id", "age_group", "work_status", "medical_need_level",
                    "day_of_week", "is_weekend", "activity_id", "activity_sequence",
                    "sequence_order", "activity_purpose", "home_zone", "destination_zone",
                    "planned_start_datetime", "planned_end_datetime", "is_mandatory",
                    "baseline_cancel_probability",
                }
            })
    if not retained:
        return [], []
    timed = build_time_feasible_legs(profiles, retained, spatial_by_id)
    return [dict(row) for row in timed["activities"]], [dict(row) for row in timed["legs"]]


def _access_split(row: Mapping[str, Any], network: Mapping[str, Any]) -> tuple[float, float]:
    total = float(row.get("access_time_min") or 0.0)
    mode = row["final_mode"]
    if mode not in {"bus", "metro"} or total <= 0:
        return 0.0, 0.0
    params = network["config"]["zone_service_parameters"]
    key = f"{mode}_access_min"
    origin_value = params[row["origin_zone"]].get(key)
    destination_value = params[row["destination_zone"]].get(key)
    if origin_value is None or destination_value is None:
        return total / 2.0, total / 2.0
    configured_total = float(origin_value) + float(destination_value)
    if configured_total <= 0:
        return total / 2.0, total / 2.0
    return total * float(origin_value) / configured_total, total * float(destination_value) / configured_total


def _outdoor_segments(
    row: Mapping[str, Any], network: Mapping[str, Any],
) -> list[tuple[datetime, float]]:
    start = row["departure_time"]
    failed = float(row.get("failed_attempt_consumed_minutes") or 0.0)
    segments: list[tuple[datetime, float]] = []
    if failed > 0:
        segments.append((start, failed))
    if not row["transport_succeeded"]:
        return segments
    attempt_start = row["final_attempt_departure_time"]
    mode = row["final_mode"]
    if mode == "walk":
        duration = max(0.0, float(row["total_travel_time_min"]) - failed)
        segments.append((attempt_start, duration))
    elif mode == "metro" and int(row.get("bus_metro_transfer_count") or 0) > 0:
        params = network["config"]["zone_service_parameters"]
        transfer_penalty = float(
            network["config"]["metro_accessibility"]["bus_feeder"]
            ["transfer_penalty_min_per_feeder"]
        )
        cursor = attempt_start
        origin_bus = row.get("origin_feeder_mode") == "bus"
        destination_bus = row.get("destination_feeder_mode") == "bus"
        if origin_bus:
            access = float(row.get("origin_feeder_access_minutes") or 0.0)
            wait = float(row.get("origin_feeder_wait_minutes") or 0.0)
            if access > 0:
                segments.append((cursor, access))
            if wait > 0:
                segments.append((cursor + timedelta(minutes=access), wait))
            cursor += timedelta(
                minutes=float(row.get("origin_feeder_total_time_minutes") or 0.0)
                + transfer_penalty
            )
        else:
            access = float(
                row.get("origin_metro_walk_access_minutes")
                if row.get("origin_metro_walk_access_minutes") is not None
                else params[row["origin_zone"]]["metro_access_min"] or 0.0
            )
            if access > 0:
                segments.append((cursor, access))
            cursor += timedelta(minutes=access)
        # Metro platform waiting is station time, not outdoor heat/rain exposure.
        if destination_bus:
            feeder_total = float(row.get("destination_feeder_total_time_minutes") or 0.0)
            feeder_start = attempt_start + timedelta(
                minutes=max(0.0, float(row["total_travel_time_min"]) - feeder_total)
            )
            access = float(row.get("destination_feeder_access_minutes") or 0.0)
            wait = float(row.get("destination_feeder_wait_minutes") or 0.0)
            if access > 0:
                segments.append((feeder_start, access))
            if wait > 0:
                segments.append((feeder_start + timedelta(minutes=access), wait))
        else:
            access = float(
                row.get("destination_metro_walk_access_minutes")
                if row.get("destination_metro_walk_access_minutes") is not None
                else params[row["destination_zone"]]["metro_access_min"] or 0.0
            )
            if access > 0:
                segments.append((
                    attempt_start + timedelta(
                        minutes=max(0.0, float(row["total_travel_time_min"]) - access)
                    ),
                    access,
                ))
    elif mode in {"bus", "metro"}:
        origin_access, destination_access = _access_split(row, network)
        wait = float(row["wait_minutes"])
        vehicle = float(row["in_vehicle_time_min"])
        transfer = float(row["transfer_time_min"])
        if origin_access > 0:
            segments.append((attempt_start, origin_access))
        if mode == "bus" and wait > 0:
            segments.append((attempt_start + timedelta(minutes=origin_access), wait))
        if destination_access > 0:
            destination_start = attempt_start + timedelta(
                minutes=origin_access + wait + vehicle + transfer
            )
            segments.append((destination_start, destination_access))
    elif mode == "ride_hailing":
        wait = float(row["wait_minutes"])
        if wait > 0:
            segments.append((attempt_start, wait))
    return [(segment_start, duration) for segment_start, duration in segments if duration > 0]


def _overlap_minutes(
    start: datetime, duration: float, events: Sequence[Mapping[str, Any]],
) -> float:
    end = start + timedelta(minutes=duration)
    return sum(
        max(0.0, (min(end, event["end"]) - max(start, event["start"])).total_seconds() / 60.0)
        for event in events
        if min(end, event["end"]) > max(start, event["start"])
    )


def _add_exposure(
    choices: Sequence[Mapping[str, Any]], agents: Mapping[int, Mapping[str, Any]],
    weather_scenario: str, day_type: str, formal_config: Mapping[str, Any],
    heat_config: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    network = build_transport_network()
    events = _events_for(formal_config, weather_scenario, day_type)
    rows = []
    for raw in choices:
        row = dict(raw)
        segments = _outdoor_segments(row, network)
        outdoor = sum(duration for _start, duration in segments)
        heat_dose = sum(
            calculate_heat_hazard_dose(
                start.hour * 60 + start.minute + start.second / 60.0,
                duration, weather_scenario, config=heat_config,
            )
            for start, duration in segments
        )
        rain = sum(_overlap_minutes(start, duration, events) for start, duration in segments)
        age_group = agents[int(row["agent_id"])]["age_group"]
        vulnerability = heat_vulnerability_weight(age_group, config=heat_config)
        row.update({
            "outdoor_exposure_minutes": round(outdoor, 6),
            "heat_hazard_dose_c_min": round(heat_dose, 6),
            "age_vulnerability_weight": vulnerability,
            "heat_risk_burden": round(heat_dose * vulnerability, 6),
            "rain_exposure_minutes": round(rain if weather_scenario == "W2" else 0.0, 6),
        })
        rows.append(row)
    return rows


def _final_activity_results(
    states: Sequence[Mapping[str, Any]], transport_rows: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    transport = {row["activity_id"]: row for row in transport_rows}
    results = []
    for state in states:
        transport_row = transport.get(state["activity_id"], {})
        if state["remote_work"]:
            final_status = "completed"
        elif state["weather_cancellation"]:
            final_status = "weather_cancelled"
        else:
            final_status = transport_row.get("final_status", "transport_unmet")
        results.append({
            **dict(state),
            "final_status": final_status,
            "weather_cancelled": final_status == "weather_cancelled",
            "completed": final_status == "completed",
            "transport_unmet": final_status == "transport_unmet",
            "transport_succeeded": transport_row.get("transport_succeeded", False),
            "activity_completed": final_status == "completed",
            "late_but_reached": transport_row.get("late_but_reached", False),
            "mandatory_activity_incomplete": bool(
                state["is_mandatory"] and final_status != "completed"
            ),
            "completion_failure_reason": transport_row.get("completion_failure_reason"),
            "actual_arrival_time": transport_row.get("actual_arrival_time"),
            "arrival_delay_minutes": transport_row.get("arrival_delay_minutes"),
            "on_time_arrival": transport_row.get("on_time_arrival"),
        })
    return results


def _update_summary(
    base: Mapping[str, Any], activities: Sequence[Mapping[str, Any]],
    choices: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    row = dict(base)
    necessary = [activity for activity in activities if activity["is_mandatory"]]
    completed = [activity for activity in activities if activity["completed"]]
    completed_necessary = [activity for activity in necessary if activity["completed"]]
    activity_by_id = {activity["activity_id"]: activity for activity in activities}
    necessary_heat = sum(
        choice["heat_risk_burden"] for choice in choices
        if activity_by_id.get(choice["activity_id"], {}).get("is_mandatory")
    )
    completed_travel_necessary = [
        activity for activity in necessary
        if activity["travel_required"] and activity["completed"]
    ]
    planned_travel_necessary = [activity for activity in necessary if activity["travel_required"]]
    row.update({
        "planned_activities": len(activities),
        "weather_exposed_activities": sum(activity["weather_exposed"] for activity in activities),
        "remote_work": sum(activity["remote_work"] for activity in activities),
        "travel_required_activities": sum(activity["travel_required"] for activity in activities),
        "completed_activities": len(completed),
        "activity_completion_rate": round(len(completed) / len(activities), 6) if activities else None,
        "planned_necessary_activities": len(necessary),
        "completed_necessary_activities": len(completed_necessary),
        "necessary_activity_completion_rate": round(len(completed_necessary) / len(necessary), 6) if necessary else None,
        "weather_cancelled_activities": sum(activity["weather_cancellation"] for activity in activities),
        "transport_unmet_activities": sum(activity["transport_unmet"] for activity in activities),
        "necessary_transport_unmet_activities": sum(activity["transport_unmet"] for activity in necessary),
        "transport_related_unmet": sum(activity["transport_unmet"] for activity in activities),
        "necessary_transport_related_unmet": sum(activity["transport_unmet"] for activity in necessary),
        "mandatory_activity_incomplete": sum(
            activity["mandatory_activity_incomplete"] for activity in activities
        ),
        "late_but_reached": sum(activity["late_but_reached"] for activity in activities),
        "transport_unmet": sum(activity["transport_unmet"] for activity in activities),
        "total_outdoor_exposure_minutes": round(sum(choice["outdoor_exposure_minutes"] for choice in choices), 6),
        "total_heat_hazard_dose_c_min": round(sum(choice["heat_hazard_dose_c_min"] for choice in choices), 6),
        "total_heat_risk_burden": round(sum(choice["heat_risk_burden"] for choice in choices), 6),
        "necessary_heat_risk_burden": round(necessary_heat, 6),
        "heat_risk_per_completed_travel_required_necessary_activity": (
            round(necessary_heat / len(completed_travel_necessary), 6)
            if completed_travel_necessary else None
        ),
        "heat_risk_per_planned_travel_required_necessary_activity": (
            round(necessary_heat / len(planned_travel_necessary), 6)
            if planned_travel_necessary else None
        ),
        "total_rain_exposure_minutes": round(sum(choice["rain_exposure_minutes"] for choice in choices), 6),
        "total_system_wait_minutes": round(sum(choice["cumulative_wait_minutes"] for choice in choices), 6),
    })
    return row


def run_formal_nine_zone_50_experiment(
    *, config: Mapping[str, Any] | None = None, seed: int | None = None,
    weather_scenarios: Sequence[str] | None = None,
    day_types: Sequence[str] | None = None,
    paired_inputs: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    experiment = dict(config or load_formal_50_config())
    validate_formal_50_config(experiment)
    formal = load_formal_nine_zone_config(ROOT / experiment["formal_transport_config"])
    formal = _deep_merge(formal, experiment.get("formal_overrides", {}))
    formal["total_agents"] = int(experiment["total_agents"])
    validate_formal_nine_zone_config(formal)
    seed = int(experiment["seed"] if seed is None else seed)
    inputs = (
        build_formal_nine_zone_inputs(config=formal, seed=seed)
        if paired_inputs is None else paired_inputs
    )
    if int(inputs["seed"]) != seed or len(inputs["agents"]) != int(experiment["total_agents"]):
        raise ValueError("paired inputs must match the requested seed and Agent count")
    profile_objects = [_profile(row) for row in inputs["agents"]]
    profiles = {profile.agent_id: profile for profile in profile_objects}
    agent_rows = {int(row["agent_id"]): row for row in inputs["agents"]}
    symmetric = load_symmetric_experiment_config(ROOT / experiment["symmetric_behavior_config"])
    heat_config = load_emergence_config(ROOT / experiment["emergence_heat_config"])
    all_states: list[Dict[str, Any]] = []
    all_activity_results: list[Dict[str, Any]] = []
    all_choices: list[Dict[str, Any]] = []
    all_dispatch: list[Dict[str, Any]] = []
    all_vehicle_states: list[Dict[str, Any]] = []
    summaries: list[Dict[str, Any]] = []
    selected_weather = tuple(weather_scenarios or experiment["weather_scenarios"])
    if not selected_weather or any(value not in WEATHER_SCENARIOS for value in selected_weather):
        raise ValueError("selected weather scenarios must be a non-empty W0/W1/W2 subset")
    selected_day_types = tuple(day_types or experiment["day_types"])
    if not selected_day_types or any(value not in experiment["day_types"] for value in selected_day_types):
        raise ValueError("selected day types must be a non-empty configured subset")
    for weather_scenario in selected_weather:
        for day_type in selected_day_types:
            selected_date = date.fromisoformat(formal["selected_days"][day_type])
            planned_activities = [
                row for row in inputs["activities"] if _activity_day(row) == selected_date
            ]
            planned_legs = [
                row for row in inputs["legs"] if row["departure_time"].date() == selected_date
            ]
            states = _activity_states(
                planned_activities, planned_legs, profiles, weather_scenario, day_type,
                formal_config=formal, experiment=experiment, symmetric=symmetric, seed=seed,
            )
            retained_activities, travel_legs = _rebuild_travel_legs(
                states, profile_objects, inputs["spatial_by_id"],
            )
            transport = run_formal_transport_scenario(
                inputs, config=formal, weather_scenario=weather_scenario,
                day_type=day_type, activities=retained_activities,
                legs=travel_legs, seed=seed,
            )
            choices = _add_exposure(
                transport["mode_choices"], agent_rows, weather_scenario, day_type,
                formal, heat_config,
            )
            activity_results = _final_activity_results(states, transport["activity_results"])
            summary = _update_summary(transport["summary"], activity_results, choices)
            all_states.extend(states)
            all_activity_results.extend(activity_results)
            all_choices.extend(choices)
            all_dispatch.extend(transport["ride_hailing_dispatch"])
            all_vehicle_states.extend(transport["vehicle_end_states"])
            summaries.append(summary)
    return {
        "config": experiment, "formal_config": formal, "inputs": inputs,
        "activity_states": all_states, "activity_results": all_activity_results,
        "mode_choices": all_choices, "ride_hailing_dispatch": all_dispatch,
        "vehicle_end_states": all_vehicle_states, "summary_rows": summaries,
    }
