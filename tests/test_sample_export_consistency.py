import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_50_agent_spatial_example import main


class SampleExportConsistencyTests(unittest.TestCase):
    def test_every_activity_inherits_agent_identity_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            main(Path(folder))
            with (Path(folder) / "agents.csv").open(encoding="utf-8-sig", newline="") as stream:
                agents = {row["agent_id"]: row for row in csv.DictReader(stream)}
            with (Path(folder) / "activities.csv").open(encoding="utf-8-sig", newline="") as stream:
                activities = list(csv.DictReader(stream))
            self.assertTrue(activities)
            for activity in activities:
                agent = agents[activity["agent_id"]]
                for field in ("home_zone", "home_zone_name", "age_group", "work_status", "medical_need_level"):
                    self.assertEqual(activity[field], agent[field])

            with (Path(folder) / "validation.json").open(encoding="utf-8") as stream:
                validation = json.load(stream)
            self.assertTrue(validation["all_passed"])
            self.assertTrue(validation["checks"])
            self.assertTrue(all(check["passed"] and check["violation_count"] == 0 for check in validation["checks"].values()))

            with (Path(folder) / "leg_mode_time_supply.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                time_supply = list(csv.DictReader(stream))
            self.assertEqual(len(time_supply), 3004)
            self.assertEqual(
                len({(row["leg_id"], row["mode"]) for row in time_supply}),
                len(time_supply),
            )
            required = {
                "departure_time", "time_period", "operating", "base_total_time_min",
                "period_speed_multiplier", "period_wait_time_min",
                "period_transfer_penalty_min", "time_adjusted_total_time_min",
                "latest_feasible_departure", "supply_level",
            }
            self.assertTrue(required.issubset(time_supply[0]))


if __name__ == "__main__":
    unittest.main()
