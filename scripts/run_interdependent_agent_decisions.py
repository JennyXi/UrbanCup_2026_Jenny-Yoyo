"""Run the sequential shared-state Agent decision mechanism."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.interdependent_decision_system import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_interdependent_decision_config,
    run_interdependent_decision_experiment,
)


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "interdependent_agent_decisions"


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return
    fields = list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {key: _value(value) for key, value in row.items()}
            for row in materialized
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run chronological Agent choices with immediate shared-road feedback.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--weather-scenario", choices=("W0", "W1", "W2"), default=None)
    parser.add_argument("--day-type", choices=("workday", "rest_day"), default=None)
    args = parser.parse_args()

    coupling = load_interdependent_decision_config(args.config)
    formal_path = args.config.parent / coupling["formal_config_path"]
    with formal_path.open(encoding="utf-8-sig") as stream:
        formal = json.load(stream)
    result = run_interdependent_decision_experiment(
        coupling_config=coupling,
        formal_config=formal,
        seed=args.seed,
        weather_scenario=args.weather_scenario,
        day_type=args.day_type,
    )

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "decision_audit.csv", result["decisions"])
    _write_csv(output / "traffic_state_events.csv", result["traffic_state_events"])
    _write_csv(output / "traffic_state_final.csv", result["traffic_state_rows"])
    _write_csv(output / "influence_edges.csv", result["influence_edges"])
    (output / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = result["summary"]
    print("Interdependent Agent decision experiment complete")
    print(
        f"  decisions={summary['decision_count']}, "
        f"ride_hailing={summary['ride_hailing_choice_count']}, "
        f"affected_by_prior_agents={summary['affected_decision_count']}, "
        f"max_probability_change={summary['maximum_absolute_probability_change']}"
    )
    resolved_output = output.resolve()
    try:
        display_output = resolved_output.relative_to(Path.cwd().resolve())
    except ValueError:
        display_output = output
    print(f"Files: {display_output}")


if __name__ == "__main__":
    main()
