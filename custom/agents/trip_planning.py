"""Deterministic Monday-Sunday baseline activity generation.

This activity-only layer reads an assigned ``home_zone``. It does not assign
destinations or create origins, legs, OD pairs, distances, modes, weather
responses, subsidies, prices, dispatch outcomes, or congestion.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from custom.agents.agent_population import (
    FLEXIBLE_NON_WORKER_SHARES,
    MEDICAL_NEED_LEVEL_SHARES,
    PART_TIME_WORKER_SHARE,
)


AGE_GROUPS = ("18-39", "40-59", "60+")
VALID_HOME_ZONES = tuple(f"Z{index}" for index in range(1, 10))
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MANDATORY_PURPOSES = {"work", "medical"}
BASELINE_CANCEL_PROBABILITY = {
    "work": 0.03,
    "medical": 0.01,
    "out_of_home_family_care": 0.08,
    "out_of_home_family_activity": 0.12,
    "shopping": 0.15,
    "visit": 0.16,
    "social_leisure": 0.24,
}
MEDICAL_WEEKLY_COUNT_OPTIONS = {
    "low": (0, 1),
    "standard": (0, 1, 2),
    "high": (1, 2, 3),
}
# Scenario assumptions for an additional weekend medical need. These preserve
# the existing weekday medical schedule used by completed workday experiments.
ELDER_WEEKEND_MEDICAL_PROBABILITY = {
    "low": 0.01,
    "standard": 0.03,
    "high": 0.06,
}

# Total modeled out-of-home activities per day. Work and scheduled elder
# medical visits count toward these totals. Weekends are more active; age
# changes probabilities rather than forbidding otherwise reasonable purposes.
DAILY_ACTIVITY_COUNT_OPTIONS = {
    "weekday": {
        "18-39": ((0, 0.15), (1, 0.55), (2, 0.25), (3, 0.05)),
        "40-59": ((0, 0.25), (1, 0.55), (2, 0.17), (3, 0.03)),
        "60+": ((0, 0.45), (1, 0.43), (2, 0.10), (3, 0.02)),
    },
    "weekend": {
        "18-39": ((0, 0.10), (1, 0.28), (2, 0.34), (3, 0.23), (4, 0.05)),
        "40-59": ((0, 0.18), (1, 0.36), (2, 0.29), (3, 0.14), (4, 0.03)),
        "60+": ((0, 0.32), (1, 0.42), (2, 0.20), (3, 0.05), (4, 0.01)),
    },
}

OPTIONAL_PURPOSE_PROBABILITIES = {
    "18-39": (
        ("shopping", 0.25), ("social_leisure", 0.40), ("visit", 0.16),
        ("out_of_home_family_activity", 0.09),
        ("out_of_home_family_care", 0.04), ("medical", 0.06),
    ),
    "40-59": (
        ("shopping", 0.23), ("social_leisure", 0.22), ("visit", 0.17),
        ("out_of_home_family_activity", 0.17),
        ("out_of_home_family_care", 0.11), ("medical", 0.10),
    ),
    # Elder medical frequency continues to come from medical_need_level;
    # all optional daily-life purposes remain possible.
    "60+": (
        ("shopping", 0.28), ("social_leisure", 0.20), ("visit", 0.24),
        ("out_of_home_family_activity", 0.20),
        ("out_of_home_family_care", 0.08),
    ),
}
OUTPUT_FIELDS = (
    "agent_id", "age_group", "work_status", "medical_need_level", "day_of_week",
    "is_weekend", "activity_id", "activity_sequence", "sequence_order", "activity_purpose", "home_zone",
    "destination_zone", "planned_start_datetime", "planned_end_datetime", "is_mandatory",
    "baseline_cancel_probability",
)

# Purpose-specific discrete duration distributions (minutes, probability).
# All durations are on the model's 30-minute grid and last at least 30 minutes.
# Long social, visiting, and family activities may exceed eight hours.
NON_WORK_DURATION_OPTIONS = {
    "shopping": ((30, 0.30), (60, 0.40), (90, 0.20), (120, 0.10)),
    "medical": ((60, 0.15), (90, 0.25), (120, 0.25), (180, 0.20), (240, 0.15)),
    "social_leisure": ((60, 0.10), (120, 0.24), (180, 0.22), (240, 0.18), (360, 0.10), (480, 0.08), (600, 0.05), (720, 0.03)),
    "visit": ((60, 0.10), (120, 0.24), (180, 0.22), (240, 0.18), (360, 0.10), (480, 0.08), (600, 0.05), (720, 0.03)),
    "out_of_home_family_care": ((60, 0.08), (120, 0.20), (180, 0.22), (240, 0.18), (360, 0.14), (480, 0.08), (600, 0.06), (720, 0.04)),
    "out_of_home_family_activity": ((60, 0.12), (120, 0.25), (180, 0.22), (240, 0.17), (360, 0.12), (480, 0.08), (600, 0.04)),
}


def _read_agent(agent: Any, field_name: str) -> Any:
    if isinstance(agent, dict):
        if field_name not in agent:
            raise ValueError(f"Agent missing required field: {field_name}")
        return agent[field_name]
    if not hasattr(agent, field_name):
        raise ValueError(f"Agent missing required field: {field_name}")
    return getattr(agent, field_name)


def _read_optional_agent(agent: Any, field_name: str) -> Any:
    return agent.get(field_name) if isinstance(agent, dict) else getattr(agent, field_name, None)


def _validate_week_start(simulation_week_start: datetime) -> None:
    if not isinstance(simulation_week_start, datetime):
        raise ValueError("simulation_week_start must be a datetime")
    if simulation_week_start.weekday() != 0 or simulation_week_start.time() != time(0, 0):
        raise ValueError(
            "simulation_week_start must be Monday 00:00; "
            f"received {simulation_week_start.isoformat()} "
            f"({simulation_week_start.strftime('%A')})"
        )


def _stable_seed(random_seed: Any, agent_id: Any, namespace: str = "plan") -> int:
    if random_seed is None or isinstance(random_seed, (dict, list, set)):
        raise ValueError("random_seed must be a stable scalar value")
    if agent_id is None:
        raise ValueError("agent_id must not be None")
    key = f"{random_seed}|{type(agent_id).__name__}:{agent_id!r}|T5B-{namespace}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def _weighted_choice(rng: random.Random, choices: Sequence[Tuple[str, float]]) -> str:
    purposes, weights = zip(*choices)
    if any(isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("activity weights must be finite non-negative numbers")
    if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("activity weights must sum to 1")
    return rng.choices(purposes, weights=weights, k=1)[0]


def _sample_half_hour_time(rng: random.Random, start: time, end: time) -> time:
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    choices = list(range(start_minutes, end_minutes + 1, 30))
    chosen = rng.choice(choices)
    return time(chosen // 60, chosen % 60)


def _add_minutes(value: time, minutes: int) -> time:
    total = value.hour * 60 + value.minute + minutes
    if total >= 24 * 60:
        raise ValueError("Activity may not cross midnight in T5B")
    return time(total // 60, total % 60)


def _derive_statuses(agent: Any, age_group: str, random_seed: Any, agent_id: Any) -> Tuple[str, str | None]:
    work_status = _read_optional_agent(agent, "work_status")
    medical_need_level = _read_optional_agent(agent, "medical_need_level")
    if age_group in {"18-39", "40-59"}:
        allowed = {"regular_worker", "flexible_non_worker"}
        if work_status is None:
            raise ValueError("work_status must be inherited from Agent; activity generation cannot sample it")
        if work_status not in allowed:
            raise ValueError(f"Invalid work_status for {age_group}: {work_status}")
        if medical_need_level is not None:
            raise ValueError("medical_need_level must be None for non-elder agents")
        if work_status == "flexible_non_worker":
            if not bool(_read_optional_agent(agent, "digital_access")):
                raise ValueError("flexible_non_worker must retain digital_access")
            if _read_optional_agent(agent, "independent_ride_hailing") is False:
                raise ValueError("flexible_non_worker cannot have lower independent ride-hailing ability")
        return work_status, None
    if work_status is None:
        raise ValueError("work_status must be inherited from Agent; activity generation cannot sample it")
    if work_status not in {"retired", "part_time_worker"}:
        raise ValueError(f"Invalid elder work_status: {work_status}")
    if medical_need_level is None:
        raise ValueError("medical_need_level must be inherited from elder Agent")
    if medical_need_level not in {"low", "standard", "high"}:
        raise ValueError(f"Invalid medical_need_level: {medical_need_level}")
    return work_status, medical_need_level


def _nonconsecutive_days(rng: random.Random, count: int) -> List[int]:
    candidates = [days for days in __import__("itertools").combinations(range(5), count) if all(b - a > 1 for a, b in zip(days, days[1:]))]
    return list(rng.choice(candidates))


def _elder_weekday_schedule(rng: random.Random, work_status: str, medical_need_level: str) -> Dict[int, str]:
    medical_count = rng.choice(MEDICAL_WEEKLY_COUNT_OPTIONS[medical_need_level])
    if medical_need_level == "high" and medical_count == 3:
        start = rng.choice((0, 1, 2))
        medical_days = [start, start + 1, start + 2]
    else:
        medical_days = _nonconsecutive_days(rng, medical_count)
    schedule = {day: "medical" for day in medical_days}
    if work_status == "part_time_worker":
        available = [day for day in range(5) if day not in schedule]
        work_count = min(rng.choice((1, 2)), len(available))
        for day in sorted(rng.sample(available, work_count)):
            schedule[day] = "work"
    return schedule


def _sample_duration(rng: random.Random, purpose: str) -> int:
    options = NON_WORK_DURATION_OPTIONS[purpose]
    return rng.choices([item[0] for item in options], weights=[item[1] for item in options], k=1)[0]


def _sample_activity_count(rng: random.Random, age_group: str, weekend: bool) -> int:
    options = DAILY_ACTIVITY_COUNT_OPTIONS["weekend" if weekend else "weekday"][age_group]
    return rng.choices([item[0] for item in options], weights=[item[1] for item in options], k=1)[0]


def _clock_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _schedule_optional_templates(
    rng: random.Random,
    age_group: str,
    count: int,
    occupied: Sequence[Tuple[str, time, time]],
    *,
    weekend: bool,
    after_work_only: bool = False,
) -> List[Tuple[str, time, time]]:
    """Place optional activities on the 30-minute grid without overlap.

    A conservative 90-minute planning buffer is left around activities so the
    later destination draw can realize even the model's extreme cross-zone
    travel time without breaking a fixed arrival or the home deadline.
    """
    if count <= 0:
        return []
    day_end = {"18-39": 22 * 60 + 30, "40-59": 20 * 60 + 30, "60+": 18 * 60 + 30}[age_group]
    earliest = 9 * 60
    work_rows = [row for row in occupied if row[0] == "work"]
    if after_work_only and work_rows:
        earliest = max(earliest, _clock_minutes(work_rows[0][2]) + 90)

    placed = list(occupied)
    optional = []
    for _ in range(count):
        purpose = _weighted_choice(rng, OPTIONAL_PURPOSE_PROBABILITIES[age_group])
        candidates_by_duration = []
        for duration, probability in NON_WORK_DURATION_OPTIONS[purpose]:
            starts = []
            for start in range(earliest, day_end - duration + 1, 30):
                end = start + duration
                if purpose == "shopping" and (start < 10 * 60 or end > 22 * 60):
                    continue
                if purpose == "medical" and end > 20 * 60:
                    continue
                conflicts = any(
                    start < _clock_minutes(existing_end) + 90
                    and end + 90 > _clock_minutes(existing_start)
                    for _, existing_start, existing_end in placed
                )
                if not conflicts:
                    starts.append(start)
            if starts:
                candidates_by_duration.append((duration, probability, starts))
        if not candidates_by_duration:
            continue
        duration_row = rng.choices(
            candidates_by_duration,
            weights=[row[1] for row in candidates_by_duration],
            k=1,
        )[0]
        duration, _, starts = duration_row
        start_weights = []
        for start in starts:
            weight = 1.0
            if purpose == "social_leisure" and not weekend:
                if age_group == "18-39" and start >= 18 * 60:
                    weight = 3.0
                elif age_group == "40-59" and start >= 18 * 60:
                    weight = 1.8
            if age_group == "60+" and start <= 16 * 60:
                weight *= 1.5
            start_weights.append(weight)
        start = rng.choices(starts, weights=start_weights, k=1)[0]
        row = (purpose, time(start // 60, start % 60), time((start + duration) // 60, (start + duration) % 60))
        placed.append(row)
        optional.append(row)
    return optional


def _sample_company_schedule(rng: random.Random, work_status: str) -> Tuple[time, time] | None:
    if work_status not in {"regular_worker", "part_time_worker"}:
        return None
    if work_status == "regular_worker":
        start = _sample_half_hour_time(rng, time(8, 0), time(10, 30))
        duration = rng.choice((480, 510, 540, 570, 600))
    else:
        start = _sample_half_hour_time(rng, time(10, 0), time(10, 30))
        duration = rng.choice((390, 420, 450))
    end = _add_minutes(start, duration)
    if end < time(17, 0):
        end = time(17, 0)
    if end > time(21, 30):
        end = time(21, 30)
    return start, end


def _weekday_templates(age_group: str, work_status: str, elder_schedule: Dict[int, str], day_index: int, rng: random.Random, company_schedule: Tuple[time, time] | None) -> List[Tuple[str, time, time]]:
    activities: List[Tuple[str, time, time]] = []
    if age_group in {"18-39", "40-59"} and work_status == "regular_worker":
        start, end = company_schedule
        activities.append(("work", start, end))
    elif age_group == "60+":
        fixed_purpose = elder_schedule.get(day_index)
        if fixed_purpose == "work":
            start, end = company_schedule
            activities.append((fixed_purpose, start, end))
        elif fixed_purpose == "medical":
            start = _sample_half_hour_time(rng, time(9, 0), time(14, 0))
            activities.append((fixed_purpose, start, _add_minutes(start, _sample_duration(rng, fixed_purpose))))

    desired_total = max(len(activities), _sample_activity_count(rng, age_group, weekend=False))
    activities.extend(_schedule_optional_templates(
        rng,
        age_group,
        desired_total - len(activities),
        activities,
        weekend=False,
        after_work_only=any(row[0] == "work" for row in activities),
    ))
    return sorted(activities, key=lambda item: (item[1], item[2], item[0]))


def _weekend_templates(
    age_group: str, rng: random.Random, medical_need_level: str | None = None,
) -> List[Tuple[str, time, time]]:
    activities: List[Tuple[str, time, time]] = []
    weekend_medical = (
        age_group == "60+"
        and medical_need_level is not None
        and rng.random()
        < ELDER_WEEKEND_MEDICAL_PROBABILITY[medical_need_level]
    )
    if weekend_medical:
        start = _sample_half_hour_time(rng, time(9, 0), time(14, 0))
        activities.append(
            ("medical", start, _add_minutes(start, _sample_duration(rng, "medical")))
        )
    count = max(len(activities), _sample_activity_count(rng, age_group, weekend=True))
    activities.extend(
        _schedule_optional_templates(
            rng,
            age_group,
            count - len(activities),
            activities,
            weekend=True,
        )
    )
    return sorted(
        activities,
        key=lambda item: (item[1], item[2], item[0]),
    )


def _make_activity(*, agent_id: Any, age_group: str, work_status: str, medical_need_level: str | None, home_zone: str, day_index: int, day_date, sequence: int, sequence_order: int, purpose: str, start_time: time, end_time: time) -> Dict[str, Any]:
    start_datetime = datetime.combine(day_date, start_time)
    end_datetime = datetime.combine(day_date, end_time)
    return {
        "agent_id": agent_id,
        "age_group": age_group,
        "work_status": work_status,
        "medical_need_level": medical_need_level,
        "day_of_week": DAY_NAMES[day_index],
        "is_weekend": day_index >= 5,
        "activity_id": f"activity-{agent_id}-{day_date.isoformat()}-{sequence:03d}",
        "activity_sequence": sequence,
        "sequence_order": sequence_order,
        "activity_purpose": purpose,
        "home_zone": home_zone,
        "destination_zone": None,
        "planned_start_datetime": start_datetime,
        "planned_end_datetime": end_datetime,
        "is_mandatory": purpose in MANDATORY_PURPOSES,
        "baseline_cancel_probability": BASELINE_CANCEL_PROBABILITY[purpose],
    }


def generate_weekly_activity_plan_with_audit(agent: Any, simulation_week_start: datetime, random_seed: Any) -> Dict[str, Any]:
    """Generate one Agent's activities plus explicit candidate-slot audit counts."""
    _validate_week_start(simulation_week_start)
    agent_id = _read_agent(agent, "agent_id")
    age_group = _read_agent(agent, "age_group")
    home_zone = _read_agent(agent, "home_zone")
    if age_group not in AGE_GROUPS:
        raise ValueError(f"Unsupported age_group: {age_group}")
    if home_zone is None:
        raise ValueError(f"Agent {agent_id} must have home_zone before activity generation")
    if home_zone not in VALID_HOME_ZONES:
        raise ValueError(f"Agent {agent_id} has invalid home_zone: {home_zone}")
    work_status, medical_need_level = _derive_statuses(agent, age_group, random_seed, agent_id)
    rng = random.Random(_stable_seed(random_seed, agent_id))
    company_schedule = _sample_company_schedule(rng, work_status)
    elder_schedule = _elder_weekday_schedule(rng, work_status, medical_need_level) if age_group == "60+" else {}
    activities = []
    sequence = 1
    slot_categories = ("weekday_activity", "weekend_activity")
    slot_audit = {
        category: {"total_candidate_slots": 0, "modeled_activity_slot_count": 0, "no_in_scope_slot_count": 0}
        for category in slot_categories
    }
    fixed_activity_slot_count = 0
    for day_index in range(7):
        day_date = (simulation_week_start + timedelta(days=day_index)).date()
        if day_index < 5:
            templates = _weekday_templates(age_group, work_status, elder_schedule, day_index, rng, company_schedule)
            category = "weekday_activity"
            capacity = 3
        else:
            templates = _weekend_templates(
                age_group,
                rng,
                medical_need_level if age_group == "60+" else None,
            )
            category = "weekend_activity"
            capacity = 4
        fixed_activity_slot_count += sum(purpose in MANDATORY_PURPOSES for purpose, _, _ in templates)
        slot_audit[category]["total_candidate_slots"] += capacity
        slot_audit[category]["modeled_activity_slot_count"] += len(templates)
        slot_audit[category]["no_in_scope_slot_count"] += capacity - len(templates)
        templates = sorted(templates, key=lambda item: (item[1], item[2], item[0]))
        for sequence_order, (purpose, start_time, end_time) in enumerate(templates, start=1):
            activities.append(_make_activity(agent_id=agent_id, age_group=age_group, work_status=work_status, medical_need_level=medical_need_level, home_zone=home_zone, day_index=day_index, day_date=day_date, sequence=sequence, sequence_order=sequence_order, purpose=purpose, start_time=start_time, end_time=end_time))
            sequence += 1
    validate_activity_plan(activities)
    active_days = {item["planned_start_datetime"].date() for item in activities}
    total_candidate_slots = sum(item["total_candidate_slots"] for item in slot_audit.values())
    modeled_count = sum(item["modeled_activity_slot_count"] for item in slot_audit.values())
    no_scope_count = sum(item["no_in_scope_slot_count"] for item in slot_audit.values())
    audit = {
        "total_candidate_slots": total_candidate_slots,
        "modeled_activity_slot_count": modeled_count,
        "no_in_scope_slot_count": no_scope_count,
        "empty_agent_day_count": 7 - len(active_days),
        "fixed_activity_slot_count": fixed_activity_slot_count,
        "slot_breakdown": slot_audit,
    }
    if modeled_count + no_scope_count != total_candidate_slots:
        raise AssertionError("Candidate-slot audit counts do not balance")
    return {"activities": activities, "audit": audit}


