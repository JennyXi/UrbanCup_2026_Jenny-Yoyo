"""Create time-feasible daily leg chains from destination-assigned activities."""

from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping

from custom.agents.trip_planning import NON_WORK_DURATION_OPTIONS
from custom.spatial.destination_assignment import effective_choice_distance


HOME_ARRIVAL_DEADLINES = {
    "18-39": time(0, 0),  # midnight at the end of the activity day
    "40-59": time(22, 0),
    "60+": time(20, 0),
}


def _deadline_datetime(day, age_group: str) -> datetime:
    clock = HOME_ARRIVAL_DEADLINES[age_group]
    return datetime.combine(day + timedelta(days=clock == time(0, 0)), clock)


def _allowed_non_work_durations(purpose: str) -> List[int]:
    return sorted(minutes for minutes, _ in NON_WORK_DURATION_OPTIONS[purpose] if minutes >= 30)


def _duration_minutes(activity: Mapping[str, Any]) -> int:
    return int((activity["planned_end_datetime"] - activity["planned_start_datetime"]).total_seconds() / 60)


def _remove_optional_or_raise(day_records, activity, removed_activity_ids, reason: str) -> None:
    if activity["is_mandatory"]:
        raise ValueError(f"Mandatory activity {activity['activity_id']} is infeasible: {reason}")
    removed_activity_ids.add(activity["activity_id"])
    day_records.remove(activity)


def _shorten_to_legal_duration(activity: Dict[str, Any], available_minutes: int) -> bool:
    """Shorten without exceeding the sampled duration; never wrap across midnight."""
    original_minutes = _duration_minutes(activity)
    legal = [
        minutes for minutes in _allowed_non_work_durations(activity["activity_purpose"])
        if minutes <= min(original_minutes, available_minutes)
    ]
    if not legal:
        return False
    duration = max(legal)
    activity["planned_end_datetime"] = activity["planned_start_datetime"] + timedelta(minutes=duration)
    return True


def _read(agent: Any, field: str) -> Any:
    return agent[field] if isinstance(agent, dict) else getattr(agent, field)


def estimate_travel_time_minutes(origin: str, destination: str, spatial_by_id: Mapping[str, Mapping[str, Any]]) -> int:
    """Generalized urban travel time, including congestion/waiting/transfer burden.

    Effective network distance is converted at 18 km/h, rounded up to five
    minutes, with a 10-minute minimum and a 90-minute extreme upper bound.
    """
    distance = effective_choice_distance(origin, destination, spatial_by_id)
    return min(90, max(10, int(math.ceil((distance / 18.0 * 60.0) / 5.0) * 5)))


