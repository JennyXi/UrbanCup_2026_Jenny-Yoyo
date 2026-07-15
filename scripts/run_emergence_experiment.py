"""Run the independent shared-feedback emergence experiment."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from custom.agents.emergence_experiment import (
    DAY_TYPES, load_emergence_config, run_emergence_experiment, summarize_macro,
)
from custom.agents.symmetric_weather_experiment import WEATHER_TYPES


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0])
    fieldnames.extend(
        key for row in rows for key in row
        if key not in fieldnames
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def describe(values: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 6),
        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        "median": round(statistics.median(values), 6),
        "minimum": round(min(values), 6), "maximum": round(max(values), 6),
    }


def access_group(row: dict) -> str:
    if row["digital_access"]:
        return "digital"
    return "assisted_non_digital" if row["family_assistance"] else "unassisted_non_digital"


def summarize_groups(result: dict) -> list[dict]:
    rows = []
    legs_by_activity: dict[str, list[dict]] = {}
    for leg in result["leg_results"]:
        legs_by_activity.setdefault(f'{leg["weather_week"]}|{leg["activity_id"]}', []).append(leg)
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            subset = [row for row in result["activity_results"] if row["weather_week"] == week and row["day_type"] == day_type]
            keys = sorted({
                (profile.age_group, access_group({
                    "digital_access": profile.digital_access,
                    "family_assistance": profile.family_assistance,
                })) for profile in result["profiles"]
            })
            for age, access in keys:
                group = [row for row in subset if row["age_group"] == age and access_group(row) == access]
                members = [
                    profile for profile in result["profiles"]
                    if profile.age_group == age and access_group({
                        "digital_access": profile.digital_access,
                        "family_assistance": profile.family_assistance,
                    }) == access
                ]
                legs = [leg for row in group for leg in legs_by_activity.get(f'{week}|{row["activity_id"]}', [])]
                modes = Counter(leg["final_success_mode"] for leg in legs if leg["final_success_mode"])
                successful = sum(modes.values())
                necessary = [row for row in group if row["necessary_activity"]]
                planned_travel_required_necessary = [
                    row for row in necessary if row["travel_required"]
                ]
                completed_travel_required_necessary = [
                    row for row in planned_travel_required_necessary if row["activity_completed"]
                ]
                travel_required = sum(row["travel_required"] for row in group)
                completed_necessary = sum(row["activity_completed"] for row in necessary)
                heat_dose = sum(float(row["heat_hazard_dose_c_min"]) for row in group)
                heat_risk = sum(float(row["heat_risk_burden"]) for row in group)
                necessary_heat_risk = sum(float(row["heat_risk_burden"]) for row in necessary)
                rows.append({
                    "seed": result["seed"], "weather_week": week, "day_type": day_type,
                    "age_group": age, "access_group": access,
                    "agent_count": len(members),
                    "planned_activities": len(group), "weather_cancellations": sum(row["weather_cancellation"] for row in group),
                    "activity_completion_rate": round(sum(row["activity_completed"] for row in group) / len(group), 6) if group else 0.0,
                    "necessary_completion_rate": round(sum(row["activity_completed"] for row in necessary) / len(necessary), 6) if necessary else 1.0,
                    "transport_related_unmet": sum(row["transport_related_unmet"] for row in group),
                    "walking_share": round(modes["walk"] / successful, 6) if successful else 0.0,
                    "bus_share": round(modes["bus"] / successful, 6) if successful else 0.0,
                    "ride_hailing_share": round(modes["ride_hailing"] / successful, 6) if successful else 0.0,
                    "average_wait_min": round(sum(float(leg["cumulative_wait_min"]) for leg in legs) / len(legs), 6) if legs else 0.0,
                    "average_fare_yuan": round(sum(float(leg["cumulative_fare_yuan"]) for leg in legs) / len(legs), 6) if legs else 0.0,
                    "outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in group), 6),
                    "heat_exposure_minutes_alias": round(sum(float(row["heat_exposure_index"]) for row in group), 6),
                    "heat_hazard_dose_c_min": round(heat_dose, 6),
                    "heat_risk_burden": round(heat_risk, 6),
                    "heat_hazard_dose_per_agent": round(heat_dose / len(members), 6) if members else 0.0,
                    "heat_risk_burden_per_agent": round(heat_risk / len(members), 6) if members else 0.0,
                    "heat_risk_per_travel_required_activity": round(heat_risk / travel_required, 6) if travel_required else 0.0,
                    "necessary_heat_risk_burden": round(necessary_heat_risk, 6),
                    "heat_risk_per_completed_travel_required_necessary_activity": round(
                        necessary_heat_risk / len(completed_travel_required_necessary), 6
                    ) if completed_travel_required_necessary else 0.0,
                    "planned_travel_required_necessary_activities": len(planned_travel_required_necessary),
                    "heat_risk_per_planned_travel_required_necessary_activity": round(
                        necessary_heat_risk / len(planned_travel_required_necessary), 6
                    ) if planned_travel_required_necessary else 0.0,
                })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/emergence_experiment")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    parser.add_argument("--bus-frequency-multiplier", type=float, default=1.0)
    parser.add_argument("--ride-supply-multiplier", type=float, default=1.0)
    parser.add_argument("--detail", action="store_true", help="write activity, leg and time-bin detail CSV files")
    args = parser.parse_args()
    config = load_emergence_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(config["seed_count"])
    seeds = list(range(seed_start, seed_start + seed_count))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    macro_rows: list[dict] = []
    group_rows: list[dict] = []
    activity_rows: list[dict] = []
    leg_rows: list[dict] = []
    state_rows: list[dict] = []
    ride_request_rows: list[dict] = []
    ride_vehicle_rows: list[dict] = []
    identity_rows: list[dict] = []
    for seed in seeds:
        result = run_emergence_experiment(
            seed, bus_frequency_multiplier=args.bus_frequency_multiplier,
            ride_supply_multiplier=args.ride_supply_multiplier, config=config,
        )
        macro_rows.extend(summarize_macro(result))
        group_rows.extend(summarize_groups(result))
        if args.detail:
            activity_rows.extend({"seed": seed, **row} for row in result["activity_results"])
            leg_rows.extend({"seed": seed, **row} for row in result["leg_results"])
            state_rows.extend({"seed": seed, **row} for row in result["system_state"])
            ride_request_rows.extend({"seed": seed, **row} for row in result["ride_hailing_requests"])
            ride_vehicle_rows.extend({"seed": seed, **row} for row in result["ride_hailing_vehicle_states"])
        paired = {(row["activity_id"], row["weather_week"]): row for row in result["activity_results"]}
        fields = ("agent_id", "activity_id", "day_type", "activity_purpose", "departure_time", "return_time", "origin_zone", "destination_zone", "distance_km")
        for activity in result["activities"]:
            w0, w1, w2 = (paired[(activity["activity_id"], week)] for week in WEATHER_TYPES)
            identity_rows.append({"seed": seed, "activity_id": activity["activity_id"], "paired_schedule_identical": all(w0[field] == w1[field] == w2[field] for field in fields)})

    metric_names = [key for key, value in macro_rows[0].items() if key not in {"seed", "weather_week", "weather_type", "day_type"} and isinstance(value, (int, float))]
    distribution = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            rows = [row for row in macro_rows if row["weather_week"] == week and row["day_type"] == day_type]
            for metric in metric_names:
                distribution.append({"weather_week": week, "day_type": day_type, "metric": metric, **describe([float(row[metric]) for row in rows])})

    check_rows = []
    checks = {
        "walking_W0_gt_W1_gt_W2": lambda w0, w1, w2: w0["walking_share"] > w1["walking_share"] > w2["walking_share"],
        "ride_hailing_W0_lt_W1_lt_W2": lambda w0, w1, w2: w0["ride_hailing_share"] < w1["ride_hailing_share"] < w2["ride_hailing_share"],
        "fallback_W2_gt_W0": lambda w0, w1, w2: w2["fallback_attempts"] > w0["fallback_attempts"],
        "road_speed_W2_lt_W0": lambda w0, w1, w2: w2["minimum_road_speed_multiplier"] < w0["minimum_road_speed_multiplier"],
        "feedback_changes_some_modes": lambda w0, w1, w2: max(w0["mode_changes_after_feedback"], w1["mode_changes_after_feedback"], w2["mode_changes_after_feedback"]) > 0,
        "shared_supply_constraint_appears": lambda w0, w1, w2: max(w0["supply_constrained_primary_attempts"], w1["supply_constrained_primary_attempts"], w2["supply_constrained_primary_attempts"]) > 0,
    }
    for day_type in DAY_TYPES:
        for name, predicate in checks.items():
            passed = 0
            for seed in seeds:
                lookup = {row["weather_week"]: row for row in macro_rows if row["seed"] == seed and row["day_type"] == day_type}
                passed += int(predicate(lookup["W0"], lookup["W1"], lookup["W2"]))
            check_rows.append({"day_type": day_type, "check": name, "passed_seeds": passed, "total_seeds": seed_count, "pass_rate": round(passed / seed_count, 6)})

    write_csv(output / "per_seed_macro.csv", macro_rows)
    write_csv(output / "per_seed_age_access_group.csv", group_rows)
    write_csv(output / "distribution_summary.csv", distribution)
    write_csv(output / "emergence_direction_checks.csv", check_rows)
    write_csv(output / "paired_schedule_identity_audit.csv", identity_rows)
    if args.detail:
        write_csv(output / "activity_results_all_seeds.csv", activity_rows)
        write_csv(output / "leg_results_all_seeds.csv", leg_rows)
        write_csv(output / "time_bin_system_state_all_seeds.csv", state_rows)
        write_csv(output / "ride_hailing_request_audit_all_seeds.csv", ride_request_rows)
        write_csv(output / "ride_hailing_vehicle_state_all_seeds.csv", ride_vehicle_rows)
    metadata = {
        "seeds": seeds, "bus_frequency_multiplier": args.bus_frequency_multiplier,
        "ride_supply_multiplier": args.ride_supply_multiplier, "detail_written": args.detail,
        "paired_schedule_all_passed": all(row["paired_schedule_identical"] for row in identity_rows),
        "feedback_iterations": 1, "config": config,
        "interpretation": "Mechanism stress test, not a Shanghai forecast.",
    }
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    compact = {
        f"{week}_{day}": {
            metric: describe([float(row[metric]) for row in macro_rows if row["weather_week"] == week and row["day_type"] == day])["mean"]
            for metric in ("planned_activities", "walking_share", "bus_share", "ride_hailing_share", "peak_bus_load_ratio", "bus_over_capacity_bins", "average_ride_system_extra_wait_min", "minimum_road_speed_multiplier", "mode_changes_after_feedback", "fallback_attempts", "transport_related_unmet", "total_heat_hazard_dose_c_min", "total_heat_risk_burden", "heat_risk_per_completed_travel_required_necessary_activity", "heat_risk_per_planned_travel_required_necessary_activity")
        } for week in WEATHER_TYPES for day in DAY_TYPES
    }
    print(json.dumps({"output": str(output.resolve()), "scenario_means": compact, "checks": check_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
