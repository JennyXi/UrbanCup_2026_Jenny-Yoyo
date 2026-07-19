"""Run paired D0-D4 elder access policies in the formal nine-zone model."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.agent_population import AgentProfile  # noqa: E402
from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)
from scripts.run_elder_digital_access_experiment import (  # noqa: E402
    apply_digital_policy,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_elder_digital_access.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50_elder_digital_access"
GROUPS = (
    "18-39", "40-59", "elder_baseline_digital",
    "elder_baseline_nondigital_assisted", "elder_baseline_nondigital_unassisted",
)
SYSTEM_METRICS = (
    "elder_digital_count", "elder_assisted_count", "elder_community_proxy_count",
    "newly_digital_elder_count", "newly_assisted_elder_count",
    "ride_hailing_requests", "successful_ride_hailing_requests", "ride_hailing_failed",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts",
    "transport_unmet", "mandatory_activity_incomplete",
    "necessary_activity_completion_rate", "walking_mode_share", "bus_mode_share",
    "metro_mode_share", "ride_hailing_mode_share", "road_vehicle_volume",
    "mean_total_travel_time", "total_system_wait_minutes",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)
GROUP_METRICS = (
    "policy_digital_agent_count", "policy_assisted_agent_count", "policy_proxy_agent_count",
    "ride_hailing_requests", "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts", "transport_unmet",
    "planned_necessary_activities", "completed_necessary_activities",
    "mandatory_activity_incomplete", "necessary_activity_completion_rate",
    "walking_legs", "bus_legs", "metro_legs", "ride_hailing_legs",
    "mean_total_travel_time", "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _profile(row: Mapping[str, Any]) -> AgentProfile:
    return AgentProfile(**dict(row))


def _baseline_group(profile: AgentProfile) -> str:
    if not profile.is_elder:
        return profile.age_group
    if profile.digital_access:
        return "elder_baseline_digital"
    if profile.family_assistance:
        return "elder_baseline_nondigital_assisted"
    return "elder_baseline_nondigital_unassisted"


def _uniform(seed: int, agent_id: int, policy: str) -> float:
    payload = f"{seed}|{agent_id}|{policy}|community-phone-coverage".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / 2**64


def _community_proxy_ids(
    profiles: Iterable[AgentProfile], policy: str, *, seed: int,
    config: Mapping[str, Any],
) -> set[int]:
    rate = float(
        config["elder_digital_access_experiment"]["policies"][policy]
        ["community_phone_coverage_rate"]
    )
    return {
        profile.agent_id for profile in profiles
        if profile.is_elder and not profile.digital_access and not profile.family_assistance
        and _uniform(seed, profile.agent_id, policy) < rate
    }


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(statistics.mean(values), 6) if values else 0.0


def _system_row(
    summary: Mapping[str, Any], base_profiles: list[AgentProfile],
    policy_profiles: list[AgentProfile], proxy_ids: set[int],
    choices: list[Mapping[str, Any]], dispatch: list[Mapping[str, Any]], policy: str,
) -> dict[str, Any]:
    weather = summary["weather_scenario"]
    scenario_choices = [row for row in choices if row["weather_scenario"] == weather]
    scenario_dispatch = [row for row in dispatch if row["weather_scenario"] == weather]
    base = {row.agent_id: row for row in base_profiles}
    elders = [row for row in policy_profiles if row.is_elder]
    return {
        **dict(summary), "policy": policy,
        "elder_digital_count": sum(row.digital_access for row in elders),
        "elder_assisted_count": sum(bool(row.family_assistance) for row in elders),
        "elder_community_proxy_count": len(proxy_ids),
        "newly_digital_elder_count": sum(
            row.digital_access and not base[row.agent_id].digital_access for row in elders
        ),
        "newly_assisted_elder_count": sum(
            bool(row.family_assistance) and not bool(base[row.agent_id].family_assistance)
            for row in elders
        ),
        "mean_ride_hailing_wait_minutes_per_request": _mean(
            float(row["pickup_wait_min"]) for row in scenario_dispatch
        ),
        "total_outdoor_exposure_minutes": round(
            sum(float(row["outdoor_exposure_minutes"]) for row in scenario_choices), 6
        ),
        "total_heat_risk_burden": round(
            sum(float(row["heat_risk_burden"]) for row in scenario_choices), 6
        ),
    }


def _group_rows(
    result: Mapping[str, Any], base_profiles: list[AgentProfile],
    policy_profiles: list[AgentProfile], proxy_ids: set[int], policy: str, seed: int,
) -> list[dict[str, Any]]:
    base_groups = {row.agent_id: _baseline_group(row) for row in base_profiles}
    current = {row.agent_id: row for row in policy_profiles}
    output = []
    for weather in ("W0", "W2"):
        activities = [row for row in result["activity_results"] if row["weather_scenario"] == weather]
        choices = [row for row in result["mode_choices"] if row["weather_scenario"] == weather]
        dispatch = [row for row in result["ride_hailing_dispatch"] if row["weather_scenario"] == weather]
        for group in GROUPS:
            ids = {agent_id for agent_id, value in base_groups.items() if value == group}
            group_activities = [row for row in activities if int(row["agent_id"]) in ids]
            necessary = [row for row in group_activities if row["is_mandatory"]]
            group_choices = [row for row in choices if int(row["agent_id"]) in ids]
            successful = [row for row in group_choices if row["transport_succeeded"]]
            modes = Counter(row["final_mode"] for row in successful)
            group_dispatch = [row for row in dispatch if int(row["agent_id"]) in ids]
            output.append({
                "seed": seed, "policy": policy, "weather_scenario": weather,
                "day_type": "workday", "baseline_access_group": group,
                "agent_count": len(ids),
                "policy_digital_agent_count": sum(current[row].digital_access for row in ids),
                "policy_assisted_agent_count": sum(bool(current[row].family_assistance) for row in ids),
                "policy_proxy_agent_count": len(ids & proxy_ids),
                "ride_hailing_requests": len(group_dispatch),
                "successful_ride_hailing_requests": sum(row["succeeded"] for row in group_dispatch),
                "failed_ride_hailing_requests": sum(not row["succeeded"] for row in group_dispatch),
                "mean_ride_hailing_wait_minutes_per_request": _mean(
                    float(row["pickup_wait_min"]) for row in group_dispatch
                ),
                "fallback_attempts": sum(row["fallback_attempted"] for row in group_choices),
                "transport_unmet": sum(row["transport_unmet"] for row in group_activities),
                "planned_necessary_activities": len(necessary),
                "completed_necessary_activities": sum(row["completed"] for row in necessary),
                "mandatory_activity_incomplete": sum(row["mandatory_activity_incomplete"] for row in necessary),
                "necessary_activity_completion_rate": (
                    round(sum(row["completed"] for row in necessary) / len(necessary), 6)
                    if necessary else 1.0
                ),
                "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                "metro_legs": modes["metro"], "ride_hailing_legs": modes["ride_hailing"],
                "mean_total_travel_time": _mean(
                    float(row["total_travel_time_min"]) for row in successful
                ),
                "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in group_choices), 6),
                "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in group_choices), 6),
            })
    return output


def _distributions(
    rows: list[Mapping[str, Any]], policies: Iterable[str], metrics: Iterable[str],
    *, group_key: str | None = None,
) -> list[dict[str, Any]]:
    output = []
    group_values = GROUPS if group_key else (None,)
    for policy in policies:
        for weather in ("W0", "W2"):
            for group_value in group_values:
                selected = [
                    row for row in rows
                    if row["policy"] == policy and row["weather_scenario"] == weather
                    and (group_key is None or row[group_key] == group_value)
                    and (group_key is None or int(row["agent_count"]) > 0)
                ]
                for metric in metrics:
                    observed = selected
                    if metric == "necessary_activity_completion_rate" and group_key:
                        observed = [
                            row for row in observed
                            if int(row["planned_necessary_activities"]) > 0
                        ]
                    elif metric == "mean_ride_hailing_wait_minutes_per_request" and group_key:
                        observed = [
                            row for row in observed if int(row["ride_hailing_requests"]) > 0
                        ]
                    values = [float(row[metric]) for row in observed]
                    if not values:
                        continue
                    result = {
                        "policy": policy, "weather_scenario": weather, "metric": metric,
                        "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                        "median": round(statistics.median(values), 6),
                        "minimum": round(min(values), 6), "maximum": round(max(values), 6),
                    }
                    if group_key:
                        result[group_key] = group_value
                    output.append(result)
    return output


def _changes(distributions: list[Mapping[str, Any]], policies: list[str], *, group_key: str | None = None) -> list[dict[str, Any]]:
    lookup = {
        (row["policy"], row["weather_scenario"], row.get(group_key) if group_key else None, row["metric"]): float(row["mean"])
        for row in distributions
    }
    output = []
    groups = GROUPS if group_key else (None,)
    for policy in policies[1:]:
        for weather in ("W0", "W2"):
            for group in groups:
                metrics = {
                    key[3] for key in lookup
                    if key[0] == "D0_baseline" and key[1] == weather and key[2] == group
                }
                for metric in metrics:
                    old = lookup[("D0_baseline", weather, group, metric)]
                    new = lookup[(policy, weather, group, metric)]
                    row = {
                        "policy": policy, "baseline_policy": "D0_baseline",
                        "weather_scenario": weather, "metric": metric,
                        "baseline_mean": old, "policy_mean": new,
                        "absolute_change": round(new - old, 6),
                        "percent_change": round((new - old) / old * 100.0, 6) if old else None,
                        "percent_change_defined": old != 0,
                        "undefined_reason": "" if old else "baseline_zero",
                    }
                    if group_key:
                        row[group_key] = group
                    output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    args = parser.parse_args()
    config = _load(args.config)
    policies = list(config["elder_digital_access_experiment"]["policies"])
    seed_start = int(config["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(config["seed_count"] if args.seed_count is None else args.seed_count)
    base_config = load_formal_50_config(ROOT / config["base_experiment_config"])
    if sum(config["initial_vehicles"].values()) != 12:
        raise ValueError("digital-access experiment must use the selected 12-vehicle baseline")

    system_rows, group_rows, roster_rows = [], [], []
    choice_rows, dispatch_rows, checks = [], [], []
    for seed in range(seed_start, seed_start + seed_count):
        bootstrap = run_formal_nine_zone_50_experiment(
            config=base_config, seed=seed, weather_scenarios=("W0",), day_types=("workday",),
        )
        paired_inputs = bootstrap["inputs"]
        base_profiles = [_profile(row) for row in paired_inputs["agents"]]
        base_by_id = {row.agent_id: row for row in base_profiles}
        policy_results = {}
        policy_profiles_by_name = {}
        proxy_by_name = {}
        for policy in policies:
            policy_profiles = apply_digital_policy(
                base_profiles, policy, seed=seed, config=config,
            )
            proxy_ids = _community_proxy_ids(
                base_profiles, policy, seed=seed, config=config,
            )
            policy_inputs = copy.deepcopy(paired_inputs)
            policy_inputs["agents"] = [row.to_dict() for row in policy_profiles]
            run_config = copy.deepcopy(base_config)
            run_config["formal_overrides"] = {
                "experiment_condition": f"elder_access_{policy}",
                "ride_hailing_fleet": {
                    "initial_vehicles_by_day_type": {"workday": config["initial_vehicles"]}
                },
                "_ride_hailing_proxy_agent_ids": proxy_ids,
            }
            result = run_formal_nine_zone_50_experiment(
                config=run_config, seed=seed,
                weather_scenarios=tuple(config["weather_scenarios"]),
                day_types=("workday",), paired_inputs=policy_inputs,
            )
            policy_results[policy] = result
            policy_profiles_by_name[policy] = policy_profiles
            proxy_by_name[policy] = proxy_ids

        baseline_cancel = {
            (row["weather_scenario"], row["activity_id"]): row["weather_cancellation"]
            for row in policy_results["D0_baseline"]["activity_results"]
        }
        baseline_priority = {
            (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
            for row in policy_results["D0_baseline"]["ride_hailing_dispatch"]
        }
        for policy in policies:
            result = policy_results[policy]
            policy_profiles = policy_profiles_by_name[policy]
            proxy_ids = proxy_by_name[policy]
            current_by_id = {row.agent_id: row for row in policy_profiles}
            for row in result["mode_choices"]:
                row["policy"] = policy
                row["baseline_access_group"] = _baseline_group(base_by_id[int(row["agent_id"])])
                row["community_phone_proxy"] = int(row["agent_id"]) in proxy_ids
            for row in result["ride_hailing_dispatch"]:
                row["policy"] = policy
            for summary in result["summary_rows"]:
                system_rows.append(_system_row(
                    summary, base_profiles, policy_profiles, proxy_ids,
                    result["mode_choices"], result["ride_hailing_dispatch"], policy,
                ))
            group_rows.extend(_group_rows(
                result, base_profiles, policy_profiles, proxy_ids, policy, seed,
            ))
            for profile in policy_profiles:
                if profile.is_elder:
                    base = base_by_id[profile.agent_id]
                    roster_rows.append({
                        "seed": seed, "policy": policy, "agent_id": profile.agent_id,
                        "baseline_access_group": _baseline_group(base),
                        "baseline_smartphone_access": base.smartphone_access,
                        "baseline_digital_access": base.digital_access,
                        "baseline_family_assistance": base.family_assistance,
                        "policy_smartphone_access": profile.smartphone_access,
                        "policy_digital_access": profile.digital_access,
                        "policy_family_assistance": profile.family_assistance,
                        "community_phone_proxy": profile.agent_id in proxy_ids,
                        "newly_digital": profile.digital_access and not base.digital_access,
                        "newly_assisted": bool(profile.family_assistance) and not bool(base.family_assistance),
                    })
            blocked = {
                row.agent_id for row in policy_profiles
                if not row.digital_access and not row.family_assistance and row.agent_id not in proxy_ids
            }
            illegal_rides = sum(
                row["primary_mode"] == "ride_hailing" or row["final_mode"] == "ride_hailing"
                for row in result["mode_choices"] if int(row["agent_id"]) in blocked
            )
            current_priority = {
                (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
                for row in result["ride_hailing_dispatch"]
            }
            common = set(baseline_priority) & set(current_priority)
            check = {
                "seed": seed, "policy": policy,
                "weather_cancellation_unchanged_passed": all(
                    baseline_cancel[(row["weather_scenario"], row["activity_id"])] == row["weather_cancellation"]
                    for row in result["activity_results"]
                ),
                "nonelder_profiles_unchanged_passed": all(
                    current_by_id[row.agent_id].to_dict() == row.to_dict()
                    for row in base_profiles if not row.is_elder
                ),
                "blocked_agents_cannot_ride_passed": illegal_rides == 0,
                "d4_digital_identity_unchanged_passed": (
                    policy != "D4_limited_community_phone_25pct"
                    or all(current_by_id[row.agent_id].digital_access == row.digital_access for row in base_profiles)
                ),
                "vehicle_conservation_passed": all(
                    sum(row["weather_scenario"] == weather for row in result["vehicle_end_states"]) == 12
                    for weather in config["weather_scenarios"]
                ),
                "common_dispatch_priority_passed": all(
                    baseline_priority[key] == current_priority[key] for key in common
                ),
            }
            check["passed"] = all(value for key, value in check.items() if key.endswith("_passed"))
            checks.append(check)
            choice_rows.extend(result["mode_choices"])
            dispatch_rows.extend(result["ride_hailing_dispatch"])
        print(f"Completed seed {seed} ({seed - seed_start + 1}/{seed_count})", flush=True)

    system_distribution = _distributions(system_rows, policies, SYSTEM_METRICS)
    group_distribution = _distributions(
        group_rows, policies, GROUP_METRICS, group_key="baseline_access_group",
    )
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in {
        "system_per_seed": system_rows, "system_distributions": system_distribution,
        "system_policy_changes_vs_d0": _changes(system_distribution, policies),
        "group_per_seed": group_rows, "group_distributions": group_distribution,
        "group_policy_changes_vs_d0": _changes(
            group_distribution, policies, group_key="baseline_access_group",
        ),
        "intervention_roster": roster_rows, "mode_choices": choice_rows,
        "ride_hailing_dispatch": dispatch_rows, "consistency_checks": checks,
    }.items():
        _write_csv(output / f"{name}.csv", rows)
    print(f"Completed {len(system_rows)} policy-weather rows")
    print(f"Checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
