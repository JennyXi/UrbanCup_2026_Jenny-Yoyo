"""Create time-feasible daily leg chains from destination-assigned activities."""

from __future__ import annotations

import math
import hashlib
import random
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping

from custom.agents.trip_planning import NON_WORK_DURATION_OPTIONS
from custom.transport.network import (
    _road_network_distance,
    build_transport_network,
    load_transport_configuration,
)


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


def _stable_seed(seed: Any, *parts: Any) -> int:
    material = "|".join([repr(seed), *(f"{type(part).__name__}:{part!r}" for part in parts)])
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def sample_intrazonal_distance(
    zone_id: str,
    purpose: str,
    spatial_by_id: Mapping[str, Mapping[str, Any]],
    *,
    seed: Any,
    agent_id: Any,
    origin_location_key: str,
    destination_location_key: str,
    transport_config: Mapping[str, Any] | None = None,
) -> float:
    """Sample a positive same-zone road distance bound to an unordered location pair."""
    config = transport_config or load_transport_configuration()
    sampling = config["intrazonal_distance_sampling"]
    ranges = sampling["purpose_multiplier_ranges"]
    if purpose not in ranges:
        raise ValueError(f"No intrazonal distance sampling rule for purpose: {purpose}")
    mean = float(spatial_by_id[zone_id]["mean_intrazonal_distance"])
    row = ranges[purpose]
    location_pair = tuple(sorted((origin_location_key, destination_location_key)))
    rng = random.Random(_stable_seed(seed, agent_id, location_pair, zone_id, purpose))
    multiplier = rng.triangular(float(row["low"]), float(row["high"]), float(row["mode"]))
    return min(
        float(sampling["maximum_distance_km"]),
        max(float(sampling["minimum_distance_km"]), mean * multiplier),
    )


def _home_location_key(agent_id: Any, zone_id: str) -> str:
    return f"{agent_id}:home:{zone_id}"


def _activity_location_key(agent_id: Any, activity: Mapping[str, Any]) -> str:
    purpose = activity["activity_purpose"]
    destination = activity["destination_zone"]
    if purpose in {"work", "medical"}:
        return f"{agent_id}:{purpose}:{destination}"
    if purpose in {"visit", "out_of_home_family_care", "out_of_home_family_activity"}:
        return f"{agent_id}:family:{destination}"
    return f"{agent_id}:activity:{activity['activity_id']}:{destination}"


def _euclidean_distance(origin: str, destination: str, spatial_by_id: Mapping[str, Mapping[str, Any]]) -> float:
    if origin == destination:
        return 0.0
    left = spatial_by_id[origin]
    right = spatial_by_id[destination]
    return math.hypot(
        float(left["centroid_x"]) - float(right["centroid_x"]),
        float(left["centroid_y"]) - float(right["centroid_y"]),
    )


def _leg_distances(
    origin: str,
    destination: str,
    purpose: str,
    spatial_by_id: Mapping[str, Mapping[str, Any]],
    *,
    seed: Any,
    agent_id: Any,
    origin_location_key: str,
    destination_location_key: str,
    transport_config: Mapping[str, Any],
    transport_network: Mapping[str, Any],
) -> tuple[float, float]:
    if origin != destination:
        euclidean = _euclidean_distance(origin, destination, spatial_by_id)
        return euclidean, _road_network_distance(transport_network, origin, destination)
    road_distance = sample_intrazonal_distance(
        origin, purpose, spatial_by_id, seed=seed, agent_id=agent_id,
        origin_location_key=origin_location_key,
        destination_location_key=destination_location_key,
        transport_config=transport_config,
    )
    return 0.0, road_distance


def estimate_travel_time_minutes(origin: str, destination: str, spatial_by_id: Mapping[str, Mapping[str, Any]], distance_km: float | None = None) -> int:
    """Generalized urban travel time, including congestion/waiting/transfer burden.

    Synthetic road-network distance is converted at 18 km/h, rounded up to five
    minutes, with a 10-minute minimum and a 90-minute extreme upper bound.
    """
    if distance_km is None:
        euclidean = _euclidean_distance(origin, destination, spatial_by_id)
        distance = euclidean * max(
            float(spatial_by_id[origin].get("network_distance_multiplier", 1.0)),
            float(spatial_by_id[destination].get("network_distance_multiplier", 1.0)),
        ) if origin != destination else float(spatial_by_id[origin]["mean_intrazonal_distance"])
    else:
        distance = distance_km
    return min(90, max(10, int(math.ceil((distance / 18.0 * 60.0) / 5.0) * 5)))


