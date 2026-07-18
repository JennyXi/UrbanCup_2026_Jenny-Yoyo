from __future__ import annotations

import unittest

from custom.agents.agent_population import AgentProfile
from scripts.run_formal_nine_zone_50_elder_digital_access import (
    DEFAULT_CONFIG_PATH,
    _community_proxy_ids,
    _load,
)


class FormalNineZoneElderDigitalAccessTests(unittest.TestCase):
    def test_configuration_contains_d0_to_d4_and_selected_fleet(self):
        config = _load(DEFAULT_CONFIG_PATH)
        self.assertEqual(sum(config["initial_vehicles"].values()), 12)
        self.assertEqual(
            list(config["elder_digital_access_experiment"]["policies"]),
            [
                "D0_baseline", "D1_targeted_digital_training_75pct",
                "D2_family_assistance_90pct", "D3_universal_elder_digital_access",
                "D4_limited_community_phone_25pct",
            ],
        )

    def test_d4_proxy_is_stable_and_only_covers_nondigital_unassisted_elders(self):
        config = _load(DEFAULT_CONFIG_PATH)
        profiles = [
            AgentProfile(1, "60+", (60, 100), True, False, False, "x", smartphone_access=False),
            AgentProfile(2, "60+", (60, 100), True, False, True, "x", smartphone_access=True),
            AgentProfile(3, "60+", (60, 100), True, True, False, "x", smartphone_access=True),
            AgentProfile(4, "40-59", (40, 59), False, True, None, "x", smartphone_access=True),
        ]
        first = _community_proxy_ids(
            profiles, "D4_limited_community_phone_25pct", seed=47, config=config,
        )
        second = _community_proxy_ids(
            profiles, "D4_limited_community_phone_25pct", seed=47, config=config,
        )
        self.assertEqual(first, second)
        self.assertTrue(first <= {1})


if __name__ == "__main__":
    unittest.main()
