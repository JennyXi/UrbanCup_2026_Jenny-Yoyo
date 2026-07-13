"""Generate a deterministic 50-Agent spatial review sample for the current nine-zone city."""

from __future__ import annotations

import csv
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.agent_population import generate_population_agents
from custom.agents.trip_planning import generate_seven_day_activity_plans
from custom.agents.leg_generation import build_time_feasible_legs
from custom.spatial.destination_assignment import (
    assign_destination_zones_with_audit,
    effective_choice_distance,
    load_destination_configuration,
)
from custom.spatial.home_zone_assignment import assign_home_zones
from custom.spatial.zone_configuration import (
    allocate_zone_age_quotas,
    derive_spatial_configuration,
    load_zone_configuration,
)
from custom.transport.network import MODES, build_transport_network, calculate_leg_mode_option
from custom.transport.time_supply import calculate_time_adjusted_leg_mode_option


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "examples" / "agents_50_new_city"
SEED = 47
WEEK_START = datetime(2026, 7, 6)


def as_dict(agent):
    return agent.to_dict() if hasattr(agent, "to_dict") else dict(vars(agent))


def write_csv(path: Path, rows):
    rows = list(rows)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_sample(agent_rows, activity_rows, legs, trajectory_rows, spatial_by_id, quotas):
    agent_ids = [row["agent_id"] for row in agent_rows]
    assert len(agent_rows) == 50 and len(set(agent_ids)) == 50
    assert set(spatial_by_id) == {f"Z{index}" for index in range(1, 10)}
    assert sum(quotas["zone_totals"].values()) == 50
    assert Counter(row["home_zone"] for row in agent_rows) == Counter(quotas["zone_totals"])
    assert sum(quotas["city_age_totals"].values()) == 50
    assert Counter(row["age_group"] for row in agent_rows) == Counter(quotas["city_age_totals"])
    elders = [row for row in agent_rows if row["is_elder"]]
    assert sum(row["smartphone_access"] for row in elders) == int(len(elders) * 0.873 + 0.5)
    assert sum(row["digital_access"] for row in elders) == int(len(elders) * 0.483 + 0.5)
    assert all(not row["digital_access"] or row["smartphone_access"] for row in elders)

    known_agents = set(agent_ids)
    agent_by_id = {row["agent_id"]: row for row in agent_rows}
    for activity in activity_rows:
        assert activity["agent_id"] in known_agents
        assert activity["home_zone"] in spatial_by_id
        assert activity["destination_zone"] in spatial_by_id
        assert activity["activity_start_time"] < activity["activity_end_time"]
        inherited = agent_by_id[activity["agent_id"]]
        for field in ("home_zone", "home_zone_name", "age_group", "work_status", "medical_need_level"):
            assert activity[field] == inherited[field]

    by_day = defaultdict(list)
    for leg in legs:
        assert leg["agent_id"] in known_agents
        assert leg["origin_zone"] in spatial_by_id
        assert leg["destination_zone"] in spatial_by_id
        assert leg["euclidean_distance_km"] >= 0
        assert leg["road_network_distance_km"] > 0
        if leg["origin_zone"] != leg["destination_zone"]:
            assert leg["road_network_distance_km"] >= leg["euclidean_distance_km"]
        assert leg["departure_time"] + __import__("datetime").timedelta(minutes=leg["travel_time_minutes"]) == leg["arrival_time"]
        by_day[(leg["agent_id"], leg["day"])].append(leg)
    homes = {row["agent_id"]: row["home_zone"] for row in agent_rows}
    for (agent_id, _), rows in by_day.items():
        rows.sort(key=lambda row: row["leg_sequence"])
        assert [row["leg_sequence"] for row in rows] == list(range(1, len(rows) + 1))
        assert rows[0]["origin_zone"] == homes[agent_id]
        assert rows[-1]["destination_zone"] == homes[agent_id]
        assert rows[-1]["leg_role"] == "return_home"
        for previous, current in zip(rows, rows[1:]):
            assert previous["destination_zone"] == current["origin_zone"]

    fixed = defaultdict(lambda: defaultdict(set))
    for activity in activity_rows:
        purpose = activity["activity_purpose"]
        if purpose == "work":
            fixed[activity["agent_id"]]["work"].add(activity["destination_zone"])
        elif purpose == "medical":
            fixed[activity["agent_id"]]["medical"].add(activity["destination_zone"])
        elif purpose in {"visit", "out_of_home_family_care", "out_of_home_family_activity"}:
            fixed[activity["agent_id"]]["family_observed"].add(activity["destination_zone"])
    assert all(len(groups.get(group, ())) <= 1 for groups in fixed.values() for group in ("work", "medical"))
    assert len(trajectory_rows) == 50 and {row["agent_id"] for row in trajectory_rows} == known_agents

    z9 = spatial_by_id["Z9"]
    z6 = spatial_by_id["Z6"]
    euclidean = ((z9["centroid_x"] - z6["centroid_x"]) ** 2 +
                 (z9["centroid_y"] - z6["centroid_y"]) ** 2) ** 0.5
    assert effective_choice_distance("Z9", "Z6", spatial_by_id) > euclidean

    identity_mismatch_count = 0
    invalid_interval_count = 0
    non_work_under_30m_count = 0
    legacy_purpose_count = 0
    shopping_hours_violation_count = 0
    intervals = defaultdict(list)
    for activity in activity_rows:
        inherited = agent_by_id[activity["agent_id"]]
        identity_mismatch_count += any(
            activity[field] != inherited[field]
            for field in ("home_zone", "home_zone_name", "age_group", "work_status", "medical_need_level")
        )
        start = activity["activity_start_time"]
        end = activity["activity_end_time"]
        duration = end - start
        invalid_interval_count += end <= start or start.date() != end.date()
        non_work_under_30m_count += activity["activity_purpose"] != "work" and duration < timedelta(minutes=30)
        legacy_purpose_count += activity["activity_purpose"] in {"social", "leisure"}
        shopping_hours_violation_count += (
            activity["activity_purpose"] == "shopping"
            and (start.time() < datetime.min.time().replace(hour=10) or end.time() > datetime.min.time().replace(hour=22))
        )
        intervals[(activity["agent_id"], start.date())].append((start, end))

    overlap_count = 0
    for rows in intervals.values():
        rows.sort()
        overlap_count += sum(current[0] < previous[1] for previous, current in zip(rows, rows[1:]))

    leg_time_identity_violation_count = sum(
        leg["departure_time"] + timedelta(minutes=leg["travel_time_minutes"]) != leg["arrival_time"]
        for leg in legs
    )
    age_deadline_minutes = {"18-39": 24 * 60, "40-59": 22 * 60, "60+": 20 * 60}
    home_arrival_deadline_violation_count = 0
    for leg in legs:
        if leg["leg_role"] != "return_home":
            continue
        day_start = datetime.combine(datetime.fromisoformat(leg["date"]).date(), datetime.min.time())
        arrival_minutes = int((leg["arrival_time"] - day_start).total_seconds() / 60)
        home_arrival_deadline_violation_count += arrival_minutes > age_deadline_minutes[agent_by_id[leg["agent_id"]]["age_group"]]

    checks = {
        "agent_activity_identity": identity_mismatch_count,
        "activity_end_after_start_same_day": invalid_interval_count,
        "non_work_duration_at_least_30m": non_work_under_30m_count,
        "no_legacy_social_or_leisure": legacy_purpose_count,
        "activity_non_overlap": overlap_count,
        "shopping_within_10_22": shopping_hours_violation_count,
        "leg_time_identity": leg_time_identity_violation_count,
        "age_specific_home_arrival_deadline": home_arrival_deadline_violation_count,
    }
    validation = {
        "all_passed": all(count == 0 for count in checks.values()),
        "checks": {
            name: {"passed": count == 0, "violation_count": int(count)}
            for name, count in checks.items()
        },
    }
    assert validation["all_passed"], validation
    return validation


