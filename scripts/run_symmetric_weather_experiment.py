"""Run the independent necessary-activity state-machine audit over many seeds."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from custom.agents.symmetric_weather_experiment import (
    DAY_TYPES, PURPOSES, WEATHER_TYPES, load_symmetric_experiment_config,
    run_symmetric_experiment, summarize_seed,
)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def describe(values: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 6),
        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        "median": round(statistics.median(values), 6),
        "minimum": round(min(values), 6), "maximum": round(max(values), 6),
    }


def aggregate_weather_day(seed: int, week: str, day_type: str, rows: list[dict]) -> dict:
    work = [row for row in rows if row["activity_purpose"] == "work"]
    travel = [row for row in rows if row["travel_required"]]
    necessary = [row for row in rows if row["necessary_activity"]]
    modes = Counter(
        mode for row in rows for mode in (row["outbound_final_mode"], row["return_final_mode"]) if mode
    )
    fallback_uses = sum(row["outbound_fallback_used"] + row["return_fallback_used"] for row in rows)
    fallback_success = sum(row["outbound_fallback_success"] + row["return_fallback_success"] for row in rows)
    generated_legs = sum(row["outbound_leg_generated"] + row["return_leg_generated"] for row in rows)
    requests = sum(row["ride_hailing_request_count"] for row in rows)
    return {
        "seed": seed, "weather_week": week, "weather_type": WEATHER_TYPES[week], "day_type": day_type,
        "planned_activities": len(rows),
        "remote_work_count": sum(row["remote_work"] for row in work),
        "remote_work_rate": round(sum(row["remote_work"] for row in work) / len(work), 6) if work else 0.0,
        "work_weather_exposed_count": sum(row["work_weather_exposed"] for row in work),
        "remote_work_rate_among_exposed_work": round(
            sum(row["remote_work"] for row in work) / sum(row["work_weather_exposed"] for row in work), 6
        ) if any(row["work_weather_exposed"] for row in work) else 0.0,
        "travel_required_count": len(travel),
        "weather_cancellations": sum(row["weather_cancellation"] for row in rows),
        "generated_legs": generated_legs,
        "initial_walk_count": sum(row["outbound_initial_mode"] == "walk" for row in rows),
        "initial_bus_count": sum(row["outbound_initial_mode"] == "bus" for row in rows),
        "initial_ride_hailing_count": sum(row["outbound_initial_mode"] == "ride_hailing" for row in rows),
        "fallback_uses": fallback_uses,
        "fallback_use_rate": round(fallback_uses / generated_legs, 6) if generated_legs else 0.0,
        "fallback_successes": fallback_success,
        "fallback_success_rate": round(fallback_success / fallback_uses, 6) if fallback_uses else 0.0,
        "final_walking_legs": modes["walk"], "final_bus_legs": modes["bus"],
        "final_ride_hailing_legs": modes["ride_hailing"],
        "necessary_activity_completion_rate": round(sum(row["activity_completed"] for row in necessary) / len(necessary), 6),
        "transport_related_unmet": sum(row["transport_related_unmet"] for row in rows),
        "return_failures": sum(row["return_transport_failure"] for row in rows),
        "stranded_after_activity": sum(row["stranded_after_activity"] for row in rows),
        "ride_hailing_demand": requests,
        "average_ride_hailing_wait_min": round(sum(row["ride_hailing_wait_min"] for row in rows) / requests, 6) if requests else 0.0,
        "average_travel_time_min": round(statistics.mean(row["cumulative_travel_time_min"] for row in travel), 6) if travel else 0.0,
        "average_fare_yuan": round(statistics.mean(row["cumulative_fare_yuan"] for row in travel), 6) if travel else 0.0,
        "cumulative_wait_min": round(sum(row["cumulative_wait_min"] for row in rows), 6),
        "cumulative_fare_yuan": round(sum(row["cumulative_fare_yuan"] for row in rows), 6),
        "cumulative_outdoor_exposure_minutes": round(sum(row["outdoor_exposure_minutes"] for row in rows), 6),
        "heat_exposure_index": round(sum(row["heat_exposure_index"] for row in rows), 6),
        "rain_exposure_index": round(sum(row["rain_exposure_index"] for row in rows), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/symmetric_weather_experiment")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    args = parser.parse_args()
    config = load_symmetric_experiment_config()
    seed_start = args.seed_start if args.seed_start is not None else int(config["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(config["seed_count"])
    seeds = list(range(seed_start, seed_start + seed_count))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    detail_rows: list[dict] = []
    purpose_rows: list[dict] = []
    weather_rows: list[dict] = []
    identity_rows: list[dict] = []
    state_rows: list[dict] = []
    for seed in seeds:
        result = run_symmetric_experiment(seed, config=config)
        detail_rows.extend({"seed": seed, **row} for row in result["results"])
        purpose_rows.extend(summarize_seed(result))
        for week in WEATHER_TYPES:
            for day_type in DAY_TYPES:
                subset = [row for row in result["results"] if row["weather_week"] == week and row["day_type"] == day_type]
                weather_rows.append(aggregate_weather_day(seed, week, day_type, subset))
        paired = {(row["activity_id"], row["weather_week"]): row for row in result["results"]}
        for activity in result["activities"]:
            w0, w1, w2 = (paired[(activity["activity_id"], week)] for week in WEATHER_TYPES)
            fields = ("agent_id", "activity_id", "day_type", "activity_purpose", "departure_time", "return_time", "work_start_time", "work_end_time", "origin_zone", "destination_zone", "distance_km")
            identity_rows.append({"seed": seed, "activity_id": activity["activity_id"], "paired_fields_identical": all(w0[field] == w1[field] == w2[field] for field in fields)})
        for row in result["results"]:
            exclusive = int(row["remote_work"]) + int(row["weather_cancellation"]) + int(row["travel_required"])
            state_rows.append({
                "seed": seed, "weather_week": row["weather_week"], "activity_id": row["activity_id"],
                "exclusive_initial_state": exclusive == 1,
                "attempts_within_limit": row["outbound_attempt_count"] <= 2 and row["return_attempt_count"] <= 2,
                "remote_has_no_legs": not row["remote_work"] or (not row["outbound_leg_generated"] and not row["return_leg_generated"]),
                "return_failure_preserves_completion": not row["return_transport_failure"] or (row["activity_completed"] and not row["transport_related_unmet"]),
            })

    numeric_metrics = [key for key, value in weather_rows[0].items() if key not in {"seed", "weather_week", "weather_type", "day_type"} and isinstance(value, (int, float))]
    distribution = []
    for week in WEATHER_TYPES:
        for day_type in DAY_TYPES:
            subset = [row for row in weather_rows if row["weather_week"] == week and row["day_type"] == day_type]
            for metric in numeric_metrics:
                distribution.append({"weather_week": week, "day_type": day_type, "metric": metric, **describe([float(row[metric]) for row in subset])})

    checks = []
    predicates = {
        "walking_W0_gt_W1_gt_W2": lambda w0, w1, w2: w0["final_walking_legs"] > w1["final_walking_legs"] > w2["final_walking_legs"],
        "ride_hailing_W0_lt_W1_lt_W2": lambda w0, w1, w2: w0["final_ride_hailing_legs"] < w1["final_ride_hailing_legs"] < w2["final_ride_hailing_legs"],
        "necessary_completion_W0_ge_W1_ge_W2": lambda w0, w1, w2: w0["necessary_activity_completion_rate"] >= w1["necessary_activity_completion_rate"] >= w2["necessary_activity_completion_rate"],
        "hazards_separated": lambda w0, w1, w2: w0["heat_exposure_index"] == w0["rain_exposure_index"] == w1["rain_exposure_index"] == w2["heat_exposure_index"] == 0,
    }
    for day_type in DAY_TYPES:
        for name, predicate in predicates.items():
            successes = 0
            for seed in seeds:
                lookup = {row["weather_week"]: row for row in weather_rows if row["seed"] == seed and row["day_type"] == day_type}
                successes += int(predicate(lookup["W0"], lookup["W1"], lookup["W2"]))
            checks.append({"day_type": day_type, "check": name, "successful_seeds": successes, "total_seeds": seed_count, "success_proportion": round(successes / seed_count, 6)})

    write_csv(output / "activity_results_all_seeds.csv", detail_rows)
    write_csv(output / "per_seed_weather_purpose.csv", purpose_rows)
    write_csv(output / "per_seed_weather_day_summary.csv", weather_rows)
    write_csv(output / "distribution_summary.csv", distribution)
    write_csv(output / "direction_checks.csv", checks)
    write_csv(output / "paired_activity_identity_audit.csv", identity_rows)
    write_csv(output / "state_conservation_audit.csv", state_rows)
    with (output / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump({
            "seeds": seeds, "config": config,
            "paired_identity_all_passed": all(row["paired_fields_identical"] for row in identity_rows),
            "state_audit_all_passed": all(all(value for key, value in row.items() if key not in {"seed", "weather_week", "activity_id"}) for row in state_rows),
            "formal_weather_calendar_modified": False,
            "remote_work_parameters_are_scenario_assumptions_not_shanghai_observations": True,
        }, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(output.resolve()), "weather_day_means": {
        f"{week}_{day_type}": {metric: describe([float(row[metric]) for row in weather_rows if row["weather_week"] == week and row["day_type"] == day_type])["mean"] for metric in numeric_metrics}
        for week in WEATHER_TYPES for day_type in DAY_TYPES
    }, "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
