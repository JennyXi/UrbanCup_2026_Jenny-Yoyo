"""Run paired age-weather outdoor-exposure choice sensitivity for 200 Agents."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

DEFAULT_CONFIG = ROOT / "config" / "formal_nine_zone_200_age_weather_sensitivity.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "formal_nine_zone_200_age_weather_sensitivity"
METRICS = (
    "walking_mode_share", "bus_mode_share", "metro_mode_share",
    "ride_hailing_mode_share", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes", "mean_total_travel_time",
    "activity_completion_rate", "necessary_activity_completion_rate",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(statistics.mean(values), 6) if values else 0.0


def _elder_row(
    result: Mapping[str, Any], condition: str, weather: str, seed: int,
) -> dict[str, Any]:
    elder_ids = {
        int(row["agent_id"]) for row in result["inputs"]["agents"]
        if row["age_group"] == "60+"
    }
    choices = [
        row for row in result["mode_choices"]
        if row["weather_scenario"] == weather and int(row["agent_id"]) in elder_ids
    ]
    successful = [row for row in choices if row["transport_succeeded"]]
    modes = Counter(row["final_mode"] for row in successful)
    activities = [
        row for row in result["activity_results"]
        if row["weather_scenario"] == weather and int(row["agent_id"]) in elder_ids
    ]
    necessary = [row for row in activities if row["is_mandatory"]]
    total = len(successful)
    return {
        "seed": seed, "behavior_scenario": condition,
        "weather_scenario": weather, "elder_agent_count": len(elder_ids),
        "elder_successful_legs": total,
        "elder_walking_share": round(modes["walk"] / total, 6) if total else None,
        "elder_bus_share": round(modes["bus"] / total, 6) if total else None,
        "elder_metro_share": round(modes["metro"] / total, 6) if total else None,
        "elder_ride_hailing_share": round(modes["ride_hailing"] / total, 6) if total else None,
        "elder_ride_hailing_requests": sum(
            row["primary_mode"] == "ride_hailing" for row in choices
        ),
        "elder_mean_choice_exposure_disutility": _mean(
            float(row["weather_exposure_disutility"] or 0.0) for row in choices
        ),
        "elder_total_outdoor_exposure_minutes": round(
            sum(float(row["outdoor_exposure_minutes"]) for row in choices), 6
        ),
        "elder_total_heat_risk_burden": round(
            sum(float(row["heat_risk_burden"]) for row in choices), 6
        ),
        "elder_necessary_activity_completion_rate": (
            round(sum(row["completed"] for row in necessary) / len(necessary), 6)
            if necessary else None
        ),
    }


def _distributions(rows: list[Mapping[str, Any]], metrics: Iterable[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["behavior_scenario"]), str(row["weather_scenario"]))].append(row)
    output = []
    for (condition, weather), selected in sorted(groups.items()):
        for metric in metrics:
            values = [float(row[metric]) for row in selected if row.get(metric) not in (None, "")]
            if values:
                output.append({
                    "behavior_scenario": condition, "weather_scenario": weather,
                    "metric": metric, "seed_count": len(values),
                    "mean": round(statistics.mean(values), 6),
                    "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                    "minimum": round(min(values), 6), "maximum": round(max(values), 6),
                })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    args = parser.parse_args()
    experiment = _load(args.config)
    base = load_formal_50_config(ROOT / experiment["base_experiment_config"])
    seed_start = int(experiment["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(experiment["seed_count"] if args.seed_count is None else args.seed_count)
    weather = tuple(experiment["weather_scenarios"])
    system_rows: list[dict[str, Any]] = []
    elder_rows: list[dict[str, Any]] = []
    choice_rows: list[dict[str, Any]] = []
    pairing_rows: list[dict[str, Any]] = []
    for seed in range(seed_start, seed_start + seed_count):
        paired_inputs = None
        baseline_signatures = None
        for condition, rates in experiment["behavior_scenarios"].items():
            run_config = copy.deepcopy(base)
            run_config["formal_overrides"]["experiment_condition"] = condition
            run_config["formal_overrides"]["mode_choice"] = {
                "weather_exposure_disutility": {
                    "enabled": True,
                    "utility_penalty_per_outdoor_minute": rates,
                    "age_vulnerability_weight": experiment["age_vulnerability_weight"],
                }
            }
            result = run_formal_nine_zone_50_experiment(
                config=run_config, seed=seed, weather_scenarios=weather,
                day_types=(experiment["day_type"],), paired_inputs=paired_inputs,
            )
            paired_inputs = result["inputs"]
            signatures = tuple(
                (row["activity_id"], row["agent_id"], row["destination_zone"],
                 row["planned_start_datetime"], row["planned_end_datetime"])
                for row in result["inputs"]["activities"]
            )
            if baseline_signatures is None:
                baseline_signatures = signatures
            pairing_rows.append({
                "seed": seed, "behavior_scenario": condition,
                "paired_inputs_identical": signatures == baseline_signatures,
            })
            for summary in result["summary_rows"]:
                system_rows.append({"behavior_scenario": condition, **summary})
                elder_rows.append(_elder_row(
                    result, condition, summary["weather_scenario"], seed,
                ))
            choice_rows.extend(
                {"seed": seed, "behavior_scenario": condition, **row}
                for row in result["mode_choices"]
            )
        print(f"Completed seed {seed} ({seed - seed_start + 1}/{seed_count})", flush=True)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write(output / "system_per_seed.csv", system_rows)
    _write(output / "system_distributions.csv", _distributions(system_rows, METRICS))
    _write(output / "elder_per_seed.csv", elder_rows)
    elder_metrics = [key for key in elder_rows[0] if key.startswith("elder_") and key not in {"elder_agent_count"}]
    _write(output / "elder_distributions.csv", _distributions(elder_rows, elder_metrics))
    _write(output / "mode_choices.csv", choice_rows)
    _write(output / "pairing_checks.csv", pairing_rows)
    metadata = {
        "experiment_id": experiment["experiment_id"], "seed_start": seed_start,
        "seed_count": seed_count, "agents": 200, "fleet_total": 48,
        "weather_scenarios": list(weather),
        "all_pairing_checks_passed": all(row["paired_inputs_identical"] for row in pairing_rows),
        "interpretation": "mechanism sensitivity, not calibrated health valuation",
    }
    (output / "experiment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Age-weather sensitivity complete: {seed_count} seeds")
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
