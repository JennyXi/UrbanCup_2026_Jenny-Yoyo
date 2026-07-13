"""Create time-feasible daily leg chains from destination-assigned activities."""

from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping

from custom.spatial.destination_assignment import effective_choice_distance


HOME_ARRIVAL_DEADLINES = {
    "18-39": time(0, 0),  # midnight at the end of the activity day
    "40-59": time(22, 0),
    "60+": time(20, 0),
}


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
        day_records.sort(key=lambda item: (item["planned_start_datetime"], item["sequence_order"]))

        # Enforce the age-specific home-arrival deadline before creating legs.
        # Preserve duration and move only the final non-work activity earlier;
        # if it cannot fit after the preceding activity/travel and facility
        # opening time, omit that optional activity instead.
        while day_records:
            final = day_records[-1]
            return_minutes = estimate_travel_time_minutes(final["destination_zone"], home, spatial_by_id)
            deadline_clock = HOME_ARRIVAL_DEADLINES[age_group]
            deadline = (
                datetime.combine(day + timedelta(days=1), deadline_clock)
                if deadline_clock == time(0, 0)
                else datetime.combine(day, deadline_clock)
            )
            latest_end = deadline - timedelta(minutes=return_minutes)
            if final["planned_end_datetime"] <= latest_end:
                break
            if final["activity_purpose"] == "work":
                raise ValueError(f"Fixed work schedule misses home-arrival deadline for agent {agent_id} on {day}")
            duration = final["planned_end_datetime"] - final["planned_start_datetime"]
            candidate_start = latest_end - duration
            earliest_start = datetime.combine(day, time(0, 0))
            if len(day_records) > 1:
                previous = day_records[-2]
                inbound_minutes = estimate_travel_time_minutes(
                    previous["destination_zone"], final["destination_zone"], spatial_by_id
                )
                earliest_start = previous["planned_end_datetime"] + timedelta(minutes=inbound_minutes)
            if final["activity_purpose"] == "shopping":
                earliest_start = max(earliest_start, datetime.combine(day, time(10, 0)))
            if candidate_start < earliest_start:
                removed_activity_ids.add(final["activity_id"])
                day_records.pop()
                continue
            final["planned_start_datetime"] = candidate_start
            final["planned_end_datetime"] = latest_end
            break

        if not day_records:
            continue
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
