"""Add realized heat and rain exposure metrics to completed platform runs.

This is a read-only postprocessor for AgentSociety API experiment outputs.  It
does not call an LLM and does not alter mode choices, dispatch, congestion, or
activity completion.  New files are written inside each input run directory.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from custom.agents.emergence_experiment import (
    calculate_heat_hazard_dose,
    heat_vulnerability_weight,
    load_emergence_config,
)
from custom.agents.formal_nine_zone_50_experiment import (
    _outdoor_segments,
)
from custom.agents.formal_nine_zone_experiment import (
    ROOT,
    _events_for,
    load_formal_nine_zone_config,
)
from custom.transport.network import build_transport_network


DEFAULT_PLATFORM_CONFIG = ROOT / "config" / "formal_nine_zone_2000_api_360_vehicle.json"
OUTPUT_LEGS = "leg_weather_exposure.csv"
OUTPUT_GROUPS = "group_weather_exposure_summary.csv"
OUTPUT_SCENARIO = "scenario_weather_exposure_summary.csv"
OUTPUT_METADATA = "weather_exposure_metadata.json"

NUMERIC_FIELDS = {
    "failed_attempt_consumed_minutes", "wait_minutes", "access_time_min",
    "transfer_time_min", "in_vehicle_time_min", "total_travel_time_min",
    "origin_feeder_total_time_minutes", "origin_feeder_access_minutes",
    "origin_feeder_wait_minutes", "destination_feeder_total_time_minutes",
    "destination_feeder_access_minutes", "destination_feeder_wait_minutes",
    "origin_metro_walk_access_minutes", "destination_metro_walk_access_minutes",
    "bus_metro_transfer_count",
}
DATETIME_FIELDS = {"departure_time", "final_attempt_departure_time"}
BOOL_FIELDS = {
    "transport_succeeded", "fallback_attempted", "fallback_succeeded",
    "activity_completed", "digital_access", "family_assistance", "is_mandatory",
    "completed", "transport_unmet",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"required output file is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise ValueError(f"refusing to write an empty result: {path}")
    fields = list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", "", "none", "nan"}:
        return False
    raise ValueError(f"cannot parse boolean value: {value!r}")


def _typed_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(raw)
    for field in NUMERIC_FIELDS:
        if field in row:
            row[field] = float(row[field] or 0.0)
    for field in BOOL_FIELDS:
        if field in row:
            row[field] = _bool(row[field])
    for field in DATETIME_FIELDS:
        if field in row and row[field]:
            row[field] = datetime.fromisoformat(str(row[field]))
    if not row.get("final_attempt_departure_time"):
        row["final_attempt_departure_time"] = row.get("departure_time")
    return row


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_formal_config(platform_path: Path) -> dict[str, Any]:
    platform = json.loads(platform_path.read_text(encoding="utf-8-sig"))
    formal_path = Path(platform["formal_transport_config"])
    if not formal_path.is_absolute():
        candidate = platform_path.parent / formal_path
        formal_path = candidate if candidate.exists() else ROOT / "config" / formal_path.name
    formal = load_formal_nine_zone_config(formal_path)
    return _deep_merge(formal, platform.get("formal_overrides", {}))


def _rain_overlap_minutes(
    start: datetime, duration: float, events: Sequence[Mapping[str, Any]],
) -> float:
    from datetime import timedelta

    end = start + timedelta(minutes=duration)
    return sum(
        max(0.0, (min(end, event["end"]) - max(start, event["start"])).total_seconds() / 60.0)
        for event in events
        if min(end, event["end"]) > max(start, event["start"])
    )


def _heat_config_at_threshold(base: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    config = copy.deepcopy(dict(base))
    config["heat_exposure"]["heat_stress_threshold_c"] = float(threshold)
    return config


def _agent_attributes(audit_rows: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    agents: dict[int, dict[str, Any]] = {}
    for raw in audit_rows:
        agent_id = int(raw["agent_id"])
        attributes = {
            "age_group": str(raw["age_group"]),
            "digital_access": _bool(raw.get("digital_access")),
            "family_assistance": _bool(raw.get("family_assistance")),
            "elder_access_policy": str(raw.get("elder_access_policy") or ""),
        }
        if agent_id in agents and agents[agent_id] != attributes:
            raise ValueError(f"inconsistent audit attributes for agent {agent_id}")
        agents[agent_id] = attributes
    return agents


def _elder_access_group(attributes: Mapping[str, Any]) -> str:
    if attributes["age_group"] != "60+":
        return "under_60"
    if attributes["digital_access"]:
        return "60+_digital"
    if attributes["family_assistance"]:
        return "60+_nondigital_assisted"
    return "60+_nondigital_unassisted"


def calculate_leg_exposure_rows(
    mode_rows: Sequence[Mapping[str, Any]], audit_rows: Sequence[Mapping[str, Any]],
    formal_config: Mapping[str, Any], heat_config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Calculate exposure without changing any original behavioral field."""
    agents = _agent_attributes(audit_rows)
    network = build_transport_network()
    heat26 = _heat_config_at_threshold(heat_config, 26.0)
    heat32 = _heat_config_at_threshold(heat_config, 32.0)
    results: list[dict[str, Any]] = []
    for raw in mode_rows:
        row = _typed_row(raw)
        agent_id = int(row["agent_id"])
        if agent_id not in agents:
            raise ValueError(f"agent {agent_id} is missing from decision_audit.csv")
        weather = str(row["weather_scenario"])
        day_type = str(row["day_type"])
        segments = _outdoor_segments(row, network)
        events = _events_for(formal_config, weather, day_type)
        outdoor = sum(duration for _start, duration in segments)
        rain = (
            sum(_rain_overlap_minutes(start, duration, events) for start, duration in segments)
            if weather == "W2" else 0.0
        )
        dose26 = sum(
            calculate_heat_hazard_dose(start.hour * 60 + start.minute + start.second / 60.0,
                                       duration, weather, config=heat26)
            for start, duration in segments
        )
        dose32 = sum(
            calculate_heat_hazard_dose(start.hour * 60 + start.minute + start.second / 60.0,
                                       duration, weather, config=heat32)
            for start, duration in segments
        )
        attrs = agents[agent_id]
        vulnerability = heat_vulnerability_weight(attrs["age_group"], config=heat_config)
        values = [outdoor, rain, dose26, dose32, vulnerability]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError(f"invalid exposure result for leg {row['leg_id']}")
        results.append({
            "leg_id": row["leg_id"],
            "agent_id": agent_id,
            "activity_id": row.get("activity_id", ""),
            "purpose": row.get("purpose", ""),
            "leg_role": row.get("leg_role", ""),
            "origin_zone": row.get("origin_zone", ""),
            "destination_zone": row.get("destination_zone", ""),
            "departure_time": row["departure_time"].isoformat(sep=" "),
            "weather_scenario": weather,
            "day_type": day_type,
            "policy": row.get("policy", ""),
            "primary_mode": row.get("primary_mode", ""),
            "final_mode": row.get("final_mode", ""),
            "transport_succeeded": row.get("transport_succeeded", False),
            "fallback_attempted": row.get("fallback_attempted", False),
            "fallback_succeeded": row.get("fallback_succeeded", False),
            "age_group": attrs["age_group"],
            "digital_access": attrs["digital_access"],
            "family_assistance": attrs["family_assistance"],
            "elder_access_group": _elder_access_group(attrs),
            "outdoor_segments": [
                {"start_time": start.isoformat(sep=" "), "duration_minutes": round(duration, 6)}
                for start, duration in segments
            ],
            "outdoor_exposure_minutes": round(outdoor, 6),
            "failed_attempt_outdoor_exposure_minutes": round(
                float(row.get("failed_attempt_consumed_minutes") or 0.0), 6
            ),
            "heat_hazard_dose_c_min_threshold_26": round(dose26, 6),
            "heat_hazard_dose_c_min_threshold_32": round(dose32, 6),
            "age_vulnerability_weight": round(vulnerability, 6),
            "heat_risk_burden_threshold_26": round(dose26 * vulnerability, 6),
            "heat_risk_burden_threshold_32": round(dose32 * vulnerability, 6),
            "rain_exposure_minutes": round(rain, 6),
        })
    return results


