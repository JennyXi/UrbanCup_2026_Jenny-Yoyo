"""Config-driven Z1-Z9 multimodal network without Agent mode choice."""

from __future__ import annotations

import heapq
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from custom.spatial.zone_configuration import derive_spatial_configuration, load_zone_configuration


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "multimodal_transport_network.json"
MODES = ("walk", "bus", "metro", "ride_hailing")
OUTPUT_FIELDS = (
    "origin_zone",
    "destination_zone",
    "mode",
    "available",
    "access_mode",
    "euclidean_distance_km",
    "road_network_distance_km",
    "main_network_distance_km",
    "access_distance_km",
    "network_distance_km",
    "in_vehicle_time_min",
    "access_time_min",
    "wait_time_min",
    "transfer_time_min",
    "total_time_min",
    "main_fare",
    "access_fare",
    "fare",
    "line_transfer_count",
    "mode_transfer_count",
    "transfers",
)


def load_transport_configuration(path: Path | str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _edge_key(left: str, right: str) -> Tuple[str, str]:
    return tuple(sorted((left, right)))


def _road_edges(zones: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], float]:
    zone_by_id = {zone["zone_id"]: zone for zone in zones}
    edges: Dict[Tuple[str, str], float] = {}
    for origin in zones:
        for destination_id in origin.get("connected_to", []):
            destination = zone_by_id[destination_id]
            euclidean = math.hypot(
                origin["centroid_x"] - destination["centroid_x"],
                origin["centroid_y"] - destination["centroid_y"],
            )
            multiplier = max(
                float(origin.get("network_distance_multiplier", 1.0)),
                float(destination.get("network_distance_multiplier", 1.0)),
            )
            edges[_edge_key(origin["zone_id"], destination_id)] = euclidean * multiplier
    return edges


def _road_adjacency(zone_ids: Iterable[str], edges: Mapping[Tuple[str, str], float]):
    adjacency = {zone_id: [] for zone_id in zone_ids}
    for (left, right), distance in edges.items():
        adjacency[left].append((right, distance))
        adjacency[right].append((left, distance))
    return adjacency


def _shortest_road_distance(
    adjacency: Mapping[str, Sequence[Tuple[str, float]]], origin: str, destination: str
) -> Optional[float]:
    """Return the shortest distance along configured road edges."""
    if origin == destination:
        return 0.0
    queue = [(0.0, origin)]
    best = {origin: 0.0}
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > best[node] + 1e-12:
            continue
        if node == destination:
            return distance
        for neighbour, edge_distance in adjacency.get(node, []):
            candidate = distance + edge_distance
            if candidate + 1e-12 < best.get(neighbour, math.inf):
                best[neighbour] = candidate
                heapq.heappush(queue, (candidate, neighbour))
    return None


def _service_adjacency(
    services: Sequence[Mapping[str, Any]],
    id_field: str,
    road_edges: Mapping[Tuple[str, str], float],
    distance_factor: float,
) -> Dict[str, List[Tuple[str, str, float]]]:
    adjacency: Dict[str, List[Tuple[str, str, float]]] = {}
    for service in services:
        service_id = service[id_field]
        for left, right in zip(service["zones"], service["zones"][1:]):
            key = _edge_key(left, right)
            if key not in road_edges:
                raise ValueError(f"{service_id} uses non-road edge {left}-{right}")
            distance = road_edges[key] * distance_factor
            adjacency.setdefault(left, []).append((right, service_id, distance))
            adjacency.setdefault(right, []).append((left, service_id, distance))
    return adjacency


