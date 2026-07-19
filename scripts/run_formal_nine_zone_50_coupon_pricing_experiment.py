"""Run paired 50-Agent coupon intensity or pricing-form audits."""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.coupon_experiment import allocate_daily_coupons  # noqa: E402
from custom.agents.formal_nine_zone_50_experiment import (  # noqa: E402
    load_formal_50_config,
    run_formal_nine_zone_50_experiment,
)
from scripts.run_formal_nine_zone_50_coupon_experiment import (  # noqa: E402
    GROUP_METRICS, GROUPS, SYSTEM_METRICS, _group_rows, _load, _outcomes,
    _profile, _system_row, _write_csv,
)

DEFAULT_CONFIG = ROOT / "config" / "formal_nine_zone_50_coupon_pricing_experiment.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "formal_nine_zone_50_coupon_pricing"

REQUEST_SEGMENT_METRICS = (
    "ride_hailing_requests", "successful_requests", "failed_requests",
    "coupon_bound_requests", "coupon_redeemed_requests",
    "coupon_induced_requests", "total_realized_subsidy_yuan",
    "mean_request_distance_km", "mean_original_fare_yuan",
    "median_original_fare_yuan", "mean_offered_fare_yuan",
    "mean_offered_discount_yuan_per_bound_request",
    "mean_realized_subsidy_yuan_per_redeemed_request",
    "mean_pickup_wait_minutes", "maximum_pickup_wait_minutes",
    "induced_requests_per_100_yuan_realized_subsidy",
)
EQUAL_BUDGET_METRICS = (
    "maximum_daily_subsidy_budget_yuan",
    "realized_subsidy_budget_utilization_rate",
)


def _distance_band(distance_km: float, config: Mapping[str, Any]) -> str:
    analysis = config["distance_fare_analysis"]
    short_max = float(analysis["short_max_km"])
    medium_max = float(analysis["medium_max_km"])
    if distance_km <= short_max:
        return f"short_le_{short_max:g}km"
    if distance_km <= medium_max:
        return f"medium_{short_max:g}_{medium_max:g}km"
    return f"long_gt_{medium_max:g}km"


