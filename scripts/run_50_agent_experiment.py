"""Command-line entry point for the two-zone 50-agent experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from custom.agents.simple_experiment import run_experiment, write_experiment_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the S1/S2 three-mode experiment")
    parser.add_argument("--agents", type=int, default=50)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--s2-share", type=float, default=0.60)
    parser.add_argument("--output", default="outputs/simple_agent_50")
    args = parser.parse_args()
    result = run_experiment(args.agents, seed=args.seed, s2_share=args.s2_share)
    paths = write_experiment_outputs(result, Path(args.output))

    print("Population:", result["population"])
    print("\nSystem impact by weather:")
    for row in result["system_summaries"]:
        print(
            f"  {row['weather_week']} {row['weather_type']}: "
            f"cancelled_activities={row['cancelled_activity_count']}, "
            f"executed_trips={row['trip_count']}/{row['planned_trip_count']}, "
            f"modes={row['mode_trip_counts']}, shares={row['mode_shares']}, "
            f"avg_time={row['average_travel_time_min']} min, "
            f"avg_fare={row['average_fare_yuan']} yuan, "
            f"ride_hailing_demand={row['ride_hailing_demand']}, "
            f"avg_ride_hailing_wait={row['average_ride_hailing_wait_min']} min, "
            f"ride_hailing_vkm={row['ride_hailing_vehicle_km']}, "
            f"bus_peak_load={row['cross_zone_bus_peak_load_ratio']}"
        )
    print("\nFiles:")
    for label, path in paths.items():
        print(f"  {label}: {path.resolve()}")


if __name__ == "__main__":
    main()
