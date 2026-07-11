import unittest

from custom.agents.agent_population import (
    AgentProfile,
    generate_population_agents,
    summarize_population,
    validate_agent_profile,
)


class AgentPopulationT1Tests(unittest.TestCase):
    def test_new_coupon_attributes_default_to_unconfigured(self):
        agents = generate_population_agents(total_agents=100, seed=42)

        self.assertTrue(all(agent.coupon_awareness_probability is None for agent in agents))
        self.assertTrue(all(agent.coupon_claim_probability is None for agent in agents))
        self.assertTrue(all(agent.independent_ride_hailing is None for agent in agents))

        summary = summarize_population(agents)
        self.assertEqual(
            summary["coupon_attributes"],
            {
                "awareness_configured": 0,
                "claim_configured": 0,
                "independent_ride_hailing_configured": 0,
            },
        )

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