def build_time_feasible_legs(
    agents: Iterable[Any],
    activities: Iterable[Mapping[str, Any]],
    spatial_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Return adjusted activities and legs with exact departure/arrival identities.

    Work arrival/end times are fixed. A later non-work activity may move forward
    only when required by the preceding activity plus inter-activity travel.
    """
    agent_by_id = {_read(agent, "agent_id"): agent for agent in agents}
    records = [deepcopy(dict(item)) for item in activities]
    removed_activity_ids = set()
    grouped = defaultdict(list)
    for item in records:
        grouped[(item["agent_id"], item["planned_start_datetime"].date())].append(item)

    legs = []
    for (agent_id, day), day_records in sorted(grouped.items()):
        agent = agent_by_id[agent_id]
        home = _read(agent, "home_zone")
        age_group = _read(agent, "age_group")
        # Rebuild the day until every retained activity fits. Optional
        # activities may be shortened to another legal purpose-specific
        # duration or removed. Datetimes are never converted to time-only
        # values, so midnight cannot silently wrap to the next day.
        while day_records:
            day_records.sort(key=lambda item: (item["planned_start_datetime"], item["sequence_order"]))
            restart = False
            origin = home
            previous_end = None
            for index, activity in enumerate(day_records):
                start = activity["planned_start_datetime"]
                end = activity["planned_end_datetime"]
                if start.date() != day or end <= start:
                    _remove_optional_or_raise(
                        day_records, activity, removed_activity_ids,
                        "start must remain on the activity day and end must be later than start",
                    )
                    restart = True
                    break

                if activity["activity_purpose"] != "work" and _duration_minutes(activity) < 30:
                    _remove_optional_or_raise(
                        day_records, activity, removed_activity_ids,
                        "non-work activity must last at least 30 minutes",
                    )
                    restart = True
                    break

                travel_minutes = estimate_travel_time_minutes(origin, activity["destination_zone"], spatial_by_id)
                earliest_start = previous_end + timedelta(minutes=travel_minutes) if previous_end is not None else start
                if start < earliest_start:
                    if activity["activity_purpose"] == "work":
                        removable = next(
                            (row for row in reversed(day_records[:index]) if not row["is_mandatory"]),
                            None,
                        )
                        if removable is None:
                            raise ValueError(f"Fixed work arrival is infeasible for agent {agent_id} on {day}")
                        _remove_optional_or_raise(day_records, removable, removed_activity_ids, "conflicts with fixed work arrival")
                        restart = True
                        break
                    duration = end - start
                    activity["planned_start_datetime"] = earliest_start
                    activity["planned_end_datetime"] = earliest_start + duration

                if activity["planned_end_datetime"].date() != day:
                    _remove_optional_or_raise(
                        day_records, activity, removed_activity_ids,
                        "forward adjustment would cross midnight",
                    )
                    restart = True
                    break
                origin = activity["destination_zone"]
                previous_end = activity["planned_end_datetime"]
            if restart:
                continue

            final = day_records[-1]
            return_minutes = estimate_travel_time_minutes(final["destination_zone"], home, spatial_by_id)
            deadline = _deadline_datetime(day, age_group)
            latest_end = deadline - timedelta(minutes=return_minutes)
            if final["planned_end_datetime"] > latest_end:
                if final["activity_purpose"] == "work":
                    raise ValueError(f"Fixed work schedule misses home-arrival deadline for agent {agent_id} on {day}")
                earliest_start = datetime.combine(day, time(9, 0))
                if len(day_records) > 1:
                    previous = day_records[-2]
                    inbound_minutes = estimate_travel_time_minutes(
                        previous["destination_zone"], final["destination_zone"], spatial_by_id
                    )
                    earliest_start = previous["planned_end_datetime"] + timedelta(minutes=inbound_minutes)
                if final["activity_purpose"] == "shopping":
                    earliest_start = max(earliest_start, datetime.combine(day, time(10, 0)))
                available = int((latest_end - earliest_start).total_seconds() / 60)
                original_duration = _duration_minutes(final)
                legal = [
                    minutes for minutes in _allowed_non_work_durations(final["activity_purpose"])
                    if minutes <= min(original_duration, available)
                ]
                if not legal:
                    _remove_optional_or_raise(
                        day_records, final, removed_activity_ids,
                        "activity plus return travel cannot meet the age-specific home deadline",
                    )
                    continue
                duration = max(legal)
                latest_start = latest_end - timedelta(minutes=duration)
                final["planned_start_datetime"] = min(
                    max(final["planned_start_datetime"], earliest_start),
                    latest_start,
                )
                final["planned_end_datetime"] = final["planned_start_datetime"] + timedelta(minutes=duration)

            if final["planned_end_datetime"] + timedelta(minutes=return_minutes) > deadline:
                raise AssertionError("Home-arrival deadline enforcement failed")
            break

        if not day_records:
            continue
        for sequence_order, activity in enumerate(day_records, start=1):
            activity["sequence_order"] = sequence_order

        origin = home
        previous_end = None
        for index, activity in enumerate(day_records, start=1):
            travel_minutes = estimate_travel_time_minutes(origin, activity["destination_zone"], spatial_by_id)
            travel = timedelta(minutes=travel_minutes)
            earliest_start = previous_end + travel if previous_end is not None else None
            if earliest_start is not None and activity["planned_start_datetime"] < earliest_start:
                if activity["activity_purpose"] == "work":
                    raise ValueError(f"Fixed work arrival is infeasible for agent {agent_id} on {day}")
                duration = activity["planned_end_datetime"] - activity["planned_start_datetime"]
                activity["planned_start_datetime"] = earliest_start
                activity["planned_end_datetime"] = earliest_start + duration
                if activity["planned_end_datetime"].date() != day:
                    raise ValueError(f"Activity adjustment crosses midnight for agent {agent_id} on {day}")
            arrival = activity["planned_start_datetime"]
            departure = arrival - travel
            legs.append({
                "leg_id": f"{agent_id}-{day.isoformat()}-L{index:02d}",
                "agent_id": agent_id,
                "date": day.isoformat(),
                "day": activity["day_of_week"],
                "leg_sequence": index,
                "leg_role": "outbound" if index == 1 else "between_activities",
                "activity_id": activity["activity_id"],
                "purpose": activity["activity_purpose"],
                "origin_zone": origin,
                "destination_zone": activity["destination_zone"],
                "departure_time": departure,
                "travel_time_minutes": travel_minutes,
                "arrival_time": arrival,
                "effective_distance_km": round(effective_choice_distance(origin, activity["destination_zone"], spatial_by_id), 3),
            })
            origin = activity["destination_zone"]
            previous_end = activity["planned_end_datetime"]

        final = day_records[-1]
        travel_minutes = estimate_travel_time_minutes(origin, home, spatial_by_id)
        departure = final["planned_end_datetime"]
        arrival = departure + timedelta(minutes=travel_minutes)
        legs.append({
            "leg_id": f"{agent_id}-{day.isoformat()}-L{len(day_records)+1:02d}",
            "agent_id": agent_id,
            "date": day.isoformat(),
            "day": final["day_of_week"],
            "leg_sequence": len(day_records) + 1,
            "leg_role": "return_home",
            "activity_id": final["activity_id"],
            "purpose": "return_home",
            "origin_zone": origin,
            "destination_zone": home,
            "departure_time": departure,
            "travel_time_minutes": travel_minutes,
            "arrival_time": arrival,
            "effective_distance_km": round(effective_choice_distance(origin, home, spatial_by_id), 3),
        })

    records = [item for item in records if item["activity_id"] not in removed_activity_ids]
    records.sort(key=lambda item: (item["agent_id"], item["planned_start_datetime"], item["sequence_order"]))
    return {"activities": records, "legs": legs}
