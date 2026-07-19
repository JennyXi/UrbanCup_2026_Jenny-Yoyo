"""Run the paired 50-Agent W0/W2 workday P0 versus transport stress audit."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)

STRESS_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_stress_test.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50_stress_test"


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    normalized = [{key: _value(value) for key, value in row.items()} for row in rows]
    if not normalized:
        return
    fields = list(dict.fromkeys(key for row in normalized for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(normalized)


def _vehicle_overlap_free(dispatch: Iterable[Mapping[str, Any]]) -> bool:
    by_vehicle: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in dispatch:
        if row["succeeded"]:
            by_vehicle[(row["weather_scenario"], row["vehicle_id"])].append(row)
    for rows in by_vehicle.values():
        ordered = sorted(rows, key=lambda row: float(row["busy_start"]))
        if any(
            float(later["busy_start"]) + 1e-9 < float(earlier["busy_until"])
            for earlier, later in zip(ordered, ordered[1:])
        ):
            return False
    return True


def _audit(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    choices = result["mode_choices"]
    dispatch = result["ride_hailing_dispatch"]
    states = result["vehicle_end_states"]
    audits = []
    for summary in result["summary_rows"]:
        weather = summary["weather_scenario"]
        scenario_choices = [row for row in choices if row["weather_scenario"] == weather]
        scenario_dispatch = [row for row in dispatch if row["weather_scenario"] == weather]
        scenario_states = [row for row in states if row["weather_scenario"] == weather]
        fallback_time_ok = all(
            abs(
                (row["final_attempt_departure_time"] - row["departure_time"]).total_seconds() / 60.0
                - float(row["failed_attempt_consumed_minutes"])
            ) < 1e-6
            for row in scenario_choices if row["fallback_attempted"]
        )
        threshold_ok = all(
            row["activity_completed"] == (
                not row["maximum_commute_time_exceeded"]
                and not row["maximum_lateness_exceeded"]
            )
            for row in scenario_choices
            if row["leg_role"] != "return_home" and row["transport_succeeded"]
        )
        audits.append({
            "experiment_condition": summary["experiment_condition"],
            "weather_scenario": weather,
            "initial_vehicle_total": summary["initial_ride_hailing_vehicles"],
            "end_vehicle_total": len(scenario_states),
            "vehicle_conservation_pass": len(scenario_states) == summary["initial_ride_hailing_vehicles"],
            "vehicle_service_overlap_free": _vehicle_overlap_free(scenario_dispatch),
            "fallback_departure_time_updated": fallback_time_ok,
            "completion_threshold_logic_pass": threshold_ok,
        })
    return audits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    configs = [load_formal_50_config(DEFAULT_CONFIG_PATH), load_formal_50_config(STRESS_CONFIG_PATH)]
    results = [
        run_formal_nine_zone_50_experiment(
            config=config, seed=args.seed,
            weather_scenarios=("W0", "W2"), day_types=("workday",),
        )
        for config in configs
    ]
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "macro_summary.csv", [row for result in results for row in result["summary_rows"]])
    _write_csv(output / "mode_choices.csv", [row for result in results for row in result["mode_choices"]])
    _write_csv(output / "activity_results.csv", [row for result in results for row in result["activity_results"]])
    _write_csv(output / "ride_hailing_dispatch.csv", [row for result in results for row in result["ride_hailing_dispatch"]])
    _write_csv(output / "vehicle_end_states.csv", [row for result in results for row in result["vehicle_end_states"]])
    audits = [row for result in results for row in _audit(result)]
    _write_csv(output / "logic_audit.csv", audits)

    print("Formal nine-zone 50-Agent stress comparison complete")
    for result in results:
        for row in result["summary_rows"]:
            print(
                f"  {row['experiment_condition']} {row['weather_scenario']} workday: "
                f"fleet={row['initial_ride_hailing_vehicles']}, "
                f"RH requests/success/failed={row['ride_hailing_requests']}/"
                f"{row['successful_ride_hailing_requests']}/{row['ride_hailing_failed']}, "
                f"fallback success/failed={row['fallback_succeeded']}/{row['fallback_failed']}, "
                f"late_reached={row['late_but_reached']}, transport_unmet={row['transport_unmet']}, "
                f"mandatory_incomplete={row['mandatory_activity_incomplete']}, "
                f"completion={row['activity_completion_rate']}"
            )
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