def generate_weekly_activity_plan(agent: Any, simulation_week_start: datetime, random_seed: Any) -> List[Dict[str, Any]]:
    """Generate one placed Agent's ordered Monday-Sunday baseline activities."""
    return generate_weekly_activity_plan_with_audit(agent, simulation_week_start, random_seed)["activities"]


def generate_seven_day_activity_plans(agents: Iterable[Any], simulation_week_start: datetime, random_seed: Any) -> List[Dict[str, Any]]:
    """Generate activity records for multiple already-placed Agents."""
    all_activities = []
    seen_agent_ids = set()
    for agent in agents:
        agent_id = _read_agent(agent, "agent_id")
        identity = f"{type(agent_id).__name__}:{agent_id!r}"
        if identity in seen_agent_ids:
            raise ValueError(f"Duplicate agent_id: {agent_id}")
        seen_agent_ids.add(identity)
        all_activities.extend(generate_weekly_activity_plan(agent, simulation_week_start, random_seed))
    validate_activity_plan(all_activities)
    return all_activities


def generate_seven_day_activity_plans_with_audit(agents: Iterable[Any], simulation_week_start: datetime, random_seed: Any) -> Dict[str, Any]:
    """Generate multi-Agent activities and aggregate balanced slot audits."""
    all_activities = []
    per_agent_audit = {}
    totals = {
        "total_candidate_slots": 0,
        "modeled_activity_slot_count": 0,
        "no_in_scope_slot_count": 0,
        "empty_agent_day_count": 0,
        "fixed_activity_slot_count": 0,
    }
    seen_agent_ids = set()
    for agent in agents:
        agent_id = _read_agent(agent, "agent_id")
        identity = f"{type(agent_id).__name__}:{agent_id!r}"
        if identity in seen_agent_ids:
            raise ValueError(f"Duplicate agent_id: {agent_id}")
        seen_agent_ids.add(identity)
        result = generate_weekly_activity_plan_with_audit(agent, simulation_week_start, random_seed)
        all_activities.extend(result["activities"])
        per_agent_audit[agent_id] = result["audit"]
        for field in totals:
            totals[field] += result["audit"][field]
    validate_activity_plan(all_activities)
    if totals["modeled_activity_slot_count"] + totals["no_in_scope_slot_count"] != totals["total_candidate_slots"]:
        raise AssertionError("Aggregate candidate-slot audit counts do not balance")
    return {"activities": all_activities, "audit": totals, "per_agent_audit": per_agent_audit}


