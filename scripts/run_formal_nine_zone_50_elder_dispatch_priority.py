"""Run paired P0/P4 elder dispatch priority in the formal nine-zone 50-Agent model."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_elder_dispatch_priority.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50_elder_dispatch_priority"
GROUPS = (
    "18-39", "40-59", "elder_digital", "elder_nondigital_assisted",
    "elder_nondigital_unassisted",
)
SYSTEM_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests", "ride_hailing_failed",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts",
    "fallback_succeeded", "transport_unmet", "mandatory_activity_incomplete",
    "necessary_activity_completion_rate", "walking_mode_share", "bus_mode_share",
    "metro_mode_share", "ride_hailing_mode_share", "mean_total_travel_time",
    "road_vehicle_volume", "mean_road_speed_kmh", "total_system_wait_minutes",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)
GROUP_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "ride_hailing_success_rate",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts",
    "transport_unmet", "planned_necessary_activities",
    "completed_necessary_activities", "necessary_activity_completion_rate",
    "walking_legs", "bus_legs", "metro_legs", "ride_hailing_legs",
    "mean_total_travel_time", "total_outdoor_exposure_minutes",
    "total_heat_risk_burden",
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


def _group(profile: Mapping[str, Any]) -> str:
    if profile["age_group"] != "60+":
        return str(profile["age_group"])
    if profile["digital_access"]:
        return "elder_digital"
    if profile["family_assistance"]:
        return "elder_nondigital_assisted"
    return "elder_nondigital_unassisted"


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(statistics.mean(values), 6) if values else 0.0


def _system_row(
    summary: Mapping[str, Any], dispatch: list[Mapping[str, Any]], policy: str,
) -> dict[str, Any]:
    weather = summary["weather_scenario"]
    requests = [row for row in dispatch if row["weather_scenario"] == weather]
    return {
        **dict(summary),
        "policy": policy,
        "mean_ride_hailing_wait_minutes_per_request": _mean(
            float(row["pickup_wait_min"]) for row in requests
        ),
    }


def _group_rows(
    result: Mapping[str, Any], policy: str, seed: int,
) -> list[dict[str, Any]]:
    profiles = {int(row["agent_id"]): row for row in result["inputs"]["agents"]}
    group_by_id = {agent_id: _group(row) for agent_id, row in profiles.items()}
    output = []
    for weather in ("W0", "W2"):
        activities = [row for row in result["activity_results"] if row["weather_scenario"] == weather]
        choices = [row for row in result["mode_choices"] if row["weather_scenario"] == weather]
        dispatch = [row for row in result["ride_hailing_dispatch"] if row["weather_scenario"] == weather]
        for group in GROUPS:
            ids = {agent_id for agent_id, value in group_by_id.items() if value == group}
            group_activities = [row for row in activities if int(row["agent_id"]) in ids]
            necessary = [row for row in group_activities if row["is_mandatory"]]
            group_choices = [row for row in choices if int(row["agent_id"]) in ids]
            successful = [row for row in group_choices if row["transport_succeeded"]]
            modes = Counter(row["final_mode"] for row in successful)
            requests = [row for row in dispatch if int(row["agent_id"]) in ids]
            successful_requests = [row for row in requests if row["succeeded"]]
            output.append({
                "seed": seed, "policy": policy, "weather_scenario": weather,
                "day_type": "workday", "group": group, "agent_count": len(ids),
                "ride_hailing_requests": len(requests),
                "successful_ride_hailing_requests": len(successful_requests),
                "failed_ride_hailing_requests": len(requests) - len(successful_requests),
                "ride_hailing_success_rate": (
                    round(len(successful_requests) / len(requests), 6) if requests else None
                ),
                "mean_ride_hailing_wait_minutes_per_request": _mean(
                    float(row["pickup_wait_min"]) for row in requests
                ),
                "fallback_attempts": sum(row["fallback_attempted"] for row in group_choices),
                "transport_unmet": sum(row["transport_unmet"] for row in group_activities),
                "planned_necessary_activities": len(necessary),
                "completed_necessary_activities": sum(row["completed"] for row in necessary),
                "necessary_activity_completion_rate": (
                    round(sum(row["completed"] for row in necessary) / len(necessary), 6)
                    if necessary else None
                ),
                "walking_legs": modes["walk"], "bus_legs": modes["bus"],
                "metro_legs": modes["metro"], "ride_hailing_legs": modes["ride_hailing"],
                "mean_total_travel_time": _mean(
                    float(row["total_travel_time_min"]) for row in successful
                ),
                "total_outdoor_exposure_minutes": round(
                    sum(float(row["outdoor_exposure_minutes"]) for row in group_choices), 6
                ),
                "total_heat_risk_burden": round(
                    sum(float(row["heat_risk_burden"]) for row in group_choices), 6
                ),
            })
    return output


def _distributions(
    rows: list[Mapping[str, Any]], metrics: Iterable[str], *, group_key: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["policy"], row["weather_scenario"])
        if group_key:
            key += (row[group_key],)
        grouped[key].append(row)
    output = []
    for key, selected in grouped.items():
        for metric in metrics:
            values = [float(row[metric]) for row in selected if row.get(metric) is not None]
            if not values:
                continue
            result = {
                "policy": key[0], "weather_scenario": key[1], "metric": metric,
                "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "median": round(statistics.median(values), 6),
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            }
            if group_key:
                result[group_key] = key[2]
            output.append(result)
    return output


def _changes(
    distributions: list[Mapping[str, Any]], *, group_key: str | None = None,
) -> list[dict[str, Any]]:
    lookup = {
        (row["policy"], row["weather_scenario"], row.get(group_key) if group_key else None, row["metric"]): float(row["mean"])
        for row in distributions
    }
    output = []
    for key, policy_value in lookup.items():
        policy, weather, group, metric = key
        if policy != "P4_elder_priority":
            continue
        baseline_key = ("P0_first_come", weather, group, metric)
        if baseline_key not in lookup:
            continue
        baseline = lookup[baseline_key]
        output.append({
            "policy": policy, "baseline_policy": "P0_first_come",
            "weather_scenario": weather, "group": group if group_key else "",
            "metric": metric, "baseline_mean": baseline, "policy_mean": policy_value,
            "absolute_change": round(policy_value - baseline, 6),
            "percent_change": round((policy_value - baseline) / baseline * 100.0, 6) if baseline else None,
            "percent_change_defined": baseline != 0,
            "undefined_reason": "" if baseline else "baseline_zero",
        })
    return output


def _transfer_rows(
    seed: int, baseline: Mapping[str, Any], priority: Mapping[str, Any],
) -> list[dict[str, Any]]:
    base = {
        (row["weather_scenario"], row["leg_id"]): row
        for row in baseline["ride_hailing_dispatch"]
    }
    policy = {
        (row["weather_scenario"], row["leg_id"]): row
        for row in priority["ride_hailing_dispatch"]
    }
    profiles = {int(row["agent_id"]): row for row in baseline["inputs"]["agents"]}
    output = []
    for key in sorted(set(base) | set(policy)):
        left, right = base.get(key), policy.get(key)
        source = left or right
        same_request = left is not None and right is not None
        left_success = bool(left and left["succeeded"])
        right_success = bool(right and right["succeeded"])
        wait_change = None
        if same_request:
            wait_change = round(float(right["pickup_wait_min"]) - float(left["pickup_wait_min"]), 6)
        output.append({
            "seed": seed, "weather_scenario": key[0], "leg_id": key[1],
            "agent_id": source["agent_id"], "age_group": profiles[int(source["agent_id"])]["age_group"],
            "group": _group(profiles[int(source["agent_id"])]),
            "shared_request": same_request,
            "p0_succeeded": left_success, "p4_succeeded": right_success,
            "benefited_failure_to_success": same_request and not left_success and right_success,
            "harmed_success_to_failure": same_request and left_success and not right_success,
            "pickup_wait_change_minutes": wait_change,
            "wait_improved": same_request and wait_change is not None and wait_change < -1e-9,
            "wait_worsened": same_request and wait_change is not None and wait_change > 1e-9,
            "base_dispatch_priority_identical": (
                same_request and left["dispatch_priority"] == right["dispatch_priority"]
            ),
        })
    return output


def _checks(
    seed: int, policy: str, result: Mapping[str, Any], baseline: Mapping[str, Any],
    vehicle_total: int,
) -> dict[str, Any]:
    dispatch = result["ride_hailing_dispatch"]
    by_vehicle: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in dispatch:
        if row["succeeded"]:
            by_vehicle[(row["weather_scenario"], str(row["vehicle_id"]))].append(row)
    nonoverlap = True
    for rows in by_vehicle.values():
        rows.sort(key=lambda row: float(row["busy_start"]))
        nonoverlap &= all(
            float(right["busy_start"]) >= float(left["busy_until"]) - 1e-9
            for left, right in zip(rows, rows[1:])
        )
    order = True
    for weather in ("W0", "W2"):
        rows = [row for row in dispatch if row["weather_scenario"] == weather]
        keys = [
            (row["request_time"], int(row["dispatch_priority_group_rank"]),
             float(row["dispatch_priority"]), str(row["leg_id"]))
            for row in rows
        ]
        order &= keys == sorted(keys)
    base_priority = {
        (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
        for row in baseline["ride_hailing_dispatch"]
    }
    current_priority = {
        (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
        for row in dispatch
    }
    common = set(base_priority) & set(current_priority)
    row = {
        "seed": seed, "policy": policy,
        "vehicle_conservation_passed": all(
            sum(state["weather_scenario"] == weather for state in result["vehicle_end_states"]) == vehicle_total
            for weather in ("W0", "W2")
        ),
        "vehicle_assignments_nonoverlapping_passed": nonoverlap,
        "request_time_then_policy_order_passed": order,
        "common_base_dispatch_priority_passed": all(
            base_priority[key] == current_priority[key] for key in common
        ),
    }
    row["passed"] = all(value for key, value in row.items() if key.endswith("_passed"))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    args = parser.parse_args()
    experiment = _load(args.config)
    policies = list(experiment["policies"])
    seed_start = int(experiment["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(experiment["seed_count"] if args.seed_count is None else args.seed_count)
    vehicle_total = sum(int(value) for value in experiment["initial_vehicles"].values())
    if vehicle_total != 12:
        raise ValueError("elder-priority experiment must use the selected 12-vehicle baseline")
    base_config = load_formal_50_config(ROOT / experiment["base_experiment_config"])

    system_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    choice_rows: list[dict[str, Any]] = []
    dispatch_rows: list[dict[str, Any]] = []
    transfer_rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        bootstrap = run_formal_nine_zone_50_experiment(
            config=base_config, seed=seed, weather_scenarios=("W0",), day_types=("workday",),
        )
        paired_inputs = bootstrap["inputs"]
        results: dict[str, Mapping[str, Any]] = {}
        for policy in policies:
            run_config = copy.deepcopy(base_config)
            run_config["formal_overrides"] = {
                "experiment_condition": f"elder_dispatch_{policy}",
                "ride_hailing_fleet": {
                    "initial_vehicles_by_day_type": {"workday": experiment["initial_vehicles"]},
                    "dispatch_priority_policy": experiment["policies"][policy]["dispatch_priority_policy"],
                },
            }
            result = run_formal_nine_zone_50_experiment(
                config=run_config, seed=seed, weather_scenarios=tuple(experiment["weather_scenarios"]),
                day_types=("workday",), paired_inputs=copy.deepcopy(paired_inputs),
            )
            results[policy] = result
            for row in result["mode_choices"]:
                row["seed"] = seed
                row["priority_policy"] = policy
            for row in result["ride_hailing_dispatch"]:
                row["seed"] = seed
                row["priority_policy"] = policy
            system_rows.extend(
                _system_row(summary, result["ride_hailing_dispatch"], policy)
                for summary in result["summary_rows"]
            )
            group_rows.extend(_group_rows(result, policy, seed))
            choice_rows.extend(result["mode_choices"])
            dispatch_rows.extend(result["ride_hailing_dispatch"])
        baseline = results["P0_first_come"]
        priority = results["P4_elder_priority"]
        transfer_rows.extend(_transfer_rows(seed, baseline, priority))
        for policy in policies:
            checks.append(_checks(seed, policy, results[policy], baseline, vehicle_total))
        print(f"Completed seed {seed} ({seed - seed_start + 1}/{seed_count})", flush=True)

    system_distribution = _distributions(system_rows, SYSTEM_METRICS)
    group_distribution = _distributions(group_rows, GROUP_METRICS, group_key="group")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "system_per_seed": system_rows,
        "system_distributions": system_distribution,
        "system_policy_changes_vs_p0": _changes(system_distribution),
        "group_per_seed": group_rows,
        "group_distributions": group_distribution,
        "group_policy_changes_vs_p0": _changes(group_distribution, group_key="group"),
        "request_transfer_audit": transfer_rows,
        "mode_choices": choice_rows,
        "ride_hailing_dispatch": dispatch_rows,
        "consistency_checks": checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as stream:
        json.dump({**experiment, "seed_start": seed_start, "seed_count": seed_count}, stream, ensure_ascii=False, indent=2)
    print(f"Completed {len(system_rows)} policy-weather rows")
    print(f"Checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
