"""Run the finite 20-percent-off coupon competition experiment."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.agents.coupon_experiment import (
    COUPON_POLICIES, allocate_daily_coupons, allocation_map,
)
from custom.agents.emergence_experiment import (
    DAY_TYPES, build_emergence_activities, load_emergence_config,
    run_emergence_weather, summarize_macro,
)
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import (
    WEATHER_TYPES, load_symmetric_experiment_config,
)


GROUPS = (
    "18-39",
    "40-59",
    "60+_digital",
    "60+_nondigital_assisted",
    "60+_nondigital_unassisted",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    fields.extend(key for row in rows for key in row if key not in fields)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def coupon_group(profile: AgentProfile) -> str:
    if profile.age_group != "60+":
        return profile.age_group
    if profile.digital_access:
        return "60+_digital"
    if profile.family_assistance:
        return "60+_nondigital_assisted"
    return "60+_nondigital_unassisted"


def _main_symmetric_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    symmetric = copy.deepcopy(load_symmetric_experiment_config())
    probability = float(
        config["coupon_experiment"]["main_experiment_ride_hailing_noncapacity_success_probability"]
    )
    for weather in symmetric["transport_success_probability"].values():
        weather["ride_hailing"] = probability
    return symmetric


def run_coupon_policy(
    profiles: list[AgentProfile], activities: list[dict[str, Any]], policy: str,
    *, seed: int, config: Mapping[str, Any], symmetric: Mapping[str, Any],
    transport_config: Mapping[str, Any] | None = None,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    allocations = []
    for day_type in DAY_TYPES:
        allocations.extend(allocate_daily_coupons(
            profiles, policy, day_type, seed=seed, config=config,
        ))
    mapped = allocation_map(allocations)
    activity_results: list[Dict[str, Any]] = []
    leg_results: list[Dict[str, Any]] = []
    requests: list[Dict[str, Any]] = []
    vehicles: list[Dict[str, Any]] = []
    outcomes: list[Dict[str, Any]] = []
    system_state: list[Dict[str, Any]] = []
    pre_feedback: list[Dict[str, Any]] = []
    experiment = config["coupon_experiment"]
    for week in WEATHER_TYPES:
        weather = run_emergence_weather(
            profiles, activities, week, seed=seed,
            bus_frequency_multiplier=float(experiment["fixed_bus_frequency_multiplier"]),
            ride_supply_multiplier=float(experiment["fixed_ride_supply_multiplier"]),
            config=config, symmetric=symmetric, coupon_allocations=mapped,
            transport_config=transport_config,
        )
        activity_results.extend(weather["activity_results"])
        leg_results.extend(weather["leg_results"])
        requests.extend(weather["ride_hailing_requests"])
        vehicles.extend({**row, "weather_week": week} for row in weather["ride_hailing_vehicle_states"])
        outcomes.extend(weather["coupon_outcomes"])
        system_state.extend(weather["system_state"])
        pre_feedback.extend(weather["pre_feedback_system_state"])
    result = {
        "seed": seed, "profiles": profiles, "activities": activities,
        "activity_results": activity_results, "leg_results": leg_results,
        "ride_hailing_requests": requests,
        "ride_hailing_vehicle_states": vehicles,
        "coupon_outcomes": outcomes,
        "system_state": system_state,
        "pre_feedback_system_state": pre_feedback,
    }
    return result, allocations


def summarize_coupon_system(
    result: Mapping[str, Any], allocations: Iterable[Mapping[str, Any]], policy: str,
) -> list[Dict[str, Any]]:
    allocation_rows = list(allocations)
    output = []
    for macro in summarize_macro(result):
        week, day_type = macro["weather_week"], macro["day_type"]
        daily = [row for row in allocation_rows if row["day_type"] == day_type]
        outcomes = [
            row for row in result["coupon_outcomes"]
            if row["weather_week"] == week and row["day_type"] == day_type
        ]
        requests = [
            row for row in result["ride_hailing_requests"]
            if row["weather_week"] == week and row["day_type"] == day_type
        ]
        output.append({
            "policy": policy, **macro,
            "coupon_reached": sum(bool(row["coupon_reached"]) for row in daily),
            "coupon_participated": sum(bool(row["coupon_participated"]) for row in daily),
            "coupon_awarded": sum(bool(row["coupon_awarded"]) for row in daily),
            "coupon_redeemed": sum(bool(row["coupon_redeemed"]) for row in outcomes),
            "coupon_expired_after_failed_request": sum(
                row["coupon_status"] == "expired_after_failed_request" for row in outcomes
            ),
            "coupon_unused_no_ride_request": sum(
                row["coupon_status"] == "unused_no_ride_request" for row in outcomes
            ),
            "coupon_induced_requests": sum(bool(row["coupon_induced_request"]) for row in requests),
            "coupon_subsidy_yuan": round(sum(float(row["coupon_subsidy_yuan"]) for row in outcomes), 2),
        })
    return output


def summarize_coupon_groups(
    result: Mapping[str, Any], allocations: Iterable[Mapping[str, Any]], policy: str,
) -> list[Dict[str, Any]]:
    profiles = {profile.agent_id: profile for profile in result["profiles"]}
    allocation_rows = list(allocations)
    output = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            for group in GROUPS:
                agent_ids = {agent_id for agent_id, profile in profiles.items() if coupon_group(profile) == group}
                daily = [row for row in allocation_rows if row["day_type"] == day_type and row["agent_id"] in agent_ids]
                activities = [
                    row for row in result["activity_results"]
                    if row["weather_week"] == week and row["day_type"] == day_type
                    and row["agent_id"] in agent_ids
                ]
                necessary = [row for row in activities if row["necessary_activity"]]
                legs = [
                    row for row in result["leg_results"]
                    if row["weather_week"] == week and row["day_type"] == day_type
                    and row["agent_id"] in agent_ids
                ]
                requests = [
                    row for row in result["ride_hailing_requests"]
                    if row["weather_week"] == week and row["day_type"] == day_type
                    and row["agent_id"] in agent_ids
                ]
                outcomes = [
                    row for row in result["coupon_outcomes"]
                    if row["weather_week"] == week and row["day_type"] == day_type
                    and row["agent_id"] in agent_ids
                ]
                successful = [row for row in requests if row["succeeded"]]
                output.append({
                    "seed": result["seed"], "policy": policy,
                    "weather_week": week, "day_type": day_type,
                    "group": group, "agent_count": len(agent_ids),
                    "coupon_reached": sum(bool(row["coupon_reached"]) for row in daily),
                    "coupon_participated": sum(bool(row["coupon_participated"]) for row in daily),
                    "coupon_awarded": sum(bool(row["coupon_awarded"]) for row in daily),
                    "coupon_redeemed": sum(bool(row["coupon_redeemed"]) for row in outcomes),
                    "coupon_subsidy_yuan": round(sum(float(row["coupon_subsidy_yuan"]) for row in outcomes), 2),
                    "coupon_induced_requests": sum(bool(row["coupon_induced_request"]) for row in requests),
                    "ride_hailing_requests": len(requests),
                    "successful_ride_hailing_requests": len(successful),
                    "failed_ride_hailing_requests": len(requests) - len(successful),
                    "mean_ride_hailing_wait_minutes": round(
                        sum(float(row["pickup_wait_min"]) for row in requests) / len(requests), 6
                    ) if requests else 0.0,
                    "fallback_attempts": sum(bool(row["fallback_used"]) for row in legs),
                    "fallback_successes": sum(bool(row["fallback_success"]) for row in legs),
                    "transport_unmet": sum(bool(row["transport_related_unmet"]) for row in activities),
                    "necessary_transport_unmet": sum(bool(row["necessary_transport_related_unmet"]) for row in activities),
                    "necessary_activity_completion_rate": round(
                        sum(bool(row["activity_completed"]) for row in necessary) / len(necessary), 6
                    ) if necessary else 1.0,
                    "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in activities), 6),
                    "total_heat_hazard_dose_c_min": round(sum(float(row["heat_hazard_dose_c_min"]) for row in activities), 6),
                    "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in activities), 6),
                })
    return output


def _distribution(
    rows: Iterable[Mapping[str, Any]], keys: tuple[str, ...], metrics: tuple[str, ...],
) -> list[Dict[str, Any]]:
    grouped: Dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output = []
    for group_key, group_rows in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0]))):
        for metric in metrics:
            values = [float(row[metric]) for row in group_rows]
            output.append({
                **dict(zip(keys, group_key)), "metric": metric,
                "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "median": round(statistics.median(values), 6),
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            })
    return output


def _consistency_checks(
    result: Mapping[str, Any], allocations: Iterable[Mapping[str, Any]], policy: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    allocations = list(allocations)
    pool = int(config["coupon_experiment"]["daily_total_coupon_pool"])
    pool_ok = all(
        sum(row["coupon_awarded"] for row in allocations if row["day_type"] == day_type) <= pool
        for day_type in DAY_TYPES
    )
    one_award = len({(row["agent_id"], row["day_type"]) for row in allocations if row["coupon_awarded"]}) == sum(
        row["coupon_awarded"] for row in allocations
    )
    one_redemption = all(
        count <= 1 for count in Counter(
            (row["weather_week"], row["day_type"], row["agent_id"])
            for row in result["coupon_outcomes"] if row["coupon_redeemed"]
        ).values()
    )
    public_exclusion = all(
        not row["public_coupon_participated"]
        for row in allocations if row["nondigital_unassisted"]
    )
    vehicle_count_ok = True
    no_overlap = True
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            expected = sum(config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"][day_type].values())
            states = [row for row in result["ride_hailing_vehicle_states"] if row["weather_week"] == week and row["day_type"] == day_type]
            vehicle_count_ok &= len(states) == expected
            by_vehicle: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for request in result["ride_hailing_requests"]:
                if request["weather_week"] == week and request["day_type"] == day_type and request["succeeded"]:
                    by_vehicle[request["vehicle_id"]].append(request)
            for requests in by_vehicle.values():
                requests.sort(key=lambda row: float(row["busy_start"]))
                no_overlap &= all(
                    float(right["busy_start"]) >= float(left["busy_until"])
                    for left, right in zip(requests, requests[1:])
                )
    return {
        "seed": result["seed"], "policy": policy,
        "coupon_pool_limit_passed": pool_ok,
        "one_coupon_per_agent_day_passed": one_award,
        "one_redemption_per_agent_day_weather_passed": one_redemption,
        "nondigital_unassisted_public_exclusion_passed": public_exclusion,
        "vehicle_total_conserved": vehicle_count_ok,
        "vehicle_assignments_nonoverlapping": no_overlap,
        "passed": all((pool_ok, one_award, one_redemption, public_exclusion, vehicle_count_ok, no_overlap)),
    }


def run_coupon_experiment(
    *, seed_start: int, seed_count: int, output: Path,
    config: Mapping[str, Any] | None = None,
    transport_config: Mapping[str, Any] | None = None,
) -> Dict[str, list[Dict[str, Any]]]:
    config = config or load_emergence_config()
    symmetric = _main_symmetric_config(config)
    system_rows: list[Dict[str, Any]] = []
    group_rows: list[Dict[str, Any]] = []
    allocation_rows: list[Dict[str, Any]] = []
    outcome_rows: list[Dict[str, Any]] = []
    request_rows: list[Dict[str, Any]] = []
    activity_rows: list[Dict[str, Any]] = []
    checks: list[Dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        profiles = assign_two_zone_homes(
            generate_population_agents(int(config["total_agents"]), seed=seed), seed=seed,
            s2_share=float(symmetric["s2_home_share"]),
        )
        activities = build_emergence_activities(profiles, seed=seed, config=config, symmetric=symmetric)
        for policy in COUPON_POLICIES:
            result, allocations = run_coupon_policy(
                profiles, activities, policy, seed=seed, config=config, symmetric=symmetric,
                transport_config=transport_config,
            )
            system_rows.extend(summarize_coupon_system(result, allocations, policy))
            group_rows.extend(summarize_coupon_groups(result, allocations, policy))
            allocation_rows.extend({"seed": seed, **row} for row in allocations)
            outcome_rows.extend({"seed": seed, "policy": policy, **row} for row in result["coupon_outcomes"])
            request_rows.extend({"seed": seed, "policy": policy, **row} for row in result["ride_hailing_requests"])
            activity_rows.extend({"seed": seed, "policy": policy, **row} for row in result["activity_results"])
            checks.append(_consistency_checks(result, allocations, policy, config))

    system_metrics = (
        "coupon_reached", "coupon_participated", "coupon_awarded", "coupon_redeemed",
        "coupon_induced_requests", "coupon_subsidy_yuan", "ride_hailing_requests",
        "successful_ride_hailing_requests", "failed_ride_hailing_requests",
        "fallback_attempts", "transport_related_unmet", "necessary_activity_completion_rate",
        "total_outdoor_exposure_minutes", "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
    )
    group_metrics = (
        "coupon_reached", "coupon_participated", "coupon_awarded", "coupon_redeemed",
        "coupon_induced_requests", "coupon_subsidy_yuan", "ride_hailing_requests",
        "successful_ride_hailing_requests", "failed_ride_hailing_requests",
        "mean_ride_hailing_wait_minutes", "fallback_attempts", "transport_unmet",
        "necessary_activity_completion_rate", "total_outdoor_exposure_minutes",
        "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
    )
    system_distribution = _distribution(
        system_rows, ("policy", "weather_week", "day_type"), system_metrics,
    )
    group_distribution = _distribution(
        group_rows, ("policy", "weather_week", "day_type", "group"), group_metrics,
    )
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "system_per_seed": system_rows,
        "group_per_seed": group_rows,
        "system_distribution": system_distribution,
        "group_distribution": group_distribution,
        "coupon_allocation_roster": allocation_rows,
        "coupon_outcomes": outcome_rows,
        "ride_hailing_request_audit": request_rows,
        "activity_results": activity_rows,
        "consistency_checks": checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            "seed_start": seed_start, "seed_count": seed_count,
            "total_agents": config["total_agents"],
            "coupon_experiment": config["coupon_experiment"],
            "common_agents_activities_weather_fleet_seed_dispatch_priority": True,
            "ride_hailing_noncapacity_failure_disabled": True,
            "interpretation": "Mechanism experiment, not a calibrated Shanghai policy forecast.",
        }, handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/coupon_competition_50_agents_30")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int, default=30)
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    tables = run_coupon_experiment(
        seed_start=seed_start, seed_count=args.seed_count,
        output=Path(args.output), config=config,
    )
    checks = tables["consistency_checks"]
    print(f"Completed {len(tables['system_per_seed'])} policy-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
