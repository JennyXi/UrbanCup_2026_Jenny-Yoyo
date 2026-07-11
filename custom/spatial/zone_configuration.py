"""Derived configuration and audit utilities for the nine-zone synthetic city.

This module does not generate agents, home zones, destinations, OD pairs,
trips, modes, prices, dispatch outcomes, weather effects, or congestion.
"""

from __future__ import annotations

import itertools
import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


AGE_GROUPS = ("18-39", "40-59", "60+")
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "shanghai_synthetic_city.json"
)


def load_zone_configuration(path: Optional[Any] = None) -> Dict[str, Any]:
    """Load and validate a JSON zone configuration."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    validate_zone_configuration(config)
    return config


def _require_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return float(value)


def _validate_composition(values: Any, field_name: str, tolerance: float) -> None:
    if not isinstance(values, dict) or set(values) != set(AGE_GROUPS):
        raise ValueError(f"{field_name} must contain exactly {AGE_GROUPS}")
    numeric = [_require_number(values[group], f"{field_name}.{group}") for group in AGE_GROUPS]
    if any(value < 0 or value > 1 for value in numeric):
        raise ValueError(f"{field_name} values must be in [0, 1]")
    if not math.isclose(sum(numeric), 1.0, rel_tol=0.0, abs_tol=tolerance):
        raise ValueError(f"{field_name} must sum to 1")


def validate_zone_configuration(config: Dict[str, Any]) -> None:
    """Validate raw ring, zone, density, coordinate, and age inputs."""
    if not isinstance(config, dict):
        raise ValueError("configuration must be a dictionary")
    tolerance = _require_number(config.get("age_share_tolerance"), "age_share_tolerance")
    if tolerance <= 0:
        raise ValueError("age_share_tolerance must be positive")
    _validate_composition(config.get("citywide_age_target"), "citywide_age_target", tolerance)
    spatial_scale = _require_number(config.get("spatial_scale"), "spatial_scale")
    if spatial_scale <= 0:
        raise ValueError("spatial_scale must be positive")

    rings = config.get("rings")
    zones = config.get("zones")
    if not isinstance(rings, list) or not rings:
        raise ValueError("rings must be a non-empty list")
    if not isinstance(zones, list) or not zones:
        raise ValueError("zones must be a non-empty list")

    ring_ids = [ring.get("ring_id") for ring in rings]
    if any(not isinstance(ring_id, str) or not ring_id for ring_id in ring_ids):
        raise ValueError("each ring_id must be a non-empty string")
    if len(ring_ids) != len(set(ring_ids)):
        raise ValueError("ring_id values must be unique")

    ring_map = {}
    for ring in rings:
        ring_id = ring["ring_id"]
        inner = _require_number(ring.get("inner_radius"), f"{ring_id}.inner_radius")
        outer = _require_number(ring.get("outer_radius"), f"{ring_id}.outer_radius")
        coverage = _require_number(
            ring.get("modeled_coverage_share"), f"{ring_id}.modeled_coverage_share"
        )
        if inner < 0 or not inner < outer:
            raise ValueError(f"{ring_id} must satisfy 0 <= inner_radius < outer_radius")
        if not 0 < coverage <= 1:
            raise ValueError(f"{ring_id}.modeled_coverage_share must be in (0, 1]")
        ring_map[ring_id] = (inner, outer)

    zone_ids = [zone.get("zone_id") for zone in zones]
    if any(not isinstance(zone_id, str) or not zone_id for zone_id in zone_ids):
        raise ValueError("each zone_id must be a non-empty string")
    if len(zone_ids) != len(set(zone_ids)):
        raise ValueError("zone_id values must be unique")

    shares_by_ring = {ring_id: 0.0 for ring_id in ring_ids}
    for zone in zones:
        zone_id = zone["zone_id"]
        ring_id = zone.get("spatial_ring")
        if ring_id not in ring_map:
            raise ValueError(f"{zone_id} references unknown ring: {ring_id}")
        share = _require_number(
            zone.get("within_ring_area_share"), f"{zone_id}.within_ring_area_share"
        )
        if share < 0:
            raise ValueError(f"{zone_id}.within_ring_area_share must be non-negative")
        shares_by_ring[ring_id] += share

        radius = _require_number(
            zone.get("radial_distance_from_center"),
            f"{zone_id}.radial_distance_from_center",
        )
        inner, outer = ring_map[ring_id]
        if not inner <= radius <= outer:
            raise ValueError(f"{zone_id} centroid radius must lie within its ring")
        angle = zone.get("angular_position_degrees")
        if radius == 0:
            if angle is not None:
                raise ValueError(f"{zone_id} angle must be null when radius is 0")
        else:
            angle_value = _require_number(angle, f"{zone_id}.angular_position_degrees")
            if not 0 <= angle_value < 360:
                raise ValueError(f"{zone_id} angle must be in [0, 360)")

        density = _require_number(
            zone.get("residential_density_factor"),
            f"{zone_id}.residential_density_factor",
        )
        if density <= 0:
            raise ValueError(f"{zone_id}.residential_density_factor must be positive")
        _validate_composition(
            zone.get("base_age_composition"),
            f"{zone_id}.base_age_composition",
            tolerance,
        )

    for ring_id, share_sum in shares_by_ring.items():
        if not math.isclose(share_sum, 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise ValueError(f"within_ring_area_share for {ring_id} must sum to 1")


def calibrate_age_composition(
    zones: Sequence[Dict[str, Any]],
    citywide_age_target: Dict[str, float],
    tolerance: float,
) -> Dict[str, Any]:
    """Apply one explicit uniform correction per age group.

    The same age-specific correction is applied to every zone, preserving the
    cross-zone ordering for each age group. Infeasible corrections raise and
    are never clipped or silently replaced with another method.
    """
    base_implied = {
        group: sum(
            zone["population_weight"] * zone["base_age_composition"][group]
            for zone in zones
        )
        for group in AGE_GROUPS
    }
    correction = {
        group: citywide_age_target[group] - base_implied[group] for group in AGE_GROUPS
    }
    calibrated = {}
    for zone in zones:
        zone_id = zone["zone_id"]
        values = {
            group: zone["base_age_composition"][group] + correction[group]
            for group in AGE_GROUPS
        }
        if any(value < 0 or value > 1 for value in values.values()):
            raise ValueError(
                f"Age calibration infeasible for {zone_id}: calibrated share outside [0, 1]"
            )
        if not math.isclose(sum(values.values()), 1.0, rel_tol=0.0, abs_tol=tolerance):
            raise ValueError(f"Age calibration failed to preserve row sum for {zone_id}")
        calibrated[zone_id] = values

    implied = {
        group: sum(
            zone["population_weight"] * calibrated[zone["zone_id"]][group]
            for zone in zones
        )
        for group in AGE_GROUPS
    }
    for group in AGE_GROUPS:
        if not math.isclose(
            implied[group], citywide_age_target[group], rel_tol=0.0, abs_tol=tolerance
        ):
            raise ValueError(f"Calibrated citywide age share misses target for {group}")

    return {
        "base_implied_city_age_share": base_implied,
        "age_correction": correction,
        "calibrated_age_composition": calibrated,
        "implied_city_age_share": implied,
        "difference_by_age_group": {
            group: implied[group] - citywide_age_target[group] for group in AGE_GROUPS
        },
    }


def derive_spatial_configuration(config: Dict[str, Any]) -> Dict[str, Any]:
    """Derive full-precision geometry, capacity, population, and age fields."""
    validate_zone_configuration(config)
    source = deepcopy(config)
    tolerance = source["age_share_tolerance"]
    spatial_scale = float(source["spatial_scale"])
    ring_map = {}
    for ring in source["rings"]:
        base_inner = float(ring["inner_radius"])
        base_outer = float(ring["outer_radius"])
        inner = base_inner * spatial_scale
        outer = base_outer * spatial_scale
        theoretical = math.pi * (outer * outer - inner * inner)
        represented = theoretical * float(ring["modeled_coverage_share"])
        derived_ring = deepcopy(ring)
        derived_ring["base_inner_radius"] = base_inner
        derived_ring["base_outer_radius"] = base_outer
        derived_ring["inner_radius"] = inner
        derived_ring["outer_radius"] = outer
        derived_ring["theoretical_ring_area"] = theoretical
        derived_ring["represented_ring_area"] = represented
        ring_map[ring["ring_id"]] = derived_ring

    zones = []
    for raw_zone in source["zones"]:
        zone = deepcopy(raw_zone)
        base_radius = float(zone["radial_distance_from_center"])
        radius = base_radius * spatial_scale
        angle = zone["angular_position_degrees"]
        if radius == 0:
            x = y = 0.0
        else:
            radians = math.radians(float(angle))
            x = radius * math.cos(radians)
            y = radius * math.sin(radians)
        ring = ring_map[zone["spatial_ring"]]
        area = ring["represented_ring_area"] * float(zone["within_ring_area_share"])
        capacity = area * float(zone["residential_density_factor"])
        zone.update(
            {
                "base_radial_distance_from_center": base_radius,
                "radial_distance_from_center": radius,
                "centroid_x": x,
                "centroid_y": y,
                "synthetic_area": area,
                "residential_population_capacity": capacity,
                "equivalent_radius": math.sqrt(area / math.pi),
            }
        )
        zone["mean_intrazonal_distance"] = (
            128.0 / (45.0 * math.pi) * zone["equivalent_radius"]
        )
        zones.append(zone)

    total_area = sum(zone["synthetic_area"] for zone in zones)
    total_capacity = sum(zone["residential_population_capacity"] for zone in zones)
    if total_area <= 0 or total_capacity <= 0:
        raise ValueError("derived total area and population capacity must be positive")
    for zone in zones:
        zone["area_weight"] = zone["synthetic_area"] / total_area
        zone["population_weight"] = zone["residential_population_capacity"] / total_capacity

    age_audit = calibrate_age_composition(
        zones, source["citywide_age_target"], tolerance
    )
    for zone in zones:
        zone["calibrated_age_composition"] = age_audit["calibrated_age_composition"][
            zone["zone_id"]
        ]

    derived = {
        "calibration_id": source.get("calibration_id"),
        "spatial_scale": spatial_scale,
        "total_agents": source.get("total_agents"),
        "distance_unit": source.get("distance_unit"),
        "synthetic_area_unit": source.get("synthetic_area_unit"),
        "age_share_tolerance": tolerance,
        "citywide_age_target": deepcopy(source["citywide_age_target"]),
        "rings": list(ring_map.values()),
        "zones": zones,
        "total_synthetic_area": total_area,
        "total_residential_population_capacity": total_capacity,
        **age_audit,
    }
    return derived


def _largest_remainder(values: Sequence[float], total: int) -> List[int]:
    raw = [total * value for value in values]
    result = [math.floor(value) for value in raw]
    remainder = total - sum(result)
    order = sorted(range(len(raw)), key=lambda index: (-(raw[index] - result[index]), index))
    for index in order[:remainder]:
        result[index] += 1
    return result


def _controlled_round_matrix(
    expected: Sequence[Sequence[float]],
    row_targets: Sequence[int],
    column_targets: Sequence[int],
) -> List[List[int]]:
    """Floor/ceiling controlled rounding with exact row and column margins."""
    base = [[math.floor(value) for value in row] for row in expected]
    row_deficit = [target - sum(row) for target, row in zip(row_targets, base)]
    column_deficit = [
        target - sum(base[row][column] for row in range(len(base)))
        for column, target in enumerate(column_targets)
    ]
    if any(value < 0 for value in row_deficit + column_deficit):
        raise ValueError("Controlled rounding received inconsistent margins")
    if sum(row_deficit) != sum(column_deficit):
        raise ValueError("Controlled rounding row and column deficits differ")

    options = []
    column_count = len(column_targets)
    for row_index, count in enumerate(row_deficit):
        if count > column_count:
            raise ValueError("Controlled rounding requires more than one increment per cell")
        row_options = []
        for columns in itertools.combinations(range(column_count), count):
            score = sum(expected[row_index][column] - base[row_index][column] for column in columns)
            row_options.append((columns, score))
        options.append(row_options)

    @lru_cache(maxsize=None)
    def solve(row_index: int, remaining: Tuple[int, ...]):
        if row_index == len(base):
            return (0.0, ()) if all(value == 0 for value in remaining) else None
        best = None
        for columns, score in options[row_index]:
            updated = list(remaining)
            feasible = True
            for column in columns:
                updated[column] -= 1
                if updated[column] < 0:
                    feasible = False
            if not feasible:
                continue
            suffix = solve(row_index + 1, tuple(updated))
            if suffix is None:
                continue
            candidate = (score + suffix[0], (columns,) + suffix[1])
            if best is None or candidate[0] > best[0] + 1e-15:
                best = candidate
        return best

    solution = solve(0, tuple(column_deficit))
    if solution is None:
        raise ValueError("No feasible two-dimensional controlled rounding solution")
    result = [row[:] for row in base]
    for row_index, columns in enumerate(solution[1]):
        for column in columns:
            result[row_index][column] += 1
    return result


def allocate_zone_age_quotas(
    derived_config: Dict[str, Any],
    total_agents: Optional[int] = None,
) -> Dict[str, Any]:
    """Create exact zone x age integer quotas without generating agents."""
    if total_agents is None:
        if derived_config.get("total_agents") is None:
            raise ValueError("total_agents must be provided or present in derived_config")
        actual_total = derived_config["total_agents"]
        total_source = "configuration"
    else:
        actual_total = total_agents
        total_source = "explicit_argument"
    if isinstance(actual_total, bool) or not isinstance(actual_total, int) or actual_total <= 0:
        raise ValueError("total_agents must be a positive integer")

    zones = derived_config["zones"]
    population_weights = [zone["population_weight"] for zone in zones]
    target_shares = [derived_config["citywide_age_target"][group] for group in AGE_GROUPS]
    row_targets = _largest_remainder(population_weights, actual_total)
    column_targets = _largest_remainder(target_shares, actual_total)
    expected = [
        [
            actual_total
            * zone["population_weight"]
            * zone["calibrated_age_composition"][group]
            for group in AGE_GROUPS
        ]
        for zone in zones
    ]
    matrix = _controlled_round_matrix(expected, row_targets, column_targets)

    if any(not isinstance(value, int) or value < 0 for row in matrix for value in row):
        raise AssertionError("Quota matrix must contain non-negative integers")
    if [sum(row) for row in matrix] != row_targets:
        raise AssertionError("Quota matrix row sums do not match zone targets")
    if [sum(row[column] for row in matrix) for column in range(len(AGE_GROUPS))] != column_targets:
        raise AssertionError("Quota matrix column sums do not match city age targets")
    if sum(sum(row) for row in matrix) != actual_total:
        raise AssertionError("Quota matrix total does not match total_agents")

    return {
        "total_agents_used": actual_total,
        "total_agents_source": total_source,
        "zone_ids": [zone["zone_id"] for zone in zones],
        "age_groups": list(AGE_GROUPS),
        "zone_totals": dict(zip((zone["zone_id"] for zone in zones), row_targets)),
        "city_age_totals": dict(zip(AGE_GROUPS, column_targets)),
        "expected_count_matrix": expected,
        "quota_matrix": {
            zone["zone_id"]: dict(zip(AGE_GROUPS, matrix[index]))
            for index, zone in enumerate(zones)
        },
    }


def build_spatial_audit(
    derived_config: Dict[str, Any],
    total_agents: Optional[int] = None,
) -> Dict[str, Any]:
    """Build full-precision geometry, distance, sorting, age, and quota audit output."""
    zones = derived_config["zones"]
    zone_ids = [zone["zone_id"] for zone in zones]
    distance_matrix = []
    nonzero_distances = []
    for origin in zones:
        row = []
        for destination in zones:
            distance = math.hypot(
                origin["centroid_x"] - destination["centroid_x"],
                origin["centroid_y"] - destination["centroid_y"],
            )
            row.append(distance)
            if distance > 0:
                nonzero_distances.append(distance)
        distance_matrix.append(row)

    ring_summary = {}
    for ring in derived_config["rings"]:
        ring_id = ring["ring_id"]
        ring_zones = [zone for zone in zones if zone["spatial_ring"] == ring_id]
        ring_summary[ring_id] = {
            "synthetic_area": sum(zone["synthetic_area"] for zone in ring_zones),
            "area_weight": sum(zone["area_weight"] for zone in ring_zones),
            "population_weight": sum(zone["population_weight"] for zone in ring_zones),
        }

    descending = lambda field: [
        zone["zone_id"] for zone in sorted(zones, key=lambda zone: (-zone[field], zone["zone_id"]))
    ]
    ring_area_order = [
        ring_id
        for ring_id, _ in sorted(
            ring_summary.items(), key=lambda item: (-item[1]["synthetic_area"], item[0])
        )
    ]
    ring_population_order = [
        ring_id
        for ring_id, _ in sorted(
            ring_summary.items(), key=lambda item: (-item[1]["population_weight"], item[0])
        )
    ]

    areas = [zone["synthetic_area"] for zone in zones]
    return {
        "zone_ids": zone_ids,
        "total_synthetic_area": derived_config["total_synthetic_area"],
        "area_weight_sum": sum(zone["area_weight"] for zone in zones),
        "population_weight_sum": sum(zone["population_weight"] for zone in zones),
        "equivalent_radius": {zone["zone_id"]: zone["equivalent_radius"] for zone in zones},
        "mean_intrazonal_distance": {
            zone["zone_id"]: zone["mean_intrazonal_distance"] for zone in zones
        },
        "zone_to_zone_euclidean_distance": {
            zone_id: dict(zip(zone_ids, distance_matrix[index]))
            for index, zone_id in enumerate(zone_ids)
        },
        "minimum_nonzero_interzonal_distance": min(nonzero_distances),
        "zone_area_order_descending": descending("synthetic_area"),
        "zone_population_weight_order_descending": descending("population_weight"),
        "ring_summary": ring_summary,
        "ring_area_order_descending": ring_area_order,
        "ring_population_weight_order_descending": ring_population_order,
        "maximum_to_minimum_zone_area_ratio": max(areas) / min(areas),
        "area_ratio_interpretation": "soft_model_complexity_check_only",
        "base_implied_city_age_share": derived_config["base_implied_city_age_share"],
        "implied_city_age_share": derived_config["implied_city_age_share"],
        "difference_by_age_group": derived_config["difference_by_age_group"],
        "quota_audit": allocate_zone_age_quotas(derived_config, total_agents),
    }
