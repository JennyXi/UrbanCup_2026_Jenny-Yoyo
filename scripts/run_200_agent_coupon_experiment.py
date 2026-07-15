"""Run C0-C3 at 200 agents with proportional fleet and coupon coverage."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from custom.agents.emergence_experiment import load_emergence_config
from scripts.run_coupon_competition_experiment import run_coupon_experiment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "agent_200_coupon_experiment.json"


def load_agent_200_coupon_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_run_config(experiment: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(load_emergence_config())
    config["total_agents"] = int(experiment["total_agents"])
    config["coupon_experiment"]["daily_total_coupon_pool"] = int(
        experiment["daily_total_coupon_pool"]
    )
    config["ride_hailing_feedback"]["initial_daily_vehicles_by_day_type"] = copy.deepcopy(
        experiment["initial_daily_vehicles_by_day_type"]
    )
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/coupon_competition_200_agents_smoke_3")
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--seed-count", type=int)
    args = parser.parse_args()
    experiment = load_agent_200_coupon_config()
    seed_start = args.seed_start if args.seed_start is not None else int(experiment["seed_start"])
    seed_count = args.seed_count if args.seed_count is not None else int(experiment["default_seed_count"])
    tables = run_coupon_experiment(
        seed_start=seed_start,
        seed_count=seed_count,
        output=Path(args.output),
        config=build_run_config(experiment),
    )
    checks = tables["consistency_checks"]
    print(f"Completed {len(tables['system_per_seed'])} policy-weather-day rows")
    print(f"Consistency checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print("Population: 200 agents")
    print("Daily coupon pool: 40")
    print("Fleet: workday 52; rest day 44")
    print(f"Output: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
