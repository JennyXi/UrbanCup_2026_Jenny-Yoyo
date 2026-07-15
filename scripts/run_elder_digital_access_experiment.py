"""Run the independent 50-agent elder digital-access mechanism experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.agents.emergence_experiment import (
    DAY_TYPES, build_emergence_activities, load_emergence_config,
    run_emergence_weather, summarize_macro,
)
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import (
    WEATHER_TYPES, load_symmetric_experiment_config,
)


NECESSARY_PURPOSES = {"work", "medical"}
GROUPS = (
    "elder_baseline_digital",
    "elder_baseline_nondigital_assisted",
    "elder_baseline_nondigital_unassisted",
    "under_60",
)
SYSTEM_METRICS = (
    "activity_completion_rate", "necessary_activity_completion_rate",
    "transport_related_unmet", "necessary_transport_related_unmet",
    "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
    "fallback_attempts", "fallback_successes", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "total_bus_wait_minutes", "total_ride_hailing_wait_minutes",
    "total_system_wait_minutes", "mean_ride_hailing_wait_minutes_per_request",
    "mean_total_travel_time", "total_travel_time_minutes",
    "total_in_vehicle_time_minutes", "total_fare_yuan",
    "road_vehicle_volume", "peak_road_volume_capacity_ratio",
    "minimum_road_speed_multiplier", "total_outdoor_exposure_minutes",
    "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
    "necessary_heat_risk_burden",
)
GROUP_METRICS = (
    "planned_activities", "completed_activities", "activity_completion_rate",
    "planned_necessary_activities", "completed_necessary_activities",
    "necessary_activity_completion_rate", "weather_cancelled_activities",
    "transport_related_unmet", "necessary_transport_related_unmet",
    "actual_legs",
    "walking_legs", "bus_legs", "ride_hailing_legs",
    "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "fallback_attempts", "fallback_successes",
    "total_bus_wait_minutes", "total_ride_hailing_wait_minutes",
    "total_travel_time_minutes", "mean_total_travel_time",
    "total_fare_yuan", "total_outdoor_exposure_minutes",
    "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _rank(seed: int, agent_id: int, stage: str) -> int:
    value = f"{seed}|{agent_id}|elder-digital-access|{stage}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def baseline_group(profile: AgentProfile) -> str:
    if not profile.is_elder:
        return "under_60"
    if profile.digital_access:
        return "elder_baseline_digital"
    if profile.family_assistance:
        return "elder_baseline_nondigital_assisted"
    return "elder_baseline_nondigital_unassisted"


def apply_digital_policy(
    profiles: Iterable[AgentProfile], policy_name: str, *, seed: int,
    config: Mapping[str, Any],
) -> list[AgentProfile]:
    """Apply only the configured elder access intervention to copied profiles."""
    base = list(profiles)
    policy = config["elder_digital_access_experiment"]["policies"][policy_name]
    changed = [replace(profile) for profile in base]
    elders = [profile for profile in changed if profile.is_elder]

    digital_target = policy["elder_digital_access_target"]
    if digital_target is not None:
        target_count = int(len(elders) * float(digital_target) + 0.5)
        current_count = sum(profile.digital_access for profile in elders)
        candidates = [profile for profile in elders if not profile.digital_access]
        if not policy["provide_smartphone_if_needed"]:
            candidates = [profile for profile in candidates if profile.smartphone_access]
        candidates.sort(key=lambda profile: (
            bool(profile.family_assistance)
            if policy["prioritize_unassisted_for_training"] else False,
            _rank(seed, profile.agent_id, policy_name),
        ))
        if current_count + len(candidates) < target_count:
            raise ValueError(f"{policy_name} digital target exceeds eligible elders")
        for profile in candidates[:max(0, target_count - current_count)]:
            if policy["provide_smartphone_if_needed"]:
                profile.smartphone_access = True
            profile.digital_access = True

    assistance_target = policy["elder_family_assistance_target"]
    if assistance_target is not None:
        target_count = int(len(elders) * float(assistance_target) + 0.5)
        current_count = sum(bool(profile.family_assistance) for profile in elders)
        candidates = sorted(
            (profile for profile in elders if not profile.family_assistance),
            key=lambda profile: _rank(seed, profile.agent_id, policy_name),
        )
        for profile in candidates[:max(0, target_count - current_count)]:
            profile.family_assistance = True
    return changed


def _run_policy(
    base_profiles: list[AgentProfile], activities: list[dict[str, Any]],
    policy: str, *, seed: int, config: Mapping[str, Any],
    symmetric: Mapping[str, Any],
) -> tuple[list[AgentProfile], dict[str, Any]]:
    profiles = apply_digital_policy(base_profiles, policy, seed=seed, config=config)
    activity_results: list[dict[str, Any]] = []
    leg_results: list[dict[str, Any]] = []
    pre_feedback: list[dict[str, Any]] = []
    system_state: list[dict[str, Any]] = []
    experiment = config["elder_digital_access_experiment"]
    for week in WEATHER_TYPES:
        result = run_emergence_weather(
            profiles, activities, week, seed=seed,
            bus_frequency_multiplier=float(experiment["fixed_bus_frequency_multiplier"]),
            ride_supply_multiplier=float(experiment["fixed_ride_supply_multiplier"]),
            config=config, symmetric=symmetric,
        )
        activity_results.extend(result["activity_results"])
        leg_results.extend(result["leg_results"])
        pre_feedback.extend(result["pre_feedback_system_state"])
        system_state.extend(result["system_state"])
    return profiles, {
        "seed": seed, "profiles": profiles, "activities": activities,
        "activity_results": activity_results, "leg_results": leg_results,
        "pre_feedback_system_state": pre_feedback, "system_state": system_state,
    }


def summarize_groups(
    result: Mapping[str, Any], base_profiles: Iterable[AgentProfile], policy: str,
) -> list[dict[str, Any]]:
    base_by_id = {profile.agent_id: profile for profile in base_profiles}
    policy_by_id = {profile.agent_id: profile for profile in result["profiles"]}
    rows: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            for group in GROUPS:
                agent_ids = {
                    agent_id for agent_id, profile in base_by_id.items()
                    if baseline_group(profile) == group
                }
                if not agent_ids:
                    continue
                activities = [row for row in result["activity_results"] if
                              row["weather_week"] == week and row["day_type"] == day_type
                              and row["agent_id"] in agent_ids]
                legs = [row for row in result["leg_results"] if
                        row["weather_week"] == week and row["day_type"] == day_type
                        and row["agent_id"] in agent_ids]
                necessary = [row for row in activities if row["activity_purpose"] in NECESSARY_PURPOSES]
                modes = Counter(row["final_success_mode"] for row in legs if row["final_success_mode"])
                successful = sum(modes.values())
                ride_requests = sum(int(row["ride_hailing_request_count"]) for row in legs)
                ride_success = modes["ride_hailing"]
                total_travel = sum(float(row["cumulative_travel_time_min"]) for row in legs)
                rows.append({
                    "seed": result["seed"], "policy": policy,
                    "weather_scenario": week, "day_type": day_type,
                    "baseline_access_group": group, "agent_count": len(agent_ids),
                    "policy_digital_agent_count": sum(policy_by_id[row].digital_access for row in agent_ids),
                    "policy_assisted_agent_count": sum(bool(policy_by_id[row].family_assistance) for row in agent_ids),
                    "planned_activities": len(activities),
                    "completed_activities": sum(row["activity_completed"] for row in activities),
                    "activity_completion_rate": round(sum(row["activity_completed"] for row in activities) / len(activities), 6) if activities else 1.0,
                    "planned_necessary_activities": len(necessary),
                    "completed_necessary_activities": sum(row["activity_completed"] for row in necessary),
                    "necessary_activity_completion_rate": round(sum(row["activity_completed"] for row in necessary) / len(necessary), 6) if necessary else 1.0,
                    "weather_cancelled_activities": sum(row["weather_cancellation"] for row in activities),
                    "transport_related_unmet": sum(row["transport_related_unmet"] for row in activities),
                    "necessary_transport_related_unmet": sum(row["necessary_transport_related_unmet"] for row in activities),
                    "actual_legs": len(legs),
                    "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                    "ride_hailing_legs": ride_success,
                    "walking_mode_share": round(modes["walk"] / successful, 6) if successful else 0.0,
                    "bus_mode_share": round(modes["bus"] / successful, 6) if successful else 0.0,
                    "ride_hailing_mode_share": round(ride_success / successful, 6) if successful else 0.0,
                    "ride_hailing_requests": ride_requests,
                    "successful_ride_hailing_requests": ride_success,
                    "failed_ride_hailing_requests": ride_requests - ride_success,
                    "fallback_attempts": sum(row["fallback_used"] for row in legs),
                    "fallback_successes": sum(row["fallback_success"] for row in legs),
                    "total_bus_wait_minutes": round(sum(float(row["bus_wait_minutes"]) for row in legs), 6),
                    "total_ride_hailing_wait_minutes": round(sum(float(row["ride_hailing_wait_min"]) for row in legs), 6),
                    "total_travel_time_minutes": round(total_travel, 6),
                    "mean_total_travel_time": round(total_travel / len(legs), 6) if legs else 0.0,
                    "total_fare_yuan": round(sum(float(row["cumulative_fare_yuan"]) for row in legs), 6),
                    "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in activities), 6),
                    "total_heat_hazard_dose_c_min": round(sum(float(row["heat_hazard_dose_c_min"]) for row in activities), 6),
                    "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in activities), 6),
                })
    return rows


def _describe(rows: list[dict[str, Any]], keys: tuple[str, ...], metrics: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(row[key] for key in keys), []).append(row)
    output: list[dict[str, Any]] = []
    for group_key, group_rows in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        for metric in metrics:
            metric_rows = group_rows
            if metric == "activity_completion_rate" and "planned_activities" in group_rows[0]:
                metric_rows = [row for row in group_rows if float(row["planned_activities"]) > 0]
            elif metric == "necessary_activity_completion_rate" and "planned_necessary_activities" in group_rows[0]:
                metric_rows = [row for row in group_rows if float(row["planned_necessary_activities"]) > 0]
            elif metric in {"walking_mode_share", "bus_mode_share", "ride_hailing_mode_share"} and "actual_legs" in group_rows[0]:
                metric_rows = [row for row in group_rows if
                               float(row["walking_legs"]) + float(row["bus_legs"])
                               + float(row["ride_hailing_legs"]) > 0]
            elif metric == "mean_total_travel_time" and "actual_legs" in group_rows[0]:
                metric_rows = [row for row in group_rows if float(row["actual_legs"]) > 0]
            if not metric_rows:
                continue
            values = [float(row[metric]) for row in metric_rows]
            output.append({
                **dict(zip(keys, group_key)), "metric": metric,
                "seed_count_observed": len(values),
                "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "median": round(statistics.median(values), 6),
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            })
    return output


def _policy_changes(distribution: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    lookup = {tuple(row[key] for key in keys) + (row["metric"], row["policy"]): row for row in distribution}
    policies = sorted({row["policy"] for row in distribution if row["policy"] != "D0_baseline"})
    base_keys = sorted({tuple(row[key] for key in keys) + (row["metric"],) for row in distribution}, key=lambda row: tuple(map(str, row)))
    output = []
    for base_key in base_keys:
        prefix, metric = base_key[:-1], base_key[-1]
        baseline = lookup[prefix + (metric, "D0_baseline")]
        old = float(baseline["mean"])
        for policy in policies:
            new = float(lookup[prefix + (metric, policy)]["mean"])
            output.append({
                **dict(zip(keys, prefix)), "policy": policy, "baseline_policy": "D0_baseline",
                "metric": metric, "baseline_mean": old, "policy_mean": new,
                "absolute_change": round(new - old, 6),
                "percent_change": round((new - old) / old * 100.0, 6) if old else "",
                "percent_change_defined": old != 0,
                "undefined_reason": "" if old else "baseline_zero",
            })
    return output


def run_digital_access_experiment(
    *, seed_start: int, seed_count: int, output: Path,
    config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    config = config or load_emergence_config()
    symmetric = load_symmetric_experiment_config()
    policies = tuple(config["elder_digital_access_experiment"]["policies"])
    system_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    roster: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        base_profiles = assign_two_zone_homes(
            generate_population_agents(int(config["total_agents"]), seed=seed), seed=seed,
            s2_share=float(symmetric["s2_home_share"]),
        )
        activities = build_emergence_activities(base_profiles, seed=seed, config=config, symmetric=symmetric)
        base_by_id = {profile.agent_id: profile for profile in base_profiles}
        results: dict[str, dict[str, Any]] = {}
        for policy in policies:
            profiles, result = _run_policy(
                base_profiles, activities, policy, seed=seed, config=config, symmetric=symmetric,
            )
            results[policy] = result
            system_rows.extend({
                "policy": policy, **{key: row[key] for key in row},
            } for row in summarize_macro(result))
            group_rows.extend(summarize_groups(result, base_profiles, policy))
            for profile in profiles:
                if not profile.is_elder:
                    continue
                base = base_by_id[profile.agent_id]
                roster.append({
                    "seed": seed, "policy": policy, "agent_id": profile.agent_id,
                    "baseline_access_group": baseline_group(base),
                    "baseline_smartphone_access": base.smartphone_access,
                    "baseline_digital_access": base.digital_access,
                    "baseline_family_assistance": base.family_assistance,
                    "policy_smartphone_access": profile.smartphone_access,
                    "policy_digital_access": profile.digital_access,
                    "policy_family_assistance": profile.family_assistance,
                    "newly_independent_digital": profile.digital_access and not base.digital_access,
                    "newly_assisted": bool(profile.family_assistance) and not bool(base.family_assistance),
                })
        baseline_cancel = {
            (row["activity_id"], row["weather_week"]): row["weather_cancellation"]
            for row in results["D0_baseline"]["activity_results"]
        }
        for policy, result in results.items():
            policy_profiles = {profile.agent_id: profile for profile in result["profiles"]}
            blocked = {agent_id for agent_id, profile in policy_profiles.items()
                       if not profile.digital_access and not profile.family_assistance}
            illegal_ride = sum(
                any(row[field] == "ride_hailing" for field in
                    ("pre_feedback_mode", "initial_mode", "fallback_mode", "final_success_mode"))
                for row in result["leg_results"] if row["agent_id"] in blocked
            )
            cancellation_changes = sum(
                baseline_cancel[(row["activity_id"], row["weather_week"])] != row["weather_cancellation"]
                for row in result["activity_results"]
            )
            nonelder_changes = sum(
                profile.to_dict() != base_by_id[agent_id].to_dict()
                for agent_id, profile in policy_profiles.items() if not profile.is_elder
            )
            checks.append({
                "seed": seed, "policy": policy,
                "planned_activity_count_unchanged": len(result["activities"]) == len(activities),
                "weather_cancellation_changes_vs_d0": cancellation_changes,
                "nondigital_unassisted_illegal_ride_legs": illegal_ride,
                "nonelder_profile_changes": nonelder_changes,
                "passed": cancellation_changes == 0 and illegal_ride == 0 and nonelder_changes == 0,
            })

    system_distribution = _describe(
        system_rows, ("policy", "weather_week", "day_type"), SYSTEM_METRICS,
    )
    group_distribution = _describe(
        group_rows, ("policy", "weather_scenario", "day_type", "baseline_access_group"), GROUP_METRICS,
    )
    system_changes = _policy_changes(system_distribution, ("weather_week", "day_type"))
    group_changes = _policy_changes(
        group_distribution, ("weather_scenario", "day_type", "baseline_access_group"),
    )
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "system_per_seed": system_rows, "group_per_seed": group_rows,
        "system_distribution": system_distribution, "group_distribution": group_distribution,
        "system_policy_changes": system_changes, "group_policy_changes": group_changes,
        "intervention_roster": roster, "consistency_checks": checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            "seed_start": seed_start, "seed_count": seed_count, "total_agents": config["total_agents"],
            "policies": config["elder_digital_access_experiment"],
            "baseline_groups_are_fixed_before_intervention": True,
            "interpretation": "Mechanism experiment, not a calibrated Shanghai impact forecast.",
        }, handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/elder_digital_access_30")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int, default=30)
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    result = run_digital_access_experiment(
        seed_start=seed_start, seed_count=args.seed_count, output=Path(args.output), config=config,
    )
    checks = result["consistency_checks"]
    print(f"Completed {len(result['system_per_seed'])} policy-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
