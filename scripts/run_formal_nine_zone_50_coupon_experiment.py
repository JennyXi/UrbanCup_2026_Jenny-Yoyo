"""Run paired C0-C4 coupons in the formal nine-zone 50-Agent model."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.agent_population import AgentProfile  # noqa: E402
from custom.agents.coupon_experiment import (  # noqa: E402
    COUPON_POLICIES_WITH_PUBLIC_GOODS as COUPON_POLICIES,
    allocate_daily_coupons,
)
from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)
from custom.agents.public_goods_coupon import (  # noqa: E402
    OFFICIAL_PUBLIC_GOODS_AGENT,
    PUBLIC_GOODS_POLICY,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_50_coupon_experiment.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_50_coupon_experiment"
GROUPS = (
    "18-39", "40-59", "60+_digital",
    "60+_nondigital_assisted", "60+_nondigital_unassisted",
)
SYSTEM_METRICS = (
    "coupon_reached", "coupon_participated", "coupon_awarded", "coupon_redeemed",
    "coupon_induced_requests", "coupon_ineligible_ride_requests",
    "coupon_subsidy_yuan",
    "public_goods_linked_agents", "public_goods_total_contribution_tokens",
    "public_goods_mean_cooperation_score", "public_goods_mean_awarded_need_score",
    "public_goods_coupons_created_by_multiplier",
    "ride_hailing_requests", "successful_ride_hailing_requests", "ride_hailing_failed",
    "mean_ride_hailing_wait_minutes_per_request", "fallback_attempts", "fallback_succeeded",
    "transport_unmet", "mandatory_activity_incomplete", "activity_completion_rate",
    "necessary_activity_completion_rate", "walking_mode_share", "bus_mode_share",
    "metro_mode_share", "ride_hailing_mode_share", "mean_total_travel_time",
    "road_vehicle_volume", "mean_volume_capacity_ratio",
    "mean_experienced_road_leg_speed_kmh", "mean_road_speed_kmh",
    "total_outdoor_exposure_minutes", "total_heat_risk_burden",
)
GROUP_METRICS = (
    "coupon_awarded", "coupon_redeemed", "coupon_induced_requests",
    "coupon_ineligible_ride_requests",
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "mean_ride_hailing_wait_minutes_per_request",
    "fallback_attempts", "transport_unmet", "mandatory_activity_incomplete",
    "necessary_activity_completion_rate", "total_outdoor_exposure_minutes",
    "total_heat_risk_burden",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _profile(row: Mapping[str, Any]) -> AgentProfile:
    return AgentProfile(**dict(row))


def _group(row: Mapping[str, Any]) -> str:
    if row["age_group"] != "60+":
        return str(row["age_group"])
    if row["digital_access"]:
        return "60+_digital"
    if row.get("family_assistance"):
        return "60+_nondigital_assisted"
    return "60+_nondigital_unassisted"


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(statistics.mean(values), 6) if values else 0.0


def _outcomes(
    allocations: list[Mapping[str, Any]], choices: list[Mapping[str, Any]],
    policy: str, weather: str, seed: int,
) -> list[dict[str, Any]]:
    by_agent: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    ineligible_by_agent: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in choices:
        if row["weather_scenario"] != weather:
            continue
        if row["coupon_bound"]:
            by_agent[int(row["agent_id"])].append(row)
        elif (
            row.get("primary_mode") == "ride_hailing"
            and row.get("coupon_ineligibility_reason")
            == "minimum_original_fare_not_met"
        ):
            ineligible_by_agent[int(row["agent_id"])].append(row)
    rows = []
    for allocation in allocations:
        bound = sorted(
            by_agent.get(int(allocation["agent_id"]), []),
            key=lambda row: (row["departure_time"], row["leg_id"]),
        )
        ineligible = sorted(
            ineligible_by_agent.get(int(allocation["agent_id"]), []),
            key=lambda row: (row["departure_time"], row["leg_id"]),
        )
        first = bound[0] if bound else None
        awarded = bool(allocation["coupon_awarded"])
        if not awarded:
            status = "not_awarded"
        elif first is None and ineligible:
            status = "unused_ineligible_ride_request"
        elif first is None:
            status = "unused_no_ride_request"
        elif first["coupon_redeemed"]:
            status = "redeemed"
        else:
            status = "expired_after_failed_request"
        rows.append({
            "seed": seed, "policy": policy, "weather_scenario": weather,
            **dict(allocation), "coupon_status": status,
            "bound_leg_id": None if first is None else first["leg_id"],
            "ineligible_ride_request_count": len(ineligible),
            "first_ineligible_leg_id": None if not ineligible else ineligible[0]["leg_id"],
            "coupon_ineligibility_reason": (
                "" if not ineligible else "minimum_original_fare_not_met"
            ),
            "coupon_redeemed": bool(first and first["coupon_redeemed"]),
            "coupon_subsidy_yuan": 0.0 if first is None else first["coupon_subsidy_yuan"],
        })
    return rows


def _system_row(
    summary: Mapping[str, Any], allocations: list[Mapping[str, Any]],
    outcomes: list[Mapping[str, Any]], choices: list[Mapping[str, Any]],
    dispatch: list[Mapping[str, Any]], policy: str,
) -> dict[str, Any]:
    weather = summary["weather_scenario"]
    scenario_choices = [row for row in choices if row["weather_scenario"] == weather]
    scenario_dispatch = [row for row in dispatch if row["weather_scenario"] == weather]
    awarded_public_goods = [
        row for row in allocations
        if row["coupon_awarded"] and row["coupon_policy"] == PUBLIC_GOODS_POLICY
    ]
    participating_public_goods = [
        row for row in allocations
        if row["coupon_participated"] and row["coupon_policy"] == PUBLIC_GOODS_POLICY
    ]
    return {
        **dict(summary), "policy": policy,
        "coupon_reached": sum(bool(row["coupon_reached"]) for row in allocations),
        "coupon_participated": sum(bool(row["coupon_participated"]) for row in allocations),
        "coupon_awarded": sum(bool(row["coupon_awarded"]) for row in allocations),
        "coupon_redeemed": sum(bool(row["coupon_redeemed"]) for row in outcomes),
        "coupon_expired_after_failed_request": sum(
            row["coupon_status"] == "expired_after_failed_request" for row in outcomes
        ),
        "coupon_induced_requests": sum(bool(row["coupon_induced_request"]) for row in scenario_choices),
        "coupon_ineligible_ride_requests": sum(
            int(row.get("ineligible_ride_request_count", 0)) for row in outcomes
        ),
        "coupon_subsidy_yuan": round(sum(float(row["coupon_subsidy_yuan"]) for row in outcomes), 2),
        "public_goods_linked_agents": sum(
            bool(row["pg_linked_decision"]) for row in allocations
        ),
        "public_goods_total_contribution_tokens": sum(
            int(row["pg_total_contribution"]) for row in allocations
        ),
        "public_goods_mean_cooperation_score": round(
            _mean(float(row["pg_cooperation_score"]) for row in participating_public_goods),
            6,
        ),
        "public_goods_mean_awarded_need_score": round(
            _mean(float(row["pg_need_score"]) for row in awarded_public_goods), 6
        ),
        "public_goods_coupons_created_by_multiplier": sum(
            int(row["pg_coupons_created_by_multiplier"]) for row in allocations
        ),
        "mean_ride_hailing_wait_minutes_per_request": _mean(
            float(row["pickup_wait_min"]) for row in scenario_dispatch
        ),
        "total_outdoor_exposure_minutes": round(
            sum(float(row["outdoor_exposure_minutes"]) for row in scenario_choices), 6
        ),
        "total_heat_risk_burden": round(
            sum(float(row["heat_risk_burden"]) for row in scenario_choices), 6
        ),
    }


def _group_rows(
    result: Mapping[str, Any], allocations: list[Mapping[str, Any]],
    outcomes: list[Mapping[str, Any]], policy: str, seed: int,
    weather_scenarios: Iterable[str] = ("W0", "W2"),
) -> list[dict[str, Any]]:
    agents = {int(row["agent_id"]): row for row in result["inputs"]["agents"]}
    output = []
    for weather in weather_scenarios:
        activities = [row for row in result["activity_results"] if row["weather_scenario"] == weather]
        choices = [row for row in result["mode_choices"] if row["weather_scenario"] == weather]
        dispatch = [row for row in result["ride_hailing_dispatch"] if row["weather_scenario"] == weather]
        weather_outcomes = [row for row in outcomes if row["weather_scenario"] == weather]
        for group in GROUPS:
            ids = {agent_id for agent_id, row in agents.items() if _group(row) == group}
            group_activities = [row for row in activities if int(row["agent_id"]) in ids]
            necessary = [row for row in group_activities if row["is_mandatory"]]
            group_choices = [row for row in choices if int(row["agent_id"]) in ids]
            successful = [row for row in group_choices if row["transport_succeeded"]]
            counts = Counter(row["final_mode"] for row in successful)
            group_dispatch = [row for row in dispatch if int(row["agent_id"]) in ids]
            daily = [row for row in allocations if int(row["agent_id"]) in ids]
            daily_outcomes = [row for row in weather_outcomes if int(row["agent_id"]) in ids]
            output.append({
                "seed": seed, "policy": policy, "weather_scenario": weather,
                "day_type": "workday", "group": group, "agent_count": len(ids),
                "coupon_reached": sum(bool(row["coupon_reached"]) for row in daily),
                "coupon_participated": sum(bool(row["coupon_participated"]) for row in daily),
                "coupon_awarded": sum(bool(row["coupon_awarded"]) for row in daily),
                "coupon_redeemed": sum(bool(row["coupon_redeemed"]) for row in daily_outcomes),
                "coupon_induced_requests": sum(bool(row["coupon_induced_request"]) for row in group_choices),
                "coupon_ineligible_ride_requests": sum(
                    int(row.get("ineligible_ride_request_count", 0))
                    for row in daily_outcomes
                ),
                "coupon_subsidy_yuan": round(sum(float(row["coupon_subsidy_yuan"]) for row in daily_outcomes), 2),
                "ride_hailing_requests": len(group_dispatch),
                "successful_ride_hailing_requests": sum(row["succeeded"] for row in group_dispatch),
                "failed_ride_hailing_requests": sum(not row["succeeded"] for row in group_dispatch),
                "mean_ride_hailing_wait_minutes_per_request": _mean(
                    float(row["pickup_wait_min"]) for row in group_dispatch
                ),
                "fallback_attempts": sum(row["fallback_attempted"] for row in group_choices),
                "transport_unmet": sum(row["transport_unmet"] for row in group_activities),
                "mandatory_activity_incomplete": sum(row["mandatory_activity_incomplete"] for row in necessary),
                "necessary_activity_completion_rate": (
                    round(sum(row["completed"] for row in necessary) / len(necessary), 6)
                    if necessary else 1.0
                ),
                "walking_legs": counts["walk"], "bus_legs": counts["bus"],
                "metro_legs": counts["metro"], "ride_hailing_legs": counts["ride_hailing"],
                "total_outdoor_exposure_minutes": round(sum(float(row["outdoor_exposure_minutes"]) for row in group_choices), 6),
                "total_heat_risk_burden": round(sum(float(row["heat_risk_burden"]) for row in group_choices), 6),
            })
    return output


def _distributions(
    rows: list[Mapping[str, Any]], weather_scenarios: Iterable[str] = ("W0", "W2"),
) -> list[dict[str, Any]]:
    output = []
    for policy in COUPON_POLICIES:
        for weather in weather_scenarios:
            group = [row for row in rows if row["policy"] == policy and row["weather_scenario"] == weather]
            for metric in SYSTEM_METRICS:
                values = [float(row[metric]) for row in group]
                output.append({
                    "policy": policy, "weather_scenario": weather, "metric": metric,
                    "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                    "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                    "median": round(statistics.median(values), 6),
                    "minimum": round(min(values), 6), "maximum": round(max(values), 6),
                })
    return output


def _group_distributions(
    rows: list[Mapping[str, Any]], weather_scenarios: Iterable[str] = ("W0", "W2"),
) -> list[dict[str, Any]]:
    output = []
    for policy in COUPON_POLICIES:
        for weather in weather_scenarios:
            for group_name in GROUPS:
                group = [
                    row for row in rows
                    if row["policy"] == policy and row["weather_scenario"] == weather
                    and row["group"] == group_name
                ]
                for metric in GROUP_METRICS:
                    values = [float(row[metric]) for row in group]
                    output.append({
                        "policy": policy, "weather_scenario": weather,
                        "group": group_name, "metric": metric,
                        "seed_count": len(values), "mean": round(statistics.mean(values), 6),
                        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                        "median": round(statistics.median(values), 6),
                        "minimum": round(min(values), 6), "maximum": round(max(values), 6),
                    })
    return output


def _policy_changes(
    distributions: list[Mapping[str, Any]], weather_scenarios: Iterable[str] = ("W0", "W2"),
) -> list[dict[str, Any]]:
    lookup = {
        (row["policy"], row["weather_scenario"], row["metric"]): float(row["mean"])
        for row in distributions
    }
    output = []
    for policy in COUPON_POLICIES[1:]:
        for weather in weather_scenarios:
            for metric in SYSTEM_METRICS:
                baseline = lookup[("C0_no_coupon", weather, metric)]
                current = lookup[(policy, weather, metric)]
                output.append({
                    "policy": policy, "baseline_policy": "C0_no_coupon",
                    "weather_scenario": weather, "metric": metric,
                    "baseline_mean": baseline, "policy_mean": current,
                    "absolute_change": round(current - baseline, 6),
                    "percent_change": round((current - baseline) / baseline * 100.0, 6) if baseline else None,
                    "percent_change_defined": baseline != 0,
                    "undefined_reason": "" if baseline else "baseline_zero",
                })
    return output


def _run_seed(
    seed: int,
    config: Mapping[str, Any],
    base_config: Mapping[str, Any],
    weather_scenarios: tuple[str, ...],
    fleet_total: int,
) -> dict[str, Any]:
    """Run one paired seed; safe to execute in a separate process."""
    system_rows, group_rows, allocation_rows = [], [], []
    outcome_rows, choice_rows, dispatch_rows, checks = [], [], [], []
    policy_results = {}
    policy_allocations = {}
    paired_inputs = None
    for policy in COUPON_POLICIES:
        if paired_inputs is None:
            bootstrap = run_formal_nine_zone_50_experiment(
                config=base_config, seed=seed, weather_scenarios=("W0",),
                day_types=("workday",),
            )
            paired_inputs = bootstrap["inputs"]
        profiles = [_profile(row) for row in paired_inputs["agents"]]
        allocations = allocate_daily_coupons(
            profiles, policy, "workday", seed=seed, config=config,
        )
        allocation_map = {int(row["agent_id"]): row for row in allocations}
        run_config = copy.deepcopy(base_config)
        formal_overrides = run_config.setdefault("formal_overrides", {})
        formal_overrides["experiment_condition"] = f"coupon_{policy}"
        fleet_overrides = formal_overrides.setdefault("ride_hailing_fleet", {})
        fleet_overrides.setdefault("initial_vehicles_by_day_type", {})[
            config["day_type"]
        ] = config["initial_vehicles"]
        formal_overrides["_coupon_allocations"] = allocation_map
        formal_overrides["_coupon_discount_multiplier"] = float(
            config["coupon_experiment"]["discount_multiplier"]
        )
        if config.get("mode_choice_override"):
            formal_overrides["mode_choice"] = copy.deepcopy(
                config["mode_choice_override"]
            )
        result = run_formal_nine_zone_50_experiment(
            config=run_config, seed=seed,
            weather_scenarios=weather_scenarios,
            day_types=(config["day_type"],), paired_inputs=paired_inputs,
        )
        policy_results[policy] = result
        policy_allocations[policy] = allocations

    baseline = {
        (row["weather_scenario"], row["leg_id"]): row["primary_mode"]
        for row in policy_results["C0_no_coupon"]["mode_choices"]
    }
    baseline_priorities = {
        (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
        for row in policy_results["C0_no_coupon"]["ride_hailing_dispatch"]
    }
    for policy in COUPON_POLICIES:
        result = policy_results[policy]
        allocations = policy_allocations[policy]
        for row in result["mode_choices"]:
            row["policy"] = policy
            row["coupon_induced_request"] = bool(
                row["coupon_bound"] and row["primary_mode"] == "ride_hailing"
                and baseline.get((row["weather_scenario"], row["leg_id"])) != "ride_hailing"
            )
        for row in result["ride_hailing_dispatch"]:
            row["policy"] = policy
        outcomes = []
        for weather in config["weather_scenarios"]:
            outcomes.extend(_outcomes(
                allocations, result["mode_choices"], policy, weather, seed,
            ))
        for summary in result["summary_rows"]:
            weather_outcomes = [
                row for row in outcomes
                if row["weather_scenario"] == summary["weather_scenario"]
            ]
            system_rows.append(_system_row(
                summary, allocations, weather_outcomes, result["mode_choices"],
                result["ride_hailing_dispatch"], policy,
            ))
        group_rows.extend(_group_rows(
            result, allocations, outcomes, policy, seed, weather_scenarios,
        ))
        allocation_rows.extend({"seed": seed, "policy": policy, **row} for row in allocations)
        outcome_rows.extend(outcomes)
        choice_rows.extend({"seed": seed, **row} for row in result["mode_choices"])
        dispatch_rows.extend({"seed": seed, **row} for row in result["ride_hailing_dispatch"])
        by_vehicle: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for dispatch_row in result["ride_hailing_dispatch"]:
            if dispatch_row["succeeded"]:
                by_vehicle[(dispatch_row["weather_scenario"], dispatch_row["vehicle_id"])].append(dispatch_row)
        vehicle_nonoverlap = all(
            all(
                float(right["busy_start"]) + 1e-9 >= float(left["busy_until"])
                for left, right in zip(ordered, ordered[1:])
            )
            for vehicle_rows in by_vehicle.values()
            for ordered in [sorted(vehicle_rows, key=lambda row: float(row["busy_start"]))]
        )
        current_priorities = {
            (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
            for row in result["ride_hailing_dispatch"]
        }
        common_priority = set(baseline_priorities) & set(current_priorities)
        public_goods_rows = (
            allocations if policy == PUBLIC_GOODS_POLICY else []
        )
        public_goods_participants = sum(
            bool(row["coupon_participated"]) for row in public_goods_rows
        )
        public_goods_awards = sum(
            bool(row["coupon_awarded"]) for row in public_goods_rows
        )
        expected_public_goods_awards = min(
            int(config["coupon_experiment"]["daily_total_coupon_pool"]),
            public_goods_participants,
        )
        public_goods_awarded_ranks = sorted(
            int(row["coupon_allocation_rank"])
            for row in public_goods_rows
            if row["coupon_awarded"]
        )
        check = {
            "seed": seed, "policy": policy,
            "coupon_pool_limit_passed": sum(row["coupon_awarded"] for row in allocations) <= int(config["coupon_experiment"]["daily_total_coupon_pool"]),
            "one_coupon_per_agent_day_passed": len({row["agent_id"] for row in allocations if row["coupon_awarded"]}) == sum(row["coupon_awarded"] for row in allocations),
            "one_binding_per_agent_weather_passed": all(value <= 1 for value in Counter(
                (row["weather_scenario"], row["agent_id"])
                for row in result["mode_choices"] if row["coupon_bound"]
            ).values()),
            "vehicle_conservation_passed": all(
                sum(row["weather_scenario"] == weather for row in result["vehicle_end_states"]) == fleet_total
                for weather in weather_scenarios
            ),
            "vehicle_assignments_nonoverlapping_passed": vehicle_nonoverlap,
            "common_dispatch_priority_passed": all(
                baseline_priorities[key] == current_priorities[key] for key in common_priority
            ),
            "common_agents_activities_od_passed": all(
                candidate["inputs"]["agents"] == result["inputs"]["agents"]
                and candidate["inputs"]["activities"] == result["inputs"]["activities"]
                for candidate in policy_results.values()
            ),
            "nondigital_unassisted_public_exclusion_passed": all(
                not row["public_coupon_participated"]
                or (
                    policy == PUBLIC_GOODS_POLICY
                    and row["community_phone_covered"]
                    and row["coupon_access_channel"] == "community_phone"
                )
                for row in allocations if row["nondigital_unassisted"]
            ),
            "coupon_only_bound_request_can_be_induced_passed": all(
                not row["coupon_induced_request"] or row["coupon_bound"]
                for row in result["mode_choices"]
            ),
            "public_goods_coupon_conservation_passed": (
                policy != PUBLIC_GOODS_POLICY
                or (
                    public_goods_awards == expected_public_goods_awards
                    and all(
                        int(row["pg_coupons_created_by_multiplier"]) == 0
                        and int(row["pg_physical_coupon_pool"])
                        == int(config["coupon_experiment"]["daily_total_coupon_pool"])
                        for row in public_goods_rows
                    )
                )
            ),
            "public_goods_linked_decisions_passed": (
                policy != PUBLIC_GOODS_POLICY
                or public_goods_participants <= 1
                or all(
                    row["pg_linked_decision"]
                    and int(row["pg_peer_feedback_source_count"])
                    == public_goods_participants - 1
                    for row in public_goods_rows
                    if row["coupon_participated"]
                )
            ),
            "public_goods_official_parent_passed": (
                policy != PUBLIC_GOODS_POLICY
                or all(
                    row["pg_official_parent_agent_class"]
                    == OFFICIAL_PUBLIC_GOODS_AGENT
                    for row in public_goods_rows
                )
            ),
            "public_goods_priority_rank_passed": (
                policy != PUBLIC_GOODS_POLICY
                or public_goods_awarded_ranks
                == list(range(1, public_goods_awards + 1))
            ),
        }
        check["passed"] = all(
            value for key, value in check.items() if key.endswith("_passed")
        )
        checks.append(check)
    return {
        "seed": seed,
        "system_rows": system_rows, "group_rows": group_rows,
        "allocation_rows": allocation_rows, "outcome_rows": outcome_rows,
        "choice_rows": choice_rows, "dispatch_rows": dispatch_rows,
        "checks": checks,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of seed processes to run in parallel (default: 1).",
    )
    args = parser.parse_args()
    config = _load(args.config)
    seed_start = int(config["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(config["seed_count"] if args.seed_count is None else args.seed_count)
    base_config = load_formal_50_config(ROOT / config["base_experiment_config"])
    fleet_total = sum(config["initial_vehicles"].values())
    expected_fleet_total = int(config.get("expected_initial_vehicle_total", 12))
    if fleet_total != expected_fleet_total:
        raise ValueError("formal coupon vehicle map must sum to expected_initial_vehicle_total")
    weather_scenarios = tuple(config["weather_scenarios"])
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    system_rows, group_rows, allocation_rows = [], [], []
    outcome_rows, choice_rows, dispatch_rows, checks = [], [], [], []
    seeds = list(range(seed_start, seed_start + seed_count))
    completed: dict[int, dict[str, Any]] = {}
    if args.workers == 1:
        for index, seed in enumerate(seeds, start=1):
            completed[seed] = _run_seed(
                seed, config, base_config, weather_scenarios, fleet_total,
            )
            print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)
    else:
        worker_count = min(args.workers, seed_count)
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _run_seed, seed, config, base_config,
                    weather_scenarios, fleet_total,
                ): seed
                for seed in seeds
            }
            for index, future in enumerate(as_completed(futures), start=1):
                seed = futures[future]
                completed[seed] = future.result()
                print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)

    for seed in seeds:
        result = completed[seed]
        system_rows.extend(result["system_rows"])
        group_rows.extend(result["group_rows"])
        allocation_rows.extend(result["allocation_rows"])
        outcome_rows.extend(result["outcome_rows"])
        choice_rows.extend(result["choice_rows"])
        dispatch_rows.extend(result["dispatch_rows"])
        checks.extend(result["checks"])

    distributions = _distributions(system_rows, weather_scenarios)
    group_distributions = _group_distributions(group_rows, weather_scenarios)
    policy_changes = _policy_changes(distributions, weather_scenarios)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in {
        "system_per_seed": system_rows, "group_per_seed": group_rows,
        "system_distributions": distributions,
        "group_distributions": group_distributions,
        "policy_changes_vs_c0": policy_changes,
        "coupon_allocations": allocation_rows,
        "coupon_outcomes": outcome_rows, "mode_choices": choice_rows,
        "ride_hailing_dispatch": dispatch_rows, "consistency_checks": checks,
    }.items():
        _write_csv(output / f"{name}.csv", rows)
    print(f"Completed {len(system_rows)} policy-weather rows")
    print(f"Checks passed: {sum(row['passed'] for row in checks)}/{len(checks)}")
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
