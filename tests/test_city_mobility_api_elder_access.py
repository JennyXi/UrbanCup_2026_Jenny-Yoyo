from pathlib import Path
import unittest

from custom.agents.agent_population import generate_population_agents
from scripts.run_city_mobility_200_api import (
    DEFAULT_ELDER_ACCESS_CONFIG,
    _apply_elder_access_policy,
)


def _rows(total: int = 200, seed: int = 47):
    return [row.to_dict() for row in generate_population_agents(total, seed=seed)]


def _apply(policy: str, seed: int = 47):
    return _apply_elder_access_policy(
        _rows(seed=seed),
        policy,
        seed=seed,
        config_path=Path(DEFAULT_ELDER_ACCESS_CONFIG),
    )


class CityMobilityApiElderAccessTests(unittest.TestCase):
    def test_d0_preserves_population_and_reports_baseline(self):
        baseline = _rows()
        changed, roster, audit = _apply("D0")
        self.assertEqual(changed, baseline)
        self.assertEqual(len(roster), audit["elder_count"])
        self.assertEqual(audit["policy"], "D0_baseline")
        self.assertEqual(audit["newly_digital_elder_count"], 0)

    def test_d1_reaches_75_percent_without_changing_nonelders(self):
        changed, _, audit = _apply("D1")
        elders = [row for row in changed if row["is_elder"]]
        self.assertEqual(
            sum(row["digital_access"] for row in elders),
            int(len(elders) * 0.75 + 0.5),
        )
        self.assertEqual(
            audit["policy"], "D1_targeted_digital_training_75pct"
        )
        self.assertEqual(audit["nonelder_profile_changes"], 0)

    def test_d3_makes_every_elder_digitally_accessible_with_a_device(self):
        changed, _, audit = _apply("D3")
        elders = [row for row in changed if row["is_elder"]]
        self.assertTrue(all(
            row["digital_access"] and row["smartphone_access"] for row in elders
        ))
        self.assertEqual(
            audit["policy_elder_digital_count"], audit["elder_count"]
        )
        self.assertEqual(audit["nonelder_profile_changes"], 0)

    def test_assignment_is_reproducible_and_rejects_unknown_policy(self):
        self.assertEqual(_apply("D1"), _apply("D1"))
        with self.assertRaisesRegex(ValueError, "elder access policy"):
            _apply("D2")


if __name__ == "__main__":
    unittest.main()
