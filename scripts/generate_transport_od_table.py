"""Export the complete 9 x 9 x 4 baseline transport alternative table."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.transport.network import OUTPUT_FIELDS, build_all_od_options, build_transport_network


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "transport_network"


def main(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    network = build_transport_network()
    rows = build_all_od_options(network)
    with (output_dir / "od_mode_options.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "network_id": network["config"]["network_id"],
        "zone_count": len(network["zone_ids"]),
        "mode_count": 4,
        "row_count": len(rows),
        "available_by_mode": dict(Counter(row["mode"] for row in rows if row["available"])),
        "unavailable_by_mode": dict(Counter(row["mode"] for row in rows if not row["available"])),
        "agent_mode_choice_implemented": False,
        "weather_parameters_applied": False,
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    main(args.output_dir)
