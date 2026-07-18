"""Run the 200-Agent C0-C3 A1 coupon experiment at the 36-vehicle baseline."""

from pathlib import Path

from scripts import run_formal_nine_zone_50_coupon_experiment as runner


ROOT = Path(__file__).resolve().parents[1]
runner.DEFAULT_CONFIG_PATH = ROOT / "config" / "formal_nine_zone_200_coupon_experiment.json"
runner.DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_200_coupon_smoke"


if __name__ == "__main__":
    runner.main()
