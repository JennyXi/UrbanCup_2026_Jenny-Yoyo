"""Run the formal nine-zone 50-Agent W0/W1/W2 P0 experiment."""

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
    run_formal_nine_zone_50_experiment,
)


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50"


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = [{key: _value(value) for key, value in row.items()} for row in rows]
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _purpose_summary(activity_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        groups[(row["weather_scenario"], row["day_type"], row["activity_purpose"])].append(row)
    output = []
    for (weather, day_type, purpose), rows in sorted(groups.items()):
        exposed = sum(row["weather_exposed"] for row in rows)
        cancelled = sum(row["weather_cancellation"] for row in rows)
        output.append({
            "weather_scenario": weather, "day_type": day_type,
            "activity_purpose": purpose, "planned_activities": len(rows),
            "weather_exposed_activities": exposed,
            "weather_cancelled_activities": cancelled,
            "conditional_cancel_rate": round(cancelled / exposed, 6) if exposed else None,
            "remote_work": sum(row["remote_work"] for row in rows),
            "completed_activities": sum(row["completed"] for row in rows),
            "transport_unmet": sum(row["transport_unmet"] for row in rows),
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--w0-only", action="store_true")
    args = parser.parse_args()
    with args.config.open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    result = run_formal_nine_zone_50_experiment(
        config=config, seed=args.seed,
        weather_scenarios=("W0",) if args.w0_only else None,
    )
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "agents.csv", result["inputs"]["agents"])
    _write_csv(output / "planned_activities_main_od.csv", result["inputs"]["activities"])
    _write_csv(output / "planned_legs_main_od.csv", result["inputs"]["legs"])
    _write_csv(output / "activity_states.csv", result["activity_states"])
    _write_csv(output / "activity_results.csv", result["activity_results"])
    _write_csv(output / "mode_choices.csv", result["mode_choices"])
    _write_csv(output / "ride_hailing_dispatch.csv", result["ride_hailing_dispatch"])
    _write_csv(output / "vehicle_end_states.csv", result["vehicle_end_states"])
    _write_csv(output / "macro_summary.csv", result["summary_rows"])
    _write_csv(output / "activity_purpose_summary.csv", _purpose_summary(result["activity_results"]))
    compact = {
        "experiment_id": config["experiment_id"],
        "seed": result["inputs"]["seed"],
        "agent_count": len(result["inputs"]["agents"]),
        "enabled_modes": result["formal_config"]["enabled_modes"],
        "main_od_pipeline": "T1 population -> home-zone quotas -> seven-day activities -> T6 destinations -> feasible legs",
        "summary_rows": result["summary_rows"],
    }
    (output / "experiment_summary.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("Formal nine-zone 50-Agent experiment complete")
    for row in result["summary_rows"]:
        print(
            f"  {row['weather_scenario']} {row['day_type']}: "
            f"activities={row['planned_activities']}, cancel={row['weather_cancelled_activities']}, "
            f"remote={row['remote_work']}, walk/bus/metro/RH="
            f"{row['walking_legs']}/{row['bus_legs']}/{row['metro_legs']}/{row['ride_hailing_legs']}, "
            f"unmet={row['transport_related_unmet']}, "
            f"avg_time={row['mean_total_travel_time']} min"
        )
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
