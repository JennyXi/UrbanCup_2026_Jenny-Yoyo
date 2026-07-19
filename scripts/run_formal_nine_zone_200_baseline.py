"""Run the paired 200-Agent W0/W1/W2 workday P0 smoke experiment."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

DEFAULT_CONFIG = ROOT / "config" / "formal_nine_zone_200_baseline.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "formal_nine_zone_200_baseline_smoke"
PER_100_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "fallback_attempts",
    "transport_related_unmet", "mandatory_activity_incomplete",
)
SUMMARY_METRICS = (
    "planned_activities", "completed_activities", "activity_completion_rate",
    "necessary_activity_completion_rate", "weather_cancelled_activities",
    "transport_related_unmet", "mandatory_activity_incomplete",
    "walking_mode_share", "bus_mode_share", "metro_mode_share",
    "ride_hailing_mode_share", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes", "fallback_attempts", "fallback_successes",
    "mean_total_travel_time", "mean_fare_yuan", "on_time_arrival_rate",
    "road_vehicle_volume", "mean_volume_capacity_ratio", "mean_road_speed_kmh",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)


def _serial(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = [{key: _serial(value) for key, value in row.items()} for row in rows]
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _add_scale_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(row)
    agents = int(row["agent_count"])
    for metric in PER_100_METRICS:
        output[f"{metric}_per_100_agents"] = round(float(row[metric]) * 100 / agents, 6)
    return output


def _distribution(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["weather_scenario"]), str(row["day_type"]))].append(row)
    output = []
    for (weather, day_type), selected in sorted(grouped.items()):
        metrics = SUMMARY_METRICS + tuple(f"{name}_per_100_agents" for name in PER_100_METRICS)
        for metric in metrics:
            values = [float(row[metric]) for row in selected if row.get(metric) is not None]
            if not values:
                continue
            output.append({
                "weather_scenario": weather, "day_type": day_type, "metric": metric,
                "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            })
    return output


def _checks(
    result: Mapping[str, Any], seed: int, fleet_total: int,
    weather_scenarios: Iterable[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    def add(weather: str, name: str, passed: bool, detail: str) -> None:
        checks.append({"seed": seed, "weather_scenario": weather, "check": name,
                       "passed": bool(passed), "detail": detail})

    zones = {str(row["home_zone"]) for row in result["inputs"]["agents"]}
    weather_scenarios = tuple(weather_scenarios)
    for weather in weather_scenarios:
        summary = next(row for row in result["summary_rows"] if row["weather_scenario"] == weather)
        choices = [row for row in result["mode_choices"] if row["weather_scenario"] == weather]
        dispatch = [row for row in result["ride_hailing_dispatch"] if row["weather_scenario"] == weather]
        states = [row for row in result["vehicle_end_states"] if row["weather_scenario"] == weather]
        shares = [summary.get(f"{mode}_mode_share") for mode in ("walking", "bus", "metro", "ride_hailing")]
        add(weather, "agent_count_is_200", len(result["inputs"]["agents"]) == 200,
            f"agents={len(result['inputs']['agents'])}")
        add(weather, "all_nine_home_zones_present", len(zones) == 9, f"zones={sorted(zones)}")
        add(weather, "vehicle_conservation", len(states) == fleet_total,
            f"end_states={len(states)}, initial={fleet_total}")
        by_vehicle: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in dispatch:
            if row["succeeded"]:
                by_vehicle[str(row["vehicle_id"])].append(row)
        no_overlap = True
        for services in by_vehicle.values():
            ordered = sorted(services, key=lambda row: row["busy_start"])
            no_overlap &= all(later["busy_start"] >= earlier["busy_until"]
                              for earlier, later in zip(ordered, ordered[1:]))
        add(weather, "no_vehicle_double_service", no_overlap,
            f"successful_dispatches={sum(row['succeeded'] for row in dispatch)}")
        add(weather, "successful_mode_accounting",
            sum(int(summary[f"{mode}_legs"]) for mode in ("walking", "bus", "metro", "ride_hailing"))
            == int(summary["successful_legs"]), f"successful_legs={summary['successful_legs']}")
        share_sum = sum(float(value) for value in shares if value is not None)
        add(weather, "mode_shares_sum_to_one", abs(share_sum - 1.0) <= 1e-5,
            f"share_sum={share_sum:.6f}")
        finite = all(
            value is None or (isinstance(value, (int, float)) and math.isfinite(float(value)) and value >= 0)
            for row in choices for value in (
                row.get("total_travel_time_min"), row.get("fare_yuan"),
                row.get("outdoor_exposure_minutes"), row.get("heat_risk_burden"),
            )
        )
        add(weather, "finite_nonnegative_outputs", finite, f"choice_rows={len(choices)}")
    planned_counts = {
        row["weather_scenario"]: row["planned_activities"]
        for row in result["summary_rows"]
    }
    add("paired", "paired_planned_activity_count",
        len(set(planned_counts.values())) == 1,
        ", ".join(f"{weather}={planned_counts[weather]}" for weather in weather_scenarios))
    add("paired", "public_transport_capacity_disabled",
        not result["formal_config"]["bus_system"]["capacity_constraint_enabled"],
        "bus and metro boarding are guaranteed")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    args = parser.parse_args()
    config = load_formal_50_config(args.config)
    seed_start = int(config["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(config["smoke_seed_count"] if args.seed_count is None else args.seed_count)
    if seed_count <= 0:
        raise ValueError("seed-count must be positive")
    fleet_total = sum(config["formal_overrides"]["ride_hailing_fleet"]
                      ["initial_vehicles_by_day_type"]["workday"].values())
    weather_scenarios = tuple(config["run_weather_scenarios"])
    system_rows: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    dispatch: list[dict[str, Any]] = []
    vehicle_states: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    population_rows: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        result = run_formal_nine_zone_50_experiment(
            config=config, seed=seed, weather_scenarios=weather_scenarios,
            day_types=("workday",),
        )
        system_rows.extend(_add_scale_metrics(row) for row in result["summary_rows"])
        choices.extend({"seed": seed, **row} for row in result["mode_choices"])
        dispatch.extend({"seed": seed, **row} for row in result["ride_hailing_dispatch"])
        vehicle_states.extend({"seed": seed, **row} for row in result["vehicle_end_states"])
        checks.extend(_checks(result, seed, fleet_total, weather_scenarios))
        population_rows.extend({"seed": seed, **row} for row in result["inputs"]["agents"])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "system_per_seed.csv", system_rows)
    _write_csv(output / "system_distributions.csv", _distribution(system_rows))
    _write_csv(output / "mode_choices.csv", choices)
    _write_csv(output / "ride_hailing_dispatch.csv", dispatch)
    _write_csv(output / "vehicle_end_states.csv", vehicle_states)
    _write_csv(output / "population_by_seed.csv", population_rows)
    _write_csv(output / "consistency_checks.csv", checks)
    metadata = {
        "experiment_id": config["experiment_id"], "seed_start": seed_start,
        "seed_count": seed_count, "agents_per_seed": 200,
        "weather_scenarios": list(weather_scenarios), "day_types": ["workday"],
        "policy": "P0_no_policy", "initial_ride_hailing_vehicles": fleet_total,
        "all_checks_passed": all(row["passed"] for row in checks),
        "scale_definition": config["scale_definition"],
    }
    (output / "experiment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"200-Agent P0 smoke complete: {seed_count} seeds")
    for weather in weather_scenarios:
        selected = [row for row in system_rows if row["weather_scenario"] == weather]
        avg = lambda name: statistics.mean(float(row[name]) for row in selected if row.get(name) is not None)
        print(
            f"  {weather}: walk/bus/metro/RH share="
            f"{avg('walking_mode_share'):.3f}/{avg('bus_mode_share'):.3f}/"
            f"{avg('metro_mode_share'):.3f}/{avg('ride_hailing_mode_share'):.3f}, "
            f"RH requests/success/fail={avg('ride_hailing_requests'):.1f}/"
            f"{avg('successful_ride_hailing_requests'):.1f}/{avg('failed_ride_hailing_requests'):.1f}, "
            f"unmet={avg('transport_related_unmet'):.1f}, "
            f"necessary completion={avg('necessary_activity_completion_rate'):.3f}, "
            f"avg time={avg('mean_total_travel_time'):.1f} min"
        )
    failed = [row for row in checks if not row["passed"]]
    print(f"Checks: {len(checks) - len(failed)}/{len(checks)} passed")
    print(f"Files: {output.resolve()}")
    if failed:
        raise SystemExit("Consistency checks failed; inspect consistency_checks.csv")


if __name__ == "__main__":
    main()
