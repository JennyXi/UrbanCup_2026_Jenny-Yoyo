"""Run same-time elder ride-hailing dispatch priority scenarios at 200 agents."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.emergence_experiment import (
    DAY_TYPES, _elder_dispatch_rank, load_emergence_config,
    run_emergence_experiment, summarize_macro,
)
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES
from scripts.run_coupon_competition_experiment import GROUPS, _main_symmetric_config, coupon_group


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "elder_dispatch_priority_experiment.json"
SYSTEM_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "mean_ride_hailing_wait_minutes_per_request",
    "fallback_attempts", "fallback_successes", "transport_related_unmet",
    "necessary_transport_related_unmet", "necessary_activity_completion_rate",
    "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
    "mean_total_travel_time", "total_travel_time_minutes", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "mean_dynamic_congestion_multiplier",
    "total_heat_risk_burden", "necessary_heat_risk_burden",
)
GROUP_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "mean_ride_hailing_wait_minutes",
    "fallback_attempts", "transport_unmet", "necessary_transport_unmet",
    "necessary_activity_completion_rate", "total_heat_risk_burden",
)


def load_priority_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_run_config(experiment: Mapping[str, Any], policy: str) -> dict[str, Any]:
    config = copy.deepcopy(load_emergence_config())
    config["total_agents"] = int(experiment["total_agents"])
    config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"] = copy.deepcopy(
        experiment["initial_daily_vehicles_by_day_type"]
    )
    config["ride_hailing_feedback"]["dispatch_priority_policy"] = policy
    return config


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    fields.extend(key for row in rows for key in row if key not in fields)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _mean(rows: Iterable[Mapping[str, Any]], metric: str) -> float:
    values = [float(row[metric]) for row in rows]
    return statistics.mean(values) if values else 0.0


def summarize_groups(result: Mapping[str, Any], policy: str) -> list[dict[str, Any]]:
    profiles = {profile.agent_id: profile for profile in result["profiles"]}
    output: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            for group in GROUPS:
                agent_ids = {agent_id for agent_id, profile in profiles.items() if coupon_group(profile) == group}
                activities = [row for row in result["activity_results"]
                              if row["weather_week"] == week and row["day_type"] == day_type
                              and row["agent_id"] in agent_ids]
                necessary = [row for row in activities if row["necessary_activity"]]
                requests = [row for row in result["ride_hailing_requests"]
                            if row["weather_week"] == week and row["day_type"] == day_type
                            and row["agent_id"] in agent_ids]
                successful = [row for row in requests if row["succeeded"]]
                legs = [row for row in result["leg_results"]
                        if row["weather_week"] == week and row["day_type"] == day_type
                        and row["agent_id"] in agent_ids]
                output.append({
                    "seed": result["seed"], "policy": policy,
                    "weather_week": week, "day_type": day_type,
                    "group": group, "agent_count": len(agent_ids),
                    "ride_hailing_requests": len(requests),
                    "successful_ride_hailing_requests": len(successful),
                    "failed_ride_hailing_requests": len(requests) - len(successful),
                    "mean_ride_hailing_wait_minutes": round(
                        sum(float(row["pickup_wait_min"]) for row in requests) / len(requests), 6
                    ) if requests else 0.0,
                    "fallback_attempts": sum(bool(row["fallback_used"]) for row in legs),
                    "transport_unmet": sum(bool(row["transport_related_unmet"]) for row in activities),
                    "necessary_transport_unmet": sum(bool(row["necessary_transport_related_unmet"]) for row in activities),
                    "necessary_activity_completion_rate": round(
                        sum(bool(row["activity_completed"]) for row in necessary) / len(necessary), 6
                    ) if necessary else 1.0,
                    "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in activities), 6),
                })
    return output


def _checks(result: Mapping[str, Any], policy: str, config: Mapping[str, Any]) -> dict[str, Any]:
    vehicle_count = True
    nonoverlap = True
    chronological = True
    policy_order = True
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            expected = sum(config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"][day_type].values())
            states = [row for row in result["ride_hailing_vehicle_states"]
                      if row["weather_week"] == week and row["day_type"] == day_type]
            vehicle_count &= len(states) == expected
            requests = [row for row in result["ride_hailing_requests"]
                        if row["weather_week"] == week and row["day_type"] == day_type]
            chronological &= all(float(right["request_time"]) >= float(left["request_time"]) - 1e-9
                                 for left, right in zip(requests, requests[1:]))
            by_time: dict[float, list[Mapping[str, Any]]] = defaultdict(list)
            by_vehicle: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for row in requests:
                by_time[float(row["request_time"])].append(row)
                if row["succeeded"]:
                    by_vehicle[str(row["vehicle_id"])].append(row)
            for rows in by_time.values():
                policy_order &= all(
                    (int(right["dispatch_priority_group_rank"]), float(right["dispatch_priority"]), str(right["leg_id"]))
                    >= (int(left["dispatch_priority_group_rank"]), float(left["dispatch_priority"]), str(left["leg_id"]))
                    for left, right in zip(rows, rows[1:])
                )
            for rows in by_vehicle.values():
                rows.sort(key=lambda row: float(row["busy_start"]))
                nonoverlap &= all(float(right["busy_start"]) >= float(left["busy_until"]) - 1e-9
                                  for left, right in zip(rows, rows[1:]))
    return {
        "seed": result["seed"], "policy": policy,
        "vehicle_total_conserved": vehicle_count,
        "vehicle_assignments_nonoverlapping": nonoverlap,
        "later_requests_do_not_preempt": chronological,
        "same_time_requests_follow_policy_rank": policy_order,
        "passed": vehicle_count and nonoverlap and chronological and policy_order,
    }


def _distribution(rows: list[dict[str, Any]], keys: tuple[str, ...], metrics: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output = []
    for group_key, selected in grouped.items():
        for metric in metrics:
            values = [float(row[metric]) for row in selected]
            output.append({
                **dict(zip(keys, group_key)), "metric": metric,
                "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "median": round(statistics.median(values), 6),
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            })
    return output


def run_priority_experiment(
    *, seed_start: int, seed_count: int, output: Path,
    experiment: Mapping[str, Any] | None = None,
    transport_config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    experiment = experiment or load_priority_config()
    policies = list(experiment["policies"])
    symmetric = _main_symmetric_config(build_run_config(experiment, policies[0]))
    system_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    common_priority_checks: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        results: dict[str, Mapping[str, Any]] = {}
        for policy in policies:
            config = build_run_config(experiment, policy)
            result = run_emergence_experiment(
                seed, config=config, symmetric=symmetric,
                transport_config=transport_config,
            )
            results[policy] = result
            system_rows.extend({"policy": policy, **row} for row in summarize_macro(result))
            group_rows.extend(summarize_groups(result, policy))
            request_rows.extend({"seed": seed, "policy": policy, **row}
                                for row in result["ride_hailing_requests"])
            checks.append(_checks(result, policy, config))
        base = {(row["weather_week"], row["leg_id"]): float(row["dispatch_priority"])
                for row in results[policies[0]]["ride_hailing_requests"]}
        for policy in policies[1:]:
            current = {(row["weather_week"], row["leg_id"]): float(row["dispatch_priority"])
                       for row in results[policy]["ride_hailing_requests"]}
            common = set(base) & set(current)
            common_priority_checks.append({
                "seed": seed, "policy": policy, "shared_request_count": len(common),
                "shared_base_dispatch_priorities_identical": bool(common) and all(base[key] == current[key] for key in common),
            })

    system_distribution = _distribution(system_rows, ("policy", "weather_week", "day_type"), SYSTEM_METRICS)
    group_distribution = _distribution(group_rows, ("policy", "weather_week", "day_type", "group"), GROUP_METRICS)
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "priority_system_per_seed": system_rows,
        "priority_group_per_seed": group_rows,
        "priority_system_distribution": system_distribution,
        "priority_group_distribution": group_distribution,
        "priority_request_audit": request_rows,
        "priority_consistency_checks": checks,
        "priority_common_random_checks": common_priority_checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({**dict(experiment), "seed_start": seed_start, "seed_count": seed_count,
                   "common_agents_activities_weather_fleet_seed_base_dispatch_priority": True},
                  handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/elder_dispatch_priority_200_agents_smoke_3")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    args = parser.parse_args()
    experiment = load_priority_config()
    seed_start = args.seed_start if args.seed_start is not None else int(experiment["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(experiment["default_seed_count"])
    result = run_priority_experiment(seed_start=seed_start, seed_count=seed_count,
                                     output=Path(args.output), experiment=experiment)
    checks = result["priority_consistency_checks"]
    common = result["priority_common_random_checks"]
    print(f"Completed {len(result['priority_system_per_seed'])} policy-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Common-random checks passed: {sum(row['shared_base_dispatch_priorities_identical'] for row in common)}/{len(common)}")
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
