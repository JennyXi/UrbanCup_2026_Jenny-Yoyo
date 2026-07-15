"""Screen 50/100/200/500-agent demand loads under fixed transport supply."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.emergence_experiment import DAY_TYPES, load_emergence_config
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES
from scripts.run_elder_digital_access_experiment import run_digital_access_experiment


POLICIES = (
    "D0_baseline", "D1_targeted_digital_training_75pct",
    "D2_family_assistance_90pct", "D3_universal_elder_digital_access",
)
SCREEN_METRICS = (
    "planned_activities", "activity_completion_rate",
    "necessary_activity_completion_rate", "transport_related_unmet",
    "necessary_transport_related_unmet", "walking_mode_share", "bus_mode_share",
    "ride_hailing_mode_share", "peak_bus_load_ratio", "bus_over_capacity_bins",
    "peak_ride_demand_supply_ratio", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "fallback_attempts", "fallback_successes",
    "mean_bus_wait_minutes_per_attempt", "mean_ride_hailing_wait_minutes_per_request",
    "mean_total_travel_time", "total_travel_time_minutes",
    "total_in_vehicle_time_minutes", "road_vehicle_volume",
    "peak_road_volume_capacity_ratio", "minimum_road_speed_multiplier",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _mean(rows: Iterable[Mapping[str, Any]], metric: str) -> float:
    values = [float(row[metric]) for row in rows]
    return statistics.mean(values) if values else 0.0


def summarize_scale_system(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for agent_count in sorted({int(row["population_agents"]) for row in rows}):
        for policy in POLICIES:
            for week in WEATHER_TYPES:
                for day_type in DAY_TYPES:
                    selected = [row for row in rows if
                                int(row["population_agents"]) == agent_count
                                and row["policy"] == policy
                                and row["weather_week"] == week
                                and row["day_type"] == day_type]
                    output.append({
                        "population_agents": agent_count, "policy": policy,
                        "weather_scenario": week, "day_type": day_type,
                        "seed_count": len(selected),
                        **{metric: round(_mean(selected, metric), 6) for metric in SCREEN_METRICS},
                    })
    return output


def _group_mean(
    rows: list[dict[str, Any]], *, agent_count: int, policy: str,
    week: str, day_type: str, group: str, metric: str,
) -> float:
    selected = [row for row in rows if
                int(row["population_agents"]) == agent_count and row["policy"] == policy
                and row["weather_scenario"] == week and row["day_type"] == day_type
                and row["baseline_access_group"] == group]
    if metric == "necessary_activity_completion_rate":
        selected = [row for row in selected if float(row["planned_necessary_activities"]) > 0]
    if metric == "ride_wait_per_request":
        requests = sum(float(row["ride_hailing_requests"]) for row in selected)
        waits = sum(float(row["total_ride_hailing_wait_minutes"]) for row in selected)
        return waits / requests if requests else 0.0
    return _mean(selected, metric)


def build_d3_spillovers(
    system_summary: list[dict[str, Any]], group_rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    flags = config["agent_scale_screen"]["competition_flags"]
    lookup = {
        (int(row["population_agents"]), row["policy"], row["weather_scenario"], row["day_type"]): row
        for row in system_summary
    }
    for agent_count in sorted({key[0] for key in lookup}):
        for week in WEATHER_TYPES:
            for day_type in DAY_TYPES:
                base = lookup[(agent_count, "D0_baseline", week, day_type)]
                policy = lookup[(agent_count, "D3_universal_elder_digital_access", week, day_type)]
                target_base_unmet = _group_mean(
                    group_rows, agent_count=agent_count, policy="D0_baseline",
                    week=week, day_type=day_type,
                    group="elder_baseline_nondigital_unassisted", metric="transport_related_unmet",
                )
                target_policy_unmet = _group_mean(
                    group_rows, agent_count=agent_count, policy="D3_universal_elder_digital_access",
                    week=week, day_type=day_type,
                    group="elder_baseline_nondigital_unassisted", metric="transport_related_unmet",
                )
                target_base_completion = _group_mean(
                    group_rows, agent_count=agent_count, policy="D0_baseline",
                    week=week, day_type=day_type,
                    group="elder_baseline_nondigital_unassisted", metric="necessary_activity_completion_rate",
                )
                target_policy_completion = _group_mean(
                    group_rows, agent_count=agent_count, policy="D3_universal_elder_digital_access",
                    week=week, day_type=day_type,
                    group="elder_baseline_nondigital_unassisted", metric="necessary_activity_completion_rate",
                )
                under60_base_wait = _group_mean(
                    group_rows, agent_count=agent_count, policy="D0_baseline",
                    week=week, day_type=day_type, group="under_60", metric="ride_wait_per_request",
                )
                under60_policy_wait = _group_mean(
                    group_rows, agent_count=agent_count, policy="D3_universal_elder_digital_access",
                    week=week, day_type=day_type, group="under_60", metric="ride_wait_per_request",
                )
                under60_base_unmet = _group_mean(
                    group_rows, agent_count=agent_count, policy="D0_baseline",
                    week=week, day_type=day_type, group="under_60", metric="transport_related_unmet",
                )
                under60_policy_unmet = _group_mean(
                    group_rows, agent_count=agent_count, policy="D3_universal_elder_digital_access",
                    week=week, day_type=day_type, group="under_60", metric="transport_related_unmet",
                )
                under60_base_time = _group_mean(
                    group_rows, agent_count=agent_count, policy="D0_baseline",
                    week=week, day_type=day_type, group="under_60", metric="total_travel_time_minutes",
                )
                under60_policy_time = _group_mean(
                    group_rows, agent_count=agent_count, policy="D3_universal_elder_digital_access",
                    week=week, day_type=day_type, group="under_60", metric="total_travel_time_minutes",
                )
                ride_competition = float(policy["peak_ride_demand_supply_ratio"]) >= float(flags["ride_demand_supply_ratio"])
                road_pressure = float(policy["peak_road_volume_capacity_ratio"]) >= float(flags["road_volume_capacity_ratio"])
                wait_displacement = under60_policy_wait - under60_base_wait >= float(flags["under60_ride_wait_increase_minutes"])
                unmet_displacement = under60_policy_unmet > under60_base_unmet
                output.append({
                    "population_agents": agent_count, "weather_scenario": week, "day_type": day_type,
                    "d3_elder_unassisted_transport_unmet_change": round(target_policy_unmet - target_base_unmet, 6),
                    "d3_elder_unassisted_necessary_completion_change": round(target_policy_completion - target_base_completion, 6),
                    "d3_system_ride_requests_change": round(float(policy["ride_hailing_requests"]) - float(base["ride_hailing_requests"]), 6),
                    "d3_system_failed_ride_requests_change": round(float(policy["failed_ride_hailing_requests"]) - float(base["failed_ride_hailing_requests"]), 6),
                    "d3_system_total_travel_time_change_minutes": round(float(policy["total_travel_time_minutes"]) - float(base["total_travel_time_minutes"]), 6),
                    "d3_system_road_vehicle_change": round(float(policy["road_vehicle_volume"]) - float(base["road_vehicle_volume"]), 6),
                    "d3_peak_ride_demand_supply_ratio": policy["peak_ride_demand_supply_ratio"],
                    "d3_peak_road_volume_capacity_ratio": policy["peak_road_volume_capacity_ratio"],
                    "d3_minimum_road_speed_multiplier": policy["minimum_road_speed_multiplier"],
                    "under60_ride_wait_change_minutes_per_request": round(under60_policy_wait - under60_base_wait, 6),
                    "under60_transport_unmet_change": round(under60_policy_unmet - under60_base_unmet, 6),
                    "under60_total_travel_time_change_minutes": round(under60_policy_time - under60_base_time, 6),
                    "ride_supply_competition_flag": ride_competition,
                    "road_pressure_flag": road_pressure,
                    "under60_wait_displacement_flag": wait_displacement,
                    "under60_unmet_displacement_flag": unmet_displacement,
                    "any_competition_or_displacement_flag": (
                        ride_competition or road_pressure or wait_displacement or unmet_displacement
                    ),
                })
    return output


def build_candidate_scales(spillovers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    flag_names = (
        "ride_supply_competition_flag", "road_pressure_flag",
        "under60_wait_displacement_flag", "under60_unmet_displacement_flag",
        "any_competition_or_displacement_flag",
    )
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            rows = sorted(
                (row for row in spillovers if row["weather_scenario"] == week and row["day_type"] == day_type),
                key=lambda row: int(row["population_agents"]),
            )
            for flag in flag_names:
                first = next((row for row in rows if row[flag]), None)
                output.append({
                    "weather_scenario": week, "day_type": day_type, "criterion": flag,
                    "first_agent_count": first["population_agents"] if first else "",
                    "found_within_tested_scales": first is not None,
                    "note": "first tested scale meeting the mechanism flag" if first else "not found within tested scales",
                    "not_a_calibrated_population_threshold": True,
                })
    return output


def run_agent_scale_screen(
    *, seed_start: int, seed_count: int, output: Path,
    agent_counts: list[int] | None = None, config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    config = config or load_emergence_config()
    screen = config["agent_scale_screen"]
    counts = agent_counts or [int(value) for value in screen["agent_counts"]]
    system_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for count in counts:
        local = copy.deepcopy(config)
        local["total_agents"] = int(count)
        local["road_feedback"]["reference_road_vehicles_per_30_min"] = float(
            screen["fixed_reference_road_vehicles_per_30_min"]
        )
        result = run_digital_access_experiment(
            seed_start=seed_start, seed_count=seed_count,
            output=output / f"agents_{count}", config=local,
        )
        system_rows.extend({"population_agents": count, **row} for row in result["system_per_seed"])
        group_rows.extend({"population_agents": count, **row} for row in result["group_per_seed"])
        checks.extend({"population_agents": count, **row} for row in result["consistency_checks"])
    summary = summarize_scale_system(system_rows)
    spillovers = build_d3_spillovers(summary, group_rows, config)
    candidates = build_candidate_scales(spillovers)
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "scale_system_per_seed": system_rows,
        "scale_group_per_seed": group_rows,
        "scale_screen_summary": summary,
        "d3_spillover_vs_d0": spillovers,
        "candidate_competition_scales": candidates,
        "scale_consistency_checks": checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "scale_screen_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            "agent_counts": counts, "seed_start": seed_start, "seed_count": seed_count,
            "fixed_supply": {
                "bus_frequency_multiplier": screen["fixed_bus_frequency_multiplier"],
                "ride_supply_multiplier": screen["fixed_ride_supply_multiplier"],
                "reference_road_vehicles_per_30_min": screen["fixed_reference_road_vehicles_per_30_min"],
            },
            "flags": screen["competition_flags"], "interpretation": screen["interpretation"],
        }, handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/agent_scale_screen_3")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    parser.add_argument("--agent-counts", type=int, nargs="+", help="subset of configured scales, e.g. 100 200")
    args = parser.parse_args()
    config = load_emergence_config()
    screen = config["agent_scale_screen"]
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(screen["default_seed_count"])
    configured_counts = [int(value) for value in screen["agent_counts"]]
    agent_counts = args.agent_counts or configured_counts
    if agent_counts != sorted(set(agent_counts)) or any(value not in configured_counts for value in agent_counts):
        parser.error(f"--agent-counts must be sorted unique values from {configured_counts}")
    result = run_agent_scale_screen(
        seed_start=seed_start, seed_count=seed_count, output=Path(args.output),
        agent_counts=agent_counts, config=config,
    )
    checks = result["scale_consistency_checks"]
    print(f"Completed {len(result['scale_system_per_seed'])} scale-policy-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(json.dumps(result["candidate_competition_scales"], ensure_ascii=False, indent=2))
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