def _validate_configuration(config: Mapping[str, Any], zones: Sequence[Mapping[str, Any]]) -> None:
    zone_ids = {zone["zone_id"] for zone in zones}
    if tuple(config.get("supported_modes", ())) != MODES:
        raise ValueError(f"supported_modes must be exactly {MODES}")
    for mode in MODES:
        params = config["modes"][mode]
        if float(params.get("base_speed_kmh", 0)) <= 0 or "speed_kmh" in params:
            raise ValueError(f"{mode} must define only a positive base_speed_kmh")
    if config["graphs"]["road"].get("intrazonal_distance_source") != "derived_spatial.mean_intrazonal_distance":
        raise ValueError("intrazonal distance must reuse derived_spatial.mean_intrazonal_distance")
    if set(config.get("zone_service_parameters", {})) != zone_ids:
        raise ValueError("zone_service_parameters must cover every zone exactly once")
    intrazonal = config.get("intrazonal_services", {})
    if set(intrazonal) != zone_ids:
        raise ValueError("intrazonal_services must cover every zone exactly once")
    expected_intrazonal_fields = {"road", "bus", "metro", "ride_hailing"}
    for zone_id, service in intrazonal.items():
        if set(service) != expected_intrazonal_fields or any(not isinstance(value, bool) for value in service.values()):
            raise ValueError(f"{zone_id} intrazonal_services must contain four boolean mode fields")
        if not all(service[mode] for mode in ("road", "bus", "ride_hailing")):
            raise ValueError(f"{zone_id} must retain intrazonal road, bus and ride-hailing service")
    metro_intrazonal = set(config["graphs"]["metro"].get("intrazonal_service_zones", ()))
    if metro_intrazonal != {zone_id for zone_id, service in intrazonal.items() if service["metro"]}:
        raise ValueError("metro intrazonal service zones are inconsistent")
    sampling = config.get("intrazonal_distance_sampling", {})
    if sampling.get("distribution") != "triangular_multiplier":
        raise ValueError("intrazonal distance sampling must use triangular_multiplier")
    if not 0 < float(sampling.get("minimum_distance_km", 0)) < float(sampling.get("maximum_distance_km", 0)):
        raise ValueError("intrazonal distance absolute bounds are invalid")
    expected_purposes = {
        "shopping", "social_leisure", "medical", "visit",
        "out_of_home_family_care", "out_of_home_family_activity", "work",
    }
    purpose_ranges = sampling.get("purpose_multiplier_ranges", {})
    if set(purpose_ranges) != expected_purposes:
        raise ValueError("intrazonal distance sampling purposes are incomplete")
    for purpose, row in purpose_ranges.items():
        if set(row) != {"low", "mode", "high"} or not 0 < row["low"] <= row["mode"] <= row["high"]:
            raise ValueError(f"Invalid triangular range for {purpose}")
    metro_rules = config.get("intrazonal_metro", {})
    coverage = metro_rules.get("metro_coverage_rate", {})
    if set(coverage) != zone_ids or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
        for value in coverage.values()
    ):
        raise ValueError("metro_coverage_rate must contain Z1-Z9 values between 0 and 1")
    if {zone_id for zone_id, rate in coverage.items() if rate > 0} != metro_intrazonal:
        raise ValueError("metro coverage and intrazonal service zones are inconsistent")
    if float(metro_rules.get("minimum_trip_mean_multiplier", 0)) <= 0 or float(metro_rules.get("minimum_trip_distance_km", 0)) <= 0:
        raise ValueError("intrazonal metro distance thresholds must be positive")
    for mode in MODES:
        params = config["modes"][mode]
        for key, value in params.items():
            if key == "provenance" or value is None:
                continue
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{mode}.{key} must be non-negative")
    for graph_name, list_name, id_field in (
        ("bus", "routes", "route_id"),
        ("metro", "lines", "line_id"),
    ):
        services = config["graphs"][graph_name][list_name]
        ids = [service[id_field] for service in services]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {graph_name} service IDs")
        for service in services:
            if len(service["zones"]) < 2 or any(zone not in zone_ids for zone in service["zones"]):
                raise ValueError(f"Invalid {graph_name} service {service[id_field]}")
    if any("Z9" in line["zones"] for line in config["graphs"]["metro"]["lines"]):
        raise ValueError("Z9 must not be served by metro in v1")


