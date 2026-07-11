import unittest
from collections import Counter

from custom.agents.agent_population import (
    AgentProfile,
    FLEXIBLE_NON_WORKER_SHARES,
    MEDICAL_NEED_LEVEL_SHARES,
    PART_TIME_WORKER_SHARE,
    generate_population_agents,
    summarize_population,
    validate_agent_profile,
)


class AgentPopulationT1Tests(unittest.TestCase):
    def test_new_coupon_attributes_default_to_unconfigured(self):
        agents = generate_population_agents(total_agents=100, seed=42)

        self.assertTrue(all(agent.coupon_awareness_probability is None for agent in agents))
        self.assertTrue(all(agent.coupon_claim_probability is None for agent in agents))
        self.assertTrue(all(agent.independent_ride_hailing is True for agent in agents if not agent.is_elder))
        self.assertTrue(all(agent.independent_ride_hailing is None for agent in agents if agent.is_elder))

        summary = summarize_population(agents)
        self.assertEqual(
            summary["coupon_attributes"],
            {
                "awareness_configured": 0,
                "claim_configured": 0,
                "independent_ride_hailing_configured": 73,
            },
        )

    def test_work_and_medical_statuses_are_stable_and_age_appropriate(self):
        agents = generate_population_agents(total_agents=100, seed=42)
        for agent in agents:
            if agent.age_group in {"18-39", "40-59"}:
                self.assertIn(agent.work_status, {"regular_worker", "flexible_non_worker"})
                self.assertIsNone(agent.medical_need_level)
                self.assertTrue(agent.digital_access)
                self.assertTrue(agent.independent_ride_hailing)
            else:
                self.assertIn(agent.work_status, {"retired", "part_time_worker"})
                self.assertIn(agent.medical_need_level, {"low", "standard", "high"})

    def test_status_share_configuration_scales_with_population(self):
        self.assertEqual(FLEXIBLE_NON_WORKER_SHARES, {"18-39": 0.10, "40-59": 0.08})
        self.assertEqual(PART_TIME_WORKER_SHARE, 0.17)
        self.assertEqual(MEDICAL_NEED_LEVEL_SHARES, {"low": 0.35, "standard": 0.55, "high": 0.10})
        for total in (50, 100, 200):
            agents = generate_population_agents(total, seed=47)
            age_counts = Counter(agent.age_group for agent in agents)
            status_counts = Counter((agent.age_group, agent.work_status) for agent in agents)
            self.assertEqual(status_counts[("18-39", "flexible_non_worker")], int(age_counts["18-39"] * 0.10 + 0.5))
            self.assertEqual(status_counts[("40-59", "flexible_non_worker")], int(age_counts["40-59"] * 0.08 + 0.5))
            self.assertEqual(status_counts[("60+", "part_time_worker")], int(age_counts["60+"] * 0.17 + 0.5))

    def test_family_assistance_only_applies_to_elder_agents(self):
        agents = generate_population_agents(total_agents=100, seed=42)

        self.assertTrue(all(agent.family_assistance is None for agent in agents if not agent.is_elder))
        self.assertTrue(all(isinstance(agent.family_assistance, bool) for agent in agents if agent.is_elder))

    def test_to_dict_includes_new_coupon_attributes(self):
        result = generate_population_agents(total_agents=1, seed=42)[0].to_dict()

        self.assertIn("coupon_awareness_probability", result)
        self.assertIn("coupon_claim_probability", result)
        self.assertIn("independent_ride_hailing", result)

    def test_invalid_coupon_attributes_are_rejected(self):
        invalid_values = [
            ("coupon_awareness_probability", -0.01),
            ("coupon_awareness_probability", 1.01),
            ("coupon_claim_probability", "unknown"),
            ("independent_ride_hailing", 1),
        ]

        for field_name, value in invalid_values:
            with self.subTest(field_name=field_name, value=value):
                agent = self._make_elder_agent()
                setattr(agent, field_name, value)
                with self.assertRaises(ValueError):
                    validate_agent_profile(agent)

    def test_non_elder_family_assistance_is_rejected(self):
        agent = AgentProfile(
            agent_id=1,
            age_group="18-39",
            age_range=(18, 39),
            is_elder=False,
            digital_access=True,
            family_assistance=False,
            segment="18-39",
        )

        with self.assertRaisesRegex(ValueError, "non-elder"):
            validate_agent_profile(agent)

    def test_generation_remains_reproducible(self):
        first = [agent.to_dict() for agent in generate_population_agents(100, seed=7)]
        second = [agent.to_dict() for agent in generate_population_agents(100, seed=7)]

        self.assertEqual(first, second)

    @staticmethod
    def _make_elder_agent():
        return AgentProfile(
            agent_id=1,
            age_group="60+",
            age_range=(60, 99),
            is_elder=True,
            digital_access=True,
            family_assistance=False,
            segment="60+",
        )


if __name__ == "__main__":
    unittest.main()