def _mean_or_none(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _request_segment_rows(
    dispatch_rows: list[Mapping[str, Any]], config: Mapping[str, Any],
    experiment_set: str,
) -> list[dict[str, Any]]:
    scenarios = tuple(config["experiment_sets"][experiment_set]["scenarios"])
    weather_scenarios = tuple(config["weather_scenarios"])
    seeds = tuple(sorted({int(row["seed"]) for row in dispatch_rows}))
    distance_categories = (
        _distance_band(0.0, config),
        _distance_band(float(config["distance_fare_analysis"]["short_max_km"]) + 0.001, config),
        _distance_band(float(config["distance_fare_analysis"]["medium_max_km"]) + 0.001, config),
    )
    dimensions = {
        "all": ("all_requests",),
        "distance_band": distance_categories,
        "work_distance_band": distance_categories,
        "zone_scope": ("intrazonal", "interzonal"),
        "activity_purpose": ("work", "medical", "shopping", "other"),
    }
    output: list[dict[str, Any]] = []
    for seed in seeds:
        for scenario in scenarios:
            for weather in weather_scenarios:
                base = [
                    row for row in dispatch_rows
                    if int(row["seed"]) == seed and row["policy"] == scenario
                    and row["weather_scenario"] == weather
                ]
                for dimension, categories in dimensions.items():
                    for category in categories:
                        if dimension == "all":
                            selected = base
                        elif dimension in {"distance_band", "work_distance_band"}:
                            selected = [
                                row for row in base
                                if _distance_band(float(row["request_network_distance_km"]), config) == category
                                and (dimension != "work_distance_band" or row["purpose"] == "work")
                            ]
                        elif dimension == "zone_scope":
                            selected = [
                                row for row in base
                                if ("intrazonal" if row["origin_zone"] == row["destination_zone"] else "interzonal") == category
                            ]
                        else:
                            selected = [
                                row for row in base
                                if (
                                    row["purpose"] if row["purpose"] in {"work", "medical", "shopping"}
                                    else "other"
                                ) == category
                            ]
                        succeeded = [row for row in selected if row["succeeded"]]
                        bound = [row for row in selected if row["coupon_bound"]]
                        redeemed = [row for row in bound if row["succeeded"]]
                        distances = [float(row["request_network_distance_km"]) for row in selected]
                        originals = [float(row["fare_before_coupon_yuan"]) for row in selected]
                        offered = [float(row["fare_after_coupon_yuan"]) for row in selected]
                        waits = [float(row["pickup_wait_min"]) for row in selected]
                        offered_discounts = [
                            float(row["coupon_subsidy_yuan"]) for row in bound
                        ]
                        realized_subsidy = sum(
                            float(row["coupon_subsidy_yuan"]) for row in redeemed
                        )
                        induced = sum(bool(row["coupon_induced_request"]) for row in selected)
                        output.append({
                            "seed": seed, "experiment_set": experiment_set,
                            "coupon_scenario": scenario, "weather_scenario": weather,
                            "segment_dimension": dimension, "segment": category,
                            "ride_hailing_requests": len(selected),
                            "successful_requests": len(succeeded),
                            "failed_requests": len(selected) - len(succeeded),
                            "coupon_bound_requests": len(bound),
                            "coupon_redeemed_requests": len(redeemed),
                            "coupon_induced_requests": induced,
                            "total_realized_subsidy_yuan": round(realized_subsidy, 6),
                            "mean_request_distance_km": _mean_or_none(distances),
                            "mean_original_fare_yuan": _mean_or_none(originals),
                            "median_original_fare_yuan": round(statistics.median(originals), 6) if originals else None,
                            "mean_offered_fare_yuan": _mean_or_none(offered),
                            "mean_offered_discount_yuan_per_bound_request": _mean_or_none(offered_discounts),
                            "mean_realized_subsidy_yuan_per_redeemed_request": (
                                round(realized_subsidy / len(redeemed), 6) if redeemed else None
                            ),
                            "mean_pickup_wait_minutes": _mean_or_none(waits),
                            "maximum_pickup_wait_minutes": round(max(waits), 6) if waits else None,
                            "induced_requests_per_100_yuan_realized_subsidy": (
                                round(induced / realized_subsidy * 100.0, 6)
                                if realized_subsidy else None
                            ),
                        })
    return output


def _describe_request_segments(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keys = sorted({
        (row["coupon_scenario"], row["weather_scenario"], row["segment_dimension"], row["segment"])
        for row in rows
    })
    output: list[dict[str, Any]] = []
    for scenario, weather, dimension, segment in keys:
        selected = [
            row for row in rows
            if (row["coupon_scenario"], row["weather_scenario"], row["segment_dimension"], row["segment"])
            == (scenario, weather, dimension, segment)
        ]
        for metric in REQUEST_SEGMENT_METRICS:
            values = [float(row[metric]) for row in selected if row[metric] is not None]
            output.append({
                "coupon_scenario": scenario, "weather_scenario": weather,
                "segment_dimension": dimension, "segment": segment,
                "metric": metric, "seed_count": len(selected),
                "defined_seed_count": len(values),
                "mean": _mean_or_none(values),
                "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else (0.0 if values else None),
                "median": round(statistics.median(values), 6) if values else None,
                "minimum": round(min(values), 6) if values else None,
                "maximum": round(max(values), 6) if values else None,
            })
    return output


def _describe(
    rows: list[Mapping[str, Any]], scenario_ids: Iterable[str],
    weather_scenarios: Iterable[str], metrics: Iterable[str],
    *, group_dimension: bool = False,
) -> list[dict[str, Any]]:
    output = []
    groups = GROUPS if group_dimension else (None,)
    for scenario in scenario_ids:
        for weather in weather_scenarios:
            for group_name in groups:
                selected = [
                    row for row in rows
                    if row["policy"] == scenario and row["weather_scenario"] == weather
                    and (not group_dimension or row["group"] == group_name)
                ]
                for metric in metrics:
                    values = [float(row[metric]) for row in selected]
                    result = {
                        "coupon_scenario": scenario, "weather_scenario": weather,
                        "metric": metric, "seed_count": len(values),
                        "mean": round(statistics.mean(values), 6),
                        "std_dev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                        "median": round(statistics.median(values), 6),
                        "minimum": round(min(values), 6), "maximum": round(max(values), 6),
                    }
                    if group_dimension:
                        result["group"] = group_name
                    output.append(result)
    return output


def _changes(
    distributions: list[Mapping[str, Any]], scenario_ids: Iterable[str],
    weather_scenarios: Iterable[str], baseline: str,
) -> list[dict[str, Any]]:
    lookup = {
        (row["coupon_scenario"], row["weather_scenario"], row["metric"]): float(row["mean"])
        for row in distributions
    }
    output = []
    for scenario in scenario_ids:
        if scenario == baseline:
            continue
        for weather in weather_scenarios:
            for metric in SYSTEM_METRICS:
                base = lookup[(baseline, weather, metric)]
                current = lookup[(scenario, weather, metric)]
                output.append({
                    "coupon_scenario": scenario, "baseline_scenario": baseline,
                    "weather_scenario": weather, "metric": metric,
                    "baseline_mean": base, "scenario_mean": current,
                    "absolute_change": round(current - base, 6),
                    "percent_change": round((current - base) / base * 100.0, 6) if base else None,
                    "percent_change_defined": base != 0.0,
                    "undefined_reason": "" if base else "baseline_zero",
                })
    return output


def _vehicle_nonoverlap(dispatch: Iterable[Mapping[str, Any]]) -> bool:
    by_vehicle: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in dispatch:
        if row["succeeded"]:
            by_vehicle[(row["weather_scenario"], row["vehicle_id"])].append(row)
    return all(
        all(
            float(right["busy_start"]) + 1e-9 >= float(left["busy_until"])
            for left, right in zip(ordered, ordered[1:])
        )
        for vehicle_rows in by_vehicle.values()
        for ordered in [sorted(vehicle_rows, key=lambda row: float(row["busy_start"]))]
    )


def _threshold_binding_passed(
    mode_choices: Iterable[Mapping[str, Any]], threshold: float | None,
) -> bool:
    """Validate the primary ride offer, not a fallback mode's final fare."""
    if threshold is None:
        return True
    return all(
        float(
            row.get("primary_fare_before_coupon_yuan")
            if row.get("primary_fare_before_coupon_yuan") is not None
            else row["fare_before_coupon_yuan"]
        ) + 1e-9 >= float(threshold)
        for row in mode_choices if row["coupon_bound"]
    )


def _run_seed(
    seed: int, experiment_set: str, config: Mapping[str, Any],
    base_config: Mapping[str, Any],
) -> dict[str, Any]:
    weather_scenarios = tuple(config["weather_scenarios"])
    experiment_spec = config["experiment_sets"][experiment_set]
    scenario_specs = experiment_spec["scenarios"]
    maximum_budget = experiment_spec.get("maximum_daily_subsidy_budget_yuan")
    bootstrap = run_formal_nine_zone_50_experiment(
        config=base_config, seed=seed, weather_scenarios=("W0",),
        day_types=(config["day_type"],),
    )
    paired_inputs = bootstrap["inputs"]
    profiles = [_profile(row) for row in paired_inputs["agents"]]
    shared_allocations = allocate_daily_coupons(
        profiles, config["allocation_policy"], config["day_type"],
        seed=seed, config=config,
    )
    shared_awarded = {
        int(row["agent_id"]) for row in shared_allocations if row["coupon_awarded"]
    }
    results: dict[str, Mapping[str, Any]] = {}
    allocations_by_scenario: dict[str, list[Mapping[str, Any]]] = {}
    for scenario, spec in scenario_specs.items():
        enabled = bool(spec["coupon_enabled"])
        allocations = shared_allocations if enabled else []
        allocation_map = {
            int(row["agent_id"]): row for row in allocations
        }
        run_config = copy.deepcopy(base_config)
        overrides = run_config.setdefault("formal_overrides", {})
        overrides["experiment_condition"] = f"coupon_pricing_{experiment_set}_{scenario}"
        overrides.setdefault("ride_hailing_fleet", {}).setdefault(
            "initial_vehicles_by_day_type", {}
        )[config["day_type"]] = copy.deepcopy(config["initial_vehicles"])
        overrides["_coupon_allocations"] = allocation_map
        overrides["_coupon_pricing"] = {"scheme_id": scenario, **dict(spec)}
        if config.get("mode_choice_override"):
            overrides["mode_choice"] = copy.deepcopy(config["mode_choice_override"])
        result = run_formal_nine_zone_50_experiment(
            config=run_config, seed=seed, weather_scenarios=weather_scenarios,
            day_types=(config["day_type"],), paired_inputs=paired_inputs,
        )
        results[scenario] = result
        allocations_by_scenario[scenario] = allocations

    baseline_id = next(
        scenario for scenario, spec in scenario_specs.items()
        if not spec["coupon_enabled"]
    )
    baseline_modes = {
        (row["weather_scenario"], row["leg_id"]): row["primary_mode"]
        for row in results[baseline_id]["mode_choices"]
    }
    baseline_priorities = {
        (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
        for row in results[baseline_id]["ride_hailing_dispatch"]
    }
    system_rows, group_rows, outcome_rows = [], [], []
    choice_rows, dispatch_rows, allocation_rows, checks = [], [], [], []
    for scenario, spec in scenario_specs.items():
        result = results[scenario]
        allocations = allocations_by_scenario[scenario]
        for row in result["mode_choices"]:
            row["policy"] = scenario
            row["coupon_induced_request"] = bool(
                row["coupon_bound"] and row["primary_mode"] == "ride_hailing"
                and baseline_modes.get((row["weather_scenario"], row["leg_id"])) != "ride_hailing"
            )
        choice_by_leg = {
            (row["weather_scenario"], row["leg_id"]): row
            for row in result["mode_choices"]
        }
        for row in result["ride_hailing_dispatch"]:
            row["policy"] = scenario
            choice = choice_by_leg[(row["weather_scenario"], row["leg_id"])]
            row["coupon_induced_request"] = bool(choice["coupon_induced_request"])
        outcomes = []
        for weather in weather_scenarios:
            outcomes.extend(_outcomes(
                list(allocations), result["mode_choices"], scenario, weather, seed,
            ))
        for summary in result["summary_rows"]:
            weather = summary["weather_scenario"]
            row = _system_row(
                summary, list(allocations),
                [item for item in outcomes if item["weather_scenario"] == weather],
                result["mode_choices"], result["ride_hailing_dispatch"], scenario,
            )
            redeemed = int(row["coupon_redeemed"])
            subsidy = float(row["coupon_subsidy_yuan"])
            induced = int(row["coupon_induced_requests"])
            policy_budget = (
                float(maximum_budget)
                if maximum_budget is not None and spec["coupon_enabled"] else 0.0
            )
            row.update({
                "experiment_set": experiment_set,
                "coupon_scenario": scenario,
                "coupon_pricing_type": spec["pricing_type"],
                "discount_multiplier": spec.get("discount_multiplier"),
                "discount_amount_yuan": spec.get("discount_amount_yuan"),
                "minimum_original_fare_yuan": spec.get("minimum_original_fare_yuan"),
                "maximum_discount_amount_yuan": spec.get("maximum_discount_amount_yuan"),
                "maximum_daily_subsidy_budget_yuan": policy_budget,
                "realized_subsidy_budget_utilization_rate": (
                    round(subsidy / policy_budget, 6) if policy_budget else 0.0
                ),
                "subsidy_per_redeemed_coupon_yuan": round(subsidy / redeemed, 6) if redeemed else None,
                "induced_requests_per_100_yuan_subsidy": round(induced / subsidy * 100.0, 6) if subsidy else None,
            })
            system_rows.append(row)
        group_rows.extend(_group_rows(
            result, list(allocations), outcomes, scenario, seed, weather_scenarios,
        ))
        outcome_rows.extend({"experiment_set": experiment_set, **row} for row in outcomes)
        choice_rows.extend({"seed": seed, "experiment_set": experiment_set, **row} for row in result["mode_choices"])
        dispatch_rows.extend({"seed": seed, "experiment_set": experiment_set, **row} for row in result["ride_hailing_dispatch"])
        allocation_rows.extend(
            {"seed": seed, "experiment_set": experiment_set, "coupon_scenario": scenario, **row}
            for row in allocations
        )
        current_priorities = {
            (row["weather_scenario"], row["leg_id"]): row["dispatch_priority"]
            for row in result["ride_hailing_dispatch"]
        }
        common = set(baseline_priorities) & set(current_priorities)
        threshold = spec.get("minimum_original_fare_yuan")
        bound = [row for row in result["mode_choices"] if row["coupon_bound"]]
        maximum_per_coupon = experiment_spec.get(
            "maximum_discount_amount_yuan_per_coupon"
        )
        check = {
            "seed": seed, "experiment_set": experiment_set,
            "coupon_scenario": scenario,
            "common_agents_activities_od_passed": all(
                candidate["inputs"]["agents"] == result["inputs"]["agents"]
                and candidate["inputs"]["activities"] == result["inputs"]["activities"]
                for candidate in results.values()
            ),
            "shared_awarded_recipients_passed": (
                not spec["coupon_enabled"] or {
                    int(row["agent_id"]) for row in allocations if row["coupon_awarded"]
                } == shared_awarded
            ),
            "coupon_pool_limit_passed": len(shared_awarded) <= int(
                config["coupon_experiment"]["daily_total_coupon_pool"]
            ),
            "one_coupon_per_agent_day_passed": len(shared_awarded) == sum(
                bool(row["coupon_awarded"]) for row in shared_allocations
            ),
            "threshold_binding_passed": (
                _threshold_binding_passed(result["mode_choices"], threshold)
            ),
            "zero_subsidy_never_redeemed_passed": all(
                not row["coupon_redeemed"] or float(row["coupon_subsidy_yuan"]) > 0.0
                for row in result["mode_choices"]
            ),
            "vehicle_conservation_passed": all(
                sum(row["weather_scenario"] == weather for row in result["vehicle_end_states"])
                == sum(config["initial_vehicles"].values())
                for weather in weather_scenarios
            ),
            "vehicle_assignments_nonoverlapping_passed": _vehicle_nonoverlap(
                result["ride_hailing_dispatch"]
            ),
            "common_dispatch_priority_passed": all(
                current_priorities[key] == baseline_priorities[key] for key in common
            ),
            "maximum_subsidy_liability_passed": (
                maximum_budget is None or not spec["coupon_enabled"]
                or len(shared_awarded) * float(maximum_per_coupon)
                <= float(maximum_budget) + 1e-9
            ),
            "realized_subsidy_budget_not_exceeded_passed": (
                maximum_budget is None or all(
                    sum(
                        float(row["coupon_subsidy_yuan"])
                        for row in result["mode_choices"]
                        if row["weather_scenario"] == weather and row["coupon_redeemed"]
                    ) <= float(maximum_budget) + 1e-9
                    for weather in weather_scenarios
                )
            ),
        }
        check["passed"] = all(
            value for key, value in check.items() if key.endswith("_passed")
        )
        checks.append(check)
    return {
        "seed": seed, "system_rows": system_rows, "group_rows": group_rows,
        "outcome_rows": outcome_rows, "choice_rows": choice_rows,
        "dispatch_rows": dispatch_rows, "allocation_rows": allocation_rows,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--experiment-set",
        choices=("intensity", "format", "equal_budget", "high_value_format"),
        required=True,
    )
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--seed-count", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    config = _load(args.config)
    seed_start = int(config["seed_start"] if args.seed_start is None else args.seed_start)
    seed_count = int(config["seed_count"] if args.seed_count is None else args.seed_count)
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    base_config = load_formal_50_config(ROOT / config["base_experiment_config"])
    output = args.output_dir or (DEFAULT_OUTPUT / args.experiment_set)
    seeds = list(range(seed_start, seed_start + seed_count))
    completed: dict[int, Mapping[str, Any]] = {}
    if args.workers == 1:
        for index, seed in enumerate(seeds, start=1):
            completed[seed] = _run_seed(seed, args.experiment_set, config, base_config)
            print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=min(args.workers, seed_count)) as executor:
            futures = {
                executor.submit(_run_seed, seed, args.experiment_set, config, base_config): seed
                for seed in seeds
            }
            for index, future in enumerate(as_completed(futures), start=1):
                seed = futures[future]
                completed[seed] = future.result()
                print(f"Completed seed {seed} ({index}/{seed_count})", flush=True)
    tables = {key: [] for key in (
        "system_rows", "group_rows", "outcome_rows", "choice_rows",
        "dispatch_rows", "allocation_rows", "checks",
    )}
    for seed in seeds:
        for key in tables:
            tables[key].extend(completed[seed][key])
    scenario_ids = tuple(config["experiment_sets"][args.experiment_set]["scenarios"])
    weather_scenarios = tuple(config["weather_scenarios"])
    system_metrics = tuple(SYSTEM_METRICS) + (
        EQUAL_BUDGET_METRICS if args.experiment_set == "equal_budget" else ()
    )
    distributions = _describe(
        tables["system_rows"], scenario_ids, weather_scenarios, system_metrics,
    )
    group_distributions = _describe(
        tables["group_rows"], scenario_ids, weather_scenarios, GROUP_METRICS,
        group_dimension=True,
    )
    baseline = next(
        scenario for scenario, spec in config["experiment_sets"][args.experiment_set]["scenarios"].items()
        if not spec["coupon_enabled"]
    )
    request_segments = _request_segment_rows(
        tables["dispatch_rows"], config, args.experiment_set,
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in {
        "system_per_seed": tables["system_rows"],
        "system_distributions": distributions,
        "changes_vs_k0": _changes(distributions, scenario_ids, weather_scenarios, baseline),
        "group_per_seed": tables["group_rows"],
        "group_distributions": group_distributions,
        "coupon_allocations": tables["allocation_rows"],
        "coupon_outcomes": tables["outcome_rows"],
        "mode_choices": tables["choice_rows"],
        "ride_hailing_dispatch": tables["dispatch_rows"],
        "ride_hailing_request_segments_per_seed": request_segments,
        "ride_hailing_request_segment_distributions": _describe_request_segments(
            request_segments
        ),
        "consistency_checks": tables["checks"],
    }.items():
        _write_csv(output / f"{name}.csv", rows)
    metadata = {
        "experiment_set": args.experiment_set, "seed_start": seed_start,
        "seed_count": seed_count, "weather_scenarios": weather_scenarios,
        "allocation_policy": config["allocation_policy"],
        "scenarios": config["experiment_sets"][args.experiment_set]["scenarios"],
        "checks_passed": sum(bool(row["passed"]) for row in tables["checks"]),
        "checks_total": len(tables["checks"]),
    }
    (output / "experiment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Checks passed: {metadata['checks_passed']}/{metadata['checks_total']}")
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
