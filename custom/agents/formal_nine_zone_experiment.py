"""Formal nine-zone, four-mode Agent transport baseline.

The functional zones remain one continuous city. It reuses the repository's
T1/T6 activity and spatial pipeline, T7-T10 transport layers, and the conserved
ride-hailing fleet from the independent emergence experiment.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, Mapping, Sequence

from custom.agents.agent_population import generate_population_agents
from custom.agents.emergence_experiment import _RideHailingFleet
from custom.agents.leg_generation import build_time_feasible_legs
from custom.agents.trip_planning import generate_seven_day_activity_plans
from custom.spatial.destination_assignment import (
    assign_destination_zones_with_audit,
    load_destination_configuration,
)
from custom.spatial.home_zone_assignment import assign_home_zones
from custom.spatial.zone_configuration import (
    allocate_zone_age_quotas,
    derive_spatial_configuration,
    load_zone_configuration,
)
from custom.transport.dynamic_congestion import (
    calculate_dynamic_congestion_leg_mode_option,
)
from custom.transport.network import build_transport_network, metro_leg_accessibility
from custom.transport.time_supply import (
    load_time_supply_configuration,
    period_supply_parameters,
)
from custom.transport.weather_supply import calculate_weather_adjusted_leg_mode_option


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_agent_experiment.json"
ENABLED_MODES = ("walk", "bus", "metro", "ride_hailing")
WEATHER_SCENARIOS = ("W0", "W1", "W2")


def load_formal_nine_zone_config(
    path: Path | str = DEFAULT_CONFIG_PATH,
) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    validate_formal_nine_zone_config(config)
    return config


def validate_formal_nine_zone_config(config: Mapping[str, Any]) -> None:
    if tuple(config["enabled_modes"]) != ENABLED_MODES:
        raise ValueError("formal baseline must enable exactly walk, bus, metro and ride_hailing")
    if config["policy"] != "P0_no_policy":
        raise ValueError("this entry point is the no-policy P0 baseline")
    if bool(config["activity_behavior_changes_enabled"]):
        raise ValueError("activity behavior changes must be disabled in the transport baseline")
    if set(config["weather_scenarios"]) != set(WEATHER_SCENARIOS):
        raise ValueError("weather scenarios must be W0, W1 and W2")
    zones = {f"Z{index}" for index in range(1, 10)}
    for day_type in ("workday", "rest_day"):
        counts = config["ride_hailing_fleet"]["initial_vehicles_by_day_type"][day_type]
        if set(counts) != zones or any(int(value) < 0 for value in counts.values()):
            raise ValueError(f"{day_type} fleet must contain non-negative counts for Z1-Z9")
    fallback = config["fallback"]
    if int(fallback["maximum_attempts_after_primary"]) != 1:
        raise ValueError("formal baseline permits exactly one fallback attempt")
    if not fallback["preserve_consumed_wait"] or not fallback["remove_failed_mode"]:
        raise ValueError("fallback must preserve consumed wait and remove the failed mode")
    if float(config["ride_hailing_fleet"]["non_capacity_success_probability"]) != 1.0:
        raise ValueError("P0 baseline disables probability-based non-capacity ride failure")
    linkage = config["activity_time_linkage"]
    if set(linkage["reliability_buffer_min"]) != set(ENABLED_MODES):
        raise ValueError("reliability buffers must cover all enabled modes")
    if any(float(value) < 0 for value in linkage["reliability_buffer_min"].values()):
        raise ValueError("reliability buffers must be non-negative")
    if not 0 <= float(linkage["on_time_tolerance_min"]) <= float(linkage["maximum_acceptable_lateness_min"]):
        raise ValueError("arrival thresholds are inconsistent")
    if float(linkage["maximum_early_departure_min"]) <= 0:
        raise ValueError("maximum early departure must be positive")
    if float(linkage["maximum_commute_time_min"]) <= 0:
        raise ValueError("maximum commute time must be positive")
    if float(linkage["lateness_penalty_yuan_per_min"]) < 0:
        raise ValueError("lateness penalty must be non-negative")
    if int(linkage["departure_choice_iterations"]) != 2:
        raise ValueError("formal experiment uses exactly two bounded departure-choice passes")
    concentration = config["departure_time_concentration"]
    factor = float(concentration["compression_factor"])
    if not 0 < factor <= 1:
        raise ValueError("departure-time compression factor must be in (0, 1]")
    for purpose in ("work", "medical"):
        time.fromisoformat(concentration["center_time_by_purpose"][purpose])


def _as_dict(value: Any) -> Dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(vars(value))


def _stable_uniform(seed: int, *parts: Any) -> float:
    payload = "|".join(map(str, (seed, *parts))).encode("utf-8")
    number = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return (number + 0.5) / 2**64


def _stable_gumbel(seed: int, agent_id: Any, leg_id: str, mode: str) -> float:
    uniform = _stable_uniform(seed, agent_id, leg_id, mode, "mode-choice")
    return -math.log(-math.log(uniform))


def _dispatch_priority(seed: int, leg_id: str) -> float:
    return _stable_uniform(seed, leg_id, "ride-dispatch-priority")


def _dispatch_group_rank(
    config: Mapping[str, Any], agent: Mapping[str, Any], leg: Mapping[str, Any],
) -> int:
    """Return the policy rank after actual request time and before the common tie-breaker."""
    policy = str(
        config.get("ride_hailing_fleet", {}).get(
            "dispatch_priority_policy", "P0_first_come",
        )
    )
    if policy == "P0_first_come":
        return 0
    if policy == "P4_elder_priority":
        return 0 if str(agent["age_group"]) == "60+" else 1
    raise ValueError(f"unknown formal ride-hailing dispatch priority policy: {policy}")


def _clock_minutes(moment: datetime) -> float:
    return moment.hour * 60 + moment.minute + moment.second / 60.0


def _bin_start(moment: datetime, bin_minutes: int) -> datetime:
    minute = moment.hour * 60 + moment.minute
    floored = (minute // bin_minutes) * bin_minutes
    return datetime.combine(moment.date(), time()) + timedelta(minutes=floored)


def _events_for(
    config: Mapping[str, Any], weather_scenario: str, day_type: str,
) -> list[Dict[str, Any]]:
    selected_date = date.fromisoformat(config["selected_days"][day_type])
    scenario = config["weather_scenarios"][weather_scenario]
    if scenario["weather_type"] == "normal":
        return []
    events = []
    for start_clock, end_clock in scenario["windows_by_day_type"][day_type]:
        start = datetime.combine(selected_date, time.fromisoformat(start_clock))
        end = datetime.combine(selected_date, time.fromisoformat(end_clock))
        if end <= start:
            end += timedelta(days=1)
        events.append({"weather_type": scenario["weather_type"], "start": start, "end": end})
    return events


def _weather_at_departure(moment: datetime, events: Sequence[Mapping[str, Any]]) -> str:
    for event in events:
        if event["start"] <= moment < event["end"]:
            return str(event["weather_type"])
    return "normal"


def build_formal_nine_zone_inputs(
    *, config: Mapping[str, Any] | None = None, seed: int | None = None,
) -> Dict[str, Any]:
    """Generate one paired population, activity calendar and nine-zone leg set."""
    config = dict(config or load_formal_nine_zone_config())
    validate_formal_nine_zone_config(config)
    seed = int(config["seed"] if seed is None else seed)
    spatial = derive_spatial_configuration(load_zone_configuration())
    spatial_by_id = {row["zone_id"]: row for row in spatial["zones"]}
    quotas = allocate_zone_age_quotas(spatial, total_agents=int(config["total_agents"]))
    population = generate_population_agents(total_agents=int(config["total_agents"]), seed=seed)
    agents = assign_home_zones(population, quotas["quota_matrix"], seed=seed)
    activities = generate_seven_day_activity_plans(
        agents, datetime.fromisoformat(config["week_start"]), seed,
    )
    destination_result = assign_destination_zones_with_audit(
        agents, activities, spatial, load_destination_configuration(), seed,
    )
    timed = build_time_feasible_legs(agents, destination_result["activities"], spatial_by_id)
    selected_dates = {date.fromisoformat(value) for value in config["selected_days"].values()}
    selected_activities = [
        dict(row) for row in timed["activities"]
        if row["planned_start_datetime"].date() in selected_dates
    ]
    selected_legs = [
        dict(row) for row in timed["legs"]
        if row["departure_time"].date() in selected_dates
    ]
    return {
        "seed": seed,
        "agents": [_as_dict(row) for row in agents],
        "activities": selected_activities,
        "legs": selected_legs,
        "spatial": spatial,
        "spatial_by_id": spatial_by_id,
        "destination_audit": destination_result["selection_audit"],
    }


def _raw_option(
    network: Mapping[str, Any], leg: Mapping[str, Any], mode: str,
    events: Sequence[Mapping[str, Any]], *, seed: int,
    excess_flow_pcu_per_hour: float | None = None,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    if mode not in ENABLED_MODES:
        raise ValueError(f"mode is outside the formal experiment choice set: {mode}")
    if excess_flow_pcu_per_hour is None:
        option = calculate_weather_adjusted_leg_mode_option(
            network, leg, mode, events, seed=seed,
        )
        if option["available"]:
            option = dict(option)
            option["final_total_time_min"] = option["weather_adjusted_total_time_min"]
            option["final_in_vehicle_time_min"] = option["weather_adjusted_vehicle_time_min"]
            option["final_speed_kmh"] = (
                float(option["main_network_distance_km"]) /
                (float(option["weather_adjusted_vehicle_time_min"]) / 60.0)
                if float(option["weather_adjusted_vehicle_time_min"]) > 0 else 0.0
            )
            option["scenario_vc"] = None
    else:
        road = config["road_feedback"]
        option = calculate_dynamic_congestion_leg_mode_option(
            network, leg, mode, events,
            excess_flow_pcu_per_hour if mode in {"bus", "ride_hailing"} else None,
            corridor_id=road["representative_corridor_id"],
            direction=road["representative_direction"],
            shared_state_flow_is_aggregated=True,
            seed=seed,
        )
    return dict(option)


def _feeder_leg(
    parent: Mapping[str, Any], zone: str, departure: datetime, distance_km: float,
    endpoint: str,
) -> Dict[str, Any]:
    leg = dict(parent)
    leg.update({
        "leg_id": f"{parent['leg_id']}::{endpoint}-metro-feeder",
        "origin_zone": zone,
        "destination_zone": zone,
        "departure_time": departure,
        "road_network_distance_km": distance_km,
    })
    return leg


def _gateway_feeder_leg(
    parent: Mapping[str, Any], departure: datetime, endpoint: str,
) -> Dict[str, Any]:
    leg = dict(parent)
    origin, destination = (("Z9", "Z6") if endpoint == "origin" else ("Z6", "Z9"))
    leg.update({
        "leg_id": f"{parent['leg_id']}::{endpoint}-z9-gateway-feeder",
        "origin_zone": origin,
        "destination_zone": destination,
        "departure_time": departure,
    })
    return leg


def _option(
    network: Mapping[str, Any], leg: Mapping[str, Any], mode: str,
    events: Sequence[Mapping[str, Any]], *, seed: int,
    excess_flow_pcu_per_hour: float | None = None,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a mode option, composing scheduled-bus feeders into metro itineraries."""
    if mode != "metro":
        return _raw_option(
            network, leg, mode, events, seed=seed,
            excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
        )

    accessibility = metro_leg_accessibility(network, leg, seed=seed)
    topology_leg = dict(leg)
    topology_leg["allow_bus_metro_feeder"] = True
    topology = _raw_option(
        network, topology_leg, "metro", events, seed=seed,
        excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
    )
    defaults = {
        **accessibility,
        "origin_feeder_mode": "walk" if accessibility["metro_origin_accessible"] else "bus",
        "destination_feeder_mode": "walk" if accessibility["metro_destination_accessible"] else "bus",
        "bus_metro_transfer_count": 0,
        "feeder_bus_time_minutes": 0.0,
        "feeder_bus_wait_minutes": 0.0,
        "feeder_bus_access_minutes": 0.0,
        "feeder_bus_in_vehicle_minutes": 0.0,
        "feeder_bus_fare_yuan": 0.0,
        "metro_main_time_minutes": None,
        "origin_feeder_total_time_minutes": 0.0,
        "origin_feeder_access_minutes": 0.0,
        "origin_feeder_wait_minutes": 0.0,
        "destination_feeder_total_time_minutes": 0.0,
        "destination_feeder_access_minutes": 0.0,
        "destination_feeder_wait_minutes": 0.0,
        "feeder_bus_mean_scenario_vc": None,
    }
    topology = {**topology, **defaults}
    if not topology["available"]:
        return topology

    feeder_config = network["config"]["metro_accessibility"]["bus_feeder"]
    transfer_penalty = float(feeder_config["transfer_penalty_min_per_feeder"])
    origin = str(leg["origin_zone"])
    destination = str(leg["destination_zone"])
    origin_local = not accessibility["metro_origin_accessible"] and origin != "Z9"
    destination_local = not accessibility["metro_destination_accessible"] and destination != "Z9"
    origin_direct_access = 0.0 if origin == "Z9" else float(
        network["config"]["zone_service_parameters"][origin]["metro_access_min"]
    )
    destination_direct_access = 0.0 if destination == "Z9" else float(
        network["config"]["zone_service_parameters"][destination]["metro_access_min"]
    )

    local_feeders: list[Dict[str, Any]] = []
    origin_feeder = None
    if origin_local:
        origin_feeder = _raw_option(
            network,
            _feeder_leg(
                leg, origin, leg["departure_time"],
                float(feeder_config["distance_km_by_zone"][origin]), "origin",
            ),
            "bus", events, seed=seed,
            excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
        )
        if not origin_feeder["available"]:
            topology["available"] = False
            return topology
        local_feeders.append(origin_feeder)

    origin_adjustment = (
        float(origin_feeder["final_total_time_min"]) + transfer_penalty - origin_direct_access
        if origin_feeder is not None else 0.0
    )
    if origin_feeder is not None:
        topology_leg["departure_time"] = leg["departure_time"] + timedelta(minutes=origin_adjustment)
        topology = {
            **_raw_option(
                network, topology_leg, "metro", events, seed=seed,
                excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
            ),
            **defaults,
        }
        if not topology["available"]:
            return topology

    destination_feeder = None
    if destination_local:
        destination_start = (
            leg["departure_time"] + timedelta(
                minutes=origin_adjustment + float(topology["final_total_time_min"])
                - destination_direct_access + transfer_penalty
            )
        )
        destination_feeder = _raw_option(
            network,
            _feeder_leg(
                leg, destination, destination_start,
                float(feeder_config["distance_km_by_zone"][destination]), "destination",
            ),
            "bus", events, seed=seed,
            excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
        )
        if not destination_feeder["available"]:
            topology["available"] = False
            return topology
        local_feeders.append(destination_feeder)

    local_access = sum(float(row["access_time_min"]) for row in local_feeders)
    local_wait = sum(float(row["period_wait_time_min"]) for row in local_feeders)
    local_vehicle = sum(float(row["final_in_vehicle_time_min"]) for row in local_feeders)
    local_fare = sum(float(row["fare"]) for row in local_feeders)
    local_distance = sum(float(row["main_network_distance_km"]) for row in local_feeders)
    local_count = len(local_feeders)
    access = (
        float(topology["access_time_min"])
        - (origin_direct_access if origin_local else 0.0)
        - (destination_direct_access if destination_local else 0.0)
        + local_access
    )
    wait = float(topology["period_wait_time_min"]) + local_wait
    vehicle = float(topology["final_in_vehicle_time_min"]) + local_vehicle
    transfer = float(topology["period_transfer_penalty_min"]) + local_count * transfer_penalty
    total = access + wait + vehicle + transfer
    fare = float(topology["fare"]) + local_fare

    gateway_feeders = []
    origin_gateway = None
    destination_gateway = None
    if origin == "Z9":
        origin_gateway = _raw_option(
            network, _gateway_feeder_leg(leg, leg["departure_time"], "origin"),
            "bus", events, seed=seed,
            excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
        )
        gateway_feeders.append(origin_gateway)
    if destination == "Z9":
        destination_gateway = _raw_option(
            network,
            _gateway_feeder_leg(
                leg, leg["departure_time"] + timedelta(minutes=total), "destination",
            ),
            "bus", events, seed=seed,
            excess_flow_pcu_per_hour=excess_flow_pcu_per_hour, config=config,
        )
        gateway_feeders.append(destination_gateway)
    all_feeders = local_feeders + [row for row in gateway_feeders if row["available"]]
    feeder_time = sum(float(row["final_total_time_min"]) for row in all_feeders)
    feeder_wait = sum(float(row["period_wait_time_min"]) for row in all_feeders)
    feeder_access = sum(float(row["access_time_min"]) for row in all_feeders)
    feeder_vehicle = sum(float(row["final_in_vehicle_time_min"]) for row in all_feeders)
    feeder_fare = local_fare + (2.0 * len(gateway_feeders))
    feeder_vcs = [float(row["scenario_vc"]) for row in all_feeders if row.get("scenario_vc") is not None]
    total_distance = float(topology["main_network_distance_km"]) + local_distance
    walk_speed = float(network["config"]["modes"]["walk"]["base_speed_kmh"])
    removed_direct_access_distance = (
        (origin_direct_access if origin_local else 0.0)
        + (destination_direct_access if destination_local else 0.0)
    ) * walk_speed / 60.0
    access_distance = (
        float(topology["access_distance_km"]) - removed_direct_access_distance
        + sum(float(row["access_distance_km"]) for row in local_feeders)
    )
    result = dict(topology)
    result.update({
        **accessibility,
        "access_mode": "mixed_walk_bus" if local_count or gateway_feeders else "walk",
        "access_time_min": access,
        "period_wait_time_min": wait,
        "wait_time_min": wait,
        "period_transfer_penalty_min": transfer,
        "transfer_time_min": transfer,
        "final_in_vehicle_time_min": vehicle,
        "weather_adjusted_vehicle_time_min": vehicle,
        "in_vehicle_time_min": vehicle,
        "final_total_time_min": total,
        "weather_adjusted_total_time_min": total,
        "total_time_min": total,
        "fare": fare,
        "access_fare": float(topology["access_fare"]) + local_fare,
        "main_network_distance_km": total_distance,
        "access_distance_km": access_distance,
        "network_distance_km": total_distance + access_distance,
        "final_speed_kmh": total_distance / (vehicle / 60.0) if vehicle > 0 else 0.0,
        "origin_feeder_mode": "bus" if origin_local or origin == "Z9" else "walk",
        "destination_feeder_mode": "bus" if destination_local or destination == "Z9" else "walk",
        "bus_metro_transfer_count": local_count + int(topology["mode_transfer_count"]),
        "feeder_bus_time_minutes": feeder_time,
        "feeder_bus_wait_minutes": feeder_wait,
        "feeder_bus_access_minutes": feeder_access,
        "feeder_bus_in_vehicle_minutes": feeder_vehicle,
        "feeder_bus_fare_yuan": feeder_fare,
        "metro_main_time_minutes": total - sum(
            float(row["final_total_time_min"]) for row in local_feeders
        ) - local_count * transfer_penalty,
        "origin_feeder_total_time_minutes": 0.0 if (origin_feeder or origin_gateway) is None else float((origin_feeder or origin_gateway)["final_total_time_min"]),
        "origin_feeder_access_minutes": 0.0 if (origin_feeder or origin_gateway) is None else float((origin_feeder or origin_gateway)["access_time_min"]),
        "origin_feeder_wait_minutes": 0.0 if (origin_feeder or origin_gateway) is None else float((origin_feeder or origin_gateway)["period_wait_time_min"]),
        "destination_feeder_total_time_minutes": 0.0 if (destination_feeder or destination_gateway) is None else float((destination_feeder or destination_gateway)["final_total_time_min"]),
        "destination_feeder_access_minutes": 0.0 if (destination_feeder or destination_gateway) is None else float((destination_feeder or destination_gateway)["access_time_min"]),
        "destination_feeder_wait_minutes": 0.0 if (destination_feeder or destination_gateway) is None else float((destination_feeder or destination_gateway)["period_wait_time_min"]),
        "feeder_bus_mean_scenario_vc": mean(feeder_vcs) if feeder_vcs else None,
    })
    return result


