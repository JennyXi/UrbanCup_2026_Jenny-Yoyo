"""Run a configurable PersonAgent Urban Cup pilot using implemented GitHub layers.

The experiment is deliberately a small integration pilot: one representative
planned activity/leg and one LLM mode decision per person.  It does not invent
the dispatch, fleet, endogenous-flow, or causal-estimation layers that the
source Urban Cup repository explicitly marks as unfinished.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agentsociety2
import ray
import ray._private.services as ray_private_services
import ray._private.utils as ray_private_utils
from agentsociety2.contrib.env.simple_social_space import SimpleSocialSpace
from agentsociety2.society import AgentSociety
from agentsociety2.society.models import QuestionItem
from agentsociety2.society.questionnaire import Questionnaire
from urban_router import UrbanReadOnlyRouter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
URBAN_ROOT = Path(
    os.getenv(
        "URBAN_CUP_REFERENCE_ROOT",
        str(WORKSPACE_ROOT / "UrbanCup_2026_Jenny-Yoyo-reference"),
    )
).resolve()
if not URBAN_ROOT.is_dir():
    raise FileNotFoundError(f"Urban Cup source checkout not found: {URBAN_ROOT}")
sys.path.insert(0, str(URBAN_ROOT))

from custom.agents.agent_population import generate_population_agents  # noqa: E402
from custom.agents.leg_generation import build_time_feasible_legs  # noqa: E402
from custom.agents.trip_planning import generate_seven_day_activity_plans  # noqa: E402
from custom.envs import weather as t2_weather  # noqa: E402
from custom.policies.t3_policy import evaluate_t3_policy  # noqa: E402
from custom.spatial.destination_assignment import (  # noqa: E402
    assign_destination_zones_with_audit,
    load_destination_configuration,
)
from custom.spatial.home_zone_assignment import assign_home_zones  # noqa: E402
from custom.spatial.zone_configuration import (  # noqa: E402
    allocate_zone_age_quotas,
    derive_spatial_configuration,
    load_zone_configuration,
)
from custom.transport.dynamic_congestion import (  # noqa: E402
    calculate_dynamic_congestion_leg_mode_option,
)
from custom.transport.network import MODES, build_transport_network  # noqa: E402
from custom.transport.weather_supply import weather_events_from_t2_config  # noqa: E402


SEED = 47
WEEK_START = datetime(2026, 7, 6)
DISCOUNT_LOW = 10.0
DISCOUNT_HIGH = 20.0
W2_WINDOWS = (
    ("Tuesday", "07:00", "12:00"),
    ("Thursday", "15:00", "21:00"),
    ("Saturday", "09:00", "18:00"),
)
ARMS = (
    {"arm": "W0_P0", "weather": "W0", "policy": "P0", "discount": None},
    {"arm": "W1_P0", "weather": "W1", "policy": "P0", "discount": None},
    {"arm": "W2_P0", "weather": "W2", "policy": "P0", "discount": None},
    {"arm": "W2_P1_low", "weather": "W2", "policy": "P1", "discount": "low"},
    {"arm": "W2_P1_high", "weather": "W2", "policy": "P1", "discount": "high"},
    {"arm": "W2_P2_low", "weather": "W2", "policy": "P2", "discount": "low"},
    {"arm": "W2_P2_high", "weather": "W2", "policy": "P2", "discount": "high"},
    {"arm": "W2_P3_low", "weather": "W2", "policy": "P3", "discount": "low"},
    {"arm": "W2_P3_high", "weather": "W2", "policy": "P3", "discount": "high"},
    {"arm": "W2_P4", "weather": "W2", "policy": "P4", "discount": None},
)
TEXT_RESULT_SUFFIXES = {
    ".csv",
    ".err",
    ".json",
    ".jsonl",
    ".log",
    ".out",
    ".txt",
    ".yaml",
    ".yml",
}
CREDENTIAL_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")


def force_minimal_ray_dashboard_agent() -> None:
    ray_private_utils.get_dashboard_dependency_error = lambda: ImportError(
        "Dashboard intentionally disabled for the D-drive pilot"
    )
    # Ray 2.56 still starts a minimal dashboard head even when
    # include_dashboard=False.  Its only requested module is usage telemetry,
    # which is not part of this experiment and can exceed the fixed startup
    # deadline when imported from /mnt/d.  Returning no API-server process is
    # accepted by ray._private.node.Node.start_api_server.
    ray_private_services.start_api_server = lambda *args, **kwargs: ("", None)


def redact_credentials(text: str) -> str:
    return CREDENTIAL_PATTERN.sub("[REDACTED]", text)


def sanitize_text_results(root: Path) -> list[str]:
    sanitized: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_RESULT_SUFFIXES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        redacted = redact_credentials(original)
        if redacted != original:
            path.write_text(redacted, encoding="utf-8")
            sanitized.append(str(path.relative_to(root)))
    return sanitized


def persist_ray_diagnostics(run_dir: Path) -> str | None:
    """Copy Ray startup logs before WSL can tear down its temporary filesystem."""
    source = Path(tempfile.gettempdir()) / "ray" / "session_latest" / "logs"
    if not source.is_dir():
        return None
    destination = run_dir / "ray_startup_logs"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    sanitize_text_results(destination)
    return str(destination)


def as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(vars(value))


def pilot_profile_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Fill required T2/T3 behavioral fields with documented pilot assumptions."""
    result = dict(row)
    medical = result.get("medical_need_level")
    result["mobility_constraint"] = (
        "high" if medical == "high" else "mild" if result["is_elder"] else "none"
    )
    work_status = result.get("work_status")
    result["schedule_flexibility"] = (
        "low"
        if work_status == "regular_worker"
        else "medium"
        if work_status in {"part_time_worker", "flexible_non_worker"}
        else "high"
    )
    # Base values from the repository's evidence-to-parameter mapping.
    result["coupon_awareness_probability"] = 0.50
    result["coupon_claim_probability"] = 0.40
    if result.get("independent_ride_hailing") is None:
        result["independent_ride_hailing"] = bool(result.get("digital_access"))
    return result


