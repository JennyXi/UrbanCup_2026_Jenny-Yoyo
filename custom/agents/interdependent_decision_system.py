"""Sequential Agent decisions coupled through a shared road-state registry.

The existing formal experiment uses bounded batch feedback.  This module adds
an auditable event-driven alternative: each Agent reads the current traffic
state, receives mode probabilities, makes one reproducible draw, and publishes
the traffic impact of a ride-hailing choice before the next Agent decides.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from custom.agents.formal_nine_zone_experiment import (
    ENABLED_MODES,
    _annotate_schedule_constraints,
    _choose_all,
    _events_for,
    _option,
    _reschedule_selected,
    _scheduled_bus_trips_per_bin,
    _score_options,
    build_formal_nine_zone_inputs,
    load_formal_nine_zone_config,
    validate_formal_nine_zone_config,
)
from custom.transport.network import build_transport_network


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "interdependent_agent_decisions.json"


def _stable_uniform(seed: int, *parts: Any) -> float:
    payload = "|".join(map(str, (seed, *parts))).encode("utf-8")
    number = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return (number + 0.5) / 2**64


def _bin_start(moment: datetime, bin_minutes: int) -> datetime:
    minute = moment.hour * 60 + moment.minute
    floored = (minute // bin_minutes) * bin_minutes
    return datetime.combine(moment.date(), time()) + timedelta(minutes=floored)


def load_interdependent_decision_config(
    path: Path | str = DEFAULT_CONFIG_PATH,
) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    validate_interdependent_decision_config(config)
    return config


def validate_interdependent_decision_config(config: Mapping[str, Any]) -> None:
    choice = config.get("choice_model", {})
    if choice.get("type") != "multinomial_logit":
        raise ValueError("interdependent choices require multinomial_logit")
    temperature = float(choice.get("utility_temperature", 0.0))
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("utility_temperature must be finite and positive")
    if not str(choice.get("stable_draw_namespace", "")):
        raise ValueError("stable_draw_namespace must be non-empty")
    precision = int(choice.get("probability_precision", -1))
    if precision < 3 or precision > 15:
        raise ValueError("probability_precision must be between 3 and 15")

    order = config.get("decision_order", {})
    if (
        order.get("primary") != "departure_time"
        or order.get("same_time_tie_breaker") != "stable_seeded_hash"
        or order.get("publish_after_each_decision") is not True
    ):
        raise ValueError("decision order must be chronological with immediate publication")

    state = config.get("shared_traffic_state", {})
    if tuple(state.get("key_fields", ())) != (
        "corridor_id", "direction", "time_bin",
    ):
        raise ValueError("shared traffic key must be corridor_id + direction + time_bin")
    if not str(state.get("corridor_id", "")) or not str(state.get("direction", "")):
        raise ValueError("corridor_id and direction must be non-empty")
    if int(state.get("time_bin_minutes", 0)) <= 0:
        raise ValueError("time_bin_minutes must be positive")
    if state.get("include_scheduled_bus_base_flow") is not True:
        raise ValueError("the v1 mechanism requires scheduled-bus base flow")
    if tuple(state.get("endogenous_update_modes", ())) != ("ride_hailing",):
        raise ValueError("only ride_hailing may create endogenous road vehicles")
    for field in (
        "ride_hailing_vehicle_pcu", "represented_trips_per_agent",
        "directional_divisor",
    ):
        value = float(state.get(field, 0.0))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field} must be finite and positive")
    if state.get("flow_unit") != "PCU/hour/direction":
        raise ValueError("shared traffic state must use PCU/hour/direction")

    audit = config.get("audit", {})
    if audit.get("compute_no_prior_agent_counterfactual") is not True:
        raise ValueError("the causal audit counterfactual must remain enabled")
    tolerance = float(audit.get("probability_change_tolerance", -1.0))
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("probability_change_tolerance must be finite and non-negative")


def softmax_choice_probabilities(
    scored_options: Sequence[Mapping[str, Any]], *, temperature: float = 1.0,
) -> Dict[str, float]:
    """Convert systematic utilities into multinomial-logit probabilities."""
    if not math.isfinite(float(temperature)) or float(temperature) <= 0:
        raise ValueError("temperature must be finite and positive")
    if not scored_options:
        return {}
    utilities = {
        str(row["mode"]): float(row["systematic_utility"])
        for row in scored_options
    }
    if len(utilities) != len(scored_options):
        raise ValueError("scored options must contain unique modes")
    if any(not math.isfinite(value) for value in utilities.values()):
        raise ValueError("systematic utilities must be finite")
    maximum = max(utilities.values()) / float(temperature)
    weights = {
        mode: math.exp(value / float(temperature) - maximum)
        for mode, value in utilities.items()
    }
    total = sum(weights.values())
    return {mode: weight / total for mode, weight in weights.items()}


def _draw_mode(probabilities: Mapping[str, float], draw: float) -> str:
    if not probabilities:
        return ""
    if not 0 <= draw < 1:
        raise ValueError("choice draw must be in [0, 1)")
    cumulative = 0.0
    available = [mode for mode in ENABLED_MODES if mode in probabilities]
    for mode in available:
        cumulative += float(probabilities[mode])
        if draw < cumulative:
            return mode
    return available[-1]


class SharedTrafficStateRegistry:
    """Versioned endogenous road flow keyed by corridor, direction and time bin."""

    def __init__(self, state_config: Mapping[str, Any]):
        self.config = copy.deepcopy(dict(state_config))
        self.bin_minutes = int(self.config["time_bin_minutes"])
        self._flow: Dict[tuple[str, str, datetime], float] = defaultdict(float)
        self._sources: Dict[tuple[str, str, datetime], list[Dict[str, Any]]] = defaultdict(list)
        self._state_versions: Dict[tuple[str, str, datetime], int] = defaultdict(int)
        self._global_version = 0
        self.events: list[Dict[str, Any]] = []

    def key(self, moment: datetime) -> tuple[str, str, datetime]:
        return (
            str(self.config["corridor_id"]),
            str(self.config["direction"]),
            _bin_start(moment, self.bin_minutes),
        )

    @staticmethod
    def key_string(key: tuple[str, str, datetime]) -> str:
        corridor, direction, bin_start = key
        return f"{corridor}|{direction}|{bin_start.isoformat(timespec='minutes')}"

    @property
    def flow_contribution_per_ride(self) -> float:
        pcu_per_bin = (
            float(self.config["ride_hailing_vehicle_pcu"])
            * float(self.config["represented_trips_per_agent"])
        )
        return (
            pcu_per_bin * 60.0 / self.bin_minutes
            / float(self.config["directional_divisor"])
        )

    def snapshot(self, moment: datetime, base_flow: float) -> Dict[str, Any]:
        if not math.isfinite(float(base_flow)) or float(base_flow) < 0:
            raise ValueError("base_flow must be finite and non-negative")
        key = self.key(moment)
        endogenous = self._flow[key]
        return {
            "key": key,
            "state_key": self.key_string(key),
            "time_bin_start": key[2],
            "base_flow_pcu_per_hour": float(base_flow),
            "endogenous_flow_pcu_per_hour": endogenous,
            "total_flow_pcu_per_hour": float(base_flow) + endogenous,
            "state_version": self._state_versions[key],
            "global_version": self._global_version,
            "sources": [dict(row) for row in self._sources[key]],
        }

    def publish_choice(
        self, *, agent_id: Any, leg_id: str, mode: str, departure_time: datetime,
        decision_sequence: int, base_flow: float,
    ) -> Dict[str, Any] | None:
        if mode not in self.config["endogenous_update_modes"]:
            return None
        before = self.snapshot(departure_time, base_flow)
        key = before["key"]
        contribution = self.flow_contribution_per_ride
        self._flow[key] += contribution
        self._global_version += 1
        self._state_versions[key] += 1
        source = {
            "decision_sequence": decision_sequence,
            "agent_id": agent_id,
            "leg_id": str(leg_id),
            "mode": mode,
            "flow_contribution_pcu_per_hour": contribution,
        }
        self._sources[key].append(source)
        after = self.snapshot(departure_time, base_flow)
        event = {
            "event_sequence": len(self.events) + 1,
            **source,
            "shared_state_key": before["state_key"],
            "departure_time": departure_time,
            "time_bin_start": before["time_bin_start"],
            "base_flow_pcu_per_hour": before["base_flow_pcu_per_hour"],
            "endogenous_flow_before": before["endogenous_flow_pcu_per_hour"],
            "endogenous_flow_after": after["endogenous_flow_pcu_per_hour"],
            "total_flow_before": before["total_flow_pcu_per_hour"],
            "total_flow_after": after["total_flow_pcu_per_hour"],
            "state_version_after": after["state_version"],
            "global_version_after": after["global_version"],
        }
        self.events.append(event)
        return dict(event)

    def state_rows(self) -> list[Dict[str, Any]]:
        rows = []
        for key in sorted(self._flow, key=lambda item: (item[2], item[0], item[1])):
            rows.append({
                "shared_state_key": self.key_string(key),
                "corridor_id": key[0],
                "direction": key[1],
                "time_bin_start": key[2],
                "endogenous_flow_pcu_per_hour": self._flow[key],
                "state_version": self._state_versions[key],
                "source_leg_ids": [row["leg_id"] for row in self._sources[key]],
            })
        return rows


def _scheduled_bus_base_flow(
    moment: datetime, network: Mapping[str, Any], formal_config: Mapping[str, Any],
    coupling_config: Mapping[str, Any],
) -> float:
    state = coupling_config["shared_traffic_state"]
    bus_trips = _scheduled_bus_trips_per_bin(moment, network, formal_config)
    return (
        bus_trips
        * float(formal_config["road_feedback"]["bus_vehicle_pcu"])
        * 60.0
        / int(state["time_bin_minutes"])
        / float(state["directional_divisor"])
    )


def _evaluate_probabilities(
    leg: Mapping[str, Any], agent: Mapping[str, Any], flow: float,
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
    formal_config: Mapping[str, Any], coupling_config: Mapping[str, Any], seed: int,
) -> Dict[str, Any]:
    options = {
        mode: _option(
            network, leg, mode, events, seed=seed,
            excess_flow_pcu_per_hour=flow, config=formal_config,
        )
        for mode in ENABLED_MODES
    }
    scored = _score_options(
        leg, agent, options, events, formal_config, seed,
        include_random_shock=False,
    )
    probabilities = softmax_choice_probabilities(
        scored,
        temperature=float(coupling_config["choice_model"]["utility_temperature"]),
    )
    return {"options": options, "scored_options": scored, "probabilities": probabilities}


def _round_mapping(values: Mapping[str, float], precision: int) -> Dict[str, float]:
    return {mode: round(float(values.get(mode, 0.0)), precision) for mode in ENABLED_MODES}


def _decision_order_key(seed: int, leg: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        leg["departure_time"],
        _stable_uniform(seed, leg["leg_id"], "interdependent-decision-order"),
        str(leg["leg_id"]),
    )


def _prepare_legs(
    agents: Mapping[Any, Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any]], legs: Sequence[Mapping[str, Any]],
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
    formal_config: Mapping[str, Any], seed: int,
) -> list[Dict[str, Any]]:
    """Reuse the formal model's first pass to obtain one bounded departure plan."""
    annotated = _annotate_schedule_constraints(legs, activities, formal_config)
    independent = _choose_all(
        annotated, agents, network, events, formal_config, seed, None,
    )
    return [row["leg"] for row in _reschedule_selected(independent, formal_config)]


