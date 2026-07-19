"""Paired 50-agent experiment with one weather-stable S1-S2 metro line."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping

from custom.agents.emergence_experiment import (
    load_emergence_config,
    run_emergence_experiment,
    summarize_macro,
)
from custom.agents.simple_mode_choice import load_simple_config
from custom.agents.symmetric_weather_experiment import load_symmetric_experiment_config


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "metro_50_agent_experiment.json"
SCENARIOS = ("M0_no_metro", "M1_optimistic_metro", "M2_realistic_access")


def load_metro_experiment_config(path: Path | str = CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if int(config["total_agents"]) != 50:
        raise ValueError("the first metro experiment must use 50 agents")
    if tuple(config["scenarios"]) != SCENARIOS:
        raise ValueError(f"metro scenarios must be {SCENARIOS}")
    probabilities = config["metro_success_probability"]
    if set(probabilities) != {"W0", "W1", "W2"}:
        raise ValueError("metro success probability must cover W0/W1/W2")
    if any(float(value) != 1.0 for value in probabilities.values()):
        raise ValueError("the stable-metro experiment requires success probability 1.0")
    return config


def load_metro_transport_config(
    experiment: Mapping[str, Any] | None = None, *, scenario: str = "M1_optimistic_metro",
) -> Dict[str, Any]:
    experiment = experiment or load_metro_experiment_config()
    if scenario not in SCENARIOS[1:]:
        raise ValueError("metro transport config is only available for M1/M2")
    path = ROOT / str(experiment["metro_transport_config"])
    transport = copy.deepcopy(load_simple_config(path))
    if scenario == "M2_realistic_access":
        transport["metro_zone_service_parameters"] = copy.deepcopy(
            experiment["realistic_metro_zone_service_parameters"]
        )
    return transport


def build_metro_symmetric_config(
    experiment: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    experiment = experiment or load_metro_experiment_config()
    symmetric = copy.deepcopy(load_symmetric_experiment_config())
    for week, probability in experiment["metro_success_probability"].items():
        symmetric["transport_success_probability"][week]["metro"] = float(probability)
    symmetric["failed_attempt_charge_fraction"]["metro"] = float(
        experiment["metro_failed_attempt_charge_fraction"]
    )
    return symmetric


def run_metro_scenario(
    seed: int, scenario: str, *, experiment: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    experiment = experiment or load_metro_experiment_config()
    emergence = copy.deepcopy(load_emergence_config())
    emergence["total_agents"] = int(experiment["total_agents"])
    symmetric = build_metro_symmetric_config(experiment)
    transport = (
        load_metro_transport_config(experiment, scenario=scenario)
        if scenario != "M0_no_metro" else None
    )
    result = run_emergence_experiment(
        seed, config=emergence, symmetric=symmetric, transport_config=transport,
    )
    result["scenario"] = scenario
    return result


def summarize_metro_scenario(result: Mapping[str, Any]) -> list[Dict[str, Any]]:
    return [{"scenario": result["scenario"], **row} for row in summarize_macro(result)]
