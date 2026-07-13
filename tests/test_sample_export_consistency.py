import csv
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


if __name__ == "__main__":
    unittest.main()
