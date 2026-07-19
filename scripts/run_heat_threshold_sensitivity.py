"""Compare heat-risk accounting thresholds without changing travel behavior."""

from __future__ import annotations

import argparse
import copy
import csv
import statistics
from pathlib import Path
from typing import Any, Mapping

from custom.agents.emergence_experiment import (
    DAY_TYPES, load_emergence_config, run_emergence_experiment, summarize_macro,
)


METRICS = (
    "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
    "necessary_activity_completion_rate",
    "heat_risk_per_completed_travel_required_necessary_activity",
    "heat_risk_per_planned_travel_required_necessary_activity",
)

AGGREGATE_METRICS = (
    "planned_activities", "completed_activities", "activity_completion_rate",
    "planned_necessary_activities", "completed_necessary_activities",
    "necessary_activity_completion_rate", "weather_cancelled_activities",
    "transport_related_unmet", "necessary_transport_related_unmet",
    "walking_legs", "bus_legs", "ride_hailing_legs",
    "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
    "fallback_attempts", "fallback_successes", "transport_success_rate",
    "total_bus_wait_minutes", "total_ride_hailing_wait_minutes",
    "total_system_wait_minutes", "mean_bus_wait_minutes_per_attempt",
    "mean_ride_hailing_wait_minutes_per_request", "mean_total_travel_time",
    "bus_demand", "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "scheduled_bus_vehicle_trips",
    "successful_ride_hailing_vehicle_trips", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "mean_dynamic_congestion_multiplier",
    "mean_road_speed_kmh", "total_outdoor_exposure_minutes",
    "total_heat_hazard_dose_c_min", "total_heat_risk_burden",
    "necessary_heat_risk_burden",
    "heat_risk_per_completed_travel_required_necessary_activity",
    "heat_risk_per_planned_travel_required_necessary_activity",
)

NON_HEAT_POLICY_CHANGE_METRICS = (
    "necessary_activity_completion_rate", "transport_related_unmet",
    "necessary_transport_related_unmet", "mean_bus_wait_minutes_per_attempt",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts",
    "walking_mode_share", "bus_mode_share", "ride_hailing_mode_share",
    "mean_road_speed_kmh",
)

HEAT_POLICY_CHANGE_METRICS = (
    "total_heat_hazard_dose_c_min", "necessary_heat_risk_burden",
    "heat_risk_per_completed_travel_required_necessary_activity",
    "heat_risk_per_planned_travel_required_necessary_activity",
)

POLICY_CHANGE_METRICS = NON_HEAT_POLICY_CHANGE_METRICS + HEAT_POLICY_CHANGE_METRICS

