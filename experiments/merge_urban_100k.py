"""Strictly merge partitioned Urban Cup 100k context and answer databases.

The merge is local/CPU-only.  It requires complete, unique context coverage of
IDs 1..100000.  Answer coverage may be incomplete, but every missing or invalid
agent is represented explicitly and prevents a PASS status.  Later attempts
may replace only a previously invalid answer; a valid answer is immutable.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from urban_100k_partitioned import (
    ANSWER_SCHEMA,
    DEFAULT_PARTITION_SIZE,
    DEFAULT_POPULATION_SIZE,
    SCHEMA_VERSION,
    connect_readonly,
    context_partition_path,
    decode_metadata_value,
    expected_arm,
    read_metadata,
    sha256_file,
    source_sha256,
    utc_now,
    validate_context_partition,
    write_metadata,
)


FINAL_SCHEMA = "urban-cup-final-answers-v1"


def load_all_contexts(
    root: Path, population_size: int, partition_size: int
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    total_partitions = (population_size + partition_size - 1) // partition_size
    contexts: dict[int, dict[str, Any]] = {}
    partition_records: list[dict[str, Any]] = []
    for partition_index in range(total_partitions):
        path = context_partition_path(root, partition_index)
        record = validate_context_partition(
            path,
            partition_index=partition_index,
            population_size=population_size,
            partition_size=partition_size,
        )
        partition_records.append(record)
        with connect_readonly(path) as connection:
            for agent_id, context_json in connection.execute(
                "SELECT agent_id, context_json FROM contexts ORDER BY agent_id"
            ):
                agent_id = int(agent_id)
                if agent_id in contexts:
                    raise ValueError(f"Duplicate context agent ID: {agent_id}")
                contexts[agent_id] = json.loads(context_json)
    expected_ids = set(range(1, population_size + 1))
    actual_ids = set(contexts)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise ValueError(
            f"Context coverage mismatch; missing={missing[:10]}, extra={extra[:10]}"
        )
    arm_counts = Counter(
        str(context["scenario"]["arm"]) for context in contexts.values()
    )
    expected_per_arm = population_size // 10
    if len(arm_counts) != 10 or set(arm_counts.values()) != {expected_per_arm}:
        raise ValueError(
            f"Context scenario arms are not balanced at {expected_per_arm} each: "
            f"{dict(arm_counts)}"
        )
    code_hashes = {str(record["code_sha256"]) for record in partition_records}
    if len(code_hashes) != 1:
        raise ValueError(f"Context partitions were built by mixed code: {code_hashes}")
    return contexts, partition_records


def discover_answer_partitions(root: Path) -> list[tuple[int, int, Path]]:
    answers_root = root / "answers"
    if not answers_root.is_dir():
        return []
    discovered: list[tuple[int, int, Path]] = []
    for attempt_dir in sorted(answers_root.glob("attempt-*")):
        if not attempt_dir.is_dir():
            continue
        try:
            attempt = int(attempt_dir.name.removeprefix("attempt-"))
        except ValueError as exc:
            raise ValueError(f"Invalid attempt directory: {attempt_dir}") from exc
        for path in sorted(attempt_dir.glob("part-*.sqlite")):
            try:
                partition_index = int(path.stem.removeprefix("part-"))
            except ValueError as exc:
                raise ValueError(f"Invalid answer partition name: {path}") from exc
            discovered.append((attempt, partition_index, path))
    return sorted(discovered)


def load_answers(
    root: Path,
    contexts: dict[int, dict[str, Any]],
    answer_partitions: list[tuple[int, int, Path]],
) -> tuple[dict[int, dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    chosen: dict[int, dict[str, Any]] = {}
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    partition_audit: list[dict[str, Any]] = []
    seen_in_attempt: defaultdict[int, set[int]] = defaultdict(set)
    answer_models: set[str] = set()
    answer_code_hashes: set[str] = set()
    questionnaire_hashes: set[str] = set()

    for attempt, partition_index, path in answer_partitions:
        expected_context_path = context_partition_path(root, partition_index)
        expected_context_sha = sha256_file(expected_context_path)
        with connect_readonly(path) as connection:
            metadata = read_metadata(connection)
            if decode_metadata_value(metadata, "schema") != ANSWER_SCHEMA:
                raise ValueError(f"Unexpected answer schema in {path}")
            if int(decode_metadata_value(metadata, "attempt")) != attempt:
                raise ValueError(f"Attempt metadata mismatch in {path}")
            if int(decode_metadata_value(metadata, "partition_index")) != partition_index:
                raise ValueError(f"Partition metadata mismatch in {path}")
            if decode_metadata_value(metadata, "context_sha256") != expected_context_sha:
                raise ValueError(f"Context checksum mismatch in {path}")
            if int(decode_metadata_value(metadata, "seed")) != 47:
                raise ValueError(f"Seed mismatch in {path}")
            answer_models.add(str(decode_metadata_value(metadata, "model")))
            answer_code_hashes.add(
                str(decode_metadata_value(metadata, "code_sha256"))
            )
            questionnaire_hashes.add(
                str(decode_metadata_value(metadata, "questionnaire_sha256"))
            )
            retry_source = decode_metadata_value(metadata, "retry_source")
            if attempt == 0 and retry_source is not None:
                raise ValueError(f"Attempt zero cannot be a retry: {path}")
            if attempt > 0 and retry_source is None:
                raise ValueError(f"Retry attempt lacks retry_source: {path}")
            rows = list(
                connection.execute(
                    """
                    SELECT agent_id, arm, choice, reason, parse_success,
                           constraint_valid, weather_exposed, weather_cancelled,
                           trip_continues
                    FROM answers ORDER BY agent_id
                    """
                )
            )
        metadata_usage = decode_metadata_value(metadata, "llm_usage")
        for key in usage:
            usage[key] += int(metadata_usage.get(key, 0))
        valid_count = 0
        for row in rows:
            agent_id = int(row[0])
            lower_bound = partition_index * DEFAULT_PARTITION_SIZE + 1
            upper_bound = min(
                DEFAULT_POPULATION_SIZE,
                lower_bound + DEFAULT_PARTITION_SIZE - 1,
            )
            if not lower_bound <= agent_id <= upper_bound:
                raise ValueError(
                    f"Agent {agent_id} is outside answer partition {partition_index}"
                )
            if agent_id in seen_in_attempt[attempt]:
                raise ValueError(
                    f"Agent {agent_id} appears twice in attempt {attempt}"
                )
            seen_in_attempt[attempt].add(agent_id)
            if agent_id not in contexts:
                raise ValueError(f"Answer references unknown agent ID {agent_id}")
            if str(row[1]) != expected_arm(agent_id):
                raise ValueError(f"Answer arm mismatch for agent {agent_id}")
            candidate = {
                "agent_id": agent_id,
                "arm": str(row[1]),
                "choice": row[2],
                "reason": row[3],
                "parse_success": bool(row[4]),
                "constraint_valid": bool(row[5]),
                "weather_exposed": bool(row[6]),
                "weather_cancelled": bool(row[7]),
                "trip_continues": bool(row[8]),
                "selected_attempt": attempt,
                "source_partition": partition_index,
            }
            previous = chosen.get(agent_id)
            if previous is not None and previous["constraint_valid"]:
                raise ValueError(
                    f"Attempt {attempt} illegally reruns already-valid agent {agent_id}"
                )
            if previous is None and attempt > 0:
                raise ValueError(
                    f"Retry attempt {attempt} has no earlier invalid row for agent {agent_id}"
                )
            chosen[agent_id] = candidate
            valid_count += int(candidate["constraint_valid"])
        partition_audit.append(
            {
                "attempt": attempt,
                "partition_index": partition_index,
                "relative_path": str(path.relative_to(root)).replace("\\", "/"),
                "sha256": sha256_file(path),
                "agent_count": len(rows),
                "constraint_valid_count": valid_count,
                "model": decode_metadata_value(metadata, "model"),
                "code_sha256": decode_metadata_value(metadata, "code_sha256"),
                "questionnaire_sha256": decode_metadata_value(
                    metadata, "questionnaire_sha256"
                ),
                "llm_usage": metadata_usage,
            }
        )
    if len(answer_models) > 1:
        raise ValueError(f"Answer partitions use mixed models: {answer_models}")
    if len(answer_code_hashes) > 1:
        raise ValueError(
            f"Answer partitions use mixed execution code: {answer_code_hashes}"
        )
    if len(questionnaire_hashes) > 1:
        raise ValueError(
            f"Answer partitions use mixed questionnaires: {questionnaire_hashes}"
        )
    return chosen, usage, partition_audit


def build_final_rows(
    contexts: dict[int, dict[str, Any]],
    answers: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for agent_id in sorted(contexts):
        context = contexts[agent_id]
        answer = answers.get(agent_id)
        rows.append(
            {
                "agent_id": agent_id,
                "age_group": context["age_group"],
                "home_zone": context["home_zone"],
                "arm": context["scenario"]["arm"],
                "weather": context["scenario"]["weather"],
                "policy": context["scenario"]["policy"],
                "discount_level": context["scenario"]["discount"],
                "purpose": context["representative_trip"]["purpose"],
                "weather_exposed": bool(
                    context["weather_activity_effect"]["outbound_weather_exposed"]
                ),
                "weather_cancelled": bool(
                    context["weather_activity_effect"]["weather_cancelled"]
                ),
                "trip_continues": bool(context["trip_continues"]),
                "choice": None if answer is None else answer["choice"],
                "reason": None if answer is None else answer["reason"],
                "parse_success": False
                if answer is None
                else answer["parse_success"],
                "constraint_valid": False
                if answer is None
                else answer["constraint_valid"],
                "response_missing": answer is None,
                "selected_attempt": None
                if answer is None
                else answer["selected_attempt"],
                "source_partition": None
                if answer is None
                else answer["source_partition"],
            }
        )
    return rows


def summarize(
    rows: list[dict[str, Any]], usage: dict[str, int]
) -> dict[str, Any]:
    response_count = sum(not row["response_missing"] for row in rows)
    valid_count = sum(row["constraint_valid"] for row in rows)
    parse_count = sum(row["parse_success"] for row in rows)
    missing_ids = [row["agent_id"] for row in rows if row["response_missing"]]
    invalid_ids = [
        row["agent_id"]
        for row in rows
        if not row["response_missing"] and not row["constraint_valid"]
    ]
    choices = Counter(
        str(row["choice"]) for row in rows if not row["response_missing"]
    )
    by_arm: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if not row["response_missing"]:
            by_arm[str(row["arm"])][str(row["choice"])] += 1
    status = (
        "PASS"
        if response_count == DEFAULT_POPULATION_SIZE
        and valid_count == DEFAULT_POPULATION_SIZE
        else "INCOMPLETE_OR_INVALID"
    )
    return {
        "status": status,
        "population_size": len(rows),
        "response_count": response_count,
        "missing_response_count": len(missing_ids),
        "invalid_response_count": len(invalid_ids),
        "parse_success_count": parse_count,
        "constraint_valid_count": valid_count,
        "missing_agent_ids": missing_ids,
        "invalid_agent_ids": invalid_ids,
        "choice_counts": dict(sorted(choices.items())),
        "choice_counts_by_arm": {
            arm: dict(sorted(counts.items()))
            for arm, counts in sorted(by_arm.items())
        },
        "weather_exposed_count": sum(row["weather_exposed"] for row in rows),
        "weather_cancelled_count": sum(row["weather_cancelled"] for row in rows),
        "token_usage": usage,
        "causal_effect_claimed": False,
    }


def write_final_database(
    path: Path,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite final output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError(f"Inspect stale final partial before retrying: {partial}")
    connection = sqlite3.connect(partial)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE final_answers(
                agent_id INTEGER PRIMARY KEY,
                age_group TEXT NOT NULL,
                home_zone TEXT NOT NULL,
                arm TEXT NOT NULL,
                weather TEXT NOT NULL,
                policy TEXT NOT NULL,
                discount_level TEXT,
                purpose TEXT NOT NULL,
                weather_exposed INTEGER NOT NULL,
                weather_cancelled INTEGER NOT NULL,
                trip_continues INTEGER NOT NULL,
                choice TEXT,
                reason TEXT,
                parse_success INTEGER NOT NULL,
                constraint_valid INTEGER NOT NULL,
                response_missing INTEGER NOT NULL,
                selected_attempt INTEGER,
                source_partition INTEGER
            )
            """
        )
        write_metadata(connection, metadata)
        connection.executemany(
            """
            INSERT INTO final_answers VALUES (
                :agent_id, :age_group, :home_zone, :arm, :weather, :policy,
                :discount_level, :purpose, :weather_exposed,
                :weather_cancelled, :trip_continues, :choice, :reason,
                :parse_success, :constraint_valid, :response_missing,
                :selected_attempt, :source_partition
            )
            """,
            [
                {
                    **row,
                    "weather_exposed": int(row["weather_exposed"]),
                    "weather_cancelled": int(row["weather_cancelled"]),
                    "trip_continues": int(row["trip_continues"]),
                    "parse_success": int(row["parse_success"]),
                    "constraint_valid": int(row["constraint_valid"]),
                    "response_missing": int(row["response_missing"]),
                }
                for row in rows
            ],
        )
        connection.execute(
            "CREATE INDEX final_valid_idx ON final_answers(constraint_valid, agent_id)"
        )
        connection.execute(
            "CREATE INDEX final_arm_choice_idx ON final_answers(arm, choice)"
        )
        connection.commit()
    except Exception:
        connection.close()
        raise
    else:
        connection.close()
    partial.replace(path)


