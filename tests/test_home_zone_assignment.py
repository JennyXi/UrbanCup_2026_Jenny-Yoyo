import unittest
from collections import Counter, defaultdict
from copy import deepcopy

from custom.agents.agent_population import generate_population_agents
from custom.spatial.home_zone_assignment import VALID_ZONE_IDS, assign_home_zones
from custom.spatial.zone_configuration import (
    allocate_zone_age_quotas,
    derive_spatial_configuration,
    load_zone_configuration,
)


class HomeZoneAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        derived = derive_spatial_configuration(load_zone_configuration())
        cls.quota_result = allocate_zone_age_quotas(derived, 1000)
        cls.quotas = cls.quota_result["quota_matrix"]

    def setUp(self):
        self.agents = generate_population_agents(total_agents=1000, seed=42)

    @staticmethod
    def mapping(agents):
        return {agent.agent_id: agent.home_zone for agent in agents}

    @staticmethod
    def quota_counts(agents):
        counts = defaultdict(Counter)
        for agent in agents:
            counts[agent.home_zone][agent.age_group] += 1
        return {
            zone_id: {age_group: counts[zone_id][age_group] for age_group in ("18-39", "40-59", "60+")}
            for zone_id in VALID_ZONE_IDS
        }

    def test_1000_agent_zone_age_quotas_match_exactly(self):
        assigned = assign_home_zones(self.agents, self.quotas, seed=2026)
        self.assertEqual(self.quota_counts(assigned), self.quotas)

    def test_fixed_seed_is_reproducible(self):
        first = assign_home_zones(self.agents, self.quotas, seed=2026)
        second = assign_home_zones(self.agents, self.quotas, seed=2026)
        self.assertEqual(self.mapping(first), self.mapping(second))

    def test_input_order_does_not_change_individual_assignment(self):
        forward = assign_home_zones(self.agents, self.quotas, seed=2026)
        reversed_input = assign_home_zones(list(reversed(self.agents)), self.quotas, seed=2026)
        self.assertEqual(self.mapping(forward), self.mapping(reversed_input))

    def test_quota_zone_key_order_does_not_change_assignment(self):
        reversed_quotas = {
            zone_id: self.quotas[zone_id] for zone_id in reversed(tuple(self.quotas))
        }
        normal = assign_home_zones(self.agents, self.quotas, seed=2026)
        reordered = assign_home_zones(self.agents, reversed_quotas, seed=2026)
        self.assertEqual(self.mapping(normal), self.mapping(reordered))

    def test_different_seed_changes_mapping_but_not_quotas(self):
        first = assign_home_zones(self.agents, self.quotas, seed=2026)
        second = assign_home_zones(self.agents, self.quotas, seed=2027)
        self.assertNotEqual(self.mapping(first), self.mapping(second))
        self.assertEqual(self.quota_counts(first), self.quotas)
        self.assertEqual(self.quota_counts(second), self.quotas)

    def test_agent_total_mismatch_raises(self):
        with self.assertRaisesRegex(ValueError, "Agent total"):
            assign_home_zones(self.agents[:-1], self.quotas, seed=2026)

    def test_age_group_count_mismatch_raises(self):
        altered = deepcopy(self.agents)
        altered[0].age_group = "40-59"
        with self.assertRaisesRegex(ValueError, "Agent count"):
            assign_home_zones(altered, self.quotas, seed=2026)

    def test_other_agent_attributes_and_inputs_are_unchanged(self):
        before = [agent.to_dict() for agent in self.agents]
        assigned = assign_home_zones(self.agents, self.quotas, seed=2026)
        after_inputs = [agent.to_dict() for agent in self.agents]
        self.assertEqual(before, after_inputs)

        for original, result in zip(before, assigned):
            result_data = result.to_dict()
            home_zone = result_data.pop("home_zone")
            expected = dict(original)
            expected.pop("home_zone")
            self.assertEqual(result_data, expected)
            self.assertIn(home_zone, VALID_ZONE_IDS)

    def test_each_agent_has_exactly_one_valid_home_zone(self):
        assigned = assign_home_zones(self.agents, self.quotas, seed=2026)
        self.assertEqual(len(assigned), 1000)
        self.assertEqual(len({agent.agent_id for agent in assigned}), 1000)
        self.assertTrue(all(agent.home_zone in VALID_ZONE_IDS for agent in assigned))

    def test_existing_home_zone_raises_without_overwrite(self):
        self.agents[0].home_zone = "Z1"
        with self.assertRaisesRegex(ValueError, "already has home_zone"):
            assign_home_zones(self.agents, self.quotas, seed=2026)
        self.assertEqual(self.agents[0].home_zone, "Z1")

    def test_dictionary_agents_preserve_arbitrary_attributes(self):
        agents = []
        agent_id = 1
        for age_group, count in self.quota_result["city_age_totals"].items():
            for _ in range(count):
                agents.append(
                    {
                        "agent_id": agent_id,
                        "age_group": age_group,
                        "digital_access": age_group != "60+",
                        "family_assistance": None if age_group != "60+" else False,
                        "trip_plan_template": {"source": "unchanged"},
                    }
                )
                agent_id += 1
        assigned = assign_home_zones(agents, self.quotas, seed=2026)
        for original, result in zip(agents, assigned):
            self.assertNotIn("home_zone", original)
            expected = deepcopy(original)
            expected["home_zone"] = result["home_zone"]
            self.assertEqual(result, expected)

    def test_invalid_quota_matrix_raises(self):
        invalid = deepcopy(self.quotas)
        del invalid["Z9"]
        with self.assertRaisesRegex(ValueError, "exactly"):
            assign_home_zones(self.agents, invalid, seed=2026)

    def test_age_group_labels_are_not_implicitly_converted(self):
        altered = deepcopy(self.agents)
        altered[0].age_group = "18_39"
        with self.assertRaisesRegex(ValueError, "Unsupported age_group"):
            assign_home_zones(altered, self.quotas, seed=2026)


if __name__ == "__main__":
    unittest.main()