def build_time_feasible_legs(
    agents: Iterable[Any],
    activities: Iterable[Mapping[str, Any]],
    spatial_by_id: Mapping[str, Mapping[str, Any]],
    seed: Any = 47,
    transport_config: Mapping[str, Any] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return adjusted activities and legs with exact departure/arrival identities.

    Work arrival/end times are fixed. A later non-work activity may move forward
    only when required by the preceding activity plus inter-activity travel.
    """
    agent_by_id = {_read(agent, "agent_id"): agent for agent in agents}
    transport_config = transport_config or load_transport_configuration()
    transport_network = build_transport_network(
        config=transport_config,
        spatial={"zones": list(spatial_by_id.values())},
    )
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
            origin_location_key = _home_location_key(agent_id, home)
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

                destination_location_key = _activity_location_key(agent_id, activity)
                _, road_distance = _leg_distances(
                    origin, activity["destination_zone"], activity["activity_purpose"], spatial_by_id,
                    seed=seed, agent_id=agent_id,
                    origin_location_key=origin_location_key,
                    destination_location_key=destination_location_key,
                    transport_config=transport_config,
                    transport_network=transport_network,
                )
                travel_minutes = estimate_travel_time_minutes(origin, activity["destination_zone"], spatial_by_id, road_distance)
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
                origin_location_key = destination_location_key
                previous_end = activity["planned_end_datetime"]
            if restart:
                continue

            final = day_records[-1]
            _, return_road_distance = _leg_distances(
                final["destination_zone"], home, final["activity_purpose"], spatial_by_id,
                seed=seed, agent_id=agent_id,
                origin_location_key=_activity_location_key(agent_id, final),
                destination_location_key=_home_location_key(agent_id, home),
                transport_config=transport_config,
                transport_network=transport_network,
            )
            return_minutes = estimate_travel_time_minutes(final["destination_zone"], home, spatial_by_id, return_road_distance)
            deadline = _deadline_datetime(day, age_group)
            latest_end = deadline - timedelta(minutes=return_minutes)
            if final["planned_end_datetime"] > latest_end:
                if final["activity_purpose"] == "work":
                    raise ValueError(f"Fixed work schedule misses home-arrival deadline for agent {agent_id} on {day}")
                earliest_start = datetime.combine(day, time(9, 0))
                if len(day_records) > 1:
                    previous = day_records[-2]
                    _, inbound_road_distance = _leg_distances(
                        previous["destination_zone"], final["destination_zone"], final["activity_purpose"], spatial_by_id,
                        seed=seed, agent_id=agent_id,
                        origin_location_key=_activity_location_key(agent_id, previous),
                        destination_location_key=_activity_location_key(agent_id, final),
                        transport_config=transport_config,
                        transport_network=transport_network,
                    )
                    inbound_minutes = estimate_travel_time_minutes(
                        previous["destination_zone"], final["destination_zone"], spatial_by_id, inbound_road_distance
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
        origin_location_key = _home_location_key(agent_id, home)
        previous_end = None
        for index, activity in enumerate(day_records, start=1):
            destination_location_key = _activity_location_key(agent_id, activity)
            euclidean_distance, road_distance = _leg_distances(
                origin, activity["destination_zone"], activity["activity_purpose"], spatial_by_id,
                seed=seed, agent_id=agent_id,
                origin_location_key=origin_location_key,
                destination_location_key=destination_location_key,
                transport_config=transport_config,
                transport_network=transport_network,
            )
            travel_minutes = estimate_travel_time_minutes(origin, activity["destination_zone"], spatial_by_id, road_distance)
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
                "euclidean_distance_km": round(euclidean_distance, 3),
                "road_network_distance_km": round(road_distance, 3),
            })
            origin = activity["destination_zone"]
            origin_location_key = destination_location_key
            previous_end = activity["planned_end_datetime"]

        final = day_records[-1]
        euclidean_distance, road_distance = _leg_distances(
            origin, home, final["activity_purpose"], spatial_by_id,
            seed=seed, agent_id=agent_id,
            origin_location_key=origin_location_key,
            destination_location_key=_home_location_key(agent_id, home),
            transport_config=transport_config,
            transport_network=transport_network,
        )
        travel_minutes = estimate_travel_time_minutes(origin, home, spatial_by_id, road_distance)
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
            "euclidean_distance_km": round(euclidean_distance, 3),
            "road_network_distance_km": round(road_distance, 3),
        })

    records = [item for item in records if item["activity_id"] not in removed_activity_ids]
    records.sort(key=lambda item: (item["agent_id"], item["planned_start_datetime"], item["sequence_order"]))
    return {"activities": records, "legs": legs}
