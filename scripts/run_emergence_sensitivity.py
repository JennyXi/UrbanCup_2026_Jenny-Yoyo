"""Sweep shared supply multipliers to reveal thresholds and nonlinear responses."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

from custom.agents.emergence_experiment import DAY_TYPES, load_emergence_config, run_emergence_experiment, summarize_macro
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES


def parse_grid(value: str) -> list[float]:
    values = sorted({float(item.strip()) for item in value.split(",") if item.strip()})
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("grid values must be positive comma-separated numbers")
    return values


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/emergence_sensitivity")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int, default=30)
    parser.add_argument("--bus-grid", type=parse_grid, default=parse_grid("0.60,0.80,1.00,1.20,1.50"))
    parser.add_argument("--ride-grid", type=parse_grid, default=parse_grid("0.60,1.00,1.40"))
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    seeds = range(seed_start, seed_start + args.seed_count)
    per_seed = []
    for bus_multiplier in args.bus_grid:
        for ride_multiplier in args.ride_grid:
            for seed in seeds:
                result = run_emergence_experiment(
                    seed, bus_frequency_multiplier=bus_multiplier,
                    ride_supply_multiplier=ride_multiplier, config=config,
                )
                per_seed.extend({
                    "bus_frequency_multiplier": bus_multiplier,
                    "ride_supply_multiplier": ride_multiplier,
                    **row,
                } for row in summarize_macro(result))
    metrics = (
        "walking_share", "bus_share", "ride_hailing_share", "peak_bus_load_ratio",
        "bus_over_capacity_bins", "peak_ride_demand_supply_ratio",
        "average_ride_system_extra_wait_min", "minimum_road_speed_multiplier",
        "mode_changes_after_feedback", "supply_constrained_primary_attempts",
        "fallback_attempts", "transport_failures", "transport_related_unmet",
        "necessary_activity_completion_rate", "total_wait_min", "total_fare_yuan",
        "total_outdoor_exposure_minutes", "total_heat_exposure_minutes",
        "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
        "necessary_heat_risk_burden", "mean_heat_risk_per_travel_required_activity",
        "heat_risk_per_completed_travel_required_necessary_activity",
        "planned_travel_required_necessary_activities",
        "heat_risk_per_planned_travel_required_necessary_activity",
    )
    aggregate = []
    for bus_multiplier in args.bus_grid:
        for ride_multiplier in args.ride_grid:
            for week in WEATHER_TYPES:
                for day_type in DAY_TYPES:
                    rows = [
                        row for row in per_seed
                        if row["bus_frequency_multiplier"] == bus_multiplier
                        and row["ride_supply_multiplier"] == ride_multiplier
                        and row["weather_week"] == week and row["day_type"] == day_type
                    ]
                    aggregate.append({
                        "bus_frequency_multiplier": bus_multiplier,
                        "ride_supply_multiplier": ride_multiplier,
                        "weather_week": week, "day_type": day_type,
                        **{f"mean_{metric}": round(statistics.mean(float(row[metric]) for row in rows), 6) for metric in metrics},
                        "seeds_with_bus_overload_rate": round(sum(row["bus_over_capacity_bins"] > 0 for row in rows) / len(rows), 6),
                        "seeds_with_transport_unmet_rate": round(sum(row["transport_related_unmet"] > 0 for row in rows) / len(rows), 6),
                    })
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "sensitivity_per_seed.csv", per_seed)
    write_csv(output / "sensitivity_aggregate.csv", aggregate)
    print(f"Completed {len(args.bus_grid)} x {len(args.ride_grid)} supply scenarios x {args.seed_count} seeds")
    print(f"Aggregate: {(output / 'sensitivity_aggregate.csv').resolve()}")
    print(f"Per seed: {(output / 'sensitivity_per_seed.csv').resolve()}")


if __name__ == "__main__":
    main()
