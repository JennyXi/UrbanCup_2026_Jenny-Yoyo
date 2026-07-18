from __future__ import annotations

import csv
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results"
FIGURES = ROOT / "docs" / "figures"


class CompetitionReportTests(unittest.TestCase):
    def test_committed_artifacts_are_current(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-m", "scripts.build_competition_report", "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_paired_request_effect_has_positive_interval(self) -> None:
        with (RESULTS / "paired_policy_effects.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        target = next(
            row
            for row in rows
            if row["policy"] == "C1_public_limited"
            and row["weather_scenario"] == "W2"
            and row["metric"] == "ride_hailing_requests"
        )
        self.assertEqual(int(target["seed_count"]), 10)
        self.assertGreater(float(target["ci95_low"]), 0.0)

    def test_failure_effect_is_not_overclaimed(self) -> None:
        with (RESULTS / "paired_policy_effects.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        target = next(
            row
            for row in rows
            if row["policy"] == "C1_public_limited"
            and row["weather_scenario"] == "W2"
            and row["metric"] == "failed_ride_hailing_requests"
        )
        self.assertLessEqual(float(target["ci95_low"]), 0.0)
        self.assertGreaterEqual(float(target["ci95_high"]), 0.0)

    def test_svg_figures_are_well_formed_and_accessible(self) -> None:
        expected = {
            "nine_zone_network.svg",
            "weather_mode_shift.svg",
            "w2_policy_tradeoffs.svg",
            "coupon_funnel.svg",
        }
        self.assertEqual({path.name for path in FIGURES.glob("*.svg")}, expected)
        for path in FIGURES.glob("*.svg"):
            root = ET.parse(path).getroot()
            tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
            self.assertIn("title", tags, path)
            self.assertIn("desc", tags, path)

    def test_readme_describes_completed_mode_choice(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Agent 方式选择", readme)
        self.assertNotIn("尚未实现 Agent 交通方式选择", readme)


if __name__ == "__main__":
    unittest.main()
