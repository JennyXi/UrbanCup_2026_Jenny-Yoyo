"""Read-only acceptance audit for the 10,000-person metro-transfer contexts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ARMS = (
    "W0_P0",
    "W1_P0",
    "W2_P0",
    "W2_P1_low",
    "W2_P1_high",
    "W2_P2_low",
    "W2_P2_high",
    "W2_P3_low",
    "W2_P3_high",
    "W2_P4",
)
MODES = ("walk", "bus", "metro", "ride_hailing")
CONTEXT_SCHEMA = "urban-cup-context-partition-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        str(key): json.loads(str(value))
        for key, value in connection.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        )
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def is_non_negative_integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def is_non_negative_number(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def audit(
    output_root: Path,
    expected_code_sha256: str,
    expected_agents: int,
    partition_size: int,
) -> dict[str, Any]:
    require(expected_agents > 0, "expected-agents must be positive")
    require(partition_size > 0, "partition-size must be positive")
    require(
        expected_agents % partition_size == 0,
        "expected-agents must end on a partition boundary",
    )
    require(
        expected_agents % len(ARMS) == 0,
        "expected-agents must divide evenly across scenario arms",
    )
    expected_partitions = expected_agents // partition_size
    expected_arm_count = expected_agents // len(ARMS)

    root = output_root.resolve()
    manifest_path = root / "context_manifest.json"
    require(manifest_path.is_file(), f"missing manifest: {manifest_path}")
    partials = sorted(root.rglob("*.partial"))
    require(not partials, f"partial outputs remain: {partials}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest = {
        "status": "STRUCTURAL_TEST_CONTEXTS_READY",
        "seed": 47,
        "population_size": 100000,
        "partition_size": partition_size,
        "prepared_partitions": expected_partitions,
        "prepared_agents": expected_agents,
        "code_sha256": expected_code_sha256,
    }
    for key, value in expected_manifest.items():
        require(
            manifest.get(key) == value,
            f"manifest {key!r}: expected {value!r}, got {manifest.get(key)!r}",
        )
    require(
        manifest.get("scenario_arm_counts")
        == {arm: expected_arm_count for arm in ARMS},
        "manifest scenario-arm counts do not match the frozen ten-arm design",
    )

    records = manifest.get("partitions")
    require(isinstance(records, list), "manifest partitions must be a list")
    require(
        len(records) == expected_partitions,
        "manifest partition count is incorrect",
    )
    require(
        [record.get("partition_index") for record in records]
        == list(range(expected_partitions)),
        "manifest partitions are missing, duplicated, or out of order",
    )

    context_dir = root / "contexts"
    expected_files = {
        context_dir / f"part-{index:05d}.sqlite"
        for index in range(expected_partitions)
    }
    actual_files = set(context_dir.glob("part-*.sqlite"))
    require(actual_files == expected_files, "context partition file set is incorrect")

    all_ids: list[int] = []
    observed_arms: Counter[str] = Counter()
    available_mode_options = 0
    available_metro_options = 0
    transfer_metro_options = 0
    line_transfer_metro_options = 0
    mode_transfer_metro_options = 0

    for index, record in enumerate(records):
        path = context_dir / f"part-{index:05d}.sqlite"
        expected_start = index * partition_size + 1
        expected_end = expected_start + partition_size - 1
        expected_relative = f"contexts/part-{index:05d}.sqlite"
        require(record.get("relative_path") == expected_relative, f"bad path for {path}")
        require(record.get("start_agent_id") == expected_start, f"bad start ID in {path}")
        require(record.get("end_agent_id") == expected_end, f"bad end ID in {path}")
        require(record.get("agent_count") == partition_size, f"bad count in {path}")
        require(record.get("code_sha256") == expected_code_sha256, f"bad code hash in {path}")
        require(record.get("bytes") == path.stat().st_size, f"bad byte count in {path}")
        require(record.get("sha256") == sha256_file(path), f"bad file hash in {path}")

        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            require(integrity == [("ok",)], f"SQLite integrity failed in {path}")
            metadata = decode_metadata(connection)
            rows = list(
                connection.execute(
                    "SELECT agent_id, arm, context_json FROM contexts ORDER BY agent_id"
                )
            )
        finally:
            connection.close()

        expected_metadata = {
            "schema": CONTEXT_SCHEMA,
            "seed": 47,
            "population_size": 100000,
            "partition_size": partition_size,
            "partition_index": index,
            "start_agent_id": expected_start,
            "end_agent_id": expected_end,
            "agent_count": partition_size,
            "code_sha256": expected_code_sha256,
        }
        for key, value in expected_metadata.items():
            require(
                metadata.get(key) == value,
                f"metadata {key!r} mismatch in {path}",
            )
        require(len(rows) == partition_size, f"row count mismatch in {path}")
        require(
            [int(row[0]) for row in rows]
            == list(range(expected_start, expected_end + 1)),
            f"agent IDs are not contiguous in {path}",
        )

        partition_arms: Counter[str] = Counter()
        for raw_agent_id, raw_arm, context_json in rows:
            agent_id = int(raw_agent_id)
            arm = str(raw_arm)
            expected_arm = ARMS[(agent_id - 1) % len(ARMS)]
            require(arm == expected_arm, f"arm mismatch for agent {agent_id}")
            context = json.loads(context_json)
            require(context.get("agent_id") == agent_id, f"embedded ID mismatch for {agent_id}")
            require(
                context.get("scenario", {}).get("arm") == arm,
                f"embedded arm mismatch for {agent_id}",
            )
            require(
                context.get("model_boundaries", {}).get(
                    "t10_excess_flow_pcu_per_hour"
                )
                == 0.0,
                f"T10 pre-choice flow is not zero for agent {agent_id}",
            )

            options = context.get("mode_options")
            require(isinstance(options, list), f"mode options missing for agent {agent_id}")
            require(
                [option.get("mode") for option in options] == list(MODES),
                f"mode option set or order changed for agent {agent_id}",
            )
            for option in options:
                mode = str(option["mode"])
                required_fields = (
                    "transfers",
                    "line_transfer_count",
                    "mode_transfer_count",
                    "transfer_time_min",
                )
                require(
                    all(field in option for field in required_fields),
                    f"metrofix fields missing for agent {agent_id}, mode {mode}",
                )
                values = {field: option[field] for field in required_fields}
                if bool(option.get("static_available")):
                    require(
                        is_non_negative_integer(values["line_transfer_count"]),
                        f"invalid line-transfer count for agent {agent_id}, mode {mode}",
                    )
                    require(
                        is_non_negative_integer(values["mode_transfer_count"]),
                        f"invalid mode-transfer count for agent {agent_id}, mode {mode}",
                    )
                    require(
                        is_non_negative_integer(values["transfers"]),
                        f"invalid total transfer count for agent {agent_id}, mode {mode}",
                    )
                    require(
                        values["transfers"]
                        == values["line_transfer_count"]
                        + values["mode_transfer_count"],
                        f"transfer-count identity failed for agent {agent_id}, mode {mode}",
                    )
                    require(
                        is_non_negative_number(values["transfer_time_min"]),
                        f"invalid transfer time for agent {agent_id}, mode {mode}",
                    )
                    available_mode_options += 1
                    if mode == "metro":
                        available_metro_options += 1
                        if values["transfers"] > 0:
                            transfer_metro_options += 1
                        if values["line_transfer_count"] > 0:
                            line_transfer_metro_options += 1
                        if values["mode_transfer_count"] > 0:
                            mode_transfer_metro_options += 1
                else:
                    require(
                        all(value is None for value in values.values()),
                        f"unavailable option has non-null metrofix fields for agent {agent_id}, mode {mode}",
                    )

            all_ids.append(agent_id)
            partition_arms[arm] += 1
            observed_arms[arm] += 1

        require(
            record.get("arm_counts") == dict(sorted(partition_arms.items())),
            f"manifest arm counts mismatch in {path}",
        )

    require(all_ids == list(range(1, expected_agents + 1)), "global ID coverage failed")
    require(
        observed_arms == Counter({arm: expected_arm_count for arm in ARMS}),
        "observed scenario-arm counts failed",
    )
    require(available_metro_options > 0, "no available metro option was generated")
    require(transfer_metro_options > 0, "no metro option with a transfer was generated")
    require(
        line_transfer_metro_options > 0,
        "no cross-line metro option was generated",
    )
    require(
        mode_transfer_metro_options > 0,
        "no feeder-to-metro mode transfer was generated",
    )
    require(
        len([path for path in root.rglob("*") if path.is_file()])
        == expected_partitions + 1,
        "unexpected output files were found",
    )

    return {
        "status": "METROFIX_CONTEXT_AUDIT_PASS",
        "output_root": str(root),
        "code_sha256": expected_code_sha256,
        "agent_count": len(all_ids),
        "partition_count": expected_partitions,
        "scenario_arm_counts": dict(sorted(observed_arms.items())),
        "available_mode_options": available_mode_options,
        "available_metro_options": available_metro_options,
        "metro_options_with_any_transfer": transfer_metro_options,
        "metro_options_with_line_transfer": line_transfer_metro_options,
        "metro_options_with_mode_transfer": mode_transfer_metro_options,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--expected-code-sha256", required=True)
    parser.add_argument("--expected-agents", type=int, default=10000)
    parser.add_argument("--partition-size", type=int, default=1000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = audit(
        Path(args.output_root),
        args.expected_code_sha256,
        args.expected_agents,
        args.partition_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
