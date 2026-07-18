"""Find candidate ride-supply saturation and road-pressure points."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from pathlib import Path
from typing import Any, Mapping

from custom.agents.emergence_experiment import (
    DAY_TYPES, load_emergence_config, run_emergence_experiment, summarize_macro,
)
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES


METRICS = (
    "activity_completion_rate", "necessary_activity_completion_rate",
    "transport_related_unmet", "necessary_transport_related_unmet",
    "mean_ride_hailing_wait_minutes_per_request", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "fallback_attempts", "fallback_successes", "walking_mode_share",
    "bus_mode_share", "ride_hailing_mode_share", "scheduled_bus_vehicle_trips",
    "successful_ride_hailing_vehicle_trips", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "peak_road_volume_capacity_ratio",
    "mean_dynamic_congestion_multiplier", "mean_road_speed_kmh",
    "minimum_road_speed_multiplier",
    "mean_total_travel_time", "total_travel_time_minutes",
    "total_non_wait_travel_time_minutes", "total_in_vehicle_time_minutes",
    "total_bus_in_vehicle_time_minutes", "total_ride_hailing_in_vehicle_time_minutes",
    "total_heat_hazard_dose_c_min",
    "necessary_heat_risk_burden",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _describe(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.mean(values), 6),
        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        "median": round(statistics.median(values), 6),
        "minimum": round(min(values), 6),
        "maximum": round(max(values), 6),
    }


def _percent_change(new: float, old: float) -> float | str:
    return round((new - old) / old * 100.0, 6) if old != 0 else ""


def build_marginal_changes(
    aggregate: list[dict[str, Any]], grid: list[float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            lookup = {
                float(row["ride_supply_multiplier"]): row for row in aggregate
                if row["weather_scenario"] == week and row["day_type"] == day_type
            }
            baseline = lookup[1.0]
            for previous, current in zip(grid, grid[1:]):
                left, right = lookup[previous], lookup[current]
                wait_left = float(left["mean_ride_hailing_wait_minutes_per_request"])
                wait_right = float(right["mean_ride_hailing_wait_minutes_per_request"])
                volume_left = float(left["road_vehicle_volume"])
                volume_right = float(right["road_vehicle_volume"])
                speed_left = float(left["minimum_road_speed_multiplier"])
                speed_right = float(right["minimum_road_speed_multiplier"])
                mean_time_left = float(left["mean_total_travel_time"])
                mean_time_right = float(right["mean_total_travel_time"])
                total_time_left = float(left["total_travel_time_minutes"])
                total_time_right = float(right["total_travel_time_minutes"])
                rows.append({
                    "weather_scenario": week, "day_type": day_type,
                    "previous_ride_supply_multiplier": previous,
                    "ride_supply_multiplier": current,
                    "necessary_completion_gain_percentage_points": round(
                        (float(right["necessary_activity_completion_rate"])
                         - float(left["necessary_activity_completion_rate"])) * 100.0, 6
                    ),
                    "activity_completion_gain_percentage_points": round(
                        (float(right["activity_completion_rate"])
                         - float(left["activity_completion_rate"])) * 100.0, 6
                    ),
                    "ride_wait_reduction_percent": (
                        round((wait_left - wait_right) / wait_left * 100.0, 6)
                        if wait_left != 0 else ""
                    ),
                    "transport_unmet_change": round(
                        float(right["transport_related_unmet"])
                        - float(left["transport_related_unmet"]), 6
                    ),
                    "road_vehicle_volume_change": round(volume_right - volume_left, 6),
                    "road_vehicle_volume_change_percent": _percent_change(volume_right, volume_left),
                    "shared_road_speed_multiplier_decline_from_previous_percent": (
                        round((speed_left - speed_right) / speed_left * 100.0, 6)
                        if speed_left != 0 else ""
                    ),
                    "shared_road_speed_multiplier_decline_from_p0_percent": (
                        round((float(baseline["minimum_road_speed_multiplier"]) - speed_right)
                              / float(baseline["minimum_road_speed_multiplier"]) * 100.0, 6)
                        if float(baseline["minimum_road_speed_multiplier"]) != 0 else ""
                    ),
                    "mean_travel_time_change_minutes": round(
                        mean_time_right - mean_time_left, 6
                    ),
                    "mean_travel_time_change_percent": _percent_change(mean_time_right, mean_time_left),
                    "mean_travel_time_increase_from_p0_percent": _percent_change(
                        mean_time_right, float(baseline["mean_total_travel_time"])
                    ),
                    "total_travel_time_change_minutes": round(total_time_right - total_time_left, 6),
                    "total_travel_time_change_percent": _percent_change(total_time_right, total_time_left),
                    "total_in_vehicle_time_change_minutes": round(
                        float(right["total_in_vehicle_time_minutes"])
                        - float(left["total_in_vehicle_time_minutes"]), 6
                    ),
                    "peak_road_volume_capacity_ratio": right["peak_road_volume_capacity_ratio"],
                })
    return rows


def identify_candidate_thresholds(
    aggregate: list[dict[str, Any]], marginal: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    experiment = config["ride_supply_threshold_experiment"]
    saturation = experiment["accessibility_saturation_rule"]
    pressure = experiment["road_pressure_rule"]
    summaries: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            aggregate_rows = sorted(
                (row for row in aggregate if row["weather_scenario"] == week and row["day_type"] == day_type),
                key=lambda row: float(row["ride_supply_multiplier"]),
            )
            marginal_rows = [
                row for row in marginal
                if row["weather_scenario"] == week and row["day_type"] == day_type
                and float(row["ride_supply_multiplier"]) > 1.0
            ]
            saturation_row = next((row for row in marginal_rows if
                float(row["necessary_completion_gain_percentage_points"])
                <= float(saturation["maximum_adjacent_necessary_completion_gain_percentage_points"])
                and row["ride_wait_reduction_percent"] != ""
                and float(row["ride_wait_reduction_percent"])
                <= float(saturation["maximum_adjacent_ride_wait_reduction_percent"])
            ), None)
            baseline = next(row for row in aggregate_rows if float(row["ride_supply_multiplier"]) == 1.0)
            pressure_row = next((row for row in aggregate_rows if
                float(row["ride_supply_multiplier"]) >= 1.0 and (
                    float(row["peak_road_volume_capacity_ratio"])
                    >= float(pressure["peak_volume_capacity_ratio_threshold"])
                    or (
                        (float(row["mean_total_travel_time"])
                         - float(baseline["mean_total_travel_time"]))
                        / float(baseline["mean_total_travel_time"]) * 100.0
                        >= float(pressure["minimum_mean_total_travel_time_increase_from_p0_percent"])
                    )
                    or (
                        (float(baseline["minimum_road_speed_multiplier"])
                         - float(row["minimum_road_speed_multiplier"]))
                        / float(baseline["minimum_road_speed_multiplier"]) * 100.0
                        >= float(pressure["minimum_speed_decline_from_p0_percent"])
                    )
                )
            ), None)
            candidates = [
                float(row["ride_supply_multiplier"])
                for row in (saturation_row, pressure_row) if row is not None
            ]
            summaries.append({
                "weather_scenario": week, "day_type": day_type,
                "accessibility_saturation_multiplier": (
                    saturation_row["ride_supply_multiplier"] if saturation_row else ""
                ),
                "accessibility_saturation_found": saturation_row is not None,
                "accessibility_saturation_note": (
                    "first adjacent point above P0 meeting both marginal-benefit rules"
                    if saturation_row else "not found within tested grid"
                ),
                "road_pressure_multiplier": (
                    pressure_row["ride_supply_multiplier"] if pressure_row else ""
                ),
                "road_pressure_found": pressure_row is not None,
                "road_pressure_note": (
                    "first point at/above P0 crossing travel-time, speed-decline or peak V/C rule"
                    if pressure_row else "not found within tested grid"
                ),
                "candidate_tradeoff_upper_multiplier": min(candidates) if candidates else "",
                "candidate_is_mechanism_rule_not_optimum": True,
            })
    return summaries


def run_supply_threshold_experiment(
    *, seed_start: int, seed_count: int, output: Path,
    config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    config = config or load_emergence_config()
    experiment = config["ride_supply_threshold_experiment"]
    run_config = copy.deepcopy(config)
    road_reference = float(experiment["fixed_reference_road_vehicles_per_30_min"])
    run_config["road_feedback"]["reference_road_vehicles_per_30_min"] = road_reference
    grid = [float(value) for value in experiment["ride_supply_multipliers"]]
    bus_frequency = float(experiment["fixed_bus_frequency_multiplier"])
    per_seed: list[dict[str, Any]] = []
    for ride_supply in grid:
        for seed in range(seed_start, seed_start + seed_count):
            result = run_emergence_experiment(
                seed, bus_frequency_multiplier=bus_frequency,
                ride_supply_multiplier=ride_supply, config=run_config,
            )
            per_seed.extend({
                "seed": seed, "weather_scenario": row["weather_week"],
                "day_type": row["day_type"],
                "ride_supply_multiplier": ride_supply,
                "bus_frequency_multiplier": bus_frequency,
                "reference_road_vehicles_per_30_min": road_reference,
                **{key: row[key] for key in METRICS},
            } for row in summarize_macro(result))

    aggregate: list[dict[str, Any]] = []
    distributions: list[dict[str, Any]] = []
    for ride_supply in grid:
        for week in WEATHER_TYPES:
            for day_type in DAY_TYPES:
                rows = [
                    row for row in per_seed
                    if row["ride_supply_multiplier"] == ride_supply
                    and row["weather_scenario"] == week and row["day_type"] == day_type
                ]
                aggregate.append({
                    "ride_supply_multiplier": ride_supply,
                    "bus_frequency_multiplier": bus_frequency,
                    "reference_road_vehicles_per_30_min": road_reference,
                    "weather_scenario": week, "day_type": day_type,
                    **{
                        metric: round(statistics.mean(float(row[metric]) for row in rows), 6)
                        for metric in METRICS
                    },
                })
                for metric in METRICS:
                    distributions.append({
                        "ride_supply_multiplier": ride_supply,
                        "weather_scenario": week, "day_type": day_type,
                        "metric": metric,
                        **_describe([float(row[metric]) for row in rows]),
                    })
    marginal = build_marginal_changes(aggregate, grid)
    thresholds = identify_candidate_thresholds(aggregate, marginal, config)
    schedule_checks = [{
        "seed": seed, "weather_scenario": week, "day_type": day_type,
        "scheduled_bus_vehicle_trips_constant": len({
            row["scheduled_bus_vehicle_trips"] for row in per_seed
            if row["seed"] == seed and row["weather_scenario"] == week and row["day_type"] == day_type
        }) == 1,
    } for seed in range(seed_start, seed_start + seed_count)
      for week in WEATHER_TYPES for day_type in DAY_TYPES]

    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "ride_supply_per_seed.csv", per_seed)
    _write_csv(output / "ride_supply_aggregate.csv", aggregate)
    _write_csv(output / "ride_supply_distribution.csv", distributions)
    _write_csv(output / "ride_supply_marginal_changes.csv", marginal)
    _write_csv(output / "ride_supply_candidate_thresholds.csv", thresholds)
    _write_csv(output / "fixed_bus_schedule_checks.csv", schedule_checks)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            "seed_start": seed_start, "seed_count": seed_count,
            "ride_supply_grid": grid, "fixed_bus_frequency_multiplier": bus_frequency,
            "fixed_reference_road_vehicles_per_30_min": road_reference,
            "rules": experiment,
            "interpretation": "Mechanism threshold scan, not a calibrated optimum or forecast.",
        }, handle, ensure_ascii=False, indent=2)
    return {
        "per_seed": per_seed, "aggregate": aggregate, "distribution": distributions,
        "marginal": marginal, "thresholds": thresholds,
        "schedule_checks": schedule_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/ride_supply_threshold_30")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int, default=30)
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    result = run_supply_threshold_experiment(
        seed_start=seed_start, seed_count=args.seed_count,
        output=Path(args.output), config=config,
    )
    checks = result["schedule_checks"]
    print(f"Completed {len(result['per_seed'])} per-seed weather/day rows")
    print(f"Fixed-bus checks passed: {sum(row['scheduled_bus_vehicle_trips_constant'] for row in checks)}/{len(checks)}")
    print(json.dumps(result["thresholds"], ensure_ascii=False, indent=2))
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