def build_transport_network(
    config: Optional[Mapping[str, Any]] = None,
    spatial: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    config = dict(config or load_transport_configuration())
    spatial = dict(spatial or derive_spatial_configuration(load_zone_configuration()))
    zones = spatial["zones"]
    _validate_configuration(config, zones)
    zone_ids = [zone["zone_id"] for zone in zones]
    road_edges = _road_edges(zones)
    road = _road_adjacency(zone_ids, road_edges)
    bus = _service_adjacency(
        config["graphs"]["bus"]["routes"],
        "route_id",
        road_edges,
        float(config["modes"]["bus"]["route_distance_factor"]),
    )
    metro = _service_adjacency(
        config["graphs"]["metro"]["lines"],
        "line_id",
        road_edges,
        float(config["modes"]["metro"]["route_distance_factor"]),
    )
    return {
        "config": config,
        "spatial": spatial,
        "zone_ids": zone_ids,
        "zone_by_id": {zone["zone_id"]: zone for zone in zones},
        "road_edges": road_edges,
        "road": road,
        "bus": bus,
        "metro": metro,
    }


def _euclidean_distance(network: Mapping[str, Any], origin: str, destination: str) -> float:
    if origin == destination:
        return 0.0
    left = network["zone_by_id"][origin]
    right = network["zone_by_id"][destination]
    return math.hypot(
        float(left["centroid_x"]) - float(right["centroid_x"]),
        float(left["centroid_y"]) - float(right["centroid_y"]),
    )


def _road_network_distance(
    network: Mapping[str, Any], origin: str, destination: str,
    intrazonal_distance_km: float | None = None,
) -> float:
    if origin == destination:
        return (
            float(intrazonal_distance_km)
            if intrazonal_distance_km is not None
            else float(network["zone_by_id"][origin]["mean_intrazonal_distance"])
        )
    distance = _shortest_road_distance(network["road"], origin, destination)
    if distance is None:
        raise ValueError(f"No configured road path from {origin} to {destination}")
    return distance


def _stable_fraction(seed: Any, *parts: Any) -> float:
    material = "|".join([repr(seed), *(f"{type(part).__name__}:{part!r}" for part in parts)])
    integer = int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")
    return integer / float(2**64)


def intrazonal_metro_is_covered(
    network: Mapping[str, Any], zone_id: str, distance_km: float, trip_key: Any, seed: Any = 47
) -> bool:
    """Require a long-enough trip and independently covered origin/destination endpoints."""
    rules = network["config"]["intrazonal_metro"]
    rate = float(rules["metro_coverage_rate"][zone_id])
    threshold = max(
        float(rules["minimum_trip_distance_km"]),
        float(network["zone_by_id"][zone_id]["mean_intrazonal_distance"])
        * float(rules["minimum_trip_mean_multiplier"]),
    )
    if rate <= 0 or distance_km < threshold:
        return False
    return (
        _stable_fraction(seed, trip_key, zone_id, "origin_endpoint") < rate
        and _stable_fraction(seed, trip_key, zone_id, "destination_endpoint") < rate
    )


def _shortest_service_path(
    adjacency: Mapping[str, Sequence[Tuple[str, str, float]]],
    origin: str,
    destination: str,
    speed_kmh: float,
    transfer_penalty_min: float,
    origin_access_min: float = 0.0,
    destination_access_min: float = 0.0,
) -> Optional[Tuple[float, int, float]]:
    if origin == destination:
        return None
    queue = [(origin_access_min, 0.0, 0, origin, "")]
    best = {(origin, ""): origin_access_min}
    while queue:
        cost, distance, transfers, node, current_service = heapq.heappop(queue)
        state = (node, current_service)
        if cost > best[state] + 1e-12:
            continue
        if node == destination:
            return distance, transfers, cost + destination_access_min
        for neighbour, service_id, edge_distance in adjacency.get(node, []):
            transfer = int(bool(current_service) and current_service != service_id)
            candidate_cost = cost + edge_distance / speed_kmh * 60.0 + transfer * transfer_penalty_min
            candidate_state = (neighbour, service_id)
            if candidate_cost + 1e-12 < best.get(candidate_state, math.inf):
                best[candidate_state] = candidate_cost
                heapq.heappush(
                    queue,
                    (candidate_cost, distance + edge_distance, transfers + transfer, neighbour, service_id),
                )
    return None


def _unavailable(
    origin: str, destination: str, mode: str,
    euclidean_distance: float, road_network_distance: float,
) -> Dict[str, Any]:
    return {
        "origin_zone": origin,
        "destination_zone": destination,
        "mode": mode,
        "available": False,
        "access_mode": None,
        "euclidean_distance_km": round(euclidean_distance, 3),
        "road_network_distance_km": round(road_network_distance, 3),
        "main_network_distance_km": None,
        "access_distance_km": None,
        "network_distance_km": None,
        "in_vehicle_time_min": None,
        "access_time_min": None,
        "wait_time_min": None,
        "transfer_time_min": None,
        "total_time_min": None,
        "main_fare": None,
        "access_fare": None,
        "fare": None,
        "line_transfer_count": None,
        "mode_transfer_count": None,
        "transfers": None,
    }


def _available(
    origin: str,
    destination: str,
    mode: str,
    access_mode: str,
    euclidean_distance: float,
    road_network_distance: float,
    main_distance: float,
    access_distance: float,
    in_vehicle: float,
    access: float,
    wait: float,
    transfer: float,
    main_fare: float,
    access_fare: float,
    line_transfer_count: int,
    mode_transfer_count: int,
) -> Dict[str, Any]:
    components = [round(value, 3) for value in (in_vehicle, access, wait, transfer)]
    return {
        "origin_zone": origin,
        "destination_zone": destination,
        "mode": mode,
        "available": True,
        "access_mode": access_mode,
        "euclidean_distance_km": round(euclidean_distance, 3),
        "road_network_distance_km": round(road_network_distance, 3),
        "main_network_distance_km": round(main_distance, 3),
        "access_distance_km": round(access_distance, 3),
        "network_distance_km": round(main_distance + access_distance, 3),
        "in_vehicle_time_min": components[0],
        "access_time_min": components[1],
        "wait_time_min": components[2],
        "transfer_time_min": components[3],
        "total_time_min": round(sum(components), 3),
        "main_fare": round(main_fare, 2),
        "access_fare": round(access_fare, 2),
        "fare": round(main_fare + access_fare, 2),
        "line_transfer_count": line_transfer_count,
        "mode_transfer_count": mode_transfer_count,
        "transfers": line_transfer_count + mode_transfer_count,
    }


def calculate_od_option(
    network: Mapping[str, Any], origin: str, destination: str, mode: str,
    *, intrazonal_distance_km: float | None = None,
    trip_key: Any = None,
    enforce_intrazonal_metro_coverage: bool = False,
    seed: Any = 47,
) -> Dict[str, Any]:
    if origin not in network["zone_ids"] or destination not in network["zone_ids"]:
        raise ValueError("origin and destination must be Z1-Z9")
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    config = network["config"]
    params = config["modes"][mode]
    zone_params = config["zone_service_parameters"]
    euclidean_distance = _euclidean_distance(network, origin, destination)
    road_network_distance = _road_network_distance(
        network, origin, destination, intrazonal_distance_km
    )

    def unavailable() -> Dict[str, Any]:
        return _unavailable(
            origin, destination, mode, euclidean_distance, road_network_distance
        )

    if mode in {"walk", "ride_hailing"}:
        distance = road_network_distance
        if mode == "walk":
            if distance > params["maximum_distance_km"]:
                return unavailable()
            movement = distance / params["base_speed_kmh"] * 60.0
            return _available(
                origin, destination, mode, "none", euclidean_distance,
                road_network_distance, distance, 0.0, movement, 0.0, 0.0,
                0.0, 0.0, 0.0, 0, 0,
            )
        fare = params["base_fare"] + max(0.0, distance - params["included_distance_km"]) * params["per_km_after_included"]
        return _available(
            origin,
            destination,
            mode,
            "walk",
            euclidean_distance,
            road_network_distance,
            distance,
            0.0,
            distance / params["base_speed_kmh"] * 60.0,
            params["access_time_min"],
            zone_params[origin]["ride_hailing_wait_min"],
            0.0,
            fare,
            0.0,
            0,
            0,
        )

    effective_origin = origin
    effective_destination = destination
    access_mode = "walk"
    access_fare = 0.0
    mode_transfers = 0
    mode_transfer_time = 0.0
    feeder_access_time = 0.0
    feeder_access_distance = 0.0
    uses_metro_feeder = False
    if mode == "metro" and (origin == "Z9" or destination == "Z9"):
        if origin == destination:
            return unavailable()
        feeder = config["graphs"]["metro"]["feeder_access"]["Z9"]
        uses_metro_feeder = True
        access_mode = feeder["access_mode"]
        access_fare = float(feeder["access_fare"])
        mode_transfers = int(feeder["mode_transfer_count"])
        mode_transfer_time = mode_transfers * float(params["mode_transfer_penalty_min"])
        if origin == "Z9":
            effective_origin = feeder["gateway_zone"]
            feeder_row = calculate_od_option(network, "Z9", effective_origin, access_mode)
        else:
            effective_destination = feeder["gateway_zone"]
            feeder_row = calculate_od_option(network, effective_destination, "Z9", access_mode)
        feeder_access_time = feeder_row["total_time_min"]
        feeder_access_distance = feeder_row["network_distance_km"]
        if effective_origin == effective_destination:
            return unavailable()

    access_key = f"{mode}_access_min"
    wait_key = f"{mode}_wait_min"
    if mode in {"bus", "metro"} and origin == destination:
        if not config["intrazonal_services"][origin][mode]:
            return unavailable()
        distance = road_network_distance * float(params["route_distance_factor"])
        if mode == "metro":
            rules = config["intrazonal_metro"]
            threshold = max(
                float(rules["minimum_trip_distance_km"]),
                float(network["zone_by_id"][origin]["mean_intrazonal_distance"])
                * float(rules["minimum_trip_mean_multiplier"]),
            )
            if distance < threshold:
                return unavailable()
            if enforce_intrazonal_metro_coverage:
                if trip_key is None:
                    raise ValueError("trip_key is required when enforcing intrazonal metro coverage")
                if not intrazonal_metro_is_covered(network, origin, distance, trip_key, seed):
                    return unavailable()
        path = (distance, 0, distance / params["base_speed_kmh"] * 60.0)
    else:
        origin_access = 0.0 if uses_metro_feeder and origin == "Z9" else zone_params[effective_origin][access_key]
        destination_access = 0.0 if uses_metro_feeder and destination == "Z9" else zone_params[effective_destination][access_key]
        path = _shortest_service_path(
            network[mode],
            effective_origin,
            effective_destination,
            float(params["base_speed_kmh"]),
            float(params["transfer_penalty_min"]),
            float(origin_access),
            float(destination_access),
        )
    if path is None:
        return unavailable()
    distance, transfers, _ = path
    access_values = (
        0.0 if uses_metro_feeder and origin == "Z9" else zone_params[effective_origin][access_key],
        0.0 if uses_metro_feeder and destination == "Z9" else zone_params[effective_destination][access_key],
    )
    wait = zone_params[effective_origin][wait_key]
    if any(value is None for value in access_values) or wait is None:
        return unavailable()
    access = sum(access_values) + feeder_access_time
    access_distance = (
        sum(access_values) * float(config["modes"]["walk"]["base_speed_kmh"]) / 60.0
        + feeder_access_distance
    )
    transfer_time = transfers * params["transfer_penalty_min"] + mode_transfer_time
    if mode == "bus":
        fare = params["flat_fare"]
    else:
        excess = max(0.0, distance - params["included_distance_km"])
        fare = params["base_fare"] + math.ceil(excess / params["increment_distance_km"]) * params["increment_fare"]
    return _available(
        origin,
        destination,
        mode,
        access_mode,
        euclidean_distance,
        road_network_distance,
        distance,
        access_distance,
        distance / params["base_speed_kmh"] * 60.0,
        access,
        wait,
        transfer_time,
        fare,
        access_fare,
        transfers,
        mode_transfers,
    )


def calculate_leg_mode_option(
    network: Mapping[str, Any], leg: Mapping[str, Any], mode: str, seed: Any = 47
) -> Dict[str, Any]:
    """Instantiate a zone-level option for one concrete leg without choosing a mode."""
    origin = leg["origin_zone"]
    destination = leg["destination_zone"]
    same_zone = origin == destination
    return calculate_od_option(
        network, origin, destination, mode,
        intrazonal_distance_km=float(leg["road_network_distance_km"]) if same_zone else None,
        trip_key=leg["leg_id"],
        enforce_intrazonal_metro_coverage=same_zone and mode == "metro",
        seed=seed,
    )


def build_all_od_options(network: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    network = network or build_transport_network()
    rows = [
        calculate_od_option(network, origin, destination, mode)
        for origin in network["zone_ids"]
        for destination in network["zone_ids"]
        for mode in MODES
    ]
    if any(tuple(row) != OUTPUT_FIELDS for row in rows):
        raise AssertionError("OD option output fields changed")
    return rows