def validate_activity_plan(activities: Iterable[Dict[str, Any]]) -> None:
    """Validate fields, 30-minute grid, IDs, home zones, ordering, and non-overlap."""
    seen_ids = set()
    intervals_by_agent = defaultdict(list)
    sequences_by_agent = defaultdict(list)
    daily_sequences = defaultdict(list)
    for activity in activities:
        if tuple(activity.keys()) != OUTPUT_FIELDS:
            raise ValueError("Activity fields do not match the T5B output contract")
        activity_id = activity["activity_id"]
        if activity_id in seen_ids:
            raise ValueError(f"Duplicate activity_id: {activity_id}")
        seen_ids.add(activity_id)
        if activity["home_zone"] not in VALID_HOME_ZONES:
            raise ValueError(f"Invalid activity home_zone: {activity['home_zone']}")
        if activity["destination_zone"] is not None:
            raise ValueError("T5B destination_zone must remain None")
        start, end = activity["planned_start_datetime"], activity["planned_end_datetime"]
        if start.minute not in {0, 30} or end.minute not in {0, 30} or start.second or end.second:
            raise ValueError(f"Activity time is not on the 30-minute grid: {activity_id}")
        if end <= start:
            raise ValueError(f"Activity end must be later than start: {activity_id}")
        if end.date() != start.date():
            raise ValueError(f"Activity may not cross midnight: {activity_id}")
        if activity["activity_purpose"] != "work" and end - start < timedelta(minutes=30):
            raise ValueError(f"Non-work activity is shorter than 30 minutes: {activity_id}")
        intervals_by_agent[activity["agent_id"]].append((start, end, activity_id))
        sequences_by_agent[activity["agent_id"]].append(activity["activity_sequence"])
        daily_sequences[(activity["agent_id"], start.date())].append(activity["sequence_order"])
    for agent_id, intervals in intervals_by_agent.items():
        if intervals != sorted(intervals, key=lambda item: (item[0], item[2])):
            raise ValueError(f"Activities are not time ordered for agent {agent_id}")
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                raise ValueError(f"Overlapping activities for agent {agent_id}: {previous[2]} and {current[2]}")
        if sequences_by_agent[agent_id] != list(range(1, len(intervals) + 1)):
            raise ValueError(f"Invalid activity sequence for agent {agent_id}")
    for key, values in daily_sequences.items():
        if values != list(range(1, len(values) + 1)):
            raise ValueError(f"Invalid daily sequence_order for agent/day {key}")