def merge(args: argparse.Namespace) -> None:
    root = Path(args.output_root).resolve()
    final_path = Path(args.final_db).resolve()
    try:
        final_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("final-db must be inside output-root") from exc
    summary_path = final_path.with_suffix(".summary.json")
    summary_partial = summary_path.with_name(summary_path.name + ".partial")
    if final_path.exists():
        raise FileExistsError(f"Refusing to overwrite final output: {final_path}")
    if summary_path.exists():
        raise FileExistsError(f"Refusing to overwrite final summary: {summary_path}")
    if summary_partial.exists():
        raise FileExistsError(f"Inspect stale summary partial: {summary_partial}")
    population_size = int(args.population_size)
    partition_size = int(args.partition_size)
    if population_size != DEFAULT_POPULATION_SIZE:
        raise ValueError("Final merge is frozen to exactly 100,000 agents")
    if partition_size != DEFAULT_PARTITION_SIZE:
        raise ValueError("Final merge is frozen to 1,000-agent partitions")
    contexts, context_audit = load_all_contexts(
        root, population_size, partition_size
    )
    answer_partitions = discover_answer_partitions(root)
    answers, usage, answer_audit = load_answers(
        root, contexts, answer_partitions
    )
    context_code_hashes = {
        str(record["code_sha256"]) for record in context_audit
    }
    current_code_sha256 = source_sha256()
    if context_code_hashes != {current_code_sha256}:
        raise ValueError(
            "Current source does not match the code that produced the contexts"
        )
    answer_code_hashes = {
        str(record["code_sha256"]) for record in answer_audit
    }
    if answer_code_hashes and answer_code_hashes != context_code_hashes:
        raise ValueError(
            "Context and answer partitions were not produced by the same frozen code"
        )
    rows = build_final_rows(contexts, answers)
    summary = summarize(rows, usage)
    metadata = {
        "schema": FINAL_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "seed": 47,
        "population_size": population_size,
        "partition_size": partition_size,
        "code_sha256": current_code_sha256,
        "summary": summary,
        "context_partitions": context_audit,
        "answer_partitions": answer_audit,
    }
    write_final_database(final_path, rows, metadata)
    final_sha256 = sha256_file(final_path)
    summary_payload = {
        **summary,
        "final_database": str(final_path),
        "final_database_sha256": final_sha256,
        "context_partition_count": len(context_audit),
        "answer_partition_count": len(answer_audit),
        "completed_at_utc": utc_now(),
    }
    summary_partial.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_partial.replace(summary_path)
    print(f"STATUS={summary['status']}")
    print(f"RESPONSES={summary['response_count']}/{population_size}")
    print(f"CONSTRAINT_VALID={summary['constraint_valid_count']}/{population_size}")
    print(f"FINAL_DB={final_path}")
    print(f"SUMMARY={summary_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Strict Urban Cup 100k merge")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--final-db", required=True)
    parser.add_argument("--population-size", type=int, default=DEFAULT_POPULATION_SIZE)
    parser.add_argument("--partition-size", type=int, default=DEFAULT_PARTITION_SIZE)
    args = parser.parse_args()
    merge(args)


if __name__ == "__main__":
    main()
