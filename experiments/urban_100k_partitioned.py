"""Partitioned 100,000-person Urban Cup preparation and LLM execution.

This module is intentionally an execution *building block*, not an automatic
launcher.  It never submits Slurm jobs, retries jobs, downloads weights, or
loads a secret from disk.  A paid LLM partition must be invoked explicitly and
receives its credentials only through pre-existing environment variables.

The scalable layout is:

* one bounded SQLite database per context partition;
* one bounded SQLite database per LLM attempt/partition;
* AgentSociety workspaces only in the caller-supplied node-local directory;
* atomic ``.partial`` -> final renames and refusal to overwrite final output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# AgentSociety 2.8.1 validates LLM variables at import time, even for a command
# that only prepares deterministic contexts.  Supply non-routable placeholders
# solely to make the CPU/no-LLM subcommand importable, while retaining the set
# that was genuinely missing so the paid subcommand can reject it before any
# request.  These values are not credentials and no LLM object is constructed
# by ``prepare-contexts``.
LLM_ENVIRONMENT_NAMES = (
    "AGENTSOCIETY_LLM_API_KEY",
    "AGENTSOCIETY_LLM_API_BASE",
    "AGENTSOCIETY_LLM_MODEL",
    "AGENTSOCIETY_CODER_LLM_API_KEY",
    "AGENTSOCIETY_CODER_LLM_API_BASE",
    "AGENTSOCIETY_CODER_LLM_MODEL",
)
MISSING_LLM_ENVIRONMENT_AT_START = {
    name for name in LLM_ENVIRONMENT_NAMES if not os.getenv(name)
}
NO_LLM_IMPORT_DEFAULTS = {
    "AGENTSOCIETY_LLM_API_KEY": "dummy-no-llm-context-preparation",
    "AGENTSOCIETY_LLM_API_BASE": "http://127.0.0.1:9",
    "AGENTSOCIETY_LLM_MODEL": "no-llm-context-preparation",
    "AGENTSOCIETY_CODER_LLM_API_KEY": "dummy-no-llm-context-preparation",
    "AGENTSOCIETY_CODER_LLM_API_BASE": "http://127.0.0.1:9",
    "AGENTSOCIETY_CODER_LLM_MODEL": "no-llm-context-preparation",
}
for environment_name in MISSING_LLM_ENVIRONMENT_AT_START:
    os.environ[environment_name] = NO_LLM_IMPORT_DEFAULTS[environment_name]

import agentsociety2
import ray
from agentsociety2.contrib.env.simple_social_space import SimpleSocialSpace
from agentsociety2.society import AgentSociety
from agentsociety2.society.models import QuestionItem
from agentsociety2.society.questionnaire import Questionnaire

from urban_github_50_agents import (
    ARMS,
    SEED,
    URBAN_ROOT,
    build_assigned_population,
    build_decision_contexts_for_assigned_agents,
    flatten_results,
    force_minimal_ray_dashboard_agent,
)
from urban_router import UrbanReadOnlyRouter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
DEFAULT_POPULATION_SIZE = 100_000
DEFAULT_PARTITION_SIZE = 1_000
EXPECTED_AGENT_IDS = range(1, DEFAULT_POPULATION_SIZE + 1)
CONTEXT_SCHEMA = "urban-cup-context-partition-v1"
ANSWER_SCHEMA = "urban-cup-answer-partition-v1"
QUESTIONNAIRE_ID = "urban_mode_choice_intention_100k"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_sha256() -> str:
    digest = hashlib.sha256()
    local_paths = [
        Path(__file__),
        Path(__file__).with_name("urban_github_50_agents.py"),
        Path(__file__).with_name("urban_router.py"),
    ]
    reference_paths = sorted((URBAN_ROOT / "custom").rglob("*.py"))
    for config_suffix in ("*.json", "*.yaml", "*.yml"):
        reference_paths.extend(sorted((URBAN_ROOT / "config").rglob(config_suffix)))
    for path in [*local_paths, *sorted(set(reference_paths))]:
        try:
            relative_name = path.relative_to(URBAN_ROOT).as_posix()
        except ValueError:
            relative_name = path.name
        digest.update(relative_name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(
            f"Refusing to replace stale partial file; inspect it first: {partial}"
        )
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    partial.replace(path)


def partition_bounds(
    partition_index: int, population_size: int, partition_size: int
) -> tuple[int, int]:
    if partition_index < 0:
        raise ValueError("partition_index must be non-negative")
    start_id = partition_index * partition_size + 1
    end_id = min(population_size, start_id + partition_size - 1)
    if start_id > population_size:
        raise ValueError(
            f"partition {partition_index} starts after population {population_size}"
        )
    return start_id, end_id


def expected_arm(agent_id: int) -> str:
    return str(ARMS[(agent_id - 1) % len(ARMS)]["arm"])


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in connection.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        )
    }


def write_metadata(
    connection: sqlite3.Connection, values: dict[str, Any]
) -> None:
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [(key, canonical_json(value)) for key, value in sorted(values.items())],
    )


def decode_metadata_value(metadata: dict[str, str], key: str) -> Any:
    if key not in metadata:
        raise ValueError(f"Partition metadata is missing {key!r}")
    return json.loads(metadata[key])


def context_partition_path(root: Path, partition_index: int) -> Path:
    return root / "contexts" / f"part-{partition_index:05d}.sqlite"


def answer_partition_path(root: Path, attempt: int, partition_index: int) -> Path:
    return (
        root
        / "answers"
        / f"attempt-{attempt:03d}"
        / f"part-{partition_index:05d}.sqlite"
    )


def validate_context_partition(
    path: Path,
    *,
    partition_index: int,
    population_size: int,
    partition_size: int,
    expected_code_sha256: str | None = None,
) -> dict[str, Any]:
    start_id, end_id = partition_bounds(
        partition_index, population_size, partition_size
    )
    with connect_readonly(path) as connection:
        metadata = read_metadata(connection)
        if decode_metadata_value(metadata, "schema") != CONTEXT_SCHEMA:
            raise ValueError(f"Unexpected context schema in {path}")
        expected_metadata = {
            "seed": SEED,
            "population_size": population_size,
            "partition_size": partition_size,
            "partition_index": partition_index,
            "start_agent_id": start_id,
            "end_agent_id": end_id,
            "agent_count": end_id - start_id + 1,
        }
        for key, expected_value in expected_metadata.items():
            if decode_metadata_value(metadata, key) != expected_value:
                raise ValueError(f"Context metadata mismatch for {key} in {path}")
        recorded_code_sha256 = str(decode_metadata_value(metadata, "code_sha256"))
        if (
            expected_code_sha256 is not None
            and recorded_code_sha256 != expected_code_sha256
        ):
            raise ValueError(
                f"Code hash drift in completed context partition: {path}"
            )
        rows = list(
            connection.execute(
                "SELECT agent_id, arm, context_json FROM contexts ORDER BY agent_id"
            )
        )
    ids = [int(row[0]) for row in rows]
    expected = list(range(start_id, end_id + 1))
    if ids != expected:
        raise ValueError(
            f"{path} does not exactly cover IDs {start_id}-{end_id}"
        )
    arm_counts: Counter[str] = Counter()
    for agent_id, arm, context_json in rows:
        context = json.loads(context_json)
        if int(context.get("agent_id")) != int(agent_id):
            raise ValueError(f"Context ID mismatch for agent {agent_id} in {path}")
        if str(arm) != expected_arm(int(agent_id)):
            raise ValueError(f"Scenario arm mismatch for agent {agent_id} in {path}")
        if context.get("scenario", {}).get("arm") != str(arm):
            raise ValueError(f"Embedded arm mismatch for agent {agent_id} in {path}")
        if context.get("model_boundaries", {}).get(
            "t10_excess_flow_pcu_per_hour"
        ) != 0.0:
            raise ValueError(f"T10 pre-choice flow is not zero for agent {agent_id}")
        arm_counts[str(arm)] += 1
    return {
        "partition_index": partition_index,
        "start_agent_id": start_id,
        "end_agent_id": end_id,
        "agent_count": len(rows),
        "arm_counts": dict(sorted(arm_counts.items())),
        "relative_path": str(path.relative_to(path.parents[1])).replace("\\", "/"),
        "sha256": sha256_file(path),
        "code_sha256": recorded_code_sha256,
        "bytes": path.stat().st_size,
    }


def write_context_partition(
    path: Path,
    *,
    contexts: list[dict[str, Any]],
    design: dict[str, Any],
    partition_index: int,
    population_size: int,
    partition_size: int,
    code_sha256: str,
) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite completed partition: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(
            f"Refusing to replace stale partial partition: {partial}"
        )
    start_id, end_id = partition_bounds(
        partition_index, population_size, partition_size
    )
    expected_ids = list(range(start_id, end_id + 1))
    ids = [int(item["agent_id"]) for item in contexts]
    if ids != expected_ids:
        raise ValueError(
            f"Generated partition {partition_index} has non-contiguous agent IDs"
        )
    connection = sqlite3.connect(partial)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE contexts(
                agent_id INTEGER PRIMARY KEY,
                arm TEXT NOT NULL,
                context_json TEXT NOT NULL
            )
            """
        )
        write_metadata(
            connection,
            {
                "schema": CONTEXT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "created_at_utc": utc_now(),
                "seed": SEED,
                "population_size": population_size,
                "partition_size": partition_size,
                "partition_index": partition_index,
                "start_agent_id": start_id,
                "end_agent_id": end_id,
                "agent_count": len(contexts),
                "code_sha256": code_sha256,
                "design": design,
            },
        )
        connection.executemany(
            "INSERT INTO contexts(agent_id, arm, context_json) VALUES (?, ?, ?)",
            [
                (
                    int(context["agent_id"]),
                    str(context["scenario"]["arm"]),
                    canonical_json(context),
                )
                for context in contexts
            ],
        )
        connection.commit()
    except Exception:
        connection.close()
        raise
    else:
        connection.close()
    partial.replace(path)