def build_urban_population(
    total_agents: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    agents, spatial, spatial_by_id = build_assigned_population(total_agents)
    return materialize_urban_population(agents, spatial, spatial_by_id)


def build_assigned_population(
    total_agents: int,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """Create the full seeded population and home-zone assignment only.

    The seven-day plans and legs are intentionally deferred.  At 100,000
    agents those high-cardinality records must be generated one partition at a
    time, while the lightweight full population/home assignment is retained so
    the existing Seed-47 quotas and agent IDs remain exactly compatible.
    """
    spatial = derive_spatial_configuration(load_zone_configuration())
    spatial_by_id = {zone["zone_id"]: zone for zone in spatial["zones"]}
    quotas = allocate_zone_age_quotas(spatial, total_agents=total_agents)
    population = generate_population_agents(total_agents=total_agents, seed=SEED)
    agents = assign_home_zones(population, quotas["quota_matrix"], seed=SEED)
    return list(agents), spatial, spatial_by_id


def materialize_urban_population(
    agents: list[Any],
    spatial: dict[str, Any],
    spatial_by_id: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Materialize plans, destinations, and legs for one bounded agent slice."""
    baseline = generate_seven_day_activity_plans(agents, WEEK_START, SEED)
    destination = assign_destination_zones_with_audit(
        agents,
        baseline,
        spatial,
        load_destination_configuration(),
        SEED,
    )
    timed = build_time_feasible_legs(
        agents, destination["activities"], spatial_by_id
    )
    rows = []
    for agent in agents:
        row = pilot_profile_fields(as_dict(agent))
        row["home_zone_name"] = spatial_by_id[row["home_zone"]]["display_name"]
        rows.append(row)
    return rows, [dict(item) for item in timed["legs"]], spatial_by_id


def configure_weather(week: str) -> list[dict[str, Any]]:
    t2_weather.set_scenario_level("base")
    t2_weather.init_rng(SEED)
    t2_weather.set_w2_windows(W2_WINDOWS)
    t2_weather.set_week(week)
    return weather_events_from_t2_config(WEEK_START)


def choose_representative_leg(
    agent_id: int,
    legs_by_agent: dict[int, list[dict[str, Any]]],
    weather_week: str,
) -> tuple[dict[str, Any], bool]:
    candidates = [
        leg
        for leg in legs_by_agent[agent_id]
        if leg.get("leg_role") in {"outbound", "between_activities"}
    ]
    if not candidates:
        raise ValueError(f"Agent {agent_id} has no activity-bound leg")
    candidates.sort(key=lambda item: (item["departure_time"], item["leg_sequence"]))
    configure_weather(weather_week)
    exposed = [
        leg
        for leg in candidates
        if t2_weather.outbound_weather_exposure(
            str(leg["day"]), leg["departure_time"], leg["arrival_time"]
        )
    ]
    return (exposed[0], True) if exposed else (candidates[0], False)


def existing_ride_access(profile: dict[str, Any]) -> bool:
    return bool(
        profile.get("independent_ride_hailing")
        or (profile.get("is_elder") and profile.get("family_assistance"))
    )


def policy_ride_access(
    profile: dict[str, Any], policy: str, policy_result: dict[str, Any] | None
) -> bool:
    if policy == "P3":
        return True
    if policy in {"P1", "P2"}:
        return bool(policy_result and policy_result.get("access_channel") == "online_self")
    return existing_ride_access(profile)


def compact_mode_option(
    mode: str,
    option: dict[str, Any],
    profile: dict[str, Any],
    arm: dict[str, Any],
    policy_result: dict[str, Any] | None,
) -> dict[str, Any]:
    static_available = bool(option.get("available"))
    operating = bool(option.get("operating", static_available))
    access_ok = True
    if mode == "ride_hailing":
        access_ok = policy_ride_access(profile, arm["policy"], policy_result)
    selectable = static_available and operating and access_ok
    fare = option.get("fare")
    eligible_discount = bool(
        mode == "ride_hailing"
        and policy_result
        and policy_result.get("price_discount_eligible")
    )
    discount = float(policy_result["discount_amount"]) if eligible_discount else 0.0
    conditional_fare = None if fare is None else round(max(0.0, float(fare) - discount), 2)
    line_transfers = option.get("line_transfer_count")
    mode_transfers = option.get("mode_transfer_count")
    transfers = option.get("transfers")
    if transfers is None and (line_transfers is not None or mode_transfers is not None):
        transfers = int(line_transfers or 0) + int(mode_transfers or 0)
    return {
        "mode": mode,
        "selectable": selectable,
        "static_available": static_available,
        "operating_at_departure": operating,
        "access_eligible": access_ok,
        "final_total_time_min": option.get("final_total_time_min"),
        "base_fare_cny": fare,
        "conditional_fare_if_selected_cny": conditional_fare,
        "conditional_discount_if_selected_cny": discount,
        "wait_time_min": option.get("period_wait_time_min"),
        "transfers": transfers,
        "line_transfer_count": line_transfers,
        "mode_transfer_count": mode_transfers,
        "transfer_time_min": option.get(
            "period_transfer_penalty_min", option.get("transfer_time_min")
        ),
        "time_period": option.get("time_period"),
        "weather_type": option.get("weather_type"),
        "weather_phase": option.get("weather_phase"),
        "weather_speed_multiplier": option.get("weather_speed_multiplier"),
        "road_capacity_multiplier": option.get("road_capacity_multiplier"),
        "t10_extra_congestion_multiplier": option.get("extra_multiplier"),
    }


def build_decision_contexts_for_assigned_agents(
    assigned_agents: list[Any],
    spatial: dict[str, Any],
    spatial_by_id: dict[str, Any],
    population_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build decision contexts for a bounded slice of a full assigned population.

    T1/T6 random draws are keyed by seed and agent ID in the reference model,
    so materializing a slice preserves the same per-agent result as a monolithic
    run without retaining 100,000 agents' plans and legs in memory.
    """
    agent_rows, legs, _ = materialize_urban_population(
        assigned_agents, spatial, spatial_by_id
    )
    legs_by_agent: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for leg in legs:
        legs_by_agent[int(leg["agent_id"])].append(leg)
    network = build_transport_network()

    contexts: list[dict[str, Any]] = []
    for profile in agent_rows:
        agent_id = int(profile["agent_id"])
        arm = dict(ARMS[(agent_id - 1) % len(ARMS)])
        events = configure_weather(arm["weather"])
        leg, weather_target_found = choose_representative_leg(
            agent_id, legs_by_agent, arm["weather"]
        )
        # choose_representative_leg resets the same weather configuration.
        events = weather_events_from_t2_config(WEEK_START)
        activity_for_t2 = {
            "agent_id": agent_id,
            "activity_id": leg["activity_id"],
            "day_of_week": leg["day"],
            "activity_purpose": leg["purpose"],
            "planned_outbound_departure": leg["departure_time"],
            "planned_activity_arrival": leg["arrival_time"],
        }
        weather_decision = t2_weather.evaluate_planned_activity(
            activity_for_t2, profile, scenario_level="base", seed=SEED
        )
        trip_continues = bool(weather_decision["activity_executes"])
        policy_result = evaluate_t3_policy(
            profile,
            {"leg_id": leg["leg_id"], "trip_continues": trip_continues},
            policy_scenario=arm["policy"],
            discount_level=arm["discount"],
            discount_amount_low=DISCOUNT_LOW,
            discount_amount_high=DISCOUNT_HIGH,
            weekly_discount_use_count=0,
            random_seed=SEED,
        )

        mode_options = []
        for mode in MODES:
            option = calculate_dynamic_congestion_leg_mode_option(
                network,
                leg,
                mode,
                events,
                0.0,
                corridor_id=f"{leg['origin_zone']}-{leg['destination_zone']}",
                direction=f"{leg['origin_zone']}_to_{leg['destination_zone']}",
                shared_state_flow_is_aggregated=True,
                excess_flow_source="agent_mode_choice_scenario_delta",
                seed=SEED,
            )
            mode_options.append(
                compact_mode_option(mode, option, profile, arm, policy_result)
            )

        context = {
            "agent_id": agent_id,
            "name": f"Urban Resident {agent_id:02d}",
            "age_group": profile["age_group"],
            "is_elder": profile["is_elder"],
            "work_status": profile["work_status"],
            "medical_need_level": profile["medical_need_level"],
            "smartphone_access": profile["smartphone_access"],
            "digital_access": profile["digital_access"],
            "family_assistance": profile["family_assistance"],
            "independent_ride_hailing": profile["independent_ride_hailing"],
            "mobility_constraint": profile["mobility_constraint"],
            "schedule_flexibility": profile["schedule_flexibility"],
            "home_zone": profile["home_zone"],
            "home_zone_name": profile["home_zone_name"],
            "scenario": arm,
            "representative_trip": {
                "leg_id": leg["leg_id"],
                "purpose": leg["purpose"],
                "leg_role": leg["leg_role"],
                "day": leg["day"],
                "departure_time": leg["departure_time"].isoformat(),
                "arrival_time": leg["arrival_time"].isoformat(),
                "origin_zone": leg["origin_zone"],
                "origin_zone_name": spatial_by_id[leg["origin_zone"]]["display_name"],
                "destination_zone": leg["destination_zone"],
                "destination_zone_name": spatial_by_id[leg["destination_zone"]]["display_name"],
                "road_network_distance_km": leg["road_network_distance_km"],
                "weather_target_leg_found": weather_target_found,
            },
            "weather_activity_effect": {
                key: weather_decision[key]
                for key in (
                    "weather_type",
                    "outbound_weather_exposed",
                    "p_weather_cancel",
                    "weather_random_draw",
                    "weather_cancelled",
                    "activity_executes",
                    "unmet_mandatory_trip",
                    "ride_hailing_odds_multiplier",
                )
            },
            "policy_effect": policy_result,
            "trip_continues": trip_continues,
            "mode_options": mode_options,
            "model_boundaries": {
                "actual_dispatch_simulated": False,
                "endogenous_flow_simulated": False,
                "t10_excess_flow_pcu_per_hour": 0.0,
                "discount_is_conditional_until_ride_selected": True,
            },
        }
        contexts.append(context)

    agent_count = len(contexts)
    design = {
        "title": f"{agent_count}-Agent Urban Cup GitHub-factors integration pilot",
        "seed": SEED,
        "population_generated": population_size,
        "agents_run": agent_count,
        "decision_count_per_agent": 1,
        "scenario_arms": list(ARMS),
        "w2_windows": list(W2_WINDOWS),
        "discount_amounts_cny": {"low": DISCOUNT_LOW, "high": DISCOUNT_HIGH},
        "pilot_assumptions": {
            "coupon_awareness_probability": 0.50,
            "coupon_claim_probability": 0.40,
            "elder_independent_ride_hailing": "equals digital_access",
            "mobility_constraint": "derived from elder medical need",
            "schedule_flexibility": "derived from work status",
        },
        "github_layers_included": [
            "T1 population and seven-day activity plan",
            "T2 weather activity disruption",
            "T3 policy eligibility",
            "T4 nine-zone synthetic city",
            "T6 destination assignment",
            "T7 multimodal network",
            "T8 time-dependent supply",
            "T9 weather-adjusted supply",
            "T10 marginal congestion with honest zero pre-choice excess flow",
        ],
        "not_implemented_in_source_and_not_fabricated": [
            "ride-hailing dispatch success",
            "vehicle competition and dynamic waiting",
            "endogenous scenario road flow",
            "actual coupon redemption after dispatch",
            "causal treatment-effect estimation",
        ],
    }
    return contexts, design


def build_decision_contexts(
    population_size: int,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Legacy-compatible wrapper used by the existing 50/200 launchers."""
    agents, spatial, spatial_by_id = build_assigned_population(population_size)
    return build_decision_contexts_for_assigned_agents(
        agents[:limit], spatial, spatial_by_id, population_size
    )


def write_contexts(path: Path, contexts: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for context in contexts:
            stream.write(json.dumps(context, ensure_ascii=False, default=str) + "\n")


def flatten_results(
    contexts: list[dict[str, Any]], response: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    context_by_id = {int(item["agent_id"]): item for item in contexts}
    answer_by_id = {
        int(item.agent_id): item.answers[0]
        for item in response.responses
        if item.answers
    }
    rows = []
    violations = []
    for agent_id, context in sorted(context_by_id.items()):
        answer = answer_by_id.get(agent_id)
        choice = None if answer is None else answer.parsed_value
        reason = None if answer is None else answer.reason
        parse_success = bool(answer and answer.parse_success)
        selectable = {
            item["mode"]
            for item in context["mode_options"]
            if item["selectable"]
        }
        valid = parse_success and (
            choice == "cancel_trip" or choice in selectable
        )
        if not context["trip_continues"] and choice != "cancel_trip":
            valid = False
        if not valid:
            violations.append(
                {
                    "agent_id": agent_id,
                    "choice": choice,
                    "trip_continues": context["trip_continues"],
                    "selectable_modes": sorted(selectable),
                }
            )
        rows.append(
            {
                "agent_id": agent_id,
                "age_group": context["age_group"],
                "home_zone": context["home_zone"],
                "arm": context["scenario"]["arm"],
                "weather": context["scenario"]["weather"],
                "policy": context["scenario"]["policy"],
                "discount_level": context["scenario"]["discount"],
                "purpose": context["representative_trip"]["purpose"],
                "weather_exposed": context["weather_activity_effect"]["outbound_weather_exposed"],
                "weather_cancelled": context["weather_activity_effect"]["weather_cancelled"],
                "trip_continues": context["trip_continues"],
                "choice": choice,
                "reason": reason,
                "parse_success": parse_success,
                "constraint_valid": valid,
            }
        )
    return rows, violations


def aggregate_results(
    rows: list[dict[str, Any]], violations: list[dict[str, Any]], token_stats: dict[str, Any]
) -> dict[str, Any]:
    choices = Counter(str(row["choice"]) for row in rows)
    by_arm: dict[str, Counter] = defaultdict(Counter)
    by_age: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_arm[row["arm"]][str(row["choice"])] += 1
        by_age[row["age_group"]][str(row["choice"])] += 1
    usage = {
        "calls": sum(int(item.get("calls", 0)) for item in token_stats.values()),
        "input_tokens": sum(int(item.get("input", 0)) for item in token_stats.values()),
        "output_tokens": sum(int(item.get("output", 0)) for item in token_stats.values()),
    }
    return {
        "agent_responses": len(rows),
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "constraint_valid_count": sum(row["constraint_valid"] for row in rows),
        "weather_exposed_count": sum(row["weather_exposed"] for row in rows),
        "weather_cancelled_count": sum(row["weather_cancelled"] for row in rows),
        "choice_counts": dict(sorted(choices.items())),
        "choice_counts_by_arm": {
            key: dict(sorted(value.items())) for key, value in sorted(by_arm.items())
        },
        "choice_counts_by_age": {
            key: dict(sorted(value.items())) for key, value in sorted(by_age.items())
        },
        "constraint_violations": violations,
        "token_usage": usage,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def run_experiment(
    run_dir: Path,
    limit: int,
    selected_agent_ids: set[int] | None = None,
    population_size: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    actual_population_size = population_size or limit
    context_limit = actual_population_size if selected_agent_ids else limit
    contexts, design = build_decision_contexts(actual_population_size, context_limit)
    if selected_agent_ids:
        contexts = [
            item for item in contexts if int(item["agent_id"]) in selected_agent_ids
        ]
        found_ids = {int(item["agent_id"]) for item in contexts}
        if found_ids != selected_agent_ids:
            missing = sorted(selected_agent_ids - found_ids)
            raise ValueError(f"Unknown selected agent ids: {missing}")
        design["title"] = (
            f"{len(contexts)}-Agent targeted retry for "
            f"{actual_population_size}-Agent Urban Cup pilot"
        )
        design["agents_run"] = len(contexts)
        design["targeted_retry_agent_ids"] = sorted(selected_agent_ids)
    expected_count = len(contexts)
    (run_dir / "design.json").write_text(
        json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_contexts(run_dir / "agent_decision_contexts.jsonl", contexts)

    pairs = [(item["agent_id"], item["name"]) for item in contexts]
    specs = [
        {
            "id": item["agent_id"],
            "profile": {
                "id": item["agent_id"],
                "name": item["name"],
                "decision_context": item,
            },
            "config": {
                "enable_memory": False,
                "enable_todo_list": False,
                "max_react_turns": 3,
            },
        }
        for item in contexts
    ]
    social_space = SimpleSocialSpace(pairs)
    router = UrbanReadOnlyRouter([social_space], max_steps=2, max_llm_call_retry=1)
    router.run_dir = run_dir
    router.bind_env_workspaces(run_dir / "env", ["SimpleSocialSpace"])

    force_minimal_ray_dashboard_agent()
    society = AgentSociety(
        agent_specs=specs,
        agent_class_name="PersonAgent",
        env_router=router,
        start_t=datetime(2026, 7, 6, 6, 0, 0),
        run_dir=run_dir,
        batch_size=min(10, expected_count),
        enable_replay=True,
        env_module_types=["SimpleSocialSpace"],
        env_kwargs={"SimpleSocialSpace": {"agent_id_name_pairs": pairs}},
    )
    initialized = False
    try:
        await society.init()
        questionnaire = Questionnaire(
            questionnaire_id="urban_mode_choice_intention_pilot",
            title="Representative urban trip decision",
            description=(
                "Use only your decision_context. The source model has already computed "
                "weather cancellation, policy eligibility, and feasible mode attributes."
            ),
            questions=[
                QuestionItem(
                    id="mode_choice",
                    prompt=(
                        "Choose your final action for the representative trip in your "
                        "decision_context. If trip_continues is false, choose cancel_trip. "
                        "Otherwise choose only a mode whose selectable field is true, or "
                        "cancel_trip if you personally decide not to travel. Consider your "
                        "age, mobility constraint, schedule flexibility, trip purpose, "
                        "travel time, wait, transfers, conditional fare, weather, digital "
                        "access, subsidy eligibility, and dispatch-priority eligibility."
                    ),
                    response_type="choice",
                    choices=["walk", "bus", "metro", "ride_hailing", "cancel_trip"],
                )
            ],
        )
        response = await society.run_questionnaire(questionnaire)
        await society.to_workspace()
        rows, violations = flatten_results(contexts, response)
        write_csv(run_dir / "agent_choices.csv", rows)
        (run_dir / "questionnaire_response.json").write_text(
            json.dumps(response.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        aggregate = aggregate_results(rows, violations, dict(society._token_stats))
        return {
            "status": (
                "PASS"
                if not violations and len(rows) == expected_count
                else "PASS_WITH_WARNINGS"
            ),
            "experiment": design["title"],
            "framework": {
                "agentsociety2": agentsociety2.__version__,
                "ray": ray.__version__,
                "agent_class": "PersonAgent",
                "llm_model": "deepseek-v4-flash",
                "environment": "SimpleSocialSpace",
            },
            "design": design,
            "results": aggregate,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if initialized or society.agent_ids:
            await society.close()
        if ray.is_initialized():
            ray.shutdown()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--agent-limit", type=int, default=50)
    parser.add_argument(
        "--population-size",
        type=int,
        default=None,
        help="Population generated before optional limiting/filtering; defaults to agent-limit.",
    )
    parser.add_argument(
        "--agent-ids",
        default="",
        help="Optional comma-separated subset for targeted questionnaire retry.",
    )
    args = parser.parse_args()
    population_size = args.population_size or args.agent_limit
    if not 1 <= args.agent_limit <= 1000:
        parser.error("--agent-limit must be between 1 and 1000")
    if not 1 <= population_size <= 1000:
        parser.error("--population-size must be between 1 and 1000")
    if args.agent_limit > population_size:
        parser.error("--agent-limit cannot exceed --population-size")
    selected_agent_ids = (
        {int(value) for value in args.agent_ids.split(",") if value.strip()}
        if args.agent_ids
        else None
    )
    run_dir = PROJECT_ROOT / "runs" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    summary_path = run_dir / "summary.json"
    try:
        summary = await run_experiment(
            run_dir,
            args.agent_limit,
            selected_agent_ids=selected_agent_ids,
            population_size=population_size,
        )
    except Exception as exc:
        ray_log_dir = persist_ray_diagnostics(run_dir)
        summary = {
            "status": "ERROR",
            "experiment": (
                f"{args.agent_limit}-Agent Urban Cup GitHub-factors "
                "integration pilot"
            ),
            "agent_limit": args.agent_limit,
            "population_size": population_size,
            "selected_agent_ids": sorted(selected_agent_ids or []),
            "error_type": type(exc).__name__,
            "error": redact_credentials(str(exc)),
            "ray_log_dir": ray_log_dir,
            "api_key_persisted_in_result": False,
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sanitize_text_results(run_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"SUMMARY_PATH={summary_path}")
        raise SystemExit(1)
    summary["api_key_persisted_in_result"] = False
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sanitize_text_results(run_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"SUMMARY_PATH={summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
