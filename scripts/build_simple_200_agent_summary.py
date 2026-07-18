"""Build compact, Git-friendly summaries for the 50- and 200-agent testbeds."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


POLICY_METRICS = (
    "ride_hailing_requests", "failed_ride_hailing_requests",
    "mean_ride_hailing_wait_minutes_per_request", "road_vehicle_volume",
    "mean_volume_capacity_ratio", "necessary_activity_completion_rate",
    "mean_total_travel_time", "total_heat_risk_burden",
    "coupon_redeemed", "coupon_induced_requests",
)
COVERAGE_METRICS = (
    "coupon_pool", "coupon_coverage_rate", "ride_hailing_requests",
    "failed_ride_hailing_requests", "mean_ride_hailing_wait_minutes_per_request",
    "road_vehicle_volume", "mean_volume_capacity_ratio", "mean_total_travel_time",
    "total_travel_time_minutes", "necessary_activity_completion_rate",
)
PRIORITY_METRICS = (
    "ride_hailing_requests", "successful_ride_hailing_requests",
    "failed_ride_hailing_requests", "mean_ride_hailing_wait_minutes",
    "transport_unmet", "necessary_activity_completion_rate",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _mean(rows: Iterable[Mapping[str, Any]], metric: str) -> float:
    return statistics.mean(float(row[metric]) for row in rows)


def _build_policy_summary(coupon_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    policy_summary = []
    for policy in ("C0_no_coupon", "C1_public_limited", "C2_elder_limited", "C3_mixed"):
        selected = [row for row in coupon_rows if row["policy"] == policy]
        policy_summary.append({
            "policy": policy, "seed_count": len({row["seed"] for row in selected}),
            **{metric: round(_mean(selected, metric), 6) for metric in POLICY_METRICS},
        })
    return policy_summary


def build_compact_summary(root: Path, output: Path) -> dict[str, list[dict[str, Any]]]:
    coupon_rows = _read(root / "outputs" / "coupon_competition_200_agents_30" / "system_per_seed.csv")
    policy_summary = _build_policy_summary(coupon_rows)

    coverage_source = _read(
        root / "outputs" / "coupon_coverage_threshold_200_agents_smoke_3"
        / "coverage_overall_summary.csv"
    )
    coverage_summary = [{metric: row[metric] for metric in COVERAGE_METRICS}
                        for row in coverage_source]

    priority_rows = _read(
        root / "outputs" / "elder_dispatch_priority_200_agents_smoke_3"
        / "priority_group_per_seed.csv"
    )
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in priority_rows:
        grouped[(row["policy"], row["group"])].append(row)
    priority_summary = []
    for policy in ("R0_first_come", "R1_elder_medical_priority", "R2_all_elder_priority"):
        for group in (
            "18-39", "40-59", "60+_digital",
            "60+_nondigital_assisted", "60+_nondigital_unassisted",
        ):
            selected = grouped[(policy, group)]
            priority_summary.append({
                "policy": policy, "group": group,
                "seed_count": len({row["seed"] for row in selected}),
                **{metric: round(_mean(selected, metric), 6) for metric in PRIORITY_METRICS},
            })

    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "policy_summary": policy_summary,
        "coupon_coverage_summary": coverage_summary,
        "dispatch_priority_summary": priority_summary,
    }
    for name, rows in tables.items():
        _write(output / f"{name}.csv", rows)
    return tables


def build_repository_results(root: Path, output: Path) -> dict[str, int]:
    """Publish small summaries while keeping large per-agent outputs local."""
    source_by_size = {
        "50_agent": root / "outputs" / "coupon_competition_50_agents_30",
        "200_agent": root / "outputs" / "coupon_competition_200_agents_30",
    }
    row_counts: dict[str, int] = {}
    for label, source in source_by_size.items():
        target = output / label
        target.mkdir(parents=True, exist_ok=True)
        rows = _build_policy_summary(_read(source / "system_per_seed.csv"))
        _write(target / "policy_summary.csv", rows)
        row_counts[f"{label}/policy_summary.csv"] = len(rows)
        with (source / "experiment_metadata.json").open(encoding="utf-8-sig") as handle:
            metadata = json.load(handle)
        with (target / "experiment_metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    extra_200 = build_compact_summary(root, output / "200_agent")
    for name, rows in extra_200.items():
        row_counts[f"200_agent/{name}.csv"] = len(rows)
    return row_counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("outputs/simple_200_agent_summary"))
    parser.add_argument(
        "--repository-output", type=Path,
        help="Also write separated 50/200-agent summaries suitable for version control.",
    )
    args = parser.parse_args()
    tables = build_compact_summary(args.root.resolve(), args.output)
    print("Compact 200-agent summary created:")
    for name, rows in tables.items():
        print(f"  {name}.csv: {len(rows)} rows")
    print(f"Output: {args.output.resolve()}")
    if args.repository_output:
        published = build_repository_results(args.root.resolve(), args.repository_output)
        print("Separated repository summaries created:")
        for name, count in published.items():
            print(f"  {name}: {count} rows")
        print(f"Repository output: {args.repository_output.resolve()}")


if __name__ == "__main__":
    main()