def main(output_dir=DEFAULT_OUTPUT_DIR):
    spatial = derive_spatial_configuration(load_zone_configuration())
    spatial_by_id = {zone["zone_id"]: zone for zone in spatial["zones"]}
    quotas = allocate_zone_age_quotas(spatial, total_agents=50)
    population = generate_population_agents(total_agents=50, seed=SEED)
    agents = assign_home_zones(population, quotas["quota_matrix"], seed=SEED)
    baseline = generate_seven_day_activity_plans(agents, WEEK_START, SEED)
    destination_result = assign_destination_zones_with_audit(
        agents,
        baseline,
        spatial,
        load_destination_configuration(),
        SEED,
    )
    activities = destination_result["activities"]

    agent_rows = []
    for agent in agents:
        row = as_dict(agent)
        zone = spatial_by_id[row["home_zone"]]
        row["home_zone_name"] = zone["display_name"]
        agent_rows.append(row)

    timed = build_time_feasible_legs(agents, activities, spatial_by_id)
    activities = timed["activities"]
    agent_by_id = {row["agent_id"]: row for row in agent_rows}
    activity_rows = []
    for activity in activities:
        copied = dict(activity)
        inherited = agent_by_id[copied["agent_id"]]
        for field in ("home_zone", "age_group", "work_status", "medical_need_level"):
            copied[field] = inherited[field]
        copied["home_zone_name"] = inherited["home_zone_name"]
        copied["destination_zone_name"] = spatial_by_id[copied["destination_zone"]]["display_name"]
        copied["activity_start_time"] = copied.pop("planned_start_datetime")
        copied["activity_end_time"] = copied.pop("planned_end_datetime")
        activity_rows.append(copied)

    grouped = defaultdict(list)
    for activity in activity_rows:
        grouped[(activity["agent_id"], activity["activity_start_time"].date())].append(activity)

    legs = []
    for raw_leg in timed["legs"]:
        leg = dict(raw_leg)
        leg["origin_zone_name"] = spatial_by_id[leg["origin_zone"]]["display_name"]
        leg["destination_zone_name"] = spatial_by_id[leg["destination_zone"]]["display_name"]
        legs.append(leg)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "agents.csv", agent_rows)
    write_csv(output_dir / "activities.csv", activity_rows)
    write_csv(output_dir / "legs.csv", legs)
    network = build_transport_network()
    leg_mode_rows = []
    time_supply_rows = []
    for leg in legs:
        for mode in MODES:
            identifiers = {
                "leg_id": leg["leg_id"],
                "agent_id": leg["agent_id"],
                "activity_id": leg["activity_id"],
                "purpose": leg["purpose"],
                "leg_role": leg["leg_role"],
            }
            leg_mode_rows.append({
                **identifiers,
                **calculate_leg_mode_option(network, leg, mode, seed=SEED),
            })
            time_supply_rows.append({
                **identifiers,
                "departure_time": leg["departure_time"],
                **calculate_time_adjusted_leg_mode_option(network, leg, mode, seed=SEED),
            })
    write_csv(output_dir / "leg_mode_options.csv", leg_mode_rows)
    write_csv(output_dir / "leg_mode_time_supply.csv", time_supply_rows)

    review_day = WEEK_START.date()
    monday_activities = defaultdict(list)
    for row in activity_rows:
        start = row["activity_start_time"]
        if start.date() == review_day:
            monday_activities[row["agent_id"]].append(row)
    trajectory_rows = []
    for agent in agent_rows:
        agent_id = agent["agent_id"]
        home = agent["home_zone"]
        rows = sorted(
            monday_activities.get(agent_id, []),
            key=lambda row: (row["activity_start_time"], row["sequence_order"]),
        )
        stops = [f"HOME@{home}"]
        for row in rows:
            start = row["activity_start_time"].strftime("%H:%M")
            end = row["activity_end_time"].strftime("%H:%M")
            stops.append(f"{start}-{end} {row['activity_purpose']}@{row['destination_zone']}")
        stops.append(f"HOME@{home}")
        trajectory_rows.append({
            "agent_id": agent_id,
            "age_group": agent["age_group"],
            "work_status": agent["work_status"],
            "medical_need_level": agent["medical_need_level"],
            "home_zone": home,
            "home_zone_name": agent["home_zone_name"],
            "modeled_activity_count": len(rows),
            "day_status": "modeled_travel" if rows else "no_in_scope_trip_or_home_day",
            "trajectory": " -> ".join(stops) if rows else f"HOME@{home} (全天无建模出行)",
        })
    write_csv(output_dir / "monday_trajectories.csv", trajectory_rows)
    validation = validate_sample(agent_rows, activity_rows, legs, trajectory_rows, spatial_by_id, quotas)
    with (output_dir / "validation.json").open("w", encoding="utf-8") as stream:
        json.dump(validation, stream, ensure_ascii=False, indent=2)

    home_counts = Counter(row["home_zone"] for row in agent_rows)
    age_home = defaultdict(Counter)
    for row in agent_rows:
        age_home[row["age_group"]][row["home_zone"]] += 1
    purpose_dest = defaultdict(Counter)
    purpose_same_zone = defaultdict(Counter)
    for row in activity_rows:
        purpose_dest[row["activity_purpose"]][row["destination_zone"]] += 1
        purpose_same_zone[row["activity_purpose"]]["total"] += 1
        purpose_same_zone[row["activity_purpose"]]["same_zone"] += row["home_zone"] == row["destination_zone"]
    od = Counter((row["origin_zone"], row["destination_zone"]) for row in legs)
    distances = [row["road_network_distance_km"] for row in legs]
    elders = [row for row in agent_rows if row["is_elder"]]
    work_activities = [row for row in activity_rows if row["activity_purpose"] == "work"]
    departures = [row["departure_time"] for row in legs]
    overlap_count = 0
    intervals = defaultdict(list)
    for row in activity_rows:
        intervals[row["agent_id"]].append((row["activity_start_time"], row["activity_end_time"]))
    for rows in intervals.values():
        rows.sort()
        overlap_count += sum(current[0] < previous[1] for previous, current in zip(rows, rows[1:]))
    age_by_agent = {row["agent_id"]: row["age_group"] for row in agent_rows}
    home_arrivals = defaultdict(list)
    for leg in legs:
        if leg["leg_role"] == "return_home":
            day_start = datetime.combine(datetime.fromisoformat(leg["date"]).date(), datetime.min.time())
            minutes_after_day_start = int((leg["arrival_time"] - day_start).total_seconds() / 60)
            home_arrivals[age_by_agent[leg["agent_id"]]].append((minutes_after_day_start, leg["arrival_time"]))
    summary = {
        "seed": SEED,
        "agent_count": len(agent_rows),
        "activity_count": len(activity_rows),
        "leg_count": len(legs),
        "leg_mode_option_count": len(leg_mode_rows),
        "time_supply_option_count": len(time_supply_rows),
        "time_supply_operating_count": sum(row["operating"] for row in time_supply_rows),
        "available_intrazonal_metro_option_count": sum(
            row["available"] and row["mode"] == "metro" and row["origin_zone"] == row["destination_zone"]
            for row in leg_mode_rows
        ),
        "family_destination_reuse": destination_result["selection_audit"]["family_destination_reuse"],
        "monday_modeled_traveler_count": sum(row["modeled_activity_count"] > 0 for row in trajectory_rows),
        "elder_access": {
            "elder_count": len(elders),
            "smartphone_count": sum(row["smartphone_access"] for row in elders),
            "digital_access_count": sum(row["digital_access"] for row in elders),
        },
        "home_zone_counts": dict(sorted(home_counts.items())),
        "age_by_home_zone": {age: dict(sorted(counts.items())) for age, counts in sorted(age_home.items())},
        "purpose_destination_counts": {purpose: dict(sorted(counts.items())) for purpose, counts in sorted(purpose_dest.items())},
        "same_zone_activity_share": {
            purpose: {
                "same_zone_count": counts["same_zone"],
                "total_count": counts["total"],
                "share": round(counts["same_zone"] / counts["total"], 3),
            }
            for purpose, counts in sorted(purpose_same_zone.items())
        },
        "top_20_od": [
            {"origin": origin, "destination": destination, "count": count}
            for (origin, destination), count in od.most_common(20)
        ],
        "road_network_distance_km": {
            "mean": round(sum(distances) / len(distances), 3),
            "maximum": round(max(distances), 3),
            "over_20_count": sum(value > 20 for value in distances),
            "over_30_count": sum(value > 30 for value in distances),
        },
        "time_audit": {
            "earliest_work_arrival": min((row["activity_start_time"] for row in work_activities), key=lambda value: value.time()).strftime("%H:%M"),
            "latest_work_arrival": max((row["activity_start_time"] for row in work_activities), key=lambda value: value.time()).strftime("%H:%M"),
            "earliest_departure": min(departures, key=lambda value: value.time()).strftime("%H:%M"),
            "latest_departure": max(departures, key=lambda value: value.time()).strftime("%H:%M"),
            "earliest_work_end": min((row["activity_end_time"] for row in work_activities), key=lambda value: value.time()).strftime("%H:%M"),
            "latest_work_end": max((row["activity_end_time"] for row in work_activities), key=lambda value: value.time()).strftime("%H:%M"),
            "latest_home_arrival_by_age": {
                age: max(values, key=lambda value: value[0])[1].isoformat(sep=" ")
                for age, values in sorted(home_arrivals.items())
            },
            "activity_overlap_count": overlap_count,
        },
        "leg_status": "time-feasible leg chain derived from activity arrival/end times and deterministic travel times",
        "validation": validation,
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)

    markdown = [
        "# 50 Agent 新九区空间样例", "",
        f"- Seed：{SEED}",
        f"- Agent：{len(agent_rows)}",
        f"- Activities：{len(activity_rows)}",
        f"- Review legs：{len(legs)}", "",
        f"- 周一有建模出行的 Agent：{summary['monday_modeled_traveler_count']} / 50", "",
        f"- 老年人智能手机拥有：{summary['elder_access']['smartphone_count']} / {summary['elder_access']['elder_count']}",
        f"- 老年人数字接入：{summary['elder_access']['digital_access_count']} / {summary['elder_access']['elder_count']}", "",
        "## Home-zone 人口", "",
        "| Zone | 人数 |", "|---|---:|",
        *[f"| {zone} | {home_counts.get(zone, 0)} |" for zone in spatial_by_id], "",
        "## 距离", "",
        "- `euclidean_distance_km`：跨区质心直线距离；同区为0。",
        "- `road_network_distance_km`：跨区为沿connected_to道路图累计的最短路径距离（每条边应用绕行系数）；同区为按活动地点对抽样的合成道路距离。",
        "- `leg_mode_options.csv`中的`network_distance_km = main_network_distance_km + access_distance_km`。", "",
        "- `leg_mode_time_supply.csv`保留静态方案，并按具体departure_time增加正常天气分时供给、末班车和调整后总时间；不包含Agent方式选择。", "",
        "> 如目录中仍存在 `legs_review_draft.csv`，它是旧版人工检查草稿，不属于当前生成流程；其中旧距离字段已弃用。", "",
        f"- 平均合成道路距离：{summary['road_network_distance_km']['mean']} km",
        f"- 最大合成道路距离：{summary['road_network_distance_km']['maximum']} km",
        f"- 超过20 km：{summary['road_network_distance_km']['over_20_count']} legs",
        f"- 超过30 km：{summary['road_network_distance_km']['over_30_count']} legs", "",
        "> legs.csv 使用活动到达/结束时间与确定性旅行时间生成，并通过逐段时间恒等式检查。", "",
        "## 周一一日轨迹", "",
        "| Agent | 年龄 | Home | 轨迹 |", "|---|---|---|---|",
        *[f"| {row['agent_id']} | {row['age_group']} | {row['home_zone']} | {row['trajectory']} |" for row in trajectory_rows], "",
    ]
    (output_dir / "README.md").write_text("\n".join(markdown), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    main(args.output_dir)
