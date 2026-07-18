"""Run the ten-seed 36-versus-24 vehicle A1 confirmation experiment."""

from pathlib import Path

from scripts import run_formal_nine_zone_50_supply_threshold as runner


ROOT = Path(__file__).resolve().parents[1]
runner.DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_200_supply_confirmation.json"
runner.DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_200_supply_confirmation_a1_10seeds"


if __name__ == "__main__":
    runner.main()
