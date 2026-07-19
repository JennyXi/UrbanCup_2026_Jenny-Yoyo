"""Run paired A0/A1/A2 elderly ride-hailing preference sensitivity."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

DEFAULT_CONFIG = ROOT / "config" / "formal_nine_zone_200_elder_ride_preference_sensitivity.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "formal_nine_zone_200_elder_ride_preference_sensitivity"
GROUPS = (
    "18-39", "40-59", "60+", "60+_digital",
    "60+_nondigital_assisted", "60+_nondigital_unassisted",
    "60+_medical_low", "60+_medical_standard", "60+_medical_high",
    "60+_digital_medical_low", "60+_digital_medical_standard",
    "60+_digital_medical_high",
)
SYSTEM_METRICS = (
    "walking_mode_share", "bus_mode_share", "metro_mode_share",
    "ride_hailing_mode_share", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes", "mean_total_travel_time",
    "activity_completion_rate", "necessary_activity_completion_rate",
    "transport_related_unmet", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "mean_road_speed_kmh",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)
GROUP_METRICS = (
    "agent_count", "successful_legs", "ride_hailing_requests",
    "successful_ride_hailing_requests", "failed_ride_hailing_requests",
    "ride_hailing_requests_per_100_agents", "ride_hailing_mode_share",
    "mean_ride_hailing_wait_minutes", "necessary_activity_completion_rate",
    "transport_related_unmet", "total_outdoor_exposure_minutes",
    "total_heat_risk_burden",
    "exposed_travel_required_necessary_activities",
    "completed_exposed_travel_required_necessary_activities",
    "exposed_travel_required_necessary_completion_rate",
    "exposed_necessary_ride_hailing_legs",
    "exposed_necessary_ride_hailing_mode_share",
    "exposed_necessary_outdoor_exposure_minutes",
    "exposed_necessary_heat_risk_burden",
    "exposed_necessary_rain_exposure_minutes",
    "metro_legs", "bus_metro_transfer_legs",
    "bus_metro_transfer_share_of_metro",
    "exposed_necessary_bus_metro_transfer_legs",
    "exposed_necessary_bus_metro_transfer_share",
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


def _group(profile: Mapping[str, Any]) -> tuple[str, ...]:
    age = str(profile["age_group"])
    if age != "60+":
        return (age,)
    if profile["digital_access"]:
        subgroup = "60+_digital"
    elif profile.get("family_assistance"):
        subgroup = "60+_nondigital_assisted"
    else:
        subgroup = "60+_nondigital_unassisted"
    medical_group = f"60+_medical_{profile['medical_need_level']}"
    groups = ["60+", subgroup, medical_group]
    if profile["digital_access"]:
        groups.append(f"60+_digital_medical_{profile['medical_need_level']}")
    return tuple(groups)


def _mean(rows: list[Mapping[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
    return round(statistics.mean(values), 6) if values else None


def _group_rows(result: Mapping[str, Any], scenario: str, weather: str, seed: int) -> list[dict[str, Any]]:
    profiles = {int(row["agent_id"]): row for row in result["inputs"]["agents"]}
    members = {group: set() for group in GROUPS}
    for agent_id, profile in profiles.items():
        for group in _group(profile):
            members[group].add(agent_id)
    choices = [row for row in result["mode_choices"] if row["weather_scenario"] == weather]
    dispatch = [row for row in result["ride_hailing_dispatch"] if row["weather_scenario"] == weather]
    activities = [row for row in result["activity_results"] if row["weather_scenario"] == weather]
    output = []
    for group, ids in members.items():
        selected = [row for row in choices if int(row["agent_id"]) in ids]
        successful = [row for row in selected if row["transport_succeeded"]]
        rides = [row for row in dispatch if int(row["agent_id"]) in ids]
        necessary = [row for row in activities if int(row["agent_id"]) in ids and row["is_mandatory"]]
        exposed_necessary = [
            row for row in necessary
            if row["weather_exposed"] and row["travel_required"]
        ]
        exposed_ids = {str(row["activity_id"]) for row in exposed_necessary}
        exposed_choices = [
            row for row in successful
            if str(row["activity_id"]) in exposed_ids
        ]
        exposed_modes = Counter(row["final_mode"] for row in exposed_choices)
        modes = Counter(row["final_mode"] for row in successful)
        metro = [row for row in successful if row["final_mode"] == "metro"]
        transfer = [
            row for row in metro if int(row.get("bus_metro_transfer_count") or 0) > 0
        ]
        exposed_transfer = [
            row for row in exposed_choices
            if row["final_mode"] == "metro"
            and int(row.get("bus_metro_transfer_count") or 0) > 0
        ]
        count = len(ids)
        output.append({
            "seed": seed, "preference_scenario": scenario,
            "weather_scenario": weather, "group": group, "agent_count": count,
            "successful_legs": len(successful), "ride_hailing_requests": len(rides),
            "successful_ride_hailing_requests": sum(bool(row["succeeded"]) for row in rides),
            "failed_ride_hailing_requests": sum(not bool(row["succeeded"]) for row in rides),
            "ride_hailing_requests_per_100_agents": round(100 * len(rides) / count, 6) if count else None,
            "ride_hailing_mode_share": round(modes["ride_hailing"] / len(successful), 6) if successful else None,
            "mean_ride_hailing_wait_minutes": _mean(rides, "pickup_wait_min"),
            "necessary_activity_completion_rate": (
                round(sum(bool(row["completed"]) for row in necessary) / len(necessary), 6)
                if necessary else None
            ),
            "transport_related_unmet": sum(
                bool(row.get("transport_unmet")) for row in activities if int(row["agent_id"]) in ids
            ),
            "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in selected), 6),
            "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in selected), 6),
            "metro_legs": len(metro),
            "bus_metro_transfer_legs": len(transfer),
            "bus_metro_transfer_share_of_metro": (
                round(len(transfer) / len(metro), 6) if metro else None
            ),
            "exposed_travel_required_necessary_activities": len(exposed_necessary),
            "completed_exposed_travel_required_necessary_activities": sum(
                bool(row["completed"]) for row in exposed_necessary
            ),
            "exposed_travel_required_necessary_completion_rate": (
                round(sum(bool(row["completed"]) for row in exposed_necessary) / len(exposed_necessary), 6)
                if exposed_necessary else None
            ),
            "exposed_necessary_ride_hailing_legs": exposed_modes["ride_hailing"],
            "exposed_necessary_ride_hailing_mode_share": (
                round(exposed_modes["ride_hailing"] / len(exposed_choices), 6)
                if exposed_choices else None
            ),
            "exposed_necessary_outdoor_exposure_minutes": round(
                sum(float(row["outdoor_exposure_minutes"]) for row in exposed_choices), 6
            ),
            "exposed_necessary_heat_risk_burden": round(
                sum(float(row["heat_risk_burden"]) for row in exposed_choices), 6
            ),
            "exposed_necessary_rain_exposure_minutes": round(
                sum(float(row["rain_exposure_minutes"]) for row in exposed_choices), 6
            ),
            "exposed_necessary_bus_metro_transfer_legs": len(exposed_transfer),
            "exposed_necessary_bus_metro_transfer_share": (
                round(len(exposed_transfer) / len(exposed_choices), 6)
                if exposed_choices else None
            ),
        })
    return output


def _describe(rows: list[Mapping[str, Any]], metrics: Iterable[str], group: bool = False) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["preference_scenario"]), str(row["weather_scenario"]))
        if group:
            key += (str(row["group"]),)
        grouped[key].append(row)
    output = []
    for key, selected in sorted(grouped.items()):
        for metric in metrics:
            values = [float(row[metric]) for row in selected if row.get(metric) not in (None, "")]
            if not values:
                continue
            item = {
                "preference_scenario": key[0], "weather_scenario": key[1],
                "metric": metric, "seed_count": len(values),
                "mean": round(statistics.mean(values), 6),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "minimum": round(min(values), 6), "maximum": round(max(values), 6),
            }
            if group:
                item["group"] = key[2]
            output.append(item)
    return output


def _run_seed(seed: int, experiment: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    paired_inputs = None
    base_signature = None
    systems, groups, checks, option_audit, activity_audit = [], [], [], [], []
    sensitivity_parameter = experiment.get(
        "sensitivity_parameter", "elder_ride_hailing_mode_constant"
    )
    scenarios = experiment.get(
        "behavior_scenarios", experiment.get("preference_scenarios")
    )
    if not scenarios:
        raise ValueError("experiment must define behavior_scenarios")
    for scenario, scenario_value in scenarios.items():
        config = copy.deepcopy(base)
        config["formal_overrides"]["ride_hailing_fleet"]["initial_vehicles_by_day_type"][experiment["day_type"]] = copy.deepcopy(experiment["initial_vehicles"])
        exposure = copy.deepcopy(experiment["weather_exposure_disutility"])
        if sensitivity_parameter in {
            "elder_weather_vulnerability_weight",
            "elder_weather_exposure_choice_weight",
        }:
            constant = float(experiment["fixed_elder_ride_hailing_mode_constant"])
            exposure["age_vulnerability_weight"]["60+"] = float(scenario_value)
        elif sensitivity_parameter == "elder_medical_need_exposure_weight":
            constant = float(experiment["fixed_elder_ride_hailing_mode_constant"])
            exposure["age_vulnerability_weight"]["60+"] = float(
                experiment["fixed_elder_weather_exposure_choice_weight"]
            )
            exposure["medical_need_level_weight"] = copy.deepcopy(scenario_value)
        elif sensitivity_parameter == "combined_elder_behavior":
            constant = float(scenario_value["ride_hailing_mode_constant"])
            exposure["age_vulnerability_weight"]["60+"] = float(
                scenario_value["weather_exposure_choice_weight"]
            )
            exposure["medical_need_level_weight"] = copy.deepcopy(
                scenario_value["medical_need_level_weight"]
            )
        elif sensitivity_parameter == "elder_exposed_necessary_fare_sensitivity":
            constant = float(experiment["fixed_elder_ride_hailing_mode_constant"])
            exposure["age_vulnerability_weight"]["60+"] = float(
                experiment["fixed_elder_weather_exposure_choice_weight"]
            )
            exposure["medical_need_level_weight"] = copy.deepcopy(
                experiment["fixed_medical_need_level_weight"]
            )
        elif sensitivity_parameter == "elder_transfer_burden_minutes":
            constant = float(experiment["fixed_elder_ride_hailing_mode_constant"])
            exposure["age_vulnerability_weight"]["60+"] = float(
                experiment["fixed_elder_weather_exposure_choice_weight"]
            )
            exposure["medical_need_level_weight"] = copy.deepcopy(
                experiment["fixed_medical_need_level_weight"]
            )
        elif sensitivity_parameter == "elder_ride_hailing_mode_constant":
            constant = float(scenario_value)
        else:
            raise ValueError(f"unsupported sensitivity parameter: {sensitivity_parameter}")
        config["formal_overrides"]["mode_choice"] = {
            "age_mode_constant": {"60+": {"ride_hailing": constant}},
            "weather_exposure_disutility": exposure,
        }
        if sensitivity_parameter == "elder_exposed_necessary_fare_sensitivity":
            config["formal_overrides"]["mode_choice"]["conditional_fare_sensitivity"] = {
                "enabled": True,
                "necessary_purposes": ["work", "medical"],
                "weather_types": ["extreme_heat", "heavy_rain"],
                "elder_exposed_necessary_multiplier": float(scenario_value),
            }
        if sensitivity_parameter == "elder_transfer_burden_minutes":
            config["formal_overrides"]["mode_choice"]["conditional_fare_sensitivity"] = {
                "enabled": True,
                "necessary_purposes": ["work", "medical"],
                "weather_types": ["extreme_heat", "heavy_rain"],
                "elder_exposed_necessary_multiplier": float(
                    experiment["fixed_elder_exposed_necessary_fare_sensitivity"]
                ),
            }
            config["formal_overrides"]["mode_choice"]["age_transfer_burden"] = {
                "enabled": True,
                "minutes_per_transfer_by_age": {
                    "18-39": 0.0, "40-59": 0.0, "60+": float(scenario_value),
                },
            }
        result = run_formal_nine_zone_50_experiment(
            config=config, seed=seed,
            weather_scenarios=tuple(experiment["weather_scenarios"]),
            day_types=(experiment["day_type"],), paired_inputs=paired_inputs,
        )
        paired_inputs = result["inputs"]
        signature = tuple(
            (row["activity_id"], row["agent_id"], row["destination_zone"], row["planned_start_datetime"])
            for row in result["inputs"]["activities"]
        )
        if base_signature is None:
            base_signature = signature
        checks.append({
            "seed": seed, "preference_scenario": scenario,
            "paired_inputs_identical": signature == base_signature,
            "sensitivity_parameter": sensitivity_parameter,
            "scenario_value": (
                json.dumps(scenario_value, ensure_ascii=False, sort_keys=True)
                if isinstance(scenario_value, Mapping) else float(scenario_value)
            ),
            "elder_ride_hailing_mode_constant": constant,
            "elder_weather_vulnerability_weight": float(
                exposure["age_vulnerability_weight"]["60+"]
            ),
            "fleet_total": sum(experiment["initial_vehicles"].values()),
            "passed": signature == base_signature and len(result["inputs"]["agents"]) == 200,
        })
        for row in result["summary_rows"]:
            systems.append({"preference_scenario": scenario, **row})
            groups.extend(_group_rows(result, scenario, row["weather_scenario"], seed))
        if experiment.get("write_option_audit", False):
            states = {
                (str(row["weather_scenario"]), str(row["activity_id"])): row
                for row in result["activity_results"]
            }
            choices = {
                (str(row["weather_scenario"]), str(row["leg_id"])): row
                for row in result["mode_choices"]
            }
            retained_activity_keys = set()
            for row in result.get("choice_option_audit", ()):
                state = states.get((str(row["weather_scenario"]), str(row["activity_id"])))
                if not state or str(row["weather_scenario"]) not in {"W1", "W2"}:
                    continue
                if str(row["age_group"]) not in {"40-59", "60+"}:
                    continue
                if not (
                    state["is_mandatory"] and state["weather_exposed"]
                    and state["travel_required"] and row["leg_role"] != "return_home"
                ):
                    continue
                final = choices.get((str(row["weather_scenario"]), str(row["leg_id"])), {})
                option_audit.append({
                    "seed": seed, "behavior_scenario": scenario,
                    **dict(row),
                    "activity_final_status": state["final_status"],
                    "activity_completed": state["completed"],
                    "transport_unmet": state["transport_unmet"],
                    "final_mode": final.get("final_mode", ""),
                    "primary_failure_reason": final.get("primary_failure_reason", ""),
                    "completion_failure_reason": final.get("completion_failure_reason", ""),
                })
                retained_activity_keys.add(
                    (str(row["weather_scenario"]), str(row["activity_id"]))
                )
            for key in sorted(retained_activity_keys):
                state = states[key]
                inbound = [
                    row for row in result["mode_choices"]
                    if str(row["weather_scenario"]) == key[0]
                    and str(row["activity_id"]) == key[1]
                    and row["leg_role"] != "return_home"
                ]
                final = inbound[0] if inbound else {}
                activity_audit.append({
                    "seed": seed, "behavior_scenario": scenario,
                    "weather_scenario": key[0], "activity_id": key[1],
                    "agent_id": state["agent_id"], "age_group": state["age_group"],
                    "medical_need_level": state.get("medical_need_level"),
                    "activity_purpose": state["activity_purpose"],
                    "digital_access": next(
                        row["digital_access"] for row in result["inputs"]["agents"]
                        if int(row["agent_id"]) == int(state["agent_id"])
                    ),
                    "family_assistance": next(
                        bool(row.get("family_assistance")) for row in result["inputs"]["agents"]
                        if int(row["agent_id"]) == int(state["agent_id"])
                    ),
                    "final_status": state["final_status"],
                    "activity_completed": state["completed"],
                    "transport_unmet": state["transport_unmet"],
                    "primary_mode": final.get("primary_mode", ""),
                    "final_mode": final.get("final_mode", ""),
                    "transport_succeeded": final.get("transport_succeeded", False),
                    "arrival_delay_minutes": final.get("arrival_delay_minutes"),
                    "completion_failure_reason": final.get("completion_failure_reason", ""),
                })
    return {
        "systems": systems, "groups": groups, "checks": checks,
        "option_audit": option_audit, "activity_audit": activity_audit,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    experiment = _load(args.config)
    scenarios = experiment.get(
        "behavior_scenarios", experiment.get("preference_scenarios")
    )
    if not scenarios:
        raise ValueError("experiment must define behavior_scenarios")
    base = load_formal_50_config(ROOT / experiment["base_experiment_config"])
    seed_start = int(experiment["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(experiment["seed_count"] if args.seed_count is None else args.seed_count)
    seeds = list(range(seed_start, seed_start + seed_count))
    completed = {}
    if args.workers == 1:
        for index, seed in enumerate(seeds, 1):
            completed[seed] = _run_seed(seed, experiment, base)
            print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=min(args.workers, seed_count)) as executor:
            futures = {executor.submit(_run_seed, seed, experiment, base): seed for seed in seeds}
            for index, future in enumerate(as_completed(futures), 1):
                seed = futures[future]
                completed[seed] = future.result()
                print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)
    systems, groups, checks, option_audit, activity_audit = [], [], [], [], []
    for seed in seeds:
        systems.extend(completed[seed]["systems"])
        groups.extend(completed[seed]["groups"])
        checks.extend(completed[seed]["checks"])
        option_audit.extend(completed[seed]["option_audit"])
        activity_audit.extend(completed[seed]["activity_audit"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(args.output_dir / "system_per_seed.csv", systems)
    _write(args.output_dir / "system_distributions.csv", _describe(systems, SYSTEM_METRICS))
    _write(args.output_dir / "age_access_group_per_seed.csv", groups)
    _write(args.output_dir / "age_access_group_distributions.csv", _describe(groups, GROUP_METRICS, group=True))
    _write(args.output_dir / "pairing_checks.csv", checks)
    _write(args.output_dir / "necessary_activity_mode_option_audit.csv", option_audit)
    _write(args.output_dir / "necessary_activity_outcomes.csv", activity_audit)
    metadata = {
        "experiment_id": experiment["experiment_id"], "seed_start": seed_start,
        "seed_count": seed_count, "agents": 200,
        "fleet_total": sum(experiment["initial_vehicles"].values()),
        "behavior_scenarios": scenarios,
        "sensitivity_parameter": experiment.get(
            "sensitivity_parameter", "elder_ride_hailing_mode_constant"
        ),
        "weather_scenarios": experiment["weather_scenarios"],
        "all_checks_passed": all(row["passed"] for row in checks),
        "interpretation": "behavioral sensitivity, not a calibrated elderly ride-hailing share or health coefficient",
    }
    (args.output_dir / "experiment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Files: {args.output_dir.resolve()}")
    if not metadata["all_checks_passed"]:
        raise SystemExit("Pairing checks failed")


if __name__ == "__main__":
    main()
