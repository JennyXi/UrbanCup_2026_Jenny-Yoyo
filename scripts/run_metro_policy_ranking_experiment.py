"""Compare policy rankings with no metro and realistic-access metro at 50 agents."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.agent_population import generate_population_agents
from custom.agents.emergence_experiment import (
    build_emergence_activities, load_emergence_config,
    run_emergence_experiment, summarize_macro,
)
from custom.agents.metro_experiment import (
    load_metro_experiment_config, load_metro_transport_config,
)
from custom.agents.simple_experiment import assign_two_zone_homes
from scripts.run_coupon_competition_experiment import (
    _main_symmetric_config, run_coupon_policy,
    summarize_coupon_groups,
)
from scripts.run_elder_digital_access_experiment import (
    _run_policy as run_digital_policy,
    summarize_groups as summarize_digital_groups,
)
from scripts.run_elder_dispatch_priority_experiment import (
    build_run_config, load_priority_config,
    summarize_groups as summarize_priority_groups,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "metro_policy_ranking_50.json"
SYSTEM_METRICS = (
    "necessary_activity_completion_rate", "transport_related_unmet",
    "necessary_transport_related_unmet", "mean_ride_hailing_wait_minutes_per_request",
    "fallback_attempts", "walking_mode_share", "bus_mode_share",
    "ride_hailing_mode_share", "metro_mode_share", "mean_total_travel_time",
    "road_vehicle_volume", "mean_road_speed_kmh", "total_heat_risk_burden",
    "necessary_heat_risk_burden",
)
RANK_DIRECTIONS = {
    "necessary_activity_completion_rate": "higher",
    "transport_related_unmet": "lower",
    "necessary_transport_related_unmet": "lower",
    "mean_ride_hailing_wait_minutes_per_request": "lower",
    "fallback_attempts": "lower",
    "mean_total_travel_time": "lower",
    "road_vehicle_volume": "lower",
    "total_heat_risk_burden": "lower",
    "necessary_heat_risk_burden": "lower",
}


def _load(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    fields.extend(key for row in rows for key in row if key not in fields)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _transport(scenario: str, metro_experiment: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if scenario == "M0_no_metro":
        return None
    return load_metro_transport_config(metro_experiment, scenario=scenario)


def _append_macro(
    rows: list[dict[str, Any]], result: Mapping[str, Any], *,
    scenario: str, family: str, policy: str,
) -> None:
    for row in summarize_macro(result):
        rows.append({
            "metro_scenario": scenario, "policy_family": family,
            "policy": policy, **row,
        })


def _append_groups(
    rows: list[dict[str, Any]], source: Iterable[Mapping[str, Any]], *,
    scenario: str, family: str,
) -> None:
    for original in source:
        row = dict(original)
        group = row.pop("group", row.pop("baseline_access_group", ""))
        weather = row.pop("weather_scenario", row.get("weather_week", ""))
        rows.append({
            "metro_scenario": scenario, "policy_family": family,
            "weather_week": weather, "group": group, **row,
        })


def _distribution(
    rows: Iterable[Mapping[str, Any]], keys: tuple[str, ...], metrics: Iterable[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output = []
    for key, selected in sorted(groups.items()):
        for metric in metrics:
            values = [float(row[metric]) for row in selected if metric in row and row[metric] != ""]
            if not values:
                continue
            output.append({
                **dict(zip(keys, key)), "metric": metric, "seed_count": len(values),
                "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "median": round(statistics.median(values), 6),
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            })
    return output


def _rank_signature(values: Mapping[str, float], direction: str) -> str:
    grouped: dict[float, list[str]] = defaultdict(list)
    for policy, value in values.items():
        grouped[round(float(value), 6)].append(policy)
    ordered = sorted(grouped, reverse=direction == "higher")
    return " > ".join(" = ".join(sorted(grouped[value])) for value in ordered)


def _rank_comparison(summary: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    lookup: dict[tuple[str, str, str, str, str], dict[str, float]] = defaultdict(dict)
    for row in summary:
        metric = str(row["metric"])
        if metric not in RANK_DIRECTIONS:
            continue
        key = (
            str(row["policy_family"]), str(row["weather_week"]),
            str(row["day_type"]), metric, str(row["metro_scenario"]),
        )
        lookup[key][str(row["policy"])] = float(row["mean"])
    output = []
    base_keys = sorted({key[:4] for key in lookup})
    for family, weather, day_type, metric in base_keys:
        m0 = lookup.get((family, weather, day_type, metric, "M0_no_metro"), {})
        m2 = lookup.get((family, weather, day_type, metric, "M2_realistic_access"), {})
        if not m0 or not m2:
            continue
        direction = RANK_DIRECTIONS[metric]
        m0_rank = _rank_signature(m0, direction)
        m2_rank = _rank_signature(m2, direction)
        output.append({
            "policy_family": family, "weather_week": weather, "day_type": day_type,
            "metric": metric, "better_direction": direction,
            "M0_policy_order": m0_rank, "M2_policy_order": m2_rank,
            "ranking_changed": m0_rank != m2_rank,
        })
    return output


def run_experiment(*, seed_start: int, seed_count: int, output: Path) -> dict[str, Any]:
    spec = _load()
    emergence = load_emergence_config()
    emergence["total_agents"] = int(spec["total_agents"])
    symmetric = _main_symmetric_config(emergence)
    metro_experiment = load_metro_experiment_config()
    for week, probability in metro_experiment["metro_success_probability"].items():
        symmetric["transport_success_probability"][week]["metro"] = float(probability)
    symmetric["failed_attempt_charge_fraction"]["metro"] = float(
        metro_experiment["metro_failed_attempt_charge_fraction"]
    )
    priority_spec = load_priority_config()
    priority_spec["total_agents"] = int(spec["total_agents"])
    priority_spec["initial_daily_vehicles_by_day_type"] = copy.deepcopy(
        emergence["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"]
    )
    system_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []

    for seed in range(seed_start, seed_start + seed_count):
        profiles = assign_two_zone_homes(
            generate_population_agents(int(spec["total_agents"]), seed=seed), seed=seed,
            s2_share=float(symmetric["s2_home_share"]),
        )
        activities = build_emergence_activities(
            profiles, seed=seed, config=emergence, symmetric=symmetric,
        )
        for scenario in spec["metro_scenarios"]:
            transport = _transport(scenario, metro_experiment)
            for policy in spec["policy_families"]["coupon"]:
                result, allocations = run_coupon_policy(
                    profiles, activities, policy, seed=seed, config=emergence,
                    symmetric=symmetric, transport_config=transport,
                )
                _append_macro(system_rows, result, scenario=scenario, family="coupon", policy=policy)
                _append_groups(
                    group_rows, summarize_coupon_groups(result, allocations, policy),
                    scenario=scenario, family="coupon",
                )
            for policy in spec["policy_families"]["digital_access"]:
                _, result = run_digital_policy(
                    profiles, activities, policy, seed=seed, config=emergence,
                    symmetric=symmetric, transport_config=transport,
                )
                _append_macro(system_rows, result, scenario=scenario, family="digital_access", policy=policy)
                _append_groups(
                    group_rows, summarize_digital_groups(result, profiles, policy),
                    scenario=scenario, family="digital_access",
                )
            for policy in spec["policy_families"]["elder_dispatch_priority"]:
                policy_config = build_run_config(priority_spec, policy)
                result = run_emergence_experiment(
                    seed, config=policy_config, symmetric=symmetric,
                    transport_config=transport,
                )
                _append_macro(
                    system_rows, result, scenario=scenario,
                    family="elder_dispatch_priority", policy=policy,
                )
                _append_groups(
                    group_rows, summarize_priority_groups(result, policy),
                    scenario=scenario, family="elder_dispatch_priority",
                )

    system_summary = _distribution(
        system_rows,
        ("metro_scenario", "policy_family", "policy", "weather_week", "day_type"),
        SYSTEM_METRICS,
    )
    group_metrics = (
        "necessary_activity_completion_rate", "transport_unmet",
        "necessary_transport_related_unmet", "necessary_transport_unmet",
        "mean_ride_hailing_wait_minutes", "ride_hailing_requests",
        "failed_ride_hailing_requests", "total_heat_risk_burden",
    )
    group_summary = _distribution(
        group_rows,
        ("metro_scenario", "policy_family", "policy", "weather_week", "day_type", "group"),
        group_metrics,
    )
    ranks = _rank_comparison(system_summary)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "policy_macro_per_seed.csv", system_rows)
    _write_csv(output / "policy_metric_summary.csv", system_summary)
    _write_csv(output / "target_group_summary.csv", group_summary)
    _write_csv(output / "policy_rank_comparison.csv", ranks)
    metadata = {
        **spec, "seed_start": seed_start, "seed_count": seed_count,
        "files": [
            "policy_macro_per_seed.csv", "policy_metric_summary.csv",
            "target_group_summary.csv", "policy_rank_comparison.csv",
        ],
        "ranking_change_count": sum(bool(row["ranking_changed"]) for row in ranks),
        "ranking_comparison_count": len(ranks),
    }
    with (output / "experiment_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    return {"system": system_rows, "summary": system_summary, "groups": group_summary, "ranks": ranks}


def main() -> None:
    spec = _load()
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-start", type=int, default=int(spec["seed_start"]))
    parser.add_argument("--seed-count", type=int, default=int(spec["default_seed_count"]))
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "outputs" / "metro_policy_ranking_50",
    )
    args = parser.parse_args()
    result = run_experiment(seed_start=args.seed_start, seed_count=args.seed_count, output=args.output)
    print(f"Rows: per-seed={len(result['system'])}, summaries={len(result['summary'])}, ranks={len(result['ranks'])}")
    print(f"Ranking changes: {sum(bool(row['ranking_changed']) for row in result['ranks'])}/{len(result['ranks'])}")
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
