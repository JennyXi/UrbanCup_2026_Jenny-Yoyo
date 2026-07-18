"""Calibrate a no-coupon 200-agent baseline over three conserved-fleet tiers."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from custom.agents.emergence_experiment import (
    DAY_TYPES,
    load_emergence_config,
    run_emergence_experiment,
    summarize_macro,
)
from custom.agents.symmetric_weather_experiment import (
    WEATHER_TYPES,
    load_symmetric_experiment_config,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "agent_200_c0_fleet_calibration.json"
SUMMARY_METRICS = (
    "planned_activities",
    "activity_completion_rate",
    "necessary_activity_completion_rate",
    "transport_related_unmet",
    "necessary_transport_related_unmet",
    "ride_hailing_requests",
    "successful_ride_hailing_requests",
    "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes_per_request",
    "fallback_attempts",
    "fallback_successes",
    "walking_mode_share",
    "bus_mode_share",
    "ride_hailing_mode_share",
    "mean_total_travel_time",
    "total_travel_time_minutes",
    "road_vehicle_volume",
    "mean_volume_capacity_ratio",
    "peak_road_volume_capacity_ratio",
    "mean_dynamic_congestion_multiplier",
    "mean_road_speed_kmh",
)


def load_calibration_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
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


def _symmetric_without_noncapacity_failure(
    success_probability: float,
) -> dict[str, Any]:
    symmetric = copy.deepcopy(load_symmetric_experiment_config())
    for weather in symmetric["transport_success_probability"].values():
        weather["ride_hailing"] = float(success_probability)
    return symmetric


def _request_summary(
    result: Mapping[str, Any], week: str, day_type: str, initial_total: int,
) -> dict[str, Any]:
    requests = [
        row for row in result["ride_hailing_requests"]
        if row["weather_week"] == week and row["day_type"] == day_type
    ]
    successful = [row for row in requests if bool(row["succeeded"])]
    failures = Counter(str(row["failure_reason"]) for row in requests if not row["succeeded"])
    waits = [float(row["pickup_wait_min"]) for row in requests]
    used_vehicle_ids = {str(row["vehicle_id"]) for row in successful}
    return {
        "no_vehicle_failures": failures["no_vehicle_available"],
        "wait_limit_failures": failures["vehicle_wait_limit_exceeded"],
        "noncapacity_failures": failures["non_capacity_transport_failure"],
        "mean_actual_pickup_wait_min": round(statistics.mean(waits), 6) if waits else 0.0,
        "maximum_actual_pickup_wait_min": round(max(waits), 6) if waits else 0.0,
        "mean_completed_orders_per_initial_vehicle": round(
            len(successful) / initial_total, 6
        ) if initial_total else 0.0,
        "share_vehicles_serving_at_least_one_order": round(
            len(used_vehicle_ids) / initial_total, 6
        ) if initial_total else 0.0,
    }


def _fleet_end_rows(
    result: Mapping[str, Any], *, seed: int, tier: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            states = [
                row for row in result["ride_hailing_vehicle_states"]
                if row["weather_week"] == week and row["day_type"] == day_type
            ]
            for zone in ("S1", "S2"):
                selected = [row for row in states if row["current_zone"] == zone]
                rows.append({
                    "seed": seed,
                    "fleet_tier": tier,
                    "weather_scenario": week,
                    "day_type": day_type,
                    "zone": zone,
                    "end_idle_vehicles": sum(row["status"] == "idle" for row in selected),
                    "end_busy_vehicles": sum(row["status"] == "busy" for row in selected),
                    "end_total_vehicles": len(selected),
                })
    return rows


def _checks(
    result: Mapping[str, Any], *, seed: int, tier: str,
    counts: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            expected = sum(int(value) for value in counts[day_type].values())
            states = [
                row for row in result["ride_hailing_vehicle_states"]
                if row["weather_week"] == week and row["day_type"] == day_type
            ]
            requests = [
                row for row in result["ride_hailing_requests"]
                if row["weather_week"] == week and row["day_type"] == day_type
            ]
            by_vehicle: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for row in requests:
                if row["succeeded"]:
                    by_vehicle[str(row["vehicle_id"])].append(row)
            nonoverlap = True
            for assignments in by_vehicle.values():
                assignments.sort(key=lambda row: float(row["busy_start"]))
                nonoverlap &= all(
                    float(right["busy_start"]) >= float(left["busy_until"]) - 1e-9
                    for left, right in zip(assignments, assignments[1:])
                )
            conserved = len(states) == expected
            noncapacity_disabled = all(
                row["failure_reason"] != "non_capacity_transport_failure"
                for row in requests
            )
            output.append({
                "seed": seed,
                "fleet_tier": tier,
                "weather_scenario": week,
                "day_type": day_type,
                "expected_initial_vehicles": expected,
                "final_vehicle_records": len(states),
                "vehicle_total_conserved": conserved,
                "vehicle_assignments_nonoverlapping": nonoverlap,
                "noncapacity_failure_disabled": noncapacity_disabled,
                "passed": conserved and nonoverlap and noncapacity_disabled,
            })
    return output


def run_calibration(
    *, seed_start: int, seed_count: int, output: Path,
    tier_names: list[str] | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    calibration = calibration or load_calibration_config()
    base = load_emergence_config()
    symmetric = _symmetric_without_noncapacity_failure(
        float(calibration["ride_hailing_noncapacity_success_probability"])
    )
    configured_tiers = calibration["fleet_tiers"]
    selected_tiers = tier_names or list(configured_tiers)
    per_seed: list[dict[str, Any]] = []
    end_states: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for tier in selected_tiers:
        counts = configured_tiers[tier]
        local = copy.deepcopy(base)
        local["total_agents"] = int(calibration["total_agents"])
        local["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"] = copy.deepcopy(counts)
        for seed in range(seed_start, seed_start + seed_count):
            result = run_emergence_experiment(
                seed,
                bus_frequency_multiplier=float(calibration["bus_frequency_multiplier"]),
                ride_supply_multiplier=1.0,
                config=local,
                symmetric=symmetric,
            )
            for macro in summarize_macro(result):
                day_type = macro["day_type"]
                initial_by_zone = counts[day_type]
                initial_total = sum(int(value) for value in initial_by_zone.values())
                per_seed.append({
                    "seed": seed,
                    "fleet_tier": tier,
                    "population_agents": int(calibration["total_agents"]),
                    "weather_scenario": macro["weather_week"],
                    "day_type": day_type,
                    "initial_vehicles_s1": int(initial_by_zone["S1"]),
                    "initial_vehicles_s2": int(initial_by_zone["S2"]),
                    "initial_vehicles_total": initial_total,
                    **macro,
                    **_request_summary(result, macro["weather_week"], day_type, initial_total),
                })
            end_states.extend(_fleet_end_rows(result, seed=seed, tier=tier))
            checks.extend(_checks(result, seed=seed, tier=tier, counts=counts))

    aggregate: list[dict[str, Any]] = []
    audit_metrics = (
        "no_vehicle_failures", "wait_limit_failures", "noncapacity_failures",
        "mean_actual_pickup_wait_min", "maximum_actual_pickup_wait_min",
        "mean_completed_orders_per_initial_vehicle",
        "share_vehicles_serving_at_least_one_order",
    )
    for tier in selected_tiers:
        for week in WEATHER_TYPES:
            for day_type in DAY_TYPES:
                selected = [
                    row for row in per_seed
                    if row["fleet_tier"] == tier
                    and row["weather_scenario"] == week
                    and row["day_type"] == day_type
                ]
                first = selected[0]
                aggregate.append({
                    "fleet_tier": tier,
                    "weather_scenario": week,
                    "day_type": day_type,
                    "seed_count": len(selected),
                    "population_agents": first["population_agents"],
                    "initial_vehicles_s1": first["initial_vehicles_s1"],
                    "initial_vehicles_s2": first["initial_vehicles_s2"],
                    "initial_vehicles_total": first["initial_vehicles_total"],
                    **{
                        metric: round(_mean(selected, metric), 6)
                        for metric in (*SUMMARY_METRICS, *audit_metrics)
                    },
                })

    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "c0_fleet_per_seed": per_seed,
        "c0_fleet_summary": aggregate,
        "c0_fleet_end_zone_states": end_states,
        "c0_fleet_consistency_checks": checks,
    }
    for name, rows in tables.items():
        _write_csv(output / f"{name}.csv", rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            **dict(calibration),
            "seed_start": seed_start,
            "seed_count": seed_count,
            "selected_fleet_tiers": selected_tiers,
            "coupon_policy": "C0_no_coupon",
            "formal_model_configuration_modified": False,
        }, handle, ensure_ascii=False, indent=2)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/agent_200_c0_fleet_calibration_3")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    parser.add_argument("--tiers", nargs="+", choices=("low", "middle", "proportional"))
    args = parser.parse_args()
    calibration = load_calibration_config()
    seed_start = args.seed_start if args.seed_start is not None else int(calibration["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(calibration["default_seed_count"])
    tables = run_calibration(
        seed_start=seed_start,
        seed_count=seed_count,
        output=Path(args.output),
        tier_names=args.tiers,
        calibration=calibration,
    )
    checks = tables["c0_fleet_consistency_checks"]
    print(f"Completed {len(tables['c0_fleet_per_seed'])} fleet-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