def _annotate_schedule_constraints(
    legs: Sequence[Mapping[str, Any]], activities: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    """Attach fixed target arrivals and the earliest feasible departure to inbound legs."""
    by_agent_day: Dict[tuple[Any, date], list[Mapping[str, Any]]] = defaultdict(list)
    for activity in activities:
        by_agent_day[(activity["agent_id"], activity["planned_start_datetime"].date())].append(activity)
    previous_end: Dict[str, datetime | None] = {}
    for rows in by_agent_day.values():
        ordered = sorted(rows, key=lambda row: (row["planned_start_datetime"], row["sequence_order"]))
        prior = None
        for activity in ordered:
            previous_end[activity["activity_id"]] = prior
            prior = activity["planned_end_datetime"]
    maximum_early = float(config["activity_time_linkage"]["maximum_early_departure_min"])
    output = []
    for source in legs:
        leg = dict(source)
        if leg["leg_role"] == "return_home":
            leg.update({"target_arrival_time": None, "earliest_feasible_departure": None})
        else:
            target = leg["arrival_time"]
            earliest = previous_end.get(leg["activity_id"])
            if earliest is None:
                earliest = target - timedelta(minutes=maximum_early)
            else:
                earliest = max(earliest, target - timedelta(minutes=maximum_early))
            leg.update({"target_arrival_time": target, "earliest_feasible_departure": earliest})
        output.append(leg)
    return output


def _schedule_option(
    leg: Mapping[str, Any], mode: str, total_time_min: float, config: Mapping[str, Any],
) -> Dict[str, Any]:
    linkage = config["activity_time_linkage"]
    buffer_min = float(linkage["reliability_buffer_min"][mode])
    target = leg.get("target_arrival_time")
    if target is None:
        return {
            "reliability_buffer_min": buffer_min,
            "planned_departure_time": leg["departure_time"],
            "expected_arrival_time": None,
            "expected_arrival_delay_min": 0.0,
            "schedule_acceptable": True,
        }
    earliest = leg["earliest_feasible_departure"]
    required = target - timedelta(minutes=total_time_min + buffer_min)
    planned_departure = max(required, earliest)
    expected_arrival = planned_departure + timedelta(minutes=total_time_min)
    expected_delay = max(0.0, (expected_arrival - target).total_seconds() / 60.0)
    return {
        "reliability_buffer_min": buffer_min,
        "planned_departure_time": planned_departure,
        "expected_arrival_time": expected_arrival,
        "expected_arrival_delay_min": expected_delay,
        "schedule_acceptable": expected_delay <= float(linkage["maximum_acceptable_lateness_min"]),
    }


def _available_to_agent(
    mode: str, option: Mapping[str, Any], agent: Mapping[str, Any],
    *, coupon_proxy_access: bool = False,
) -> bool:
    if not bool(option["available"]):
        return False
    if mode == "ride_hailing":
        return bool(
            agent["digital_access"] or agent.get("family_assistance")
            or coupon_proxy_access
        )
    return True


def _score_options(
    leg: Mapping[str, Any], agent: Mapping[str, Any], options: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]], config: Mapping[str, Any], seed: int,
    *, excluded_modes: Iterable[str] = (), coupon_available: bool = False,
    coupon_proxy_access: bool = False,
) -> list[Dict[str, Any]]:
    choice = config["mode_choice"]
    excluded = set(excluded_modes)
    rows = []
    for mode in ENABLED_MODES:
        option = options[mode]
        if mode in excluded or not _available_to_agent(
            mode, option, agent, coupon_proxy_access=coupon_proxy_access,
        ):
            continue
        total_time = float(option["final_total_time_min"])
        schedule = _schedule_option(leg, mode, total_time, config)
        if not schedule["schedule_acceptable"]:
            continue
        fare_before_coupon = float(option["fare"])
        coupon_applied = bool(mode == "ride_hailing" and coupon_available)
        multiplier = float(config.get("_coupon_discount_multiplier", 1.0))
        fare = fare_before_coupon * multiplier if coupon_applied else fare_before_coupon
        time_cost = total_time / 60.0 * float(
            choice["value_of_time_yuan_per_hour"][agent["age_group"]]
        )
        lateness_cost = (
            float(schedule["expected_arrival_delay_min"])
            * float(config["activity_time_linkage"]["lateness_penalty_yuan_per_min"])
        )
        utility = -float(choice["generalized_cost_weight"]) * (time_cost + fare + lateness_cost)
        utility += float(choice["age_mode_constant"][agent["age_group"]][mode])
        weather_type = _weather_at_departure(schedule["planned_departure_time"], events)
        utility += float(choice["weather_preference"][weather_type][mode])
        utility += float(choice["random_scale"]) * _stable_gumbel(
            seed, agent["agent_id"], leg["leg_id"], mode,
        )
        rows.append({
            **dict(option), **schedule,
            "fare": fare,
            "fare_before_coupon": fare_before_coupon,
            "coupon_applied_to_choice": coupon_applied,
            "coupon_discount_multiplier": multiplier if coupon_applied else 1.0,
            "coupon_subsidy_yuan": fare_before_coupon - fare,
            "utility": round(utility, 6),
        })
    return sorted(rows, key=lambda row: (-row["utility"], ENABLED_MODES.index(row["mode"])))


