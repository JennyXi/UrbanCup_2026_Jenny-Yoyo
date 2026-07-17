"""Run the paired 50-Agent ride-hailing fleet threshold screen."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_supply_threshold.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50_supply_threshold"

METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests", "ride_hailing_failed",
    "mean_ride_hailing_wait_minutes_per_request", "max_ride_hailing_wait_minutes",
    "fallback_attempts", "fallback_succeeded", "fallback_failed",
    "late_but_reached", "transport_unmet", "mandatory_activity_incomplete",
    "activity_completion_rate", "necessary_activity_completion_rate",
    "walking_mode_share", "bus_mode_share", "metro_mode_share", "ride_hailing_mode_share",
    "mean_total_travel_time", "mean_road_speed_kmh",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _describe(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.mean(values), 6),
        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        "median": round(statistics.median(values), 6),
        "minimum": round(min(values), 6),
        "maximum": round(max(values), 6),
    }


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _run_row(
    summary: Mapping[str, Any], dispatch: list[Mapping[str, Any]],
    vehicle_total: int,
) -> dict[str, Any]:
    waits = [float(row["pickup_wait_min"]) for row in dispatch]
    failures = Counter(row["failure_reason"] for row in dispatch if not row["succeeded"])
    return {
        **dict(summary),
        "vehicle_total": vehicle_total,
        "mean_ride_hailing_wait_minutes_per_request": (
            round(statistics.mean(waits), 6) if waits else 0.0
        ),
        "max_ride_hailing_wait_minutes": round(max(waits), 6) if waits else 0.0,
        "no_vehicle_failures": failures["no_vehicle_available"],
        "wait_limit_failures": failures["vehicle_wait_limit_exceeded"],
        "non_capacity_failures": failures["non_capacity_transport_failure"],
        "competition_event": int(summary["ride_hailing_failed"]) > 0,
        "collapse_event": int(summary["transport_unmet"]) > 0,
    }


def _aggregate(per_seed: list[dict[str, Any]], config: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    aggregates: list[dict[str, Any]] = []
    distributions: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    rules = config["classification_rules"]
    totals = [int(value) for value in config["vehicle_pools_by_total"]]
    for total in sorted(totals, reverse=True):
        for weather in config["weather_scenarios"]:
            rows = [
                row for row in per_seed
                if int(row["vehicle_total"]) == total and row["weather_scenario"] == weather
            ]
            aggregates.append({
                "vehicle_total": total,
                "weather_scenario": weather,
                "seed_count": len(rows),
                **{
                    metric: round(statistics.mean(float(row[metric]) for row in rows), 6)
                    for metric in METRICS
                },
                "competition_seed_share": round(
                    sum(_truthy(row["competition_event"]) for row in rows) / len(rows), 6
                ),
                "collapse_seed_share": round(
                    sum(_truthy(row["collapse_event"]) for row in rows) / len(rows), 6
                ),
                "no_vehicle_failure_seed_share": round(
                    sum(int(row["no_vehicle_failures"]) > 0 for row in rows) / len(rows), 6
                ),
            })
            for metric in METRICS:
                distributions.append({
                    "vehicle_total": total, "weather_scenario": weather, "metric": metric,
                    **_describe([float(row[metric]) for row in rows]),
                })
    for row in aggregates:
        competition = float(row["competition_seed_share"])
        collapse = float(row["collapse_seed_share"])
        if collapse >= float(rules["system_collapse_min_seed_share"]):
            label = "system_collapse_risk"
        elif competition >= float(rules["widespread_competition_min_seed_share"]):
            label = "tight_competition"
        elif competition >= float(rules["stable_competition_min_seed_share"]):
            label = "moderate_competition"
        else:
            label = "loose_supply"
        classifications.append({
            "vehicle_total": row["vehicle_total"],
            "weather_scenario": row["weather_scenario"],
            "competition_seed_share": competition,
            "collapse_seed_share": collapse,
            "classification": label,
            "mechanism_screen_not_calibration": True,
        })
    return aggregates, distributions, classifications


def _candidate(classifications: list[dict[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    rules = config["classification_rules"]
    by_total: dict[int, list[dict[str, Any]]] = {}
    for row in classifications:
        by_total.setdefault(int(row["vehicle_total"]), []).append(row)
    candidates = []
    for total in sorted(by_total, reverse=True):
        rows = by_total[total]
        if (
            max(float(row["competition_seed_share"]) for row in rows)
            >= float(rules["stable_competition_min_seed_share"])
            and max(float(row["collapse_seed_share"]) for row in rows)
            < float(rules["system_collapse_min_seed_share"])
        ):
            candidates.append(total)
    selected = max(candidates) if candidates else None
    return {
        "candidate_vehicle_total": selected,
        "candidate_found": selected is not None,
        "candidate_rule": rules["candidate_rule"],
        "not_a_calibrated_real_world_fleet": True,
    }


def _monotonicity_audit(aggregate: list[dict[str, Any]], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for weather in config["weather_scenarios"]:
        ordered = sorted(
            (row for row in aggregate if row["weather_scenario"] == weather),
            key=lambda row: int(row["vehicle_total"]), reverse=True,
        )
        checks = {
            "ride_failures_non_decreasing_as_supply_falls": all(
                float(right["ride_hailing_failed"]) + 1e-9 >= float(left["ride_hailing_failed"])
                for left, right in zip(ordered, ordered[1:])
            ),
            "successful_orders_non_increasing_as_supply_falls": all(
                float(right["successful_ride_hailing_requests"]) <= float(left["successful_ride_hailing_requests"]) + 1e-9
                for left, right in zip(ordered, ordered[1:])
            ),
            "request_wait_non_decreasing_as_supply_falls": all(
                float(right["mean_ride_hailing_wait_minutes_per_request"]) + 1e-9
                >= float(left["mean_ride_hailing_wait_minutes_per_request"])
                for left, right in zip(ordered, ordered[1:])
            ),
        }
        rows.append({
            "weather_scenario": weather,
            **checks,
            "all_monotonicity_checks_pass": all(checks.values()),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    parser.add_argument(
        "--reuse-per-seed", action="store_true",
        help="Rebuild aggregate/audit files from an existing per_seed_macro.csv.",
    )
    args = parser.parse_args()
    config = _load(args.config)
    seed_start = int(config["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(config["seed_count"] if args.seed_count is None else args.seed_count)
    base_path = ROOT / config["base_experiment_config"]
    base_config = load_formal_50_config(base_path)
    pools = {
        int(total): zones for total, zones in config["vehicle_pools_by_total"].items()
    }
    for total, zones in pools.items():
        if set(zones) != {f"Z{index}" for index in range(1, 10)} or sum(zones.values()) != total:
            raise ValueError(f"vehicle pool {total} must contain Z1-Z9 and sum to its label")
    ordered_totals = sorted(pools, reverse=True)
    for higher, lower in zip(ordered_totals, ordered_totals[1:]):
        if any(pools[lower][zone] > pools[higher][zone] for zone in pools[higher]):
            raise ValueError(
                f"vehicle pools must be spatially nested; {lower} cannot add vehicles relative to {higher}"
            )

    output = args.output_dir
    if args.reuse_per_seed:
        per_seed = _read_csv(output / "per_seed_macro.csv")
    else:
        per_seed: list[dict[str, Any]] = []
        for seed in range(seed_start, seed_start + seed_count):
            paired_inputs = None
            for total in ordered_totals:
                run_config = copy.deepcopy(base_config)
                run_config["formal_overrides"] = {
                    "experiment_condition": f"fleet_{total}",
                    "ride_hailing_fleet": {
                        "initial_vehicles_by_day_type": {"workday": pools[total]}
                    },
                }
                result = run_formal_nine_zone_50_experiment(
                    config=run_config, seed=seed,
                    weather_scenarios=tuple(config["weather_scenarios"]),
                    day_types=(config["day_type"],), paired_inputs=paired_inputs,
                )
                paired_inputs = result["inputs"]
                for summary in result["summary_rows"]:
                    dispatch = [
                        row for row in result["ride_hailing_dispatch"]
                        if row["weather_scenario"] == summary["weather_scenario"]
                        and row["day_type"] == summary["day_type"]
                    ]
                    per_seed.append(_run_row(summary, dispatch, total))
            print(f"Completed seed {seed} ({seed - seed_start + 1}/{seed_count})", flush=True)

    aggregate, distributions, classifications = _aggregate(per_seed, config)
    candidate = _candidate(classifications, config)
    monotonicity = _monotonicity_audit(aggregate, config)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "per_seed_macro.csv", per_seed)
    _write_csv(output / "aggregate_means.csv", aggregate)
    _write_csv(output / "metric_distributions.csv", distributions)
    _write_csv(output / "supply_classification.csv", classifications)
    _write_csv(output / "monotonicity_audit.csv", monotonicity)
    (output / "candidate_baseline.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(candidate, ensure_ascii=False))
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
