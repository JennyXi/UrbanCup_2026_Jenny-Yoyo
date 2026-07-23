"""Build the reproducible 2000-Agent rest-day platform run plan.

This script makes no LLM calls. It creates paired C1/C2/C3 coupon allocations,
a 19-scenario manifest, and a resumable shell runner. Each completed scenario is
then passed to the existing offline heat/rain exposure postprocessor.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.agent_population import AgentProfile  # noqa: E402
from custom.agents.coupon_experiment import allocate_daily_coupons  # noqa: E402
from custom.agents.formal_nine_zone_experiment import (  # noqa: E402
    build_formal_nine_zone_inputs,
)
from scripts.run_city_mobility_200_api import _build_formal_config  # noqa: E402


DEFAULT_PLATFORM_CONFIG = (
    ROOT / "config" / "formal_nine_zone_2000_api_360_vehicle_rest_day.json"
)
DEFAULT_COUPON_CONFIG = ROOT / "config" / "formal_nine_zone_50_coupon_experiment.json"


def scenario_specs() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for weather in ("W0", "W1", "W2"):
        rows.append({"scenario": f"{weather}_C0", "weather": weather, "policy": "C0"})
    for policy in ("C1", "C2", "C3"):
        for weather in ("W0", "W1", "W2"):
            rows.append({"scenario": f"{weather}_{policy}", "weather": weather, "policy": policy})
    for policy in ("D1", "D3"):
        for weather in ("W0", "W1", "W2"):
            rows.append({"scenario": f"{weather}_{policy}", "weather": weather, "policy": policy})
    rows.append({"scenario": "W2_P4", "weather": "W2", "policy": "P4"})
    return rows


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    fields = list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(materialized)


def _coupon_allocations(
    platform_config: Path,
    coupon_config_path: Path,
    output_dir: Path,
    seed: int,
) -> dict[str, Path]:
    experiment = json.loads(platform_config.read_text(encoding="utf-8-sig"))
    policy_design = experiment["large_scale_policy_design"]
    _, formal = _build_formal_config(platform_config, {}, 0.8)
    inputs = build_formal_nine_zone_inputs(config=formal, seed=seed)
    profiles = [AgentProfile(**dict(row)) for row in inputs["agents"]]
    coupon_config = json.loads(coupon_config_path.read_text(encoding="utf-8-sig"))
    coupon_config = copy.deepcopy(coupon_config)
    coupon_config["coupon_experiment"]["daily_total_coupon_pool"] = int(
        policy_design["coupon_pool_size"]
    )
    coupon_dir = output_dir / "coupon_allocations"
    coupon_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    names = {
        "C1": "C1_public_limited",
        "C2": "C2_elder_limited",
        "C3": "C3_mixed",
    }
    for short_name, policy in names.items():
        allocations = allocate_daily_coupons(
            profiles, policy, "rest_day", seed=seed, config=coupon_config
        )
        path = coupon_dir / f"{short_name.lower()}_rest_day_seed{seed}.json"
        payload = {
            "summary": {
                "policy": policy,
                "day_type": "rest_day",
                "seed": seed,
                "agents": len(profiles),
                "coupon_pool_size": int(policy_design["coupon_pool_size"]),
                "awarded": sum(bool(row["coupon_awarded"]) for row in allocations),
                "api_contribution_decisions": 0,
                "common_across_weather": True,
            },
            "allocations": allocations,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths[short_name] = path
    return paths


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _scenario_arguments(
    row: Mapping[str, str], coupon_paths: Mapping[str, Path]
) -> list[str]:
    policy = row["policy"]
    args = ["--elder-access-policy", "D0"]
    if policy in coupon_paths:
        args += ["--coupon-result", _relative(coupon_paths[policy])]
    elif policy in {"D1", "D3"}:
        args = ["--elder-access-policy", policy]
    elif policy == "P4":
        args += ["--dispatch-priority-policy", "P4_elder_priority"]
    return args


def build_plan(
    *, platform_config: Path, coupon_config: Path, output_dir: Path, seed: int
) -> dict[str, Any]:
    experiment = json.loads(platform_config.read_text(encoding="utf-8-sig"))
    if int(experiment["total_agents"]) != 2000:
        raise ValueError("rest-day platform plan must use 2000 Agents")
    if tuple(experiment["run_day_types"]) != ("rest_day",):
        raise ValueError("rest-day platform config must run rest_day only")
    output_dir.mkdir(parents=True, exist_ok=True)
    coupon_paths = _coupon_allocations(
        platform_config, coupon_config, output_dir, seed
    )
    rows = []
    for spec in scenario_specs():
        scenario_output = (
            f"outputs/platform_2000_rest_day_{spec['scenario']}_seed{seed}_360_vehicle"
        )
        rows.append({
            **spec,
            "day_type": "rest_day",
            "seed": seed,
            "agents": 2000,
            "vehicles": 360,
            "represented_trips_per_agent": 3.0,
            "output_dir": scenario_output,
            "coupon_result": (
                _relative(coupon_paths[spec["policy"]])
                if spec["policy"] in coupon_paths else ""
            ),
            "elder_access_policy": spec["policy"] if spec["policy"] in {"D1", "D3"} else "D0",
            "dispatch_priority_policy": "P4_elder_priority" if spec["policy"] == "P4" else "P0_first_come",
        })
    _write_csv(output_dir / "scenario_manifest.csv", rows)
    manifest = {
        "status": "READY",
        "llm_calls_made": 0,
        "platform_config": _relative(platform_config),
        "seed": seed,
        "day_type": "rest_day",
        "scenario_count": len(rows),
        "scenarios": rows,
    }
    (output_dir / "scenario_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    config_rel = _relative(platform_config)
    shell = [
        "#!/usr/bin/env bash",
        "set -u",
        'PYTHON="${PYTHON:-.venv/bin/python}"',
        'MODEL="${AGENTSOCIETY_LLM_MODEL:-qwen3.6-35b-a3b-no-think}"',
        "failures=0",
        "",
        "run_one() {",
        '  scenario="$1"; shift',
        '  output_dir="$1"; shift',
        '  if [ -s "$output_dir/summary.json" ]; then',
        '    if [ ! -s "$output_dir/scenario_weather_exposure_summary.csv" ]; then',
        '      "$PYTHON" -B -X utf8 -m scripts.postprocess_platform_weather_exposure \\',
        '        "$output_dir" --platform-config ' + shlex.quote(config_rel) + ' \\',
        '        > "$output_dir/exposure_postprocess.log" 2>&1',
        "    fi",
        '    echo "SKIP completed $scenario"',
        "    return 0",
        "  fi",
        '  mkdir -p "$output_dir"',
        '  echo "START $scenario"',
        '  env AGENTSOCIETY_LLM_MODEL="$MODEL" "$PYTHON" -B -X utf8 \\',
        "    -m scripts.run_city_mobility_200_api \\",
        f"    --formal-experiment-config {shlex.quote(config_rel)} \\",
        "    --day-type rest_day --seed " + str(seed) + " \\",
        "    --represented-trips-per-agent 3.0 --concurrency 8 \\",
        "    --progress-every 100 --output-dir \"$output_dir\" \"$@\" \\",
        '    > "$output_dir/run.log" 2>&1',
        "  status=$?",
        '  if [ "$status" -ne 0 ] || [ ! -s "$output_dir/summary.json" ]; then',
        '    echo "FAILED $scenario exit=$status; see $output_dir/run.log"',
        "    return 1",
        "  fi",
        '  "$PYTHON" -B -X utf8 -m scripts.postprocess_platform_weather_exposure \\',
        '    "$output_dir" --platform-config ' + shlex.quote(config_rel) + ' \\',
        '    > "$output_dir/exposure_postprocess.log" 2>&1',
        '  echo "DONE $scenario"',
        "}",
        "",
    ]
    for row in rows:
        extra = _scenario_arguments(row, coupon_paths)
        quoted_extra = " ".join(shlex.quote(value) for value in extra)
        shell.append(
            "run_one "
            + shlex.quote(row["scenario"])
            + " "
            + shlex.quote(row["output_dir"])
            + " --weather-scenario "
            + shlex.quote(row["weather"])
            + " "
            + quoted_extra
            + " || failures=$((failures + 1))"
        )
    shell += [
        "",
        'if [ "$failures" -ne 0 ]; then',
        '  echo "REST-DAY MATRIX FINISHED WITH $failures FAILED SCENARIOS"',
        "  exit 1",
        "fi",
        f'echo "REST-DAY MATRIX FINISHED: {len(rows)} scenarios, 0 failures"',
    ]
    runner_path = output_dir / "run_rest_day_matrix.sh"
    runner_path.write_text("\n".join(shell) + "\n", encoding="utf-8", newline="\n")
    return {**manifest, "runner": _relative(runner_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-config", type=Path, default=DEFAULT_PLATFORM_CONFIG)
    parser.add_argument("--coupon-config", type=Path, default=DEFAULT_COUPON_CONFIG)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "outputs" / "platform_2000_rest_day_seed47_plan",
    )
    args = parser.parse_args()
    result = build_plan(
        platform_config=args.platform_config,
        coupon_config=args.coupon_config,
        output_dir=args.output_dir,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