def _scheduled_bus_trips_per_bin(
    moment: datetime, network: Mapping[str, Any], config: Mapping[str, Any],
) -> float:
    bin_minutes = int(config["road_feedback"]["time_bin_minutes"])
    supply = period_supply_parameters(
        load_time_supply_configuration(), "bus", "Z1", "Z1", moment,
    )
    if not supply["operating"]:
        return 0.0
    route_count = len(network["config"]["graphs"]["bus"]["routes"])
    directions = 2 if config["bus_system"]["scheduled_routes_in_both_directions"] else 1
    return route_count * directions * bin_minutes / float(supply["headway_min"])


def _new_fleet(config: Mapping[str, Any], day_type: str) -> _RideHailingFleet:
    fleet_config = config["ride_hailing_fleet"]
    return _RideHailingFleet(
        day_type,
        fleet_config["initial_vehicles_by_day_type"][day_type],
        vehicle_id_prefix=fleet_config["vehicle_id_prefix"],
    )


def _preview_successful_rides(
    selected: Sequence[Mapping[str, Any]], agents: Mapping[Any, Mapping[str, Any]],
    config: Mapping[str, Any], day_type: str, seed: int,
) -> list[Mapping[str, Any]]:
    fleet = _new_fleet(config, day_type)
    requests = [row for row in selected if row["chosen_mode"] == "ride_hailing"]
    requests.sort(key=lambda row: (
        row["leg"]["departure_time"],
        _dispatch_group_rank(config, agents[row["leg"]["agent_id"]], row["leg"]),
        _dispatch_priority(seed, row["leg"]["leg_id"]),
        row["leg"]["leg_id"],
    ))
    successful = []
    for row in requests:
        option = row["chosen_option"]
        result = fleet.request(
            request_time=_clock_minutes(row["leg"]["departure_time"]),
            origin_zone=row["leg"]["origin_zone"],
            destination_zone=row["leg"]["destination_zone"],
            base_pickup_wait_min=float(option["period_wait_time_min"]),
            in_vehicle_time_min=float(option["final_in_vehicle_time_min"]),
            maximum_vehicle_wait_min=float(config["ride_hailing_fleet"]["maximum_vehicle_wait_min"]),
            non_capacity_success=True,
        )
        if result["succeeded"]:
            successful.append(row)
    return successful


