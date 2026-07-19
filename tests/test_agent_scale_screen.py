import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from custom.agents.emergence_experiment import load_emergence_config
from scripts import run_agent_scale_screen as scale_module
from scripts.run_agent_scale_screen import run_agent_scale_screen


class AgentScaleScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_emergence_config()
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = run_agent_scale_screen(
            seed_start=3001, seed_count=1, agent_counts=[50, 100],
            output=Path(cls.temp.name), config=cls.config,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_configured_screen_is_50_100_200_500(self):
        self.assertEqual(self.config["agent_scale_screen"]["agent_counts"], [50, 100, 200, 500])

    def test_every_scale_policy_weather_day_is_present(self):
        rows = self.result["scale_system_per_seed"]
        self.assertEqual(len(rows), 2 * 4 * 3 * 2)
        self.assertEqual({row["population_agents"] for row in rows}, {50, 100})

    def test_group_agent_counts_conserve_population(self):
        rows = self.result["scale_group_per_seed"]
        for count in (50, 100):
            selected = [row for row in rows if
                        row["population_agents"] == count and row["policy"] == "D0_baseline"
                        and row["weather_scenario"] == "W0" and row["day_type"] == "workday"]
            self.assertEqual(sum(row["agent_count"] for row in selected), count)

    def test_transport_supply_is_fixed_across_population_scales(self):
        rows = [row for row in self.result["scale_system_per_seed"] if
                row["policy"] == "D0_baseline" and row["weather_week"] == "W0"
                and row["day_type"] == "workday"]
        self.assertEqual(len({row["scheduled_bus_vehicle_trips"] for row in rows}), 1)

    def test_spillover_table_has_one_row_per_scale_weather_day(self):
        self.assertEqual(len(self.result["d3_spillover_vs_d0"]), 2 * 3 * 2)
        self.assertTrue(all(
            isinstance(row["any_competition_or_displacement_flag"], bool)
            for row in self.result["d3_spillover_vs_d0"]
        ))

    def test_candidate_table_covers_five_criteria(self):
        self.assertEqual(len(self.result["candidate_competition_scales"]), 3 * 2 * 5)
        self.assertTrue(all(row["not_a_calibrated_population_threshold"] for row in self.result["candidate_competition_scales"]))

    def test_all_inherited_consistency_checks_pass(self):
        self.assertTrue(all(row["passed"] for row in self.result["scale_consistency_checks"]))

    def test_expected_top_level_outputs_exist(self):
        expected = {
            "scale_system_per_seed.csv", "scale_group_per_seed.csv",
            "scale_screen_summary.csv", "d3_spillover_vs_d0.csv",
            "candidate_competition_scales.csv", "scale_consistency_checks.csv",
            "scale_screen_metadata.json", "agents_50", "agents_100",
        }
        self.assertEqual(expected, {row.name for row in Path(self.temp.name).iterdir()})

    def test_cli_accepts_configured_subset(self):
        with patch("sys.argv", [
            "run_agent_scale_screen", "--agent-counts", "100", "200",
            "--seed-count", "1", "--output", str(Path(self.temp.name) / "cli"),
        ]), patch.object(scale_module, "run_agent_scale_screen") as runner:
            runner.return_value = {
                "scale_system_per_seed": [], "scale_consistency_checks": [],
                "candidate_competition_scales": [],
            }
            scale_module.main()
            self.assertEqual(runner.call_args.kwargs["agent_counts"], [100, 200])


if __name__ == "__main__":
    unittest.main()