def _summarize_group(rows: Sequence[Mapping[str, Any]], group: str, value: str) -> dict[str, Any]:
    return {
        "weather_scenario": rows[0]["weather_scenario"],
        "day_type": rows[0]["day_type"],
        "policy": rows[0]["policy"],
        "group_dimension": group,
        "group_value": value,
        "legs": len(rows),
        "agents": len({int(row["agent_id"]) for row in rows}),
        "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in rows), 6),
        "mean_outdoor_exposure_minutes_per_leg": round(mean(float(row["outdoor_exposure_minutes"]) for row in rows), 6),
        "total_heat_hazard_dose_c_min_threshold_26": round(sum(float(row["heat_hazard_dose_c_min_threshold_26"]) for row in rows), 6),
        "total_heat_hazard_dose_c_min_threshold_32": round(sum(float(row["heat_hazard_dose_c_min_threshold_32"]) for row in rows), 6),
        "total_heat_risk_burden_threshold_26": round(sum(float(row["heat_risk_burden_threshold_26"]) for row in rows), 6),
        "total_heat_risk_burden_threshold_32": round(sum(float(row["heat_risk_burden_threshold_32"]) for row in rows), 6),
        "total_rain_exposure_minutes": round(sum(float(row["rain_exposure_minutes"]) for row in rows), 6),
    }