def _road_flow_by_bin(
    legs: Sequence[Mapping[str, Any]], successful_rides: Sequence[Mapping[str, Any]],
    network: Mapping[str, Any], config: Mapping[str, Any],
) -> Dict[datetime, float]:
    road = config["road_feedback"]
    bin_minutes = int(road["time_bin_minutes"])
    ride_counts = Counter(
        _bin_start(row["leg"]["departure_time"], bin_minutes) for row in successful_rides
    )
    bins = {_bin_start(row["departure_time"], bin_minutes) for row in legs}
    result = {}
    for bin_start in bins:
        bus_trips = _scheduled_bus_trips_per_bin(bin_start, network, config)
        ride_trips = ride_counts[bin_start] * float(road["agent_trip_weight"])
        pcu_per_bin = (
            bus_trips * float(road["bus_vehicle_pcu"])
            + ride_trips * float(road["ride_hailing_vehicle_pcu"])
        )
        result[bin_start] = (
            pcu_per_bin * 60.0 / bin_minutes / float(road["directional_divisor"])
        )
    return result


def _choose_all(
    legs: Sequence[Mapping[str, Any]], agents: Mapping[Any, Mapping[str, Any]],
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any], seed: int,
    flow_by_bin: Mapping[datetime, float] | None = None,
) -> list[Dict[str, Any]]:
    bin_minutes = int(config["road_feedback"]["time_bin_minutes"])
    selected = []
    coupon_allocations = config.get("_coupon_allocations", {})
    coupon_bound_agents: set[int] = set()
    for leg in sorted(legs, key=lambda row: (row["departure_time"], row["leg_id"])):
        agent_id = int(leg["agent_id"])
        allocation = coupon_allocations.get(agent_id, {})
        coupon_available = bool(allocation.get("coupon_awarded")) and agent_id not in coupon_bound_agents
        proxy_access = bool(
            coupon_available and allocation.get("coupon_access_channel") == "community_phone"
        ) or agent_id in config.get("_ride_hailing_proxy_agent_ids", set())
        key = _bin_start(leg["departure_time"], bin_minutes)
        flow = None if flow_by_bin is None else flow_by_bin.get(key)
        if flow_by_bin is not None and flow is None:
            road = config["road_feedback"]
            bus = _scheduled_bus_trips_per_bin(key, network, config)
            flow = (
                bus * float(road["bus_vehicle_pcu"]) * 60.0 / bin_minutes
                / float(road["directional_divisor"])
            )
        options = {
            mode: _option(
                network, leg, mode, events, seed=seed,
                excess_flow_pcu_per_hour=flow, config=config,
            )
            for mode in ENABLED_MODES
        }
        scored = _score_options(
            leg, agents[leg["agent_id"]], options, events, config, seed,
            coupon_available=coupon_available, coupon_proxy_access=proxy_access,
        )
        if not scored:
            selected.append({"leg": leg, "chosen_mode": "", "chosen_option": None, "options": options})
            continue
        chosen = scored[0]
        coupon_bound = bool(chosen["mode"] == "ride_hailing" and chosen["coupon_applied_to_choice"])
        if coupon_bound:
            coupon_bound_agents.add(agent_id)
        selected.append({
            "leg": leg, "chosen_mode": chosen["mode"], "chosen_option": chosen,
            "options": options, "scored_options": scored,
            "coupon_bound_to_primary": coupon_bound,
        })
    return selected