def run_interdependent_decision_experiment(
    *, coupling_config: Mapping[str, Any] | None = None,
    formal_config: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    seed: int | None = None,
    weather_scenario: str | None = None,
    day_type: str | None = None,
    force_mode_by_leg_id: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Run one chronological mode-choice scenario with immediate shared-state updates."""
    coupling = copy.deepcopy(dict(
        coupling_config or load_interdependent_decision_config()
    ))
    validate_interdependent_decision_config(coupling)
    formal = copy.deepcopy(dict(formal_config or load_formal_nine_zone_config()))
    validate_formal_nine_zone_config(formal)
    state_config = coupling["shared_traffic_state"]
    if int(state_config["time_bin_minutes"]) != int(formal["road_feedback"]["time_bin_minutes"]):
        raise ValueError("coupling and formal road-feedback time bins must match")
    if str(state_config["corridor_id"]) != str(formal["road_feedback"]["representative_corridor_id"]):
        raise ValueError("coupling and formal corridor_id must match")
    if str(state_config["direction"]) != str(formal["road_feedback"]["representative_direction"]):
        raise ValueError("coupling and formal direction must match")

    weather_scenario = str(weather_scenario or coupling["weather_scenario"])
    day_type = str(day_type or coupling["day_type"])
    if weather_scenario not in formal["weather_scenarios"]:
        raise ValueError(f"unknown weather_scenario: {weather_scenario}")
    if day_type not in formal["selected_days"]:
        raise ValueError(f"unknown day_type: {day_type}")
    seed = int(formal["seed"] if seed is None else seed)
    inputs = dict(inputs or build_formal_nine_zone_inputs(config=formal, seed=seed))
    agents = {row["agent_id"]: row for row in inputs["agents"]}
    selected_date = date.fromisoformat(formal["selected_days"][day_type])
    activities = [
        row for row in inputs["activities"]
        if row["planned_start_datetime"].date() == selected_date
    ]
    legs = [
        row for row in inputs["legs"]
        if row["departure_time"].date() == selected_date
    ]
    network = build_transport_network()
    events = _events_for(formal, weather_scenario, day_type)
    planned_legs = _prepare_legs(
        agents, activities, legs, network, events, formal, seed,
    )
    ordered_legs = sorted(planned_legs, key=lambda row: _decision_order_key(seed, row))

    registry = SharedTrafficStateRegistry(state_config)
    forced = {str(key): str(value) for key, value in (force_mode_by_leg_id or {}).items()}
    precision = int(coupling["choice_model"]["probability_precision"])
    tolerance = float(coupling["audit"]["probability_change_tolerance"])
    namespace = str(coupling["choice_model"]["stable_draw_namespace"])
    decisions: list[Dict[str, Any]] = []
    influence_edges: list[Dict[str, Any]] = []

    for sequence, leg in enumerate(ordered_legs, start=1):
        agent = agents[leg["agent_id"]]
        departure = leg["departure_time"]
        base_flow = _scheduled_bus_base_flow(
            departure, network, formal, coupling,
        )
        before = registry.snapshot(departure, base_flow)
        coupled = _evaluate_probabilities(
            leg, agent, before["total_flow_pcu_per_hour"], network, events,
            formal, coupling, seed,
        )
        if before["sources"]:
            uncoupled = _evaluate_probabilities(
                leg, agent, base_flow, network, events, formal, coupling, seed,
            )
        else:
            uncoupled = coupled
        coupled_probabilities = coupled["probabilities"]
        uncoupled_probabilities = uncoupled["probabilities"]
        deltas = {
            mode: float(coupled_probabilities.get(mode, 0.0))
            - float(uncoupled_probabilities.get(mode, 0.0))
            for mode in ENABLED_MODES
        }
        maximum_delta = max((abs(value) for value in deltas.values()), default=0.0)
        affected = bool(before["sources"] and maximum_delta > tolerance)
        draw = _stable_uniform(seed, agent["agent_id"], leg["leg_id"], namespace)
        chosen_mode = _draw_mode(coupled_probabilities, draw)
        forced_mode = forced.get(str(leg["leg_id"]))
        if forced_mode is not None:
            if forced_mode not in coupled_probabilities:
                raise ValueError(
                    f"forced mode {forced_mode!r} is unavailable for leg {leg['leg_id']}"
                )
            chosen_mode = forced_mode
        event = registry.publish_choice(
            agent_id=agent["agent_id"], leg_id=str(leg["leg_id"]), mode=chosen_mode,
            departure_time=departure, decision_sequence=sequence, base_flow=base_flow,
        )
        after = registry.snapshot(departure, base_flow)
        utility_by_mode = {
            str(row["mode"]): float(row["systematic_utility"])
            for row in coupled["scored_options"]
        }
        travel_time_by_mode = {
            mode: (
                None if not coupled["options"][mode].get("available")
                else round(float(coupled["options"][mode]["final_total_time_min"]), 6)
            )
            for mode in ENABLED_MODES
        }
        source_leg_ids = [str(row["leg_id"]) for row in before["sources"]]
        decision = {
            "decision_sequence": sequence,
            "agent_id": agent["agent_id"],
            "leg_id": str(leg["leg_id"]),
            "activity_id": leg.get("activity_id"),
            "purpose": leg.get("purpose"),
            "origin_zone": leg["origin_zone"],
            "destination_zone": leg["destination_zone"],
            "departure_time": departure,
            "shared_state_key": before["state_key"],
            "state_version_before": before["state_version"],
            "state_version_after": after["state_version"],
            "base_road_flow_pcu_per_hour": round(base_flow, 6),
            "endogenous_flow_before": round(before["endogenous_flow_pcu_per_hour"], 6),
            "total_flow_before": round(before["total_flow_pcu_per_hour"], 6),
            "endogenous_flow_after": round(after["endogenous_flow_pcu_per_hour"], 6),
            "total_flow_after": round(after["total_flow_pcu_per_hour"], 6),
            "prior_influencer_count": len(before["sources"]),
            "prior_influencer_leg_ids": source_leg_ids,
            "mode_probabilities_without_prior_agents": _round_mapping(
                uncoupled_probabilities, precision,
            ),
            "mode_probabilities_with_prior_agents": _round_mapping(
                coupled_probabilities, precision,
            ),
            "probability_delta_from_prior_agents": _round_mapping(deltas, precision),
            "maximum_absolute_probability_delta": round(maximum_delta, precision),
            "affected_by_prior_agents": affected,
            "systematic_utility_by_mode": {
                mode: round(float(utility_by_mode[mode]), 6)
                for mode in ENABLED_MODES if mode in utility_by_mode
            },
            "travel_time_minutes_by_mode": travel_time_by_mode,
            "choice_draw": round(draw, precision),
            "chosen_mode": chosen_mode,
            "chosen_probability": round(
                float(coupled_probabilities.get(chosen_mode, 0.0)), precision,
            ),
            "forced_choice": forced_mode is not None,
            "published_traffic_event": event is not None,
        }
        decisions.append(decision)
        if affected and coupling["audit"].get("record_influence_edges", True):
            rounded_delta = _round_mapping(deltas, precision)
            for source in before["sources"]:
                influence_edges.append({
                    "source_decision_sequence": source["decision_sequence"],
                    "source_agent_id": source["agent_id"],
                    "source_leg_id": source["leg_id"],
                    "target_decision_sequence": sequence,
                    "target_agent_id": agent["agent_id"],
                    "target_leg_id": str(leg["leg_id"]),
                    "shared_state_key": before["state_key"],
                    "mechanism": "ride_hailing_choice_to_shared_road_flow_to_mode_probability",
                    "source_flow_contribution_pcu_per_hour": source[
                        "flow_contribution_pcu_per_hour"
                    ],
                    "target_aggregate_probability_delta": rounded_delta,
                })

    mode_counts = Counter(row["chosen_mode"] for row in decisions)
    changed = [row for row in decisions if row["affected_by_prior_agents"]]
    summary = {
        "experiment_id": coupling["experiment_id"],
        "seed": seed,
        "weather_scenario": weather_scenario,
        "day_type": day_type,
        "decision_count": len(decisions),
        "ride_hailing_choice_count": mode_counts["ride_hailing"],
        "affected_decision_count": len(changed),
        "affected_decision_share": round(len(changed) / len(decisions), 6) if decisions else 0.0,
        "maximum_absolute_probability_change": max(
            (row["maximum_absolute_probability_delta"] for row in decisions),
            default=0.0,
        ),
        "mode_choice_counts": {mode: mode_counts[mode] for mode in ENABLED_MODES},
        "traffic_event_count": len(registry.events),
        "influence_edge_count": len(influence_edges),
        "represented_trips_per_agent": float(state_config["represented_trips_per_agent"]),
        "flow_contribution_per_ride_pcu_per_hour": registry.flow_contribution_per_ride,
    }
    return {
        "coupling_config": coupling,
        "formal_config": formal,
        "inputs": inputs,
        "decisions": decisions,
        "traffic_state_events": registry.events,
        "traffic_state_rows": registry.state_rows(),
        "influence_edges": influence_edges,
        "summary": summary,
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "SharedTrafficStateRegistry",
    "load_interdependent_decision_config",
    "run_interdependent_decision_experiment",
    "softmax_choice_probabilities",
    "validate_interdependent_decision_config",
]
