"""Run the paired 200-Agent coupon discount-intensity sensitivity experiment."""

from pathlib import Path

from scripts import run_formal_nine_zone_50_coupon_pricing_experiment as runner


ROOT = Path(__file__).resolve().parents[1]
runner.DEFAULT_CONFIG = ROOT / "config" / "formal_nine_zone_200_coupon_sensitivity.json"
runner.DEFAULT_OUTPUT = ROOT / "outputs" / "formal_nine_zone_200_coupon_sensitivity"


if __name__ == "__main__":
    runner.main()
