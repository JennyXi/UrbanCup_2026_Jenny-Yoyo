"""Convenience entry point for the 200-Agent A1 fleet threshold screen."""

from pathlib import Path

from scripts import run_formal_nine_zone_50_supply_threshold as runner


ROOT = Path(__file__).resolve().parents[1]
runner.DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_200_supply_threshold.json"
runner.DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_200_supply_threshold_a1"


if __name__ == "__main__":
    runner.main()
