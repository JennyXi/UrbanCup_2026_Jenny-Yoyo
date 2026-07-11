import math
import unittest
from copy import deepcopy

from custom.spatial.zone_configuration import (
    AGE_GROUPS,
    allocate_zone_age_quotas,
    build_spatial_audit,
    calibrate_age_composition,
    derive_spatial_configuration,
    load_zone_configuration,
    validate_zone_configuration,
)


class ZoneConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_zone_configuration()
        cls.derived = derive_spatial_configuration(cls.config)

    def test_core_derived_totals_and_orders(self):
        audit = build_spatial_audit(self.derived)
        self.assertAlmostEqual(audit["area_weight_sum"], 1.0, places=12)
        self.assertAlmostEqual(audit["population_weight_sum"], 1.0, places=12)
        self.assertEqual(
            audit["ring_area_order_descending"],
            ["middle", "peripheral", "suburban", "inner", "core"],
        )
        self.assertEqual(
            audit["ring_population_weight_order_descending"],
            ["middle", "inner", "suburban", "peripheral", "core"],
        )
        self.assertEqual(audit["zone_area_order_descending"][0], "Z7")
        self.assertEqual(audit["zone_population_weight_order_descending"][0], "Z7")
        self.assertEqual(audit["area_ratio_interpretation"], "soft_model_complexity_check_only")
        self.assertAlmostEqual(audit["maximum_to_minimum_zone_area_ratio"], 4.963636363636364)
        self.assertEqual(self.derived["spatial_scale"], 0.82)

    def test_coordinates_come_from_full_precision_radius_and_angle(self):
        for zone in self.derived["zones"]:
            radius = zone["base_radial_distance_from_center"] * self.config["spatial_scale"]
            angle = zone["angular_position_degrees"]
            if radius == 0:
                expected_x = expected_y = 0.0
            else:
                expected_x = radius * math.cos(math.radians(angle))
                expected_y = radius * math.sin(math.radians(angle))
            self.assertEqual(zone["centroid_x"], expected_x)
            self.assertEqual(zone["centroid_y"], expected_y)
            self.assertAlmostEqual(math.hypot(zone["centroid_x"], zone["centroid_y"]), radius)

    def test_distance_audit_is_symmetric_and_uses_coordinates(self):
        audit = build_spatial_audit(self.derived)
        matrix = audit["zone_to_zone_euclidean_distance"]
        for first in audit["zone_ids"]:
            self.assertEqual(matrix[first][first], 0.0)
            for second in audit["zone_ids"]:
                self.assertAlmostEqual(matrix[first][second], matrix[second][first])
        self.assertAlmostEqual(audit["minimum_nonzero_interzonal_distance"], 4.1)

    def test_spatial_scale_changes_lengths_and_areas_but_not_population_or_quotas(self):
        unscaled_config = deepcopy(self.config)
        unscaled_config["spatial_scale"] = 1.0
        unscaled = derive_spatial_configuration(unscaled_config)
        scaled = self.derived
        scale = self.config["spatial_scale"]
        self.assertEqual(scale, 0.82)
        self.assertAlmostEqual(scaled["total_synthetic_area"], unscaled["total_synthetic_area"] * scale ** 2)
        for scaled_ring, unscaled_ring in zip(scaled["rings"], unscaled["rings"]):
            for field in ("inner_radius", "outer_radius"):
                self.assertAlmostEqual(scaled_ring[field], unscaled_ring[field] * scale)
            for field in ("theoretical_ring_area", "represented_ring_area"):
                self.assertAlmostEqual(scaled_ring[field], unscaled_ring[field] * scale ** 2)
        for scaled_zone, unscaled_zone in zip(scaled["zones"], unscaled["zones"]):
            for field in (
                "radial_distance_from_center", "centroid_x", "centroid_y",
                "equivalent_radius", "mean_intrazonal_distance",
            ):
                self.assertAlmostEqual(scaled_zone[field], unscaled_zone[field] * scale)
            self.assertAlmostEqual(scaled_zone["synthetic_area"], unscaled_zone["synthetic_area"] * scale ** 2)
            self.assertAlmostEqual(scaled_zone["population_weight"], unscaled_zone["population_weight"])
            for group in AGE_GROUPS:
                self.assertAlmostEqual(
                    scaled_zone["calibrated_age_composition"][group],
                    unscaled_zone["calibrated_age_composition"][group],
                    places=12,
                )
        self.assertEqual(
            allocate_zone_age_quotas(scaled, 1000)["quota_matrix"],
            allocate_zone_age_quotas(unscaled, 1000)["quota_matrix"],
        )

    def test_scaled_key_city_dimensions_are_derived(self):
        audit = build_spatial_audit(self.derived)
        unscaled_config = deepcopy(self.config)
        unscaled_config["spatial_scale"] = 1.0
        unscaled = derive_spatial_configuration(unscaled_config)
        unscaled_audit = build_spatial_audit(unscaled)
        scale = self.config["spatial_scale"]
        matrix = audit["zone_to_zone_euclidean_distance"]
        maximum_centroid = max(
            matrix[first][second]
            for index, first in enumerate(audit["zone_ids"])
            for second in audit["zone_ids"][index + 1:]
        )
        z1_maximum = max(matrix["Z1"].values())
        zones = {zone["zone_id"]: zone for zone in self.derived["zones"]}
        approximate_diameter = max(
            matrix[first][second] + zones[first]["equivalent_radius"] + zones[second]["equivalent_radius"]
            for index, first in enumerate(audit["zone_ids"])
            for second in audit["zone_ids"][index + 1:]
        )
        unscaled_matrix = unscaled_audit["zone_to_zone_euclidean_distance"]
        unscaled_maximum = max(
            unscaled_matrix[first][second]
            for index, first in enumerate(unscaled_audit["zone_ids"])
            for second in unscaled_audit["zone_ids"][index + 1:]
        )
        unscaled_zones = {zone["zone_id"]: zone for zone in unscaled["zones"]}
        unscaled_diameter = max(
            unscaled_matrix[first][second]
            + unscaled_zones[first]["equivalent_radius"]
            + unscaled_zones[second]["equivalent_radius"]
            for index, first in enumerate(unscaled_audit["zone_ids"])
            for second in unscaled_audit["zone_ids"][index + 1:]
        )
        self.assertAlmostEqual(maximum_centroid, unscaled_maximum * scale)
        self.assertAlmostEqual(z1_maximum, max(unscaled_matrix["Z1"].values()) * scale)
        self.assertAlmostEqual(approximate_diameter, unscaled_diameter * scale)
        self.assertAlmostEqual(
            self.derived["total_synthetic_area"],
            unscaled["total_synthetic_area"] * scale ** 2,
        )

    def test_equivalent_radius_and_intrazonal_distance_are_derived(self):
        audit = build_spatial_audit(self.derived)
        for zone in self.derived["zones"]:
            equivalent = math.sqrt(zone["synthetic_area"] / math.pi)
            intrazonal = 128.0 / (45.0 * math.pi) * equivalent
            self.assertAlmostEqual(audit["equivalent_radius"][zone["zone_id"]], equivalent)
            self.assertAlmostEqual(
                audit["mean_intrazonal_distance"][zone["zone_id"]], intrazonal
            )

    def test_dynamic_age_calibration_hits_target_and_preserves_order(self):
        target = self.derived["citywide_age_target"]
        implied = self.derived["implied_city_age_share"]
        tolerance = self.derived["age_share_tolerance"]
        for group in AGE_GROUPS:
            self.assertTrue(math.isclose(implied[group], target[group], abs_tol=tolerance))

        zones = self.derived["zones"]
        for zone in zones:
            values = zone["calibrated_age_composition"]
            self.assertTrue(all(0 <= value <= 1 for value in values.values()))
            self.assertAlmostEqual(sum(values.values()), 1.0)
        for group in AGE_GROUPS:
            base_order = sorted(zones, key=lambda zone: zone["base_age_composition"][group])
            calibrated_order = sorted(
                zones, key=lambda zone: zone["calibrated_age_composition"][group]
            )
            self.assertEqual(
                [zone["zone_id"] for zone in base_order],
                [zone["zone_id"] for zone in calibrated_order],
            )

    def test_infeasible_uniform_age_calibration_raises(self):
        zones = [
            {
                "zone_id": "A",
                "population_weight": 0.5,
                "base_age_composition": {"18-39": 1.0, "40-59": 0.0, "60+": 0.0},
            },
            {
                "zone_id": "B",
                "population_weight": 0.5,
                "base_age_composition": {"18-39": 0.0, "40-59": 0.0, "60+": 1.0},
            },
        ]
        with self.assertRaisesRegex(ValueError, "infeasible"):
            calibrate_age_composition(
                zones, {"18-39": 0.4, "40-59": 0.33, "60+": 0.27}, 1e-9
            )

    def test_dynamic_quota_margins_for_required_totals(self):
        for total_agents in (9, 17, 1000, 1001, 1379):
            with self.subTest(total_agents=total_agents):
                first = allocate_zone_age_quotas(self.derived, total_agents)
                second = allocate_zone_age_quotas(self.derived, total_agents)
                self.assertEqual(first, second)
                self.assertEqual(first["total_agents_used"], total_agents)
                self.assertEqual(first["total_agents_source"], "explicit_argument")
                matrix = first["quota_matrix"]
                self.assertEqual(sum(first["zone_totals"].values()), total_agents)
                self.assertEqual(sum(first["city_age_totals"].values()), total_agents)
                self.assertEqual(
                    sum(sum(row.values()) for row in matrix.values()), total_agents
                )
                for zone_id, row in matrix.items():
                    self.assertEqual(sum(row.values()), first["zone_totals"][zone_id])
                    self.assertTrue(
                        all(isinstance(value, int) and value >= 0 for value in row.values())
                    )
                for group in AGE_GROUPS:
                    self.assertEqual(
                        sum(row[group] for row in matrix.values()),
                        first["city_age_totals"][group],
                    )

    def test_small_total_does_not_require_every_cell_positive(self):
        quotas = allocate_zone_age_quotas(self.derived, 9)["quota_matrix"]
        self.assertTrue(any(value == 0 for row in quotas.values() for value in row.values()))

    def test_total_agents_override_and_configuration_source(self):
        configured = allocate_zone_age_quotas(self.derived)
        overridden = allocate_zone_age_quotas(self.derived, 17)
        self.assertEqual(configured["total_agents_used"], self.config["total_agents"])
        self.assertEqual(configured["total_agents_source"], "configuration")
        self.assertEqual(overridden["total_agents_used"], 17)
        self.assertEqual(overridden["total_agents_source"], "explicit_argument")

        missing = deepcopy(self.derived)
        missing["total_agents"] = None
        with self.assertRaisesRegex(ValueError, "must be provided"):
            allocate_zone_age_quotas(missing)

    def test_current_1000_agent_quota_is_fully_mixed(self):
        quotas = allocate_zone_age_quotas(self.derived, 1000)
        self.assertTrue(
            all(value > 0 for row in quotas["quota_matrix"].values() for value in row.values())
        )
        self.assertEqual(quotas["city_age_totals"], {"18-39": 400, "40-59": 330, "60+": 270})

    def test_invalid_configuration_cases_raise(self):
        cases = []

        duplicate_ring = deepcopy(self.config)
        duplicate_ring["rings"][1]["ring_id"] = duplicate_ring["rings"][0]["ring_id"]
        cases.append(duplicate_ring)

        duplicate_zone = deepcopy(self.config)
        duplicate_zone["zones"][1]["zone_id"] = duplicate_zone["zones"][0]["zone_id"]
        cases.append(duplicate_zone)

        unknown_ring = deepcopy(self.config)
        unknown_ring["zones"][0]["spatial_ring"] = "unknown"
        cases.append(unknown_ring)

        invalid_radii = deepcopy(self.config)
        invalid_radii["rings"][0]["outer_radius"] = 0
        cases.append(invalid_radii)

        invalid_coverage = deepcopy(self.config)
        invalid_coverage["rings"][0]["modeled_coverage_share"] = 0
        cases.append(invalid_coverage)

        invalid_ring_share = deepcopy(self.config)
        invalid_ring_share["zones"][1]["within_ring_area_share"] = 0.4
        cases.append(invalid_ring_share)

        centroid_outside = deepcopy(self.config)
        centroid_outside["zones"][1]["radial_distance_from_center"] = 9
        cases.append(centroid_outside)

        invalid_angle = deepcopy(self.config)
        invalid_angle["zones"][1]["angular_position_degrees"] = 360
        cases.append(invalid_angle)

        center_angle = deepcopy(self.config)
        center_angle["zones"][0]["angular_position_degrees"] = 0
        cases.append(center_angle)

        invalid_density = deepcopy(self.config)
        invalid_density["zones"][0]["residential_density_factor"] = 0
        cases.append(invalid_density)

        invalid_scale = deepcopy(self.config)
        invalid_scale["spatial_scale"] = 0
        cases.append(invalid_scale)

        invalid_city_age = deepcopy(self.config)
        invalid_city_age["citywide_age_target"]["18-39"] = 0.5
        cases.append(invalid_city_age)

        invalid_zone_age = deepcopy(self.config)
        invalid_zone_age["zones"][0]["base_age_composition"]["60+"] = -0.1
        cases.append(invalid_zone_age)

        for index, invalid in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate_zone_configuration(invalid)


if __name__ == "__main__":
    unittest.main()