THRESHOLD_INVARIANT_METRICS = (
    "planned_activities", "completed_activities", "activity_completion_rate",
    "planned_necessary_activities", "completed_necessary_activities",
    "necessary_activity_completion_rate", "weather_cancelled_activities",
    "transport_related_unmet", "necessary_transport_related_unmet",
    "walking_legs", "bus_legs", "ride_hailing_legs", "walking_mode_share",
    "bus_mode_share", "ride_hailing_mode_share", "fallback_attempts",
    "fallback_successes", "transport_success_rate", "total_bus_wait_minutes",
    "total_ride_hailing_wait_minutes", "total_system_wait_minutes",
    "mean_bus_wait_minutes_per_attempt", "mean_ride_hailing_wait_minutes_per_request",
    "mean_total_travel_time", "total_wait_min", "total_fare_yuan", "bus_demand",
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "scheduled_bus_vehicle_trips",
    "successful_ride_hailing_vehicle_trips", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "mean_dynamic_congestion_multiplier",
    "mean_road_speed_kmh",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_policy_changes(per_seed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return long-form P1/P2 changes against P0; zero baselines yield blanks."""
    baseline_policy = "P0_baseline"
    lookup = {
        (row["seed"], row["weather_scenario"], row["day_type"], row["heat_threshold_c"], row["policy"]): row
        for row in per_seed
    }
    changes: list[dict[str, Any]] = []
    for row in per_seed:
        if row["policy"] == baseline_policy:
            continue
        baseline = lookup[(
            row["seed"], row["weather_scenario"], row["day_type"],
            row["heat_threshold_c"], baseline_policy,
        )]
        metrics = (
            POLICY_CHANGE_METRICS if float(row["heat_threshold_c"]) == 26.0
            else HEAT_POLICY_CHANGE_METRICS
        )
        for metric in metrics:
            base_value = float(baseline[metric])
            policy_value = float(row[metric])
            absolute = policy_value - base_value
            changes.append({
                "seed": row["seed"], "weather_scenario": row["weather_scenario"],
                "day_type": row["day_type"], "policy": row["policy"],
                "heat_threshold_c": row["heat_threshold_c"], "baseline_policy": baseline_policy,
                "metric": metric, "baseline_value": base_value,
                "policy_value": policy_value, "absolute_change": round(absolute, 6),
                "percent_change": round(absolute / base_value * 100.0, 6) if base_value != 0 else "",
                "percent_change_defined": base_value != 0,
                "undefined_reason": "" if base_value != 0 else "baseline_zero",
            })
    return changes


def build_consistency_checks(per_seed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(row: Mapping[str, Any], name: str, passed: bool, detail: str) -> None:
        checks.append({
            "seed": row["seed"], "weather_scenario": row["weather_scenario"],
            "day_type": row["day_type"], "policy": row["policy"],
            "heat_threshold_c": row["heat_threshold_c"], "check": name,
            "passed": bool(passed), "detail": detail,
        })

    for row in per_seed:
        mode_sum = row["walking_legs"] + row["bus_legs"] + row["ride_hailing_legs"]
        add(row, "successful_mode_leg_conservation", mode_sum == row["successful_legs"], f"mode_sum={mode_sum}; successful_legs={row['successful_legs']}")
        attempts_ok = (
            row["fallback_successes"] <= row["fallback_attempts"]
            and row["failed_ride_hailing_requests"] >= 0
        )
        add(row, "fallback_conservation", attempts_ok, f"fallback_attempts={row['fallback_attempts']}; fallback_successes={row['fallback_successes']}")
        final_category_sum = (
            row["completed_activities"] + row["weather_cancelled_activities"]
            + row["transport_related_unmet"]
        )
        add(row, "exclusive_activity_final_status", final_category_sum == row["planned_activities"], f"category_sum={final_category_sum}; planned={row['planned_activities']}")
        road_sum = row["scheduled_bus_vehicle_trips"] + row["successful_ride_hailing_vehicle_trips"]
        add(row, "shared_road_vehicle_conservation", abs(road_sum - row["road_vehicle_volume"]) <= 1e-9, f"bus_plus_ride={road_sum}; road_volume={row['road_vehicle_volume']}")
        share_sum = row["walking_mode_share"] + row["bus_mode_share"] + row["ride_hailing_mode_share"]
        expected_share = 1.0 if row["successful_legs"] else 0.0
        add(row, "mode_shares_sum", abs(share_sum - expected_share) <= 2e-6, f"share_sum={share_sum}")

    grouped_policy: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in per_seed:
        grouped_policy.setdefault((row["seed"], row["weather_scenario"], row["day_type"], row["heat_threshold_c"]), []).append(row)
    for rows in grouped_policy.values():
        add(rows[0], "policy_preserves_planned_activities", len({row["planned_activities"] for row in rows}) == 1, "compared all policy scenarios")
        by_policy = {row["policy"]: row for row in rows}
        schedule_ok = (
            by_policy["P1_bus_frequency_plus_50pct"]["scheduled_bus_vehicle_trips"]
            > by_policy["P0_baseline"]["scheduled_bus_vehicle_trips"]
            == by_policy["P2_ride_supply_plus_40pct"]["scheduled_bus_vehicle_trips"]
        )
        add(rows[0], "bus_frequency_policy_changes_trips_only_in_p1", schedule_ok, "P1 frequency exceeds P0; P2 preserves P0 bus schedule")

    grouped_threshold: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in per_seed:
        grouped_threshold.setdefault((row["seed"], row["weather_scenario"], row["day_type"], row["policy"]), []).append(row)
    for rows in grouped_threshold.values():
        invariant = all(len({row[metric] for row in rows}) == 1 for metric in THRESHOLD_INVARIANT_METRICS)
        add(rows[0], "heat_threshold_changes_heat_only", invariant, "compared activity, mode, wait, fare and congestion fields across thresholds")
    return checks


def run_threshold_sensitivity(
    *, seed_start: int, seed_count: int, output: Path,
    config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    base = copy.deepcopy(config or load_emergence_config())
    sensitivity = base["heat_threshold_sensitivity"]
    thresholds = [
        float(value)
        for value in base["heat_exposure"]["heat_stress_threshold_sensitivity_c"]
    ]
    scenarios = sensitivity["policy_scenarios"]
    per_seed: list[dict[str, Any]] = []
    for threshold in thresholds:
        threshold_config = copy.deepcopy(base)
        threshold_config["heat_exposure"]["heat_stress_threshold_c"] = threshold
        for policy, parameters in scenarios.items():
            for seed in range(seed_start, seed_start + seed_count):
                result = run_emergence_experiment(
                    seed,
                    bus_frequency_multiplier=float(parameters["bus_frequency_multiplier"]),
                    ride_supply_multiplier=float(parameters["ride_supply_multiplier"]),
                    config=threshold_config,
                )
                for row in summarize_macro(result):
                    if row["weather_week"] == "W1":
                        per_seed.append({
                            "seed": row["seed"],
                            "weather_scenario": row["weather_week"],
                            "day_type": row["day_type"],
                            "policy": policy,
                            "heat_threshold_c": threshold,
                            "non_heat_analysis_eligible": threshold == 26.0,
                            "heat_sensitivity_only": threshold == 32.0,
                            "heat_stress_threshold_c": threshold,
                            "policy_scenario": policy,
                            "bus_frequency_multiplier": parameters["bus_frequency_multiplier"],
                            "ride_supply_multiplier": parameters["ride_supply_multiplier"],
                            **{
                                key: value for key, value in row.items()
                                if key not in {"seed", "day_type"}
                            },
                        })

    aggregate: list[dict[str, Any]] = []
    for threshold in thresholds:
        for policy in scenarios:
            for day_type in DAY_TYPES:
                rows = [
                    row for row in per_seed
                    if row["heat_stress_threshold_c"] == threshold
                    and row["policy_scenario"] == policy
                    and row["day_type"] == day_type
                ]
                aggregate.append({
                    "heat_stress_threshold_c": threshold,
                    "policy_scenario": policy,
                    "day_type": day_type,
                    **{
                        metric: round(statistics.mean(float(row[metric]) for row in rows), 6)
                        for metric in AGGREGATE_METRICS
                    },
                })

    ranking: list[dict[str, Any]] = []
    lower_is_better = set(METRICS) - {"necessary_activity_completion_rate"}
    for threshold in thresholds:
        for day_type in DAY_TYPES:
            rows = [
                row for row in aggregate
                if row["heat_stress_threshold_c"] == threshold and row["day_type"] == day_type
            ]
            for metric in METRICS:
                ordered = sorted(
                    rows,
                    key=lambda row: (
                        float(row[metric]) if metric in lower_is_better else -float(row[metric]),
                        str(row["policy_scenario"]),
                    ),
                )
                tied_groups: list[list[str]] = []
                tied_values: list[float] = []
                for row in ordered:
                    value = float(row[metric])
                    if tied_values and abs(value - tied_values[-1]) <= 1e-12:
                        tied_groups[-1].append(str(row["policy_scenario"]))
                    else:
                        tied_values.append(value)
                        tied_groups.append([str(row["policy_scenario"])])
                ranking.append({
                    "heat_stress_threshold_c": threshold,
                    "day_type": day_type,
                    "metric": metric,
                    "policy_order_best_to_worst": " > ".join(
                        " = ".join(group) for group in tied_groups
                    ),
                    "tie_note": "equals sign denotes an exact aggregate-metric tie",
                })
    comparison: list[dict[str, Any]] = []
    for day_type in DAY_TYPES:
        for metric in METRICS:
            rows = [row for row in ranking if row["day_type"] == day_type and row["metric"] == metric]
            orders = {float(row["heat_stress_threshold_c"]): row["policy_order_best_to_worst"] for row in rows}
            comparison.append({
                "day_type": day_type,
                "metric": metric,
                **{f"ranking_at_{threshold:g}c": orders[threshold] for threshold in thresholds},
                "ranking_changed": len(set(orders.values())) > 1,
            })

    policy_changes = build_policy_changes(per_seed)
    consistency_checks = build_consistency_checks(per_seed)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "unified_per_seed_macro.csv", per_seed)
    _write_csv(output / "heat_threshold_per_seed.csv", per_seed)
    _write_csv(output / "heat_threshold_aggregate.csv", aggregate)
    _write_csv(output / "heat_threshold_policy_ranking.csv", ranking)
    _write_csv(output / "heat_threshold_ranking_comparison.csv", comparison)
    _write_csv(output / "policy_changes_vs_p0.csv", policy_changes)
    _write_csv(output / "consistency_checks.csv", consistency_checks)
    return {
        "per_seed": per_seed, "aggregate": aggregate, "ranking": ranking,
        "comparison": comparison, "policy_changes": policy_changes,
        "consistency_checks": consistency_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/heat_threshold_sensitivity")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int, default=3)
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    result = run_threshold_sensitivity(
        seed_start=seed_start, seed_count=args.seed_count,
        output=Path(args.output), config=config,
    )
    print(f"Completed {len(result['per_seed'])} W1 day-type rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in result['consistency_checks'])}/{len(result['consistency_checks'])}")
    print(f"Unified macro: {(Path(args.output) / 'unified_per_seed_macro.csv').resolve()}")
    print(f"Policy changes: {(Path(args.output) / 'policy_changes_vs_p0.csv').resolve()}")
    print(f"Aggregate: {(Path(args.output) / 'heat_threshold_aggregate.csv').resolve()}")
    print(f"Ranking comparison: {(Path(args.output) / 'heat_threshold_ranking_comparison.csv').resolve()}")


if __name__ == "__main__":
    main()