def group_summaries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = [_summarize_group(rows, "all", "all")]
    for dimension in ("age_group", "digital_access", "elder_access_group", "final_mode", "purpose"):
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row[dimension])].append(row)
        output.extend(_summarize_group(group, dimension, value) for value, group in sorted(grouped.items()))
    return output


def scenario_summary(
    leg_rows: Sequence[Mapping[str, Any]], activity_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    activities = [_typed_row(row) for row in activity_rows]
    legs_by_activity: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in leg_rows:
        legs_by_activity[(int(row["agent_id"]), str(row["activity_id"]))].append(row)
    necessary = [row for row in activities if row.get("is_mandatory", False)]
    planned_travel_required = [
        row for row in necessary
        if (int(row["agent_id"]), str(row["activity_id"])) in legs_by_activity
    ]
    completed_travel_required = [row for row in planned_travel_required if row.get("completed", False)]
    necessary_keys = {(int(row["agent_id"]), str(row["activity_id"])) for row in necessary}
    necessary_legs = [
        row for row in leg_rows
        if (int(row["agent_id"]), str(row["activity_id"])) in necessary_keys
    ]
    risk26 = sum(float(row["heat_risk_burden_threshold_26"]) for row in necessary_legs)
    risk32 = sum(float(row["heat_risk_burden_threshold_32"]) for row in necessary_legs)
    def safe_div(numerator: float, denominator: int) -> float | None:
        return round(numerator / denominator, 6) if denominator else None
    return {
        "weather_scenario": leg_rows[0]["weather_scenario"],
        "day_type": leg_rows[0]["day_type"],
        "policy": leg_rows[0]["policy"],
        "legs": len(leg_rows),
        "agents_with_legs": len({int(row["agent_id"]) for row in leg_rows}),
        "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in leg_rows), 6),
        "total_heat_hazard_dose_c_min_threshold_26": round(sum(float(row["heat_hazard_dose_c_min_threshold_26"]) for row in leg_rows), 6),
        "total_heat_hazard_dose_c_min_threshold_32": round(sum(float(row["heat_hazard_dose_c_min_threshold_32"]) for row in leg_rows), 6),
        "total_heat_risk_burden_threshold_26": round(sum(float(row["heat_risk_burden_threshold_26"]) for row in leg_rows), 6),
        "total_heat_risk_burden_threshold_32": round(sum(float(row["heat_risk_burden_threshold_32"]) for row in leg_rows), 6),
        "necessary_heat_risk_burden_threshold_26": round(risk26, 6),
        "necessary_heat_risk_burden_threshold_32": round(risk32, 6),
        "planned_travel_required_necessary_activities": len(planned_travel_required),
        "completed_travel_required_necessary_activities": len(completed_travel_required),
        "heat_risk_per_planned_travel_required_necessary_activity_threshold_26": safe_div(risk26, len(planned_travel_required)),
        "heat_risk_per_planned_travel_required_necessary_activity_threshold_32": safe_div(risk32, len(planned_travel_required)),
        "heat_risk_per_completed_travel_required_necessary_activity_threshold_26": safe_div(risk26, len(completed_travel_required)),
        "heat_risk_per_completed_travel_required_necessary_activity_threshold_32": safe_div(risk32, len(completed_travel_required)),
        "total_rain_exposure_minutes": round(sum(float(row["rain_exposure_minutes"]) for row in leg_rows), 6),
    }


def process_run(input_dir: Path, platform_config: Path) -> dict[str, Any]:
    summary_path = input_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"summary.json is missing, so this is not a completed run: {input_dir}")
    original_summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    mode_rows = _read_csv(input_dir / "mode_choices.csv")
    audit_rows = _read_csv(input_dir / "decision_audit.csv")
    activity_rows = _read_csv(input_dir / "activity_results.csv")
    formal = _load_formal_config(platform_config)
    heat = load_emergence_config()
    legs = calculate_leg_exposure_rows(mode_rows, audit_rows, formal, heat)
    if len(legs) != int(original_summary["travel_decisions"]):
        raise ValueError("leg count changed during postprocessing")
    groups = group_summaries(legs)
    scenario = scenario_summary(legs, activity_rows)
    _write_csv(input_dir / OUTPUT_LEGS, legs)
    _write_csv(input_dir / OUTPUT_GROUPS, groups)
    _write_csv(input_dir / OUTPUT_SCENARIO, [scenario])
    metadata = {
        "status": "PASS",
        "source_run": str(input_dir.resolve()),
        "source_summary_status": original_summary.get("status"),
        "behavioral_outputs_changed": False,
        "llm_calls_added": 0,
        "heat_definition": "UTCI degree-minutes above threshold, integrated over realized outdoor segments",
        "heat_units": "degree Celsius minutes (C min)",
        "heat_risk_definition": "heat hazard dose multiplied by scenario age vulnerability weight",
        "heat_risk_units": "age-weighted C min (scenario burden index, not a clinical probability)",
        "heat_thresholds_c": [26.0, 32.0],
        "heat_enabled_weather": "W1 only; the full day uses the periodic UTCI curve",
        "rain_definition": "realized outdoor minutes overlapping configured heavy-rain windows",
        "rain_units": "minutes",
        "rain_enabled_weather": "W2 only",
        "outdoor_definition": {
            "walk": "entire successful walking attempt",
            "bus": "origin access walk, wait, and destination access walk",
            "metro": "station/feeder access and egress walking plus outdoor feeder-bus waits; metro platform wait excluded",
            "ride_hailing": "pickup wait",
            "failed_ride_hailing": "consumed wait before fallback, counted once",
        },
        "files": [OUTPUT_LEGS, OUTPUT_GROUPS, OUTPUT_SCENARIO, OUTPUT_METADATA],
    }
    (input_dir / OUTPUT_METADATA).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"input_dir": str(input_dir), **scenario}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dirs", nargs="+", type=Path, help="completed platform output directories")
    parser.add_argument("--platform-config", type=Path, default=DEFAULT_PLATFORM_CONFIG)
    parser.add_argument(
        "--combined-output", type=Path,
        help="optional CSV containing one exposure-summary row per processed run",
    )
    args = parser.parse_args()
    results = [process_run(path, args.platform_config) for path in args.input_dirs]
    if args.combined_output:
        args.combined_output.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(args.combined_output, results)
    print(json.dumps({"status": "PASS", "processed_runs": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