def _reschedule_selected(
    selected: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    """Move each inbound leg to the departure implied by its selected expected option."""
    concentration = config["departure_time_concentration"]
    rows = []
    for source in selected:
        row = dict(source)
        leg = copy.deepcopy(source["leg"])
        option = source.get("chosen_option")
        if option is not None and leg.get("target_arrival_time") is not None:
            planned = option["planned_departure_time"]
            leg["unconcentrated_departure_time"] = planned
            is_concentrated = (
                bool(concentration["enabled"])
                and leg.get("purpose") in {"work", "medical"}
                and leg.get("leg_role") != "return_home"
            )
            if is_concentrated:
                center_clock = time.fromisoformat(
                    concentration["center_time_by_purpose"][leg["purpose"]]
                )
                center = datetime.combine(planned.date(), center_clock)
                factor = float(concentration["compression_factor"])
                compressed = center + (planned - center) * factor
                compressed = max(compressed, leg["earliest_feasible_departure"])
                compressed = min(compressed, leg["target_arrival_time"])
                leg["departure_time"] = compressed
                leg["departure_time_concentrated"] = compressed != planned
            else:
                leg["departure_time"] = planned
                leg["departure_time_concentrated"] = False
        row["leg"] = leg
        rows.append(row)
    return rows


def _refresh_locked_choices(
    selected: Sequence[Mapping[str, Any]], agents: Mapping[Any, Mapping[str, Any]],
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
    flow_by_bin: Mapping[datetime, float], seed: int,
) -> list[Dict[str, Any]]:
    """Re-evaluate the selected mode at its actual planned departure without a third choice pass."""
    bin_minutes = int(config["road_feedback"]["time_bin_minutes"])
    output = []
    coupon_allocations = config.get("_coupon_allocations", {})
    coupon_bound_agents: set[int] = set()
    for source in sorted(selected, key=lambda row: (row["leg"]["departure_time"], row["leg"]["leg_id"])):
        leg = source["leg"]
        mode = source.get("chosen_mode", "")
        agent_id = int(leg["agent_id"])
        allocation = coupon_allocations.get(agent_id, {})
        coupon_available = bool(allocation.get("coupon_awarded")) and agent_id not in coupon_bound_agents
        proxy_access = bool(
            coupon_available and allocation.get("coupon_access_channel") == "community_phone"
        ) or agent_id in config.get("_ride_hailing_proxy_agent_ids", set())
        key = _bin_start(leg["departure_time"], bin_minutes)
        flow = flow_by_bin.get(key)
        if flow is None:
            road = config["road_feedback"]
            bus = _scheduled_bus_trips_per_bin(key, network, config)
            flow = bus * float(road["bus_vehicle_pcu"]) * 60.0 / bin_minutes / float(road["directional_divisor"])
        options = {
            candidate: _option(
                network, leg, candidate, events, seed=seed,
                excess_flow_pcu_per_hour=flow, config=config,
            ) for candidate in ENABLED_MODES
        }
        scored = _score_options(
            leg, agents[leg["agent_id"]], options, events, config, seed,
            coupon_available=coupon_available, coupon_proxy_access=proxy_access,
        )
        locked = next((row for row in scored if row["mode"] == mode), None)
        coupon_bound = bool(
            locked is not None and mode == "ride_hailing"
            and locked["coupon_applied_to_choice"]
        )
        if coupon_bound:
            coupon_bound_agents.add(agent_id)
        output.append({
            "leg": leg,
            "chosen_mode": mode if locked is not None else "",
            "chosen_option": locked,
            "options": options,
            "scored_options": scored,
            "coupon_bound_to_primary": coupon_bound,
        })
    return output


def _fallback(
    row: Mapping[str, Any], consumed_minutes: float, agents: Mapping[Any, Mapping[str, Any]],
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
    flow_by_bin: Mapping[datetime, float], seed: int,
) -> Dict[str, Any] | None:
    failed_mode = row["chosen_mode"]
    updated_leg = copy.deepcopy(row["leg"])
    updated_leg["departure_time"] = updated_leg["departure_time"] + timedelta(minutes=consumed_minutes)
    bin_minutes = int(config["road_feedback"]["time_bin_minutes"])
    key = _bin_start(updated_leg["departure_time"], bin_minutes)
    flow = flow_by_bin.get(key)
    if flow is None:
        bus = _scheduled_bus_trips_per_bin(key, network, config)
        road = config["road_feedback"]
        flow = (
            bus * float(road["bus_vehicle_pcu"]) * 60.0 / bin_minutes
            / float(road["directional_divisor"])
        )
    options = {
        mode: _option(
            network, updated_leg, mode, events, seed=seed,
            excess_flow_pcu_per_hour=flow, config=config,
        ) for mode in ENABLED_MODES
    }
    scored = _score_options(
        updated_leg, agents[updated_leg["agent_id"]], options, events, config, seed,
        excluded_modes={failed_mode},
    )
    return None if not scored else {"leg": updated_leg, "option": scored[0]}


def _itinerary_pattern(mode: str, option: Mapping[str, Any] | None) -> str:
    if not mode or option is None:
        return ""
    if mode == "walk":
        return "walk"
    if mode == "bus":
        return "walk-bus-walk"
    if mode == "ride_hailing":
        return "walk-ride_hailing-walk"
    origin_bus = option.get("origin_feeder_mode") == "bus"
    destination_bus = option.get("destination_feeder_mode") == "bus"
    if origin_bus and destination_bus:
        return "walk-bus-walk-metro-walk-bus-walk"
    if origin_bus:
        return "walk-bus-walk-metro-walk"
    if destination_bus:
        return "walk-metro-walk-bus-walk"
    return "walk-metro-walk"


def _simulate_final_choices(
    selected: Sequence[Mapping[str, Any]], agents: Mapping[Any, Mapping[str, Any]],
    network: Mapping[str, Any], events: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
    flow_by_bin: Mapping[datetime, float], day_type: str, seed: int,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    fleet = _new_fleet(config, day_type)
    ordered = sorted(selected, key=lambda row: (
        row["leg"]["departure_time"],
        _dispatch_group_rank(config, agents[row["leg"]["agent_id"]], row["leg"])
        if row["chosen_mode"] == "ride_hailing" else -1,
        _dispatch_priority(seed, row["leg"]["leg_id"])
        if row["chosen_mode"] == "ride_hailing" else -1.0,
        row["leg"]["leg_id"],
    ))
    results: list[Dict[str, Any]] = []
    dispatch_rows: list[Dict[str, Any]] = []
    for row in ordered:
        leg = row["leg"]
        mode = row["chosen_mode"]
        option = row["chosen_option"]
        primary_failed = not bool(option)
        failure_reason = "no_available_mode" if primary_failed else ""
        consumed = 0.0
        dispatch = None
        if mode == "ride_hailing" and option is not None:
            dispatch_policy = str(
                config["ride_hailing_fleet"].get(
                    "dispatch_priority_policy", "P0_first_come",
                )
            )
            dispatch_group_rank = _dispatch_group_rank(
                config, agents[leg["agent_id"]], leg,
            )
            base_dispatch_priority = _dispatch_priority(seed, leg["leg_id"])
            dispatch = fleet.request(
                request_time=_clock_minutes(leg["departure_time"]),
                origin_zone=leg["origin_zone"], destination_zone=leg["destination_zone"],
                base_pickup_wait_min=float(option["period_wait_time_min"]),
                in_vehicle_time_min=float(option["final_in_vehicle_time_min"]),
                maximum_vehicle_wait_min=float(config["ride_hailing_fleet"]["maximum_vehicle_wait_min"]),
                non_capacity_success=True,
            )
            primary_failed = not dispatch["succeeded"]
            failure_reason = dispatch["failure_reason"]
            consumed = float(dispatch["pickup_wait_min"]) if primary_failed else 0.0
            dispatch_rows.append({
                "leg_id": leg["leg_id"], "agent_id": leg["agent_id"],
                "request_time": leg["departure_time"],
                "dispatch_priority": round(base_dispatch_priority, 9),
                "dispatch_priority_policy": dispatch_policy,
                "dispatch_priority_group_rank": dispatch_group_rank,
                "effective_dispatch_priority": round(
                    dispatch_group_rank + base_dispatch_priority, 9
                ),
                "age_group": agents[leg["agent_id"]]["age_group"],
                "origin_zone": leg["origin_zone"], "destination_zone": leg["destination_zone"],
                "coupon_bound": bool(row.get("coupon_bound_to_primary")),
                "fare_before_coupon_yuan": round(float(option.get("fare_before_coupon", option["fare"])), 2),
                "fare_after_coupon_yuan": round(float(option["fare"]), 2),
                "coupon_subsidy_yuan": round(float(option.get("coupon_subsidy_yuan", 0.0)), 2),
                **dispatch,
            })

        fallback_attempted = False
        fallback_succeeded = False
        final_mode = mode
        final_option = option
        if primary_failed:
            fallback_attempted = True
            fallback = _fallback(
                row, consumed, agents, network, events, config, flow_by_bin, seed,
            )
            if fallback is not None:
                final_option = fallback["option"]
                final_mode = final_option["mode"]
                fallback_succeeded = True
        succeeded = bool(final_option) and (not primary_failed or fallback_succeeded)
        actual_wait = 0.0
        if succeeded and final_mode in {"bus", "metro"}:
            actual_wait = float(final_option["period_wait_time_min"])
        elif succeeded and final_mode == "ride_hailing" and dispatch:
            actual_wait = float(dispatch["pickup_wait_min"])
        total_time = None
        fare = None
        actual_arrival = None
        arrival_delay = None
        on_time_arrival = None
        activity_completed = None
        late_but_reached = False
        maximum_commute_time_exceeded = False
        maximum_lateness_exceeded = False
        completion_failure_reason = ""
        if succeeded:
            total_time = float(final_option["final_total_time_min"]) + consumed
            if final_mode == "ride_hailing" and dispatch:
                total_time += float(dispatch["pickup_wait_min"]) - float(final_option["period_wait_time_min"])
            fare = float(final_option["fare"])
            if leg.get("target_arrival_time") is not None:
                actual_arrival = leg["departure_time"] + timedelta(minutes=total_time)
                arrival_delay = max(
                    0.0,
                    (actual_arrival - leg["target_arrival_time"]).total_seconds() / 60.0,
                )
                on_time_arrival = arrival_delay <= float(
                    config["activity_time_linkage"]["on_time_tolerance_min"]
                )
                late_but_reached = not on_time_arrival
                maximum_commute_time_exceeded = total_time > float(
                    config["activity_time_linkage"]["maximum_commute_time_min"]
                )
                maximum_lateness_exceeded = arrival_delay > float(
                    config["activity_time_linkage"]["maximum_acceptable_lateness_min"]
                )
                activity_completed = not (
                    maximum_commute_time_exceeded or maximum_lateness_exceeded
                )
                if maximum_commute_time_exceeded and maximum_lateness_exceeded:
                    completion_failure_reason = "commute_and_lateness_limits_exceeded"
                elif maximum_commute_time_exceeded:
                    completion_failure_reason = "maximum_commute_time_exceeded"
                elif maximum_lateness_exceeded:
                    completion_failure_reason = "maximum_lateness_exceeded"
        metro_access = row["options"]["metro"]
        results.append({
            "leg_id": leg["leg_id"], "agent_id": leg["agent_id"],
            "date": leg["date"], "day": leg["day"], "leg_role": leg["leg_role"],
            "activity_id": leg["activity_id"], "purpose": leg["purpose"],
            "origin_zone": leg["origin_zone"], "destination_zone": leg["destination_zone"],
            "departure_time": leg["departure_time"],
            "planned_activity_start_time": leg.get("target_arrival_time"),
            "earliest_feasible_departure": leg.get("earliest_feasible_departure"),
            "unconcentrated_departure_time": leg.get("unconcentrated_departure_time"),
            "departure_time_concentrated": bool(leg.get("departure_time_concentrated", False)),
            "reliability_buffer_min": None if final_option is None else final_option.get("reliability_buffer_min"),
            "expected_arrival_time": (
                None if not succeeded or leg.get("target_arrival_time") is None
                else leg["departure_time"] + timedelta(minutes=float(final_option["final_total_time_min"]))
            ),
            "actual_arrival_time": actual_arrival,
            "arrival_delay_minutes": None if arrival_delay is None else round(arrival_delay, 3),
            "on_time_arrival": on_time_arrival,
            "activity_completed": activity_completed,
            "late_but_reached": late_but_reached,
            "maximum_commute_time_exceeded": maximum_commute_time_exceeded,
            "maximum_lateness_exceeded": maximum_lateness_exceeded,
            "completion_failure_reason": completion_failure_reason,
            "metro_origin_accessible": metro_access["metro_origin_accessible"],
            "metro_destination_accessible": metro_access["metro_destination_accessible"],
            "origin_feeder_mode": None if final_option is None else final_option.get("origin_feeder_mode"),
            "destination_feeder_mode": None if final_option is None else final_option.get("destination_feeder_mode"),
            "bus_metro_transfer_count": 0 if final_option is None else int(final_option.get("bus_metro_transfer_count") or 0),
            "feeder_bus_time_minutes": 0.0 if final_option is None else round(float(final_option.get("feeder_bus_time_minutes") or 0.0), 3),
            "feeder_bus_wait_minutes": 0.0 if final_option is None else round(float(final_option.get("feeder_bus_wait_minutes") or 0.0), 3),
            "feeder_bus_access_minutes": 0.0 if final_option is None else round(float(final_option.get("feeder_bus_access_minutes") or 0.0), 3),
            "feeder_bus_in_vehicle_minutes": 0.0 if final_option is None else round(float(final_option.get("feeder_bus_in_vehicle_minutes") or 0.0), 3),
            "feeder_bus_fare_yuan": 0.0 if final_option is None else round(float(final_option.get("feeder_bus_fare_yuan") or 0.0), 2),
            "metro_main_time_minutes": None if final_option is None else (
                None if final_option.get("metro_main_time_minutes") is None
                else round(float(final_option["metro_main_time_minutes"]), 3)
            ),
            "origin_feeder_total_time_minutes": 0.0 if final_option is None else round(float(final_option.get("origin_feeder_total_time_minutes") or 0.0), 3),
            "origin_feeder_access_minutes": 0.0 if final_option is None else round(float(final_option.get("origin_feeder_access_minutes") or 0.0), 3),
            "origin_feeder_wait_minutes": 0.0 if final_option is None else round(float(final_option.get("origin_feeder_wait_minutes") or 0.0), 3),
            "destination_feeder_total_time_minutes": 0.0 if final_option is None else round(float(final_option.get("destination_feeder_total_time_minutes") or 0.0), 3),
            "destination_feeder_access_minutes": 0.0 if final_option is None else round(float(final_option.get("destination_feeder_access_minutes") or 0.0), 3),
            "destination_feeder_wait_minutes": 0.0 if final_option is None else round(float(final_option.get("destination_feeder_wait_minutes") or 0.0), 3),
            "final_attempt_departure_time": leg["departure_time"] + timedelta(minutes=consumed),
            "primary_mode": mode, "primary_failed": primary_failed,
            "primary_failure_reason": failure_reason,
            "fallback_attempted": fallback_attempted,
            "fallback_succeeded": fallback_succeeded,
            "final_mode": final_mode if succeeded else "",
            "itinerary_pattern": _itinerary_pattern(final_mode, final_option) if succeeded else "",
            "transport_succeeded": succeeded,
            "wait_minutes": None if not succeeded else round(actual_wait, 3),
            "failed_attempt_consumed_minutes": round(consumed, 3),
            "cumulative_wait_minutes": round(consumed + actual_wait, 3),
            "access_time_min": None if not succeeded else round(float(final_option["access_time_min"]), 3),
            "transfer_time_min": None if not succeeded else round(float(final_option["period_transfer_penalty_min"]), 3),
            "in_vehicle_time_min": None if not succeeded else round(float(final_option["final_in_vehicle_time_min"]), 3),
            "total_travel_time_min": None if total_time is None else round(total_time, 3),
            "fare_yuan": None if fare is None else round(fare, 2),
            "fare_before_coupon_yuan": None if final_option is None else round(
                float(final_option.get("fare_before_coupon", final_option["fare"])), 2
            ),
            "coupon_bound": bool(row.get("coupon_bound_to_primary")),
            "coupon_redeemed": bool(
                row.get("coupon_bound_to_primary") and mode == "ride_hailing"
                and not primary_failed
            ),
            "coupon_expired_after_failed_request": bool(
                row.get("coupon_bound_to_primary") and mode == "ride_hailing"
                and primary_failed
            ),
            "coupon_subsidy_yuan": round(
                float(option.get("coupon_subsidy_yuan", 0.0))
                if option is not None and row.get("coupon_bound_to_primary") and not primary_failed
                else 0.0,
                2,
            ),
            "final_speed_kmh": None if not succeeded else round(float(final_option["final_speed_kmh"]), 3),
            "scenario_vc": None if not succeeded else final_option.get("scenario_vc"),
            "weather_type_at_departure": _weather_at_departure(leg["departure_time"], events),
        })
    end_states = fleet.states(24 * 60)
    if len(end_states) != fleet.initial_total:
        raise AssertionError("ride-hailing vehicle conservation failed")
    return sorted(results, key=lambda row: row["leg_id"]), dispatch_rows, end_states


def _scenario_summary(
    results: Sequence[Mapping[str, Any]], activities: Sequence[Mapping[str, Any]],
    dispatch: Sequence[Mapping[str, Any]], end_states: Sequence[Mapping[str, Any]],
    network: Mapping[str, Any], config: Mapping[str, Any], weather_scenario: str,
    day_type: str, flow_by_bin: Mapping[datetime, float], seed: int,
) -> Dict[str, Any]:
    successful = [row for row in results if row["transport_succeeded"]]
    counts = Counter(row["final_mode"] for row in successful)
    mode_total = len(successful)
    bus_rows = [row for row in successful if row["final_mode"] == "bus"]
    metro_rows = [row for row in successful if row["final_mode"] == "metro"]
    feeder_metro_rows = [
        row for row in metro_rows if int(row.get("bus_metro_transfer_count") or 0) > 0
    ]
    ride_rows = [row for row in successful if row["final_mode"] == "ride_hailing"]
    road_rows = [row for row in successful if row["final_mode"] in {"bus", "ride_hailing"}]
    inbound_rows = [row for row in successful if row["leg_role"] != "return_home"]
    on_time_rows = [row for row in inbound_rows if row["on_time_arrival"]]
    bus_inbound = [row for row in inbound_rows if row["final_mode"] == "bus"]
    bus_late = [row for row in bus_inbound if not row["on_time_arrival"]]
    interzonal = [row for row in successful if row["origin_zone"] != row["destination_zone"]]
    interzonal_metro = [row for row in interzonal if row["final_mode"] == "metro"]
    selected_date = date.fromisoformat(config["selected_days"][day_type])
    day_activities = [row for row in activities if row["planned_start_datetime"].date() == selected_date]
    inbound_success = {
        row["activity_id"]: bool(row["activity_completed"])
        for row in results if row["leg_role"] != "return_home"
    }
    inbound_transport_success = {
        row["activity_id"]: bool(row["transport_succeeded"])
        for row in results if row["leg_role"] != "return_home"
    }
    completed_activities = sum(inbound_success.get(row["activity_id"], False) for row in day_activities)
    necessary = [row for row in day_activities if row["is_mandatory"]]
    completed_necessary = sum(inbound_success.get(row["activity_id"], False) for row in necessary)
    initial_counts = config["ride_hailing_fleet"]["initial_vehicles_by_day_type"][day_type]
    idle_by_zone = Counter(row["current_zone"] for row in end_states if row["status"] == "idle")
    bin_minutes = int(config["road_feedback"]["time_bin_minutes"])
    service_day_start = datetime.combine(selected_date, time())
    bus_scheduled = sum(
        _scheduled_bus_trips_per_bin(
            service_day_start + timedelta(minutes=offset), network, config,
        )
        for offset in range(0, 24 * 60, bin_minutes)
    )
    successful_dispatch = [row for row in dispatch if row["succeeded"]]
    actual_road_vehicle_volume = bus_scheduled + len(successful_dispatch)
    return {
        "seed": seed, "policy": config["policy"],
        "experiment_condition": config.get("experiment_condition", "P0_baseline"),
        "weather_scenario": weather_scenario,
        "weather_type": config["weather_scenarios"][weather_scenario]["weather_type"],
        "day_type": day_type,
        "agent_count": int(config["total_agents"]),
        "planned_activities": len(day_activities),
        "completed_activities": completed_activities,
        "activity_completion_rate": round(completed_activities / len(day_activities), 6) if day_activities else None,
        "planned_necessary_activities": len(necessary),
        "completed_necessary_activities": completed_necessary,
        "necessary_activity_completion_rate": round(completed_necessary / len(necessary), 6) if necessary else None,
        "weather_cancelled_activities": 0,
        "transport_unmet_activities": sum(
            not row["transport_succeeded"] for row in results if row["leg_role"] != "return_home"
        ),
        "necessary_transport_unmet_activities": sum(
            not inbound_transport_success.get(row["activity_id"], False) for row in necessary
        ),
        "planned_legs": len(results),
        "successful_legs": len(successful), "transport_unmet_legs": len(results) - len(successful),
        "transport_success_rate": round(len(successful) / len(results), 6) if results else None,
        "walking_legs": counts["walk"], "bus_legs": counts["bus"],
        "metro_legs": counts["metro"],
        "ride_hailing_legs": counts["ride_hailing"],
        "walking_mode_share": round(counts["walk"] / mode_total, 6) if mode_total else None,
        "bus_mode_share": round(counts["bus"] / mode_total, 6) if mode_total else None,
        "metro_mode_share": round(counts["metro"] / mode_total, 6) if mode_total else None,
        "ride_hailing_mode_share": round(counts["ride_hailing"] / mode_total, 6) if mode_total else None,
        "ride_hailing_requests": len(dispatch),
        "successful_ride_hailing_requests": len(successful_dispatch),
        "failed_ride_hailing_requests": len(dispatch) - len(successful_dispatch),
        "ride_hailing_failed": len(dispatch) - len(successful_dispatch),
        "fallback_attempts": sum(row["fallback_attempted"] for row in results),
        "fallback_successes": sum(row["fallback_succeeded"] for row in results),
        "fallback_succeeded": sum(row["fallback_succeeded"] for row in results),
        "fallback_failed": sum(
            row["fallback_attempted"] and not row["fallback_succeeded"] for row in results
        ),
        "late_but_reached": sum(
            row["late_but_reached"] for row in results if row["leg_role"] != "return_home"
        ),
        "reached_but_activity_incomplete": sum(
            row["transport_succeeded"] and not row["activity_completed"]
            for row in results if row["leg_role"] != "return_home"
        ),
        "transport_unmet": sum(
            not row["transport_succeeded"] for row in results if row["leg_role"] != "return_home"
        ),
        "mandatory_activity_incomplete": len(necessary) - completed_necessary,
        "mean_bus_wait_minutes": round(mean(row["wait_minutes"] for row in bus_rows), 3) if bus_rows else None,
        "mean_metro_wait_minutes": round(mean(row["wait_minutes"] for row in metro_rows), 3) if metro_rows else None,
        "bus_metro_transfer_legs": len(feeder_metro_rows),
        "bus_metro_transfer_share_of_metro": round(len(feeder_metro_rows) / len(metro_rows), 6) if metro_rows else None,
        "mean_feeder_bus_time_minutes": round(mean(row["feeder_bus_time_minutes"] for row in feeder_metro_rows), 3) if feeder_metro_rows else None,
        "mean_ride_hailing_wait_minutes": round(mean(row["wait_minutes"] for row in ride_rows), 3) if ride_rows else None,
        "mean_total_travel_time": round(mean(row["total_travel_time_min"] for row in successful), 3) if successful else None,
        "mean_fare_yuan": round(mean(row["fare_yuan"] for row in successful), 3) if successful else None,
        "on_time_arrivals": len(on_time_rows),
        "on_time_arrival_rate": round(len(on_time_rows) / len(inbound_rows), 6) if inbound_rows else None,
        "bus_activity_arrivals": len(bus_inbound),
        "bus_late_arrivals": len(bus_late),
        "bus_late_arrival_rate": round(len(bus_late) / len(bus_inbound), 6) if bus_inbound else None,
        "interzonal_metro_legs": len(interzonal_metro),
        "interzonal_metro_share": round(len(interzonal_metro) / len(interzonal), 6) if interzonal else None,
        "scheduled_bus_vehicle_trips": round(bus_scheduled, 3),
        "bus_demand": counts["bus"],
        "bus_feeder_boardings": sum(int(row.get("bus_metro_transfer_count") or 0) for row in metro_rows),
        "total_bus_passenger_boardings": counts["bus"] + sum(
            int(row.get("bus_metro_transfer_count") or 0) for row in metro_rows
        ),
        "successful_ride_hailing_vehicle_trips": len(successful_dispatch),
        "road_vehicle_volume": round(actual_road_vehicle_volume, 3),
        "mean_volume_capacity_ratio": round(mean(float(row["scenario_vc"]) for row in road_rows if row["scenario_vc"] is not None), 6) if any(row["scenario_vc"] is not None for row in road_rows) else None,
        "mean_road_speed_kmh": round(mean(float(row["final_speed_kmh"]) for row in road_rows), 3) if road_rows else None,
        "intrazonal_successful_legs": sum(row["origin_zone"] == row["destination_zone"] for row in successful),
        "interzonal_successful_legs": sum(row["origin_zone"] != row["destination_zone"] for row in successful),
        "initial_ride_hailing_vehicles": sum(int(value) for value in initial_counts.values()),
        "end_idle_vehicles": sum(row["status"] == "idle" for row in end_states),
        "end_busy_vehicles": sum(row["status"] == "busy" for row in end_states),
        "end_idle_by_zone": json.dumps({zone: idle_by_zone[zone] for zone in sorted(initial_counts)}, ensure_ascii=False),
        "feedback_flow_is_first_round_successful_rides": True,
        "metro_enabled": True,
    }


def _activity_results(
    activities: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any], weather_scenario: str, day_type: str,
) -> list[Dict[str, Any]]:
    selected_date = date.fromisoformat(config["selected_days"][day_type])
    inbound = {
        row["activity_id"]: row for row in results if row["leg_role"] != "return_home"
    }
    rows = []
    for activity in activities:
        if activity["planned_start_datetime"].date() != selected_date:
            continue
        leg = inbound.get(activity["activity_id"])
        transport_succeeded = bool(leg and leg["transport_succeeded"])
        completed = bool(leg and leg["activity_completed"])
        final_status = (
            "completed" if completed else
            "reached_but_activity_incomplete" if transport_succeeded else
            "transport_unmet"
        )
        rows.append({
            "weather_scenario": weather_scenario, "day_type": day_type,
            "policy": config["policy"], "agent_id": activity["agent_id"],
            "activity_id": activity["activity_id"],
            "activity_purpose": activity["activity_purpose"],
            "is_mandatory": activity["is_mandatory"],
            "origin_zone": None if leg is None else leg["origin_zone"],
            "destination_zone": activity["destination_zone"],
            "final_status": final_status,
            "weather_cancelled": False, "transport_unmet": not transport_succeeded,
            "completed": completed,
            "transport_succeeded": transport_succeeded,
            "activity_completed": completed,
            "late_but_reached": False if leg is None else leg["late_but_reached"],
            "mandatory_activity_incomplete": bool(activity["is_mandatory"] and not completed),
            "completion_failure_reason": None if leg is None else leg["completion_failure_reason"],
            "actual_arrival_time": None if leg is None else leg["actual_arrival_time"],
            "arrival_delay_minutes": None if leg is None else leg["arrival_delay_minutes"],
            "on_time_arrival": None if leg is None else leg["on_time_arrival"],
        })
    return rows


def run_formal_nine_zone_baseline(
    *, config: Mapping[str, Any] | None = None, seed: int | None = None,
) -> Dict[str, Any]:
    """Run W0/W1/W2 for one workday and one rest day under P0."""
    config = dict(config or load_formal_nine_zone_config())
    validate_formal_nine_zone_config(config)
    inputs = build_formal_nine_zone_inputs(config=config, seed=seed)
    seed = int(inputs["seed"])
    all_results: list[Dict[str, Any]] = []
    all_activity_results: list[Dict[str, Any]] = []
    all_dispatch: list[Dict[str, Any]] = []
    all_states: list[Dict[str, Any]] = []
    summaries: list[Dict[str, Any]] = []
    for weather_scenario in WEATHER_SCENARIOS:
        for day_type, day_value in config["selected_days"].items():
            selected_date = date.fromisoformat(day_value)
            legs = [row for row in inputs["legs"] if row["departure_time"].date() == selected_date]
            activities = [
                row for row in inputs["activities"]
                if row["planned_start_datetime"].date() == selected_date
            ]
            scenario = run_formal_transport_scenario(
                inputs, config=config, weather_scenario=weather_scenario,
                day_type=day_type, activities=activities, legs=legs, seed=seed,
            )
            all_results.extend(scenario["mode_choices"])
            all_activity_results.extend(scenario["activity_results"])
            all_dispatch.extend(scenario["ride_hailing_dispatch"])
            all_states.extend(scenario["vehicle_end_states"])
            summaries.append(scenario["summary"])
    return {
        "config": config, "inputs": inputs, "activity_results": all_activity_results,
        "mode_choices": all_results,
        "ride_hailing_dispatch": all_dispatch, "vehicle_end_states": all_states,
        "summary_rows": summaries,
    }


def run_formal_transport_scenario(
    inputs: Mapping[str, Any], *, config: Mapping[str, Any],
    weather_scenario: str, day_type: str,
    activities: Sequence[Mapping[str, Any]], legs: Sequence[Mapping[str, Any]],
    seed: int | None = None,
) -> Dict[str, Any]:
    """Run one paired weather/day transport scenario for supplied main-pipeline OD legs."""
    validate_formal_nine_zone_config(config)
    if weather_scenario not in WEATHER_SCENARIOS:
        raise ValueError(f"unknown weather scenario: {weather_scenario}")
    if day_type not in config["selected_days"]:
        raise ValueError(f"unknown day type: {day_type}")
    seed = int(inputs["seed"] if seed is None else seed)
    agents = {row["agent_id"]: row for row in inputs["agents"]}
    network = build_transport_network()
    events = _events_for(config, weather_scenario, day_type)
    current_legs = _annotate_schedule_constraints(legs, activities, config)
    flow_by_bin = None
    scheduled_rows = []
    for _ in range(int(config["activity_time_linkage"]["departure_choice_iterations"])):
        choices = _choose_all(
            current_legs, agents, network, events, config, seed, flow_by_bin,
        )
        scheduled_rows = _reschedule_selected(choices, config)
        current_legs = [row["leg"] for row in scheduled_rows]
        preview_success = _preview_successful_rides(
            scheduled_rows, agents, config, day_type, seed,
        )
        flow_by_bin = _road_flow_by_bin(current_legs, preview_success, network, config)
    final_choices = _refresh_locked_choices(
        scheduled_rows, agents, network, events, config, flow_by_bin, seed,
    )
    results, dispatch, states = _simulate_final_choices(
        final_choices, agents, network, events, config, flow_by_bin, day_type, seed,
    )
    for row in results:
        row.update({
            "weather_scenario": weather_scenario, "day_type": day_type,
            "policy": config["policy"],
            "experiment_condition": config.get("experiment_condition", "P0_baseline"),
        })
    for row in dispatch:
        row.update({
            "weather_scenario": weather_scenario, "day_type": day_type,
            "policy": config["policy"],
            "experiment_condition": config.get("experiment_condition", "P0_baseline"),
        })
    for row in states:
        row.update({
            "weather_scenario": weather_scenario, "policy": config["policy"],
            "experiment_condition": config.get("experiment_condition", "P0_baseline"),
        })
    return {
        "mode_choices": results,
        "activity_results": _activity_results(
            activities, results, config, weather_scenario, day_type,
        ),
        "ride_hailing_dispatch": dispatch,
        "vehicle_end_states": states,
        "flow_by_bin": flow_by_bin,
        "summary": _scenario_summary(
            results, activities, dispatch, states, network, config,
            weather_scenario, day_type, flow_by_bin, seed,
        ),
    }
