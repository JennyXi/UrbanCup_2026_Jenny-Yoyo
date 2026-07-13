"""T6 deterministic destination-zone assignment for baseline activities.

Only ``destination_zone`` is populated on copied activity records. Selection
uses a gravity score, purpose-specific soft penalties, and extreme hard
candidate exclusion. No origin, leg, OD, distance field, mode, weather,
pricing, dispatch, waiting-time, or congestion output is created.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


DEFAULT_DESTINATION_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "destination_choice.json"
ZONE_IDS = tuple(f"Z{index}" for index in range(1, 10))
SUPPORTED_PURPOSES = (
    "work", "medical", "visit", "out_of_home_family_care",
    "out_of_home_family_activity", "shopping", "social", "leisure",
)
FAMILY_PURPOSES = {"visit", "out_of_home_family_care", "out_of_home_family_activity"}
ACTIVITY_LEVEL_PURPOSES = {"shopping", "social", "leisure"}
ATTRACTION_FIELDS = ("employment_weight", "medical_weight", "service_weight")
MECHANISM = "gravity_soft_penalty_extreme_hard_exclusion"


def load_destination_configuration(path: Any = None) -> Dict[str, Any]:
    config_path = Path(path) if path is not None else DEFAULT_DESTINATION_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    validate_destination_configuration(config)
    return config


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return float(value)


def validate_destination_configuration(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise ValueError("destination_config must be a mapping")
    if config.get("destination_mechanism") != MECHANISM:
        raise ValueError(f"destination_mechanism must be {MECHANISM}")
    decay = config.get("distance_decay")
    required_decay = {"long_distance", "medium_distance", "local_distance"}
    if not isinstance(decay, Mapping) or set(decay) != required_decay:
        raise ValueError(f"distance_decay must contain exactly {sorted(required_decay)}")
    for name, value in decay.items():
        _finite_positive(value, f"distance_decay.{name}")

    attraction_mapping = config.get("purpose_attraction_mapping")
    distance_mapping = config.get("purpose_distance_mapping")
    if not isinstance(attraction_mapping, Mapping) or set(attraction_mapping) != set(SUPPORTED_PURPOSES):
        raise ValueError("purpose_attraction_mapping must cover exactly the supported purposes")
    if not isinstance(distance_mapping, Mapping) or set(distance_mapping) != set(SUPPORTED_PURPOSES):
        raise ValueError("purpose_distance_mapping must cover exactly the supported purposes")
    valid_attractions = set(ATTRACTION_FIELDS) | {"population_weight"}
    if any(value not in valid_attractions for value in attraction_mapping.values()):
        raise ValueError("purpose attraction mapping references an unknown field")
    if any(value not in required_decay for value in distance_mapping.values()):
        raise ValueError("purpose distance mapping references an unknown decay class")
    family_attractions = {attraction_mapping[purpose] for purpose in FAMILY_PURPOSES}
    family_decays = {distance_mapping[purpose] for purpose in FAMILY_PURPOSES}
    if len(family_attractions) != 1 or len(family_decays) != 1:
        raise ValueError("family purposes must share attraction and base distance-decay fields")

    constraints = config.get("purpose_distance_constraints")
    if not isinstance(constraints, Mapping) or set(constraints) != set(SUPPORTED_PURPOSES):
        raise ValueError("purpose_distance_constraints must cover exactly the supported purposes")
    constraint_fields = {"soft_limit_km", "extra_decay", "hard_limit_km"}
    for purpose, row in constraints.items():
        if not isinstance(row, Mapping) or set(row) != constraint_fields:
            raise ValueError(f"{purpose} constraint must contain exactly {sorted(constraint_fields)}")
        soft = _finite_positive(row["soft_limit_km"], f"{purpose}.soft_limit_km")
        _finite_positive(row["extra_decay"], f"{purpose}.extra_decay")
        hard = _finite_positive(row["hard_limit_km"], f"{purpose}.hard_limit_km")
        if not soft < hard:
            raise ValueError(f"{purpose} must satisfy soft_limit_km < hard_limit_km")

    weights = config.get("zone_attraction_weights")
    if not isinstance(weights, Mapping) or set(weights) != set(ZONE_IDS):
        raise ValueError("zone_attraction_weights must contain exactly Z1-Z9")
    for zone_id in ZONE_IDS:
        row = weights[zone_id]
        if not isinstance(row, Mapping) or set(row) != set(ATTRACTION_FIELDS):
            raise ValueError(f"{zone_id} attraction row must contain exactly {ATTRACTION_FIELDS}")
        for field in ATTRACTION_FIELDS:
            _finite_positive(row[field], f"{zone_id}.{field}")
    for field in ATTRACTION_FIELDS:
        total = sum(float(weights[zone_id][field]) for zone_id in ZONE_IDS)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"{field} must sum to 1 across zones")

    expected_fixed = {
        "work": "work_zone", "medical": "medical_zone", "visit": "family_zone",
        "out_of_home_family_care": "family_zone",
        "out_of_home_family_activity": "family_zone",
    }
    if config.get("fixed_destination_groups") != expected_fixed:
        raise ValueError("fixed_destination_groups does not match the T6 contract")


def _read(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        if field not in item:
            raise ValueError(f"Missing required field: {field}")
        return item[field]
    if not hasattr(item, field):
        raise ValueError(f"Missing required field: {field}")
    return getattr(item, field)


def _stable_seed(seed: Any, *parts: Any) -> int:
    if seed is None or isinstance(seed, (dict, list, set)):
        raise ValueError("seed must be a stable scalar value")
    material = "|".join([repr(seed), *(f"{type(part).__name__}:{part!r}" for part in parts)])
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def _spatial_maps(derived_spatial_config: Mapping[str, Any]) -> Dict[str, Any]:
    zones = derived_spatial_config.get("zones") if isinstance(derived_spatial_config, Mapping) else None
    if not isinstance(zones, Sequence) or isinstance(zones, (str, bytes)):
        raise ValueError("derived_spatial_config.zones must be a sequence")
    by_id = {}
    for zone in zones:
        zone_id = zone.get("zone_id") if isinstance(zone, Mapping) else None
        if zone_id in by_id:
            raise ValueError(f"Duplicate spatial zone: {zone_id}")
        if zone_id not in ZONE_IDS:
            raise ValueError(f"Invalid spatial zone: {zone_id}")
        for field in ("centroid_x", "centroid_y"):
            value = zone.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{zone_id}.{field} must be finite numeric")
        for field in ("mean_intrazonal_distance", "population_weight", "network_distance_multiplier"):
            _finite_positive(zone.get(field), f"{zone_id}.{field}")
        by_id[zone_id] = zone
    if set(by_id) != set(ZONE_IDS):
        raise ValueError("derived_spatial_config must contain exactly Z1-Z9")
    return by_id


def effective_choice_distance(origin_zone: str, destination_zone: str, spatial_by_id: Mapping[str, Any]) -> float:
    if origin_zone not in spatial_by_id or destination_zone not in spatial_by_id:
        raise ValueError("effective distance received an unknown zone")
    origin = spatial_by_id[origin_zone]
    if origin_zone == destination_zone:
        return float(origin["mean_intrazonal_distance"])
    destination = spatial_by_id[destination_zone]
    euclidean = math.hypot(
        float(origin["centroid_x"]) - float(destination["centroid_x"]),
        float(origin["centroid_y"]) - float(destination["centroid_y"]),
    )
    return euclidean * max(
        float(origin.get("network_distance_multiplier", 1.0)),
        float(destination.get("network_distance_multiplier", 1.0)),
    )


def _strictest_family_constraint(purposes: Iterable[str], config: Mapping[str, Any]) -> Dict[str, float]:
    rows = [config["purpose_distance_constraints"][purpose] for purpose in sorted(set(purposes))]
    if not rows:
        raise ValueError("family constraint requires at least one observed family purpose")
    return {
        "soft_limit_km": min(float(row["soft_limit_km"]) for row in rows),
        "extra_decay": max(float(row["extra_decay"]) for row in rows),
        "hard_limit_km": min(float(row["hard_limit_km"]) for row in rows),
    }


def _choose_zone(*, agent_id: Any, random_key: str, home_zone: str, purpose: str, spatial_by_id: Mapping[str, Any], config: Mapping[str, Any], seed: Any, constraint_override: Mapping[str, float] | None = None) -> Tuple[str, Dict[str, Any]]:
    attraction_field = config["purpose_attraction_mapping"][purpose]
    beta = float(config["distance_decay"][config["purpose_distance_mapping"][purpose]])
    constraint = constraint_override or config["purpose_distance_constraints"][purpose]
    soft_limit = float(constraint["soft_limit_km"])
    extra_decay = float(constraint["extra_decay"])
    hard_limit = float(constraint["hard_limit_km"])
    candidates = []
    for zone_id in ZONE_IDS:
        attraction = (
            _finite_positive(spatial_by_id[zone_id]["population_weight"], f"{zone_id}.population_weight")
            if attraction_field == "population_weight"
            else _finite_positive(config["zone_attraction_weights"][zone_id][attraction_field], f"{zone_id}.{attraction_field}")
        )
        distance = effective_choice_distance(home_zone, zone_id, spatial_by_id)
        original_score = attraction * math.exp(-beta * distance)
        adjusted_score = original_score
        if distance > soft_limit:
            adjusted_score *= math.exp(-extra_decay * (distance - soft_limit))
        candidates.append({
            "zone_id": zone_id, "distance": distance,
            "original_score": original_score, "adjusted_score": adjusted_score,
        })
    legal = [candidate for candidate in candidates if candidate["distance"] <= hard_limit]
    excluded_count = len(candidates) - len(legal)
    fallback = False
    if legal:
        rng = random.Random(_stable_seed(seed, agent_id, random_key))
        destination = rng.choices(
            [candidate["zone_id"] for candidate in legal],
            weights=[candidate["adjusted_score"] for candidate in legal],
            k=1,
        )[0]
    else:
        fallback = True
        minimum_distance = min(candidate["distance"] for candidate in candidates)
        nearest = [
            candidate for candidate in candidates
            if math.isclose(candidate["distance"], minimum_distance, rel_tol=0.0, abs_tol=1e-12)
        ]
        best_original = max(candidate["original_score"] for candidate in nearest)
        best = [
            candidate for candidate in nearest
            if math.isclose(candidate["original_score"], best_original, rel_tol=0.0, abs_tol=1e-15)
        ]
        destination = min(best, key=lambda candidate: int(candidate["zone_id"][1:]))["zone_id"]
    return destination, {
        "candidate_exclusion_count": excluded_count,
        "fallback": fallback,
        "soft_limit_km": soft_limit,
        "hard_limit_km": hard_limit,
    }


def _validate_inputs(agents: Iterable[Any], weekly_activities: Iterable[Mapping[str, Any]]) -> Tuple[Dict[Any, Any], List[Mapping[str, Any]], Dict[Any, List[Mapping[str, Any]]]]:
    agent_by_id = {}
    for agent in agents:
        agent_id = _read(agent, "agent_id")
        if agent_id in agent_by_id:
            raise ValueError(f"Duplicate agent_id: {agent_id}")
        home_zone = _read(agent, "home_zone")
        if home_zone not in ZONE_IDS:
            raise ValueError(f"Agent {agent_id} has invalid home_zone: {home_zone}")
        agent_by_id[agent_id] = agent
    records = list(weekly_activities)
    by_agent = defaultdict(list)
    seen_activity_ids = set()
    for activity in records:
        if not isinstance(activity, Mapping):
            raise ValueError("Each activity must be a mapping")
        activity_id = _read(activity, "activity_id")
        if activity_id in seen_activity_ids:
            raise ValueError(f"Duplicate activity_id: {activity_id}")
        seen_activity_ids.add(activity_id)
        agent_id = _read(activity, "agent_id")
        if agent_id not in agent_by_id:
            raise ValueError(f"Activity references unknown agent_id: {agent_id}")
        purpose = _read(activity, "activity_purpose")
        if purpose not in SUPPORTED_PURPOSES:
            raise ValueError(f"Unsupported activity purpose: {purpose}")
        home_zone = _read(activity, "home_zone")
        if home_zone not in ZONE_IDS or home_zone != _read(agent_by_id[agent_id], "home_zone"):
            raise ValueError(f"Activity {activity_id} has invalid or inconsistent home_zone")
        if activity.get("destination_zone") is not None:
            raise ValueError(f"Activity {activity_id} already has destination_zone")
        if purpose == "work" and _read(agent_by_id[agent_id], "work_status") not in {"regular_worker", "part_time_worker"}:
            raise ValueError(f"Agent {agent_id} has work activity with invalid work_status")
        by_agent[agent_id].append(activity)
    return agent_by_id, records, by_agent


def assign_destination_zones_with_audit(agents: Iterable[Any], weekly_activities: Iterable[Mapping[str, Any]], derived_spatial_config: Mapping[str, Any], destination_config: Mapping[str, Any], seed: Any) -> Dict[str, Any]:
    """Assign destinations and return selection-event audit separately."""
    validate_destination_configuration(destination_config)
    spatial_by_id = _spatial_maps(derived_spatial_config)
    agent_by_id, records, by_agent = _validate_inputs(agents, weekly_activities)
    fixed_destinations = {}
    event_audit = defaultdict(lambda: {"selection_event_count": 0, "candidate_exclusion_count": 0, "fallback_count": 0})
    fixed_distribution = {
        "work_zone": Counter(), "medical_zone": Counter(), "family_zone": Counter(),
    }

    def record_event(group: str, diagnostic: Mapping[str, Any]) -> None:
        event_audit[group]["selection_event_count"] += 1
        event_audit[group]["candidate_exclusion_count"] += diagnostic["candidate_exclusion_count"]
        event_audit[group]["fallback_count"] += int(diagnostic["fallback"])

    for agent_id in sorted(by_agent, key=lambda value: (type(value).__name__, repr(value))):
        home_zone = _read(agent_by_id[agent_id], "home_zone")
        purposes = {activity["activity_purpose"] for activity in by_agent[agent_id]}
        if "work" in purposes:
            destination, diagnostic = _choose_zone(
                agent_id=agent_id, random_key="fixed:work_zone", home_zone=home_zone,
                purpose="work", spatial_by_id=spatial_by_id,
                config=destination_config, seed=seed,
            )
            fixed_destinations[(agent_id, "work_zone")] = destination
            fixed_distribution["work_zone"][destination] += 1
            record_event("work", diagnostic)
        if "medical" in purposes:
            destination, diagnostic = _choose_zone(
                agent_id=agent_id, random_key="fixed:medical_zone", home_zone=home_zone,
                purpose="medical", spatial_by_id=spatial_by_id,
                config=destination_config, seed=seed,
            )
            fixed_destinations[(agent_id, "medical_zone")] = destination
            fixed_distribution["medical_zone"][destination] += 1
            record_event("medical", diagnostic)
        family_purposes = purposes & FAMILY_PURPOSES
        if family_purposes:
            strictest = _strictest_family_constraint(family_purposes, destination_config)
            destination, diagnostic = _choose_zone(
                agent_id=agent_id, random_key="fixed:family_zone", home_zone=home_zone,
                purpose="visit", spatial_by_id=spatial_by_id,
                config=destination_config, seed=seed, constraint_override=strictest,
            )
            fixed_destinations[(agent_id, "family_zone")] = destination
            fixed_distribution["family_zone"][destination] += 1
            record_event("family", diagnostic)

    assigned = []
    for original in records:
        agent_id = original["agent_id"]
        purpose = original["activity_purpose"]
        if purpose == "work":
            destination = fixed_destinations[(agent_id, "work_zone")]
        elif purpose == "medical":
            destination = fixed_destinations[(agent_id, "medical_zone")]
        elif purpose in FAMILY_PURPOSES:
            destination = fixed_destinations[(agent_id, "family_zone")]
        else:
            destination, diagnostic = _choose_zone(
                agent_id=agent_id, random_key=f"activity:{original['activity_id']}",
                home_zone=original["home_zone"], purpose=purpose,
                spatial_by_id=spatial_by_id, config=destination_config, seed=seed,
            )
            record_event(purpose, diagnostic)
        copied = deepcopy(dict(original))
        copied["destination_zone"] = destination
        assigned.append(copied)

    by_group = {}
    total_events = total_exclusions = total_fallbacks = 0
    for group in ("work", "medical", "family", "shopping", "social", "leisure"):
        values = event_audit[group]
        events = values["selection_event_count"]
        fallbacks = values["fallback_count"]
        by_group[group] = {
            **values,
            "fallback_share": fallbacks / events if events else 0.0,
        }
        total_events += events
        total_exclusions += values["candidate_exclusion_count"]
        total_fallbacks += fallbacks
    selection_audit = {
        "destination_mechanism": destination_config["destination_mechanism"],
        "selection_event_count": total_events,
        "candidate_exclusion_count": total_exclusions,
        "fallback_count": total_fallbacks,
        "fallback_share": total_fallbacks / total_events if total_events else 0.0,
        "by_selection_group": by_group,
        "agent_level_fixed_destination_distribution": {
            group: dict(sorted(counter.items())) for group, counter in fixed_distribution.items()
        },
    }
    return {"activities": assigned, "selection_audit": selection_audit}


def assign_destination_zones(agents: Iterable[Any], weekly_activities: Iterable[Mapping[str, Any]], derived_spatial_config: Mapping[str, Any], destination_config: Mapping[str, Any], seed: Any) -> List[Dict[str, Any]]:
    """Compatibility interface returning only copied assigned activities."""
    return assign_destination_zones_with_audit(
        agents, weekly_activities, derived_spatial_config, destination_config, seed
    )["activities"]


def build_destination_audit(assigned_activities: Iterable[Mapping[str, Any]], derived_spatial_config: Mapping[str, Any], selection_audit: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Audit effective distances and activity-level demand flows."""
    spatial_by_id = _spatial_maps(derived_spatial_config)
    records = list(assigned_activities)
    purpose_totals = Counter()
    purpose_same = Counter()
    purpose_over20 = Counter()
    purpose_over30 = Counter()
    purpose_distance_sum = Counter()
    home_destination = defaultdict(Counter)
    purpose_destination = defaultdict(Counter)
    purpose_flow = defaultdict(lambda: defaultdict(Counter))
    z7_work = Counter()
    peripheral_medical = {"Z8": Counter(), "Z9": Counter()}
    for activity in records:
        purpose = _read(activity, "activity_purpose")
        home = _read(activity, "home_zone")
        destination = _read(activity, "destination_zone")
        if purpose not in SUPPORTED_PURPOSES or home not in ZONE_IDS or destination not in ZONE_IDS:
            raise ValueError("Cannot audit invalid purpose or zone")
        distance = effective_choice_distance(home, destination, spatial_by_id)
        purpose_totals[purpose] += 1
        purpose_distance_sum[purpose] += distance
        purpose_same[purpose] += home == destination
        purpose_over20[purpose] += distance > 20
        purpose_over30[purpose] += distance > 30
        home_destination[home][destination] += 1
        purpose_destination[purpose][destination] += 1
        purpose_flow[purpose][home][destination] += 1
        if purpose == "work" and home == "Z7":
            z7_work[destination] += 1
        if purpose == "medical" and home in peripheral_medical:
            peripheral_medical[home][destination] += 1
    purpose_audit = {}
    for purpose in sorted(purpose_totals):
        total = purpose_totals[purpose]
        purpose_audit[purpose] = {
            "count": total,
            "average_effective_distance": purpose_distance_sum[purpose] / total,
            "same_zone_count": purpose_same[purpose],
            "same_zone_share": purpose_same[purpose] / total,
            "over_20_km_count": purpose_over20[purpose],
            "over_20_km_share": purpose_over20[purpose] / total,
            "over_30_km_count": purpose_over30[purpose],
            "over_30_km_share": purpose_over30[purpose] / total,
            "destination_distribution": dict(sorted(purpose_destination[purpose].items())),
        }
    result = {
        "total_activities": len(records),
        "purpose_audit": purpose_audit,
        "home_to_destination_distribution": {
            home: dict(sorted(destinations.items()))
            for home, destinations in sorted(home_destination.items())
        },
        "activity_level_demand_flow_distribution": {
            purpose: {
                home: dict(sorted(destinations.items()))
                for home, destinations in sorted(home_rows.items())
            }
            for purpose, home_rows in sorted(purpose_flow.items())
        },
        "z7_worker_destination_distribution": dict(sorted(z7_work.items())),
        "peripheral_medical_destination_distribution": {
            home: dict(sorted(destinations.items()))
            for home, destinations in peripheral_medical.items()
        },
    }
    if selection_audit is not None:
        result["selection_audit"] = deepcopy(dict(selection_audit))
    return result
