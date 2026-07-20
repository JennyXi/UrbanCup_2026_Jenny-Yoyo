"""Run one API-backed 200-Agent urban-mobility scenario with shared traffic state.

The public-goods result is an input to coupon allocation only.  Travel choices
remain urban-mobility decisions over walk, bus, metro and ride hailing.  Every
travel leg is decided through the configured AgentSociety LLM dispatcher, then
a ride-hailing choice immediately updates the shared road state seen by later
Agents in the same 30-minute bin.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    _deep_merge,
    load_formal_50_config,
)
from custom.agents.formal_nine_zone_experiment import (  # noqa: E402
    ENABLED_MODES,
    _activity_results,
    _events_for,
    _option,
    _scenario_summary,
    _score_options,
    _simulate_final_choices,
    build_formal_nine_zone_inputs,
    load_formal_nine_zone_config,
    validate_formal_nine_zone_config,
)
from custom.agents.interdependent_decision_system import (  # noqa: E402
    SharedTrafficStateRegistry,
    _bin_start,
    _decision_order_key,
    _prepare_legs,
    _scheduled_bus_base_flow,
    load_interdependent_decision_config,
    softmax_choice_probabilities,
    validate_interdependent_decision_config,
)
from custom.transport.network import build_transport_network  # noqa: E402


DEFAULT_FORMAL_EXPERIMENT = ROOT / "config" / "formal_nine_zone_200_baseline.json"
DEFAULT_COUPLING_CONFIG = ROOT / "config" / "interdependent_agent_decisions.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "city_mobility_200_api_w2_seed47"


def _serial(value: Any) -> Any:
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
            {key: _serial(value) for key, value in row.items()}
            for row in materialized
        )


def _load_coupon_allocations(path: Path | None) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    if path is None:
        return {}, {"source": None, "api_backed": False}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = [dict(row) for row in payload["allocations"]]
    by_id = {int(row["agent_id"]): row for row in rows}
    source_summary = dict(payload.get("summary", {}))
    return by_id, {
        "source": str(path.resolve()),
        "api_backed": bool(source_summary.get("api_contribution_decisions")),
        "api_contribution_decisions": int(
            source_summary.get("api_contribution_decisions", 0)
        ),
        "awarded": sum(bool(row.get("coupon_awarded")) for row in rows),
    }


def _build_formal_config(
    experiment_path: Path,
    coupon_allocations: Mapping[int, Mapping[str, Any]],
    discount_multiplier: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    experiment = load_formal_50_config(experiment_path)
    formal_path = ROOT / experiment["formal_transport_config"]
    formal = load_formal_nine_zone_config(formal_path)
    formal = _deep_merge(formal, experiment.get("formal_overrides", {}))
    formal["total_agents"] = int(experiment["total_agents"])
    formal["_coupon_allocations"] = {
        int(agent_id): dict(allocation)
        for agent_id, allocation in coupon_allocations.items()
    }
    formal["_coupon_discount_multiplier"] = float(discount_multiplier)
    validate_formal_nine_zone_config(formal)
    return experiment, formal


def _evaluate(
    *,
    leg: Mapping[str, Any],
    agent: Mapping[str, Any],
    flow: float,
    network: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    formal: Mapping[str, Any],
    coupling: Mapping[str, Any],
    seed: int,
    coupon_available: bool,
    coupon_proxy_access: bool,
) -> dict[str, Any]:
    options = {
        mode: _option(
            network,
            leg,
            mode,
            events,
            seed=seed,
            excess_flow_pcu_per_hour=flow,
            config=formal,
        )
        for mode in ENABLED_MODES
    }
    scored = _score_options(
        leg,
        agent,
        options,
        events,
        formal,
        seed,
        coupon_available=coupon_available,
        coupon_proxy_access=coupon_proxy_access,
        include_random_shock=False,
    )
    probabilities = softmax_choice_probabilities(
        scored,
        temperature=float(coupling["choice_model"]["utility_temperature"]),
    )
    return {"options": options, "scored_options": scored, "probabilities": probabilities}


def _round_mapping(values: Mapping[str, float], precision: int) -> dict[str, float]:
    return {
        mode: round(float(values.get(mode, 0.0)), precision)
        for mode in ENABLED_MODES
    }


def _prompt_payload(
    *,
    agent: Mapping[str, Any],
    leg: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    weather_scenario: str,
    road_state: Mapping[str, Any],
    coupon_available: bool,
) -> dict[str, Any]:
    probability = evaluation["probabilities"]
    options = []
    for row in evaluation["scored_options"]:
        mode = str(row["mode"])
        options.append({
            "mode": mode,
            "travel_time_min": round(float(row["final_total_time_min"]), 2),
            "wait_time_min": round(float(row.get("period_wait_time_min") or 0.0), 2),
            "fare_yuan": round(float(row["fare"]), 2),
            "coupon_applied": bool(row.get("coupon_applied_to_choice")),
            "expected_outdoor_minutes": round(
                float(row.get("expected_outdoor_exposure_minutes_at_choice") or 0.0), 2
            ),
            "model_probability": round(float(probability[mode]), 6),
        })
    return {
        "task": "choose_one_urban_travel_mode",
        "resident": {
            "agent_id": int(agent["agent_id"]),
            "age_group": agent["age_group"],
            "work_status": agent["work_status"],
            "medical_need_level": agent["medical_need_level"],
            "digital_access": bool(agent["digital_access"]),
            "family_assistance": bool(agent.get("family_assistance")),
        },
        "trip": {
            "purpose": leg.get("purpose"),
            "origin_zone": leg["origin_zone"],
            "destination_zone": leg["destination_zone"],
            "departure_time": leg["departure_time"].isoformat(sep=" "),
            "weather_scenario": weather_scenario,
        },
        "shared_road_state": {
            "time_bin": road_state["time_bin_start"].isoformat(sep=" "),
            "prior_ride_hailing_choices": len(road_state["sources"]),
            "base_flow_pcu_per_hour": round(
                float(road_state["base_flow_pcu_per_hour"]), 3
            ),
            "endogenous_flow_pcu_per_hour": round(
                float(road_state["endogenous_flow_pcu_per_hour"]), 3
            ),
        },
        "coupon_available": coupon_available,
        "available_options": options,
        "response_schema": {
            "mode": "walk|bus|metro|ride_hailing",
            "reason": "one short sentence",
        },
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    candidates = [stripped]
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match and match.group(0) != stripped:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


async def _llm_choice(
    client: Any,
    payload: Mapping[str, Any],
    probabilities: Mapping[str, float],
) -> dict[str, Any]:
    available = set(probabilities)
    fallback = max(probabilities, key=probabilities.get) if probabilities else ""
    try:
        response = await client.call(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是城市居民出行决策Agent。根据个人属性、天气、优惠券、"
                        "实时道路状态和候选方式作出一次实际选择。只能选择提供的方式，"
                        "只返回符合response_schema的JSON，不要Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            temperature=0.2,
            max_tokens=120,
            max_retries=2,
        )
        content = response.choices[0].message.content or ""
        parsed = _extract_json_object(content)
        mode = "" if parsed is None else str(parsed.get("mode", "")).strip().lower()
        if mode not in available:
            return {
                "mode": fallback,
                "reason": "API返回无法解析或选择了不可用方式，使用最高模型概率回退。",
                "api_succeeded": False,
                "raw_response": content[:500],
            }
        return {
            "mode": mode,
            "reason": str(parsed.get("reason", ""))[:500],
            "api_succeeded": True,
            "raw_response": content[:500],
        }
    except Exception as exc:  # Keep a complete city run even if one call fails.
        return {
            "mode": fallback,
            "reason": f"API调用失败，使用最高模型概率回退：{type(exc).__name__}",
            "api_succeeded": False,
            "raw_response": "",
        }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    coupon_allocations, coupon_source = _load_coupon_allocations(args.coupon_result)
    experiment, formal = _build_formal_config(
        args.formal_experiment_config,
        coupon_allocations,
        args.discount_multiplier,
    )
    coupling = load_interdependent_decision_config(args.coupling_config)
    coupling = json.loads(json.dumps(coupling))
    # Preserve the 50-Agent mechanism's represented demand when scaling to 200.
    coupling["shared_traffic_state"]["represented_trips_per_agent"] = float(
        args.represented_trips_per_agent
    )
    validate_interdependent_decision_config(coupling)
    seed = int(args.seed)
    weather_scenario = str(args.weather_scenario)
    day_type = str(args.day_type)
    inputs = build_formal_nine_zone_inputs(config=formal, seed=seed)
    agents = {int(row["agent_id"]): row for row in inputs["agents"]}
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
        agents,
        activities,
        legs,
        network,
        events,
        formal,
        seed,
    )
    ordered_legs = sorted(planned_legs, key=lambda row: _decision_order_key(seed, row))
    if args.max_decisions is not None:
        ordered_legs = ordered_legs[: int(args.max_decisions)]

    client = None
    if not args.dry_run:
        from agentsociety2.config import build_client_for_role

        client = build_client_for_role("default")

    precision = int(coupling["choice_model"]["probability_precision"])
    tolerance = float(coupling["audit"]["probability_change_tolerance"])
    coupon_bound_agents: set[int] = set()
    decisions: list[dict[str, Any]] = []
    influence_edges: list[dict[str, Any]] = []
    simulation_choices: list[dict[str, Any]] = []
    bin_minutes = int(coupling["shared_traffic_state"]["time_bin_minutes"])
    sequence_by_leg_id = {
        str(leg["leg_id"]): sequence
        for sequence, leg in enumerate(ordered_legs, start=1)
    }
    first_leg_id_by_agent: dict[int, str] = {}
    legs_by_bin: dict[datetime, list[Mapping[str, Any]]] = {}
    for leg in ordered_legs:
        agent_id = int(leg["agent_id"])
        first_leg_id_by_agent.setdefault(agent_id, str(leg["leg_id"]))
        legs_by_bin.setdefault(
            _bin_start(leg["departure_time"], bin_minutes), []
        ).append(leg)
    registries = {
        bin_start: SharedTrafficStateRegistry(coupling["shared_traffic_state"])
        for bin_start in legs_by_bin
    }
    api_semaphore = asyncio.Semaphore(int(args.concurrency))
    progress_lock = asyncio.Lock()
    completed_count = 0
    completed_ride_count = 0
    completed_affected_count = 0
    completed_failure_count = 0

    async def process_time_bin(
        bin_start: datetime,
        bin_legs: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        nonlocal completed_count, completed_ride_count
        nonlocal completed_affected_count, completed_failure_count
        registry = registries[bin_start]
        local_decisions: list[dict[str, Any]] = []
        local_edges: list[dict[str, Any]] = []
        local_simulation: list[dict[str, Any]] = []
        ordered_bin_legs = sorted(
            bin_legs,
            key=lambda row: sequence_by_leg_id[str(row["leg_id"])],
        )
        for leg in ordered_bin_legs:
            sequence = sequence_by_leg_id[str(leg["leg_id"])]
            agent_id = int(leg["agent_id"])
            agent = agents[agent_id]
            allocation = coupon_allocations.get(agent_id, {})
            # Independent time bins can run concurrently because a coupon is
            # presented only at the resident's first trip decision of the day.
            coupon_available = bool(
                allocation.get("coupon_awarded")
                and str(leg["leg_id"]) == first_leg_id_by_agent[agent_id]
            )
            coupon_proxy_access = bool(
                coupon_available
                and allocation.get("coupon_access_channel")
                in {"community_phone", "family_proxy"}
            )
            base_flow = _scheduled_bus_base_flow(
                leg["departure_time"], network, formal, coupling
            )
            before = registry.snapshot(leg["departure_time"], base_flow)
            coupled = _evaluate(
                leg=leg,
                agent=agent,
                flow=float(before["total_flow_pcu_per_hour"]),
                network=network,
                events=events,
                formal=formal,
                coupling=coupling,
                seed=seed,
                coupon_available=coupon_available,
                coupon_proxy_access=coupon_proxy_access,
            )
            uncoupled = (
                _evaluate(
                    leg=leg,
                    agent=agent,
                    flow=base_flow,
                    network=network,
                    events=events,
                    formal=formal,
                    coupling=coupling,
                    seed=seed,
                    coupon_available=coupon_available,
                    coupon_proxy_access=coupon_proxy_access,
                )
                if before["sources"] else coupled
            )
            probabilities = coupled["probabilities"]
            deltas = {
                mode: float(probabilities.get(mode, 0.0))
                - float(uncoupled["probabilities"].get(mode, 0.0))
                for mode in ENABLED_MODES
            }
            max_delta = max((abs(value) for value in deltas.values()), default=0.0)
            affected = bool(before["sources"] and max_delta > tolerance)
            prompt = _prompt_payload(
                agent=agent,
                leg=leg,
                evaluation=coupled,
                weather_scenario=weather_scenario,
                road_state=before,
                coupon_available=coupon_available,
            )
            if args.dry_run:
                chosen_mode = max(probabilities, key=probabilities.get) if probabilities else ""
                api_result = {
                    "mode": chosen_mode,
                    "reason": "dry-run：使用最高模型概率。",
                    "api_succeeded": True,
                    "raw_response": "",
                }
            else:
                async with api_semaphore:
                    api_result = await _llm_choice(client, prompt, probabilities)
                chosen_mode = api_result["mode"]

            chosen_option = next(
                (row for row in coupled["scored_options"] if row["mode"] == chosen_mode),
                None,
            )
            coupon_bound = bool(
                chosen_option is not None
                and chosen_mode == "ride_hailing"
                and chosen_option.get("coupon_applied_to_choice")
            )
            if coupon_bound:
                coupon_bound_agents.add(agent_id)
            event = registry.publish_choice(
                agent_id=agent_id,
                leg_id=str(leg["leg_id"]),
                mode=chosen_mode,
                departure_time=leg["departure_time"],
                decision_sequence=sequence,
                base_flow=base_flow,
            )
            after = registry.snapshot(leg["departure_time"], base_flow)
            source_leg_ids = [str(row["leg_id"]) for row in before["sources"]]
            local_decisions.append({
                "decision_sequence": sequence,
                "agent_id": agent_id,
                "age_group": agent["age_group"],
                "digital_access": bool(agent["digital_access"]),
                "leg_id": str(leg["leg_id"]),
                "purpose": leg.get("purpose"),
                "origin_zone": leg["origin_zone"],
                "destination_zone": leg["destination_zone"],
                "departure_time": leg["departure_time"],
                "weather_scenario": weather_scenario,
                "shared_state_key": before["state_key"],
                "state_version_before": before["state_version"],
                "state_version_after": after["state_version"],
                "base_road_flow_pcu_per_hour": round(base_flow, 6),
                "endogenous_flow_before": round(
                    float(before["endogenous_flow_pcu_per_hour"]), 6
                ),
                "endogenous_flow_after": round(
                    float(after["endogenous_flow_pcu_per_hour"]), 6
                ),
                "prior_influencer_count": len(before["sources"]),
                "prior_influencer_leg_ids": source_leg_ids,
                "mode_probabilities_without_prior_agents": _round_mapping(
                    uncoupled["probabilities"], precision
                ),
                                "mode_probabilities_with_prior_agents": _round_mapping(
                    probabilities, precision
                ),
                "available_options": prompt["available_options"],
                "probability_delta_from_prior_agents": _round_mapping(deltas, precision),
                "maximum_absolute_probability_delta": round(max_delta, precision),
                "affected_by_prior_agents": affected,
                "coupon_awarded": bool(allocation.get("coupon_awarded")),
                "coupon_available_at_choice": coupon_available,
                "coupon_binding_rule": "first_trip_decision_only",
                "coupon_bound_to_ride_hailing": coupon_bound,
                "chosen_mode": chosen_mode,
                "chosen_probability": round(
                    float(probabilities.get(chosen_mode, 0.0)), precision
                ),
                "llm_reason": api_result["reason"],
                "raw_response": api_result["raw_response"],
                "api_decision_succeeded": bool(api_result["api_succeeded"]),
                "api_call_attempted": not args.dry_run,
                "published_traffic_event": event is not None,
            })
            local_simulation.append({
                "_decision_sequence": sequence,
                "leg": leg,
                "chosen_mode": chosen_mode,
                "chosen_option": chosen_option,
                "options": coupled["options"],
                "scored_options": coupled["scored_options"],
                "coupon_bound_to_primary": coupon_bound,
            })
            if affected and coupling["audit"].get("record_influence_edges", True):
                rounded_delta = _round_mapping(deltas, precision)
                for source in before["sources"]:
                    local_edges.append({
                        "source_decision_sequence": source["decision_sequence"],
                        "source_agent_id": source["agent_id"],
                        "source_leg_id": source["leg_id"],
                        "target_decision_sequence": sequence,
                        "target_agent_id": agent_id,
                        "target_leg_id": str(leg["leg_id"]),
                        "shared_state_key": before["state_key"],
                        "mechanism": (
                            "ride_hailing_choice_to_shared_road_flow_to_mode_probability"
                        ),
                        "source_flow_contribution_pcu_per_hour": source[
                            "flow_contribution_pcu_per_hour"
                        ],
                        "target_probability_delta": rounded_delta,
                    })
            async with progress_lock:
                completed_count += 1
                completed_ride_count += chosen_mode == "ride_hailing"
                completed_affected_count += affected
                completed_failure_count += not bool(api_result["api_succeeded"])
                if (
                    completed_count % int(args.progress_every) == 0
                    or completed_count == len(ordered_legs)
                ):
                    print(
                        json.dumps({
                            "progress": f"{completed_count}/{len(ordered_legs)}",
                            "ride_hailing": completed_ride_count,
                            "affected": completed_affected_count,
                            "api_failures": completed_failure_count,
                        }, ensure_ascii=False),
                        flush=True,
                    )
        return local_decisions, local_edges, local_simulation

    bin_results = await asyncio.gather(*(
        process_time_bin(bin_start, bin_legs)
        for bin_start, bin_legs in sorted(legs_by_bin.items())
    ))
    for local_decisions, local_edges, local_simulation in bin_results:
        decisions.extend(local_decisions)
        influence_edges.extend(local_edges)
        simulation_choices.extend(local_simulation)
    decisions.sort(key=lambda row: int(row["decision_sequence"]))
    influence_edges.sort(key=lambda row: (
        int(row["target_decision_sequence"]), int(row["source_decision_sequence"])
    ))
    simulation_choices.sort(key=lambda row: int(row["_decision_sequence"]))

    traffic_events = sorted(
        [event for registry in registries.values() for event in registry.events],
        key=lambda row: int(row["decision_sequence"]),
    )
    for event_sequence, event in enumerate(traffic_events, start=1):
        event["event_sequence"] = event_sequence
        event["global_version_after"] = event_sequence
    traffic_state_rows = sorted(
        [row for registry in registries.values() for row in registry.state_rows()],
        key=lambda row: row["time_bin_start"],
    )
    unique_bins: dict[datetime, float] = {}
    for bin_start, bin_legs in legs_by_bin.items():
        leg = bin_legs[0]
        base_flow = _scheduled_bus_base_flow(
            leg["departure_time"], network, formal, coupling
        )
        unique_bins[bin_start] = float(
            registries[bin_start].snapshot(
                leg["departure_time"], base_flow
            )["total_flow_pcu_per_hour"]
        )
    mode_choices, dispatch, vehicle_states = _simulate_final_choices(
        simulation_choices,
        agents,
        network,
        events,
        formal,
        unique_bins,
        day_type,
        seed,
    )
    for row in mode_choices:
        row.update({
            "weather_scenario": weather_scenario,
            "day_type": day_type,
            "policy": "C4_public_goods",
            "experiment_condition": "API_interdependent_city_mobility",
        })
    for row in dispatch:
        row.update({
            "weather_scenario": weather_scenario,
            "day_type": day_type,
            "policy": "C4_public_goods",
            "experiment_condition": "API_interdependent_city_mobility",
        })
    for row in vehicle_states:
        row.update({
            "weather_scenario": weather_scenario,
            "policy": "C4_public_goods",
            "experiment_condition": "API_interdependent_city_mobility",
        })
    activity_results = _activity_results(
        activities,
        mode_choices,
        formal,
        weather_scenario,
        day_type,
    )
    formal_summary = _scenario_summary(
        mode_choices,
        activities,
        dispatch,
        vehicle_states,
        network,
        formal,
        weather_scenario,
        day_type,
        unique_bins,
        seed,
    )

    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    model = "dry-run"
    api_base = ""
    if client is not None:
        token_stats = client.take_token_stats()
        usage = {
            "calls": sum(int(item["calls"]) for item in token_stats.values()),
            "input_tokens": sum(int(item["input"]) for item in token_stats.values()),
            "output_tokens": sum(int(item["output"]) for item in token_stats.values()),
        }
        model = client.model_name
        api_base = client.base_url
    api_failures = sum(not row["api_decision_succeeded"] for row in decisions)
    chosen_counts = Counter(row["chosen_mode"] for row in decisions)
    final_counts = Counter(
        row["final_mode"] for row in mode_choices if row["transport_succeeded"]
    )
    redeemed = sum(bool(row.get("coupon_redeemed")) for row in mode_choices)
    summary = {
        "status": (
            "DRY_RUN" if args.dry_run else
            "PASS" if api_failures == 0 else
            "PASS_WITH_API_FALLBACKS"
        ),
        "experiment": "GitHub nine-zone API-backed interdependent urban mobility",
        "seed": seed,
        "weather_scenario": weather_scenario,
        "day_type": day_type,
        "agents": len(agents),
        "agents_with_travel": len({int(row["agent_id"]) for row in ordered_legs}),
        "agents_without_workday_travel": len(agents) - len({int(row["agent_id"]) for row in ordered_legs}),
        "travel_decisions": len(decisions),
        "api_travel_decisions": sum(row["api_call_attempted"] for row in decisions),
        "api_decision_failures": api_failures,
        "model": model,
        "api_base": api_base,
        "api_key_present": bool(os.getenv("AGENTSOCIETY_LLM_API_KEY")),
        "api_key_persisted": False,
        "usage": usage,
        "chosen_mode_counts": {mode: chosen_counts[mode] for mode in ENABLED_MODES},
        "final_successful_mode_counts": {mode: final_counts[mode] for mode in ENABLED_MODES},
        "ride_hailing_traffic_events": len(traffic_events),
        "affected_decisions": sum(row["affected_by_prior_agents"] for row in decisions),
        "maximum_absolute_probability_change": max(
            (float(row["maximum_absolute_probability_delta"]) for row in decisions),
            default=0.0,
        ),
        "influence_edges": len(influence_edges),
        "represented_trips_per_agent": float(
            coupling["shared_traffic_state"]["represented_trips_per_agent"]
        ),
        "coupon_source": coupon_source,
        "coupon_awarded": sum(
            bool(row.get("coupon_awarded")) for row in coupon_allocations.values()
        ),
        "coupon_binding_rule": "awarded coupon is offered on the first trip decision only",
        "coupon_bound_to_ride_hailing": len(coupon_bound_agents),
        "coupon_redeemed": redeemed,
        "ride_hailing_requests": len(dispatch),
        "successful_ride_hailing_requests": sum(bool(row["succeeded"]) for row in dispatch),
        "failed_ride_hailing_requests": sum(not bool(row["succeeded"]) for row in dispatch),
        "transport_success_rate": formal_summary["transport_success_rate"],
        "necessary_activity_completion_rate": formal_summary[
            "necessary_activity_completion_rate"
        ],
        "mean_total_travel_time": formal_summary["mean_total_travel_time"],
        "initial_ride_hailing_vehicles": formal_summary["initial_ride_hailing_vehicles"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "decision_audit.csv", decisions)
    _write_csv(output / "influence_edges.csv", influence_edges)
    _write_csv(output / "traffic_state_events.csv", traffic_events)
    _write_csv(output / "traffic_state_final.csv", traffic_state_rows)
    _write_csv(output / "mode_choices.csv", mode_choices)
    _write_csv(output / "ride_hailing_dispatch.csv", dispatch)
    _write_csv(output / "vehicle_end_states.csv", vehicle_states)
    _write_csv(output / "activity_results.csv", activity_results)
    _write_csv(output / "coupon_allocations.csv", coupon_allocations.values())
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formal-experiment-config",
        type=Path,
        default=DEFAULT_FORMAL_EXPERIMENT,
    )
    parser.add_argument(
        "--coupling-config",
        type=Path,
        default=DEFAULT_COUPLING_CONFIG,
    )
    parser.add_argument("--coupon-result", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--weather-scenario", choices=("W0", "W1", "W2"), default="W2")
    parser.add_argument("--day-type", choices=("workday", "rest_day"), default="workday")
    parser.add_argument("--discount-multiplier", type=float, default=0.8)
    parser.add_argument("--represented-trips-per-agent", type=float, default=30.0)
    parser.add_argument("--max-decisions", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_decisions is not None and args.max_decisions <= 0:
        raise ValueError("--max-decisions must be positive")
    if args.progress_every <= 0:
        raise ValueError("--progress-every must be positive")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be positive")
    summary = asyncio.run(run(args))
    # Keep CLI output portable when Windows inherits a non-UTF-8 code page.
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
