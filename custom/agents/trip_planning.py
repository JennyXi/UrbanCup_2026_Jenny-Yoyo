"""T1 deterministic seven-day potential trip-plan generation.

The module generates potential outbound/return legs only. It does not apply
weather cancellation, subsidies, mode choice, pricing, or dispatch logic.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Tuple


AGE_GROUPS = ("18-39", "40-59", "60+")
DAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

MANDATORY_PURPOSES = {"work", "medical"}

BASELINE_CANCEL_PROBABILITY = {
    "work": 0.03,
    "medical": 0.01,
    "grocery": 0.08,
    "daily_errand": 0.10,
    "family_care": 0.08,
    "family_activity": 0.12,
    "shopping": 0.15,
    "visit": 0.16,
    "park": 0.20,
    "leisure": 0.24,
    "social": 0.24,
    "community": 0.25,
}

DESTINATION_BY_PURPOSE = {
    "work": "employment_zone",
    "medical": "medical_zone",
    "grocery": "local_retail_zone",
    "daily_errand": "service_zone",
    "family_care": "family_care_zone",
    "family_activity": "family_activity_zone",
    "shopping": "retail_zone",
    "visit": "residential_visit_zone",
    "park": "park_zone",
    "leisure": "leisure_zone",
    "social": "social_zone",
    "community": "community_zone",
}

OUTPUT_FIELDS = (
    "agent_id",
    "age_group",
    "day_of_week",
    "is_weekend",
    "trip_id",
    "leg_id",
    "trip_sequence",
    "trip_purpose",
    "origin_zone",
    "destination_zone",
    "planned_departure_datetime",
    "planned_return_datetime",
    "is_outbound",
    "is_mandatory",
    "baseline_cancel_probability",
)


def _read_agent(agent: Any, field_name: str) -> Any:
    if isinstance(agent, dict):
        if field_name not in agent:
            raise ValueError(f"Agent missing required field: {field_name}")
        return agent[field_name]
    if not hasattr(agent, field_name):
        raise ValueError(f"Agent missing required field: {field_name}")
    return getattr(agent, field_name)


def _validate_week_start(simulation_week_start: datetime) -> None:
    if not isinstance(simulation_week_start, datetime):
        raise ValueError("simulation_week_start must be a datetime")
    if simulation_week_start.weekday() != 0 or simulation_week_start.time() != time(0, 0):
        raise ValueError("simulation_week_start must be Monday 00:00")


def _stable_seed(random_seed: Any, agent_id: Any) -> int:
    if random_seed is None or isinstance(random_seed, (dict, list, set)):
        raise ValueError("random_seed must be a stable scalar value")
    if agent_id is None:
        raise ValueError("agent_id must not be None")
    key = f"{random_seed}|{agent_id}|T1-weekly-plan".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def _home_zone(agent_id: Any) -> str:
    digest = hashlib.sha256(str(agent_id).encode("utf-8")).digest()
    return f"residential_zone_{(int.from_bytes(digest[:2], 'big') % 9) + 1}"


def _weighted_choice(rng: random.Random, choices: Sequence[Tuple[str, float]]) -> str:
    purposes, weights = zip(*choices)
    return rng.choices(purposes, weights=weights, k=1)[0]


def _weekday_activities(age_group: str, day_index: int, rng: random.Random) -> List[Tuple[str, time, time]]:
    if age_group == "18-39":
        activities = [("work", time(8, 0), time(17, 30))]
        if rng.random() < 0.38:
            evening = "shopping" if rng.random() < 0.45 else "social"
            activities.append((evening, time(18, 45), time(20, 45)))
        return activities

    if age_group == "40-59":
        activities = [("work", time(8, 30), time(17, 30))]
        if rng.random() < 0.56:
            extra = _weighted_choice(
                rng,
                (("family_care", 0.48), ("daily_errand", 0.30), ("shopping", 0.22)),
            )
            activities.append((extra, time(18, 30), time(20, 15)))
        return activities

    purpose = _weighted_choice(
        rng,
        (
            ("grocery", 0.30),
            ("medical", 0.20),
            ("daily_errand", 0.25),
            ("community", 0.20),
            ("work", 0.05),
        ),
    )
    if day_index % 2 == 0:
        return [(purpose, time(9, 30), time(11, 30))]
    return [(purpose, time(13, 30), time(15, 30))]


def _weekend_activities(age_group: str, day_index: int, rng: random.Random) -> List[Tuple[str, time, time]]:
    if age_group == "18-39":
        purpose = _weighted_choice(
            rng,
            (("shopping", 0.28), ("leisure", 0.30), ("social", 0.24), ("visit", 0.18)),
        )
        start = time(10, 30) if day_index == 5 else time(14, 0)
        end = time(13, 0) if day_index == 5 else time(17, 30)
        return [(purpose, start, end)]

    if age_group == "40-59":
        purpose = _weighted_choice(
            rng,
            (("family_activity", 0.45), ("shopping", 0.30), ("visit", 0.25)),
        )
        start = time(10, 0) if day_index == 5 else time(14, 0)
        end = time(13, 0) if day_index == 5 else time(17, 0)
        return [(purpose, start, end)]

    purpose = _weighted_choice(
        rng,
        (("visit", 0.36), ("park", 0.34), ("family_activity", 0.30)),
    )
    start = time(9, 30) if day_index == 5 else time(14, 0)
    end = time(11, 30) if day_index == 5 else time(16, 30)
    return [(purpose, start, end)]


def _make_trip_legs(
    *,
    agent_id: Any,
    age_group: str,
    day_date: date,
    day_index: int,
    trip_sequence: int,
    purpose: str,
    outbound_time: time,
    return_time: time,
) -> List[Dict[str, Any]]:
    outbound_datetime = datetime.combine(day_date, outbound_time)
    return_datetime = datetime.combine(day_date, return_time)
    if return_datetime <= outbound_datetime:
        raise ValueError("planned return must be later than outbound departure")

    trip_id = f"trip-{agent_id}-{day_date.isoformat()}-{trip_sequence:03d}"
    home = _home_zone(agent_id)
    destination = DESTINATION_BY_PURPOSE[purpose]
    outbound = {
        "agent_id": agent_id,
        "age_group": age_group,
        "day_of_week": DAY_NAMES[day_index],
        "is_weekend": day_index >= 5,
        "trip_id": trip_id,
        "leg_id": f"{trip_id}-outbound",
        "trip_sequence": trip_sequence,
        "trip_purpose": purpose,
        "origin_zone": home,
        "destination_zone": destination,
        "planned_departure_datetime": outbound_datetime,
        "planned_return_datetime": return_datetime,
        "is_outbound": True,
        "is_mandatory": purpose in MANDATORY_PURPOSES,
        "baseline_cancel_probability": BASELINE_CANCEL_PROBABILITY[purpose],
    }
    returning = {
        "agent_id": agent_id,
        "age_group": age_group,
        "day_of_week": DAY_NAMES[day_index],
        "is_weekend": day_index >= 5,
        "trip_id": trip_id,
        "leg_id": f"{trip_id}-return",
        "trip_sequence": trip_sequence,
        "trip_purpose": purpose,
        "origin_zone": destination,
        "destination_zone": home,
        "planned_departure_datetime": return_datetime,
        "planned_return_datetime": return_datetime,
        "is_outbound": False,
        "is_mandatory": purpose in MANDATORY_PURPOSES,
        "baseline_cancel_probability": BASELINE_CANCEL_PROBABILITY[purpose],
    }
    return [outbound, returning]


def generate_weekly_trip_plan(
    agent: Any,
    simulation_week_start: datetime,
    random_seed: Any,
) -> List[Dict[str, Any]]:
    """Generate Monday-Sunday potential trip legs for one agent."""
    _validate_week_start(simulation_week_start)
    agent_id = _read_agent(agent, "agent_id")
    age_group = _read_agent(agent, "age_group")
    if age_group not in AGE_GROUPS:
        raise ValueError(f"Unsupported age_group: {age_group}")

    rng = random.Random(_stable_seed(random_seed, agent_id))
    legs: List[Dict[str, Any]] = []
    sequence = 1
    for day_index in range(7):
        day_date = (simulation_week_start + timedelta(days=day_index)).date()
        if day_index < 5:
            activities = _weekday_activities(age_group, day_index, rng)
        else:
            activities = _weekend_activities(age_group, day_index, rng)
        for purpose, outbound_time, return_time in activities:
            legs.extend(
                _make_trip_legs(
                    agent_id=agent_id,
                    age_group=age_group,
                    day_date=day_date,
                    day_index=day_index,
                    trip_sequence=sequence,
                    purpose=purpose,
                    outbound_time=outbound_time,
                    return_time=return_time,
                )
            )
            sequence += 1

    validate_trip_plan(legs)
    return legs


def generate_seven_day_trip_plans(
    agents: Iterable[Any],
    simulation_week_start: datetime,
    random_seed: Any,
) -> List[Dict[str, Any]]:
    """Generate and validate weekly potential trip legs for multiple agents."""
    all_legs: List[Dict[str, Any]] = []
    seen_agent_ids = set()
    for agent in agents:
        agent_id = _read_agent(agent, "agent_id")
        if agent_id in seen_agent_ids:
            raise ValueError(f"Duplicate agent_id: {agent_id}")
        seen_agent_ids.add(agent_id)
        all_legs.extend(generate_weekly_trip_plan(agent, simulation_week_start, random_seed))
    validate_trip_plan(all_legs)
    return all_legs


def validate_trip_plan(legs: Iterable[Dict[str, Any]]) -> None:
    """Validate field shape, trip pairing, stable uniqueness, and no overlap."""
    leg_list = list(legs)
    seen_leg_ids = set()
    trips: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    intervals_by_agent: Dict[Any, List[Tuple[datetime, datetime, str]]] = defaultdict(list)

    for leg in leg_list:
        if tuple(leg.keys()) != OUTPUT_FIELDS:
            raise ValueError("Trip leg fields do not match the T1 output contract")
        leg_id = leg["leg_id"]
        if leg_id in seen_leg_ids:
            raise ValueError(f"Duplicate leg_id: {leg_id}")
        seen_leg_ids.add(leg_id)
        trips[leg["trip_id"]].append(leg)

    for trip_id, pair in trips.items():
        if len(pair) != 2:
            raise ValueError(f"Trip must contain exactly two legs: {trip_id}")
        outbound = next((leg for leg in pair if leg["is_outbound"]), None)
        returning = next((leg for leg in pair if not leg["is_outbound"]), None)
        if outbound is None or returning is None:
            raise ValueError(f"Trip must contain outbound and return legs: {trip_id}")
        if returning["planned_departure_datetime"] <= outbound["planned_departure_datetime"]:
            raise ValueError(f"Return must be later than outbound: {trip_id}")
        if outbound["planned_return_datetime"] != returning["planned_departure_datetime"]:
            raise ValueError(f"Trip return timestamps are inconsistent: {trip_id}")
        intervals_by_agent[outbound["agent_id"]].append(
            (
                outbound["planned_departure_datetime"],
                returning["planned_departure_datetime"],
                trip_id,
            )
        )

    for agent_id, intervals in intervals_by_agent.items():
        intervals.sort(key=lambda item: (item[0], item[2]))
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                raise ValueError(
                    f"Overlapping trips for agent {agent_id}: {previous[2]} and {current[2]}"
                )
