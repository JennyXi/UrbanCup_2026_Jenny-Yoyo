"""Scan public coupon coverage for a congestion/travel-time turning point."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.agent_population import generate_population_agents
from custom.agents.emergence_experiment import DAY_TYPES, build_emergence_activities, load_emergence_config
from custom.agents.simple_experiment import assign_two_zone_homes
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES
from scripts.run_coupon_competition_experiment import (
    _consistency_checks, _main_symmetric_config, run_coupon_policy, summarize_coupon_system,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "coupon_coverage_threshold_experiment.json"
METRICS = (
    "coupon_awarded", "coupon_redeemed", "coupon_induced_requests", "coupon_subsidy_yuan",
    "ride_hailing_requests", "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts", "transport_related_unmet",
    "necessary_activity_completion_rate", "walking_mode_share", "bus_mode_share",
    "ride_hailing_mode_share", "mean_bus_wait_minutes_per_attempt", "mean_total_travel_time",
    "total_travel_time_minutes", "total_bus_in_vehicle_time_minutes",
    "total_ride_hailing_in_vehicle_time_minutes", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "peak_road_volume_capacity_ratio",
    "mean_dynamic_congestion_multiplier", "mean_road_speed_kmh",
    "total_heat_risk_burden", "necessary_heat_risk_burden",
)


def load_threshold_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
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


def _mean(rows: Iterable[Mapping[str, Any]], metric: str) -> float:
    values = [float(row[metric]) for row in rows]
    return statistics.mean(values) if values else 0.0


def build_run_config(experiment: Mapping[str, Any], pool: int) -> dict[str, Any]:
    config = copy.deepcopy(load_emergence_config())
    config["total_agents"] = int(experiment["total_agents"])
    config["coupon_experiment"]["discount_multiplier"] = float(experiment["discount_multiplier"])
    config["coupon_experiment"]["daily_total_coupon_pool"] = max(1, int(pool))
    config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"] = copy.deepcopy(
        experiment["initial_daily_vehicles_by_day_type"]
    )
    return config


def _aggregate(per_seed: list[dict[str, Any]], pools: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool in pools:
        for week in WEATHER_TYPES:
            for day_type in DAY_TYPES:
                selected = [row for row in per_seed if int(row["coupon_pool"]) == pool
                            and row["weather_week"] == week and row["day_type"] == day_type]
                rows.append({
                    "coupon_pool": pool, "coupon_coverage_rate": pool / 200.0,
                    "weather_scenario": week, "day_type": day_type,
                    "seed_count": len(selected),
                    **{metric: round(_mean(selected, metric), 6) for metric in METRICS},
                })
    return rows


def _overall(aggregate: list[dict[str, Any]], pools: list[int]) -> list[dict[str, Any]]:
    rows = []
    for pool in pools:
        selected = [row for row in aggregate if int(row["coupon_pool"]) == pool]
        rows.append({
            "coupon_pool": pool, "coupon_coverage_rate": pool / 200.0,
            "weather_scenario": "ALL", "day_type": "ALL",
            **{metric: round(_mean(selected, metric), 6) for metric in METRICS},
        })
    return rows


def _marginal_rows(
    aggregate: list[dict[str, Any]], overall: list[dict[str, Any]], pools: list[int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    scenario_keys = [(week, day_type) for week in WEATHER_TYPES for day_type in DAY_TYPES]
    scenario_keys.append(("ALL", "ALL"))
    source = aggregate + overall
    for week, day_type in scenario_keys:
        lookup = {int(row["coupon_pool"]): row for row in source
                  if row["weather_scenario"] == week and row["day_type"] == day_type}
        baseline = lookup[pools[0]]
        for previous_pool, pool in zip(pools, pools[1:]):
            previous, current = lookup[previous_pool], lookup[pool]
            mean_time_change = float(current["mean_total_travel_time"]) - float(previous["mean_total_travel_time"])
            total_time_change = float(current["total_travel_time_minutes"]) - float(previous["total_travel_time_minutes"])
            road_change = float(current["road_vehicle_volume"]) - float(previous["road_vehicle_volume"])
            output.append({
                "weather_scenario": week, "day_type": day_type,
                "previous_coupon_pool": previous_pool, "coupon_pool": pool,
                "previous_coverage_rate": previous_pool / 200.0, "coupon_coverage_rate": pool / 200.0,
                "ride_requests_change": round(float(current["ride_hailing_requests"]) - float(previous["ride_hailing_requests"]), 6),
                "failed_ride_requests_change": round(float(current["failed_ride_hailing_requests"]) - float(previous["failed_ride_hailing_requests"]), 6),
                "ride_wait_change_min": round(float(current["mean_ride_hailing_wait_minutes_per_request"]) - float(previous["mean_ride_hailing_wait_minutes_per_request"]), 6),
                "road_vehicle_volume_change": round(road_change, 6),
                "mean_volume_capacity_ratio_change": round(float(current["mean_volume_capacity_ratio"]) - float(previous["mean_volume_capacity_ratio"]), 6),
                "mean_congestion_multiplier_change": round(float(current["mean_dynamic_congestion_multiplier"]) - float(previous["mean_dynamic_congestion_multiplier"]), 6),
                "mean_travel_time_change_min": round(mean_time_change, 6),
                "total_travel_time_change_min": round(total_time_change, 6),
                "mean_travel_time_change_from_c0_min": round(float(current["mean_total_travel_time"]) - float(baseline["mean_total_travel_time"]), 6),
                "total_travel_time_change_from_c0_min": round(float(current["total_travel_time_minutes"]) - float(baseline["total_travel_time_minutes"]), 6),
                "necessary_completion_change_percentage_points": round((float(current["necessary_activity_completion_rate"]) - float(previous["necessary_activity_completion_rate"])) * 100.0, 6),
                "heat_risk_change": round(float(current["total_heat_risk_burden"]) - float(previous["total_heat_risk_burden"]), 6),
                "adjacent_mean_time_turn_up": road_change > 0.0 and mean_time_change > 0.0,
                "adjacent_total_time_turn_up": road_change > 0.0 and total_time_change > 0.0,
                "mean_time_above_c0": float(current["mean_total_travel_time"]) > float(baseline["mean_total_travel_time"]),
                "total_time_above_c0": float(current["total_travel_time_minutes"]) > float(baseline["total_travel_time_minutes"]),
            })
    return output


def _thresholds(marginal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    keys = [(w, d) for w in WEATHER_TYPES for d in DAY_TYPES] + [("ALL", "ALL")]
    for week, day_type in keys:
        rows = [row for row in marginal if row["weather_scenario"] == week and row["day_type"] == day_type]
        for criterion in ("adjacent_mean_time_turn_up", "adjacent_total_time_turn_up",
                          "mean_time_above_c0", "total_time_above_c0"):
            found = next((row for row in rows if row[criterion]), None)
            output.append({
                "weather_scenario": week, "day_type": day_type, "criterion": criterion,
                "first_coupon_pool": found["coupon_pool"] if found else "",
                "first_coverage_rate": found["coupon_coverage_rate"] if found else "",
                "found_within_grid": found is not None,
                "mechanism_candidate_not_calibrated_optimum": True,
            })
    return output


def run_threshold_experiment(
    *, seed_start: int, seed_count: int, output: Path,
    pools: list[int] | None = None, experiment: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    experiment = experiment or load_threshold_config()
    pools = pools or [int(value) for value in experiment["coupon_pool_grid"]]
    symmetric = _main_symmetric_config(build_run_config(experiment, max(pools)))
    per_seed: list[dict[str, Any]] = []
    allocations_all: list[dict[str, Any]] = []
    requests_all: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    nested_checks: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        population_config = build_run_config(experiment, max(pools))
        profiles = assign_two_zone_homes(
            generate_population_agents(int(experiment["total_agents"]), seed=seed), seed=seed,
            s2_share=float(symmetric["s2_home_share"]),
        )
        activities = build_emergence_activities(
            profiles, seed=seed, config=population_config, symmetric=symmetric,
        )
        awards_by_pool_day: dict[tuple[int, str], set[int]] = {}
        for pool in pools:
            config = build_run_config(experiment, pool)
            policy = "C0_no_coupon" if pool == 0 else "C1_public_limited"
            result, allocations = run_coupon_policy(
                profiles, activities, policy, seed=seed, config=config, symmetric=symmetric,
            )
            per_seed.extend({
                "coupon_pool": pool,
                "coupon_coverage_rate": pool / float(experiment["total_agents"]),
                "threshold_policy": policy, **row,
            } for row in summarize_coupon_system(result, allocations, policy))
            allocations_all.extend({"seed": seed, "coupon_pool": pool, **row} for row in allocations)
            requests_all.extend({"seed": seed, "coupon_pool": pool, **row} for row in result["ride_hailing_requests"])
            checks.append({"coupon_pool": pool, **_consistency_checks(result, allocations, policy, config)})
            for day_type in DAY_TYPES:
                awards_by_pool_day[(pool, day_type)] = {
                    int(row["agent_id"]) for row in allocations
                    if row["day_type"] == day_type and row["coupon_awarded"]
                }
        for day_type in DAY_TYPES:
            nested = all(awards_by_pool_day[(left, day_type)] <= awards_by_pool_day[(right, day_type)]
                         for left, right in zip(pools, pools[1:]))
            nested_checks.append({"seed": seed, "day_type": day_type,
                                  "public_awards_nested_across_pool_grid": nested})

    aggregate = _aggregate(per_seed, pools)
    overall = _overall(aggregate, pools)
    marginal = _marginal_rows(aggregate, overall, pools)
    thresholds = _thresholds(marginal)
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "coverage_per_seed": per_seed, "coverage_scenario_summary": aggregate,
        "coverage_overall_summary": overall, "coverage_marginal_changes": marginal,
        "coverage_candidate_thresholds": thresholds,
        "coverage_coupon_allocations": allocations_all, "coverage_request_audit": requests_all,
        "coverage_consistency_checks": checks, "coverage_nested_award_checks": nested_checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({**dict(experiment), "seed_start": seed_start, "seed_count": seed_count,
                   "selected_coupon_pool_grid": pools,
                   "common_agents_activities_weather_fleet_seed_dispatch_priority": True},
                  handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/coupon_coverage_threshold_200_agents_smoke_3")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    args = parser.parse_args()
    experiment = load_threshold_config()
    seed_start = args.seed_start if args.seed_start is not None else int(experiment["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(experiment["default_seed_count"])
    result = run_threshold_experiment(seed_start=seed_start, seed_count=seed_count,
                                      output=Path(args.output), experiment=experiment)
    checks = result["coverage_consistency_checks"]
    nested = result["coverage_nested_award_checks"]
    print(f"Completed {len(result['coverage_per_seed'])} pool-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Nested award checks passed: {sum(row['public_awards_nested_across_pool_grid'] for row in nested)}/{len(nested)}")
    print(json.dumps(result["coverage_candidate_thresholds"], ensure_ascii=False, indent=2))
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
