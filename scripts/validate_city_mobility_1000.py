"""Validate the completed 1000-Agent API experiment and emit auditable checks."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output_dir",
        type=Path,
        nargs="?",
        default=Path("outputs/city_mobility_1000_api_w2_seed47_main_elder_v2"),
    )
    args = parser.parse_args()
    root = args.output_dir.resolve()
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    agents = read_csv(root / "agents.csv")
    decisions = read_csv(root / "decision_audit.csv")
    api_calls = read_csv(root / "api_call_audit.csv")
    coupon_api = read_csv(root / "coupon_api_decisions.csv")
    coupons = read_csv(root / "coupon_allocations.csv")
    edges = read_csv(root / "influence_edges.csv")
    events = read_csv(root / "traffic_state_events.csv")
    choices = read_csv(root / "mode_choices.csv")
    dispatch = read_csv(root / "ride_hailing_dispatch.csv")
    activities = read_csv(root / "activity_results.csv")

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, actual: Any, expected: Any) -> None:
        checks.append(
            {"check": name, "passed": bool(passed), "actual": actual, "expected": expected}
        )

    check("summary_status", summary["status"] == "PASS", summary["status"], "PASS")
    check("agent_count", len(agents) == 1000, len(agents), 1000)
    age_counts = Counter(row["age_group"] for row in agents)
    check(
        "age_group_counts",
        age_counts == Counter({"18-39": 400, "40-59": 330, "60+": 270}),
        dict(age_counts),
        {"18-39": 400, "40-59": 330, "60+": 270},
    )
    decision_ids = [row["decision_id"] for row in decisions]
    check(
        "travel_decision_count",
        len(decisions) == summary["travel_decisions"] == 1996,
        len(decisions),
        1996,
    )
    check(
        "unique_travel_decisions",
        len(set(decision_ids)) == len(decision_ids),
        len(set(decision_ids)),
        len(decision_ids),
    )
    check(
        "travel_api_attempted_all",
        all(truth(row["api_call_attempted"]) for row in decisions),
        sum(truth(row["api_call_attempted"]) for row in decisions),
        len(decisions),
    )
    check(
        "travel_api_succeeded_all",
        all(truth(row["api_decision_succeeded"]) for row in decisions),
        sum(truth(row["api_decision_succeeded"]) for row in decisions),
        len(decisions),
    )
    check(
        "travel_api_response_hashes",
        all(len(row["response_sha256"]) == 64 for row in decisions),
        sum(len(row["response_sha256"]) == 64 for row in decisions),
        len(decisions),
    )
    api_scope = Counter(row["scope"] for row in api_calls)
    check(
        "api_attempt_count",
        len(api_calls) == 2618,
        {"total": len(api_calls), **dict(api_scope)},
        {"total": 2618, "coupon": 622, "travel": 1996},
    )
    check(
        "api_attempt_failures",
        all(truth(row["success"]) for row in api_calls),
        sum(not truth(row["success"]) for row in api_calls),
        0,
    )
    check(
        "coupon_api_succeeded_all",
        len(coupon_api) == 622
        and all(truth(row["api_decision_succeeded"]) for row in coupon_api),
        sum(truth(row["api_decision_succeeded"]) for row in coupon_api),
        622,
    )

    ride_decisions = [row for row in decisions if row["chosen_mode"] == "ride_hailing"]
    check(
        "ride_hailing_events_match_choices",
        len(events) == len(ride_decisions) == summary["ride_hailing_traffic_events"],
        {"events": len(events), "choices": len(ride_decisions)},
        summary["ride_hailing_traffic_events"],
    )
    check(
        "traffic_event_publication_exact",
        all(
            truth(row["published_traffic_event"])
            == (row["chosen_mode"] == "ride_hailing")
            for row in decisions
        ),
        sum(truth(row["published_traffic_event"]) for row in decisions),
        len(ride_decisions),
    )
    chronological_edges = all(
        int(row["source_decision_sequence"]) < int(row["target_decision_sequence"])
        for row in edges
    )
    check("influence_edges_chronological", chronological_edges, len(edges), len(edges))
    check(
        "influence_edge_count",
        len(edges) == summary["influence_edges"],
        len(edges),
        summary["influence_edges"],
    )
    affected = sum(truth(row["affected_by_prior_agents"]) for row in decisions)
    check(
        "affected_decision_count",
        affected == summary["affected_decisions"],
        affected,
        summary["affected_decisions"],
    )

    check("coupon_allocation_count", len(coupons) == 1000, len(coupons), 1000)
    awarded = sum(truth(row["coupon_awarded"]) for row in coupons)
    check("coupon_pool_bound", awarded == 200, awarded, 200)
    check(
        "public_multiplier_creates_no_coupons",
        all(int(row["pg_coupons_created_by_multiplier"]) == 0 for row in coupons),
        sum(int(row["pg_coupons_created_by_multiplier"]) for row in coupons),
        0,
    )
    bound = [row for row in decisions if truth(row["coupon_bound_to_ride_hailing"])]
    check(
        "coupon_binding_only_ride_hailing",
        len(bound) == summary["coupon_funnel"]["bound_to_ride_hailing"]
        and all(
            row["chosen_mode"] == "ride_hailing"
            and truth(row["coupon_available_at_choice"])
            for row in bound
        ),
        len(bound),
        summary["coupon_funnel"]["bound_to_ride_hailing"],
    )
    redeemed = [row for row in choices if truth(row["coupon_redeemed"])]
    check(
        "coupon_redemption_matches_binding",
        len(redeemed) == summary["coupon_funnel"]["redeemed"]
        and all(truth(row["coupon_bound"]) for row in redeemed),
        len(redeemed),
        summary["coupon_funnel"]["redeemed"],
    )
    check(
        "ride_hailing_dispatch_success",
        len(dispatch) == summary["ride_hailing_requests"]
        and all(truth(row["succeeded"]) for row in dispatch),
        {"requests": len(dispatch), "failures": sum(not truth(row["succeeded"]) for row in dispatch)},
        {"requests": summary["ride_hailing_requests"], "failures": 0},
    )

    necessary = [row for row in activities if truth(row["is_mandatory"])]
    necessary_rate = (
        sum(truth(row["completed"]) for row in necessary) / len(necessary)
        if necessary
        else 0.0
    )
    activity_rate = sum(truth(row["completed"]) for row in activities) / len(activities)
    check(
        "activity_completion_rate",
        abs(activity_rate - float(summary["activity_completion_rate"])) < 1e-6,
        round(activity_rate, 6),
        summary["activity_completion_rate"],
    )
    check(
        "necessary_activity_completion_rate",
        abs(necessary_rate - float(summary["necessary_activity_completion_rate"])) < 1e-6,
        round(necessary_rate, 6),
        summary["necessary_activity_completion_rate"],
    )
    check(
        "a1_main_elder_version_recorded",
        summary["age_parameter_version"]["version"]
        == "A1_main_stable_elder_behavior_7d21a4f"
        and summary["age_parameter_version"][
            "w2_age_weather_exposure_multiplier_loaded"
        ]
        and not summary["age_parameter_version"][
            "strictly_comparable_to_200_age_behavior"
        ],
        summary["age_parameter_version"]["version"],
        "A1_main_stable_elder_behavior_7d21a4f",
    )
    age_parameters = summary["age_parameter_version"]
    check(
        "main_elder_parameter_values",
        float(age_parameters["age_mode_constant"]["60+"]["ride_hailing"]) == 0.3
        and float(
            age_parameters["weather_exposure_disutility"][
                "age_vulnerability_weight"
            ]["60+"]
        )
        == 1.6
        and float(
            age_parameters["conditional_fare_sensitivity"][
                "elder_exposed_necessary_multiplier"
            ]
        )
        == 0.9
        and float(
            age_parameters["age_transfer_burden"]["minutes_per_transfer_by_age"][
                "60+"
            ]
        )
        == 3.0,
        {
            "ride_hailing_constant": age_parameters["age_mode_constant"]["60+"][
                "ride_hailing"
            ],
            "w2_exposure_weight": age_parameters["weather_exposure_disutility"][
                "age_vulnerability_weight"
            ]["60+"],
            "fare_multiplier": age_parameters["conditional_fare_sensitivity"][
                "elder_exposed_necessary_multiplier"
            ],
            "transfer_burden": age_parameters["age_transfer_burden"][
                "minutes_per_transfer_by_age"
            ]["60+"],
        },
        {
            "ride_hailing_constant": 0.3,
            "w2_exposure_weight": 1.6,
            "fare_multiplier": 0.9,
            "transfer_burden": 3.0,
        },
    )
    elder_decisions = [row for row in decisions if row["age_group"] == "60+"]
    check(
        "elder_exposure_weight_audited",
        bool(elder_decisions)
        and all(
            abs(float(row["chosen_weather_exposure_age_weight"]) - 1.6) < 1e-9
            for row in elder_decisions
        ),
        sum(
            abs(float(row["chosen_weather_exposure_age_weight"]) - 1.6) < 1e-9
            for row in elder_decisions
        ),
        len(elder_decisions),
    )
    check(
        "elder_conditional_fare_sensitivity_audited",
        any(
            abs(float(row["chosen_fare_sensitivity_multiplier"]) - 0.9) < 1e-9
            for row in elder_decisions
        ),
        sorted(
            {
                float(row["chosen_fare_sensitivity_multiplier"])
                for row in elder_decisions
            }
        ),
        "contains 0.9",
    )
    elder = [row for row in agents if row["age_group"] == "60+"]
    elder_segments = Counter(
        "digital_self"
        if truth(row["digital_access"])
        else "family_proxy"
        if truth(row["family_assistance"])
        else "nondigital_unassisted"
        for row in elder
    )
    check(
        "elder_digital_segments",
        elder_segments
        == Counter({"digital_self": 130, "family_proxy": 93, "nondigital_unassisted": 47}),
        dict(elder_segments),
        {"digital_self": 130, "family_proxy": 93, "nondigital_unassisted": 47},
    )
    unassisted_ids = {
        int(row["agent_id"])
        for row in elder
        if not truth(row["digital_access"]) and not truth(row["family_assistance"])
    }
    unassisted_ride = sum(
        int(row["agent_id"]) in unassisted_ids and row["primary_mode"] == "ride_hailing"
        for row in choices
    )
    check("unassisted_elder_ride_hailing_barrier", unassisted_ride == 0, unassisted_ride, 0)
    check(
        "api_key_not_marked_persisted",
        summary["api_key_present"] and not summary["api_key_persisted"],
        {"present_during_run": summary["api_key_present"], "persisted": summary["api_key_persisted"]},
        {"present_during_run": True, "persisted": False},
    )

    report = {
        "status": "PASS" if all(row["passed"] for row in checks) else "FAIL",
        "checks_passed": sum(row["passed"] for row in checks),
        "checks_total": len(checks),
        "checks": checks,
    }
    (root / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (root / "validation_checks.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("check", "passed", "actual", "expected")
        )
        writer.writeheader()
        for row in checks:
            writer.writerow(
                {
                    **row,
                    "actual": json.dumps(row["actual"], ensure_ascii=False),
                    "expected": json.dumps(row["expected"], ensure_ascii=False),
                }
            )
    print(json.dumps(report, ensure_ascii=True))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