def prepare_contexts(args: argparse.Namespace) -> None:
    population_size = int(args.population_size)
    partition_size = int(args.partition_size)
    if population_size <= 0 or partition_size <= 0:
        raise ValueError("population-size and partition-size must be positive")
    if population_size != DEFAULT_POPULATION_SIZE:
        raise ValueError(
            "This entry point is frozen to a 100,000-person parent population; "
            "use --max-agents for bounded structural tests"
        )
    if partition_size != DEFAULT_PARTITION_SIZE:
        raise ValueError("This execution design is frozen to 1,000-agent partitions")
    max_agents = population_size if args.max_agents is None else int(args.max_agents)
    if not 1 <= max_agents <= population_size:
        raise ValueError("max-agents must be between 1 and population-size")
    if max_agents % partition_size != 0 and max_agents != population_size:
        raise ValueError(
            "max-agents must end on a partition boundary for a resumable test"
        )

    root = Path(args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "context_manifest.json"
    if manifest_path.exists() and not args.resume:
        raise FileExistsError(
            f"Manifest already exists; pass --resume to verify and continue: {manifest_path}"
        )
    code_sha256 = source_sha256()
    agents, spatial, spatial_by_id = build_assigned_population(population_size)
    total_partitions = (population_size + partition_size - 1) // partition_size
    partitions_to_prepare = (max_agents + partition_size - 1) // partition_size
    records: list[dict[str, Any]] = []
    for partition_index in range(partitions_to_prepare):
        start_id, end_id = partition_bounds(
            partition_index, population_size, partition_size
        )
        path = context_partition_path(root, partition_index)
        if path.exists():
            if not args.resume:
                raise FileExistsError(path)
            record = validate_context_partition(
                path,
                partition_index=partition_index,
                population_size=population_size,
                partition_size=partition_size,
                expected_code_sha256=code_sha256,
            )
            records.append(record)
            continue
        assigned_slice = agents[start_id - 1 : end_id]
        contexts, design = build_decision_contexts_for_assigned_agents(
            assigned_slice,
            spatial,
            spatial_by_id,
            population_size,
        )
        design["title"] = (
            f"Urban Cup 100,000-person contexts, partition {partition_index:05d}"
        )
        design["partition_index"] = partition_index
        design["agent_id_range"] = [start_id, end_id]
        write_context_partition(
            path,
            contexts=contexts,
            design=design,
            partition_index=partition_index,
            population_size=population_size,
            partition_size=partition_size,
            code_sha256=code_sha256,
        )
        records.append(
            validate_context_partition(
                path,
                partition_index=partition_index,
                population_size=population_size,
                partition_size=partition_size,
                expected_code_sha256=code_sha256,
            )
        )
        del contexts

    prepared_agents = sum(int(record["agent_count"]) for record in records)
    combined_arms: Counter[str] = Counter()
    for record in records:
        combined_arms.update(record["arm_counts"])
    if prepared_agents == population_size:
        expected_per_arm = population_size // len(ARMS)
        if set(combined_arms.values()) != {expected_per_arm}:
            raise ValueError("Complete context set does not contain 10,000 agents per arm")
        status = "CONTEXTS_READY"
    else:
        status = "STRUCTURAL_TEST_CONTEXTS_READY"
    manifest = {
        "schema": "urban-cup-context-manifest-v1",
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "created_or_verified_at_utc": utc_now(),
        "seed": SEED,
        "population_size": population_size,
        "partition_size": partition_size,
        "total_partitions": total_partitions,
        "prepared_partitions": len(records),
        "prepared_agents": prepared_agents,
        "code_sha256": code_sha256,
        "scenario_arm_counts": dict(sorted(combined_arms.items())),
        "partitions": records,
    }
    if manifest_path.exists():
        verified = manifest_path.with_name("context_manifest.verified.json")
        if verified.exists():
            raise FileExistsError(
                f"Refusing to overwrite prior verification manifest: {verified}"
            )
        atomic_write_json(verified, manifest)
        output_manifest = verified
    else:
        atomic_write_json(manifest_path, manifest)
        output_manifest = manifest_path
    print(f"STATUS={status}")
    print(f"PREPARED_AGENTS={prepared_agents}")
    print(f"MANIFEST={output_manifest}")


def load_contexts(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    with connect_readonly(path) as connection:
        metadata = read_metadata(connection)
        rows = list(
            connection.execute(
                "SELECT context_json FROM contexts ORDER BY agent_id"
            )
        )
    contexts = [json.loads(row[0]) for row in rows]
    return contexts, metadata


def validate_complete_context_manifest(
    root: Path,
    *,
    partition_index: int,
    context_sha256: str,
    code_sha256: str,
) -> None:
    manifest_path = root / "context_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Formal run requires context_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema": "urban-cup-context-manifest-v1",
        "status": "CONTEXTS_READY",
        "seed": SEED,
        "population_size": DEFAULT_POPULATION_SIZE,
        "partition_size": DEFAULT_PARTITION_SIZE,
        "total_partitions": 100,
        "prepared_partitions": 100,
        "prepared_agents": DEFAULT_POPULATION_SIZE,
        "code_sha256": code_sha256,
    }
    for key, expected_value in expected_fields.items():
        if manifest.get(key) != expected_value:
            raise ValueError(f"Formal context manifest mismatch for {key}")
    records = {
        int(record["partition_index"]): record
        for record in manifest.get("partitions", [])
    }
    if set(records) != set(range(100)):
        raise ValueError("Formal context manifest does not list partitions 0..99")
    if str(records[partition_index].get("sha256")) != context_sha256:
        raise ValueError("Target context checksum differs from the formal manifest")


def invalid_ids_from_answer_partition(
    answer_path: Path,
    *,
    expected_context_sha256: str,
    expected_partition_index: int,
    expected_prior_attempt: int,
    expected_model: str,
    expected_code_sha256: str,
    expected_questionnaire_sha256: str,
) -> set[int]:
    with connect_readonly(answer_path) as connection:
        metadata = read_metadata(connection)
        if decode_metadata_value(metadata, "schema") != ANSWER_SCHEMA:
            raise ValueError("Retry source has an unexpected answer schema")
        prior_context_sha = decode_metadata_value(metadata, "context_sha256")
        if prior_context_sha != expected_context_sha256:
            raise ValueError("Retry source was produced from a different context partition")
        if int(decode_metadata_value(metadata, "partition_index")) != expected_partition_index:
            raise ValueError("Retry source partition index does not match")
        if int(decode_metadata_value(metadata, "attempt")) != expected_prior_attempt:
            raise ValueError("Retry source must be the immediately preceding attempt")
        if str(decode_metadata_value(metadata, "model")) != expected_model:
            raise ValueError("Retry source model differs from the configured model")
        if str(decode_metadata_value(metadata, "code_sha256")) != expected_code_sha256:
            raise ValueError("Retry source was produced by different code")
        if (
            str(decode_metadata_value(metadata, "questionnaire_sha256"))
            != expected_questionnaire_sha256
        ):
            raise ValueError("Retry source used a different questionnaire")
        return {
            int(row[0])
            for row in connection.execute(
                "SELECT agent_id FROM answers WHERE constraint_valid = 0"
            )
        }


def build_questionnaire() -> Questionnaire:
    return Questionnaire(
        questionnaire_id=QUESTIONNAIRE_ID,
        title="Representative urban trip decision",
        description=(
            "Use only your decision_context. The source model has already computed "
            "weather cancellation, policy eligibility, and feasible mode attributes."
        ),
        questions=[
            QuestionItem(
                id="mode_choice",
                prompt=(
                    "Choose your final action for the representative trip in your "
                    "decision_context. If trip_continues is false, choose cancel_trip. "
                    "Otherwise choose only a mode whose selectable field is true, or "
                    "cancel_trip if you personally decide not to travel. Consider your "
                    "age, mobility constraint, schedule flexibility, trip purpose, "
                    "travel time, wait, transfers, conditional fare, weather, digital "
                    "access, subsidy eligibility, and dispatch-priority eligibility."
                ),
                response_type="choice",
                choices=["walk", "bus", "metro", "ride_hailing", "cancel_trip"],
            )
        ],
    )


def questionnaire_sha256() -> str:
    questionnaire = build_questionnaire()
    payload = (
        questionnaire.model_dump()
        if hasattr(questionnaire, "model_dump")
        else vars(questionnaire)
    )
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


async def execute_questionnaire_partition(
    contexts: list[dict[str, Any]], workspace_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    pairs = [(int(item["agent_id"]), str(item["name"])) for item in contexts]
    specs = [
        {
            "id": int(item["agent_id"]),
            "profile": {
                "id": int(item["agent_id"]),
                "name": str(item["name"]),
                "decision_context": item,
            },
            "config": {
                "enable_memory": False,
                "enable_todo_list": False,
                "max_react_turns": 3,
            },
        }
        for item in contexts
    ]
    social_space = SimpleSocialSpace(pairs)
    router = UrbanReadOnlyRouter([social_space], max_steps=2, max_llm_call_retry=1)
    router.run_dir = workspace_dir
    router.bind_env_workspaces(workspace_dir / "env", ["SimpleSocialSpace"])
    force_minimal_ray_dashboard_agent()
    society = AgentSociety(
        agent_specs=specs,
        agent_class_name="PersonAgent",
        env_router=router,
        start_t=datetime(2026, 7, 6, 6, 0, 0),
        run_dir=workspace_dir,
        batch_size=min(10, len(contexts)),
        enable_replay=False,
        env_module_types=["SimpleSocialSpace"],
        env_kwargs={"SimpleSocialSpace": {"agent_id_name_pairs": pairs}},
    )
    initialized = False
    try:
        await society.init()
        initialized = True
        response = await society.run_questionnaire(build_questionnaire())
        rows, violations = flatten_results(contexts, response)
        token_stats = dict(society._token_stats)
        return rows, violations, token_stats
    finally:
        if initialized or society.agent_ids:
            await society.close()
        if ray.is_initialized():
            ray.shutdown()


def aggregate_usage(token_stats: dict[str, Any]) -> dict[str, int]:
    return {
        "calls": sum(int(item.get("calls", 0)) for item in token_stats.values()),
        "input_tokens": sum(
            int(item.get("input", 0)) for item in token_stats.values()
        ),
        "output_tokens": sum(
            int(item.get("output", 0)) for item in token_stats.values()
        ),
    }


def write_answer_partition(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite completed answer partition: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(
            f"Refusing to replace stale answer partition: {partial}"
        )
    connection = sqlite3.connect(partial)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE answers(
                agent_id INTEGER PRIMARY KEY,
                arm TEXT NOT NULL,
                choice TEXT,
                reason TEXT,
                parse_success INTEGER NOT NULL,
                constraint_valid INTEGER NOT NULL,
                weather_exposed INTEGER NOT NULL,
                weather_cancelled INTEGER NOT NULL,
                trip_continues INTEGER NOT NULL
            )
            """
        )
        write_metadata(connection, metadata)
        connection.executemany(
            """
            INSERT INTO answers(
                agent_id, arm, choice, reason, parse_success, constraint_valid,
                weather_exposed, weather_cancelled, trip_continues
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(row["agent_id"]),
                    str(row["arm"]),
                    row["choice"],
                    row["reason"],
                    int(bool(row["parse_success"])),
                    int(bool(row["constraint_valid"])),
                    int(bool(row["weather_exposed"])),
                    int(bool(row["weather_cancelled"])),
                    int(bool(row["trip_continues"])),
                )
                for row in rows
            ],
        )
        connection.execute(
            "CREATE INDEX answers_valid_idx ON answers(constraint_valid, agent_id)"
        )
        connection.commit()
    except Exception:
        connection.close()
        raise
    else:
        connection.close()
    partial.replace(path)


async def run_llm_partition(args: argparse.Namespace) -> None:
    missing = sorted(
        MISSING_LLM_ENVIRONMENT_AT_START
        | {
            name
            for name, placeholder in NO_LLM_IMPORT_DEFAULTS.items()
            if os.getenv(name) == placeholder
        }
    )
    if missing:
        raise RuntimeError(
            "Missing required secret/config environment variables: "
            + ", ".join(missing)
        )
    root = Path(args.output_root).resolve()
    context_path = context_partition_path(root, args.partition_index)
    context_sha256 = sha256_file(context_path)
    contexts, context_metadata = load_contexts(context_path)
    recorded_partition_index = int(
        decode_metadata_value(context_metadata, "partition_index")
    )
    if recorded_partition_index != args.partition_index:
        raise ValueError("Context partition index does not match its metadata")
    model_name = str(os.environ["AGENTSOCIETY_LLM_MODEL"])
    code_sha256 = source_sha256()
    prompt_sha256 = questionnaire_sha256()
    population_size = int(decode_metadata_value(context_metadata, "population_size"))
    partition_size = int(decode_metadata_value(context_metadata, "partition_size"))
    validate_context_partition(
        context_path,
        partition_index=args.partition_index,
        population_size=population_size,
        partition_size=partition_size,
        expected_code_sha256=code_sha256,
    )
    if args.require_complete_context_set:
        validate_complete_context_manifest(
            root,
            partition_index=args.partition_index,
            context_sha256=context_sha256,
            code_sha256=code_sha256,
        )

    retry_source: Path | None = None
    retry_relative_path: str | None = None
    if args.retry_from is not None:
        if args.max_agents is not None:
            raise ValueError("--max-agents cannot be combined with --retry-from")
        if args.attempt <= 0:
            raise ValueError("A retry partition must use --attempt greater than zero")
        retry_source = Path(args.retry_from).resolve()
        try:
            retry_relative_path = str(retry_source.relative_to(root)).replace(
                "\\", "/"
            )
        except ValueError as exc:
            raise ValueError("retry-from must be inside output-root") from exc
        target_ids = invalid_ids_from_answer_partition(
            retry_source,
            expected_context_sha256=context_sha256,
            expected_partition_index=args.partition_index,
            expected_prior_attempt=args.attempt - 1,
            expected_model=model_name,
            expected_code_sha256=code_sha256,
            expected_questionnaire_sha256=prompt_sha256,
        )
        if not target_ids:
            raise ValueError("Retry source contains no invalid agents; nothing to rerun")
        contexts = [
            item for item in contexts if int(item["agent_id"]) in target_ids
        ]
        if {int(item["agent_id"]) for item in contexts} != target_ids:
            raise ValueError("Retry source references IDs outside this context partition")
    elif args.attempt != 0:
        raise ValueError("Attempts greater than zero require --retry-from")
    elif args.max_agents is not None:
        max_agents = int(args.max_agents)
        if not 1 <= max_agents <= len(contexts):
            raise ValueError(
                f"max-agents must be between 1 and {len(contexts)} for this partition"
            )
        contexts = contexts[:max_agents]

    output_path = answer_partition_path(
        root, args.attempt, args.partition_index
    )
    output_partial = output_path.with_name(output_path.name + ".partial")
    if output_path.exists():
        raise FileExistsError(f"Answer partition already exists: {output_path}")
    if output_partial.exists():
        raise FileExistsError(
            f"Inspect stale answer partial before a paid retry: {output_partial}"
        )

    workspace_dir = Path(args.workspace_dir).resolve()
    try:
        workspace_dir.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("workspace-dir must not be inside shared output-root")
    if os.getenv("SLURM_JOB_ID"):
        slurm_tmp_raw = os.getenv("SLURM_TMPDIR")
        if not slurm_tmp_raw:
            raise RuntimeError("SLURM_TMPDIR is required for an HPC LLM partition")
        slurm_tmp = Path(slurm_tmp_raw).resolve()
        try:
            workspace_dir.relative_to(slurm_tmp)
        except ValueError as exc:
            raise ValueError(
                "On Slurm, workspace-dir must be under node-local SLURM_TMPDIR"
            ) from exc
    if workspace_dir.exists():
        if any(workspace_dir.iterdir()):
            raise FileExistsError(
                f"Workspace must be a new or empty node-local directory: {workspace_dir}"
            )
    else:
        workspace_dir.mkdir(parents=True)

    started_at_utc = utc_now()
    started = time.perf_counter()
    rows, violations, token_stats = await execute_questionnaire_partition(
        contexts, workspace_dir
    )
    usage = aggregate_usage(token_stats)
    expected_ids = sorted(int(item["agent_id"]) for item in contexts)
    actual_ids = sorted(int(row["agent_id"]) for row in rows)
    if actual_ids != expected_ids:
        raise ValueError("Questionnaire result IDs do not match requested IDs")
    violation_ids = sorted(int(item["agent_id"]) for item in violations)
    metadata = {
        "schema": ANSWER_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "started_at_utc": started_at_utc,
        "completed_at_utc": utc_now(),
        "seed": SEED,
        "population_size": population_size,
        "partition_index": args.partition_index,
        "attempt": args.attempt,
        "agent_count_requested": len(contexts),
        "agent_id_min": min(expected_ids),
        "agent_id_max": max(expected_ids),
        "scenario_arm_counts": dict(
            sorted(Counter(str(row["arm"]) for row in rows).items())
        ),
        "model": model_name,
        "agent_class": "PersonAgent",
        "environment": "SimpleSocialSpace",
        "agentsociety2_version": agentsociety2.__version__,
        "ray_version": ray.__version__,
        "context_relative_path": str(
            context_path.relative_to(root)
        ).replace("\\", "/"),
        "context_sha256": context_sha256,
        "code_sha256": code_sha256,
        "questionnaire_sha256": prompt_sha256,
        "retry_source": retry_relative_path,
        "llm_usage": usage,
        "constraint_valid_count": len(rows) - len(violations),
        "invalid_agent_ids": violation_ids,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "llm_ray_max_workers": os.getenv("AGENTSOCIETY_LLM_RAY_MAX_WORKERS", ""),
        "llm_ray_concurrency": os.getenv("AGENTSOCIETY_LLM_RAY_CONCURRENCY", ""),
        "secret_persisted": False,
    }
    write_answer_partition(
        output_path,
        rows=rows,
        metadata=metadata,
    )
    print(f"ANSWER_PARTITION={output_path}")
    print(f"AGENTS_REQUESTED={len(rows)}")
    print(f"CONSTRAINT_VALID={len(rows) - len(violations)}")
    print(f"INVALID={len(violations)}")
    print(f"LLM_CALLS={usage['calls']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or run bounded Urban Cup 100k partitions"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare-contexts", help="CPU-only streaming context generation"
    )
    prepare.add_argument("--output-root", required=True)
    prepare.add_argument("--population-size", type=int, default=DEFAULT_POPULATION_SIZE)
    prepare.add_argument("--partition-size", type=int, default=DEFAULT_PARTITION_SIZE)
    prepare.add_argument(
        "--max-agents",
        type=int,
        default=None,
        help="Prepare only the first N agents for a bounded structural test.",
    )
    prepare.add_argument(
        "--resume",
        action="store_true",
        help="Verify completed partitions and continue missing ones; never overwrite.",
    )

    run = subparsers.add_parser(
        "run-llm-partition", help="Run one explicit paid API partition"
    )
    run.add_argument("--output-root", required=True)
    run.add_argument("--partition-index", required=True, type=int)
    run.add_argument("--attempt", type=int, default=0)
    run.add_argument(
        "--max-agents",
        type=int,
        default=None,
        help="Bounded benchmark only: run the first N agents in this partition.",
    )
    run.add_argument(
        "--retry-from",
        default=None,
        help="Prior answer SQLite; only its invalid agents are rerun.",
    )
    run.add_argument(
        "--workspace-dir",
        required=True,
        help="New/empty node-local workspace directory, normally under SLURM_TMPDIR.",
    )
    run.add_argument(
        "--require-complete-context-set",
        action="store_true",
        help="Formal gate: require a 100-partition CONTEXTS_READY manifest.",
    )
    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "prepare-contexts":
        prepare_contexts(args)
    elif args.command == "run-llm-partition":
        await run_llm_partition(args)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Do not serialize arbitrary provider exceptions: some SDKs include
        # request headers.  The exception class is sufficient for Slurm triage
        # without risking an API key in stdout/stderr.
        print(f"ERROR_TYPE={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
