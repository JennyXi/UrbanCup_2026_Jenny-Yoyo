"""Fifty-agent two-zone experiment built from the main T1 population rules."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.envs import weather
from custom.agents.simple_mode_choice import (
    SimpleAgent,
    calculate_ride_hailing_feedback_wait,
    choose_mode,
    load_simple_config,
)


AGE_VALUE_OF_TIME = {"18-39": 35.0, "40-59": 30.0, "60+": 20.0}
BUS_CAPACITY_PASSENGERS = 50


def _stable_fraction(seed: int, *parts: Any) -> float:
    payload = "|".join(map(str, (seed, *parts))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / 2**64


def assign_two_zone_homes(
    profiles: Iterable[AgentProfile], *, seed: int, s2_share: float = 0.60
) -> list[AgentProfile]:
    """Assign exact, age-stratified S1/S2 totals with a stable ranking."""
    if not 0 <= s2_share <= 1:
        raise ValueError("s2_share must be in [0, 1]")
    grouped: Dict[str, list[AgentProfile]] = defaultdict(list)
    for profile in profiles:
        grouped[profile.age_group].append(profile)
    assigned = []
    for age_group in ("18-39", "40-59", "60+"):
        rows = sorted(
            grouped[age_group],
            key=lambda row: (_stable_fraction(seed, "home", row.agent_id), row.agent_id),
        )
        s2_count = int(len(rows) * s2_share + 0.5)
        s2_ids = {row.agent_id for row in rows[:s2_count]}
        for row in rows:
            row.home_zone = "S2" if row.agent_id in s2_ids else "S1"
            assigned.append(row)
    return sorted(assigned, key=lambda row: row.agent_id)


def _is_worker(profile: AgentProfile) -> bool:
    return profile.work_status in {"regular_worker", "part_time_worker"}


def build_experiment_trips(profiles: Iterable[AgentProfile], *, seed: int) -> list[Dict[str, Any]]:
    """Create one outbound and one return leg per agent for a representative day."""
    trips = []
    for profile in profiles:
        if _is_worker(profile):
            destination = "S1"
            purpose = "work"
        else:
            destination = "S1" if _stable_fraction(seed, "destination", profile.agent_id) < 0.45 else "S2"
            purpose = "non_work"
        outbound = {
            "trip_id": f"A{profile.agent_id:03d}-OUT",
            "agent_id": str(profile.agent_id),
            "origin_zone": profile.home_zone,
            "destination_zone": destination,
            "direction": "outbound",
            "purpose": purpose,
        }
        returned = {
            "trip_id": f"A{profile.agent_id:03d}-RET",
            "agent_id": str(profile.agent_id),
            "origin_zone": destination,
            "destination_zone": profile.home_zone,
            "direction": "return",
            "purpose": purpose,
        }
        trips.extend((outbound, returned))
    return trips


def evaluate_activity_cancellations(
    profiles: list[AgentProfile], weather_week: str, *, seed: int, config: Dict[str, Any]
) -> list[Dict[str, Any]]:
    """Reuse T2 once per Agent; a cancelled activity removes both associated legs."""
    parameters = config["activity_cancellation"]
    weather.set_week(weather_week)
    weather.set_scenario_level(parameters["scenario_level"])
    weather.init_rng(seed)
    if weather_week == "W2":
        weather.set_w2_windows([tuple(window) for window in parameters["w2_windows"]])
    decisions = []
    for profile in profiles:
        purpose = "work" if _is_worker(profile) else "shopping"
        timing = parameters["work_outbound"] if purpose == "work" else parameters["non_work_outbound"]
        activity = {
            "agent_id": profile.agent_id,
            "activity_id": f"A{profile.agent_id:03d}-ACT",
            "day_of_week": parameters["simulation_day"],
            "activity_purpose": purpose,
            "planned_outbound_departure": timing["departure"],
            "planned_activity_arrival": timing["arrival"],
        }
        t2_profile = {
            "age_group": profile.age_group,
            "mobility_constraint": parameters["mobility_constraint_by_age"][profile.age_group],
            "schedule_flexibility": parameters["schedule_flexibility_by_work_status"][profile.work_status],
        }
        decision = weather.evaluate_planned_activity(
            activity, t2_profile, scenario_level=parameters["scenario_level"], seed=seed
        )
        decisions.append({
            "weather_week": weather_week,
            "weather_type": decision["weather_type"],
            "agent_id": profile.agent_id,
            "activity_id": activity["activity_id"],
            "activity_purpose": purpose,
            "outbound_weather_exposed": decision["outbound_weather_exposed"],
            "p_weather_cancel": decision["p_weather_cancel"],
            "weather_random_draw": decision["weather_random_draw"],
            "weather_cancelled": decision["weather_cancelled"],
            "activity_executes": decision["activity_executes"],
            "cancelled_leg_count": 0 if decision["activity_executes"] else 2,
            "unmet_mandatory_trip": decision["unmet_mandatory_trip"],
        })
    return decisions


def run_weather_scenario(
    profiles: list[AgentProfile], trips: list[Dict[str, Any]], weather_week: str, *, seed: int
) -> Dict[str, list[Dict[str, Any]]]:
    profile_by_id = {str(profile.agent_id): profile for profile in profiles}
    config = load_simple_config()
    activity_decisions = evaluate_activity_cancellations(
        profiles, weather_week, seed=seed, config=config
    )
    executing_agent_ids = {
        str(row["agent_id"]) for row in activity_decisions if row["activity_executes"]
    }
    retained_trips = [trip for trip in trips if trip["agent_id"] in executing_agent_ids]
    agents = {
        agent_id: SimpleAgent(
            agent_id=agent_id,
            age_group=profile.age_group,
            home_zone=str(profile.home_zone),
            digital_access=bool(profile.digital_access),
            value_of_time_yuan_per_hour=AGE_VALUE_OF_TIME[profile.age_group],
            family_assistance=bool(profile.family_assistance),
        )
        for agent_id, profile in profile_by_id.items()
    }
    first_round = {
        trip["trip_id"]: choose_mode(agents[trip["agent_id"]], trip, weather_week, seed=seed)
        for trip in retained_trips
    }
    initial_requests = sum(
        decision["chosen_mode"] == "ride_hailing" for decision in first_round.values()
    )
    feedback_wait = calculate_ride_hailing_feedback_wait(initial_requests, config=config)
    results = []
    for trip in retained_trips:
        profile = profile_by_id[trip["agent_id"]]
        decision = choose_mode(
            agents[trip["agent_id"]], trip, weather_week, seed=seed, config=config,
            ride_hailing_extra_wait_min=feedback_wait,
        )
        chosen = next(row for row in decision["alternatives"] if row["mode"] == decision["chosen_mode"])
        ride_option = next(
            (row for row in decision["alternatives"] if row["mode"] == "ride_hailing"), None
        )
        results.append({
            "weather_week": weather_week,
            "weather_type": decision["weather_type"],
            "agent_id": profile.agent_id,
            "age_group": profile.age_group,
            "work_status": profile.work_status,
            "is_elder": profile.is_elder,
            "digital_access": profile.digital_access,
            "family_assistance": profile.family_assistance,
            "medical_need_level": profile.medical_need_level,
            "home_zone": profile.home_zone,
            "trip_id": trip["trip_id"],
            "direction": trip["direction"],
            "purpose": trip["purpose"],
            "origin_zone": trip["origin_zone"],
            "destination_zone": trip["destination_zone"],
            "chosen_mode": decision["chosen_mode"],
            "first_round_chosen_mode": first_round[trip["trip_id"]]["chosen_mode"],
            "first_round_ride_hailing_requests": initial_requests,
            "ride_hailing_feedback_wait_min": feedback_wait,
            "ride_hailing_total_wait_min": ride_option["wait_time_min"] if ride_option else "",
            "distance_km": chosen["distance_km"],
            "travel_time_min": decision["chosen_time_min"],
            "fare_yuan": decision["chosen_fare_yuan"],
            "bus_coverage_rate": chosen["service_coverage_rate"] if chosen["mode"] == "bus" else "",
            "chosen_utility": chosen["utility"],
        })
    return {"trip_decisions": results, "activity_decisions": activity_decisions}


def summarize_system(
    rows: list[Dict[str, Any]], activity_decisions: list[Dict[str, Any]], planned_trip_count: int
) -> Dict[str, Any]:
    modes = Counter(row["chosen_mode"] for row in rows)
    total = len(rows)
    ride_rows = [row for row in rows if row["chosen_mode"] == "ride_hailing"]
    bus_rows = [row for row in rows if row["chosen_mode"] == "bus"]
    cross_zone_bus = [row for row in bus_rows if row["origin_zone"] != row["destination_zone"]]
    cross_by_direction = Counter(row["direction"] for row in cross_zone_bus)
    peak_bus_passengers = max(cross_by_direction.values(), default=0)
    return {
        "weather_week": rows[0]["weather_week"],
        "weather_type": rows[0]["weather_type"],
        "agent_count": len({row["agent_id"] for row in rows}),
        "planned_agent_count": len(activity_decisions),
        "planned_trip_count": planned_trip_count,
        "trip_count": total,
        "cancelled_activity_count": sum(row["weather_cancelled"] for row in activity_decisions),
        "cancelled_trip_count": sum(row["cancelled_leg_count"] for row in activity_decisions),
        "unmet_mandatory_activity_count": sum(row["unmet_mandatory_trip"] for row in activity_decisions),
        "mode_trip_counts": {mode: modes.get(mode, 0) for mode in ("walk", "bus", "ride_hailing")},
        "mode_shares": {mode: round(modes.get(mode, 0) / total, 4) for mode in ("walk", "bus", "ride_hailing")},
        "average_travel_time_min": round(sum(row["travel_time_min"] for row in rows) / total, 3),
        "average_fare_yuan": round(sum(row["fare_yuan"] for row in rows) / total, 3),
        "bus_passenger_trips": len(bus_rows),
        "cross_zone_bus_passengers_peak_direction": peak_bus_passengers,
        "bus_capacity_assumption_passengers": BUS_CAPACITY_PASSENGERS,
        "cross_zone_bus_peak_load_ratio": round(peak_bus_passengers / BUS_CAPACITY_PASSENGERS, 4),
        "ride_hailing_vehicle_trips": len(ride_rows),
        "ride_hailing_demand": len(ride_rows),
        "first_round_ride_hailing_requests": rows[0]["first_round_ride_hailing_requests"],
        "ride_hailing_feedback_wait_min": rows[0]["ride_hailing_feedback_wait_min"],
        "average_ride_hailing_wait_min": round(
            sum(float(row["ride_hailing_total_wait_min"]) for row in ride_rows) / len(ride_rows), 3
        ) if ride_rows else 0.0,
        "ride_hailing_vehicle_km": round(sum(row["distance_km"] for row in ride_rows), 3),
        "additional_road_flow_pcu_per_representative_day": len(ride_rows),
        "t10_note": "Not PCU/hour: aggregate by direction/time bin before passing to T10.",
    }


def run_experiment(total_agents: int = 50, *, seed: int = 2026, s2_share: float = 0.60) -> Dict[str, Any]:
    profiles = assign_two_zone_homes(
        generate_population_agents(total_agents, seed=seed), seed=seed, s2_share=s2_share
    )
    trips = build_experiment_trips(profiles, seed=seed)
    decisions = []
    activity_decisions = []
    summaries = []
    for weather_week in ("W0", "W1", "W2"):
        scenario = run_weather_scenario(profiles, trips, weather_week, seed=seed)
        rows = scenario["trip_decisions"]
        decisions.extend(rows)
        activity_decisions.extend(scenario["activity_decisions"])
        summaries.append(summarize_system(rows, scenario["activity_decisions"], len(trips)))
    population = {
        "total_agents": len(profiles),
        "age_group_counts": dict(sorted(Counter(row.age_group for row in profiles).items())),
        "home_zone_counts": dict(sorted(Counter(row.home_zone for row in profiles).items())),
        "work_status_counts": dict(sorted(Counter(str(row.work_status) for row in profiles).items())),
        "elder_digital_access_counts": dict(sorted(Counter(
            "digital" if row.digital_access else "non_digital" for row in profiles if row.is_elder
        ).items())),
    }
    return {
        "population": population,
        "activity_decisions": activity_decisions,
        "decisions": decisions,
        "system_summaries": summaries,
    }


def write_experiment_outputs(result: Dict[str, Any], output_dir: Path | str) -> Dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    decisions_path = output / "agent_mode_choices.csv"
    summaries_path = output / "system_impact_summary.csv"
    activities_path = output / "activity_weather_decisions.csv"
    metadata_path = output / "experiment_summary.json"
    with decisions_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["decisions"][0]))
        writer.writeheader()
        writer.writerows(result["decisions"])
    with summaries_path.open("w", encoding="utf-8-sig", newline="") as handle:
        flattened = []
        for summary in result["system_summaries"]:
            row = {key: value for key, value in summary.items() if key not in {"mode_trip_counts", "mode_shares"}}
            for mode, value in summary["mode_trip_counts"].items():
                row[f"{mode}_trips"] = value
            for mode, value in summary["mode_shares"].items():
                row[f"{mode}_share"] = value
            flattened.append(row)
        writer = csv.DictWriter(handle, fieldnames=list(flattened[0]))
        writer.writeheader()
        writer.writerows(flattened)
    with activities_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["activity_decisions"][0]))
        writer.writeheader()
        writer.writerows(result["activity_decisions"])
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump({"population": result["population"], "system_summaries": result["system_summaries"]}, handle, ensure_ascii=False, indent=2)
    return {
        "activities": activities_path,
        "choices": decisions_path,
        "impact": summaries_path,
        "summary": metadata_path,
    }
